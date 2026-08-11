"""RG Manager v0.9.44 dedicated item deletion / return-code cleanup UI.

Deletion is archival, never a physical DELETE, so historical sales/inventory/P&L
foreign keys remain valid.

The special returned-item cleanup path lets the user explicitly identify an
incorrect Coupang return-discount option and its original managed product. It:
- records return_discount_aliases for future imports;
- converts already-imported ordinary sale inventory postings to returned-item
  discount sale postings using the existing return_discount_v099 machinery;
- archives the child option so it no longer circulates as a managed SKU.
"""
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


def _display_code(item_code, option_id=None):
    code = str(item_code or "").strip()
    if re.fullmatch(r"CP-\d+", code):
        return str(option_id or code[3:])
    return code


def _load_products(core):
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


def _label(row):
    status = "사용" if int(row.active or 0) else "삭제됨"
    return f"{_display_code(row.item_code, row.option_id)} | {row.name} | {status}"


def _balances(core, product_id):
    with _conn(core) as con:
        rows = con.execute(
            """SELECT COALESCE(w.name,'미지정') warehouse,COALESCE(SUM(t.qty_delta),0) qty
               FROM inventory_txns t LEFT JOIN warehouses w ON w.id=t.warehouse_id
               WHERE t.product_id=? GROUP BY t.warehouse_id,w.name ORDER BY w.name""",
            (int(product_id),),
        ).fetchall()
    return [(str(r["warehouse"]), float(r["qty"] or 0)) for r in rows if abs(float(r["qty"] or 0)) > 1e-9]


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _return_cleanup_blockers(core, product_id):
    """Return evidence that this row is more than a sales-only return child."""
    blockers = []
    with _conn(core) as con:
        if _table_exists(con, "bom_items"):
            n = con.execute(
                "SELECT COUNT(*) n FROM bom_items WHERE parent_product_id=? OR component_product_id=?",
                (int(product_id), int(product_id)),
            ).fetchone()["n"]
            if int(n or 0):
                blockers.append(f"BOM 연결 {int(n):,}건")
        if _table_exists(con, "production_orders"):
            n = con.execute(
                "SELECT COUNT(*) n FROM production_orders WHERE parent_product_id=?",
                (int(product_id),),
            ).fetchone()["n"]
            if int(n or 0):
                blockers.append(f"생산이력 {int(n):,}건")
        if _table_exists(con, "purchase_lines"):
            cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(purchase_lines)")}
            if "product_id" in cols:
                n = con.execute(
                    "SELECT COUNT(*) n FROM purchase_lines WHERE product_id=?", (int(product_id),)
                ).fetchone()["n"]
                if int(n or 0):
                    blockers.append(f"매입이력 {int(n):,}건")
        if _table_exists(con, "inventory_txns"):
            n = con.execute(
                """SELECT COUNT(*) n FROM inventory_txns
                   WHERE product_id=?
                     AND COALESCE(txn_type,'') NOT IN ('판매차감','반품할인판매차감')""",
                (int(product_id),),
            ).fetchone()["n"]
            if int(n or 0):
                blockers.append(f"판매 외 재고이력 {int(n):,}건")
    return blockers


def _archive_general(core, product_id):
    balances = _balances(core, product_id)
    if balances:
        detail = ", ".join(f"{w} {q:g}" for w, q in balances)
        raise ValueError(f"현재고가 0이 아니므로 일반 삭제할 수 없습니다: {detail}")
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET active=0,updated_at=? WHERE id=?",
            (core.now_iso(), int(product_id)),
        )


def _is_return_alias(core, option_id):
    with _conn(core) as con:
        if not _table_exists(con, "return_discount_aliases"):
            return False
        row = con.execute(
            "SELECT 1 FROM return_discount_aliases WHERE discount_option_id=?",
            (str(option_id or ""),),
        ).fetchone()
        return row is not None


def _restore(core, product_id, option_id):
    if option_id and _is_return_alias(core, option_id):
        raise ValueError("반품 할인판매 alias로 등록된 옵션ID는 정상 품목으로 복원할 수 없습니다.")
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET active=1,updated_at=? WHERE id=?",
            (core.now_iso(), int(product_id)),
        )


def _name_score(a, b):
    def norm(x):
        return re.sub(r"[^0-9a-z가-힣]+", "", str(x or "").lower())
    aa, bb = norm(a), norm(b)
    if not aa or not bb:
        return 0.0
    shorter, longer = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
    score = SequenceMatcher(None, aa, bb).ratio()
    if len(shorter) >= 6 and shorter in longer:
        score = max(score, 0.94)
    return score


def _manual_return_cleanup(core, child_id, parent_id):
    if int(child_id) == int(parent_id):
        raise ValueError("반품코드와 정상 원상품은 서로 달라야 합니다.")

    import return_discount_v099 as rd

    db = core.DEFAULT_DB
    rd._ensure_schema(core, db)
    blockers = _return_cleanup_blockers(core, child_id)
    if blockers:
        raise ValueError(
            "이 품목에는 정상 관리상품일 가능성이 있는 이력이 있어 반품코드로 자동 정리하지 않았습니다: "
            + " / ".join(blockers)
        )

    with _conn(core) as con:
        child = con.execute(
            "SELECT id,item_code,option_id,name FROM products WHERE id=?", (int(child_id),)
        ).fetchone()
        parent = con.execute(
            "SELECT id,item_code,option_id,name,active FROM products WHERE id=?", (int(parent_id),)
        ).fetchone()
    if not child or not parent:
        raise ValueError("대상 품목을 찾지 못했습니다.")
    oid = str(child["option_id"] or "").strip()
    if not oid.isdigit():
        raise ValueError("반품코드 정리는 쿠팡 옵션ID가 있는 품목만 가능합니다.")
    if int(parent["active"] or 0) != 1:
        raise ValueError("정상 원상품은 현재 사용중인 품목이어야 합니다.")

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
    with _conn(core) as con:
        sales_exists = _table_exists(con, "sales_stats")
        sales_rows = []
        if sales_exists:
            if amount_col:
                sales_rows = con.execute(
                    f'''SELECT import_id,COALESCE(SUM(net_qty),0) qty,
                               COALESCE(SUM("{amount_col}"),0) amount
                        FROM sales_stats WHERE product_id=? GROUP BY import_id''',
                    (int(child_id),),
                ).fetchall()
            else:
                sales_rows = con.execute(
                    """SELECT import_id,COALESCE(SUM(net_qty),0) qty
                       FROM sales_stats WHERE product_id=? GROUP BY import_id""",
                    (int(child_id),),
                ).fetchall()

    converted = 0
    for sr in sales_rows:
        import_id = int(sr["import_id"])
        qty = float(sr["qty"] or 0)
        parsed = [{
            "option_id": oid,
            "name": str(child["name"] or ""),
            "name_key": rd._name_key(child["name"]),
            "qty": qty,
            "amount": float(sr["amount"] or 0) if amount_col else None,
            "amount_known": bool(amount_col),
        }]
        rd._post_discount(core, db, import_id, parsed, {oid: int(parent_id)})
        converted += 1

    with _conn(core) as con:
        con.execute(
            "UPDATE products SET active=0,updated_at=? WHERE id=?",
            (core.now_iso(), int(child_id)),
        )

    return converted, str(parent["name"] or "")


def _select_product(st, df, label, key, query_key, placeholder, empty_message):
    q = st.text_input("검색", key=query_key, placeholder=placeholder).strip().lower()
    filtered = df.copy()
    if q:
        mask = (
            filtered["name"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
            | filtered["item_code"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
            | filtered["option_id"].fillna("").astype(str).str.lower().str.contains(q, regex=False)
        )
        filtered = filtered.loc[mask]
    if filtered.empty:
        st.info(empty_message)
        return None
    labels = {int(r.id): _label(r) for r in filtered.itertuples()}
    pid = st.selectbox(label, list(labels), format_func=lambda x: labels[int(x)], key=key)
    return int(pid)


def render_item_delete_page(st, pd, core, page_header, section, **_kwargs):
    page_header(
        "품목 삭제",
        "잘못 등록된 품목을 보관처리하고 쿠팡 반품 할인판매 옵션을 정상 원상품에 연결합니다.",
        eyebrow="ITEM CLEANUP",
    )
    st.info(
        "삭제는 DB 행을 실제로 지우지 않습니다. 과거 판매·재고·손익 이력은 보존하고 품목을 업무 목록에서 제외합니다. "
        "쿠팡 반품 옵션은 '반품코드 정리'를 사용하세요."
    )

    tabs = st.tabs(["반품코드 정리", "일반 품목 삭제", "삭제품목 복원"])

    with tabs[0]:
        section(
            "반품 할인판매 코드 정리",
            "잘못 생성된 쿠팡 반품 옵션ID를 선택하고 실제 정상 원상품을 지정합니다. 이후 같은 반품 옵션ID는 새 품목으로 만들지 않습니다.",
        )
        all_df = _load_products(core)
        children = all_df[(all_df["active"] == 1) & all_df["option_id"].notna()].copy()
        child_id = _select_product(
            st, children, "삭제할 반품 품목", "return_cleanup_child",
            "return_cleanup_child_q", "옵션ID 또는 상품명", "대상 품목이 없습니다."
        )
        if child_id is not None:
            child = children[children["id"] == child_id].iloc[0]
            balances = _balances(core, child_id)
            if balances:
                st.caption("현재 표시 재고: " + " · ".join(f"{w} {q:g}" for w, q in balances))

            parents = all_df[
                (all_df["active"] == 1)
                & all_df["option_id"].notna()
                & (all_df["id"] != child_id)
            ].copy()
            parents["_score"] = parents["name"].map(lambda x: _name_score(child["name"], x))
            parents = parents.sort_values(["_score", "name"], ascending=[False, True])
            labels = {
                int(r.id): f"{_display_code(r.item_code, r.option_id)} | {r.name} | 유사도 {r._score:.0%}"
                for r in parents.itertuples()
            }
            if labels:
                parent_id = st.selectbox(
                    "정상 원상품", list(labels), format_func=lambda x: labels[int(x)],
                    key="return_cleanup_parent"
                )
                parent = parents[parents["id"] == int(parent_id)].iloc[0]
                st.write(
                    f"**반품코드:** {_display_code(child['item_code'], child['option_id'])} · {child['name']}  \n"
                    f"**정상 원상품:** {_display_code(parent['item_code'], parent['option_id'])} · {parent['name']}"
                )
                blockers = _return_cleanup_blockers(core, child_id)
                if blockers:
                    st.error("안전상 정리할 수 없습니다: " + " / ".join(blockers))
                confirm = st.checkbox(
                    "이 품목이 쿠팡이 반품 할인판매에 부여한 별도 옵션ID이고, 위 정상 원상품과 연결되는 것이 맞습니다.",
                    key="return_cleanup_confirm",
                )
                if st.button(
                    "반품코드 정리 및 품목 삭제",
                    type="primary",
                    disabled=bool(blockers) or not confirm,
                    key="return_cleanup_submit",
                ):
                    try:
                        converted, parent_name = _manual_return_cleanup(core, child_id, int(parent_id))
                        st.success(
                            f"반품 옵션을 {parent_name}에 연결하고 품목목록에서 삭제했습니다. "
                            f"기존 판매자료 {converted:,}개 import도 반품 할인판매로 재분류했습니다."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            else:
                st.info("연결할 정상 쿠팡RG 품목이 없습니다.")

    with tabs[1]:
        section("일반 품목 삭제", "현재고가 0인 일반 품목을 목록에서 제외합니다. 과거 이력은 보존합니다.")
        all_df = _load_products(core)
        active = all_df[all_df["active"] == 1].copy()
        pid = _select_product(
            st, active, "삭제할 품목", "general_delete_pid",
            "general_delete_q", "품목코드 또는 상품명", "삭제할 품목이 없습니다."
        )
        if pid is not None:
            row = active[active["id"] == pid].iloc[0]
            balances = _balances(core, pid)
            if balances:
                st.error("현재고가 남아 있어 일반 삭제할 수 없습니다.")
                st.caption(" · ".join(f"{w}: {q:g}" for w, q in balances))
            confirm = st.checkbox(
                f"{_display_code(row['item_code'], row['option_id'])} · {row['name']} 품목을 삭제합니다.",
                key="general_delete_confirm",
            )
            if st.button(
                "선택 품목 삭제", type="primary",
                disabled=bool(balances) or not confirm, key="general_delete_submit"
            ):
                try:
                    _archive_general(core, pid)
                    st.success("품목을 삭제(보관처리)했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    with tabs[2]:
        section("삭제품목 복원", "일반 삭제한 품목을 다시 사용 상태로 돌립니다. 반품 alias 품목은 복원할 수 없습니다.")
        all_df = _load_products(core)
        archived = all_df[all_df["active"] == 0].copy()
        pid = _select_product(
            st, archived, "복원할 품목", "restore_delete_pid",
            "restore_delete_q", "품목코드 또는 상품명", "삭제된 품목이 없습니다."
        )
        if pid is not None:
            row = archived[archived["id"] == pid].iloc[0]
            alias = _is_return_alias(core, row["option_id"])
            if alias:
                st.warning("이 옵션ID는 반품 할인판매 alias로 등록되어 있어 정상 품목으로 복원할 수 없습니다.")
            if st.button("선택 품목 복원", type="primary", disabled=alias, key="restore_delete_submit"):
                try:
                    _restore(core, pid, row["option_id"])
                    st.success("품목을 복원했습니다.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def patch_source(source: str) -> str:
    menu_label = '        "🗑️  품목 삭제",\n'
    if menu_label not in source:
        anchor = '        "📋  품목관리",\n'
        if anchor not in source:
            raise RuntimeError("품목 삭제 메뉴를 추가할 품목관리 위치를 찾지 못했습니다.")
        source = source.replace(anchor, anchor + menu_label, 1)

    handler = '''# ------------------------------\n# Item delete / return cleanup\n# ------------------------------\nelif page == "🗑️  품목 삭제":\n    item_delete_ui_v0944.render_item_delete_page(\n        st=st, pd=pd, core=core, page_header=page_header, section=section,\n        kpi=kpi, money=money, fmt_date=fmt_date, latest_updated_text=latest_updated_text,\n    )\n\n\n'''
    if 'elif page == "🗑️  품목 삭제":' not in source:
        anchor = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
        if anchor not in source:
            raise RuntimeError("품목 삭제 화면을 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, handler + anchor, 1)
    return source
