"""v0.9.171 repair rubber-glove BOMs from durable purchase source mappings.

This intentionally does NOT guess JDS codes. The user's purchase import already
stores source_name/source_detail -> product_id in purchase_source_product_map,
and committed purchase_lines may carry the same source fields. We resolve S/M/L
from those durable facts, require exactly one distinct raw product per size, then
replace only the three glove finished-product BOMs with component x5.

No inventory transaction is created, changed, moved, consumed, or adjusted.
"""
from __future__ import annotations

import re

TARGETS = {
    "S": ("96012086788", "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 소(S)"),
    "M": ("96012086789", "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 중(M)"),
    "L": ("96012086790", "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 대(L)"),
}
QTY_PER = 5.0
RULE = "v0.9.171-glove-bom-from-purchase-source-map"


def _text(v):
    return str(v or "").strip()


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(con, table):
    try:
        return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _size_match(text, size):
    s = _text(text).upper()
    return bool(re.search(rf"(?<![A-Z]){re.escape(size.upper())}(?![A-Z])", s))


def _source_is_glove(source_name, source_detail):
    return "고무장갑" in _text(source_name) or "고무장갑" in _text(source_detail)


def _collect_evidence(con, size):
    """Return product_id -> evidence rows for the requested size."""
    found = {}

    if _table_exists(con, "purchase_source_product_map"):
        cols = _columns(con, "purchase_source_product_map")
        if {"product_id", "source_name", "source_detail"}.issubset(cols):
            rows = con.execute(
                """SELECT m.product_id,m.source_name,m.source_detail,
                          p.item_code,p.name,p.option_id,p.active
                   FROM purchase_source_product_map m
                   JOIN products p ON p.id=m.product_id
                   WHERE p.active=1 AND p.option_id IS NULL"""
            ).fetchall()
            for r in rows:
                sn, sd = _text(r["source_name"]), _text(r["source_detail"])
                if _source_is_glove(sn, sd) and (_size_match(sd, size) or _size_match(sn, size)):
                    found.setdefault(int(r["product_id"]), []).append(
                        {"source": "map", "source_name": sn, "source_detail": sd}
                    )

    if _table_exists(con, "purchase_lines"):
        cols = _columns(con, "purchase_lines")
        if {"product_id", "source_name", "source_detail"}.issubset(cols):
            rows = con.execute(
                """SELECT l.product_id,l.source_name,l.source_detail,
                          p.item_code,p.name,p.option_id,p.active
                   FROM purchase_lines l
                   JOIN products p ON p.id=l.product_id
                   WHERE l.product_id IS NOT NULL
                     AND p.active=1 AND p.option_id IS NULL"""
            ).fetchall()
            for r in rows:
                sn, sd = _text(r["source_name"]), _text(r["source_detail"])
                if _source_is_glove(sn, sd) and (_size_match(sd, size) or _size_match(sn, size)):
                    found.setdefault(int(r["product_id"]), []).append(
                        {"source": "purchase_line", "source_name": sn, "source_detail": sd}
                    )
    return found


def _parent(con, option_id):
    rows = con.execute(
        """SELECT id,item_code,option_id,name FROM products
           WHERE CAST(option_id AS TEXT)=? AND active=1 ORDER BY id""",
        (str(option_id),),
    ).fetchall()
    return rows[0] if len(rows) == 1 else None


def _canonical_source(evidence):
    # Prefer the durable source map over purchase-line fallback.
    ordered = sorted(evidence, key=lambda x: 0 if x["source"] == "map" else 1)
    for e in ordered:
        if e["source_name"] and e["source_detail"]:
            return e["source_name"], e["source_detail"]
    return "", ""


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)

    with core._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS bom_items(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   parent_product_id INTEGER NOT NULL,
                   component_product_id INTEGER NOT NULL,
                   qty_per REAL NOT NULL,
                   UNIQUE(parent_product_id,component_product_id)
               )"""
        )

        resolved = {}
        diagnostics = []
        for size in ("S", "M", "L"):
            option_id, finished_name = TARGETS[size]
            parent = _parent(con, option_id)
            if parent is None:
                diagnostics.append({"size": size, "status": "parent_not_unique", "option_id": option_id})
                continue

            evidence = _collect_evidence(con, size)
            if len(evidence) != 1:
                diagnostics.append({
                    "size": size,
                    "status": "source_product_not_unique",
                    "candidate_product_ids": sorted(evidence.keys()),
                    "option_id": option_id,
                })
                continue

            component_id = next(iter(evidence.keys()))
            component = con.execute(
                "SELECT id,item_code,name,unit_cost FROM products WHERE id=? AND active=1 AND option_id IS NULL",
                (component_id,),
            ).fetchone()
            if component is None:
                diagnostics.append({"size": size, "status": "component_missing", "product_id": component_id})
                continue

            source_name, source_detail = _canonical_source(evidence[component_id])
            resolved[size] = {
                "parent": parent,
                "component": component,
                "source_name": source_name,
                "source_detail": source_detail,
                "finished_name": finished_name,
                "option_id": option_id,
            }

        if len(resolved) != 3:
            return {
                "ok": False,
                "status": "not_all_three_resolved",
                "diagnostics": diagnostics,
                "inventory_changed": False,
                "bom_changed": False,
            }

        parent_ids = [int(resolved[s]["parent"]["id"]) for s in ("S", "M", "L")]
        component_ids = [int(resolved[s]["component"]["id"]) for s in ("S", "M", "L")]
        if len(set(parent_ids)) != 3 or len(set(component_ids)) != 3:
            return {
                "ok": False,
                "status": "resolved_ids_not_distinct",
                "parents": parent_ids,
                "components": component_ids,
                "inventory_changed": False,
                "bom_changed": False,
            }

        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            pass

        try:
            changes = []
            for size in ("S", "M", "L"):
                r = resolved[size]
                parent_id = int(r["parent"]["id"])
                component_id = int(r["component"]["id"])

                # Remove the wrong v0.9.170 BOM (and any other prior BOM) only for
                # these three user-confirmed finished glove products.
                con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (parent_id,))
                con.execute(
                    "INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)",
                    (parent_id, component_id, QTY_PER),
                )

                # Restore the imported option in the raw-item name so S/M/L remain
                # distinguishable in item/inventory/BOM lists.
                sn, sd = r["source_name"], r["source_detail"]
                if sn and sd:
                    raw_name = f"{sn} [{sd}]"
                    con.execute(
                        "UPDATE products SET name=?,updated_at=? WHERE id=?",
                        (raw_name, core.now_iso(), component_id),
                    )
                else:
                    raw_name = _text(r["component"]["name"])

                con.execute(
                    "UPDATE products SET name=?,item_code=?,item_type='finished',active=1,updated_at=? WHERE id=?",
                    (r["finished_name"], f"CP-{r['option_id']}", core.now_iso(), parent_id),
                )

                changes.append({
                    "size": size,
                    "option_id": r["option_id"],
                    "parent_id": parent_id,
                    "component_id": component_id,
                    "component_code": _text(r["component"]["item_code"]),
                    "component_name": raw_name,
                    "qty_per": QTY_PER,
                })

            # Verify from the exact table the BOM UI reads.
            verified = []
            for ch in changes:
                row = con.execute(
                    """SELECT b.component_product_id,b.qty_per,p.item_code,p.name
                       FROM bom_items b JOIN products p ON p.id=b.component_product_id
                       WHERE b.parent_product_id=?""",
                    (ch["parent_id"],),
                ).fetchall()
                if len(row) != 1:
                    raise RuntimeError(f"{ch['size']} BOM 저장 검증 실패: 행 수 {len(row)}")
                x = row[0]
                if int(x["component_product_id"]) != ch["component_id"] or abs(float(x["qty_per"] or 0)-QTY_PER) > 1e-9:
                    raise RuntimeError(f"{ch['size']} BOM 저장 검증 실패: 구성품/수량 불일치")
                verified.append({
                    "size": ch["size"],
                    "component_code": _text(x["item_code"]),
                    "component_name": _text(x["name"]),
                    "qty_per": float(x["qty_per"]),
                })

            con.commit()
            return {
                "ok": True,
                "status": "verified_from_purchase_source_map",
                "rule": RULE,
                "items": verified,
                "inventory_changed": False,
                "bom_changed": True,
            }
        except Exception:
            try:
                con.rollback()
            except Exception:
                pass
            raise
