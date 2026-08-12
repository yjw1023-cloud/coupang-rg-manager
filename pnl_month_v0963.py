"""v0.9.63 monthly provisional P&L wrapper for returned-item sale consolidation.

It keeps the proven v0.9.61 HTML/JavaScript sortable renderer unchanged while
intercepting the post-advertising dataframe stage. Linked returned-item option
rows are rolled into the original product row before manual adjustments, search,
summary, and rendering.
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
    returns = importlib.import_module("return_sale_pnl_v0963")
    db = db_path or core.DEFAULT_DB

    base._NUMERIC_COLS.update({"반품판매수량", "반품판매매출"})

    original_apply = ad.apply_to_view
    original_render = base._render_table
    holder = {"meta": {"rows": 0, "qty": 0.0, "revenue": 0.0}}

    def apply_to_view(view, dataset):
        applied, meta = original_apply(view, dataset)
        month = str(st_obj.session_state.get("provisional_month_v0915") or "")
        if month:
            merged, return_meta = returns.consolidate_month(core, db, month, applied)
        else:
            merged, return_meta = applied, {"rows": 0, "qty": 0.0, "revenue": 0.0}
        holder["meta"] = return_meta
        return merged, meta

    def render_table(st, df):
        meta = holder.get("meta") or {}
        if int(meta.get("rows") or 0) > 0:
            st.caption(
                "↩ 반품판매는 별도 상품으로 보지 않고 원상품 손익에 합산했습니다. "
                f"이번 달 반품판매 {_fmt_qty(meta.get('qty'))}개 · "
                f"{int(round(float(meta.get('revenue') or 0))):,}원이며, "
                "표의 '반품판매수량'·'반품판매매출'에서 따로 확인할 수 있습니다."
            )
        return original_render(st, df)

    ad.apply_to_view = apply_to_view
    base._render_table = render_table
    try:
        return base.render_provisional_month_page(st_obj, pd_obj, core, db)
    finally:
        ad.apply_to_view = original_apply
        base._render_table = original_render
