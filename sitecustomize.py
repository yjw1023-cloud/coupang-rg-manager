"""RG Manager v0.8 runtime patch bootstrap.

v0.7 app.py가 purchase_v06를 import할 때 v0.8 매입 규칙을 적용한다.
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


def _rg_import_module(name, package=None):
    module = _original_import_module(name, package)
    if name == "purchase_v06":
        _apply_purchase_v08(module)
    return module


if not getattr(importlib, "_rg_v08_patched", False):
    importlib.import_module = _rg_import_module
    importlib._rg_v08_patched = True

_apply_purchase_v08(sys.modules.get("purchase_v06"))
