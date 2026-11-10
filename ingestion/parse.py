"""Parsing notes from the corpus review:
- The ICICI wording is two-column, so plain extraction interleaves columns: use a layout-aware parser.
- A footer repeats on every page: strip it before chunking.
- Ligature characters such as the 'fi' glyph appear in the text layer: normalise them.
- Keep the PDF page number with every block (needed for citations).
"""


def parse_pdf(path: str) -> list:
    raise NotImplementedError("Stage 1: return [{'page': int, 'text': str}, ...]")
