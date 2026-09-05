"""Compatibility entrypoint for v0.9.169.

Runs the v0.9.163 rubber-glove registration logic, v0.9.164 BUY->JDS code
normalization, v0.9.166 purchase option/detail name repair, and the v0.9.169
verified rubber-glove BOM repair.

Modules replaced by the hot updater are explicitly reloaded because Streamlit may
keep previous module objects alive after files change on disk.
"""
from __future__ import annotations

import importlib
import rubber_glove_seed_v09163_base as _base
import buy_code_normalize_v09164 as _buy
import purchase_option_name_repair_v09165 as _option_name
import rubber_glove_bom_repair_v09169 as _bom_repair

_base = importlib.reload(_base)
_buy = importlib.reload(_buy)
_option_name = importlib.reload(_option_name)
_bom_repair = importlib.reload(_bom_repair)


def apply(core, db_path=None):
    base_result = _base.apply(core, db_path=db_path)
    buy_result = _buy.apply(core, db_path=db_path)
    option_name_result = _option_name.apply(core, db_path=db_path)
    bom_repair_result = _bom_repair.apply(core, db_path=db_path)
    return {
        "ok": bool(bom_repair_result.get("ok")),
        "base": base_result,
        "buy_code_normalize": buy_result,
        "purchase_option_name_repair": option_name_result,
        "rubber_glove_bom_repair": bom_repair_result,
    }
