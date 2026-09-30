"""RG Manager v0.9.248 database safety backup helpers.

Every write path that can materially change ERP data should call backup_db()
immediately before the write transaction. Backups are SQLite-consistent snapshots
and are intentionally retained without automatic deletion.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
import sqlite3


def _safe_reason(reason: str) -> str:
    text = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", str(reason or "write")).strip("_")
    return text[:60] or "write"


def backup_dir(db_path) -> Path:
    db = Path(db_path).expanduser().resolve()
    folder = db.parent / "_db_backups"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def backup_db(core, reason: str, db_path=None):
    """Create a consistent full SQLite backup and return its path.

    No retention cleanup is performed: every backup is kept unless the user
    explicitly deletes it later.
    """
    db = Path(db_path or core.DEFAULT_DB).expanduser().resolve()
    if not db.exists():
        # Ensure the ERP DB exists before taking the first snapshot.
        core.init_db(str(db))
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = backup_dir(db) / f"{db.stem}_{stamp}_{_safe_reason(reason)}{db.suffix or '.db'}"

    src = sqlite3.connect(str(db), timeout=30)
    dst = sqlite3.connect(str(target), timeout=30)
    try:
        src.backup(dst)
        dst.commit()
    finally:
        try:
            dst.close()
        finally:
            src.close()

    if not target.exists() or target.stat().st_size <= 0:
        raise RuntimeError("ERP DB 백업 생성에 실패했습니다. 데이터 변경을 중단합니다.")
    return str(target)
