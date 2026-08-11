"""Dedicated item master UI for RG Manager v0.9.25."""
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
    """Return the first unused JDS#### code, treating legacy JDS1/JDS001 as the same number."""
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
            try:
                n = int(m.group(1))
                if 1 <= n <= 9999:
                    used_numbers.add(n)
            except Exception:
                pass

    for n in range(1, 10000):
        code = f"JDS{n:04d}"
        if n not in used_numbers and code.upper() not in used_codes:
            return code
    raise RuntimeError("JDS0001~JDS9999 품목코드를 모두 사용 중입니다.")


def _inject_item_form_css(st):
    st.markdown(
        """
<style>
/* v0.9.25 item master: strong visual separation between labels and editable fields. */
[data-testid="stMain"] [data-testid="stWidgetLabel"] p {
    color: #10213a !important;
    font-weight: 800 !important;
}

/* Text fields */
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="input"],
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="base-input"] {
    background: #dfe8f3 !important;
    border-color: #7186a1 !important;
}
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="input"] {
    border: 2px solid #7186a1 !important;
    border-radius: 9px !important;
    box-shadow: 0 1px 3px rgba(15, 35, 65, 0.14) !important;
}
[data-testid="stMain"] [data-testid="stTextInput"] input {
    background: #dfe8f3 !important;
    color: #0b172a !important;
}
[data-testid="stMain"] [data-testid="stTextInput"] div[data-baseweb="input"]:focus-within {
    border-color: #2563c5 !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 197, 0.16) !important;
}

/* Number fields */
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="input"],
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="base-input"] {
    background: #dfe8f3 !important;
    border-color: #7186a1 !important;
}
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="input"] {
    border: 2px solid #7186a1 !important;
    border-radius: 9px !important;
    box-shadow: 0 1px 3px rgba(15, 35, 65, 0.14) !important;
}
[data-testid="stMain"] [data-testid="stNumberInput"] input {
    background: #dfe8f3 !important;
    color: #0b172a !important;
}
[data-testid="stMain"] [data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within {
    border-color: #2563c5 !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 197, 0.16) !important;
}
[data-testid="stMain"] [data-testid="stNumberInput"] button {
    background: #cfdceb !important;
    border-left: 1px solid #7186a1 !important;
    color: #17345e !important;
}

/* Select boxes */
[data-testid="stMain"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: #dfe8f3 !important;
    border: 2px solid #7186a1 !important;
    border-radius: 9px !important;
    box-shadow: 0 1px 3px rgba(15, 35, 65, 0.14) !important;
    color: #0b172a !important;
}
[data-testid="stMain"] [data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within {
    border-color: #2563c5 !important;
    box-shadow: 0 0 0 3px rgba(37, 99, 197, 0.16) !important;
}
[data-testid="stMain"] [data-testid="stSelectbox"] div[data-baseweb="select"] * {
    color: #0b172a !important;
}

[data-testid="stMain"] [data-testid="stTextInput"],
[data-testid="stMain"] [data-testid="stNumberInput"],
[data-testid="stMain"] [data-testid="stSelectbox"] {
    margin-bottom: 0.5rem !important;
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
            raise ValueError(f"이미 존재하는 품목코드입니다: {_display_code(db_item_code, db_option_id)}")
        if db_option_id and con.execute("SELECT 1 FROM products WHERE option_id=?", (db_option_id,)).fetchone():
            raise ValueError(f"이미 등록된 쿠팡 옵션ID입니다: {db_option_id}")
        cur = con.execute(
            """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
               VALUES(?,?,?,?,?,1,?)""",
            (db_item_code, db_option_id, name, item_type, float(unit_cost or 0), core.now_iso()),
        )
        return int(cur.lastrowid)


def _update_product(core, product_id, name, unit_cost, active):
    name = str(name or "").strip()
    if not name:
        raise ValueError("상품명을 입력해 주세요.")
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET name=?,unit_cost=?,active=?,updated_at=? WHERE id=?",
            (name, float(unit_cost or 0), 1 if active else 0, core.now_iso(), int(product_id)),
        )


def render_item_page(st, pd, core, page_header, section, **_kwargs):
    _inject_item_form_css(st)
    page_header("품목관리", "신규 상품을 등록하고 기존 품목의 이름·원가·사용여부를 관리합니다.", eyebrow="ITEM MASTER")
    tabs = st.tabs(["품목목록", "신규등록", "품목수정"])

    with tabs[0]:
        df = _load_products(core)
        active_only = st.checkbox("사용중 품목만 보기", value=False, key="item_master_active_only")
        if active_only:
            df = df[df["active"] == 1].copy()
        view = pd.DataFrame({
            "품목코드": [_display_code(r.item_code, r.option_id) for r in df.itertuples()],
            "상품명": df["name"],
            "구분": df["option_id"].map(lambda x: "쿠팡RG" if pd.notna(x) and str(x).strip() else "자체창고"),
            "쿠팡 옵션ID": df["option_id"].fillna(""),
            "기준원가": df["unit_cost"].fillna(0),
            "자체창고": df["own_stock"].fillna(0),
            "쿠팡RG": df["rg_stock"].fillna(0),
            "반품창고": df["return_stock"].fillna(0),
            "사용": df["active"].map(lambda x: "사용" if int(x or 0) else "중지"),
        })
        st.dataframe(view, use_container_width=True, hide_index=True, height=min(650, max(220, 38 * (len(view) + 1))))

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
                    "품목코드",
                    value=auto_code,
                    help="기존 자체창고/재고 품목과 겹치지 않는 JDS+4자리 숫자 코드가 자동 입력됩니다. 필요하면 수정할 수 있습니다.",
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
        if df.empty:
            st.info("등록된 품목이 없습니다.")
        else:
            labels = {
                int(r.id): f"{_display_code(r.item_code, r.option_id)} | {r.name} | {'쿠팡RG' if r.option_id else '자체창고'}"
                for r in df.itertuples()
            }
            pid = st.selectbox("수정할 품목", list(labels), format_func=lambda x: labels[x], key="item_edit_id")
            row = df[df["id"] == pid].iloc[0]
            st.caption(f"품목코드 {_display_code(row['item_code'], row['option_id'])} · 옵션ID {row['option_id'] or '-'}")
            edit_name = st.text_input("상품명", value=str(row["name"]), key=f"item_edit_name_{pid}")
            edit_cost = st.number_input("기준원가(원)", min_value=0.0, value=float(row["unit_cost"] or 0), step=1.0, format="%.0f", key=f"item_edit_cost_{pid}")
            edit_active = st.checkbox("사용중", value=bool(row["active"]), key=f"item_edit_active_{pid}")
            st.info("품목코드와 쿠팡 옵션ID는 재고·BOM·정산 연결키이므로 여기서는 변경하지 않습니다.")
            if st.button("품목 수정 저장", type="primary", key=f"item_edit_submit_{pid}"):
                try:
                    _update_product(core, pid, edit_name, edit_cost, edit_active)
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
