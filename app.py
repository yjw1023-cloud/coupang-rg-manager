from pathlib import Path
import hashlib
import importlib
import sys
import urllib.request

import core

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
    elif name == "erp_import_v07":
        _apply_erp_guard(module)
    return module

importlib.import_module = _rg_import_module

# One-time audited repair. v0.8.3 makes this safe against concurrent
# Streamlit startup/rerun execution.
repair = _original_import_module("legacy_repair_v082")
LEGACY_REPAIR_RESULT = repair.apply(core.DEFAULT_DB)

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
    req = urllib.request.Request(LOADER_URL, headers={"User-Agent": "RG-Manager/0.8.3"})
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
    'st.sidebar.caption("v0.8.3 · repair concurrency fix + purchase W/AB")',
)
globals()["LEGACY_REPAIR_RESULT"] = LEGACY_REPAIR_RESULT
exec(compile(source, str(LOADER), "exec"), globals(), globals())
