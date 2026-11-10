"""Corpus pipeline CLI: read -> parse -> chunk -> embed -> index. Must be idempotent (D2).

Idempotency plan: derive each chunk id from (document id, clause id, content hash) and upsert,
so running twice neither duplicates nor corrupts the index. Record when the index was built,
the document versions, the chunk count and the throughput, and expose them as metrics.
"""
import argparse


def main() -> None:
    parser = argparse.ArgumentParser(prog="ingestion")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="parse, chunk, embed and index every document in corpus/manifest.yaml")
    sub.add_parser("status", help="show index freshness: build time, document versions, chunk count")
    args = parser.parse_args()
    raise SystemExit(f"'{args.command}' is not implemented yet (Stage 1).")


if __name__ == "__main__":
    main()
