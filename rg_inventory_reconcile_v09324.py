"""Manual Rocket Growth inventory comparison and confirmed ERP ledger adjustment.

v0.9.324: Coupang's *orderable* inventory is reference data, never a command
to overwrite ERP stock. Only a user-reviewed, selected and explicitly
confirmed target writes an adjustment transaction. Returned/unmatched options
cannot enter this workflow.
"""
from __future__ import annotations

from datetime import date
import math
import uuid

import pandas as pd
import streamlit as st

PAGE_LABEL = "📋  로켓그로스 재고 대사"
_PENDING = "_rg_reconcile_pending_v09324"
_MARKER = "# _rg_inventory_reconcile_v09324"


def ensure_schema(core, db=None):
    db = db or core.DEFAULT_DB
    with core._conn(db) as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS rg_reconcile_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                api_run_id INTEGER NOT NULL,
                api_synced_at TEXT NOT NULL,
                item_count INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rg_reconcile_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id INTEGER NOT NULL,
                product_id INTEGER NOT NULL,
                option_id TEXT NOT NULL,
                before_qty REAL NOT NULL,
                api_qty REAL NOT NULL,
                confirmed_qty REAL NOT NULL,
                delta REAL NOT NULL,
                inventory_txn_id INTEGER NOT NULL,
                FOREIGN KEY(batch_id) REFERENCES rg_reconcile_batches(id)
            );
        """)


def _latest_run(con):
    row = con.execute("""
        SELECT COALESCE(MAX(id),0) AS id
        FROM coupang_api_sync_runs
        WHERE sync_type='inventory' AND status='success'
    """).fetchone()
    return int(row["id"] if row else 0)


def _data(core, db=None):
    db = db or core.DEFAULT_DB
    with core._conn(db) as con:
        warehouse = con.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
        if warehouse is None:
            raise RuntimeError("쿠팡RG 창고가 없습니다.")
        wid = int(warehouse["id"])
        api_count = int(con.execute("SELECT COUNT(*) AS n FROM coupang_rg_inventory").fetchone()["n"])
        unmatched = int(con.execute("""
            SELECT COUNT(*) AS n FROM coupang_rg_inventory
            WHERE product_id IS NULL OR COALESCE(stock_type,'unmatched')='unmatched'
        """).fetchone()["n"])
        returning = int(con.execute("""
            SELECT COUNT(*) AS n FROM coupang_rg_inventory WHERE stock_type='return'
        """).fetchone()["n"])
        synced = con.execute("SELECT COALESCE(MAX(synced_at),'') AS ts FROM coupang_rg_inventory").fetchone()["ts"]
        rows = con.execute("""
            SELECT p.id AS product_id,p.option_id,p.name AS product_name,
                   COALESCE(book.qty,0) AS book_qty,
                   api.api_qty,api.sales_30d,api.synced_at
            FROM products p
            LEFT JOIN (
                SELECT product_id,SUM(qty_delta) AS qty
                FROM inventory_txns WHERE warehouse_id=?
                GROUP BY product_id
            ) AS book ON book.product_id=p.id
            LEFT JOIN (
                SELECT product_id,SUM(orderable_qty) AS api_qty,
                       SUM(COALESCE(sales_30d,0)) AS sales_30d,
                       MAX(synced_at) AS synced_at
                FROM coupang_rg_inventory
                WHERE product_id IS NOT NULL AND stock_type='normal'
                GROUP BY product_id
            ) AS api ON api.product_id=p.id
            WHERE p.active=1 AND p.option_id IS NOT NULL
              AND TRIM(p.option_id)<>''
              AND (p.item_type='finished' OR api.product_id IS NOT NULL OR p.item_code LIKE 'CP-%')
            ORDER BY CASE WHEN api.api_qty IS NULL THEN 1 ELSE 0 END,
                     ABS(COALESCE(api.api_qty,book.qty,0)-COALESCE(book.qty,0)) DESC,
                     p.name,p.id
        """, (wid,)).fetchall()
        return {
            "run_id": _latest_run(con),
            "synced_at": str(synced or ""),
            "api_count": api_count,
            "unmatched": unmatched,
            "returned": returning,
            "items": [dict(row) for row in rows],
        }


def _valid_int(value):
    try:
        n = float(value)
        if not math.isfinite(n) or n < 0 or not n.is_integer():
            raise ValueError
        return int(n)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("확정수량은 0 이상의 정수로 입력해야 합니다.") from None


def _changes_from_editor(edited, snapshot):
    lookup = {int(x["product_id"]): x for x in snapshot["items"]}
    changes = []
    seen = set()
    for _, row in edited.iterrows():
        if not bool(row["선택"]):
            continue
        pid = int(row["상품ID"])
        if pid in seen or pid not in lookup:
            raise ValueError("중복되거나 알 수 없는 상품이 선택됐습니다.")
        seen.add(pid)
        item = lookup[pid]
        if item["api_qty"] is None:
            raise ValueError(f"{item['product_name']}: API 재고가 없는 상품은 대사할 수 없습니다.")
        target = _valid_int(row["확정수량"])
        before = float(item["book_qty"])
        if abs(target - before) <= 1e-9:
            continue
        changes.append({
            "product_id": pid,
            "option_id": str(item["option_id"]),
            "name": str(item["product_name"]),
            "before_qty": before,
            "api_qty": float(item["api_qty"]),
            "target_qty": target,
        })
    return changes


def commit(core, pending, db=None):
    """All-or-nothing optimistic concurrency check + persistent ledger audit."""
    db = db or core.DEFAULT_DB
    changes = list(pending.get("changes") or [])
    if not changes:
        raise ValueError("변경할 상품이 없습니다.")
    if len(changes) != len({int(x["product_id"]) for x in changes}):
        raise ValueError("중복된 상품이 포함돼 있습니다.")
    token = str(pending.get("token") or "")
    if not token:
        raise ValueError("확정 요청 식별자가 없습니다.")
    ensure_schema(core, db)
    with core._conn(db) as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("SELECT 1 FROM rg_reconcile_batches WHERE token=?", (token,)).fetchone():
            raise ValueError("이미 처리한 확정 요청입니다. 새로 조회해 주세요.")
        if _latest_run(con) != int(pending["run_id"]):
            raise ValueError("재고 API 조회 결과가 변경됐습니다. 최신 재고를 확인하고 다시 검토해 주세요.")
        wh = con.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
        if wh is None:
            raise ValueError("쿠팡RG 창고가 없습니다.")
        wid = int(wh["id"])
        verified = []
        for item in changes:
            pid = int(item["product_id"])
            product = con.execute("""
                SELECT option_id,active FROM products WHERE id=?
            """, (pid,)).fetchone()
            if not product or int(product["active"]) != 1 or str(product["option_id"]) != item["option_id"]:
                raise ValueError(f"{item['name']}: 상품 정보가 변경됐습니다.")
            api_row = con.execute("""
                SELECT SUM(orderable_qty) AS qty
                FROM coupang_rg_inventory
                WHERE product_id=? AND stock_type='normal'
            """, (pid,)).fetchone()
            if api_row is None or api_row["qty"] is None or abs(float(api_row["qty"]) - float(item["api_qty"])) > 1e-9:
                raise ValueError(f"{item['name']}: 쿠팡 재고가 변경됐습니다.")
            current = float(con.execute("""
                SELECT COALESCE(SUM(qty_delta),0) AS qty
                FROM inventory_txns WHERE product_id=? AND warehouse_id=?
            """, (pid,wid)).fetchone()["qty"])
            if abs(current - float(item["before_qty"])) > 1e-9:
                raise ValueError(f"{item['name']}: ERP 재고가 다른 작업으로 변경됐습니다. 다시 검토해 주세요.")
            target = _valid_int(item["target_qty"])
            delta = target - current
            if abs(delta) > 1e-9:
                verified.append((item, current, target, delta))
        if not verified:
            raise ValueError("재고조정이 필요한 상품이 없습니다.")
        now = core.now_iso()
        batch = con.execute("""
            INSERT INTO rg_reconcile_batches(token,created_at,api_run_id,api_synced_at,item_count)
            VALUES (?,?,?,?,?)
        """, (token, now, int(pending["run_id"]), str(pending["synced_at"]), len(verified)))
        batch_id = int(batch.lastrowid)
        for item, before, target, delta in verified:
            txn = con.execute("""
                INSERT INTO inventory_txns
                (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                VALUES(?,?,?,?,?,?,?,?)
            """, (
                date.today().isoformat(), int(item["product_id"]), wid, float(delta),
                "수동재고대사", f"RG-RECONCILE-{batch_id}-{int(item['product_id'])}",
                f"쿠팡 API 판매가능 {item['api_qty']:g} / ERP 종전 {before:g} / 사용자 확정 {target:g}",
                now,
            ))
            con.execute("""
                INSERT INTO rg_reconcile_items
                (batch_id,product_id,option_id,before_qty,api_qty,confirmed_qty,delta,inventory_txn_id)
                VALUES(?,?,?,?,?,?,?,?)
            """, (
                batch_id,int(item["product_id"]),str(item["option_id"]),before,
                float(item["api_qty"]),target,float(delta),int(txn.lastrowid),
            ))
    return {"batch_id": batch_id, "count": len(verified),
            "delta": sum(x[3] for x in verified)}


def render_page(st_obj, pd_obj, core, api, db=None):
    db = db or core.DEFAULT_DB
    if st_obj.session_state.pop("_rg_reconcile_clear_v09324", False):
        for key in list(st_obj.session_state):
            if str(key).startswith("rg_reconcile_editor_v09324_") or key == "rg_reconcile_ack_v09324":
                st_obj.session_state.pop(key, None)
    st_obj.header("로켓그로스 재고 대사")
    completed = st_obj.session_state.pop("_rg_reconcile_completed_v09324", None)
    if completed:
        st_obj.success(f"ERP 재고 대사 {completed['count']:,}개 상품 반영 완료 · 조정번호 {completed['batch_id']}")
    st_obj.caption("쿠팡 API는 주문 가능한 수량을 보여줍니다. 입고중·판매불가 재고 등으로 물리적 재고와 다를 수 있습니다. API 조회만으로 ERP 장부재고는 바뀌지 않습니다.")
    try:
        api.ensure_schema(core, db)
        ensure_schema(core, db)
        saved = api.load_credentials(core)
        if st_obj.button("쿠팡 API 재고 새로 조회", use_container_width=False, key="rg_reconcile_fetch_v09324"):
            if saved is None:
                st_obj.error("데이터·관리 > 쿠팡 API 연동에서 연결정보를 먼저 저장하세요.")
            else:
                with st_obj.spinner("쿠팡 판매가능재고를 조회하고 있습니다..."):
                    result = api.sync_inventory(core, api.CoupangClient(saved), db)
                st_obj.session_state.pop(_PENDING, None)
                st_obj.success(f"쿠팡 API 재고 {int(result.get('rows') or 0):,}개 옵션 조회 완료. ERP 장부재고는 변경되지 않았습니다.")
        info = _data(core,db)
    except Exception as exc:
        st_obj.error(f"재고 대사 화면을 열 수 없습니다: {exc}")
        return
    if not info["api_count"] or not info["run_id"]:
        st_obj.info("쿠팡 재고를 처음 조회한 뒤 비교표를 사용할 수 있습니다. 조회 전에 API 연결정보를 저장해 주세요.")
        return

    a,b,c,d = st_obj.columns(4)
    a.metric("쿠팡 옵션 조회",f"{info['api_count']:,}개")
    b.metric("ERP 비교 대상",f"{sum(x['api_qty'] is not None for x in info['items']):,}개")
    c.metric("수량 차이",f"{sum(x['api_qty'] is not None and abs(float(x['api_qty'])-float(x['book_qty']))>1e-9 for x in info['items']):,}개")
    d.metric("미매칭 옵션",f"{info['unmatched']:,}개")
    st_obj.caption(f"쿠팡 API 최종 조회: {info['synced_at']} · 반품 옵션 {info['returned']:,}개는 대사 대상에서 제외")
    st_obj.info("확정수량은 ERP 현재 수량으로 시작합니다. 필요한 상품의 수량을 수정하고 '선택'을 체크한 다음, 변경 내용 검토와 최종 확정을 순서대로 누르세요.")

    all_items = info["items"]
    show_only_diff = st_obj.checkbox("차이가 있는 상품만 보기",value=False,key="rg_reconcile_only_diff_v09324")
    visible = [x for x in all_items if not show_only_diff or
               (x["api_qty"] is not None and abs(float(x["api_qty"])-float(x["book_qty"]))>1e-9)]
    if not visible:
        st_obj.info("조건에 맞는 비교 상품이 없습니다.")
        return
    frame = pd_obj.DataFrame([{
        "상품ID":int(x["product_id"]),
        "선택":False,
        "상품명":str(x["product_name"]),
        "옵션ID":str(x["option_id"]),
        "ERP 장부재고":float(x["book_qty"]),
        "쿠팡 판매가능재고":float(x["api_qty"]) if x["api_qty"] is not None else None,
        "차이(API-ERP)":float(x["api_qty"])-float(x["book_qty"]) if x["api_qty"] is not None else None,
        "확정수량":float(x["book_qty"]),
    } for x in visible])
    shown = st_obj.data_editor(
        frame, hide_index=True, use_container_width=True, num_rows="fixed",
        disabled=["상품ID","상품명","옵션ID","ERP 장부재고","쿠팡 판매가능재고","차이(API-ERP)"],
        column_config={
            "상품ID":None,
            "선택":st_obj.column_config.CheckboxColumn("선택",default=False),
            "상품명":st_obj.column_config.TextColumn("상품명",width="large"),
            "확정수량":st_obj.column_config.NumberColumn("확정수량",min_value=0,step=1),
        },
        key=f"rg_reconcile_editor_v09324_{info['run_id']}_{int(show_only_diff)}",
    )
    if info["unmatched"]:
        st_obj.warning(f"API 재고 중 미매칭 {info['unmatched']:,}개 옵션은 임의로 ERP 상품에 연결하거나 조정하지 않습니다.")
    if st_obj.button("선택한 상품 변경 내용 검토",type="primary",key="rg_reconcile_review_v09324"):
        try:
            changes=_changes_from_editor(shown,info)
            if not changes:
                st_obj.session_state.pop(_PENDING,None)
                st_obj.info("선택한 상품에 변경할 수량이 없습니다.")
            else:
                st_obj.session_state["rg_reconcile_ack_v09324"] = False
                st_obj.session_state[_PENDING]={
                    "token":uuid.uuid4().hex,"run_id":info["run_id"],
                    "synced_at":info["synced_at"],"changes":changes,
                }
        except Exception as exc:
            st_obj.session_state.pop(_PENDING,None)
            st_obj.error(str(exc))

    pending=st_obj.session_state.get(_PENDING)
    if pending:
        st_obj.subheader("최종 확정 전 확인")
        preview=pd_obj.DataFrame([{
            "상품명":x["name"],"옵션ID":x["option_id"],"기존 ERP":x["before_qty"],
            "쿠팡 API":x["api_qty"],"확정수량":x["target_qty"],
            "재고조정":x["target_qty"]-x["before_qty"],
        } for x in pending["changes"]])
        st_obj.dataframe(preview,hide_index=True,use_container_width=True)
        st_obj.caption("이 작업은 쿠팡 판매가능재고를 변경하지 않습니다. ERP 쿠팡RG 창고에 재고조정 이력만 추가합니다.")
        if st_obj.checkbox("변경할 상품과 수량을 확인했습니다.",key="rg_reconcile_ack_v09324"):
            if st_obj.button("최종 확정 · ERP 재고에 반영",type="primary",key="rg_reconcile_commit_v09324"):
                try:
                    outcome=commit(core,pending,db)
                    st_obj.session_state.pop(_PENDING,None)
                    st_obj.session_state["_rg_reconcile_clear_v09324"] = True
                    st_obj.session_state["_rg_reconcile_completed_v09324"] = outcome
                    st_obj.rerun()
                except Exception as exc:
                    st_obj.error(f"ERP 반영 중단 (변경 없음): {exc}")
    with st_obj.expander("최근 재고 대사 확정 이력",expanded=False):
        with core._conn(db) as con:
            rows=con.execute("""
                SELECT b.created_at AS 확정시각,p.name AS 상품명,
                       i.option_id AS 옵션ID,i.before_qty AS 종전재고,
                       i.api_qty AS API재고,i.confirmed_qty AS 확정재고,i.delta AS 조정수량
                FROM rg_reconcile_items i
                JOIN rg_reconcile_batches b ON b.id=i.batch_id
                LEFT JOIN products p ON p.id=i.product_id
                ORDER BY i.id DESC LIMIT 100
            """).fetchall()
        if rows:
            st_obj.dataframe(pd_obj.DataFrame([dict(x) for x in rows]),hide_index=True,use_container_width=True)
        else:
            st_obj.caption("아직 확정 이력이 없습니다.")


def patch_source(source):
    if _MARKER in source:
        return source
    label = '        "' + PAGE_LABEL + '",\n'
    anchor = '        "📦  재고관리",\n'
    if anchor not in source:
        raise RuntimeError("로켓그로스 재고 대사 메뉴를 추가할 위치가 없습니다.")
    source = source.replace(anchor, anchor + label, 1)
    handler_anchor = (
        "# ------------------------------\n"
        "# Inventory\n"
        "# ------------------------------\n"
        'elif page == "📦  재고관리":\n'
    )
    if handler_anchor not in source:
        raise RuntimeError("재고관리 분기 위치를 찾을 수 없습니다.")
    handler = (
        _MARKER + "\n"
        "# ------------------------------\n"
        "# RG inventory reconciliation (explicit confirmation only)\n"
        "# ------------------------------\n"
        f'elif page == "{PAGE_LABEL}":\n'
        "    rg_inventory_reconcile_v09324.render_page(st, pd, core, coupang_api_sync_v09140)\n\n\n"
    )
    return source.replace(handler_anchor, handler + handler_anchor, 1)


def apply(core, api, inventory_ui):
    # Reuse the tested v0.9.149 safety and display patches. Do NOT call its
    # apply(): it retroactively changes historical inventory and P&L behavior.
    import inventory_api_separation_v09149 as separation
    separation._patch_inventory_sync(api)
    separation._patch_inventory_ui(core, inventory_ui, core.DEFAULT_DB)
    import sidebar_groups_v0917
    for title, items in sidebar_groups_v0917._GROUPS:
        if title == "📦 재고·생산" and PAGE_LABEL not in items:
            pos = items.index("📦  재고관리") + 1
            items.insert(pos, PAGE_LABEL)
    return {"read_only_api": True, "manual_commit_only": True}
