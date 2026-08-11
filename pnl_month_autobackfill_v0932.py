"""RG Manager v0.9.32 monthly provisional P&L auto-backfill.

Monthly provisional P&L must not depend on the user opening the per-file P&L page.
When the selected month contains sales-stat imports without a provisional snapshot,
recalculate those imports directly from the database using the same current P&L
rules and save the final rows into provisional_pnl_snapshots.
"""
from __future__ import annotations

from calendar import monthrange
from typing import Any

import pandas as pd


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = [int(x) for x in str(month).split("-")]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{monthrange(y, m)[1]:02d}"


def _missing_imports(core, db, month: str):
    start, end = _month_bounds(month)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS provisional_pnl_snapshots(
                import_id INTEGER PRIMARY KEY,
                file_name TEXT,
                period_start TEXT,
                period_end TEXT,
                captured_at TEXT NOT NULL,
                rows_json TEXT NOT NULL,
                totals_json TEXT NOT NULL
            )"""
        )
        rows = c.execute(
            """SELECT i.id,i.file_name,i.period_start,i.period_end
               FROM imports i
               LEFT JOIN provisional_pnl_snapshots s ON s.import_id=i.id
               WHERE i.data_type='sales_stats'
                 AND i.period_end>=?
                 AND i.period_start<=?
                 AND s.import_id IS NULL
               ORDER BY i.period_start,i.period_end,i.id""",
            (start, end),
        ).fetchall()
    return [dict(r) for r in rows]


def _matching_ad_import(core, db, period_start: str, period_end: str):
    with core._conn(db) as c:
        row = c.execute(
            """SELECT id
               FROM imports
               WHERE data_type='ad_performance'
                 AND period_start=? AND period_end=?
               ORDER BY id DESC LIMIT 1""",
            (str(period_start), str(period_end)),
        ).fetchone()
    return int(row["id"]) if row else None


def _to_display_df(raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame(
            columns=[
                "옵션ID", "상품명", "판매수량", "예상 실현단가", "예상매출",
                "원가/개", "매출원가", "판매수수료", "입출고비", "배송비",
                "반품충당", "광고비", "광고제외이익", "예상이익", "이익률(%)",
            ]
        )

    def col(name, default=0.0):
        if name in raw.columns:
            return raw[name]
        return pd.Series(default, index=raw.index)

    out = pd.DataFrame(index=raw.index)
    out["옵션ID"] = col("option_id", "").fillna("").astype(str)
    out["상품명"] = col("name", "").fillna("").astype(str)
    out["판매수량"] = pd.to_numeric(col("net_qty"), errors="coerce").fillna(0.0)
    out["예상 실현단가"] = pd.to_numeric(col("sale_unit"), errors="coerce").fillna(0.0)
    out["예상매출"] = pd.to_numeric(col("expected_revenue"), errors="coerce").fillna(0.0)
    out["원가/개"] = pd.to_numeric(col("unit_cost"), errors="coerce").fillna(0.0)
    out["매출원가"] = -pd.to_numeric(col("cogs"), errors="coerce").fillna(0.0)
    out["판매수수료"] = -pd.to_numeric(col("expected_commission"), errors="coerce").fillna(0.0)
    out["입출고비"] = -pd.to_numeric(col("expected_inout"), errors="coerce").fillna(0.0)
    out["배송비"] = -pd.to_numeric(col("expected_delivery"), errors="coerce").fillna(0.0)
    out["반품충당"] = -pd.to_numeric(col("expected_return_reserve"), errors="coerce").fillna(0.0)
    out["광고비"] = -pd.to_numeric(col("ad_spend"), errors="coerce").fillna(0.0)
    out["광고제외이익"] = pd.to_numeric(col("profit_ex_ad"), errors="coerce").fillna(0.0)
    out["예상이익"] = pd.to_numeric(col("profit"), errors="coerce").fillna(0.0)
    out["이익률(%)"] = pd.to_numeric(col("margin_pct"), errors="coerce").fillna(0.0)
    return out.reset_index(drop=True)


def _finalize(core, db, display_df: pd.DataFrame) -> pd.DataFrame:
    import provisional_pnl_ui_v0913 as ui

    prepared = ui._apply_existing_rules(core, db, display_df)
    prepared = ui._recalculate(prepared)
    return prepared


def backfill_month(core, month: str, db_path=None) -> dict:
    db = db_path or core.DEFAULT_DB

    import pnl_views_v0912 as views
    import pnl_snapshot_fix_v0929 as snapshot_fix

    views._ensure_schema(core, db)
    missing = _missing_imports(core, db, month)
    result = {"attempted": len(missing), "saved": 0, "failed": []}

    for imp in missing:
        if str(imp["period_start"])[:7] != str(month) or str(imp["period_end"])[:7] != str(month):
            continue

        try:
            ad_id = _matching_ad_import(
                core, db, str(imp["period_start"]), str(imp["period_end"])
            )
            raw, _meta = core.estimated_pnl(int(imp["id"]), ad_id)
            display = _to_display_df(raw)
            prepared = _finalize(core, db, display)
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

    return result
