-- ============================================================
-- DishUp Menu Intelligence — Database Schema
-- Engine : SQLite 3 (WAL mode, foreign keys ON)
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Sellers ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sellers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Allergens master list ────────────────────────────────────
-- Standardised allergen names (FALCPA / EU-14 inspired).
CREATE TABLE IF NOT EXISTS allergens (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name    TEXT    NOT NULL UNIQUE   -- e.g. "gluten", "egg", "soy", "shellfish"
);

-- ── Products (menu items) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id       INTEGER NOT NULL REFERENCES sellers(id) ON DELETE CASCADE,
    name            TEXT    NOT NULL,
    price           INTEGER NOT NULL DEFAULT 0,   -- IDR integer, 0 = unknown
    category        TEXT    NOT NULL DEFAULT '',
    review_status   TEXT    NOT NULL DEFAULT 'ok'
                    CHECK (review_status IN ('ok', 'needs_review')),
    review_reasons  TEXT    NOT NULL DEFAULT '',  -- JSON array of reason strings
    is_available    INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Ingredients (per product, normalised rows) ───────────────
CREATE TABLE IF NOT EXISTS ingredients (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL    -- cleaned ingredient name
);

-- ── Product ↔ Allergen (many-to-many) ───────────────────────
CREATE TABLE IF NOT EXISTS product_allergens (
    product_id  INTEGER NOT NULL REFERENCES products(id)  ON DELETE CASCADE,
    allergen_id INTEGER NOT NULL REFERENCES allergens(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, allergen_id)
);

-- ── Pre-order batches ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS preorder_batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    po_day      TEXT    NOT NULL,   -- e.g. "Jumat", "Sabtu"
    open_time   TEXT,               -- optional HH:MM
    close_time  TEXT,               -- optional HH:MM
    notes       TEXT
);

-- ── Upload logs ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS upload_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seller_id       INTEGER REFERENCES sellers(id),
    filename        TEXT    NOT NULL,
    raw_content     TEXT    NOT NULL DEFAULT '',
    rows_parsed     INTEGER NOT NULL DEFAULT 0,
    rows_inserted   INTEGER NOT NULL DEFAULT 0,
    rows_duplicate  INTEGER NOT NULL DEFAULT 0,
    rows_needs_review INTEGER NOT NULL DEFAULT 0,
    rows_error      INTEGER NOT NULL DEFAULT 0,
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'done', 'failed')),
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- ── Indexes ──────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_products_seller   ON products(seller_id);
CREATE INDEX IF NOT EXISTS idx_products_status   ON products(review_status);
CREATE INDEX IF NOT EXISTS idx_ingredients_prod  ON ingredients(product_id);
CREATE INDEX IF NOT EXISTS idx_pa_product        ON product_allergens(product_id);
CREATE INDEX IF NOT EXISTS idx_po_product        ON preorder_batches(product_id);
CREATE INDEX IF NOT EXISTS idx_logs_seller       ON upload_logs(seller_id);

-- ── Seed allergen master ──────────────────────────────────────
INSERT OR IGNORE INTO allergens (name) VALUES
    ('gluten'),
    ('egg'),
    ('soy'),
    ('milk'),
    ('shellfish'),
    ('fish'),
    ('peanut'),
    ('tree_nut'),
    ('sesame'),
    ('wheat');
