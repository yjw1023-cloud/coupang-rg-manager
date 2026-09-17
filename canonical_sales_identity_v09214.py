"""RG Manager v0.9.214 canonical sales identity and current normal-option registry.

Authoritative source: user's 2026-09-17 RG inbound workbook.
- non-yellow rows = current normal products
- yellow rows = discontinued/hidden products (kept in DB for history)
Returned-item resale aliases are never independent ERP SKUs: they resolve to the
original product for user-facing sales analysis, while their actual resale revenue
remains preserved by the existing return-sale accounting framework.
"""
from __future__ import annotations

import math
from typing import Any

ACTIVE_NORMAL_PRODUCTS = [('96032176717', '미니 USB LED 라이트 무드등 독서등 램프', '투명'), ('96032069225', '휴대용 계란 보관함 2구 달걀 케이스', '반투명 1개 Free'), ('96032025464', '치즈 보관 케이스 보관함 보관용기 소스', '2개 1개'), ('96031958640', '카메라 렌즈 클리닝 세트 에어블로워 펌프', '1개 JD'), ('96031916854', '면도기 거치대 홀더 걸이 커버형 무타공', '그레이 1개'), ('96031868051', '마우스 손목 쿠션 받침대 팔목', '1개 블랙'), ('96031828212', '휴대폰 먼지 마개 c타입 충전 단자 보호캡', '아이보리+브라운 1세트'), ('96031753512', 'HDMI 암암 연장 젠더 4k 암젠더 케이블 F to F', '2개 JD'), ('96031662095', '미니 안경 수리 드라이버 시계 초소형 휴대용 나사 조임', '2개'), ('96031591807', '유심칩핀 유심핀 핸드폰 유심빼는핀', '오렌지+옐로 1세트'), ('96031499411', '얇은 리본끈 세트 포장 5mm 풍선', '1세트 10m 10종'), ('96031454711', '빨대 텀블러세척솔 세트 빨대솔 브러쉬 텀블러빨대 10종 세트', '1세트 실버'), ('96031403084', '야채 세척솔 브러쉬 당근 감자 전복 채소', '2개 오렌지'), ('96031368439', '손 바느질 바늘세트 큰바늘 가정용', '55개 Free'), ('96012086788', '두툼한 작업용 고무장갑 두꺼운', '5세트 노랑 소(S)'), ('96012086789', '두툼한 작업용 고무장갑 두꺼운', '5세트 노랑 중(M)'), ('96012086790', '두툼한 작업용 고무장갑 두꺼운', '5세트 노랑 대(L)'), ('95995366301', '자동차 컵홀더 패드 깔개 실리콘', '2개 블랙'), ('95997140556', '카드형 돋보기 휴대용 확대경', '2개 투명'), ('95985900965', '바늘 실 끼우개 실꿰기 바늘귀', '50개 실버'), ('95985864521', '스테인레스 청소 도구 대걸레 걸이 홀더 거치대', '2개 은색'), ('95985792307', '이쑤시개 디스펜서 원터치', '아이보리 1개'), ('95985756966', '미니 삼각대 휴대용 접이식 짐벌 액션캠 조명', 'Tripod 1개'), ('95985697006', '욕실 미끄럼 방지 스티커 패드 테이프', '20개 반투명 8cm'), ('95985636464', '탈부착 암막 시트지 잘라쓰는 블라인드 찍찍이 붙이는', '블랙 1m x 1.48m'), ('95985636462', '탈부착 암막 시트지 잘라쓰는 블라인드 찍찍이 붙이는', '블랙 2m x 1.48m'), ('95985636463', '탈부착 암막 시트지 잘라쓰는 블라인드 찍찍이 붙이는', '블랙 4m x 1.48m'), ('95912816721', '대형 견출지 라벨 스티커 300장 라벨지', '혼합 300개입 1개'), ('95912717676', '프로 야구 포토카드 앨범 바인더', '화이트 50매'), ('95912623408', '어항용 뜰채 플라스틱 2p 수족관 새우 베타 구피', '2개 Free'), ('95861208739', 'LED 공부 시계 타이머 다이얼 무소음 스톱워치', '1개 화이트 시리즈'), ('95861208738', 'LED 공부 시계 타이머 다이얼 무소음 스톱워치', '1개 블랙 시리즈'), ('95849578032', '대치동 필통 펜트레이 펜케이스', '1개 블랙'), ('95849578033', '대치동 필통 펜트레이 펜케이스', '1개 그레이'), ('95834379201', '보조거울 백미러 사이드미러 2p 보조미러', '2개 전차종'), ('95828314407', '남자 소가죽 벨트 허리띠 진짜 가죽 혁대', 'Free 블랙/다크브라운'), ('95648063867', '스텝 드릴 비트 세트 육각 구멍뚫기 파우치', '1세트'), ('95631138188', '휴대용 에어 방석 비행기', '그레이 Free'), ('95631138189', '휴대용 에어 방석 비행기', '카키그린 Free'), ('95612444686', '고급 반짇고리 바느질 미니 키트 세트', '1개 와인레드'), ('95251584939', '목운동 하네스 헬스 체인', '1개 블랙 FREE'), ('95251268814', '석재용 석공 끌 납작끝', '1개'), ('94948167737', '차량용 점프 케이블 3000A 고용량', '1개 5m'), ('94947803716', '고급 농구 골대 그물망 5p 두꺼운 5mm', '5개 화이트 one size'), ('94605540426', '물림방지 훈련 장갑 개 강아지 고양이 양손', '다크그린 1세트'), ('94481130957', '실삔 실핀 5.7cm 대용량 500g 약400p', '블랙'), ('94481093156', '옷핀 38mm 안전핀 1000p 대용량', '1000개 실버'), ('94481001229', '고급 비접촉 검전기 전압 테스터', '1개'), ('94475502655', '프라모델 도색용 집게 20p 악어클립', '20개 우드 Free'), ('94475454519', '글라스 네일 파일 5p 유리 손톱 샤이너', '5개 투명'), ('94475426058', '응원용 짝짝이 클래퍼 20p', '20개 랜덤 Free'), ('94387597514', '이발기 바리깡솔 청소솔', '10개 블루'), ('94351514099', '뷰러 리필용 고무 24p 2set 속눈썹', '2세트 블랙'), ('94351150317', '글러브 길들이기 밴드 2p 야구', '2개 0.2kg'), ('94351031953', '목재 각인 열쇠고리 10p 키링 우드', '우든 10개'), ('94350959619', '접이식 줄자 미니 휴대용 원터치 자동', '화이트 1개 1.5m'), ('94350863809', '차 커피 수납 정리함 보관함 탕비실', '1개 화이트'), ('94350806973', '실리콘 발목 보호대 2p 피겨 스케이트 가드 숏트랙', '2개 Free'), ('94350728580', '주방 실리콘 컵뚜껑 컵커버 덮개 먼지방지', '2개 그레이'), ('94350669182', '접이식 미니 빗 휴대용 거울 빗 세트', '2개 랜덤'), ('94350598714', '안경 파우치 케이스 휴대용 소프트', '2개 블랙'), ('94350521898', '손목 보호 마우스 쿠션 패드 메모리폼', '1개 블랙'), ('94350444179', '여행용 변기커버 위생 휴대용 1회용', '20개 화이트'), ('94350358280', '고양이 캣닢 박하 장난감', '1개 Free'), ('94350284412', '강아지 발톱깎이 반려동물 고양이', '1개 블루'), ('94350212237', '계란 보관 케이스 1구 휴대용', '2개 반투명'), ('94350151006', '셀카 조명 휴대폰 미니 라이트', '1개 화이트'), ('94350078271', '이어팁 실리콘 이어폰 캡', '10개 블랙'), ('94350003311', '염색 도구 세트 브러쉬 볼 귀마개', '1세트 블랙'), ('94125499117', '실리콘 병 세척솔 틈새 브러쉬', '2개 블루'), ('94103975794', '나뭇잎 점착식 메모지 떡메모지', '3개'), ('94185578349', '탁구공 수집기 수거기 탁구장', '1세트 은색'), ('94138655933', '배드민턴 라켓 가방 보관 가방 케이스', '단일상품'), ('94124510649', '용접용 각반 소가죽', '1개 Free'), ('94126073739', '엘리스 피크 두께6종 12PC 기타 케이스 우쿨렐레 용품', '단일상품')]
ARCHIVED_NORMAL_PRODUCTS = [('95594235700', '문콕 방지 가드 흡착식 테슬라 도어가드', '1개 블랙 Free'), ('95300185444', '작업용 방수 토시 PU 인조가죽 4p 2쌍', '검정 Free'), ('95300124056', '고급 원예용 전지 가위 가지치기', '1개'), ('95300023745', '목재용 마킹자 삼각자 직각자', '1개'), ('95299992627', '바베큐 내열 장갑 열차단 캠핑', '그레이 1세트'), ('95299949406', '휴대용 스테인리스 구두주걱 2p', '2개 은색'), ('95299894489', '석쇠 걸개 4p 그릴 리프터', '4개 실버'), ('95265534972', '미니 스프레이 공병 향수 분무기', '2개 투명'), ('95251457883', '화분 분갈이 매트 방수 원예', '1개 그린'), ('95251380586', '미니 계산기 휴대용 사무용', '1개 화이트'), ('95190832227', '빨대 세척솔 소형 10p', '10개 화이트'), ('95140327852', '콘센트 소켓 청소 브러쉬', '2개 블랙'), ('95060856477', '자동차 우산 걸이 후크', '2개 블랙'), ('94948187475', '자동차 쓰레기통 미니 휴지통', '1개 블랙'), ('94845793700', 'Baby on board 차량용 스티커', '1개 화이트'), ('94758590295', 'AAA 건전지 보관함 케이스', '1개 투명'), ('94731669021', '주방 냄비장갑 캔버스 오븐장갑', '2개 남색'), ('94679965319', '샤워기 흡착 홀더 거치대', '1개 실버'), ('94566989635', '접이식 바가지 휴대용 대야', '1개 블루'), ('94533105408', '고데기 거치대 홀더', '1개 블랙'), ('94391011068', '타공판 후크 걸이 10p', '10개 화이트'), ('94285787287', '공예칼 아트 나이프 세트', '1세트 실버'), ('94272018620', '용돈봉투 편지봉투 세트', '10개 화이트'), ('94138635141', 'A4 자석 집게 클립', '2개 실버'), ('94138679981', '핸드메이드 스티커 라벨', '100개 크라프트'), ('95615771344', '3색 보드마카 세트', '3개 혼합'), ('95697280722', '조리형 비닐 선물 주머니', '50개 투명'), ('95749158342', '편지봉투 규격형', '50개 화이트'), ('95864153283', '생일 케이크 토퍼 장식', '1개 골드'), ('95373907752', '비늘 제거기 생선 손질 도구', '1개 실버'), ('95371029296', '스팀 다리미 옷 거치대', '1개 화이트'), ('95551289967', '실리콘 컵뚜껑 먼지 방지 커버', '2개 그레이'), ('95593762217', '온습도계 미니 디지털', '1개 화이트'), ('95644866786', '자석 어항 청소기', '1개 블랙'), ('95321950215', '고양이 박하 캣닢 볼', '1개 그린'), ('94533191240', '자동차 컵홀더 쓰레기통', '1개 블랙'), ('94566899000', '여행용 수납 파우치', '1개 그레이'), ('94566911221', '미니 셀카봉 삼각대', '1개 블랙'), ('94566933117', '주방 병 세척솔', '2개 블루'), ('94566944231', '강아지 발 세척컵', '1개 블루'), ('94566955776', '실리콘 냄비 받침', '2개 그레이'), ('94566968012', '주방 배수구 거름망', '100개 화이트'), ('94566979444', '신발 세척 브러쉬', '2개 블루'), ('94566990881', '화장품 공병 세트', '5개 투명'), ('94567002115', '욕실 면도기 홀더', '2개 화이트'), ('94567013552', '미니 구두솔', '2개 브라운'), ('94567024989', '자동차 안전벨트 커버', '2개 블랙'), ('94567036426', '휴대용 손거울', '1개 화이트'), ('94567047863', '주방 싱크대 스펀지 홀더', '1개 실버'), ('94567059300', '욕실 칫솔 홀더', '2개 화이트'), ('94567070737', '실리콘 케이블 정리 클립', '10개 블랙'), ('94567082174', '냉장고 자석 후크', '10개 실버'), ('94567093611', '책상 케이블 홀더', '5개 블랙'), ('94567105048', '욕실 배수구 머리카락 거름망', '10개 화이트'), ('94567116485', '주방 오일 브러쉬', '2개 실리콘'), ('94567127922', '휴대용 약통 케이스', '1개 화이트'), ('94567139359', '미니 재봉 키트', '1세트 레드'), ('94567150796', '자동차 선글라스 클립', '2개 블랙'), ('94567162233', '휴대용 옷걸이 접이식', '2개 블루')]

ACTIVE_OPTION_IDS = {x[0] for x in ACTIVE_NORMAL_PRODUCTS}
ARCHIVED_OPTION_IDS = {x[0] for x in ARCHIVED_NORMAL_PRODUCTS}
ALL_NORMAL_OPTION_IDS = ACTIVE_OPTION_IDS | ARCHIVED_OPTION_IDS


def _oid(value: Any) -> str:
    if value is None:
        return ""
    try:
        x = float(value)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(value or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _exists(con, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(con, table: str) -> set[str]:
    if not _exists(con, table):
        return set()
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _ensure_column(con, table: str, name: str, decl: str) -> None:
    if name not in _cols(con, table):
        con.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {decl}')


def ensure_schema(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)
    with core._conn(db) as con:
        con.execute("""CREATE TABLE IF NOT EXISTS coupang_normal_option_registry(
            vendor_item_id TEXT PRIMARY KEY, product_name TEXT, option_name TEXT,
            active INTEGER NOT NULL DEFAULT 1, source TEXT, updated_at TEXT)""")
        for name, decl in (("product_name", "TEXT"), ("option_name", "TEXT"), ("active", "INTEGER NOT NULL DEFAULT 1"), ("source", "TEXT"), ("updated_at", "TEXT")):
            _ensure_column(con, "coupang_normal_option_registry", name, decl)
        con.execute("""CREATE TABLE IF NOT EXISTS system_hidden_products(
            product_id INTEGER PRIMARY KEY, reason TEXT NOT NULL, hidden_at TEXT NOT NULL)""")
    return db


def seed_registry(core, db=None) -> dict[str, Any]:
    db = ensure_schema(core, db)
    now = core.now_iso()
    try:
        import product_visibility_v0995 as visibility
        known = getattr(visibility, "KNOWN_RETURN_OPTION_IDS", None)
        if isinstance(known, set):
            known.difference_update(ACTIVE_OPTION_IDS)
    except Exception:
        pass
    activated = hidden = 0
    with core._conn(db) as con:
        for oid, name, option_name in ACTIVE_NORMAL_PRODUCTS + ARCHIVED_NORMAL_PRODUCTS:
            active = 1 if oid in ACTIVE_OPTION_IDS else 0
            con.execute("""INSERT INTO coupang_normal_option_registry
                (vendor_item_id,product_name,option_name,active,source,updated_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(vendor_item_id) DO UPDATE SET
                product_name=excluded.product_name,option_name=excluded.option_name,
                active=excluded.active,source=excluded.source,updated_at=excluded.updated_at""",
                (oid, name, option_name, active, "user_rg_inbound_2026-09-17", now))
        if _exists(con, "products") and "option_id" in _cols(con, "products"):
            cols = _cols(con, "products")
            for row in con.execute("SELECT id,option_id FROM products").fetchall():
                pid, oid = int(row["id"]), _oid(row["option_id"])
                if oid in ACTIVE_OPTION_IDS:
                    if "active" in cols: con.execute("UPDATE products SET active=1 WHERE id=?", (pid,))
                    con.execute("DELETE FROM system_hidden_products WHERE product_id=?", (pid,)); activated += 1
                elif oid in ARCHIVED_OPTION_IDS:
                    if "active" in cols: con.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
                    con.execute("""INSERT INTO system_hidden_products(product_id,reason,hidden_at) VALUES(?,?,?)
                        ON CONFLICT(product_id) DO UPDATE SET reason=excluded.reason,hidden_at=excluded.hidden_at""",
                        (pid, "user_discontinued_2026-09-17", now)); hidden += 1
    return {"ok": True, "normal_options": len(ALL_NORMAL_OPTION_IDS), "active_options": len(ACTIVE_OPTION_IDS), "archived_options": len(ARCHIVED_OPTION_IDS), "activated_product_rows": activated, "hidden_product_rows": hidden}


def identity_maps_from_con(con) -> dict[str, Any]:
    products, by_oid = {}, {}
    if _exists(con, "products"):
        cols = _cols(con, "products")
        fields = ["id", "option_id" if "option_id" in cols else "'' AS option_id", "name" if "name" in cols else "'' AS name", "item_code" if "item_code" in cols else "'' AS item_code", "active" if "active" in cols else "1 AS active"]
        for r in con.execute("SELECT " + ",".join(fields) + " FROM products").fetchall():
            pid, oid = int(r["id"]), _oid(r["option_id"])
            meta = {"product_id": pid, "option_id": oid, "name": str(r["name"] or ""), "item_code": str(r["item_code"] or ""), "active": int(r["active"] or 0)}
            products[pid] = meta
            old = by_oid.get(oid)
            if oid and (old is None or (meta["active"], pid) > (old["active"], old["product_id"])): by_oid[oid] = meta
    aliases = {}
    if _exists(con, "return_discount_aliases") and {"discount_option_id", "parent_product_id"}.issubset(_cols(con, "return_discount_aliases")):
        for r in con.execute("SELECT discount_option_id,parent_product_id FROM return_discount_aliases").fetchall():
            oid = _oid(r["discount_option_id"])
            if oid: aliases[oid] = int(r["parent_product_id"])
    child_map = {}
    if _exists(con, "return_discount_sales") and {"child_product_id", "parent_product_id"}.issubset(_cols(con, "return_discount_sales")):
        for r in con.execute("SELECT child_product_id,parent_product_id FROM return_discount_sales WHERE child_product_id IS NOT NULL").fetchall(): child_map[int(r["child_product_id"])] = int(r["parent_product_id"])
    for oid, parent in aliases.items():
        if oid in by_oid: child_map[int(by_oid[oid]["product_id"])] = int(parent)
    return {"products": products, "by_oid": by_oid, "alias_oid_to_parent": aliases, "child_pid_to_parent": child_map}


def resolve_from_maps(maps: dict[str, Any], product_id: Any = None, option_id: Any = None) -> dict[str, Any]:
    try: pid = int(float(product_id or 0))
    except Exception: pid = 0
    oid = _oid(option_id); products = maps.get("products", {}); by_oid = maps.get("by_oid", {}); aliases = maps.get("alias_oid_to_parent", {}); child_map = maps.get("child_pid_to_parent", {})
    is_alias = False; parent_pid = 0
    if oid and oid in aliases: parent_pid = int(aliases[oid]); is_alias = True
    elif pid > 0 and pid in child_map: parent_pid = int(child_map[pid]); is_alias = True
    elif oid in ALL_NORMAL_OPTION_IDS and oid in by_oid: parent_pid = int(by_oid[oid]["product_id"])
    elif pid > 0: parent_pid = pid
    elif oid and oid in by_oid: parent_pid = int(by_oid[oid]["product_id"])
    meta = products.get(parent_pid, {}); parent_oid = _oid(meta.get("option_id")) if meta else ""
    if not parent_oid and oid and not is_alias: parent_oid = oid
    return {"product_id": parent_pid, "option_id": parent_oid, "name": str(meta.get("name") or ""), "item_code": str(meta.get("item_code") or ""), "is_return_alias": is_alias, "archived": bool(parent_oid and parent_oid in ARCHIVED_OPTION_IDS)}


def _patch_organic(core, organic_module):
    if organic_module is None or getattr(organic_module, "_rg_canonical_identity_v09214", False): return
    original_data, original_identity = organic_module._organic_estimate_data, organic_module._identity
    def canonical_identity(sales_module, master, option_to_pid, pid_value, oid_value):
        key, pid, oid = original_identity(sales_module, master, option_to_pid, pid_value, oid_value)
        ctx = getattr(organic_module, "_rg_canonical_context_v09214", None)
        if not ctx: return key, pid, oid
        r = resolve_from_maps(ctx, pid, oid)
        if r.get("archived"): return "", 0, ""
        cpid, coid = int(r.get("product_id") or 0), _oid(r.get("option_id") or oid)
        return (f"p:{cpid}", cpid, coid) if cpid > 0 else ((f"o:{coid}", 0, coid) if coid else ("", 0, ""))
    def canonical_data(core_obj, sales_module, db, start, end):
        with core_obj._conn(db) as con: ctx = identity_maps_from_con(con)
        prev = getattr(organic_module, "_rg_canonical_context_v09214", None); organic_module._rg_canonical_context_v09214 = ctx
        try: return original_data(core_obj, sales_module, db, start, end)
        finally: organic_module._rg_canonical_context_v09214 = prev
    organic_module._identity, organic_module._organic_estimate_data = canonical_identity, canonical_data
    organic_module._rg_canonical_identity_v09214 = True


def _canonicalize_sales_frame(core, db, frame):
    if frame is None or getattr(frame, "empty", True): return frame
    import pandas as pd
    with core._conn(db) as con: maps = identity_maps_from_con(con)
    rows = []
    for _, row in frame.iterrows():
        r = resolve_from_maps(maps, row.get("product_id", 0), row.get("옵션ID", row.get("option_id", "")))
        if r.get("archived"): continue
        item = row.to_dict(); cpid = int(r.get("product_id") or 0)
        if cpid > 0:
            item["product_id"] = cpid
            if "옵션ID" in item: item["옵션ID"] = r.get("option_id") or item.get("옵션ID", "")
            if "option_id" in item: item["option_id"] = r.get("option_id") or item.get("option_id", "")
            if "상품명" in item and r.get("name"): item["상품명"] = r["name"]
            if "상품코드" in item and r.get("item_code"): item["상품코드"] = r["item_code"]
        rows.append(item)
    if not rows: return frame.iloc[0:0].copy()
    out = pd.DataFrame(rows); keys = [c for c in ("product_id", "상품코드", "옵션ID", "상품명") if c in out.columns]
    if "product_id" not in keys: return out
    additive = [c for c in ("판매수량", "취소·반품수량", "실판매수량", "API주문건수", "API주문금액", "_api_sales_qty", "sales_qty", "cancel_qty", "net_qty") if c in out.columns]
    sources = [c for c in ("_source_api", "_source_stats") if c in out.columns]
    agg = {c: "sum" for c in additive}; agg.update({c: "max" for c in sources})
    for c in out.columns:
        if c not in keys and c not in agg: agg[c] = "first"
    return out.groupby(keys, as_index=False, dropna=False).agg(agg)


def _patch_sales_analysis(core, sales_module):
    if sales_module is None or getattr(sales_module, "_rg_canonical_identity_v09214", False): return
    orig_stats, orig_api = getattr(sales_module, "_sales_stats", None), getattr(sales_module, "_api_sales", None)
    if callable(orig_stats):
        def stats(core_obj, db, start, end):
            frame, covered = orig_stats(core_obj, db, start, end); return _canonicalize_sales_frame(core_obj, db, frame), covered
        sales_module._sales_stats = stats
    if callable(orig_api):
        def api(core_obj, db, start, end, allowed_days=None): return _canonicalize_sales_frame(core_obj, db, orig_api(core_obj, db, start, end, allowed_days))
        sales_module._api_sales = api
    sales_module._rg_canonical_identity_v09214 = True


def _patch_provisional_pnl(core, pnl_module):
    if pnl_module is None or getattr(pnl_module, "_rg_canonical_identity_v09214", False): return
    original = getattr(pnl_module, "_apply_existing_rules", None)
    if not callable(original): pnl_module._rg_canonical_identity_v09214 = True; return
    def wrapped(core_obj, db, data):
        out = original(core_obj, db, data)
        if out is None or getattr(out, "empty", True): return out
        with core_obj._conn(db) as con: maps = identity_maps_from_con(con)
        col = "옵션ID" if "옵션ID" in out.columns else ("쿠팡 옵션ID" if "쿠팡 옵션ID" in out.columns else None)
        if not col: return out
        drop = []
        for idx, row in out.iterrows():
            pid = row.get("product_id", 0) if "product_id" in out.columns else 0
            if resolve_from_maps(maps, pid, row.get(col, "")).get("is_return_alias"): drop.append(idx)
        return out.drop(index=drop).copy() if drop else out
    pnl_module._apply_existing_rules = wrapped; pnl_module._rg_canonical_identity_v09214 = True


def apply(core, db=None, sales_module=None, organic_module=None, pnl_module=None):
    db = db or core.DEFAULT_DB; seeded = seed_registry(core, db)
    if sales_module is None:
        try: import sales_analysis_v09186 as sales_module
        except Exception: sales_module = None
    if organic_module is None:
        try: import organic_sales_estimate_v09211 as organic_module
        except Exception: organic_module = None
    if pnl_module is None:
        try: import provisional_pnl_ui_v0913 as pnl_module
        except Exception: pnl_module = None
    _patch_sales_analysis(core, sales_module); _patch_organic(core, organic_module); _patch_provisional_pnl(core, pnl_module)
    return seeded
