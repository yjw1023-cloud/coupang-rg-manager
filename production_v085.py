"""RG Manager v0.8.5 production routing.

Production consumes BOM components from 자체창고 and receives the finished
product directly into 쿠팡RG in the same transaction.
"""
from __future__ import annotations

from datetime import datetime


def apply(core_module):
    if getattr(core_module, "_rg_production_v085_applied", False):
        return core_module

    def produce(parent_product_id: int, qty: float, warehouse_id: int, production_date: str,
                memo=None, db_path=None) -> float:
        # warehouse_id is kept only for compatibility with the existing UI.
        # Actual production routing is fixed to 자체창고 -> 쿠팡RG.
        if qty <= 0:
            raise ValueError("생산수량은 0보다 커야 합니다.")
        if db_path is None:
            db_path = core_module.DEFAULT_DB
        core_module.init_db(db_path)

        with core_module._conn(db_path) as c:
            own = c.execute("SELECT id FROM warehouses WHERE name='자체창고'").fetchone()
            rg = c.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
            if not own or not rg:
                raise ValueError("자체창고 또는 쿠팡RG 창고를 찾지 못했습니다.")
            own_id, rg_id = int(own["id"]), int(rg["id"])

            bom = c.execute(
                """SELECT b.component_product_id,b.qty_per,p.unit_cost
                   FROM bom_items b JOIN products p ON p.id=b.component_product_id
                   WHERE b.parent_product_id=?""",
                (parent_product_id,),
            ).fetchall()
            if not bom:
                raise ValueError("등록된 BOM이 없습니다.")
            if any(int(r["component_product_id"]) == int(parent_product_id) for r in bom):
                raise ValueError("BOM 오류: 완제품이 자기 자신의 구성품으로 등록되어 있습니다.")

            # Validate all component stock first so a failed production leaves no partial deductions.
            for r in bom:
                need = float(r["qty_per"]) * float(qty)
                stock = c.execute(
                    "SELECT COALESCE(SUM(qty_delta),0) q FROM inventory_txns WHERE product_id=? AND warehouse_id=?",
                    (r["component_product_id"], own_id),
                ).fetchone()["q"]
                if float(stock or 0) < need:
                    name = c.execute(
                        "SELECT name FROM products WHERE id=?",
                        (r["component_product_id"],),
                    ).fetchone()["name"]
                    raise ValueError(f"구성품 재고 부족: {name} 필요 {need:g}, 자체창고 현재 {float(stock or 0):g}")

            unit_cost = sum(float(r["qty_per"]) * float(r["unit_cost"] or 0) for r in bom)
            ref = f"PROD-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
            txn_date = core_module.norm_date(production_date)
            now = core_module.now_iso()

            for r in bom:
                need = float(r["qty_per"]) * float(qty)
                c.execute(
                    """INSERT INTO inventory_txns
                       (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (txn_date, r["component_product_id"], own_id, -need,
                     "생산소모", ref, memo, now),
                )

            c.execute(
                """INSERT INTO inventory_txns
                   (txn_date,product_id,warehouse_id,qty_delta,txn_type,unit_cost,ref_no,memo,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (txn_date, parent_product_id, rg_id, float(qty),
                 "생산RG입고", unit_cost, ref, memo, now),
            )
            c.execute(
                "UPDATE products SET unit_cost=?,updated_at=? WHERE id=?",
                (unit_cost, now, parent_product_id),
            )
            c.execute(
                """INSERT INTO production_orders
                   (production_date,parent_product_id,qty,warehouse_id,produced_unit_cost,memo,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (txn_date, parent_product_id, float(qty), rg_id, unit_cost, memo, now),
            )
            return unit_cost

    core_module.produce = produce
    core_module._rg_production_v085_applied = True
    return core_module
