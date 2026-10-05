"""v0.9.285 one-time repair for the missing white PEVA tablecloth RG product."""
from __future__ import annotations

TARGET_OID = "96089460574"
TARGET_CODE = "CP-96089460574"
TARGET_NAME = "행사용 일회용 테이블보 PEVA / 5개 화이트 137x180cm"
TARGET_BARCODE = "S0038542080293"
COMPONENT_CODE = "JDS800"
GHOST_OID = "94731787590"


def _cols(con, table):
    try:
        return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _exists(con, table):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def apply(core):
    core.init_db(core.DEFAULT_DB)
    with core._conn(core.DEFAULT_DB) as con:
        target = con.execute(
            "SELECT id,active FROM products WHERE option_id=? OR item_code=? LIMIT 1",
            (TARGET_OID, TARGET_CODE),
        ).fetchone()
        ghost = con.execute(
            "SELECT id,active FROM products WHERE option_id=? LIMIT 1",
            (GHOST_OID,),
        ).fetchone()
    import importlib
    backup_mod = importlib.import_module("db_backup_v09248")
    backup_mod.backup_db(core, "tablecloth_white_repair_v09285", core.DEFAULT_DB)

    now = core.now_iso()
    moved = {}
    with core._conn(core.DEFAULT_DB) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            target = con.execute(
                "SELECT id FROM products WHERE option_id=? OR item_code=? LIMIT 1",
                (TARGET_OID, TARGET_CODE),
            ).fetchone()
            if target:
                target_id = int(target["id"])
                con.execute(
                    """UPDATE products
                       SET item_code=?,option_id=?,name=?,item_type='finished',active=1,updated_at=?
                       WHERE id=?""",
                    (TARGET_CODE, TARGET_OID, TARGET_NAME, now, target_id),
                )
            else:
                cur = con.execute(
                    """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
                       VALUES(?,?,?,'finished',0,1,?)""",
                    (TARGET_CODE, TARGET_OID, TARGET_NAME, now),
                )
                target_id = int(cur.lastrowid)

            component = con.execute(
                "SELECT id FROM products WHERE item_code=? LIMIT 1",
                (COMPONENT_CODE,),
            ).fetchone()
            if not component:
                raise RuntimeError("JDS800 화이트 테이블보 원재료를 찾지 못했습니다.")
            component_id = int(component["id"])

            if _exists(con, "bom_items"):
                con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (target_id,))
                con.execute(
                    """INSERT OR REPLACE INTO bom_items(parent_product_id,component_product_id,qty_per)
                       VALUES(?,?,?)""",
                    (target_id, component_id, 5),
                )

            if _exists(con, "rg_barcode_master"):
                con.execute(
                    """INSERT INTO rg_barcode_master
                       (vendor_item_id,barcode,seller_product_id,product_name,option_name,source,updated_at)
                       VALUES(?,?,?,?,?,'tablecloth_white_repair_v09285',?)
                       ON CONFLICT(vendor_item_id) DO UPDATE SET
                         barcode=excluded.barcode,
                         product_name=excluded.product_name,
                         source=excluded.source,
                         updated_at=excluded.updated_at""",
                    (TARGET_OID, TARGET_BARCODE, "", TARGET_NAME, "", now),
                )

            ghost = con.execute(
                "SELECT id FROM products WHERE option_id=? LIMIT 1",
                (GHOST_OID,),
            ).fetchone()
            ghost_id = int(ghost["id"]) if ghost else None

            for table, option_col in (
                ("sales_stats", "option_id"),
                ("ad_performance", "option_id"),
                ("settlement_sales", "option_id"),
                ("logistics_fees", "option_id"),
            ):
                if not _exists(con, table):
                    continue
                cols = _cols(con, table)
                if "product_id" in cols and option_col in cols:
                    cur = con.execute(
                        f'UPDATE "{table}" SET product_id=? WHERE CAST("{option_col}" AS TEXT)=?',
                        (target_id, TARGET_OID),
                    )
                    moved[table] = int(cur.rowcount or 0)

            if _exists(con, "coupang_rg_order_items"):
                cols = _cols(con, "coupang_rg_order_items")
                if {"vendor_item_id", "product_id"}.issubset(cols):
                    cur = con.execute(
                        """UPDATE coupang_rg_order_items
                           SET product_id=?
                           WHERE CAST(vendor_item_id AS TEXT)=?""",
                        (target_id, TARGET_OID),
                    )
                    moved["coupang_rg_order_items"] = int(cur.rowcount or 0)

            if ghost_id is not None and _exists(con, "sales_stats") and _exists(con, "imports"):
                sc = _cols(con, "sales_stats")
                ic = _cols(con, "imports")
                if {"product_id", "import_id"}.issubset(sc) and {"id", "period_start", "period_end"}.issubset(ic):
                    cur = con.execute(
                        """UPDATE sales_stats
                           SET product_id=?, option_id=?
                           WHERE product_id=?
                             AND import_id IN (
                                 SELECT id FROM imports
                                 WHERE data_type='sales_stats'
                                   AND period_end>='2026-10-01'
                                   AND period_start<='2026-10-31'
                             )""",
                        (target_id, TARGET_OID, ghost_id),
                    )
                    moved["october_ghost_sales_stats"] = int(cur.rowcount or 0)

            if ghost_id is not None and _exists(con, "coupang_rg_order_items"):
                cols = _cols(con, "coupang_rg_order_items")
                if {"product_id", "paid_date"}.issubset(cols):
                    set_bits = ["product_id=?"]
                    params = [target_id]
                    if "vendor_item_id" in cols:
                        set_bits.append("vendor_item_id=?")
                        params.append(TARGET_OID)
                    if "product_name" in cols:
                        set_bits.append("product_name=?")
                        params.append(TARGET_NAME)
                    params.append(ghost_id)
                    cur = con.execute(
                        "UPDATE coupang_rg_order_items SET " + ",".join(set_bits)
                        + " WHERE product_id=? AND paid_date>='2026-10-01' AND paid_date<='2026-10-31'",
                        params,
                    )
                    moved["october_ghost_api_orders"] = int(cur.rowcount or 0)

            # Make the restored white item user-visible even if it had once
            # been auto-hidden when report data created a placeholder master.
            if _exists(con, "system_hidden_products"):
                con.execute(
                    "DELETE FROM system_hidden_products WHERE product_id=?",
                    (target_id,),
                )

            if ghost_id is not None and ghost_id != target_id:
                con.execute(
                    """UPDATE products
                       SET active=0,updated_at=?
                       WHERE id=?""",
                    (now, ghost_id),
                )
                if _exists(con, "system_hidden_products"):
                    con.execute(
                        """INSERT INTO system_hidden_products(product_id,reason,hidden_at)
                           VALUES(?,?,?)
                           ON CONFLICT(product_id) DO UPDATE SET
                             reason=excluded.reason, hidden_at=excluded.hidden_at""",
                        (ghost_id, "obsolete_wrong_master", now),
                    )

            con.commit()
        except Exception:
            con.rollback()
            raise

    return {
        "ok": True,
        "status": "repaired",
        "target_product_id": target_id,
        "ghost_product_id": ghost_id,
        "moved": moved,
    }
