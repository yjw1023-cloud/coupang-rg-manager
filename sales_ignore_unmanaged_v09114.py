"""RG Manager v0.9.114: ignore two user-approved old sales options only when unmanaged.

The user confirmed that the old Tesla door-guard product is a legitimate historic
sale, but if its original item is no longer present in ERP it may be ignored for
ERP inventory/P&L purposes.

Option IDs:
- 95594235700
- 95644866786

Safety rule:
- If a real ERP product (including an archived non-placeholder product) exists for
  an option ID, do NOT ignore it.
- If no real ERP product exists, allow the sales-stat import to continue, then
  remove only that option's sales_stats/inventory posting from the just-created
  import and deactivate only any zero-cost auto-created placeholder.
- No other unknown option is ignored.
"""
from __future__ import annotations

from typing import Any

TARGET_OPTION_IDS = {"95594235700", "95644866786"}
_APPLIED = False


def _oid(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def _unmanaged_targets(rd, core, db) -> set[str]:
    try:
        products = rd._load_products(core, db)
    except Exception:
        return set(TARGET_OPTION_IDS)

    real = set()
    for p in products:
        oid = _oid(p.get("option_id"))
        if oid not in TARGET_OPTION_IDS:
            continue
        try:
            placeholder = bool(rd._placeholder(p))
        except Exception:
            placeholder = False
        # Archived/deleted-but-real historical ERP products still count as real.
        if not placeholder:
            real.add(oid)
    return set(TARGET_OPTION_IDS) - real


def _cleanup_import(rd, core, db, import_id: int, ignored: set[str]) -> dict:
    result = {"sales_rows": 0, "inventory_rows": 0, "placeholders_hidden": 0}
    if not ignored:
        return result

    marks = ",".join("?" for _ in ignored)
    params = tuple(sorted(ignored))
    with core._conn(db) as c:
        products = c.execute(
            f"SELECT id,item_code,option_id,unit_cost,active FROM products "
            f"WHERE CAST(option_id AS TEXT) IN ({marks})",
            params,
        ).fetchall()
        pids = [int(r["id"]) for r in products]

        if pids:
            pm = ",".join("?" for _ in pids)
            cur = c.execute(
                f"DELETE FROM sales_stats WHERE import_id=? AND product_id IN ({pm})",
                (int(import_id), *pids),
            )
            try:
                result["sales_rows"] += max(int(cur.rowcount or 0), 0)
            except Exception:
                pass

            cur = c.execute(
                f"DELETE FROM inventory_txns WHERE ref_no=? AND product_id IN ({pm})",
                (f"SALESSTAT-{int(import_id)}", *pids),
            )
            try:
                result["inventory_rows"] += max(int(cur.rowcount or 0), 0)
            except Exception:
                pass

        # Defensive cleanup if a prior wrapper ever posted these as return sales.
        if c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='return_discount_sales'"
        ).fetchone():
            c.execute(
                f"DELETE FROM return_discount_sales WHERE import_id=? "
                f"AND discount_option_id IN ({marks})",
                (int(import_id), *params),
            )

        now = core.now_iso()
        for r in products:
            oid = _oid(r["option_id"])
            if oid not in ignored:
                continue
            item_code = str(r["item_code"] or "").strip()
            try:
                unit_cost = float(r["unit_cost"] or 0)
            except Exception:
                unit_cost = 0.0
            if item_code.upper() in {oid.upper(), f"CP-{oid}".upper()} and abs(unit_cost) <= 1e-12:
                c.execute(
                    "UPDATE products SET active=0,updated_at=? WHERE id=?",
                    (now, int(r["id"])),
                )
                result["placeholders_hidden"] += 1

    return result


def apply(core, return_discount_module, db_path=None):
    global _APPLIED
    if _APPLIED or getattr(core, "_rg_sales_ignore_unmanaged_v09114_applied", False):
        return core

    rd = return_discount_module
    db = db_path or core.DEFAULT_DB
    previous_import = core.import_sales_stats

    def import_sales_stats(source, file_name, period_start, period_end, db_path=None):
        target = db_path or db
        ignored = _unmanaged_targets(rd, core, target)
        result = previous_import(source, file_name, period_start, period_end, target)

        if ignored:
            try:
                import_id = rd._find_import_id(
                    core, target, result, source, period_start, period_end
                )
            except Exception:
                import_id = None
            if import_id is not None:
                cleaned = _cleanup_import(rd, core, target, int(import_id), ignored)
                if isinstance(result, dict):
                    result = dict(result)
                    result["ignored_unmanaged_option_ids"] = sorted(ignored)
                    result["ignored_unmanaged_cleanup"] = cleaned
        return result

    core.import_sales_stats = import_sales_stats
    core._rg_sales_ignore_unmanaged_v09114_applied = True
    _APPLIED = True
    return core
