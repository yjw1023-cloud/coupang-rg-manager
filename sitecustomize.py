"""RG Manager runtime patch bootstrap.

Keeps the v0.8 purchase import hook, v0.9.87 generic advertising-report
filename period detection, v0.9.103 canonical advertising-data sync/upload,
v0.9.95 user-facing product visibility guard, v0.9.106 dormant-stock
production support, v0.9.108 safe advertising deletion, and v0.9.109
advertising source audit/orphan cleanup active from startup.
"""
import importlib
import sys

_original_import_module = importlib.import_module


def _apply_purchase_v08(module):
    if module is None or getattr(module, "_rg_purchase_v08_applied", False):
        return module
    try:
        patch = _original_import_module("purchase_v08")
        patch.apply(module)
        module._rg_purchase_v08_applied = True
    except Exception as exc:
        print(f"RG Manager v0.8 purchase patch failed: {exc}", file=sys.stderr)
    return module


def _apply_product_visibility(module):
    if module is None:
        return module
    try:
        core = _original_import_module("core")
        patch = _original_import_module("product_visibility_v0995")
        patch.apply_core(core)
        if getattr(module, "__name__", "") == "item_ui_v086":
            patch.apply_item_ui(module, core)
        elif getattr(module, "__name__", "") == "goal_management_v0979":
            patch.apply_goal_module(module, core)
    except Exception as exc:
        print(f"RG Manager v0.9.95 product visibility patch failed: {exc}", file=sys.stderr)
    return module


def _apply_dormant_production(module):
    if module is None:
        return module
    try:
        core = _original_import_module("core")
        patch = _original_import_module("production_dormant_stock_v09106")
        patch.apply(core, module)
    except Exception as exc:
        print(f"RG Manager v0.9.106 dormant-stock production patch failed: {exc}", file=sys.stderr)
    return module


def _rg_import_module(name, package=None):
    module = _original_import_module(name, package)
    if name == "purchase_v06":
        _apply_purchase_v08(module)
    elif name in ("item_ui_v086", "goal_management_v0979"):
        _apply_product_visibility(module)
    elif name == "production_batch_v095":
        _apply_dormant_production(module)
    return module


if not getattr(importlib, "_rg_v08_patched", False):
    importlib.import_module = _rg_import_module
    importlib._rg_v08_patched = True

_apply_purchase_v08(sys.modules.get("purchase_v06"))
_apply_dormant_production(sys.modules.get("production_batch_v095"))

# v0.9.87: the legacy '새 자료 반영' advertising uploader is separate from
# provisional_ad_report_v0956, so patch its filename/date widgets here.
try:
    _ad_period = _original_import_module("ad_period_v0987")
    _ad_period.apply()
except Exception as exc:
    print(f"RG Manager v0.9.87 ad period patch failed: {exc}", file=sys.stderr)

# v0.9.103: exact same period is one logical ad report; same file is idempotent.
# v0.9.108: deleting the canonical report also removes the mirrored legacy import.
try:
    _ad_report_v09103 = _original_import_module("provisional_ad_report_v0956")
    _ad_unify_v09103 = _original_import_module("ad_upload_unify_v09103")
    _ad_unify_v09103.apply(_ad_report_v09103)
    _ad_delete_v09108 = _original_import_module("ad_delete_cleanup_v09108")
    _ad_delete_v09108.apply(_ad_report_v09103)
except Exception as exc:
    print(f"RG Manager advertising patch failed: {exc}", file=sys.stderr)

# Generic Coupang data-management advertising uploads feed the same canonical
# provisional-ad tables. v0.9.108 also rejects cross-month reports and unsafe
# partial overlaps before any DB write.
try:
    _core_v0988 = _original_import_module("core")
    _ad_sync_v0988 = _original_import_module("data_management_sync_v0988")
    _ad_sync_v0988.apply(_core_v0988)
except Exception as exc:
    print(f"RG Manager ad data sync patch failed: {exc}", file=sys.stderr)

# v0.9.109: old direct provisional-ad uploads did not appear in Recent Input
# History. Audit the exact user-reported 2026-08-01~2026-08-11 row and remove it
# only when no matching generic input-history record exists. Also tag source
# origin on future direct/generic ad imports.
try:
    _core_v09109 = _original_import_module("core")
    _ad_report_for_source = _original_import_module("provisional_ad_report_v0956")
    _ad_orphan_v09109 = _original_import_module("ad_orphan_cleanup_v09109")
    _ad_orphan_v09109.apply(_core_v09109, _ad_report_for_source)
except Exception as exc:
    print(f"RG Manager v0.9.109 ad source audit failed: {exc}", file=sys.stderr)

# v0.9.95: report-only/return option IDs remain available internally for
# matching and settlement, but never appear as normal ERP items to the user.
try:
    _core_v0995 = _original_import_module("core")
    _visibility_v0995 = _original_import_module("product_visibility_v0995")
    _visibility_v0995.apply_core(_core_v0995)
    _visibility_v0995.apply_item_ui(sys.modules.get("item_ui_v086"), _core_v0995)
    _visibility_v0995.apply_goal_module(sys.modules.get("goal_management_v0979"), _core_v0995)
except Exception as exc:
    print(f"RG Manager v0.9.95 product visibility startup failed: {exc}", file=sys.stderr)
