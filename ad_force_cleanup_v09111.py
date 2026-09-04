"""RG Manager v0.9.151 runtime bootstrap.

This module is invoked directly from app.py on every Streamlit rerun and is
explicitly purged from Python's module cache before import. Besides the existing
advertising cleanup/recent-input bootstrap, it also runs the idempotent requested-
product/BOM seed, the v0.9.149 inventory/API separation patch, the v0.9.150
sales-stat gross/cancellation quantity preservation patch, and the v0.9.151
SQLite rowid hotfix for sales-stat return enrichment.
"""
from __future__ import annotations

TARGET_FILE = "A00577001_pa_total_campaign_20260801_20260811.xlsx"
TARGET_START = "2026-08-01"
TARGET_END = "2026-08-11"
FLAG = "v0.9.111_runtime_force_delete_20260801_20260811_ad"


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _apply_recent_input_unify(core):
    try:
        import recent_input_unify_v09112
        return recent_input_unify_v09112.apply(core)
    except Exception as exc:
        print(f"RG Manager v0.9.112 recent-input unification failed: {exc}")
        return {"patched": False, "error": str(exc)}


def _apply_requested_product_seed(core, db):
    try:
        import importlib
        import requested_product_seed_v09133
        requested_product_seed_v09133 = importlib.reload(requested_product_seed_v09133)
        return requested_product_seed_v09133.apply(core, db)
    except Exception as exc:
        print(f"RG Manager v0.9.134 requested product/BOM seed failed: {exc}")
        return {"ok": False, "finished_count": 0, "bom_count": 0, "unresolved": [{"reason": str(exc)}]}


def _apply_inventory_api_separation(core, db):
    try:
        import importlib
        import coupang_api_sync_v09140
        import inventory_api_separation_v09149
        import inventory_ui_v084
        inventory_api_separation_v09149 = importlib.reload(inventory_api_separation_v09149)
        return inventory_api_separation_v09149.apply(
            core,
            coupang_api_sync_v09140,
            inventory_ui_v084,
            db,
        )
    except Exception as exc:
        print(f"RG Manager v0.9.149 inventory/API separation failed: {exc}")
        return {"api_inventory_read_only": False, "error": str(exc)}


def _apply_sales_stats_returns(core, db):
    try:
        import importlib
        import return_management_v093
        import sales_quantity_v0965
        import sales_stats_returns_v09150
        import sales_stats_returns_hotfix_v09151

        sales_stats_returns_v09150 = importlib.reload(sales_stats_returns_v09150)
        sales_stats_returns_v09150.apply(
            core,
            db,
            return_management_v093,
            sales_quantity_v0965,
        )
        sales_stats_returns_hotfix_v09151 = importlib.reload(
            sales_stats_returns_hotfix_v09151
        )
        sales_stats_returns_hotfix_v09151.apply(sales_stats_returns_v09150)
        return {"ok": True, "source": "sales_stats_excel", "hotfix": "v0.9.151"}
    except Exception as exc:
        print(f"RG Manager v0.9.151 sales-stat return preservation failed: {exc}")
        return {"ok": False, "error": str(exc)}


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)

    # Must run on every rerun, even when the historical ad-cleanup flag already
    # exists. This makes updater-applied DB/bootstrap changes effective immediately.
    recent_result = _apply_recent_input_unify(core)
    product_seed_result = _apply_requested_product_seed(core, db)
    inventory_api_result = _apply_inventory_api_separation(core, db)
    sales_stats_return_result = _apply_sales_stats_returns(core, db)

    result = {
        "already_applied": False,
        "canonical_deleted": 0,
        "legacy_deleted": 0,
        "ad_rows_deleted": 0,
        "flag_written": False,
        "recent_input_unify": recent_result,
        "requested_product_seed_v09133": product_seed_result,
        "inventory_api_separation_v09149": inventory_api_result,
        "sales_stats_returns_v09150": sales_stats_return_result,
    }

    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS rg_patch_flags(
                   patch_key TEXT PRIMARY KEY,
                   applied_at TEXT NOT NULL
               )"""
        )
        if c.execute(
            "SELECT 1 FROM rg_patch_flags WHERE patch_key=?", (FLAG,)
        ).fetchone():
            result["already_applied"] = True
            return result

        if _exists(c, "provisional_ad_report_imports"):
            rows = c.execute(
                """SELECT id FROM provisional_ad_report_imports
                   WHERE file_name=? AND period_start=? AND period_end=?""",
                (TARGET_FILE, TARGET_START, TARGET_END),
            ).fetchall()
            for r in rows:
                rid = int(r["id"])
                if _exists(c, "provisional_ad_report_items"):
                    c.execute(
                        "DELETE FROM provisional_ad_report_items WHERE import_id=?",
                        (rid,),
                    )
                c.execute("DELETE FROM provisional_ad_report_imports WHERE id=?", (rid,))
                result["canonical_deleted"] += 1

        if _exists(c, "imports"):
            cols = _cols(c, "imports")
            where = ["data_type='ad_performance'", "file_name=?"]
            params = [TARGET_FILE]
            if "period_start" in cols:
                where.append("COALESCE(period_start,'')=?")
                params.append(TARGET_START)
            if "period_end" in cols:
                where.append("COALESCE(period_end,'')=?")
                params.append(TARGET_END)
            rows = c.execute(
                "SELECT id FROM imports WHERE " + " AND ".join(where),
                tuple(params),
            ).fetchall()
            for r in rows:
                iid = int(r["id"])
                if _exists(c, "ad_performance"):
                    cur = c.execute("DELETE FROM ad_performance WHERE import_id=?", (iid,))
                    try:
                        result["ad_rows_deleted"] += max(int(cur.rowcount or 0), 0)
                    except Exception:
                        pass
                c.execute(
                    "DELETE FROM imports WHERE id=? AND data_type='ad_performance'",
                    (iid,),
                )
                result["legacy_deleted"] += 1

        deleted = result["canonical_deleted"] + result["legacy_deleted"]
        if deleted > 0:
            c.execute(
                "INSERT INTO rg_patch_flags(patch_key,applied_at) VALUES(?,?)",
                (FLAG, core.now_iso()),
            )
            result["flag_written"] = True

    return result
