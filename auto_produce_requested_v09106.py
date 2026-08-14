"""One-time automatic production for the three user-requested RG SKUs in v0.9.106.

This runs once on the first ERP startup after the update. It uses the current BOM,
creates only the raw-material shortage as non-purchase dormant-stock receipt in
자체창고 at the raw material's registered ERP unit cost, consumes the BOM, and
receives finished goods into 쿠팡RG. All writes are in one SQLite transaction.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib


OP_KEY = "v0.9.106-auto-produce-requested-3"
TARGETS = [
    ("95912816721", 46),  # 대형 견출지 라벨 스티커 300장
    ("95912717676", 30),  # 프로 야구 포토카드 앨범
    ("95912623408", 48),  # 어항용 뜰채 2P
]


def _num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def apply(core_module, batch_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    batch_module.ensure_schema(core_module, db)
    prod_date = core_module.norm_date(date.today())
    now = core_module.now_iso()
    ref_no = f"AUTOPROD-V09106-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
    synthetic_hash = hashlib.sha256(OP_KEY.encode("utf-8")).hexdigest()

    with core_module._conn(db) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute(
                """CREATE TABLE IF NOT EXISTS rg_one_time_operations(
                       op_key TEXT PRIMARY KEY,
                       completed_at TEXT NOT NULL,
                       detail TEXT
                   )"""
            )
            done = c.execute(
                "SELECT completed_at,detail FROM rg_one_time_operations WHERE op_key=?",
                (OP_KEY,),
            ).fetchone()
            if done:
                c.rollback()
                return {"status": "already_done", "completed_at": done["completed_at"]}

            existing_batch = c.execute(
                "SELECT id,created_at FROM production_batch_imports WHERE file_hash=?",
                (synthetic_hash,),
            ).fetchone()
            if existing_batch:
                c.execute(
                    "INSERT OR IGNORE INTO rg_one_time_operations(op_key,completed_at,detail) VALUES(?,?,?)",
                    (OP_KEY, now, f"recovered from production_batch_imports id={existing_batch['id']}"),
                )
                c.commit()
                return {"status": "already_done", "batch_id": int(existing_batch["id"])}

            own = c.execute("SELECT id FROM warehouses WHERE name='자체창고'").fetchone()
            rg = c.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
            if not own or not rg:
                raise RuntimeError("자체창고 또는 쿠팡RG 창고를 찾지 못했습니다.")
            own_id, rg_id = int(own["id"]), int(rg["id"])

            prepared = []
            total_need = {}
            component_meta = {}

            for option_id, qty in TARGETS:
                rows = c.execute(
                    """SELECT id,item_code,option_id,name,item_type,active
                       FROM products WHERE CAST(option_id AS TEXT)=? ORDER BY id""",
                    (option_id,),
                ).fetchall()
                if len(rows) != 1:
                    raise RuntimeError(f"자동생산 대상 옵션ID {option_id}의 ERP 상품을 정확히 1개 찾지 못했습니다.")
                p = rows[0]
                pid = int(p["id"])
                bom = c.execute(
                    """SELECT b.component_product_id,b.qty_per,
                              cp.item_code,cp.name,cp.unit_cost
                       FROM bom_items b
                       JOIN products cp ON cp.id=b.component_product_id
                       WHERE b.parent_product_id=? ORDER BY b.id""",
                    (pid,),
                ).fetchall()
                if not bom:
                    raise RuntimeError(f"자동생산 대상 {option_id}에 BOM이 없습니다.")
                if any(int(b["component_product_id"]) == pid for b in bom):
                    raise RuntimeError(f"자동생산 대상 {option_id}의 BOM에 자기 자신이 포함되어 있습니다.")

                unit_cost = 0.0
                for b in bom:
                    cid = int(b["component_product_id"])
                    qper = _num(b["qty_per"])
                    comp_cost = _num(b["unit_cost"])
                    if qper <= 0:
                        raise RuntimeError(f"자동생산 대상 {option_id}의 BOM 소요량이 0 이하입니다.")
                    if comp_cost <= 0:
                        raise RuntimeError(
                            f"원재료 {b['item_code'] or cid} ({b['name'] or ''})의 ERP 등록원가가 0원입니다. 자동생산을 중단합니다."
                        )
                    need = qper * qty
                    unit_cost += qper * comp_cost
                    total_need[cid] = total_need.get(cid, 0.0) + need
                    component_meta[cid] = {
                        "item_code": str(b["item_code"] or ""),
                        "name": str(b["name"] or ""),
                        "unit_cost": comp_cost,
                    }
                prepared.append({
                    "option_id": option_id,
                    "product_id": pid,
                    "name": str(p["name"] or ""),
                    "qty": int(qty),
                    "bom": bom,
                    "unit_cost": unit_cost,
                })

            if len(prepared) != 3:
                raise RuntimeError("자동생산 대상 3개 상품 검증에 실패했습니다.")

            live = {}
            if total_need:
                marks = ",".join("?" for _ in total_need)
                rows = c.execute(
                    f"""SELECT product_id,COALESCE(SUM(qty_delta),0) qty
                        FROM inventory_txns
                        WHERE warehouse_id=? AND product_id IN ({marks})
                        GROUP BY product_id""",
                    (own_id, *total_need.keys()),
                ).fetchall()
                live = {int(r["product_id"]): _num(r["qty"]) for r in rows}

            dormant_qty = 0.0
            dormant_value = 0.0
            for cid, required in total_need.items():
                current = live.get(cid, 0.0)
                usable = max(current, 0.0)
                shortage = max(required - usable, 0.0)
                if shortage <= 1e-12:
                    continue
                meta = component_meta[cid]
                c.execute(
                    """INSERT INTO inventory_txns
                       (txn_date,product_id,warehouse_id,qty_delta,txn_type,unit_cost,ref_no,memo,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        prod_date, cid, own_id, shortage, "불용재고전환입고", meta["unit_cost"],
                        ref_no + "-DORMANT",
                        "v0.9.106 업데이트 자동생산용 불용재고 전환입고 (매입 아님)",
                        now,
                    ),
                )
                dormant_qty += shortage
                dormant_value += shortage * meta["unit_cost"]

            total_finished = sum(x["qty"] for x in prepared)
            cur = c.execute(
                """INSERT INTO production_batch_imports
                   (file_hash,file_name,production_date,target_rows,total_qty,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (synthetic_hash, "v0.9.106 업데이트 자동생산", prod_date, 3, total_finished, now),
            )
            batch_id = int(cur.lastrowid)

            for idx, p in enumerate(prepared, start=1):
                memo = f"v0.9.106 업데이트 자동생산 / 옵션ID {p['option_id']}"
                for b in p["bom"]:
                    need = _num(b["qty_per"]) * p["qty"]
                    c.execute(
                        """INSERT INTO inventory_txns
                           (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (prod_date, int(b["component_product_id"]), own_id, -need, "생산소모", ref_no, memo, now),
                    )

                c.execute(
                    """INSERT INTO inventory_txns
                       (txn_date,product_id,warehouse_id,qty_delta,txn_type,unit_cost,ref_no,memo,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                    (prod_date, p["product_id"], rg_id, p["qty"], "생산RG입고", p["unit_cost"], ref_no, memo, now),
                )
                c.execute(
                    "UPDATE products SET unit_cost=?,updated_at=? WHERE id=?",
                    (p["unit_cost"], now, p["product_id"]),
                )
                c.execute(
                    """INSERT INTO production_orders
                       (production_date,parent_product_id,qty,warehouse_id,produced_unit_cost,memo,created_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (prod_date, p["product_id"], p["qty"], rg_id, p["unit_cost"], memo, now),
                )
                c.execute(
                    """INSERT INTO production_batch_lines
                       (batch_id,source_row,option_id,product_id,qty,produced_unit_cost,ref_no,created_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (batch_id, idx, p["option_id"], p["product_id"], p["qty"], p["unit_cost"], ref_no, now),
                )

            detail = (
                f"targets=3,total_finished={total_finished},dormant_qty={dormant_qty:g},"
                f"dormant_value={dormant_value:g},batch_id={batch_id}"
            )
            c.execute(
                "INSERT INTO rg_one_time_operations(op_key,completed_at,detail) VALUES(?,?,?)",
                (OP_KEY, now, detail),
            )
            c.commit()
            return {
                "status": "produced",
                "batch_id": batch_id,
                "targets": 3,
                "total_finished": total_finished,
                "dormant_qty": dormant_qty,
                "dormant_value": dormant_value,
                "production_date": prod_date,
            }
        except Exception:
            c.rollback()
            raise
