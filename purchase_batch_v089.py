"""RG Manager v0.8.9 purchase batch labels.

Adds a user-entered purchase batch label (e.g. 16차) to Excel purchase imports
and restores the legacy Claude ERP batch labels that were stored in purchases.note
but omitted by the v0.7 migration.
"""
from __future__ import annotations

import re
from typing import Any

_BATCH_KEY = "_rg_purchase_batch_v089"
_APPLIED = False

# Audited against the user's original Claude ERP erp.db on 2026-08-07.
# Only rows whose legacy purchases.note contained a batch label are included.
_LEGACY_BATCH_IDS = {
    "9차": (454, 455, 456, 457, 458, 459, 460, 461, 462, 463, 464, 465, 468, 469, 470, 471),
    "10차": tuple(range(503, 517)),
    "11차": tuple(range(525, 532)),
    "12차": (538, 539, 540),
    "13차": tuple(range(542, 559)),
    "14차": (559, 560),
    "15차": tuple(range(561, 573)),
}


def normalize_batch(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    m = re.fullmatch(r"(\d+)\s*(?:차)?\s*(?:수입)?", text)
    if m:
        return f"{int(m.group(1))}차"
    return text


def ensure_schema(db_path, core_module) -> dict[str, int]:
    core_module.init_db(db_path)
    restored = 0
    with core_module._conn(db_path) as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(purchase_lines)").fetchall()}
        if "purchase_batch" not in cols:
            c.execute("ALTER TABLE purchase_lines ADD COLUMN purchase_batch TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS ix_purchase_lines_batch ON purchase_lines(purchase_batch)")

        link_exists = c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='legacy_v07_purchase_links'"
        ).fetchone()
        if link_exists:
            for label, legacy_ids in _LEGACY_BATCH_IDS.items():
                placeholders = ",".join("?" for _ in legacy_ids)
                cur = c.execute(
                    f"""UPDATE purchase_lines
                        SET purchase_batch=?
                        WHERE (purchase_batch IS NULL OR TRIM(purchase_batch)='')
                          AND id IN (
                              SELECT purchase_line_id FROM legacy_v07_purchase_links
                              WHERE legacy_id IN ({placeholders})
                          )""",
                    (label, *legacy_ids),
                )
                restored += max(int(cur.rowcount or 0), 0)
    return {"restored": restored}


def _max_purchase_line_id(db_path, core_module) -> int:
    with core_module._conn(db_path) as c:
        row = c.execute("SELECT COALESCE(MAX(id),0) AS m FROM purchase_lines").fetchone()
        return int(row["m"] or 0)


def _apply_batch_to_new_rows(db_path, core_module, after_id: int, batch: str) -> int:
    batch = normalize_batch(batch)
    if not batch:
        return 0
    with core_module._conn(db_path) as c:
        cur = c.execute(
            """UPDATE purchase_lines SET purchase_batch=?
               WHERE id>? AND (purchase_batch IS NULL OR TRIM(purchase_batch)='')""",
            (batch, int(after_id)),
        )
        changed = max(int(cur.rowcount or 0), 0)

        import_ids = [
            int(r["import_id"])
            for r in c.execute(
                "SELECT DISTINCT import_id FROM purchase_lines WHERE id>?", (int(after_id),)
            ).fetchall()
        ]
        for import_id in import_ids:
            row = c.execute("SELECT notes FROM imports WHERE id=?", (import_id,)).fetchone()
            if not row:
                continue
            old = str(row["notes"] or "").strip()
            tag = f"매입차수={batch}"
            if tag not in old:
                new = f"{old} | {tag}" if old else tag
                c.execute("UPDATE imports SET notes=? WHERE id=?", (new, import_id))
        return changed


def _recent_batches(db_path, core_module):
    with core_module._conn(db_path) as c:
        return c.execute(
            """SELECT purchase_batch, MIN(purchase_date) first_date, MAX(purchase_date) last_date,
                      COUNT(*) lines, SUM(qty_receipt) qty, SUM(landed_total_krw) amount
               FROM purchase_lines
               WHERE purchase_batch IS NOT NULL AND TRIM(purchase_batch)<>''
               GROUP BY purchase_batch
               ORDER BY MAX(purchase_date) DESC, MAX(id) DESC
               LIMIT 12"""
        ).fetchall()


def apply(purchase_module, core_module):
    global _APPLIED
    if purchase_module is None:
        return purchase_module
    if getattr(purchase_module, "_rg_purchase_batch_v089_applied", False):
        return purchase_module

    ensure_schema(core_module.DEFAULT_DB, core_module)
    original_render = getattr(purchase_module, "render_purchase_page", None)
    if not callable(original_render):
        purchase_module._rg_purchase_batch_v089_applied = True
        return purchase_module

    def render_purchase_page(*args, **kwargs):
        st_obj = kwargs.get("st")
        if st_obj is None:
            for obj in args:
                if hasattr(obj, "file_uploader") and hasattr(obj, "text_input"):
                    st_obj = obj
                    break
        db_path = core_module.DEFAULT_DB
        ensure_schema(db_path, core_module)
        before_id = _max_purchase_line_id(db_path, core_module)

        original_file_uploader = getattr(st_obj, "file_uploader", None) if st_obj is not None else None
        injected = False
        if callable(original_file_uploader):
            def file_uploader_wrapper(*u_args, **u_kwargs):
                nonlocal injected
                if not injected:
                    injected = True
                    st_obj.text_input(
                        "매입 차수",
                        key=_BATCH_KEY,
                        placeholder="예: 16차",
                        help="이 Excel에서 확정되는 모든 매입상품에 같은 차수가 저장됩니다. 숫자만 입력해도 자동으로 '차'가 붙습니다.",
                    )
                    try:
                        rows = _recent_batches(db_path, core_module)
                        if rows:
                            latest = str(rows[0]["purchase_batch"] or "")
                            st_obj.caption(f"기존 ERP 차수 9~15차 복구 완료 · 최근 등록 차수: {latest}")
                    except Exception:
                        pass
                return original_file_uploader(*u_args, **u_kwargs)
            st_obj.file_uploader = file_uploader_wrapper

        try:
            result = original_render(*args, **kwargs)
        finally:
            if st_obj is not None and callable(original_file_uploader):
                st_obj.file_uploader = original_file_uploader

        batch = normalize_batch(st_obj.session_state.get(_BATCH_KEY, "")) if st_obj is not None else ""
        changed = _apply_batch_to_new_rows(db_path, core_module, before_id, batch)
        if changed and st_obj is not None:
            st_obj.success(f"매입 차수 {batch}로 {changed}개 매입행을 저장했습니다.")
        return result

    purchase_module.render_purchase_page = render_purchase_page
    purchase_module.ensure_purchase_batch_schema = lambda db_path=core_module.DEFAULT_DB: ensure_schema(db_path, core_module)
    purchase_module.normalize_purchase_batch = normalize_batch
    purchase_module._rg_purchase_batch_v089_applied = True
    _APPLIED = True
    return purchase_module
