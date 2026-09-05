"""Compatibility entrypoint for v0.9.170.

Runs the existing glove/product maintenance and then the direct user-confirmed
S/M/L BOM registration. The final v0.9.170 step writes and immediately verifies
JDS761/JDS762/JDS763 x5 against Coupang options 96012086788/789/790.
"""
from __future__ import annotations

import importlib
import rubber_glove_seed_v09163_base as _base
import buy_code_normalize_v09164 as _buy
import purchase_option_name_repair_v09165 as _option_name
import rubber_glove_force_bom_v09170 as _force_bom

_base = importlib.reload(_base)
_buy = importlib.reload(_buy)
_option_name = importlib.reload(_option_name)
_force_bom = importlib.reload(_force_bom)


def apply(core, db_path=None):
    base_result = _base.apply(core, db_path=db_path)
    buy_result = _buy.apply(core, db_path=db_path)
    option_name_result = _option_name.apply(core, db_path=db_path)
    force_bom_result = _force_bom.apply(core, db_path=db_path)
    return {
        "ok": bool(force_bom_result.get("ok")),
        "base": base_result,
        "buy_code_normalize": buy_result,
        "purchase_option_name_repair": option_name_result,
        "rubber_glove_force_bom": force_bom_result,
    }
