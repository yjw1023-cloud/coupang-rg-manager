"""v0.9.222 ERP-wide product identity.

Business rule:
- verified master option ID => normal product
- non-master option ID confirmed by the user in return_discount_aliases => return resale
- product_id never decides whether a sale is a return
"""
from __future__ import annotations

import importlib
from typing import Any


def _oid(value: Any) -> str:
    if value is None:
        return ""
    try:
        x = float(value)
        if abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(value).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _verified_normal_ids(con) -> set[str]:
    if not _exists(con, "coupang_normal_option_registry"):
        return set()
    rows = con.execute("SELECT vendor_item_id FROM coupang_normal_option_registry").fetchall()
    return {_oid(r["vendor_item_id"]) for r in rows if _oid(r["vendor_item_id"])}


def alias_maps(core, db):
    """Confirmed return mappings. Only explicit user confirmation is authoritative."""
    core.init_db(db)
    with core._conn(db) as con:
        normal_ids = _verified_normal_ids(con)
        if not _exists(con, "return_discount_aliases"):
            return {}, {}
        cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(return_discount_aliases)").fetchall()}
        method_expr = "a.match_method" if "match_method" in cols else "'' AS match_method"
        rows = con.execute(
            f"""SELECT a.discount_option_id,a.parent_product_id,{method_expr},
                       p.option_id AS parent_option_id
                FROM return_discount_aliases a
                LEFT JOIN products p ON p.id=a.parent_product_id"""
        ).fetchall()
        by_oid = {}
        for r in rows:
            oid = _oid(r["discount_option_id"])
            method = str(r["match_method"] or "")
            if not oid or oid in normal_ids or not method.startswith("manual"):
                continue
            try:
                parent_pid = int(r["parent_product_id"])
            except Exception:
                continue
            by_oid[oid] = (parent_pid, _oid(r["parent_option_id"]))

        by_pid = {}
        if by_oid and _exists(con, "products"):
            marks = ",".join("?" for _ in by_oid)
            children = con.execute(
                f"SELECT id,option_id FROM products WHERE CAST(option_id AS TEXT) IN ({marks})",
                tuple(sorted(by_oid)),
            ).fetchall()
            for r in children:
                oid = _oid(r["option_id"])
                if oid in by_oid:
                    by_pid[int(r["id"])] = by_oid[oid]
    return by_oid, by_pid


def normal_maps(core, db):
    """Collapse duplicate DB rows for one verified normal option to one representative."""
    core.init_db(db)
    with core._conn(db) as con:
        normal_ids = _verified_normal_ids(con)
        if not normal_ids or not _exists(con, "products"):
            return {}, {}
        marks = ",".join("?" for _ in normal_ids)
        rows = con.execute(
            f"""SELECT id,item_code,option_id,name,item_type,unit_cost,active
                FROM products WHERE CAST(option_id AS TEXT) IN ({marks})""",
            tuple(sorted(normal_ids)),
        ).fetchall()

    grouped = {}
    for r in rows:
        oid = _oid(r["option_id"])
        if oid:
            grouped.setdefault(oid, []).append(dict(r))

    def score(p):
        oid = _oid(p.get("option_id"))
        code = _oid(p.get("item_code"))
        return (
            1 if str(p.get("item_type") or "") == "finished" else 0,
            1 if code != oid else 0,
            1 if float(p.get("unit_cost") or 0) > 0 else 0,
            1 if int(p.get("active") or 0) else 0,
            -int(p.get("id") or 0),
        )

    by_oid, by_pid = {}, {}
    for oid, products in grouped.items():
        rep = max(products, key=score)
        target = (int(rep["id"]), oid)
        by_oid[oid] = target
        for p in products:
            by_pid[int(p["id"])] = target
    return by_oid, by_pid


def canonical_maps(core, db):
    normal_oid, normal_pid = normal_maps(core, db)
    return_oid, return_pid = alias_maps(core, db)
    by_oid = dict(normal_oid)
    by_oid.update(return_oid)
    by_pid = dict(normal_pid)
    by_pid.update(return_pid)
    return by_oid, by_pid


def resolve(core, db, product_id=None, option_id=None):
    return_oid, return_pid = alias_maps(core, db)
    normal_oid, normal_pid = normal_maps(core, db)
    oid = _oid(option_id)
    try:
        pid = int(float(product_id or 0))
    except Exception:
        pid = 0

    # Option ID is the business discriminator. product_id is only a fallback when
    # the source row genuinely has no option ID.
    if oid:
        if oid in normal_oid:
            parent_pid, parent_oid = normal_oid[oid]
            is_return = False
        elif oid in return_oid:
            parent_pid, parent_oid = return_oid[oid]
            is_return = True
        else:
            return {
                "product_id": pid,
                "option_id": oid,
                "is_return_alias": False,
                "source_option_id": oid,
                "source_product_id": pid,
            }
    else:
        target = normal_pid.get(pid)
        is_return = False
        if not target:
            target = return_pid.get(pid)
            is_return = bool(target)
        if not target:
            return {
                "product_id": pid,
                "option_id": oid,
                "is_return_alias": False,
                "source_option_id": oid,
                "source_product_id": pid,
            }
        parent_pid, parent_oid = target

    return {
        "product_id": int(parent_pid),
        "option_id": _oid(parent_oid),
        "is_return_alias": is_return,
        "source_option_id": oid,
        "source_product_id": pid,
    }


def _hide_noncanonical_products(core, db) -> int:
    return_oid, _ = alias_maps(core, db)
    normal_oid, normal_pid = normal_maps(core, db)
    now = core.now_iso()
    hidden = 0
    with core._conn(db) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS system_hidden_products(
          product_id INTEGER PRIMARY KEY,reason TEXT NOT NULL,hidden_at TEXT NOT NULL)""")
        rows = con.execute("SELECT id,option_id FROM products").fetchall() if _exists(con, "products") else []
        for r in rows:
            pid = int(r["id"])
            oid = _oid(r["option_id"])
            reason = None
            if oid in return_oid:
                reason = "return_alias_manual_v09222"
            elif oid in normal_oid and normal_pid.get(pid, (pid, oid))[0] != pid:
                reason = "duplicate_normal_product_v09222"
            if reason:
                con.execute(
                    """INSERT INTO system_hidden_products(product_id,reason,hidden_at)
                       VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET
                       reason=excluded.reason,hidden_at=excluded.hidden_at""",
                    (pid, reason, now),
                )
                con.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
                hidden += 1
    return hidden


def _patch_v214(core, db):
    try:
        rules = importlib.import_module("canonical_product_rules_v09214")
        rules._alias_maps = lambda core_obj, db_path: canonical_maps(core_obj, db_path)
        rules._patch_return_hide(core)
        rules._patch_sales_analysis()
        rules._patch_organic()
        return True
    except Exception:
        return False


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)
    hidden = _hide_noncanonical_products(core, db)
    patched = _patch_v214(core, db)
    core.rg_return_alias_maps = lambda db_path=None: alias_maps(core, db_path or core.DEFAULT_DB)
    core.rg_canonical_product_maps = lambda db_path=None: canonical_maps(core, db_path or core.DEFAULT_DB)
    core.rg_resolve_product_identity = lambda product_id=None, option_id=None, db_path=None: resolve(
        core, db_path or core.DEFAULT_DB, product_id, option_id
    )
    core.rg_is_return_alias = lambda option_id, db_path=None: _oid(option_id) in alias_maps(
        core, db_path or core.DEFAULT_DB
    )[0]
    return {
        "ok": True,
        "source": "normal_registry+manual_return_option_aliases",
        "hidden_noncanonical_products": hidden,
        "v09214_patched": patched,
        "product_id_return_matching": False,
    }
