"""Local embeddings and idempotent Chroma indexing."""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import datetime, timezone

import chromadb
from sentence_transformers import SentenceTransformer


COLLECTION_NAME = "motor_policy_chunks"
DEFAULT_EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def get_collection():
    attempts = max(1, int(os.getenv("CHROMA_CONNECT_ATTEMPTS", "6")))
    delay = max(0.1, float(os.getenv("CHROMA_CONNECT_DELAY_SECONDS", "2")))
    last_error = None
    for attempt in range(attempts):
        try:
            client = chromadb.HttpClient(
                host=os.getenv("CHROMA_HOST", "chroma"),
                port=int(os.getenv("CHROMA_PORT", "8000")),
            )
            return client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        except Exception as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(delay)
    raise RuntimeError(f"Chroma unavailable after {attempts} attempts: {last_error}") from last_error


def get_embedder(model_name: str | None = None):
    name = model_name or os.getenv("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL
    if not name:
        raise ValueError("EMBEDDING_MODEL is not configured")
    return SentenceTransformer(name)


def build_signature(chunks: list[dict], model_name: str, document_versions: dict) -> str:
    payload = {
        "model": model_name,
        "documents": document_versions,
        "chunks": [
            (item["chunk_id"], item["text"], item.get("references", []),
             {key: item.get(key) for key in (
                 "chapter", "section", "clause_id", "page", "source_title",
                 "source_role", "source_url", "source_file", "source_sha256", "retrieved",
                 "pdf_creation_date", "source_type",
             )})
            for item in sorted(chunks, key=lambda value: value["chunk_id"])
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def upsert_chunks(
    chunks: list[dict],
    model_name: str,
    document_versions: dict,
    signature: str,
) -> dict:
    collection = get_collection()
    existing_metadata = collection.metadata or {}

    if (
        existing_metadata.get("build_signature") == signature
        and collection.count() == len(chunks)
    ):
        return {
            "changed": False,
            "chunk_count": len(chunks),
            "throughput": 0.0,
            "build_time": existing_metadata.get("build_time"),
        }

    started = time.perf_counter()
    embedder = get_embedder(model_name)

    ids = [item["chunk_id"] for item in chunks]
    documents = [item["text"] for item in chunks]
    metadatas = []

    for item in chunks:
        metadata = {
            key: item.get(key, "")
            for key in (
                "document_id", "chapter", "section", "clause_id", "page",
                "source_title", "source_role", "source_url", "source_file",
                "source_sha256", "retrieved", "pdf_creation_date", "source_type",
            )
        }
        metadata["references_json"] = json.dumps(
            item.get("references", [])
        )
        metadatas.append(metadata)

    existing = collection.get(include=["metadatas"])
    keep_ids = set(ids)
    previous_versions = existing_metadata.get("document_versions_json", "{}")
    try:
        previously_indexed = set(json.loads(previous_versions))
    except (TypeError, json.JSONDecodeError):
        previously_indexed = set()
    indexed_document_ids = set(document_versions) | previously_indexed

    stale_ids = [
        existing_id
        for existing_id, metadata in zip(
            existing.get("ids", []),
            existing.get("metadatas", []),
        )
        if metadata
        and metadata.get("document_id") in indexed_document_ids
        and existing_id not in keep_ids
    ]

    if stale_ids:
        collection.delete(ids=stale_ids)

    batch_size = 64

    for start in range(0, len(chunks), batch_size):
        end = start + batch_size
        vectors = embedder.encode(
            documents[start:end],
            normalize_embeddings=True,
            show_progress_bar=False,
        ).tolist()

        collection.upsert(
            ids=ids[start:end],
            documents=documents[start:end],
            metadatas=metadatas[start:end],
            embeddings=vectors,
        )

    elapsed = max(time.perf_counter() - started, 0.000001)
    throughput = len(chunks) / elapsed
    build_time = time.time()

    # Do not include hnsw:space here. Chroma makes it immutable.
    updated_metadata = {
        key: value
        for key, value in existing_metadata.items()
        if key != "hnsw:space"
    }
    updated_metadata.update({
        "build_signature": signature,
        "build_time": build_time,
        "chunk_count": len(chunks),
        "document_versions_json": json.dumps(
            document_versions, sort_keys=True
        ),
        "embedding_model": model_name,
        "throughput_chunks_per_second": throughput,
        "last_build_utc": datetime.now(timezone.utc).isoformat(),
    })

    collection.modify(metadata=updated_metadata)

    return {
        "changed": True,
        "chunk_count": len(chunks),
        "throughput": throughput,
        "build_time": build_time,
    }
