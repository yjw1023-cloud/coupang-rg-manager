"""v0.9.103 advertising upload de-duplication and exact-period replacement.

Rules:
- The same raw file may arrive through more than one internal upload/sync path.
  Treat the same SHA-256 as one record and refresh it in place instead of raising
  a duplicate-upload error.
- A newly selected report for the exact same date range automatically replaces
  the prior exact-range record. The user should not have to resolve an internal
  synchronization duplicate.
- Only partial/unequal date-range overlaps still require the existing explicit
  replacement checkbox, because those could double-count days.
"""
from __future__ import annotations

import hashlib


def apply(ad_module):
    if ad_module is None or getattr(ad_module, "_rg_ad_upload_unify_v09103_applied", False):
        return ad_module

    original_overlaps = ad_module._overlaps

    def ui_overlaps(core, db, start, end):
        rows = original_overlaps(core, db, start, end)
        ps, pe = start.isoformat(), end.isoformat()
        # Exact-range rows are safe: _save below replaces/refreshes them as one
        # logical report. Keep only partial overlaps visible to the user.
        return [
            r for r in rows
            if not (str(r.get("period_start") or "") == ps and str(r.get("period_end") or "") == pe)
        ]

    def save(core, db, file_name, raw, start, end, grouped, replace_overlap=False):
        ad_module._ensure_schema(core, db)
        if start > end:
            raise ValueError("광고자료 시작일은 종료일보다 늦을 수 없습니다.")
        if start.strftime("%Y-%m") != end.strftime("%Y-%m"):
            raise ValueError("광고성과보고서는 한 달 안의 기간으로 나눠 업로드해 주세요.")

        digest = hashlib.sha256(raw).hexdigest()
        ps, pe = start.isoformat(), end.isoformat()
        all_overlaps = original_overlaps(core, db, start, end)
        total = float(grouped["ad_spend"].sum())

        with core._conn(db) as c:
            same_hash = c.execute(
                """SELECT id,file_name,period_start,period_end
                   FROM provisional_ad_report_imports
                   WHERE file_hash=? LIMIT 1""",
                (digest,),
            ).fetchone()

            same_hash_id = int(same_hash["id"]) if same_hash else None
            exact = [
                r for r in all_overlaps
                if str(r.get("period_start") or "") == ps
                and str(r.get("period_end") or "") == pe
                and int(r.get("id") or 0) != (same_hash_id or -1)
            ]
            partial = [
                r for r in all_overlaps
                if int(r.get("id") or 0) != (same_hash_id or -1)
                and r not in exact
            ]

            if partial and not replace_overlap:
                raise ValueError(
                    "기존 광고자료와 일부 기간이 겹칩니다. "
                    "'겹치는 기존 광고자료 교체'를 체크해 주세요."
                )

            # Exact same period is one logical daily/range report. Always replace
            # older exact-range rows, regardless of which internal path created them.
            delete_rows = list(exact)
            if replace_overlap:
                delete_rows.extend(partial)
            for r in delete_rows:
                rid = int(r["id"])
                c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (rid,))
                c.execute("DELETE FROM provisional_ad_report_imports WHERE id=?", (rid,))

            if same_hash_id is not None:
                # Idempotent save: same file already mirrored by another internal
                # route. Refresh that one row and its items; never create a duplicate.
                c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (same_hash_id,))
                c.execute(
                    """UPDATE provisional_ad_report_imports
                       SET file_name=?,period_start=?,period_end=?,total_ad_spend=?,imported_at=?
                       WHERE id=?""",
                    (str(file_name), ps, pe, total, core.now_iso(), same_hash_id),
                )
                import_id = same_hash_id
                deduped = True
            else:
                cur = c.execute(
                    """INSERT INTO provisional_ad_report_imports
                       (file_name,file_hash,period_start,period_end,total_ad_spend,imported_at)
                       VALUES(?,?,?,?,?,?)""",
                    (str(file_name), digest, ps, pe, total, core.now_iso()),
                )
                import_id = int(cur.lastrowid)
                deduped = False

            c.executemany(
                """INSERT INTO provisional_ad_report_items(import_id,option_id,product_name,ad_spend)
                   VALUES(?,?,?,?)""",
                [
                    (import_id, str(r.option_id), str(r.product_name or ""), float(r.ad_spend))
                    for r in grouped.itertuples(index=False)
                ],
            )

        return {
            "import_id": import_id,
            "total": total,
            "options": len(grouped),
            "deduped": deduped,
            "replaced_exact": len(exact),
        }

    ad_module._overlaps = ui_overlaps
    ad_module._save = save
    ad_module._rg_ad_upload_unify_v09103_applied = True
    return ad_module
