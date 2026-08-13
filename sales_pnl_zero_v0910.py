"""Final sales/P&L zero-quantity row filter for RG Manager v0.9.10.

This patch is intentionally applied LAST, after the common search, P&L cleanup,
and returned-item discount-sale wrappers.  It filters the final product-level
sales/P&L dataframe itself, so rows whose displayed sales quantity is exactly
zero cannot leak back into the screen through another wrapper.

Rules:
- hide sales quantity == 0
- keep negative quantities (net cancellations / returns)
- presentation only; persisted sales and inventory data are untouched

v0.9.41 also activates the sales-stat managed-product guard after
return_discount_v099 has been loaded.  This prevents active zero-cost ERP products
from being mistaken for auto-created return-discount placeholders.

v0.9.91 also bootstraps the advertising-period repair from a module that app.py
always imports directly.  This avoids relying on Python's optional sitecustomize
startup hook, which is not guaranteed in the packaged Streamlit launch path.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False
_AD_BOOTSTRAPPED = False


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


def _bootstrap_ad_repair() -> None:
    """Apply ad filename-period + canonical sync through a guaranteed app path."""
    global _AD_BOOTSTRAPPED
    if _AD_BOOTSTRAPPED:
        return
    try:
        import core
        import ad_period_v0987
        import data_management_sync_v0988

        # UI filename recognition for the generic Coupang-data uploader.
        ad_period_v0987.apply()
        # Repairs already-saved wrong periods and mirrors them to provisional P&L.
        data_management_sync_v0988.apply(core)
        _AD_BOOTSTRAPPED = True
    except Exception as exc:
        # Do not take the whole ERP down if a repair has an unexpected local-data
        # issue.  A later rerun may retry after the underlying data is corrected.
        print(f"RG Manager v0.9.91 advertising repair bootstrap failed: {exc}")


def apply() -> None:
    global _APPLIED

    # v0.9.91: run this BEFORE the normal one-time UI guard. app.py always imports
    # and calls this module, so the existing misdated 8/12 advertising record is
    # repaired even when sitecustomize.py was never executed by the launcher.
    _bootstrap_ad_repair()

    # v0.9.41: return_discount_v099 is already imported/applied before this module
    # in app.py.  Its import wrapper resolves _resolve at runtime, so replacing the
    # module resolver here immediately affects subsequent 판매통계 uploads.
    import return_discount_v099
    import sales_import_guard_v0941
    sales_import_guard_v0941.apply(return_discount_v099)

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
