"""Stage 1: embed with a local sentence-transformers model (name comes from EMBEDDING_MODEL) and upsert into Chroma.

Store the embedding model name and version in each chunk's metadata so a model change re-embeds
only the affected chunks.
"""


def upsert_chunks(chunks: list) -> int:
    raise NotImplementedError("Stage 1")
