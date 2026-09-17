"""v0.9.219 hard fix for unresolved return-sale matching.

Some historical sales rows can contain a normal/original option_id while their
product_id still points at a separate returned-item child product. The v0.9.217
matcher trusted the row option_id first, so those rows looked normal and no user
confirmation dialog appeared. This patch treats the product master row behind
product_id as an additional identity source and asks for a mapping whenever that
child option is neither a verified normal option nor an already-confirmed alias.
"""
from __future__ import annotations

from typing import Any

import shared_return_match_ui_v09217 as base


def _product_identity(core, db, product_id: int) -> tuple[str, str]:
    if not product_id:
        return "", ""
    try:
        with core._conn(db) as c:
            if not base._exists(c, "products"):
                return "", ""
            row = c.execute(
                "SELECT option_id,name FROM products WHERE id=?", (int(product_id),)
            ).fetchone()
        if not row:
            return "", ""
        return base._oid(row["option_id"]), str(row["name"] or "")
    except Exception:
        return "", ""


def _pick_unmatched_identity(
    core,
    db,
    product_id: int,
    row_option_id: Any,
    normals: set[str],
    aliases: set[str],
) -> tuple[str, str]:
    """Prefer an unresolved child option referenced by product_id.

    A row-level option_id may already have been normalized to the original option
    by an older importer. If product_id still points at a distinct child product,
    that child is what the user must confirm once and store in the shared alias map.
    """
    row_oid = base._oid(row_option_id)
    product_oid, product_name = _product_identity(core, db, product_id)

    if product_oid and product_oid not in normals and product_oid not in aliases:
        return product_oid, product_name
    if row_oid and row_oid not in normals and row_oid not in aliases:
        return row_oid, product_name
    return "", product_name


def period_unmatched(core, db, start, end) -> list[dict]:
    normals = base._normal_ids(core, db)
    aliases = base._alias_ids(core, db)
    with core._conn(db) as c:
        if not (base._exists(c, "imports") and base._exists(c, "sales_stats")):
            return []
        scols = base._cols(c, "sales_stats")
        if "import_id" not in scols:
            return []
        imports = c.execute(
            """SELECT id FROM imports WHERE data_type='sales_stats'
               AND period_start>=? AND period_end<=?""",
            (str(start), str(end)),
        ).fetchall()
        if not imports:
            return []
        ids = [int(r["id"]) for r in imports]
        marks = ",".join("?" for _ in ids)
        pid_expr = "product_id" if "product_id" in scols else "0 AS product_id"
        oid_expr = "option_id" if "option_id" in scols else "'' AS option_id"
        qty_expr = "SUM(COALESCE(sales_qty,0))" if "sales_qty" in scols else (
            "SUM(COALESCE(net_qty,0))" if "net_qty" in scols else "0"
        )
        rows = c.execute(
            f"SELECT {pid_expr},{oid_expr},{qty_expr} qty FROM sales_stats "
            f"WHERE import_id IN ({marks}) GROUP BY product_id,option_id",
            ids,
        ).fetchall()

    out: dict[str, dict] = {}
    for r in rows:
        try:
            pid = int(r["product_id"] or 0)
        except Exception:
            pid = 0
        row_oid = r["option_id"] if "option_id" in r.keys() else ""
        oid, pname = _pick_unmatched_identity(core, db, pid, row_oid, normals, aliases)
        if not oid:
            continue
        out[oid] = {
            "option_id": oid,
            "product_id": pid,
            "name": pname or f"옵션ID {oid}",
            "qty": float(r["qty"] or 0),
        }
    return list(out.values())


def frame_unmatched(core, db, frame) -> list[dict]:
    if frame is None or getattr(frame, "empty", True):
        return []
    normals = base._normal_ids(core, db)
    aliases = base._alias_ids(core, db)
    oidcol = next((c for c in ("옵션ID", "쿠팡 옵션ID", "option_id") if c in frame.columns), None)
    pidcol = "product_id" if "product_id" in frame.columns else None
    if oidcol is None and pidcol is None:
        return []

    out: dict[str, dict] = {}
    for _, row in frame.iterrows():
        pid = 0
        if pidcol:
            try:
                pid = int(float(row.get(pidcol) or 0))
            except Exception:
                pid = 0
        row_oid = row.get(oidcol) if oidcol else ""
        oid, pname = _pick_unmatched_identity(core, db, pid, row_oid, normals, aliases)
        if not oid:
            continue
        name = str(row.get("상품명") or row.get("아이템") or pname or f"옵션ID {oid}")
        qty = row.get("판매수량") if "판매수량" in frame.columns else None
        try:
            qty = float(qty) if qty is not None else None
        except Exception:
            qty = None
        out[oid] = {"option_id": oid, "product_id": pid, "name": name, "qty": qty}
    return list(out.values())


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)

    # Make sure the shared product resolver is active before analytical screens
    # aggregate rows. It collapses confirmed return aliases and duplicate normal
    # product rows to one representative original product.
    try:
        import shared_product_identity_v09215 as shared
        shared_result = shared.apply(core, db)
    except Exception as exc:
        shared_result = {"ok": False, "error": str(exc)}

    base.period_unmatched = period_unmatched
    base.frame_unmatched = frame_unmatched
    matcher_result = base.apply(core, db)

    # Re-export explicit hooks so screens can force the check before aggregation.
    core.rg_ensure_return_mappings_for_period = lambda start, end, source="ERP", db_path=None: base.ensure_period_mappings(
        core, db_path or core.DEFAULT_DB, start, end, source
    )
    core.rg_ensure_return_mappings_for_frame = lambda frame, source="ERP", db_path=None: base.ensure_frame_mappings(
        core, db_path or core.DEFAULT_DB, frame, source
    )
    return {
        "ok": True,
        "fix": "product_id_child_identity_v09219",
        "shared_identity": shared_result,
        "matcher": matcher_result,
    }
