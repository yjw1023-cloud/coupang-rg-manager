"""v0.9.198 inline editor for monthly provisional P&L.

The v0.9.197 page used a separate expander/selectbox form for per-product unit
commission and RG logistics overrides. That works but is slow for many new items.
This patch replaces that form with direct editing of the two unit-cost columns in
the main monthly P&L grid.

Rules:
- only `평균 수수료` and `평균 입출고배송비` are editable;
- editing a cell saves that product/month override immediately after commit;
- clearing a cell removes only that field's monthly override and returns to the
  recent confirmed/default value;
- untouched auto/default values are never accidentally converted to overrides;
- confirmed settlement rows are never edited.
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

RULE_VERSION = "0.9.198-inline-unit-cost-editor"
_EDITABLE = ("평균 수수료", "평균 입출고배송비")


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
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _same(a, b) -> bool:
    a, b = _nullable(a), _nullable(b)
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-9


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
        show = show.reset_index(drop=True)
        baseline = show.copy()

        st_obj.caption(
            "평균 수수료와 평균 입출고배송비 칸을 직접 클릭해 입력하세요. "
            "Enter 또는 다른 셀을 클릭하면 바로 저장·재계산됩니다. "
            "수동값을 지우고 싶으면 해당 셀 값을 삭제하면 자동값으로 돌아갑니다."
        )

        disabled = [c for c in show.columns if c not in _EDITABLE]
        column_config = {}
        try:
            column_config["평균 수수료"] = st_obj.column_config.NumberColumn(
                "평균 수수료", help="원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용",
                min_value=0.0, step=10.0, format="%.0f",
            )
            column_config["평균 입출고배송비"] = st_obj.column_config.NumberColumn(
                "평균 입출고배송비", help="입출고비+배송비 합계, 원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용",
                min_value=0.0, step=10.0, format="%.0f",
            )
            if "상품명" in show.columns:
                column_config["상품명"] = st_obj.column_config.TextColumn("상품명", width="large")
        except Exception:
            column_config = {}

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
            # A new widget key is important after save: it drops the old edit
            # delta, reloads the freshly calculated values, and prevents a rerun
            # loop while still feeling like an immediate cell save.
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
