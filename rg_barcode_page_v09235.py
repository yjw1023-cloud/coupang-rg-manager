"""RG Manager v0.9.237 — product-picker based barcode printing page.

Workflow:
1) Search active RG/finished products already registered in ERP.
2) Newest registered products are shown first.
3) Pick products with the left-most checkbox and enter print quantity.
4) Review a concise print plan (item / barcode / quantity).
5) Explicitly confirm before the 50x30 mm Code128 print view is rendered.

No inbound Excel upload is required.
"""
from __future__ import annotations

import importlib
from typing import Any

PAGE_LABEL = "바코드 인쇄"
_PLAN_KEY = "rg_barcode_v09237_print_plan"
_CONFIRMED_KEY = "rg_barcode_v09237_confirmed"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _oid(value: Any) -> str:
    value = _text(value)
    if value.upper().startswith("CP-"):
        value = value[3:]
    if value.endswith(".0") and value[:-2].isdigit():
        value = value[:-2]
    return value


def _table_columns(core):
    with core._conn(core.DEFAULT_DB) as con:
        return {str(r["name"]) for r in con.execute("PRAGMA table_info(products)").fetchall()}


def _load_products(core):
    """Return active RG/finished products newest-first without assuming one schema version."""
    cols = _table_columns(core)

    def expr(name, fallback):
        return name if name in cols else fallback

    select_parts = [
        "id",
        expr("name", "'' AS name"),
        expr("item_code", "'' AS item_code"),
        expr("option_id", "'' AS option_id"),
        expr("product_type", "'' AS product_type"),
        expr("active", "1 AS active"),
        expr("created_at", "'' AS created_at"),
    ]

    barcode_col = next((x for x in ("barcode", "product_barcode", "rg_barcode") if x in cols), None)
    if barcode_col:
        select_parts.append(f"{barcode_col} AS product_barcode")
    else:
        select_parts.append("'' AS product_barcode")

    where = []
    if "active" in cols:
        where.append("COALESCE(active,1)=1")
    if "option_id" in cols:
        where.append("TRIM(COALESCE(CAST(option_id AS TEXT),''))<>''")
    if "product_type" in cols:
        # Prefer actual sellable finished products; legacy rows can have blank type.
        where.append("(product_type='finished' OR COALESCE(product_type,'')='')")

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    if "created_at" in cols:
        order_sql = " ORDER BY CASE WHEN created_at IS NULL OR created_at='' THEN 1 ELSE 0 END, created_at DESC, id DESC"
    else:
        order_sql = " ORDER BY id DESC"

    barcode = importlib.import_module("rg_barcode_print_v09193")
    barcode.ensure_schema(core)
    with core._conn(core.DEFAULT_DB) as con:
        rows = con.execute(
            "SELECT " + ", ".join(select_parts) + " FROM products" + where_sql + order_sql
        ).fetchall()
        masters = {
            _oid(r["vendor_item_id"]): dict(r)
            for r in con.execute(
                """SELECT vendor_item_id,barcode,product_name,option_name,updated_at
                   FROM rg_barcode_master"""
            ).fetchall()
        }

    result = []
    seen = set()
    for row in rows:
        option_id = _oid(row["option_id"])
        if not option_id or option_id in seen:
            continue
        seen.add(option_id)
        saved = masters.get(option_id, {})
        product_barcode = _text(row["product_barcode"])
        saved_barcode = _text(saved.get("barcode"))
        result.append(
            {
                "선택": False,
                "상품명": _text(row["name"]) or _text(saved.get("product_name")),
                "상품코드": _text(row["item_code"]),
                "옵션ID": option_id,
                "바코드": saved_barcode or product_barcode,
                "출력수량": 0,
                "등록일": _text(row["created_at"]),
            }
        )
    return result


def _records(value):
    if hasattr(value, "to_dict"):
        return [dict(x) for x in value.to_dict("records")]
    return [dict(x) for x in (value or [])]


def _save_barcodes(core, rows):
    barcode = importlib.import_module("rg_barcode_print_v09193")
    payload = [
        {
            "option_id": r.get("옵션ID"),
            "barcode": r.get("바코드"),
            "product_name": r.get("상품명"),
        }
        for r in rows
        if _text(r.get("바코드"))
    ]
    if payload:
        barcode._save_rows(core, payload, source="barcode_print_page")


def _plan_from_editor(core, edited):
    barcode = importlib.import_module("rg_barcode_print_v09193")
    selected = []
    errors = []
    for row in _records(edited):
        if not bool(row.get("선택")):
            continue
        option_id = _oid(row.get("옵션ID"))
        name = _text(row.get("상품명"))
        code = _text(row.get("바코드"))
        try:
            qty = int(row.get("출력수량") or 0)
        except Exception:
            qty = 0
        if qty < 1:
            errors.append(f"{name or option_id}: 출력수량을 1개 이상 입력해 주세요.")
            continue
        if not code:
            errors.append(f"{name or option_id}: 바코드가 없습니다.")
            continue
        try:
            # Validate against the exact Code128 implementation used by printing.
            barcode.barcode_svg(code)
        except Exception as exc:
            errors.append(f"{name or option_id}: 바코드 오류 - {exc}")
            continue
        selected.append(
            {
                "상품명": name,
                "옵션명": "",
                "옵션ID": option_id,
                "바코드": code,
                "출력수량": qty,
            }
        )

    total = sum(int(x["출력수량"]) for x in selected)
    if total > barcode.MAX_TOTAL_LABELS:
        errors.append(f"한 번에 최대 {barcode.MAX_TOTAL_LABELS:,}장까지 인쇄할 수 있습니다.")
    if not selected and not errors:
        errors.append("인쇄할 상품을 선택해 주세요.")
    return selected, errors


def render_page(st, core):
    pd = importlib.import_module("pandas")
    barcode = importlib.import_module("rg_barcode_print_v09193")
    components = importlib.import_module("streamlit.components.v1")

    st.title("바코드 인쇄")
    st.caption("ERP에 등록된 로켓그로스 상품을 직접 선택해 50×30mm 바코드 라벨을 인쇄합니다.")

    search = st.text_input(
        "상품 검색",
        placeholder="상품명, 상품코드 또는 옵션ID를 입력하세요",
        key="rg_barcode_v09237_search",
    ).strip().lower()

    try:
        rows = _load_products(core)
    except Exception as exc:
        st.error(f"상품 목록을 불러오지 못했습니다: {exc}")
        return

    if search:
        rows = [
            r for r in rows
            if search in _text(r.get("상품명")).lower()
            or search in _text(r.get("상품코드")).lower()
            or search in _text(r.get("옵션ID")).lower()
            or search in _text(r.get("바코드")).lower()
        ]

    st.caption(
        f"검색 결과 {len(rows):,}개 · 최근 등록 상품이 위에 표시됩니다. "
        "왼쪽 체크박스로 선택하고 출력수량을 입력하세요."
    )

    if not rows:
        st.info("조건에 맞는 상품이 없습니다.")
        return

    frame = pd.DataFrame(rows)
    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        height=min(720, max(300, 38 * (min(len(frame), 16) + 1))),
        key="rg_barcode_v09237_editor",
        disabled=["상품명", "상품코드", "옵션ID", "등록일"],
        column_config={
            "선택": st.column_config.CheckboxColumn("선택", width="small"),
            "상품명": st.column_config.TextColumn("상품명", width="large"),
            "상품코드": st.column_config.TextColumn("상품코드", width="medium"),
            "옵션ID": st.column_config.TextColumn("옵션ID", width="medium"),
            "바코드": st.column_config.TextColumn(
                "바코드", width="medium",
                help="저장된 바코드가 없거나 잘못된 경우 여기서 직접 입력/수정할 수 있습니다.",
            ),
            "출력수량": st.column_config.NumberColumn(
                "출력수량", min_value=0, max_value=5000, step=1, format="%d", width="small"
            ),
            "등록일": st.column_config.TextColumn("등록일", width="medium"),
        },
    )

    c1, c2 = st.columns([1, 2])
    if c1.button("선택 내용 확인", type="primary", use_container_width=True):
        plan, errors = _plan_from_editor(core, edited)
        if errors:
            for msg in errors[:10]:
                st.error(msg)
            st.session_state.pop(_PLAN_KEY, None)
            st.session_state[_CONFIRMED_KEY] = False
        else:
            try:
                _save_barcodes(core, plan)
            except Exception as exc:
                st.error(f"바코드 저장 실패: {exc}")
                return
            st.session_state[_PLAN_KEY] = plan
            st.session_state[_CONFIRMED_KEY] = False
            st.rerun()

    selected_now = [r for r in _records(edited) if bool(r.get("선택"))]
    c2.caption(
        f"현재 선택 {len(selected_now):,}개 상품 · "
        f"입력 수량 합계 {sum(max(0, int(r.get('출력수량') or 0)) for r in selected_now):,}장"
    )

    plan = st.session_state.get(_PLAN_KEY)
    if not plan:
        return

    st.divider()
    st.subheader("인쇄 전 최종 확인")
    st.caption("아래 내용이 실제 인쇄될 목록입니다. 확인 후에만 인쇄 화면을 엽니다.")

    review = pd.DataFrame(
        [
            {
                "상품명": r["상품명"],
                "바코드": r["바코드"],
                "바코드 수량": int(r["출력수량"]),
            }
            for r in plan
        ]
    )
    st.dataframe(review, use_container_width=True, hide_index=True)
    total = sum(int(r["출력수량"]) for r in plan)
    st.info(f"총 {len(plan):,}개 상품 / 바코드 {total:,}장을 인쇄합니다.")

    b1, b2 = st.columns([1, 1])
    if b1.button("수정하러 돌아가기", use_container_width=True):
        st.session_state.pop(_PLAN_KEY, None)
        st.session_state[_CONFIRMED_KEY] = False
        st.rerun()

    if b2.button("이 내용으로 인쇄 확정", type="primary", use_container_width=True):
        st.session_state[_CONFIRMED_KEY] = True
        st.rerun()

    if not st.session_state.get(_CONFIRMED_KEY):
        return

    st.success("인쇄 내용이 확정되었습니다. 아래 인쇄 버튼을 누르면 프린터 창이 열립니다.")
    try:
        print_html = barcode.build_print_html(plan)
        components.html(print_html, height=390, scrolling=True)
    except Exception as exc:
        st.error(f"바코드 인쇄 화면 생성 실패: {exc}")
