"""v0.9.203 inline editor + Excel bulk input for monthly provisional P&L.

Keeps the v0.9.199 direct-edit workflow and adds a safer bulk workflow for many
products. Operators can download the currently displayed rows to Excel, fill only
the change columns, upload the workbook, review validation results, and apply all
valid monthly overrides with a single rerun/recalculation.

Rules:
- only `평균 수수료` and `평균 입출고배송비` are editable;
- direct cell editing still saves after commit for quick one-off corrections;
- Excel matching always uses `옵션ID`, never row order or product name;
- blank Excel change cells mean "keep the existing override"; numeric 0 means
  explicitly apply 0 won;
- uploaded workbooks must match the currently selected month;
- all rows are validated before any Excel changes are written;
- percentage columns render with one decimal place and a `%` sign;
- all other numeric columns render as whole numbers with locale thousands
  separators; no decimal tails are shown;
- every numeric column (including the two editable columns) is center-aligned;
- option ID is center-aligned, product name remains left-aligned;
- confirmed settlement rows are never edited.
"""
from __future__ import annotations

import io
import math
from typing import Any

import pandas as pd

RULE_VERSION = "0.9.203-inline-unit-cost-excel-bulk"
_EDITABLE = ("평균 수수료", "평균 입출고배송비")
_PERCENT_COLS = {"반품률", "이익률(%)"}
_ID_COLS = {"옵션ID"}
_TEXT_COLS = {"상품명"}
_BULK_SHEET = "잠정손익_입력"
_BULK_MONTH = "조회월"
_BULK_COMM = "변경 평균 수수료"
_BULK_LOGI = "변경 평균 입출고배송비"


def _oid(v: Any) -> str:
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _nullable(v: Any):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, str) and not v.strip():
        return None
    try:
        x = float(str(v).replace(",", "").replace("%", "").strip())
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _same(a, b) -> bool:
    a, b = _nullable(a), _nullable(b)
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= 1e-9


def _prepare_numeric_display(show: pd.DataFrame, month_ui) -> pd.DataFrame:
    """Round display values without changing the calculation dataframe."""
    out = show.copy()
    numeric_cols = set(getattr(month_ui, "_NUMERIC_COLS", set())) | set(_EDITABLE) | _PERCENT_COLS
    for col in out.columns:
        if col in _ID_COLS or col in _TEXT_COLS:
            continue
        if col not in numeric_cols and not pd.api.types.is_numeric_dtype(out[col]):
            continue
        vals = pd.to_numeric(out[col], errors="coerce")
        if col in _PERCENT_COLS:
            out[col] = vals.round(1)
        else:
            out[col] = vals.round(0)
    return out


def _number_column(st_obj, label: str, editable: bool = False, percent: bool = False):
    kwargs = {
        "label": label,
        "alignment": "center",
    }
    if percent:
        kwargs["format"] = "%.1f%%"
    else:
        kwargs["format"] = "localized"
    if editable:
        kwargs.update({
            "help": "원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용",
            "min_value": 0.0,
            "step": 10.0,
        })
    return st_obj.column_config.NumberColumn(**kwargs)


def _month_text(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    try:
        year = int(getattr(v, "year"))
        month = int(getattr(v, "month"))
        if 1 <= month <= 12:
            return f"{year:04d}-{month:02d}"
    except Exception:
        pass
    s = str(v).strip()
    if len(s) >= 7 and s[4] in ("-", "/", "."):
        try:
            return f"{int(s[:4]):04d}-{int(s[5:7]):02d}"
        except Exception:
            pass
    return s[:7]


def _bulk_template_bytes(show: pd.DataFrame, month: str) -> bytes:
    def whole(v):
        x = _nullable(v)
        return int(round(float(x))) if x is not None else 0

    rows = []
    for _, r in show.iterrows():
        oid = _oid(r.get("옵션ID"))
        if not oid:
            continue
        rows.append({
            _BULK_MONTH: str(month),
            "옵션ID": oid,
            "상품명": str(r.get("상품명") or ""),
            "현재 평균 수수료": whole(r.get("평균 수수료")),
            _BULK_COMM: None,
            "현재 평균 입출고배송비": whole(r.get("평균 입출고배송비")),
            _BULK_LOGI: None,
        })
    template = pd.DataFrame(rows)
    guide = pd.DataFrame(
        [
            ["입력 방법", "'변경 평균 수수료'와 '변경 평균 입출고배송비' 중 필요한 칸만 입력합니다."],
            ["빈칸", "기존 값을 그대로 유지합니다. 빈칸은 0원이 아닙니다."],
            ["0 입력", "해당 평균비용을 실제 0원으로 수동 적용합니다."],
            ["매칭 기준", "옵션ID만 사용합니다. 행 순서와 상품명은 매칭에 사용하지 않습니다."],
            ["주의", "조회월과 옵션ID는 수정하지 마세요. 다른 달 양식은 업로드가 차단됩니다."],
        ],
        columns=["항목", "설명"],
    )

    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        template.to_excel(writer, sheet_name=_BULK_SHEET, index=False)
        guide.to_excel(writer, sheet_name="사용방법", index=False)
        ws = writer.sheets[_BULK_SHEET]
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        widths = {
            "A": 12,
            "B": 18,
            "C": 54,
            "D": 20,
            "E": 20,
            "F": 27,
            "G": 27,
        }
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
        for cell in ws["A"][1:]:
            cell.number_format = "@"
        for cell in ws["B"][1:]:
            cell.number_format = "@"
        for col in ("D", "E", "F", "G"):
            for cell in ws[col][1:]:
                cell.number_format = '#,##0'

        help_ws = writer.sheets["사용방법"]
        help_ws.column_dimensions["A"].width = 18
        help_ws.column_dimensions["B"].width = 86
        help_ws.freeze_panes = "A2"
    return out.getvalue()


def _optional_amount(v: Any):
    """Return (present, parsed_value, error_message). Blank and zero differ."""
    if v is None:
        return False, None, None
    try:
        if pd.isna(v):
            return False, None, None
    except Exception:
        pass
    if isinstance(v, str) and not v.strip():
        return False, None, None
    try:
        x = float(str(v).replace(",", "").replace("원", "").strip())
    except Exception:
        return True, None, "숫자가 아닙니다"
    if not math.isfinite(x):
        return True, None, "유효한 숫자가 아닙니다"
    if x < 0:
        return True, None, "0원 이상만 입력할 수 있습니다"
    return True, float(x), None


def _read_bulk_workbook(uploaded, month: str, basis, core, active_db):
    try:
        raw = uploaded.getvalue() if hasattr(uploaded, "getvalue") else uploaded.read()
        df = pd.read_excel(io.BytesIO(raw), sheet_name=_BULK_SHEET, dtype={"옵션ID": str})
    except ValueError as exc:
        return [], [{"행": "-", "옵션ID": "", "오류": f"'{_BULK_SHEET}' 시트를 찾을 수 없습니다: {exc}"}]
    except Exception as exc:
        return [], [{"행": "-", "옵션ID": "", "오류": f"Excel 파일을 읽을 수 없습니다: {exc}"}]

    df.columns = [str(c).strip() for c in df.columns]
    required = {_BULK_MONTH, "옵션ID", _BULK_COMM, _BULK_LOGI}
    missing = [c for c in required if c not in df.columns]
    if missing:
        return [], [{"행": "-", "옵션ID": "", "오류": "필수 열이 없습니다: " + ", ".join(missing)}]

    errors = []
    changes = []
    seen = set()
    active_month = str(month)

    for idx, row in df.iterrows():
        excel_row = int(idx) + 2
        oid = _oid(row.get("옵션ID"))
        comm_present, comm_value, comm_error = _optional_amount(row.get(_BULK_COMM))
        logi_present, logi_value, logi_error = _optional_amount(row.get(_BULK_LOGI))

        if not comm_present and not logi_present:
            continue

        row_month = _month_text(row.get(_BULK_MONTH))
        if row_month != active_month:
            errors.append({
                "행": excel_row,
                "옵션ID": oid,
                "오류": f"조회월 불일치 ({row_month or '빈칸'} → 현재 {active_month})",
            })
            continue
        if not oid:
            errors.append({"행": excel_row, "옵션ID": "", "오류": "옵션ID가 비어 있습니다"})
            continue
        if oid in seen:
            errors.append({"행": excel_row, "옵션ID": oid, "오류": "같은 옵션ID가 두 번 이상 입력되었습니다"})
            continue
        seen.add(oid)
        if comm_error:
            errors.append({"행": excel_row, "옵션ID": oid, "오류": f"{_BULK_COMM}: {comm_error}"})
            continue
        if logi_error:
            errors.append({"행": excel_row, "옵션ID": oid, "오류": f"{_BULK_LOGI}: {logi_error}"})
            continue
        if basis._pid(core, active_db, oid) is None:
            errors.append({"행": excel_row, "옵션ID": oid, "오류": "ERP 상품과 매칭되지 않는 옵션ID입니다"})
            continue

        changes.append({
            "row": excel_row,
            "oid": oid,
            "name": str(row.get("상품명") or ""),
            "comm_present": bool(comm_present),
            "comm": comm_value,
            "logi_present": bool(logi_present),
            "logi": logi_value,
        })

    return changes, errors


def _apply_bulk_changes(basis, core, active_db, month: str, changes) -> int:
    current = basis._manual(core, active_db, month)
    saved_count = 0
    for ch in changes:
        oid = ch["oid"]
        saved = current.get(oid, {})
        old_comm = basis._nullable(saved.get("commission_unit_override"))
        old_logi = basis._nullable(saved.get("logistics_unit_override"))
        new_comm = ch["comm"] if ch["comm_present"] else old_comm
        new_logi = ch["logi"] if ch["logi_present"] else old_logi
        if _same(old_comm, new_comm) and _same(old_logi, new_logi):
            continue
        basis._save(core, active_db, month, oid, new_comm, new_logi)
        current[oid] = {
            "commission_unit_override": new_comm,
            "logistics_unit_override": new_logi,
        }
        saved_count += 1
    return saved_count


def _render_bulk_excel_controls(st_obj, basis, core, active_db, month: str, baseline: pd.DataFrame):
    message_key = f"_rg203_bulk_message_{month}"
    pending = st_obj.session_state.pop(message_key, None)
    if pending:
        try:
            st_obj.toast(str(pending))
        except Exception:
            st_obj.success(str(pending))

    with st_obj.expander("📥 평균비용 Excel 일괄입력", expanded=False):
        st_obj.caption(
            "현재 표를 Excel로 내려받아 '변경' 열만 입력한 뒤 업로드하세요. "
            "옵션ID로 매칭하며 빈칸은 기존값 유지, 0은 0원 적용입니다. "
            "검증 후 한 번에 저장하므로 여러 상품을 입력해도 재계산은 마지막에 한 번만 합니다."
        )
        try:
            template_bytes = _bulk_template_bytes(baseline, month)
        except Exception as exc:
            st_obj.error(f"Excel 양식을 만들 수 없습니다: {exc}")
            return

        left, right = st_obj.columns(2)
        left.download_button(
            "Excel 양식 다운로드",
            data=template_bytes,
            file_name=f"잠정손익_평균비용_{month}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"rg203_bulk_download_{month}",
            use_container_width=True,
        )

        upload_nonce_key = f"_rg203_bulk_upload_nonce_{month}"
        upload_nonce = int(st_obj.session_state.get(upload_nonce_key, 0) or 0)
        uploaded = right.file_uploader(
            "작성한 Excel 업로드",
            type=["xlsx"],
            key=f"rg203_bulk_upload_{month}_{upload_nonce}",
            help="이 화면에서 내려받은 .xlsx 양식을 사용하세요.",
        )
        if uploaded is None:
            return

        changes, errors = _read_bulk_workbook(uploaded, month, basis, core, active_db)
        st_obj.caption(
            f"검증 결과 · 적용 후보 {len(changes):,}건 · 오류/미매칭 {len(errors):,}건"
        )
        if errors:
            st_obj.error("오류가 있는 상태에서는 DB를 변경하지 않습니다. 아래 행을 수정한 뒤 다시 업로드하세요.")
            st_obj.dataframe(pd.DataFrame(errors), use_container_width=True, hide_index=True)
            return
        if not changes:
            st_obj.info("'변경' 열에 입력된 값이 없습니다.")
            return

        preview = pd.DataFrame([
            {
                "옵션ID": ch["oid"],
                "상품명": ch["name"],
                "평균 수수료 변경": (
                    int(round(ch["comm"])) if ch["comm_present"] else "유지"
                ),
                "평균 입출고배송비 변경": (
                    int(round(ch["logi"])) if ch["logi_present"] else "유지"
                ),
            }
            for ch in changes
        ])
        st_obj.dataframe(preview, use_container_width=True, hide_index=True, height=min(420, 36 * (len(preview) + 1)))

        if st_obj.button(
            f"검증된 {len(changes):,}건 일괄 적용",
            type="primary",
            key=f"rg203_bulk_apply_{month}_{upload_nonce}",
            use_container_width=True,
        ):
            try:
                saved_count = _apply_bulk_changes(basis, core, active_db, month, changes)
            except Exception as exc:
                st_obj.error(f"일괄 저장 중 오류가 발생했습니다. DB를 확인해 주세요: {exc}")
                return

            st_obj.session_state[upload_nonce_key] = upload_nonce + 1
            inline_nonce_key = f"_rg198_inline_nonce_{month}"
            st_obj.session_state[inline_nonce_key] = int(st_obj.session_state.get(inline_nonce_key, 0) or 0) + 1
            st_obj.session_state[message_key] = (
                f"Excel 일괄입력으로 {saved_count:,}개 상품의 잠정비용을 저장했습니다."
                if saved_count
                else "업로드 값이 현재 수동값과 같아 변경된 상품이 없습니다."
            )
            st_obj.rerun()


def apply(core, db_path=None):
    import provisional_sales_basis_v09196 as basis
    import pnl_month_sales_basis_v09197 as bridge
    import pnl_month_v0961 as month_ui

    def remember_context(st_obj, basis_arg, core_arg, db, month: str, data):
        month_ui._rg198_active_month = str(month)
        month_ui._rg198_active_db = db or db_path or core_arg.DEFAULT_DB
        return None

    bridge.render_month_editor = remember_context
    bridge._rg198_inline_editor = RULE_VERSION

    if not hasattr(month_ui, "_rg198_original_render_table"):
        month_ui._rg198_original_render_table = month_ui._render_table
    original_render = month_ui._rg198_original_render_table

    def render_table(st_obj, df):
        if df is None or getattr(df, "empty", True):
            return original_render(st_obj, df)

        month = str(getattr(month_ui, "_rg198_active_month", "") or "")
        active_db = getattr(month_ui, "_rg198_active_db", None) or db_path or core.DEFAULT_DB
        if not month or "옵션ID" not in df.columns or not set(_EDITABLE).issubset(df.columns):
            return original_render(st_obj, df)

        show = df.copy()
        try:
            cols = month_ui._ordered_columns(show)
            show = show[[c for c in cols if c in show.columns]]
        except Exception:
            pass
        show = _prepare_numeric_display(show.reset_index(drop=True), month_ui)
        baseline = show.copy()

        st_obj.caption(
            "평균 수수료와 평균 입출고배송비 칸을 직접 클릭해 입력하거나, "
            "아래 Excel 일괄입력으로 여러 상품을 한 번에 반영하세요. "
            "반품률·이익률은 %로, 금액은 소수점 없이 천 단위 콤마로 표시합니다."
        )

        _render_bulk_excel_controls(st_obj, basis, core, active_db, month, baseline)

        disabled = [c for c in show.columns if c not in _EDITABLE]
        column_config = {}
        for col in show.columns:
            try:
                if col == "상품명":
                    column_config[col] = st_obj.column_config.TextColumn(
                        "상품명", width="large", alignment="left"
                    )
                elif col == "옵션ID":
                    column_config[col] = st_obj.column_config.TextColumn(
                        "옵션ID", alignment="center"
                    )
                elif col in _PERCENT_COLS:
                    column_config[col] = _number_column(st_obj, col, percent=True)
                elif col in _EDITABLE:
                    help_text = (
                        "입출고비+배송비 합계, 원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용"
                        if col == "평균 입출고배송비"
                        else "원/개 · 직접 입력 시 이 달 잠정손익에만 우선 적용"
                    )
                    column_config[col] = st_obj.column_config.NumberColumn(
                        col,
                        help=help_text,
                        min_value=0.0,
                        step=10.0,
                        format="localized",
                        alignment="center",
                    )
                elif pd.api.types.is_numeric_dtype(show[col]) or col in getattr(month_ui, "_NUMERIC_COLS", set()):
                    column_config[col] = _number_column(st_obj, col)
                else:
                    column_config[col] = st_obj.column_config.TextColumn(col, alignment="center")
            except TypeError:
                if col in _PERCENT_COLS:
                    column_config[col] = st_obj.column_config.NumberColumn(col, format="%.1f%%")
                elif col in _EDITABLE:
                    column_config[col] = st_obj.column_config.NumberColumn(
                        col, min_value=0.0, step=10.0, format="localized"
                    )
                elif pd.api.types.is_numeric_dtype(show[col]) or col in getattr(month_ui, "_NUMERIC_COLS", set()):
                    column_config[col] = st_obj.column_config.NumberColumn(col, format="localized")

        nonce_key = f"_rg198_inline_nonce_{month}"
        nonce = int(st_obj.session_state.get(nonce_key, 0) or 0)
        widget_key = f"rg198_inline_pnl_{month}_{nonce}"
        height = min(850, max(250, 36 * (len(show) + 1)))

        edited = st_obj.data_editor(
            show,
            key=widget_key,
            use_container_width=True,
            hide_index=True,
            disabled=disabled,
            column_config=column_config,
            height=height,
        )

        current = basis._manual(core, active_db, month)
        saved_count = 0
        for pos in range(min(len(baseline), len(edited))):
            oid = _oid(baseline.at[pos, "옵션ID"])
            if not oid:
                continue
            saved = current.get(oid, {})
            old_comm_override = basis._nullable(saved.get("commission_unit_override"))
            old_logi_override = basis._nullable(saved.get("logistics_unit_override"))
            new_comm_override = old_comm_override
            new_logi_override = old_logi_override
            changed = False

            base_comm = _nullable(baseline.at[pos, "평균 수수료"])
            edit_comm = _nullable(edited.at[pos, "평균 수수료"])
            if not _same(base_comm, edit_comm):
                new_comm_override = None if edit_comm is None else max(0.0, float(edit_comm))
                changed = True

            base_logi = _nullable(baseline.at[pos, "평균 입출고배송비"])
            edit_logi = _nullable(edited.at[pos, "평균 입출고배송비"])
            if not _same(base_logi, edit_logi):
                new_logi_override = None if edit_logi is None else max(0.0, float(edit_logi))
                changed = True

            if changed:
                basis._save(
                    core, active_db, month, oid,
                    new_comm_override, new_logi_override,
                )
                saved_count += 1

        if saved_count:
            st_obj.session_state[nonce_key] = nonce + 1
            try:
                st_obj.toast(f"{saved_count:,}개 상품의 잠정비용을 저장했습니다.")
            except Exception:
                pass
            st_obj.rerun()

    month_ui._render_table = render_table
    month_ui._rg198_inline_editor = RULE_VERSION
    core._rg_pnl_inline_editor_v09198 = RULE_VERSION
    return {"ok": True, "rule_version": RULE_VERSION}
