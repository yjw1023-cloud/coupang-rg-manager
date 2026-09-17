"""v0.9.223 shared return-option confirmation.

Business rule:
- option ID in the verified master registry = normal product
- option ID outside the registry = return option and must be confirmed by user
- old automatic aliases are suggestions only
- only rows in return_alias_user_confirmations are treated as user-confirmed
"""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any
import streamlit as st


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _exists(c, table: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()} if _exists(c, table) else set()


def _ensure_confirmation_schema(core, db):
    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS return_alias_user_confirmations(
          discount_option_id TEXT PRIMARY KEY,
          parent_product_id INTEGER NOT NULL,
          confirmed_at TEXT NOT NULL)""")


def _normal_ids(core, db) -> set[str]:
    with core._conn(db) as c:
        if not _exists(c, "coupang_normal_option_registry"):
            return set()
        rows = c.execute("SELECT vendor_item_id FROM coupang_normal_option_registry").fetchall()
    return {_oid(r["vendor_item_id"]) for r in rows if _oid(r["vendor_item_id"])}


def confirmed_alias_map(core, db) -> dict[str, int]:
    _ensure_confirmation_schema(core, db)
    normals = _normal_ids(core, db)
    with core._conn(db) as c:
        rows = c.execute("SELECT discount_option_id,parent_product_id FROM return_alias_user_confirmations").fetchall()
    out = {}
    for r in rows:
        oid = _oid(r["discount_option_id"])
        if oid and oid not in normals:
            out[oid] = int(r["parent_product_id"])
    return out


def _legacy_suggestions(core, db) -> dict[str, int]:
    out = {}
    with core._conn(db) as c:
        if not _exists(c, "return_discount_aliases"):
            return out
        rows = c.execute("SELECT discount_option_id,parent_product_id FROM return_discount_aliases").fetchall()
    for r in rows:
        oid = _oid(r["discount_option_id"])
        if oid:
            try:
                out[oid] = int(r["parent_product_id"])
            except Exception:
                pass
    return out


def _normal_products(core, db) -> list[dict]:
    normals = _normal_ids(core, db)
    if not normals:
        return []
    with core._conn(db) as c:
        rows = c.execute("SELECT id,item_code,option_id,name,item_type,unit_cost,active FROM products").fetchall()
    grouped = {}
    for r in rows:
        p = dict(r)
        oid = _oid(p.get("option_id"))
        if oid in normals:
            p["option_id"] = oid
            grouped.setdefault(oid, []).append(p)

    def score(p):
        code = _oid(p.get("item_code")); oid = _oid(p.get("option_id"))
        return (
            1 if str(p.get("item_type") or "") == "finished" else 0,
            1 if code != oid else 0,
            1 if float(p.get("unit_cost") or 0) > 0 else 0,
            1 if int(p.get("active") or 0) else 0,
            -int(p.get("id") or 0),
        )

    out = [max(ps, key=score) for ps in grouped.values()]
    out.sort(key=lambda p: (str(p.get("name") or ""), str(p.get("option_id") or "")))
    return out


def normal_option_to_product(core, db) -> dict[str, int]:
    return {_oid(p.get("option_id")): int(p["id"]) for p in _normal_products(core, db)}


def _product_label(p: dict) -> str:
    status = "" if int(p.get("active") or 0) else " · 판매중단/보관"
    return f"{p.get('name','')} · 옵션ID {p.get('option_id','')}{status}"


def _candidate_score(item: dict, p: dict) -> float:
    a = str(item.get("name") or "").lower().strip()
    b = str(p.get("name") or "").lower().strip()
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _save_alias(core, db, child_oid: str, child_name: str, parent_pid: int):
    child_oid = _oid(child_oid)
    if not child_oid or child_oid in _normal_ids(core, db):
        raise ValueError("정상 원장 옵션ID는 반품으로 저장할 수 없습니다.")
    now = core.now_iso()
    _ensure_confirmation_schema(core, db)
    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS return_discount_aliases(
          discount_option_id TEXT PRIMARY KEY,parent_product_id INTEGER NOT NULL,
          discount_name TEXT,match_method TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""")
        c.execute("""INSERT INTO return_discount_aliases
          (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
          VALUES(?,?,?,?,?,?) ON CONFLICT(discount_option_id) DO UPDATE SET
          parent_product_id=excluded.parent_product_id,discount_name=excluded.discount_name,
          match_method=excluded.match_method,updated_at=excluded.updated_at""",
          (child_oid,int(parent_pid),child_name,"manual_user_shared",now,now))
        c.execute("""INSERT INTO return_alias_user_confirmations(discount_option_id,parent_product_id,confirmed_at)
          VALUES(?,?,?) ON CONFLICT(discount_option_id) DO UPDATE SET
          parent_product_id=excluded.parent_product_id,confirmed_at=excluded.confirmed_at""",
          (child_oid,int(parent_pid),now))
        c.execute("""CREATE TABLE IF NOT EXISTS system_hidden_products(
          product_id INTEGER PRIMARY KEY,reason TEXT NOT NULL,hidden_at TEXT NOT NULL)""")
        if _exists(c, "products"):
            for r in c.execute("SELECT id FROM products WHERE CAST(option_id AS TEXT)=?", (child_oid,)).fetchall():
                pid = int(r["id"])
                c.execute("""INSERT INTO system_hidden_products(product_id,reason,hidden_at)
                  VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET reason=excluded.reason,hidden_at=excluded.hidden_at""",
                  (pid,"return_alias_user_confirmed_v09223",now))
                c.execute("UPDATE products SET active=0 WHERE id=?", (pid,))


def _row_name(core, db, product_id) -> str:
    try:
        with core._conn(db) as c:
            r = c.execute("SELECT name FROM products WHERE id=?", (int(product_id),)).fetchone()
        return str(r["name"] or "") if r else ""
    except Exception:
        return ""


def period_unmatched(core, db, start, end) -> list[dict]:
    normals = _normal_ids(core, db)
    confirmed = set(confirmed_alias_map(core, db))
    suggested = _legacy_suggestions(core, db)
    with core._conn(db) as c:
        if not (_exists(c, "imports") and _exists(c, "sales_stats")):
            return []
        scols = _cols(c, "sales_stats")
        imports = c.execute("""SELECT id FROM imports WHERE data_type='sales_stats'
          AND period_start>=? AND period_end<=?""", (str(start),str(end))).fetchall()
        if not imports:
            return []
        ids = [int(r["id"]) for r in imports]
        marks = ",".join("?" for _ in ids)
        pid_expr = "product_id" if "product_id" in scols else "0 AS product_id"
        oid_expr = "option_id" if "option_id" in scols else "'' AS option_id"
        if "sales_qty" in scols and "net_qty" in scols:
            qty_expr = "SUM(CASE WHEN COALESCE(sales_qty,0)<>0 THEN sales_qty ELSE COALESCE(net_qty,0) END)"
        elif "sales_qty" in scols:
            qty_expr = "SUM(COALESCE(sales_qty,0))"
        else:
            qty_expr = "SUM(COALESCE(net_qty,0))" if "net_qty" in scols else "0"
        rows = c.execute(f"SELECT {pid_expr},{oid_expr},{qty_expr} qty FROM sales_stats WHERE import_id IN ({marks}) GROUP BY product_id,option_id", ids).fetchall()

    out = {}
    for r in rows:
        oid = _oid(r["option_id"])
        if not oid or oid in normals or oid in confirmed:
            continue
        try: pid = int(r["product_id"] or 0)
        except Exception: pid = 0
        out[oid] = {
            "option_id": oid,
            "product_id": pid,
            "name": _row_name(core, db, pid) or f"옵션ID {oid}",
            "qty": float(r["qty"] or 0),
            "suggested_parent_id": suggested.get(oid),
        }
    return list(out.values())


def frame_unmatched(core, db, frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    normals = _normal_ids(core, db)
    confirmed = set(confirmed_alias_map(core, db))
    suggested = _legacy_suggestions(core, db)
    oidcol = next((c for c in ("옵션ID","쿠팡 옵션ID","option_id") if c in frame.columns), None)
    pidcol = "product_id" if "product_id" in frame.columns else None
    if oidcol is None:
        return []
    out = {}
    for _, row in frame.iterrows():
        oid = _oid(row.get(oidcol))
        if not oid or oid in normals or oid in confirmed:
            continue
        try: pid = int(float(row.get(pidcol) or 0)) if pidcol else 0
        except Exception: pid = 0
        name = str(row.get("상품명") or row.get("아이템") or _row_name(core, db, pid) or f"옵션ID {oid}")
        qty = row.get("판매수량") if "판매수량" in frame.columns else None
        try: qty = float(qty) if qty is not None else None
        except Exception: qty = None
        out[oid] = {"option_id":oid,"product_id":pid,"name":name,"qty":qty,"suggested_parent_id":suggested.get(oid)}
    return list(out.values())


def _ask(core, db, items: list[dict], source: str) -> bool:
    if not items:
        return False
    products = _normal_products(core, db)
    if not products:
        st.error("정상 원상품 목록을 불러오지 못해 반품상품을 매칭할 수 없습니다.")
        st.stop()
    item = items[0]
    oid = _oid(item.get("option_id")); name = str(item.get("name") or f"옵션ID {oid}")
    ordered = sorted(products, key=lambda p: _candidate_score(item,p), reverse=True)
    suggested = int(item.get("suggested_parent_id") or 0)
    if suggested:
        ordered.sort(key=lambda p: 0 if int(p["id"]) == suggested else 1)
    ids = [int(p["id"]) for p in ordered]; by_id = {int(p["id"]):p for p in ordered}
    st.warning(f"{source}에서 원장에 없는 판매 옵션을 발견했습니다. 이 반품상품의 원상품을 확인해 주세요.")
    with st.container(border=True):
        st.markdown(f"**매칭 확인: {name}**")
        extra = f" · 판매수량 {item.get('qty'):g}" if isinstance(item.get("qty"),(int,float)) else ""
        old = " · 기존 자동추천을 첫 번째로 표시" if suggested else ""
        st.caption(f"옵션ID {oid}{extra}{old} · 미확정 {len(items)}건 중 1건")
        selected = st.selectbox("이 반품상품의 원상품", ids, format_func=lambda pid:_product_label(by_id[int(pid)]), key=f"_rg_return_confirm_v09223_{source}_{oid}")
        if st.button("이 원상품으로 확정", type="primary", key=f"_rg_return_confirm_save_v09223_{source}_{oid}"):
            _save_alias(core, db, oid, name, int(selected))
            st.rerun()
    st.stop()
    return True


def ensure_period_mappings(core, db, start, end, source: str):
    return _ask(core, db, period_unmatched(core, db, start, end), source)


def ensure_frame_mappings(core, db, frame, source: str):
    return _ask(core, db, frame_unmatched(core, db, frame), source)


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db); _ensure_confirmation_schema(core, db)
    try:
        import sales_analysis_v09186 as sales
        old = getattr(sales,"_sales_stats",None)
        if callable(old) and not getattr(old,"_rg_shared_match_v09223",False):
            def wrapped(core_obj,db_path,start,end,_old=old):
                ensure_period_mappings(core_obj,db_path,start,end,"판매분석")
                return _old(core_obj,db_path,start,end)
            wrapped._rg_shared_match_v09223=True; sales._sales_stats=wrapped
    except Exception:
        pass
    try:
        import provisional_pnl_ui_v0913 as pnl
        old = getattr(pnl,"_apply_existing_rules",None)
        if callable(old) and not getattr(old,"_rg_shared_match_v09223",False):
            def wrapped_pnl(core_obj,db_path,data,_old=old):
                ensure_frame_mappings(core_obj,db_path,data,"잠정손익")
                return _old(core_obj,db_path,data)
            wrapped_pnl._rg_shared_match_v09223=True; pnl._apply_existing_rules=wrapped_pnl
    except Exception:
        pass
    core.rg_confirmed_return_alias_map=lambda db_path=None: confirmed_alias_map(core,db_path or core.DEFAULT_DB)
    core.rg_ensure_return_mappings_for_period=lambda start,end,source="ERP",db_path=None: ensure_period_mappings(core,db_path or core.DEFAULT_DB,start,end,source)
    core.rg_ensure_return_mappings_for_frame=lambda frame,source="ERP",db_path=None: ensure_frame_mappings(core,db_path or core.DEFAULT_DB,frame,source)
    return {"ok":True,"confirmation_table":"return_alias_user_confirmations","policy":"explicit_user_confirmation_only"}
