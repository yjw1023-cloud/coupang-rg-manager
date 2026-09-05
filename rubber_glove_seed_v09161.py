"""Compatibility entrypoint for v0.9.183.

v0.9.172 is based on an audit of the user's live rocketgrowth.db, not inferred
JDS codes. It first repairs the 18th purchase import, then reapplies the normal
glove finished-product/commercial defaults against the repaired durable source
mapping, normalizes any remaining BUY codes, and keeps every JDS generator on
max(existing)+1.

v0.9.175 clears the warehouse-stocktake exporter cache so updated API stock
prefill takes effect immediately. v0.9.176 also clears the target-Excel format
and previous-actual modules so the fixed prefill wiring is loaded immediately
after an in-app update without a full process restart.

v0.9.182 force-loads the BOM upsert patch on every app rerun. Saving an
already-existing finished/component pair therefore edits bom_items.qty_per instead
of reporting success after an insert-ignore no-op.

v0.9.183 also preloads the production batch module and applies the BOM quantity
preview patch, so the production target table shows the actual per-finished-unit
required quantities rather than only the number of component rows.
"""
from __future__ import annotations

import importlib
import sys
import purchase_import32_repair_v09172 as _purchase_repair
import rubber_glove_seed_v09163_base as _base
import buy_code_normalize_v09164 as _buy
import purchase_code_generation_v09172 as _code_generation

_purchase_repair = importlib.reload(_purchase_repair)
_base = importlib.reload(_base)
_buy = importlib.reload(_buy)
_code_generation = importlib.reload(_code_generation)


def apply(core, db_path=None):
    # These modules are imported later in app/goal rendering. Remove old
    # Streamlit-run copies now so the updated files are read on the next import.
    for name in (
        "inventory_stocktake_v0969",
        "goal_excel_format_v09100",
        "goal_prev_actual_template_v09174",
        "bom_save_upsert_v09182",
        "production_bom_qty_ui_v09183",
        "production_batch_v095",
    ):
        sys.modules.pop(name, None)
    importlib.invalidate_caches()

    # v0.9.182: core.add_bom historically behaved like add-only for an existing
    # parent/component pair. Load the edit-aware wrapper before the BOM screen is
    # rendered so the same Save button becomes INSERT-or-UPDATE as operators expect.
    bom_upsert = importlib.import_module("bom_save_upsert_v09182")
    bom_upsert = importlib.reload(bom_upsert)
    bom_upsert.apply(core)

    # v0.9.183: production_batch_v095 is normally imported later by app.py. Load
    # and patch it here first so an in-app updater refresh cannot leave the old
    # `N개 구성품` preview module cached for the current Streamlit process.
    production_batch = importlib.import_module("production_batch_v095")
    bom_qty_ui = importlib.import_module("production_bom_qty_ui_v09183")
    bom_qty_ui = importlib.reload(bom_qty_ui)
    bom_qty_ui.apply(production_batch, core)

    # Important order: repair the actual purchase/product/inventory ownership first.
    purchase_repair_result = _purchase_repair.apply(core, db_path=db_path)

    # Existing commercial defaults + latest-first inventory presentation remain in
    # one place. Once the durable source map is repaired, this base module resolves
    # glove S/M/L from the correct product IDs rather than guessed codes.
    base_result = _base.apply(core, db_path=db_path)
    buy_result = _buy.apply(core, db_path=db_path)

    # Apply last so both the new-item review proposal and actual creation path use
    # the same max(existing JDS)+1 policy after all one-time repairs are complete.
    code_generation_result = _code_generation.apply(core)

    return {
        "ok": bool(purchase_repair_result.get("ok")),
        "purchase_import32_repair": purchase_repair_result,
        "base": base_result,
        "buy_code_normalize": buy_result,
        "jds_code_generation": code_generation_result,
        "bom_save_upsert": True,
        "production_bom_qty_preview": True,
    }
