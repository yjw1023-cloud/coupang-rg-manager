"""v0.9.112 unify advertising uploads in Recent Input History.

The ERP historically stores advertising-performance reports through two paths:
- generic '새 자료 반영' -> imports/ad_performance (+ canonical mirror)
- provisional P&L ad upload -> provisional_ad_report_* only

The Recent Input History table is built from the generic imports table, so direct
provisional-P&L uploads such as the user's 2026-08-13 report can be correctly
applied to P&L while remaining invisible in that history table.

This patch keeps the existing table but augments it at render time with canonical
advertising reports that are not already represented by filename in the table.
It also removes the exact obsolete cross-month 2026-07-16~2026-08-02 generic ad
history that the user previously authorized deleting.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd

_EXPECTED_COLUMNS = ("자료", "대상기간", "주요내용", "파일명", "입력일시", "상태")
_STALE_FILE = "A00577001_pa_total_campaign_20260716_20260802.xlsx"
_STALE_START = "2026-07-16"
_STALE_END = "2026-08-02"


def _table_exists(c, name: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _cols(c, name: str) -> set[str]:
    if not _table_exists(c, name):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{name}")').fetchall()}


def _cleanup_stale_cross_month(core, db):
    """Delete only the exact obsolete 7/16~8/2 ad source previously authorized."""
    deleted = {"canonical": 0, "legacy": 0, "ad_rows": 0}
    with core._conn(db) as c:
        if _table_exists(c, "provisional_ad_report_imports"):
            rows = c.execute(
                """SELECT id FROM provisional_ad_report_imports
                   WHERE file_name=? AND period_start=? AND period_end=?""",
                (_STALE_FILE, _STALE_START, _STALE_END),
            ).fetchall()
            for r in rows:
                rid = int(r["id"])
                if _table_exists(c, "provisional_ad_report_items"):
                    c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (rid,))
                c.execute("DELETE FROM provisional_ad_report_imports WHERE id=?", (rid,))
                deleted["canonical"] += 1

        if _table_exists(c, "imports"):
            cols = _cols(c, "imports")
            where = ["data_type='ad_performance'", "file_name=?"]
            params: list[Any] = [_STALE_FILE]
            if "period_start" in cols:
                where.append("COALESCE(period_start,'')=?")
                params.append(_STALE_START)
            if "period_end" in cols:
                where.append("COALESCE(period_end,'')=?")
                params.append(_STALE_END)
            rows = c.execute(
                "SELECT id FROM imports WHERE " + " AND ".join(where), tuple(params)
            ).fetchall()
            for r in rows:
                iid = int(r["id"])
                if _table_exists(c, "ad_performance"):
                    cur = c.execute("DELETE FROM ad_performance WHERE import_id=?", (iid,))
                    try:
                        deleted["ad_rows"] += max(0, int(cur.rowcount or 0))
                    except Exception:
                        pass
                c.execute("DELETE FROM imports WHERE id=? AND data_type='ad_performance'", (iid,))
                deleted["legacy"] += 1
    return deleted


def _period_text(start: Any, end: Any) -> str:
    s = str(start or "").strip()[:10]
    e = str(end or "").strip()[:10]
    sd = s.replace("-", ".") if s else "기간 정보 없음"
    ed = e.replace("-", ".") if e else sd
    return sd if sd == ed else f"{sd} ~ {ed}"


def _display_time(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.strftime("%m/%d %H:%M")
    except Exception:
        return text


def _sort_time(value: Any):
    text = str(value or "").strip()
    if not text:
        return pd.Timestamp.min
    try:
        if len(text) >= 16 and text[4] == "-":
            return pd.Timestamp(text)
    except Exception:
        pass
    try:
        year = datetime.now().year
        return pd.Timestamp(datetime.strptime(f"{year}/{text}", "%Y/%m/%d %H:%M"))
    except Exception:
        try:
            return pd.Timestamp(text)
        except Exception:
            return pd.Timestamp.min


def _canonical_missing_rows(core, db, existing: pd.DataFrame) -> list[dict]:
    with core._conn(db) as c:
        if not _table_exists(c, "provisional_ad_report_imports"):
            return []
        has_items = _table_exists(c, "provisional_ad_report_items")
        if has_items:
            rows = c.execute(
                """SELECT i.id,i.file_name,i.period_start,i.period_end,
                          i.total_ad_spend,i.imported_at,
                          COUNT(x.option_id) AS option_rows
                   FROM provisional_ad_report_imports i
                   LEFT JOIN provisional_ad_report_items x ON x.import_id=i.id
                   GROUP BY i.id,i.file_name,i.period_start,i.period_end,
                            i.total_ad_spend,i.imported_at
                   ORDER BY i.imported_at DESC,i.id DESC"""
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT id,file_name,period_start,period_end,total_ad_spend,
                          imported_at,0 AS option_rows
                   FROM provisional_ad_report_imports
                   ORDER BY imported_at DESC,id DESC"""
            ).fetchall()

    existing_names = set()
    if "파일명" in existing.columns:
        existing_names = {
            str(x or "").strip() for x in existing["파일명"].tolist() if str(x or "").strip()
        }

    out = []
    for r in rows:
        file_name = str(r["file_name"] or "").strip()
        if not file_name or file_name in existing_names:
            continue
        total = int(round(float(r["total_ad_spend"] or 0)))
        count = int(r["option_rows"] or 0)
        out.append({
            "자료": "광고 성과보고서",
            "대상기간": _period_text(r["period_start"], r["period_end"]),
            "주요내용": f"{total:,}원 · {count:,}행",
            "파일명": file_name,
            "입력일시": _display_time(r["imported_at"]),
            "상태": "정상",
        })
    return out


def _looks_like_recent_history(df: Any) -> bool:
    if not isinstance(df, pd.DataFrame):
        return False
    cols = tuple(str(c) for c in df.columns)
    return all(c in cols for c in _EXPECTED_COLUMNS)


def _merge_recent_history(core, db, df: pd.DataFrame) -> pd.DataFrame:
    extra = _canonical_missing_rows(core, db, df)
    if not extra:
        return df
    out = pd.concat([df.copy(), pd.DataFrame(extra, columns=list(df.columns))], ignore_index=True)
    if "입력일시" in out.columns:
        out["__rg_sort"] = out["입력일시"].map(_sort_time)
        out = out.sort_values("__rg_sort", ascending=False, kind="stable").drop(columns=["__rg_sort"])
        out = out.reset_index(drop=True)
    return out


def apply(core):
    db = core.DEFAULT_DB
    core.init_db(db)
    cleanup = _cleanup_stale_cross_month(core, db)

    import streamlit as st
    if getattr(st, "_rg_recent_input_unify_v09112_applied", False):
        return {"cleanup": cleanup, "patched": True}

    original_dataframe = st.dataframe

    def dataframe(data=None, *args, **kwargs):
        try:
            if _looks_like_recent_history(data):
                data = _merge_recent_history(core, db, data)
        except Exception as exc:
            print(f"RG Manager v0.9.112 recent-input merge skipped: {exc}")
        return original_dataframe(data, *args, **kwargs)

    st.dataframe = dataframe
    st._rg_recent_input_unify_v09112_applied = True
    return {"cleanup": cleanup, "patched": True}
