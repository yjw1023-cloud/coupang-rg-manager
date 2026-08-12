"""v0.9.65 returned-item sale consolidation using gross/cancel/net quantities.

Financial arithmetic stays on signed net quantity, while the visible 판매수량
shows actual gross units sold.  Returned-item gross sales and cancellations are
kept as subsets of the original product's totals.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

import return_sale_pnl_v0963 as base


def _num(v: Any) -> float:
    return base._num(v)


def _oid(v: Any) -> str:
    return base._oid(v)


def _return_amounts(core, db, month: str):
    start, end = base._month_bounds(month)
    with core._conn(db) as con:
        if not base._table_exists(con, "return_discount_sales"):
            return {}
        rows = con.execute(
            """SELECT discount_option_id,
                      COALESCE(SUM(CASE WHEN amount_known=1 THEN net_sales_amount ELSE 0 END),0) amount,
                      COALESCE(MIN(amount_known),0) all_known
               FROM return_discount_sales
               WHERE period_start>=? AND period_end<=?
               GROUP BY discount_option_id""",
            (start, end),
        ).fetchall()
    return {
        _oid(r["discount_option_id"]): {
            "amount": _num(r["amount"]),
            "all_known": bool(r["all_known"]),
        }
        for r in rows
    }


def _sum_rows(df: pd.DataFrame, indices, col: str) -> float:
    if col not in df.columns:
        return 0.0
    return sum(_num(df.at[i, col]) for i in indices)


def _set(df: pd.DataFrame, idx, col: str, value: float):
    if col in df.columns:
        df.at[idx, col] = float(value)


def _recalc_display(df: pd.DataFrame, idx, unit_cost: float | None = None):
    net_qty = _num(df.at[idx, "순판매수량"]) if "순판매수량" in df.columns else _num(df.at[idx, "판매수량"])
    revenue = _num(df.at[idx, "예상매출"]) if "예상매출" in df.columns else 0.0
    if "예상 실현단가" in df.columns:
        df.at[idx, "예상 실현단가"] = revenue / net_qty if abs(net_qty) > 1e-12 else 0.0
    if unit_cost is not None and unit_cost > 0 and "원가/개" in df.columns:
        df.at[idx, "원가/개"] = abs(unit_cost)
    if "RG비용" in df.columns:
        df.at[idx, "RG비용"] = sum(
            _num(df.at[idx, c]) for c in ("입출고비", "배송비", "반품충당") if c in df.columns
        )
    if "이익률(%)" in df.columns:
        profit = _num(df.at[idx, "예상이익"]) if "예상이익" in df.columns else 0.0
        df.at[idx, "이익률(%)"] = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0


def consolidate_month(core, db, month: str, view: pd.DataFrame):
    if not isinstance(view, pd.DataFrame) or view.empty or "옵션ID" not in view.columns:
        return view, {"rows": 0, "sales_qty": 0.0, "cancel_qty": 0.0, "revenue": 0.0}

    import return_discount_v099 as rd

    repair = base.ensure_known_aliases(core, db, rd)
    aliases = base._alias_map(core, db)
    if not aliases:
        return view, {"rows": 0, "sales_qty": 0.0, "cancel_qty": 0.0, "revenue": 0.0, "repair": repair}

    amounts = _return_amounts(core, db, month)
    out = view.copy()
    for col in ("취소수량", "순판매수량", "반품판매수량", "반품판매취소", "반품판매매출"):
        if col not in out.columns:
            out[col] = 0.0

    oid_series = out["옵션ID"].map(_oid)
    drop_indices = set()
    merged_rows = 0
    total_sales = 0.0
    total_cancel = 0.0
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

        sales_qty = _sum_rows(out, alias_indices, "판매수량")
        cancel_qty = _sum_rows(out, alias_indices, "취소수량")
        net_qty = _sum_rows(out, alias_indices, "순판매수량")
        fallback_revenue = _sum_rows(out, alias_indices, "예상매출")
        amount_info = amounts.get(discount_oid) or {}
        revenue = _num(amount_info.get("amount")) if amount_info.get("all_known") else fallback_revenue

        cost = abs(_num(info.get("parent_unit_cost")))
        if cost <= 1e-12 and parent_idx is not None and "원가/개" in out.columns:
            cost = abs(_num(out.at[parent_idx, "원가/개"]))

        alias_ad = _sum_rows(out, alias_indices, "광고비")
        alias_commission = _sum_rows(out, alias_indices, "판매수수료")
        alias_inout = _sum_rows(out, alias_indices, "입출고비")
        alias_delivery = _sum_rows(out, alias_indices, "배송비")
        alias_return = _sum_rows(out, alias_indices, "반품충당")

        parent_net = _num(out.at[parent_idx, "순판매수량"]) if parent_idx is not None and "순판매수량" in out.columns else 0.0
        parent_revenue = _num(out.at[parent_idx, "예상매출"]) if parent_idx is not None and "예상매출" in out.columns else 0.0

        if abs(alias_commission) > 1e-12 and abs(fallback_revenue) > 1e-12:
            commission = alias_commission * (revenue / fallback_revenue)
        elif parent_idx is not None and abs(parent_revenue) > 1e-12 and "판매수수료" in out.columns:
            commission = (_num(out.at[parent_idx, "판매수수료"]) / parent_revenue) * revenue
        else:
            commission = -revenue * 0.108

        def signed_fee(col: str, alias_value: float) -> float:
            if abs(alias_value) > 1e-12:
                return alias_value
            if parent_idx is not None and abs(parent_net) > 1e-12 and col in out.columns:
                return (_num(out.at[parent_idx, col]) / parent_net) * net_qty
            return 0.0

        inout = signed_fee("입출고비", alias_inout)
        delivery = signed_fee("배송비", alias_delivery)
        ret = signed_fee("반품충당", alias_return)
        cogs = -net_qty * cost
        no_ad = revenue + cogs + commission + inout + delivery + ret
        profit = no_ad + alias_ad

        metrics = {
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
            out.at[target, "판매수량"] = sales_qty
            out.at[target, "취소수량"] = cancel_qty
            out.at[target, "순판매수량"] = net_qty
            out.at[target, "반품판매수량"] = sales_qty
            out.at[target, "반품판매취소"] = cancel_qty
            out.at[target, "반품판매매출"] = revenue
            for col, value in metrics.items():
                _set(out, target, col, value)
            for extra in alias_indices[1:]:
                drop_indices.add(extra)
            parent_idx = target
        else:
            out.at[parent_idx, "판매수량"] = _num(out.at[parent_idx, "판매수량"]) + sales_qty
            out.at[parent_idx, "취소수량"] = _num(out.at[parent_idx, "취소수량"]) + cancel_qty
            out.at[parent_idx, "순판매수량"] = _num(out.at[parent_idx, "순판매수량"]) + net_qty
            out.at[parent_idx, "반품판매수량"] = _num(out.at[parent_idx, "반품판매수량"]) + sales_qty
            out.at[parent_idx, "반품판매취소"] = _num(out.at[parent_idx, "반품판매취소"]) + cancel_qty
            out.at[parent_idx, "반품판매매출"] = _num(out.at[parent_idx, "반품판매매출"]) + revenue
            for col, value in metrics.items():
                if col in out.columns:
                    out.at[parent_idx, col] = _num(out.at[parent_idx, col]) + value
            drop_indices.update(alias_indices)

        _recalc_display(out, parent_idx, cost)
        merged_rows += len(alias_indices)
        total_sales += sales_qty
        total_cancel += cancel_qty
        total_revenue += revenue

    if drop_indices:
        out = out.drop(index=list(drop_indices)).copy()

    # Stable, readable quantity ordering.
    ordered = []
    quantity_cols = {"취소수량", "순판매수량", "반품판매수량", "반품판매취소", "반품판매매출"}
    for col in out.columns:
        if col not in quantity_cols:
            ordered.append(col)
        if col == "판매수량":
            ordered.extend(["취소수량", "순판매수량", "반품판매수량", "반품판매취소"])
        if col == "예상매출":
            ordered.append("반품판매매출")
    for col in ("취소수량", "순판매수량", "반품판매수량", "반품판매취소", "반품판매매출"):
        if col not in ordered and col in out.columns:
            ordered.append(col)
    out = out[[c for c in ordered if c in out.columns]]

    return out, {
        "rows": merged_rows,
        "sales_qty": total_sales,
        "cancel_qty": total_cancel,
        "revenue": total_revenue,
        "repair": repair,
    }
