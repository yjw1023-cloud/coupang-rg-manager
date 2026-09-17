"""v0.9.215 single shared product identity for all ERP menus.

The authoritative source for returned-item resale -> original product mapping is
`return_discount_aliases`. Manual mappings confirmed by the user in the
unmatched-sales dialog are already written to that table with match_method
`manual_user`; every ERP view must read the same table instead of maintaining a
menu-local mapping.
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
    try:
        rows = con.execute(
            "SELECT vendor_item_id FROM coupang_normal_option_registry"
        ).fetchall()
    except Exception:
        return set()
    return {_oid(r["vendor_item_id"]) for r in rows if _oid(r["vendor_item_id"])}


def alias_maps(core, db):
    """Return the ERP-wide child-option/child-product -> original-product maps."""
    core.init_db(db)
    with core._conn(db) as con:
        if not _exists(con, "return_discount_aliases"):
            return {}, {}
        normal_ids = _verified_normal_ids(con)
        rows = con.execute(
            """SELECT a.discount_option_id,a.parent_product_id,
                      p.option_id AS parent_option_id
               FROM return_discount_aliases a
               LEFT JOIN products p ON p.id=a.parent_product_id"""
        ).fetchall()

        by_oid = {}
        for r in rows:
            child_oid = _oid(r["discount_option_id"])
            if not child_oid or child_oid in normal_ids:
                continue
            try:
                parent_pid = int(r["parent_product_id"])
            except Exception:
                continue
            by_oid[child_oid] = (parent_pid, _oid(r["parent_option_id"]))

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


def resolve(core, db, product_id=None, option_id=None):
    """Resolve any sale/ad/order row to its one canonical original product."""
    by_oid, by_pid = alias_maps(core, db)
    oid = _oid(option_id)
    try:
        pid = int(float(product_id or 0))
    except Exception:
        pid = 0
    target = by_oid.get(oid) or by_pid.get(pid)
    if target:
        parent_pid, parent_oid = target
        return {
            "product_id": int(parent_pid),
            "option_id": _oid(parent_oid),
            "is_return_alias": True,
            "source_option_id": oid,
        }
    return {
        "product_id": pid,
        "option_id": oid,
        "is_return_alias": False,
        "source_option_id": oid,
    }


def _hide_alias_products(core, db) -> int:
    """Keep technical return child rows for history but never expose as normal SKUs."""
    by_oid, _ = alias_maps(core, db)
    if not by_oid:
        return 0
    now = core.now_iso()
    hidden = 0
    with core._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS system_hidden_products(
                   product_id INTEGER PRIMARY KEY,
                   reason TEXT NOT NULL,
                   hidden_at TEXT NOT NULL
               )"""
        )
        marks = ",".join("?" for _ in by_oid)
        rows = con.execute(
            f"SELECT id,option_id FROM products WHERE CAST(option_id AS TEXT) IN ({marks})",
            tuple(sorted(by_oid)),
        ).fetchall()
        for r in rows:
            pid = int(r["id"])
            con.execute(
                """INSERT INTO system_hidden_products(product_id,reason,hidden_at)
                   VALUES(?,?,?)
                   ON CONFLICT(product_id) DO UPDATE SET
                     reason=excluded.reason,hidden_at=excluded.hidden_at""",
                (pid, "return_alias_shared_v09215", now),
            )
            con.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
            hidden += 1
    return hidden


def _patch_v214(core, db):
    """Make every v0.9.214 analytical patch read this one live alias map."""
    try:
        rules = importlib.import_module("canonical_product_rules_v09214")
        rules._alias_maps = alias_maps
        # Re-run lightweight view patch installers. Existing wrappers are idempotent.
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

    hidden = _hide_alias_products(core, db)
    patched = _patch_v214(core, db)

    # These are the only public product-identity entry points other modules
    # should need. They always query the shared DB table, so a manual decision
    # made in 잠정실적 is immediately visible to every menu on the next query.
    core.rg_return_alias_maps = lambda db_path=None: alias_maps(
        core, db_path or core.DEFAULT_DB
    )
    core.rg_resolve_product_identity = lambda product_id=None, option_id=None, db_path=None: resolve(
        core, db_path or core.DEFAULT_DB, product_id, option_id
    )
    core.rg_is_return_alias = lambda option_id, db_path=None: _oid(option_id) in alias_maps(
        core, db_path or core.DEFAULT_DB
    )[0]

    return {
        "ok": True,
        "source": "return_discount_aliases",
        "hidden_return_children": hidden,
        "v09214_patched": patched,
    }
