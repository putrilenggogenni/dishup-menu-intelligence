"""
ai_normalizer.py -- IBM watsonx.ai (Granite) integration for DishUp.

Enhances items that the rule-based parser flagged as needs_review:
  - Fills missing fields (category, po_day)
  - Re-infers price when possible
  - Re-runs allergen detection on AI-corrected ingredients

Falls back gracefully to rule-based result when watsonx is
unavailable or not configured.
"""
from __future__ import annotations

import json
import os
import re
import logging
from typing import Any

from backend.parser import detect_allergens, _review_status

logger = logging.getLogger(__name__)

# ── Optional watsonx import ───────────────────────────────────────────────────
try:
    from ibm_watsonx_ai import APIClient, Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    from ibm_watsonx_ai.metanames import GenTextParamsMetaNames as GenParams
    _WATSONX_AVAILABLE = True
except ImportError:
    _WATSONX_AVAILABLE = False
    logger.warning("ibm-watsonx-ai not installed; AI normalisation disabled.")


# ── Prompt ────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Kamu adalah asisten ekstraksi data untuk platform pre-order makanan DishUp.
Tugas: baca baris menu berikut dan kembalikan JSON yang sudah diperbaiki.

Format output — array JSON, satu objek per baris menu:
[
  {
    "name": "<nama menu>",
    "price": <integer IDR tanpa simbol, 0 jika tidak ada>,
    "category": "<Bento|Dessert|Drink|Snack|Lainnya>",
    "ingredients": "<bahan dipisah koma>",
    "po_day": "<hari PO bahasa Indonesia, atau 'Tidak diketahui'>"
  }
]

Aturan penting:
- Kembalikan HANYA array JSON valid, tanpa markdown, tanpa teks lain.
- Jangan mengarang harga; gunakan 0 jika tidak jelas.
- Infer kategori dari nama/bahan bila label tidak ada.
"""


def _build_prompt(raw_lines: list[str]) -> str:
    numbered = "\n".join(f"{i+1}. {l}" for i, l in enumerate(raw_lines))
    return _SYSTEM_PROMPT + f"\n\nBaris menu:\n{numbered}\n\nOutput JSON:"


# ── Watsonx factory ───────────────────────────────────────────────────────────

def _get_model():
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
            params={
                GenParams.MAX_NEW_TOKENS: 1024,
                GenParams.TEMPERATURE: 0.0,
                GenParams.STOP_SEQUENCES: ["```"],
            },
        )
    except Exception as exc:
        logger.error("watsonx init failed: %s", exc)
        return None


def _extract_json_array(text: str) -> list[dict] | None:
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group())
        return data if isinstance(data, list) else None
    except json.JSONDecodeError:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def ai_normalise(parsed_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Take parser output, enhance needs_review items via AI when available.

    Returns list of DB-ready dicts with keys:
        name, price, category, ingredients_list, allergens_list,
        po_day, review_status, review_reasons
    """
    clean = [i for i in parsed_items if i.get("review_status") == "ok"]
    to_fix = [i for i in parsed_items if i.get("review_status") != "ok"]

    results: list[dict[str, Any]] = [_to_db(i) for i in clean]

    if to_fix:
        model = _get_model()
        if model:
            raw_lines = [i["_raw_line"] for i in to_fix]
            try:
                response = model.generate_text(prompt=_build_prompt(raw_lines))
                ai_items = _extract_json_array(response)
                if ai_items and len(ai_items) == len(to_fix):
                    for ai_item in ai_items:
                        ing_list = [
                            x.strip()
                            for x in re.split(r"[,;/]", str(ai_item.get("ingredients", "")))
                            if x.strip()
                        ]
                        allergens = detect_allergens(ing_list)
                        item = {
                            "name": str(ai_item.get("name", "")).strip(),
                            "price": int(ai_item.get("price", 0)),
                            "category": str(ai_item.get("category", "Lainnya")).strip(),
                            "ingredients_list": ing_list,
                            "allergens_list": allergens,
                            "po_day": str(ai_item.get("po_day", "Tidak diketahui")).strip(),
                            "review_status": "ok",
                            "review_reasons": [],
                        }
                        # Re-check review status on AI result
                        status, reasons = _review_status(item)
                        item["review_status"] = status
                        item["review_reasons"] = reasons
                        results.append(item)
                    logger.info("AI fixed %d items.", len(ai_items))
                    return results
            except Exception as exc:
                logger.error("AI normalisation error: %s", exc)

        # Fallback: mark as needs_review but keep rule-based data
        for i in to_fix:
            results.append(_to_db(i))

    return results


def _to_db(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "name":             item.get("name", ""),
        "price":            item.get("price", 0) or 0,
        "category":         item.get("category", ""),
        "ingredients_list": item.get("ingredients_list", []),
        "allergens_list":   item.get("allergens_list", []),
        "po_day":           item.get("po_day", ""),
        "review_status":    item.get("review_status", "needs_review"),
        "review_reasons":   item.get("review_reasons", []),
    }
