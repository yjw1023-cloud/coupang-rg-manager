"""RG Manager v0.9.39 production-target BOM repair.

The production Excel's Coupang option ID is authoritative for the finished product
that should receive current production. Older databases can have the current BOM
attached to a historical/return-generated Coupang option instead.

This patch is deliberately target-guided:
- only runs when an exact production-Excel option product exists but has no BOM;
- searches other current BOM parents for one strongly matching product;
- moves the current BOM only when evidence is unambiguous:
  * explicit return alias -> exact target, or
  * legacy-ERP mapped old code -> current non-legacy production target, or
  * archived old code -> current active production target;
- preserves historical production/sales/inventory rows;
- logs the current-BOM parent migration;
- never guesses when multiple candidates remain.
"""
from __future__ import annotations

from typing import Any

import bom_parent_repair_v0938 as base

_APPLIED = False


def _legacy_mapped_ids(con) -> set[int]:
    if not base._table_exists(con, "legacy_v07_mappings"):
        return set()
    cols = {
        str(r["name"])
        for r in con.execute("PRAGMA table_info(legacy_v07_mappings)").fetchall()
    }
    if "product_id" not in cols:
        return set()
    if "source_system" in cols:
        rows = con.execute(
            """SELECT DISTINCT product_id
               FROM legacy_v07_mappings
               WHERE product_id IS NOT NULL
                 AND COALESCE(source_system,'')='claude_erp'"""
        ).fetchall()
    else:
        rows = con.execute(
            """SELECT DISTINCT product_id
               FROM legacy_v07_mappings
               WHERE product_id IS NOT NULL"""
        ).fetchall()
    return {int(r["product_id"]) for r in rows}


def _strong_pair(a: Any, b: Any) -> bool:
    return bool(
        base._strong_child_to_parent_name(a, b)
        or base._strong_child_to_parent_name(b, a)
    )


def _target_product(production_batch_module, con, option_id: str):
    product, error = production_batch_module._find_product(con, option_id)
    if error or product is None:
        return None
    return dict(product)


def _bom_rows(con, parent_id: int):
    return con.execute(
        """SELECT id,parent_product_id,component_product_id,qty_per
           FROM bom_items
           WHERE parent_product_id=?
           ORDER BY id""",
        (int(parent_id),),
    ).fetchall()


def _candidate_method(
    child_id: int,
    child: dict[str, Any],
    target_id: int,
    explicit: dict[int, int],
    legacy_ids: set[int],
) -> str | None:
    if explicit.get(child_id) == target_id:
        return "explicit_return_alias"

    # Key v0.9.39 case: old Claude ERP carried a Coupang return/temporary option
    # as if it were the product code. The current Coupang inbound Excel points to
    # the real option ID, which is not that old legacy mapping.
    if child_id in legacy_ids and target_id not in legacy_ids:
        return "legacy_old_code_to_current_option"

    # If v0.9.37 already archived a stale return/old option, it may still own the
    # current BOM. Moving that BOM to the exact active production target is safe
    # when the name/package match is unique.
    if int(child.get("active") or 0) != 1:
        return "archived_old_option"

    return None


def _upsert_alias(con, child: dict[str, Any], target_id: int, now: str, method: str) -> None:
    if not base._table_exists(con, "return_discount_aliases"):
        return
    oid = base._oid(child.get("option_id"))
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
            int(target_id),
            str(child.get("name") or ""),
            f"production_target_v0939:{method}",
            now,
            now,
        ),
    )


def repair_for_production_rows(
    core_module,
    production_batch_module,
    parsed_rows: list[dict[str, Any]],
    db_path=None,
) -> dict[str, Any]:
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    repaired: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    with core_module._conn(db) as con:
        if not base._table_exists(con, "bom_items"):
            return {"repaired": repaired, "skipped": skipped}

        products = base._products(con)
        by_id = {int(p["id"]): p for p in products}
        explicit = base._explicit_parent_map(con)
        legacy_ids = _legacy_mapped_ids(con)

        for src in parsed_rows:
            option_id = str(src.get("option_id") or "").strip()
            if not option_id:
                continue

            target = _target_product(production_batch_module, con, option_id)
            if not target:
                continue
            target_id = int(target["id"])

            # Already correct.
            if _bom_rows(con, target_id):
                continue
            if int(target.get("active") or 0) != 1:
                skipped.append({
                    "option_id": option_id,
                    "target_product_id": target_id,
                    "reason": "생산대상 원상품이 보관상태",
                })
                continue

            source_name = str(src.get("source_name") or "").strip()
            option_name = str(src.get("option_name") or "").strip()
            names = [
                str(target.get("name") or "").strip(),
                source_name,
                option_name,
            ]
            names = [x for x in names if x]

            candidates: list[tuple[int, str]] = []
            for child in products:
                child_id = int(child["id"])
                if child_id == target_id:
                    continue
                rows = _bom_rows(con, child_id)
                if not rows:
                    continue
                if str(child.get("item_type") or "").strip().lower() != "finished":
                    continue

                child_name = str(child.get("name") or "").strip()
                if not child_name or not any(_strong_pair(child_name, n) for n in names):
                    continue

                # Never create a self-BOM on the new target.
                if any(int(r["component_product_id"]) == target_id for r in rows):
                    continue

                method = _candidate_method(
                    child_id, child, target_id, explicit, legacy_ids
                )
                if method:
                    candidates.append((child_id, method))

            # No guessing: exactly one old/current-BOM owner must qualify.
            if len(candidates) != 1:
                if candidates:
                    skipped.append({
                        "option_id": option_id,
                        "target_product_id": target_id,
                        "reason": f"BOM 이전 후보 {len(candidates)}개로 모호",
                        "candidates": [x[0] for x in candidates],
                    })
                continue

            child_id, method = candidates[0]
            child = by_id.get(child_id)
            rows = _bom_rows(con, child_id)
            if not child or not rows:
                continue

            # Re-check target just before write.
            if _bom_rows(con, target_id):
                continue

            base._ensure_log_table(con)
            now = core_module.now_iso()
            for row in rows:
                con.execute(
                    """INSERT INTO bom_change_log
                       (action,bom_id,parent_product_id,component_product_id,
                        qty_per,changed_at,note)
                       VALUES(?,?,?,?,?,?,?)""",
                    (
                        "MIGRATE_PRODUCTION_TARGET_PARENT",
                        int(row["id"]),
                        child_id,
                        int(row["component_product_id"]),
                        float(row["qty_per"] or 0),
                        now,
                        (
                            f"생산 Excel 옵션ID {option_id}의 정상 원상품 "
                            f"product_id={target_id}로 현재 BOM 이동 ({method})"
                        ),
                    ),
                )

            con.execute(
                "UPDATE bom_items SET parent_product_id=? WHERE parent_product_id=?",
                (target_id, child_id),
            )

            # Old return/legacy option remains for history, but must not act as a
            # managed current SKU anywhere else.
            con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=?",
                (now, child_id),
            )
            _upsert_alias(con, child, target_id, now, method)

            repaired.append({
                "option_id": option_id,
                "old_product_id": child_id,
                "target_product_id": target_id,
                "components": len(rows),
                "method": method,
            })

    return {"repaired": repaired, "skipped": skipped}


def apply(core_module, production_batch_module) -> None:
    global _APPLIED
    if _APPLIED or getattr(
        production_batch_module, "_rg_production_bom_repair_v0939_applied", False
    ):
        return

    # Keep the v0.9.38 generic repair as a first pass.
    try:
        base.apply(core_module, production_batch_module)
    except Exception:
        pass

    previous_validate = production_batch_module.validate_rows
    previous_execute = production_batch_module.execute_batch

    def validate_rows(core, parsed_rows, db_path=None):
        repair_for_production_rows(
            core, production_batch_module, parsed_rows, db_path
        )
        return previous_validate(core, parsed_rows, db_path)

    def execute_batch(core, parsed, file_name, production_date, db_path=None):
        repair_for_production_rows(
            core, production_batch_module, list(parsed.get("rows") or []), db_path
        )
        return previous_execute(
            core, parsed, file_name, production_date, db_path
        )

    production_batch_module.validate_rows = validate_rows
    production_batch_module.execute_batch = execute_batch
    production_batch_module._rg_production_bom_repair_v0939_applied = True
    _APPLIED = True
