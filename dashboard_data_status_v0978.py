"""RG Manager v0.9.78 dashboard current-month input coverage.

Shows, immediately above the dashboard monthly performance section, how far the
current month's sales-stat Excel and advertising-performance Excel have been
continuously entered from the 1st.  The next required input date is based on
continuous coverage, so a later file never hides a gap in the middle of the month.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any


_MARKER = "# _rg_dashboard_data_status_v0978"


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    safe = str(table).replace('"', '""')
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{safe}")').fetchall()}


def _to_date(value: Any) -> date | None:
    text = str(value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except Exception:
        return None


def _load_periods(core, db, kind: str):
    core.init_db(db)
    with core._conn(db) as c:
        if kind == "sales":
            cols = _cols(c, "imports")
            if not {"period_start", "period_end"}.issubset(cols):
                return []
            where = ""
            params: tuple[Any, ...] = ()
            if "data_type" in cols:
                where = " WHERE data_type=?"
                params = ("sales_stats",)
            rows = c.execute(
                "SELECT period_start,period_end" +
                (",file_name" if "file_name" in cols else ",'' AS file_name") +
                " FROM imports" + where +
                " ORDER BY COALESCE(period_start,''),COALESCE(period_end,'')",
                params,
            ).fetchall()
        elif kind == "ads":
            cols = _cols(c, "provisional_ad_report_imports")
            if not {"period_start", "period_end"}.issubset(cols):
                return []
            rows = c.execute(
                "SELECT period_start,period_end" +
                (",file_name" if "file_name" in cols else ",'' AS file_name") +
                " FROM provisional_ad_report_imports "
                "ORDER BY COALESCE(period_start,''),COALESCE(period_end,'')"
            ).fetchall()
        else:
            return []

    out = []
    for r in rows:
        a = _to_date(r["period_start"])
        b = _to_date(r["period_end"]) or a
        if a is None or b is None:
            continue
        if b < a:
            a, b = b, a
        out.append((a, b, str(r["file_name"] or "")))
    return out


def _coverage(periods, month_start: date, today: date):
    covered: set[date] = set()
    files = set()
    month_end_limit = today

    for a, b, file_name in periods:
        if b < month_start or a > month_end_limit:
            continue
        left = max(a, month_start)
        right = min(b, month_end_limit)
        if left > right:
            continue
        if file_name:
            files.add(file_name)
        d = left
        while d <= right:
            covered.add(d)
            d += timedelta(days=1)

    cursor = month_start
    continuous_end = None
    while cursor <= month_end_limit and cursor in covered:
        continuous_end = cursor
        cursor += timedelta(days=1)

    latest = max(covered) if covered else None
    next_date = (continuous_end + timedelta(days=1)) if continuous_end else month_start
    yesterday = today - timedelta(days=1)
    completed_target = yesterday if yesterday >= month_start else None
    missing_completed_days = 0
    if completed_target is not None and next_date <= completed_target:
        missing_completed_days = (completed_target - next_date).days + 1

    return {
        "continuous_end": continuous_end,
        "latest": latest,
        "next_date": next_date,
        "files": len(files),
        "missing_completed_days": missing_completed_days,
        "covered_days": len(covered),
    }


def _date_short(d: date | None) -> str:
    if d is None:
        return "-"
    return f"{d.month}월 {d.day}일"


def _status_text(status, month_start: date, today: date):
    end = status["continuous_end"]
    latest = status["latest"]
    next_date = status["next_date"]
    yesterday = today - timedelta(days=1)

    if end is None:
        headline = "당월 입력 없음"
        detail = f"다음 입력: {_date_short(month_start)}부터"
        tone = "empty"
    else:
        headline = f"{_date_short(end)}까지 입력"
        detail = f"다음 입력: {_date_short(next_date)}부터"
        if yesterday >= month_start and end >= yesterday:
            tone = "complete"
        else:
            tone = "pending"

    gap_note = ""
    if latest is not None and end is not None and latest > end:
        gap_note = f" · 이후 {_date_short(latest)}까지 자료가 있으나 중간 누락 있음"
    elif status["missing_completed_days"] > 0:
        gap_note = f" · 전일 기준 {status['missing_completed_days']:,}일 미입력"

    return headline, detail + gap_note, tone


def _card(st_obj, column, title: str, status, month_start: date, today: date):
    headline, detail, tone = _status_text(status, month_start, today)
    icon = "✅" if tone == "complete" else "⚠️" if tone == "pending" else "⬜"
    with column:
        with st_obj.container(border=True):
            st_obj.markdown(f"**{title}**")
            st_obj.markdown(f"### {icon} {headline}")
            st_obj.caption(detail)
            st_obj.caption(f"당월 인식 파일 {status['files']:,}개 · 연속 입력 기준")


def render(st_obj, core, db_path=None):
    """Render current-month sales/ad upload coverage cards."""
    db = db_path or core.DEFAULT_DB
    today = date.today()
    month_start = today.replace(day=1)

    try:
        sales = _coverage(_load_periods(core, db, "sales"), month_start, today)
    except Exception:
        sales = _coverage([], month_start, today)
    try:
        ads = _coverage(_load_periods(core, db, "ads"), month_start, today)
    except Exception:
        ads = _coverage([], month_start, today)

    st_obj.markdown("### 당월 자료 입력 현황")
    st_obj.caption(
        "당월 1일부터 연속으로 입력된 날짜를 기준으로 표시합니다. "
        "따라서 중간 날짜가 비어 있으면 뒤 날짜의 파일이 있어도 누락 구간부터 다시 안내합니다."
    )
    c1, c2 = st_obj.columns(2)
    _card(st_obj, c1, "📊 판매 Excel", sales, month_start, today)
    _card(st_obj, c2, "📣 광고비 Excel", ads, month_start, today)


def patch_source(source: str) -> str:
    """Insert the status cards immediately before every dashboard 월별 실적 branch."""
    if _MARKER in source:
        return source

    pattern = re.compile(r'(?m)^([ \t]*)(section\("월별 실적")')
    matches = list(pattern.finditer(source))
    if not matches:
        raise RuntimeError("v0.9.78 대시보드의 월별 실적 위치를 찾지 못했습니다.")

    def repl(match):
        indent = match.group(1)
        return (
            f"{indent}pnl_month_default_v0915.render_dashboard_data_status(st, core)\n"
            f"{indent}{match.group(2)}"
        )

    source = pattern.sub(repl, source)
    return _MARKER + "\n" + source
