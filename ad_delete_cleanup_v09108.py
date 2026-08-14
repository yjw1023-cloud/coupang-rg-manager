"""v0.9.108 keep canonical and legacy advertising deletion in sync.

When the user deletes an advertising-performance report from the monthly
provisional advertising UI, remove the mirrored legacy import with the same
file hash as well. This prevents an obsolete cross-month source from remaining
in Recent Input History after the canonical report was intentionally removed.
"""
from __future__ import annotations


def apply(ad_module):
    if ad_module is None or getattr(ad_module, "_rg_ad_delete_cleanup_v09108_applied", False):
        return ad_module

    original_delete = ad_module._delete_import

    def delete_import(core, db, import_id: int):
        ad_module._ensure_schema(core, db)
        with core._conn(db) as c:
            row = c.execute(
                """SELECT file_hash FROM provisional_ad_report_imports WHERE id=?""",
                (int(import_id),),
            ).fetchone()
            digest = str(row["file_hash"] or "") if row else ""

        original_delete(core, db, int(import_id))

        if not digest:
            return
        with core._conn(db) as c:
            legacy = c.execute(
                """SELECT id FROM imports
                   WHERE data_type='ad_performance' AND file_hash=?""",
                (digest,),
            ).fetchall()
            for r in legacy:
                legacy_id = int(r["id"])
                c.execute("DELETE FROM ad_performance WHERE import_id=?", (legacy_id,))
                c.execute(
                    "DELETE FROM imports WHERE id=? AND data_type='ad_performance'",
                    (legacy_id,),
                )

    ad_module._delete_import = delete_import
    ad_module._rg_ad_delete_cleanup_v09108_applied = True
    return ad_module
