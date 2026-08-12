"""RG Manager v0.9.70 one-time/idempotent registration for user-requested products.

Registers three Coupang RG finished products from the supplied option IDs and,
for each, a same-name own-warehouse/raw item with the next unused JDS#### code.

The routine is intentionally idempotent:
- existing finished option IDs are never duplicated;
- existing same-name raw items are never duplicated;
- archived matching rows are reactivated;
- existing unit costs and inventory are not changed;
- no inventory quantity is created by product registration.
"""
from __future__ import annotations

import re


REQUESTED_PRODUCTS = [
    (
        "95912623408",
        "어항용 뜰채 플라스틱 2p 수족관 새우 베타 구피, Free 2개",
    ),
    (
        "95912717676",
        "프로 야구 포토카드 앨범 바인더, 화이트 50매",
    ),
    (
        "95912816721",
        "대형 견출지 라벨 스티커 300장 라벨지, 혼합 300개입 1개",
    ),
]


def _next_jds_code(con) -> str:
    rows = con.execute(
        "SELECT item_code FROM products WHERE item_code IS NOT NULL"
    ).fetchall()
    used_numbers = set()
    used_codes = set()
    for r in rows:
        code = str(r["item_code"] or "").strip()
        if not code:
            continue
        used_codes.add(code.upper())
        m = re.fullmatch(r"JDS(\d+)", code, flags=re.IGNORECASE)
        if m:
            n = int(m.group(1))
            if 1 <= n <= 9999:
                used_numbers.add(n)
    for n in range(1, 10000):
        code = f"JDS{n:04d}"
        if n not in used_numbers and code.upper() not in used_codes:
            return code
    raise RuntimeError("JDS0001~JDS9999 품목코드를 모두 사용 중입니다.")


def apply(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    now = core_module.now_iso()
    result = {"finished": [], "raw": []}

    with core_module._conn(db) as con:
        for option_id, name in REQUESTED_PRODUCTS:
            # 1) Coupang RG finished product: visible code is the option ID; the
            # canonical DB item_code remains CP-<option_id> to preserve existing
            # ERP conventions and user-facing CP-prefix hiding.
            row = con.execute(
                "SELECT id,item_code,option_id,name,item_type,active FROM products WHERE option_id=? ORDER BY id LIMIT 1",
                (option_id,),
            ).fetchone()

            if row:
                pid = int(row["id"])
                if int(row["active"] or 0) != 1:
                    con.execute(
                        "UPDATE products SET active=1,updated_at=? WHERE id=?",
                        (now, pid),
                    )
                result["finished"].append(
                    {"option_id": option_id, "name": str(row["name"] or name), "id": pid, "status": "existing"}
                )
            else:
                canonical_code = f"CP-{option_id}"
                by_code = con.execute(
                    "SELECT id,option_id,name,active FROM products WHERE item_code=? ORDER BY id LIMIT 1",
                    (canonical_code,),
                ).fetchone()
                if by_code:
                    pid = int(by_code["id"])
                    existing_oid = str(by_code["option_id"] or "").strip()
                    if existing_oid and existing_oid != option_id:
                        raise ValueError(
                            f"품목코드 {canonical_code}가 다른 옵션ID {existing_oid}에 이미 연결되어 있습니다."
                        )
                    con.execute(
                        "UPDATE products SET option_id=?,item_type='finished',active=1,updated_at=? WHERE id=?",
                        (option_id, now, pid),
                    )
                    result["finished"].append(
                        {"option_id": option_id, "name": str(by_code["name"] or name), "id": pid, "status": "reused"}
                    )
                else:
                    cur = con.execute(
                        """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
                           VALUES(?,?,?,?,0,1,?)""",
                        (canonical_code, option_id, name, "finished", now),
                    )
                    pid = int(cur.lastrowid)
                    result["finished"].append(
                        {"option_id": option_id, "name": name, "id": pid, "status": "created"}
                    )

            # 2) Same-name own-warehouse/raw item with a permanently unique JDS code.
            raw = con.execute(
                """SELECT id,item_code,name,active
                   FROM products
                   WHERE option_id IS NULL AND name=?
                   ORDER BY CASE WHEN item_type='raw' THEN 0 ELSE 1 END,id
                   LIMIT 1""",
                (name,),
            ).fetchone()
            if raw:
                raw_id = int(raw["id"])
                if int(raw["active"] or 0) != 1:
                    con.execute(
                        "UPDATE products SET active=1,item_type='raw',updated_at=? WHERE id=?",
                        (now, raw_id),
                    )
                result["raw"].append(
                    {"item_code": str(raw["item_code"] or ""), "name": name, "id": raw_id, "status": "existing"}
                )
            else:
                jds = _next_jds_code(con)
                cur = con.execute(
                    """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
                       VALUES(?,NULL,?,'raw',0,1,?)""",
                    (jds, name, now),
                )
                raw_id = int(cur.lastrowid)
                result["raw"].append(
                    {"item_code": jds, "name": name, "id": raw_id, "status": "created"}
                )

    return result
