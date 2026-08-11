"""RG Manager v0.9.48 canonical RG original-product restoration.

The user supplied authoritative Rocket Growth original option IDs in v0.9.47.
Some of those legitimate products may already have been archived or registered as
return-discount aliases by older heuristics.  That creates a confusing state where
new registration says "already exists" while Item Master hides the product.

This repair makes canonical IDs authoritative normal managed products:
- reactivate existing canonical product rows and mark them as finished goods;
- remove any erroneous return_discount_aliases/return_discount_sales records for
  canonical option IDs;
- remove return-discount inventory deductions created by that erroneous mapping;
- rebuild ordinary SALESSTAT inventory deductions for the canonical product from
  existing sales_stats history, idempotently;
- force future canonical option IDs to bypass every return-sale resolver.

No product row or sales history is physically deleted.
"""
from __future__ import annotations

_APPLIED = False


def _exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


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

        # option_id is intended to be unique. If legacy data ever contains more
        # than one row, keep the oldest row as the canonical managed product and
        # archive later duplicates without deleting history.
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

        # Undo only return-sale postings that were tied to this canonical option.
        # The corresponding normal sale postings are rebuilt below from sales_stats.
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
            # Idempotent: replace the canonical product's standard sales posting.
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

    core_module.CANONICAL_RG_RESTORE_RESULT = {
        "repaired": repaired,
        "missing": missing,
    }

    # Canonical option IDs are authoritative normal sales even if a stale alias is
    # somehow reintroduced later. Strip them before delegating to older resolvers.
    previous_resolve = rd._resolve
    canonical_ids = set(str(x) for x in canonical_module.CANONICAL_RG)

    def resolve(core, db, parsed):
        normal_rows = []
        other_rows = []
        for row in parsed:
            oid = str(row.get("option_id") or "")
            if oid in canonical_ids:
                normal_rows.append(row)
            else:
                other_rows.append(row)
        # Canonical rows intentionally produce no return mapping.
        return previous_resolve(core, db, other_rows) if other_rows else {}

    rd._resolve = resolve
    rd._rg_canonical_restore_v0948_applied = True
    _APPLIED = True
