"""v0.9.88 unify generic Coupang data-management advertising reports.

The legacy `쿠팡 자료 관리` screen and the provisional-P&L advertising uploader
historically stored advertising reports in different places.  This patch keeps
the legacy screen usable while making `provisional_ad_report_imports` the
canonical source for dashboard/P&L/status display.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
import importlib
import re
from typing import Any

import streamlit as st

_PATCHED = False
_AD_LABELS = {"광고 성과보고서", "광고성과보고서"}


def _active() -> bool:
    return bool(st.session_state.get("_rg_ad_generic_period_active", False))


def _label(args, kwargs) -> str:
    return str(args[0] if args else kwargs.get("label", "") or "")


def _first_uploaded(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


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


def _period_from_filename(name: str):
    try:
        period = importlib.import_module("ad_period_v0987")._period_from_filename(name)
        if period:
            return period
    except Exception:
        pass
    text = str(name or "")
    found = []
    for m in re.finditer(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text):
        try:
            found.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except Exception:
            pass
    found.sort(key=lambda x: x[0])
    if len(found) < 2:
        return None
    a, b = found[0][1], found[1][1]
    return (b, a) if b < a else (a, b)


def _current_period():
    parsed = _period_from_filename(str(st.session_state.get("_rg_ad_sync_file_name") or ""))
    if parsed:
        return parsed
    a = _as_date(st.session_state.get("_rg_ad_sync_start"))
    b = _as_date(st.session_state.get("_rg_ad_sync_end"))
    if a is None or b is None:
        return None
    return (b, a) if b < a else (a, b)


def _replace_key(start: date, end: date) -> str:
    return f"_rg_ad_sync_replace_{start:%Y%m%d}_{end:%Y%m%d}"


def _canonical_module():
    return importlib.import_module("provisional_ad_report_v0956")


def _summary(core):
    today = date.today()
    month = today.strftime("%Y-%m")
    month_start = today.replace(day=1)
    try:
        dataset = _canonical_module().load_month(core, month, core.DEFAULT_DB)
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

    covered: set[date] = set()
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

    period_start = month_start if continuous_end is not None else min(a for a, _ in periods)
    period_end = continuous_end if continuous_end is not None else max(b for _, b in periods)
    yesterday = today - timedelta(days=1)
    complete = continuous_end is not None and continuous_end >= yesterday

    latest_text = ""
    if latest_imported:
        try:
            dt = datetime.fromisoformat(latest_imported.replace("Z", "+00:00"))
            latest_text = dt.strftime("%m/%d %H:%M")
        except Exception:
            m = re.search(r"(\d{2})-(\d{2}).*?(\d{2}):(\d{2})", latest_imported)
            if m:
                latest_text = f"{m.group(1)}/{m.group(2)} {m.group(3)}:{m.group(4)}"

    return {
        "start": period_start,
        "end": period_end,
        "total": float(dataset.get("total") or 0.0),
        "rows": len(dataset.get("items") or {}),
        "latest": latest_text,
        "complete": complete,
    }


def _replace_status_card(body: str, core) -> str:
    # The legacy status card is custom HTML.  Dedicated P&L headings do not use
    # the spaced label and are intentionally left untouched.
    if "광고 성과보고서" not in body or "<" not in body or ">" not in body:
        return body
    info = _summary(core)
    if not info:
        return body

    out = str(body)
    period = f"{info['start']:%Y.%m.%d} ~ {info['end']:%Y.%m.%d}"
    out = re.sub(
        r"20\d{2}[./-]\d{2}[./-]\d{2}\s*~\s*20\d{2}[./-]\d{2}[./-]\d{2}",
        period,
        out,
        count=1,
    )
    out = re.sub(
        r"\d[\d,]*원\s*[·ㆍ]\s*\d[\d,]*행",
        f"{int(round(info['total'])):,}원 · {int(info['rows']):,}행",
        out,
        count=1,
    )
    if info.get("latest"):
        out = re.sub(
            r"최근\s*입력\s*\d{2}/\d{2}\s+\d{2}:\d{2}",
            f"최근 입력 {info['latest']}",
            out,
            count=1,
        )
    if info.get("complete"):
        out = out.replace("업데이트 필요", "정상", 1)
    elif "업데이트 필요" not in out:
        out = out.replace("정상", "업데이트 필요", 1)
    return out


def apply(core) -> None:
    global _PATCHED
    if _PATCHED:
        return

    original_file_uploader = st.file_uploader
    original_date_input = st.date_input
    original_button = st.button
    original_markdown = st.markdown

    def file_uploader_wrapper(*args, **kwargs):
        result = original_file_uploader(*args, **kwargs)
        if not _active():
            return result
        label = _label(args, kwargs).replace(" ", "").lower()
        if "광고성과보고서excel" in label:
            return result
        uploaded = _first_uploaded(result)
        if uploaded is None:
            st.session_state.pop("_rg_ad_sync_file_name", None)
            st.session_state.pop("_rg_ad_sync_raw", None)
            return result
        name = str(getattr(uploaded, "name", "") or "")
        try:
            raw = uploaded.getvalue()
        except Exception:
            try:
                raw = uploaded.read()
            except Exception:
                raw = b""
        st.session_state["_rg_ad_sync_file_name"] = name
        st.session_state["_rg_ad_sync_raw"] = bytes(raw or b"")

        # If this file overlaps canonical provisional-ad data, require the same
        # explicit replacement confirmation used by the dedicated P&L uploader.
        period = _current_period()
        if period and raw:
            try:
                ad = _canonical_module()
                overlaps = ad._overlaps(core, core.DEFAULT_DB, period[0], period[1])
            except Exception:
                overlaps = []
            if overlaps:
                names = ", ".join(
                    f"{r['period_start']}~{r['period_end']} {r['file_name']}" for r in overlaps[:3]
                )
                st.warning("잠정손익 광고자료와 기간이 겹칩니다: " + names)
                st.checkbox(
                    "겹치는 기존 광고자료를 삭제하고 이 파일로 교체",
                    key=_replace_key(period[0], period[1]),
                )
        return result

    def date_input_wrapper(*args, **kwargs):
        result = original_date_input(*args, **kwargs)
        if _active():
            label = _label(args, kwargs)
            if "시작" in label:
                st.session_state["_rg_ad_sync_start"] = _as_date(result)
            elif "종료" in label:
                st.session_state["_rg_ad_sync_end"] = _as_date(result)
            elif isinstance(result, (tuple, list)) and len(result) == 2:
                st.session_state["_rg_ad_sync_start"] = _as_date(result[0])
                st.session_state["_rg_ad_sync_end"] = _as_date(result[1])
        return result

    def button_wrapper(*args, **kwargs):
        clicked = original_button(*args, **kwargs)
        if not _active():
            return clicked
        label = _label(args, kwargs).replace(" ", "")
        if "자료반영" not in label:
            return clicked
        if not clicked:
            return False

        raw = st.session_state.get("_rg_ad_sync_raw") or b""
        file_name = str(st.session_state.get("_rg_ad_sync_file_name") or "")
        period = _current_period()
        if not raw or not file_name or not period:
            st.error("광고성과보고서 파일 또는 조회기간을 확인해 주세요.")
            return False
        start, end = period
        try:
            ad = _canonical_module()
            grouped, _total = ad._parse_excel(bytes(raw))
            overlaps = ad._overlaps(core, core.DEFAULT_DB, start, end)
            replace = bool(st.session_state.get(_replace_key(start, end), False))
            if overlaps and not replace:
                st.error("기존 잠정손익 광고자료와 기간이 겹칩니다. 교체 항목을 체크한 뒤 다시 반영해 주세요.")
                return False
            ad._save(
                core,
                core.DEFAULT_DB,
                file_name,
                bytes(raw),
                start,
                end,
                grouped,
                replace_overlap=replace,
            )
            st.success(
                f"광고성과보고서를 잠정손익에도 반영했습니다. {start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
            )
            # Return True so the legacy screen also records its normal upload
            # history.  From now on both histories receive the same file/period.
            return True
        except Exception as exc:
            st.error("잠정손익 광고자료 반영 실패: " + str(exc))
            return False

    def markdown_wrapper(body, *args, **kwargs):
        try:
            body = _replace_status_card(str(body), core)
        except Exception:
            pass
        return original_markdown(body, *args, **kwargs)

    st.file_uploader = file_uploader_wrapper
    st.date_input = date_input_wrapper
    st.button = button_wrapper
    st.markdown = markdown_wrapper
    _PATCHED = True
