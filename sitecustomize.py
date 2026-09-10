"""RG Manager runtime patch bootstrap.

Keeps the v0.8 purchase import hook, v0.9.87 generic advertising-report
filename period detection, v0.9.103 canonical advertising-data sync/upload,
v0.9.95 user-facing product visibility guard, v0.9.106 dormant-stock
production support, v0.9.108 safe advertising deletion, v0.9.109
advertising source audit/orphan cleanup, v0.9.110 exact one-time ad cleanup,
and optional-feature startup safety.
"""
import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys


# v0.9.190: optional feature modules must never prevent the ERP from starting.
# If the real sales-analysis file is absent, Python's normal PathFinder gets the
# first chance to load it. Only when no real file exists do we provide a small
# safe module that keeps dashboard/data-management/update screens usable.
class _RgOptionalFeatureFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    _NAMES = {"sales_analysis_v09186"}

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self._NAMES:
            return None
        real_spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if real_spec is not None:
            return None
        return importlib.util.spec_from_loader(fullname, self)

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        if module.__name__ != "sales_analysis_v09186":
            return
        module.PAGE_LABEL = "📊  판매분석"
        module._rg_optional_feature_stub = True

        def render_page(st_obj, pd_obj, core, db_path=None):
            st_obj.error(
                "판매분석 기능 파일이 누락되었습니다. ERP 자체는 정상 사용할 수 있습니다. "
                "데이터·관리의 프로그램 업데이트에서 최신 버전으로 복구해 주세요."
            )

        def patch_source(source: str) -> str:
            label = module.PAGE_LABEL
            if f'"{label}",' not in source:
                anchor = '"📈  잠정손익",'
                if anchor in source:
                    source = source.replace(anchor, f'"{label}",\n        ' + anchor, 1)
            marker = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
            handler = f'elif page == "{label}":'
            if marker in source and handler not in source:
                block = (
                    '# ------------------------------\n'
                    '# Sales analysis (safe missing-module page)\n'
                    '# ------------------------------\n'
                    f'{handler}\n'
                    '    sales_analysis_v09186.render_page(st, pd, core)\n\n\n'
                )
                source = source.replace(marker, block + marker, 1)
            return source

        module.render_page = render_page
        module.patch_source = patch_source


if not any(isinstance(x, _RgOptionalFeatureFinder) for x in sys.meta_path):
    sys.meta_path.insert(0, _RgOptionalFeatureFinder())


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

# v0.9.110: user explicitly authorized deletion of exactly one stale report.
try:
    _core_v09110 = _original_import_module("core")
    _ad_force_v09110 = _original_import_module("ad_force_cleanup_v09110")
    _core_v09110.AD_FORCE_CLEANUP_V09110_RESULT = _ad_force_v09110.apply(_core_v09110)
except Exception as exc:
    print(f"RG Manager v0.9.110 exact ad cleanup failed: {exc}", file=sys.stderr)

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

# v0.9.133: register the ten user-supplied Coupang finished products and exact BOMs.
try:
    _core_v09133 = _original_import_module("core")
    _seed_v09133 = _original_import_module("requested_product_seed_v09133")
    _core_v09133.REQUESTED_PRODUCT_SEED_V09133_RESULT = _seed_v09133.apply(_core_v09133)
except Exception as exc:
    print(f"RG Manager v0.9.133 requested product/BOM seed failed: {exc}", file=sys.stderr)

# v0.9.161: register the three user-confirmed rubber-glove RG options and exact BOMs.
try:
    _core_v09161 = _original_import_module("core")
    _glove_seed_v09161 = _original_import_module("rubber_glove_seed_v09161")
    _core_v09161.RUBBER_GLOVE_SEED_V09161_RESULT = _glove_seed_v09161.apply(_core_v09161)
except Exception as exc:
    print(f"RG Manager v0.9.161 rubber glove product/BOM seed failed: {exc}", file=sys.stderr)
