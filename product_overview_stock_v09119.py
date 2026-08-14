"""RG Manager v0.9.119 product-overview own-stock KPI correction.

The top '자체창고재고' KPI is intended to summarize the raw/BOM stock available
for producing the selected finished product.  The previous v0.9.118 screen read
the selected finished product's own-warehouse balance instead, which can be zero
while its BOM component stock is positive.

This patch changes only the top KPI.  The detailed warehouse table continues to
show the finished product's actual warehouse balances, and the BOM table remains
unchanged.
"""
from __future__ import annotations

from typing import Any


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def apply(overview_module):
    if getattr(overview_module, "_rg_product_overview_stock_v09119_applied", False):
        return overview_module

    base = getattr(overview_module, "_base", None)
    if base is None or not hasattr(base, "_bom") or not hasattr(overview_module, "_render_cards"):
        return overview_module

    original_render_page = overview_module.render_page

    def render_page(st, pd_obj, core, db_path=None):
        original_bom = base._bom
        original_render_cards = overview_module._render_cards
        state = {"bom_own_stock": None}

        def bom_wrapper(core_arg, db_arg, product_id):
            df, max_make = original_bom(core_arg, db_arg, product_id)
            total = 0.0
            if df is not None and not getattr(df, "empty", True) and "own_stock" in df.columns:
                try:
                    total = float(pd_obj.to_numeric(df["own_stock"], errors="coerce").fillna(0).sum())
                except Exception:
                    total = sum(_num(x) for x in list(df["own_stock"]))
            state["bom_own_stock"] = total
            return df, max_make

        def cards_wrapper(st_obj, items):
            total = state.get("bom_own_stock")
            patched = []
            for label, value, negative in list(items or []):
                if str(label) == "자체창고재고" and total is not None:
                    value = overview_module._qty(total)
                    negative = bool(total < 0)
                patched.append((label, value, negative))
            return original_render_cards(st_obj, patched)

        base._bom = bom_wrapper
        overview_module._render_cards = cards_wrapper
        try:
            return original_render_page(st, pd_obj, core, db_path)
        finally:
            base._bom = original_bom
            overview_module._render_cards = original_render_cards

    overview_module.render_page = render_page
    overview_module._rg_product_overview_stock_v09119_applied = True
    return overview_module
