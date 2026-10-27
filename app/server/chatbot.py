"""
app/server/chatbot.py
=====================
DB-grounded Q&A engine for the DishUp buyer chatbot.

Design rules
------------
- Answers are generated ONLY from data in the SQLite database.
- Products with review_status = 'needs_review' trigger a seller-
  confirmation message instead of specific product details.
- If no relevant product is found, an explicit "not found" message
  is returned.
- Every response dict includes ``"ai_generated": True`` so the
  frontend can display the "This message is AI Generated" label.
- watsonx.ai (Granite) is used when credentials are configured;
  the rule-based engine serves as a reliable offline fallback.
"""

from __future__ import annotations

import os
import re
import logging
from typing import Any

from app.server.queries import list_products

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional watsonx.ai import (graceful fallback when not installed)
# ---------------------------------------------------------------------------
try:
    from ibm_watsonx_ai import APIClient, Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
    _WATSONX_AVAILABLE = True
except ImportError:
    _WATSONX_AVAILABLE = False


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------

# Each tuple is (intent_name, [regex_patterns]).
# Patterns are checked against the lower-cased question.
_INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("allergen_query",   [
        r"alergi", r"allergen", r"gluten.free", r"bebas gluten", r"bebas susu",
        r"bebas telur", r"egg.free", r"dairy.free", r"kacang", r"mengandung apa",
        r"aman.*alergi", r"nuts?",
    ]),
    ("ingredient_query", [
        r"bahan", r"ingredient", r"terbuat dari", r"dibuat dari", r"isinya",
    ]),
    ("price_query",      [
        r"harga", r"berapa", r"price", r"murah", r"mahal", r"cost",
    ]),
    ("availability",     [
        r"tersedia", r"ada\b", r"available", r"jual", r"bisa pesan",
        r"pre.order", r"kapan", r"hari",
    ]),
    ("list_products",    [
        r"daftar menu", r"semua menu", r"menu apa", r"ada menu", r"list",
    ]),
    ("greeting",         [
        r"^halo", r"^hai\b", r"^hi\b", r"^hello", r"^selamat",
    ]),
]


def detect_intent(question: str) -> str:
    """
    Classify the buyer's question into one of the intent categories.
    Returns "general" when no pattern matches.
    """
    lower = question.lower()
    for intent, patterns in _INTENT_PATTERNS:
        for pattern in patterns:
            if re.search(pattern, lower):
                return intent
    return "general"


# ---------------------------------------------------------------------------
# Keyword-based product retrieval
# ---------------------------------------------------------------------------

def find_relevant_products(question: str) -> list[dict]:
    """
    Score all products by how many question keywords appear in their
    name, ingredients, or category, then return the top 5.

    Falls back to the full product list when no keywords match,
    so greeting / list-all intents work correctly.
    """
    all_products = list_products()
    if not all_products:
        return []

    # Extract words with 3+ characters as meaningful keywords
    keywords = re.findall(r"\b\w{3,}\b", question.lower())
    if not keywords:
        return all_products

    scored: list[tuple[int, dict]] = []
    for product in all_products:
        name_text = product["name"].lower()
        ing_text  = " ".join(product.get("ingredients", [])).lower()
        cat_text  = product.get("category", "").lower()

        score = 0
        for kw in keywords:
            if kw in name_text:  score += 3   # name match is strongest
            if kw in ing_text:   score += 2   # ingredient match
            if kw in cat_text:   score += 1   # category match
        if score > 0:
            scored.append((score, product))

    if not scored:
        return all_products  # no keyword match — return everything

    scored.sort(key=lambda x: -x[0])
    return [p for _, p in scored[:5]]


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

# Message shown when a product is flagged as needs_review
_NEEDS_REVIEW_MSG = (
    "Mohon maaf, informasi produk ini masih dalam proses verifikasi dan "
    "belum dapat dikonfirmasi. Silakan hubungi seller secara langsung untuk "
    "detail lebih lanjut."
)

# Message shown when no matching product exists in the DB
_NOT_FOUND_MSG = (
    "Maaf, saya tidak menemukan produk yang sesuai dengan pertanyaan Anda "
    "di database kami. Silakan coba kata kunci lain atau hubungi seller."
)


def _fmt_price(price: int) -> str:
    """Format an integer IDR price for display."""
    if price == 0:
        return "harga belum tersedia (perlu konfirmasi seller)"
    return "Rp" + f"{price:,}".replace(",", ".")


def _fmt_allergens(allergens: list[str]) -> str:
    return ", ".join(allergens) if allergens else "tidak ada alergen terdeteksi"


def _fmt_po_days(po_days: list[str]) -> str:
    return ", ".join(po_days) if po_days else "jadwal belum diketahui"


def _product_full_summary(p: dict) -> str:
    """Return a formatted multi-line product summary string."""
    lines = [
        f"**{p['name']}** ({p.get('category', '-')})",
        f"  Harga      : {_fmt_price(p['price'])}",
        f"  Bahan      : {', '.join(p.get('ingredients', [])) or '-'}",
        f"  Alergen    : {_fmt_allergens(p.get('allergens', []))}",
        f"  Pre-order  : {_fmt_po_days(p.get('po_days', []))}",
        f"  Ketersediaan: {'Tersedia' if p.get('is_available') else 'Tidak tersedia'}",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Rule-based answer builder
# ---------------------------------------------------------------------------

def _rule_based_answer(question: str, intent: str, products: list[dict]) -> str:
    """
    Generate a text answer from the database using rule-based logic.

    Dispatches to intent-specific sections; falls back to a full
    product summary for unrecognised intents.
    """
    if intent == "greeting":
        return (
            "Halo! Saya asisten DishUp. Saya siap membantu menjawab pertanyaan "
            "tentang menu, harga, bahan, alergen, dan jadwal pre-order "
            "berdasarkan data yang tersedia. Silakan tanya!"
        )

    if not products:
        return _NOT_FOUND_MSG

    parts: list[str] = []

    # ── List all products ──────────────────────────────────────────────────
    if intent == "list_products":
        parts.append(f"Berikut {len(products)} produk yang ditemukan:\n")
        for p in products[:8]:
            if p.get("review_status") == "needs_review":
                parts.append(f"- {p['name']} _(perlu konfirmasi seller)_")
            else:
                parts.append(f"- {p['name']} — {_fmt_price(p['price'])}")
        return "\n".join(parts)

    # ── Allergen query ─────────────────────────────────────────────────────
    if intent == "allergen_query":
        for p in products:
            if p.get("review_status") == "needs_review":
                parts.append(f"**{p['name']}**: {_NEEDS_REVIEW_MSG}")
                continue
            allergens = p.get("allergens", [])
            if allergens:
                parts.append(
                    f"**{p['name']}** mengandung alergen: **{', '.join(allergens)}**."
                )
            else:
                parts.append(
                    f"**{p['name']}**: tidak ada alergen terdeteksi dari data yang tersedia."
                )
        return "\n\n".join(parts) if parts else _NOT_FOUND_MSG

    # ── Ingredient query ───────────────────────────────────────────────────
    if intent == "ingredient_query":
        for p in products[:3]:
            if p.get("review_status") == "needs_review":
                parts.append(f"**{p['name']}**: {_NEEDS_REVIEW_MSG}")
                continue
            ings = p.get("ingredients", [])
            parts.append(
                f"**{p['name']}** terbuat dari: "
                f"{', '.join(ings) if ings else 'data belum tersedia'}."
            )
        return "\n\n".join(parts) if parts else _NOT_FOUND_MSG

    # ── Price query ────────────────────────────────────────────────────────
    if intent == "price_query":
        for p in products[:3]:
            if p.get("review_status") == "needs_review":
                parts.append(f"**{p['name']}**: {_NEEDS_REVIEW_MSG}")
                continue
            parts.append(f"**{p['name']}**: {_fmt_price(p['price'])}")
        return "\n".join(parts) if parts else _NOT_FOUND_MSG

    # ── Availability / pre-order schedule ─────────────────────────────────
    if intent == "availability":
        for p in products[:3]:
            if p.get("review_status") == "needs_review":
                parts.append(f"**{p['name']}**: {_NEEDS_REVIEW_MSG}")
                continue
            avail = "tersedia" if p.get("is_available") else "tidak tersedia saat ini"
            po    = _fmt_po_days(p.get("po_days", []))
            parts.append(f"**{p['name']}**: {avail}, pre-order hari {po}.")
        return "\n".join(parts) if parts else _NOT_FOUND_MSG

    # ── General / fallback — full summary of best-matching product ─────────
    best = products[0]
    if best.get("review_status") == "needs_review":
        return f"**{best['name']}**: {_NEEDS_REVIEW_MSG}"
    return _product_full_summary(best)


# ---------------------------------------------------------------------------
# watsonx.ai model factory
# ---------------------------------------------------------------------------

def _get_watsonx_model():
    """
    Return a configured Granite ModelInference instance, or None.
    Returns None when:
      - ibm_watsonx_ai is not installed
      - WATSONX_API_KEY is not set / is the placeholder value
    """
    if not _WATSONX_AVAILABLE:
        return None

    api_key    = os.getenv("WATSONX_API_KEY", "")
    project_id = os.getenv("WATSONX_PROJECT_ID", "")
    url        = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")

    if not api_key or api_key.startswith("your_"):
        return None

    try:
        creds  = Credentials(url=url, api_key=api_key)
        client = APIClient(creds)
        return ModelInference(
            model_id="ibm/granite-3-8b-instruct",
            api_client=client,
            project_id=project_id,
            params={
                GenParams.MAX_NEW_TOKENS: 512,
                GenParams.TEMPERATURE:    0.2,
            },
        )
    except Exception as exc:
        logger.error("watsonx init failed: %s", exc)
        return None


def _watsonx_answer(question: str, products: list[dict]) -> str | None:
    """
    Generate an answer using IBM Granite, grounded strictly in the
    product context built from DB rows.  Returns None on failure.
    """
    model = _get_watsonx_model()
    if not model:
        return None

    # Build a short, factual context string — never include invented data
    ctx_lines: list[str] = []
    for p in products[:4]:
        if p.get("review_status") == "needs_review":
            ctx_lines.append(
                f"- {p['name']}: DATA DALAM REVIEW — jangan berikan detail spesifik."
            )
        else:
            ctx_lines.append(
                f"- {p['name']}: harga {_fmt_price(p['price'])}, "
                f"bahan: {', '.join(p.get('ingredients', []))}, "
                f"alergen: {_fmt_allergens(p.get('allergens', []))}, "
                f"PO: {_fmt_po_days(p.get('po_days', []))}."
            )

    prompt = (
        "Kamu adalah asisten chatbot DishUp. "
        "Jawab HANYA menggunakan data berikut. "
        "Jika data tidak tersedia atau produk berstatus REVIEW, "
        "katakan bahwa informasi perlu dikonfirmasi seller. "
        "Jangan mengarang data. Jawab dalam Bahasa Indonesia.\n\n"
        "DATA PRODUK:\n" + "\n".join(ctx_lines) +
        "\n\nPERTANYAAN: " + question +
        "\n\nJAWABAN:"
    )

    try:
        return model.generate_text(prompt=prompt).strip()
    except Exception as exc:
        logger.error("watsonx chat error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def answer(question: str) -> dict[str, Any]:
    """
    Main chatbot entry point called by the Flask /api/chat endpoint.

    Parameters
    ----------
    question : str
        Raw question text from the buyer.

    Returns
    -------
    dict with keys:
        answer              str   — response text (may contain **bold** markdown)
        ai_generated        bool  — always True (displayed as label in UI)
        products_referenced list  — product names used to form the answer
        used_ai             bool  — True if Granite was used, False = rule-based
    """
    intent   = detect_intent(question)
    products = find_relevant_products(question)

    # Try watsonx.ai first; fall back to rule-based engine
    wx_text = _watsonx_answer(question, products)
    if wx_text:
        return {
            "answer":               wx_text,
            "ai_generated":         True,
            "products_referenced":  [p["name"] for p in products],
            "used_ai":              True,
        }

    rb_text = _rule_based_answer(question, intent, products)
    return {
        "answer":               rb_text,
        "ai_generated":         True,
        "products_referenced":  [p["name"] for p in products],
        "used_ai":              False,
    }
