"""Google Drive fallback source for RG Manager updates."""
from __future__ import annotations
import json
import shutil
from pathlib import Path


def update_root(app_root):
    app_root = Path(app_root)
    cfg = app_root / "data" / "ai_drive_relay_config.json"
    if not cfg.exists():
        return None
    try:
        obj = json.loads(cfg.read_text(encoding="utf-8"))
        base = Path(str(obj.get("base_path") or "")).expanduser()
    except Exception:
        return None
    p = base / "update"
    return p if p.exists() and p.is_dir() else None


def manifest(app_root):
    root = update_root(app_root)
    if root is None:
        return None
    p = root / "latest.json"
    if not p.exists():
        return None
    try:
        m = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(m, dict) or not m.get("version") or not isinstance(m.get("files"), list):
        return None
    m = dict(m)
    m["_source"] = "drive"
    return m


def read_file(app_root, rel):
    root = update_root(app_root)
    if root is None:
        raise RuntimeError("Google Drive 업데이트 폴더를 찾지 못했습니다.")
    rel = str(rel).replace("\\", "/").lstrip("/")
    src = (root / rel).resolve()
    base = root.resolve()
    if src != base and base not in src.parents:
        raise RuntimeError("잘못된 Drive 업데이트 경로입니다.")
    if not src.exists() or not src.is_file():
        raise RuntimeError("Drive 업데이트 파일이 없습니다: " + rel)
    return src.read_bytes()
