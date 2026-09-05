"""v0.9.169 verified rubber-glove BOM repair.

The user's September purchase created the glove raw SKUs first in the new-item
batch. After the JDS sequencing repair they are JDS761/JDS762/JDS763, followed by
JDS765 vegetable brush, JDS766 straw brush, and the ribbon SKUs visible in the
inventory list.  This repair therefore uses those three item codes as the first
and strongest lookup, but ONLY after verifying the expected unit cost and own-
warehouse stock.  If that exact-code verification is unavailable, durable source
mapping / purchase-line / strict stock+cost fallbacks are used.

All three S/M/L components must resolve to distinct product IDs before any BOM is
written.  The three finished products are ensured by the confirmed Coupang codes.
No inventory quantity is created, moved, consumed, or adjusted.
"""
from __future__ import annotations

import math
import re

RULE = "v0.9.169-glove-bom-verified-jds761-763"

TARGETS = {
    "S": {
        "option_id": "96012086788",
        "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 소(S)",
        "raw_code": "JDS761",
        "qty_per": 5.0,
        "expected_unit_cost": 847.0,
        "expected_own_stock": 240.0,
    },
    "M": {
        "option_id": "96012086789",
        "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 중(M)",
        "raw_code": "JDS762",
        "qty_per": 5.0,
        "expected_unit_cost": 844.0,
        "expected_own_stock": 250.0,
    },
    "L": {
        "option_id": "96012086790",
        "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 대(L)",
        "raw_code": "JDS763",
        "qty_per": 5.0,
        "expected_unit_cost": 849.0,
        "expected_own_stock": 983.0,
    },
}


def _norm(v):
    return str(v or "").strip()


def _num(v):
    try:
        x = float(v or 0)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(con, table):
    try:
        return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _size_match(text, size):
    s = _norm(text).upper()
    return bool(re.search(rf"(?<![A-Z]){re.escape(size.upper())}(?![A-Z])", s))


def _own_stock(con, product_id):
    if not _table_exists(con, "inventory_txns") or not _table_exists(con, "warehouses"):
        return 0.0
    row = con.execute(
        """SELECT COALESCE(SUM(t.qty_delta),0) q
           FROM inventory_txns t
           JOIN warehouses w ON w.id=t.warehouse_id
           WHERE t.product_id=? AND w.name='자체창고'""",
        (int(product_id),),
    ).fetchone()
    return _num(row["q"] if row else 0)


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
        """CREATE TABLE IF NOT EXISTS rubber_glove_bom_repair_audit(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               rule TEXT NOT NULL,
               size TEXT NOT NULL,
               status TEXT NOT NULL,
               parent_product_id INTEGER,
               component_product_id INTEGER,
               component_code TEXT,
               component_name TEXT,
               unit_cost REAL,
               own_stock REAL,
               created_at TEXT NOT NULL
           )"""
    )


def _ensure_parent(core, con, target):
    oid = str(target["option_id"])
    code = f"CP-{oid}"
    rows = con.execute(
        "SELECT id,item_code,option_id,name FROM products WHERE CAST(option_id AS TEXT)=? ORDER BY id",
        (oid,),
    ).fetchall()
    if len(rows) == 1:
        pid = int(rows[0]["id"])
        con.execute(
            "UPDATE products SET item_code=?,name=?,item_type='finished',active=1,updated_at=? WHERE id=?",
            (code, target["finished_name"], core.now_iso(), pid),
        )
        return con.execute(
            "SELECT id,item_code,option_id,name FROM products WHERE id=?", (pid,)
        ).fetchone()
    if len(rows) > 1:
        return None

    rows = con.execute(
        "SELECT id,item_code,option_id,name FROM products WHERE item_code=? ORDER BY id",
        (code,),
    ).fetchall()
    if len(rows) == 1:
        pid = int(rows[0]["id"])
        con.execute(
            "UPDATE products SET option_id=?,name=?,item_type='finished',active=1,updated_at=? WHERE id=?",
            (oid, target["finished_name"], core.now_iso(), pid),
        )
        return con.execute(
            "SELECT id,item_code,option_id,name FROM products WHERE id=?", (pid,)
        ).fetchone()
    if len(rows) > 1:
        return None

    cur = con.execute(
        """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
           VALUES(?,?,?,'finished',0,1,?)""",
        (code, oid, target["finished_name"], core.now_iso()),
    )
    return con.execute(
        "SELECT id,item_code,option_id,name FROM products WHERE id=?", (int(cur.lastrowid),)
    ).fetchone()


def _row_by_id(con, pid):
    return con.execute(
        """SELECT id,item_code,name,unit_cost,item_type,option_id,active
           FROM products WHERE id=?""",
        (int(pid),),
    ).fetchone()


def _verified_exact_code(con, size):
    target = TARGETS[size]
    rows = con.execute(
        """SELECT id,item_code,name,unit_cost,item_type,option_id,active
           FROM products
           WHERE UPPER(TRIM(COALESCE(item_code,'')))=? AND active=1 AND option_id IS NULL""",
        (target["raw_code"].upper(),),
    ).fetchall()
    if len(rows) != 1:
        return None
    r = rows[0]
    cost_diff = abs(_num(r["unit_cost"]) - float(target["expected_unit_cost"]))
    stock_diff = abs(_own_stock(con, int(r["id"])) - float(target["expected_own_stock"]))
    # The item code is known from the exact registration order; require the cost
    # to stay close. Stock may subsequently be changed by a legitimate stocktake,
    # so exact stock is preferred but not mandatory once code+cost agree.
    if cost_diff <= 5.0 and stock_diff <= 5.0:
        return r
    if cost_diff <= 1.5:
        return r
    return None


def _mapped_component(con, size):
    if not _table_exists(con, "purchase_source_product_map"):
        return None
    cols = _columns(con, "purchase_source_product_map")
    if not {"product_id", "source_name", "source_detail"}.issubset(cols):
        return None
    rows = con.execute(
        """SELECT p.id,p.item_code,p.name,p.unit_cost,p.item_type,p.option_id,p.active,
                  m.source_name,m.source_detail
           FROM purchase_source_product_map m
           JOIN products p ON p.id=m.product_id
           WHERE p.active=1 AND p.option_id IS NULL"""
    ).fetchall()
    matches = [
        r for r in rows
        if "고무장갑" in _norm(r["source_name"])
        and (_size_match(r["source_detail"], size) or _size_match(r["source_name"], size))
    ]
    unique = {}
    for r in matches:
        unique[int(r["id"])] = r
    return next(iter(unique.values())) if len(unique) == 1 else None


def _purchase_line_component(con, size):
    if not _table_exists(con, "purchase_lines"):
        return None
    cols = _columns(con, "purchase_lines")
    needed = {"product_id", "source_name", "source_detail"}
    if not needed.issubset(cols):
        return None
    rows = con.execute(
        """SELECT DISTINCT p.id,p.item_code,p.name,p.unit_cost,p.item_type,p.option_id,p.active,
                          l.source_name,l.source_detail
           FROM purchase_lines l
           JOIN products p ON p.id=l.product_id
           WHERE p.active=1 AND p.option_id IS NULL AND l.product_id IS NOT NULL"""
    ).fetchall()
    matches = [
        r for r in rows
        if "고무장갑" in _norm(r["source_name"])
        and (_size_match(r["source_detail"], size) or _size_match(r["source_name"], size))
    ]
    unique = {}
    for r in matches:
        unique[int(r["id"])] = r
    return next(iter(unique.values())) if len(unique) == 1 else None


def _strict_stock_cost(con, size):
    target = TARGETS[size]
    rows = con.execute(
        """SELECT id,item_code,name,unit_cost,item_type,option_id,active
           FROM products WHERE active=1 AND option_id IS NULL ORDER BY id DESC"""
    ).fetchall()
    exact = []
    for r in rows:
        cost_diff = abs(_num(r["unit_cost"]) - float(target["expected_unit_cost"]))
        stock_diff = abs(_own_stock(con, int(r["id"])) - float(target["expected_own_stock"]))
        if cost_diff <= 3.0 and stock_diff <= 0.01:
            exact.append(r)
    unique = {int(r["id"]): r for r in exact}
    if len(unique) == 1:
        return next(iter(unique.values()))
    return None


def _resolve_component(con, size):
    for resolver in (
        lambda: _verified_exact_code(con, size),
        lambda: _mapped_component(con, size),
        lambda: _purchase_line_component(con, size),
        lambda: _strict_stock_cost(con, size),
    ):
        row = resolver()
        if row is not None:
            return row
    return None


def _audit(core, con, size, status, parent=None, component=None):
    con.execute(
        """INSERT INTO rubber_glove_bom_repair_audit
           (rule,size,status,parent_product_id,component_product_id,component_code,component_name,unit_cost,own_stock,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            RULE,
            size,
            status,
            int(parent["id"]) if parent is not None else None,
            int(component["id"]) if component is not None else None,
            _norm(component["item_code"]) if component is not None else "",
            _norm(component["name"]) if component is not None else "",
            _num(component["unit_cost"]) if component is not None else 0.0,
            _own_stock(con, int(component["id"])) if component is not None else 0.0,
            core.now_iso(),
        ),
    )


def _upsert_single_bom(con, parent_id, component_id, qty_per):
    # User-confirmed product consists only of the matching glove raw SKU x5.
    con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (int(parent_id),))
    con.execute(
        "INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)",
        (int(parent_id), int(component_id), float(qty_per)),
    )


def _repair_visible_name(core, con, size, component):
    pid = int(component["id"])
    current = _row_by_id(con, pid)
    old = _norm(current["name"])
    if _size_match(old, size):
        return old

    # Prefer the original purchase detail when available.
    if _table_exists(con, "purchase_source_product_map"):
        cols = _columns(con, "purchase_source_product_map")
        if {"product_id", "source_name", "source_detail"}.issubset(cols):
            rows = con.execute(
                """SELECT source_name,source_detail FROM purchase_source_product_map
                   WHERE product_id=? ORDER BY created_at DESC""",
                (pid,),
            ).fetchall()
            pairs = [(_norm(r["source_name"]), _norm(r["source_detail"])) for r in rows]
            pairs = [p for p in pairs if p[0] and p[1] and _size_match(p[1], size)]
            if pairs:
                new_name = f"{pairs[0][0]} [{pairs[0][1]}]"
                con.execute("UPDATE products SET name=?,updated_at=? WHERE id=?", (new_name, core.now_iso(), pid))
                return new_name

    if "고무장갑" in old:
        new_name = f"{old} [{size}]"
        con.execute("UPDATE products SET name=?,updated_at=? WHERE id=?", (new_name, core.now_iso(), pid))
        return new_name
    return old


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    result = {"ok": False, "rule": RULE, "inventory_changed": False, "items": []}

    with core._conn(db) as con:
        _ensure_tables(con)
        parents = {}
        components = {}

        for size in ("S", "M", "L"):
            parent = _ensure_parent(core, con, TARGETS[size])
            if parent is None:
                _audit(core, con, size, "parent_ambiguous")
                result["items"].append({"size": size, "status": "parent_ambiguous"})
                continue
            parents[size] = parent

            component = _resolve_component(con, size)
            if component is None:
                _audit(core, con, size, "component_unresolved", parent=parent)
                result["items"].append({"size": size, "status": "component_unresolved", "parent_id": int(parent["id"])})
                continue
            components[size] = component

        if len(parents) != 3 or len(components) != 3:
            con.commit()
            result["status"] = "pending_unresolved"
            return result

        component_ids = [int(components[s]["id"]) for s in ("S", "M", "L")]
        parent_ids = [int(parents[s]["id"]) for s in ("S", "M", "L")]
        if len(set(component_ids)) != 3 or len(set(parent_ids)) != 3:
            for size in ("S", "M", "L"):
                _audit(core, con, size, "not_distinct", parent=parents[size], component=components[size])
            con.commit()
            result["status"] = "pending_not_distinct"
            return result

        changes = []
        for size in ("S", "M", "L"):
            parent = parents[size]
            component = components[size]
            _upsert_single_bom(con, int(parent["id"]), int(component["id"]), TARGETS[size]["qty_per"])
            visible_name = _repair_visible_name(core, con, size, component)
            refreshed = _row_by_id(con, int(component["id"]))
            _audit(core, con, size, "bom_linked", parent=parent, component=refreshed)
            changes.append({
                "size": size,
                "option_id": TARGETS[size]["option_id"],
                "parent_id": int(parent["id"]),
                "component_id": int(component["id"]),
                "component_code": _norm(refreshed["item_code"]),
                "component_name": visible_name,
                "qty_per": TARGETS[size]["qty_per"],
                "unit_cost": _num(refreshed["unit_cost"]),
                "own_stock": _own_stock(con, int(component["id"])),
                "status": "bom_linked",
            })

        # Verify what the BOM screen itself reads before committing success.
        verify = []
        for size in ("S", "M", "L"):
            rows = con.execute(
                """SELECT b.parent_product_id,b.component_product_id,b.qty_per
                   FROM bom_items b WHERE b.parent_product_id=?""",
                (int(parents[size]["id"]),),
            ).fetchall()
            ok = (
                len(rows) == 1
                and int(rows[0]["component_product_id"]) == int(components[size]["id"])
                and abs(_num(rows[0]["qty_per"]) - 5.0) < 1e-9
            )
            verify.append(ok)

        if not all(verify):
            con.rollback()
            result["status"] = "verification_failed"
            return result

        con.commit()
        result.update({"ok": True, "status": "bom_linked_verified_all_three", "items": changes, "verified": True})
        return result
