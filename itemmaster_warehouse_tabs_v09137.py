"""RG Manager v0.9.137 item-master warehouse tab semantics.

The legacy inventory presentation used stock!=0 for every warehouse tab. That is
correct on the inventory page but wrong on Item Master: a newly registered Coupang
finished product legitimately has RG stock 0 before production/inbound, yet it must
still appear under the CoupangRG item-master tab.

Item Master rules:
- 자체창고: all active raw/self-warehouse items, including zero stock.
- 쿠팡RG: all active Coupang finished products, including zero stock.
- 반품창고: products with non-zero return stock (return is a stock state, not an
  item-master classification).
- 전체: unchanged.

Inventory-page tabs remain unchanged and continue to mean "currently stocked".
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

_REQUIRED = {"품목코드", "상품명", "반품창고", "자체창고", "쿠팡RG"}
_APPLIED_ATTR = "_rg_itemmaster_warehouse_tabs_v09137"


def _unwrap_inventory_dataframe(fn):
    """Return the Streamlit dataframe callable under inventory_ui_v084 wrapper."""
    try:
        names = list(getattr(fn, "__code__", None).co_freevars or [])
        cells = list(getattr(fn, "__closure__", None) or [])
        env = {name: cell.cell_contents for name, cell in zip(names, cells)}
        inner = env.get("original_dataframe")
        if callable(inner):
            return inner
    except Exception:
        pass
    return fn


def _frame_kwargs(kwargs: dict[str, Any], rows: int) -> dict[str, Any]:
    out = dict(kwargs)
    out["hide_index"] = True
    out["use_container_width"] = True
    out["height"] = min(650, max(180, 38 * (int(rows) + 1)))
    return out


def _tab_columns(view: pd.DataFrame, warehouse: str) -> list[str]:
    preferred = [
        "품목코드", "상품명", "기준원가",
        "최근매입가", "매입평균원가", "최근생산원가", "생산평균원가",
        "원가상태", warehouse,
    ]
    return [c for c in preferred if c in view.columns]


def _itemmaster_sub(view: pd.DataFrame, warehouse: str) -> pd.DataFrame:
    if warehouse == "자체창고":
        mask = view["구분"].fillna("").astype(str).eq("자체창고")
    elif warehouse == "쿠팡RG":
        mask = view["구분"].fillna("").astype(str).eq("쿠팡RG")
    else:
        qty = pd.to_numeric(view["반품창고"], errors="coerce").fillna(0)
        mask = qty.abs().gt(1e-12)

    out = view.loc[mask, _tab_columns(view, warehouse)].copy()
    out = out.rename(columns={warehouse: "현재고"})
    if "현재고" in out.columns:
        out["현재고"] = pd.to_numeric(out["현재고"], errors="coerce").fillna(0)
    if not out.empty:
        out = out.sort_values(["상품명", "품목코드"], kind="stable").reset_index(drop=True)
    return out


def apply(inventory_module=None):
    if getattr(st, _APPLIED_ATTR, False):
        return

    wrapped = st.dataframe
    raw_dataframe = _unwrap_inventory_dataframe(wrapped)

    def dataframe_itemmaster_tabs(data=None, *args, **kwargs):
        is_itemmaster = (
            isinstance(data, pd.DataFrame)
            and _REQUIRED.issubset(set(data.columns))
            and {"구분", "상태"}.issubset(set(data.columns))
        )
        if not is_itemmaster:
            return wrapped(data, *args, **kwargs)

        # Reuse the existing cost enrichment so this patch only changes tab
        # membership semantics, not displayed cost calculations.
        view = data.copy()
        try:
            if inventory_module is not None and hasattr(inventory_module, "_enrich_view"):
                view, _ = inventory_module._enrich_view(data)
        except Exception:
            view = data.copy()

        tabs = st.tabs(["전체", "자체창고", "쿠팡RG", "반품창고"])
        with tabs[0]:
            raw_dataframe(view, *args, **_frame_kwargs(kwargs, len(view)))

        for tab, warehouse in zip(tabs[1:], ["자체창고", "쿠팡RG", "반품창고"]):
            sub = _itemmaster_sub(view, warehouse)
            with tab:
                if sub.empty:
                    if warehouse == "자체창고":
                        st.info("등록된 자체창고 품목이 없습니다.")
                    elif warehouse == "쿠팡RG":
                        st.info("등록된 쿠팡RG 판매상품이 없습니다.")
                    else:
                        st.info("반품창고에 현재 재고가 있는 상품이 없습니다.")
                else:
                    raw_dataframe(sub, *args, **_frame_kwargs(kwargs, len(sub)))
        return None

    st.dataframe = dataframe_itemmaster_tabs
    setattr(st, _APPLIED_ATTR, True)
