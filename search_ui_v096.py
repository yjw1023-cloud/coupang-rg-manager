"""RG Manager v0.9.6 common search UI.

Adds a reusable search box in front of product/item oriented read-only tables.
The filter is presentation-only: it never changes persisted data.

Rules
- Search across all visible columns so product name, item code and Coupang option ID work.
- Only attach to tables that look like product/item lists.
- Do not add a second search box to the v0.9.4 clickable purchase-history list,
  because that page already renders its own dedicated product search field.
- Inventory tabs are wrapped before this module, so one search box filters the
  source inventory table and all warehouse tabs consistently.
"""
from __future__ import annotations

import hashlib
import inspect
import re
from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False

_SKIP_KEYS = {
    "_rg_purchase_history_list_v094",
}

# Exact normalized names plus conservative suffix rules.  This avoids adding
# search bars to ordinary accounting/date summary tables while still covering
# inventory, product cost, item master, returns, purchase history, and similar
# product-oriented lists.
_EXACT_IDENTIFIER_COLUMNS = {
    "상품명",
    "제품명",
    "품목명",
    "품목코드",
    "상품코드",
    "제품코드",
    "매입상품",
    "판매상품",
    "sku",
    "itemcode",
    "optionid",
    "옵션id",
    "쿠팡옵션id",
    "광고집행옵션id",
}


def _norm_col(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.strip().lower()
    return re.sub(r"[\s_\-·./()\[\]]+", "", text)


def _identifier_columns(df: pd.DataFrame) -> list[str]:
    cols = []
    for col in df.columns:
        norm = _norm_col(col)
        if norm in _EXACT_IDENTIFIER_COLUMNS:
            cols.append(str(col))
            continue
        # Common variants such as '쿠팡 상품명', '노출상품명', '판매자 상품코드'.
        if norm.endswith("상품명") or norm.endswith("품목명"):
            # Raw-source/detail tables are not primary item lists.
            if not norm.startswith("매입자료") and not norm.startswith("원본"):
                cols.append(str(col))
                continue
        if norm.endswith("품목코드") or norm.endswith("상품코드") or norm.endswith("옵션id"):
            cols.append(str(col))
    return cols


def _callsite_token(explicit_key: Any, columns) -> str:
    parts = [str(explicit_key or ""), "|".join(map(str, columns))]
    try:
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back
        while frame is not None:
            filename = str(frame.f_code.co_filename)
            if "search_ui_v096.py" not in filename and "streamlit" not in filename.lower():
                parts.append(f"{filename}:{frame.f_lineno}")
                break
            frame = frame.f_back
    except Exception:
        pass
    digest = hashlib.sha1("||".join(parts).encode("utf-8", errors="ignore")).hexdigest()[:12]
    return digest


def _filter_frame(df: pd.DataFrame, query: str) -> pd.DataFrame:
    query = str(query or "").strip()
    if not query:
        return df

    terms = [x for x in re.split(r"\s+", query.lower()) if x]
    if not terms:
        return df

    # Search all displayed columns, not just the identifier columns. This lets
    # users search option IDs, dates, status labels, and other visible values too.
    text_cols = []
    for col in df.columns:
        try:
            text_cols.append(df[col].fillna("").astype(str).str.lower())
        except Exception:
            continue
    if not text_cols:
        return df

    mask = pd.Series(True, index=df.index)
    for term in terms:
        term_mask = pd.Series(False, index=df.index)
        for series in text_cols:
            try:
                term_mask = term_mask | series.str.contains(term, regex=False, na=False)
            except Exception:
                continue
        mask = mask & term_mask
    return df.loc[mask].copy()


def _should_search(data, kwargs) -> bool:
    if not isinstance(data, pd.DataFrame) or data.empty:
        return False
    explicit_key = str(kwargs.get("key") or "")
    if explicit_key in _SKIP_KEYS:
        return False
    return bool(_identifier_columns(data))


def apply() -> None:
    global _APPLIED
    if _APPLIED or getattr(st, "_rg_common_search_v096", False):
        return

    previous_dataframe = st.dataframe

    def dataframe_with_search(data=None, *args, **kwargs):
        if not _should_search(data, kwargs):
            return previous_dataframe(data, *args, **kwargs)

        token = _callsite_token(kwargs.get("key"), data.columns)
        query = st.text_input(
            "🔎 검색",
            key=f"_rg_table_search_v096_{token}",
            placeholder="상품명 · 품목코드 · 쿠팡 옵션ID 검색",
            label_visibility="visible",
        )
        filtered = _filter_frame(data, query)
        if str(query or "").strip():
            st.caption(f"검색 결과 {len(filtered):,}개 / 전체 {len(data):,}개")

        return previous_dataframe(filtered, *args, **kwargs)

    st.dataframe = dataframe_with_search
    st._rg_common_search_v096 = True
    _APPLIED = True
