"""Sales P&L presentation cleanup for RG Manager v0.9.8.

Presentation-only rules:
- In the product-level sales/P&L table, hide rows whose net sales quantity is exactly 0.
- Keep negative quantities visible because they represent net cancellations/returns.
- If a non-zero sales row has both expected realized unit price and unit cost at 0,
  show a clear warning that item-master / settlement linkage needs review.

This module never edits persisted sales, inventory, cost, or settlement data.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False

_REQUIRED = {
    "옵션ID",
    "상품명",
    "판매수량",
    "예상 실현단가",
    "예상매출",
    "원가/개",
    "예상이익",
}


def _number_series(series: pd.Series) -> pd.Series:
    """Convert numeric-looking display values to floats without raising."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").fillna(0.0)
    cleaned = (
        series.fillna("")
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("원", "", regex=False)
        .str.replace("개", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(0.0)


def _is_sales_pnl_table(data: Any) -> bool:
    return isinstance(data, pd.DataFrame) and _REQUIRED.issubset(set(map(str, data.columns)))


def _problem_rows(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df.iloc[0:0]
    qty = _number_series(df["판매수량"])
    unit_price = _number_series(df["예상 실현단가"])
    unit_cost = _number_series(df["원가/개"])
    mask = (qty.abs() > 1e-12) & (unit_price.abs() <= 1e-12) & (unit_cost.abs() <= 1e-12)
    return df.loc[mask].copy()


def _warn_missing_links(df: pd.DataFrame) -> None:
    bad = _problem_rows(df)
    if bad.empty:
        return
    labels = []
    for r in bad.head(8).itertuples(index=False):
        try:
            option_id = str(getattr(r, "옵션ID") or "").strip()
        except Exception:
            option_id = ""
        try:
            name = str(getattr(r, "상품명") or "").strip()
        except Exception:
            name = ""
        labels.append(f"{option_id} {name}".strip())
    more = "" if len(bad) <= 8 else f" 외 {len(bad) - 8:,}개"
    st.warning(
        "판매수량은 있지만 '예상 실현단가'와 '원가/개'가 모두 0인 상품이 "
        f"{len(bad):,}개 있습니다. 상품마스터의 쿠팡 옵션ID 등록/원가와 "
        "판매수수료 정산 이력 연결을 확인하세요.\n\n"
        + " · ".join(labels)
        + more
    )


def apply() -> None:
    global _APPLIED
    if _APPLIED or getattr(st, "_rg_sales_pnl_ui_v098", False):
        return

    previous_dataframe = st.dataframe

    def dataframe_sales_pnl_cleanup(data=None, *args, **kwargs):
        if not _is_sales_pnl_table(data):
            return previous_dataframe(data, *args, **kwargs)

        qty = _number_series(data["판매수량"])
        filtered = data.loc[qty.abs() > 1e-12].copy()
        _warn_missing_links(filtered)

        # Recalculate table height when the caller supplied a height based on the
        # unfiltered row count. This keeps the table compact after zero-sales rows
        # are removed, while respecting an explicitly smaller caller height.
        if "height" in kwargs:
            try:
                requested = int(kwargs["height"])
                compact = min(700, max(220, 38 * (len(filtered) + 1)))
                kwargs = dict(kwargs)
                kwargs["height"] = min(requested, compact)
            except Exception:
                pass

        return previous_dataframe(filtered, *args, **kwargs)

    st.dataframe = dataframe_sales_pnl_cleanup
    st._rg_sales_pnl_ui_v098 = True
    _APPLIED = True
