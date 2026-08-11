"""RG Manager v0.9.41 sales-stat managed-product guard.

Fixes a false return-discount classification in return_discount_v099.

Background
----------
return_discount_v099 historically treated a product as an auto-created placeholder
when its item_code matched CP-<option_id> and unit_cost was 0.  That is not a safe
managed-SKU test: legitimate Coupang products can exist in ERP with zero cost
(before BOM/cost setup, or after legacy migration).

Rules
-----
- an option explicitly present in return_discount_aliases remains a return-discount
  child and is mapped to its parent;
- an existing ACTIVE ERP product is a managed normal SKU even when unit_cost == 0;
- an existing non-placeholder archived product is also treated as a normal SKU;
- only unresolved/placeholder rows are sent through the old unique-name matching;
- ambiguous unknown options are still blocked rather than guessed.

This patches only the resolver used before sales-stat import.  It does not change
sales, inventory, return or P&L posting rules.
"""
from __future__ import annotations

_APPLIED = False


def apply(return_discount_module) -> None:
    global _APPLIED
    if _APPLIED or getattr(return_discount_module, "_rg_sales_import_guard_v0941_applied", False):
        return

    rd = return_discount_module

    def _managed_existing(p, aliases) -> bool:
        if not p:
            return False
        oid = str(p.get("option_id") or "")
        if oid and oid in aliases:
            return False
        # Core fix: zero cost is NOT evidence that an active ERP product is a
        # temporary return option. Active products are managed products.
        if int(p.get("active") or 0) == 1:
            return True
        # Archived products that do not look like legacy auto-created placeholders
        # are still ordinary historical SKUs and their sales data may be imported.
        return not rd._placeholder(p)

    def resolve(core, db, parsed):
        products = rd._load_products(core, db)
        by_oid = {p["option_id"]: p for p in products if p.get("option_id")}
        aliases = rd._alias_map(core, db)

        same_file = {}
        for row in parsed:
            p = by_oid.get(row["option_id"])
            if _managed_existing(p, aliases):
                same_file.setdefault(row["name_key"], set()).add(p["id"])

        master = {}
        for p in products:
            if p.get("option_id") and _managed_existing(p, aliases):
                master.setdefault(p["name_key"], set()).add(p["id"])

        mappings, unresolved = {}, []
        for row in parsed:
            oid = row["option_id"]

            # Explicit return aliases always win, regardless of active/cost state.
            if oid in aliases:
                mappings[oid] = aliases[oid]
                continue

            p = by_oid.get(oid)
            if _managed_existing(p, aliases):
                # Existing managed product: import as normal sale. No return mapping.
                continue

            cand = set(same_file.get(row["name_key"], set()))
            if not cand:
                cand = set(master.get(row["name_key"], set()))
            if p:
                cand.discard(p["id"])

            if len(cand) == 1:
                mappings[oid] = next(iter(cand))
            else:
                unresolved.append((oid, row["name"], len(cand)))

        if unresolved:
            lines = []
            for oid, name, n in unresolved[:8]:
                reason = "원상품 후보 없음" if n == 0 else f"원상품 후보 {n}개"
                lines.append(f"{oid} | {name} ({reason})")
            more = "" if len(unresolved) <= 8 else f" 외 {len(unresolved)-8}개"
            raise ValueError(
                "품목관리에 없는 판매 옵션은 반품 할인판매로 처리해야 하지만 "
                "원상품을 안전하게 자동 매칭할 수 없습니다. 임의 처리하지 않았습니다.\n"
                + "\n".join(lines) + more
            )
        return mappings

    rd._resolve = resolve
    rd._rg_sales_import_guard_v0941_applied = True
    _APPLIED = True
