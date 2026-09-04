from __future__ import annotations

import inspect
import sqlite3
from types import SimpleNamespace

import pandas as pd

import inventory_api_separation_v09149 as patch


class Core:
    def __init__(self, db):
        self.DEFAULT_DB = db

    def now_iso(self):
        return "2026-09-04T12:30:00"

    def _conn(self, db):
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        return con


def _db(tmp_path):
    db = tmp_path / "rg.db"
    with sqlite3.connect(db) as con:
        con.executescript(
            """
            CREATE TABLE products(
                id INTEGER PRIMARY KEY,
                item_code TEXT,
                option_id TEXT
            );
            CREATE TABLE inventory_txns(
                id INTEGER PRIMARY KEY,
                txn_date TEXT,
                product_id INTEGER,
                warehouse_id INTEGER,
                qty_delta REAL,
                txn_type TEXT,
                ref_no TEXT,
                memo TEXT,
                created_at TEXT
            );
            CREATE TABLE coupang_rg_inventory(
                product_id INTEGER,
                orderable_qty REAL,
                synced_at TEXT,
                stock_type TEXT
            );
            """
        )
        con.execute("INSERT INTO products VALUES(1,'CP-123','123')")
        con.execute(
            "INSERT INTO inventory_txns(txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no) "
            "VALUES('2026-09-01',1,2,14,'생산RG입고','PROD-1')"
        )
        con.execute(
            "INSERT INTO inventory_txns(txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no) "
            "VALUES('2026-09-02',1,2,-4,'쿠팡API재고조정','API-1')"
        )
        con.execute(
            "INSERT INTO coupang_rg_inventory VALUES(1,8,'2026-09-04T12:00:00','normal')"
        )
    return db


def _inventory_ui():
    ui = SimpleNamespace()
    ui._display_code = lambda item_code, option_id=None: (
        str(option_id or str(item_code)[3:])
        if str(item_code or "").startswith("CP-")
        else str(item_code or "")
    )
    ui._enrich_view = lambda df: (df.copy(), False)
    ui._tab_frame = lambda df, warehouse, item_master: df.copy()
    return ui


def _api():
    api = SimpleNamespace()
    api.ensure_schema = lambda core, db: None
    api.patch_source = lambda source: source

    def sync_inventory(core, client, db_path=None):
        adjusted = api._reconcile_warehouse_inventory(
            core, None, "쿠팡RG", {1: {"qty": 8}}, 1, "normal"
        )
        return {"run_id": None, "rows": 1, "matched": 1, **adjusted}

    api.sync_inventory = sync_inventory
    return api


def test_inventory_ui_shows_book_and_api_stock_separately(tmp_path):
    db = _db(tmp_path)
    core = Core(db)
    ui = _inventory_ui()
    api = _api()

    patch.apply(core, api, ui, db)

    with core._conn(db) as con:
        ledger_qty = float(con.execute(
            "SELECT COALESCE(SUM(qty_delta),0) q FROM inventory_txns WHERE product_id=1"
        ).fetchone()["q"])
        original = con.execute(
            "SELECT COUNT(*) n FROM inventory_txns WHERE txn_type='쿠팡API재고조정'"
        ).fetchone()["n"]
        reversal = con.execute(
            "SELECT COUNT(*) n FROM inventory_txns WHERE txn_type='쿠팡API재고조정취소'"
        ).fetchone()["n"]
    assert original == 1
    assert reversal == 1
    assert ledger_qty == 14

    raw = pd.DataFrame(
        [{
            "품목코드": "123",
            "상품명": "테스트",
            "기준원가": 100,
            "자체창고": 0,
            "쿠팡RG": ledger_qty,
            "반품창고": 0,
        }]
    )
    view, item_master = ui._enrich_view(raw)

    assert int(view.loc[0, "쿠팡RG 장부재고"]) == 14
    assert int(view.loc[0, "쿠팡 판매가능재고"]) == 8
    assert int(view.loc[0, "재고차이"]) == -6
    assert view.loc[0, "API 조회시각"] == "2026-09-04T12:00:00"

    rg = ui._tab_frame(view, "쿠팡RG", item_master)
    assert list(rg[["장부재고", "쿠팡 판매가능재고", "차이(API-장부)"]].iloc[0]) == [14, 8, -6]

    patch.apply(core, api, ui, db)
    with core._conn(db) as con:
        assert con.execute(
            "SELECT COUNT(*) n FROM inventory_txns WHERE txn_type='쿠팡API재고조정취소'"
        ).fetchone()["n"] == 1


def test_inventory_sync_is_read_only_for_book_inventory(tmp_path):
    db = _db(tmp_path)
    core = Core(db)
    ui = _inventory_ui()
    api = _api()

    patch.apply(core, api, ui, db)
    result = api.sync_inventory(core, None, db)

    assert result["book_inventory_unchanged"] is True
    assert result["adjusted_rows"] == 0
    assert result["adjusted_qty"] == 0


def test_api_orders_and_returns_are_not_provisional_sources_or_ui_actions(tmp_path):
    db = _db(tmp_path)
    core = Core(db)
    ui = _inventory_ui()
    api = _api()

    patch.apply(core, api, ui, db)

    assert api.provisional_months_from_api(core, db) == []
    rows, meta = api.provisional_rows_from_api(core, "2026-09", db)
    assert rows == []
    assert meta["source"] == "sales_stats_excel_only"
    assert meta["activity_rows"] == 0

    source = inspect.getsource(api.render_page)
    assert "api.sync_orders" not in source
    assert "api.sync_returns" not in source
    assert "api.sync_inventory" in source
    assert "api.sync_revenue" in source
    assert "api.sync_settlements" in source
