"""
pipeline/validate.py
====================
Stage 2 of the DishUp ETL pipeline: Clean + Validate + Flag.

Takes the raw extracted dicts from extract.py and applies:

1. **Price validation**   — already an int from extract; 0 means unknown.
2. **Allergen detection** — maps ingredient substrings to canonical allergen
                            names (gluten, egg, soy, milk, shellfish, fish,
                            peanut, sesame, tree_nut, wheat).
3. **Field normalisation** — strip whitespace, default empty strings.
4. **Review-status logic** — sets ``review_status`` to ``"needs_review"``
                             when any required field is missing/zero,
                             otherwise ``"ok"``.
5. **Review reasons**     — a JSON-serialisable list of strings explaining
                            why the item was flagged (e.g. ``"price_missing"``).

Output format (one dict per product)
-------------------------------------
{
    "name":           str,
    "price":          int,          # IDR, 0 = unknown
    "category":       str,
    "ingredients":    list[str],    # individual ingredient strings
    "allergens":      list[str],    # canonical allergen names
    "po_day":         str,
    "review_status":  "ok" | "needs_review",
    "review_reasons": list[str],    # empty when review_status == "ok"
}
"""

from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Allergen keyword mapping
# ---------------------------------------------------------------------------
#
# Keys   : lowercase substrings that may appear inside ingredient names.
#          Sorted by length (longest first) at runtime to avoid shorter keys
#          shadowing more-specific longer ones.
# Values : canonical allergen names that match the `allergens` DB table.
#
_ALLERGEN_MAP: dict[str, str] = {
    # ── Gluten sources ────────────────────────────────────────────────────
    "tepung terigu":  "gluten",    # wheat flour
    "tepung panko":   "gluten",    # panko breadcrumbs
    "tepung roti":    "gluten",    # breadcrumbs
    "kulit gyoza":    "gluten",    # gyoza wrappers
    "tepung crispy":  "gluten",    # crispy coating mix
    "panko":          "gluten",
    "terigu":         "gluten",
    "gyoza":          "gluten",
    "seitan":         "gluten",
    "udon":           "gluten",
    "ramen":          "gluten",
    "pasta":          "gluten",
    "roti":           "gluten",
    "mie":            "gluten",
    "mi ":            "gluten",    # trailing space avoids matching "mie"
    "tepung":         "wheat",     # generic flour -> wheat (less specific)

    # ── Soy sources ───────────────────────────────────────────────────────
    "kecap manis":    "soy",       # sweet soy sauce
    "kecap asin":     "soy",       # salty soy sauce
    "kecap":          "soy",       # any soy sauce
    "teriyaki":       "soy",       # teriyaki sauce contains soy
    "tonkatsu":       "soy",       # tonkatsu sauce contains soy
    "edamame":        "soy",
    "tempe":          "soy",
    "tahu":           "soy",
    "tofu":           "soy",
    "miso":           "soy",

    # ── Egg sources ───────────────────────────────────────────────────────
    "mayonnaise":     "egg",
    "mayo":           "egg",
    "telur":          "egg",
    "egg":            "egg",

    # ── Milk / dairy sources ──────────────────────────────────────────────
    "es krim":        "milk",
    "yogurt":         "milk",
    "mentega":        "milk",      # butter
    "butter":         "milk",
    "cheese":         "milk",
    "cream":          "milk",
    "krim":           "milk",
    "keju":           "milk",      # cheese
    "susu":           "milk",
    "milk":           "milk",

    # ── Shellfish sources ─────────────────────────────────────────────────
    "kepiting":       "shellfish", # crab
    "lobster":        "shellfish",
    "kerang":         "shellfish", # clam / mussel
    "udang":          "shellfish", # shrimp/prawn
    "cumi":           "shellfish", # squid
    "ebi":            "shellfish", # dried shrimp

    # ── Fish sources ──────────────────────────────────────────────────────
    "anchovy":        "fish",
    "salmon":         "fish",
    "dashi":          "fish",
    "tuna":           "fish",
    "ikan":           "fish",
    "teri":           "fish",

    # ── Peanut sources ────────────────────────────────────────────────────
    "kacang tanah":   "peanut",
    "selai kacang":   "peanut",
    "peanut":         "peanut",

    # ── Sesame sources ────────────────────────────────────────────────────
    "minyak wijen":   "sesame",
    "sesame":         "sesame",
    "wijen":          "sesame",

    # ── Tree nut sources ──────────────────────────────────────────────────
    "kacang mede":    "tree_nut",
    "kacang almond":  "tree_nut",
    "hazelnut":       "tree_nut",
    "pistachio":      "tree_nut",
    "cashew":         "tree_nut",
    "walnut":         "tree_nut",
    "almond":         "tree_nut",
}

# Pre-sort by key length descending once at import time.
# Longer keys (more specific) are checked before shorter ones.
_SORTED_ALLERGEN_KEYS: list[tuple[str, str]] = sorted(
    _ALLERGEN_MAP.items(), key=lambda kv: -len(kv[0])
)


def detect_allergens(ingredients: list[str]) -> list[str]:
    """
    Detect canonical allergen names from a list of ingredient strings.

    Parameters
    ----------
    ingredients : list[str]
        Individual ingredient name strings (already split).

    Returns
    -------
    list[str]
        Sorted, unique canonical allergen names found.
        Example: ["egg", "gluten", "soy"]
    """
    found: set[str] = set()
    # Join all ingredients into one searchable string for substring matching
    combined = " ".join(ingredients).lower()

    for keyword, allergen in _SORTED_ALLERGEN_KEYS:
        if keyword in combined:
            found.add(allergen)

    return sorted(found)


# ---------------------------------------------------------------------------
# Review-status logic
# ---------------------------------------------------------------------------

def _compute_review_status(item: dict[str, Any]) -> tuple[str, list[str]]:
    """
    Determine whether a product item needs review.

    Checks five required fields. If any are absent/zero the item is
    flagged as ``"needs_review"`` and the reasons list is populated.

    Returns
    -------
    tuple[str, list[str]]
        (review_status, reasons)
        review_status is ``"ok"`` or ``"needs_review"``.
        reasons is a list of string codes (empty when status is ``"ok"``).
    """
    reasons: list[str] = []

    # Name should be a real string, not a fallback placeholder
    if not item.get("name") or str(item["name"]).startswith("Item #"):
        reasons.append("name_missing")

    # Price of 0 means extraction failed
    if not item.get("price"):
        reasons.append("price_missing_or_zero")

    # Category helps buyers browse the catalogue
    if not item.get("category", "").strip():
        reasons.append("category_missing")

    # Ingredients are required for allergen detection to work
    if not item.get("ingredients"):
        reasons.append("ingredients_missing")

    # Pre-order day is essential for a PO platform
    if not item.get("po_day", "").strip():
        reasons.append("po_day_missing")

    status = "needs_review" if reasons else "ok"
    return status, reasons


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_items(raw_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Validate and enrich a list of raw extracted product dicts.

    For each item this function:
    - Detects allergens from the ingredients list.
    - Computes review_status and review_reasons.
    - Returns a clean dict ready for pipeline/load.py.

    Parameters
    ----------
    raw_items : list[dict]
        Output of ``extract_document()`` or ``extract_from_bytes()``.

    Returns
    -------
    list[dict]
        Validated dicts with the following keys:
        name, price, category, ingredients (list), allergens (list),
        po_day, review_status, review_reasons (list).
    """
    validated: list[dict[str, Any]] = []

    for raw in raw_items:
        # Detect allergens from the split ingredient list
        allergens = detect_allergens(raw.get("ingredients", []))

        # Build intermediate dict for review-status check
        candidate = {
            "name":        raw.get("name", "").strip(),
            "price":       raw.get("price", 0),
            "category":    raw.get("category", "").strip(),
            "ingredients": raw.get("ingredients", []),
            "po_day":      raw.get("po_day", "").strip(),
        }

        review_status, review_reasons = _compute_review_status(candidate)

        validated.append({
            "name":           candidate["name"],
            "price":          candidate["price"],
            "category":       candidate["category"],
            "ingredients":    candidate["ingredients"],
            "allergens":      allergens,
            "po_day":         candidate["po_day"],
            "review_status":  review_status,
            "review_reasons": review_reasons,
        })

    return validated
