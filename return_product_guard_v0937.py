"""RG Manager v0.9.37 return-generated product guard.

Coupang can assign temporary option IDs to returned-item discount resale rows.
The legacy sales-stat importer may create those unknown option IDs as normal
`products` rows so sales history can keep a foreign-key target. Those rows are
historical/internal aliases, not managed SKUs.

This guard:
- archives child products already proven by return_discount_aliases/sales;
- detects conservative sales-only legacy leftovers and excludes them from BOM
  finished-product recommendations;
- never deletes products or historical sales/inventory records;
- keeps the oldest/original managed product selectable.
"""
from __future__ import annotations

import math
import re
import sqlite3
from typing import Any

_APPLIED = False


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _num(v: Any) -> float:
    try:
        x = float(v)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def _cp_style(item_code: Any, option_id: Any) -> bool:
    oid = _oid(option_id)
    code = str(item_code or "").strip()
    if not oid:
        return False
    return bool(re.fullmatch(rf"(?:CP-)?{re.escape(oid)}", code, flags=re.I))


_PACK_RE = re.compile(
    r"(?<![0-9])([0-9]+)\s*(?:개입|개|p|pcs?|세트|set)(?![a-z0-9가-힣])",
    flags=re.I,
)


def _pack_qty(name: Any) -> int | None:
    vals = []
    for m in _PACK_RE.finditer(str(name or "").lower()):
        try:
            vals.append(int(m.group(1)))
        except Exception:
            pass
    return vals[0] if vals else None


def _name_tokens(name: Any) -> tuple[str, ...]:
    s = str(name or "").lower()
    s = _PACK_RE.sub(" ", s)
    tokens = re.findall(r"[a-z가-힣]+", s)
    stop = {"개", "개입", "p", "pc", "pcs", "세트", "set"}
    return tuple(t for t in tokens if t not in stop and len(t) >= 1)


def _strong_child_to_parent_name(child_name: Any, parent_name: Any) -> bool:
    """Conservative legacy-return match.

    A return option often keeps a shorter form of the original option name.
    We only accept the child when:
    - both names have at least three meaningful tokens;
    - if both expose a package quantity, the quantity is the same;
    - every child token exists in the older parent name;
    - the parent adds at most two descriptive tokens.
    """
    cq, pq = _pack_qty(child_name), _pack_qty(parent_name)
    if cq is not None and pq is not None and cq != pq:
        return False

    child = set(_name_tokens(child_name))
    parent = set(_name_tokens(parent_name))
    if len(child) < 3 or len(parent) < 3:
        return False
    if not child.issubset(parent):
        return False
    return len(parent - child) <= 2


def _load_guard_state(core_module):
    db = core_module.DEFAULT_DB
    core_module.init_db(db)

    with core_module._conn(db) as con:
        products = [
            dict(r)
            for r in con.execute(
                """SELECT id,item_code,option_id,name,item_type,unit_cost,active
                   FROM products ORDER BY id"""
            ).fetchall()
        ]

        known = set()
        if _table_exists(con, "return_discount_sales"):
            for r in con.execute(
                """SELECT DISTINCT child_product_id
                   FROM return_discount_sales
                   WHERE child_product_id IS NOT NULL"""
            ).fetchall():
                known.add(int(r["child_product_id"]))

        if _table_exists(con, "return_discount_aliases"):
            for r in con.execute(
                """SELECT p.id
                   FROM products p
                   JOIN return_discount_aliases a
                     ON CAST(p.option_id AS TEXT)=CAST(a.discount_option_id AS TEXT)
                   WHERE p.id<>a.parent_product_id"""
            ).fetchall():
                known.add(int(r["id"]))

        # Proven return aliases are not managed SKUs. Preserve the row/history,
        # but remove it from all active-product based screens.
        if known:
            marks = ",".join("?" for _ in known)
            con.execute(
                f"UPDATE products SET active=0,updated_at=? "
                f"WHERE id IN ({marks}) AND COALESCE(active,1)<>0",
                (core_module.now_iso(), *sorted(known)),
            )

        sales_ids = set()
        if _table_exists(con, "sales_stats"):
            for r in con.execute(
                "SELECT DISTINCT product_id FROM sales_stats WHERE product_id IS NOT NULL"
            ).fetchall():
                sales_ids.add(int(r["product_id"]))

        protected = set()
        if _table_exists(con, "bom_items"):
            for r in con.execute(
                "SELECT parent_product_id,component_product_id FROM bom_items"
            ).fetchall():
                protected.add(int(r["parent_product_id"]))
                protected.add(int(r["component_product_id"]))

        if _table_exists(con, "production_orders"):
            for r in con.execute(
                "SELECT DISTINCT parent_product_id FROM production_orders "
                "WHERE parent_product_id IS NOT NULL"
            ).fetchall():
                protected.add(int(r["parent_product_id"]))

        if _table_exists(con, "inventory_txns"):
            for r in con.execute(
                """SELECT DISTINCT product_id
                   FROM inventory_txns
                   WHERE product_id IS NOT NULL
                     AND COALESCE(txn_type,'') NOT IN ('판매차감','반품할인판매차감')"""
            ).fetchall():
                protected.add(int(r["product_id"]))

    by_id = {int(p["id"]): p for p in products}
    suspected = set()

    # Legacy leftovers may predate return_discount_aliases. Do not mutate them;
    # only keep them out of managed-BOM recommendations when the evidence is
    # conservative: zero-cost CP row, sales-only history, and exactly one older
    # strongly matching finished product.
    for child in products:
        cid = int(child["id"])
        if cid in known or cid in protected or cid not in sales_ids:
            continue
        if int(child.get("active") or 0) != 1:
            continue
        if str(child.get("item_type") or "").strip().lower() != "finished":
            continue
        if abs(_num(child.get("unit_cost"))) > 1e-12:
            continue
        if not _cp_style(child.get("item_code"), child.get("option_id")):
            continue

        parents = []
        for parent in products:
            pid = int(parent["id"])
            if pid >= cid or pid in known:
                continue
            if int(parent.get("active") or 0) != 1:
                continue
            if str(parent.get("item_type") or "").strip().lower() != "finished":
                continue
            if _strong_child_to_parent_name(child.get("name"), parent.get("name")):
                parents.append(pid)

        if len(parents) == 1:
            suspected.add(cid)

    return known, suspected, by_id


def apply() -> None:
    """Patch BOM finished-product recommendation filtering."""
    global _APPLIED
    if _APPLIED:
        return

    import bom_candidate_filter_v0927 as bom

    if getattr(bom, "_rg_return_product_guard_v0937_applied", False):
        _APPLIED = True
        return

    original_filter = bom.filter_options

    def filter_options(options, kind: str, core_module):
        values = original_filter(options, kind, core_module)
        if kind != "finished":
            return values

        try:
            known, suspected, _ = _load_guard_state(core_module)
            excluded = known | suspected
            if not excluded:
                return values

            maps = bom._load_products(core_module)
            out = []
            for value in list(values):
                pid = bom._candidate_pid(value, maps)
                if pid is None or int(pid) not in excluded:
                    out.append(value)
            return out
        except Exception:
            # Never break the BOM page because of a cleanup/diagnostic failure.
            return values

    bom.filter_options = filter_options
    bom._rg_return_product_guard_v0937_applied = True
    _APPLIED = True
