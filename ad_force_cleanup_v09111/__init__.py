"""v0.9.216 runtime wrapper.

Delegates legacy bootstrap work, then applies authoritative normal-product rules,
ERP-wide shared product identity, and the hardened Organic sales display.
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

    try:
        rules = importlib.import_module("canonical_product_rules_v09214")
        rules = importlib.reload(rules)
        canonical = rules.apply(core, db)
    except Exception as exc:
        canonical = {"ok": False, "error": str(exc)}
        print(f"RG Manager v0.9.214 canonical product rules failed: {exc}")

    try:
        shared = importlib.import_module("shared_product_identity_v09215")
        shared = importlib.reload(shared)
        shared_identity = shared.apply(core, db)
    except Exception as exc:
        shared_identity = {"ok": False, "error": str(exc)}
        print(f"RG Manager v0.9.215 shared product identity failed: {exc}")

    try:
        display = importlib.import_module("organic_sales_display_v09216")
        display = importlib.reload(display)
        organic_display = display.apply(core, db)
    except Exception as exc:
        organic_display = {"ok": False, "error": str(exc)}
        print(f"RG Manager v0.9.216 organic display patch failed: {exc}")

    if isinstance(result, dict):
        result["canonical_product_rules_v09214"] = canonical
        result["shared_product_identity_v09215"] = shared_identity
        result["organic_sales_display_v09216"] = organic_display
    return result
