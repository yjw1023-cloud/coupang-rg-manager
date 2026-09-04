"""v0.9.152: enrich an already-imported sales-stat file without re-importing sales.

When the same sales-stat period/file is submitted again only to recover gross/cancel
quantities, bypass the legacy replacement/import wrapper chain entirely. This keeps
existing sales rows and inventory deductions untouched and only fills sales_qty /
cancel_qty on the existing sales_stats import.
"""
from __future__ import annotations

_APPLIED = False


def _existing_import(core, base, db, source, period_start, period_end, parsed):
    ps = core.norm_date(period_start) if callable(getattr(core, "norm_date", None)) else str(period_start)[:10]
    pe = core.norm_date(period_end) if callable(getattr(core, "norm_date", None)) else str(period_end)[:10]
    digest = None
    try:
        digest = core.file_hash(source)
    except Exception:
        pass

    with core._conn(db) as c:
        rows = c.execute(
            """SELECT id,file_hash FROM imports
               WHERE data_type='sales_stats' AND period_start=? AND period_end=?
               ORDER BY id DESC""",
            (ps, pe),
        ).fetchall()
        if not rows:
            return None

        # Strongest match: exact file contents already imported.
        if digest:
            for r in rows:
                if str(r["file_hash"] or "") == str(digest):
                    return int(r["id"])

        # Safe fallback for a re-downloaded copy whose workbook metadata changed:
        # only reuse an existing import when option-level NET quantities match.
        pcols = base._cols(c, "products")
        if not {"id", "option_id"}.issubset(pcols):
            return None
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

        incoming = {}
        for row in parsed:
            oid = base._oid(row.get("option_id"))
            pid = aliases.get(oid) or direct.get(oid)
            if pid is None:
                continue
            incoming[int(pid)] = incoming.get(int(pid), 0.0) + base._num(row.get("net_qty"))

        if not incoming:
            return None

        for r in rows:
            iid = int(r["id"])
            existing_rows = c.execute(
                """SELECT product_id,COALESCE(SUM(net_qty),0) net_qty
                   FROM sales_stats WHERE import_id=? GROUP BY product_id""",
                (iid,),
            ).fetchall()
            existing = {int(x["product_id"]): float(x["net_qty"] or 0) for x in existing_rows}
            keys = set(existing) | set(incoming)
            if all(abs(existing.get(k, 0.0) - incoming.get(k, 0.0)) <= 1e-9 for k in keys):
                return iid
    return None


def apply(core, base, db_path=None):
    global _APPLIED
    if _APPLIED or getattr(core, "_rg_existing_sales_enrich_v09152_applied", False):
        return core

    db = db_path or core.DEFAULT_DB
    previous = core.import_sales_stats

    def import_sales_stats(source, file_name, period_start, period_end, db_path=None):
        target = db_path or db
        base.ensure_schema(core, target)
        parsed, meta = base.parse_sales_quantities(source)

        if meta.get("available"):
            import_id = _existing_import(
                core, base, target, source, period_start, period_end, parsed
            )
            if import_id is not None:
                stats = base.enrich_import(core, target, import_id, parsed)
                return {
                    "status": "existing_enriched",
                    "import_id": int(import_id),
                    "file_name": str(file_name or ""),
                    "period_start": core.norm_date(period_start),
                    "period_end": core.norm_date(period_end),
                    "sales_qty_preserved": float(stats.get("sales_qty") or 0),
                    "cancel_qty_preserved": float(stats.get("cancel_qty") or 0),
                    "sales_qty_source": meta.get("gross_col", ""),
                    "cancel_qty_source": meta.get("cancel_col", ""),
                    "inventory_deducted_rows": 0,
                    "inventory_deducted_qty": 0.0,
                    "replaced_previous_period": False,
                    "message": "기존 판매자료는 유지하고 판매/취소·반품수량만 보강했습니다.",
                }

        return previous(source, file_name, period_start, period_end, target)

    core.import_sales_stats = import_sales_stats
    core._rg_existing_sales_enrich_v09152_applied = True
    _APPLIED = True
    return core
