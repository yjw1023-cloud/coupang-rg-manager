"""v0.9.197 bridge: apply sales-stat revenue + monthly unit costs to monthly P&L.

v0.9.196 correctly wrapped core.estimated_pnl, but the normal monthly P&L page no
longer renders core.estimated_pnl directly: it aggregates previously saved
snapshots and then draws a custom HTML table. Therefore an already-created
snapshot could keep 0-won revenue for a new product and the Streamlit dataframe
wrapper never got a chance to render the new unit-cost editor.

This bridge patches the exact two extension points used by the live monthly page:
- pnl_manual_blocks_v0955.render_adjust: replace the obsolete total-value editor
  with the per-unit monthly editor.
- provisional_manual_adjust_v0952.apply_to_view: apply v0.9.197 calculations to
  the ad-adjusted monthly dataframe immediately before search/summary/table.

Confirmed settlement tables are never edited.
"""
from __future__ import annotations

import importlib

RULE_VERSION = "0.9.197-month-page-bridge"
_SENTINEL = "__rg197_month_page__"


def apply(core, db_path=None):
    basis = importlib.import_module("provisional_sales_basis_v09196")
    blocks = importlib.import_module("pnl_manual_blocks_v0955")
    adjust = importlib.import_module("provisional_manual_adjust_v0952")

    if not hasattr(blocks, "_rg197_original_render_adjust"):
        blocks._rg197_original_render_adjust = blocks.render_adjust

    def render_adjust(st_obj, manual_adjust_module, core_arg, month: str, auto_view, db):
        active_db = db or db_path or core_arg.DEFAULT_DB
        fixed, meta = basis.apply_month_view(core_arg, active_db, str(month), auto_view)
        basis.render_month_editor(st_obj, core_arg, active_db, str(month), fixed)
        matched = int(meta.get("matched_sales") or 0)
        if matched:
            st_obj.caption(
                f"판매통계 순 판매 금액을 직접 적용한 상품 {matched:,}개 · "
                "수수료/입출고배송비는 이 달 수동값 → 최근 확정월 평균 → 기존 자동값 순서로 적용합니다."
            )
        return {
            _SENTINEL: True,
            "month": str(month),
            "db": active_db,
            "meta": meta,
        }

    blocks.render_adjust = render_adjust
    blocks._rg197_month_page_bridge = RULE_VERSION

    if not hasattr(adjust, "_rg197_original_apply_to_view"):
        adjust._rg197_original_apply_to_view = adjust.apply_to_view
    original_apply = adjust._rg197_original_apply_to_view

    def apply_to_view(view, adjustments):
        if isinstance(adjustments, dict) and adjustments.get(_SENTINEL):
            month = str(adjustments.get("month") or "")
            active_db = adjustments.get("db") or db_path or core.DEFAULT_DB
            fixed, meta = basis.apply_month_view(core, active_db, month, view)
            return fixed, {
                "applied": int(meta.get("manual_products") or 0),
                "rg197": meta,
            }
        return original_apply(view, adjustments)

    adjust.apply_to_view = apply_to_view
    adjust._rg197_month_page_bridge = RULE_VERSION

    try:
        month_ui = importlib.import_module("pnl_month_v0961")
        month_ui._NUMERIC_COLS.update({"평균 수수료", "평균 입출고배송비"})
        order = list(month_ui._PRIMARY_COL_ORDER)
        for col in ("평균 수수료", "평균 입출고배송비"):
            while col in order:
                order.remove(col)
        anchor = order.index("예상 실현단가") + 1 if "예상 실현단가" in order else min(6, len(order))
        order[anchor:anchor] = ["평균 수수료", "평균 입출고배송비"]
        month_ui._PRIMARY_COL_ORDER[:] = order
        month_ui._rg197_month_page_bridge = RULE_VERSION
    except Exception:
        pass

    core._rg_pnl_month_sales_basis_v09197 = RULE_VERSION
    return {"ok": True, "rule_version": RULE_VERSION}
