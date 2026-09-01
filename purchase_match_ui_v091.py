"""RG Manager purchase matching table layout + new-item registration.

v0.9.132 restores a workflow that the v0.9.1 compact matching table accidentally
hid: when a purchase row has no self-warehouse match, the operator can explicitly
confirm that it is a new item. The ERP then creates a raw/self-warehouse item with
an automatically assigned JDS#### code and immediately connects the purchase row
back to that new item before the normal purchase-confirmation pipeline continues.
"""
from __future__ import annotations

import re
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


def _unused_jds_codes(core_module, db_path, count: int) -> list[str]:
    """Return the first unused JDS#### codes; archived codes remain reserved."""
    core_module.init_db(db_path)
    used_numbers: set[int] = set()
    used_codes: set[str] = set()
    with core_module._conn(db_path) as con:
        rows = con.execute("SELECT item_code FROM products WHERE item_code IS NOT NULL").fetchall()
    for row in rows:
        code = str(row["item_code"] or "").strip()
        if not code:
            continue
        used_codes.add(code.upper())
        m = re.fullmatch(r"JDS(\d+)", code, flags=re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 9999:
                used_numbers.add(n)

    out: list[str] = []
    for n in range(1, 10000):
        code = f"JDS{n:04d}"
        if n in used_numbers or code.upper() in used_codes:
            continue
        out.append(code)
        if len(out) >= count:
            return out
    raise RuntimeError("JDS0001~JDS9999 품목코드를 모두 사용 중입니다.")


def _unresolved_groups(excel_rows, current_pids, overrides):
    """Group currently-unmatched rows by source name/detail so duplicates create once."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for row in excel_rows:
        idx = int(row["index"])
        target_pid = overrides.get(str(idx), current_pids.get(idx))
        try:
            target_pid = int(target_pid) if target_pid is not None else None
        except Exception:
            target_pid = None
        if target_pid is not None:
            continue

        source_name = str(row.get("source_name") or "").strip()
        source_detail = str(row.get("source_detail") or "").strip()
        key = (source_name, source_detail)
        g = groups.setdefault(
            key,
            {
                "source_name": source_name,
                "source_detail": source_detail,
                "indices": [],
                "qty": 0.0,
                "amount": 0.0,
            },
        )
        q = base._num(row.get("qty"))
        u = base._num(row.get("unit_cost"))
        g["indices"].append(idx)
        g["qty"] += q
        g["amount"] += q * u

    out = []
    for key, g in groups.items():
        qty = float(g["qty"] or 0)
        g["avg_unit_cost"] = float(g["amount"] or 0) / qty if qty else 0.0
        g["key"] = key
        out.append(g)
    return out


def _create_new_products(core_module, db_path, selected_groups):
    """Create all selected raw items in one DB transaction and return key -> product info."""
    if not selected_groups:
        return {}
    codes = _unused_jds_codes(core_module, db_path, len(selected_groups))
    created: dict[tuple[str, str], dict[str, Any]] = {}
    core_module.init_db(db_path)
    with core_module._conn(db_path) as con:
        for g, code in zip(selected_groups, codes):
            name = str(g.get("new_name") or "").strip()
            if not name:
                raise ValueError("신규 품목명이 비어 있는 항목이 있습니다.")
            if con.execute("SELECT 1 FROM products WHERE item_code=?", (code,)).fetchone():
                raise ValueError(f"자동 품목코드가 이미 사용 중입니다: {code}")
            cur = con.execute(
                """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
                   VALUES(?,NULL,?,'raw',?,1,?)""",
                (code, name, float(g.get("avg_unit_cost") or 0), core_module.now_iso()),
            )
            created[g["key"]] = {
                "product_id": int(cur.lastrowid),
                "item_code": code,
                "name": name,
            }
    return created


def _render_new_item_registration(
    st_obj,
    pd_obj,
    core_module,
    db_path,
    file_fp,
    excel_rows,
    meta_rows,
    current_pids,
    overrides_all,
    overrides,
):
    groups = _unresolved_groups(excel_rows, current_pids, overrides)
    if not groups:
        return False

    st_obj.markdown("### 신규 아이템 확인")
    st_obj.warning(
        f"기존 자체창고 품목과 매칭되지 않은 매입상품이 {len(groups):,}개 있습니다. "
        "기존 품목이면 위 '매칭 수정'에서 선택하고, 정말 신규 품목이면 아래에서 신규등록을 확인하세요."
    )
    st_obj.caption(
        "신규등록하면 JDS0001 형식의 첫 미사용 품목코드를 자동 부여하고 자체창고 기초상품(raw)으로 등록합니다. "
        "같은 매입상품명+상세정보가 여러 행이면 한 품목으로 묶어 등록합니다."
    )

    select_all = st_obj.checkbox(
        "현재 미매칭 항목을 모두 신규 품목으로 선택",
        value=False,
        key=f"_rg_purchase_new_all_v09132_{file_fp[:16]}",
    )
    proposed = _unused_jds_codes(core_module, db_path, len(groups))
    rows = []
    group_by_no: dict[int, dict[str, Any]] = {}
    for no, (g, code) in enumerate(zip(groups, proposed), start=1):
        source_display = g["source_name"]
        if g["source_detail"]:
            source_display += f" · {g['source_detail']}"
        rows.append(
            {
                "신규등록": bool(select_all),
                "No.": no,
                "매입상품": source_display,
                "신규 품목명": g["source_name"] or source_display,
                "자동 품목코드": code,
                "매입수량": base._fmt_qty(g["qty"]),
                "평균매입원가": base._fmt_money(g["avg_unit_cost"]),
                "원본행": ", ".join(str(x) for x in g["indices"]),
            }
        )
        group_by_no[no] = g

    new_df = pd_obj.DataFrame(rows)
    key_suffix = "all" if select_all else "manual"
    editor_key = f"_rg_purchase_new_editor_v09132_{file_fp[:16]}_{key_suffix}"
    try:
        config = {
            "신규등록": st_obj.column_config.CheckboxColumn(
                "신규등록",
                help="기존 품목이 아니라 새 자체창고 기초상품일 때만 체크하세요.",
                width="small",
            ),
            "No.": st_obj.column_config.NumberColumn("No.", width="small"),
            "매입상품": st_obj.column_config.TextColumn("매입상품", width="large"),
            "신규 품목명": st_obj.column_config.TextColumn(
                "신규 품목명",
                help="등록될 ERP 품목명입니다. 필요하면 여기서 수정하세요.",
                width="large",
            ),
            "자동 품목코드": st_obj.column_config.TextColumn("자동 품목코드", width="small"),
            "매입수량": st_obj.column_config.TextColumn("매입수량", width="small"),
            "평균매입원가": st_obj.column_config.TextColumn("평균매입원가", width="small"),
            "원본행": st_obj.column_config.TextColumn("원본행", width="small"),
        }
    except Exception:
        config = None

    kwargs = dict(
        key=editor_key,
        hide_index=True,
        use_container_width=True,
        num_rows="fixed",
        disabled=["No.", "매입상품", "자동 품목코드", "매입수량", "평균매입원가", "원본행"],
    )
    if config is not None:
        kwargs["column_config"] = config
    edited = st_obj.data_editor(new_df, **kwargs)

    selected_groups = []
    for _, row in edited.iterrows():
        if not bool(row.get("신규등록")):
            continue
        no = int(row["No."])
        g = dict(group_by_no[no])
        g["new_name"] = str(row.get("신규 품목명") or "").strip()
        selected_groups.append(g)

    if not selected_groups:
        st_obj.info("신규 품목으로 등록할 항목을 체크하거나, 기존 품목이면 위 표에서 매칭해 주세요.")
        return False

    confirm = st_obj.checkbox(
        f"선택한 {len(selected_groups):,}개가 기존 품목이 아닌 신규 아이템임을 확인했습니다.",
        key=f"_rg_purchase_new_confirm_v09132_{file_fp[:16]}",
    )
    if not st_obj.button(
        f"선택한 {len(selected_groups):,}개 신규등록 + 매입매칭",
        type="primary",
        disabled=not confirm,
        key=f"_rg_purchase_new_submit_v09132_{file_fp[:16]}",
    ):
        return False

    try:
        created = _create_new_products(core_module, db_path, selected_groups)
        for g in selected_groups:
            info = created[g["key"]]
            pid = int(info["product_id"])
            for idx in g["indices"]:
                overrides[str(int(idx))] = pid
                mr = meta_rows.get(str(int(idx)), {})
                # The old widget's option list was built before creation. Setting
                # state here is best-effort; after rerun the new product is in the
                # option list and the normal matching loop confirms the selection.
                try:
                    base._set_original_widget_choice(st_obj, mr, pid)
                except Exception:
                    pass

        overrides_all[file_fp] = overrides
        st_obj.session_state[base._OVERRIDE_KEY] = overrides_all
        labels = [f"{x['item_code']} {x['name']}" for x in created.values()]
        st_obj.success("신규 품목을 등록하고 매입행에 연결했습니다: " + " / ".join(labels))
        try:
            st_obj.rerun()
        except Exception:
            pass
        return True
    except Exception as exc:
        st_obj.error(f"신규 품목 자동등록에 실패했습니다: {exc}")
        return False


def _render_review_table(st_obj, pd_obj, core_module, db_path, file_fp: str,
                         excel_rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
    products = base._self_warehouse_products(core_module, db_path)

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
        "기존 품목이면 오른쪽 '매칭 수정'에서 선택합니다. 기존 품목이 없는 신규 아이템은 "
        "표 아래 '신규 아이템 확인'에서 JDS 코드 자동등록 후 바로 매입매칭할 수 있습니다."
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
                help="기존 자체창고 품목이면 여기서 선택하세요. 신규 품목은 아래 신규등록 영역을 사용합니다.",
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

    # Do not immediately rerun when unresolved rows remain; the operator needs the
    # new-item confirmation controls rendered below the table first.
    groups_before = _unresolved_groups(excel_rows, current_pids, overrides)
    if changed and not unresolved_widget and not groups_before:
        try:
            st_obj.rerun()
        except Exception:
            pass

    _render_new_item_registration(
        st_obj,
        pd_obj,
        core_module,
        db_path,
        file_fp,
        excel_rows,
        meta_rows,
        current_pids,
        overrides_all,
        overrides,
    )


def apply():
    global _APPLIED
    if _APPLIED:
        return
    base._render_review_table = _render_review_table
    _APPLIED = True
