"""v0.9.296 one-time/idempotent XL rubber glove finished-product seed.

Registers only the finished RG product. Raw material/BOM are intentionally left
unset because the XL raw material has not been imported yet.
"""
from __future__ import annotations

OPTION_ID = "96150451576"
ITEM_CODE = "CP-96150451576"
NAME = "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 특대(XL)"


def apply(core):
    core.init_db(core.DEFAULT_DB)
    now = core.now_iso()
    with core._conn(core.DEFAULT_DB) as con:
        existing = con.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products WHERE CAST(option_id AS TEXT)=? OR item_code=?""",
            (OPTION_ID, ITEM_CODE),
        ).fetchone()
        if existing:
            return {"status":"exists","product":dict(existing)}

        backup_mod = __import__("db_backup_v09248")
        backup = backup_mod.backup_db(core, "rubber_glove_xl_finished_seed", core.DEFAULT_DB)

        con.execute("BEGIN IMMEDIATE")
        try:
            cur = con.execute(
                """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
                   VALUES(?,?,?,'finished',0,1,?)""",
                (ITEM_CODE, OPTION_ID, NAME, now),
            )
            pid = int(cur.lastrowid)
            con.commit()
        except Exception:
            con.rollback()
            raise

    return {
        "status":"created",
        "backup":backup,
        "product":{
            "id":pid,
            "item_code":ITEM_CODE,
            "option_id":OPTION_ID,
            "name":NAME,
            "item_type":"finished",
            "unit_cost":0,
            "active":1,
        },
        "bom":"not_created_raw_material_not_imported",
    }
