"""RG Manager v0.9.4 purchase-history list UX.

Replaces the v0.9.2 product selectbox with a visible item list.
Users click a product row to reveal its purchase history below.
"""
from __future__ import annotations

import re
from typing import Any

_APPLIED = False
_SELECTED_KEY = "_rg_purchase_history_selected_pid_v094"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _fmt_qty(value: Any) -> str:
    n = _num(value)
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}개"
    return f"{n:,.2f}".rstrip("0").rstrip(".") + "개"


def _fmt_money(value: Any) -> str:
    return f"{int(round(_num(value))):,}원"


def _display_code(item_code: Any, option_id: Any = None) -> str:
    code = "" if item_code is None else str(item_code).strip()
    if re.fullmatch(r"CP-\d+", code):
        return str(option_id or code[3:])
    return code


def _list_frame(pd_obj, base_module, products, all_hist):
    latest = {}
    if not all_hist.empty:
        ordered = all_hist.sort_values(["purchase_date", "id"], ascending=[False, False])
        for r in ordered.itertuples():
            pid = int(r.product_id)
            if pid not in latest:
                latest[pid] = r

    rows = []
    for r in products.itertuples():
        pid = int(r.id)
        last = latest.get(pid)
        rows.append(
            {
                "_product_id": pid,
                "품목코드": _display_code(r.item_code, r.option_id),
                "상품명": str(r.name or ""),
                "최근매입일": str(getattr(last, "purchase_date", "") or r.last_purchase_date or ""),
                "최근매입가": _fmt_money(getattr(last, "unit_cost", 0)) if last is not None else "-",
                "누적매입수량": _fmt_qty(r.total_qty),
                "누적매입액": _fmt_money(r.total_amount),
                "매입횟수": f"{int(r.purchase_count or 0):,}회",
            }
        )
    return pd_obj.DataFrame(rows)


def _extract_selected_rows(event) -> list[int]:
    if event is None:
        return []
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    rows = getattr(selection, "rows", None)
    if rows is None and isinstance(selection, dict):
        rows = selection.get("rows")
    try:
        return [int(x) for x in (rows or [])]
    except Exception:
        return []


def _render_clickable_product_list(st_obj, pd_obj, frame):
    """Return selected product_id or None.

    Primary path uses Streamlit dataframe row selection. The fallback keeps the
    full item list visible and uses a checkbox-style selection column.
    """
    selected_pid = st_obj.session_state.get(_SELECTED_KEY)
    display_cols = [
        "품목코드",
        "상품명",
        "최근매입일",
        "최근매입가",
        "누적매입수량",
        "누적매입액",
        "매입횟수",
    ]
    visible = frame[display_cols].copy()

    try:
        event = st_obj.dataframe(
            visible,
            use_container_width=True,
            hide_index=True,
            height=min(650, max(260, 38 * (len(visible) + 1))),
            on_select="rerun",
            selection_mode="single-row",
            key="_rg_purchase_history_list_v094",
        )
        selected_rows = _extract_selected_rows(event)
        if selected_rows:
            idx = selected_rows[0]
            if 0 <= idx < len(frame):
                selected_pid = int(frame.iloc[idx]["_product_id"])
                st_obj.session_state[_SELECTED_KEY] = selected_pid
        return int(selected_pid) if selected_pid is not None else None
    except (TypeError, AttributeError):
        fallback = visible.copy()
        fallback.insert(0, "선택", False)
        if selected_pid is not None:
            matches = frame.index[frame["_product_id"] == int(selected_pid)].tolist()
            if matches:
                fallback.loc[matches[0], "선택"] = True

        edited = st_obj.data_editor(
            fallback,
            use_container_width=True,
            hide_index=True,
            height=min(650, max(260, 38 * (len(fallback) + 1))),
            disabled=display_cols,
            num_rows="fixed",
            key="_rg_purchase_history_editor_v094",
        )
        chosen = edited.index[edited["선택"] == True].tolist()
        if chosen:
            idx = int(chosen[-1])
            selected_pid = int(frame.iloc[idx]["_product_id"])
            st_obj.session_state[_SELECTED_KEY] = selected_pid
        return int(selected_pid) if selected_pid is not None else None


def _filter_products(st_obj, pd_obj, frame):
    query = st_obj.text_input(
        "상품 검색",
        key="_rg_purchase_history_search_v094",
        placeholder="품목코드 또는 상품명",
    ).strip()
    if not query:
        return frame.reset_index(drop=True)

    q = query.lower()
    mask = (
        frame["품목코드"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        | frame["상품명"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
    )
    return frame.loc[mask].reset_index(drop=True)


def render_purchase_history_page(st, pd, core, page_header, section, base_module, **_kwargs):
    db_path = core.DEFAULT_DB
    page_header(
        "매입이력",
        "매입한 상품을 목록에서 클릭하면 과거 매입일·차수·수량·매입가·매입금액을 바로 확인합니다.",
        eyebrow="PURCHASE HISTORY",
    )

    products = base_module._products_with_history(pd, core, db_path)
    if products.empty:
        st.info("아직 저장된 매입이력이 없습니다. 매입관리에서 매입자료를 먼저 등록해 주세요.")
        return

    all_hist = base_module._history(pd, core, db_path)
    item_frame = _list_frame(pd, base_module, products, all_hist)

    tabs = st.tabs(["상품별 매입이력", "전체 매입내역"])

    with tabs[0]:
        section(
            "매입상품 목록",
            "상품을 클릭하면 아래에 해당 상품의 과거 매입내역이 표시됩니다. 상품은 모두 목록에 보이며 검색은 필터 용도입니다.",
        )
        filtered = _filter_products(st, pd, item_frame)
        if filtered.empty:
            st.info("검색 조건에 맞는 매입상품이 없습니다.")
            return

        pid = _render_clickable_product_list(st, pd, filtered)
        if pid is None:
            st.info("위 목록에서 확인할 상품을 클릭하세요.")
        else:
            product_row = item_frame[item_frame["_product_id"] == pid]
            if product_row.empty:
                st.session_state.pop(_SELECTED_KEY, None)
                st.info("위 목록에서 확인할 상품을 클릭하세요.")
            else:
                pr = product_row.iloc[0]
                hist = base_module._history(pd, core, db_path, pid)
                section(
                    f"{pr['품목코드']} | {pr['상품명']}",
                    "선택한 상품의 과거 매입내역입니다. 최신 매입순으로 표시합니다.",
                )
                base_module._render_kpis(st, hist)

                detail = base_module._history_display(pd, hist)
                keep = ["매입일", "차수", "매입수량", "개당 매입가", "매입금액"]
                detail = detail[keep]
                st.dataframe(
                    detail,
                    use_container_width=True,
                    hide_index=True,
                    height=min(650, max(220, 38 * (len(detail) + 1))),
                )
                base_module._render_trend(st, pd, hist)

                with st.expander("원본 매입자료 정보"):
                    raw = hist[
                        [
                            "purchase_date",
                            "purchase_batch",
                            "source_name",
                            "source_detail",
                            "file_name",
                            "sheet_name",
                            "source_row",
                        ]
                    ].copy()
                    raw.columns = ["매입일", "차수", "매입자료 상품명", "상세정보", "파일", "시트", "원본행"]
                    st.dataframe(raw, use_container_width=True, hide_index=True)

    with tabs[1]:
        if all_hist.empty:
            st.info("저장된 매입이력이 없습니다.")
            return

        c1, c2, c3 = st.columns(3)
        c1.metric("매입이력", f"{len(all_hist):,}건")
        c2.metric("매입상품", f"{all_hist['product_id'].nunique():,}개")
        c3.metric("누적 매입액", _fmt_money(all_hist["amount"].fillna(0).sum()))

        batches = [x for x in all_hist["purchase_batch"].fillna("").astype(str).unique().tolist() if x]
        batch_options = ["전체"] + sorted(
            batches,
            key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else -1,
            reverse=True,
        )
        selected_batch = st.selectbox("차수", batch_options, key="purchase_history_all_batch")
        view_hist = all_hist if selected_batch == "전체" else all_hist[all_hist["purchase_batch"] == selected_batch]

        rows = []
        for r in view_hist.itertuples():
            rows.append(
                {
                    "매입일": str(r.purchase_date or ""),
                    "차수": str(r.purchase_batch or ""),
                    "품목코드": _display_code(r.item_code, r.option_id),
                    "상품명": str(r.product_name or ""),
                    "매입수량": _fmt_qty(r.qty),
                    "개당 매입가": _fmt_money(r.unit_cost),
                    "매입금액": _fmt_money(r.amount),
                }
            )
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            height=min(700, max(240, 38 * (len(rows) + 1))),
        )


def apply(base_module):
    global _APPLIED
    if _APPLIED:
        return base_module
    if base_module is None:
        return base_module

    def patched_render(st, pd, core, page_header, section, **kwargs):
        return render_purchase_history_page(
            st=st,
            pd=pd,
            core=core,
            page_header=page_header,
            section=section,
            base_module=base_module,
            **kwargs,
        )

    base_module.render_purchase_history_page = patched_render
    base_module._rg_purchase_history_v094_applied = True
    _APPLIED = True
    return base_module
