"""
app/server/queries.py
=====================
Read-only database queries used by the Flask API.

All functions return plain Python dicts / lists — no sqlite3.Row objects
are exposed outside this module.  Write operations (insert / delete / toggle)
are handled by pipeline/load.py.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

# Resolve DB path relative to this file: app/server/ -> project root -> db/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(_PROJECT_ROOT, "db", "dishup.db")


def _conn() -> sqlite3.Connection:
    """Return a SQLite connection with Row factory and FK enforcement."""
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


# ---------------------------------------------------------------------------
# Sellers
# ---------------------------------------------------------------------------

def list_sellers() -> list[dict]:
    """Return all sellers ordered alphabetically."""
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM sellers ORDER BY name")]


# ---------------------------------------------------------------------------
# Allergens
# ---------------------------------------------------------------------------

def list_allergens() -> list[dict]:
    """Return the full allergen master list."""
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM allergens ORDER BY name")]


# ---------------------------------------------------------------------------
# Products  (with joined allergens, ingredients, po_days)
# ---------------------------------------------------------------------------

def list_products(
    seller_id: int | None = None,
    category:  str | None = None,
    status:    str | None = None,
    search:    str | None = None,
) -> list[dict]:
    """
    Return products with aggregated allergens, ingredients, and PO days.

    Filters
    -------
    seller_id : int, optional
    category  : str, optional   — exact match
    status    : str, optional   — "ok" | "needs_review"
    search    : str, optional   — LIKE match on name or ingredient name
    """
    sql = """
        SELECT
            p.*,
            s.name                                       AS seller_name,
            GROUP_CONCAT(DISTINCT a.name)                AS allergen_names,
            GROUP_CONCAT(DISTINCT pb.po_day)             AS po_days,
            GROUP_CONCAT(DISTINCT i.name ORDER BY i.id)  AS ingredient_names
        FROM products p
        JOIN   sellers s          ON s.id  = p.seller_id
        LEFT JOIN product_allergens pa ON pa.product_id = p.id
        LEFT JOIN allergens a          ON a.id           = pa.allergen_id
        LEFT JOIN preorder_batches pb  ON pb.product_id  = p.id
        LEFT JOIN ingredients i        ON i.product_id   = p.id
        WHERE 1=1
    """
    params: list[Any] = []

    if seller_id:
        sql += " AND p.seller_id = ?"
        params.append(seller_id)
    if category:
        sql += " AND p.category = ?"
        params.append(category)
    if status:
        sql += " AND p.review_status = ?"
        params.append(status)
    if search:
        sql += " AND (p.name LIKE ? OR i.name LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]

    # needs_review items appear first, then alphabetically by category + name
    sql += " GROUP BY p.id ORDER BY p.review_status DESC, p.category, p.name"

    with _conn() as c:
        rows = c.execute(sql, params).fetchall()

    result: list[dict] = []
    for r in rows:
        d = dict(r)
        # Convert comma-concatenated strings back to lists
        d["allergens"]    = [x for x in (d.pop("allergen_names",  "") or "").split(",") if x]
        d["po_days"]      = [x for x in (d.pop("po_days",         "") or "").split(",") if x]
        d["ingredients"]  = [x for x in (d.pop("ingredient_names","") or "").split(",") if x]
        d["review_reasons"] = json.loads(d.get("review_reasons") or "[]")
        result.append(d)

    return result


def delete_product(product_id: int) -> None:
    """Hard-delete a product and all its child rows (FK CASCADE)."""
    with _conn() as c:
        c.execute("DELETE FROM products WHERE id=?", (product_id,))


def toggle_availability(product_id: int) -> bool:
    """
    Flip a product's is_available flag.
    Returns True on success, False if the product was not found.
    """
    with _conn() as c:
        row = c.execute(
            "SELECT is_available FROM products WHERE id=?", (product_id,)
        ).fetchone()
        if not row:
            return False
        new_val = 0 if row["is_available"] else 1
        c.execute(
            "UPDATE products SET is_available=?, updated_at=datetime('now') WHERE id=?",
            (new_val, product_id),
        )
        return True


# ---------------------------------------------------------------------------
# Upload logs
# ---------------------------------------------------------------------------

def list_upload_logs(seller_id: int | None = None) -> list[dict]:
    """Return the 50 most recent upload log entries."""
    sql = """
        SELECT ul.*, s.name AS seller_name
        FROM   upload_logs ul
        LEFT JOIN sellers s ON s.id = ul.seller_id
    """
    params: list[Any] = []
    if seller_id:
        sql += " WHERE ul.seller_id=?"
        params.append(seller_id)
    sql += " ORDER BY ul.created_at DESC LIMIT 50"

    with _conn() as c:
        return [dict(r) for r in c.execute(sql, params)]


# ---------------------------------------------------------------------------
# Dashboard statistics
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    """Return aggregated counts and distributions for the dashboard."""
    with _conn() as c:
        total         = c.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        needs_review  = c.execute(
            "SELECT COUNT(*) FROM products WHERE review_status='needs_review'"
        ).fetchone()[0]
        total_sellers = c.execute("SELECT COUNT(*) FROM sellers").fetchone()[0]
        total_uploads = c.execute("SELECT COUNT(*) FROM upload_logs").fetchone()[0]

        by_category = [
            dict(r) for r in c.execute(
                "SELECT category, COUNT(*) AS cnt FROM products"
                " GROUP BY category ORDER BY cnt DESC"
            )
        ]
        by_allergen = [
            dict(r) for r in c.execute(
                """
                SELECT a.name AS allergen, COUNT(*) AS cnt
                FROM   product_allergens pa
                JOIN   allergens a ON a.id = pa.allergen_id
                GROUP  BY a.name ORDER BY cnt DESC
                """
            )
        ]
        by_po_day = [
            dict(r) for r in c.execute(
                "SELECT po_day, COUNT(*) AS cnt FROM preorder_batches"
                " GROUP BY po_day ORDER BY cnt DESC"
            )
        ]

    return {
        "total_products": total,
        "needs_review":   needs_review,
        "total_sellers":  total_sellers,
        "total_uploads":  total_uploads,
        "by_category":    by_category,
        "by_allergen":    by_allergen,
        "by_po_day":      by_po_day,
    }
