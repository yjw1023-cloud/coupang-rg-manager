from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from urllib.parse import urlparse, parse_qs

import coupang_api_sync_v09140 as api


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeCore:
    def __init__(self, root: Path):
        self.DEFAULT_DB = root / "data" / "rocketgrowth.db"

    def now_iso(self):
        return "2026-09-03 12:00:00"

    @contextmanager
    def _conn(self, db):
        con = sqlite3.connect(str(db))
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def init_db(self, db):
        Path(db).parent.mkdir(parents=True, exist_ok=True)
        with self._conn(db) as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS products(
                    id INTEGER PRIMARY KEY,
                    item_code TEXT,
                    option_id TEXT,
                    name TEXT,
                    unit_cost REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS warehouses(
                    id INTEGER PRIMARY KEY,
                    name TEXT UNIQUE
                );
                CREATE TABLE IF NOT EXISTS inventory_txns(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    txn_date TEXT,
                    product_id INTEGER,
                    warehouse_id INTEGER,
                    qty_delta REAL,
                    txn_type TEXT,
                    ref_no TEXT,
                    memo TEXT,
                    created_at TEXT
                );
                INSERT OR IGNORE INTO warehouses(id,name) VALUES(1,'쿠팡RG');
                INSERT OR IGNORE INTO warehouses(id,name) VALUES(2,'반품창고');
                """
            )


class CoupangClientTests(unittest.TestCase):
    def setUp(self):
        self.credentials = api.Credentials("A00123456", "ACCESS", "SECRET")
        self.fixed = datetime(2026, 9, 3, 1, 2, 3, tzinfo=timezone.utc)

    def test_authorization_matches_official_hmac_shape(self):
        client = api.CoupangClient(self.credentials, now=lambda: self.fixed)
        path = "/test/path"
        query = "a=1&b=hello"
        stamp = "260903T010203Z"
        expected = hmac.new(
            b"SECRET", (stamp + "GET" + path + query).encode(), hashlib.sha256
        ).hexdigest()
        auth = client.authorization("GET", path, query, stamp)
        self.assertIn("algorithm=HmacSHA256", auth)
        self.assertIn("access-key=ACCESS", auth)
        self.assertIn(f"signature={expected}", auth)

    def test_order_pagination_uses_returned_next_token(self):
        calls = []

        def opener(request, timeout):
            calls.append(request)
            query = parse_qs(urlparse(request.full_url).query)
            if "nextToken" not in query:
                return _Response({"data": [{"orderId": 1}], "nextToken": "NEXT"})
            self.assertEqual(query["nextToken"], ["NEXT"])
            return _Response({"data": [{"orderId": 2}]})

        client = api.CoupangClient(
            self.credentials, opener=opener, now=lambda: self.fixed
        )
        rows = client.orders("2026-09-01", "2026-09-03")
        self.assertEqual([x["orderId"] for x in rows], [1, 2])
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("Authorization" in x.headers for x in calls))

    def test_long_order_range_is_split_without_overlap(self):
        ranges = []

        def opener(request, timeout):
            q = parse_qs(urlparse(request.full_url).query)
            ranges.append((q["paidDateFrom"][0], q["paidDateTo"][0]))
            return _Response({"data": []})

        client = api.CoupangClient(
            self.credentials, opener=opener, now=lambda: self.fixed
        )
        client.orders("2026-07-01", "2026-09-03")
        self.assertEqual(
            ranges,
            [
                ("20260701", "20260730"),
                ("20260731", "20260829"),
                ("20260830", "20260903"),
            ],
        )


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = FakeCore(Path(self.tmp.name))
        self.core.init_db(self.core.DEFAULT_DB)
        with self.core._conn(self.core.DEFAULT_DB) as con:
            con.execute(
                "INSERT INTO products(id,item_code,option_id,name,unit_cost) VALUES(1,'CP-7001','7001','상품A',1500)"
            )
            con.execute(
                """INSERT INTO inventory_txns
                   (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                   VALUES('2026-09-01',1,1,3,'기초','BASE','',?)""",
                (self.core.now_iso(),),
            )

    def tearDown(self):
        self.tmp.cleanup()

    def test_inventory_sync_reconciles_once_and_keeps_unmatched(self):
        class Client:
            def inventory(self):
                return [
                    {
                        "vendorItemId": 7001,
                        "externalSkuId": "SKU-A",
                        "inventoryDetails": {"totalOrderableQuantity": 10},
                        "salesCountMap": {"SALES_COUNT_LAST_THIRTY_DAYS": 7},
                    },
                    {
                        "vendorItemId": 9999,
                        "inventoryDetails": {"totalOrderableQuantity": 4},
                        "salesCountMap": {},
                    },
                ]

        first = api.sync_inventory(self.core, Client())
        second = api.sync_inventory(self.core, Client())
        self.assertEqual(first["adjusted_rows"], 1)
        self.assertEqual(first["adjusted_qty"], 7)
        self.assertEqual(second["adjusted_rows"], 0)
        self.assertEqual(first["matched"], 1)
        with self.core._conn(self.core.DEFAULT_DB) as con:
            balance = con.execute(
                "SELECT SUM(qty_delta) q FROM inventory_txns WHERE product_id=1 AND warehouse_id=1"
            ).fetchone()["q"]
            unmatched = con.execute(
                "SELECT COUNT(*) n FROM coupang_rg_inventory WHERE product_id IS NULL"
            ).fetchone()["n"]
        self.assertEqual(balance, 10)
        self.assertEqual(unmatched, 1)

    def test_inventory_separates_normal_and_sums_return_options_once(self):
        class OrderClient:
            def orders(self, start, end):
                return [{
                    "orderId": "ORDER-1",
                    "paidAt": "2026-09-01T00:00:00Z",
                    "orderItems": [
                        {"vendorItemId": 8001, "productName": "상품A", "salesQuantity": 1},
                        {"vendorItemId": 8002, "productName": "상품A", "salesQuantity": 1},
                        {"vendorItemId": 8003, "productName": "상품A", "salesQuantity": 1},
                    ],
                }]

        # Same unique name + a different option id becomes a permanent return
        # alias of normal option 7001.
        api.sync_orders(self.core, OrderClient(), "2026-09-01", "2026-09-01")

        class InventoryClient:
            def inventory(self):
                return [
                    {"vendorItemId": 7001, "inventoryDetails": {"totalOrderableQuantity": 100}},
                    {"vendorItemId": 8001, "inventoryDetails": {"totalOrderableQuantity": 3}},
                    {"vendorItemId": 8002, "inventoryDetails": {"totalOrderableQuantity": 2}},
                    {"vendorItemId": 8003, "inventoryDetails": {"totalOrderableQuantity": 1}},
                ]

        first = api.sync_inventory(self.core, InventoryClient())
        second = api.sync_inventory(self.core, InventoryClient())
        self.assertEqual(first["normal_adjusted_rows"], 1)
        self.assertEqual(first["return_adjusted_rows"], 1)
        self.assertEqual(second["adjusted_rows"], 0)
        with self.core._conn(self.core.DEFAULT_DB) as con:
            balances = {
                r["name"]: r["qty"]
                for r in con.execute(
                    """SELECT w.name,COALESCE(SUM(t.qty_delta),0) qty
                       FROM warehouses w LEFT JOIN inventory_txns t ON t.warehouse_id=w.id
                       GROUP BY w.id,w.name"""
                )
            }
            aliases = {
                r["discount_option_id"]: r["parent_product_id"]
                for r in con.execute(
                    "SELECT discount_option_id,parent_product_id FROM return_discount_aliases"
                )
            }
            kinds = {
                r["vendor_item_id"]: r["stock_type"]
                for r in con.execute(
                    "SELECT vendor_item_id,stock_type FROM coupang_rg_inventory"
                )
            }
        self.assertEqual(balances["쿠팡RG"], 100)
        self.assertEqual(balances["반품창고"], 6)
        self.assertEqual(aliases, {"8001": 1, "8002": 1, "8003": 1})
        self.assertEqual(kinds["7001"], "normal")
        self.assertTrue(all(kinds[x] == "return" for x in ("8001", "8002", "8003")))

    def test_ambiguous_same_name_stays_unmatched_and_does_not_move_return_stock(self):
        with self.core._conn(self.core.DEFAULT_DB) as con:
            con.execute(
                "INSERT INTO products(id,item_code,option_id,name,unit_cost) "
                "VALUES(2,'CP-7002','7002','상품A',2000)"
            )

        class OrderClient:
            def orders(self, start, end):
                return [{
                    "orderId": "ORDER-X",
                    "paidAt": "2026-09-01T00:00:00Z",
                    "orderItems": [{
                        "vendorItemId": 9001,
                        "productName": "상품A",
                        "salesQuantity": 1,
                    }],
                }]

        class InventoryClient:
            def inventory(self):
                return [{
                    "vendorItemId": 9001,
                    "inventoryDetails": {"totalOrderableQuantity": 4},
                }]

        api.sync_orders(self.core, OrderClient(), "2026-09-01", "2026-09-01")
        result = api.sync_inventory(self.core, InventoryClient())
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["return_adjusted_rows"], 0)
        with self.core._conn(self.core.DEFAULT_DB) as con:
            alias_count = con.execute(
                "SELECT COUNT(*) n FROM return_discount_aliases WHERE discount_option_id='9001'"
            ).fetchone()["n"]
            return_qty = con.execute(
                """SELECT COALESCE(SUM(t.qty_delta),0) q
                   FROM inventory_txns t JOIN warehouses w ON w.id=t.warehouse_id
                   WHERE w.name='반품창고'"""
            ).fetchone()["q"]
        self.assertEqual(alias_count, 0)
        self.assertEqual(return_qty, 0)

    def test_explicit_return_mapping_overrides_legacy_child_product(self):
        with self.core._conn(self.core.DEFAULT_DB) as con:
            con.execute(
                "INSERT INTO products(id,item_code,option_id,name,unit_cost) "
                "VALUES(2,'CP-8001','8001','상품A',0)"
            )
        api.save_return_mapping(self.core, "8001", 1, "상품A")

        class Client:
            def inventory(self):
                return [
                    {"vendorItemId": 7001, "inventoryDetails": {"totalOrderableQuantity": 10}},
                    {"vendorItemId": 8001, "inventoryDetails": {"totalOrderableQuantity": 4}},
                ]

        result = api.sync_inventory(self.core, Client())
        self.assertEqual(result["matched"], 2)
        with self.core._conn(self.core.DEFAULT_DB) as con:
            row = con.execute(
                """SELECT product_id,stock_type FROM coupang_rg_inventory
                   WHERE vendor_item_id='8001'"""
            ).fetchone()
            return_qty = con.execute(
                """SELECT COALESCE(SUM(t.qty_delta),0) q
                   FROM inventory_txns t JOIN warehouses w ON w.id=t.warehouse_id
                   WHERE w.name='반품창고' AND t.product_id=1"""
            ).fetchone()["q"]
            wrong_child_qty = con.execute(
                """SELECT COALESCE(SUM(t.qty_delta),0) q
                   FROM inventory_txns t WHERE t.product_id=2"""
            ).fetchone()["q"]
        self.assertEqual(row["product_id"], 1)
        self.assertEqual(row["stock_type"], "return")
        self.assertEqual(return_qty, 4)
        self.assertEqual(wrong_child_qty, 0)

    def test_revenue_sync_replaces_same_period_and_links_option(self):
        class Client:
            def revenue(self, start, end):
                return [{
                    "orderId": 123,
                    "saleType": "SALE",
                    "saleDate": "2026-09-01",
                    "recognitionDate": "2026-09-02",
                    "settlementDate": "2026-09-10",
                    "items": [{
                        "vendorItemId": 7001,
                        "productName": "상품A",
                        "vendorItemName": "상품A 옵션",
                        "quantity": 2,
                        "salePrice": 20000,
                        "saleAmount": 19000,
                        "serviceFee": 1900,
                        "serviceFeeVat": 190,
                        "settlementAmount": 16910,
                    }],
                }]

        one = api.sync_revenue(self.core, Client(), "2026-09-01", "2026-09-03")
        two = api.sync_revenue(self.core, Client(), "2026-09-01", "2026-09-03")
        self.assertEqual(one["rows"], 1)
        self.assertEqual(two["rows"], 1)
        with self.core._conn(self.core.DEFAULT_DB) as con:
            row = con.execute(
                "SELECT COUNT(*) n,MAX(product_id) pid,MAX(sale_amount) amount FROM coupang_revenue_items"
            ).fetchone()
        self.assertEqual(row["n"], 1)
        self.assertEqual(row["pid"], 1)
        self.assertEqual(row["amount"], 19000)

    def test_complete_revenue_month_replaces_legacy_sales_without_doubling(self):
        import pandas as pd

        class Client:
            def revenue(self, start, end):
                return [{
                    "orderId": 123,
                    "saleType": "SALE",
                    "saleDate": "2026-09-01",
                    "recognitionDate": "2026-09-02",
                    "settlementDate": "2026-09-10",
                    "items": [{
                        "vendorItemId": 7001,
                        "quantity": 2,
                        "saleAmount": 19000,
                        "serviceFee": 1900,
                        "serviceFeeVat": 190,
                        "settlementAmount": 16910,
                    }],
                }]

        api.sync_revenue(self.core, Client(), "2026-09-01", "2026-09-30")
        legacy = pd.DataFrame([{
            "product_id": 1,
            "option_id": "7001",
            "qty": 99,
            "realized_sales": 999999,
            "cogs": 3000,
            "commission": 99999,
            "inout": 100,
            "delivery": 200,
            "return_pickup": 0,
            "return_restock": 0,
        }])
        self.core.confirmed_monthly_pnl = lambda month: (legacy.copy(), {"ad_billable_total": 500})
        self.core.monthly_available = lambda: []
        api._patch_confirmed_pnl(self.core, self.core.DEFAULT_DB)
        result, meta = self.core.confirmed_monthly_pnl("2026-09")
        self.assertEqual(float(result["realized_sales"].sum()), 19000)
        self.assertEqual(float(result["commission"].sum()), 2090)
        self.assertEqual(float(result["inout"].sum()), 100)
        self.assertEqual(float(result["delivery"].sum()), 200)
        self.assertTrue(meta["api_revenue_source"])
        self.assertIn("2026-09", self.core.monthly_available())

    def test_patch_source_adds_page_before_grouped_navigation(self):
        source = '''page = st.sidebar.radio("메뉴", [
        "🏠  대시보드",
        "📥  기존ERP 이관",
        "📦  재고관리",
])

# ------------------------------
# Inventory
# ------------------------------
elif page == "📦  재고관리":
    pass
'''
        patched = api.patch_source(source)
        self.assertIn(api.PAGE_LABEL, patched)
        self.assertIn("coupang_api_sync_v09140.render_page", patched)
        self.assertEqual(patched.count(api._MARKER), 1)
        self.assertEqual(api.patch_source(patched), patched)


if __name__ == "__main__":
    unittest.main()
