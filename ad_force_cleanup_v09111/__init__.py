"""v0.9.222 runtime wrapper.

Delegates legacy bootstrap work, then installs the authoritative normal-product
registry, option-ID-only shared product identity, Organic display and the shared
manual return matcher. No product_id-based return detector is loaded.
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
        rules._seed_registry(core, target)
        rules._sync_visibility(core, target)
        rules._patch_return_hide(core)
        sales = rules._patch_sales_analysis()
        organic = rules._patch_organic()
        canonical = {
            "ok": True,
            "active": len(rules.ACTIVE_IDS),
            "discontinued": len(rules.DISCONTINUED_IDS),
            "return_repaired": 0,
            "sales_analysis": sales,
            "organic": organic,
            "matching_policy": "manual_option_id_only",
        }
    except Exception as exc:
        canonical = {"ok": False, "error": str(exc)}
        print(f"RG Manager canonical product rules failed: {exc}")

    try:
        shared = importlib.import_module("shared_product_identity_v09215")
        shared = importlib.reload(shared)
        shared_identity = shared.apply(core, target)
    except Exception as exc:
        shared_identity = {"ok": False, "error": str(exc)}
        print(f"RG Manager shared product identity failed: {exc}")

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
        shared_matcher = matcher.apply(core, target)
    except Exception as exc:
        shared_matcher = {"ok": False, "error": str(exc)}
        print(f"RG Manager shared return matcher failed: {exc}")

    if isinstance(result, dict):
        result["canonical_product_rules_v09214"] = canonical
        result["shared_product_identity_v09215"] = shared_identity
        result["organic_sales_display_v09216"] = organic_display
        result["shared_return_match_ui_v09217"] = shared_matcher
        result["return_master_match_fix_v09219"] = {"ok": False, "disabled": True, "reason": "product_id_matching_removed_v09222"}
    return result
