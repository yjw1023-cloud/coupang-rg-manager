"""v0.9.170 direct confirmed rubber-glove BOM registration.

User-confirmed mapping:
- Coupang option 96012086788 (S) -> JDS761 x5
- Coupang option 96012086789 (M) -> JDS762 x5
- Coupang option 96012086790 (L) -> JDS763 x5

No inventory transaction is created or changed. This module only writes bom_items
and verifies the saved rows immediately.
"""
from __future__ import annotations

TARGETS = [
    ("S", "96012086788", "JDS761", 5.0, "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 소(S)"),
    ("M", "96012086789", "JDS762", 5.0, "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 중(M)"),
    ("L", "96012086790", "JDS763", 5.0, "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 대(L)"),
]


def _ensure_parent(core, con, option_id, name):
    row = con.execute(
        "SELECT id FROM products WHERE CAST(option_id AS TEXT)=? ORDER BY id LIMIT 1",
        (option_id,),
    ).fetchone()
    if row:
        pid = int(row["id"])
        con.execute(
            "UPDATE products SET item_code=?,name=?,item_type='finished',active=1,updated_at=? WHERE id=?",
            (f"CP-{option_id}", name, core.now_iso(), pid),
        )
        return pid
    cur = con.execute(
        "INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at) VALUES(?,?,?,'finished',0,1,?)",
        (f"CP-{option_id}", option_id, name, core.now_iso()),
    )
    return int(cur.lastrowid)


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    with core._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS bom_items(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   parent_product_id INTEGER NOT NULL,
                   component_product_id INTEGER NOT NULL,
                   qty_per REAL NOT NULL,
                   UNIQUE(parent_product_id,component_product_id)
               )"""
        )

        resolved = []
        for size, option_id, raw_code, qty_per, finished_name in TARGETS:
            component = con.execute(
                "SELECT id,item_code,name FROM products WHERE UPPER(TRIM(item_code))=? AND active=1 ORDER BY id LIMIT 1",
                (raw_code.upper(),),
            ).fetchone()
            if component is None:
                return {"ok": False, "status": "raw_missing", "missing": raw_code, "inventory_changed": False}
            parent_id = _ensure_parent(core, con, option_id, finished_name)
            resolved.append((size, option_id, raw_code, qty_per, parent_id, int(component["id"])))

        if len({x[5] for x in resolved}) != 3 or len({x[4] for x in resolved}) != 3:
            return {"ok": False, "status": "ids_not_distinct", "inventory_changed": False}

        for size, option_id, raw_code, qty_per, parent_id, component_id in resolved:
            con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (parent_id,))
            con.execute(
                "INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)",
                (parent_id, component_id, qty_per),
            )

        con.commit()

        verified = []
        for size, option_id, raw_code, qty_per, parent_id, component_id in resolved:
            row = con.execute(
                "SELECT b.qty_per,p.item_code FROM bom_items b JOIN products p ON p.id=b.component_product_id WHERE b.parent_product_id=?",
                (parent_id,),
            ).fetchone()
            if row is None or str(row["item_code"] or "").strip().upper() != raw_code.upper() or abs(float(row["qty_per"] or 0)-qty_per) > 1e-9:
                return {"ok": False, "status": "verify_failed", "size": size, "inventory_changed": False}
            verified.append({"size": size, "option_id": option_id, "component_code": raw_code, "qty_per": qty_per})

        return {"ok": True, "status": "verified_3_boms", "items": verified, "inventory_changed": False}
