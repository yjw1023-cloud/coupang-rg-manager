"""v0.9.246 direct GitHub API updater.

Replaces the legacy "최신 버전 확인" button block at runtime.
- Reads update/latest.json from GitHub Contents API, not raw CDN.
- Adds no-cache headers and a unique query parameter.
- Persists the fetched manifest in session_state.
- Can apply the manifest files directly from the same GitHub Contents API.
- Never touches data/, .venv/, sample_data/.
"""
from __future__ import annotations

import ast
import base64
import urllib.error
import json
import os
import importlib
import sys
import shutil
import tempfile
import time
import urllib.request
import urllib.parse
from pathlib import Path

REPO = "yjw1023-cloud/coupang-rg-manager"
API_ROOT = f"https://api.github.com/repos/{REPO}/contents"
RAW_ROOT = f"https://raw.githubusercontent.com/{REPO}/main"
_STATE = "_rg_direct_updater_v09246_manifest"


def _request_json(url: str):
    sep = "&" if "?" in url else "?"
    req = urllib.request.Request(
        f"{url}{sep}_rgcb={time.time_ns()}",
        headers={
            "User-Agent": "RG-Manager/0.9.246",
            "Accept": "application/vnd.github+json",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _fetch_raw(path: str) -> bytes:
    safe = "/".join(urllib.parse.quote(x, safe="") for x in str(path).split("/"))
    sep = "&" if "?" in safe else "?"
    req = urllib.request.Request(
        f"{RAW_ROOT}/{safe}?_rgcb={time.time_ns()}",
        headers={
            "User-Agent": "RG-Manager/0.9.253",
            "Cache-Control": "no-cache, no-store, max-age=0",
            "Pragma": "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _fetch_file(path: str) -> bytes:
    # Prefer the GitHub Contents API when available, but automatically fall back
    # to raw.githubusercontent.com when the unauthenticated API rate limit is
    # exhausted (HTTP 403) or the API is otherwise temporarily unavailable.
    try:
        obj = _request_json(f"{API_ROOT}/{path}?ref=main")
        content = obj.get("content")
        if content:
            return base64.b64decode(content)
    except Exception:
        pass
    return _fetch_raw(path)


def _fetch_manifest_via_commit():
    # Resolve the current main-branch commit first, then read the manifest at that
    # immutable SHA. This avoids stale raw-CDN branch snapshots.
    ref = _request_json(f"https://api.github.com/repos/{REPO}/git/ref/heads/main")
    sha = str(((ref or {}).get("object") or {}).get("sha") or "").strip()
    if not sha:
        raise RuntimeError("GitHub main 브랜치 커밋을 확인하지 못했습니다.")
    safe = urllib.parse.quote("update/latest.json", safe="/")
    obj = _request_json(f"{API_ROOT}/{safe}?ref={sha}")
    content = obj.get("content")
    if not content:
        raise RuntimeError("GitHub에서 최신 업데이트 정보를 읽지 못했습니다.")
    return base64.b64decode(content)


def fetch_manifest(root=None):
    """Read several independent GitHub paths and keep the newest manifest.

    GitHub can briefly expose different branch snapshots through ref, contents,
    and raw endpoints immediately after a commit. One click therefore checks all
    available paths (with short retries) instead of trusting the first response.
    """
    candidates = []
    errors = []

    for attempt in range(3):
        readers = (
            _fetch_manifest_via_commit,
            lambda: base64.b64decode(
                _request_json(f"{API_ROOT}/update/latest.json?ref=main").get("content") or b""
            ),
            lambda: _fetch_raw("update/latest.json"),
        )
        for reader in readers:
            try:
                raw = reader()
                if not raw:
                    continue
                manifest = json.loads(raw.decode("utf-8"))
                if not isinstance(manifest, dict) or not manifest.get("version"):
                    continue
                files = manifest.get("files")
                if not isinstance(files, list) or not files:
                    continue
                manifest = dict(manifest)
                manifest["_source"] = "github"
                candidates.append(manifest)
            except Exception as exc:
                errors.append(str(exc))
        if candidates:
            newest = max(
                candidates,
                key=lambda m: (
                    _version_tuple(m.get("version")),
                    1 if str(m.get("_source") or "") == "github" else 0,
                ),
            )
            # A second pass catches the short propagation window without making
            # the user click repeatedly.
            if attempt >= 1:
                return newest
        if attempt < 2:
            time.sleep(0.8)

    if candidates:
        return max(
            candidates,
            key=lambda m: (
                _version_tuple(m.get("version")),
                1 if str(m.get("_source") or "") == "github" else 0,
            ),
        )
    raise RuntimeError(
        "GitHub에서 최신 업데이트 정보를 확인하지 못했습니다."
        + (f" ({errors[-1]})" if errors else "")
    )


def _version_tuple(value):
    out = []
    for part in str(value or "").strip().lstrip("vV").split("."):
        try:
            out.append(int(part))
        except Exception:
            out.append(0)
    return tuple(out)


def _local_version(root: Path):
    try:
        return (root / "VERSION.txt").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _safe_target(root: Path, rel: str) -> Path:
    rel = str(rel).replace("\\", "/").lstrip("/")
    if rel.startswith("data/") or rel.startswith(".venv/") or rel.startswith("sample_data/"):
        raise RuntimeError(f"보호된 사용자 데이터 경로는 업데이트할 수 없습니다: {rel}")
    target = (root / rel).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise RuntimeError(f"잘못된 업데이트 경로입니다: {rel}")
    return target


def apply_update(root: Path, manifest):
    files = [str(x) for x in manifest.get("files") or []]
    staging = Path(tempfile.mkdtemp(prefix="rg_update_", dir=str(root)))
    backup = root / "_code_backup"
    backup.mkdir(parents=True, exist_ok=True)
    try:
        downloaded = []
        source = str(manifest.get("_source") or "github")
        for rel in files:
            target = _safe_target(root, rel)
            if source == "drive":
                drive = importlib.import_module("drive_update_fallback_v09286")
                payload = drive.read_file(root, rel)
            else:
                payload = _fetch_file(rel)
            staged = staging / rel
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(payload)
            downloaded.append((rel, staged, target))

        # Back up only files that are about to be replaced.
        for rel, _staged, target in downloaded:
            if target.exists() and target.is_file():
                b = backup / rel
                b.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, b)

        for _rel, staged, target in downloaded:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            shutil.copy2(staged, tmp)
            os.replace(tmp, target)
            # Verify that the bytes on disk are exactly the bytes downloaded.
            if target.read_bytes() != staged.read_bytes():
                raise RuntimeError(f"업데이트 파일 검증 실패: {_rel}")

        # Streamlit reruns do not guarantee that already-imported Python modules
        # are reloaded from the newly written source files. Reload every updated
        # top-level .py module that is currently present in sys.modules. reload()
        # mutates the existing module object in place, so references held by the
        # running app (for example pnl_views_v0912) immediately see new functions.
        importlib.invalidate_caches()
        for rel, _staged, _target in downloaded:
            rel_path = Path(rel)
            if rel_path.suffix != ".py" or len(rel_path.parts) != 1:
                continue
            mod_name = rel_path.stem
            if mod_name in {"app", Path(__file__).stem}:
                continue
            mod = sys.modules.get(mod_name)
            if mod is None:
                continue
            try:
                importlib.reload(mod)
            except Exception:
                # Do not make the updater unusable because one optional module
                # cannot be hot-reloaded; app.py will still reload it on restart.
                pass
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def render(st, root):
    root = Path(root)
    applied = st.session_state.pop("_rg_update_applied_v09246", None)
    if applied:
        st.success(f"v{applied} 업데이트를 적용했고 새 코드로 다시 로드했습니다.")
    clicked = st.button("최신 버전 확인", use_container_width=True, key="rg_direct_update_check_v09246")
    if clicked:
        try:
            with st.spinner("GitHub에서 최신 버전을 확인하고 있습니다..."):
                manifest = fetch_manifest(root)
            st.session_state[_STATE] = manifest
        except Exception as exc:
            st.error(f"최신 버전 확인 실패: {exc}")
            st.session_state.pop(_STATE, None)

    manifest = st.session_state.get(_STATE)
    if not manifest:
        return

    current = _local_version(root)
    latest = str(manifest.get("version") or "")
    if _version_tuple(latest) <= _version_tuple(current):
        st.success(f"현재 최신 버전입니다. v{current or latest}")
        return

    st.info(f"새 버전 v{latest}이 있습니다. 현재 버전: v{current or '-'}")
    message = str(manifest.get("message") or "").strip()
    if message:
        st.caption(message)

    if st.button("업데이트 적용", type="primary", use_container_width=True, key="rg_direct_update_apply_v09246"):
        try:
            with st.spinner(f"v{latest} 업데이트를 적용하고 있습니다..."):
                apply_update(root, manifest)
            st.session_state.pop(_STATE, None)
            st.session_state["_rg_update_applied_v09246"] = latest
            # The updater replaces Python files on disk while the current
            # Streamlit process still has the old modules in memory. Force one
            # rerun so app.py starts again from the newly written files and its
            # module-cache eviction logic reloads patched modules immediately.
            st.rerun()
        except Exception as exc:
            st.error(f"업데이트 적용 실패: {exc}")


class _Transformer(ast.NodeTransformer):
    def __init__(self):
        self.replaced = 0

    def visit_If(self, node):
        self.generic_visit(node)
        test = node.test
        if not isinstance(test, ast.Call):
            return node
        func = test.func
        if not (isinstance(func, ast.Attribute) and func.attr == "button"):
            return node

        label = None
        if test.args and isinstance(test.args[0], ast.Constant) and isinstance(test.args[0].value, str):
            label = test.args[0].value
        for kw in test.keywords:
            if kw.arg == "label" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                label = kw.value.value
        if "최신 버전 확인" not in str(label or ""):
            return node

        self.replaced += 1
        return ast.Expr(
            value=ast.Call(
                func=ast.Attribute(
                    value=ast.Name(id="update_check_v09246", ctx=ast.Load()),
                    attr="render",
                    ctx=ast.Load(),
                ),
                args=[
                    ast.Name(id="st", ctx=ast.Load()),
                    ast.Name(id="ROOT", ctx=ast.Load()),
                ],
                keywords=[],
            )
        )


def patch_source(source: str) -> str:
    tree = ast.parse(source)
    tx = _Transformer()
    tree = tx.visit(tree)
    ast.fix_missing_locations(tree)
    if tx.replaced < 1:
        # Keep startup safe on unknown legacy variants; do not break the ERP.
        return source
    return ast.unparse(tree)
