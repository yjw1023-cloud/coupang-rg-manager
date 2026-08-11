"""RG Manager v0.9.38 current BOM list presentation.

Only the current BOM table is intercepted.
- Adds a search box immediately above the table.
- Displays required quantity as an integer with Streamlit %d formatting.
- Forces the BOM quantity input to use positive integers only.
- Activates the return-generated product guard so Coupang return-only option IDs
  are never suggested as managed BOM finished products.
- Repairs legacy current BOMs that were attached to a return-generated option
  instead of the original managed Coupang option.
Database BOM quantities already stored are not modified by the table formatter.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

_REQUIRED = {"완제품", "구성품", "소요수량", "구성품원가", "완제품당 원가"}
_QTY_LABEL = "완제품 1개당 소요수량"


def _as_dataframe(obj: Any):
    if isinstance(obj, pd.DataFrame):
        return obj
    try:
        data = getattr(obj, "data", None)
        if isinstance(data, pd.DataFrame):
            return data
    except Exception:
        pass
    return None


def _is_current_bom_table(obj: Any) -> bool:
    df = _as_dataframe(obj)
    return df is not None and _REQUIRED.issubset(set(df.columns))


def _qty_int(v: Any):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        return int(round(float(str(v).replace(",", "").strip())))
    except Exception:
        return v


def _filter_and_format(st_obj, obj: Any) -> pd.DataFrame:
    base = _as_dataframe(obj)
    out = base.copy()
    out["소요수량"] = out["소요수량"].map(_qty_int)
    try:
        out["소요수량"] = pd.to_numeric(out["소요수량"], errors="coerce").astype("Int64")
    except Exception:
        pass

    q = st_obj.text_input(
        "현재 BOM 검색",
        placeholder="완제품명 또는 구성품명 입력",
        key="current_bom_search_v0936",
    ).strip().lower()

    if q:
        mask = pd.Series(False, index=out.index)
        for col in ("완제품", "구성품"):
            if col in out.columns:
                mask = mask | out[col].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        out = out[mask].copy()
        st_obj.caption(f"검색 결과 {len(out):,}개 / 전체 {len(base):,}개")

    return out


def _positive_int(v, default=1):
    try:
        return max(1, int(round(float(v))))
    except Exception:
        return int(default)


def apply() -> None:
    import streamlit as st
    import core
    import production_batch_v095
    import return_product_guard_v0937
    import bom_parent_repair_v0938

    # v0.9.37: remove return-only option IDs from managed BOM selectors.
    return_product_guard_v0937.apply()

    # v0.9.38: if an old current BOM is still attached to one of those return
    # option rows, move only the current bom_items parent link to the normal
    # managed product. Batch validation retries this repair before production.
    bom_parent_repair_v0938.apply(core, production_batch_v095)

    if getattr(st, "_rg_current_bom_ui_v0936_applied", False):
        return

    original_dataframe = st.dataframe
    original_number_input = st.number_input

    def wrapped_dataframe(data=None, *args, **kwargs):
        if _is_current_bom_table(data):
            data = _filter_and_format(st, data)
            kwargs.setdefault("hide_index", True)
            column_config = dict(kwargs.get("column_config") or {})
            column_config["소요수량"] = st.column_config.NumberColumn(
                "소요수량",
                format="%d",
            )
            kwargs["column_config"] = column_config
        return original_dataframe(data, *args, **kwargs)

    def wrapped_number_input(label, *args, **kwargs):
        if str(label) != _QTY_LABEL:
            return original_number_input(label, *args, **kwargs)

        # Streamlit positional order after label:
        # min_value, max_value, value, step, format, key, ...
        pos = list(args)
        if len(pos) >= 1:
            pos[0] = 1
            kwargs.pop("min_value", None)
        else:
            kwargs["min_value"] = 1

        if len(pos) >= 2 and pos[1] is not None:
            pos[1] = _positive_int(pos[1])
            kwargs.pop("max_value", None)
        elif "max_value" in kwargs and kwargs["max_value"] is not None:
            kwargs["max_value"] = _positive_int(kwargs["max_value"])

        if len(pos) >= 3:
            pos[2] = _positive_int(pos[2])
            kwargs.pop("value", None)
        else:
            kwargs["value"] = _positive_int(kwargs.get("value", 1))

        if len(pos) >= 4:
            pos[3] = 1
            kwargs.pop("step", None)
        else:
            kwargs["step"] = 1

        if len(pos) >= 5:
            pos[4] = "%d"
            kwargs.pop("format", None)
        else:
            kwargs["format"] = "%d"

        return original_number_input(label, *pos, **kwargs)

    st.dataframe = wrapped_dataframe
    st.number_input = wrapped_number_input
    st._rg_current_bom_ui_v0936_applied = True
