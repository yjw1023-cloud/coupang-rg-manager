"""Focused regression tests for manually confirmed RG inventory reconciliation."""
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest

import rg_inventory_reconcile_v09324 as mod


class Core:
    def __init__(self, db):
        self.DEFAULT_DB = db

    def now_iso(self):
        return "2026-10-13 17:30:00"

    def _conn(self, path):
        con = sqlite3.connect(str(path))
        con.row_factory = sqlite3.Row
        return con


@pytest.fixture
def prepared(tmp_path):
    db = tmp_path / "rg_stock_test.db"
    core = Core(db)
    with core._conn(db) as c:
        c.executescript("""
            CREATE TABLE products(
                id INTEGER PRIMARY KEY,item_code TEXT,option_id TEXT,
                name TEXT,item_type TEXT,active INTEGER
            );
            CREATE TABLE warehouses(id INTEGER PRIMARY KEY,name TEXT);
            CREATE TABLE inventory_txns(
                id INTEGER PRIMARY KEY AUTOINCREMENT,txn_date TEXT,
                product_id INTEGER,warehouse_id INTEGER,qty_delta REAL,
                txn_type TEXT,ref_no TEXT,memo TEXT,created_at TEXT
            );
            CREATE TABLE coupang_rg_inventory(
                product_id INTEGER,orderable_qty REAL,sales_30d REAL,
                synced_at TEXT,stock_type TEXT
            );
            CREATE TABLE coupang_api_sync_runs(
                id INTEGER PRIMARY KEY,sync_type TEXT,status TEXT
            );
            INSERT INTO warehouses VALUES(1,'쿠팡RG');
            INSERT INTO products VALUES(1,'CP-111','111','테스트 삼각대','finished',1);
            INSERT INTO products VALUES(2,'CP-222','222','반품 상품','finished',1);
            INSERT INTO inventory_txns(txn_date,product_id,warehouse_id,qty_delta,txn_type)
                VALUES('2026-10-12',1,1,100,'생산RG입고');
            INSERT INTO coupang_rg_inventory
                VALUES(1,93,30,'2026-10-13 17:00:00','normal');
            INSERT INTO coupang_rg_inventory
                VALUES(2,15,0,'2026-10-13 17:00:00','return');
            INSERT INTO coupang_api_sync_runs VALUES(100,'inventory','success');
        """)
    return core, db


def pending(target=96, token="test-token"):
    return {
        "token": token, "run_id": 100,
        "synced_at": "2026-10-13 17:00:00",
        "changes": [{
            "product_id": 1,"option_id": "111","name": "테스트 삼각대",
            "before_qty": 100,"api_qty": 93,"target_qty": target,
        }],
    }


def count_txns(core, db):
    with core._conn(db) as c:
        return int(c.execute("SELECT COUNT(*) AS n FROM inventory_txns").fetchone()["n"])


def test_api_read_only_comparison_does_not_change_erp_ledger(prepared):
    core, db = prepared
    before = count_txns(core, db)
    data = mod._data(core, db)
    assert data["run_id"] == 100
    assert data["api_count"] == 2
    assert data["items"][0]["api_qty"] == 93
    assert data["items"][0]["book_qty"] == 100
    assert count_txns(core, db) == before


def test_manual_review_confirmation_writes_only_selected_delta(prepared):
    core, db = prepared
    result = mod.commit(core, pending(), db)
    assert result["count"] == 1
    assert result["delta"] == -4
    with core._conn(db) as c:
        txns = c.execute("SELECT qty_delta,txn_type FROM inventory_txns ORDER BY id").fetchall()
        assert [int(x["qty_delta"]) for x in txns] == [100, -4]
        assert txns[-1]["txn_type"] == "수동재고대사"
        row = c.execute("SELECT before_qty,api_qty,confirmed_qty FROM rg_reconcile_items").fetchone()
        assert (row["before_qty"], row["api_qty"], row["confirmed_qty"]) == (100,93,96)


def test_double_commit_does_not_adjust_twice(prepared):
    core, db = prepared
    mod.commit(core, pending(), db)
    with pytest.raises(ValueError, match="이미 처리"):
        mod.commit(core, pending(), db)
    assert count_txns(core, db) == 2


def test_changed_erp_stock_blocks_complete_batch(prepared):
    core, db = prepared
    with core._conn(db) as c:
        c.execute("""
            INSERT INTO inventory_txns(txn_date,product_id,warehouse_id,qty_delta,txn_type)
            VALUES('2026-10-13',1,1,-2,'판매차감')
        """)
    with pytest.raises(ValueError, match="ERP 재고가"):
        mod.commit(core, pending(), db)
    assert count_txns(core, db) == 2


def test_new_api_sync_blocks_stale_confirmation(prepared):
    core, db = prepared
    with core._conn(db) as c:
        c.execute("INSERT INTO coupang_api_sync_runs VALUES(101,'inventory','success')")
    with pytest.raises(ValueError, match="API 조회 결과"):
        mod.commit(core, pending(), db)
    assert count_txns(core, db) == 1


def test_return_options_are_not_confirmable(prepared):
    core, db = prepared
    info = mod._data(core, db)
    only_return = [x for x in info["items"] if x["product_id"] == 2][0]
    assert only_return["api_qty"] is None
    editor = pd.DataFrame([{"상품ID":2,"선택":True,"확정수량":1}])
    with pytest.raises(ValueError, match="API 재고가 없는"):
        mod._changes_from_editor(editor, info)
    assert count_txns(core, db) == 1


def test_bad_negative_or_fractional_final_quantities_rejected():
    for value in [-1, 2.5, float("nan"), "hello"]:
        with pytest.raises(ValueError, match="정수"):
            mod._valid_int(value)
