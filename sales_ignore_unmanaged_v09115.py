"""RG Manager v0.9.115: robustly ignore two user-approved unmanaged old sales options.

This patch works even when an older return_discount_v099 wrapper is still alive in
core.import_sales_stats after a Streamlit hot update. It patches the live wrapper's
resolver globals, not only the newly imported module object.

Only these option IDs are eligible:
- 95594235700
- 95644866786

If a real ERP product exists for an ID, that ID is NOT ignored.
"""
from __future__ import annotations

from typing import Any

TARGET_OPTION_IDS = {"95594235700", "95644866786"}


def _oid(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        return s[:-2]
    return s


def _table_exists(c, name: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _ignored_ids(core, db) -> set[str]:
    core.init_db(db)
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT id,item_code,option_id,unit_cost,active
               FROM products
               WHERE CAST(option_id AS TEXT) IN (?,?)""",
            tuple(sorted(TARGET_OPTION_IDS)),
        ).fetchall()

    real = set()
    for r in rows:
        oid = _oid(r["option_id"])
        code = str(r["item_code"] or "").strip().upper()
        try:
            cost = float(r["unit_cost"] or 0)
        except Exception:
            cost = 0.0
        placeholder = code in {oid.upper(), f"CP-{oid}".upper()} and abs(cost) <= 1e-12
        if not placeholder:
            real.add(oid)
    return set(TARGET_OPTION_IDS) - real


def _walk_functions(fn, seen=None):
    if seen is None:
        seen = set()
    if not callable(fn) or id(fn) in seen:
        return
    seen.add(id(fn))
    yield fn
    closure = getattr(fn, "__closure__", None) or ()
    for cell in closure:
        try:
            value = cell.cell_contents
        except Exception:
            continue
        if callable(value):
            yield from _walk_functions(value, seen)


def _patch_live_resolvers(core, db) -> int:
    patched = 0
    root = core.import_sales_stats
    for fn in _walk_functions(root):
        g = getattr(fn, "__globals__", None)
        if not isinstance(g, dict):
            continue
        if "_resolve" not in g or "_parse_sales_file" not in g or "_load_products" not in g:
            continue
        if g.get("_rg_v09115_resolve_patched"):
            continue
        previous_resolve = g.get("_resolve")
        if not callable(previous_resolve):
            continue

        def resolve(core_arg, db_arg, parsed, _previous=previous_resolve):
            ignored = _ignored_ids(core_arg, db_arg)
            if ignored:
                parsed = [r for r in parsed if _oid(r.get("option_id")) not in ignored]
            return _previous(core_arg, db_arg, parsed)

        g["_resolve"] = resolve
        g["_rg_v09115_resolve_patched"] = True
        patched += 1
    return patched


def _find_import_id(core, db, result, file_name, period_start, period_end):
    if isinstance(result, dict) and result.get("import_id"):
        return int(result["import_id"])
    ps = core.norm_date(period_start)
    pe = core.norm_date(period_end)
    with core._conn(db) as c:
        row = c.execute(
            """SELECT id FROM imports
               WHERE data_type='sales_stats' AND file_name=?
                 AND period_start=? AND period_end=?
               ORDER BY id DESC LIMIT 1""",
            (str(file_name or ""), ps, pe),
        ).fetchone()
        if not row:
            row = c.execute(
                """SELECT id FROM imports
                   WHERE data_type='sales_stats' AND period_start=? AND period_end=?
                   ORDER BY id DESC LIMIT 1""",
                (ps, pe),
            ).fetchone()
    return int(row["id"]) if row else None


def _cleanup_import(core, db, import_id: int, ignored: set[str]) -> dict:
    out = {"sales_rows": 0, "inventory_rows": 0, "placeholder_rows": 0}
    if not ignored:
        return out
    marks = ",".join("?" for _ in ignored)
    params = tuple(sorted(ignored))

    with core._conn(db) as c:
        products = c.execute(
            f"SELECT id,item_code,option_id,unit_cost FROM products "
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
                out["sales_rows"] = max(int(cur.rowcount or 0), 0)
            except Exception:
                pass
            cur = c.execute(
                f"DELETE FROM inventory_txns WHERE ref_no=? AND product_id IN ({pm})",
                (f"SALESSTAT-{int(import_id)}", *pids),
            )
            try:
                out["inventory_rows"] = max(int(cur.rowcount or 0), 0)
            except Exception:
                pass

        if _table_exists(c, "return_discount_sales"):
            c.execute(
                f"DELETE FROM return_discount_sales WHERE import_id=? "
                f"AND discount_option_id IN ({marks})",
                (int(import_id), *params),
            )

        now = core.now_iso()
        for r in products:
            oid = _oid(r["option_id"])
            code = str(r["item_code"] or "").strip().upper()
            try:
                cost = float(r["unit_cost"] or 0)
            except Exception:
                cost = 0.0
            if oid in ignored and code in {oid.upper(), f"CP-{oid}".upper()} and abs(cost) <= 1e-12:
                c.execute(
                    "UPDATE products SET active=0,updated_at=? WHERE id=?",
                    (now, int(r["id"])),
                )
                out["placeholder_rows"] += 1
    return out


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    _patch_live_resolvers(core, db)

    if getattr(core, "_rg_sales_ignore_unmanaged_v09115_applied", False):
        return core

    previous_import = core.import_sales_stats

    def import_sales_stats(source, file_name, period_start, period_end, db_path=None):
        target = db_path or db
        # The live resolver chain is patched again defensively in case another
        # module replaced import_sales_stats after startup.
        _patch_live_resolvers(core, target)
        ignored = _ignored_ids(core, target)
        result = previous_import(source, file_name, period_start, period_end, target)
        import_id = _find_import_id(
            core, target, result, file_name, period_start, period_end
        )
        if import_id is not None and ignored:
            cleaned = _cleanup_import(core, target, import_id, ignored)
            if isinstance(result, dict):
                result = dict(result)
                result["ignored_unmanaged_option_ids"] = sorted(ignored)
                result["ignored_unmanaged_cleanup"] = cleaned
        return result

    core.import_sales_stats = import_sales_stats
    core._rg_sales_ignore_unmanaged_v09115_applied = True
    return core
