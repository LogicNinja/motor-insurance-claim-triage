
"""Layout-aware PDF parsing with page provenance."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

import pdfplumber


LIGATURES = str.maketrans({
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
})

FOOTER_PATTERNS = [
    re.compile(r"^\s*page\s+\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*private car package policy wording\s*$", re.IGNORECASE),
]


def _normalize_text(text: str) -> str:
    text = (text or "").translate(LIGATURES)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if any(pattern.match(line) for pattern in FOOTER_PATTERNS):
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", text)
    return text.strip()



def _find_column_start(words: list[dict], midpoint: float):
    """Find the first aligned row clearly inside both text columns."""

    rows = []

    for word in sorted(words, key=lambda item: item["top"]):
        matching_row = None

        for row in rows:
            if abs(row["top"] - word["top"]) <= 3:
                matching_row = row
                break

        if matching_row is None:
            matching_row = {"top": word["top"], "words": []}
            rows.append(matching_row)

        matching_row["words"].append(word)

    for row in rows:
        left = [
            w for w in row["words"]
            if w["x0"] < midpoint - 100
        ]
        right = [
            w for w in row["words"]
            if w["x0"] > midpoint + 10
        ]

        if left and right:
            return min(w["top"] for w in row["words"])

    return None



def _extract_two_columns(page, words: list[dict], midpoint: float) -> str:
    """Extract a full-width header followed by complete text columns."""

    body_top = _find_column_start(words, midpoint)

    if body_top is None:
        return page.extract_text(layout=True) or ""

    header = page.crop(
        (0, 0, page.width, max(0, body_top - 2)),
        strict=False,
    ).extract_text(
        layout=False,
        x_tolerance=3,
        y_tolerance=3,
    ) or ""

    left = page.crop(
        (0, body_top, midpoint, page.height),
        strict=False,
    ).extract_text(
        layout=False,
        x_tolerance=3,
        y_tolerance=3,
    ) or ""

    right = page.crop(
        (midpoint, body_top, page.width, page.height),
        strict=False,
    ).extract_text(
        layout=False,
        x_tolerance=3,
        y_tolerance=3,
    ) or ""

    return "\n\n".join(
        part for part in (header, left, right) if part.strip()
    )



def _extract_page_in_reading_order(page) -> str:
    """Select extraction based on page layout."""

    words = page.extract_words(
        x_tolerance=3,
        y_tolerance=3,
        keep_blank_chars=False,
    )

    if not words:
        return ""

    midpoint = page.width / 2

    left_words = [w for w in words if w["x1"] <= midpoint]
    right_words = [w for w in words if w["x0"] >= midpoint]
    crossing_words = [
        w for w in words
        if w["x0"] < midpoint < w["x1"]
    ]

    # Table-like/full-width page: preserve its layout.
    if len(crossing_words) >= 10:
        return page.extract_text(
            layout=True,
            x_tolerance=3,
            y_tolerance=3,
        ) or ""

    if min(len(left_words), len(right_words)) < 20:
        return page.extract_text(
            layout=False,
            x_tolerance=3,
            y_tolerance=3,
        ) or ""

    return _extract_two_columns(page, words, midpoint)


def _remove_repeated_footer(pages: list[dict]) -> list[dict]:
    counts = Counter()

    for page in pages:
        for line in page["text"].splitlines()[-3:]:
            candidate = re.sub(r"\s+", " ", line).strip()
            if 3 <= len(candidate) <= 100:
                counts[candidate] += 1

    threshold = max(3, int(len(pages) * 0.5))
    repeated = {
        line for line, count in counts.items()
        if count >= threshold
    }

    for page in pages:
        lines = page["text"].splitlines()
        cleaned = []

        for index, line in enumerate(lines):
            candidate = re.sub(r"\s+", " ", line).strip()
            is_bottom_line = index >= max(0, len(lines) - 3)

            if is_bottom_line and candidate in repeated:
                continue

            cleaned.append(line)

        page["text"] = "\n".join(cleaned).strip()

    return pages


def parse_pdf(path: str) -> list[dict]:
    pdf_path = Path(path)

    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    pages = []

    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = _extract_page_in_reading_order(page)

            pages.append({
                "page": page_number,
                "text": _normalize_text(text),
            })

    return _remove_repeated_footer(pages)
