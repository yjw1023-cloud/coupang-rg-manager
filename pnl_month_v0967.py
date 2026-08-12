"""v0.9.67 monthly provisional P&L refresh-order fix.

v0.9.66 tried to monkey-patch a snapshot reader on pnl_month_v0961, but that
function actually lives in pnl_month_default_v0914.  Do not patch either module.
Simply refresh stale monthly snapshots first, then render the proven v0.9.65
quantity/return-sale view.  This is simpler and avoids AttributeError.
"""
from __future__ import annotations

import importlib


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    previous = importlib.import_module("pnl_month_v0965")
    refresh = importlib.import_module("pnl_snapshot_refresh_v0966")
    month_helpers = importlib.import_module("pnl_month_default_v0914")
    db = db_path or core.DEFAULT_DB

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
