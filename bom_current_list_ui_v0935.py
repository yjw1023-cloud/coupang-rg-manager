"""RG Manager v0.9.35 current BOM list presentation.

Only the BOM table with columns
완제품/구성품/소요수량/구성품원가/완제품당 원가 is intercepted.
Adds a search box immediately above that table and displays required quantity
as an integer. Database BOM quantities are not modified.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

_REQUIRED = {"완제품", "구성품", "소요수량", "구성품원가", "완제품당 원가"}


def _is_current_bom_table(obj: Any) -> bool:
    return isinstance(obj, pd.DataFrame) and _REQUIRED.issubset(set(obj.columns))


def _qty_int(v: Any):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(round(float(str(v).replace(",", "").strip())))
    except Exception:
        return v


def _filter_and_format(st_obj, df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["소요수량"] = out["소요수량"].map(_qty_int)
    try:
        out["소요수량"] = pd.to_numeric(out["소요수량"], errors="coerce").astype("Int64")
    except Exception:
        pass

    q = st_obj.text_input(
        "현재 BOM 검색",
        placeholder="완제품명 또는 구성품명 입력",
        key="current_bom_search_v0935",
    ).strip().lower()

    if q:
        text_cols = [c for c in ["완제품", "구성품"] if c in out.columns]
        mask = pd.Series(False, index=out.index)
        for col in text_cols:
            mask = mask | out[col].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        out = out[mask].copy()
        st_obj.caption(f"검색 결과 {len(out):,}개 / 전체 {len(df):,}개")

    return out


def apply() -> None:
    import streamlit as st

    if getattr(st, "_rg_current_bom_ui_v0935_applied", False):
        return

    original_dataframe = st.dataframe

    def wrapped_dataframe(data=None, *args, **kwargs):
        if _is_current_bom_table(data):
            data = _filter_and_format(st, data)
            kwargs.setdefault("hide_index", True)
        return original_dataframe(data, *args, **kwargs)

    st.dataframe = wrapped_dataframe
    st._rg_current_bom_ui_v0935_applied = True
