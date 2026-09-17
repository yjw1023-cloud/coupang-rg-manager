"""v0.9.221 authoritative option-ID return matcher.

Rules:
- The Coupang normal-product registry is the only normal-product authority.
- A sales option ID found in that registry is a normal sale.
- A sales option ID already present in return_discount_aliases is a confirmed return resale.
- Any other sales option ID must be confirmed by the user and mapped to one normal master product.

This intentionally removes the invalid v0.9.220 assumption that a return can be
identified merely because product_id differs while the option ID is the same.
"""
from __future__ import annotations

from typing import Any

import shared_return_match_ui_v09217 as base
import shared_product_identity_v09215 as shared


def _ensure_authoritative_master(core, db):
    """Seed the 2026-09-17 authoritative 132 normal option IDs into the live DB."""
    import canonical_product_rules_v09214 as rules
    rules._seed_registry(core, db)
    rules._sync_visibility(core, db)


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
        oid, pname = base._row_identity(
            core, db, pid, r["option_id"] if "option_id" in r.keys() else ""
        )
        if not oid or oid in normals or oid in aliases:
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
        oid, pname = base._row_identity(core, db, pid, row.get(oidcol) if oidcol else "")
        if not oid or oid in normals or oid in aliases:
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

    # First fix the live master itself. The uploaded current DB still had only
    # the old 68-option registry, so matching could not be trustworthy until this
    # authoritative seed runs.
    _ensure_authoritative_master(core, db)

    # v0.9.220 introduced product-id aliases from an invalid assumption. They are
    # no longer part of product identity and any rows created by that version are
    # discarded.
    try:
        with core._conn(db) as c:
            if base._exists(c, "return_product_aliases"):
                c.execute("DELETE FROM return_product_aliases")
    except Exception:
        pass

    # Confirmed return aliases are still resolved to their original master product
    # before analytical aggregation.
    try:
        shared_result = shared.apply(core, db)
    except Exception as exc:
        shared_result = {"ok": False, "error": str(exc)}

    # Existing screen wrappers call these module globals dynamically, so replacing
    # them here immediately changes Organic Sales, Sales Analysis and provisional P&L.
    base.period_unmatched = period_unmatched
    base.frame_unmatched = frame_unmatched
    matcher_result = base.apply(core, db)

    core.rg_ensure_return_mappings_for_period = lambda start, end, source="ERP", db_path=None: base.ensure_period_mappings(
        core, db_path or core.DEFAULT_DB, start, end, source
    )
    core.rg_ensure_return_mappings_for_frame = lambda frame, source="ERP", db_path=None: base.ensure_frame_mappings(
        core, db_path or core.DEFAULT_DB, frame, source
    )
    return {
        "ok": True,
        "fix": "authoritative_option_id_master_v09221",
        "normal_option_count": len(base._normal_ids(core, db)),
        "shared_identity": shared_result,
        "matcher": matcher_result,
    }
