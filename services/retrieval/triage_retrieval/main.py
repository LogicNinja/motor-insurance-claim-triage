"""Retrieval API backed by the shared Chroma collection and local embeddings."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
import uuid
from urllib.parse import unquote

import chromadb
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

from .cache import CACHE_ACCESS, CACHE_ENTRIES, TTL_SECONDS, cache_key, response_cache
from shared.logging_setup import correlation_id, setup_logging
from shared.telemetry import extract_trace_context, setup_tracing


COLLECTION_NAME = "motor_policy_chunks"

app = FastAPI(title="Retrieval service", version="0.2.0")
setup_logging("retrieval")
log = logging.getLogger("retrieval")
tracer = setup_tracing("claim-triage-retrieval")
SEARCH_REQUESTS = Counter("retrieval_searches_total", "Corpus search requests")
SEARCH_DURATION = Histogram("retrieval_search_duration_seconds", "Corpus search duration")
CORPUS_CHUNKS = Gauge("corpus_chunks_total", "Number of chunks in the corpus index")
CORPUS_BUILD_TIME = Gauge("corpus_last_build_timestamp_seconds", "Unix timestamp of last corpus build")
CORPUS_THROUGHPUT = Gauge("corpus_index_throughput_chunks_per_second", "Throughput of last corpus build")
CORPUS_BUILT = Gauge("corpus_index_built", "Whether a corpus build has completed")


@app.middleware("http")
async def trace_and_log_request(request, call_next):
    cid = request.headers.get("x-correlation-id") or uuid.uuid4().hex
    token = correlation_id.set(cid)
    path = "/clause/{clause_id}" if request.url.path.startswith("/clause/") else request.url.path
    parent = extract_trace_context(dict(request.headers))
    started = time.perf_counter()
    status = 500
    try:
        with tracer.start_as_current_span(
            f"HTTP {request.method} {path}", context=parent,
            attributes={"http.request.method": request.method, "url.path": path, "correlation_id": cid},
        ) as span:
            try:
                response = await call_next(request)
                status = response.status_code
                response.headers["x-correlation-id"] = cid
                span.set_attribute("http.response.status_code", status)
                return response
            except Exception as exc:
                span.record_exception(exc)
                raise
            finally:
                log.info("request", extra={"fields": {"method": request.method, "path": path,
                           "status": status, "duration_ms": round((time.perf_counter()-started)*1000, 1)}})
    finally:
        correlation_id.reset(token)

_embedder = None
_embedder_error: Exception | None = None
_embedder_error_at = 0.0
_collection = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)
    filters: dict = Field(default_factory=dict)


class ReferenceRequest(BaseModel):
    term: str


def _get_collection(refresh_metadata: bool = False):
    global _collection
    if _collection is None or refresh_metadata:
        client = chromadb.HttpClient(
            host=os.getenv("CHROMA_HOST", "chroma"),
            port=int(os.getenv("CHROMA_PORT", "8000")),
        )
        try:
            _collection = client.get_collection(name=COLLECTION_NAME)
        except Exception:
            _collection = client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
    return _collection


def _get_embedder():
    global _embedder, _embedder_error, _embedder_error_at
    if _embedder is None:
        if _embedder_error is not None and time.monotonic() - _embedder_error_at < 300:
            raise RuntimeError("embedding model is temporarily unavailable") from _embedder_error
        model_name = os.getenv("EMBEDDING_MODEL") or "sentence-transformers/all-MiniLM-L6-v2"
        try:
            _embedder = SentenceTransformer(model_name)
            _embedder_error = None
        except Exception as exc:
            _embedder_error = exc
            _embedder_error_at = time.monotonic()
            raise
    return _embedder


def _make_where(filters: dict):
    """Accept simple equality filters or a native Chroma where expression."""
    if not filters:
        return None

    if any(key.startswith("$") for key in filters):
        return filters

    conditions = [
        {key: {"$eq": value}}
        for key, value in filters.items()
    ]

    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _explicit_clause_candidates(collection, query: str) -> list[dict]:
    """Add exact clause matches when a query names a required policy anchor."""
    normalized = re.sub(r"\s+", " ", query).casefold()
    filters = []
    if re.search(r"general exceptions?\s+iii\s*\(a\)", normalized):
        filters.append({"clause_id": {"$eq": "General Exceptions III(a)"}})
    if re.search(r"section\s+i\s*\(?a\)?", normalized):
        filters.append({"$or": [
            {"clause_id": {"$eq": "Section I(A) clause 1"}},
            {"clause_id": {"$eq": "Section I(A) clause 1 item VI"}},
        ]})
    if re.search(r"section\s+i\s*\(?b\)?", normalized):
        filters.append({"$or": [
            {"clause_id": {"$eq": "Section I(B) clause 1"}},
            {"clause_id": {"$eq": "Section I(B) clause 1 item V"}},
        ]})
    if re.search(r"chapter\s+4\b", normalized):
        filters.append({"chapter": {"$eq": "CHAPTER 4 - Limitations as to Use"}})

    candidates = []
    for where in filters:
        found = collection.get(where=where, include=["documents", "metadatas"])
        for chunk_id, document, metadata in zip(
            found.get("ids", []), found.get("documents", []), found.get("metadatas", [])
        ):
            candidates.append({
                "id": chunk_id,
                "document": document,
                "metadata": metadata or {},
                "distance": 0.0,
            })
    return candidates


_LEXICAL_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how", "i",
    "in", "is", "it", "my", "of", "on", "or", "the", "this", "to", "was", "what",
    "when", "where", "which", "with",
}


def _lexical_candidates(collection, query: str, top_k: int, where: dict | None) -> list[dict]:
    """Rank corpus text locally when the embedding model cannot be loaded."""
    tokens = [token for token in re.findall(r"[a-z0-9]+", query.casefold())
              if len(token) > 2 and token not in _LEXICAL_STOP_WORDS]
    if not tokens:
        return []
    query_terms = set(tokens)
    data = collection.get(where=where, include=["documents", "metadatas"])
    documents = [
        {"id": cid, "document": text or "", "metadata": metadata or {}}
        for cid, text, metadata in zip(data.get("ids", []), data.get("documents", []), data.get("metadatas", []))
    ]
    if not documents:
        return []

    frequencies = []
    document_terms = []
    for item in documents:
        terms = re.findall(r"[a-z0-9]+", (item["document"] + " " + str(item["metadata"])).casefold())
        counts: dict[str, int] = {}
        for term in terms:
            counts[term] = counts.get(term, 0) + 1
        document_terms.append((terms, counts))
        frequencies.append(set(counts))

    total = len(documents)
    ranked = []
    for item, (terms, counts), term_set in zip(documents, document_terms, frequencies):
        score = 0.0
        for term in query_terms & term_set:
            df = sum(term in present for present in frequencies)
            inverse_frequency = max(0.0, math.log(1 + (total - df + 0.5) / (df + 0.5)))
            tf = counts[term]
            score += inverse_frequency * (tf * 2.2) / (tf + 1.2)
        if score > 0:
            item["distance"] = -score
            ranked.append(item)
    ranked.sort(key=lambda item: (item["distance"], item["id"]))
    return ranked[:top_k]


@app.get("/health")
def health():
    try:
        collection = _get_collection()
        return {
            "status": "ok",
            "collection": collection.name,
            "chunks": collection.count(),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/status")
def status():
    try:
        collection = _get_collection(refresh_metadata=True)
        metadata = collection.metadata or {}
        versions = metadata.get("document_versions_json", "{}")
        try:
            versions = json.loads(versions)
        except (TypeError, json.JSONDecodeError):
            versions = {}

        return {
            "collection": collection.name,
            "chunk_count": collection.count(),
            "embedding_model": metadata.get("embedding_model"),
            "build_time": metadata.get("build_time"),
            "last_build_utc": metadata.get("last_build_utc"),
            "build_signature": metadata.get("build_signature"),
            "index_state": "built" if metadata.get("build_signature") else "not_built",
            "throughput_chunks_per_second": metadata.get(
                "throughput_chunks_per_second"
            ),
            "document_versions": versions,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/metrics", response_class=PlainTextResponse)
def metrics():
    try:
        collection = _get_collection(refresh_metadata=True)
        metadata = collection.metadata or {}
        CORPUS_CHUNKS.set(collection.count())
        CORPUS_BUILD_TIME.set(float(metadata.get("build_time", 0) or 0))
        CORPUS_THROUGHPUT.set(float(metadata.get("throughput_chunks_per_second", 0) or 0))
        CORPUS_BUILT.set(1 if metadata.get("build_signature") else 0)
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/search")
def search(req: SearchRequest):
    started = time.perf_counter()
    SEARCH_REQUESTS.inc()
    with tracer.start_as_current_span("retrieval.search", attributes={
        "correlation_id": correlation_id.get(), "retrieval.query": req.query, "retrieval.top_k": req.top_k,
    }) as span:
        try:
            collection = _get_collection()
            corpus_version = str((collection.metadata or {}).get("build_signature", ""))
            key = cache_key("search", corpus_version, {
                "query": req.query, "top_k": req.top_k, "filters": req.filters,
            })
            cached = response_cache.get(key, "search")
            if cached is not None:
                span.set_attribute("retrieval.cache_hit", True)
                span.set_attribute("retrieval.result_count", cached.get("count", 0))
                return cached
            span.set_attribute("retrieval.cache_hit", False)
            if collection.count() == 0:
                raise HTTPException(status_code=503, detail="Corpus index is empty")

            where = _make_where(req.filters)
            retrieval_mode = "vector"
            try:
                vector = _get_embedder().encode(
                    [req.query],
                    normalize_embeddings=True,
                    show_progress_bar=False,
                ).tolist()
            except Exception as exc:
                retrieval_mode = "lexical_fallback"
                log.warning("embedding_model_unavailable_using_lexical_fallback", extra={
                    "fields": {"error_type": type(exc).__name__, "correlation_id": correlation_id.get()}
                })
                span.set_attribute("retrieval.mode", retrieval_mode)
                ranked = {
                    item["id"]: item
                    for item in _lexical_candidates(collection, req.query, req.top_k, where)
                }
            else:
                query_args = {
                    "query_embeddings": vector,
                    "n_results": min(req.top_k, collection.count()),
                    "include": ["documents", "metadatas", "distances"],
                }
                if where:
                    query_args["where"] = where

                result = collection.query(**query_args)
                ranked = {
                    chunk_id: {"id": chunk_id, "document": text, "metadata": metadata or {}, "distance": distance}
                    for chunk_id, text, metadata, distance in zip(
                        result.get("ids", [[]])[0], result.get("documents", [[]])[0],
                        result.get("metadatas", [[]])[0], result.get("distances", [[]])[0],
                    )
                }
            for item in _explicit_clause_candidates(collection, req.query):
                ranked[item["id"]] = item

            hits = []
            for chunk_id, item in ranked.items():
                metadata = item["metadata"]
                try:
                    references = json.loads(metadata.get("references_json", "[]"))
                except (TypeError, json.JSONDecodeError):
                    references = []
                hits.append({
                    "chunk_id": chunk_id,
                    "text": item["document"],
                    "distance": item["distance"],
                    "retrieval_mode": retrieval_mode,
                    "provenance": {
                        key: metadata.get(key) for key in (
                            "document_id", "source_title", "source_role", "source_url", "source_file",
                            "source_sha256", "retrieved", "page", "chapter", "section", "clause_id",
                        )
                    },
                    "references": references,
                })
            span.set_attribute("retrieval.result_count", len(hits))
            span.set_attribute("retrieval.mode", retrieval_mode)
            response = {"query": req.query, "count": len(hits), "results": hits,
                        "retrieval_mode": retrieval_mode}
            if retrieval_mode == "vector":
                response_cache.put(key, response, TTL_SECONDS)
            return response
        except HTTPException:
            raise
        except Exception as exc:
            span.record_exception(exc)
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            SEARCH_DURATION.observe(time.perf_counter() - started)


@app.get("/clause/{clause_id}")
def clause(clause_id: str):
    collection = _get_collection()
    corpus_version = str((collection.metadata or {}).get("build_signature", ""))
    key = cache_key("clause", corpus_version, {"clause_id": clause_id})
    cached = response_cache.get(key, "clause")
    if cached is not None:
        return cached
    wanted = unquote(clause_id).strip().casefold()
    aliases = {
        "ch4": "chapter 4 - limitations as to use",
        "chapter 4": "chapter 4 - limitations as to use",
        "limitations as to use": "chapter 4 - limitations as to use",
    }
    wanted = aliases.get(wanted, wanted)
    data = collection.get(include=["documents", "metadatas"])
    matches = []
    for cid, document, meta in zip(data.get("ids", []), data.get("documents", []), data.get("metadatas", [])):
        meta = meta or {}
        identifiers = {str(meta.get("clause_id", "")).casefold(), str(meta.get("chapter", "")).casefold(), cid.casefold()}
        if wanted in identifiers:
            matches.append(_format_passage(cid, document, meta))
    if not matches:
        raise HTTPException(status_code=404, detail=f"Clause not found: {clause_id}")
    response = {"clause_id": clause_id, "count": len(matches), "clauses": matches}
    response_cache.put(key, response, TTL_SECONDS)
    return response


@app.post("/references")
def references(req: ReferenceRequest):
    term = re.sub(r"\s+", " ", req.term).strip()
    if not term:
        raise HTTPException(status_code=422, detail="term must not be empty")
    collection = _get_collection()
    corpus_version = str((collection.metadata or {}).get("build_signature", ""))
    key = cache_key("references", corpus_version, {"term": term.casefold()})
    cached = response_cache.get(key, "references")
    if cached is not None:
        return cached
    # Named references resolve to the chapter that defines the term.
    if "limitations as to use" in term.casefold():
        target = "CHAPTER 4 - Limitations as to Use"
        result = clause(target)
        response = {"term": term, "clauses": result["clauses"]}
        response_cache.put(key, response, TTL_SECONDS)
        return response
    found = collection.get(include=["documents", "metadatas"])
    matches = []
    needle = term.casefold()
    for cid, document, meta in zip(found.get("ids", []), found.get("documents", []), found.get("metadatas", [])):
        if needle in (document or "").casefold() or needle in str((meta or {}).get("references_json", "")).casefold():
            matches.append(_format_passage(cid, document, meta or {}))
    response = {"term": term, "clauses": matches[:20]}
    response_cache.put(key, response, TTL_SECONDS)
    return response


def _format_passage(chunk_id: str, document: str, metadata: dict) -> dict:
    try:
        refs = json.loads(metadata.get("references_json", "[]"))
    except (TypeError, json.JSONDecodeError):
        refs = []
    return {"chunk_id": chunk_id, "text": document, "clause_id": metadata.get("clause_id"),
            "page": metadata.get("page"), "chapter": metadata.get("chapter"), "section": metadata.get("section"),
            "document_id": metadata.get("document_id"), "source_title": metadata.get("source_title"),
            "source_file": metadata.get("source_file"), "source_sha256": metadata.get("source_sha256"),
            "references": refs}
