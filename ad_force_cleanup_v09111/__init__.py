"""v0.9.225 runtime bootstrap.

The user's uploaded product master is the authority. Its 132 option IDs are kept
in canonical_product_rules_v09214.CURRENT_IDS and are used directly by Organic,
Sales Analysis, provisional P&L and shared identity. The old DB registry is no
longer used to decide whether an option is a normal product.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
import sys


def _load_legacy():
    path = Path(__file__).resolve().parent.parent / "ad_force_cleanup_v09111.py"
    name = "_rg_legacy_ad_force_cleanup_v09111"
    spec = importlib.util.spec_from_file_location(name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError(f"legacy bootstrap not found: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def apply(core, db=None):
    legacy = _load_legacy()
    result = legacy.apply(core, db)
    target = db or core.DEFAULT_DB

    try:
        rules = importlib.import_module("canonical_product_rules_v09214")
        rules = importlib.reload(rules)
        core.init_db(target)
        master_ids = set(rules.CURRENT_IDS)

        # The uploaded master list itself is authoritative. Do not seed/read a
        # second registry for normal-vs-return classification.
        rules._sync_visibility(core, target)
        rules._patch_return_hide(core)
        sales = rules._patch_sales_analysis()
        organic = rules._patch_organic()
        canonical = {
            "ok": True,
            "master_count": len(master_ids),
            "active": len(rules.ACTIVE_IDS),
            "discontinued": len(rules.DISCONTINUED_IDS),
            "source": "uploaded_master_CURRENT_IDS",
            "registry_used_for_classification": False,
            "sales_analysis": sales,
            "organic": organic,
        }
    except Exception as exc:
        master_ids = set()
        canonical = {"ok": False, "error": str(exc)}
        print(f"RG Manager canonical product rules failed: {exc}")

    try:
        shared = importlib.import_module("shared_product_identity_v09215")
        shared = importlib.reload(shared)
        if master_ids:
            shared._verified_normal_ids = lambda con: set(master_ids)
        shared_identity = shared.apply(core, target)
    except Exception as exc:
        shared_identity = {"ok": False, "error": str(exc)}
        print(f"RG Manager shared product identity failed: {exc}")

    # Patch the actual Organic data function's master lookup before the display
    # module renders it. This prevents a stale/incomplete DB registry from ever
    # classifying a master product (e.g. discontinued items) as a return.
    try:
        organic_module = importlib.import_module("organic_sales_estimate_v09211")
        if master_ids:
            organic_module._normal_ids = lambda con, sales_module: set(master_ids)
    except Exception as exc:
        print(f"RG Manager organic master source patch failed: {exc}")

    try:
        display = importlib.import_module("organic_sales_display_v09216")
        display = importlib.reload(display)
        organic_display = display.apply(core, target)
    except Exception as exc:
        organic_display = {"ok": False, "error": str(exc)}
        print(f"RG Manager organic display patch failed: {exc}")

    try:
        matcher = importlib.import_module("shared_return_match_ui_v09217")
        matcher = importlib.reload(matcher)
        if master_ids:
            matcher._normal_ids = lambda core_obj, db_path: set(master_ids)
        shared_matcher = matcher.apply(core, target)
    except Exception as exc:
        shared_matcher = {"ok": False, "error": str(exc)}
        print(f"RG Manager shared return matcher failed: {exc}")

    if isinstance(result, dict):
        result["canonical_product_rules_v09214"] = canonical
        result["shared_product_identity_v09215"] = shared_identity
        result["organic_sales_display_v09216"] = organic_display
        result["shared_return_match_ui_v09217"] = shared_matcher
        result["normal_product_source"] = "uploaded_master_CURRENT_IDS"
    return result
