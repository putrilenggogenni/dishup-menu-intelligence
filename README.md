# DishUp Menu Intelligence

**Document → Database AI pipeline** untuk platform pre-order makanan **DishUp**.  
Seller kecil mengupload dokumen menu teks atau PDF; sistem secara otomatis mengekstrak,
memvalidasi, mendeteksi alergen, dan menyimpan data ke SQLite.

---

## Struktur Folder

```
dishup-menu-intelligence/
├── data/                          # Dokumen menu contoh
│   ├── menu_nippon_bento.txt      # Menu standar (1 seller)
│   └── menu_dengan_alergen.txt    # Menu dengan berbagai alergen + item needs_review
│
├── pipeline/                      # ETL pipeline (3 stage)
│   ├── __init__.py
│   ├── extract.py                 # Stage 1: Dokumen → JSON terstruktur
│   ├── validate.py                # Stage 2: Validasi, deteksi alergen, needs_review
│   └── load.py                    # Stage 3: Simpan ke SQLite
│
├── db/                            # Database layer
│   ├── schema.sql                 # DDL — semua tabel + seed allergen master
│   └── dishup.db                  # SQLite database (dibuat otomatis)
│
├── app/                           # Frontend + server
│   ├── index.html                 # Seller tool SPA (3 tab: Dashboard, Upload, Produk)
│   ├── style.css                  # Design system (navy #1B2CC1, mobile-first 430px)
│   ├── script.js                  # Vanilla JS — tanpa framework
│   └── server/
│       ├── __init__.py
│       ├── app.py                 # Flask REST API (10 endpoint)
│       ├── queries.py             # Read-only DB queries
│       └── chatbot.py             # DB-grounded Q&A engine (dipakai /api/chat)
│
├── backend/                       # Modul AI (IBM watsonx.ai Granite integration)
│   ├── ai_normalizer.py           # Perbaikan field kosong via IBM Granite (opsional)
│   └── ...
│
├── run.py                         # Entry point server development
├── seed.py                        # Isi database dengan data contoh
├── requirements.txt
├── .env.example
└── README.md
```

---

## Cara Menjalankan

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

Untuk dukungan PDF (opsional):
```bash
pip install pypdf
```

> **Python 3.14:** Gunakan `py run.py` / `py seed.py` jika `python` tidak tersedia di PATH.

### 2. Konfigurasi environment

```bash
cp .env.example .env
```

Edit `.env` dan isi credential IBM watsonx.ai jika tersedia:
```env
WATSONX_API_KEY=your_api_key_here
WATSONX_PROJECT_ID=your_project_id_here
WATSONX_URL=https://us-south.ml.cloud.ibm.com
```

> **Catatan:** Tanpa konfigurasi watsonx, semua fitur tetap berjalan menggunakan rule-based engine.

### 3. Inisialisasi database + seed data

```bash
python seed.py
```

Output yang diharapkan:
```
Initialising database...
Database ready.

  Nippon Bento: inserted=6  duplicate=0  needs_review=1
  Dapur Ibu Sari: inserted=4  duplicate=0  needs_review=0
  Snack Station: inserted=3  duplicate=0  needs_review=0

Selesai: 13 produk disimpan, 1 needs_review, 0 duplikat.
```

### 4. Jalankan server

```bash
python run.py
```

Buka **http://localhost:5000** di browser.

---

## Seller Tool — Fitur UI

Aplikasi di **http://localhost:5000** adalah single-page app khusus seller dengan 3 tab:

| Tab | Fungsi |
|-----|--------|
| **Dashboard** | Statistik total produk, needs_review, seller, upload; grafik kategori & alergen; tabel produk terbaru |
| **Upload** | Form upload dokumen menu (.txt / .pdf / .md / .csv), drag-and-drop, JSON preview hasil ekstraksi |
| **Produk** | Katalog lengkap dengan filter seller, kategori, status; modal detail bahan & alergen; toggle ketersediaan; hapus produk |

---

## REST API Endpoints

| Method | Endpoint | Deskripsi |
|--------|----------|-----------|
| `GET`    | `/api/health`                    | Liveness check |
| `POST`   | `/api/upload`                    | Upload & proses dokumen menu |
| `GET`    | `/api/products`                  | List produk (filter: seller_id, category, status, search) |
| `DELETE` | `/api/products/<id>`             | Hapus produk |
| `PATCH`  | `/api/products/<id>/toggle`      | Toggle ketersediaan produk |
| `GET`    | `/api/sellers`                   | Daftar seller |
| `GET`    | `/api/allergens`                 | Master list alergen |
| `GET`    | `/api/logs`                      | Riwayat upload |
| `GET`    | `/api/stats`                     | Statistik dashboard |
| `POST`   | `/api/chat`                      | Menu Q&A (DB-grounded) |

### Contoh: Upload dokumen

```bash
curl -X POST http://localhost:5000/api/upload \
  -F "seller_name=Nippon Bento" \
  -F "file=@data/menu_nippon_bento.txt"
```

### Contoh: Tanya menu via API

```bash
curl -X POST http://localhost:5000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Apakah Chicken Katsu Bento mengandung gluten?"}'
```

---

## Format Dokumen Menu

Setiap baris = satu produk. Pemisah: `|` atau `;`.

```
Nama Produk | Harga | Bahan: bahan1, bahan2 | Kategori: X | PO: Hari
```

**Format harga yang didukung:**
- `Rp35.000` · `Rp 42.000` · `48000` · `35,000`

**Item ditandai `needs_review` jika:**
- Nama kosong / placeholder
- Harga 0 atau tidak ditemukan
- Kategori tidak ada
- Bahan tidak ada
- Hari PO tidak ada

---

## Pipeline ETL

```
Dokumen (.txt/.pdf)
       │
       ▼
pipeline/extract.py        Stage 1: Parse baris → JSON kasar
       │                             Normalise harga, split bahan, dedup
       ▼
pipeline/validate.py       Stage 2: Deteksi alergen (30+ mapping)
       │                             Hitung review_status + review_reasons
       ▼
pipeline/load.py           Stage 3: INSERT ke SQLite
                                     Tabel: products, ingredients,
                                     allergens, product_allergens,
                                     preorder_batches, upload_logs
```

### Mapping Alergen (contoh)

| Bahan | Alergen |
|-------|---------|
| tepung panko, terigu, gyoza | gluten |
| kecap, teriyaki, tahu, tempe | soy |
| telur, mayonnaise | egg |
| susu, mentega, keju | milk |
| udang, ebi, kepiting | shellfish |
| salmon, ikan, teri | fish |
| kacang tanah | peanut |
| wijen, minyak wijen | sesame |

---

## Contoh Query `/api/chat`

Endpoint ini berguna untuk integrasi eksternal atau pengujian chatbot:

### 1. Query Alergen
```
Apakah Chicken Katsu Bento mengandung gluten?
```
**Jawaban yang diharapkan:** menyebut alergen gluten, egg, soy dari database.

### 2. Query Harga
```
Harga Salmon Teriyaki Bento berapa?
```
**Jawaban yang diharapkan:** `Rp48.000` — diambil langsung dari kolom `price`.

### 3. Query Bahan / Ingredient
```
Apa saja bahan yang digunakan untuk Ebi Fry Bento?
```
**Jawaban yang diharapkan:** daftar bahan dari tabel `ingredients`.

### 4. Query Jadwal PO
```
Menu pre-order hari Jumat ada apa saja?
```
**Jawaban yang diharapkan:** daftar produk dengan `po_day = Jumat`.

### 5. Query Produk needs_review
```
Apa bahan Mystery Roll?
```
**Jawaban yang diharapkan:** pesan konfirmasi seller karena produk berstatus `needs_review`.

---

## Tech Stack

| Layer | Teknologi |
|-------|-----------|
| Backend | Python 3.14+, Flask 3, Flask-CORS |
| Database | SQLite 3 (WAL mode, FK enforcement) |
| Pipeline | Tiga modul terpisah: extract, validate, load |
| AI (opsional) | IBM watsonx.ai — Granite 3.8B Instruct |
| Frontend | HTML5 semantik, CSS custom properties, Vanilla JS |
| Design | Mobile-first 430px, Inter font, navy `#1B2CC1`, bg `#F2F4F8` |

---

## Skema Database

Lihat [`db/schema.sql`](db/schema.sql) untuk DDL lengkap.

```
sellers ──┐
          ├── products ──┬── ingredients
          │              ├── product_allergens ── allergens
          │              └── preorder_batches
          └── upload_logs
```
