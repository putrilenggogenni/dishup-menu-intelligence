"""
database.py -- SQLite persistence layer for DishUp Menu Intelligence.
Schema: sellers, products, ingredients, allergens, product_allergens,
        preorder_batches, upload_logs.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "dishup.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Apply schema.sql to create/migrate all tables."""
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        sql = fh.read()
    with get_conn() as conn:
        conn.executescript(sql)


# ── Sellers ──────────────────────────────────────────────────────────────────

def get_or_create_seller(name: str) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM sellers WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO sellers (name) VALUES (?)", (name,))
        return cur.lastrowid


def list_sellers() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM sellers ORDER BY name")]


# ── Allergens ─────────────────────────────────────────────────────────────────

def get_allergen_id(name: str) -> int:
    """Get or create an allergen by canonical name."""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM allergens WHERE name=?", (name,)).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO allergens (name) VALUES (?)", (name,))
        return cur.lastrowid


def list_allergens() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM allergens ORDER BY name")]


# ── Products ──────────────────────────────────────────────────────────────────

def insert_product(seller_id: int, item: dict[str, Any]) -> tuple[str, int]:
    """
    Insert a full product with ingredients, allergens, and preorder batch.
    Returns ('inserted'|'duplicate'|'error', product_id).
    item keys: name, price, category, ingredients_list, allergens_list,
               po_day, review_status, review_reasons
    """
    name = item.get("name", "").strip()
    po_day = item.get("po_day", "")

    try:
        with get_conn() as conn:
            # Duplicate check: same seller + name + po_day
            existing = conn.execute(
                """SELECT p.id FROM products p
                   JOIN preorder_batches pb ON pb.product_id = p.id
                   WHERE p.seller_id=? AND p.name=? AND pb.po_day=?""",
                (seller_id, name, po_day),
            ).fetchone()
            if existing:
                return "duplicate", existing["id"]

            reasons_json = json.dumps(item.get("review_reasons", []))

            cur = conn.execute(
                """INSERT INTO products
                   (seller_id, name, price, category, review_status, review_reasons)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    seller_id,
                    name,
                    item.get("price", 0),
                    item.get("category", ""),
                    item.get("review_status", "ok"),
                    reasons_json,
                ),
            )
            product_id = cur.lastrowid

            # Ingredients
            for ing in item.get("ingredients_list", []):
                ing = ing.strip()
                if ing:
                    conn.execute(
                        "INSERT INTO ingredients (product_id, name) VALUES (?, ?)",
                        (product_id, ing),
                    )

            # Allergens
            for allergen in item.get("allergens_list", []):
                allergen = allergen.strip().lower()
                if allergen:
                    a_row = conn.execute(
                        "SELECT id FROM allergens WHERE name=?", (allergen,)
                    ).fetchone()
                    if a_row:
                        conn.execute(
                            "INSERT OR IGNORE INTO product_allergens (product_id, allergen_id) VALUES (?, ?)",
                            (product_id, a_row["id"]),
                        )

            # Pre-order batch
            if po_day:
                conn.execute(
                    "INSERT INTO preorder_batches (product_id, po_day) VALUES (?, ?)",
                    (product_id, po_day),
                )

            return "inserted", product_id

    except Exception as exc:
        return "error", -1


def list_products(
    seller_id: int | None = None,
    category: str | None = None,
    status: str | None = None,
    search: str | None = None,
) -> list[dict]:
    """Return products with joined seller name, allergens, po_days."""
    q = """
        SELECT
            p.*,
            s.name AS seller_name,
            GROUP_CONCAT(DISTINCT a.name)  AS allergen_names,
            GROUP_CONCAT(DISTINCT pb.po_day) AS po_days,
            GROUP_CONCAT(DISTINCT i.name ORDER BY i.id) AS ingredient_names
        FROM products p
        JOIN sellers s ON s.id = p.seller_id
        LEFT JOIN product_allergens pa ON pa.product_id = p.id
        LEFT JOIN allergens a          ON a.id = pa.allergen_id
        LEFT JOIN preorder_batches pb  ON pb.product_id = p.id
        LEFT JOIN ingredients i        ON i.product_id = p.id
        WHERE 1=1
    """
    params: list[Any] = []
    if seller_id:
        q += " AND p.seller_id=?"
        params.append(seller_id)
    if category:
        q += " AND p.category=?"
        params.append(category)
    if status:
        q += " AND p.review_status=?"
        params.append(status)
    if search:
        q += " AND (p.name LIKE ? OR i.name LIKE ?)"
        params.extend([f"%{search}%", f"%{search}%"])
    q += " GROUP BY p.id ORDER BY p.review_status DESC, p.category, p.name"

    with get_conn() as conn:
        rows = conn.execute(q, params).fetchall()

    result = []
    for r in rows:
        d = dict(r)
        d["allergens"] = [x for x in (d.get("allergen_names") or "").split(",") if x]
        d["po_days"]   = [x for x in (d.get("po_days") or "").split(",") if x]
        d["ingredients"] = [x for x in (d.get("ingredient_names") or "").split(",") if x]
        d["review_reasons"] = json.loads(d.get("review_reasons") or "[]")
        result.append(d)
    return result


def get_product_detail(product_id: int) -> dict | None:
    rows = list_products()
    for r in rows:
        if r["id"] == product_id:
            return r
    return None


def delete_product(product_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))


def toggle_availability(product_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT is_available FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            return False
        conn.execute(
            "UPDATE products SET is_available=?, updated_at=datetime('now') WHERE id=?",
            (0 if row["is_available"] else 1, product_id),
        )
        return True


# ── Upload logs ───────────────────────────────────────────────────────────────

def create_upload_log(seller_id: int, filename: str, raw_content: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO upload_logs (seller_id, filename, raw_content) VALUES (?, ?, ?)",
            (seller_id, filename, raw_content),
        )
        return cur.lastrowid


def update_upload_log(log_id: int, **kwargs):
    if not kwargs:
        return
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [log_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE upload_logs SET {sets} WHERE id=?", vals)


def list_upload_logs(seller_id: int | None = None) -> list[dict]:
    q = "SELECT ul.*, s.name AS seller_name FROM upload_logs ul LEFT JOIN sellers s ON s.id=ul.seller_id"
    params: list[Any] = []
    if seller_id:
        q += " WHERE ul.seller_id=?"
        params.append(seller_id)
    q += " ORDER BY ul.created_at DESC LIMIT 50"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(q, params)]


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        needs_review = conn.execute(
            "SELECT COUNT(*) FROM products WHERE review_status='needs_review'"
        ).fetchone()[0]
        total_sellers = conn.execute("SELECT COUNT(*) FROM sellers").fetchone()[0]
        total_uploads = conn.execute("SELECT COUNT(*) FROM upload_logs").fetchone()[0]
        by_category = [
            dict(r) for r in conn.execute(
                "SELECT category, COUNT(*) AS cnt FROM products GROUP BY category ORDER BY cnt DESC"
            )
        ]
        by_allergen = [
            dict(r) for r in conn.execute(
                """SELECT a.name AS allergen, COUNT(*) AS cnt
                   FROM product_allergens pa
                   JOIN allergens a ON a.id = pa.allergen_id
                   GROUP BY a.name ORDER BY cnt DESC"""
            )
        ]
        by_po_day = [
            dict(r) for r in conn.execute(
                "SELECT po_day, COUNT(*) AS cnt FROM preorder_batches GROUP BY po_day ORDER BY cnt DESC"
            )
        ]
    return {
        "total_products": total,
        "needs_review": needs_review,
        "total_sellers": total_sellers,
        "total_uploads": total_uploads,
        "by_category": by_category,
        "by_allergen": by_allergen,
        "by_po_day": by_po_day,
    }
