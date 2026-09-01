"""RG Manager v0.9.129 dashboard upload coverage with previous-month closing visibility.

The old dashboard only showed the calendar month containing ``date.today()``.
That made the previous month's last entered date disappear on the first day of a
new month, exactly when the user still needs to finish month-end uploads.

v0.9.129 keeps the current-month cards and also shows the previous month when:
- today is within the first five days of the month, or
- the previous month is not continuously covered through month-end.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from typing import Any


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
                "SELECT period_start,period_end"
                + (",file_name" if "file_name" in cols else ",'' AS file_name")
                + " FROM imports"
                + where
                + " ORDER BY COALESCE(period_start,''),COALESCE(period_end,'')",
                params,
            ).fetchall()
        elif kind == "ads":
            cols = _cols(c, "provisional_ad_report_imports")
            if not {"period_start", "period_end"}.issubset(cols):
                return []
            rows = c.execute(
                "SELECT period_start,period_end"
                + (",file_name" if "file_name" in cols else ",'' AS file_name")
                + " FROM provisional_ad_report_imports "
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


def _month_end(month_start: date) -> date:
    return month_start.replace(day=monthrange(month_start.year, month_start.month)[1])


def _previous_month_start(current_month_start: date) -> date:
    return (current_month_start - timedelta(days=1)).replace(day=1)


def _coverage(periods, month_start: date, target_end: date):
    """Return continuous coverage inside [month_start, target_end]."""
    covered: set[date] = set()
    files = set()

    for a, b, file_name in periods:
        if b < month_start or a > target_end:
            continue
        left = max(a, month_start)
        right = min(b, target_end)
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
    while cursor <= target_end and cursor in covered:
        continuous_end = cursor
        cursor += timedelta(days=1)

    latest = max(covered) if covered else None
    next_date = (continuous_end + timedelta(days=1)) if continuous_end else month_start
    missing_days = 0
    if next_date <= target_end:
        missing_days = (target_end - next_date).days + 1

    return {
        "continuous_end": continuous_end,
        "latest": latest,
        "next_date": next_date,
        "files": len(files),
        "missing_days": missing_days,
        "covered_days": len(covered),
        "target_end": target_end,
        "complete": continuous_end is not None and continuous_end >= target_end,
    }


def _date_short(d: date | None) -> str:
    if d is None:
        return "-"
    return f"{d.month}월 {d.day}일"


def _status_text(status, month_start: date, label: str):
    end = status["continuous_end"]
    latest = status["latest"]
    next_date = status["next_date"]

    if end is None:
        headline = f"{label} 입력 없음"
        detail = f"다음 입력: {_date_short(month_start)}부터"
        tone = "empty"
    else:
        headline = f"{_date_short(end)}까지 입력"
        if status["complete"]:
            detail = f"{label} 연속 입력 완료"
            tone = "complete"
        else:
            detail = f"다음 입력: {_date_short(next_date)}부터"
            tone = "pending"

    if not status["complete"]:
        if latest is not None and end is not None and latest > end:
            detail += f" · 이후 {_date_short(latest)}까지 자료가 있으나 중간 누락 있음"
        elif status["missing_days"] > 0:
            detail += f" · {status['missing_days']:,}일 미입력"

    return headline, detail, tone


def _card(st_obj, column, title: str, status, month_start: date, label: str):
    headline, detail, tone = _status_text(status, month_start, label)
    icon = "✅" if tone == "complete" else "⚠️" if tone == "pending" else "⬜"
    with column:
        with st_obj.container(border=True):
            st_obj.markdown(f"**{title}**")
            st_obj.markdown(f"### {icon} {headline}")
            st_obj.caption(detail)
            st_obj.caption(f"{label} 인식 파일 {status['files']:,}개 · 연속 입력 기준")


def _render_pair(st_obj, title: str, caption: str, sales, ads, month_start: date, label: str):
    st_obj.markdown(f"### {title}")
    st_obj.caption(caption)
    c1, c2 = st_obj.columns(2)
    _card(st_obj, c1, "📊 판매 Excel", sales, month_start, label)
    _card(st_obj, c2, "📣 광고비 Excel", ads, month_start, label)


def render(st_obj, core, db_path=None):
    """Render previous-month closing status when needed, then current-month status."""
    db = db_path or core.DEFAULT_DB
    today = date.today()
    current_start = today.replace(day=1)
    previous_start = _previous_month_start(current_start)
    previous_end = _month_end(previous_start)

    sales_periods = []
    ad_periods = []
    try:
        sales_periods = _load_periods(core, db, "sales")
    except Exception:
        pass
    try:
        ad_periods = _load_periods(core, db, "ads")
    except Exception:
        pass

    previous_sales = _coverage(sales_periods, previous_start, previous_end)
    previous_ads = _coverage(ad_periods, previous_start, previous_end)

    # Current month only expects completed days through yesterday.
    # On the first day of a month there are no completed current-month days yet,
    # but we still show "input none / next input 1st" for clarity.
    current_target = today - timedelta(days=1)
    current_sales = _coverage(sales_periods, current_start, current_target)
    current_ads = _coverage(ad_periods, current_start, current_target)

    # Month-end work is most relevant during the first five days. If either file
    # type still has a gap, keep the previous-month block visible after day five.
    show_previous = (
        today.day <= 5
        or not previous_sales["complete"]
        or not previous_ads["complete"]
    )

    if show_previous:
        prev_label = f"{previous_start.month}월"
        _render_pair(
            st_obj,
            f"직전 월 마감 입력 현황 · {previous_start.year}-{previous_start.month:02d}",
            "직전 월 자료가 월말까지 연속으로 입력됐는지 보여줍니다. "
            "새 달이 시작돼도 마지막 입력일과 남은 구간을 확인할 수 있습니다.",
            previous_sales,
            previous_ads,
            previous_start,
            prev_label,
        )
        st_obj.markdown("")

    cur_label = f"{current_start.month}월"
    _render_pair(
        st_obj,
        f"당월 자료 입력 현황 · {current_start.year}-{current_start.month:02d}",
        "당월 1일부터 연속으로 입력된 날짜를 기준으로 표시합니다. "
        "중간 날짜가 비어 있으면 뒤 날짜의 파일이 있어도 누락 구간부터 다시 안내합니다.",
        current_sales,
        current_ads,
        current_start,
        cur_label,
    )
