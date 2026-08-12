"""v0.9.64 monthly provisional P&L wrapper for signed returned-item sales.

Keeps the v0.9.61 client-side sortable table, consolidates return-sale aliases
into the original product, and separates positive returned-item sales from
negative cancellation/refund rows.
"""
from __future__ import annotations

import importlib


def _fmt_qty(v):
    try:
        x = float(v or 0)
        return f"{int(round(x)):,}" if abs(x - round(x)) < 1e-9 else f"{x:,.1f}"
    except Exception:
        return str(v)


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    base = importlib.import_module("pnl_month_v0961")
    ad = importlib.import_module("provisional_ad_report_v0956")
    returns = importlib.import_module("return_sale_pnl_v0964")
    db = db_path or core.DEFAULT_DB

    base._NUMERIC_COLS.update({"반품판매수량", "반품판매취소", "반품판매매출"})

    original_apply = ad.apply_to_view
    original_render = base._render_table
    holder = {
        "meta": {
            "rows": 0,
            "sales_qty": 0.0,
            "cancel_qty": 0.0,
            "revenue": 0.0,
        }
    }

    def apply_to_view(view, dataset):
        applied, meta = original_apply(view, dataset)
        month = str(st_obj.session_state.get("provisional_month_v0915") or "")
        if month:
            merged, return_meta = returns.consolidate_month(core, db, month, applied)
        else:
            merged, return_meta = applied, {
                "rows": 0,
                "sales_qty": 0.0,
                "cancel_qty": 0.0,
                "revenue": 0.0,
            }
        holder["meta"] = return_meta
        return merged, meta

    def render_table(st, df):
        meta = holder.get("meta") or {}
        if int(meta.get("rows") or 0) > 0:
            st.caption(
                "↩ 반품판매 옵션은 원상품 손익에 합산합니다. "
                f"이번 달 반품판매 {_fmt_qty(meta.get('sales_qty'))}개 · "
                f"취소/환불 {_fmt_qty(meta.get('cancel_qty'))}개 · "
                f"반품판매 순매출 {int(round(float(meta.get('revenue') or 0))):,}원입니다. "
                "양수 판매와 음수 취소/환불을 분리해 계산합니다."
            )
        return original_render(st, df)

    ad.apply_to_view = apply_to_view
    base._render_table = render_table
    try:
        return base.render_provisional_month_page(st_obj, pd_obj, core, db)
    finally:
        ad.apply_to_view = original_apply
        base._render_table = original_render
