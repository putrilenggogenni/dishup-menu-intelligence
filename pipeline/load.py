"""
pipeline/load.py
================
Stage 3 of the DishUp ETL pipeline: Validated JSON -> SQLite.

Takes the list of validated product dicts from validate.py and
persists them into the relational SQLite database defined in
db/schema.sql.

Tables written
--------------
  sellers           — seller row (get-or-create by name)
  products          — core product row
  ingredients       — one row per ingredient, FK to products
  product_allergens — many-to-many join, FK to products + allergens
  preorder_batches  — one row per PO day, FK to products
  upload_logs       — audit log entry for this upload

Duplicate detection
-------------------
A product is considered a duplicate when the database already
contains a row with the same (seller_id, product.name, po_day).

Return value
------------
``load_to_db`` returns a dict:
{
    "inserted":     int,
    "duplicate":    int,
    "error":        int,
    "needs_review": int,   # subset of inserted that were flagged
    "log_id":       int,   # upload_logs row id
}
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

# ---------------------------------------------------------------------------
# DB configuration
# ---------------------------------------------------------------------------

# Locate db/ relative to this file: pipeline/ -> project root -> db/
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH     = os.path.join(_PROJECT_ROOT, "db", "dishup.db")
SCHEMA_PATH = os.path.join(_PROJECT_ROOT, "db", "schema.sql")


def _get_connection() -> sqlite3.Connection:
    """Open and return a SQLite connection with WAL mode and FK support."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """
    Apply db/schema.sql to create or migrate all tables.
    Safe to call repeatedly — uses CREATE TABLE IF NOT EXISTS.
    """
    with open(SCHEMA_PATH, "r", encoding="utf-8") as fh:
        sql = fh.read()
    with _get_connection() as conn:
        conn.executescript(sql)


# ---------------------------------------------------------------------------
# Seller helpers
# ---------------------------------------------------------------------------

def get_or_create_seller(name: str) -> int:
    """
    Return the id of a seller row, creating it if it doesn't exist.
    Thread-safe because each call opens its own connection.
    """
    with _get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM sellers WHERE name=?", (name,)
        ).fetchone()
        if row:
            return row["id"]
        cur = conn.execute("INSERT INTO sellers (name) VALUES (?)", (name,))
        return cur.lastrowid


# ---------------------------------------------------------------------------
# Single-product insert
# ---------------------------------------------------------------------------

def _insert_one(conn: sqlite3.Connection, seller_id: int,
                item: dict[str, Any]) -> str:
    """
    Insert one product and its related rows inside an existing connection.
    Returns "inserted", "duplicate", or "error".
    """
    name   = item.get("name", "").strip()
    po_day = item.get("po_day", "")

    # ── Duplicate check ────────────────────────────────────────────────────
    # A product is a duplicate when the same seller already has a row
    # with the same (name, po_day) combination.
    dup_check = conn.execute(
        """
        SELECT p.id FROM products p
        JOIN   preorder_batches pb ON pb.product_id = p.id
        WHERE  p.seller_id = ? AND p.name = ? AND pb.po_day = ?
        """,
        (seller_id, name, po_day),
    ).fetchone()

    if dup_check:
        return "duplicate"

    # ── Insert core product row ────────────────────────────────────────────
    reasons_json = json.dumps(item.get("review_reasons", []))
    cur = conn.execute(
        """
        INSERT INTO products
            (seller_id, name, price, category, review_status, review_reasons)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
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

    # ── Insert ingredients ─────────────────────────────────────────────────
    for ingredient in item.get("ingredients", []):
        ingredient = ingredient.strip()
        if ingredient:
            conn.execute(
                "INSERT INTO ingredients (product_id, name) VALUES (?, ?)",
                (product_id, ingredient),
            )

    # ── Link allergens (from allergens master table) ───────────────────────
    for allergen_name in item.get("allergens", []):
        allergen_name = allergen_name.strip().lower()
        if not allergen_name:
            continue
        allergen_row = conn.execute(
            "SELECT id FROM allergens WHERE name=?", (allergen_name,)
        ).fetchone()
        if allergen_row:
            # INSERT OR IGNORE in case of duplicate junction rows
            conn.execute(
                """
                INSERT OR IGNORE INTO product_allergens
                    (product_id, allergen_id)
                VALUES (?, ?)
                """,
                (product_id, allergen_row["id"]),
            )

    # ── Insert pre-order batch row ─────────────────────────────────────────
    if po_day:
        conn.execute(
            "INSERT INTO preorder_batches (product_id, po_day) VALUES (?, ?)",
            (product_id, po_day),
        )

    return "inserted"


# ---------------------------------------------------------------------------
# Batch load (public API)
# ---------------------------------------------------------------------------

def load_to_db(
    seller_name:  str,
    validated_items: list[dict[str, Any]],
    filename:     str = "unknown",
    raw_content:  str = "",
) -> dict[str, Any]:
    """
    Persist a batch of validated product dicts to SQLite.

    Parameters
    ----------
    seller_name : str
        Human-readable seller name.  A sellers row is created if needed.
    validated_items : list[dict]
        Output of ``validate_items()``.
    filename : str
        Original upload filename — stored in upload_logs.
    raw_content : str
        Raw document text — stored in upload_logs for audit purposes.

    Returns
    -------
    dict
        {
            "inserted":     int,
            "duplicate":    int,
            "error":        int,
            "needs_review": int,   # subset of inserted with needs_review status
            "log_id":       int,
        }
    """
    seller_id = get_or_create_seller(seller_name)

    counts: dict[str, int] = {
        "inserted":     0,
        "duplicate":    0,
        "error":        0,
        "needs_review": 0,
    }

    # Use a single connection for all inserts (one transaction per product
    # is fine for small batches; no explicit BEGIN needed — sqlite3's context
    # manager commits on __exit__).
    for item in validated_items:
        try:
            with _get_connection() as conn:
                result = _insert_one(conn, seller_id, item)
            counts[result] += 1
            # Track how many inserted rows are flagged for review
            if result == "inserted" and item.get("review_status") == "needs_review":
                counts["needs_review"] += 1
        except Exception:
            counts["error"] += 1

    # ── Write audit log ────────────────────────────────────────────────────
    with _get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO upload_logs
                (seller_id, filename, raw_content,
                 rows_parsed, rows_inserted, rows_duplicate,
                 rows_needs_review, rows_error, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'done')
            """,
            (
                seller_id,
                filename,
                raw_content,
                len(validated_items),
                counts["inserted"],
                counts["duplicate"],
                counts["needs_review"],
                counts["error"],
            ),
        )
        log_id = cur.lastrowid

    counts["log_id"] = log_id
    return counts
