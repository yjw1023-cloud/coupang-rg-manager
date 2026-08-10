"""Final sales/P&L zero-quantity row filter for RG Manager v0.9.10.

This patch is intentionally applied LAST, after the common search, P&L cleanup,
and returned-item discount-sale wrappers.  It filters the final product-level
sales/P&L dataframe itself, so rows whose displayed sales quantity is exactly
zero cannot leak back into the screen through another wrapper.

Rules:
- hide sales quantity == 0
- keep negative quantities (net cancellations / returns)
- presentation only; persisted sales and inventory data are untouched
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False


def _num_series(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(0.0)
    cleaned = (
        series.fillna("")
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("개", "", regex=False)
        .str.replace("원", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _is_product_sales_pnl(data: Any) -> bool:
    if not isinstance(data, pd.DataFrame) or data.empty:
        return False
    cols = set(map(str, data.columns))
    # Keep this deliberately broader than v0.9.8.  These three columns uniquely
    # identify the product-level sales/P&L table in the current application.
    if not {"상품명", "판매수량", "예상매출"}.issubset(cols):
        return False
    return bool({"예상 실현단가", "원가/개", "예상이익"} & cols)


def _filter_zero_qty(data: pd.DataFrame) -> pd.DataFrame:
    qty = _num_series(data["판매수량"])
    return data.loc[qty.abs() > 1e-12].copy()


def apply() -> None:
    global _APPLIED
    if _APPLIED or getattr(st, "_rg_sales_pnl_zero_v0910", False):
        return

    previous_dataframe = st.dataframe

    def dataframe(data=None, *args, **kwargs):
        if _is_product_sales_pnl(data):
            data = _filter_zero_qty(data)
            if "height" in kwargs:
                try:
                    kwargs = dict(kwargs)
                    requested = int(kwargs["height"])
                    compact = min(700, max(220, 38 * (len(data) + 1)))
                    kwargs["height"] = min(requested, compact)
                except Exception:
                    pass
        return previous_dataframe(data, *args, **kwargs)

    st.dataframe = dataframe
    st._rg_sales_pnl_zero_v0910 = True
    _APPLIED = True
