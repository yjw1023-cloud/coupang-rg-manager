"""v0.9.157 explicit returned-item resale alias for the side-mirror product.

The Coupang return-resale option 95928633818 is not an independent managed SKU.
It belongs to the normal side-mirror product option 95834379201 and must be
consolidated into that product in provisional P&L.  The legacy automatic name
matcher missed it because Coupang appended variant text (", 2개, 천차종") to the
return-resale product name.

This patch only adds the explicit alias to the existing return-sale framework.
The existing repair path then:
- consolidates the child row into the parent row in provisional P&L;
- removes ordinary Coupang-RG sales deduction for the child;
- records the resale against 반품창고;
- archives the child option from the normal managed-product list.
"""
from __future__ import annotations

CHILD_OPTION_ID = "95928633818"
PARENT_OPTION_ID = "95834379201"
DISCOUNT_NAME = "보조거울 백미러 사이드미러 2p 보조미러, 2개, 천차종"


def apply(base_module):
    desired = {
        "parent_option_id": PARENT_OPTION_ID,
        "discount_name": DISCOUNT_NAME,
    }
    current = dict(getattr(base_module, "KNOWN_RETURN_ALIASES", {}).get(CHILD_OPTION_ID) or {})
    changed = current != desired
    base_module.KNOWN_RETURN_ALIASES[CHILD_OPTION_ID] = desired

    # ensure_known_aliases caches per DB. Clear it only when this mapping is newly
    # installed so the existing local DB is repaired on the very next P&L render.
    if changed:
        try:
            base_module._REPAIR_CACHE.clear()
        except Exception:
            pass

    base_module._rg_return_sale_alias_v09157 = True
    return {
        "ok": True,
        "child_option_id": CHILD_OPTION_ID,
        "parent_option_id": PARENT_OPTION_ID,
        "cache_cleared": changed,
    }
