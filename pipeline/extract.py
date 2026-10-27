"""
pipeline/extract.py
===================
Stage 1 of the DishUp ETL pipeline: Document -> Structured JSON.

Reads a raw menu document (plain text or PDF) and extracts one
structured dict per product line. The output is a list of dicts
ready for the validate stage.

Supported input formats
-----------------------
- Plain text (.txt, .md, .csv)  — UTF-8, one product per line
- PDF (.pdf)                     — requires `pip install pypdf`

Supported row separators
------------------------
  |  pipe      e.g.  Chicken Katsu | Rp35.000 | Bahan: ... | PO: Jumat
  ;  semicolon e.g.  Chicken Katsu; Rp35.000; Bahan: ...; PO: Jumat
  \\t tab

Expected field labels (case-insensitive)
-----------------------------------------
  Bahan / Ingredients   -> ingredients
  Kategori / Category   -> category
  PO / Pre-Order        -> po_day

Price formats handled
---------------------
  Rp35.000  |  Rp 42.000  |  48000  |  35,000
"""

from __future__ import annotations

import io
import re
from typing import Any


# ---------------------------------------------------------------------------
# Price helpers
# ---------------------------------------------------------------------------

# Matches the "Rp" prefix in various forms: "Rp", "RP.", "rp ", etc.
_RP_PREFIX = re.compile(r"[Rr][Pp]\.?\s*")


def _normalise_price(raw: str) -> int:
    """
    Convert a raw price string to an integer IDR value.

    Examples
    --------
    >>> _normalise_price("Rp35.000")   -> 35000
    >>> _normalise_price("Rp 42.000") -> 42000
    >>> _normalise_price("48000")      -> 48000
    >>> _normalise_price("35,000")     -> 35000

    Returns 0 if the string cannot be parsed.
    """
    cleaned = _RP_PREFIX.sub("", raw).strip()
    # Remove thousand-separator dots and commas
    cleaned = cleaned.replace(".", "").replace(",", "")
    try:
        value = int(cleaned)
        return value if value >= 0 else 0
    except ValueError:
        return 0


# ---------------------------------------------------------------------------
# Row splitting
# ---------------------------------------------------------------------------

def _split_row(line: str) -> list[str]:
    """Split one document line into token segments."""
    if "|" in line:
        return [p.strip() for p in line.split("|")]
    if ";" in line:
        return [p.strip() for p in line.split(";")]
    # Fallback: tab-separated
    return [p.strip() for p in line.split("\t")]


# ---------------------------------------------------------------------------
# Field extraction
# ---------------------------------------------------------------------------

def _extract_labelled_field(text: str, *label_keys: str) -> str:
    """
    Extract the value after a labelled field in the format "Key: value".
    Stops at the next pipe separator or end of string.
    Tries each key in order; returns empty string if none found.
    """
    for key in label_keys:
        pattern = rf"(?i){re.escape(key)}\s*:\s*(.+?)(?:\s*\||$)"
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return ""


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------

def _extract_pdf_text(file_bytes: bytes) -> str:
    """
    Convert PDF bytes to plain text using pypdf.
    Raises ImportError with install guidance if pypdf is not installed.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError(
            "PDF support requires pypdf. Install it with: pip install pypdf"
        )

    reader = PdfReader(io.BytesIO(file_bytes))
    pages: list[str] = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)


# ---------------------------------------------------------------------------
# Core extraction logic
# ---------------------------------------------------------------------------

def extract_document(raw_text: str) -> list[dict[str, Any]]:
    """
    Parse a plain-text menu document into a list of raw product dicts.

    Each output dict contains:
        name            str   — product name
        price           int   — price in IDR (0 if unknown)
        category        str   — category label (empty if not found)
        ingredients_raw str   — raw ingredients string from document
        ingredients     list  — split list of ingredient strings
        po_day          str   — pre-order day (empty if not found)
        _raw_line       str   — original source line (for debugging)

    Duplicate rows (same name + po_day) are silently dropped.
    Comment lines (starting with #) and blank lines are skipped.
    """
    items: list[dict[str, Any]] = []
    seen: set[str] = set()  # deduplication keys: "name|po_day"

    for lineno, line in enumerate(raw_text.splitlines(), start=1):
        # Strip UTF-8 BOM and surrounding whitespace
        line = line.lstrip("\ufeff").strip()

        # Skip blank lines and comment lines
        if not line or line.startswith("#"):
            continue

        parts = _split_row(line)

        # Need at least two segments to form a meaningful row
        if len(parts) < 2:
            continue

        # ── Extract: Name ──────────────────────────────────────────────────
        # First segment; strip a leading numeric index like "1." or "2)"
        name_raw = re.sub(r"^\d+[\.\)]\s*", "", parts[0]).strip()
        if not name_raw and len(parts) > 1:
            name_raw = parts[1]
        name = name_raw or f"Item #{lineno}"

        # ── Extract: Price ─────────────────────────────────────────────────
        # Search all segments for something that looks like a price value
        price = 0
        for segment in parts:
            seg = segment.strip()
            looks_like_price = (
                re.search(r"(?i)(rp|rupiah)", seg) or
                re.match(r"^\d[\d.,]*$", seg)
            )
            if looks_like_price:
                candidate = _normalise_price(seg)
                if candidate > 0:
                    price = candidate
                    break

        # ── Extract: Labelled fields ───────────────────────────────────────
        joined = " | ".join(parts)
        ingredients_raw = _extract_labelled_field(
            joined, "Bahan", "Ingredients", "Bahan-bahan"
        )
        category = _extract_labelled_field(joined, "Kategori", "Category")
        po_day   = _extract_labelled_field(joined, "PO", "Pre-Order", "Pre Order")

        # Split ingredients string into individual items
        ingredients = (
            [x.strip() for x in re.split(r"[,;/]", ingredients_raw) if x.strip()]
            if ingredients_raw else []
        )

        # ── Deduplication ──────────────────────────────────────────────────
        dedup_key = f"{name.lower()}|{po_day.lower()}"
        if dedup_key in seen:
            continue   # silently skip duplicate rows
        seen.add(dedup_key)

        items.append({
            "name":            name,
            "price":           price,
            "category":        category,
            "ingredients_raw": ingredients_raw,
            "ingredients":     ingredients,
            "po_day":          po_day,
            "_raw_line":       line,
        })

    return items


# ---------------------------------------------------------------------------
# Public entry point (bytes + filename)
# ---------------------------------------------------------------------------

def extract_from_bytes(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """
    Top-level function: accept raw file bytes and a filename string.
    Routes to PDF or plain-text extraction based on file extension.

    Parameters
    ----------
    file_bytes : bytes
        Raw file content.
    filename : str
        Original filename (used to detect .pdf extension).

    Returns
    -------
    list[dict]
        List of raw extracted product dicts (not yet validated).
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"

    if ext == "pdf":
        raw_text = _extract_pdf_text(file_bytes)
    else:
        # Decode as UTF-8; replace undecodable bytes with the replacement char
        raw_text = file_bytes.decode("utf-8", errors="replace")

    return extract_document(raw_text)
