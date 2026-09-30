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
import shutil
import tempfile
import time
import urllib.request
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


def fetch_manifest():
    raw = _fetch_file("update/latest.json")
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict) or not manifest.get("version"):
        raise RuntimeError("업데이트 정보 형식이 올바르지 않습니다.")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise RuntimeError("업데이트 파일 목록이 없습니다.")
    return manifest


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
        for rel in files:
            target = _safe_target(root, rel)
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
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def render(st, root):
    root = Path(root)
    clicked = st.button("최신 버전 확인", use_container_width=True, key="rg_direct_update_check_v09246")
    if clicked:
        try:
            with st.spinner("GitHub에서 최신 버전을 직접 확인하고 있습니다..."):
                manifest = fetch_manifest()
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
            st.success(f"v{latest} 업데이트를 적용했습니다. 프로그램을 다시 실행해 주세요.")
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
