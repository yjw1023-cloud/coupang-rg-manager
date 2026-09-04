"""v0.9.151 hotfix for sales-stat return quantity enrichment.

SQLite exposes ``rowid`` using the INTEGER PRIMARY KEY column name when a table
has one.  v0.9.150 selected ``rowid`` and then attempted ``row['rowid']``, which
raises ``IndexError: No item with that key`` on the current sales_stats schema.
This patch aliases rowid explicitly and replaces only the enrichment function.
"""
from __future__ import annotations


def apply(base):
    if getattr(base, "_rg_sales_stats_returns_hotfix_v09151", False):
        return base

    def enrich_import(core, db, import_id: int, parsed):
        base.ensure_schema(core, db)
        result = {
            "matched_options": 0,
            "unmatched_options": 0,
            "sales_qty": 0.0,
            "cancel_qty": 0.0,
        }
        if not parsed:
            return result

        with core._conn(db) as c:
            pcols = base._cols(c, "products")
            if not {"id", "option_id"}.issubset(pcols):
                return result

            code_expr = "item_code" if "item_code" in pcols else "'' AS item_code"
            direct = {}
            for r in c.execute(f"SELECT id,option_id,{code_expr} FROM products"):
                for raw in (r["option_id"], r["item_code"]):
                    key = base._oid(raw)
                    if key:
                        direct.setdefault(key, int(r["id"]))

            aliases = {}
            if base._exists(c, "return_discount_aliases"):
                aliases = {
                    base._oid(r["discount_option_id"]): int(r["parent_product_id"])
                    for r in c.execute(
                        "SELECT discount_option_id,parent_product_id FROM return_discount_aliases"
                    )
                }

            agg = {}
            for row in parsed:
                oid = base._oid(row.get("option_id"))
                pid = aliases.get(oid) or direct.get(oid)
                if pid is None:
                    result["unmatched_options"] += 1
                    continue
                target = agg.setdefault(
                    int(pid),
                    {"sales_qty": 0.0, "cancel_qty": 0.0, "net_qty": 0.0},
                )
                for key in target:
                    target[key] += base._num(row.get(key))
                result["matched_options"] += 1

            c.execute(
                "UPDATE sales_stats SET sales_qty=0,cancel_qty=0 WHERE import_id=?",
                (int(import_id),),
            )
            for pid, values in agg.items():
                rows = c.execute(
                    """SELECT rowid AS _rg_rowid
                       FROM sales_stats
                       WHERE import_id=? AND product_id=?
                       ORDER BY rowid""",
                    (int(import_id), int(pid)),
                ).fetchall()
                if not rows:
                    result["unmatched_options"] += 1
                    continue

                rowid = int(rows[0]["_rg_rowid"])
                c.execute(
                    "UPDATE sales_stats SET sales_qty=?,cancel_qty=? WHERE rowid=?",
                    (
                        float(values["sales_qty"]),
                        float(values["cancel_qty"]),
                        rowid,
                    ),
                )
                result["sales_qty"] += float(values["sales_qty"])
                result["cancel_qty"] += float(values["cancel_qty"])

        return result

    base.enrich_import = enrich_import
    base._rg_sales_stats_returns_hotfix_v09151 = True
    return base
