"""BOM save must edit an existing parent/component relationship, not silently ignore it.

v0.9.182
- If the selected finished product + component already exists in bom_items, update
  qty_per in place.
- If it does not exist, delegate to the existing core.add_bom implementation.
- Keep the existing finished/raw/active safety rules even for updates.
- Consolidate accidental duplicate rows for the exact same parent/component pair.
- Log quantity changes in bom_change_log while leaving all historical production
  and inventory transactions untouched.
"""
from __future__ import annotations

import sqlite3


_MARKER = "_rg_bom_save_upsert_v09182_applied"


def _conn(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _product(con, product_id: int):
    return con.execute(
        "SELECT id,name,item_code,item_type,active FROM products WHERE id=?",
        (int(product_id),),
    ).fetchone()


def _validate(con, parent_product_id: int, component_product_id: int, qty_per: float):
    try:
        qty = float(qty_per)
    except Exception as exc:
        raise ValueError("소요수량은 숫자여야 합니다.") from exc
    if qty <= 0:
        raise ValueError("소요수량은 0보다 커야 합니다.")
    if int(parent_product_id) == int(component_product_id):
        raise ValueError("완제품과 구성품은 같을 수 없습니다.")

    parent = _product(con, parent_product_id)
    component = _product(con, component_product_id)
    if not parent:
        raise ValueError("완제품 품목을 찾지 못했습니다.")
    if int(parent["active"] or 0) != 1 or str(parent["item_type"] or "").lower() != "finished":
        raise ValueError("BOM의 완제품은 사용중인 완제품 품목만 선택할 수 있습니다.")
    if not component:
        raise ValueError("구성품 품목을 찾지 못했습니다.")
    if int(component["active"] or 0) != 1 or str(component["item_type"] or "").lower() != "raw":
        raise ValueError("BOM의 구성품은 사용중인 자체창고 구성품만 선택할 수 있습니다.")
    return qty


def _ensure_log(con):
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


def apply(core_module):
    if core_module is None or getattr(core_module, _MARKER, False):
        return core_module

    previous_add_bom = core_module.add_bom

    def add_bom(parent_product_id: int, component_product_id: int, qty_per: float, db_path=None):
        db = db_path or core_module.DEFAULT_DB
        with _conn(core_module, db) as con:
            qty = _validate(con, parent_product_id, component_product_id, qty_per)
            rows = con.execute(
                """SELECT id,qty_per FROM bom_items
                   WHERE parent_product_id=? AND component_product_id=?
                   ORDER BY id""",
                (int(parent_product_id), int(component_product_id)),
            ).fetchall()

            if rows:
                keep_id = int(rows[0]["id"])
                old_qty = float(rows[0]["qty_per"] or 0)
                _ensure_log(con)
                now = core_module.now_iso()
                if abs(old_qty - qty) > 1e-12:
                    con.execute(
                        """INSERT INTO bom_change_log
                           (action,bom_id,parent_product_id,component_product_id,qty_per,changed_at,note)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            "UPDATE_QTY",
                            keep_id,
                            int(parent_product_id),
                            int(component_product_id),
                            old_qty,
                            now,
                            f"BOM 소요수량 {old_qty:g} -> {qty:g}",
                        ),
                    )
                con.execute("UPDATE bom_items SET qty_per=? WHERE id=?", (qty, keep_id))

                # Same parent/component duplicated rows are never meaningful for a
                # recipe. Keep one canonical row so the current-BOM table and
                # production consumption cannot double-count it.
                for duplicate in rows[1:]:
                    dup_id = int(duplicate["id"])
                    con.execute(
                        """INSERT INTO bom_change_log
                           (action,bom_id,parent_product_id,component_product_id,qty_per,changed_at,note)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            "DELETE_DUPLICATE",
                            dup_id,
                            int(parent_product_id),
                            int(component_product_id),
                            float(duplicate["qty_per"] or 0),
                            now,
                            f"동일 BOM 중복행 정리; 유지 bom_id={keep_id}",
                        ),
                    )
                    con.execute("DELETE FROM bom_items WHERE id=?", (dup_id,))
                return {
                    "action": "updated",
                    "bom_id": keep_id,
                    "old_qty": old_qty,
                    "qty_per": qty,
                }

        # New relationship: retain the established add_bom path and all of its
        # existing schema/side-effect behavior.
        if db_path is None:
            result = previous_add_bom(parent_product_id, component_product_id, qty_per)
        else:
            try:
                result = previous_add_bom(
                    parent_product_id, component_product_id, qty_per, db_path=db_path
                )
            except TypeError:
                result = previous_add_bom(parent_product_id, component_product_id, qty_per)
        return result if result is not None else {"action": "inserted", "qty_per": float(qty_per)}

    core_module.add_bom = add_bom
    setattr(core_module, _MARKER, True)
    return core_module
