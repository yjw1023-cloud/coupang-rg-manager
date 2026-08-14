"""v0.9.105 refresh stale monthly provisional P&L snapshots.

Problem fixed
-------------
Monthly provisional snapshots can become stale after sales imports or P&L rule
changes. v0.9.105 also changes the automatic expected realized sale unit from the
latest single settlement row to cumulative weighted average realized sale unit.

Rules
-----
- For each selected month, build a fingerprint from the current sales-stat imports.
- v0.9.105 rule version is part of that fingerprint, forcing one clean rebuild.
- Before rebuilding, apply cumulative realized sale unit = total realized sales /
  total positive sold quantity. Manual expected sale unit still has priority.
- Manual monthly overrides and advertising-report data remain stored separately.
"""
from __future__ import annotations

import hashlib
import json
from calendar import monthrange

_RULE_VERSION = "0.9.105-realized-unit-average"


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = (int(x) for x in str(month).split("-"))
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"


def _exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(con, table: str) -> set[str]:
    if not _exists(con, table):
        return set()
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _ensure_state(core, db):
    core.init_db(db)
    with core._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS provisional_snapshot_refresh_state(
                   month TEXT PRIMARY KEY,
                   version_tag TEXT NOT NULL,
                   source_fingerprint TEXT NOT NULL,
                   refreshed_at TEXT NOT NULL
               )"""
        )


def _imports(core, db, month: str):
    start, end = _month_bounds(month)
    with core._conn(db) as con:
        ic = _cols(con, "imports")
        if not {"id", "data_type", "period_start", "period_end"}.issubset(ic):
            return []
        file_hash_expr = "file_hash" if "file_hash" in ic else "''"
        file_name_expr = "file_name" if "file_name" in ic else "''"
        created_expr = "created_at" if "created_at" in ic else "''"
        rows = con.execute(
            f"""SELECT id,{file_hash_expr} file_hash,{file_name_expr} file_name,
                       period_start,period_end,{created_expr} created_at
                FROM imports
                WHERE data_type='sales_stats'
                  AND period_start>=? AND period_end<=?
                ORDER BY period_start,period_end,id""",
            (start, end),
        ).fetchall()
        sc = _cols(con, "sales_stats")
        has_sales = {"import_id", "net_qty"}.issubset(sc)
        result = []
        for r in rows:
            x = dict(r)
            if has_sales:
                s = con.execute(
                    """SELECT COUNT(*) row_count,
                              COALESCE(SUM(net_qty),0) net_qty_sum
                       FROM sales_stats WHERE import_id=?""",
                    (int(r["id"]),),
                ).fetchone()
                x["row_count"] = int(s["row_count"] or 0)
                x["net_qty_sum"] = float(s["net_qty_sum"] or 0)
            result.append(x)
    return result


def _fingerprint(imports) -> str:
    payload = {
        "version": _RULE_VERSION,
        "imports": imports,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _current_state(core, db, month: str):
    _ensure_state(core, db)
    with core._conn(db) as con:
        row = con.execute(
            """SELECT version_tag,source_fingerprint,refreshed_at
               FROM provisional_snapshot_refresh_state WHERE month=?""",
            (str(month),),
        ).fetchone()
    return dict(row) if row else None


def _save_state(core, db, month: str, fingerprint: str):
    with core._conn(db) as con:
        con.execute(
            """INSERT INTO provisional_snapshot_refresh_state
               (month,version_tag,source_fingerprint,refreshed_at)
               VALUES(?,?,?,?)
               ON CONFLICT(month) DO UPDATE SET
                 version_tag=excluded.version_tag,
                 source_fingerprint=excluded.source_fingerprint,
                 refreshed_at=excluded.refreshed_at""",
            (str(month), _RULE_VERSION, str(fingerprint), core.now_iso()),
        )


def refresh_month(core, month: str, db_path=None) -> dict:
    db = db_path or core.DEFAULT_DB

    # v0.9.105: install the weighted-average realized unit rule before any
    # estimated_pnl call. core.estimated_pnl already gives manual_expected_sale
    # priority through _effective(manual, historical).
    import realized_sale_unit_avg_v09105 as unit_avg
    unit_avg.apply(core)

    imports = _imports(core, db, month)
    fingerprint = _fingerprint(imports)
    state = _current_state(core, db, month)
    if state and str(state.get("version_tag")) == _RULE_VERSION and str(state.get("source_fingerprint")) == fingerprint:
        return {"needed": False, "attempted": 0, "saved": 0, "failed": []}

    import pnl_month_autobackfill_v0932 as bf
    import pnl_snapshot_fix_v0929 as snapshot_fix
    import pnl_views_v0912 as views

    views._ensure_schema(core, db)
    result = {"needed": True, "attempted": len(imports), "saved": 0, "failed": []}

    for imp in imports:
        try:
            ad_id = bf._matching_ad_import(
                core, db, str(imp["period_start"]), str(imp["period_end"])
            )
            raw, _meta = core.estimated_pnl(int(imp["id"]), ad_id)
            display = bf._to_display_df(raw)
            prepared = bf._finalize(core, db, display)
            ok = snapshot_fix._save_for_import(
                views, core, db, prepared, int(imp["id"])
            )
            if ok:
                result["saved"] += 1
            else:
                result["failed"].append(
                    {"import_id": int(imp["id"]), "error": "저장할 손익행이 없습니다."}
                )
        except Exception as exc:
            result["failed"].append(
                {"import_id": int(imp["id"]), "error": str(exc)}
            )

    if not result["failed"]:
        _save_state(core, db, month, fingerprint)
    return result
