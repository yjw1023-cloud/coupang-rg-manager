"""RG Manager v0.9.29 provisional P&L snapshot binding fix.

The old snapshot code tried to infer the sales import by comparing the final
rendered product/quantity signature with the raw sales-stat import. That breaks
when later P&L presentation rules hide/merge rows (return-discount aliases,
zero-quantity rows, etc.).

This patch remembers the exact sales_import_id when core.estimated_pnl() is
called, then saves the final displayed provisional dataframe directly to that
import id. The original signature matcher remains as a fallback.
"""
from __future__ import annotations

from datetime import datetime
import json

import streamlit as st

_MARKER = "_rg_pnl_snapshot_fix_v0929"
_STATE_IMPORT = "_rg_provisional_sales_import_id_v0929"
_STATE_AD_IMPORT = "_rg_provisional_ad_import_id_v0929"


def _remember_import(core_module):
    if getattr(core_module, "_rg_estimated_pnl_import_binding_v0929", False):
        return
    original = core_module.estimated_pnl

    def estimated_pnl(sales_import_id, ad_import_id=None, *args, **kwargs):
        result = original(sales_import_id, ad_import_id, *args, **kwargs)
        try:
            st.session_state[_STATE_IMPORT] = int(sales_import_id)
            st.session_state[_STATE_AD_IMPORT] = int(ad_import_id) if ad_import_id is not None else None
        except Exception:
            pass
        return result

    core_module.estimated_pnl = estimated_pnl
    core_module._rg_estimated_pnl_import_binding_v0929 = True


def _save_for_import(views_module, core_module, db, df, import_id: int) -> bool:
    cleaned = views_module._clean_provisional(df)
    if cleaned is None or cleaned.empty:
        return False

    with core_module._conn(db) as c:
        imp = c.execute(
            """SELECT id,file_name,period_start,period_end
               FROM imports
               WHERE id=? AND data_type='sales_stats'""",
            (int(import_id),),
        ).fetchone()
        if not imp:
            return False

    rows = views_module._snapshot_rows(cleaned)
    totals = views_module._provisional_totals(rows)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with core_module._conn(db) as c:
        c.execute(
            """INSERT INTO provisional_pnl_snapshots
               (import_id,file_name,period_start,period_end,captured_at,rows_json,totals_json)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(import_id) DO UPDATE SET
                 file_name=excluded.file_name,
                 period_start=excluded.period_start,
                 period_end=excluded.period_end,
                 captured_at=excluded.captured_at,
                 rows_json=excluded.rows_json,
                 totals_json=excluded.totals_json""",
            (
                int(imp["id"]),
                str(imp["file_name"] or ""),
                str(imp["period_start"] or ""),
                str(imp["period_end"] or ""),
                now,
                json.dumps(rows, ensure_ascii=False),
                json.dumps(totals, ensure_ascii=False),
            ),
        )
    return True


def _bind_snapshot_save(core_module, views_module, db_path=None):
    if getattr(views_module, _MARKER, False):
        return
    original = views_module._save_snapshot

    def save_snapshot(core, db, df):
        import_id = None
        try:
            import_id = st.session_state.get(_STATE_IMPORT)
        except Exception:
            import_id = None

        if import_id is not None:
            try:
                if _save_for_import(views_module, core, db, df, int(import_id)):
                    return True
            except Exception:
                # Fall through to the legacy signature matcher below.
                pass

        return original(core, db, df)

    views_module._save_snapshot = save_snapshot
    setattr(views_module, _MARKER, True)


def apply(core_module, views_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    views_module._ensure_schema(core_module, db)
    _remember_import(core_module)
    _bind_snapshot_save(core_module, views_module, db)
    return True
