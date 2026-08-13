"""v0.9.89 unify generic advertising import with provisional P&L storage.

The generic Coupang data-management uploader remains the owner of the UI and
legacy `imports/ad_performance` history. After that normal import succeeds,
the same file is mirrored into `provisional_ad_report_*`, which is the source
used by dashboard/goal/provisional P&L. No Streamlit button is intercepted.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import hashlib
import importlib
from typing import Any

import pandas as pd

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
    return importlib.import_module("provisional_ad_report_v0956")


def _same_period(row, start: date, end: date) -> bool:
    return _as_date(row.get("period_start")) == start and _as_date(row.get("period_end")) == end


def _already_saved(core, db, raw: bytes) -> bool:
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with core._conn(db) as c:
            row = c.execute(
                "SELECT 1 FROM provisional_ad_report_imports WHERE file_hash=? LIMIT 1",
                (digest,),
            ).fetchone()
        return row is not None
    except Exception:
        return False


def _sync_one(core, source, file_name: str, start: date, end: date, db):
    raw = core.as_bytes(source)
    if not raw:
        return
    ad = _canonical_module()
    ad._ensure_schema(core, db)
    if _already_saved(core, db, raw):
        return

    grouped, _total = ad._parse_excel(raw)
    overlaps = ad._overlaps(core, db, start, end)
    replace = False
    if overlaps:
        # Mirror the legacy importer's exact-scope replacement rule. A wider
        # overlapping file must not erase days outside the newly uploaded range.
        if all(_same_period(r, start, end) for r in overlaps):
            replace = True
        else:
            fully_covered = all(
                (_as_date(r.get("period_start")) or start) <= start
                and (_as_date(r.get("period_end")) or end) >= end
                for r in overlaps
            )
            if fully_covered:
                return
            raise ValueError(
                "기존 광고자료와 기간이 일부 겹칩니다. 잠정손익의 광고성과보고서에서 겹치는 기간을 정리한 뒤 다시 올려 주세요."
            )

    ad._save(
        core,
        db,
        file_name,
        raw,
        start,
        end,
        grouped,
        replace_overlap=replace,
    )


def _canonical_month_summary(core, db):
    today = date.today()
    month = today.strftime("%Y-%m")
    month_start = today.replace(day=1)
    try:
        dataset = _canonical_module().load_month(core, month, db)
    except Exception:
        return None

    imports = list(dataset.get("imports") or [])
    if not imports:
        return None

    periods = []
    latest_imported = ""
    for row in imports:
        a = _as_date(row.get("period_start"))
        b = _as_date(row.get("period_end")) or a
        if a is None or b is None:
            continue
        if b < a:
            a, b = b, a
        periods.append((a, b))
        latest_imported = max(latest_imported, str(row.get("imported_at") or ""))

    if not periods:
        return None

    covered = set()
    for a, b in periods:
        left = max(a, month_start)
        right = min(b, today)
        d = left
        while d <= right:
            covered.add(d)
            d += timedelta(days=1)

    cursor = month_start
    continuous_end = None
    while cursor <= today and cursor in covered:
        continuous_end = cursor
        cursor += timedelta(days=1)

    if continuous_end is not None:
        start = month_start
        end = continuous_end
    else:
        start = min(a for a, _ in periods)
        end = max(b for _, b in periods)

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "main_value": float(dataset.get("total") or 0.0),
        "row_count": int(len(dataset.get("items") or {})),
        "created_at": latest_imported,
        "file_name": "당월 광고성과보고서 통합",
    }


def _patch_overview(core, original_get_import_overview):
    def wrapper(db_path=None):
        db = db_path or core.DEFAULT_DB
        df = original_get_import_overview(db)
        summary = _canonical_month_summary(core, db)
        if not summary:
            return df

        if df is None or df.empty:
            row = {
                "id": 0,
                "file_name": summary["file_name"],
                "file_hash": "",
                "data_type": "ad_performance",
                "period_start": summary["period_start"],
                "period_end": summary["period_end"],
                "settlement_month": None,
                "created_at": summary["created_at"],
                "notes": None,
                "row_count": summary["row_count"],
                "main_value": summary["main_value"],
                "main_kind": "money",
            }
            return pd.DataFrame([row])

        out = df.copy()
        mask = (
            out["data_type"].astype(str).eq("ad_performance")
            if "data_type" in out.columns
            else pd.Series(False, index=out.index)
        )
        if mask.any():
            idx = out.index[mask][0]
            for key, value in summary.items():
                if key in out.columns:
                    out.at[idx, key] = value
            if "main_kind" in out.columns:
                out.at[idx, "main_kind"] = "money"
            return out

        row = {c: None for c in out.columns}
        row.update({
            "id": 0,
            "file_name": summary["file_name"],
            "data_type": "ad_performance",
            "period_start": summary["period_start"],
            "period_end": summary["period_end"],
            "created_at": summary["created_at"],
            "row_count": summary["row_count"],
            "main_value": summary["main_value"],
            "main_kind": "money",
        })
        return pd.concat([pd.DataFrame([row]), out], ignore_index=True)

    return wrapper


def apply(core) -> None:
    global _PATCHED
    if _PATCHED or getattr(core, "_rg_ad_import_unified_v0989", False):
        _PATCHED = True
        return

    original_import = core.import_ad_performance
    original_overview = core.get_import_overview

    def import_wrapper(source, file_name: str, period_start=None, period_end=None, db_path=None):
        db = db_path or core.DEFAULT_DB
        raw = core.as_bytes(source)
        result = original_import(
            source,
            file_name,
            period_start=period_start,
            period_end=period_end,
            db_path=db,
        )

        start = _as_date((result or {}).get("period_start")) or _as_date(period_start)
        end = _as_date((result or {}).get("period_end")) or _as_date(period_end)
        if start is None or end is None:
            try:
                parsed = core.extract_period_from_filename(file_name)
                if parsed:
                    start = start or _as_date(parsed[0])
                    end = end or _as_date(parsed[1])
            except Exception:
                pass

        if start is not None and end is not None:
            if end < start:
                start, end = end, start
            try:
                _sync_one(core, raw, file_name, start, end, db)
            except Exception as exc:
                # The normal Coupang-data import has already succeeded. Never
                # turn it into a failed upload just because the mirror had a problem.
                try:
                    result = dict(result or {})
                    result["provisional_sync_warning"] = str(exc)
                except Exception:
                    pass
        return result

    core.import_ad_performance = import_wrapper
    core.get_import_overview = _patch_overview(core, original_overview)
    core._rg_ad_import_unified_v0989 = True
    _PATCHED = True
