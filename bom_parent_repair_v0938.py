"""RG Manager v0.9.38 repair for BOMs attached to Coupang return-generated option IDs.

A historical sales-stat import could create a temporary Coupang return option as a
normal finished product. In some databases a current BOM was later attached to
that temporary product instead of the original managed option.

This module repairs only the *current* BOM link:
- historical sales / production / inventory rows are never rewritten;
- if a return child is explicitly linked to one parent, that parent is preferred;
- otherwise a conservative legacy heuristic is used only for a sales-only child
  with a BOM and one uniquely matching active normal product;
- repair runs only when the target parent currently has no BOM;
- ambiguous or conflicting cases are left untouched.
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
    return tuple(t for t in tokens if t not in stop)


def _strong_child_to_parent_name(child_name: Any, parent_name: Any) -> bool:
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


def _ensure_log_table(con: sqlite3.Connection) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS bom_change_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            bom_id INTEGER,
            parent_product_id INTEGER NOT NULL,
            component_product_id INTEGER NOT NULL,
            qty_per REAL NOT NULL,
            changed_at TEXT NOT NULL,
            note TEXT
        )"""
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS ix_bom_change_log_parent "
        "ON bom_change_log(parent_product_id, changed_at)"
    )


def _products(con: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(r)
        for r in con.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products ORDER BY id"""
        ).fetchall()
    ]


def _bom_parent_ids(con: sqlite3.Connection) -> set[int]:
    if not _table_exists(con, "bom_items"):
        return set()
    return {
        int(r["parent_product_id"])
        for r in con.execute(
            "SELECT DISTINCT parent_product_id FROM bom_items"
        ).fetchall()
    }


def _explicit_parent_map(con: sqlite3.Connection) -> dict[int, int]:
    out: dict[int, int] = {}

    if _table_exists(con, "return_discount_sales"):
        rows = con.execute(
            """SELECT child_product_id,parent_product_id
               FROM return_discount_sales
               WHERE child_product_id IS NOT NULL
                 AND parent_product_id IS NOT NULL"""
        ).fetchall()
        grouped: dict[int, set[int]] = {}
        for r in rows:
            grouped.setdefault(int(r["child_product_id"]), set()).add(
                int(r["parent_product_id"])
            )
        for child_id, parents in grouped.items():
            if len(parents) == 1:
                out[child_id] = next(iter(parents))

    if _table_exists(con, "return_discount_aliases"):
        rows = con.execute(
            """SELECT p.id child_product_id,a.parent_product_id
               FROM products p
               JOIN return_discount_aliases a
                 ON CAST(p.option_id AS TEXT)=CAST(a.discount_option_id AS TEXT)
               WHERE p.id<>a.parent_product_id"""
        ).fetchall()
        for r in rows:
            cid = int(r["child_product_id"])
            pid = int(r["parent_product_id"])
            if cid not in out or out[cid] == pid:
                out[cid] = pid
    return out


def _has_sales(con: sqlite3.Connection, product_id: int) -> bool:
    if not _table_exists(con, "sales_stats"):
        return False
    return con.execute(
        "SELECT 1 FROM sales_stats WHERE product_id=? LIMIT 1",
        (int(product_id),),
    ).fetchone() is not None


def _has_production(con: sqlite3.Connection, product_id: int) -> bool:
    if not _table_exists(con, "production_orders"):
        return False
    return con.execute(
        "SELECT 1 FROM production_orders WHERE parent_product_id=? LIMIT 1",
        (int(product_id),),
    ).fetchone() is not None


def _has_non_sales_inventory(con: sqlite3.Connection, product_id: int) -> bool:
    if not _table_exists(con, "inventory_txns"):
        return False
    return con.execute(
        """SELECT 1 FROM inventory_txns
           WHERE product_id=?
             AND COALESCE(txn_type,'') NOT IN ('판매차감','반품할인판매차감')
           LIMIT 1""",
        (int(product_id),),
    ).fetchone() is not None


def _legacy_parent_candidate(
    con: sqlite3.Connection,
    child: dict[str, Any],
    products: list[dict[str, Any]],
    bom_parents: set[int],
) -> int | None:
    cid = int(child["id"])
    if cid not in bom_parents:
        return None
    if str(child.get("item_type") or "").strip().lower() != "finished":
        return None
    if not _cp_style(child.get("item_code"), child.get("option_id")):
        return None
    if not _has_sales(con, cid):
        return None

    # A product that has already been produced or received/adjusted as a managed
    # SKU is not silently reclassified as a return alias.
    if _has_production(con, cid) or _has_non_sales_inventory(con, cid):
        return None

    candidates = []
    for parent in products:
        pid = int(parent["id"])
        if pid == cid or pid in bom_parents:
            continue
        if int(parent.get("active") or 0) != 1:
            continue
        if str(parent.get("item_type") or "").strip().lower() != "finished":
            continue
        if not _cp_style(parent.get("item_code"), parent.get("option_id")):
            continue
        if not _has_sales(con, pid):
            continue
        if _strong_child_to_parent_name(child.get("name"), parent.get("name")):
            candidates.append(pid)

    return candidates[0] if len(candidates) == 1 else None


def _upsert_alias_if_possible(
    con: sqlite3.Connection,
    child: dict[str, Any],
    parent_id: int,
    now: str,
) -> None:
    if not _table_exists(con, "return_discount_aliases"):
        return
    oid = _oid(child.get("option_id"))
    if not oid:
        return
    con.execute(
        """INSERT INTO return_discount_aliases
           (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(discount_option_id) DO UPDATE SET
             parent_product_id=excluded.parent_product_id,
             discount_name=excluded.discount_name,
             match_method=excluded.match_method,
             updated_at=excluded.updated_at""",
        (
            oid,
            int(parent_id),
            str(child.get("name") or ""),
            "repair_bom_parent_v0938",
            now,
            now,
        ),
    )


def repair_current_bom_links(core_module, db_path=None) -> dict[str, Any]:
    """Move wrongly attached current BOMs to their normal managed parent.

    Only bom_items.parent_product_id is changed. Historical production_orders,
    sales_stats and inventory_txns remain untouched.
    """
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    repaired = []
    skipped = []

    with core_module._conn(db) as con:
        if not _table_exists(con, "bom_items"):
            return {"repaired": repaired, "skipped": skipped}

        products = _products(con)
        by_id = {int(p["id"]): p for p in products}
        bom_parents = _bom_parent_ids(con)
        explicit = _explicit_parent_map(con)

        plans: list[tuple[int, int, str]] = []
        for child_id in sorted(bom_parents):
            child = by_id.get(child_id)
            if not child:
                continue

            target = explicit.get(child_id)
            method = "return_alias"
            if target is None:
                target = _legacy_parent_candidate(con, child, products, bom_parents)
                method = "legacy_sales_only_name"

            if target is None or target == child_id:
                continue
            parent = by_id.get(int(target))
            if not parent:
                skipped.append((child_id, target, "원상품 없음"))
                continue
            if int(parent.get("active") or 0) != 1:
                skipped.append((child_id, target, "원상품 보관상태"))
                continue
            if str(parent.get("item_type") or "").strip().lower() != "finished":
                skipped.append((child_id, target, "원상품 완제품 아님"))
                continue
            if int(target) in bom_parents:
                skipped.append((child_id, target, "원상품에 이미 BOM 있음"))
                continue

            plans.append((child_id, int(target), method))

        if not plans:
            return {"repaired": repaired, "skipped": skipped}

        _ensure_log_table(con)
        now = core_module.now_iso()

        for child_id, parent_id, method in plans:
            rows = con.execute(
                """SELECT id,component_product_id,qty_per
                   FROM bom_items WHERE parent_product_id=? ORDER BY id""",
                (child_id,),
            ).fetchall()
            if not rows:
                continue

            # Re-check inside the same transaction because another rerun may have
            # repaired the row while this run was waiting for the DB lock.
            if con.execute(
                "SELECT 1 FROM bom_items WHERE parent_product_id=? LIMIT 1",
                (parent_id,),
            ).fetchone():
                skipped.append((child_id, parent_id, "원상품 BOM 동시 생성"))
                continue

            for row in rows:
                con.execute(
                    """INSERT INTO bom_change_log
                       (action,bom_id,parent_product_id,component_product_id,
                        qty_per,changed_at,note)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        "MIGRATE_RETURN_ALIAS_PARENT",
                        int(row["id"]),
                        child_id,
                        int(row["component_product_id"]),
                        float(row["qty_per"] or 0),
                        now,
                        f"반품 파생상품 BOM을 정상 원상품 product_id={parent_id}로 이동 ({method})",
                    ),
                )

            con.execute(
                "UPDATE bom_items SET parent_product_id=? WHERE parent_product_id=?",
                (parent_id, child_id),
            )
            con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=?",
                (now, child_id),
            )

            child = by_id.get(child_id)
            if method == "legacy_sales_only_name" and child is not None:
                _upsert_alias_if_possible(con, child, parent_id, now)

            repaired.append(
                {
                    "child_product_id": child_id,
                    "parent_product_id": parent_id,
                    "components": len(rows),
                    "method": method,
                }
            )

    return {"repaired": repaired, "skipped": skipped}


def apply(core_module, production_batch_module) -> None:
    """Repair existing links and make batch validation self-healing."""
    global _APPLIED
    if _APPLIED or getattr(
        production_batch_module, "_rg_bom_parent_repair_v0938_applied", False
    ):
        return

    # Startup repair makes the normal BOM screen consistent immediately.
    try:
        repair_current_bom_links(core_module)
    except Exception:
        # Batch validation retries and will surface a real BOM error if repair
        # cannot be applied. Startup must not prevent the ERP from opening.
        pass

    original_validate = production_batch_module.validate_rows
    original_execute = production_batch_module.execute_batch

    def validate_rows(core, parsed_rows, db_path=None):
        repair_current_bom_links(core, db_path)
        return original_validate(core, parsed_rows, db_path)

    def execute_batch(core, parsed, file_name, production_date, db_path=None):
        repair_current_bom_links(core, db_path)
        return original_execute(core, parsed, file_name, production_date, db_path)

    production_batch_module.validate_rows = validate_rows
    production_batch_module.execute_batch = execute_batch
    production_batch_module._rg_bom_parent_repair_v0938_applied = True
    _APPLIED = True
