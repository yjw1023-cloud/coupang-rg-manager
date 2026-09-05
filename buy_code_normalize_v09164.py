"""Normalize legacy BUY-* own-item codes to sequential JDS codes.

v0.9.164
- BUY-* was an old literal products.item_code, not a second hidden JDS code.
- Only non-Coupang products (option_id IS NULL) whose current code starts BUY-
  are migrated.
- Existing product ids are preserved, so purchase history, inventory and BOM
  relations that use product_id remain intact.
- Old/new code pairs are audited in product_code_aliases.
- Future JDS allocation is patched to highest numeric JDS + 1.
"""
from __future__ import annotations

import re

RULE = "v0.9.164-buy-to-jds"


def _next_jds_code_in_connection(con) -> str:
    max_no = 0
    used = set()
    rows = con.execute("SELECT item_code FROM products WHERE item_code IS NOT NULL").fetchall()
    for row in rows:
        code = str(row["item_code"] or "").strip()
        if not code:
            continue
        used.add(code.upper())
        m = re.fullmatch(r"JDS0*(\d+)", code, flags=re.IGNORECASE)
        if m:
            max_no = max(max_no, int(m.group(1)))
    n = max_no + 1
    while f"JDS{n}".upper() in used:
        n += 1
    return f"JDS{n}"


def _patch_generators(core):
    try:
        mod = __import__("purchase_new_item_persist_v09136", fromlist=["*"])
        mod._next_jds_code_in_connection = _next_jds_code_in_connection
    except Exception:
        pass

    try:
        mod = __import__("item_ui_v086", fromlist=["*"])

        def _next_jds_code(core_module):
            core_module.init_db(core_module.DEFAULT_DB)
            with core_module._conn(core_module.DEFAULT_DB) as con:
                return _next_jds_code_in_connection(con)

        mod._next_jds_code = _next_jds_code
    except Exception:
        pass

    try:
        mod = __import__("requested_product_seed_v09133", fromlist=["*"])
        mod._next_jds_code = _next_jds_code_in_connection
    except Exception:
        pass


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    changes = []

    with core._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS product_code_aliases(
                   old_code TEXT PRIMARY KEY,
                   product_id INTEGER NOT NULL,
                   new_code TEXT NOT NULL,
                   reason TEXT NOT NULL,
                   migrated_at TEXT NOT NULL
               )"""
        )

        candidates = con.execute(
            """SELECT id,item_code,name,item_type,active
               FROM products
               WHERE option_id IS NULL
                 AND item_code IS NOT NULL
                 AND UPPER(TRIM(item_code)) LIKE 'BUY-%'
               ORDER BY id"""
        ).fetchall()

        for row in candidates:
            pid = int(row["id"])
            old_code = str(row["item_code"] or "").strip()
            if not old_code:
                continue

            # If this exact old code was already migrated but a stale startup
            # restored it, reuse the audited new code when still available.
            alias = con.execute(
                "SELECT new_code FROM product_code_aliases WHERE old_code=? AND product_id=?",
                (old_code, pid),
            ).fetchone()
            new_code = str(alias["new_code"] or "").strip() if alias else ""
            if new_code:
                occupied = con.execute(
                    "SELECT id FROM products WHERE UPPER(item_code)=UPPER(?) AND id<>? LIMIT 1",
                    (new_code, pid),
                ).fetchone()
                if occupied:
                    new_code = ""

            if not new_code:
                new_code = _next_jds_code_in_connection(con)

            con.execute(
                "UPDATE products SET item_code=?,updated_at=? WHERE id=?",
                (new_code, core.now_iso(), pid),
            )
            con.execute(
                """INSERT INTO product_code_aliases(old_code,product_id,new_code,reason,migrated_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(old_code) DO UPDATE SET
                     product_id=excluded.product_id,
                     new_code=excluded.new_code,
                     reason=excluded.reason,
                     migrated_at=excluded.migrated_at""",
                (old_code, pid, new_code, RULE, core.now_iso()),
            )
            changes.append(
                {
                    "product_id": pid,
                    "name": str(row["name"] or ""),
                    "old_code": old_code,
                    "new_code": new_code,
                }
            )

        try:
            con.commit()
        except Exception:
            pass

    _patch_generators(core)
    return {
        "ok": True,
        "rule": RULE,
        "changed": len(changes),
        "changes": changes,
        "inventory_changed": False,
        "bom_changed": False,
    }
