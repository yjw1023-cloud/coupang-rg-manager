"""v0.9.104 sales-stat same-file post-import notice fix.

After a successful sales-stat import Streamlit reruns while keeping the uploaded
file selected.  The legacy period notice then sees the newly saved period and
incorrectly warns that the user is trying to replace an existing period.

This patch distinguishes the currently selected file from a genuinely different
file for the same period:
- same file hash already stored -> show a success/already-reflected notice only;
- different file for the same exact period -> keep the explicit replacement
  confirmation because sales-stat replacement also changes inventory deductions;
- no changes to sales posting, return-sale matching, provisional P&L or inventory
  replacement semantics.
"""
from __future__ import annotations

import hashlib

import streamlit as st


_APPLIED = False


def _selected_file_hash() -> str:
    """Find the currently selected UploadedFile in Streamlit session state."""
    expected_name = str(st.session_state.get("_rg_sales_filename_name") or "").strip()
    if not expected_name:
        return ""

    try:
        keys = list(st.session_state.keys())
    except Exception:
        keys = []

    for key in keys:
        try:
            value = st.session_state.get(key)
        except Exception:
            continue
        candidates = value if isinstance(value, (list, tuple)) else [value]
        for obj in candidates:
            try:
                name = str(getattr(obj, "name", "") or "").strip()
                if name != expected_name or not hasattr(obj, "getvalue"):
                    continue
                raw = obj.getvalue()
                if raw is None:
                    continue
                return hashlib.sha256(bytes(raw)).hexdigest()
            except Exception:
                continue
    return ""


def apply(sales_period_module, core_module):
    global _APPLIED
    if _APPLIED or getattr(sales_period_module, "_rg_samefile_notice_v09104_applied", False):
        return sales_period_module

    sp = sales_period_module

    def render_period_notice(core_obj, start, end):
        if start is None or end is None:
            return

        if not getattr(sp, "_caption_rendered", False):
            auto = sp._filename_period()
            file_name = str(st.session_state.get("_rg_sales_filename_name") or "").strip()
            if auto and auto == (start, end) and file_name:
                st.success(
                    f"파일명에서 기간 자동 인식: {start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
                )
            else:
                st.caption(
                    "파일명에서 기간을 찾지 못해 최근 완료된 월요일~일요일을 기본값으로 표시합니다. "
                    "필요하면 시작일과 종료일을 수정하세요."
                )
            sp._caption_rendered = True

        if start > end:
            st.error("조회 시작일은 종료일보다 늦을 수 없습니다.")
            return

        rows = sp._existing_rows(core_obj, start, end)
        if not rows:
            return

        current_hash = _selected_file_hash()
        saved_hashes = {str(r["file_hash"] or "") for r in rows}
        replace_key = sp._replace_key(start, end)

        if current_hash and current_hash in saved_hashes:
            # A previous replacement confirmation must never leak into the next
            # genuinely different file selected for this same period.
            try:
                st.session_state[replace_key] = False
            except Exception:
                pass
            st.success(
                f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d} 현재 선택한 판매통계는 이미 정상 반영되어 있습니다. "
                "중복으로 다시 저장되지 않습니다."
            )
            return

        st.warning(
            f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d} 재고현황 판매통계가 이미 입력되어 있습니다. "
            "현재 선택한 파일이 다른 파일이라면 아래 항목을 체크해 교체하세요."
        )
        st.checkbox(
            "기존 기간 자료를 새 파일로 교체",
            key=replace_key,
        )

    sp._render_period_notice = render_period_notice
    sp._rg_samefile_notice_v09104_applied = True
    _APPLIED = True
    return sp
