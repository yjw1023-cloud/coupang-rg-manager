"""v0.9.126 monthly provisional P&L quantity / return-rate presentation.

Visible quantity columns distinguish gross sales, customer cancel/refund quantity,
net sales, and returned-item resale quantities.  v0.9.126 additionally presents
the existing cancel/refund signal to the operator as `반품수량` and calculates
`반품률 = 반품수량 / 판매수량 * 100` for each product.  Financial arithmetic and
inventory logic continue to use the existing signed net quantity and are not changed.

v0.9.148 adds an explicit current-month provisional-input reset control before the
monthly table is rendered.
"""
from __future__ import annotations

import importlib


def _fmt_qty(v):
    try:
        x = float(v or 0)
        return f"{int(round(x)):,}" if abs(x - round(x)) < 1e-9 else f"{x:,.1f}"
    except Exception:
        return str(v)


def _num(v):
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("%", "").strip()
        return float(v or 0)
    except Exception:
        return 0.0


def _add_return_columns(df):
    """Add operator-facing return quantity/rate without changing source quantities."""
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    if "취소수량" in out.columns:
        out["반품수량"] = out["취소수량"].map(_num)
    else:
        out["반품수량"] = 0.0
    if "판매수량" in out.columns:
        gross = out["판매수량"].map(_num)
        returns = out["반품수량"].map(_num)
        out["반품률"] = [
            (r / g * 100.0) if abs(g) > 1e-12 else 0.0
            for r, g in zip(returns, gross)
        ]
    else:
        out["반품률"] = 0.0
    return out


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    base = importlib.import_module("pnl_month_v0961")
    ad = importlib.import_module("provisional_ad_report_v0956")
    quantities = importlib.import_module("sales_quantity_v0965")
    returns = importlib.import_module("return_sale_pnl_v0965")
    manual_adjust = importlib.import_module("provisional_manual_adjust_v0952")
    manual_net = importlib.import_module("provisional_manual_netqty_v0965")
    manual_net.apply(manual_adjust)

    db = db_path or core.DEFAULT_DB

    # v0.9.148: current-month sales facts can be explicitly cleared regardless
    # of whether they came from sales-stat Excel or manual Coupang API sync.
    reset = importlib.import_module("provisional_month_reset_v09148")
    reset.render_current_month_reset(st_obj, core, db)

    base._NUMERIC_COLS.update({
        "취소수량", "반품철회수량", "순판매수량", "반품수량", "반품률",
        "반품판매수량", "반품판매취소", "반품판매매출"
    })

    original_apply = ad.apply_to_view
    original_render = base._render_table
    original_fmt = base._fmt
    original_ordered = base._ordered_columns
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
            merged = _add_return_columns(merged)
        else:
            merged, qty_meta, return_meta = applied, {"exact": False}, {"rows": 0}
        holder["qty_meta"] = qty_meta
        holder["return_meta"] = return_meta
        return merged, meta

    def fmt(col, value):
        if col == "반품률":
            return f"{_num(value):,.1f}%"
        if col == "반품수량":
            return _fmt_qty(value)
        return original_fmt(col, value)

    def ordered_columns(df):
        cols = list(original_ordered(df))
        # Keep 취소수량 in the dataframe for summary validation, but present the
        # operator-facing name `반품수량` in the monthly table to avoid duplicate
        # columns carrying the same cancel/refund signal.
        if "반품수량" in cols:
            cols = [c for c in cols if c != "취소수량"]
        preferred = [
            "옵션ID", "상품명", "판매수량", "반품수량",
            "반품철회수량", "반품률", "순판매수량",
        ]
        first = [c for c in preferred if c in cols]
        rest = [c for c in cols if c not in first]
        return first + rest

    def render_table(st, df):
        qty_meta = holder.get("qty_meta") or {}
        return_meta = holder.get("return_meta") or {}
        if qty_meta.get("exact"):
            st.caption(
                "판매수량은 주문 API의 고객 결제일 기준입니다. 반품수량은 반품·취소 API의 접수일 기준이며, "
                "반품철회는 철회일에 되돌립니다. 순판매수량 = 판매수량 - 반품수량 + 반품철회수량입니다."
            )
        elif qty_meta.get("source") == "coupang_order_api":
            st.caption(
                "판매수량은 쿠팡 주문 API의 고객 결제일 기준 실제 수량입니다. "
                "반품수량은 반품 API를 연결하기 전까지 0개로 표시됩니다."
            )
        elif int(qty_meta.get("matched") or 0) > 0:
            st.caption(
                "일부 과거 판매자료는 실제 판매/취소·환불 수량 컬럼이 없어 반품수량·반품률이 정확하지 않을 수 있습니다."
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
    base._fmt = fmt
    base._ordered_columns = ordered_columns
    try:
        return base.render_provisional_month_page(st_obj, pd_obj, core, db)
    finally:
        ad.apply_to_view = original_apply
        base._render_table = original_render
        base._fmt = original_fmt
        base._ordered_columns = original_ordered
