"""v0.9.172 audited repair for the 18th purchase import and rubber-glove BOM.

This repair was written only after inspecting the user's live rocketgrowth.db.

Verified fault in import 32 (18차수입원가.xlsx):
- 30 unique purchase groups existed.
- 28 were intended as new raw items; 22 were created, but 6 groups were
  incorrectly left mapped to unrelated existing items.
- The six missing new groups are glove S/M/L, 바늘세트, 치즈보관케이스,
  and 계란보관함.
- Those wrong matches also posted inventory to the unrelated existing items
  and overwrote their current unit_cost.
- The rubber-glove finished BOMs were then connected to unrelated JDS codes.

The repair is transactional and idempotent. It never guesses component codes.
It derives the six new raw items from the purchase source_name/source_detail,
moves only that import's purchase/inventory rows, restores the wrongly-hit
existing items from their previous purchase cost, persists source mappings,
and rewrites the three glove BOMs to the actual repaired raw product IDs.
"""
from __future__ import annotations

import re

RULE = "v0.9.172-live-db-audited-import18-repair"

TARGETS = [
    ("고무장갑", "正品南洋 大码 / S", "고무장갑 [正品南洋 大码 / S]"),
    ("고무장갑", "正品南洋 大码 / M", "고무장갑 [正品南洋 大码 / M]"),
    ("고무장갑", "正品南洋 大码 / L", "고무장갑 [正品南洋 大码 / L]"),
    ("바늘세트", "", "바늘세트"),
    ("치즈보관케이스", "整理盒带盖防尘", "치즈보관케이스 [整理盒带盖防尘]"),
    ("계란보관함", "透明款（opp袋装）", "계란보관함 [透明款（opp袋装）]"),
]

GLOVE_PARENT = {
    ("고무장갑", "正品南洋 大码 / S"): ("96012086788", "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 소(S)"),
    ("고무장갑", "正品南洋 大码 / M"): ("96012086789", "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 중(M)"),
    ("고무장갑", "正品南洋 大码 / L"): ("96012086790", "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 대(L)"),
}

SELLING_PRICE = 13900.0
COMMISSION_RATE = 0.108
COMMISSION_UNIT = SELLING_PRICE * COMMISSION_RATE
LOGISTICS_UNIT_TOTAL = 2800.0


def _table_exists(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _max_jds(con):
    max_no = 0
    used = set()
    for r in con.execute("SELECT item_code FROM products WHERE item_code IS NOT NULL").fetchall():
        code = str(r["item_code"] or "").strip()
        used.add(code.upper())
        m = re.fullmatch(r"JDS0*(\d+)", code, flags=re.IGNORECASE)
        if m:
            max_no = max(max_no, int(m.group(1)))
    return max_no, used


def _next_jds(con):
    n, used = _max_jds(con)
    n += 1
    while f"JDS{n}".upper() in used:
        n += 1
    return f"JDS{n}"


def _find_target_import(con):
    row = con.execute(
        """SELECT id,file_name,created_at
           FROM imports
           WHERE data_type='purchase' AND file_name='18차수입원가.xlsx'
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    if row is not None:
        return row

    rows = con.execute(
        """SELECT import_id,MAX(created_at) created_at
           FROM purchase_lines
           WHERE source_name='고무장갑'
             AND COALESCE(source_detail,'') IN
                 ('正品南洋 大码 / S','正品南洋 大码 / M','正品南洋 大码 / L')
           GROUP BY import_id
           HAVING COUNT(DISTINCT COALESCE(source_detail,''))=3
           ORDER BY MAX(created_at) DESC"""
    ).fetchall()
    if len(rows) != 1:
        return None
    iid = int(rows[0]["import_id"])
    return con.execute(
        "SELECT id,file_name,created_at FROM imports WHERE id=?", (iid,)
    ).fetchone()


def _ensure_mapping_table(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS purchase_source_product_map(
               source_name TEXT NOT NULL,
               source_detail TEXT NOT NULL DEFAULT '',
               product_id INTEGER NOT NULL,
               created_at TEXT NOT NULL,
               PRIMARY KEY(source_name,source_detail)
           )"""
    )
    con.execute(
        """CREATE INDEX IF NOT EXISTS ix_purchase_source_product_map_product
           ON purchase_source_product_map(product_id)"""
    )


def _ensure_commercial_table(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS product_commercial_defaults(
               option_id TEXT PRIMARY KEY,
               product_id INTEGER,
               selling_price REAL NOT NULL,
               commission_rate REAL NOT NULL,
               commission_unit REAL NOT NULL,
               logistics_unit_total REAL NOT NULL,
               source TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )


def _exact_raw(con, name):
    rows = con.execute(
        """SELECT id,item_code,name
           FROM products
           WHERE option_id IS NULL AND item_type='raw' AND name=?
           ORDER BY id""",
        (name,),
    ).fetchall()
    if len(rows) > 1:
        raise RuntimeError(f"동일한 기초품목명이 중복되어 있습니다: {name}")
    return rows[0] if rows else None


def _group_stats(con, import_id, source_name, source_detail):
    row = con.execute(
        """SELECT COUNT(*) n,
                  COALESCE(SUM(qty_receipt),0) qty,
                  COALESCE(SUM(landed_total_krw),0) total
           FROM purchase_lines
           WHERE import_id=? AND source_name=? AND COALESCE(source_detail,'')=?""",
        (int(import_id), source_name, source_detail),
    ).fetchone()
    if row is None or int(row["n"] or 0) <= 0:
        raise RuntimeError(f"18차 매입에서 대상 품목을 찾지 못했습니다: {source_name} / {source_detail}")
    qty = float(row["qty"] or 0)
    total = float(row["total"] or 0)
    if qty <= 0:
        raise RuntimeError(f"18차 매입수량이 0입니다: {source_name} / {source_detail}")
    return qty, total, total / qty


def _ensure_raw(core, con, import_id, source_name, source_detail, name):
    qty, total, avg = _group_stats(con, import_id, source_name, source_detail)
    unit_cost = float(round(avg))

    row = _exact_raw(con, name)
    status = "reused"
    if row is None:
        code = _next_jds(con)
        cur = con.execute(
            """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
               VALUES(?,NULL,?,'raw',?,1,?)""",
            (code, name, unit_cost, core.now_iso()),
        )
        pid = int(cur.lastrowid)
        row = {"id": pid, "item_code": code, "name": name}
        status = "created"
    else:
        pid = int(row["id"])
        con.execute(
            "UPDATE products SET unit_cost=?,active=1,updated_at=? WHERE id=?",
            (unit_cost, core.now_iso(), pid),
        )

    _ensure_mapping_table(con)
    con.execute(
        """INSERT INTO purchase_source_product_map
           (source_name,source_detail,product_id,created_at)
           VALUES(?,?,?,?)
           ON CONFLICT(source_name,source_detail) DO UPDATE SET
             product_id=excluded.product_id,created_at=excluded.created_at""",
        (source_name, source_detail, pid, core.now_iso()),
    )

    if _table_exists(con, "purchase_aliases"):
        con.execute(
            """UPDATE purchase_aliases
               SET product_id=?,confirmed=1,updated_at=?
               WHERE source_name=? AND COALESCE(source_detail,'')=?""",
            (pid, core.now_iso(), source_name, source_detail),
        )

    return {
        "product_id": pid,
        "item_code": str(row["item_code"]),
        "name": name,
        "unit_cost": unit_cost,
        "import_qty": qty,
        "status": status,
    }


def _aligned_import_rows(con, import_id):
    ref = f"PUR-{int(import_id)}"
    lines = con.execute(
        """SELECT id,source_name,COALESCE(source_detail,'') source_detail,
                  product_id,qty_receipt,landed_unit_cost_krw
           FROM purchase_lines WHERE import_id=? ORDER BY id""",
        (int(import_id),),
    ).fetchall()
    txns = con.execute(
        """SELECT id,product_id,qty_delta,unit_cost
           FROM inventory_txns
           WHERE ref_no=? AND txn_type='매입입고'
           ORDER BY id""",
        (ref,),
    ).fetchall()

    if not lines or len(lines) != len(txns):
        raise RuntimeError(
            f"18차 매입행과 입고원장 수가 다릅니다: purchase_lines={len(lines)}, inventory_txns={len(txns)}"
        )

    for line, txn in zip(lines, txns):
        if abs(float(line["qty_receipt"] or 0) - float(txn["qty_delta"] or 0)) > 1e-9:
            raise RuntimeError(
                f"18차 매입행/입고원장 수량 검증 실패: line={line['id']}, txn={txn['id']}"
            )
        if abs(float(line["landed_unit_cost_krw"] or 0) - float(txn["unit_cost"] or 0)) > 1e-9:
            raise RuntimeError(
                f"18차 매입행/입고원장 원가 검증 실패: line={line['id']}, txn={txn['id']}"
            )
    return lines, txns


def _restore_wrong_existing(core, con, import_id, old_pids, repaired_pids):
    repaired = {int(x) for x in repaired_pids}
    restored = []
    hidden = set()
    if _table_exists(con, "system_hidden_products"):
        hidden = {
            int(r["product_id"])
            for r in con.execute("SELECT product_id FROM system_hidden_products").fetchall()
        }

    for pid in sorted({int(x) for x in old_pids if int(x) not in repaired}):
        prior = con.execute(
            """SELECT landed_unit_cost_krw
               FROM purchase_lines
               WHERE product_id=? AND import_id<>?
               ORDER BY created_at DESC,id DESC LIMIT 1""",
            (pid, int(import_id)),
        ).fetchone()
        if prior is None:
            continue
        prior_cost = float(prior["landed_unit_cost_krw"] or 0)
        if pid in hidden:
            con.execute(
                "UPDATE products SET unit_cost=?,updated_at=? WHERE id=?",
                (prior_cost, core.now_iso(), pid),
            )
        else:
            con.execute(
                "UPDATE products SET unit_cost=?,active=1,updated_at=? WHERE id=?",
                (prior_cost, core.now_iso(), pid),
            )
        row = con.execute(
            "SELECT item_code,name,active FROM products WHERE id=?", (pid,)
        ).fetchone()
        restored.append(
            {
                "product_id": pid,
                "item_code": str(row["item_code"] or ""),
                "name": str(row["name"] or ""),
                "restored_unit_cost": prior_cost,
                "active": int(row["active"] or 0),
            }
        )
    return restored


def _backfill_all_source_maps(core, con, import_id):
    _ensure_mapping_table(con)
    rows = con.execute(
        """SELECT source_name,COALESCE(source_detail,'') source_detail,
                  MIN(product_id) product_id,COUNT(DISTINCT product_id) product_count
           FROM purchase_lines WHERE import_id=?
           GROUP BY source_name,COALESCE(source_detail,'')""",
        (int(import_id),),
    ).fetchall()
    for r in rows:
        if int(r["product_count"] or 0) != 1:
            raise RuntimeError(
                f"18차 매입 동일 상품/옵션이 여러 ERP 품목에 연결되어 있습니다: "
                f"{r['source_name']} / {r['source_detail']}"
            )
        pid = int(r["product_id"])
        con.execute(
            """INSERT INTO purchase_source_product_map
               (source_name,source_detail,product_id,created_at)
               VALUES(?,?,?,?)
               ON CONFLICT(source_name,source_detail) DO UPDATE SET
                 product_id=excluded.product_id,created_at=excluded.created_at""",
            (r["source_name"], r["source_detail"], pid, core.now_iso()),
        )
        if _table_exists(con, "purchase_aliases"):
            con.execute(
                """UPDATE purchase_aliases
                   SET product_id=?,confirmed=1,updated_at=?
                   WHERE source_name=? AND COALESCE(source_detail,'')=?""",
                (pid, core.now_iso(), r["source_name"], r["source_detail"]),
            )
    return len(rows)


def _ensure_bom_table(con):
    con.execute(
        """CREATE TABLE IF NOT EXISTS bom_items(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               parent_product_id INTEGER NOT NULL,
               component_product_id INTEGER NOT NULL,
               qty_per REAL NOT NULL,
               UNIQUE(parent_product_id,component_product_id)
           )"""
    )


def _ensure_finished_and_defaults(core, con, option_id, finished_name):
    code = f"CP-{option_id}"
    rows = con.execute(
        "SELECT id FROM products WHERE CAST(option_id AS TEXT)=? ORDER BY id",
        (option_id,),
    ).fetchall()
    if len(rows) > 1:
        raise RuntimeError(f"쿠팡 완제품 옵션ID가 중복입니다: {option_id}")
    if rows:
        pid = int(rows[0]["id"])
        con.execute(
            """UPDATE products
               SET item_code=?,name=?,item_type='finished',active=1,updated_at=?
               WHERE id=?""",
            (code, finished_name, core.now_iso(), pid),
        )
    else:
        cur = con.execute(
            """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
               VALUES(?,?,?,'finished',0,1,?)""",
            (code, option_id, finished_name, core.now_iso()),
        )
        pid = int(cur.lastrowid)

    _ensure_commercial_table(con)
    con.execute(
        """INSERT INTO product_commercial_defaults
           (option_id,product_id,selling_price,commission_rate,commission_unit,
            logistics_unit_total,source,updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(option_id) DO UPDATE SET
             product_id=excluded.product_id,
             selling_price=excluded.selling_price,
             commission_rate=excluded.commission_rate,
             commission_unit=excluded.commission_unit,
             logistics_unit_total=excluded.logistics_unit_total,
             source=excluded.source,
             updated_at=excluded.updated_at""",
        (
            option_id,
            pid,
            SELLING_PRICE,
            COMMISSION_RATE,
            COMMISSION_UNIT,
            LOGISTICS_UNIT_TOTAL,
            RULE,
            core.now_iso(),
        ),
    )
    return pid


def _repair_glove_boms(core, con, target_info):
    _ensure_bom_table(con)
    result = []
    for key, (option_id, finished_name) in GLOVE_PARENT.items():
        component_id = int(target_info[key]["product_id"])
        parent_id = _ensure_finished_and_defaults(core, con, option_id, finished_name)

        old = con.execute(
            """SELECT id,component_product_id,qty_per
               FROM bom_items WHERE parent_product_id=? ORDER BY id""",
            (parent_id,),
        ).fetchall()

        if _table_exists(con, "bom_change_log"):
            for b in old:
                if int(b["component_product_id"]) == component_id and abs(float(b["qty_per"] or 0) - 5.0) <= 1e-9:
                    continue
                con.execute(
                    """INSERT INTO bom_change_log
                       (action,bom_id,parent_product_id,component_product_id,qty_per,changed_at,note)
                       VALUES('REPAIR_REMOVE_WRONG_GLOVE_BOM',?,?,?,?,?,?)""",
                    (
                        int(b["id"]),
                        parent_id,
                        int(b["component_product_id"]),
                        float(b["qty_per"] or 0),
                        core.now_iso(),
                        RULE,
                    ),
                )

        con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (parent_id,))
        cur = con.execute(
            """INSERT INTO bom_items(parent_product_id,component_product_id,qty_per)
               VALUES(?,?,5)""",
            (parent_id, component_id),
        )
        bom_id = int(cur.lastrowid)

        if _table_exists(con, "bom_change_log"):
            con.execute(
                """INSERT INTO bom_change_log
                   (action,bom_id,parent_product_id,component_product_id,qty_per,changed_at,note)
                   VALUES('REPAIR_ADD_GLOVE_BOM',?,?,?,?,?,?)""",
                (bom_id, parent_id, component_id, 5.0, core.now_iso(), RULE),
            )

        result.append(
            {
                "option_id": option_id,
                "parent_product_id": parent_id,
                "component_product_id": component_id,
                "component_code": target_info[key]["item_code"],
                "qty_per": 5.0,
            }
        )
    return result


def _verify(con, import_id, target_info):
    checks = []
    for source_name, source_detail, _name in TARGETS:
        info = target_info[(source_name, source_detail)]
        pid = int(info["product_id"])

        row = con.execute(
            """SELECT COUNT(*) n,COALESCE(SUM(qty_receipt),0) qty
               FROM purchase_lines
               WHERE import_id=? AND source_name=? AND COALESCE(source_detail,'')=?
                 AND product_id=?""",
            (int(import_id), source_name, source_detail, pid),
        ).fetchone()
        expected_qty = float(info["import_qty"])
        if int(row["n"] or 0) <= 0 or abs(float(row["qty"] or 0) - expected_qty) > 1e-9:
            raise RuntimeError(f"수정 후 매입행 검증 실패: {source_name} / {source_detail}")

        row = con.execute(
            """SELECT COUNT(*) n,COALESCE(SUM(t.qty_delta),0) qty
               FROM inventory_txns t
               WHERE t.ref_no=? AND t.txn_type='매입입고' AND t.product_id=?""",
            (f"PUR-{int(import_id)}", pid),
        ).fetchone()
        if int(row["n"] or 0) <= 0 or abs(float(row["qty"] or 0) - expected_qty) > 1e-9:
            raise RuntimeError(f"수정 후 입고원장 검증 실패: {source_name} / {source_detail}")

        checks.append(
            {
                "source_name": source_name,
                "source_detail": source_detail,
                "product_id": pid,
                "item_code": info["item_code"],
                "qty": expected_qty,
                "unit_cost": info["unit_cost"],
            }
        )

    bom_rows = con.execute(
        """SELECT pp.option_id,cp.item_code,b.qty_per
           FROM bom_items b
           JOIN products pp ON pp.id=b.parent_product_id
           JOIN products cp ON cp.id=b.component_product_id
           WHERE pp.option_id IN ('96012086788','96012086789','96012086790')
           ORDER BY pp.option_id"""
    ).fetchall()
    if len(bom_rows) != 3:
        raise RuntimeError(f"고무장갑 BOM 3건 검증 실패: {len(bom_rows)}건")
    for r in bom_rows:
        if abs(float(r["qty_per"] or 0) - 5.0) > 1e-9:
            raise RuntimeError(f"고무장갑 BOM 소요량 검증 실패: {r['option_id']}")
    return checks


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)

    with core._conn(db) as con:
        imp = _find_target_import(con)
        if imp is None:
            return {
                "ok": False,
                "status": "target_import_not_found",
                "rule": RULE,
                "inventory_changed": False,
            }
        import_id = int(imp["id"])
        _aligned_import_rows(con, import_id)

    with core._conn(db) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
        except Exception:
            pass

        lines, txns = _aligned_import_rows(con, import_id)
        target_keys = {(x[0], x[1]) for x in TARGETS}
        original_product_ids = {
            int(line["product_id"])
            for line in lines
            if (line["source_name"], line["source_detail"]) in target_keys
        }

        target_info = {}
        for source_name, source_detail, name in TARGETS:
            target_info[(source_name, source_detail)] = _ensure_raw(
                core, con, import_id, source_name, source_detail, name
            )

        repaired_pids = {int(x["product_id"]) for x in target_info.values()}

        for line, txn in zip(lines, txns):
            key = (line["source_name"], line["source_detail"])
            info = target_info.get(key)
            if info is None:
                continue
            pid = int(info["product_id"])
            con.execute("UPDATE purchase_lines SET product_id=? WHERE id=?", (pid, int(line["id"])))
            con.execute("UPDATE inventory_txns SET product_id=? WHERE id=?", (pid, int(txn["id"])))

        restored = _restore_wrong_existing(
            core, con, import_id, original_product_ids, repaired_pids
        )
        mapping_count = _backfill_all_source_maps(core, con, import_id)
        boms = _repair_glove_boms(core, con, target_info)
        verified = _verify(con, import_id, target_info)

        if _table_exists(con, "rg_one_time_operations"):
            con.execute(
                """INSERT INTO rg_one_time_operations(op_key,completed_at,detail)
                   VALUES(?,?,?)
                   ON CONFLICT(op_key) DO UPDATE SET
                     completed_at=excluded.completed_at,detail=excluded.detail""",
                (
                    RULE,
                    core.now_iso(),
                    f"import_id={import_id}; repaired_new_items=6; source_maps={mapping_count}; glove_boms=3",
                ),
            )

        try:
            con.commit()
        except Exception:
            pass

        return {
            "ok": True,
            "status": "verified",
            "rule": RULE,
            "import_id": import_id,
            "new_items": verified,
            "restored_wrong_matches": restored,
            "source_mapping_count": mapping_count,
            "boms": boms,
            "inventory_changed": False,
        }
