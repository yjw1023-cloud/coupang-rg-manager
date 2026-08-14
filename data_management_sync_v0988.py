"""v0.9.103 advertising-performance date and canonical sync.

Key rules:
- Filename dates win over stale date widgets for advertising performance reports.
- New legacy/generic ad imports are mirrored into provisional_ad_report_* so
  dashboard / goal / provisional P&L use the same canonical data.
- Do NOT remirror every historical legacy import at process startup. Replaying old
  rows on every launch can overwrite a newer report for the same day and creates
  confusing 'existing overlap' warnings in the provisional P&L uploader.
"""
from __future__ import annotations

from datetime import date, datetime
import importlib
from typing import Any

_PATCHED = False


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _canonical_module():
    mod = importlib.import_module("provisional_ad_report_v0956")
    try:
        patch = importlib.import_module("ad_upload_unify_v09103")
        patch.apply(mod)
    except Exception:
        pass
    return mod


def _filename_period(file_name: str):
    try:
        mod = importlib.import_module("ad_period_v0987")
        parsed = mod._period_from_filename(file_name)
        if parsed:
            return parsed
    except Exception:
        pass
    return None


def _legacy_import_row(core, db, import_id: int):
    with core._conn(db) as c:
        row = c.execute(
            """SELECT id,file_name,file_hash,period_start,period_end,created_at
               FROM imports WHERE id=? AND data_type='ad_performance'""",
            (int(import_id),),
        ).fetchone()
    return dict(row) if row else None


def _legacy_items(core, db, import_id: int):
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT ap.option_id,
                      COALESCE(MAX(p.name),'') AS product_name,
                      COALESCE(SUM(ap.spend),0) AS ad_spend
               FROM ad_performance ap
               LEFT JOIN products p ON p.id=ap.product_id
               WHERE ap.import_id=?
               GROUP BY ap.option_id""",
            (int(import_id),),
        ).fetchall()
    out = []
    for r in rows:
        oid = str(r["option_id"] or "").strip()
        spend = float(r["ad_spend"] or 0)
        if oid and spend > 0:
            out.append({
                "option_id": oid,
                "product_name": str(r["product_name"] or ""),
                "ad_spend": spend,
            })
    return out


def _repair_legacy_period(core, db, import_id: int, start: date, end: date):
    ps, pe = start.isoformat(), end.isoformat()
    with core._conn(db) as c:
        c.execute(
            """UPDATE imports
               SET period_start=?, period_end=?
               WHERE id=? AND data_type='ad_performance'""",
            (ps, pe, int(import_id)),
        )
        c.execute(
            """UPDATE ad_performance
               SET period_start=?, period_end=?
               WHERE import_id=?""",
            (ps, pe, int(import_id)),
        )


def _mirror_legacy_to_canonical(core, db, import_id: int, start: date, end: date):
    row = _legacy_import_row(core, db, import_id)
    if not row:
        return False

    items = _legacy_items(core, db, import_id)
    if not items:
        return False

    ad = _canonical_module()
    ad._ensure_schema(core, db)

    ps, pe = start.isoformat(), end.isoformat()
    digest = str(row.get("file_hash") or "")
    file_name = str(row.get("file_name") or "")
    imported_at = str(row.get("created_at") or core.now_iso())
    total = float(sum(x["ad_spend"] for x in items))

    with core._conn(db) as c:
        same_hash = None
        if digest:
            same_hash = c.execute(
                """SELECT id FROM provisional_ad_report_imports
                   WHERE file_hash=? LIMIT 1""",
                (digest,),
            ).fetchone()

        # One exact date range = one canonical report. This path runs only when
        # the generic/legacy uploader actually imports a file, never at startup.
        exact_rows = c.execute(
            """SELECT id FROM provisional_ad_report_imports
               WHERE period_start=? AND period_end=?""",
            (ps, pe),
        ).fetchall()
        for ex in exact_rows:
            ex_id = int(ex["id"])
            if same_hash and ex_id == int(same_hash["id"]):
                continue
            c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (ex_id,))
            c.execute("DELETE FROM provisional_ad_report_imports WHERE id=?", (ex_id,))

        if same_hash:
            cid = int(same_hash["id"])
            c.execute(
                """UPDATE provisional_ad_report_imports
                   SET file_name=?, period_start=?, period_end=?,
                       total_ad_spend=?, imported_at=?
                   WHERE id=?""",
                (file_name, ps, pe, total, imported_at, cid),
            )
            c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (cid,))
        else:
            cur = c.execute(
                """INSERT INTO provisional_ad_report_imports
                   (file_name,file_hash,period_start,period_end,total_ad_spend,imported_at)
                   VALUES(?,?,?,?,?,?)""",
                (file_name, digest, ps, pe, total, imported_at),
            )
            cid = int(cur.lastrowid)

        c.executemany(
            """INSERT INTO provisional_ad_report_items
               (import_id,option_id,product_name,ad_spend)
               VALUES(?,?,?,?)""",
            [
                (cid, x["option_id"], x["product_name"], float(x["ad_spend"]))
                for x in items
            ],
        )
    return True


def _repair_one(core, db, import_id: int):
    row = _legacy_import_row(core, db, import_id)
    if not row:
        return False

    parsed = _filename_period(str(row.get("file_name") or ""))
    if parsed:
        start, end = parsed
    else:
        start = _as_date(row.get("period_start"))
        end = _as_date(row.get("period_end"))
        if start is None or end is None:
            return False

    if end < start:
        start, end = end, start

    current_start = _as_date(row.get("period_start"))
    current_end = _as_date(row.get("period_end"))
    if current_start != start or current_end != end:
        _repair_legacy_period(core, db, import_id, start, end)

    _mirror_legacy_to_canonical(core, db, import_id, start, end)
    return True


def _repair_existing(core, db):
    """Legacy manual repair helper retained for diagnostics; no startup call."""
    try:
        core.init_db(db)
        with core._conn(db) as c:
            rows = c.execute(
                """SELECT id FROM imports
                   WHERE data_type='ad_performance'
                   ORDER BY id"""
            ).fetchall()
        for r in rows:
            try:
                _repair_one(core, db, int(r["id"]))
            except Exception as exc:
                print(f"RG Manager ad repair skipped import {r['id']}: {exc}")
    except Exception as exc:
        print(f"RG Manager ad repair failed: {exc}")


def apply(core) -> None:
    global _PATCHED
    if _PATCHED or getattr(core, "_rg_ad_import_unified_v0990", False):
        _PATCHED = True
        return

    original_import = core.import_ad_performance

    # v0.9.103: intentionally no _repair_existing() here. Historical imports
    # must not be replayed on every ERP startup. Only a real new import below
    # is mirrored to the canonical provisional-ad tables.

    def import_wrapper(source, file_name: str, period_start=None, period_end=None, db_path=None):
        db = db_path or core.DEFAULT_DB

        parsed = _filename_period(file_name)
        if parsed:
            start, end = parsed
            if end < start:
                start, end = end, start
            period_start = start.isoformat()
            period_end = end.isoformat()
        else:
            start = _as_date(period_start)
            end = _as_date(period_end)

        result = original_import(
            source,
            file_name,
            period_start=period_start,
            period_end=period_end,
            db_path=db,
        )

        import_id = int((result or {}).get("import_id") or 0)
        if import_id:
            try:
                if start is not None and end is not None:
                    if end < start:
                        start, end = end, start
                    _repair_legacy_period(core, db, import_id, start, end)
                    _mirror_legacy_to_canonical(core, db, import_id, start, end)
                else:
                    _repair_one(core, db, import_id)
            except Exception as exc:
                try:
                    result = dict(result or {})
                    result["provisional_sync_warning"] = str(exc)
                except Exception:
                    pass
        return result

    core.import_ad_performance = import_wrapper
    core._rg_ad_import_unified_v0990 = True
    _PATCHED = True
