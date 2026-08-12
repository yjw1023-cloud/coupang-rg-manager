from pathlib import Path
import hashlib
import importlib
import sys
import urllib.request

import core

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# v0.9.62: Streamlit reruns reuse Python's module cache.  The updater replaces
# files on disk, but without clearing these modules an already-running ERP can
# keep executing the previous P&L renderer until the whole process is restarted.
# Purge only the monthly provisional renderer chain on every app rerun so the
# freshly downloaded implementation is used immediately after an update.
for _rg_mod in (
    "pnl_month_default_v0915",
    "pnl_month_v0959",
    "pnl_month_v0960",
    "pnl_month_v0961",
):
    sys.modules.pop(_rg_mod, None)
importlib.invalidate_caches()

# Apply v0.8 purchase rules and legacy-import guards whenever
# the pinned v0.7 loader imports those modules.
_original_import_module = importlib.import_module

def _apply_purchase_v08(module):
    if module is None or getattr(module, "_rg_purchase_v08_applied", False):
        return module
    patch = _original_import_module("purchase_v08")
    patch.apply(module)
    module._rg_purchase_v08_applied = True
    return module

def _apply_purchase_batch_v089(module):
    if module is None or getattr(module, "_rg_purchase_batch_v089_applied", False):
        return module
    patch = _original_import_module("purchase_batch_v089")
    patch.apply(module, core)
    return module

def _apply_purchase_match_v090(module):
    if module is None or getattr(module, "_rg_purchase_match_ui_v090_applied", False):
        return module
    patch = _original_import_module("purchase_match_ui_v090")
    patch.apply(module, core)
    return module

def _apply_purchase_match_v091(module):
    if module is None:
        return module
    patch = _original_import_module("purchase_match_ui_v091")
    patch.apply()
    return module

def _apply_erp_guard(module):
    if module is None or getattr(module, "_rg_v082_guard_applied", False):
        return module
    guard = _original_import_module("erp_import_guard_v082")
    guard.apply(module)
    return module

def _rg_import_module(name, package=None):
    module = _original_import_module(name, package)
    if name == "purchase_v06":
        _apply_purchase_v08(module)
        _apply_purchase_batch_v089(module)
        _apply_purchase_match_v090(module)
        _apply_purchase_match_v091(module)
    elif name == "erp_import_v07":
        _apply_erp_guard(module)
    return module

importlib.import_module = _rg_import_module

# One-time audited repair. v0.8.3 makes this safe against concurrent
# Streamlit startup/rerun execution.
repair = _original_import_module("legacy_repair_v082")
LEGACY_REPAIR_RESULT = repair.apply(core.DEFAULT_DB)

# v0.8.4 inventory presentation: warehouse tabs + user-facing Coupang code.
inventory_ui = _original_import_module("inventory_ui_v084")
inventory_ui.apply()

# v0.8.5 production routing baseline.
production_patch = _original_import_module("production_v085")
production_patch.apply(core)

# v0.8.6 dedicated item master page.
item_ui_v086 = _original_import_module("item_ui_v086")

# v0.9.44 dedicated item deletion / manual return-option cleanup page.
item_delete_ui_v0944 = _original_import_module("item_delete_ui_v0944")

# v0.9.45: remove obsolete Item Master delete footer and allow sales-only
# negative stock on a user-confirmed return child to be cleaned automatically.
item_delete_fix_v0945 = _original_import_module("item_delete_fix_v0945")
item_delete_fix_v0945.apply(item_ui_v086, item_delete_ui_v0944, core)

# v0.8.7 sales-stat upload period. v0.9.7 fixes duplicate replacement widget rendering.
sales_period_v087 = _original_import_module("sales_period_v087")
sales_period_v087.apply(core)

# v0.8.8 actual-event inventory rule: production/sales are posted even when
# stock is insufficient, so shortages remain visible as negative inventory.
inventory_flow_v088 = _original_import_module("inventory_flow_v088")
inventory_flow_v088.apply(core)

# v0.9.2 item-by-item purchase history.
purchase_history_v092 = _original_import_module("purchase_history_v092")

# v0.9.4 purchase history UX: visible item list + click-to-open history.
purchase_history_v094 = _original_import_module("purchase_history_v094")
purchase_history_v094.apply(purchase_history_v092)

# v0.9.42 purchase-cost UX: latest + quantity-weighted average purchase cost.
purchase_cost_ui_v0942 = _original_import_module("purchase_cost_ui_v0942")
purchase_cost_ui_v0942.apply(purchase_history_v092)

# v0.9.3 return management dashboard.
return_management_v093 = _original_import_module("return_management_v093")

# v0.9.5 all-or-nothing production from Coupang inbound Excel.
production_batch_v095 = _original_import_module("production_batch_v095")

# v0.9.40 direct production-target BOM reconciliation. Apply immediately after
# the production module loads so the production page cannot miss the patch.
production_bom_link_v0940 = _original_import_module("production_bom_link_v0940")
production_bom_link_v0940.apply(core, production_batch_v095)

# v0.9.6 reusable search boxes for product/item list tables.
search_ui_v096 = _original_import_module("search_ui_v096")
search_ui_v096.apply()

# v0.9.12 P&L menu separation + provisional snapshot capture.
pnl_views_v0912 = _original_import_module("pnl_views_v0912")
pnl_views_v0912.apply(core)

# v0.9.8 sales P&L presentation cleanup.
sales_pnl_ui_v098 = _original_import_module("sales_pnl_ui_v098")
sales_pnl_ui_v098.apply()

# v0.9.9 returned-item discount resale.
return_discount_v099 = _original_import_module("return_discount_v099")
return_discount_v099.apply(core)

# v0.9.44 exact ERP option = normal sale; unknown similar + discounted option = return sale.
return_sale_match_v0944 = _original_import_module("return_sale_match_v0944")
return_sale_match_v0944.apply(return_discount_v099, core)

# v0.9.11 moving weighted-average cost + 10.8% commission fallback.
pnl_cost_commission_v0911 = _original_import_module("pnl_cost_commission_v0911")
pnl_cost_commission_v0911.apply(core)

# v0.9.10 zero-quantity display guard.
sales_pnl_zero_v0910 = _original_import_module("sales_pnl_zero_v0910")
sales_pnl_zero_v0910.apply()

# v0.9.13 final consolidated provisional P&L dataframe transform.
provisional_pnl_ui_v0913 = _original_import_module("provisional_pnl_ui_v0913")
provisional_pnl_ui_v0913.apply(core)

# v0.9.14 month helpers remain valid; only its old source-moving patch was unsafe.
pnl_month_default_v0914 = _original_import_module("pnl_month_default_v0914")
pnl_month_default_v0914.apply()

# v0.9.15 safe monthly routing: never copies/re-indents legacy source blocks.
pnl_month_default_v0915 = _original_import_module("pnl_month_default_v0915")

# v0.9.31: the patched legacy BOM UI contains direct references to this module.
# Export it into the globals used by exec() so BOM selectors can resolve it.
bom_candidate_filter_v0927 = _original_import_module("bom_candidate_filter_v0927")

# Keep a stable copy of the known-good v0.7 loader.
LOADER_DIR = ROOT / "_code_base"
LOADER_DIR.mkdir(parents=True, exist_ok=True)
LOADER = LOADER_DIR / "app_loader_v07.py"
LOADER_BLOB_SHA = "4f0032c0a3475711453b541123bf6220d1cd2bb0"
LOADER_URL = (
    "https://raw.githubusercontent.com/yjw1023-cloud/coupang-rg-manager/"
    "f93d2b0576e4f67a250bd3a98af0d439f11541ec/app.py"
)

def _git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()

def _ensure_loader():
    if LOADER.exists():
        try:
            data = LOADER.read_bytes()
            if _git_blob_sha(data) == LOADER_BLOB_SHA:
                return
        except Exception:
            pass
    req = urllib.request.Request(LOADER_URL, headers={"User-Agent": "RG-Manager/0.9.15"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = resp.read()
    if _git_blob_sha(data) != LOADER_BLOB_SHA:
        raise RuntimeError("기본 실행 모듈 검증에 실패했습니다. 업데이트 파일을 다시 확인해 주세요.")
    tmp = LOADER.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(LOADER)

_ensure_loader()
source = LOADER.read_text(encoding="utf-8")
source = source.replace(
    'st.sidebar.caption("v0.7 · legacy ERP import")',
    'st.sidebar.caption("v0.9.15 · safe monthly P&L")',
)
loader_exec = 'exec(compile(source, str(BASE_APP), "exec"), globals(), globals())'
if loader_exec not in source:
    raise RuntimeError("기본 실행 모듈의 최종 실행 위치를 찾지 못했습니다.")
source = source.replace(
    loader_exec,
    'source = item_ui_v086.patch_source(source)\n'
    'source = item_delete_ui_v0944.patch_source(source)\n'
    'source = purchase_history_v092.patch_source(source)\n'
    'source = return_management_v093.patch_source(source)\n'
    'source = production_batch_v095.patch_source(source)\n'
    'source = pnl_views_v0912.patch_source(source)\n'
    'source = provisional_pnl_ui_v0913.patch_source(source)\n'
    'source = pnl_month_default_v0915.patch_source(source)\n' + loader_exec,
    1,
)
globals()["LEGACY_REPAIR_RESULT"] = LEGACY_REPAIR_RESULT
globals()["item_ui_v086"] = item_ui_v086
globals()["item_delete_ui_v0944"] = item_delete_ui_v0944
globals()["item_delete_fix_v0945"] = item_delete_fix_v0945
globals()["purchase_history_v092"] = purchase_history_v092
globals()["purchase_history_v094"] = purchase_history_v094
globals()["return_management_v093"] = return_management_v093
globals()["production_batch_v095"] = production_batch_v095
globals()["search_ui_v096"] = search_ui_v096
globals()["pnl_views_v0912"] = pnl_views_v0912
globals()["sales_pnl_ui_v098"] = sales_pnl_ui_v098
globals()["return_discount_v099"] = return_discount_v099
globals()["return_sale_match_v0944"] = return_sale_match_v0944
globals()["pnl_cost_commission_v0911"] = pnl_cost_commission_v0911
globals()["sales_pnl_zero_v0910"] = sales_pnl_zero_v0910
globals()["provisional_pnl_ui_v0913"] = provisional_pnl_ui_v0913
globals()["pnl_month_default_v0914"] = pnl_month_default_v0914
globals()["pnl_month_default_v0915"] = pnl_month_default_v0915
globals()["bom_candidate_filter_v0927"] = bom_candidate_filter_v0927
exec(compile(source, str(LOADER), "exec"), globals(), globals())
