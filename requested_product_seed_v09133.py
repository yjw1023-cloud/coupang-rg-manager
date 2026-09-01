"""RG Manager requested Coupang finished-product/BOM seed.

The seed is idempotent and runs on app startup/rerun. v0.9.139 additionally
repairs the three blackout-blind BOMs after seeding, because their first temporary
raw rows were created at cost 0 before the later purchase Excel created/updated
the actual purchased JDS rows.
"""
from __future__ import annotations

import re
from typing import Any


REQUESTS = [
    {
        "option_id": "95985636462",
        "finished_name": "탈부착 암막 시트지 잘라쓰는 블라인드 찍찍이 붙이는, 블랙 2m x 1.48m",
        "raw_name": "암막 블라인드 블랙 2m x 1.48m",
        "qty": 1.0,
        "aliases": ["암막 블라인드 2m", "암막 시트지 2m", "블랙 2m x 1.48m", "2m x 1.48m"],
        "tokens": ["암막", "2m"],
    },
    {
        "option_id": "95985636463",
        "finished_name": "탈부착 암막 시트지 잘라쓰는 블라인드 찍찍이 붙이는, 블랙 4m x 1.48m",
        "raw_name": "암막 블라인드 블랙 4m x 1.48m",
        "qty": 1.0,
        "aliases": ["암막 블라인드 4m", "암막 시트지 4m", "블랙 4m x 1.48m", "4m x 1.48m"],
        "tokens": ["암막", "4m"],
    },
    {
        "option_id": "95985636464",
        "finished_name": "탈부착 암막 시트지 잘라쓰는 블라인드 찍찍이 붙이는, 블랙 1m x 1.48m",
        "raw_name": "암막 블라인드 블랙 1m x 1.48m",
        "qty": 1.0,
        "aliases": ["암막 블라인드 1m", "암막 시트지 1m", "블랙 1m x 1.48m", "1m x 1.48m"],
        "tokens": ["암막", "1m"],
    },
    {
        "option_id": "95985697006",
        "finished_name": "욕실 미끄럼 방지 스티커 패드 테이프, 반투명 8cm 20개",
        "raw_name": "욕실 미끄럼 방지 스티커 반투명 8cm 20개",
        "qty": 1.0,
        "aliases": ["욕실 미끄럼 방지 스티커", "미끄럼 방지 스티커", "반투명 8cm 20개"],
        "tokens": ["미끄럼", "방지", "스티커"],
    },
    {
        "option_id": "95985756966",
        "finished_name": "미니 삼각대 휴대용 접이식 짐벌 액션캠 조명, Tripod 1개",
        "raw_name": "미니 삼각대",
        "qty": 1.0,
        "aliases": ["미니 삼각대", "미니삼각대", "Tripod"],
        "tokens": ["미니", "삼각대"],
    },
    {
        "option_id": "95985792307",
        "finished_name": "이쑤시개 디스펜서 원터치, 아이보리 1개",
        "raw_name": "이쑤시개 디스펜서 아이보리",
        "qty": 1.0,
        "aliases": ["이쑤시개 디스펜서", "이쑤시개통", "이쑤시개"],
        "tokens": ["이쑤시개"],
    },
    {
        "option_id": "95985864521",
        "finished_name": "스테인레스 청소 도구 대걸레 걸이 홀더 거치대, 은색 2개",
        "raw_name": None,
        "qty": 2.0,
        "aliases": [
            "스테인레스 청소 도구 걸이", "스테인레스 청소도구 걸이",
            "스테인리스 청소 도구 걸이", "스테인리스 청소도구 걸이",
            "스테인레스 청소도구 홀더", "스테인리스 청소도구 홀더",
            "스테인레스 대걸레 홀더", "스테인리스 대걸레 홀더",
            "대걸레 걸이", "대걸레 홀더", "밀대 걸이", "밀대 홀더",
            "청소 도구 걸이", "청소도구 걸이",
        ],
        "tokens": ["청소", "걸이"],
        "dormant_existing_only": True,
    },
    {
        "option_id": "95985900965",
        "finished_name": "바늘 실 끼우개 실꿰기 바늘귀, 실버 50개",
        "raw_name": "바늘 실 끼우개",
        "qty": 50.0,
        "aliases": ["바늘 실 끼우개", "바늘 실끼우개", "실 끼우개", "실끼우개"],
        "tokens": ["바늘", "끼우개"],
    },
    {
        "option_id": "95997140556",
        "finished_name": "카드형 돋보기 휴대용 확대경, 투명 2개",
        "raw_name": "카드형 돋보기",
        "qty": 1.0,
        "aliases": ["카드형 돋보기", "카드 돋보기", "휴대용 확대경"],
        "tokens": ["카드", "돋보기"],
    },
    {
        "option_id": "95995366301",
        "finished_name": "자동차 컵홀더 패드 깔개 실리콘, 2개 블랙",
        "raw_name": "자동차 컵홀더 패드 블랙 2개",
        "qty": 1.0,
        "aliases": ["컵홀더깔개", "컵홀더 깔개", "컵홀더 패드", "자동차 컵홀더"],
        "tokens": ["컵홀더"],
    },
]


def _norm(value: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(value or "").lower())


def _next_jds_code(con) -> str:
    rows = con.execute("SELECT item_code FROM products WHERE item_code IS NOT NULL").fetchall()
    used_numbers: set[int] = set()
    used_codes: set[str] = set()
    for row in rows:
        code = str(row["item_code"] or "").strip()
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


def _ensure_finished(core_module, con, req):
    oid = req["option_id"]
    name = req["finished_name"]
    now = core_module.now_iso()
    row = con.execute(
        """SELECT id,item_code,option_id,name,item_type,active FROM products
           WHERE CAST(option_id AS TEXT)=?
           ORDER BY CASE WHEN item_type='finished' THEN 0 ELSE 1 END,id LIMIT 1""",
        (oid,),
    ).fetchone()
    if row:
        pid = int(row["id"])
        con.execute(
            "UPDATE products SET item_code=?,option_id=?,name=?,item_type='finished',active=1,updated_at=? WHERE id=?",
            (f"CP-{oid}", oid, name, now, pid),
        )
        return pid, "existing"

    code = f"CP-{oid}"
    by_code = con.execute("SELECT id,option_id FROM products WHERE item_code=? ORDER BY id LIMIT 1", (code,)).fetchone()
    if by_code:
        existing_oid = str(by_code["option_id"] or "").strip()
        if existing_oid and existing_oid != oid:
            raise RuntimeError(f"{code}가 다른 옵션ID {existing_oid}에 이미 연결되어 있습니다.")
        pid = int(by_code["id"])
        con.execute(
            "UPDATE products SET option_id=?,name=?,item_type='finished',active=1,updated_at=? WHERE id=?",
            (oid, name, now, pid),
        )
        return pid, "reused"

    cur = con.execute(
        "INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at) VALUES(?,?,?,?,0,1,?)",
        (code, oid, name, "finished", now),
    )
    return int(cur.lastrowid), "created"


def _component_candidates(con, parent_id: int):
    return con.execute(
        """SELECT p.id,p.item_code,p.name,p.item_type,p.active,
                  COALESCE((SELECT SUM(t.qty_delta) FROM inventory_txns t
                            JOIN warehouses w ON w.id=t.warehouse_id
                            WHERE t.product_id=p.id AND w.name='자체창고'),0) AS own_stock,
                  EXISTS(SELECT 1 FROM inventory_txns t
                         JOIN warehouses w ON w.id=t.warehouse_id
                         WHERE t.product_id=p.id AND w.name='자체창고') AS own_history
           FROM products p WHERE p.id<>? AND p.option_id IS NULL ORDER BY p.id""",
        (int(parent_id),),
    ).fetchall()


def _score_candidate(row, req) -> int:
    name_n = _norm(row["name"])
    if not name_n:
        return -1
    score = 0
    exact = False
    for alias in req.get("aliases") or []:
        a = _norm(alias)
        if not a:
            continue
        if name_n == a:
            score = max(score, 420 + min(len(a), 80))
            exact = True
        elif len(a) >= 4 and (a in name_n or (len(name_n) >= 4 and name_n in a)):
            score = max(score, 260 + min(len(a), 80))
    for token in req.get("tokens") or []:
        t = _norm(token)
        if t and t in name_n:
            score += 35
    code = str(row["item_code"] or "").upper()
    if code.startswith("JDS"):
        score += 25
    if str(row["item_type"] or "") == "raw":
        score += 20
    if int(row["own_history"] or 0):
        score += 25
    if float(row["own_stock"] or 0) > 0:
        score += 35
    if req.get("dormant_existing_only"):
        if int(row["active"] or 0) == 0:
            score += 45
    elif int(row["active"] or 0) == 1:
        score += 10
    if not exact and score < 280:
        return -1
    return score


def _resolve_existing_component(con, parent_id: int, req):
    raw_name = str(req.get("raw_name") or "").strip()
    if raw_name:
        exact_rows = con.execute(
            """SELECT p.id,p.item_code,p.name,p.item_type,p.active,
                      COALESCE((SELECT SUM(t.qty_delta) FROM inventory_txns t
                                JOIN warehouses w ON w.id=t.warehouse_id
                                WHERE t.product_id=p.id AND w.name='자체창고'),0) AS own_stock,
                      EXISTS(SELECT 1 FROM inventory_txns t
                             JOIN warehouses w ON w.id=t.warehouse_id
                             WHERE t.product_id=p.id AND w.name='자체창고') AS own_history
               FROM products p WHERE p.id<>? AND p.option_id IS NULL AND p.name=?
               ORDER BY CASE WHEN p.item_type='raw' THEN 0 ELSE 1 END,
                        CASE WHEN p.active=1 THEN 0 ELSE 1 END,p.id""",
            (int(parent_id), raw_name),
        ).fetchall()
        if exact_rows:
            return exact_rows[0], "exact"

    ranked = []
    for row in _component_candidates(con, parent_id):
        score = _score_candidate(row, req)
        if score >= 0:
            ranked.append((score, int(row["id"]), row))
    if not ranked:
        return None, ""
    ranked.sort(key=lambda x: (-x[0], x[1]))
    if len(ranked) >= 2 and ranked[0][0] == ranked[1][0]:
        return None, "ambiguous"
    return ranked[0][2], "matched"


def _create_raw(core_module, con, raw_name: str):
    code = _next_jds_code(con)
    cur = con.execute(
        "INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at) VALUES(?,NULL,?,'raw',0,1,?)",
        (code, raw_name, core_module.now_iso()),
    )
    return {"id": int(cur.lastrowid), "item_code": code, "name": raw_name,
            "item_type": "raw", "active": 1, "own_stock": 0.0, "own_history": 0}


def _prepare_component(core_module, con, req, parent_id: int):
    component, how = _resolve_existing_component(con, parent_id, req)
    if component is None:
        if req.get("dormant_existing_only"):
            reason = "후보가 여러 개" if how == "ambiguous" else "일치하는 기존 불용재고 품목을 찾지 못함"
            return None, f"{reason}; 새 JDS 기초품목은 만들지 않았습니다."
        component = _create_raw(core_module, con, str(req["raw_name"]))
        return component, "created"
    if not req.get("dormant_existing_only"):
        con.execute("UPDATE products SET item_type='raw',active=1,updated_at=? WHERE id=?",
                    (core_module.now_iso(), int(component["id"])))
    return component, how or "matched"


def _add_exact_bom(core_module, db, parent_id: int, component, qty: float, preserve_component_state: bool):
    cid = int(component["id"])
    if int(parent_id) == cid:
        raise RuntimeError("완제품과 BOM 구성품이 동일합니다.")
    old_active = int(component["active"] or 0)
    old_type = str(component["item_type"] or "")
    with core_module._conn(db) as con:
        con.execute("DELETE FROM bom_items WHERE parent_product_id=?", (int(parent_id),))
        if preserve_component_state:
            con.execute("UPDATE products SET item_type='raw',active=1,updated_at=? WHERE id=?",
                        (core_module.now_iso(), cid))
    try:
        if hasattr(core_module, "add_bom"):
            try:
                core_module.add_bom(int(parent_id), cid, float(qty), db_path=db)
            except TypeError:
                core_module.add_bom(int(parent_id), cid, float(qty))
        else:
            with core_module._conn(db) as con:
                con.execute("INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)",
                            (int(parent_id), cid, float(qty)))
    finally:
        if preserve_component_state:
            with core_module._conn(db) as con:
                con.execute("UPDATE products SET item_type=?,active=?,updated_at=? WHERE id=?",
                            (old_type, old_active, core_module.now_iso(), cid))
    with core_module._conn(db) as con:
        rows = con.execute(
            """SELECT b.component_product_id,b.qty_per,c.item_code,c.name
               FROM bom_items b JOIN products c ON c.id=b.component_product_id
               WHERE b.parent_product_id=? ORDER BY b.rowid""",
            (int(parent_id),),
        ).fetchall()
    if len(rows) != 1:
        raise RuntimeError(f"BOM 저장 검증 실패: parent={parent_id}, rows={len(rows)}")
    row = rows[0]
    if int(row["component_product_id"]) != cid or abs(float(row["qty_per"] or 0) - float(qty)) > 1e-9:
        raise RuntimeError(f"BOM 저장 검증 실패: parent={parent_id}")
    return row


def apply(core_module, db_path=None):
    db = db_path or core_module.DEFAULT_DB
    core_module.init_db(db)
    result = {"finished": [], "bom": [], "unresolved": []}
    prepared = []

    with core_module._conn(db) as con:
        for req in REQUESTS:
            pid, pstatus = _ensure_finished(core_module, con, req)
            component, cstatus = _prepare_component(core_module, con, req, pid)
            result["finished"].append({
                "option_id": req["option_id"], "name": req["finished_name"],
                "product_id": pid, "status": pstatus,
            })
            if component is None:
                result["unresolved"].append({
                    "option_id": req["option_id"], "name": req["finished_name"], "reason": cstatus,
                })
                continue
            prepared.append((req, pid, dict(component), cstatus))

    for req, pid, component, cstatus in prepared:
        try:
            row = _add_exact_bom(core_module, db, pid, component, float(req["qty"]),
                                 preserve_component_state=bool(req.get("dormant_existing_only")))
            result["bom"].append({
                "option_id": req["option_id"], "name": req["finished_name"],
                "component_id": int(row["component_product_id"]),
                "component_code": str(row["item_code"] or ""),
                "component_name": str(row["name"] or ""),
                "qty": float(row["qty_per"] or 0), "component_status": cstatus,
                "dormant_reuse": bool(req.get("dormant_existing_only")),
            })
        except Exception as exc:
            result["unresolved"].append({
                "option_id": req["option_id"], "name": req["finished_name"],
                "reason": f"BOM 등록 실패: {exc}",
            })

    # v0.9.139: the seed above intentionally preserves the original idempotent
    # behavior, then corrects only the three blackout BOMs if a different JDS row
    # now owns real purchase history/cost. This must run AFTER the seed, otherwise
    # a later seed pass would reconnect the zero-cost temporary component.
    try:
        import blackout_bom_cost_repair_v09139 as _blackout_repair
        result["blackout_bom_cost_repair"] = _blackout_repair.apply(core_module, db)
    except Exception as exc:
        result["unresolved"].append({"reason": f"암막 BOM 원가 연결 복구 실패: {exc}"})

    result["ok"] = len(result["unresolved"]) == 0
    result["finished_count"] = len(result["finished"])
    result["bom_count"] = len(result["bom"])
    return result
