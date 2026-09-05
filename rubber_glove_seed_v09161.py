"""Compatibility entrypoint for v0.9.166.

Runs the v0.9.163 rubber-glove registration logic, v0.9.164 BUY->JDS code
normalization, and v0.9.166 purchase option/detail name repair.

The option-name module is explicitly reloaded because Streamlit updater refreshes
replace files on disk while Python can retain the previous module object in cache.
"""
from __future__ import annotations

import importlib
import rubber_glove_seed_v09163_base as _base
import buy_code_normalize_v09164 as _buy
import purchase_option_name_repair_v09165 as _option_name

_option_name = importlib.reload(_option_name)


def apply(core, db_path=None):
    base_result = _base.apply(core, db_path=db_path)
    buy_result = _buy.apply(core, db_path=db_path)
    option_name_result = _option_name.apply(core, db_path=db_path)
    return {
        "ok": True,
        "base": base_result,
        "buy_code_normalize": buy_result,
        "purchase_option_name_repair": option_name_result,
    }
