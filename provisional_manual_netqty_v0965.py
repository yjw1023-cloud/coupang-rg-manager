"""v0.9.65 manual provisional adjustment guard.

Once visible 판매수량 becomes gross sales, a manual unit-price override must still
multiply by signed 순판매수량.  This wrapper temporarily feeds net quantity to the
existing v0.9.52 adjustment function, then restores the gross display quantity.
"""
from __future__ import annotations


def apply(target_module):
    if getattr(target_module, "_rg_manual_netqty_v0965_applied", False):
        return target_module

    original = target_module.apply_to_view

    def apply_to_view(view, adjustments):
        if view is None or getattr(view, "empty", True) or "순판매수량" not in view.columns or "판매수량" not in view.columns:
            return original(view, adjustments)
        temp = view.copy()
        gross = temp["판매수량"].copy()
        temp["판매수량"] = temp["순판매수량"]
        out, meta = original(temp, adjustments)
        if out is not None and not getattr(out, "empty", True) and "판매수량" in out.columns:
            out["판매수량"] = gross.reindex(out.index)
        return out, meta

    target_module.apply_to_view = apply_to_view
    target_module._rg_manual_netqty_v0965_applied = True
    return target_module
