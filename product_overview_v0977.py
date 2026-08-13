"""RG Manager v0.9.77 product overview UX corrections.

Changes on top of v0.9.76:
- Make finished-product and period select boxes clearly visible with a strong border.
- Default period is current month from the 1st through yesterday.
- Current-month / recent-period calculations exclude the still-in-progress current day.
- Sales and provisional P&L tables show one row for the selected query period,
  rather than exposing source-file period fragments as if they were query periods.
"""
from __future__ import annotations

from datetime import date, timedelta
import importlib
import math
from typing import Any

import pandas as pd


_base = importlib.import_module("product_overview_v0976")
PAGE_LABEL = _base.PAGE_LABEL
apply_sidebar = _base.apply_sidebar
patch_source = _base.patch_source
_BASE_PERIOD_BOUNDS = _base._period_bounds

_SELECTBOX_CSS = r"""
<style>
/* v0.9.77 — product overview selectors must be as visible as the search field. */
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: #ffffff !important;
    border: 2px solid #7b899b !important;
    border-radius: 8px !important;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06) !important;
    transition: border-color 0.12s ease, box-shadow 0.12s ease !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
    border-color: #52657c !important;
    box-shadow: 0 1px 4px rgba(15, 23, 42, 0.10) !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div {
    border-color: #2f6db5 !important;
    box-shadow: 0 0 0 2px rgba(47, 109, 181, 0.16) !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] input,
div[data-testid="stSelectbox"] div[data-baseweb="select"] [role="combobox"] {
    background: transparent !important;
}
</style>
"""


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


def _period_bounds(label: str):
    today = date.today()
    yesterday = today - timedelta(days=1)

    if label == "이번 달":
        start = today.replace(day=1)
        # On the first day of a month there is no completed current-month day.
        # Keep the range valid while normally ending at yesterday.
        return start, yesterday if yesterday >= start else start
    if label == "최근 30일":
        end = yesterday
        return end - timedelta(days=29), end
    if label == "최근 90일":
        end = yesterday
        return end - timedelta(days=89), end
    return _BASE_PERIOD_BOUNDS(label)


def _weighted_rate(df: pd.DataFrame, qty_col: str, rate_col: str) -> float:
    qty = df[qty_col].map(_num)
    rate = df[rate_col].map(_num)
    total = float(qty.sum())
    if abs(total) <= 1e-12:
        return 0.0
    return float((qty * rate).sum() / total)


def _aggregate_visible_period(df: pd.DataFrame, start: date | None, end: date | None) -> pd.DataFrame:
    """Collapse source-file fragments into the query period shown by the selector."""
    if not isinstance(df, pd.DataFrame) or df.empty or start is None or end is None:
        return df

    cols = list(map(str, df.columns))
    cset = set(cols)
    start_text = start.isoformat()
    end_text = end.isoformat()

    # Sales history table.
    sales_need = {"시작일", "종료일", "판매수량", "취소수량", "순판매수량"}
    if sales_need.issubset(cset) and "예상매출" not in cset:
        rate_cols = [c for c in cols if "률" in c]
        row = {
            "시작일": start_text,
            "종료일": end_text,
            "판매수량": _base._fmt_qty(df["판매수량"].map(_num).sum()),
            "취소수량": _base._fmt_qty(df["취소수량"].map(_num).sum()),
            "순판매수량": _base._fmt_qty(df["순판매수량"].map(_num).sum()),
        }
        for col in rate_cols:
            row[col] = _base._fmt_pct(_weighted_rate(df, "판매수량", col))
        return pd.DataFrame([{c: row.get(c, "") for c in cols}], columns=cols)

    # Provisional revenue/profit history table.
    provisional_need = {"시작일", "종료일", "판매수량", "예상매출", "광고비", "예상이익", "이익률"}
    if provisional_need.issubset(cset):
        qty = float(df["판매수량"].map(_num).sum())
        revenue = float(df["예상매출"].map(_num).sum())
        ad = float(df["광고비"].map(_num).sum())
        profit = float(df["예상이익"].map(_num).sum())
        margin = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0
        row = {
            "시작일": start_text,
            "종료일": end_text,
            "판매수량": _base._fmt_qty(qty),
            "예상매출": _base._fmt_money(revenue),
            "광고비": _base._fmt_money(ad),
            "예상이익": _base._fmt_money(profit),
            "이익률": _base._fmt_pct(margin),
        }
        return pd.DataFrame([{c: row.get(c, "") for c in cols}], columns=cols)

    # Return/cancel summary table uses the same selected period.
    return_need = {"시작일", "종료일", "판매수량", "반품·취소수량"}
    if return_need.issubset(cset):
        rate_cols = [c for c in cols if "률" in c]
        row = {
            "시작일": start_text,
            "종료일": end_text,
            "판매수량": _base._fmt_qty(df["판매수량"].map(_num).sum()),
            "반품·취소수량": _base._fmt_qty(df["반품·취소수량"].map(_num).sum()),
        }
        for col in rate_cols:
            row[col] = _base._fmt_pct(_weighted_rate(df, "판매수량", col))
        return pd.DataFrame([{c: row.get(c, "") for c in cols}], columns=cols)

    return df


def render_page(st, pd_obj, core, db_path=None):
    """Render v0.9.76 through narrowly scoped UI wrappers for v0.9.77 corrections."""
    st.markdown(_SELECTBOX_CSS, unsafe_allow_html=True)

    original_selectbox = st.selectbox
    original_dataframe = st.dataframe
    original_period_bounds = _base._period_bounds
    state = {"start": None, "end": None}

    def selectbox_wrapper(label, *args, **kwargs):
        label_text = str(label or "")
        mutable_args = list(args)
        if label_text == "조회기간":
            options = mutable_args[0] if mutable_args else kwargs.get("options", [])
            try:
                option_list = list(options)
                default_index = option_list.index("이번 달")
            except Exception:
                default_index = 0

            # New key prevents an old v0.9.76 '최근 30일' session value from
            # overriding the corrected default on the first v0.9.77 run.
            kwargs["key"] = "product_overview_period_v0977"
            if len(mutable_args) >= 2:
                mutable_args[1] = default_index
                kwargs.pop("index", None)
            else:
                kwargs["index"] = default_index

            value = original_selectbox(label, *mutable_args, **kwargs)
            state["start"], state["end"] = _period_bounds(str(value))
            return value
        return original_selectbox(label, *mutable_args, **kwargs)

    def dataframe_wrapper(data=None, *args, **kwargs):
        if isinstance(data, pd.DataFrame):
            data = _aggregate_visible_period(data, state.get("start"), state.get("end"))
        return original_dataframe(data, *args, **kwargs)

    try:
        st.selectbox = selectbox_wrapper
        st.dataframe = dataframe_wrapper
        _base._period_bounds = _period_bounds
        return _base.render_page(st, pd_obj, core, db_path)
    finally:
        st.selectbox = original_selectbox
        st.dataframe = original_dataframe
        _base._period_bounds = original_period_bounds
