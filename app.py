from pathlib import Path
import importlib
import sys
import urllib.request

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# v0.8.1: apply the purchase rules explicitly from app.py.
# Do not rely on sitecustomize.py because the project directory is not
# guaranteed to be on sys.path during Python interpreter startup.
_original_import_module = importlib.import_module

def _apply_purchase_v08(module):
    if module is None or getattr(module, "_rg_purchase_v08_applied", False):
        return module
    patch = _original_import_module("purchase_v08")
    patch.apply(module)
    module._rg_purchase_v08_applied = True
    return module

def _rg_import_module(name, package=None):
    module = _original_import_module(name, package)
    if name == "purchase_v06":
        _apply_purchase_v08(module)
    return module

importlib.import_module = _rg_import_module

# The updater keeps the previous app.py in _code_backup before replacing it.
# Prefer that local copy so normal startup does not depend on the network.
BACKUP_LOADER = ROOT / "_code_backup" / "app.py"
if BACKUP_LOADER.exists():
    source = BACKUP_LOADER.read_text(encoding="utf-8")
else:
    # Recovery path for installations without a backup.
    BASE_URL = (
        "https://raw.githubusercontent.com/yjw1023-cloud/"
        "coupang-rg-manager/f93d2b0576e4f67a250bd3a98af0d439f11541ec/app.py"
    )
    req = urllib.request.Request(BASE_URL, headers={"User-Agent": "RG-Manager/0.8.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        source = resp.read().decode("utf-8")

# Avoid accidental recursive execution if an unusual backup already contains
# this v0.8.1 bootstrap.
if "v0.8.1: apply the purchase rules explicitly from app.py" in source:
    raise RuntimeError(
        "이전 app.py 백업이 올바르지 않습니다. 프로그램 업데이트를 다시 실행해 주세요."
    )

source = source.replace(
    'st.sidebar.caption("v0.7 · legacy ERP import")',
    'st.sidebar.caption("v0.8.1 · purchase W/AB + own warehouse")',
)

globals()["_rg_purchase_v08_apply"] = _apply_purchase_v08
exec(compile(source, str(BACKUP_LOADER if BACKUP_LOADER.exists() else ROOT / "app_v07_remote.py"), "exec"), globals(), globals())
