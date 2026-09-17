"""v0.9.217 shared unresolved return-sale matcher.

Any ERP screen that encounters a Coupang option outside the verified normal-option
registry and outside the shared return alias table must ask the user which original
product it belongs to. The answer is written once to return_discount_aliases and is
therefore immediately shared by Organic Sales, provisional P&L, Sales Analysis and
future sales imports.
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
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _normal_ids(core, db) -> set[str]:
    with core._conn(db) as c:
        if not _exists(c, "coupang_normal_option_registry"):
            return set()
        rows = c.execute("SELECT vendor_item_id FROM coupang_normal_option_registry").fetchall()
    return {_oid(r["vendor_item_id"]) for r in rows if _oid(r["vendor_item_id"])}


def _alias_ids(core, db) -> set[str]:
    with core._conn(db) as c:
        if not _exists(c, "return_discount_aliases"):
            return set()
        rows = c.execute("SELECT discount_option_id FROM return_discount_aliases").fetchall()
    return {_oid(r["discount_option_id"]) for r in rows if _oid(r["discount_option_id"])}


def _normal_products(core, db) -> list[dict]:
    normals = _normal_ids(core, db)
    if not normals:
        return []
    with core._conn(db) as c:
        rows = c.execute(
            "SELECT id,item_code,option_id,name,item_type,unit_cost,active FROM products"
        ).fetchall()
    grouped: dict[str, list[dict]] = {}
    for r in rows:
        p = dict(r)
        oid = _oid(p.get("option_id"))
        if oid in normals:
            p["option_id"] = oid
            grouped.setdefault(oid, []).append(p)

    def score(p: dict):
        code = _oid(p.get("item_code"))
        oid = _oid(p.get("option_id"))
        return (
            1 if str(p.get("item_type") or "") == "finished" else 0,
            1 if code != oid else 0,
            1 if float(p.get("unit_cost") or 0) > 0 else 0,
            1 if int(p.get("active") or 0) else 0,
            -int(p.get("id") or 0),
        )

    out = []
    for oid, ps in grouped.items():
        rep = max(ps, key=score)
        out.append(rep)
    out.sort(key=lambda p: (str(p.get("name") or ""), str(p.get("option_id") or "")))
    return out


def _product_label(p: dict) -> str:
    status = "" if int(p.get("active") or 0) else " · 판매중단/보관"
    return f"{p.get('name','')} · 옵션ID {p.get('option_id','')}{status}"


def _save_alias(core, db, child_oid: str, child_name: str, parent_pid: int):
    try:
        import return_discount_v099 as rd
        rd._ensure_schema(core, db)
    except Exception:
        pass
    now = core.now_iso()
    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS return_discount_aliases(
          discount_option_id TEXT PRIMARY KEY,
          parent_product_id INTEGER NOT NULL,
          discount_name TEXT,
          match_method TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL)""")
        c.execute(
            """INSERT INTO return_discount_aliases
               (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(discount_option_id) DO UPDATE SET
                 parent_product_id=excluded.parent_product_id,
                 discount_name=excluded.discount_name,
                 match_method=excluded.match_method,
                 updated_at=excluded.updated_at""",
            (child_oid, int(parent_pid), child_name, "manual_user_shared", now, now),
        )
        c.execute("""CREATE TABLE IF NOT EXISTS system_hidden_products(
          product_id INTEGER PRIMARY KEY,reason TEXT NOT NULL,hidden_at TEXT NOT NULL)""")
        if _exists(c, "products"):
            rows = c.execute(
                "SELECT id FROM products WHERE CAST(option_id AS TEXT)=?", (child_oid,)
            ).fetchall()
            for r in rows:
                pid = int(r["id"])
                if pid == int(parent_pid):
                    continue
                c.execute(
                    """INSERT INTO system_hidden_products(product_id,reason,hidden_at)
                       VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET
                       reason=excluded.reason,hidden_at=excluded.hidden_at""",
                    (pid, "return_alias_manual_shared_v09217", now),
                )
                c.execute("UPDATE products SET active=0 WHERE id=?", (pid,))


def _candidate_score(item: dict, p: dict) -> float:
    a = str(item.get("name") or "").lower().strip()
    b = str(p.get("name") or "").lower().strip()
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _ask(core, db, items: list[dict], source: str) -> bool:
    if not items:
        return False
    products = _normal_products(core, db)
    if not products:
        st.error("정상 원상품 목록을 불러오지 못해 반품상품을 매칭할 수 없습니다.")
        st.stop()

    item = items[0]
    oid = _oid(item.get("option_id"))
    name = str(item.get("name") or f"옵션ID {oid}")
    qty = item.get("qty")
    ordered = sorted(products, key=lambda p: _candidate_score(item, p), reverse=True)
    ids = [int(p["id"]) for p in ordered]
    by_id = {int(p["id"]): p for p in ordered}

    st.warning(
        f"{source}에서 원상품이 확정되지 않은 반품/미등록 판매 옵션을 발견했습니다. "
        "원상품을 한 번 선택하면 ERP 전체에서 같은 매핑을 공유합니다."
    )
    with st.container(border=True):
        st.markdown(f"**매칭 필요: {name}**")
        extra = f" · 판매수량 {qty:g}" if isinstance(qty, (int, float)) else ""
        st.caption(f"옵션ID {oid}{extra} · 미확정 {len(items)}건 중 1건")
        selected = st.selectbox(
            "이 반품상품의 원상품",
            ids,
            format_func=lambda pid: _product_label(by_id[int(pid)]),
            key=f"_rg_shared_match_v09217_{source}_{oid}",
        )
        if st.button(
            "이 원상품으로 확정",
            type="primary",
            key=f"_rg_shared_match_save_v09217_{source}_{oid}",
        ):
            _save_alias(core, db, oid, name, int(selected))
            try:
                st.toast("매핑을 공통 상품원장에 저장했습니다.", icon="✅")
            except Exception:
                pass
            st.rerun()
    st.stop()
    return True


def _row_identity(core, db, product_id, option_id):
    oid = _oid(option_id)
    if not oid and product_id:
        try:
            with core._conn(db) as c:
                r = c.execute("SELECT option_id,name FROM products WHERE id=?", (int(product_id),)).fetchone()
            if r:
                return _oid(r["option_id"]), str(r["name"] or "")
        except Exception:
            pass
    name = ""
    if product_id:
        try:
            with core._conn(db) as c:
                r = c.execute("SELECT name FROM products WHERE id=?", (int(product_id),)).fetchone()
            if r:
                name = str(r["name"] or "")
        except Exception:
            pass
    return oid, name


def period_unmatched(core, db, start, end) -> list[dict]:
    normals = _normal_ids(core, db)
    aliases = _alias_ids(core, db)
    with core._conn(db) as c:
        if not (_exists(c, "imports") and _exists(c, "sales_stats")):
            return []
        scols = _cols(c, "sales_stats")
        if "import_id" not in scols:
            return []
        imports = c.execute(
            """SELECT id FROM imports WHERE data_type='sales_stats'
               AND period_start>=? AND period_end<=?""",
            (str(start), str(end)),
        ).fetchall()
        if not imports:
            return []
        ids = [int(r["id"]) for r in imports]
        marks = ",".join("?" for _ in ids)
        pid_expr = "product_id" if "product_id" in scols else "0 AS product_id"
        oid_expr = "option_id" if "option_id" in scols else "'' AS option_id"
        qty_expr = "SUM(COALESCE(sales_qty,0))" if "sales_qty" in scols else (
            "SUM(COALESCE(net_qty,0))" if "net_qty" in scols else "0"
        )
        rows = c.execute(
            f"SELECT {pid_expr},{oid_expr},{qty_expr} qty FROM sales_stats "
            f"WHERE import_id IN ({marks}) GROUP BY product_id,option_id",
            ids,
        ).fetchall()

    out = {}
    for r in rows:
        pid = int(r["product_id"] or 0) if "product_id" in r.keys() else 0
        oid, pname = _row_identity(core, db, pid, r["option_id"] if "option_id" in r.keys() else "")
        if not oid or oid in normals or oid in aliases:
            continue
        out[oid] = {
            "option_id": oid,
            "product_id": pid,
            "name": pname or f"옵션ID {oid}",
            "qty": float(r["qty"] or 0),
        }
    return list(out.values())


def frame_unmatched(core, db, frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    normals = _normal_ids(core, db)
    aliases = _alias_ids(core, db)
    oidcol = next((c for c in ("옵션ID", "쿠팡 옵션ID", "option_id") if c in frame.columns), None)
    pidcol = "product_id" if "product_id" in frame.columns else None
    if oidcol is None and pidcol is None:
        return []
    out = {}
    for _, row in frame.iterrows():
        pid = 0
        if pidcol:
            try:
                pid = int(float(row.get(pidcol) or 0))
            except Exception:
                pid = 0
        oid, pname = _row_identity(core, db, pid, row.get(oidcol) if oidcol else "")
        if not oid or oid in normals or oid in aliases:
            continue
        name = str(row.get("상품명") or row.get("아이템") or pname or f"옵션ID {oid}")
        qty = row.get("판매수량") if "판매수량" in frame.columns else None
        try:
            qty = float(qty) if qty is not None else None
        except Exception:
            qty = None
        out[oid] = {"option_id": oid, "product_id": pid, "name": name, "qty": qty}
    return list(out.values())


def ensure_period_mappings(core, db, start, end, source: str):
    return _ask(core, db, period_unmatched(core, db, start, end), source)


def ensure_frame_mappings(core, db, frame, source: str):
    return _ask(core, db, frame_unmatched(core, db, frame), source)


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)

    # Organic sales: ask before raw rows are aggregated and lose option identity.
    try:
        import organic_sales_estimate_v09211 as organic
        old = getattr(organic, "_organic_estimate_data", None)
        if callable(old) and not getattr(old, "_rg_shared_match_v09217", False):
            def wrapped(core_obj, sales_module, db_path, start, end, _old=old):
                ensure_period_mappings(core_obj, db_path, start, end, "오가닉판매 추정")
                return _old(core_obj, sales_module, db_path, start, end)
            wrapped._rg_shared_match_v09217 = True
            organic._organic_estimate_data = wrapped
    except Exception:
        pass

    # Sales analysis uses the same shared matcher and table.
    try:
        import sales_analysis_v09186 as sales
        old = getattr(sales, "_sales_stats", None)
        if callable(old) and not getattr(old, "_rg_shared_match_v09217", False):
            def wrapped_sales(core_obj, db_path, start, end, _old=old):
                ensure_period_mappings(core_obj, db_path, start, end, "판매분석")
                return _old(core_obj, db_path, start, end)
            wrapped_sales._rg_shared_match_v09217 = True
            sales._sales_stats = wrapped_sales
    except Exception:
        pass

    # Provisional P&L: ask from the actual rows about to be calculated.
    try:
        import provisional_pnl_ui_v0913 as pnl
        old = getattr(pnl, "_apply_existing_rules", None)
        if callable(old) and not getattr(old, "_rg_shared_match_v09217", False):
            def wrapped_pnl(core_obj, db_path, data, _old=old):
                ensure_frame_mappings(core_obj, db_path, data, "잠정손익")
                return _old(core_obj, db_path, data)
            wrapped_pnl._rg_shared_match_v09217 = True
            pnl._apply_existing_rules = wrapped_pnl
    except Exception:
        pass

    core.rg_ensure_return_mappings_for_period = lambda start, end, source="ERP", db_path=None: ensure_period_mappings(
        core, db_path or core.DEFAULT_DB, start, end, source
    )
    core.rg_ensure_return_mappings_for_frame = lambda frame, source="ERP", db_path=None: ensure_frame_mappings(
        core, db_path or core.DEFAULT_DB, frame, source
    )
    return {"ok": True, "shared_table": "return_discount_aliases"}
