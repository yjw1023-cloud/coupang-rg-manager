"""v0.9.66 monthly provisional P&L with stale-snapshot refresh.

Before the monthly snapshot rows are read, refresh all snapshots for the selected
month when the current sales-stat source fingerprint differs from the last clean
v0.9.66 build.  Then reuse the v0.9.65 quantity/return-sale presentation.
"""
from __future__ import annotations

import importlib


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    base_month = importlib.import_module("pnl_month_v0961")
    previous = importlib.import_module("pnl_month_v0965")
    refresh = importlib.import_module("pnl_snapshot_refresh_v0966")
    db = db_path or core.DEFAULT_DB

    original_rows = base_month._snapshot_rows_for_month
    holder = {"refresh": None}

    def snapshot_rows_for_month(core_obj, db_obj, month):
        holder["refresh"] = refresh.refresh_month(core_obj, month, db_obj)
        return original_rows(core_obj, db_obj, month)

    base_month._snapshot_rows_for_month = snapshot_rows_for_month
    try:
        return previous.render_provisional_month_page(st_obj, pd_obj, core, db)
    finally:
        base_month._snapshot_rows_for_month = original_rows
