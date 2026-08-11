"""Dedicated item master UI for RG Manager v0.9.26."""
from __future__ import annotations

import re
import sqlite3

import pandas as pd


def _display_code(item_code, option_id=None):
    code = "" if item_code is None else str(item_code)
    if re.fullmatch(r"CP-\d+", code):
        return str(option_id or code[3:])
    return code


def _conn(core):
    con = sqlite3.connect(str(core.DEFAULT_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _load_products(core):
    core.init_db(core.DEFAULT_DB)
    with _conn(core) as con:
        return pd.read_sql_query(
            """
            SELECT p.id,p.item_code,p.option_id,p.name,p.item_type,p.unit_cost,p.active,
                   COALESCE(SUM(CASE WHEN w.name='자체창고' THEN t.qty_delta ELSE 0 END),0) own_stock,
                   COALESCE(SUM(CASE WHEN w.name='쿠팡RG' THEN t.qty_delta ELSE 0 END),0) rg_stock,
                   COALESCE(SUM(CASE WHEN w.name='반품창고' THEN t.qty_delta ELSE 0 END),0) return_stock
            FROM products p
            LEFT JOIN inventory_txns t ON t.product_id=p.id
            LEFT JOIN warehouses w ON w.id=t.warehouse_id
            GROUP BY p.id,p.item_code,p.option_id,p.name,p.item_type,p.unit_cost,p.active
            ORDER BY p.name,p.item_code
            """,
            con,
        )


def _next_jds_code(core):
    """Return first unused JDS####; archived rows also reserve their old number."""
    core.init_db(core.DEFAULT_DB)
    used_numbers = set()
    used_codes = set()
    with _conn(core) as con:
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
    for n in range(1, 10000):
        code = f"JDS{n:04d}"
        if n not in used_numbers and code.upper() not in used_codes:
            return code
    raise RuntimeError("JDS0001~JDS9999 품목코드를 모두 사용 중입니다.")


def _inject_item_form_css(st):
    st.markdown(
        """
<style>
[data-testid="stMain"] [data-testid="stWidgetLabel"] p {
    color: #10213a !important;
    font-weight: 800 !important;
}
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="base-input"],
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="input"],
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="base-input"] {
    background: #dfe8f3 !important;
    border-color: #7186a1 !important;
}
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="input"] {
    border: 2px solid #7186a1 !important;
    border-radius: 9px !important;
    box-shadow: 0 1px 3px rgba(15,35,65,.14) !important;
}
[data-testid="stMain"] [data-testid="stTextInput"] input,
[data-testid="stMain"] [data-testid="stNumberInput"] input {
    background: #dfe8f3 !important;
    color: #0b172a !important;
}
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="input"]:focus-within,
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within {
    border-color: #2563c5 !important;
    box-shadow: 0 0 0 3px rgba(37,99,197,.16) !important;
}
[data-testid="stMain"] [data-testid="stNumberInput"] button {
    background: #cfdceb !important;
    border-left: 1px solid #7186a1 !important;
    color: #17345e !important;
}
[data-testid="stMain"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: #dfe8f3 !important;
    border: 2px solid #7186a1 !important;
    border-radius: 9px !important;
    box-shadow: 0 1px 3px rgba(15,35,65,.14) !important;
    color: #0b172a !important;
}
[data-testid="stMain"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within {
    border-color: #2563c5 !important;
    box-shadow: 0 0 0 3px rgba(37,99,197,.16) !important;
}
[data-testid="stMain"] [data-testid="stSelectbox"] div[data-baseweb="select"] * {
    color: #0b172a !important;
}
[data-testid="stMain"] [data-testid="stTextInput"],
[data-testid="stMain"] [data-testid="stNumberInput"],
[data-testid="stMain"] [data-testid="stSelectbox"] {
    margin-bottom: .5rem !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _create_product(core, kind, name, item_code, option_id, unit_cost):
    name = str(name or "").strip()
    item_code = str(item_code or "").strip()
    option_id = str(option_id or "").strip()
    if not name:
        raise ValueError("상품명을 입력해 주세요.")
    if kind == "자체창고 품목":
        if not item_code:
            item_code = _next_jds_code(core)
        db_item_code = item_code
        db_option_id = None
        item_type = "raw"
    else:
        if not option_id:
            raise ValueError("쿠팡RG 판매상품은 옵션ID를 입력해 주세요.")
        if not option_id.isdigit():
            raise ValueError("쿠팡 옵션ID는 숫자로 입력해 주세요.")
        db_option_id = option_id
        db_item_code = item_code or f"CP-{option_id}"
        item_type = "finished"

    core.init_db(core.DEFAULT_DB)
    with _conn(core) as con:
        if con.execute("SELECT 1 FROM products WHERE item_code=?", (db_item_code,)).fetchone():
            raise ValueError(f"이미 존재하거나 과거에 사용한 품목코드입니다: {_display_code(db_item_code, db_option_id)}")
        if db_option_id and con.execute("SELECT 1 FROM products WHERE option_id=?", (db_option_id,)).fetchone():
            raise ValueError(f"이미 등록된 쿠팡 옵션ID입니다: {db_option_id}")
        cur = con.execute(
            """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
               VALUES(?,?,?,?,?,1,?)""",
            (db_item_code, db_option_id, name, item_type, float(unit_cost or 0), core.now_iso()),
        )
        return int(cur.lastrowid)


def _update_product(core, product_id, name, unit_cost):
    name = str(name or "").strip()
    if not name:
        raise ValueError("상품명을 입력해 주세요.")
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET name=?,unit_cost=?,updated_at=? WHERE id=?",
            (name, float(unit_cost or 0), core.now_iso(), int(product_id)),
        )


def _inventory_balances(core, product_id):
    core.init_db(core.DEFAULT_DB)
    with _conn(core) as con:
        rows = con.execute(
            """
            SELECT COALESCE(w.name,'미지정') warehouse, COALESCE(SUM(t.qty_delta),0) qty
            FROM inventory_txns t
            LEFT JOIN warehouses w ON w.id=t.warehouse_id
            WHERE t.product_id=?
            GROUP BY t.warehouse_id,w.name
            ORDER BY w.name
            """,
            (int(product_id),),
        ).fetchall()
    return [(str(r["warehouse"]), float(r["qty"] or 0)) for r in rows if abs(float(r["qty"] or 0)) > 1e-9]


def _bom_links(core, product_id):
    core.init_db(core.DEFAULT_DB)
    with _conn(core) as con:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bom_items'"
        ).fetchone()
        if not exists:
            return []
        try:
            rows = con.execute(
                """
                SELECT b.parent_product_id,b.component_product_id,
                       COALESCE(pp.name,'') parent_name,
                       COALESCE(cp.name,'') component_name
                FROM bom_items b
                LEFT JOIN products pp ON pp.id=b.parent_product_id
                LEFT JOIN products cp ON cp.id=b.component_product_id
                WHERE b.parent_product_id=? OR b.component_product_id=?
                ORDER BY pp.name,cp.name
                """,
                (int(product_id), int(product_id)),
            ).fetchall()
        except sqlite3.Error:
            return []
    return [dict(r) for r in rows]


def _archive_product(core, product_id):
    balances = _inventory_balances(core, product_id)
    if balances:
        detail = ", ".join(f"{name} {qty:g}" for name, qty in balances)
        raise ValueError(f"재고수량이 0이 아닌 창고가 있어 삭제할 수 없습니다: {detail}")
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET active=0,updated_at=? WHERE id=?",
            (core.now_iso(), int(product_id)),
        )


def _restore_product(core, product_id):
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET active=1,updated_at=? WHERE id=?",
            (core.now_iso(), int(product_id)),
        )


def _item_label(row):
    status = "사용" if int(row.active or 0) else "삭제됨"
    kind = "쿠팡RG" if row.option_id else "자체창고"
    return f"{_display_code(row.item_code, row.option_id)} | {row.name} | {kind} | {status}"


def _render_archive_manager(st, core, all_df):
    st.markdown("### 품목 삭제 · 복원")
    st.caption("삭제는 과거 매입·재고·BOM·손익 기록을 보호하기 위해 실제 DB 행을 지우지 않고 보관 처리합니다.")

    mode = st.radio("관리 작업", ["품목 삭제", "삭제품목 복원"], horizontal=True, key="item_archive_mode")
    if mode == "품목 삭제":
        candidates = all_df[all_df["active"] == 1].copy()
    else:
        candidates = all_df[all_df["active"] == 0].copy()

    if candidates.empty:
        st.info("선택할 품목이 없습니다.")
        return

    labels = {int(r.id): _item_label(r) for r in candidates.itertuples()}
    pid = st.selectbox("대상 품목", list(labels), format_func=lambda x: labels[x], key=f"item_archive_target_{mode}")
    row = candidates[candidates["id"] == pid].iloc[0]
    code = _display_code(row["item_code"], row["option_id"])

    if mode == "삭제품목 복원":
        st.info(f"{code} · {row['name']}을(를) 다시 품목목록과 업무 후보에 표시합니다.")
        if st.button("선택 품목 복원", type="primary", key=f"item_restore_{pid}"):
            _restore_product(core, pid)
            st.success("품목을 복원했습니다.")
            st.rerun()
        return

    balances = _inventory_balances(core, pid)
    bom_links = _bom_links(core, pid)

    if balances:
        st.error("현재 재고수량이 0이 아닌 품목은 삭제할 수 없습니다.")
        st.caption(" · ".join(f"{name}: {qty:g}" for name, qty in balances))

    bom_ok = True
    if bom_links:
        parent_links = [x for x in bom_links if int(x.get("parent_product_id") or 0) == int(pid)]
        component_links = [x for x in bom_links if int(x.get("component_product_id") or 0) == int(pid)]
        msgs = []
        if parent_links:
            msgs.append(f"이 품목 자체의 BOM {len(parent_links):,}건")
        if component_links:
            parents = sorted({str(x.get("parent_name") or "완제품") for x in component_links})
            msgs.append(f"다른 완제품의 구성품 {len(component_links):,}건 ({', '.join(parents[:5])}{' 외' if len(parents) > 5 else ''})")
        st.warning("BOM 연결이 있습니다: " + " / ".join(msgs) + ". 삭제해도 BOM 기록은 보존됩니다.")
        bom_ok = st.checkbox("BOM 연결 내용을 확인했습니다.", key=f"item_archive_bom_confirm_{pid}")

    confirm = st.checkbox(
        f"{code} · {row['name']} 품목을 목록에서 삭제합니다. 과거 기록과 품목코드는 보관됩니다.",
        key=f"item_archive_confirm_{pid}",
    )
    disabled = bool(balances) or not bom_ok or not confirm
    if st.button("선택 품목 삭제", type="primary", disabled=disabled, key=f"item_archive_submit_{pid}"):
        try:
            _archive_product(core, pid)
            st.success("품목목록에서 삭제했습니다. 과거 기록과 품목코드는 보관됩니다.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def render_item_page(st, pd, core, page_header, section, **_kwargs):
    _inject_item_form_css(st)
    page_header("품목관리", "신규 상품을 등록하고 기존 품목의 이름·원가·사용여부를 관리합니다.", eyebrow="ITEM MASTER")
    tabs = st.tabs(["품목목록", "신규등록", "품목수정"])

    with tabs[0]:
        all_df = _load_products(core)
        show_archived = st.checkbox("삭제된 품목도 보기", value=False, key="item_master_show_archived")
        df = all_df.copy() if show_archived else all_df[all_df["active"] == 1].copy()
        view = pd.DataFrame({
            "품목코드": [_display_code(r.item_code, r.option_id) for r in df.itertuples()],
            "상품명": df["name"],
            "구분": df["option_id"].map(lambda x: "쿠팡RG" if pd.notna(x) and str(x).strip() else "자체창고"),
            "쿠팡 옵션ID": df["option_id"].fillna(""),
            "기준원가": df["unit_cost"].fillna(0),
            "자체창고": df["own_stock"].fillna(0),
            "쿠팡RG": df["rg_stock"].fillna(0),
            "반품창고": df["return_stock"].fillna(0),
            "상태": df["active"].map(lambda x: "사용" if int(x or 0) else "삭제됨"),
        })
        st.dataframe(view, use_container_width=True, hide_index=True, height=min(650, max(220, 38 * (len(view) + 1))))
        st.markdown("---")
        _render_archive_manager(st, core, all_df)

    with tabs[1]:
        section("신규 품목 등록", "자체창고 품목은 매입/BOM 구성품으로, 쿠팡RG 판매상품은 생산 완제품으로 사용합니다.")
        kind = st.selectbox("관리구분", ["자체창고 품목", "쿠팡RG 판매상품"], key="item_new_kind")
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("상품명", key="item_new_name")
            unit_cost = st.number_input("기준원가(원)", min_value=0.0, step=1.0, format="%.0f", key="item_new_cost")
        with c2:
            if kind == "자체창고 품목":
                auto_code = _next_jds_code(core)
                item_code = st.text_input(
                    "품목코드", value=auto_code,
                    help="기존 및 삭제된 품목과 겹치지 않는 JDS+4자리 숫자 코드가 자동 입력됩니다.",
                    key=f"item_new_code_{auto_code}",
                )
                option_id = ""
            else:
                option_id = st.text_input("쿠팡 옵션ID", help="쿠팡 상품 옵션ID 숫자", key="item_new_option")
                item_code = st.text_input("내부 품목코드(선택)", help="비워두면 옵션ID 기준으로 자동 생성됩니다.", key="item_new_code_rg")
        if st.button("신규 품목 등록", type="primary", key="item_new_submit"):
            try:
                _create_product(core, kind, name, item_code, option_id, unit_cost)
                st.success("신규 품목을 등록했습니다.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    with tabs[2]:
        df = _load_products(core)
        df = df[df["active"] == 1].copy()
        if df.empty:
            st.info("수정할 사용중 품목이 없습니다.")
        else:
            labels = {int(r.id): _item_label(r) for r in df.itertuples()}
            pid = st.selectbox("수정할 품목", list(labels), format_func=lambda x: labels[x], key="item_edit_id")
            row = df[df["id"] == pid].iloc[0]
            st.caption(f"품목코드 {_display_code(row['item_code'], row['option_id'])} · 옵션ID {row['option_id'] or '-'}")
            edit_name = st.text_input("상품명", value=str(row["name"]), key=f"item_edit_name_{pid}")
            edit_cost = st.number_input("기준원가(원)", min_value=0.0, value=float(row["unit_cost"] or 0), step=1.0, format="%.0f", key=f"item_edit_cost_{pid}")
            st.info("품목코드·쿠팡 옵션ID·사용상태는 재고/BOM/정산 연결에 영향을 주므로 여기서는 변경하지 않습니다. 삭제/복원은 품목목록에서 관리합니다.")
            if st.button("품목 수정 저장", type="primary", key=f"item_edit_submit_{pid}"):
                try:
                    _update_product(core, pid, edit_name, edit_cost)
                    st.success("품목 정보를 수정했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def patch_source(source: str) -> str:
    menu_label = '        "📋  품목관리",\n'
    if menu_label not in source:
        anchor = '        "📦  재고관리",\n'
        if anchor not in source:
            raise RuntimeError("품목관리 메뉴를 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, menu_label + anchor, 1)

    handler = """# ------------------------------\n# Item master\n# ------------------------------\nelif page == "📋  품목관리":\n    item_ui_v086.render_item_page(\n        st=st, pd=pd, core=core, page_header=page_header, section=section,\n        kpi=kpi, money=money, fmt_date=fmt_date, latest_updated_text=latest_updated_text,\n    )\n\n\n"""
    if 'elif page == "📋  품목관리":' not in source:
        anchor = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
        if anchor not in source:
            raise RuntimeError("품목관리 화면을 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, handler + anchor, 1)
    return source
