"""v0.9.167 deterministic rubber-glove BOM repair.

The original v0.9.163 seed could leave BOMs pending after the newly-created raw
items were renumbered away from JDS0020~JDS0022 while their S/M/L option text was
not visible in products.name.  This repair resolves the three raw products using
all durable signals available, then writes all three 5-unit BOMs atomically.

No inventory quantity is created, moved, or adjusted.
"""
from __future__ import annotations

import math
import re

RULE = "v0.9.167-rubber-glove-bom-repair"

TARGETS = {
    "S": {
        "option_id": "96012086788",
        "qty_per": 5.0,
        "expected_unit_cost": 847.0,
        "expected_own_stock": 240.0,
    },
    "M": {
        "option_id": "96012086789",
        "qty_per": 5.0,
        "expected_unit_cost": 844.0,
        "expected_own_stock": 250.0,
    },
    "L": {
        "option_id": "96012086790",
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


def _parent(con, option_id):
    return con.execute(
        """SELECT id,item_code,option_id,name
           FROM products
           WHERE CAST(option_id AS TEXT)=? AND active=1
           ORDER BY id LIMIT 1""",
        (str(option_id),),
    ).fetchone()


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


def _existing_bom_component(con, parent_id, size):
    if not _table_exists(con, "bom_items"):
        return None
    rows = con.execute(
        """SELECT p.id,p.item_code,p.name,p.unit_cost,p.item_type,p.option_id,b.qty_per
           FROM bom_items b
           JOIN products p ON p.id=b.component_product_id
           WHERE b.parent_product_id=? AND p.active=1""",
        (int(parent_id),),
    ).fetchall()
    glove_rows = [r for r in rows if "고무장갑" in _norm(r["name"])]
    if len(glove_rows) == 1:
        return glove_rows[0]
    size_rows = [r for r in glove_rows if _size_match(r["name"], size)]
    return size_rows[0] if len(size_rows) == 1 else None


def _mapped_component(con, size):
    if not _table_exists(con, "purchase_source_product_map"):
        return None
    cols = _columns(con, "purchase_source_product_map")
    if not {"product_id", "source_name", "source_detail"}.issubset(cols):
        return None
    rows = con.execute(
        """SELECT p.id,p.item_code,p.name,p.unit_cost,p.item_type,p.option_id,
                  m.source_name,m.source_detail
           FROM purchase_source_product_map m
           JOIN products p ON p.id=m.product_id
           WHERE p.active=1 AND p.option_id IS NULL"""
    ).fetchall()
    matches = [
        r for r in rows
        if "고무장갑" in _norm(r["source_name"])
        and _size_match(r["source_detail"], size)
    ]
    ids = {int(r["id"]) for r in matches}
    return matches[0] if len(ids) == 1 else None


def _named_component(con, size):
    rows = con.execute(
        """SELECT id,item_code,name,unit_cost,item_type,option_id
           FROM products
           WHERE active=1 AND option_id IS NULL
             AND name LIKE '%고무장갑%'
           ORDER BY id DESC"""
    ).fetchall()
    matches = [r for r in rows if _size_match(r["name"], size)]
    ids = {int(r["id"]) for r in matches}
    return matches[0] if len(ids) == 1 else None


def _fingerprint_component(con, size):
    target = TARGETS[size]
    rows = con.execute(
        """SELECT id,item_code,name,unit_cost,item_type,option_id
           FROM products
           WHERE active=1 AND option_id IS NULL
             AND name LIKE '%고무장갑%'
           ORDER BY id DESC"""
    ).fetchall()
    if not rows:
        return None

    scored = []
    for r in rows:
        cost = _num(r["unit_cost"])
        stock = _own_stock(con, int(r["id"]))
        cost_diff = abs(cost - float(target["expected_unit_cost"]))
        stock_diff = abs(stock - float(target["expected_own_stock"]))
        # Current unconsumed purchase stock is the strongest signal; unit cost is
        # the second signal.  Reject clearly unrelated glove products.
        if cost_diff > 8.0:
            continue
        score = stock_diff * 10.0 + cost_diff
        scored.append((score, stock_diff, cost_diff, int(r["id"]), r))

    if not scored:
        return None
    scored.sort(key=lambda x: (x[0], x[3]))
    best = scored[0]

    # Exact/near-exact stock from this purchase is decisive.
    if best[1] <= 0.01 and best[2] <= 8.0:
        return best[4]

    # If stock has subsequently been adjusted, accept only a uniquely close cost.
    close = [x for x in scored if x[2] <= 1.25]
    if len(close) == 1:
        return close[0][4]
    if len(close) >= 2 and close[0][2] + 0.25 < close[1][2]:
        return close[0][4]
    return None


def _resolve_component(con, parent_id, size):
    for resolver in (
        lambda: _existing_bom_component(con, parent_id, size),
        lambda: _mapped_component(con, size),
        lambda: _named_component(con, size),
        lambda: _fingerprint_component(con, size),
    ):
        row = resolver()
        if row is not None:
            return row
    return None


def _upsert_bom(con, parent_id, component_id, qty_per):
    con.execute(
        "DELETE FROM bom_items WHERE parent_product_id=? AND component_product_id<>?",
        (int(parent_id), int(component_id)),
    )
    row = con.execute(
        "SELECT id FROM bom_items WHERE parent_product_id=? AND component_product_id=?",
        (int(parent_id), int(component_id)),
    ).fetchone()
    if row:
        con.execute("UPDATE bom_items SET qty_per=? WHERE id=?", (float(qty_per), int(row["id"])))
    else:
        con.execute(
            "INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)",
            (int(parent_id), int(component_id), float(qty_per)),
        )


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    result = {"ok": False, "rule": RULE, "inventory_changed": False, "items": []}

    with core._conn(db) as con:
        if not _table_exists(con, "bom_items"):
            con.execute(
                """CREATE TABLE bom_items(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       parent_product_id INTEGER NOT NULL,
                       component_product_id INTEGER NOT NULL,
                       qty_per REAL NOT NULL,
                       UNIQUE(parent_product_id,component_product_id)
                   )"""
            )

        resolved = {}
        for size, target in TARGETS.items():
            parent = _parent(con, target["option_id"])
            if parent is None:
                result["items"].append({"size": size, "status": "parent_missing"})
                continue
            component = _resolve_component(con, int(parent["id"]), size)
            if component is None:
                result["items"].append(
                    {"size": size, "status": "component_unresolved", "parent_id": int(parent["id"])}
                )
                continue
            resolved[size] = (parent, component)

        # Safety: never partially or ambiguously rewrite the three glove BOMs.
        if len(resolved) != 3:
            result["status"] = "pending_not_all_three_resolved"
            return result
        component_ids = [int(resolved[s][1]["id"]) for s in ("S", "M", "L")]
        if len(set(component_ids)) != 3:
            result["status"] = "pending_components_not_distinct"
            return result

        changes = []
        for size in ("S", "M", "L"):
            parent, component = resolved[size]
            parent_id = int(parent["id"])
            component_id = int(component["id"])
            _upsert_bom(con, parent_id, component_id, TARGETS[size]["qty_per"])

            # If the option was lost from the visible raw-item name, add only the
            # minimal size marker.  Do not overwrite any deliberately renamed name.
            current = con.execute(
                "SELECT item_code,name,unit_cost FROM products WHERE id=?", (component_id,)
            ).fetchone()
            old_name = _norm(current["name"])
            if old_name == "고무장갑":
                new_name = f"고무장갑 [{size}]"
                con.execute(
                    "UPDATE products SET name=?,updated_at=? WHERE id=?",
                    (new_name, core.now_iso(), component_id),
                )
            else:
                new_name = old_name

            changes.append(
                {
                    "size": size,
                    "option_id": TARGETS[size]["option_id"],
                    "parent_id": parent_id,
                    "component_id": component_id,
                    "component_code": _norm(current["item_code"]),
                    "component_name": new_name,
                    "qty_per": TARGETS[size]["qty_per"],
                    "unit_cost": _num(current["unit_cost"]),
                    "own_stock": _own_stock(con, component_id),
                    "status": "bom_linked",
                }
            )

        con.commit()
        result.update({"ok": True, "status": "bom_linked_all_three", "items": changes})
        return result
