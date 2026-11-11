"""Retrieval API backed by the shared Chroma collection and local embeddings."""

from __future__ import annotations

import json
import os
import re

import chromadb
from fastapi import FastAPI, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer


COLLECTION_NAME = "motor_policy_chunks"

app = FastAPI(title="Retrieval service", version="0.2.0")

_embedder = None
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
    global _embedder
    if _embedder is None:
        model_name = os.getenv(
            "EMBEDDING_MODEL",
            "sentence-transformers/all-MiniLM-L6-v2",
        )
        _embedder = SentenceTransformer(model_name)
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
        return (
            "# HELP corpus_chunks_total Number of chunks in the corpus index\n"
            "# TYPE corpus_chunks_total gauge\n"
            f"corpus_chunks_total {collection.count()}\n"
            "# HELP corpus_last_build_timestamp_seconds Unix timestamp of last build\n"
            "# TYPE corpus_last_build_timestamp_seconds gauge\n"
            f"corpus_last_build_timestamp_seconds "
            f"{metadata.get('build_time', 0)}\n"
            "# HELP corpus_index_throughput_chunks_per_second Last build throughput\n"
            "# TYPE corpus_index_throughput_chunks_per_second gauge\n"
            f"corpus_index_throughput_chunks_per_second "
            f"{metadata.get('throughput_chunks_per_second', 0)}\n"
            "# HELP corpus_index_built Whether a corpus build has completed\n"
            "# TYPE corpus_index_built gauge\n"
            f"corpus_index_built {1 if metadata.get('build_signature') else 0}\n"
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/search")
def search(req: SearchRequest):
    try:
        collection = _get_collection()
        if collection.count() == 0:
            raise HTTPException(status_code=503, detail="Corpus index is empty")

        vector = _get_embedder().encode(
            [req.query],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

        query_args = {
            "query_embeddings": vector,
            "n_results": min(req.top_k, collection.count()),
            "include": ["documents", "metadatas", "distances"],
        }

        where = _make_where(req.filters)
        if where:
            query_args["where"] = where

        result = collection.query(**query_args)

        ranked = {
            chunk_id: {"id": chunk_id, "document": text, "metadata": metadata or {}, "distance": distance}
            for chunk_id, text, metadata, distance in zip(
                result.get("ids", [[]])[0],
                result.get("documents", [[]])[0],
                result.get("metadatas", [[]])[0],
                result.get("distances", [[]])[0],
            )
        }
        for item in _explicit_clause_candidates(collection, req.query):
            ranked[item["id"]] = item

        hits = []
        for chunk_id, item in ranked.items():
            text = item["document"]
            metadata = item["metadata"]
            distance = item["distance"]
            try:
                references = json.loads(metadata.get("references_json", "[]"))
            except (TypeError, json.JSONDecodeError):
                references = []

            hits.append({
                "chunk_id": chunk_id,
                "text": text,
                "distance": distance,
                "provenance": {
                    "document_id": metadata.get("document_id"),
                    "source_title": metadata.get("source_title"),
                    "source_role": metadata.get("source_role"),
                    "source_url": metadata.get("source_url"),
                    "source_file": metadata.get("source_file"),
                    "source_sha256": metadata.get("source_sha256"),
                    "retrieved": metadata.get("retrieved"),
                    "page": metadata.get("page"),
                    "chapter": metadata.get("chapter"),
                    "section": metadata.get("section"),
                    "clause_id": metadata.get("clause_id"),
                },
                "references": references,
            })

        return {"query": req.query, "count": len(hits), "results": hits}

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/clause/{clause_id}")
def clause(clause_id: str):
    raise HTTPException(status_code=501, detail="Not implemented: Stage 4")


@app.post("/references")
def references(req: ReferenceRequest):
    raise HTTPException(status_code=501, detail="Not implemented: Stage 4")
