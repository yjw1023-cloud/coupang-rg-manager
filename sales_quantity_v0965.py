"""v0.9.65 monthly sales quantity semantics.

The legacy provisional P&L used net_qty as the visible `판매수량`. That is useful
for inventory/COGS but confusing to an operator: a product can have a real sale
and a cancellation in the same month and appear as zero sales.

This module reads the actual sales/cancellation columns preserved in sales_stats
when they exist and exposes three distinct quantities:
- 판매수량: gross units sold
- 취소수량: cancellations/returns counted separately
- 반품철회수량: later withdrawals that restore a return request
- 순판매수량: the signed net quantity used by P&L/inventory arithmetic

v0.9.181 policy:
- monthly sales quantity is sourced from imported sales_stats only;
- legacy Coupang order/return API tables are intentionally ignored;
- older imports can have the later-added sales_qty/cancel_qty columns present in
  the schema but empty/zero for historical rows.  When gross sales is zero while
  net_qty proves positive sales activity, reconstruct gross sales as
  max(0, net_qty + cancel_qty).  If historical cancel_qty is also unavailable,
  net_qty itself becomes the conservative sales quantity;
- this legacy fallback lets already-imported historical sales files populate goal
  Excel quantity/unit-price columns without requiring the operator to re-upload them.
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
    """Return option-ID quantity counts from imported sales_stats for one month."""
    start, end = _month_bounds(month)
    core.init_db(db)
    with core._conn(db) as con:
        sc = _cols(con, "sales_stats")
        ic = _cols(con, "imports")
        pc = _cols(con, "products")

        if not {"product_id", "import_id"}.issubset(sc) or not {"id", "period_start", "period_end"}.issubset(ic):
            return {}, {"exact": False, "reason": "판매통계 수량 구조 없음", "source": "sales_stats"}
        if not {"id", "option_id", "item_code"}.issubset(pc):
            return {}, {"exact": False, "reason": "상품 옵션ID 구조 없음", "source": "sales_stats"}

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
            return {}, {"exact": False, "reason": "판매수량 컬럼 없음", "source": "sales_stats"}

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
    legacy_fallback_rows = 0
    for r in rows:
        oid = _oid(r["option_id"]) or _oid(r["item_code"])
        if not oid:
            continue
        gross = _num(r["gross_qty"])
        cancel = abs(_num(r["cancel_qty"]))
        net = _num(r["net_qty"])

        # Historical imports created before sales_qty/cancel_qty enrichment can
        # have those columns in the schema but zero/NULL in the actual old rows.
        # In that case the existence of the column must NOT be treated as proof
        # that gross sales was truly zero.  net_qty is the older authoritative
        # quantity retained by the ERP, so reconstruct gross sales from it.
        if net_col is not None and abs(gross) <= 1e-12 and net > 1e-12:
            gross = max(0.0, net + cancel)
            legacy_fallback_rows += 1
        elif gross_col is None and net_col is not None and cancel_col is not None:
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
            "withdrawal_qty": 0.0,
            "net_qty": net,
        }

    exact = bool(gross_col or (net_col and cancel_col)) and legacy_fallback_rows == 0
    return out, {
        "exact": exact,
        "sales_exact": exact,
        "returns_exact": bool(cancel_col),
        "source": "sales_stats_legacy_net_fallback" if legacy_fallback_rows else "sales_stats",
        "gross_col": gross_col,
        "cancel_col": cancel_col,
        "net_col": net_col,
        "rows": len(out),
        "legacy_fallback_rows": legacy_fallback_rows,
    }


def annotate_month(core, db, month: str, view: pd.DataFrame):
    """Replace visible quantity semantics while leaving financial values intact."""
    if not isinstance(view, pd.DataFrame) or view.empty or "옵션ID" not in view.columns:
        return view, {"exact": False, "rows": 0}

    counts, meta = month_counts(core, db, month)
    out = view.copy()
    if "취소수량" not in out.columns:
        out["취소수량"] = 0.0
    if "반품철회수량" not in out.columns:
        out["반품철회수량"] = 0.0
    if "순판매수량" not in out.columns:
        out["순판매수량"] = out["판매수량"] if "판매수량" in out.columns else 0.0

    matched = 0
    for idx in out.index:
        oid = _oid(out.at[idx, "옵션ID"])
        info = counts.get(oid)
        if not info:
            if "판매수량" in out.columns:
                out.at[idx, "순판매수량"] = _num(out.at[idx, "판매수량"])
            continue
        if "판매수량" in out.columns:
            out.at[idx, "판매수량"] = info["sales_qty"]
        out.at[idx, "취소수량"] = info["cancel_qty"]
        out.at[idx, "반품철회수량"] = info.get("withdrawal_qty", 0.0)
        out.at[idx, "순판매수량"] = info["net_qty"]
        matched += 1

    desired = []
    extras = {"취소수량", "반품철회수량", "순판매수량"}
    for col in out.columns:
        if col not in extras:
            desired.append(col)
        if col == "판매수량":
            desired.extend(["취소수량", "반품철회수량", "순판매수량"])
    for col in ("취소수량", "반품철회수량", "순판매수량"):
        if col not in desired and col in out.columns:
            desired.append(col)
    out = out[[c for c in desired if c in out.columns]]
    meta = dict(meta)
    meta["matched"] = matched
    return out, meta
