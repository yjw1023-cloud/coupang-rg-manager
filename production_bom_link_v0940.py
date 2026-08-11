"""RG Manager v0.9.40 direct production-target BOM reconciliation.

Why this exists
---------------
Older databases can contain a current BOM under an obsolete Coupang option row
while the current Rocket Growth inbound Excel contains the real option ID.  The
production Excel option ID is authoritative for *current* production.

v0.9.40 intentionally does not require the obsolete row to have already been
classified as a return alias or legacy mapping.  Instead, for each production
row whose exact option-ID product has no BOM, it looks only at current BOM
parents and moves a BOM when exactly one finished-product candidate strongly
matches the target/source product name and package quantity.

Safety rules
------------
- exact production option ID must resolve to one ERP product;
- target must currently have no BOM;
- candidate must currently own a BOM and be a different finished product;
- package quantity (2개/2P/etc.) must agree when present;
- names must strongly match after package/punctuation normalization;
- exactly one candidate must qualify;
- current bom_items.parent_product_id is the only operational relationship moved;
- historical sales, inventory and production rows are never rewritten;
- the old row is archived after migration and the change is logged.
"""
from __future__ import annotations

import re
from typing import Any

_APPLIED = False

_PACK_RE = re.compile(
    r"(?<![0-9])([0-9]+)\s*(?:개입|개|p|pcs?|세트|set)(?![a-z0-9가-힣])",
    flags=re.I,
)


def _text(v: Any) -> str:
    return str(v or "").strip()


def _pack_qty(name: Any) -> int | None:
    vals = []
    for m in _PACK_RE.finditer(_text(name).lower()):
        try:
            vals.append(int(m.group(1)))
        except Exception:
            pass
    return vals[0] if vals else None


def _core_name(name: Any) -> str:
    s = _text(name).lower()
    s = _PACK_RE.sub(" ", s)
    # Option descriptors such as colour/weight often live after the core product
    # name, so punctuation and whitespace are ignored but Korean/Latin words stay.
    return re.sub(r"[^0-9a-z가-힣]+", "", s)


def _tokens(name: Any) -> set[str]:
    s = _PACK_RE.sub(" ", _text(name).lower())
    return set(re.findall(r"[a-z가-힣]+", s))


def _strong_name_match(a: Any, b: Any) -> bool:
    aq, bq = _pack_qty(a), _pack_qty(b)
    if aq is not None and bq is not None and aq != bq:
        return False

    ca, cb = _core_name(a), _core_name(b)
    if not ca or not cb:
        return False

    shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    # The glove case is intentionally covered by this: after removing 2개/2P,
    # '글러브길들이기밴드' is contained in '글러브길들이기밴드야구'.
    if len(shorter) >= 5 and shorter in longer:
        return True

    ta, tb = _tokens(a), _tokens(b)
    if len(ta) < 2 or len(tb) < 2:
        return False
    common = ta & tb
    return len(common) >= 3 and (common == ta or common == tb)


def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _ensure_log(con) -> None:
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


def _bom_rows(con, parent_id: int):
    return con.execute(
        """SELECT id,parent_product_id,component_product_id,qty_per
           FROM bom_items WHERE parent_product_id=? ORDER BY id""",
        (int(parent_id),),
    ).fetchall()


def _products_with_bom(con):
    return con.execute(
        """SELECT DISTINCT p.id,p.item_code,p.option_id,p.name,p.item_type,p.active
           FROM products p
           JOIN bom_items b ON b.parent_product_id=p.id
           ORDER BY p.id"""
    ).fetchall()


def _target_product(batch_module, con, option_id: str):
    product, error = batch_module._find_product(con, option_id)
    if error or product is None:
        return None
    return product


def _upsert_alias(con, old_product, target_id: int, now: str) -> None:
    if not _table_exists(con, "return_discount_aliases"):
        return
    oid = _text(old_product["option_id"])
    if not oid.isdigit():
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
            int(target_id),
            _text(old_product["name"]),
            "production_exact_option_v0940",
            now,
            now,
        ),
    )


def reconcile_rows(core_module, batch_module, parsed_rows, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    repaired = []
    skipped = []

    with core_module._conn(db) as con:
        if not _table_exists(con, "bom_items"):
            return {"repaired": repaired, "skipped": skipped}

        for src in list(parsed_rows or []):
            option_id = _text(src.get("option_id"))
            if not option_id:
                continue

            target = _target_product(batch_module, con, option_id)
            if target is None:
                continue
            target_id = int(target["id"])
            if _bom_rows(con, target_id):
                continue

            target_names = [
                _text(target["name"]),
                _text(src.get("source_name")),
            ]
            target_names = [x for x in target_names if x]

            candidates = []
            for old in _products_with_bom(con):
                old_id = int(old["id"])
                if old_id == target_id:
                    continue
                if _text(old["item_type"]).lower() != "finished":
                    continue
                old_name = _text(old["name"])
                if not old_name:
                    continue
                if not any(_strong_name_match(old_name, n) for n in target_names):
                    continue

                rows = _bom_rows(con, old_id)
                if not rows:
                    continue
                if any(int(r["component_product_id"]) == target_id for r in rows):
                    continue
                candidates.append((old, rows))

            if len(candidates) != 1:
                skipped.append(
                    {
                        "option_id": option_id,
                        "target_product_id": target_id,
                        "candidate_count": len(candidates),
                    }
                )
                continue

            old, rows = candidates[0]
            old_id = int(old["id"])

            # Same-transaction recheck: never overwrite a BOM that appeared while
            # Streamlit was rerunning.
            if _bom_rows(con, target_id):
                continue

            _ensure_log(con)
            now = core_module.now_iso()
            for row in rows:
                con.execute(
                    """INSERT INTO bom_change_log
                       (action,bom_id,parent_product_id,component_product_id,
                        qty_per,changed_at,note)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        "MIGRATE_EXACT_PRODUCTION_OPTION",
                        int(row["id"]),
                        old_id,
                        int(row["component_product_id"]),
                        float(row["qty_per"] or 0),
                        now,
                        (
                            f"생산 Excel 정확 옵션ID {option_id} product_id={target_id}로 "
                            f"현재 BOM 이동; 기존 product_id={old_id}"
                        ),
                    ),
                )

            con.execute(
                "UPDATE bom_items SET parent_product_id=? WHERE parent_product_id=?",
                (target_id, old_id),
            )
            con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=?",
                (now, old_id),
            )
            _upsert_alias(con, old, target_id, now)

            repaired.append(
                {
                    "option_id": option_id,
                    "old_product_id": old_id,
                    "target_product_id": target_id,
                    "components": len(rows),
                }
            )

    return {"repaired": repaired, "skipped": skipped}


def apply(core_module, production_batch_module) -> None:
    """Patch production validation directly; app.py calls this at startup."""
    global _APPLIED
    marker = "_rg_production_bom_link_v0940_applied"
    if _APPLIED or getattr(production_batch_module, marker, False):
        return

    previous_validate = production_batch_module.validate_rows
    previous_execute = production_batch_module.execute_batch

    def validate_rows(core, parsed_rows, db_path=None):
        reconcile_rows(core, production_batch_module, parsed_rows, db_path)
        return previous_validate(core, parsed_rows, db_path)

    def execute_batch(core, parsed, file_name, production_date, db_path=None):
        reconcile_rows(
            core,
            production_batch_module,
            list(parsed.get("rows") or []),
            db_path,
        )
        return previous_execute(core, parsed, file_name, production_date, db_path)

    production_batch_module.validate_rows = validate_rows
    production_batch_module.execute_batch = execute_batch
    setattr(production_batch_module, marker, True)
    _APPLIED = True
