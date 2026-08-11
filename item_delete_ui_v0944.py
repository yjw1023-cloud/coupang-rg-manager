"""RG Manager v0.9.44 item deletion and manual return-option cleanup."""
from __future__ import annotations

from difflib import SequenceMatcher
import re
import sqlite3
import pandas as pd


def _conn(core):
    con = sqlite3.connect(str(core.DEFAULT_DB))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _code(item_code, option_id=None):
    s = str(item_code or "").strip()
    if re.fullmatch(r"CP-\d+", s):
        return str(option_id or s[3:])
    return s


def _products(core):
    core.init_db(core.DEFAULT_DB)
    with _conn(core) as con:
        return pd.read_sql_query(
            """SELECT p.id,p.item_code,p.option_id,p.name,p.item_type,p.unit_cost,p.active,
                      COALESCE(SUM(CASE WHEN w.name='자체창고' THEN t.qty_delta ELSE 0 END),0) own_stock,
                      COALESCE(SUM(CASE WHEN w.name='쿠팡RG' THEN t.qty_delta ELSE 0 END),0) rg_stock,
                      COALESCE(SUM(CASE WHEN w.name='반품창고' THEN t.qty_delta ELSE 0 END),0) return_stock
               FROM products p
               LEFT JOIN inventory_txns t ON t.product_id=p.id
               LEFT JOIN warehouses w ON w.id=t.warehouse_id
               GROUP BY p.id,p.item_code,p.option_id,p.name,p.item_type,p.unit_cost,p.active
               ORDER BY p.name,p.item_code""",
            con,
        )


def _exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _balances(core, pid):
    with _conn(core) as con:
        rows = con.execute(
            """SELECT COALESCE(w.name,'미지정') warehouse,COALESCE(SUM(t.qty_delta),0) qty
               FROM inventory_txns t LEFT JOIN warehouses w ON w.id=t.warehouse_id
               WHERE t.product_id=? GROUP BY t.warehouse_id,w.name ORDER BY w.name""",
            (int(pid),),
        ).fetchall()
    return [(str(r["warehouse"]), float(r["qty"] or 0)) for r in rows if abs(float(r["qty"] or 0)) > 1e-9]


def _blockers(core, pid):
    out = []
    with _conn(core) as con:
        if _exists(con, "bom_items"):
            n = int(con.execute(
                "SELECT COUNT(*) n FROM bom_items WHERE parent_product_id=? OR component_product_id=?",
                (int(pid), int(pid)),
            ).fetchone()["n"] or 0)
            if n: out.append(f"BOM 연결 {n:,}건")
        if _exists(con, "production_orders"):
            n = int(con.execute(
                "SELECT COUNT(*) n FROM production_orders WHERE parent_product_id=?", (int(pid),)
            ).fetchone()["n"] or 0)
            if n: out.append(f"생산이력 {n:,}건")
        if _exists(con, "purchase_lines"):
            cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(purchase_lines)")}
            if "product_id" in cols:
                n = int(con.execute(
                    "SELECT COUNT(*) n FROM purchase_lines WHERE product_id=?", (int(pid),)
                ).fetchone()["n"] or 0)
                if n: out.append(f"매입이력 {n:,}건")
        if _exists(con, "inventory_txns"):
            n = int(con.execute(
                """SELECT COUNT(*) n FROM inventory_txns
                   WHERE product_id=? AND COALESCE(txn_type,'') NOT IN ('판매차감','반품할인판매차감')""",
                (int(pid),),
            ).fetchone()["n"] or 0)
            if n: out.append(f"판매 외 재고이력 {n:,}건")
    return out


def _archive(core, pid):
    bal = _balances(core, pid)
    if bal:
        raise ValueError("현재고가 0이 아니므로 일반 삭제할 수 없습니다: " + ", ".join(f"{w} {q:g}" for w, q in bal))
    with _conn(core) as con:
        con.execute("UPDATE products SET active=0,updated_at=? WHERE id=?", (core.now_iso(), int(pid)))


def _is_alias(core, oid):
    if not oid:
        return False
    with _conn(core) as con:
        return bool(_exists(con, "return_discount_aliases") and con.execute(
            "SELECT 1 FROM return_discount_aliases WHERE discount_option_id=?", (str(oid),)
        ).fetchone())


def _restore(core, pid, oid):
    if _is_alias(core, oid):
        raise ValueError("반품 할인판매 alias로 등록된 옵션ID는 정상 품목으로 복원할 수 없습니다.")
    with _conn(core) as con:
        con.execute("UPDATE products SET active=1,updated_at=? WHERE id=?", (core.now_iso(), int(pid)))


def _score(a, b):
    norm = lambda x: re.sub(r"[^0-9a-z가-힣]+", "", str(x or "").lower())
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return 0.0
    short, long = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
    s = SequenceMatcher(None, aa, bb).ratio()
    return max(s, 0.94) if len(short) >= 6 and short in long else s


def _manual_return(core, child_id, parent_id):
    if int(child_id) == int(parent_id):
        raise ValueError("반품코드와 정상 원상품은 서로 달라야 합니다.")
    blockers = _blockers(core, child_id)
    if blockers:
        raise ValueError("정상 관리상품일 가능성이 있는 이력이 있어 반품코드로 정리하지 않았습니다: " + " / ".join(blockers))

    import return_discount_v099 as rd
    db = core.DEFAULT_DB
    rd._ensure_schema(core, db)
    with _conn(core) as con:
        child = con.execute("SELECT id,item_code,option_id,name FROM products WHERE id=?", (int(child_id),)).fetchone()
        parent = con.execute("SELECT id,item_code,option_id,name,active FROM products WHERE id=?", (int(parent_id),)).fetchone()
    if not child or not parent:
        raise ValueError("대상 품목을 찾지 못했습니다.")
    oid = str(child["option_id"] or "").strip()
    if not oid.isdigit():
        raise ValueError("반품코드 정리는 쿠팡 옵션ID가 있는 품목만 가능합니다.")
    if int(parent["active"] or 0) != 1:
        raise ValueError("정상 원상품은 현재 사용중이어야 합니다.")

    now = core.now_iso()
    with _conn(core) as con:
        con.execute(
            """INSERT INTO return_discount_aliases
               (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(discount_option_id) DO UPDATE SET
                 parent_product_id=excluded.parent_product_id,
                 discount_name=excluded.discount_name,
                 match_method=excluded.match_method,
                 updated_at=excluded.updated_at""",
            (oid, int(parent_id), str(child["name"] or ""), "manual_item_delete_v0944", now, now),
        )

    amount_col = rd._amount_column(core, db)
    rows = []
    with _conn(core) as con:
        if _exists(con, "sales_stats"):
            if amount_col:
                rows = con.execute(
                    f'''SELECT import_id,COALESCE(SUM(net_qty),0) qty,COALESCE(SUM("{amount_col}"),0) amount
                        FROM sales_stats WHERE product_id=? GROUP BY import_id''',
                    (int(child_id),),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT import_id,COALESCE(SUM(net_qty),0) qty FROM sales_stats WHERE product_id=? GROUP BY import_id",
                    (int(child_id),),
                ).fetchall()

    converted = 0
    for r in rows:
        parsed = [{
            "option_id": oid,
            "name": str(child["name"] or ""),
            "name_key": rd._name_key(child["name"]),
            "qty": float(r["qty"] or 0),
            "amount": float(r["amount"] or 0) if amount_col else None,
            "amount_known": bool(amount_col),
        }]
        rd._post_discount(core, db, int(r["import_id"]), parsed, {oid: int(parent_id)})
        converted += 1

    with _conn(core) as con:
        con.execute("UPDATE products SET active=0,updated_at=? WHERE id=?", (core.now_iso(), int(child_id)))
    return converted, str(parent["name"] or "")


def _pick(st, df, label, key, qkey):
    q = st.text_input("검색", key=qkey, placeholder="품목코드·옵션ID·상품명").strip().lower()
    work = df.copy()
    if q:
        mask = (
            work["name"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
            | work["item_code"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
            | work["option_id"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        )
        work = work.loc[mask]
    if work.empty:
        st.info("검색 조건에 맞는 품목이 없습니다.")
        return None
    labels = {
        int(r.id): f"{_code(r.item_code, r.option_id)} | {r.name} | {'사용' if int(r.active or 0) else '삭제됨'}"
        for r in work.itertuples()
    }
    return int(st.selectbox(label, list(labels), format_func=lambda x: labels[int(x)], key=key))


def render_item_delete_page(st, pd, core, page_header, section, **_kwargs):
    page_header("품목 삭제", "잘못 등록된 품목과 쿠팡 반품 할인판매 옵션을 정리합니다.", eyebrow="ITEM CLEANUP")
    st.info("삭제는 물리 삭제가 아니라 보관처리입니다. 과거 판매·재고·손익 기록은 유지됩니다.")
    tabs = st.tabs(["반품코드 정리", "일반 품목 삭제", "삭제품목 복원"])

    with tabs[0]:
        section("반품 할인판매 코드 정리", "쿠팡이 반품 할인판매에 부여한 별도 옵션ID를 실제 정상 원상품에 연결합니다.")
        all_df = _products(core)
        children = all_df[(all_df["active"] == 1) & all_df["option_id"].notna()].copy()
        child_id = _pick(st, children, "삭제할 반품 품목", "ret_child", "ret_child_q")
        if child_id is not None:
            child = children[children["id"] == child_id].iloc[0]
            bal = _balances(core, child_id)
            if bal:
                st.caption("현재 표시 재고: " + " · ".join(f"{w} {q:g}" for w, q in bal))
            parents = all_df[(all_df["active"] == 1) & all_df["option_id"].notna() & (all_df["id"] != child_id)].copy()
            parents["score"] = parents["name"].map(lambda x: _score(child["name"], x))
            parents = parents.sort_values(["score", "name"], ascending=[False, True])
            labels = {}
            for _, r in parents.iterrows():
                labels[int(r["id"])] = f"{_code(r['item_code'], r['option_id'])} | {r['name']} | 유사도 {float(r['score']):.0%}"
            if labels:
                parent_id = int(st.selectbox("정상 원상품", list(labels), format_func=lambda x: labels[int(x)], key="ret_parent"))
                parent = parents[parents["id"] == parent_id].iloc[0]
                st.write(
                    f"**반품코드:** {_code(child['item_code'], child['option_id'])} · {child['name']}  \n"
                    f"**정상 원상품:** {_code(parent['item_code'], parent['option_id'])} · {parent['name']}"
                )
                blockers = _blockers(core, child_id)
                if blockers:
                    st.error("안전상 정리할 수 없습니다: " + " / ".join(blockers))
                confirm = st.checkbox("이 옵션ID가 반품 할인판매용 코드이고 위 정상 원상품에 연결되는 것이 맞습니다.", key="ret_confirm")
                if st.button("반품코드 정리 및 품목 삭제", type="primary", disabled=bool(blockers) or not confirm, key="ret_submit"):
                    try:
                        n, parent_name = _manual_return(core, child_id, parent_id)
                        st.success(f"반품 옵션을 {parent_name}에 연결하고 삭제했습니다. 기존 판매자료 {n:,}개 import도 반품판매로 재분류했습니다.")
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))

    with tabs[1]:
        section("일반 품목 삭제", "현재고가 0인 일반 품목을 업무 목록에서 제외합니다.")
        active = _products(core)
        active = active[active["active"] == 1].copy()
        pid = _pick(st, active, "삭제할 품목", "del_pid", "del_q")
        if pid is not None:
            row = active[active["id"] == pid].iloc[0]
            bal = _balances(core, pid)
            if bal:
                st.error("현재고가 남아 있어 일반 삭제할 수 없습니다.")
                st.caption(" · ".join(f"{w}: {q:g}" for w, q in bal))
            confirm = st.checkbox(f"{_code(row['item_code'], row['option_id'])} · {row['name']} 품목을 삭제합니다.", key="del_confirm")
            if st.button("선택 품목 삭제", type="primary", disabled=bool(bal) or not confirm, key="del_submit"):
                try:
                    _archive(core, pid)
                    st.success("품목을 삭제(보관처리)했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    with tabs[2]:
        section("삭제품목 복원", "일반 삭제한 품목을 다시 사용 상태로 돌립니다. 반품 alias는 복원할 수 없습니다.")
        archived = _products(core)
        archived = archived[archived["active"] == 0].copy()
        pid = _pick(st, archived, "복원할 품목", "restore_pid", "restore_q")
        if pid is not None:
            row = archived[archived["id"] == pid].iloc[0]
            alias = _is_alias(core, row["option_id"])
            if alias:
                st.warning("이 옵션ID는 반품 할인판매 alias이므로 정상 품목으로 복원할 수 없습니다.")
            if st.button("선택 품목 복원", type="primary", disabled=alias, key="restore_submit"):
                try:
                    _restore(core, pid, row["option_id"])
                    st.success("품목을 복원했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def patch_source(source: str) -> str:
    menu = '        "🗑️  품목 삭제",\n'
    if menu not in source:
        anchor = '        "📋  품목관리",\n'
        if anchor not in source:
            raise RuntimeError("품목 삭제 메뉴 삽입 위치를 찾지 못했습니다.")
        source = source.replace(anchor, anchor + menu, 1)
    handler = '''# ------------------------------\n# Item delete / return cleanup\n# ------------------------------\nelif page == "🗑️  품목 삭제":\n    item_delete_ui_v0944.render_item_delete_page(\n        st=st, pd=pd, core=core, page_header=page_header, section=section,\n        kpi=kpi, money=money, fmt_date=fmt_date, latest_updated_text=latest_updated_text,\n    )\n\n\n'''
    if 'elif page == "🗑️  품목 삭제":' not in source:
        anchor = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
        if anchor not in source:
            raise RuntimeError("품목 삭제 화면 삽입 위치를 찾지 못했습니다.")
        source = source.replace(anchor, handler + anchor, 1)
    return source
