"""RG Manager v0.9.158 return-sale option matching.

Business rule
-------------
- Exact verified normal option IDs remain ordinary sales.
- Explicit return_discount_aliases always remain returned-item resale aliases.
- Auto-created placeholder products (CP-option id, zero cost) are NEVER treated
  as managed normal products merely because active=1.
- When the normal-option registry exists, an unknown option that is not in that
  registry may be auto-linked to one registered normal product when the normalized
  product name is a unique very-strong structural match and package quantity does
  not conflict. Price discount is supporting evidence, not mandatory, because
  Coupang returned-item resale prices can be above a current/coupon-adjusted normal
  realized price.
- Existing placeholder duplicates already stored in the ERP are repaired through
  the same resolver on startup/rerun; future unknown ambiguous options are blocked
  instead of silently becoming separate managed products.
- Historical/archived genuine ERP products remain valid originals for old sales.
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


def _full_name_key(name: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(name or "").lower())


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


def _strong_name_relation(a: Any, b: Any) -> bool:
    """Conservative structural match used only with the verified normal registry."""
    aq, bq = _pack_qty(a), _pack_qty(b)
    if aq is not None and bq is not None and aq != bq:
        return False
    ca, cb = _name_core(a), _name_core(b)
    if min(len(ca), len(cb)) < 8:
        return False
    if not (ca in cb or cb in ca):
        return False
    return _name_score(a, b) >= 0.90


def _row_unit_price(row) -> float | None:
    if not row:
        return None
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


def _known_return_option_ids() -> set[str]:
    try:
        import product_visibility_v0995
        return {str(x) for x in getattr(product_visibility_v0995, "KNOWN_RETURN_OPTION_IDS", set())}
    except Exception:
        return set()


def _normal_registry_ids(core, db) -> set[str]:
    """Verified normal RG option IDs loaded from the user's RG inbound workbook."""
    try:
        with core._conn(db) as c:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='coupang_normal_option_registry'"
            ).fetchone()
            if not exists:
                return set()
            return {
                str(r["vendor_item_id"] or "").strip()
                for r in c.execute("SELECT vendor_item_id FROM coupang_normal_option_registry")
                if str(r["vendor_item_id"] or "").strip()
            }
    except Exception:
        return set()


def _repair_existing_candidates(rd, core, db, resolve) -> dict[str, int]:
    """Repair already-created zero-cost CP-* children through the same safe resolver."""
    repaired = 0
    skipped = 0
    aliases = rd._alias_map(core, db)
    normal_ids = _normal_registry_ids(core, db)
    amount_col = rd._amount_column(core, db)

    products = rd._load_products(core, db)
    candidates = []
    for p in products:
        oid = str(p.get("option_id") or "")
        if not oid or oid in aliases or oid in normal_ids:
            continue
        try:
            placeholder = bool(rd._placeholder(p))
        except Exception:
            placeholder = False
        if placeholder:
            candidates.append(p)

    for child in candidates:
        oid = str(child.get("option_id") or "")
        try:
            with core._conn(db) as c:
                if amount_col:
                    rows = c.execute(
                        f'''SELECT import_id,COALESCE(SUM(net_qty),0) qty,
                                   COALESCE(SUM("{amount_col}"),0) amount
                            FROM sales_stats WHERE product_id=? GROUP BY import_id''',
                        (int(child["id"]),),
                    ).fetchall()
                else:
                    rows = c.execute(
                        """SELECT import_id,COALESCE(SUM(net_qty),0) qty
                           FROM sales_stats WHERE product_id=? GROUP BY import_id""",
                        (int(child["id"]),),
                    ).fetchall()
        except Exception:
            skipped += 1
            continue

        for sr in rows:
            qty = _num(sr["qty"])
            if abs(qty) <= 1e-12:
                continue
            parsed = [{
                "option_id": oid,
                "name": str(child.get("name") or ""),
                "name_key": str(child.get("name_key") or ""),
                "qty": qty,
                "amount": _num(sr["amount"]) if amount_col and "amount" in sr.keys() else None,
                "amount_known": bool(amount_col),
            }]
            try:
                mappings = resolve(core, db, parsed)
            except Exception:
                skipped += 1
                continue
            parent_pid = mappings.get(oid)
            if not parent_pid:
                continue
            rd._post_discount(core, db, int(sr["import_id"]), parsed, {oid: int(parent_pid)})
            repaired += 1

    return {"repaired": repaired, "skipped": skipped, "candidates": len(candidates)}


def apply(return_discount_module, core_module) -> None:
    global _APPLIED
    rd = return_discount_module
    if _APPLIED or getattr(rd, "_rg_return_sale_match_v0944_applied", False):
        return

    original_post = rd._post_discount

    def _managed_existing(p, aliases, known_returns) -> bool:
        if not p:
            return False
        oid = str(p.get("option_id") or "")
        if oid and (oid in aliases or oid in known_returns):
            return False
        # Critical v0.9.158 fix: import-created CP-* zero-cost placeholders are
        # not normal products just because the core importer marked them active.
        try:
            if rd._placeholder(p):
                return False
        except Exception:
            pass
        if int(p.get("active") or 0) == 1:
            return True
        try:
            return not rd._placeholder(p)
        except Exception:
            return False

    def resolve(core, db, parsed):
        products = rd._load_products(core, db)
        by_oid = {str(p.get("option_id") or ""): p for p in products if p.get("option_id")}
        aliases = rd._alias_map(core, db)
        known_returns = _known_return_option_ids()
        normal_ids = _normal_registry_ids(core, db)

        managed = [
            p for p in products
            if p.get("option_id") and _managed_existing(p, aliases, known_returns)
        ]
        parsed_by_oid = {str(r.get("option_id") or ""): r for r in parsed}

        same_file_price = {}
        exact_same_file = {}
        for p in managed:
            poid = str(p.get("option_id") or "")
            prow = parsed_by_oid.get(poid)
            if not prow:
                continue
            price = _row_unit_price(prow)
            if price and price > 0:
                same_file_price[int(p["id"])] = price
            key = _full_name_key(prow.get("name"))
            if key:
                exact_same_file.setdefault(key, []).append(p)

        alias_parent_by_name = {}
        for alias_oid, parent_pid in aliases.items():
            arow = parsed_by_oid.get(str(alias_oid))
            if not arow:
                continue
            key = _full_name_key(arow.get("name"))
            if key:
                alias_parent_by_name.setdefault(key, set()).add(int(parent_pid))

        hist_cache = {}
        mappings, unresolved = {}, []

        for row in parsed:
            oid = str(row.get("option_id") or "")
            if oid in aliases:
                mappings[oid] = int(aliases[oid])
                continue

            existing = by_oid.get(oid)
            if _managed_existing(existing, aliases, known_returns):
                continue

            discount_price = _row_unit_price(row)
            full_key = _full_name_key(row.get("name"))

            exact_candidates = []
            for p in exact_same_file.get(full_key, []):
                if existing and int(p["id"]) == int(existing["id"]):
                    continue
                ref = same_file_price.get(int(p["id"]))
                if (
                    discount_price is not None
                    and ref is not None
                    and discount_price < ref * 0.995
                ):
                    exact_candidates.append(p)
            exact_ids = {int(p["id"]) for p in exact_candidates}
            if len(exact_ids) == 1:
                mappings[oid] = next(iter(exact_ids))
                continue

            inherited = alias_parent_by_name.get(full_key, set()) if oid in known_returns else set()
            if len(inherited) == 1:
                mappings[oid] = next(iter(inherited))
                continue

            # v0.9.158 structural rule. Only trust this without price evidence
            # when the user has a verified normal-option registry. The child must
            # not itself be registered normal, and exactly one registered normal
            # product must be a very-strong name/package match.
            if normal_ids and oid not in normal_ids:
                strong = []
                for p in managed:
                    poid = str(p.get("option_id") or "")
                    if poid not in normal_ids:
                        continue
                    same_file_row = parsed_by_oid.get(poid)
                    candidate_name = (
                        same_file_row.get("name")
                        if same_file_row and same_file_row.get("name")
                        else p.get("name")
                    )
                    if _strong_name_relation(row.get("name"), candidate_name):
                        strong.append(p)
                strong_ids = {int(p["id"]) for p in strong}
                if len(strong_ids) == 1:
                    mappings[oid] = next(iter(strong_ids))
                    continue

            scored = []
            for p in managed:
                poid = str(p.get("option_id") or "")
                same_file_row = parsed_by_oid.get(poid)
                candidate_name = (
                    same_file_row.get("name")
                    if same_file_row and same_file_row.get("name")
                    else p.get("name")
                )
                score = _name_score(row.get("name"), candidate_name)
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
                if top[0] >= 0.80 and (
                    len(eligible) == 1 or top[0] - second_score >= 0.06
                ):
                    chosen = top

            if chosen:
                mappings[oid] = int(chosen[1]["id"])
                continue

            if normal_ids and oid not in normal_ids:
                reason = "정상옵션 목록에 없고 원상품 자동매칭이 확정되지 않음"
            elif discount_price is None:
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
            lines = [f"{oid} | {name} ({reason})" for oid, name, reason in unresolved[:20]]
            more = "" if len(unresolved) <= 20 else f" 외 {len(unresolved)-20}개"
            raise ValueError(
                "ERP에 없는 쿠팡 옵션ID를 별도 정상상품으로 자동 생성하지 않았습니다. "
                "정상옵션 목록에 있는 ID는 정상판매로 처리하고, 그 밖의 새 ID는 원상품이 "
                "안전하게 하나로 확정되는 경우에만 반품 재판매로 연결합니다. 애매한 경우 "
                "잘못 합산하지 않고 업로드를 중단합니다.\n"
                + "\n".join(lines) + more
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

    # Repair old placeholder duplicates with the exact same rule used for future
    # uploads. This is intentionally after rd._post_discount is replaced so the
    # repaired child is archived and inventory is moved to 반품창고 correctly.
    try:
        core_module._rg_return_sale_repair_v09158 = _repair_existing_candidates(
            rd, core_module, core_module.DEFAULT_DB, resolve
        )
    except Exception as exc:
        core_module._rg_return_sale_repair_v09158 = {"repaired": 0, "error": str(exc)}

    import canonical_rg_cleanup_v0947
    canonical_rg_cleanup_v0947.apply(core_module, rd)

    import canonical_rg_restore_v0948
    canonical_rg_restore_v0948.apply(core_module, rd, canonical_rg_cleanup_v0947)

    import august_cost_backfill_v0950
    august_cost_backfill_v0950.apply(core_module)

    # v0.9.114: user-approved exception for two old Tesla door-guard options.
    # If no real ERP product exists for them, skip only those rows instead of
    # blocking the entire monthly sales-stat import. All other unknown options
    # remain strict and continue to block ambiguous imports.
    import sales_ignore_unmanaged_v09114
    sales_ignore_unmanaged_v09114.apply(core_module, rd)
