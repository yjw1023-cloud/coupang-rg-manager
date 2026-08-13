"""v0.9.90 generic data-import advertising filename period detection.

The generic '새 자료 반영' screen must always display and use the period found
in Coupang advertising-performance filenames.  This also overrides stale
Streamlit date-widget state left by older versions.
"""
from __future__ import annotations

from datetime import date
import re

import streamlit as st

_AD_LABELS = {"광고 성과보고서", "광고성과보고서"}
_PATCHED = False
_date_call_index = 0


def _options_from_call(args, kwargs):
    if "options" in kwargs:
        return kwargs.get("options")
    if len(args) >= 2:
        return args[1]
    return None


def _period_from_filename(name: str):
    text = str(name or "")
    matches = []
    for m in re.finditer(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", text):
        try:
            matches.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except Exception:
            pass
    for m in re.finditer(
        r"(?<!\d)(20\d{2})\s*[-_./년]\s*(\d{1,2})\s*[-_./월]\s*(\d{1,2})\s*일?(?!\d)",
        text,
    ):
        try:
            matches.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except Exception:
            pass
    matches.sort(key=lambda x: x[0])
    if len(matches) < 2:
        return None
    start, end = matches[0][1], matches[1][1]
    return (end, start) if end < start else (start, end)


def _first_uploaded(result):
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return result


def _label(args, kwargs):
    return str(args[0] if args else kwargs.get("label", "") or "")


def _is_dedicated_pnl_uploader(args, kwargs):
    normalized = _label(args, kwargs).replace(" ", "").lower()
    return "광고성과보고서excel" in normalized


def _current_period():
    start = st.session_state.get("_rg_ad_filename_period_start")
    end = st.session_state.get("_rg_ad_filename_period_end")
    return (start, end) if isinstance(start, date) and isinstance(end, date) else None


def apply():
    global _PATCHED, _date_call_index
    _date_call_index = 0
    if _PATCHED:
        return

    original_selectbox = st.selectbox
    original_radio = st.radio
    original_file_uploader = st.file_uploader
    original_date_input = st.date_input

    def _set_selected(result, options):
        global _date_call_index
        try:
            texts = [str(x) for x in options] if options is not None else []
        except Exception:
            texts = []
        if any(label in texts for label in _AD_LABELS):
            st.session_state["_rg_ad_generic_period_active"] = str(result) in _AD_LABELS
            _date_call_index = 0
            if not st.session_state["_rg_ad_generic_period_active"]:
                st.session_state.pop("_rg_ad_filename_period_changed", None)

    def selectbox_wrapper(*args, **kwargs):
        result = original_selectbox(*args, **kwargs)
        _set_selected(result, _options_from_call(args, kwargs))
        return result

    def radio_wrapper(*args, **kwargs):
        result = original_radio(*args, **kwargs)
        _set_selected(result, _options_from_call(args, kwargs))
        return result

    def file_uploader_wrapper(*args, **kwargs):
        result = original_file_uploader(*args, **kwargs)

        if _is_dedicated_pnl_uploader(args, kwargs):
            st.session_state["_rg_ad_generic_period_active"] = False
            return result

        if not st.session_state.get("_rg_ad_generic_period_active", False):
            return result

        uploaded = _first_uploaded(result)
        old_name = str(st.session_state.get("_rg_ad_filename_name") or "")
        if uploaded is None:
            if old_name:
                for key in (
                    "_rg_ad_filename_name",
                    "_rg_ad_filename_period_start",
                    "_rg_ad_filename_period_end",
                    "_rg_ad_filename_period_changed",
                ):
                    st.session_state.pop(key, None)
            return result

        name = str(getattr(uploaded, "name", "") or "")
        parsed = _period_from_filename(name)
        if parsed:
            start, end = parsed
            st.session_state["_rg_ad_filename_name"] = name
            st.session_state["_rg_ad_filename_period_start"] = start
            st.session_state["_rg_ad_filename_period_end"] = end
            if name != old_name:
                st.session_state["_rg_ad_filename_period_changed"] = True
                try:
                    st.rerun()
                except Exception:
                    pass
        else:
            st.session_state["_rg_ad_filename_name"] = name
            st.session_state.pop("_rg_ad_filename_period_start", None)
            st.session_state.pop("_rg_ad_filename_period_end", None)
            st.session_state["_rg_ad_filename_period_changed"] = False
        return result

    def date_input_wrapper(*args, **kwargs):
        global _date_call_index
        if not st.session_state.get("_rg_ad_generic_period_active", False):
            return original_date_input(*args, **kwargs)

        period = _current_period()
        if not period:
            return original_date_input(*args, **kwargs)
        start_default, end_default = period

        label = _label(args, kwargs)
        value = kwargs.get("value", args[1] if len(args) >= 2 else None)
        is_range = isinstance(value, (tuple, list)) and len(value) == 2
        if "시작" in label:
            target = "start"
        elif "종료" in label:
            target = "end"
        elif is_range:
            target = "range"
        else:
            target = "start" if _date_call_index == 0 else "end" if _date_call_index == 1 else "other"
        if target == "other":
            return original_date_input(*args, **kwargs)
        _date_call_index += 1

        new_value = (
            (start_default, end_default)
            if target == "range"
            else start_default
            if target == "start"
            else end_default
        )

        # v0.9.90: stale widget state must never override explicit filename dates.
        kwargs = dict(kwargs)
        widget_key = kwargs.get("key")
        if not widget_key:
            widget_key = f"_rg_ad_filename_forced_{target}"
            kwargs["key"] = widget_key
        try:
            st.session_state[widget_key] = new_value
        except Exception:
            pass

        if len(args) >= 2:
            args = list(args)
            args[1] = new_value
            args = tuple(args)
        else:
            kwargs["value"] = new_value

        result = original_date_input(*args, **kwargs)
        if target in ("range", "end"):
            if bool(st.session_state.get("_rg_ad_filename_period_changed")):
                st.success(
                    f"광고 파일명에서 기간 자동 인식: {start_default:%Y-%m-%d} ~ {end_default:%Y-%m-%d}"
                )
            st.session_state["_rg_ad_filename_period_changed"] = False
        return result

    st.selectbox = selectbox_wrapper
    st.radio = radio_wrapper
    st.file_uploader = file_uploader_wrapper
    st.date_input = date_input_wrapper
    _PATCHED = True
