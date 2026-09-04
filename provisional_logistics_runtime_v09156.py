"""v0.9.156 safe runtime installer for provisional logistics correction.

The live ERP can already have core.estimated_pnl wrapped by older cost/commission
patches. Never replace that chain with a saved baseline function. Wrap the current
live function once and only adjust its returned logistics estimates.
"""
from __future__ import annotations

import importlib

RULE_VERSION = "0.9.156-logistics-preVAT-same-order"


def apply(core, snapshot_refresh_module=None):
    logic = importlib.import_module("provisional_logistics_unit_v09156")
    current = core.estimated_pnl

    # If our wrapper is already the live outer function, keep it. This prevents
    # Streamlit reruns from stacking wrappers indefinitely.
    if getattr(current, "_rg_logistics_rule_version", "") != RULE_VERSION:
        base = current

        def estimated_pnl(sales_import_id, ad_import_id=None, db_path=None):
            db = db_path or core.DEFAULT_DB
            if db_path is None:
                raw, meta = base(sales_import_id, ad_import_id)
            else:
                raw, meta = base(sales_import_id, ad_import_id, db_path)
            fixed, diagnostics = logic._recalculate_raw(raw, core, db)
            meta = dict(meta or {})
            meta["logistics_unit_rule"] = RULE_VERSION
            meta["logistics_unit_diagnostics"] = diagnostics
            core._rg_last_logistics_unit_diagnostics_v09156 = diagnostics
            return fixed, meta

        estimated_pnl._rg_logistics_rule_version = RULE_VERSION
        estimated_pnl._rg_logistics_wrapped_base = base
        core.estimated_pnl = estimated_pnl

    core._rg_provisional_logistics_unit_v09156_applied = True
    if snapshot_refresh_module is not None:
        snapshot_refresh_module._RULE_VERSION = RULE_VERSION
        snapshot_refresh_module._rg_provisional_logistics_unit_v09156_applied = True
    return {"ok": True, "rule_version": RULE_VERSION, "wrapper_chain_preserved": True}
