"""Compatibility entrypoint for v0.9.198.

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

v0.9.196:
- install the provisional sales-data revenue basis and monthly manual unit-cost
  override patch without making ERP startup depend on the optional UI patch.

v0.9.197:
- bridge those rules into the actual monthly P&L page, which renders saved
  snapshots through a custom HTML table instead of the legacy dataframe hook.

v0.9.198:
- replace the separate product-picker cost form with direct editing of the two
  average unit-cost cells in the monthly P&L table.
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
        return {
            "ok": False,
            "module": module_name,
            "error": f"{type(exc).__name__}: {exc}",
        }


def apply(core, db_path=None):
    for name in (
        "inventory_stocktake_v0969",
        "goal_excel_format_v09100",
        "goal_prev_actual_template_v09174",
        "bom_save_upsert_v09182",
        "production_bom_qty_ui_v09183",
        "production_batch_v095",
        "provisional_sales_basis_v09196",
        "pnl_month_sales_basis_v09197",
        "pnl_inline_editor_v09198",
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

    provisional_sales_basis_status = _optional_patch(
        "provisional_sales_basis_v09196",
        lambda module: module.apply(core, db_path=db_path),
    )

    provisional_month_bridge_status = _optional_patch(
        "pnl_month_sales_basis_v09197",
        lambda module: module.apply(core, db_path=db_path),
    )

    provisional_inline_editor_status = _optional_patch(
        "pnl_inline_editor_v09198",
        lambda module: module.apply(core, db_path=db_path),
    )

    purchase_repair_result = _purchase_repair.apply(core, db_path=db_path)
    base_result = _base.apply(core, db_path=db_path)
    buy_result = _buy.apply(core, db_path=db_path)
    code_generation_result = _code_generation.apply(core)

    return {
        "ok": bool(purchase_repair_result.get("ok")),
        "purchase_import32_repair": purchase_repair_result,
        "base": base_result,
        "buy_code_normalize": buy_result,
        "jds_code_generation": code_generation_result,
        "bom_save_upsert": bom_upsert_status,
        "production_bom_qty_preview": production_preview_status,
        "provisional_sales_basis": provisional_sales_basis_status,
        "provisional_month_bridge": provisional_month_bridge_status,
        "provisional_inline_editor": provisional_inline_editor_status,
    }
