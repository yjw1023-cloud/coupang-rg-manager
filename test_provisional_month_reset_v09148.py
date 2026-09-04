from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import tempfile
import unittest

import provisional_month_reset_v09148 as reset


class FakeCore:
    def __init__(self, root: Path):
        self.DEFAULT_DB = root / "rocketgrowth.db"

    def now_iso(self):
        return "2026-09-04 16:00:00"

    def init_db(self, db):
        Path(db).parent.mkdir(parents=True, exist_ok=True)

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


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.core = FakeCore(Path(self.tmp.name))
        self.core.init_db(self.core.DEFAULT_DB)
        with self.core._conn(self.core.DEFAULT_DB) as con:
            con.executescript(
                """
                CREATE TABLE imports(id INTEGER PRIMARY KEY,data_type TEXT,file_name TEXT,period_start TEXT,period_end TEXT);
                CREATE TABLE sales_stats(id INTEGER PRIMARY KEY,import_id INTEGER,product_id INTEGER,net_qty REAL);
                CREATE TABLE provisional_pnl_snapshots(import_id INTEGER PRIMARY KEY,period_start TEXT,period_end TEXT);
                CREATE TABLE inventory_txns(id INTEGER PRIMARY KEY,qty_delta REAL,txn_type TEXT,ref_no TEXT);
                CREATE TABLE coupang_rg_order_items(order_id TEXT,item_index INTEGER,paid_date TEXT);
                CREATE TABLE coupang_return_requests(receipt_id TEXT,created_date TEXT);
                CREATE TABLE coupang_return_items(receipt_id TEXT,item_index INTEGER,created_date TEXT);
                CREATE TABLE coupang_return_withdrawals(cancel_id TEXT,item_index INTEGER,created_date TEXT);
                CREATE TABLE coupang_revenue_items(order_id TEXT,recognition_date TEXT);
                CREATE TABLE provisional_ad_report_imports(id INTEGER PRIMARY KEY,period_start TEXT,period_end TEXT);
                CREATE TABLE coupang_api_sync_runs(
                    id INTEGER PRIMARY KEY,
                    sync_type TEXT,
                    period_start TEXT,
                    period_end TEXT,
                    status TEXT,
                    message TEXT
                );
                """
            )
            con.executemany(
                "INSERT INTO imports VALUES(?,?,?,?,?)",
                [
                    (1, "sales_stats", "sep.xlsx", "2026-09-01", "2026-09-03"),
                    (2, "sales_stats", "aug.xlsx", "2026-08-01", "2026-08-31"),
                    (3, "sales_stats", "cross.xlsx", "2026-08-30", "2026-09-02"),
                ],
            )
            con.executemany(
                "INSERT INTO sales_stats VALUES(?,?,?,?)",
                [(1, 1, 1, 5), (2, 2, 1, 7), (3, 3, 1, 9)],
            )
            con.execute("INSERT INTO provisional_pnl_snapshots VALUES(1,'2026-09-01','2026-09-03')")
            con.executemany(
                "INSERT INTO inventory_txns VALUES(?,?,?,?)",
                [
                    (1, -5, "판매차감", "SALESSTAT-1"),
                    (2, -7, "판매차감", "SALESSTAT-2"),
                    (3, -9, "판매차감", "SALESSTAT-3"),
                ],
            )
            con.executemany(
                "INSERT INTO coupang_rg_order_items VALUES(?,?,?)",
                [("SEP", 0, "2026-09-02"), ("AUG", 0, "2026-08-02")],
            )
            con.executemany(
                "INSERT INTO coupang_return_requests VALUES(?,?)",
                [("SEP", "2026-09-03"), ("AUG", "2026-08-03")],
            )
            con.executemany(
                "INSERT INTO coupang_return_items VALUES(?,?,?)",
                [("SEP", 0, "2026-09-03"), ("AUG", 0, "2026-08-03")],
            )
            con.executemany(
                "INSERT INTO coupang_return_withdrawals VALUES(?,?,?)",
                [("SEP", 0, "2026-09-04"), ("AUG", 0, "2026-08-04")],
            )
            con.execute("INSERT INTO coupang_revenue_items VALUES('SEP','2026-09-02')")
            con.execute("INSERT INTO provisional_ad_report_imports VALUES(1,'2026-09-01','2026-09-04')")
            con.executemany(
                "INSERT INTO coupang_api_sync_runs VALUES(?,?,?,?,?,?)",
                [
                    (1, "orders", "2026-09-01", "2026-09-04", "success", "sep orders"),
                    (2, "returns", "2026-09-01", "2026-09-04", "success", "sep returns"),
                    (3, "revenue", "2026-09-01", "2026-09-04", "success", "sep revenue"),
                    (4, "orders", "2026-08-01", "2026-08-31", "success", "aug orders"),
                ],
            )
        self.original_current_month = reset.current_month
        reset.current_month = lambda: "2026-09"

    def tearDown(self):
        reset.current_month = self.original_current_month
        self.tmp.cleanup()

    def test_reset_clears_only_current_provisional_sales_sources(self):
        before = reset.inspect_month(self.core, "2026-09")
        self.assertEqual(before["sales_imports"], 1)
        self.assertEqual(before["cross_month_sales_imports"], 1)
        self.assertEqual(before["api_orders"], 1)
        self.assertEqual(before["api_returns"], 1)
        self.assertEqual(before["api_withdrawals"], 1)

        result = reset.reset_month(self.core, "2026-09")
        self.assertEqual(result["sales_imports"], 1)
        self.assertEqual(result["inventory_deduction_qty"], 5)
        self.assertEqual(result["api_sync_runs_reset"], 2)

        with self.core._conn(self.core.DEFAULT_DB) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM imports WHERE id=1").fetchone()["n"], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM imports WHERE id=2").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM imports WHERE id=3").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM inventory_txns WHERE ref_no='SALESSTAT-1'").fetchone()["n"], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM inventory_txns WHERE ref_no='SALESSTAT-2'").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM coupang_rg_order_items WHERE paid_date LIKE '2026-09%'").fetchone()["n"], 0)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM coupang_rg_order_items WHERE paid_date LIKE '2026-08%'").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM coupang_revenue_items").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM provisional_ad_report_imports").fetchone()["n"], 1)
            self.assertEqual(con.execute("SELECT COUNT(*) n FROM provisional_month_reset_log").fetchone()["n"], 1)
            statuses = {
                int(r["id"]): str(r["status"])
                for r in con.execute("SELECT id,status FROM coupang_api_sync_runs ORDER BY id")
            }
            self.assertEqual(statuses[1], "reset")
            self.assertEqual(statuses[2], "reset")
            self.assertEqual(statuses[3], "success")
            self.assertEqual(statuses[4], "success")
            self.assertIn(
                "당월 잠정실적 초기화 2026-09",
                con.execute("SELECT message FROM coupang_api_sync_runs WHERE id=1").fetchone()["message"],
            )

    def test_past_month_cannot_be_reset(self):
        with self.assertRaises(ValueError):
            reset.reset_month(self.core, "2026-08")


if __name__ == "__main__":
    unittest.main()
