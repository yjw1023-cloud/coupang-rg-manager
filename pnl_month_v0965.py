"""v0.9.65 monthly provisional P&L quantity semantics.

Visible quantity columns now distinguish gross sales, cancellations and net sales.
Returned-item sales are then consolidated into the managed original product while
financial arithmetic continues to use signed net quantity.
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
    quantities = importlib.import_module("sales_quantity_v0965")
    returns = importlib.import_module("return_sale_pnl_v0965")
    manual_adjust = importlib.import_module("provisional_manual_adjust_v0952")
    manual_net = importlib.import_module("provisional_manual_netqty_v0965")
    manual_net.apply(manual_adjust)

    db = db_path or core.DEFAULT_DB
    base._NUMERIC_COLS.update({
        "취소수량", "순판매수량", "반품판매수량", "반품판매취소", "반품판매매출"
    })

    original_apply = ad.apply_to_view
    original_render = base._render_table
    holder = {
        "return_meta": {"rows": 0, "sales_qty": 0.0, "cancel_qty": 0.0, "revenue": 0.0},
        "qty_meta": {"exact": False, "matched": 0},
    }

    def apply_to_view(view, dataset):
        applied, meta = original_apply(view, dataset)
        month = str(st_obj.session_state.get("provisional_month_v0915") or "")
        if month:
            counted, qty_meta = quantities.annotate_month(core, db, month, applied)
            merged, return_meta = returns.consolidate_month(core, db, month, counted)
        else:
            merged, qty_meta, return_meta = applied, {"exact": False}, {"rows": 0}
        holder["qty_meta"] = qty_meta
        holder["return_meta"] = return_meta
        return merged, meta

    def render_table(st, df):
        qty_meta = holder.get("qty_meta") or {}
        return_meta = holder.get("return_meta") or {}
        if qty_meta.get("exact"):
            st.caption(
                "판매수량은 실제 판매된 수량, 취소수량은 취소·환불 수량, 순판매수량은 손익·재고 계산에 사용하는 순수량입니다."
            )
        elif int(qty_meta.get("matched") or 0) > 0:
            st.caption(
                "일부 과거 판매자료는 실제 판매수량/취소수량 컬럼이 없어 순판매수량 기준으로 표시될 수 있습니다."
            )
        if int(return_meta.get("rows") or 0) > 0:
            st.caption(
                "↩ 반품판매 옵션은 원상품 행에 합산합니다. "
                f"반품판매 {_fmt_qty(return_meta.get('sales_qty'))}개 · "
                f"반품판매 취소/환불 {_fmt_qty(return_meta.get('cancel_qty'))}개 · "
                f"반품판매 순매출 {int(round(float(return_meta.get('revenue') or 0))):,}원입니다."
            )
        return original_render(st, df)

    ad.apply_to_view = apply_to_view
    base._render_table = render_table
    try:
        return base.render_provisional_month_page(st_obj, pd_obj, core, db)
    finally:
        ad.apply_to_view = original_apply
        base._render_table = original_render
