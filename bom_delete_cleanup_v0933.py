"""RG Manager v0.9.34 BOM cleanup list UI.

Purpose: remove obsolete finished-product BOMs from the current BOM list.
All BOM finished products are shown as a selectable table instead of a dropdown.
The existing v0.9.28 delete engine/logging is kept; only the UI is replaced.
"""
from __future__ import annotations

import pandas as pd


def render_delete_ui(st_obj, core_module, target_module, db_path=None) -> None:
    st_obj.markdown("### BOM 삭제")
    st_obj.caption(
        "더 이상 생산·판매하지 않는 완제품의 현재 BOM을 정리하는 화면입니다. "
        "아래 목록에서 완제품 BOM을 선택해 삭제하세요. "
        "BOM을 삭제해도 과거 생산수량·생산원가·재고 차감 이력은 그대로 유지됩니다."
    )

    rows = target_module._bom_rows(core_module, db_path)
    if not rows:
        st_obj.info("현재 등록된 BOM이 없습니다.")
        return

    # One representative row per finished product.
    parents = {}
    component_counts = {}
    for row in rows:
        pid = int(row["parent_product_id"])
        if pid not in parents:
            parents[pid] = row
        component_counts[pid] = component_counts.get(pid, 0) + 1

    ordered = sorted(
        parents.items(),
        key=lambda kv: (
            0 if int(kv[1].get("parent_active") or 0) != 1 else 1,
            str(kv[1].get("parent_name") or "").lower(),
            str(kv[1].get("parent_code") or "").lower(),
        ),
    )

    q = st_obj.text_input(
        "BOM 검색",
        placeholder="완제품명 또는 품목코드 입력",
        key="bom_delete_search_v0934",
    ).strip().lower()

    if q:
        filtered = []
        for pid, row in ordered:
            haystack = " ".join(
                [
                    str(row.get("parent_name") or ""),
                    str(row.get("parent_code") or ""),
                    str(row.get("parent_option_id") or ""),
                    target_module._parent_label(row),
                ]
            ).lower()
            if q in haystack:
                filtered.append((pid, row))
    else:
        filtered = ordered

    if not filtered:
        st_obj.info("검색 조건에 맞는 BOM 완제품이 없습니다.")
        return

    inactive_count = sum(1 for _, row in ordered if int(row.get("parent_active") or 0) != 1)
    if inactive_count:
        st_obj.caption(
            f"보관/판매중지 완제품 {inactive_count:,}개를 목록 위쪽에 먼저 표시합니다."
        )

    list_df = pd.DataFrame(
        [
            {
                "선택": False,
                "상태": "보관/판매중지" if int(row.get("parent_active") or 0) != 1 else "사용중",
                "완제품": str(row.get("parent_name") or ""),
                "품목코드": target_module._display_code(
                    row.get("parent_code"), row.get("parent_option_id")
                ),
                "구성품수": int(component_counts.get(int(pid), 0)),
                "_parent_id": int(pid),
            }
            for pid, row in filtered
        ]
    )

    st_obj.markdown("#### 등록된 BOM 목록")
    edited = st_obj.data_editor(
        list_df,
        use_container_width=True,
        hide_index=True,
        disabled=["상태", "완제품", "품목코드", "구성품수", "_parent_id"],
        column_order=["선택", "상태", "완제품", "품목코드", "구성품수"],
        key=f"bom_delete_list_v0934_{len(filtered)}_{abs(hash(q))}",
    )

    selected = edited[edited["선택"] == True]  # noqa: E712
    if selected.empty:
        st_obj.info("삭제할 BOM의 왼쪽 '선택' 체크박스를 선택해 주세요.")
        return
    if len(selected) > 1:
        st_obj.warning("실수 방지를 위해 BOM은 한 번에 1개만 삭제할 수 있습니다. 하나만 선택해 주세요.")
        return

    parent_id = int(selected.iloc[0]["_parent_id"])
    selected_parent = parents[parent_id]
    selected_rows = [
        r for r in rows if int(r["parent_product_id"]) == parent_id
    ]
    parent_label = target_module._parent_label(selected_parent)
    is_inactive = int(selected_parent.get("parent_active") or 0) != 1

    if is_inactive:
        st_obj.info(f"선택: {parent_label} · 현재 보관/판매중지 품목")
    else:
        st_obj.warning(
            f"선택: {parent_label} · 현재 사용중 품목입니다. "
            "BOM을 삭제하면 이후 생산 처리에서 사용할 구성표가 없어집니다."
        )

    show = pd.DataFrame(
        [
            {
                "구성품": target_module._component_label(r),
                "소요량": float(r["qty_per"] or 0),
                "구성품원가": float(r["component_cost"] or 0),
                "완제품 1개당 원가": float(r["qty_per"] or 0)
                * float(r["component_cost"] or 0),
            }
            for r in selected_rows
        ]
    )
    st_obj.markdown("#### 선택한 BOM 구성")
    st_obj.dataframe(show, use_container_width=True, hide_index=True)

    st_obj.warning(
        f"{parent_label}의 BOM 구성 {len(selected_rows):,}개를 모두 삭제합니다. "
        "과거 생산·재고 이력은 삭제되지 않습니다."
    )

    confirmed = st_obj.checkbox(
        "선택한 완제품의 BOM 전체 삭제를 확인했습니다.",
        key=f"bom_delete_confirm_v0934_{parent_id}",
    )
    if st_obj.button(
        "선택한 BOM 삭제",
        type="primary",
        disabled=not confirmed,
        key=f"bom_delete_button_v0934_{parent_id}",
    ):
        try:
            result = target_module._delete_all(core_module, parent_id, db_path)
            st_obj.success(
                f"{parent_label}의 BOM 구성 {result['deleted']:,}개를 삭제했습니다."
            )
            try:
                st_obj.rerun()
            except Exception:
                pass
        except Exception as exc:
            st_obj.error(f"BOM 삭제 실패: {exc}")


def apply(target_module) -> None:
    if getattr(target_module, "_rg_bom_delete_cleanup_v0933_applied", False):
        return

    def _render(st_obj, core_module, db_path=None):
        return render_delete_ui(st_obj, core_module, target_module, db_path)

    target_module.render_delete_ui = _render
    target_module._rg_bom_delete_cleanup_v0933_applied = True
