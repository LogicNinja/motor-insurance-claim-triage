"""Clause-oriented chunking with hierarchical IDs and page provenance."""

from __future__ import annotations

import hashlib
import re


CHAPTER_RE = re.compile(r"^\s*(CHAPTER\s+\d+)\s*[-:–]?\s*(.*)$", re.I)
SECTION_RE = re.compile(
    r"^\s*SECTION\s+([IVXLC]+)\s*(?:\(([AB])\)|([AB]))?\.?\s*(.*)$",
    re.I,
)
GENERAL_EXCEPTIONS_RE = re.compile(r"^\s*(?:\d+\.\s*)?GENERAL\s+EXCEPTIONS\b", re.I)
LIMITATIONS_RE = re.compile(r"^\s*LIMITATIONS\s+AS\s+TO\s+USE\b", re.I)
NUMBERED_RE = re.compile(r"^\s*(\d{1,2})\.\s+(.+)$")
ROMAN_RE = re.compile(r"^\s*((?:X{1,3}|IX|IV|V|V?I{1,3}))\.\s+(.+)$", re.I)
LETTER_RE = re.compile(r"^\s*\(?([a-z])\)?\.\s+(.+)$", re.I)

KNOWN_REFERENCES = (
    "Limitations as to Use",
    "General Exceptions",
    "Section I (A)",
    "Section I (B)",
    "Section II",
    "Section III",
    "Chapter 4",
    "Chapter 2",
)


def extract_references(text: str) -> list[str]:
    """Extract named references in first-seen order."""
    found: list[str] = []
    patterns = [
        r"General\s+Exceptions?\s+(?:clause\s+)?[IVXLC]+\s*\([a-z]\)",
        r"Section\s+[IVXLC]+\s*\([A-B]\)",
        r"Chapter\s+\d+(?:\s*[-:]\s*[^.;,\n]{2,60})?",
        r"Limitations\s+as\s+to\s+Use",
    ]
    matches = []
    for pattern in patterns:
        matches.extend((m.start(), m.group(0)) for m in re.finditer(pattern, text or "", re.I))
    for _, match in sorted(matches):
        value = re.sub(r"\s+", " ", match).strip()
        if value.casefold() not in {item.casefold() for item in found}:
            found.append(value)

    lowered = (text or "").casefold()
    for reference in KNOWN_REFERENCES:
        if reference.casefold() in lowered and reference.casefold() not in {
            item.casefold() for item in found
        }:
            found.append(reference)

    return found


def _stable_id(document_id: str, clause_id: str, page: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    safe_clause = re.sub(r"[^a-zA-Z0-9_-]+", "-", clause_id).strip("-")
    return f"{document_id}:{safe_clause or 'body'}:p{page}:{digest}"


def chunk_pages(pages: list, document_id: str) -> list[dict]:
    """Create page-scoped chunks while retaining clause hierarchy."""
    chunks: list[dict] = []
    chapter = "Unclassified"
    section = ""
    clause_id = "preamble"
    clause_parent_id = "preamble"
    current_page = None
    buffer: list[str] = []

    def flush() -> None:
        nonlocal buffer

        text = "\n".join(buffer).strip()
        buffer = []

        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) < 20 or current_page is None:
            return

        chunk_chapter = chapter
        chunk_clause = clause_id
        if (document_id == "icici-pc-wording"
                and "assurance on the quality of workmanship" in compact.casefold()):
            chunk_chapter = "Chapter 7 - Add-on for Private Car Package Insurance Policy"
            chunk_clause = "repair quality assurance"
        elif (document_id == "icici-submit-track"
                and "assurance on the quality of workmanship" in compact.casefold()):
            chunk_chapter = "Claims service assurance"
            chunk_clause = "service assurance"

        chunks.append({
            "chunk_id": _stable_id(document_id, chunk_clause, current_page, compact),
            "document_id": document_id,
            "chapter": chunk_chapter,
            "section": section,
            "clause_id": chunk_clause,
            "page": current_page,
            "text": compact,
            "references": extract_references(compact),
        })

    for page_data in pages:
        page_number = int(page_data["page"])
        if current_page is not None:
            flush()
        current_page = page_number

        # The Citizen Charter PDF is a table, not a clause-numbered policy.
        # Give its page-scoped timeline rows a stable, searchable heading.
        if document_id == "icici-citizen-charter":
            chapter = "Claims Turnaround Times"
            section = ""
            clause_id = "turnaround times"
            clause_parent_id = clause_id

        for raw_line in (page_data.get("text") or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue

            chapter_match = CHAPTER_RE.match(line)
            if chapter_match:
                flush()
                chapter = re.sub(r"\s+", " ", line)
                section = ""
                clause_id = chapter
                clause_parent_id = chapter
                buffer.append(line)
                continue

            if GENERAL_EXCEPTIONS_RE.match(line):
                flush()
                clause_id = "General Exceptions"
                clause_parent_id = clause_id
                buffer.append(line)
                continue

            if LIMITATIONS_RE.match(line):
                flush()
                clause_id = "Chapter 4: Limitations as to Use"
                clause_parent_id = clause_id
                buffer.append(line)
                continue

            section_match = SECTION_RE.match(line)
            if section_match:
                flush()
                roman = section_match.group(1).upper()
                letter = section_match.group(2) or section_match.group(3)
                section = f"Section {roman}"
                if letter:
                    section += f"({letter.upper()})"
                clause_id = section
                clause_parent_id = section
                # This PDF prints the opening own-damage clause inline with
                # the section heading. Separate it to preserve hierarchy.
                inline_clause = re.search(r"\b1\.\s+The Company\b", line, re.I)
                if inline_clause:
                    buffer.append(line[:inline_clause.start()].strip())
                    flush()
                    clause_id = f"{section} clause 1"
                    clause_parent_id = clause_id
                    buffer.append(line[inline_clause.start():].strip())
                else:
                    buffer.append(line)
                continue

            if (
                section in ("Section I(A)", "Section I(B)")
                and clause_id == section
                and re.match(r"^1\.\s+The Company\b", line, re.I)
            ):
                flush()
                clause_id = f"{section} clause 1"
                clause_parent_id = clause_id
                buffer.append(line)
                continue

            numbered = NUMBERED_RE.match(line)
            if numbered and (numbered.group(2).isupper() or "GENERAL EXCEPTIONS" in numbered.group(2).upper()):
                flush()
                number = numbered.group(1)
                title = numbered.group(2).strip()
                if "GENERAL EXCEPTIONS" in title.upper():
                    clause_id = "General Exceptions"
                else:
                    clause_id = f"{section + ' ' if section else ''}clause {number}"
                clause_parent_id = clause_id
                buffer.append(line)
                continue

            roman_match = ROMAN_RE.match(line)
            if roman_match:
                flush()
                roman = roman_match.group(1).upper()
                if clause_parent_id == "General Exceptions":
                    clause_id = f"General Exceptions {roman}"
                elif section in ("Section I(A)", "Section I(B)") and clause_parent_id.endswith("clause 1"):
                    clause_id = f"{clause_parent_id} item {roman}"
                elif "clause " in clause_parent_id.lower():
                    clause_id = f"{clause_parent_id} item {roman}"
                else:
                    clause_id = f"{clause_parent_id} {roman}"
                buffer.append(line)
                continue

            letter_match = LETTER_RE.match(line)
            if letter_match and len(line) > 25:
                flush()
                letter = letter_match.group(1).lower()
                clause_id = f"{clause_id}({letter})"
                buffer.append(line)
                continue

            buffer.append(line)

    flush()
    return chunks
