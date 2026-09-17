"""v0.9.220 ERP-wide shared product identity.

Returned-item mappings can be keyed by return option ID or, when historical rows
reuse the original option ID, by the returned child product_id. Verified normal
option IDs live in coupang_normal_option_registry. Confirmed mappings are resolved
to one original product before analytical grouping.
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


def _ensure_product_alias_schema(core, db):
    with core._conn(db) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS return_product_aliases(
          child_product_id INTEGER PRIMARY KEY,
          parent_product_id INTEGER NOT NULL,
          child_option_id TEXT,
          child_name TEXT,
          match_method TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL)""")


def _verified_normal_ids(con) -> set[str]:
    if not _exists(con, "coupang_normal_option_registry"):
        return set()
    try:
        rows = con.execute("SELECT vendor_item_id FROM coupang_normal_option_registry").fetchall()
    except Exception:
        return set()
    return {_oid(r["vendor_item_id"]) for r in rows if _oid(r["vendor_item_id"])}


def alias_maps(core, db):
    """Return confirmed return maps by option_id and by exact child product_id."""
    core.init_db(db)
    _ensure_product_alias_schema(core, db)
    with core._conn(db) as con:
        normal_ids = _verified_normal_ids(con)
        by_oid = {}
        by_pid = {}

        if _exists(con, "return_discount_aliases"):
            rows = con.execute(
                """SELECT a.discount_option_id,a.parent_product_id,p.option_id AS parent_option_id
                   FROM return_discount_aliases a LEFT JOIN products p ON p.id=a.parent_product_id"""
            ).fetchall()
            for r in rows:
                child_oid = _oid(r["discount_option_id"])
                if not child_oid or child_oid in normal_ids:
                    continue
                try:
                    parent_pid = int(r["parent_product_id"])
                except Exception:
                    continue
                by_oid[child_oid] = (parent_pid, _oid(r["parent_option_id"]))
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

        # Exact child-product aliases are authoritative. They are required when a
        # return child reused the original/normal option ID, where option-ID aliases
        # cannot distinguish the two product rows.
        rows = con.execute(
            """SELECT a.child_product_id,a.parent_product_id,p.option_id AS parent_option_id
               FROM return_product_aliases a LEFT JOIN products p ON p.id=a.parent_product_id"""
        ).fetchall()
        for r in rows:
            try:
                child_pid = int(r["child_product_id"])
                parent_pid = int(r["parent_product_id"])
            except Exception:
                continue
            by_pid[child_pid] = (parent_pid, _oid(r["parent_option_id"]))
    return by_oid, by_pid


def normal_maps(core, db):
    """Map verified normal option rows to one representative original product."""
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
    for oid, ps in grouped.items():
        rep = max(ps, key=score)
        target = (int(rep["id"]), oid)
        by_oid[oid] = target
        for p in ps:
            by_pid[int(p["id"])] = target
    return by_oid, by_pid


def canonical_maps(core, db):
    """One resolver map used by analytical views before aggregation."""
    ret_oid, ret_pid = alias_maps(core, db)
    normal_oid, normal_pid = normal_maps(core, db)
    by_oid = dict(normal_oid)
    by_oid.update(ret_oid)
    by_pid = dict(normal_pid)
    by_pid.update(ret_pid)  # confirmed product-id return mapping wins
    return by_oid, by_pid


def resolve(core, db, product_id=None, option_id=None):
    ret_oid, ret_pid = alias_maps(core, db)
    norm_oid, norm_pid = normal_maps(core, db)
    oid = _oid(option_id)
    try:
        pid = int(float(product_id or 0))
    except Exception:
        pid = 0

    # Exact child product mapping must win even if that child reused a normal
    # option_id. This is the case that v0.9.219 could not represent.
    target = ret_pid.get(pid)
    is_return = bool(target)
    if not target:
        target = ret_oid.get(oid)
        is_return = bool(target)
    if not target:
        target = norm_pid.get(pid) or norm_oid.get(oid)
    if target:
        parent_pid, parent_oid = target
        return {
            "product_id": int(parent_pid),
            "option_id": _oid(parent_oid),
            "is_return_alias": is_return,
            "source_option_id": oid,
            "source_product_id": pid,
        }
    return {
        "product_id": pid,
        "option_id": oid,
        "is_return_alias": False,
        "source_option_id": oid,
        "source_product_id": pid,
    }


def _hide_noncanonical_products(core, db) -> int:
    ret_oid, ret_pid = alias_maps(core, db)
    norm_oid, norm_pid = normal_maps(core, db)
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
            if pid in ret_pid or oid in ret_oid:
                reason = "return_alias_shared_v09220"
            elif oid in norm_oid and norm_pid.get(pid, (pid, oid))[0] != pid:
                reason = "duplicate_normal_product_v09220"
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
        rules._alias_maps = canonical_maps
        try:
            rules._patch_return_hide(core)
        except Exception:
            pass
        try:
            rules._patch_sales_analysis()
        except Exception:
            pass
        try:
            rules._patch_organic()
        except Exception:
            pass
        return True
    except Exception:
        return False


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)
    _ensure_product_alias_schema(core, db)
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
        "source": "normal_registry+return_discount_aliases+return_product_aliases",
        "hidden_noncanonical_products": hidden,
        "v09214_patched": patched,
    }
