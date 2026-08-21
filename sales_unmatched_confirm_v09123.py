"""RG Manager v0.9.124: user-confirmed skip of unmatched sales rows.

When a Coupang sales-stat workbook contains option IDs that cannot be safely
matched to an ERP product, no sales data is written yet. The user sees the
unmatched rows and can either cancel or explicitly exclude those rows. On
confirmation, the exact rows are physically removed from an in-memory copy of
the uploaded workbook, then the existing sales import pipeline handles the
remaining workbook unchanged.
"""
from __future__ import annotations

from io import BytesIO
import re
from typing import Any

import pandas as pd
import streamlit as st
from openpyxl import load_workbook

_APPLIED = False
_CORE = None
_RD = None
_PREVIOUS_IMPORT = None
_DEFAULT_DB = None

_PENDING_KEY = "_rg_v09124_unmatched_sales_pending"
_FLASH_KEY = "_rg_v09124_unmatched_sales_flash"


def _oid(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    try:
        x = float(s)
        if x == x and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _source_bytes(source) -> bytes | None:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "getvalue"):
        try:
            return bytes(source.getvalue())
        except Exception:
            pass
    if hasattr(source, "read"):
        try:
            pos = source.tell() if hasattr(source, "tell") else None
            data = source.read()
            if pos is not None and hasattr(source, "seek"):
                source.seek(pos)
            return bytes(data)
        except Exception:
            pass
    return None


def _parse_unmatched_error(exc: Any, parsed: list[dict] | None = None) -> list[dict]:
    text = str(exc or "")
    signals = (
        "원상품을 안전하게 자동 매칭할 수 없습니다",
        "ERP에 없는 쿠팡 옵션ID",
        "품목관리에 없는 판매 옵션",
    )
    if not any(x in text for x in signals):
        return []

    parsed_by_oid = {
        _oid(r.get("option_id")): r
        for r in (parsed or [])
        if _oid(r.get("option_id"))
    }
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if "|" not in line:
            continue
        left, right = line.split("|", 1)
        oid = _oid(left)
        if not oid.isdigit():
            continue
        label = right.strip()
        reason = "원상품 자동 매칭 불가"
        if label.endswith(")") and " (" in label:
            label, tail = label.rsplit(" (", 1)
            reason = tail[:-1].strip() or reason

        src = parsed_by_oid.get(oid, {})
        qty = src.get("qty")
        try:
            qty = float(qty) if qty is not None else None
        except Exception:
            qty = None
        out.append(
            {
                "option_id": oid,
                "name": str(src.get("name") or label).strip(),
                "qty": qty,
                "reason": reason,
            }
        )
    return out


def _merge_rows(old, new):
    merged = {
        _oid(r.get("option_id")): dict(r)
        for r in old
        if _oid(r.get("option_id"))
    }
    for r in new:
        oid = _oid(r.get("option_id"))
        if oid:
            merged[oid] = dict(r)
    return list(merged.values())


def _header_key(value: Any) -> str:
    return re.sub(r"[\s_()\-]+", "", str(value or "")).lower()


def _filtered_workbook(raw: bytes, ignored_ids: set[str]) -> tuple[bytes, int]:
    """Return an xlsx copy with only the confirmed unmatched option rows removed."""
    if not raw:
        raise ValueError("업로드한 판매통계 파일을 다시 읽지 못했습니다.")
    if not ignored_ids:
        return raw, 0

    try:
        wb = load_workbook(BytesIO(raw))
    except Exception as exc:
        raise ValueError(f"판매통계 Excel을 다시 열지 못했습니다: {exc}") from exc

    ws = wb["판매통계"] if "판매통계" in wb.sheetnames else wb[wb.sheetnames[0]]
    header_row = None
    option_col = None

    scan_rows = min(max(int(ws.max_row or 1), 1), 15)
    for r in range(1, scan_rows + 1):
        for c in range(1, int(ws.max_column or 1) + 1):
            key = _header_key(ws.cell(r, c).value)
            if key == "옵션id" or key.endswith("옵션id"):
                header_row, option_col = r, c
                break
        if option_col is not None:
            break

    if header_row is None or option_col is None:
        raise ValueError("판매통계 시트에서 옵션ID 열을 찾지 못했습니다.")

    removed = 0
    for r in range(int(ws.max_row or 0), header_row, -1):
        if _oid(ws.cell(r, option_col).value) in ignored_ids:
            ws.delete_rows(r, 1)
            removed += 1

    if removed <= 0:
        raise ValueError(
            "제외 대상으로 확인한 옵션ID가 판매통계 시트에서 발견되지 않았습니다."
        )

    out = BytesIO()
    wb.save(out)
    return out.getvalue(), removed


def _pending(source, file_name, period_start, period_end, db_path, rows):
    raw = _source_bytes(source)
    if not raw:
        raise ValueError("업로드한 판매통계 파일을 다시 읽지 못했습니다.")
    return {
        "source": raw,
        "file_name": str(file_name or ""),
        "period_start": str(period_start or ""),
        "period_end": str(period_end or ""),
        "db_path": str(db_path or _DEFAULT_DB),
        "rows": rows,
    }


def _dialog_body():
    pending = st.session_state.get(_PENDING_KEY)
    if not pending:
        st.info("확인할 미매칭 판매 데이터가 없습니다.")
        return

    rows = list(pending.get("rows") or [])
    ids = {_oid(r.get("option_id")) for r in rows if _oid(r.get("option_id"))}

    st.warning(
        f"품목관리에 안전하게 매칭되지 않는 판매 옵션 {len(ids)}개가 있습니다."
    )
    st.write(
        "아래 항목을 버리고 나머지 판매통계만 입력할 수 있습니다. "
        "버린 항목은 이번 파일의 판매수량·재고·잠정손익에 반영되지 않습니다."
    )

    view = pd.DataFrame(
        [
            {
                "옵션ID": r.get("option_id", ""),
                "상품명": r.get("name", ""),
                "판매수량": r.get("qty", ""),
                "매칭 실패 이유": r.get("reason", ""),
            }
            for r in rows
        ]
    )
    st.dataframe(view, use_container_width=True, hide_index=True)
    st.caption(
        "이 판매를 ERP에 반영해야 하는 상품이라면 취소한 뒤 품목관리/반품매칭을 먼저 정리하세요."
    )

    c1, c2 = st.columns(2)
    confirm = c1.button(
        f"매칭 안 되는 {len(ids)}개 버리고 입력",
        type="primary",
        use_container_width=True,
        key="_rg_v09124_confirm",
    )
    cancel = c2.button(
        "취소",
        use_container_width=True,
        key="_rg_v09124_cancel",
    )

    if cancel:
        st.session_state.pop(_PENDING_KEY, None)
        st.rerun()

    if not confirm:
        return

    try:
        filtered, removed_rows = _filtered_workbook(
            bytes(pending["source"]), set(ids)
        )
    except Exception as exc:
        st.error(f"제외용 파일을 만들지 못했습니다. {exc}")
        return

    source = BytesIO(filtered)
    try:
        source.name = pending.get("file_name") or "sales.xlsx"
    except Exception:
        pass

    try:
        _PREVIOUS_IMPORT(
            source,
            pending.get("file_name", ""),
            pending.get("period_start", ""),
            pending.get("period_end", ""),
            pending.get("db_path") or _DEFAULT_DB,
        )
    except ValueError as exc:
        try:
            parsed = _RD._parse_sales_file(filtered)
        except Exception:
            parsed = []
        extra = _parse_unmatched_error(exc, parsed)
        if extra:
            pending["rows"] = _merge_rows(rows, extra)
            st.session_state[_PENDING_KEY] = pending
            st.error(
                "추가로 매칭되지 않는 옵션이 있습니다. 목록을 다시 확인해 주세요."
            )
            return
        st.error(f"자료를 반영하지 못했습니다. {exc}")
        return
    except Exception as exc:
        st.error(f"자료를 반영하지 못했습니다. {exc}")
        return

    st.session_state.pop(_PENDING_KEY, None)
    st.session_state[_FLASH_KEY] = (
        f"매칭되지 않는 옵션 {len(ids)}개({removed_rows}행)를 버리고 "
        "나머지 판매통계를 반영했습니다."
    )
    st.rerun()


def _make_dialog():
    dialog = getattr(st, "dialog", None)
    if not callable(dialog):
        return _dialog_body
    try:
        return dialog("매칭되지 않는 판매 데이터 확인", width="large")(_dialog_body)
    except TypeError:
        return dialog("매칭되지 않는 판매 데이터 확인")(_dialog_body)


_dialog = _make_dialog()


def apply(core, return_discount_module, db_path=None):
    global _APPLIED, _CORE, _RD, _PREVIOUS_IMPORT, _DEFAULT_DB

    _CORE = core
    _RD = return_discount_module
    _DEFAULT_DB = db_path or core.DEFAULT_DB

    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        try:
            st.toast(str(flash), icon="✅")
        except Exception:
            st.success(str(flash))

    if _APPLIED or getattr(
        core, "_rg_sales_unmatched_confirm_v09124_applied", False
    ):
        return core

    previous_import = core.import_sales_stats
    _PREVIOUS_IMPORT = previous_import

    def import_sales_stats(
        source, file_name, period_start, period_end, db_path=None
    ):
        target = db_path or _DEFAULT_DB

        # Preflight only. No sales/inventory/P&L data is written before the user
        # decides whether unmatched rows may be discarded.
        try:
            parsed = return_discount_module._parse_sales_file(source)
        except Exception:
            parsed = []

        if parsed:
            try:
                return_discount_module._resolve(core, target, parsed)
            except ValueError as exc:
                rows = _parse_unmatched_error(exc, parsed)
                if rows:
                    st.session_state[_PENDING_KEY] = _pending(
                        source,
                        file_name,
                        period_start,
                        period_end,
                        target,
                        rows,
                    )
                    _dialog()
                    st.stop()

        try:
            return previous_import(
                source, file_name, period_start, period_end, target
            )
        except ValueError as exc:
            rows = _parse_unmatched_error(exc, parsed)
            if not rows:
                raise
            st.session_state[_PENDING_KEY] = _pending(
                source,
                file_name,
                period_start,
                period_end,
                target,
                rows,
            )
            _dialog()
            st.stop()

    core.import_sales_stats = import_sales_stats
    core._rg_sales_unmatched_confirm_v09124_applied = True
    _APPLIED = True
    return core
