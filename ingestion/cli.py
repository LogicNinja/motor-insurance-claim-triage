"""Corpus pipeline CLI: parse, chunk, embed, index and report freshness."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import yaml

from ingestion.chunk import chunk_pages
from ingestion.index import build_signature, get_collection, upsert_chunks
from ingestion.parse import parse_pdf


def _corpus_dir() -> Path:
    return Path(os.getenv("CORPUS_DIR", "corpus")).resolve()


def _load_manifest(corpus_dir: Path) -> list[dict]:
    manifest_path = corpus_dir / "manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    documents = data.get("documents", [])
    if not isinstance(documents, list):
        raise ValueError("manifest.yaml must contain a documents list")
    return documents


def build() -> int:
    corpus_dir = _corpus_dir()
    documents = _load_manifest(corpus_dir)
    selected = [doc for doc in documents if doc.get("indexed") is True]

    if not selected:
        print("No documents marked indexed: true.")
        return 1

    chunks: list[dict] = []
    document_versions: dict[str, str] = {}

    for doc in selected:
        document_id = doc["id"]
        relative_path = doc.get("local_path")
        if not relative_path:
            raise ValueError(f"{document_id}: missing local_path")

        for required in ("title", "role", "retrieved"):
            if not doc.get(required):
                raise ValueError(f"{document_id}: missing provenance field {required}")
        if not doc.get("source_url") and not doc.get("source_filename") and not relative_path:
            raise ValueError(f"{document_id}: source_url or source filename is required")

        pdf_path = (corpus_dir / relative_path).resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"{document_id}: PDF not found: {pdf_path}")

        actual_sha256 = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
        declared_sha256 = str(doc.get("sha256", "")).lower()
        if declared_sha256 and declared_sha256 != actual_sha256:
            raise ValueError(
                f"{document_id}: SHA-256 differs from manifest; update provenance after review "
                f"(manifest={declared_sha256}, actual={actual_sha256})"
            )

        print(f"Parsing: {doc.get('title', document_id)}")
        pages = parse_pdf(str(pdf_path))
        document_chunks = chunk_pages(pages, document_id)

        for item in document_chunks:
            item.update({
                "source_title": doc.get("title", document_id),
                "source_role": doc.get("role", ""),
                "source_url": doc.get("source_url", ""),
                "source_file": relative_path,
                "source_sha256": actual_sha256,
                "retrieved": doc.get("retrieved", ""),
                "pdf_creation_date": doc.get("pdf_creation_date", ""),
                "source_type": doc.get("source_type", "pdf"),
            })

        chunks.extend(document_chunks)
        document_versions[document_id] = actual_sha256

        print(
            f"  pages={len(pages)} chunks={len(document_chunks)} "
            f"role={doc.get('role', 'unspecified')}"
        )

    model_name = os.getenv(
        "EMBEDDING_MODEL",
        "sentence-transformers/all-MiniLM-L6-v2",
    )
    signature = build_signature(chunks, model_name, document_versions)

    result = upsert_chunks(
        chunks=chunks,
        model_name=model_name,
        document_versions=document_versions,
        signature=signature,
    )

    print("\nCorpus build result")
    print("-------------------")
    print(f"Indexed documents : {len(selected)}")
    print(f"Excluded documents: {len(documents) - len(selected)}")
    print(f"Chunks            : {result['chunk_count']}")
    print(f"Changed           : {result['changed']}")
    print(f"Throughput        : {result['throughput']:.2f} chunks/sec")
    print(f"Build timestamp   : {result['build_time']}")
    print(f"Chroma collection : motor_policy_chunks")

    if not result["changed"]:
        print("No changes detected; existing index left untouched.")

    return 0


def status() -> int:
    collection = get_collection()
    metadata = collection.metadata or {}

    stored_versions = metadata.get("document_versions_json", "{}")
    try:
        stored_versions = json.loads(stored_versions)
    except (TypeError, json.JSONDecodeError):
        stored_versions = {}

    current_versions = {}
    for doc in _load_manifest(_corpus_dir()):
        if doc.get("indexed") is not True:
            continue
        path = _corpus_dir() / doc.get("local_path", "")
        if path.is_file():
            current_versions[doc["id"]] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            current_versions[doc["id"]] = "missing"
    fresh = bool(stored_versions) and current_versions == stored_versions

    print("Corpus index status")
    print("-------------------")
    print(f"Collection : {collection.name}")
    print(f"Chunk count: {collection.count()}")
    print(f"Model      : {metadata.get('embedding_model', 'not built')}")
    print(f"Build time : {metadata.get('build_time', 'not built')}")
    print(f"Throughput : {metadata.get('throughput_chunks_per_second', 'n/a')}")
    print(f"Fresh      : {fresh}")
    print(f"Signature  : {metadata.get('build_signature', 'not built')}")

    print("Documents:")
    for document_id, version in current_versions.items():
        indexed = stored_versions.get(document_id, "not indexed")
        print(f"  {document_id}: {'current' if indexed == version else 'stale/missing'}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="ingestion")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="parse, embed and index indexed corpus documents")
    sub.add_parser("status", help="show index freshness and document versions")
    args = parser.parse_args()

    try:
        exit_code = build() if args.command == "build" else status()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
