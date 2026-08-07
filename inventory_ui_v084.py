"""Inventory UI improvements for RG Manager v0.8.4.

- Render warehouse inventory as tabs: all / own / Coupang RG / returns.
- Hide the internal `CP-` prefix from user-facing product codes.

The database item_code is intentionally not rewritten here. `CP-` was an internal
migration prefix; changing persisted keys is unnecessary for this UI improvement
and would add avoidable migration risk.
"""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

_REQUIRED = {"품목코드", "상품명", "반품창고", "자체창고", "쿠팡RG"}


def _display_code(value):
    text = "" if value is None else str(value)
    if re.fullmatch(r"CP-\d+", text):
        return text[3:]
    return text


def _tab_frame(df: pd.DataFrame, warehouse: str) -> pd.DataFrame:
    qty = pd.to_numeric(df[warehouse], errors="coerce").fillna(0)
    out = df.loc[qty.abs() > 1e-12, ["품목코드", "상품명", warehouse]].copy()
    out = out.rename(columns={warehouse: "현재고"})
    if not out.empty:
        out = out.sort_values(["상품명", "품목코드"], kind="stable").reset_index(drop=True)
    return out


def _frame_kwargs(kwargs, rows: int):
    out = dict(kwargs)
    out["hide_index"] = True
    out["use_container_width"] = True
    # Keep the list readable without making short warehouse tabs excessively tall.
    out["height"] = min(650, max(180, 38 * (int(rows) + 1)))
    return out


def apply():
    if getattr(st, "_rg_inventory_tabs_v084", False):
        return

    original_dataframe = st.dataframe

    def dataframe_with_inventory_tabs(data=None, *args, **kwargs):
        if isinstance(data, pd.DataFrame) and _REQUIRED.issubset(set(data.columns)):
            view = data.copy()
            view["품목코드"] = view["품목코드"].map(_display_code)

            tabs = st.tabs(["전체", "자체창고", "쿠팡RG", "반품창고"])
            with tabs[0]:
                original_dataframe(view, *args, **_frame_kwargs(kwargs, len(view)))

            for tab, warehouse in zip(tabs[1:], ["자체창고", "쿠팡RG", "반품창고"]):
                sub = _tab_frame(view, warehouse)
                with tab:
                    if sub.empty:
                        st.info(f"{warehouse}에 현재 재고가 있는 상품이 없습니다.")
                    else:
                        original_dataframe(sub, *args, **_frame_kwargs(kwargs, len(sub)))
            return None

        return original_dataframe(data, *args, **kwargs)

    st.dataframe = dataframe_with_inventory_tabs
    st._rg_inventory_tabs_v084 = True
