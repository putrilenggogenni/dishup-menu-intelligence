"""
parser.py -- Document parser for DishUp Menu Intelligence.

Pipeline per document:
  1. Read raw text lines (txt / pdf-extracted text)
  2. Split into field tokens (pipe, semicolon, tab)
  3. Normalise price -> integer IDR
  4. Standardise allergens from ingredient names
  5. Flag needs_review when required fields are missing/suspect
  6. Return list of validated JSON-ready dicts

Allergen mapping covers common Indonesian ingredients.
"""
from __future__ import annotations

import re
from typing import Any

# ── Price normalisation ───────────────────────────────────────────────────────

_PRICE_RE = re.compile(r"[Rr][Pp]\.?\s*", re.IGNORECASE)


def normalise_price(raw: str) -> int | None:
    cleaned = _PRICE_RE.sub("", raw).strip()
    cleaned = cleaned.replace(".", "").replace(",", "")
    try:
        v = int(cleaned)
        return v if v >= 0 else None
    except ValueError:
        return None


# ── Allergen mapping ──────────────────────────────────────────────────────────
#
# Keys are lowercase substrings found inside ingredient names.
# Values are canonical allergen names (match allergens table seeds in schema.sql).
#
_ALLERGEN_MAP: dict[str, str] = {
    # Gluten sources
    "tepung terigu":   "gluten",
    "tepung panko":    "gluten",
    "panko":           "gluten",
    "tepung roti":     "gluten",
    "roti":            "gluten",
    "mie":             "gluten",
    "mi ":             "gluten",
    "udon":            "gluten",
    "ramen":           "gluten",
    "pasta":           "gluten",
    "seitan":          "gluten",
    "terigu":          "gluten",
    "kulit gyoza":     "gluten",
    "gyoza":           "gluten",
    "tepung crispy":   "gluten",
    "tepung":          "wheat",   # generic flour -> wheat
    # Soy sources
    "kecap":           "soy",
    "kecap manis":     "soy",
    "kecap asin":      "soy",
    "teriyaki":        "soy",
    "tonkatsu":        "soy",
    "miso":            "soy",
    "tahu":            "soy",
    "tempe":           "soy",
    "edamame":         "soy",
    "tofu":            "soy",
    # Egg sources
    "telur":           "egg",
    "egg":             "egg",
    "mayonnaise":      "egg",
    "mayo":            "egg",
    # Milk / dairy
    "susu":            "milk",
    "milk":            "milk",
    "keju":            "milk",
    "cheese":          "milk",
    "mentega":         "milk",
    "butter":          "milk",
    "krim":            "milk",
    "cream":           "milk",
    "yogurt":          "milk",
    "es krim":         "milk",
    # Shellfish
    "udang":           "shellfish",
    "ebi":             "shellfish",
    "kepiting":        "shellfish",
    "cumi":            "shellfish",
    "kerang":          "shellfish",
    "lobster":         "shellfish",
    # Fish
    "salmon":          "fish",
    "tuna":            "fish",
    "ikan":            "fish",
    "anchovy":         "fish",
    "teri":            "fish",
    "dashi":           "fish",
    # Peanut
    "kacang tanah":    "peanut",
    "peanut":          "peanut",
    "selai kacang":    "peanut",
    # Sesame
    "wijen":           "sesame",
    "sesame":          "sesame",
    "minyak wijen":    "sesame",
    # Tree nut
    "almond":          "tree_nut",
    "cashew":          "tree_nut",
    "walnut":          "tree_nut",
    "hazelnut":        "tree_nut",
    "pistachio":       "tree_nut",
    "kacang mede":     "tree_nut",
    "kacang almond":   "tree_nut",
}


def detect_allergens(ingredients_list: list[str]) -> list[str]:
    """Return sorted unique canonical allergen names from an ingredients list."""
    found: set[str] = set()
    combined = " ".join(ingredients_list).lower()
    # Sort by key length descending so longer/more specific phrases match first
    for keyword, allergen in sorted(_ALLERGEN_MAP.items(), key=lambda x: -len(x[0])):
        if keyword in combined:
            found.add(allergen)
    return sorted(found)


# ── Field extraction ──────────────────────────────────────────────────────────

def _extract_field(text: str, *keys: str) -> str:
    for key in keys:
        m = re.search(rf"(?i){re.escape(key)}\s*:\s*(.+?)(?:\s*\||$)", text)
        if m:
            return m.group(1).strip()
    return ""


def _split_row(line: str) -> list[str]:
    if "|" in line:
        return [p.strip() for p in line.split("|")]
    if ";" in line:
        return [p.strip() for p in line.split(";")]
    return [p.strip() for p in line.split("\t")]


# ── Review status logic ───────────────────────────────────────────────────────

def _review_status(item: dict) -> tuple[str, list[str]]:
    """
    Determine review_status and list of reasons.
    Returns ('needs_review', [...reasons]) or ('ok', []).
    """
    reasons: list[str] = []
    if not item.get("name") or item["name"].startswith("Item #"):
        reasons.append("name_missing")
    if not item.get("price") or item["price"] == 0:
        reasons.append("price_missing_or_zero")
    if not item.get("category"):
        reasons.append("category_missing")
    if not item.get("ingredients_list"):
        reasons.append("ingredients_missing")
    if not item.get("po_day"):
        reasons.append("po_day_missing")
    status = "needs_review" if reasons else "ok"
    return status, reasons


# ── PDF text extraction helper ────────────────────────────────────────────────

def _extract_pdf_text(file_bytes: bytes) -> str:
    """
    Extract plain text from PDF bytes.
    Uses pypdf if available, otherwise raises ImportError with guidance.
    """
    try:
        import io
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(file_bytes))
        pages = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
        return "\n".join(pages)
    except ImportError:
        raise ImportError(
            "pypdf is required for PDF support. Install it: pip install pypdf"
        )


# ── Main parse function ───────────────────────────────────────────────────────

def parse_document(raw_text: str) -> list[dict[str, Any]]:
    """
    Parse a raw menu document (plain text) into a list of validated dicts.

    Each dict is JSON-ready with keys:
        name, price, category,
        ingredients_list (list[str]), allergens_list (list[str]),
        po_day, review_status, review_reasons,
        _raw_line (str)
    """
    items: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for lineno, line in enumerate(raw_text.splitlines(), 1):
        line = line.lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue

        parts = _split_row(line)
        if len(parts) < 2:
            continue

        # ── Name ─────────────────────────────────────────────────────────────
        name_part = parts[0]
        name_part = re.sub(r"^\d+[\.\)]\s*", "", name_part).strip()
        if not name_part and len(parts) > 1:
            name_part = parts[1]
        name = name_part or f"Item #{lineno}"

        # ── Price ─────────────────────────────────────────────────────────────
        price: int = 0
        for part in parts:
            candidate = part.strip()
            if re.search(r"(?i)(rp|rupiah)", candidate) or re.match(r"^\d[\d.,]*$", candidate):
                p = normalise_price(candidate)
                if p is not None:
                    price = p
                    break

        # ── Structured fields ─────────────────────────────────────────────────
        rest = " | ".join(parts)
        ingredients_raw = _extract_field(rest, "Bahan", "Ingredients", "Bahan-bahan")
        category = _extract_field(rest, "Kategori", "Category")
        po_day = _extract_field(rest, "PO", "Pre-Order", "Pre Order")

        # Split ingredients into a clean list
        if ingredients_raw:
            ingredients_list = [x.strip() for x in re.split(r"[,;/]", ingredients_raw) if x.strip()]
        else:
            ingredients_list = []

        # ── Allergen detection ────────────────────────────────────────────────
        allergens_list = detect_allergens(ingredients_list)

        # ── Deduplication ─────────────────────────────────────────────────────
        dedup_key = f"{name.lower()}|{po_day.lower()}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        # ── Review status ─────────────────────────────────────────────────────
        candidate_item = {
            "name": name,
            "price": price,
            "category": category,
            "ingredients_list": ingredients_list,
            "po_day": po_day,
        }
        review_status, review_reasons = _review_status(candidate_item)

        items.append({
            "name": name,
            "price": price,
            "category": category,
            "ingredients_list": ingredients_list,
            "allergens_list": allergens_list,
            "po_day": po_day,
            "review_status": review_status,
            "review_reasons": review_reasons,
            "_raw_line": line,
        })

    return items


def parse_bytes(file_bytes: bytes, filename: str) -> list[dict[str, Any]]:
    """
    Entry point: accept raw bytes + filename, return parsed items.
    Handles .txt/.md (UTF-8) and .pdf.
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
    if ext == "pdf":
        raw_text = _extract_pdf_text(file_bytes)
    else:
        raw_text = file_bytes.decode("utf-8", errors="replace")
    return parse_document(raw_text)
