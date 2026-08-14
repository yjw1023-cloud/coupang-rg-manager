"""RG Manager v0.9.112 runtime advertising cleanup/bootstrap.

This module is invoked directly from app.py on every Streamlit rerun.

It retains the v0.9.111 one-time cleanup for the explicitly authorized stale
8/1~8/11 report, and from v0.9.112 also activates the unified Recent Input
History patch so advertising reports uploaded from either ERP screen are shown
in the same history table.
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


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)

    # Must run even when the old one-time cleanup flag is already present.
    # Streamlit updater reruns app.py without necessarily restarting Python.
    recent_result = _apply_recent_input_unify(core)

    result = {
        "already_applied": False,
        "canonical_deleted": 0,
        "legacy_deleted": 0,
        "ad_rows_deleted": 0,
        "flag_written": False,
        "recent_input_unify": recent_result,
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

        # Canonical provisional advertising data.
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

        # Generic recent-input history and option-level ad rows.
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
