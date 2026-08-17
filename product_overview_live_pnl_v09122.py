"""v0.9.122 product overview live provisional P&L alignment.

The product overview previously summed stale provisional snapshot values while its
ad card read the latest advertising-performance reports. This made revenue/profit
and ad spend use different calculation states.

This patch replaces product_overview_v0976._provisional_history with a live range
builder that follows the monthly provisional P&L pipeline:
  snapshot rows -> aggregate -> current ad report -> gross/cancel/net quantities
  (for full/current-month ranges) -> return-sale consolidation -> manual overrides.

For multi-month periods the same live calculation is performed month by month and
only source snapshots overlapping the requested range are included.
"""
from __future__ import annotations

import calendar
import importlib
from datetime import date
from typing import Any

import pandas as pd


_APPLIED = False


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").replace("개", "").replace("%", "").strip()
        return float(v or 0)
    except Exception:
        return 0.0


def _month_bounds(month: str) -> tuple[date, date]:
    y, m = (int(x) for x in str(month).split("-"))
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _months_between(start: date | None, end: date | None, fallback_months: list[str]) -> list[str]:
    if start is None or end is None:
        return sorted(set(str(x) for x in fallback_months), reverse=True)
    cur = date(start.year, start.month, 1)
    stop = date(end.year, end.month, 1)
    out = []
    while cur <= stop:
        out.append(cur.strftime("%Y-%m"))
        if cur.month == 12:
            cur = date(cur.year + 1, 1, 1)
        else:
            cur = date(cur.year, cur.month + 1, 1)
    return out


def _overlap(ps: Any, pe: Any, start: date | None, end: date | None) -> bool:
    if start is None or end is None:
        return True
    try:
        a = date.fromisoformat(str(ps)[:10])
        b = date.fromisoformat(str(pe or ps)[:10])
    except Exception:
        return True
    return b >= start and a <= end


def _ad_dataset(core, db, start: date, end: date) -> dict:
    """Load the same ad-report rows used by provisional P&L, scoped to the range."""
    ad = importlib.import_module("provisional_ad_report_v0956")
    try:
        ad._ensure_schema(core, db)
    except Exception:
        pass
    with core._conn(db) as c:
        try:
            imports = c.execute(
                """SELECT id,file_name,period_start,period_end,total_ad_spend,imported_at
                   FROM provisional_ad_report_imports
                   WHERE period_end>=? AND period_start<=?
                   ORDER BY period_start,period_end,id""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
        except Exception:
            return {"imports": [], "items": {}, "total": 0.0}
        if not imports:
            return {"imports": [], "items": {}, "total": 0.0}
        ids = [int(r["id"]) for r in imports]
        q = ",".join("?" for _ in ids)
        items = c.execute(
            f"""SELECT option_id,MAX(product_name) product_name,SUM(ad_spend) ad_spend
                FROM provisional_ad_report_items
                WHERE import_id IN ({q}) GROUP BY option_id""",
            ids,
        ).fetchall()
    mapped = {
        str(r["option_id"]): {
            "option_id": str(r["option_id"]),
            "product_name": str(r["product_name"] or ""),
            "ad_spend": float(r["ad_spend"] or 0),
        }
        for r in items
    }
    return {
        "imports": [dict(r) for r in imports],
        "items": mapped,
        "total": float(sum(float(x["ad_spend"] or 0) for x in mapped.values())),
    }


def _range_rows(core, db, month: str, start: date | None, end: date | None):
    helper = importlib.import_module("pnl_month_default_v0914")
    rows, _excluded = helper._snapshot_rows_for_month(core, db, month)
    if start is None or end is None:
        return rows
    return [
        r for r in rows
        if _overlap(r.get("_period_start"), r.get("_period_end"), start, end)
    ]


def _is_month_quantity_safe(month: str, start: date | None, end: date | None) -> bool:
    """Gross/cancel month counts are safe when the requested range starts on day 1.

    Current-month views end at yesterday but month_counts only sees uploaded sales
    imports, so it still matches the live provisional page.
    """
    if start is None or end is None:
        return False
    ms, me = _month_bounds(month)
    return start <= ms and end >= min(me, date.today()) if month == date.today().strftime("%Y-%m") else (start <= ms and end >= me)


def _live_month(core, db, month: str, target_oid: str, start: date | None, end: date | None):
    helper = importlib.import_module("pnl_month_default_v0914")
    refresh = importlib.import_module("pnl_snapshot_refresh_v0966")
    ad = importlib.import_module("provisional_ad_report_v0956")
    quantities = importlib.import_module("sales_quantity_v0965")
    returns = importlib.import_module("return_sale_pnl_v0965")
    manual = importlib.import_module("provisional_manual_adjust_v0952")
    manual_net = importlib.import_module("provisional_manual_netqty_v0965")
    manual_net.apply(manual)

    # Ensure snapshot financial rules are current before reading them.
    try:
        refresh.refresh_month(core, month, db)
    except Exception:
        pass

    rows = _range_rows(core, db, month, start, end)
    view = helper._aggregate(rows)
    if view is None or view.empty:
        return None

    ms, me = _month_bounds(month)
    rs = max(ms, start) if start is not None else ms
    re = min(me, end) if end is not None else me
    dataset = _ad_dataset(core, db, rs, re)
    view, _ = ad.apply_to_view(view, dataset)

    # The detailed monthly page distinguishes gross/cancel/net. Do exactly the
    # same when the requested range represents that month. For partial boundary
    # months keep the snapshot's signed quantity as net so we do not inject a
    # whole-month gross count into a partial-range view.
    if _is_month_quantity_safe(month, start, end):
        view, _ = quantities.annotate_month(core, db, month, view)
    else:
        view = view.copy()
        if "순판매수량" not in view.columns:
            view["순판매수량"] = pd.to_numeric(view.get("판매수량", 0), errors="coerce").fillna(0.0)
        if "취소수량" not in view.columns:
            view["취소수량"] = 0.0

    view, _ = returns.consolidate_month(core, db, month, view)

    try:
        adjustments = manual.load(core, month, db)
        view, _ = manual.apply_to_view(view, adjustments)
    except Exception:
        pass

    if view is None or view.empty or "옵션ID" not in view.columns:
        return None
    sub = view[view["옵션ID"].map(_oid).eq(target_oid)].copy()
    if sub.empty:
        return None

    def total(col: str) -> float:
        if col not in sub.columns:
            return 0.0
        return float(pd.to_numeric(sub[col], errors="coerce").fillna(0.0).sum())

    revenue = total("예상매출")
    profit = total("예상이익")
    gross = total("판매수량")
    cancel = total("취소수량")
    net = total("순판매수량") if "순판매수량" in sub.columns else gross
    return {
        "기간시작": rs.isoformat(),
        "기간종료": re.isoformat(),
        "판매수량": gross,
        "취소수량": cancel,
        "순판매수량": net,
        "예상매출": revenue,
        "매출원가": total("매출원가"),
        "판매수수료": total("판매수수료"),
        "입출고비": total("입출고비"),
        "배송비": total("배송비"),
        "반품충당": total("반품충당"),
        "광고비": total("광고비"),
        "예상이익": profit,
        "이익률": (profit / revenue * 100) if abs(revenue) > 1e-12 else 0.0,
    }


def _live_provisional_history(core, db, option_id: str, start: date | None, end: date | None):
    target = _oid(option_id)
    if not target:
        return pd.DataFrame()
    helper = importlib.import_module("pnl_month_default_v0914")
    try:
        available = helper._available_months(core, db)
    except Exception:
        available = []
    months = _months_between(start, end, available)
    rows = []
    for month in months:
        try:
            row = _live_month(core, db, month, target, start, end)
        except Exception:
            row = None
        if row:
            rows.append(row)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out.sort_values(["기간시작", "기간종료"], ascending=[False, False], kind="stable")


def apply(product_overview_module):
    global _APPLIED
    if _APPLIED or getattr(product_overview_module, "_rg_live_pnl_v09122_applied", False):
        return product_overview_module
    base = getattr(product_overview_module, "_base", None)
    if base is None:
        raise RuntimeError("상품 통합현황 기반 모듈을 찾지 못했습니다.")
    base._provisional_history = _live_provisional_history
    product_overview_module._rg_live_pnl_v09122_applied = True
    _APPLIED = True
    return product_overview_module
