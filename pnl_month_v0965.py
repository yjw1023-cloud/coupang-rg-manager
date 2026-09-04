"""Monthly provisional P&L quantity / return-rate presentation.

Visible quantity columns distinguish gross sales, customer cancel/refund quantity,
net sales, and returned-item resale quantities.  The monthly table also presents
operator-facing return quantity/rate without changing financial or inventory logic.

v0.9.155 presentation changes:
- hide the internal return-withdrawal quantity column from the monthly table;
- show per-net-sale average commission and average in/out + delivery cost directly
  beside the expected realized unit price.
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
            v = v.replace(",", "").replace("원", "").replace("%", "").strip()
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


def _add_average_cost_columns(df):
    """Add per-net-sale applied commission and logistics amounts for display only."""
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    if "순판매수량" in out.columns:
        qty = out["순판매수량"].map(_num)
    elif "판매수량" in out.columns:
        qty = out["판매수량"].map(_num)
    else:
        qty = [0.0] * len(out)

    commission = out["판매수수료"].map(_num) if "판매수수료" in out.columns else [0.0] * len(out)
    inout = out["입출고비"].map(_num) if "입출고비" in out.columns else [0.0] * len(out)
    delivery = out["배송비"].map(_num) if "배송비" in out.columns else [0.0] * len(out)

    avg_commission = []
    avg_logistics = []
    for q, fee, io, dlv in zip(qty, commission, inout, delivery):
        divisor = abs(float(q or 0))
        if divisor <= 1e-12:
            avg_commission.append(0.0)
            avg_logistics.append(0.0)
        else:
            avg_commission.append(abs(float(fee or 0)) / divisor)
            avg_logistics.append((abs(float(io or 0)) + abs(float(dlv or 0))) / divisor)

    out["평균 수수료"] = avg_commission
    out["평균 입출고배송비"] = avg_logistics
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

    reset = importlib.import_module("provisional_month_reset_v09148")
    reset.render_current_month_reset(st_obj, core, db)

    base._NUMERIC_COLS.update({
        "취소수량", "반품철회수량", "순판매수량", "반품수량", "반품률",
        "반품판매수량", "반품판매취소", "반품판매매출",
        "평균 수수료", "평균 입출고배송비",
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
            merged = _add_average_cost_columns(merged)
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
        if col in {"평균 수수료", "평균 입출고배송비"}:
            return f"{int(round(abs(_num(value)))):,}"
        return original_fmt(col, value)

    def ordered_columns(df):
        cols = list(original_ordered(df))
        # Internal columns remain available for calculation but are not shown.
        hidden = {"취소수량", "반품철회수량"}
        cols = [c for c in cols if c not in hidden]
        preferred = [
            "옵션ID", "상품명", "판매수량", "반품수량", "반품률", "순판매수량",
            "예상 실현단가", "평균 수수료", "평균 입출고배송비",
        ]
        first = [c for c in preferred if c in cols]
        rest = [c for c in cols if c not in first]
        return first + rest

    def render_table(st, df):
        qty_meta = holder.get("qty_meta") or {}
        return_meta = holder.get("return_meta") or {}
        if int(qty_meta.get("matched") or 0) > 0:
            st.caption(
                "평균 수수료와 평균 입출고배송비는 잠정손익에 적용된 금액을 순판매수량으로 나눈 1개당 평균 금액입니다."
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
