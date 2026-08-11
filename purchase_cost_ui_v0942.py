"""RG Manager v0.9.42 purchase-cost presentation.

Adds quantity-weighted average purchase cost to the existing item purchase-history
KPI row. Historical purchase rows are not modified.
"""
from __future__ import annotations

_APPLIED = False


def apply(base_module):
    global _APPLIED
    if _APPLIED or base_module is None:
        return base_module
    if getattr(base_module, "_rg_purchase_cost_ui_v0942_applied", False):
        return base_module

    def render_kpis(st_obj, hist):
        if hist is None or hist.empty:
            return
        s = base_module._summary(hist)
        qty = float(s.get("qty") or 0)
        amount = float(s.get("amount") or 0)
        avg_cost = amount / qty if qty > 0 and amount > 0 else 0

        c1, c2, c3, c4, c5 = st_obj.columns(5)
        c1.metric("최근 매입가", base_module._fmt_money(s.get("latest_cost") or 0))
        c2.metric("매입평균원가", base_module._fmt_money(avg_cost) if avg_cost > 0 else "-")
        c3.metric("누적 매입수량", base_module._fmt_qty(qty))
        c4.metric("누적 매입액", base_module._fmt_money(amount))
        c5.metric("최근 매입일", s.get("latest_date") or "-")
        batch_text = f" · 최근 차수 {s.get('latest_batch')}" if s.get("latest_batch") else ""
        st_obj.caption(f"총 {int(s.get('count') or 0):,}건의 매입이력{batch_text}")

    base_module._render_kpis = render_kpis
    base_module._rg_purchase_cost_ui_v0942_applied = True
    _APPLIED = True
    return base_module
