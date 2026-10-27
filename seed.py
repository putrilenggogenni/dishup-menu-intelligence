"""
seed.py
=======
Populate the DishUp database with representative sample products.

Usage:
    python seed.py

Runs the full pipeline (extract -> validate -> load) against the
built-in PRODUCTS list so the application has data on first launch.
"""
import sys
import os

# Ensure project root is on the Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline.extract  import extract_document
from pipeline.validate import validate_items
from pipeline.load     import init_db, load_to_db


# ---------------------------------------------------------------------------
# Sample product data (raw document lines, one per product)
# ---------------------------------------------------------------------------

# Each string mimics a real seller document line so the full pipeline runs.
SELLER_DOCS: dict[str, list[str]] = {
    "Nippon Bento": [
        "Chicken Katsu Bento  | Rp35.000 | Bahan: ayam, tepung panko, telur, nasi, saus tonkatsu | Kategori: Bento   | PO: Jumat",
        "Salmon Teriyaki Bento| 48000    | Bahan: salmon, kecap teriyaki, nasi, brokoli, wijen   | Kategori: Bento   | PO: Jumat",
        "Ebi Fry Bento        | Rp42.000 | Bahan: udang, tepung panko, telur, nasi               | Kategori: Bento   | PO: Sabtu",
        "Matcha Mochi         | Rp15.000 | Bahan: tepung ketan, matcha, gula                     | Kategori: Dessert | PO: Sabtu",
        "Gyoza Goreng         | 22000    | Bahan: daging ayam, kubis, bawang putih, kulit gyoza  | Kategori: Snack   | PO: Jumat",
        # Intentionally incomplete — triggers needs_review:
        "Mystery Roll         | 0        | Bahan:                                                | Kategori:         | PO:",
    ],
    "Dapur Ibu Sari": [
        "Nasi Box Ayam Bakar  | 28000    | Bahan: ayam, nasi, lalapan, sambal, tempe             | Kategori: Bento   | PO: Senin",
        "Nasi Box Rendang     | Rp32.000 | Bahan: daging sapi, santan, bumbu rendang, nasi       | Kategori: Bento   | PO: Rabu",
        "Es Teh Manis         | 5000     | Bahan: teh, gula, es batu                             | Kategori: Drink   | PO: Senin",
        "Brownies Cokelat     | Rp18.000 | Bahan: tepung terigu, cokelat, mentega, telur, gula   | Kategori: Dessert | PO: Sabtu",
    ],
    "Snack Station": [
        "Pisang Goreng Crispy | 12000    | Bahan: pisang, tepung crispy, minyak goreng           | Kategori: Snack   | PO: Kamis",
        "Tahu Isi Pedas       | 10000    | Bahan: tahu, wortel, kol, cabe, bihun                | Kategori: Snack   | PO: Kamis",
        "Thai Tea             | Rp15.000 | Bahan: teh thai, susu evaporasi, gula, es batu        | Kategori: Drink   | PO: Jumat",
    ],
}


def main() -> None:
    print("Initialising database...")
    init_db()
    print("Database ready.\n")

    total_inserted = 0
    total_review   = 0
    total_dup      = 0

    for seller_name, lines in SELLER_DOCS.items():
        # Join lines into a document string and run through the pipeline
        raw_text = "\n".join(lines)
        extracted = extract_document(raw_text)
        validated = validate_items(extracted)

        result = load_to_db(
            seller_name    = seller_name,
            validated_items= validated,
            filename       = f"seed_{seller_name.lower().replace(' ','_')}.txt",
            raw_content    = raw_text,
        )

        total_inserted += result["inserted"]
        total_review   += result["needs_review"]
        total_dup      += result["duplicate"]

        print(f"  {seller_name}: "
              f"inserted={result['inserted']}  "
              f"duplicate={result['duplicate']}  "
              f"needs_review={result['needs_review']}")

    print(f"\nSelesai: {total_inserted} produk disimpan, "
          f"{total_review} needs_review, "
          f"{total_dup} duplikat.")


if __name__ == "__main__":
    main()
