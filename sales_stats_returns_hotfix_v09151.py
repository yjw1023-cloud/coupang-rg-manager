"""v0.9.153 sales-stat return quantity hotfix bootstrap.

The earlier rowid hotfix is now superseded by sales_stats_quantity_guard_v09153,
which fixes rowid handling and, more importantly, validates the actual workbook
quantity columns so net quantity can never be reused as cancel quantity.
"""
from __future__ import annotations

import importlib


def apply(base):
    # base is reloaded on every Streamlit rerun, so reload and reapply the guard
    # every time as well. Do not short-circuit on any old marker.
    import sales_stats_quantity_guard_v09153

    guard = importlib.reload(sales_stats_quantity_guard_v09153)
    guard.apply(base)
    base._rg_sales_stats_returns_hotfix_v09151 = True
    base._rg_sales_stats_returns_hotfix_v09152 = True
    base._rg_sales_stats_returns_hotfix_v09153 = True
    return base
