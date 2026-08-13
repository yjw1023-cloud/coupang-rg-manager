"""RG Manager v0.9.73 forced BOM registration through core.add_bom().

The earlier v0.9.71/0.9.72 repair wrote bom_items directly.  On a legacy local DB
that can silently fail if the real schema/constraints differ from the simplified
assumption.  This repair deliberately uses the ERP's own core.add_bom() write
path, then re-opens SQLite and verifies the persisted BOM rows.

Requested BOMs:
- 95912623408 aquarium net -> same-name own/raw item x 2
- 95912717676 photo-card album -> same-name own/raw item x 1
- 95912816721 large label sticker -> same-name own/raw item x 1
"""
from __future__ import annotations

import sqlite3

import requested_product_seed_v0970 as product_seed


REQUESTS = [
    ("95912623408", "어항용 뜰채 플라스틱 2p 수족관 새우 베타 구피, Free 2개", 2.0),
    ("95912717676", "프로 야구 포토카드 앨범 바인더, 화이트 50매", 1.0),
    ("95912816721", "대형 견출지 라벨 스티커 300장 라벨지, 혼합 300개입 1개", 1.0),
]


def _connect(db):
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _resolve(core_module, db):
    # Product seeding was already proven to work on the user's local DB. Keep the
    # repair self-healing in case one of the six master rows was later archived.
    product_seed.apply(core_module, db)

    out = []
    with _connect(db) as con:
        for option_id, expected_name, qty in REQUESTS:
            parent = con.execute(
                """SELECT id,name,item_type,active FROM products
                   WHERE CAST(option_id AS TEXT)=?
                   ORDER BY CASE WHEN item_type='finished' THEN 0 ELSE 1 END,
                            CASE WHEN active=1 THEN 0 ELSE 1 END,id
                   LIMIT 1""",
                (option_id,),
            ).fetchone()
            if not parent:
                raise RuntimeError(f"완제품을 찾지 못했습니다: {option_id}")

            # Prefer the exact name used by the finished product because the user
            # requested same-name own-warehouse components.
            parent_name = str(parent["name"] or expected_name)
            component = con.execute(
                """SELECT id,item_code,name,item_type,active FROM products
                   WHERE option_id IS NULL AND name=?
                   ORDER BY CASE WHEN item_type='raw' THEN 0 ELSE 1 END,
                            CASE WHEN active=1 THEN 0 ELSE 1 END,id
                   LIMIT 1""",
                (parent_name,),
            ).fetchone()
            if not component and parent_name != expected_name:
                component = con.execute(
                    """SELECT id,item_code,name,item_type,active FROM products
                       WHERE option_id IS NULL AND name=?
                       ORDER BY CASE WHEN item_type='raw' THEN 0 ELSE 1 END,
                                CASE WHEN active=1 THEN 0 ELSE 1 END,id
                       LIMIT 1""",
                    (expected_name,),
                ).fetchone()
            if not component:
                raise RuntimeError(f"동일명 자체창고 품목을 찾지 못했습니다: {parent_name}")

            pid = int(parent["id"])
            cid = int(component["id"])
            if pid == cid:
                raise RuntimeError(f"완제품과 구성품 ID가 같습니다: {option_id}")

            # Make sure both rows satisfy the production/BOM guard.
            con.execute(
                "UPDATE products SET item_type='finished',active=1,updated_at=? WHERE id=?",
                (core_module.now_iso(), pid),
            )
            con.execute(
                "UPDATE products SET item_type='raw',active=1,updated_at=? WHERE id=?",
                (core_module.now_iso(), cid),
            )
            out.append({
                "option_id": option_id,
                "name": parent_name,
                "parent_id": pid,
                "component_id": cid,
                "component_code": str(component["item_code"] or ""),
                "qty": float(qty),
            })
    return out


def apply(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    rows = _resolve(core_module, db)

    if not hasattr(core_module, "add_bom"):
        raise RuntimeError("현재 ERP core에 add_bom 함수가 없습니다.")

    # These are newly created SKUs and the requested recipe is authoritative.
    # Remove only each requested finished product's current BOM before re-adding
    # one exact component through the ERP's official add_bom() function.
    with _connect(db) as con:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bom_items'"
        ).fetchone()
        if not exists:
            raise RuntimeError("현재 ERP DB에 bom_items 테이블이 없습니다.")
        for r in rows:
            con.execute(
                "DELETE FROM bom_items WHERE parent_product_id=?",
                (int(r["parent_id"]),),
            )

    # Use core's real BOM writer one row at a time, outside our SQLite transaction.
    for r in rows:
        try:
            core_module.add_bom(
                int(r["parent_id"]), int(r["component_id"]), float(r["qty"]), db_path=db
            )
        except TypeError:
            # Older core signature may not expose db_path.
            core_module.add_bom(
                int(r["parent_id"]), int(r["component_id"]), float(r["qty"])
            )

    # Re-open the DB and verify the exact rows that the production engine reads.
    verified = []
    with _connect(db) as con:
        for r in rows:
            got = con.execute(
                """SELECT b.parent_product_id,b.component_product_id,b.qty_per,
                          p.option_id,p.name parent_name,c.item_code component_code,c.name component_name
                   FROM bom_items b
                   JOIN products p ON p.id=b.parent_product_id
                   JOIN products c ON c.id=b.component_product_id
                   WHERE b.parent_product_id=?
                   ORDER BY b.rowid""",
                (int(r["parent_id"]),),
            ).fetchall()
            if len(got) != 1:
                raise RuntimeError(
                    f"BOM 저장 검증 실패: {r['option_id']}의 현재 BOM 행이 {len(got)}개입니다."
                )
            g = got[0]
            if int(g["component_product_id"]) != int(r["component_id"]):
                raise RuntimeError(f"BOM 구성품 검증 실패: {r['option_id']}")
            actual = float(g["qty_per"] or 0)
            if abs(actual - float(r["qty"])) > 1e-9:
                raise RuntimeError(
                    f"BOM 소요량 검증 실패: {r['option_id']} 기대 {r['qty']:g}, 실제 {actual:g}"
                )
            verified.append({
                "option_id": r["option_id"],
                "name": str(g["parent_name"] or r["name"]),
                "component_code": str(g["component_code"] or r["component_code"]),
                "qty": actual,
            })

    return {"count": len(verified), "rows": verified}
