"""RG Manager v0.9.109 advertising source audit + targeted orphan cleanup.

Why this exists
---------------
Older builds stored advertising performance through two paths:
1) generic '새 자료 반영' -> imports/ad_performance + canonical provisional tables
2) provisional P&L ad uploader -> canonical provisional tables only

Therefore an old canonical row can exist without appearing in '최근 입력 이력'.
For the user-reported 2026-08-01~2026-08-11 report, remove it only when no
matching generic input-history row exists.  Never remove any other ad report.

Going forward this patch also records a lightweight source_origin on canonical
ad imports so the same ambiguity does not recur.
"""
from __future__ import annotations

TARGET_FILE = "A00577001_pa_total_campaign_20260801_20260811.xlsx"
TARGET_START = "2026-08-01"
TARGET_END = "2026-08-11"
_APPLIED = False


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _ensure_source_column(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        if not _exists(c, "provisional_ad_report_imports"):
            return
        cols = _cols(c, "provisional_ad_report_imports")
        if "source_origin" not in cols:
            c.execute("ALTER TABLE provisional_ad_report_imports ADD COLUMN source_origin TEXT")


def _legacy_match(c, row) -> bool:
    if not _exists(c, "imports"):
        return False
    cols = _cols(c, "imports")
    if not {"data_type", "file_name"}.issubset(cols):
        return False

    digest = str(row["file_hash"] or "") if "file_hash" in row.keys() else ""
    if digest and "file_hash" in cols:
        hit = c.execute(
            """SELECT 1 FROM imports
               WHERE data_type='ad_performance' AND file_hash=? LIMIT 1""",
            (digest,),
        ).fetchone()
        if hit:
            return True

    params = [str(row["file_name"] or "")]
    where = ["data_type='ad_performance'", "file_name=?"]
    if "period_start" in cols:
        where.append("COALESCE(period_start,'')=?")
        params.append(str(row["period_start"] or ""))
    if "period_end" in cols:
        where.append("COALESCE(period_end,'')=?")
        params.append(str(row["period_end"] or ""))
    hit = c.execute(
        "SELECT 1 FROM imports WHERE " + " AND ".join(where) + " LIMIT 1",
        tuple(params),
    ).fetchone()
    return bool(hit)


def cleanup_target(core, db=None):
    db = db or core.DEFAULT_DB
    _ensure_source_column(core, db)
    result = {
        "target_found": False,
        "legacy_history_found": False,
        "removed": False,
        "reason": "target_not_found",
    }
    with core._conn(db) as c:
        if not _exists(c, "provisional_ad_report_imports"):
            result["reason"] = "canonical_table_missing"
            return result
        rows = c.execute(
            """SELECT * FROM provisional_ad_report_imports
               WHERE file_name=? AND period_start=? AND period_end=?
               ORDER BY id""",
            (TARGET_FILE, TARGET_START, TARGET_END),
        ).fetchall()
        if not rows:
            return result

        result["target_found"] = True
        protected = []
        orphan = []
        for r in rows:
            if _legacy_match(c, r):
                protected.append(r)
            else:
                orphan.append(r)

        if protected:
            result["legacy_history_found"] = True
            for r in protected:
                c.execute(
                    "UPDATE provisional_ad_report_imports SET source_origin=? WHERE id=?",
                    ("data_management_upload", int(r["id"])),
                )

        # Targeted user-authorized repair: remove only this exact untracked report.
        for r in orphan:
            rid = int(r["id"])
            if _exists(c, "provisional_ad_report_items"):
                c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (rid,))
            c.execute("DELETE FROM provisional_ad_report_imports WHERE id=?", (rid,))

        result["removed"] = bool(orphan)
        if orphan and protected:
            result["reason"] = "removed_untracked_duplicate_kept_tracked"
        elif orphan:
            result["reason"] = "removed_untracked_target"
        else:
            result["reason"] = "kept_because_input_history_exists"
    return result


def _mark_direct_save(ad_module, core):
    if ad_module is None or getattr(ad_module, "_rg_ad_source_v09109_applied", False):
        return
    original_save = getattr(ad_module, "_save", None)
    if not callable(original_save):
        return

    def save(core_obj, db, file_name, raw, start, end, grouped, replace_overlap=False):
        result = original_save(
            core_obj, db, file_name, raw, start, end, grouped,
            replace_overlap=replace_overlap,
        )
        try:
            _ensure_source_column(core_obj, db)
            rid = int((result or {}).get("import_id") or 0)
            if rid:
                with core_obj._conn(db) as c:
                    c.execute(
                        """UPDATE provisional_ad_report_imports
                           SET source_origin=COALESCE(NULLIF(source_origin,''),?)
                           WHERE id=?""",
                        ("provisional_direct_upload", rid),
                    )
        except Exception:
            pass
        return result

    ad_module._save = save
    ad_module._rg_ad_source_v09109_applied = True


def _mark_generic_import(core):
    if getattr(core, "_rg_ad_generic_source_v09109_applied", False):
        return
    original = getattr(core, "import_ad_performance", None)
    if not callable(original):
        return

    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        try:
            db = kwargs.get("db_path") or core.DEFAULT_DB
            import_id = int((result or {}).get("import_id") or 0)
            if import_id:
                _ensure_source_column(core, db)
                with core._conn(db) as c:
                    if _exists(c, "imports"):
                        row = c.execute(
                            "SELECT * FROM imports WHERE id=?", (import_id,)
                        ).fetchone()
                    else:
                        row = None
                    if row:
                        digest = str(row["file_hash"] or "") if "file_hash" in row.keys() else ""
                        if digest:
                            c.execute(
                                """UPDATE provisional_ad_report_imports
                                   SET source_origin='data_management_upload'
                                   WHERE file_hash=?""",
                                (digest,),
                            )
        except Exception:
            pass
        return result

    core.import_ad_performance = wrapped
    core._rg_ad_generic_source_v09109_applied = True


def apply(core, ad_module=None):
    global _APPLIED
    if _APPLIED or getattr(core, "_rg_ad_orphan_cleanup_v09109_applied", False):
        return getattr(core, "AD_ORPHAN_CLEANUP_V09109_RESULT", None)
    result = cleanup_target(core, core.DEFAULT_DB)
    core.AD_ORPHAN_CLEANUP_V09109_RESULT = result
    _mark_direct_save(ad_module, core)
    _mark_generic_import(core)
    core._rg_ad_orphan_cleanup_v09109_applied = True
    _APPLIED = True
    return result
