"""
app/server/app.py
=================
Flask REST API for DishUp Menu Intelligence.

REST Endpoints
--------------
  GET  /api/health
  POST /api/upload                      Upload & process a menu document
  GET  /api/products                    List products (filters: seller_id, category, status, search)
  DELETE /api/products/<id>             Hard-delete a product
  PATCH  /api/products/<id>/toggle      Toggle is_available flag
  GET  /api/sellers                     List sellers
  GET  /api/allergens                   List allergen master table
  GET  /api/logs                        Upload history
  GET  /api/stats                       Dashboard statistics
  POST /api/chat                        Buyer chatbot Q&A

Static frontend
---------------
  GET  /          -> app/index.html
  GET  /<path>    -> app/static files (style.css, script.js)
"""

from __future__ import annotations

import os
import sys

# Make project root importable regardless of where Flask is invoked from
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

# ── Pipeline imports ──────────────────────────────────────────────────────────
from pipeline.extract  import extract_from_bytes
from pipeline.validate import validate_items
from pipeline.load     import load_to_db, init_db

# ── Query / chatbot imports ───────────────────────────────────────────────────
from app.server.queries import (
    list_products,
    delete_product,
    toggle_availability,
    list_sellers,
    list_allergens,
    list_upload_logs,
    get_stats,
)
from app.server.chatbot import answer as chatbot_answer

# ── watsonx AI normaliser (optional — for repairing needs_review items) ───────
from backend.ai_normalizer import ai_normalise as _ai_normalise  # graceful fallback

# Resolve frontend directory: app/ folder at project root
_APP_DIR = os.path.join(_PROJECT_ROOT, "app")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=_APP_DIR, static_url_path="")
app.secret_key = os.getenv("FLASK_SECRET_KEY", "dishup-dev-secret")
CORS(app, resources={r"/api/*": {"origins": "*"}})

# Initialise DB tables on startup (safe to call repeatedly)
init_db()


# ---------------------------------------------------------------------------
# Static frontend
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Serve the seller tool SPA."""
    return send_from_directory(_APP_DIR, "index.html")


@app.route("/<path:path>")
def static_files(path: str):
    """Serve CSS, JS, and any other static assets from app/."""
    return send_from_directory(_APP_DIR, path)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """Simple liveness probe used by monitoring and tests."""
    return jsonify({"status": "ok", "service": "DishUp Menu Intelligence"})


# ---------------------------------------------------------------------------
# Upload endpoint  (ETL pipeline: Extract -> Validate -> Load)
# ---------------------------------------------------------------------------

@app.post("/api/upload")
def upload():
    """
    Accept a multipart/form-data upload with fields:
      - seller_name  (str, required)
      - file         (binary, required; .txt/.pdf/.md/.csv)

    Runs the full ETL pipeline and returns counts.
    """
    seller_name = request.form.get("seller_name", "").strip()
    if not seller_name:
        return jsonify({"error": "seller_name is required"}), 400

    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "No file uploaded"}), 400

    allowed = {".txt", ".pdf", ".md", ".csv"}
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed:
        return jsonify({"error": f"Unsupported file type '{ext}'. Use: .txt .pdf .md .csv"}), 400

    file_bytes = file.read()
    raw_text   = file_bytes.decode("utf-8", errors="replace") if ext != ".pdf" else ""

    try:
        # ── Stage 1: Extract ──────────────────────────────────────────────
        extracted = extract_from_bytes(file_bytes, file.filename)

        # ── Stage 2: Validate & Clean ─────────────────────────────────────
        validated = validate_items(extracted)

        # ── Optional AI repair for needs_review items ─────────────────────
        # Adapts validated items into the format expected by ai_normalise,
        # then maps them back.  Skips silently if watsonx is not configured.
        try:
            adapted = [
                {**v, "ingredients_list": v.get("ingredients", []),
                 "allergens_list": v.get("allergens", []),
                 "_raw_line": extracted[i].get("_raw_line", "")}
                for i, v in enumerate(validated)
            ]
            ai_result = _ai_normalise(adapted)
            # Merge AI-corrected fields back into validated list
            for i, (orig, ai) in enumerate(zip(validated, ai_result)):
                if orig.get("review_status") == "needs_review":
                    validated[i] = {
                        "name":           ai.get("name",   orig["name"]),
                        "price":          ai.get("price",  orig["price"]),
                        "category":       ai.get("category", orig["category"]),
                        "ingredients":    ai.get("ingredients_list", orig["ingredients"]),
                        "allergens":      ai.get("allergens_list",   orig["allergens"]),
                        "po_day":         ai.get("po_day",  orig["po_day"]),
                        "review_status":  ai.get("review_status",  orig["review_status"]),
                        "review_reasons": ai.get("review_reasons",  orig["review_reasons"]),
                    }
        except Exception:
            pass  # AI step is optional; never block a valid upload

        # ── Stage 3: Load ─────────────────────────────────────────────────
        result = load_to_db(
            seller_name=seller_name,
            validated_items=validated,
            filename=file.filename,
            raw_content=raw_text,
        )

        return jsonify({
            "message":          "Upload processed successfully",
            "rows_parsed":      len(extracted),
            "rows_inserted":    result["inserted"],
            "rows_duplicate":   result["duplicate"],
            "rows_needs_review": result["needs_review"],
            "rows_error":       result["error"],
            "log_id":           result["log_id"],
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

@app.get("/api/products")
def get_products():
    """List products with optional filters."""
    seller_id = request.args.get("seller_id", type=int)
    category  = request.args.get("category")
    status    = request.args.get("status")
    search    = request.args.get("search")
    return jsonify(list_products(
        seller_id=seller_id, category=category,
        status=status, search=search,
    ))


@app.delete("/api/products/<int:product_id>")
def remove_product(product_id: int):
    """Hard-delete a product and all child rows."""
    delete_product(product_id)
    return jsonify({"deleted": product_id})


@app.patch("/api/products/<int:product_id>/toggle")
def toggle_product(product_id: int):
    """Toggle the is_available flag of a product."""
    ok = toggle_availability(product_id)
    if not ok:
        return jsonify({"error": "Product not found"}), 404
    return jsonify({"toggled": product_id})


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

@app.get("/api/sellers")
def get_sellers():
    return jsonify(list_sellers())


@app.get("/api/allergens")
def get_allergens():
    return jsonify(list_allergens())


# ---------------------------------------------------------------------------
# Upload logs
# ---------------------------------------------------------------------------

@app.get("/api/logs")
def get_logs():
    seller_id = request.args.get("seller_id", type=int)
    return jsonify(list_upload_logs(seller_id=seller_id))


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------

@app.get("/api/stats")
def stats():
    return jsonify(get_stats())


# ---------------------------------------------------------------------------
# Chatbot
# ---------------------------------------------------------------------------

@app.post("/api/chat")
def chat():
    """
    Accept JSON body: {"question": "..."}
    Returns: {"answer": "...", "ai_generated": true, ...}
    """
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or body.get("message") or "").strip()
    if not question:
        return jsonify({"error": "question field is required"}), 400
    return jsonify(chatbot_answer(question))


# ---------------------------------------------------------------------------
# Entry point (development server)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("DishUp Menu Intelligence - Development Server")
    print("http://localhost:5000")
    app.run(debug=False, host="127.0.0.1", port=5000, use_reloader=False)
