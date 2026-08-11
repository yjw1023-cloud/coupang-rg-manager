"""RG Manager v0.9.44 return-sale option matching.

Business rule
-------------
- If Coupang sales data contains an option ID that exactly matches an ACTIVE ERP
  product, it is an ordinary sale.
- Explicit return_discount_aliases always remain returned-item discount sales.
- An unknown/archived option may be auto-matched to a managed original only when
  the product name is strongly similar AND its realized unit selling price is
  lower than the original product's normal reference selling price.
- Ambiguous/no-price cases are blocked rather than creating another managed SKU.
- Once a return alias is posted, any auto-created child product row is archived
  immediately so temporary Coupang return option IDs do not circulate in ERP.
"""
from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Any

_APPLIED = False
_PACK_RE = re.compile(r"(?<![0-9])([0-9]+)\s*(?:개입|개|p|pcs?|세트|set)(?![a-z0-9가-힣])", re.I)


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _pack_qty(name: Any):
    m = _PACK_RE.search(str(name or "").lower())
    return int(m.group(1)) if m else None


def _name_core(name: Any) -> str:
    s = str(name or "").lower()
    s = _PACK_RE.sub(" ", s)
    return re.sub(r"[^0-9a-z가-힣]+", "", s)


def _name_score(a: Any, b: Any) -> float:
    aq, bq = _pack_qty(a), _pack_qty(b)
    if aq is not None and bq is not None and aq != bq:
        return 0.0
    ca, cb = _name_core(a), _name_core(b)
    if not ca or not cb:
        return 0.0
    shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
    ratio = SequenceMatcher(None, ca, cb).ratio()
    if len(shorter) >= 6 and shorter in longer:
        ratio = max(ratio, 0.92)
    return ratio


def _row_unit_price(row) -> float | None:
    qty = abs(_num(row.get("qty")))
    amount = _num(row.get("amount"))
    if not bool(row.get("amount_known")) or qty <= 1e-12 or amount <= 0:
        return None
    return amount / qty


def _historical_price(rd, core, db, product_id: int) -> float | None:
    amount_col = rd._amount_column(core, db)
    if not amount_col:
        return None
    try:
        with core._conn(db) as c:
            row = c.execute(
                f'''SELECT COALESCE(SUM(net_qty),0) qty,
                           COALESCE(SUM("{amount_col}"),0) amount
                    FROM sales_stats
                    WHERE product_id=? AND COALESCE(net_qty,0)>0''',
                (int(product_id),),
            ).fetchone()
        qty = _num(row["qty"]) if row else 0.0
        amount = _num(row["amount"]) if row else 0.0
        return (amount / qty) if qty > 0 and amount > 0 else None
    except Exception:
        return None


def apply(return_discount_module, core_module) -> None:
    global _APPLIED
    rd = return_discount_module
    if _APPLIED or getattr(rd, "_rg_return_sale_match_v0944_applied", False):
        return

    original_post = rd._post_discount

    def _managed_existing(p, aliases) -> bool:
        if not p:
            return False
        oid = str(p.get("option_id") or "")
        if oid and oid in aliases:
            return False
        return int(p.get("active") or 0) == 1

    def resolve(core, db, parsed):
        products = rd._load_products(core, db)
        by_oid = {str(p.get("option_id") or ""): p for p in products if p.get("option_id")}
        aliases = rd._alias_map(core, db)

        managed = [p for p in products if p.get("option_id") and _managed_existing(p, aliases)]
        parsed_by_oid = {str(r.get("option_id") or ""): r for r in parsed}

        same_file_price = {}
        for p in managed:
            row = parsed_by_oid.get(str(p.get("option_id") or ""))
            price = _row_unit_price(row) if row else None
            if price and price > 0:
                same_file_price[int(p["id"])] = price

        hist_cache = {}
        mappings, unresolved = {}, []

        for row in parsed:
            oid = str(row.get("option_id") or "")
            if oid in aliases:
                mappings[oid] = int(aliases[oid])
                continue

            existing = by_oid.get(oid)
            if _managed_existing(existing, aliases):
                continue

            discount_price = _row_unit_price(row)
            scored = []
            for p in managed:
                score = _name_score(row.get("name"), p.get("name"))
                if score < 0.74:
                    continue
                ref = same_file_price.get(int(p["id"]))
                if ref is None:
                    pid = int(p["id"])
                    if pid not in hist_cache:
                        hist_cache[pid] = _historical_price(rd, core, db, pid)
                    ref = hist_cache[pid]
                discounted = bool(
                    discount_price is not None and ref is not None
                    and discount_price < ref * 0.995
                )
                scored.append((score, p, ref, discounted))

            scored.sort(key=lambda x: x[0], reverse=True)
            eligible = [x for x in scored if x[3]]

            chosen = None
            if eligible:
                top = eligible[0]
                second_score = eligible[1][0] if len(eligible) > 1 else 0.0
                if top[0] >= 0.80 and (len(eligible) == 1 or top[0] - second_score >= 0.06):
                    chosen = top

            if chosen:
                mappings[oid] = int(chosen[1]["id"])
                continue

            if discount_price is None:
                reason = "할인판매 단가를 확인할 수 없음"
            elif not scored:
                reason = "유사한 ERP 원상품 없음"
            elif not any(x[2] is not None for x in scored):
                reason = "원상품 정상 판매단가 이력 없음"
            elif not eligible:
                reason = "원상품보다 할인된 판매단가가 아님"
            else:
                reason = "유사 원상품 후보가 여러 개라 자동판정 불가"
            unresolved.append((oid, str(row.get("name") or ""), reason))

        if unresolved:
            lines = [f"{oid} | {name} ({reason})" for oid, name, reason in unresolved[:8]]
            more = "" if len(unresolved) <= 8 else f" 외 {len(unresolved)-8}개"
            raise ValueError(
                "ERP에 없는 쿠팡 옵션ID를 자동으로 새 품목 처리하지 않았습니다. "
                "동일 옵션ID는 정상판매로 처리하고, 다른 옵션ID는 유사 상품명 + 할인단가가 "
                "확인될 때만 반품 할인판매로 연결합니다.\n" + "\n".join(lines) + more
            )
        return mappings

    def post_discount(core, db, import_id, parsed, mappings):
        count = original_post(core, db, import_id, parsed, mappings)
        if mappings:
            now = core.now_iso()
            with core._conn(db) as c:
                for oid, parent_pid in mappings.items():
                    c.execute(
                        """UPDATE products SET active=0,updated_at=?
                           WHERE CAST(option_id AS TEXT)=? AND id<>?""",
                        (now, str(oid), int(parent_pid)),
                    )
        return count

    rd._resolve = resolve
    rd._post_discount = post_discount
    rd._rg_return_sale_match_v0944_applied = True
    _APPLIED = True

    # v0.9.47: user-supplied canonical Rocket Growth original-product registry.
    import canonical_rg_cleanup_v0947
    canonical_rg_cleanup_v0947.apply(core_module, rd)

    # v0.9.48: canonical IDs are authoritative originals. Restore any canonical
    # product hidden/aliased by older return heuristics and repair its sales posting.
    import canonical_rg_restore_v0948
    canonical_rg_restore_v0948.apply(core_module, rd, canonical_rg_cleanup_v0947)
