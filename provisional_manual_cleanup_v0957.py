"""One-time cleanup for retired provisional manual advertising spend.

v0.9.56 replaced manual advertising totals with Coupang advertising-performance
report uploads. This helper removes any legacy rows that may remain in the local
SQLite database. No user-facing delete UI is provided.
"""
from __future__ import annotations


def run_once(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    with core._conn(db) as c:
        exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='provisional_manual_ad_spend'"
        ).fetchone()
        if not exists:
            return 0
        before = c.execute("SELECT COUNT(*) AS n FROM provisional_manual_ad_spend").fetchone()
        count = int(before["n"] if before else 0)
        c.execute("DELETE FROM provisional_manual_ad_spend")
    return count
