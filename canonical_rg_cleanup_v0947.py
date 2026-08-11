"""RG Manager v0.9.47 canonical Rocket Growth product registry + return cleanup.

The user supplied the current Coupang Rocket Growth original-product option IDs
and full product names.  Treat these option IDs as authoritative originals for
return-sale matching.

Safety rules for automatic cleanup of an already-created foreign option row:
- never touch a canonical option ID itself;
- require an ACTIVE Coupang finished-product child with sales history;
- refuse rows with BOM, production, purchase, or non-sales inventory history;
- require exactly one canonical original with a very strong name match;
- exact normalized full-name matches may be cleaned directly because the supplied
  option ID list is authoritative;
- otherwise require the child realized selling price to be lower than the matched
  canonical original's historical normal selling price;
- convert existing ordinary sales postings through return_discount_v099, then
  archive the child product instead of deleting history.

Future sales imports also prioritize these canonical originals before falling back
to the generic v0.9.44 matcher.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

_APPLIED = False

# Authoritative original Rocket Growth option IDs from the user's Coupang item list.
# Names are kept close to Coupang full names; matching is punctuation/spacing tolerant.
CANONICAL_RG = {
    "95612444686": "고급 반짇고리 바느질 미니 키트 세트, 1개 와인레드",
    "95251584939": "목운동 하네스 헬스 체인, 1개 블랙 FREE",
    "94948167737": "차량용 점프 케이블 3000A 고용량, 1개 5m",
    "94189988104": "스텐 석쇠 집게 들게 숯불 구이 불판 교체 손잡이 업소용, 4개 Free",
    "94475426058": "응원용 짝짝이 클래퍼 20p, 20개 랜덤 Free",
    "94481093156": "옷핀 38mm 안전핀 1000p 대용량, 1000개 실버",
    "94387597514": "이발기 바리깡솔 청소솔, 10개 블루",
    "95631138188": "휴대용 에어 방석 비행기, 그레이 Free",
    "95631138189": "휴대용 에어 방석 비행기, 카키그린 Free",
    "94351150317": "글러브 길들이기 밴드 2p 야구, 2개 0.2kg",
    "94350361655": "자전거 비닐커버 5p 일회용 방수, 5개 반투명",
    "94138655933": "배드민턴 라켓 가방 보관 가방 케이스, 단일상품",
    "94121677686": "수납 지퍼백 여행용 악세사리 슬라이드 중형 수납백 반투명, 1세트 14x23cm 50매",
    "95828314407": "남자 소가죽 벨트 허리띠 진짜 가죽 확대, Free 블랙/다크브라운",
    "94475454519": "글라스 네일 파일 5p 유리 손톱 사이너, 5개 투명",
    "94350296878": "휴대용 가죽 구두주걱 미니 2p 스텐, 브라운 2개",
    "94124510649": "용접용 각반 소가죽, 1개 Free",
    "94605540426": "물림방지 훈련 장갑 개 강아지 고양이 양손, 다크그린 1세트",
}

_PACK_RE = re.compile(
    r"(?<![0-9])([0-9]+)\s*(?:개입|개|p|pcs?|세트|set)(?![a-z0-9가-힣])",
    re.I,
)


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _norm(name: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(name or "").lower())


def _pack(name: Any):
    vals = []
    for m in _PACK_RE.finditer(str(name or "").lower()):
        try:
            vals.append(int(m.group(1)))
        except Exception:
            pass
    return vals[0] if vals else None


def _score(a: Any, b: Any) -> float:
    aq, bq = _pack(a), _pack(b)
    if aq is not None and bq is not None and aq != bq:
        return 0.0
    aa, bb = _norm(a), _norm(b)
    if not aa or not bb:
        return 0.0
    short, long = (aa, bb) if len(aa) <= len(bb) else (bb, aa)
    score = SequenceMatcher(None, aa, bb).ratio()
    if len(short) >= 8 and short in long:
        score = max(score, 0.95)
    return score


def _exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _blockers(core, pid: int) -> list[str]:
    out = []
    with core._conn(core.DEFAULT_DB) as con:
        if _exists(con, "bom_items"):
            n = int(con.execute(
                "SELECT COUNT(*) n FROM bom_items WHERE parent_product_id=? OR component_product_id=?",
                (int(pid), int(pid)),
            ).fetchone()["n"] or 0)
            if n:
                out.append(f"BOM {n}")
        if _exists(con, "production_orders"):
            n = int(con.execute(
                "SELECT COUNT(*) n FROM production_orders WHERE parent_product_id=?",
                (int(pid),),
            ).fetchone()["n"] or 0)
            if n:
                out.append(f"생산 {n}")
        if _exists(con, "purchase_lines"):
            cols = {str(r["name"]) for r in con.execute("PRAGMA table_info(purchase_lines)")}
            if "product_id" in cols:
                n = int(con.execute(
                    "SELECT COUNT(*) n FROM purchase_lines WHERE product_id=?", (int(pid),)
                ).fetchone()["n"] or 0)
                if n:
                    out.append(f"매입 {n}")
        if _exists(con, "inventory_txns"):
            n = int(con.execute(
                """SELECT COUNT(*) n FROM inventory_txns
                   WHERE product_id=?
                     AND COALESCE(txn_type,'') NOT IN ('판매차감','반품할인판매차감')""",
                (int(pid),),
            ).fetchone()["n"] or 0)
            if n:
                out.append(f"판매외재고 {n}")
    return out


def _historical_price(rd, core, pid: int):
    amount_col = rd._amount_column(core, core.DEFAULT_DB)
    if not amount_col:
        return None
    try:
        with core._conn(core.DEFAULT_DB) as con:
            row = con.execute(
                f'''SELECT COALESCE(SUM(net_qty),0) qty,
                           COALESCE(SUM("{amount_col}"),0) amount
                    FROM sales_stats
                    WHERE product_id=? AND COALESCE(net_qty,0)>0''',
                (int(pid),),
            ).fetchone()
        qty = _num(row["qty"]) if row else 0.0
        amount = _num(row["amount"]) if row else 0.0
        return amount / qty if qty > 0 and amount > 0 else None
    except Exception:
        return None


def _sales_rows(rd, core, pid: int):
    amount_col = rd._amount_column(core, core.DEFAULT_DB)
    with core._conn(core.DEFAULT_DB) as con:
        if not _exists(con, "sales_stats"):
            return []
        if amount_col:
            return con.execute(
                f'''SELECT import_id,COALESCE(SUM(net_qty),0) qty,
                           COALESCE(SUM("{amount_col}"),0) amount
                    FROM sales_stats WHERE product_id=? GROUP BY import_id''',
                (int(pid),),
            ).fetchall()
        return con.execute(
            """SELECT import_id,COALESCE(SUM(net_qty),0) qty
               FROM sales_stats WHERE product_id=? GROUP BY import_id""",
            (int(pid),),
        ).fetchall()


def _child_price(rd, core, pid: int):
    return _historical_price(rd, core, pid)


def _canonical_parents(core):
    with core._conn(core.DEFAULT_DB) as con:
        rows = con.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products WHERE option_id IS NOT NULL"""
        ).fetchall()
    by_oid = {str(r["option_id"] or "").strip(): dict(r) for r in rows}
    out = {}
    for oid, cname in CANONICAL_RG.items():
        p = by_oid.get(oid)
        if p and int(p.get("active") or 0) == 1:
            p["canonical_name"] = cname
            out[oid] = p
    return out


def _best_parent(name: str, parents: dict[str, dict]):
    scored = []
    for oid, p in parents.items():
        cname = p.get("canonical_name") or p.get("name") or ""
        scored.append((_score(name, cname), oid, p, _norm(name) == _norm(cname)))
    scored.sort(key=lambda x: x[0], reverse=True)
    if not scored:
        return None
    top = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    if top[3]:
        return top
    if top[0] >= 0.88 and top[0] - second >= 0.04:
        return top
    return None


def cleanup_existing(core, rd):
    """Automatically clean high-confidence legacy return option products."""
    rd._ensure_schema(core, core.DEFAULT_DB)
    parents = _canonical_parents(core)
    if not parents:
        return {"canonical_loaded": 0, "cleaned": [], "review": []}

    aliases = rd._alias_map(core, core.DEFAULT_DB)
    with core._conn(core.DEFAULT_DB) as con:
        products = [dict(r) for r in con.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products WHERE option_id IS NOT NULL ORDER BY id"""
        ).fetchall()]

    cleaned, review = [], []
    amount_col = rd._amount_column(core, core.DEFAULT_DB)

    for child in products:
        oid = str(child.get("option_id") or "").strip()
        if not oid or oid in CANONICAL_RG or oid in aliases:
            continue
        if int(child.get("active") or 0) != 1:
            continue
        if str(child.get("item_type") or "").lower() != "finished":
            continue
        rows = _sales_rows(rd, core, int(child["id"]))
        if not rows:
            continue
        blockers = _blockers(core, int(child["id"]))
        if blockers:
            continue

        match = _best_parent(str(child.get("name") or ""), parents)
        if not match:
            continue
        score, parent_oid, parent, exact_name = match
        child_price = _child_price(rd, core, int(child["id"]))
        parent_price = _historical_price(rd, core, int(parent["id"]))
        discounted = bool(
            child_price is not None and parent_price is not None
            and child_price < parent_price * 0.995
        )
        if not exact_name and not discounted:
            review.append({
                "child_option_id": oid,
                "child_name": str(child.get("name") or ""),
                "parent_option_id": parent_oid,
                "parent_name": parent.get("canonical_name") or parent.get("name"),
                "score": score,
                "reason": "가격 할인 근거 부족",
            })
            continue

        for sr in rows:
            qty = _num(sr["qty"])
            parsed = [{
                "option_id": oid,
                "name": str(child.get("name") or ""),
                "name_key": rd._name_key(child.get("name")),
                "qty": qty,
                "amount": _num(sr["amount"]) if amount_col and "amount" in sr.keys() else None,
                "amount_known": bool(amount_col),
            }]
            rd._post_discount(
                core, core.DEFAULT_DB, int(sr["import_id"]), parsed,
                {oid: int(parent["id"])},
            )

        with core._conn(core.DEFAULT_DB) as con:
            # Catch any older ordinary sales deduction that did not use the expected
            # SALESSTAT reference.  Only sales deductions are removed here.
            con.execute(
                "DELETE FROM inventory_txns WHERE product_id=? AND txn_type='판매차감'",
                (int(child["id"]),),
            )
            con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=?",
                (core.now_iso(), int(child["id"])),
            )

        cleaned.append({
            "child_option_id": oid,
            "child_name": str(child.get("name") or ""),
            "parent_option_id": parent_oid,
            "parent_name": parent.get("canonical_name") or parent.get("name"),
            "score": score,
            "match": "exact_name" if exact_name else "name+discount_price",
        })

    return {
        "canonical_loaded": len(parents),
        "cleaned": cleaned,
        "review": review,
    }


def apply(core_module, return_discount_module) -> None:
    global _APPLIED
    rd = return_discount_module
    if _APPLIED or getattr(rd, "_rg_canonical_cleanup_v0947_applied", False):
        return

    # Run cleanup first so previously auto-created return options are archived
    # before the next sales import or UI render.
    result = cleanup_existing(core_module, rd)
    core_module.CANONICAL_RG_CLEANUP_RESULT = result

    previous_resolve = rd._resolve

    def resolve(core, db, parsed):
        parents = _canonical_parents(core)
        aliases = rd._alias_map(core, db)
        products = rd._load_products(core, db)
        by_oid = {str(p.get("option_id") or ""): p for p in products if p.get("option_id")}

        mapped = {}
        remaining = []
        parsed_by_oid = {str(r.get("option_id") or ""): r for r in parsed}

        same_file_price = {}
        for poid, parent in parents.items():
            row = parsed_by_oid.get(poid)
            if row:
                qty = abs(_num(row.get("qty")))
                amount = _num(row.get("amount"))
                if row.get("amount_known") and qty > 0 and amount > 0:
                    same_file_price[poid] = amount / qty

        for row in parsed:
            oid = str(row.get("option_id") or "")
            if oid in aliases or oid in CANONICAL_RG:
                remaining.append(row)
                continue
            existing = by_oid.get(oid)
            if existing and int(existing.get("active") or 0) == 1:
                remaining.append(row)
                continue

            match = _best_parent(str(row.get("name") or ""), parents)
            if not match:
                remaining.append(row)
                continue
            score, parent_oid, parent, exact_name = match
            qty = abs(_num(row.get("qty")))
            amount = _num(row.get("amount"))
            child_price = (
                amount / qty
                if row.get("amount_known") and qty > 0 and amount > 0
                else None
            )
            parent_price = same_file_price.get(parent_oid)
            if parent_price is None:
                parent_price = _historical_price(rd, core, int(parent["id"]))
            discounted = bool(
                child_price is not None and parent_price is not None
                and child_price < parent_price * 0.995
            )
            if exact_name or discounted:
                mapped[oid] = int(parent["id"])
            else:
                remaining.append(row)

        if remaining:
            mapped.update(previous_resolve(core, db, remaining))
        return mapped

    rd._resolve = resolve
    rd._rg_canonical_cleanup_v0947_applied = True
    _APPLIED = True
