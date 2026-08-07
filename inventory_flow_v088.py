"""RG Manager v0.8.8 inventory-flow rules.

Business rules:
- Production is an actual event. Never block it because component stock is short.
  Consume the full BOM quantity from 자체창고, allowing negative component stock,
  and receive the finished quantity directly into 쿠팡RG.
- A newly imported 재고현황 판매통계 is an actual sales event for inventory.
  Deduct each product's net sold quantity from 쿠팡RG, allowing negative RG stock.
- Same-file duplicate uploads never deduct inventory twice.
- When the user explicitly replaces an already imported sales-stat period, old
  sales-stat inventory deductions and old sales-stat imports for that period are
  removed only after the new import and new deductions succeed.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st


def _replace_key(start_iso: str, end_iso: str) -> str:
    return f"_rg_replace_sales_stats_{start_iso.replace('-', '')}_{end_iso.replace('-', '')}"


def _replacement_confirmed(start_iso: str, end_iso: str) -> bool:
    try:
        return bool(st.session_state.get(_replace_key(start_iso, end_iso), False))
    except Exception:
        return False


def apply(core_module):
    if getattr(core_module, "_rg_inventory_flow_v088_applied", False):
        return core_module

    # sales_period_v087 is applied before this module, so capturing here preserves
    # its date validation / same-period confirmation guard.
    previous_import_sales_stats = core_module.import_sales_stats

    def produce(parent_product_id: int, qty: float, warehouse_id: int, production_date: str,
                memo=None, db_path=None) -> float:
        # warehouse_id is retained only for UI compatibility.
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
                (int(parent_product_id),),
            ).fetchall()
            if not bom:
                raise ValueError("등록된 BOM이 없습니다.")
            if any(int(r["component_product_id"]) == int(parent_product_id) for r in bom):
                raise ValueError("BOM 오류: 완제품이 자기 자신의 구성품으로 등록되어 있습니다.")

            # No stock-availability guard here by design. Actual production must be
            # recorded in full; shortages remain visible as negative inventory.
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
                    (txn_date, int(r["component_product_id"]), own_id, -need,
                     "생산소모", ref, memo, now),
                )

            c.execute(
                """INSERT INTO inventory_txns
                   (txn_date,product_id,warehouse_id,qty_delta,txn_type,unit_cost,ref_no,memo,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (txn_date, int(parent_product_id), rg_id, float(qty),
                 "생산RG입고", unit_cost, ref, memo, now),
            )
            c.execute(
                "UPDATE products SET unit_cost=?,updated_at=? WHERE id=?",
                (unit_cost, now, int(parent_product_id)),
            )
            c.execute(
                """INSERT INTO production_orders
                   (production_date,parent_product_id,qty,warehouse_id,produced_unit_cost,memo,created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (txn_date, int(parent_product_id), float(qty), rg_id, unit_cost, memo, now),
            )
            return unit_cost

    def import_sales_stats(source, file_name: str, period_start: str, period_end: str,
                           db_path=None):
        if db_path is None:
            db_path = core_module.DEFAULT_DB
        ps, pe = core_module.norm_date(period_start), core_module.norm_date(period_end)
        core_module.init_db(db_path)

        # Remember what existed before this upload. The v0.8.7 guard decides
        # whether a different file for the same period is permitted.
        with core_module._conn(db_path) as c:
            old_rows = c.execute(
                """SELECT id,file_hash FROM imports
                   WHERE data_type='sales_stats' AND period_start=? AND period_end=?
                   ORDER BY id""",
                (ps, pe),
            ).fetchall()
            old_ids = [int(r["id"]) for r in old_rows]
            old_hashes = {str(r["file_hash"] or "") for r in old_rows}

        incoming_hash = core_module.file_hash(source)
        replacing = bool(old_ids and incoming_hash not in old_hashes and _replacement_confirmed(ps, pe))

        result = previous_import_sales_stats(source, file_name, period_start, period_end, db_path)
        if result.get("status") != "imported":
            # duplicate file -> previous inventory deduction remains; never deduct twice.
            return result

        import_id = int(result["import_id"])
        ref_no = f"SALESSTAT-{import_id}"
        now = core_module.now_iso()

        try:
            with core_module._conn(db_path) as c:
                rg = c.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
                if not rg:
                    raise ValueError("쿠팡RG 창고를 찾지 못했습니다.")
                rg_id = int(rg["id"])

                # Idempotency if this function is ever re-entered with the same import id.
                c.execute(
                    "DELETE FROM inventory_txns WHERE txn_type='판매차감' AND ref_no=?",
                    (ref_no,),
                )

                sales_rows = c.execute(
                    """SELECT product_id,COALESCE(SUM(net_qty),0) net_qty
                       FROM sales_stats WHERE import_id=?
                       GROUP BY product_id""",
                    (import_id,),
                ).fetchall()

                deducted_rows = 0
                deducted_qty = 0.0
                for r in sales_rows:
                    net_qty = float(r["net_qty"] or 0)
                    if abs(net_qty) <= 1e-12:
                        continue
                    c.execute(
                        """INSERT INTO inventory_txns
                           (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (pe, int(r["product_id"]), rg_id, -net_qty, "판매차감", ref_no,
                         f"재고현황 판매통계 {ps} ~ {pe}", now),
                    )
                    deducted_rows += 1
                    deducted_qty += net_qty

                # True replacement: only after the new import + deduction has been
                # prepared successfully do we remove the old period and its deductions.
                if replacing:
                    for old_id in old_ids:
                        c.execute(
                            "DELETE FROM inventory_txns WHERE txn_type='판매차감' AND ref_no=?",
                            (f"SALESSTAT-{old_id}",),
                        )
                        c.execute("DELETE FROM imports WHERE id=? AND data_type='sales_stats'", (old_id,))

            result = dict(result)
            result["inventory_deducted_rows"] = deducted_rows
            result["inventory_deducted_qty"] = deducted_qty
            result["replaced_previous_period"] = replacing
            return result
        except Exception:
            # The new sales import was committed by the original importer. If inventory
            # posting fails, remove only that new import so a clean retry is possible;
            # old period data is left intact because its deletion is in the transaction above.
            with core_module._conn(db_path) as c:
                c.execute("DELETE FROM inventory_txns WHERE txn_type='판매차감' AND ref_no=?", (ref_no,))
                c.execute("DELETE FROM imports WHERE id=? AND data_type='sales_stats'", (import_id,))
            raise

    core_module.produce = produce
    core_module.import_sales_stats = import_sales_stats
    core_module._rg_inventory_flow_v088_applied = True
    return core_module
