"""RG Manager v0.9.48+ canonical RG restoration and v0.9.49 cost repair.

The user supplied authoritative Rocket Growth original option IDs in v0.9.47.
Some legitimate products may already have been archived or registered as
return-discount aliases by older heuristics.  This repair restores those originals.

v0.9.49 also applies user-supplied baseline unit costs once per option ID.  A small
migration table records each applied option so later real production can update the
finished-product unit cost normally without the startup repair overwriting it again.

No product row or sales history is physically deleted.
"""
from __future__ import annotations

_APPLIED = False

# User-confirmed baseline costs (KRW / unit), 2026-08-11.
USER_BASELINE_COSTS = {
    "95612444686": 3540.0,
    "95849578033": 1560.0,
    "95849578032": 1560.0,
    "95648063867": 1310.0,
    "95631138189": 6493.0,
}


def _exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _apply_user_baseline_costs(core):
    """Apply each user-provided cost only once to the matching option ID."""
    now = core.now_iso()
    applied, already, missing = [], [], []
    with core._conn(core.DEFAULT_DB) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS manual_cost_migrations (
                   option_id TEXT PRIMARY KEY,
                   unit_cost REAL NOT NULL,
                   applied_at TEXT NOT NULL
               )"""
        )
        for oid, cost in USER_BASELINE_COSTS.items():
            done = con.execute(
                "SELECT unit_cost FROM manual_cost_migrations WHERE option_id=?",
                (str(oid),),
            ).fetchone()
            if done:
                already.append(str(oid))
                continue

            row = con.execute(
                """SELECT id,active FROM products
                   WHERE CAST(option_id AS TEXT)=?
                   ORDER BY active DESC,id ASC LIMIT 1""",
                (str(oid),),
            ).fetchone()
            if not row:
                missing.append(str(oid))
                continue

            pid = int(row["id"])
            con.execute(
                "UPDATE products SET unit_cost=?,updated_at=? WHERE id=?",
                (float(cost), now, pid),
            )
            con.execute(
                """INSERT INTO manual_cost_migrations(option_id,unit_cost,applied_at)
                   VALUES(?,?,?)""",
                (str(oid), float(cost), now),
            )
            applied.append({"option_id": str(oid), "product_id": pid, "unit_cost": float(cost)})

    return {"applied": applied, "already": already, "missing": missing}


def _repair_one(core, rd, oid: str, canonical_name: str):
    db = core.DEFAULT_DB
    rd._ensure_schema(core, db)
    now = core.now_iso()

    with core._conn(db) as con:
        rows = con.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products WHERE CAST(option_id AS TEXT)=? ORDER BY id""",
            (str(oid),),
        ).fetchall()
        if not rows:
            return None

        primary = rows[0]
        pid = int(primary["id"])
        duplicate_ids = [int(r["id"]) for r in rows[1:]]

        alias_row = con.execute(
            "SELECT parent_product_id FROM return_discount_aliases WHERE discount_option_id=?",
            (str(oid),),
        ).fetchone() if _exists(con, "return_discount_aliases") else None

        return_sales = []
        if _exists(con, "return_discount_sales"):
            return_sales = con.execute(
                "SELECT import_id FROM return_discount_sales WHERE discount_option_id=?",
                (str(oid),),
            ).fetchall()

        for r in return_sales:
            import_id = int(r["import_id"])
            con.execute(
                "DELETE FROM inventory_txns WHERE txn_type='반품할인판매차감' AND ref_no=?",
                (f"RETSALE-{import_id}-{oid}",),
            )

        if _exists(con, "return_discount_sales"):
            con.execute(
                "DELETE FROM return_discount_sales WHERE discount_option_id=?",
                (str(oid),),
            )
        if _exists(con, "return_discount_aliases"):
            con.execute(
                "DELETE FROM return_discount_aliases WHERE discount_option_id=?",
                (str(oid),),
            )

        con.execute(
            """UPDATE products
               SET active=1,item_type='finished',name=?,updated_at=?
               WHERE id=?""",
            (str(canonical_name), now, pid),
        )
        for dup_id in duplicate_ids:
            con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=?",
                (now, dup_id),
            )

        rg = con.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
        rg_id = int(rg["id"]) if rg else None

        sales_rows = []
        if rg_id is not None and _exists(con, "sales_stats") and _exists(con, "imports"):
            sales_rows = con.execute(
                """SELECT s.import_id,COALESCE(SUM(s.net_qty),0) net_qty,
                          COALESCE(i.period_end,i.period_start,'') txn_date
                   FROM sales_stats s
                   JOIN imports i ON i.id=s.import_id
                   WHERE s.product_id=?
                   GROUP BY s.import_id,i.period_end,i.period_start""",
                (pid,),
            ).fetchall()

        reposted = 0
        for sr in sales_rows:
            import_id = int(sr["import_id"])
            qty = float(sr["net_qty"] or 0)
            ref = f"SALESSTAT-{import_id}"
            con.execute(
                "DELETE FROM inventory_txns WHERE txn_type='판매차감' AND ref_no=? AND product_id=?",
                (ref, pid),
            )
            if abs(qty) <= 1e-12:
                continue
            con.execute(
                """INSERT INTO inventory_txns
                   (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (
                    str(sr["txn_date"] or ""), pid, rg_id, -qty, "판매차감", ref,
                    "정상 원상품 복구: 재고현황 판매통계", now,
                ),
            )
            reposted += 1

    return {
        "option_id": str(oid),
        "product_id": pid,
        "was_active": int(primary["active"] or 0),
        "had_alias": bool(alias_row),
        "return_sales_removed": len(return_sales),
        "normal_sales_reposted": reposted,
        "duplicates_archived": len(duplicate_ids),
    }


def apply(core_module, return_discount_module, canonical_module) -> None:
    global _APPLIED
    rd = return_discount_module
    if _APPLIED or getattr(rd, "_rg_canonical_restore_v0948_applied", False):
        return

    repaired = []
    missing = []
    for oid, name in canonical_module.CANONICAL_RG.items():
        result = _repair_one(core_module, rd, str(oid), str(name))
        if result is None:
            missing.append(str(oid))
        else:
            repaired.append(result)

    cost_result = _apply_user_baseline_costs(core_module)

    core_module.CANONICAL_RG_RESTORE_RESULT = {
        "repaired": repaired,
        "missing": missing,
        "baseline_costs": cost_result,
    }

    previous_resolve = rd._resolve
    canonical_ids = set(str(x) for x in canonical_module.CANONICAL_RG)

    def resolve(core, db, parsed):
        other_rows = []
        for row in parsed:
            oid = str(row.get("option_id") or "")
            if oid not in canonical_ids:
                other_rows.append(row)
        return previous_resolve(core, db, other_rows) if other_rows else {}

    rd._resolve = resolve
    rd._rg_canonical_restore_v0948_applied = True
    _APPLIED = True
