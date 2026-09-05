"""Compatibility entrypoint for v0.9.184.

This module remains the compatibility bootstrap used on every Streamlit rerun.

v0.9.184 hotfix:
- v0.9.183 accidentally shipped this wrapper without the v0.9.182
  ``bom_save_upsert_v09182.py`` dependency in the update manifest. A machine that
  jumped from v0.9.181 to v0.9.183 therefore crashed at startup with
  ModuleNotFoundError before the operator could use the ERP.
- BOM-upsert and production-preview patches are now optional at bootstrap: a
  missing/partially copied patch file is recorded in the result but must never
  prevent the whole ERP from starting.
- The v0.9.184 manifest is cumulative for the v0.9.182~v0.9.183 BOM changes, so a
  normal update still installs and enables every patch.
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


def _optional_patch(module_name, apply_func):
    """Load a UI/runtime patch without ever making ERP startup depend on it."""
    try:
        module = importlib.import_module(module_name)
        module = importlib.reload(module)
        apply_func(module)
        return {"ok": True, "module": module_name}
    except Exception as exc:
        # Partial updater copies must degrade only the optional feature, never the
        # entire ERP. The next updater pass can restore the missing/broken module.
        return {
            "ok": False,
            "module": module_name,
            "error": f"{type(exc).__name__}: {exc}",
        }


def apply(core, db_path=None):
    # These modules can be replaced by the updater while Streamlit keeps the Python
    # process alive. Clear their old copies so newly installed files are used.
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

    bom_upsert_status = _optional_patch(
        "bom_save_upsert_v09182",
        lambda module: module.apply(core),
    )

    def _apply_production_preview(module):
        production_batch = importlib.import_module("production_batch_v095")
        module.apply(production_batch, core)

    production_preview_status = _optional_patch(
        "production_bom_qty_ui_v09183",
        _apply_production_preview,
    )

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
        "bom_save_upsert": bom_upsert_status,
        "production_bom_qty_preview": production_preview_status,
    }
