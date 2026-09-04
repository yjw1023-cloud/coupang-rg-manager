"""v0.9.154 provisional P&L expense-sign guard.

Regression fixed
----------------
After provisional sales switched back to sales-stat Excel, some upstream
``estimated_pnl`` rows already carried expenses as negative numbers.  The older
monthly snapshot converter negated them again, so commission could become a
positive income item and produce impossible margins above 100%.

This module is deliberately narrow:
- it changes only provisional P&L presentation/snapshot arithmetic;
- it does not edit sales, return quantities, inventory, purchases, or settlements;
- for positive/net sales, COGS, commission, in/out, delivery, return reserve and
  advertising are always expenses regardless of the source sign;
- negative net sales still reverse COGS and commission, preserving the existing
  return/cancellation direction rule.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

RULE_VERSION = "0.9.154-expense-sign-normalization"


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = (
                v.replace(",", "")
                .replace("원", "")
                .replace("개", "")
                .replace("건", "")
                .replace("%", "")
                .strip()
            )
        x = float(v or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(0.0, index=df.index)
    if pd.api.types.is_numeric_dtype(df[col]):
        return pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return pd.to_numeric(
        df[col]
        .fillna("")
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("원", "", regex=False)
        .str.replace("개", "", regex=False)
        .str.replace("건", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip(),
        errors="coerce",
    ).fillna(0.0)


def _like(ui, old: Any, value: float, pct: bool = False):
    fn = getattr(ui, "_like", None)
    if callable(fn):
        return fn(old, value, pct)
    if isinstance(old, str):
        if pct or "%" in old:
            return f"{value:,.1f}%"
        if "원" in old:
            return f"{int(round(value)):,}원"
        if "개" in old:
            return f"{int(round(value)):,}개"
        if "," in old:
            return f"{int(round(value)):,}"
    return value


def recalculate(ui, data: pd.DataFrame) -> pd.DataFrame:
    """Final provisional P&L arithmetic with explicit expense directions."""
    if data is None or getattr(data, "empty", True):
        return data

    out = data.copy()

    # Keep the original zero-net-sales presentation rule.  Before the monthly
    # gross/return columns are added, 판매수량 is the financial net quantity.
    if "판매수량" in out.columns:
        visible_qty = _series(out, "판매수량")
        out = out.loc[visible_qty.abs() > 1e-12].copy()
        if out.empty:
            return out

    qty_col = "순판매수량" if "순판매수량" in out.columns else "판매수량"
    qty = _series(out, qty_col)

    for idx in out.index:
        q = _num(qty.loc[idx])
        revenue = _num(out.at[idx, "예상매출"]) if "예상매출" in out.columns else 0.0
        unit_cost = abs(_num(out.at[idx, "원가/개"])) if "원가/개" in out.columns else 0.0

        # Positive net sales consume COGS; negative net sales reverse COGS.
        cogs = -q * unit_cost

        raw_commission = _num(out.at[idx, "판매수수료"]) if "판매수수료" in out.columns else 0.0
        # A negative net sale reverses commission; otherwise commission is a cost.
        commission = abs(raw_commission) if q < -1e-12 else -abs(raw_commission)

        # Logistics/return handling and advertising remain expenses.  Their
        # source reports differ in sign convention, so never trust that sign.
        inout = -abs(_num(out.at[idx, "입출고비"])) if "입출고비" in out.columns else 0.0
        delivery = -abs(_num(out.at[idx, "배송비"])) if "배송비" in out.columns else 0.0
        return_reserve = -abs(_num(out.at[idx, "반품충당"])) if "반품충당" in out.columns else 0.0
        ad = -abs(_num(out.at[idx, "광고비"])) if "광고비" in out.columns else 0.0

        for col, value in (
            ("매출원가", cogs),
            ("판매수수료", commission),
            ("입출고비", inout),
            ("배송비", delivery),
            ("반품충당", return_reserve),
            ("광고비", ad),
        ):
            if col in out.columns:
                out.at[idx, col] = _like(ui, out.at[idx, col], value)

        no_ad = revenue + cogs + commission + inout + delivery + return_reserve
        profit = no_ad + ad
        margin = profit / revenue * 100.0 if abs(revenue) > 1e-12 else 0.0

        if "광고제외이익" in out.columns:
            out.at[idx, "광고제외이익"] = _like(ui, out.at[idx, "광고제외이익"], no_ad)
        if "예상이익" in out.columns:
            out.at[idx, "예상이익"] = _like(ui, out.at[idx, "예상이익"], profit)
        if "이익률(%)" in out.columns:
            out.at[idx, "이익률(%)"] = _like(ui, out.at[idx, "이익률(%)"], margin, True)
        if "RG비용" in out.columns:
            out.at[idx, "RG비용"] = _like(
                ui,
                out.at[idx, "RG비용"],
                inout + delivery + return_reserve,
            )

    return out


def apply(ui_module, snapshot_refresh_module):
    """Install on every Streamlit rerun; safe even after module hot reloads."""
    ui_module._recalculate = lambda data: recalculate(ui_module, data)
    snapshot_refresh_module._RULE_VERSION = RULE_VERSION
    ui_module._rg_provisional_expense_guard_v09154 = True
    snapshot_refresh_module._rg_provisional_expense_guard_v09154 = True
    return {
        "ok": True,
        "rule_version": RULE_VERSION,
        "snapshot_refresh_forced": True,
    }
