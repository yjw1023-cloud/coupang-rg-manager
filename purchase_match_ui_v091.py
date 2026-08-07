"""RG Manager v0.9.1 purchase matching table layout.

This is a presentation patch on top of purchase_match_ui_v090.
The existing auto-match and import logic remain unchanged.
"""
from __future__ import annotations

from typing import Any

import purchase_match_ui_v090 as base

_APPLIED = False


def _product_title(product: dict[str, Any]) -> str:
    name = str(product.get("name") or "").strip()
    opt = str(product.get("option_name") or "").strip()
    if opt and opt != name:
        return f"{name} [{opt}]"
    return name


def _choice_label(product: dict[str, Any]) -> str:
    code = str(product.get("item_code") or "").strip()
    title = _product_title(product)
    return f"{code} · {title}" if code else title


def _render_review_table(st_obj, pd_obj, core_module, db_path, file_fp: str,
                         excel_rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    products = base._self_warehouse_products(core_module, db_path)
    if not products:
        st_obj.error("자체창고 매칭 후보 상품이 없습니다. 품목관리에서 자체창고 상품을 먼저 확인해 주세요.")
        return

    by_pid = {int(p["product_id"]): p for p in products}
    choice_to_pid = {_choice_label(p): int(p["product_id"]) for p in products}
    placeholder = "— 매칭상품 선택 —"
    options = [placeholder] + list(choice_to_pid.keys())

    overrides_all = st_obj.session_state.setdefault(base._OVERRIDE_KEY, {})
    overrides = overrides_all.setdefault(file_fp, {})
    meta_rows = meta.get("rows") or {}

    table_rows = []
    current_pids: dict[int, int | None] = {}

    for row in excel_rows:
        idx = int(row["index"])
        mr = meta_rows.get(str(idx), {})
        current_pid = mr.get("current_pid")
        if current_pid is not None:
            try:
                current_pid = int(current_pid)
            except Exception:
                current_pid = None
        if current_pid is None:
            current_pid = base._pid_from_display(mr.get("current_display", ""), products)

        selected_pid = overrides.get(str(idx), current_pid)
        try:
            selected_pid = int(selected_pid) if selected_pid is not None else None
        except Exception:
            selected_pid = current_pid
        current_pids[idx] = current_pid

        selected = by_pid.get(selected_pid)
        code = str(selected.get("item_code") or "") if selected else ""
        title = _product_title(selected) if selected else ""
        stock = base._fmt_qty(selected.get("own_stock")) if selected else ""
        choice = _choice_label(selected) if selected else placeholder

        status_base = str(mr.get("status") or "")
        rate = str(mr.get("match_rate") or "")
        if str(idx) in overrides and selected_pid != current_pid:
            status = "수동 변경"
        elif "확인" in status_base:
            status = "확인 필요"
        elif "자동" in status_base:
            status = f"자동 {rate}".strip()
        elif selected_pid:
            status = rate or "매칭됨"
        else:
            status = "미매칭"

        source_display = str(row.get("source_name") or "")
        if row.get("source_detail"):
            source_display += f" · {row['source_detail']}"

        table_rows.append({
            "No.": idx,
            "매입상품": source_display,
            "품목코드": code,
            "상품명": title,
            "현재재고": stock,
            "매입수량": base._fmt_qty(row["qty"]),
            "매입원가": base._fmt_money(row["unit_cost"]),
            "상태": status,
            "매칭 수정": choice,
        })

    auto_count = sum(1 for r in table_rows if str(r["상태"]).startswith("자동"))
    manual_count = sum(1 for r in table_rows if r["상태"] == "수동 변경")
    need_count = sum(1 for r in table_rows if r["상태"] in ("확인 필요", "미매칭"))
    c1, c2, c3 = st_obj.columns(3)
    c1.metric("자동매칭", f"{auto_count}개")
    c2.metric("수동수정", f"{manual_count}개")
    c3.metric("확인필요", f"{need_count}개")
    st_obj.caption(
        "매칭 결과를 품목코드·상품명·현재재고 칸으로 나눠 표시합니다. "
        "잘못 매칭된 행은 맨 오른쪽 '매칭 수정'에서 올바른 자체창고 상품을 선택하세요."
    )

    df = pd_obj.DataFrame(table_rows)
    editor_key = f"_rg_purchase_match_editor_v091_{file_fp[:16]}"

    try:
        column_config = {
            "No.": st_obj.column_config.NumberColumn("No.", width="small"),
            "매입상품": st_obj.column_config.TextColumn("매입상품", width="large"),
            "품목코드": st_obj.column_config.TextColumn("품목코드", width="small"),
            "상품명": st_obj.column_config.TextColumn("상품명", width="large"),
            "현재재고": st_obj.column_config.TextColumn("현재재고", width="small"),
            "매입수량": st_obj.column_config.TextColumn("매입수량", width="small"),
            "매입원가": st_obj.column_config.TextColumn("매입원가", width="small"),
            "상태": st_obj.column_config.TextColumn("상태", width="small"),
            "매칭 수정": st_obj.column_config.SelectboxColumn(
                "매칭 수정",
                options=options,
                required=True,
                width="large",
                help="잘못 매칭되었으면 이 칸에서 올바른 자체창고 상품을 선택하세요.",
            ),
        }
    except Exception:
        column_config = None

    editor_kwargs = dict(
        key=editor_key,
        hide_index=True,
        use_container_width=True,
        disabled=["No.", "매입상품", "품목코드", "상품명", "현재재고", "매입수량", "매입원가", "상태"],
        num_rows="fixed",
    )
    if column_config is not None:
        editor_kwargs["column_config"] = column_config

    edited = st_obj.data_editor(df, **editor_kwargs)

    unresolved_widget = False
    changed = False
    for _, er in edited.iterrows():
        idx = int(er["No."])
        choice = str(er.get("매칭 수정") or placeholder)
        pid = choice_to_pid.get(choice)
        if pid is None:
            continue

        current_pid = current_pids.get(idx)
        old_override = overrides.get(str(idx))
        if pid != current_pid:
            overrides[str(idx)] = pid
        else:
            overrides.pop(str(idx), None)
        new_override = overrides.get(str(idx))
        if old_override != new_override:
            changed = True

        mr = meta_rows.get(str(idx), {})
        target_pid = overrides.get(str(idx), current_pid)
        if target_pid is not None and not base._set_original_widget_choice(st_obj, mr, int(target_pid)):
            unresolved_widget = True

    overrides_all[file_fp] = overrides
    st_obj.session_state[base._OVERRIDE_KEY] = overrides_all

    if unresolved_widget:
        st_obj.warning("일부 오래된 매칭 위젯은 직접 연결키가 없어 수정값 반영을 확인할 수 없습니다. 프로그램을 최신 버전으로 다시 업데이트해 주세요.")
    elif overrides:
        st_obj.info("수동으로 바꾼 매칭은 아래 매입 확정 처리에 그대로 사용됩니다.")

    if changed and not unresolved_widget:
        try:
            st_obj.rerun()
        except Exception:
            pass


def apply():
    global _APPLIED
    if _APPLIED:
        return
    base._render_review_table = _render_review_table
    _APPLIED = True
