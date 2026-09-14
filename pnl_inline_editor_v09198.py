"""v0.9.199 inline editor formatting for monthly provisional P&L.

Keeps the v0.9.198 direct-edit workflow while restoring readable numeric
presentation in the main monthly P&L grid.

Rules:
- only `평균 수수료` and `평균 입출고배송비` are editable;
- editing a cell saves that product/month override immediately after commit;
- clearing a cell removes only that field's monthly override and returns to the
  recent confirmed/default value;
- percentage columns render with one decimal place and a `%` sign;
- all other numeric columns render as whole numbers with locale thousands
  separators; no decimal tails are shown;
- every numeric column (including the two editable columns) is center-aligned;
- option ID is center-aligned, product name remains left-aligned;
- confirmed settlement rows are never edited.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

RULE_VERSION = "0.9.199-inline-unit-cost-editor-format"
_EDITABLE = ("평균 수수료", "평균 입출고배송비")
_PERCENT_COLS = {"반품률", "이익률(%)"}
_ID_COLS = {"옵션ID"}
_TEXT_COLS = {"상품명"}


def _oid(v: Any) -> str:
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _nullable(v: Any):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, str) and not v.strip():
        return None
    try:
        x = float(str(v).replace(",", "").replace("%", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _same(a, b) -> bool:
    a, b = _nullable(a), _nullable(b)
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-9


def _prepare_numeric_display(show: pd.DataFrame, month_ui) -> pd.DataFrame:
    """Round display values without changing the calculation dataframe."""
    out = show.copy()
    numeric_cols = set(getattr(month_ui, "_NUMERIC_COLS", set())) | set(_EDITABLE) | _PERCENT_COLS
    for col in out.columns:
        if col in _ID_COLS or col in _TEXT_COLS:
            continue
        if col not in numeric_cols and not pd.api.types.is_numeric_dtype(out[col]):
            continue
        vals = pd.to_numeric(out[col], errors="coerce")
        if col in _PERCENT_COLS:
            out[col] = vals.round(1)
        else:
            # Keep numeric dtype for sorting/editing, but remove every decimal
            # tail before NumberColumn's localized rendering.
            out[col] = vals.round(0)
    return out


def _number_column(st_obj, label: str, editable: bool = False, percent: bool = False):
    kwargs = {
        "label": label,
        "alignment": "center",
    }
    if percent:
        kwargs["format"] = "%.1f%%"
    else:
        kwargs["format"] = "localized"
    if editable:
        kwargs.update({
            "help": "원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용",
            "min_value": 0.0,
            "step": 10.0,
        })
    return st_obj.column_config.NumberColumn(**kwargs)


def apply(core, db_path=None):
    import provisional_sales_basis_v09196 as basis
    import pnl_month_sales_basis_v09197 as bridge
    import pnl_month_v0961 as month_ui

    # v0.9.197's render_adjust calls this function by module-global lookup, so
    # replacing it removes the separate product-picker form without touching the
    # rest of its revenue/cost calculation path. It also records the month/db for
    # the main table renderer below.
    def remember_context(st_obj, basis_arg, core_arg, db, month: str, data):
        month_ui._rg198_active_month = str(month)
        month_ui._rg198_active_db = db or db_path or core_arg.DEFAULT_DB
        return None

    bridge.render_month_editor = remember_context
    bridge._rg198_inline_editor = RULE_VERSION

    if not hasattr(month_ui, "_rg198_original_render_table"):
        month_ui._rg198_original_render_table = month_ui._render_table
    original_render = month_ui._rg198_original_render_table

    def render_table(st_obj, df):
        if df is None or getattr(df, "empty", True):
            return original_render(st_obj, df)

        month = str(getattr(month_ui, "_rg198_active_month", "") or "")
        active_db = getattr(month_ui, "_rg198_active_db", None) or db_path or core.DEFAULT_DB
        if not month or "옵션ID" not in df.columns or not set(_EDITABLE).issubset(df.columns):
            return original_render(st_obj, df)

        show = df.copy()
        try:
            cols = month_ui._ordered_columns(show)
            show = show[[c for c in cols if c in show.columns]]
        except Exception:
            pass
        show = _prepare_numeric_display(show.reset_index(drop=True), month_ui)
        baseline = show.copy()

        st_obj.caption(
            "평균 수수료와 평균 입출고배송비 칸을 직접 클릭해 입력하세요. "
            "Enter 또는 다른 셀을 클릭하면 바로 저장·재계산됩니다. "
            "반품률·이익률은 %로, 금액은 소수점 없이 천 단위 콤마로 표시합니다."
        )

        disabled = [c for c in show.columns if c not in _EDITABLE]
        column_config = {}
        for col in show.columns:
            try:
                if col == "상품명":
                    column_config[col] = st_obj.column_config.TextColumn(
                        "상품명", width="large", alignment="left"
                    )
                elif col == "옵션ID":
                    column_config[col] = st_obj.column_config.TextColumn(
                        "옵션ID", alignment="center"
                    )
                elif col in _PERCENT_COLS:
                    column_config[col] = _number_column(st_obj, col, percent=True)
                elif col in _EDITABLE:
                    help_text = (
                        "입출고비+배송비 합계, 원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용"
                        if col == "평균 입출고배송비"
                        else "원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용"
                    )
                    column_config[col] = st_obj.column_config.NumberColumn(
                        col,
                        help=help_text,
                        min_value=0.0,
                        step=10.0,
                        format="localized",
                        alignment="center",
                    )
                elif pd.api.types.is_numeric_dtype(show[col]) or col in getattr(month_ui, "_NUMERIC_COLS", set()):
                    column_config[col] = _number_column(st_obj, col)
                else:
                    column_config[col] = st_obj.column_config.TextColumn(col, alignment="center")
            except TypeError:
                # Very old Streamlit fallback: keep the formatting even if that
                # local build predates the alignment parameter.
                if col in _PERCENT_COLS:
                    column_config[col] = st_obj.column_config.NumberColumn(col, format="%.1f%%")
                elif col in _EDITABLE:
                    column_config[col] = st_obj.column_config.NumberColumn(
                        col, min_value=0.0, step=10.0, format="localized"
                    )
                elif pd.api.types.is_numeric_dtype(show[col]) or col in getattr(month_ui, "_NUMERIC_COLS", set()):
                    column_config[col] = st_obj.column_config.NumberColumn(col, format="localized")

        nonce_key = f"_rg198_inline_nonce_{month}"
        nonce = int(st_obj.session_state.get(nonce_key, 0) or 0)
        widget_key = f"rg198_inline_pnl_{month}_{nonce}"
        height = min(850, max(250, 36 * (len(show) + 1)))

        edited = st_obj.data_editor(
            show,
            key=widget_key,
            use_container_width=True,
            hide_index=True,
            disabled=disabled,
            column_config=column_config,
            height=height,
        )

        current = basis._manual(core, active_db, month)
        saved_count = 0
        for pos in range(min(len(baseline), len(edited))):
            oid = _oid(baseline.at[pos, "옵션ID"])
            if not oid:
                continue
            saved = current.get(oid, {})
            old_comm_override = basis._nullable(saved.get("commission_unit_override"))
            old_logi_override = basis._nullable(saved.get("logistics_unit_override"))
            new_comm_override = old_comm_override
            new_logi_override = old_logi_override
            changed = False

            base_comm = _nullable(baseline.at[pos, "평균 수수료"])
            edit_comm = _nullable(edited.at[pos, "평균 수수료"])
            if not _same(base_comm, edit_comm):
                new_comm_override = None if edit_comm is None else max(0.0, float(edit_comm))
                changed = True

            base_logi = _nullable(baseline.at[pos, "평균 입출고배송비"])
            edit_logi = _nullable(edited.at[pos, "평균 입출고배송비"])
            if not _same(base_logi, edit_logi):
                new_logi_override = None if edit_logi is None else max(0.0, float(edit_logi))
                changed = True

            if changed:
                basis._save(
                    core, active_db, month, oid,
                    new_comm_override, new_logi_override,
                )
                saved_count += 1

        if saved_count:
            st_obj.session_state[nonce_key] = nonce + 1
            try:
                st_obj.toast(f"{saved_count:,}개 상품의 잠정비용을 저장했습니다.")
            except Exception:
                pass
            st_obj.rerun()

    month_ui._render_table = render_table
    month_ui._rg198_inline_editor = RULE_VERSION
    core._rg_pnl_inline_editor_v09198 = RULE_VERSION
    return {"ok": True, "rule_version": RULE_VERSION}
