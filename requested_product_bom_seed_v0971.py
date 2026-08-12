"""RG Manager v0.9.71 idempotent product + BOM seed for user-requested SKUs.

Ensures the three requested Coupang finished products and their same-name own-
warehouse raw items exist, then registers the requested BOM quantities:
- aquarium net: 2 raw units per finished unit
- baseball photo-card album: 1 raw unit per finished unit
- large label sticker: 1 raw unit per finished unit

Safe/idempotent behavior:
- delegates product creation/reactivation to requested_product_seed_v0970;
- finds finished products by exact Coupang option ID;
- finds raw components by exact same name + option_id IS NULL;
- updates an existing parent/component BOM quantity instead of duplicating it;
- removes accidental duplicate rows for the exact same parent/component pair;
- does not create inventory or production history.
"""
from __future__ import annotations

import requested_product_seed_v0970 as product_seed


BOM_REQUESTS = [
    (
        "95912623408",
        "어항용 뜰채 플라스틱 2p 수족관 새우 베타 구피, Free 2개",
        2.0,
    ),
    (
        "95912717676",
        "프로 야구 포토카드 앨범 바인더, 화이트 50매",
        1.0,
    ),
    (
        "95912816721",
        "대형 견출지 라벨 스티커 300장 라벨지, 혼합 300개입 1개",
        1.0,
    ),
]


def apply(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    # First guarantee that both finished and raw master rows exist.
    product_result = product_seed.apply(core_module, db)
    core_module.init_db(db)

    bom_result = []
    with core_module._conn(db) as con:
        # Core normally creates this table; keep the seed self-contained for older DBs.
        con.execute(
            """CREATE TABLE IF NOT EXISTS bom_items(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   parent_product_id INTEGER NOT NULL,
                   component_product_id INTEGER NOT NULL,
                   qty_per REAL NOT NULL,
                   UNIQUE(parent_product_id,component_product_id)
               )"""
        )

        for option_id, name, qty_per in BOM_REQUESTS:
            parent = con.execute(
                """SELECT id,name,item_type,active
                   FROM products
                   WHERE option_id=?
                   ORDER BY CASE WHEN item_type='finished' THEN 0 ELSE 1 END,id
                   LIMIT 1""",
                (option_id,),
            ).fetchone()
            if not parent:
                raise ValueError(f"BOM 완제품을 찾을 수 없습니다: {option_id} / {name}")

            component = con.execute(
                """SELECT id,item_code,name,item_type,active
                   FROM products
                   WHERE option_id IS NULL AND name=?
                   ORDER BY CASE WHEN item_type='raw' THEN 0 ELSE 1 END,id
                   LIMIT 1""",
                (name,),
            ).fetchone()
            if not component:
                raise ValueError(f"BOM 자체창고 구성품을 찾을 수 없습니다: {name}")

            parent_id = int(parent["id"])
            component_id = int(component["id"])
            if parent_id == component_id:
                raise ValueError(f"BOM 오류: 완제품과 구성품이 동일합니다: {name}")

            existing = con.execute(
                """SELECT id,qty_per FROM bom_items
                   WHERE parent_product_id=? AND component_product_id=?
                   ORDER BY id""",
                (parent_id, component_id),
            ).fetchall()

            if existing:
                keep_id = int(existing[0]["id"])
                old_qty = float(existing[0]["qty_per"] or 0)
                con.execute(
                    "UPDATE bom_items SET qty_per=? WHERE id=?",
                    (float(qty_per), keep_id),
                )
                for extra in existing[1:]:
                    con.execute("DELETE FROM bom_items WHERE id=?", (int(extra["id"]),))
                status = "unchanged" if abs(old_qty - float(qty_per)) <= 1e-12 and len(existing) == 1 else "updated"
            else:
                cur = con.execute(
                    """INSERT INTO bom_items(parent_product_id,component_product_id,qty_per)
                       VALUES(?,?,?)""",
                    (parent_id, component_id, float(qty_per)),
                )
                keep_id = int(cur.lastrowid)
                status = "created"

            bom_result.append(
                {
                    "option_id": option_id,
                    "name": name,
                    "parent_product_id": parent_id,
                    "component_product_id": component_id,
                    "component_code": str(component["item_code"] or ""),
                    "qty_per": float(qty_per),
                    "bom_id": keep_id,
                    "status": status,
                }
            )

    return {"products": product_result, "bom": bom_result}
