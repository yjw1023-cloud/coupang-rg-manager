"""v0.9.199 monthly provisional P&L refresh + ambiguous return-sale confirmation.

Refresh stale monthly snapshots before rendering the proven v0.9.65 quantity/
return-sale view.  Also surface unresolved returned-item resale candidates directly
on the provisional P&L page so the operator can confirm the original product or
mark the option as a normal new product.  Confirmed aliases repair historical
sales and inventory through return_sale_confirm_v09195.
"""
from __future__ import annotations

import importlib


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    previous = importlib.import_module("pnl_month_v0965")
    refresh = importlib.import_module("pnl_snapshot_refresh_v0966")
    month_helpers = importlib.import_module("pnl_month_default_v0914")
    db = db_path or core.DEFAULT_DB

    # v0.9.199: ambiguous unknown Coupang option IDs must never remain silently
    # separated. Stage old placeholder rows as pending and ask the operator on the
    # same provisional-P&L screen where the mismatch is visible. The confirmation
    # module also wraps the return-sale resolver for future imports in this process.
    try:
        rd = importlib.import_module("return_discount_v099")
        confirm = importlib.import_module("return_sale_confirm_v09195")
        confirm.apply(rd, core, db)
        confirm.render_pending(st_obj, core, db, location="provisional_pnl")
    except Exception as exc:
        st_obj.caption(f"반품 재판매 매칭 확인 기능을 불러오지 못했습니다: {exc}")

    # On the first visit the selectbox key may not exist yet. Resolve the same
    # default month that the v0.9.61 renderer will choose, so the refresh still
    # happens before snapshots are read.
    month = str(st_obj.session_state.get("provisional_month_v0915") or "")
    if not month:
        months = month_helpers._available_months(core, db)
        if months:
            current = month_helpers._current_month()
            month = current if current in months else str(months[0])

    if month:
        refresh.refresh_month(core, month, db)

    return previous.render_provisional_month_page(st_obj, pd_obj, core, db)
