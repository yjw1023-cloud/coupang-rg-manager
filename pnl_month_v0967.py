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
    db = db_path or core.DEFAULT_DB

    # Refresh current sales-stat snapshots before the monthly renderer reads them.
    # refresh_month is fingerprint-guarded, so after a successful rebuild this is
    # effectively a cheap no-op until source imports change.
    month = str(st_obj.session_state.get("provisional_month_v0915") or "")
    if month:
        refresh.refresh_month(core, month, db)

    return previous.render_provisional_month_page(st_obj, pd_obj, core, db)
