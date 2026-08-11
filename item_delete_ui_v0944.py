"""RG Manager v0.9.46 item deletion and manual return-option cleanup.

All deletion/restoration targets are shown as checkbox tables instead of a
search/selectbox picker. Return-option cleanup supports multi-select and keeps the
explicit child -> original mapping because that mapping is required for future
return-stock deduction, original cost attribution and P&L classification.
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
            if n:
                out.append(f"BOM 연결 {n:,}건")
        if _exists(con, "production_orders"):
            n = int(con.execute(
                "SELECT COUNT(*) n FROM production_orders WHERE parent_product_id=?", (int(pid),)
            ).fetchone()["n"] or 0)
            if n:
                out.append(f"생산이력 {n:,}건")
        if _exists(con, "purchase_lines"):
            cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(purchase_lines)")}
            if "product_id" in cols:
                n = int(con.execute(
                    "SELECT COUNT(*) n FROM purchase_lines WHERE product_id=?", (int(pid),)
                ).fetchone()["n"] or 0)
                if n:
                    out.append(f"매입이력 {n:,}건")
        if _exists(con, "inventory_txns"):
            n = int(con.execute(
                """SELECT COUNT(*) n FROM inventory_txns
                   WHERE product_id=? AND COALESCE(txn_type,'') NOT IN ('판매차감','반품할인판매차감')""",
                (int(pid),),
            ).fetchone()["n"] or 0)
            if n:
                out.append(f"판매 외 재고이력 {n:,}건")
    return out


def _archive(core, pid):
    bal = _balances(core, pid)
    if bal:
        raise ValueError(
            "현재고가 0이 아니므로 일반 삭제할 수 없습니다: "
            + ", ".join(f"{w} {q:g}" for w, q in bal)
        )
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET active=0,updated_at=? WHERE id=?",
            (core.now_iso(), int(pid)),
        )


def _is_alias(core, oid):
    if not oid:
        return False
    with _conn(core) as con:
        return bool(
            _exists(con, "return_discount_aliases")
            and con.execute(
                "SELECT 1 FROM return_discount_aliases WHERE discount_option_id=?",
                (str(oid),),
            ).fetchone()
        )


def _restore(core, pid, oid):
    if _is_alias(core, oid):
        raise ValueError("반품 할인판매 alias로 등록된 옵션ID는 정상 품목으로 복원할 수 없습니다.")
    with _conn(core) as con:
        con.execute(
            "UPDATE products SET active=1,updated_at=? WHERE id=?",
            (core.now_iso(), int(pid)),
        )


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
        raise ValueError(
            "정상 관리상품일 가능성이 있는 이력이 있어 반품코드로 정리하지 않았습니다: "
            + " / ".join(blockers)
        )

    import return_discount_v099 as rd

    db = core.DEFAULT_DB
    rd._ensure_schema(core, db)
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
            (oid, int(parent_id), str(child["name"] or ""), "manual_item_delete_v0946", now, now),
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
        con.execute(
            "UPDATE products SET active=0,updated_at=? WHERE id=?",
            (core.now_iso(), int(child_id)),
        )
    return converted, str(parent["name"] or "")


def _selection_editor(st, df, key, include_reason=False):
    view = pd.DataFrame({
        "선택": False,
        "_id": df["id"].astype(int),
        "품목코드": [_code(r.item_code, r.option_id) for r in df.itertuples()],
        "상품명": df["name"].fillna(""),
        "쿠팡 옵션ID": df["option_id"].fillna(""),
        "자체창고": df["own_stock"].fillna(0),
        "쿠팡RG": df["rg_stock"].fillna(0),
        "반품창고": df["return_stock"].fillna(0),
    })
    if include_reason:
        reasons = []
        for pid in view["_id"]:
            blockers = _blockers(st.session_state.get("_rg_core_for_delete", None), pid) if False else []
            reasons.append("")
        view["안전확인"] = reasons

    edited = st.data_editor(
        view,
        key=key,
        hide_index=True,
        use_container_width=True,
        height=min(650, max(220, 38 * (len(view) + 1))),
        disabled=[c for c in view.columns if c not in {"선택"}],
        column_config={"_id": None},
    )
    if edited is None or edited.empty:
        return []
    return [int(x) for x in edited.loc[edited["선택"] == True, "_id"].tolist()]


def _return_list(st, pd, core, all_df):
    children = all_df[(all_df["active"] == 1) & all_df["option_id"].notna()].copy()
    if children.empty:
        st.info("정리할 쿠팡RG 품목이 없습니다.")
        return

    # Put likely return children near the top: no protected management history and
    # negative/zero RG balance first. User still explicitly checks the target.
    children["_blocked"] = children["id"].map(lambda x: bool(_blockers(core, int(x))))
    children["_suspect"] = (
        (~children["_blocked"])
        & (pd.to_numeric(children["rg_stock"], errors="coerce").fillna(0) <= 0)
    )
    children = children.sort_values(["_suspect", "name", "item_code"], ascending=[False, True, True])

    table = pd.DataFrame({
        "선택": False,
        "_id": children["id"].astype(int),
        "품목코드": [_code(r.item_code, r.option_id) for r in children.itertuples()],
        "상품명": children["name"].fillna(""),
        "옵션ID": children["option_id"].fillna(""),
        "자체창고": children["own_stock"].fillna(0),
        "쿠팡RG": children["rg_stock"].fillna(0),
        "반품창고": children["return_stock"].fillna(0),
        "정리상태": [
            ("정리 가능" if not _blockers(core, int(pid)) else "보호 이력 있음")
            for pid in children["id"]
        ],
    })
    edited = st.data_editor(
        table,
        key="return_cleanup_list_v0946",
        hide_index=True,
        use_container_width=True,
        height=min(650, max(260, 38 * (len(table) + 1))),
        disabled=[c for c in table.columns if c != "선택"],
        column_config={"_id": None},
    )
    selected_ids = [int(x) for x in edited.loc[edited["선택"] == True, "_id"].tolist()]
    if not selected_ids:
        st.caption("정리할 반품 품목의 체크박스를 선택하세요.")
        return

    st.markdown("### 선택한 반품코드의 정상 원상품 확인")
    parent_map = {}
    blocked_selected = []
    for child_id in selected_ids:
        child = children[children["id"] == child_id].iloc[0]
        blockers = _blockers(core, child_id)
        title = f"{_code(child['item_code'], child['option_id'])} · {child['name']}"
        with st.expander(title, expanded=True):
            if blockers:
                blocked_selected.append(child_id)
                st.error("안전상 정리할 수 없습니다: " + " / ".join(blockers))
                continue

            parents = all_df[
                (all_df["active"] == 1)
                & all_df["option_id"].notna()
                & (all_df["id"] != child_id)
            ].copy()
            parents["score"] = parents["name"].map(lambda x: _score(child["name"], x))
            parents = parents.sort_values(["score", "name"], ascending=[False, True])
            if parents.empty:
                blocked_selected.append(child_id)
                st.error("연결할 정상 원상품 후보가 없습니다.")
                continue
            labels = {
                int(r.id): f"{_code(r.item_code, r.option_id)} | {r.name} | 유사도 {float(r.score):.0%}"
                for r in parents.itertuples()
            }
            parent_id = int(st.selectbox(
                "정상 원상품",
                list(labels),
                format_func=lambda x, labels=labels: labels[int(x)],
                key=f"ret_parent_v0946_{child_id}",
            ))
            parent_map[child_id] = parent_id

    ready_ids = [pid for pid in selected_ids if pid in parent_map and pid not in blocked_selected]
    confirm = st.checkbox(
        f"선택한 반품코드 {len(ready_ids):,}개를 위 정상 원상품에 연결하고 품목목록에서 삭제합니다.",
        key="ret_bulk_confirm_v0946",
        disabled=not ready_ids,
    )
    if st.button(
        "선택 반품코드 일괄 정리",
        type="primary",
        disabled=not ready_ids or not confirm or bool(blocked_selected),
        key="ret_bulk_submit_v0946",
    ):
        try:
            converted = 0
            for child_id in ready_ids:
                n, _ = _manual_return(core, child_id, parent_map[child_id])
                converted += int(n or 0)
            st.success(
                f"반품코드 {len(ready_ids):,}개를 정리했습니다. 기존 판매자료 {converted:,}개 import도 반품판매로 재분류했습니다."
            )
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def _general_delete_list(st, core):
    active = _products(core)
    active = active[active["active"] == 1].copy()
    if active.empty:
        st.info("삭제할 품목이 없습니다.")
        return
    table = pd.DataFrame({
        "선택": False,
        "_id": active["id"].astype(int),
        "품목코드": [_code(r.item_code, r.option_id) for r in active.itertuples()],
        "상품명": active["name"].fillna(""),
        "자체창고": active["own_stock"].fillna(0),
        "쿠팡RG": active["rg_stock"].fillna(0),
        "반품창고": active["return_stock"].fillna(0),
    })
    table["삭제가능"] = [
        "가능" if not _balances(core, int(pid)) else "재고 있음"
        for pid in table["_id"]
    ]
    edited = st.data_editor(
        table,
        key="general_delete_list_v0946",
        hide_index=True,
        use_container_width=True,
        height=min(650, max(260, 38 * (len(table) + 1))),
        disabled=[c for c in table.columns if c != "선택"],
        column_config={"_id": None},
    )
    selected = [int(x) for x in edited.loc[edited["선택"] == True, "_id"].tolist()]
    if not selected:
        st.caption("삭제할 품목의 체크박스를 선택하세요.")
        return
    blocked = [pid for pid in selected if _balances(core, pid)]
    if blocked:
        st.error("선택 품목 중 현재고가 남아 있는 품목이 있어 일반 삭제할 수 없습니다.")
    confirm = st.checkbox(
        f"선택한 품목 {len(selected):,}개를 삭제(보관처리)합니다.",
        key="general_bulk_confirm_v0946",
        disabled=bool(blocked),
    )
    if st.button(
        "선택 품목 일괄 삭제",
        type="primary",
        disabled=bool(blocked) or not confirm,
        key="general_bulk_submit_v0946",
    ):
        try:
            for pid in selected:
                _archive(core, pid)
            st.success(f"품목 {len(selected):,}개를 삭제(보관처리)했습니다.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def _restore_list(st, core):
    archived = _products(core)
    archived = archived[archived["active"] == 0].copy()
    if archived.empty:
        st.info("삭제된 품목이 없습니다.")
        return
    table = pd.DataFrame({
        "선택": False,
        "_id": archived["id"].astype(int),
        "품목코드": [_code(r.item_code, r.option_id) for r in archived.itertuples()],
        "상품명": archived["name"].fillna(""),
        "옵션ID": archived["option_id"].fillna(""),
        "복원가능": [
            "반품 alias" if _is_alias(core, oid) else "가능"
            for oid in archived["option_id"]
        ],
    })
    edited = st.data_editor(
        table,
        key="restore_list_v0946",
        hide_index=True,
        use_container_width=True,
        height=min(650, max(260, 38 * (len(table) + 1))),
        disabled=[c for c in table.columns if c != "선택"],
        column_config={"_id": None},
    )
    selected = [int(x) for x in edited.loc[edited["선택"] == True, "_id"].tolist()]
    if not selected:
        st.caption("복원할 품목의 체크박스를 선택하세요.")
        return
    bad = []
    for pid in selected:
        row = archived[archived["id"] == pid].iloc[0]
        if _is_alias(core, row["option_id"]):
            bad.append(pid)
    if bad:
        st.error("선택 품목 중 반품 할인판매 alias가 있어 복원할 수 없습니다.")
    if st.button(
        "선택 품목 일괄 복원",
        type="primary",
        disabled=bool(bad),
        key="restore_bulk_submit_v0946",
    ):
        try:
            for pid in selected:
                row = archived[archived["id"] == pid].iloc[0]
                _restore(core, pid, row["option_id"])
            st.success(f"품목 {len(selected):,}개를 복원했습니다.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))


def render_item_delete_page(st, pd, core, page_header, section, **_kwargs):
    page_header(
        "품목 삭제",
        "잘못 등록된 품목과 쿠팡 반품 할인판매 옵션을 정리합니다.",
        eyebrow="ITEM CLEANUP",
    )
    st.info(
        "삭제는 물리 삭제가 아니라 보관처리입니다. 과거 판매·재고·손익 기록은 유지됩니다. "
        "목록에서 체크한 품목만 처리합니다."
    )
    tabs = st.tabs(["반품코드 정리", "일반 품목 삭제", "삭제품목 복원"])

    with tabs[0]:
        section(
            "반품 할인판매 코드 정리",
            "목록에서 반품용 옵션ID를 체크한 뒤 실제 정상 원상품을 확인합니다. 연결 정보는 이후 반품재고·원가·손익 처리에 사용합니다.",
        )
        _return_list(st, pd, core, _products(core))

    with tabs[1]:
        section("일반 품목 삭제", "목록에서 현재고가 0인 일반 품목을 체크해 업무 목록에서 제외합니다.")
        _general_delete_list(st, core)

    with tabs[2]:
        section("삭제품목 복원", "삭제된 품목을 목록에서 체크해 다시 사용 상태로 돌립니다. 반품 alias는 복원할 수 없습니다.")
        _restore_list(st, core)


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
