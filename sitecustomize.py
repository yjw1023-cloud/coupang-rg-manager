"""RG Manager runtime patch bootstrap.

Keeps the v0.8 purchase import hook, v0.9.87 generic advertising-report
filename period detection, v0.9.88 canonical advertising-data sync, and
v0.9.95 user-facing product visibility guard active from process startup.
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


def _rg_import_module(name, package=None):
    module = _original_import_module(name, package)
    if name == "purchase_v06":
        _apply_purchase_v08(module)
    elif name in ("item_ui_v086", "goal_management_v0979"):
        _apply_product_visibility(module)
    return module


if not getattr(importlib, "_rg_v08_patched", False):
    importlib.import_module = _rg_import_module
    importlib._rg_v08_patched = True

_apply_purchase_v08(sys.modules.get("purchase_v06"))

# v0.9.87: the legacy '새 자료 반영' advertising uploader is separate from
# provisional_ad_report_v0956, so patch its selector/uploader/date widgets here.
try:
    _ad_period = _original_import_module("ad_period_v0987")
    _ad_period.apply()
except Exception as exc:
    print(f"RG Manager v0.9.87 ad period patch failed: {exc}", file=sys.stderr)

# v0.9.88: make the generic Coupang data-management advertising uploader write
# the same canonical provisional-ad tables used by dashboard/goal/P&L screens,
# and make its status card display that same canonical source.
try:
    _core_v0988 = _original_import_module("core")
    _ad_sync_v0988 = _original_import_module("data_management_sync_v0988")
    _ad_sync_v0988.apply(_core_v0988)
except Exception as exc:
    print(f"RG Manager v0.9.88 ad data sync patch failed: {exc}", file=sys.stderr)

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
