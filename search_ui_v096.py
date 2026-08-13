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
- v0.9.75: all Streamlit text/search inputs receive a strong, always-visible
  outline.  The style targets both BaseWeb wrappers and the actual input as a
  fallback so BOM and other ERP searches remain visible across Streamlit DOM versions.
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

_SEARCH_INPUT_CSS = r"""
<style>
/* v0.9.75 — ERP 전체 검색/텍스트 입력창을 배경과 확실히 구분한다. */

/* Streamlit text-input widget 자체 간격 */
div[data-testid="stTextInput"] {
    margin-bottom: 0.35rem !important;
}

/* 라벨 가독성 */
div[data-testid="stTextInput"] label,
div[data-testid="stTextInput"] label p {
    color: #1f2937 !important;
    font-weight: 600 !important;
}

/* 현재/구버전 Streamlit BaseWeb 입력 wrapper 모두 대응 */
div[data-testid="stTextInput"] div[data-baseweb="input"],
div[data-testid="stTextInput"] div[data-baseweb="base-input"] {
    background-color: #ffffff !important;
    border: 2px solid #718096 !important;
    border-radius: 9px !important;
    box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.08) !important;
    outline: none !important;
    transition: border-color 0.12s ease, box-shadow 0.12s ease !important;
}

/* BaseWeb 내부 요소가 부모 테두리를 덮는 경우에도 흰 배경 유지 */
div[data-testid="stTextInput"] div[data-baseweb="input"] > div,
div[data-testid="stTextInput"] div[data-baseweb="base-input"] > div {
    background-color: #ffffff !important;
    border-radius: 7px !important;
}

/* 실제 input도 fallback으로 명확한 inset 선을 넣어 DOM 버전 차이를 막는다. */
div[data-testid="stTextInput"] input {
    background-color: #ffffff !important;
    color: #111827 !important;
    border-radius: 7px !important;
    box-shadow: inset 0 0 0 1px rgba(113, 128, 150, 0.55) !important;
}

/* 마우스를 올리면 더 진하게 */
div[data-testid="stTextInput"] div[data-baseweb="input"]:hover,
div[data-testid="stTextInput"] div[data-baseweb="base-input"]:hover {
    border-color: #4b5f78 !important;
    box-shadow: 0 0 0 1px rgba(75, 95, 120, 0.16) !important;
}

/* 클릭/포커스 시 파란색 강조 */
div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within,
div[data-testid="stTextInput"] div[data-baseweb="base-input"]:focus-within {
    border: 2px solid #2563a6 !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 166, 0.18) !important;
}

div[data-testid="stTextInput"]:focus-within input {
    box-shadow: inset 0 0 0 1px rgba(37, 99, 166, 0.35) !important;
}

/* placeholder도 흐리지 않게 */
div[data-testid="stTextInput"] input::placeholder {
    color: #5f6f82 !important;
    opacity: 1 !important;
}

/* Streamlit 버전에 따라 BaseWeb data 속성이 달라도 입력영역 경계가 남도록 fallback */
div[data-testid="stTextInput"] > div:last-child:has(input) {
    background-color: #ffffff !important;
    border: 2px solid #718096 !important;
    border-radius: 9px !important;
    box-shadow: 0 0 0 1px rgba(15, 23, 42, 0.08) !important;
}

div[data-testid="stTextInput"] > div:last-child:has(input):focus-within {
    border-color: #2563a6 !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 166, 0.18) !important;
}
</style>
"""


def _inject_search_input_style() -> None:
    # CSS is global to the page, so dedicated searches implemented outside this
    # module (BOM, purchase history, returns, etc.) receive the same border too.
    st.markdown(_SEARCH_INPUT_CSS, unsafe_allow_html=True)


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

    # Inject the style before the one-time wrapper guard. Streamlit reruns can
    # rebuild the DOM, while the CSS itself is harmless to inject repeatedly.
    _inject_search_input_style()

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
