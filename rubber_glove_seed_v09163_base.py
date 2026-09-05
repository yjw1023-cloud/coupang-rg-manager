"""RG Manager v0.9.163 rubber-glove registration + JDS sequencing hotfix.

Changes from v0.9.162:
- new JDS codes use the highest existing numeric JDS code + 1, never the first gap;
- the three glove raw items accidentally created as JDS0020/JDS0021/JDS0022 are
  safely identified and renamed to the next latest sequential JDS codes;
- glove BOM links follow the mapped raw product IDs, so renaming codes is safe;
- inventory/item lists default to newest registered product first;
- the confirmed selling price / commission / combined RG logistics defaults remain.

No inventory quantity is created or moved here.
"""
from __future__ import annotations

import math
import re
import sqlite3
from typing import Any

REQUESTS = [
    {"size": "S", "option_id": "96012086788", "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 소(S)", "legacy_code": "JDS0020", "qty_per": 5.0},
    {"size": "M", "option_id": "96012086789", "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 중(M)", "legacy_code": "JDS0021", "qty_per": 5.0},
    {"size": "L", "option_id": "96012086790", "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 대(L)", "legacy_code": "JDS0022", "qty_per": 5.0},
]
SELLING_PRICE = 13900.0
COMMISSION_RATE = 0.108
COMMISSION_UNIT = SELLING_PRICE * COMMISSION_RATE
LOGISTICS_UNIT_TOTAL = 2800.0
_RULE = "v0.9.163-jds-max-plus-one-latest-first"


def _num(v: Any) -> float:
    try:
        x = float(v or 0)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _table_exists(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _next_jds_code_in_connection(con) -> str:
    max_no = 0
    used = set()
    for r in con.execute("SELECT item_code FROM products WHERE item_code IS NOT NULL").fetchall():
        code = str(r["item_code"] or "").strip()
        used.add(code.upper())
        m = re.fullmatch(r"JDS0*(\d+)", code, flags=re.IGNORECASE)
        if m:
            max_no = max(max_no, int(m.group(1)))
    n = max_no + 1
    while f"JDS{n}".upper() in used:
        n += 1
    return f"JDS{n}"


def _size_match(text: Any, size: str) -> bool:
    s = str(text or "").upper().strip()
    return bool(re.search(rf"(?<![A-Z]){re.escape(size.upper())}(?![A-Z])", s))


def _component_for_size(con, size: str, legacy_code: str):
    # Prefer the durable source mapping created by the purchase-import flow.
    if _table_exists(con, "purchase_source_product_map"):
        rows = con.execute(
            """SELECT p.id,p.item_code,p.option_id,p.name,p.item_type,p.active,
                      m.source_name,m.source_detail
               FROM purchase_source_product_map m
               JOIN products p ON p.id=m.product_id
               WHERE p.active=1 AND p.option_id IS NULL"""
        ).fetchall()
        matches = [
            r for r in rows
            if "고무장갑" in str(r["source_name"] or "")
            and _size_match(r["source_detail"], size)
        ]
        if len(matches) == 1:
            return matches[0]

    # Fallback to the visible raw product name.
    rows = con.execute(
        """SELECT id,item_code,option_id,name,item_type,active
           FROM products WHERE active=1 AND option_id IS NULL"""
    ).fetchall()
    matches = [
        r for r in rows
        if "고무장갑" in str(r["name"] or "") and _size_match(r["name"], size)
    ]
    if len(matches) == 1:
        return matches[0]

    # Final compatibility fallback for the accidentally allocated v0.9.159 codes.
    return con.execute(
        """SELECT id,item_code,option_id,name,item_type,active
           FROM products WHERE item_code=? AND active=1 AND option_id IS NULL
           ORDER BY id LIMIT 1""",
        (legacy_code,),
    ).fetchone()


def _repair_glove_codes(con):
    found = []
    target_ids = set()
    for req in REQUESTS:
        row = _component_for_size(con, req["size"], req["legacy_code"])
        found.append((req, row))
        if row is not None and str(row["item_code"] or "").upper() == req["legacy_code"].upper():
            target_ids.add(int(row["id"]))

    if not target_ids:
        return found, []

    max_no = 0
    used = set()
    for r in con.execute("SELECT id,item_code FROM products WHERE item_code IS NOT NULL").fetchall():
        code = str(r["item_code"] or "").strip()
        if int(r["id"]) in target_ids:
            continue
        used.add(code.upper())
        m = re.fullmatch(r"JDS0*(\d+)", code, flags=re.IGNORECASE)
        if m:
            max_no = max(max_no, int(m.group(1)))

    changes = []
    n = max_no + 1
    for req, row in found:
        if row is None or int(row["id"]) not in target_ids:
            continue
        while f"JDS{n}".upper() in used:
            n += 1
        new_code = f"JDS{n}"
        old_code = str(row["item_code"] or "")
        con.execute("UPDATE products SET item_code=? WHERE id=?", (new_code, int(row["id"])))
        used.add(new_code.upper())
        changes.append({"size": req["size"], "product_id": int(row["id"]), "old_code": old_code, "new_code": new_code})
        n += 1

    refreshed = []
    for req, row in found:
        if row is None:
            refreshed.append((req, None))
        else:
            refreshed.append((req, con.execute(
                "SELECT id,item_code,option_id,name,item_type,active FROM products WHERE id=?",
                (int(row["id"]),),
            ).fetchone()))
    return refreshed, changes


def _ensure_tables(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS bom_items(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               parent_product_id INTEGER NOT NULL,
               component_product_id INTEGER NOT NULL,
               qty_per REAL NOT NULL,
               UNIQUE(parent_product_id,component_product_id)
           )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS product_commercial_defaults(
               option_id TEXT PRIMARY KEY,
               product_id INTEGER,
               selling_price REAL NOT NULL,
               commission_rate REAL NOT NULL,
               commission_unit REAL NOT NULL,
               logistics_unit_total REAL NOT NULL,
               source TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )


def _ensure_finished(core, con, req):
    oid = req["option_id"]
    code = f"CP-{oid}"
    row = con.execute("SELECT id FROM products WHERE CAST(option_id AS TEXT)=? ORDER BY id LIMIT 1", (oid,)).fetchone()
    if row:
        pid = int(row["id"])
        con.execute(
            "UPDATE products SET item_code=?,option_id=?,name=?,item_type='finished',active=1,updated_at=? WHERE id=?",
            (code, oid, req["finished_name"], core.now_iso(), pid),
        )
        return pid
    row = con.execute("SELECT id,option_id FROM products WHERE item_code=? ORDER BY id LIMIT 1", (code,)).fetchone()
    if row:
        existing = _oid(row["option_id"])
        if existing and existing != oid:
            raise RuntimeError(f"{code}가 다른 옵션ID {existing}에 연결되어 있습니다.")
        pid = int(row["id"])
        con.execute(
            "UPDATE products SET option_id=?,name=?,item_type='finished',active=1,updated_at=? WHERE id=?",
            (oid, req["finished_name"], core.now_iso(), pid),
        )
        return pid
    cur = con.execute(
        "INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at) VALUES(?,?,?,'finished',0,1,?)",
        (code, oid, req["finished_name"], core.now_iso()),
    )
    return int(cur.lastrowid)


def _upsert_bom(con, parent_id: int, component_id: int, qty_per: float):
    con.execute("DELETE FROM bom_items WHERE parent_product_id=? AND component_product_id<>?", (parent_id, component_id))
    row = con.execute("SELECT id FROM bom_items WHERE parent_product_id=? AND component_product_id=?", (parent_id, component_id)).fetchone()
    if row:
        con.execute("UPDATE bom_items SET qty_per=? WHERE id=?", (qty_per, int(row["id"])))
    else:
        con.execute("INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)", (parent_id, component_id, qty_per))


def _upsert_commercial(core, con, option_id: str, product_id: int):
    con.execute(
        """INSERT INTO product_commercial_defaults
           (option_id,product_id,selling_price,commission_rate,commission_unit,logistics_unit_total,source,updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(option_id) DO UPDATE SET
             product_id=excluded.product_id,selling_price=excluded.selling_price,
             commission_rate=excluded.commission_rate,commission_unit=excluded.commission_unit,
             logistics_unit_total=excluded.logistics_unit_total,source=excluded.source,updated_at=excluded.updated_at""",
        (option_id, product_id, SELLING_PRICE, COMMISSION_RATE, COMMISSION_UNIT, LOGISTICS_UNIT_TOTAL, _RULE, core.now_iso()),
    )


def _patch_jds_generators(core):
    try:
        mod = __import__("purchase_new_item_persist_v09136", fromlist=["*"])
        mod._next_jds_code_in_connection = _next_jds_code_in_connection
    except Exception:
        pass
    try:
        mod = __import__("item_ui_v086", fromlist=["*"])
        def _next_jds_code(core_module):
            core_module.init_db(core_module.DEFAULT_DB)
            with core_module._conn(core_module.DEFAULT_DB) as con:
                return _next_jds_code_in_connection(con)
        mod._next_jds_code = _next_jds_code
    except Exception:
        pass
    try:
        mod = __import__("requested_product_seed_v09133", fromlist=["*"])
        mod._next_jds_code = _next_jds_code_in_connection
    except Exception:
        pass


def _registration_map(core):
    out = {}
    try:
        with core._conn(core.DEFAULT_DB) as con:
            rows = con.execute("SELECT id,item_code,option_id FROM products").fetchall()
        for r in rows:
            item_code = str(r["item_code"] or "").strip()
            code = _oid(r["option_id"]) if re.fullmatch(r"CP-\d+", item_code) else item_code
            if code:
                out[code] = max(out.get(code, 0), int(r["id"]))
    except Exception:
        pass
    return out


def _patch_latest_first(core):
    try:
        inv = __import__("inventory_ui_v084", fromlist=["*"])
    except Exception:
        return
    if getattr(inv, "_rg_latest_first_v09163", False):
        return
    base_enrich = inv._enrich_view
    base_tab = inv._tab_frame

    def _sort(df):
        if df is None or getattr(df, "empty", True) or "품목코드" not in df.columns:
            return df
        reg = _registration_map(core)
        out = df.copy()
        out["__등록순"] = out["품목코드"].map(lambda x: reg.get(str(x or "").strip(), 0))
        out = out.sort_values("__등록순", ascending=False, kind="stable").drop(columns=["__등록순"])
        return out.reset_index(drop=True)

    def enrich(df):
        view, item_master = base_enrich(df)
        return _sort(view), item_master

    def tab_frame(df, warehouse, item_master):
        return _sort(base_tab(df, warehouse, item_master))

    inv._enrich_view = enrich
    inv._tab_frame = tab_frame
    inv._rg_latest_first_v09163 = True


def _patch_pnl_defaults(core):
    # Older v0.9.161 wrapper, if already active in a live process, remains valid.
    if getattr(core, "_rg_rubber_glove_defaults_v09161_applied", False):
        return
    if getattr(core, "_rg_rubber_glove_defaults_v09163_applied", False):
        return
    base = getattr(core, "estimated_pnl", None)
    if not callable(base):
        return

    def estimated_pnl(*args, **kwargs):
        result = base(*args, **kwargs)
        try:
            import pandas as pd
            df = result[0] if isinstance(result, tuple) else result
            if not isinstance(df, pd.DataFrame) or df.empty:
                return result
            out = df.copy()
            with core._conn(kwargs.get("db_path") or core.DEFAULT_DB) as con:
                defaults = {str(r["option_id"]): dict(r) for r in con.execute("SELECT * FROM product_commercial_defaults").fetchall()}
            for idx in out.index:
                oid = ""
                for c in ("option_id", "옵션ID"):
                    if c in out.columns:
                        oid = _oid(out.at[idx, c]); break
                d = defaults.get(oid)
                if not d:
                    continue
                qty = 0.0
                for c in ("net_qty", "판매수량", "sales_qty", "net_sales_qty"):
                    if c in out.columns:
                        qty = abs(_num(out.at[idx, c])); break
                if qty <= 0:
                    continue
                for c in ("expected_revenue", "예상매출"):
                    if c in out.columns and abs(_num(out.at[idx, c])) < 1e-9:
                        out.at[idx, c] = qty * _num(d["selling_price"])
                for c in ("expected_commission", "판매수수료", "commission"):
                    if c in out.columns and abs(_num(out.at[idx, c])) < 1e-9:
                        fee = qty * _num(d["commission_unit"])
                        out.at[idx, c] = -fee if c == "판매수수료" else fee
                manual_i = out.at[idx, "manual_expected_inout"] if "manual_expected_inout" in out.columns else None
                manual_d = out.at[idx, "manual_expected_delivery"] if "manual_expected_delivery" in out.columns else None
                ei = _num(out.at[idx, "expected_inout"]) if "expected_inout" in out.columns else 0.0
                ed = _num(out.at[idx, "expected_delivery"]) if "expected_delivery" in out.columns else 0.0
                if manual_i is None and manual_d is None and abs(ei) < 1e-9 and abs(ed) < 1e-9:
                    total = qty * _num(d["logistics_unit_total"])
                    if "expected_delivery" in out.columns:
                        out.at[idx, "expected_delivery"] = total
                    if "delivery_unit" in out.columns:
                        out.at[idx, "delivery_unit"] = _num(d["logistics_unit_total"])
                    if "배송비" in out.columns and abs(_num(out.at[idx, "배송비"])) < 1e-9:
                        out.at[idx, "배송비"] = -total
            if isinstance(result, tuple):
                parts = list(result); parts[0] = out; return tuple(parts)
            return out
        except Exception:
            return result

    core.estimated_pnl = estimated_pnl
    core._rg_rubber_glove_defaults_v09163_applied = True


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    _patch_jds_generators(core)
    _patch_latest_first(core)
    results = []
    changes = []
    with core._conn(db) as con:
        _ensure_tables(con)
        found, changes = _repair_glove_codes(con)
        by_size = {req["size"]: row for req, row in found}
        for req in REQUESTS:
            parent_id = _ensure_finished(core, con, req)
            _upsert_commercial(core, con, req["option_id"], parent_id)
            component = by_size.get(req["size"])
            status = "bom_pending"
            component_code = ""
            if component is not None:
                _upsert_bom(con, parent_id, int(component["id"]), float(req["qty_per"]))
                current = con.execute("SELECT item_code,name FROM products WHERE id=?", (int(component["id"]),)).fetchone()
                component_code = str(current["item_code"] or "")
                status = "bom_linked"
            results.append({"size": req["size"], "option_id": req["option_id"], "product_id": parent_id,
                            "component_code": component_code, "qty_per": req["qty_per"], "status": status})
        try:
            con.commit()
        except Exception:
            pass
    _patch_pnl_defaults(core)
    return {"ok": True, "rule": _RULE, "items": results, "renamed_codes": changes, "inventory_changed": False}
