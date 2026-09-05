"""Restore purchase option/detail text into own-item names.

v0.9.166
- Primary source: purchase_source_product_map(source_name, source_detail, product_id).
- Fallback/verification source: actual purchase_lines linked to product_id.
- This fixes recently registered SKUs whose purchase was committed but whose durable
  source mapping did not retain the option/detail row.
- Repair only when one product resolves to exactly one non-empty source/detail pair
  and the current product name is still exactly the untouched source_name.
- Never overwrite a manually renamed product.
- Patch future new-item naming so default names always become
  `source_name [source_detail]` when detail exists.
"""
from __future__ import annotations

RULE = "v0.9.166-purchase-option-name-from-lines"


def _norm(v):
    return str(v or "").strip()


def _effective_name(source_name, source_detail, requested_name):
    source_name = _norm(source_name)
    source_detail = _norm(source_detail)
    requested_name = _norm(requested_name)
    if not requested_name:
        requested_name = source_name
    if source_detail and requested_name == source_name:
        return f"{source_name} [{source_detail}]"
    return requested_name


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _columns(con, table):
    try:
        return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _observations(con):
    """Return product_id -> set[(source_name, source_detail)] from durable facts."""
    out = {}

    if _table_exists(con, "purchase_source_product_map"):
        cols = _columns(con, "purchase_source_product_map")
        if {"product_id", "source_name", "source_detail"}.issubset(cols):
            rows = con.execute(
                """SELECT product_id,source_name,source_detail
                   FROM purchase_source_product_map
                   WHERE product_id IS NOT NULL
                     AND TRIM(COALESCE(source_name,''))<>''
                     AND TRIM(COALESCE(source_detail,''))<>''"""
            ).fetchall()
            for r in rows:
                pair = (_norm(r["source_name"]), _norm(r["source_detail"]))
                if pair[0] and pair[1]:
                    out.setdefault(int(r["product_id"]), set()).add(pair)

    # Actual committed purchase rows are the authoritative fallback.  The user's
    # purchase workbook C/D values flow into source_name/source_detail here.
    if _table_exists(con, "purchase_lines"):
        cols = _columns(con, "purchase_lines")
        if {"product_id", "source_name", "source_detail"}.issubset(cols):
            rows = con.execute(
                """SELECT product_id,source_name,source_detail
                   FROM purchase_lines
                   WHERE product_id IS NOT NULL
                     AND TRIM(COALESCE(source_name,''))<>''
                     AND TRIM(COALESCE(source_detail,''))<>''"""
            ).fetchall()
            for r in rows:
                pair = (_norm(r["source_name"]), _norm(r["source_detail"]))
                if pair[0] and pair[1]:
                    out.setdefault(int(r["product_id"]), set()).add(pair)

    return out


def _repair_existing(core, db):
    changed = []
    skipped_multi = []
    no_source = []

    with core._conn(db) as con:
        observations = _observations(con)
        rows = con.execute(
            """SELECT id,item_code,name,option_id,active
               FROM products
               WHERE option_id IS NULL AND active=1
               ORDER BY id"""
        ).fetchall()

        for product in rows:
            pid = int(product["id"])
            pairs = observations.get(pid, set())
            if not pairs:
                continue
            if len(pairs) != 1:
                skipped_multi.append(pid)
                continue

            source_name, source_detail = next(iter(pairs))
            current_name = _norm(product["name"])

            # Do not modify a deliberate/manual/system rename.  We only repair the
            # exact broken state: current name equals the plain purchase source name.
            if current_name != source_name:
                continue

            new_name = f"{source_name} [{source_detail}]"
            if new_name == current_name:
                continue

            con.execute(
                "UPDATE products SET name=?,updated_at=? WHERE id=?",
                (new_name, core.now_iso(), pid),
            )
            changed.append(
                {
                    "product_id": pid,
                    "item_code": _norm(product["item_code"]),
                    "old_name": current_name,
                    "new_name": new_name,
                }
            )

        try:
            con.commit()
        except Exception:
            pass

    return changed, skipped_multi, no_source


def _patch_future_naming():
    try:
        mod = __import__("purchase_new_item_persist_v09136", fromlist=["*"])
        mod._effective_new_name = _effective_name
        return True
    except Exception:
        return False


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    changed, skipped_multi, no_source = _repair_existing(core, db)
    patched = _patch_future_naming()
    return {
        "ok": True,
        "rule": RULE,
        "changed": len(changed),
        "changes": changed,
        "skipped_ambiguous_product_ids": skipped_multi,
        "future_naming_patched": patched,
        "inventory_changed": False,
        "bom_changed": False,
    }
