"""RG Manager v0.9.72 forced BOM repair for the three user-requested SKUs.

This module is deliberately independent from inventory/stocktake UI module caching.
It guarantees product masters exist, writes the requested BOMs, and verifies the
persisted parent/component/qty rows before returning.
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


def _table_columns(con, table: str) -> set[str]:
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def apply(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB

    # Keep this self-healing: if v0.9.70 product seeding was not completed,
    # recreate/reactivate the exact finished/raw rows first.
    product_seed.apply(core_module, db)
    core_module.init_db(db)

    result = []
    with core_module._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS bom_items(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   parent_product_id INTEGER NOT NULL,
                   component_product_id INTEGER NOT NULL,
                   qty_per REAL NOT NULL
               )"""
        )
        cols = _table_columns(con, "bom_items")
        required = {"id", "parent_product_id", "component_product_id", "qty_per"}
        if not required.issubset(cols):
            raise RuntimeError(
                "bom_items 스키마가 예상과 다릅니다. 현재 컬럼: " + ", ".join(sorted(cols))
            )

        for option_id, name, qty_per in BOM_REQUESTS:
            parent = con.execute(
                """SELECT id,name,item_type,active
                   FROM products
                   WHERE option_id=?
                   ORDER BY CASE WHEN item_type='finished' THEN 0 ELSE 1 END,
                            CASE WHEN active=1 THEN 0 ELSE 1 END,id
                   LIMIT 1""",
                (option_id,),
            ).fetchone()
            if not parent:
                raise RuntimeError(f"완제품을 찾지 못했습니다: {option_id} / {name}")

            component = con.execute(
                """SELECT id,item_code,name,item_type,active
                   FROM products
                   WHERE option_id IS NULL AND name=?
                   ORDER BY CASE WHEN item_type='raw' THEN 0 ELSE 1 END,
                            CASE WHEN active=1 THEN 0 ELSE 1 END,id
                   LIMIT 1""",
                (name,),
            ).fetchone()
            if not component:
                raise RuntimeError(f"동일명 자체창고 품목을 찾지 못했습니다: {name}")

            parent_id = int(parent["id"])
            component_id = int(component["id"])
            if parent_id == component_id:
                raise RuntimeError(f"완제품과 구성품 ID가 동일합니다: {name}")

            existing = con.execute(
                """SELECT id,qty_per FROM bom_items
                   WHERE parent_product_id=? AND component_product_id=?
                   ORDER BY id""",
                (parent_id, component_id),
            ).fetchall()

            if existing:
                keep_id = int(existing[0]["id"])
                con.execute(
                    "UPDATE bom_items SET qty_per=? WHERE id=?",
                    (float(qty_per), keep_id),
                )
                # Remove only accidental duplicate rows for this exact pair.
                for extra in existing[1:]:
                    con.execute("DELETE FROM bom_items WHERE id=?", (int(extra["id"]),))
                status = "updated"
            else:
                cur = con.execute(
                    """INSERT INTO bom_items(parent_product_id,component_product_id,qty_per)
                       VALUES(?,?,?)""",
                    (parent_id, component_id, float(qty_per)),
                )
                keep_id = int(cur.lastrowid)
                status = "created"

            result.append(
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

        # Verify what is actually persisted in this same transaction/connection.
        verified = []
        for row in result:
            chk = con.execute(
                """SELECT qty_per FROM bom_items
                   WHERE parent_product_id=? AND component_product_id=?
                   ORDER BY id LIMIT 1""",
                (int(row["parent_product_id"]), int(row["component_product_id"])),
            ).fetchone()
            if not chk:
                raise RuntimeError(f"BOM 저장 검증 실패: {row['option_id']} / {row['name']}")
            actual = float(chk["qty_per"] or 0)
            if abs(actual - float(row["qty_per"])) > 1e-9:
                raise RuntimeError(
                    f"BOM 소요량 검증 실패: {row['option_id']} 기대 {row['qty_per']:g}, 실제 {actual:g}"
                )
            verified.append({**row, "verified": True})

    return {"count": len(verified), "rows": verified}
