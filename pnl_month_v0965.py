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

            # v0.9.287: repair the October white-tablecloth row at the final
            # monthly P&L stage. Older data had these sales attached to the
            # obsolete piano-cover master. Move the entire row identity so
            # quantity, revenue, costs and profit remain together.
            if str(month) == "2026-10" and "옵션ID" in merged.columns:
                ghost_mask = merged["옵션ID"].fillna("").astype(str).eq("94731787590")
                if ghost_mask.any():
                    merged.loc[ghost_mask, "옵션ID"] = "96089460574"
                    if "상품명" in merged.columns:
                        merged.loc[ghost_mask, "상품명"] = "행사용 일회용 테이블보 PEVA / 5개 화이트 137x180cm"

                    # If a genuine white row already exists, combine the two rows
                    # rather than leaving a duplicate. Add only additive fields;
                    # derived averages are recalculated below.
                    white_mask = merged["옵션ID"].fillna("").astype(str).eq("96089460574")
                    white_rows = merged[white_mask]
                    if len(white_rows) > 1:
                        keep_idx = white_rows.index[0]
                        drop_idxs = list(white_rows.index[1:])
                        additive = {
                            "판매수량", "취소수량", "반품철회수량", "순판매수량",
                            "예상매출", "매출원가", "판매수수료", "입출고비", "배송비",
                            "반품충당", "광고비", "광고제외이익", "예상이익", "RG비용",
                            "반품판매수량", "반품판매취소", "반품판매매출",
                        }
                        for col in additive:
                            if col in merged.columns:
                                merged.at[keep_idx, col] = sum(_num(merged.at[i, col]) for i in white_rows.index)
                        if "순판매수량" in merged.columns and "예상매출" in merged.columns and "예상 실현단가" in merged.columns:
                            q = _num(merged.at[keep_idx, "순판매수량"])
                            merged.at[keep_idx, "예상 실현단가"] = _num(merged.at[keep_idx, "예상매출"]) / q if abs(q) > 1e-12 else 0.0
                        if "예상이익" in merged.columns and "예상매출" in merged.columns and "이익률(%)" in merged.columns:
                            rev = _num(merged.at[keep_idx, "예상매출"])
                            merged.at[keep_idx, "이익률(%)"] = _num(merged.at[keep_idx, "예상이익"]) / rev * 100 if abs(rev) > 1e-12 else 0.0
                        merged = merged.drop(index=drop_idxs)

            # v0.9.284: after all quantity/return row transforms, bind ad spend
            # one last time by the row's *current exact option ID*.  This prevents
            # a transformed/reordered row from retaining another option's ad spend.
            ad_items = dict((dataset or {}).get("items") or {})
            if "광고비" not in merged.columns:
                merged["광고비"] = 0.0
            for idx in merged.index:
                oid = ad._oid(merged.at[idx, "옵션ID"] if "옵션ID" in merged.columns else "")
                item = ad_items.get(oid)
                spend = abs(ad._num(item.get("ad_spend"))) if item else 0.0
                merged.at[idx, "광고비"] = -spend

                no_ad = _num(merged.at[idx, "광고제외이익"]) if "광고제외이익" in merged.columns else 0.0
                revenue = _num(merged.at[idx, "예상매출"]) if "예상매출" in merged.columns else 0.0
                profit = no_ad - spend
                if "예상이익" in merged.columns:
                    merged.at[idx, "예상이익"] = profit
                if "이익률(%)" in merged.columns:
                    merged.at[idx, "이익률(%)"] = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0

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
