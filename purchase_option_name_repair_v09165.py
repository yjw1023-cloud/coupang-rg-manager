"""Restore purchase option/detail text into own-item names.

v0.9.165
- Purchase source mapping stores source_name + source_detail (Excel C/D).
- Some recently created own-warehouse SKUs were persisted with only source_name,
  leaving variants such as ribbon colors/options visually indistinguishable.
- Repair only when one product has exactly one non-empty mapped detail and the
  current product name is still exactly the untouched source_name.
- Never overwrite a manually renamed product.
- Patch future new-item naming so a default name always becomes
  `source_name [source_detail]` when detail exists.
"""
from __future__ import annotations

RULE = "v0.9.165-purchase-option-name"


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


def _repair_existing(core, db):
    changed = []
    skipped_multi = []
    with core._conn(db) as con:
        if not _table_exists(con, "purchase_source_product_map"):
            return changed, skipped_multi

        rows = con.execute(
            """SELECT m.product_id,m.source_name,m.source_detail,
                      p.item_code,p.name,p.option_id,p.active
               FROM purchase_source_product_map m
               JOIN products p ON p.id=m.product_id
               WHERE p.option_id IS NULL AND p.active=1
                 AND TRIM(COALESCE(m.source_detail,''))<>''
               ORDER BY m.product_id,m.created_at"""
        ).fetchall()

        grouped = {}
        for r in rows:
            pid = int(r["product_id"])
            grouped.setdefault(pid, []).append(r)

        for pid, group in grouped.items():
            pairs = {
                (_norm(r["source_name"]), _norm(r["source_detail"]))
                for r in group
                if _norm(r["source_name"]) and _norm(r["source_detail"])
            }
            if len(pairs) != 1:
                skipped_multi.append(pid)
                continue

            source_name, source_detail = next(iter(pairs))
            current_name = _norm(group[-1]["name"])
            if current_name != source_name:
                # User/manual/system rename exists; do not overwrite it.
                continue

            new_name = f"{source_name} [{source_detail}]"
            con.execute(
                "UPDATE products SET name=?,updated_at=? WHERE id=?",
                (new_name, core.now_iso(), pid),
            )
            changed.append(
                {
                    "product_id": pid,
                    "item_code": _norm(group[-1]["item_code"]),
                    "old_name": current_name,
                    "new_name": new_name,
                }
            )

        try:
            con.commit()
        except Exception:
            pass
    return changed, skipped_multi


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
    changed, skipped_multi = _repair_existing(core, db)
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
