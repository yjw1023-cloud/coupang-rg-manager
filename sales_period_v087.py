"""Weekly default period for 재고현황 판매통계 imports (v0.8.7).

Rules:
- Only applies when the selected upload type is `재고현황 판매통계`.
- Default period is the most recently completed Monday-Sunday week.
- Start/end remain freely editable; month boundaries do not force a seven-day range.
- If a different file already exists for the same period, require explicit replacement confirmation.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import streamlit as st

_SALES_LABEL = "재고현황 판매통계"
_PATCHED = False
_date_call_index = 0
_caption_rendered = False


def last_completed_week(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    current_monday = today - timedelta(days=today.weekday())
    end = current_monday - timedelta(days=1)
    start = end - timedelta(days=6)
    return start, end


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def _replace_key(start: date, end: date) -> str:
    return f"_rg_replace_sales_stats_{start:%Y%m%d}_{end:%Y%m%d}"


def _existing_rows(core_module, start: date, end: date):
    core_module.init_db(core_module.DEFAULT_DB)
    with core_module._conn(core_module.DEFAULT_DB) as c:
        return c.execute(
            """SELECT id,file_hash,file_name,created_at FROM imports
               WHERE data_type='sales_stats' AND period_start=? AND period_end=?
               ORDER BY id DESC""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()


def _render_period_notice(core_module, start: date | None, end: date | None) -> None:
    global _caption_rendered
    if start is None or end is None:
        return
    if not _caption_rendered:
        st.caption(
            "기본 기간은 가장 최근에 끝난 월요일~일요일입니다. "
            "월초·월말 등에는 시작일과 종료일을 직접 수정해도 됩니다."
        )
        _caption_rendered = True
    if start > end:
        st.error("조회 시작일은 종료일보다 늦을 수 없습니다.")
        return
    rows = _existing_rows(core_module, start, end)
    if rows:
        st.warning(
            f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d} 재고현황 판매통계가 이미 입력되어 있습니다. "
            "새 파일로 교체하려면 아래 항목을 체크하세요."
        )
        st.checkbox(
            "기존 기간 자료를 새 파일로 교체",
            key=_replace_key(start, end),
        )


def _options_from_call(args, kwargs):
    if "options" in kwargs:
        return kwargs.get("options")
    if len(args) >= 2:
        return args[1]
    return None


def _set_selected(result, options) -> None:
    global _date_call_index, _caption_rendered
    try:
        option_texts = [str(x) for x in options] if options is not None else []
    except Exception:
        option_texts = []
    if _SALES_LABEL in option_texts:
        st.session_state["_rg_sales_stats_period_active"] = str(result) == _SALES_LABEL
        _date_call_index = 0
        _caption_rendered = False


def apply(core_module) -> None:
    """Patch Streamlit period widgets and guard same-period replacement."""
    global _PATCHED, _date_call_index, _caption_rendered
    _date_call_index = 0
    _caption_rendered = False
    if _PATCHED:
        return

    original_selectbox = st.selectbox
    original_radio = st.radio
    original_date_input = st.date_input
    original_import_sales_stats = core_module.import_sales_stats

    def selectbox_wrapper(*args, **kwargs):
        result = original_selectbox(*args, **kwargs)
        _set_selected(result, _options_from_call(args, kwargs))
        return result

    def radio_wrapper(*args, **kwargs):
        result = original_radio(*args, **kwargs)
        _set_selected(result, _options_from_call(args, kwargs))
        return result

    def date_input_wrapper(*args, **kwargs):
        global _date_call_index
        if not st.session_state.get("_rg_sales_stats_period_active", False):
            return original_date_input(*args, **kwargs)

        label = str(args[0] if args else kwargs.get("label", ""))
        start_default, end_default = last_completed_week()

        value = kwargs.get("value", args[1] if len(args) >= 2 else None)
        is_range = (
            (isinstance(value, (tuple, list)) and len(value) == 2)
            or ("기간" in label and "시작" not in label and "종료" not in label)
        )

        if "시작" in label:
            target = "start"
        elif "종료" in label:
            target = "end"
        elif is_range:
            target = "range"
        else:
            # Fallback for older UI labels: first two date widgets after selecting
            # 재고현황 판매통계 are treated as start/end.
            target = "start" if _date_call_index == 0 else "end" if _date_call_index == 1 else "other"

        if target == "other":
            return original_date_input(*args, **kwargs)

        _date_call_index += 1
        new_value = (start_default, end_default) if target == "range" else start_default if target == "start" else end_default

        if len(args) >= 2:
            args = list(args)
            args[1] = new_value
            args = tuple(args)
        else:
            kwargs = dict(kwargs)
            kwargs["value"] = new_value

        result = original_date_input(*args, **kwargs)

        if target == "range":
            if isinstance(result, (tuple, list)) and len(result) == 2:
                start_value, end_value = _as_date(result[0]), _as_date(result[1])
                st.session_state["_rg_sales_period_start_value"] = start_value
                st.session_state["_rg_sales_period_end_value"] = end_value
                _render_period_notice(core_module, start_value, end_value)
        elif target == "start":
            start_value = _as_date(result)
            st.session_state["_rg_sales_period_start_value"] = start_value
            end_value = _as_date(st.session_state.get("_rg_sales_period_end_value"))
            if end_value is not None:
                _render_period_notice(core_module, start_value, end_value)
        else:
            end_value = _as_date(result)
            st.session_state["_rg_sales_period_end_value"] = end_value
            start_value = _as_date(st.session_state.get("_rg_sales_period_start_value"))
            _render_period_notice(core_module, start_value, end_value)

        return result

    def import_sales_stats_guard(source, file_name: str, period_start: str, period_end: str, db_path=core_module.DEFAULT_DB):
        start = _as_date(period_start)
        end = _as_date(period_end)
        if start is None or end is None:
            return original_import_sales_stats(source, file_name, period_start, period_end, db_path)
        if start > end:
            raise ValueError("조회 시작일은 종료일보다 늦을 수 없습니다.")

        core_module.init_db(db_path)
        with core_module._conn(db_path) as c:
            rows = c.execute(
                """SELECT id,file_hash FROM imports
                   WHERE data_type='sales_stats' AND period_start=? AND period_end=?
                   ORDER BY id DESC""",
                (start.isoformat(), end.isoformat()),
            ).fetchall()

        if rows:
            incoming_hash = core_module.file_hash(source)
            if not any(str(r["file_hash"] or "") == incoming_hash for r in rows):
                if not st.session_state.get(_replace_key(start, end), False):
                    raise ValueError(
                        f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d} 자료가 이미 있습니다. "
                        "'기존 기간 자료를 새 파일로 교체'를 체크한 뒤 다시 업로드해 주세요."
                    )

        return original_import_sales_stats(source, file_name, period_start, period_end, db_path)

    st.selectbox = selectbox_wrapper
    st.radio = radio_wrapper
    st.date_input = date_input_wrapper
    core_module.import_sales_stats = import_sales_stats_guard
    _PATCHED = True
