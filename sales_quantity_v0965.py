"""v0.9.65 monthly sales quantity semantics.

The legacy provisional P&L used net_qty as the visible `판매수량`.  That is useful
for inventory/COGS but confusing to an operator: a product can have a real sale
and a cancellation in the same month and appear as zero sales.

This module reads the actual sales/cancellation columns preserved in sales_stats
when they exist and exposes three distinct quantities:
- 판매수량: gross units sold
- 취소수량: cancellations/returns counted separately
- 순판매수량: the signed net quantity used by P&L/inventory arithmetic

If the DB is an older schema that only has net_qty, it safely falls back to the
existing net quantity and reports that the gross count is not exact.
"""
from __future__ import annotations

import calendar
from typing import Any

import pandas as pd


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(con, table: str) -> set[str]:
    if not _exists(con, table):
        return set()
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _pick(cols: set[str], candidates: tuple[str, ...]) -> str | None:
    lower = {str(c).lower(): str(c) for c in cols}
    for cand in candidates:
        if cand in cols:
            return cand
        hit = lower.get(cand.lower())
        if hit:
            return hit
    return None


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _month_bounds(month: str) -> tuple[str, str]:
    y, m = (int(x) for x in str(month).split("-"))
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}-01", f"{y:04d}-{m:02d}-{last:02d}"


def month_counts(core, db, month: str):
    """Return option-ID quantity counts plus source metadata for one month."""
    start, end = _month_bounds(month)
    core.init_db(db)
    with core._conn(db) as con:
        sc = _cols(con, "sales_stats")
        ic = _cols(con, "imports")
        pc = _cols(con, "products")
        # When the monthly P&L uses manually synchronized Coupang revenue rows,
        # its visible gross/refund/net quantities must come from that same
        # source instead of an older sales-stat Excel import.
        if _exists(con, "coupang_revenue_items") and {"id", "option_id", "item_code"}.issubset(pc):
            api_rows = con.execute(
                """SELECT r.product_id,p.option_id,p.item_code,
                          SUM(CASE WHEN r.sale_type='REFUND' THEN 0 ELSE ABS(r.quantity) END) gross_qty,
                          SUM(CASE WHEN r.sale_type='REFUND' THEN ABS(r.quantity) ELSE 0 END) cancel_qty,
                          SUM(CASE WHEN r.sale_type='REFUND' THEN -ABS(r.quantity)
                                   ELSE ABS(r.quantity) END) net_qty
                   FROM coupang_revenue_items r
                   JOIN products p ON p.id=r.product_id
                   WHERE r.recognition_date>=? AND r.recognition_date<=?
                     AND r.product_id IS NOT NULL
                   GROUP BY r.product_id,p.option_id,p.item_code""",
                (start, end),
            ).fetchall()
            if api_rows:
                api_counts = {}
                for row in api_rows:
                    oid = _oid(row["option_id"]) or _oid(row["item_code"])
                    if oid:
                        api_counts[oid] = {
                            "product_id": int(row["product_id"]),
                            "sales_qty": _num(row["gross_qty"]),
                            "cancel_qty": abs(_num(row["cancel_qty"])),
                            "net_qty": _num(row["net_qty"]),
                        }
                return api_counts, {
                    "exact": True,
                    "source": "coupang_revenue_api",
                    "rows": len(api_counts),
                }
        if not {"product_id", "import_id"}.issubset(sc) or not {"id", "period_start", "period_end"}.issubset(ic):
            return {}, {"exact": False, "reason": "판매통계 수량 구조 없음"}
        if not {"id", "option_id", "item_code"}.issubset(pc):
            return {}, {"exact": False, "reason": "상품 옵션ID 구조 없음"}

        net_col = _pick(sc, ("net_qty", "net_sales_qty", "순판매수량", "순판매상품수"))
        gross_col = _pick(sc, (
            "sales_qty", "sold_qty", "gross_qty", "gross_sales_qty", "order_qty",
            "판매수량", "판매상품수", "주문수량",
        ))
        cancel_col = _pick(sc, (
            "cancel_qty", "cancelled_qty", "canceled_qty", "cancel_count",
            "취소수량", "취소상품수", "취소건수",
        ))

        if not (net_col or gross_col):
            return {}, {"exact": False, "reason": "판매수량 컬럼 없음"}

        gross_expr = (
            f"SUM(CASE WHEN COALESCE(s.{_q(gross_col)},0)>0 THEN COALESCE(s.{_q(gross_col)},0) ELSE 0 END)"
            if gross_col else "0"
        )
        cancel_expr = (
            f"SUM(ABS(COALESCE(s.{_q(cancel_col)},0)))" if cancel_col else "0"
        )
        net_expr = f"SUM(COALESCE(s.{_q(net_col)},0))" if net_col else "0"

        rows = con.execute(
            f"""SELECT s.product_id,p.option_id,p.item_code,
                       {gross_expr} AS gross_qty,
                       {cancel_expr} AS cancel_qty,
                       {net_expr} AS net_qty
                FROM sales_stats s
                JOIN products p ON p.id=s.product_id
                JOIN imports i ON i.id=s.import_id
                WHERE i.data_type='sales_stats'
                  AND i.period_start>=? AND i.period_end<=?
                GROUP BY s.product_id,p.option_id,p.item_code""",
            (start, end),
        ).fetchall()

    out = {}
    for r in rows:
        oid = _oid(r["option_id"]) or _oid(r["item_code"])
        if not oid:
            continue
        gross = _num(r["gross_qty"])
        cancel = abs(_num(r["cancel_qty"]))
        net = _num(r["net_qty"])

        # Derive missing components without discarding sign information.
        if gross_col is None and net_col is not None and cancel_col is not None:
            gross = max(0.0, net + cancel)
        elif gross_col is None and net_col is not None:
            gross = max(0.0, net)
            cancel = max(0.0, -net)

        if cancel_col is None and gross_col is not None and net_col is not None:
            cancel = max(0.0, gross - net)
        if net_col is None:
            net = gross - cancel

        out[oid] = {
            "product_id": int(r["product_id"]),
            "sales_qty": gross,
            "cancel_qty": cancel,
            "net_qty": net,
        }

    exact = bool(gross_col or (net_col and cancel_col))
    return out, {
        "exact": exact,
        "gross_col": gross_col,
        "cancel_col": cancel_col,
        "net_col": net_col,
        "rows": len(out),
    }


def annotate_month(core, db, month: str, view: pd.DataFrame):
    """Replace visible quantity semantics while leaving financial values intact."""
    if not isinstance(view, pd.DataFrame) or view.empty or "옵션ID" not in view.columns:
        return view, {"exact": False, "rows": 0}

    counts, meta = month_counts(core, db, month)
    out = view.copy()
    if "취소수량" not in out.columns:
        out["취소수량"] = 0.0
    if "순판매수량" not in out.columns:
        out["순판매수량"] = out["판매수량"] if "판매수량" in out.columns else 0.0

    matched = 0
    for idx in out.index:
        oid = _oid(out.at[idx, "옵션ID"])
        info = counts.get(oid)
        if not info:
            # Older/unmatched rows keep the historical net quantity semantics.
            if "판매수량" in out.columns:
                out.at[idx, "순판매수량"] = _num(out.at[idx, "판매수량"])
            continue
        if "판매수량" in out.columns:
            out.at[idx, "판매수량"] = info["sales_qty"]
        out.at[idx, "취소수량"] = info["cancel_qty"]
        out.at[idx, "순판매수량"] = info["net_qty"]
        matched += 1

    # Place gross/cancel/net together so the meaning is immediately visible.
    desired = []
    extras = {"취소수량", "순판매수량"}
    for col in out.columns:
        if col not in extras:
            desired.append(col)
        if col == "판매수량":
            desired.extend(["취소수량", "순판매수량"])
    for col in ("취소수량", "순판매수량"):
        if col not in desired and col in out.columns:
            desired.append(col)
    out = out[[c for c in desired if c in out.columns]]
    meta = dict(meta)
    meta["matched"] = matched
    return out, meta
