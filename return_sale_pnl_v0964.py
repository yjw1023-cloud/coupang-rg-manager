"""v0.9.64 returned-item sale P&L sign fix.

Fixes the v0.9.63 consolidation bug where a negative returned-item sale quantity
was treated with abs(qty) for COGS/RG estimates. Negative quantity is a
cancellation/refund reversal, so product cost must reverse as well.

Display rules:
- 판매수량/예상매출 remain net monthly figures for the original product.
- 반품판매수량 counts only positive returned-item sale units.
- 반품판매취소 counts the absolute quantity of negative returned-item rows.
- 반품판매매출 is the net returned-item revenue including cancellations/refunds.
- Original-product unit cost is used for both positive sales and reversals.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

import return_sale_pnl_v0963 as base


def _num(v: Any) -> float:
    return base._num(v)


def _oid(v: Any) -> str:
    return base._oid(v)


def _return_totals(core, db, month: str):
    start, end = base._month_bounds(month)
    with core._conn(db) as con:
        if not base._table_exists(con, "return_discount_sales"):
            return {}
        rows = con.execute(
            """SELECT discount_option_id,qty,net_sales_amount,amount_known
               FROM return_discount_sales
               WHERE period_start>=? AND period_end<=?
               ORDER BY import_id,discount_option_id""",
            (start, end),
        ).fetchall()

    out = {}
    for r in rows:
        oid = _oid(r["discount_option_id"])
        if not oid:
            continue
        x = out.setdefault(
            oid,
            {
                "sales_qty": 0.0,
                "cancel_qty": 0.0,
                "net_qty": 0.0,
                "sales_amount": 0.0,
                "cancel_amount": 0.0,
                "net_amount": 0.0,
                "all_amount_known": True,
            },
        )
        q = _num(r["qty"])
        known = bool(r["amount_known"])
        amount = _num(r["net_sales_amount"]) if known else 0.0
        x["net_qty"] += q
        if q > 1e-12:
            x["sales_qty"] += q
            if known:
                x["sales_amount"] += amount
        elif q < -1e-12:
            x["cancel_qty"] += abs(q)
            if known:
                x["cancel_amount"] += amount
        if known:
            x["net_amount"] += amount
        else:
            x["all_amount_known"] = False
    return out


def _sum_rows(df: pd.DataFrame, indices, col: str) -> float:
    if col not in df.columns:
        return 0.0
    return sum(_num(df.at[i, col]) for i in indices)


def _set_numeric(df: pd.DataFrame, idx, col: str, value: float):
    if col in df.columns:
        df.at[idx, col] = float(value)


def consolidate_month(core, db, month: str, view: pd.DataFrame):
    """Roll return aliases into originals with signed cancellation arithmetic."""
    if not isinstance(view, pd.DataFrame) or view.empty or "옵션ID" not in view.columns:
        return view, {"rows": 0, "sales_qty": 0.0, "cancel_qty": 0.0, "revenue": 0.0}

    import return_discount_v099 as rd

    repair = base.ensure_known_aliases(core, db, rd)
    aliases = base._alias_map(core, db)
    if not aliases:
        return view, {
            "rows": 0,
            "sales_qty": 0.0,
            "cancel_qty": 0.0,
            "revenue": 0.0,
            "repair": repair,
        }

    totals = _return_totals(core, db, month)
    out = view.copy()
    for col in ("반품판매수량", "반품판매취소", "반품판매매출"):
        if col not in out.columns:
            out[col] = 0.0

    oid_series = out["옵션ID"].map(_oid)
    drop_indices = set()
    merged_rows = 0
    total_sales_qty = 0.0
    total_cancel_qty = 0.0
    total_revenue = 0.0

    for discount_oid, info in aliases.items():
        alias_indices = [i for i in out.index if i not in drop_indices and oid_series.loc[i] == discount_oid]
        if not alias_indices:
            continue

        parent_oid = info["parent_option_id"]
        parent_indices = [
            i for i in out.index
            if i not in drop_indices and i not in alias_indices and oid_series.loc[i] == parent_oid
        ]
        parent_idx = parent_indices[0] if parent_indices else None

        fallback_qty = _sum_rows(out, alias_indices, "판매수량")
        fallback_revenue = _sum_rows(out, alias_indices, "예상매출")
        rt = totals.get(discount_oid) or {}

        net_qty = _num(rt.get("net_qty")) if rt else fallback_qty
        if abs(net_qty) <= 1e-12 and abs(fallback_qty) > 1e-12:
            net_qty = fallback_qty
        sales_qty = _num(rt.get("sales_qty")) if rt else max(net_qty, 0.0)
        cancel_qty = _num(rt.get("cancel_qty")) if rt else max(-net_qty, 0.0)
        revenue = (
            _num(rt.get("net_amount"))
            if rt and rt.get("all_amount_known")
            else fallback_revenue
        )

        cost = abs(_num(info.get("parent_unit_cost")))
        if cost <= 1e-12 and parent_idx is not None and "원가/개" in out.columns:
            cost = abs(_num(out.at[parent_idx, "원가/개"]))

        alias_ad = _sum_rows(out, alias_indices, "광고비")
        alias_commission = _sum_rows(out, alias_indices, "판매수수료")
        alias_inout = _sum_rows(out, alias_indices, "입출고비")
        alias_delivery = _sum_rows(out, alias_indices, "배송비")
        alias_return = _sum_rows(out, alias_indices, "반품충당")

        parent_qty = _num(out.at[parent_idx, "판매수량"]) if parent_idx is not None else 0.0
        parent_revenue = (
            _num(out.at[parent_idx, "예상매출"])
            if parent_idx is not None and "예상매출" in out.columns
            else 0.0
        )

        # Preserve the alias row's calculated commission when possible. If the
        # actual return-sale revenue replaced an estimated revenue, scale the
        # existing commission by the same revenue ratio. This preserves sign.
        if abs(alias_commission) > 1e-12 and abs(fallback_revenue) > 1e-12:
            commission = alias_commission * (revenue / fallback_revenue)
        elif parent_idx is not None and abs(parent_revenue) > 1e-12 and "판매수수료" in out.columns:
            commission = (_num(out.at[parent_idx, "판매수수료"]) / parent_revenue) * revenue
        else:
            commission = -revenue * 0.108

        def signed_cost(col: str, alias_value: float) -> float:
            # If the snapshot already has a value, keep it: it may reflect a
            # real fee estimate. Otherwise derive from the original product with
            # signed quantity so a cancellation reverses the provisional charge.
            if abs(alias_value) > 1e-12:
                return alias_value
            if parent_idx is not None and abs(parent_qty) > 1e-12 and col in out.columns:
                return (_num(out.at[parent_idx, col]) / parent_qty) * net_qty
            return 0.0

        inout = signed_cost("입출고비", alias_inout)
        delivery = signed_cost("배송비", alias_delivery)
        ret = signed_cost("반품충당", alias_return)

        # Critical v0.9.64 fix: do NOT use abs(net_qty).
        # +1 sale => -cost, -1 cancellation => +cost reversal.
        cogs = -net_qty * cost
        no_ad = revenue + cogs + commission + inout + delivery + ret
        profit = no_ad + alias_ad

        metrics = {
            "판매수량": net_qty,
            "예상매출": revenue,
            "매출원가": cogs,
            "판매수수료": commission,
            "입출고비": inout,
            "배송비": delivery,
            "반품충당": ret,
            "광고비": alias_ad,
            "광고제외이익": no_ad,
            "예상이익": profit,
        }

        if parent_idx is None:
            target = alias_indices[0]
            out.at[target, "옵션ID"] = parent_oid
            if "상품명" in out.columns:
                out.at[target, "상품명"] = info["parent_name"]
            for col, value in metrics.items():
                _set_numeric(out, target, col, value)
            out.at[target, "반품판매수량"] = sales_qty
            out.at[target, "반품판매취소"] = cancel_qty
            out.at[target, "반품판매매출"] = revenue
            if "원가/개" in out.columns:
                out.at[target, "원가/개"] = cost
            for extra in alias_indices[1:]:
                drop_indices.add(extra)
            parent_idx = target
        else:
            for col, value in metrics.items():
                if col in out.columns:
                    out.at[parent_idx, col] = _num(out.at[parent_idx, col]) + value
            out.at[parent_idx, "반품판매수량"] = _num(out.at[parent_idx, "반품판매수량"]) + sales_qty
            out.at[parent_idx, "반품판매취소"] = _num(out.at[parent_idx, "반품판매취소"]) + cancel_qty
            out.at[parent_idx, "반품판매매출"] = _num(out.at[parent_idx, "반품판매매출"]) + revenue
            drop_indices.update(alias_indices)

        base._recalc_row(out, parent_idx)
        merged_rows += len(alias_indices)
        total_sales_qty += sales_qty
        total_cancel_qty += cancel_qty
        total_revenue += revenue

    if drop_indices:
        out = out.drop(index=list(drop_indices)).copy()

    desired = []
    extras = {"반품판매수량", "반품판매취소", "반품판매매출"}
    for col in out.columns:
        if col not in extras:
            desired.append(col)
        if col == "판매수량":
            desired.extend(["반품판매수량", "반품판매취소"])
        if col == "예상매출":
            desired.append("반품판매매출")
    for col in ("반품판매수량", "반품판매취소", "반품판매매출"):
        if col not in desired and col in out.columns:
            desired.append(col)
    out = out[[c for c in desired if c in out.columns]]

    return out, {
        "rows": merged_rows,
        "sales_qty": total_sales_qty,
        "cancel_qty": total_cancel_qty,
        "revenue": total_revenue,
        "repair": repair,
    }
