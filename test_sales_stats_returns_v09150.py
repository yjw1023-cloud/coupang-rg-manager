from __future__ import annotations
import io
import sqlite3
import tempfile
from pathlib import Path

import pandas as pd

import sales_stats_returns_v09150 as mod


def _xlsx(rows):
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        pd.DataFrame(rows).to_excel(w, index=False, sheet_name="판매통계")
    return bio.getvalue()


class Core:
    def __init__(self, db):
        self.DEFAULT_DB = db

    def init_db(self, db):
        return None

    def _conn(self, db):
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        return con

    def file_hash(self, source):
        return "same-hash"

    def norm_date(self, value):
        return str(value)[:10]


def _db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db = Path(f.name)
    con = sqlite3.connect(str(db))
    con.executescript("""
        CREATE TABLE imports(
            id INTEGER PRIMARY KEY,file_hash TEXT,file_name TEXT,data_type TEXT,
            period_start TEXT,period_end TEXT
        );
        CREATE TABLE products(
            id INTEGER PRIMARY KEY,option_id TEXT,item_code TEXT,name TEXT
        );
        CREATE TABLE sales_stats(
            import_id INTEGER,product_id INTEGER,net_qty REAL
        );
        INSERT INTO imports VALUES
            (1,'same-hash','sales.xlsx','sales_stats','2026-09-01','2026-09-03');
        INSERT INTO products VALUES
            (1,'111','JDS1','상품A'),
            (2,'222','JDS2','상품B');
        INSERT INTO sales_stats VALUES
            (1,1,8),
            (1,2,4);
    """)
    con.commit()
    con.close()
    return db


def test_parse_and_preserve_gross_cancel_without_touching_net_qty():
    raw = _xlsx({
        "옵션 ID": [111, 222],
        "상품명": ["상품A", "상품B"],
        "판매상품수": [10, 5],
        "취소상품수": [2, 1],
        "순판매상품수": [8, 4],
    })
    parsed, meta = mod.parse_sales_quantities(raw)
    assert meta["available"] is True
    assert sum(x["sales_qty"] for x in parsed) == 15
    assert sum(x["cancel_qty"] for x in parsed) == 3

    db = _db()
    try:
        core = Core(db)
        result = mod.enrich_import(core, db, 1, parsed)
        assert result["sales_qty"] == 15
        assert result["cancel_qty"] == 3
        with core._conn(db) as con:
            rows = con.execute(
                "SELECT product_id,net_qty,sales_qty,cancel_qty FROM sales_stats ORDER BY product_id"
            ).fetchall()
        assert [(r["product_id"], r["net_qty"], r["sales_qty"], r["cancel_qty"]) for r in rows] == [
            (1, 8.0, 10.0, 2.0),
            (2, 4.0, 5.0, 1.0),
        ]
    finally:
        db.unlink(missing_ok=True)


def test_same_import_enrichment_is_idempotent():
    raw = _xlsx({
        "옵션 ID": [111],
        "판매상품수": [10],
        "취소상품수": [2],
        "순판매상품수": [8],
    })
    parsed, _ = mod.parse_sales_quantities(raw)
    db = _db()
    try:
        core = Core(db)
        mod.enrich_import(core, db, 1, parsed)
        mod.enrich_import(core, db, 1, parsed)
        with core._conn(db) as con:
            r = con.execute(
                "SELECT net_qty,sales_qty,cancel_qty FROM sales_stats WHERE import_id=1 AND product_id=1"
            ).fetchone()
        assert (r["net_qty"], r["sales_qty"], r["cancel_qty"]) == (8.0, 10.0, 2.0)
    finally:
        db.unlink(missing_ok=True)
