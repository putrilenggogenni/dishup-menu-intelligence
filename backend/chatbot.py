"""
chatbot.py -- DB-grounded Q&A engine for DishUp buyer chatbot.

Rules:
  - ALL answers come ONLY from the SQLite database.
  - If a product has review_status='needs_review', reply with seller-confirm message.
  - If no product is found, say so explicitly.
  - Never hallucinate ingredients, allergens, or prices.
  - Every response includes {"ai_generated": True} so the UI can show the label.
"""
from __future__ import annotations

import re
import logging
from typing import Any

from backend.database import list_products, get_conn

logger = logging.getLogger(__name__)

# ── Optional watsonx import ───────────────────────────────────────────────────
try:
    from ibm_watsonx_ai import APIClient, Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
    _WATSONX_AVAILABLE = True
except ImportError:
    _WATSONX_AVAILABLE = False

import os
from dotenv import load_dotenv
load_dotenv()


# ── Intent patterns ───────────────────────────────────────────────────────────

_INTENTS: list[tuple[str, list[str]]] = [
    ("allergen_query",   [r"alergi", r"allergen", r"gluten.free", r"bebas gluten", r"bebas susu",
                          r"bebas telur", r"egg.free", r"dairy.free", r"nuts?", r"kacang",
                          r"mengandung apa", r"aman.*alergi"]),
    ("ingredient_query", [r"bahan", r"ingredient", r"terbuat dari", r"dibuat dari", r"isinya"]),
    ("price_query",      [r"harga", r"berapa", r"price", r"murah", r"mahal", r"cost"]),
    ("availability",     [r"tersedia", r"ada\b", r"available", r"jual", r"bisa pesan",
                          r"pre.order", r"kapan", r"hari"]),
    ("category_query",   [r"kategori", r"jenis", r"tipe", r"macam"]),
    ("list_products",    [r"daftar menu", r"semua menu", r"menu apa", r"ada menu", r"list"]),
    ("greeting",         [r"^halo", r"^hi\b", r"^hello", r"^hai", r"^selamat"]),
]


def detect_intent(text: str) -> str:
    lower = text.lower()
    for intent, patterns in _INTENTS:
        for p in patterns:
            if re.search(p, lower):
                return intent
    return "general"


# ── Product name extraction ───────────────────────────────────────────────────

def find_relevant_products(question: str) -> list[dict]:
    """
    Search the DB for products whose name or ingredients match keywords in the question.
    Returns all products if no keyword matches (for list/greeting intents).
    """
    all_products = list_products()
    if not all_products:
        return []

    words = re.findall(r"\b\w{3,}\b", question.lower())
    if not words:
        return all_products

    matches = []
    for p in all_products:
        name_lower = p["name"].lower()
        ing_lower = " ".join(p.get("ingredients", [])).lower()
        cat_lower = p.get("category", "").lower()
        score = 0
        for w in words:
            if w in name_lower:
                score += 3
            if w in ing_lower:
                score += 2
            if w in cat_lower:
                score += 1
        if score > 0:
            matches.append((score, p))

    if not matches:
        return all_products  # return all if no keyword match

    matches.sort(key=lambda x: -x[0])
    return [p for _, p in matches[:5]]


# ── Response builders ─────────────────────────────────────────────────────────

_NEEDS_REVIEW_MSG = (
    "Mohon maaf, informasi produk ini masih dalam review dan belum dapat dikonfirmasi. "
    "Silakan hubungi seller secara langsung untuk detail lebih lanjut."
)

_NOT_FOUND_MSG = (
    "Maaf, saya tidak menemukan produk yang sesuai dengan pertanyaan Anda di database kami. "
    "Silakan coba kata kunci lain atau hubungi seller."
)


def _format_price(p: int) -> str:
    if p == 0:
        return "harga belum tersedia (perlu konfirmasi seller)"
    return f"Rp{p:,}".replace(",", ".")


def _format_allergens(allergens: list[str]) -> str:
    if not allergens:
        return "tidak ada alergen terdeteksi"
    return ", ".join(allergens)


def _format_po_days(po_days: list[str]) -> str:
    if not po_days:
        return "jadwal belum diketahui"
    return ", ".join(po_days)


def _product_summary(p: dict) -> str:
    lines = [
        f"**{p['name']}** ({p.get('category', '-')})",
        f"  - Harga: {_format_price(p['price'])}",
        f"  - Bahan: {', '.join(p.get('ingredients', [])) or '-'}",
        f"  - Alergen: {_format_allergens(p.get('allergens', []))}",
        f"  - PO: {_format_po_days(p.get('po_days', []))}",
        f"  - Status: {'Tersedia' if p.get('is_available') else 'Tidak tersedia'}",
    ]
    return "\n".join(lines)


# ── Rule-based answer generator ───────────────────────────────────────────────

def _rule_based_answer(question: str, intent: str, products: list[dict]) -> str:
    if intent == "greeting":
        return (
            "Halo! Saya asisten DishUp. Saya bisa membantu menjawab pertanyaan "
            "tentang menu, harga, bahan, alergen, dan jadwal pre-order berdasarkan "
            "data yang tersedia di database. Silakan tanya!"
        )

    if not products:
        return _NOT_FOUND_MSG

    # If any relevant product is needs_review, prepend a warning
    review_products = [p for p in products if p.get("review_status") == "needs_review"]
    ok_products = [p for p in products if p.get("review_status") == "ok"]

    parts: list[str] = []

    if intent == "list_products":
        count = len(products)
        parts.append(f"Berikut {count} produk yang ditemukan:\n")
        for p in products[:8]:
            if p.get("review_status") == "needs_review":
                parts.append(f"- {p['name']} _(perlu konfirmasi seller)_")
            else:
                parts.append(f"- {p['name']} — {_format_price(p['price'])}")
        return "\n".join(parts)

    if intent == "allergen_query":
        lower = question.lower()
        # Check if asking whether product IS gluten-free / allergen-free
        is_free_check = re.search(
            r"(bebas|free|aman|tanpa)\s*(gluten|susu|milk|telur|egg|kacang|soy|kedelai|seafood)", lower
        )
        if is_free_check:
            allergen_kw = is_free_check.group(2)
            allergen_map = {
                "gluten": "gluten", "susu": "milk", "milk": "milk",
                "telur": "egg", "egg": "egg", "kacang": "peanut",
                "soy": "soy", "kedelai": "soy", "seafood": "shellfish",
            }
            target = allergen_map.get(allergen_kw, allergen_kw)

        for p in products:
            if p.get("review_status") == "needs_review":
                parts.append(
                    f"**{p['name']}**: {_NEEDS_REVIEW_MSG}"
                )
                continue
            allergens = p.get("allergens", [])
            if not allergens:
                parts.append(f"**{p['name']}**: tidak ada alergen terdeteksi dari data yang tersedia.")
            else:
                parts.append(
                    f"**{p['name']}** mengandung alergen: **{', '.join(allergens)}**."
                )
        return "\n\n".join(parts) if parts else _NOT_FOUND_MSG

    if intent == "ingredient_query":
        for p in products[:3]:
            if p.get("review_status") == "needs_review":
                parts.append(f"**{p['name']}**: {_NEEDS_REVIEW_MSG}")
                continue
            ings = p.get("ingredients", [])
            parts.append(
                f"**{p['name']}** terbuat dari: {', '.join(ings) if ings else 'data belum tersedia'}."
            )
        return "\n\n".join(parts) if parts else _NOT_FOUND_MSG

    if intent == "price_query":
        for p in products[:3]:
            if p.get("review_status") == "needs_review":
                parts.append(f"**{p['name']}**: {_NEEDS_REVIEW_MSG}")
                continue
            parts.append(f"**{p['name']}**: {_format_price(p['price'])}")
        return "\n".join(parts) if parts else _NOT_FOUND_MSG

    if intent == "availability":
        for p in products[:3]:
            if p.get("review_status") == "needs_review":
                parts.append(f"**{p['name']}**: {_NEEDS_REVIEW_MSG}")
                continue
            avail = "tersedia" if p.get("is_available") else "tidak tersedia saat ini"
            po = _format_po_days(p.get("po_days", []))
            parts.append(f"**{p['name']}**: {avail}, pre-order hari {po}.")
        return "\n".join(parts) if parts else _NOT_FOUND_MSG

    # General / fallback — show full summary of top result
    p = products[0]
    if p.get("review_status") == "needs_review":
        return f"**{p['name']}**: {_NEEDS_REVIEW_MSG}"
    return _product_summary(p)


# ── Watsonx-enhanced answer (optional) ───────────────────────────────────────

def _get_chat_model():
    if not _WATSONX_AVAILABLE:
        return None
    api_key = os.getenv("WATSONX_API_KEY", "")
    project_id = os.getenv("WATSONX_PROJECT_ID", "")
    url = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
    if not api_key or api_key.startswith("your_"):
        return None
    try:
        creds = Credentials(url=url, api_key=api_key)
        client = APIClient(creds)
        return ModelInference(
            model_id="ibm/granite-3-8b-instruct",
            api_client=client,
            project_id=project_id,
            params={GenParams.MAX_NEW_TOKENS: 512, GenParams.TEMPERATURE: 0.2},
        )
    except Exception:
        return None


def _watsonx_answer(question: str, products: list[dict]) -> str | None:
    model = _get_chat_model()
    if not model:
        return None

    # Build a minimal context from DB — never hallucinate
    context_lines = []
    for p in products[:4]:
        if p.get("review_status") == "needs_review":
            context_lines.append(
                f"- {p['name']}: DATA NEEDS REVIEW — do not provide specifics."
            )
        else:
            context_lines.append(
                f"- {p['name']}: harga {_format_price(p['price'])}, "
                f"bahan: {', '.join(p.get('ingredients', []))}, "
                f"alergen: {_format_allergens(p.get('allergens', []))}, "
                f"PO: {_format_po_days(p.get('po_days', []))}."
            )

    context = "\n".join(context_lines)

    prompt = f"""Kamu adalah asisten chatbot DishUp. Jawab HANYA menggunakan data berikut.
Jika data tidak tersedia atau produk berstatus NEEDS REVIEW, katakan informasi perlu dikonfirmasi seller.
Jangan mengarang data. Jawab dalam Bahasa Indonesia, singkat dan jelas.

DATA PRODUK:
{context}

PERTANYAAN PEMBELI: {question}

JAWABAN:"""

    try:
        return model.generate_text(prompt=prompt).strip()
    except Exception as exc:
        logger.error("watsonx chat error: %s", exc)
        return None


# ── Public entry point ────────────────────────────────────────────────────────

def answer(question: str) -> dict[str, Any]:
    """
    Main chatbot entry point.

    Returns:
        {
            "answer": str,
            "ai_generated": True,
            "products_referenced": [list of product names],
            "used_ai": bool  (True = watsonx, False = rule-based)
        }
    """
    intent = detect_intent(question)
    products = find_relevant_products(question)

    # Try watsonx first
    wx_answer = _watsonx_answer(question, products)
    if wx_answer:
        return {
            "answer": wx_answer,
            "ai_generated": True,
            "products_referenced": [p["name"] for p in products],
            "used_ai": True,
        }

    # Fallback: rule-based
    rb_answer = _rule_based_answer(question, intent, products)
    return {
        "answer": rb_answer,
        "ai_generated": True,
        "products_referenced": [p["name"] for p in products],
        "used_ai": False,
    }
