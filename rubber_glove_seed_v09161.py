"""Compatibility entrypoint for v0.9.164.

Keeps the v0.9.163 rubber-glove registration logic intact and then normalizes
legacy BUY-* own-item codes to sequential JDS codes.
"""
from __future__ import annotations

import rubber_glove_seed_v09163_base as _base
import buy_code_normalize_v09164 as _buy


def apply(core, db_path=None):
    base_result = _base.apply(core, db_path=db_path)
    buy_result = _buy.apply(core, db_path=db_path)
    return {
        "ok": True,
        "base": base_result,
        "buy_code_normalize": buy_result,
    }
