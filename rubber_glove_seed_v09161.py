"""Compatibility entrypoint for v0.9.171.

Runs existing glove/product maintenance and then repairs the S/M/L glove BOMs
from the durable purchase source_name/source_detail -> product_id mapping.
No JDS code is guessed in the final repair step.
"""
from __future__ import annotations

import importlib
import rubber_glove_seed_v09163_base as _base
import buy_code_normalize_v09164 as _buy
import purchase_option_name_repair_v09165 as _option_name
import rubber_glove_bom_source_map_v09171 as _source_bom

_base = importlib.reload(_base)
_buy = importlib.reload(_buy)
_option_name = importlib.reload(_option_name)
_source_bom = importlib.reload(_source_bom)


def apply(core, db_path=None):
    base_result = _base.apply(core, db_path=db_path)
    buy_result = _buy.apply(core, db_path=db_path)
    option_name_result = _option_name.apply(core, db_path=db_path)
    source_bom_result = _source_bom.apply(core, db_path=db_path)
    return {
        "ok": bool(source_bom_result.get("ok")),
        "base": base_result,
        "buy_code_normalize": buy_result,
        "purchase_option_name_repair": option_name_result,
        "rubber_glove_source_bom": source_bom_result,
    }
