"""Chunking strategy: one chunk per clause, with chapter, clause id and page as metadata.

A short clause such as Chapter 4 must stay its own chunk. Named references inside a clause
(for example General Exception III(a) pointing to 'Limitations as to Use') are extracted so the
retrieval service can resolve them deterministically instead of hoping similarity finds them.
"""
import re

_REFERENCE = re.compile("[\u2018\u201c\"]([A-Z][A-Za-z' ]{3,60}?)[\u2019\u201d\"]")


def extract_references(text: str) -> list:
    """Return quoted, capitalised terms in order, without duplicates."""
    seen = []
    for match in _REFERENCE.findall(text or ""):
        if match not in seen:
            seen.append(match)
    return seen


def chunk_pages(pages: list, document_id: str) -> list:
    raise NotImplementedError(
        "Stage 1: return [{'chunk_id', 'document_id', 'chapter', 'clause_id', 'page', 'text', 'references'}, ...]"
    )
