"""
app.py -- Flask REST API for DishUp Menu Intelligence.

Endpoints:
  GET  /api/health
  POST /api/upload                   Upload & process menu document (.txt/.pdf)
  GET  /api/products                 List products (filter: seller_id, category, status, search)
  DELETE /api/products/<id>          Delete product
  PATCH /api/products/<id>/toggle    Toggle availability
  GET  /api/sellers                  List sellers
  GET  /api/allergens                List allergen master
  GET  /api/logs                     Upload history
  GET  /api/stats                    Dashboard statistics
  POST /api/chat                     Buyer chatbot Q&A
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

from backend.database import (
    init_db,
    get_or_create_seller,
    insert_product,
    list_products,
    delete_product,
    toggle_availability,
    list_sellers,
    list_allergens,
    list_upload_logs,
    create_upload_log,
    update_upload_log,
    get_stats,
)
from backend.parser import parse_bytes
from backend.ai_normalizer import ai_normalise
from backend.chatbot import answer as chatbot_answer

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dev-secret")
CORS(app, resources={r"/api/*": {"origins": "*"}})

init_db()


# ── Static frontend ───────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path):
    return send_from_directory(FRONTEND_DIR, path)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "DishUp Menu Intelligence"})


# ── Upload ────────────────────────────────────────────────────────────────────

@app.post("/api/upload")
def upload():
    seller_name = request.form.get("seller_name", "").strip()
    if not seller_name:
        return jsonify({"error": "seller_name is required"}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded"}), 400

    allowed_ext = {".txt", ".pdf", ".md", ".csv"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_ext:
        return jsonify({"error": f"File type '{ext}' not supported. Use .txt, .pdf, .md, or .csv"}), 400

    file_bytes = file.read()
    raw_text = file_bytes.decode("utf-8", errors="replace") if ext != ".pdf" else ""

    seller_id = get_or_create_seller(seller_name)
    log_id = create_upload_log(seller_id, file.filename, raw_text)

    try:
        # 1. Parse
        parsed = parse_bytes(file_bytes, file.filename)

        # 2. AI normalise
        normalised = ai_normalise(parsed)

        # 3. Load to DB
        counts = {"inserted": 0, "duplicate": 0, "error": 0, "needs_review": 0}
        for item in normalised:
            result, _ = insert_product(seller_id, item)
            counts[result] = counts.get(result, 0) + 1
            if item.get("review_status") == "needs_review" and result == "inserted":
                counts["needs_review"] += 1

        update_upload_log(
            log_id,
            rows_parsed=len(parsed),
            rows_inserted=counts["inserted"],
            rows_duplicate=counts["duplicate"],
            rows_needs_review=counts["needs_review"],
            rows_error=counts["error"],
            status="done",
        )

        return jsonify({
            "message": "Upload processed successfully",
            "seller_id": seller_id,
            "log_id": log_id,
            "rows_parsed": len(parsed),
            "rows_inserted": counts["inserted"],
            "rows_duplicate": counts["duplicate"],
            "rows_needs_review": counts["needs_review"],
            "rows_error": counts["error"],
        })

    except Exception as exc:
        update_upload_log(log_id, status="failed")
        return jsonify({"error": str(exc)}), 500


# ── Products ──────────────────────────────────────────────────────────────────

@app.get("/api/products")
def get_products():
    seller_id = request.args.get("seller_id", type=int)
    category  = request.args.get("category")
    status    = request.args.get("status")
    search    = request.args.get("search")
    return jsonify(list_products(seller_id=seller_id, category=category,
                                 status=status, search=search))


@app.delete("/api/products/<int:product_id>")
def remove_product(product_id: int):
    delete_product(product_id)
    return jsonify({"deleted": product_id})


@app.patch("/api/products/<int:product_id>/toggle")
def toggle_product(product_id: int):
    ok = toggle_availability(product_id)
    if not ok:
        return jsonify({"error": "Not found"}), 404
    return jsonify({"toggled": product_id})


# ── Sellers ───────────────────────────────────────────────────────────────────

@app.get("/api/sellers")
def get_sellers():
    return jsonify(list_sellers())


# ── Allergens ─────────────────────────────────────────────────────────────────

@app.get("/api/allergens")
def get_allergens():
    return jsonify(list_allergens())


# ── Logs ──────────────────────────────────────────────────────────────────────

@app.get("/api/logs")
def get_logs():
    seller_id = request.args.get("seller_id", type=int)
    return jsonify(list_upload_logs(seller_id=seller_id))


# ── Stats ─────────────────────────────────────────────────────────────────────

@app.get("/api/stats")
def stats():
    return jsonify(get_stats())


# ── Chatbot ───────────────────────────────────────────────────────────────────

@app.post("/api/chat")
def chat():
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or body.get("message") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    result = chatbot_answer(question)
    return jsonify(result)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, port=5000, host="127.0.0.1", use_reloader=False)
