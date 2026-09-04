"""v0.9.156 provisional logistics unit estimator.

Fixes two defects in the legacy automatic provisional logistics estimate:
1) historical unit costs were taken from final_cost_vat (VAT-inclusive), although
   the management P&L unit values are intended to use the settlement detail's
   final cost before VAT;
2) inbound/outbound and delivery were selected independently from the latest row,
   so two different orders (or a partial/promotional row) could be mixed.

The estimator now:
- keeps explicit manual_expected_inout/manual_expected_delivery overrides first;
- treats exact-name product IDs and explicit return-discount aliases as one
  historical product family;
- finds the most recent order that contains BOTH 입출고비 and 배송비;
- uses final_cost_prevat / abs(qty) for each component from that same order;
- if duplicate settlement rows exist for an order/component, only the newest row
  is used so duplicated imports cannot double the unit cost;
- falls back to the latest pre-VAT row per component only when no complete pair
  exists;
- never edits settlement facts; it only changes provisional estimates.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd

RULE_VERSION = "0.9.156-logistics-preVAT-same-order"


def _num(v: Any) -> float:
    try:
        x = float(v or 0)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0


def _nullable(v: Any):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _name_key(v: Any) -> str:
    s = str(v or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return re.sub(r"[\s,·_/\\\-]+", "", s)


def _related_product_ids(core, c, product_id: int) -> set[int]:
    """Historical IDs that safely represent the same visible product."""
    ids = {int(product_id)}
    row = c.execute(
        "SELECT id,option_id,name FROM products WHERE id=?", (int(product_id),)
    ).fetchone()
    if not row:
        return ids

    name_key = _name_key(row["name"])
    if name_key:
        for r in c.execute("SELECT id,name FROM products").fetchall():
            if _name_key(r["name"]) == name_key:
                ids.add(int(r["id"]))

    # Explicit return-sale aliases are stronger than name matching and can carry
    # settlement history under the child option ID.
    if _exists(c, "return_discount_aliases"):
        ac = _cols(c, "return_discount_aliases")
        if {"discount_option_id", "parent_product_id"}.issubset(ac):
            parent_ids = set(ids)
            qmarks = ",".join("?" for _ in parent_ids)
            if qmarks:
                alias_rows = c.execute(
                    f"""SELECT discount_option_id,parent_product_id
                        FROM return_discount_aliases
                        WHERE parent_product_id IN ({qmarks})""",
                    tuple(sorted(parent_ids)),
                ).fetchall()
                for a in alias_rows:
                    p = c.execute(
                        "SELECT id FROM products WHERE option_id=?",
                        (str(a["discount_option_id"] or ""),),
                    ).fetchone()
                    if p:
                        ids.add(int(p["id"]))
    return ids


def _logistics_units(core, db, product_id: int) -> dict[str, Any] | None:
    with core._conn(db) as c:
        if not _exists(c, "logistics_fees"):
            return None
        lc = _cols(c, "logistics_fees")
        required = {
            "id", "fee_type", "event_date", "order_id", "product_id",
            "qty", "final_cost_prevat",
        }
        if not required.issubset(lc):
            return None

        related = _related_product_ids(core, c, int(product_id))
        if not related:
            return None
        marks = ",".join("?" for _ in related)
        rows = c.execute(
            f"""SELECT id,fee_type,event_date,order_id,product_id,option_id,
                       qty,final_cost_prevat
                FROM logistics_fees
                WHERE product_id IN ({marks})
                  AND fee_type IN ('입출고비','배송비')
                  AND ABS(COALESCE(qty,0))>0
                ORDER BY COALESCE(event_date,'' ) DESC,id DESC""",
            tuple(sorted(related)),
        ).fetchall()

    if not rows:
        return None

    # Build one candidate per physical order. Both components must come from the
    # same order so a later partial/promo row cannot be mixed with another order.
    # For the same order/component keep only the newest settlement row; this also
    # protects against old duplicate report imports.
    groups: dict[str, dict[str, Any]] = {}
    fallback: dict[str, tuple[str, int, float]] = {}
    for r in rows:
        fee = str(r["fee_type"] or "")
        if fee not in {"입출고비", "배송비"}:
            continue
        qty = abs(_num(r["qty"]))
        if qty <= 1e-12:
            continue
        raw_cost = _num(r["final_cost_prevat"])
        # Negative settlement corrections are not a forward-looking unit fee.
        if raw_cost < -1e-9:
            continue
        unit = abs(raw_cost) / qty
        event = str(r["event_date"] or "")
        rid = int(r["id"])
        order = str(r["order_id"] or "").strip()

        old = fallback.get(fee)
        if old is None or (event, rid) > (old[0], old[1]):
            fallback[fee] = (event, rid, unit)

        if not order:
            continue
        g = groups.setdefault(
            order,
            {
                "event_date": event,
                "max_id": rid,
                "components": {},
            },
        )
        g["event_date"] = max(str(g["event_date"]), event)
        g["max_id"] = max(int(g["max_id"]), rid)
        prev = g["components"].get(fee)
        if prev is None or (event, rid) > (prev[0], prev[1]):
            g["components"][fee] = (event, rid, unit)

    complete = [
        g for g in groups.values()
        if {"입출고비", "배송비"}.issubset(set(g["components"]))
    ]
    if complete:
        complete.sort(
            key=lambda g: (str(g["event_date"]), int(g["max_id"])),
            reverse=True,
        )
        g = complete[0]
        return {
            "inout_unit": float(g["components"]["입출고비"][2]),
            "delivery_unit": float(g["components"]["배송비"][2]),
            "source": "latest_complete_order_prevat",
            "event_date": str(g["event_date"]),
            "related_product_ids": sorted(related),
        }

    # Legacy data may not contain matching order IDs. In that case still correct
    # the VAT basis, but retain the old per-component latest-row fallback.
    if fallback:
        return {
            "inout_unit": float(fallback.get("입출고비", ("", 0, 0.0))[2]),
            "delivery_unit": float(fallback.get("배송비", ("", 0, 0.0))[2]),
            "source": "latest_component_prevat_fallback",
            "event_date": max(
                fallback.get("입출고비", ("", 0, 0.0))[0],
                fallback.get("배송비", ("", 0, 0.0))[0],
            ),
            "related_product_ids": sorted(related),
        }
    return None


def _recalculate_raw(raw: pd.DataFrame, core, db):
    if raw is None or getattr(raw, "empty", True):
        return raw, {}

    out = raw.copy()
    diagnostics: dict[str, Any] = {}
    for idx in out.index:
        try:
            pid = int(_num(out.at[idx, "product_id"]))
        except Exception:
            continue
        if pid <= 0:
            continue

        units = _logistics_units(core, db, pid)
        if not units:
            continue

        q = _num(out.at[idx, "net_qty"]) if "net_qty" in out.columns else 0.0
        manual_inout = (
            _nullable(out.at[idx, "manual_expected_inout"])
            if "manual_expected_inout" in out.columns else None
        )
        manual_delivery = (
            _nullable(out.at[idx, "manual_expected_delivery"])
            if "manual_expected_delivery" in out.columns else None
        )

        inout_unit = (
            abs(manual_inout)
            if manual_inout is not None
            else max(0.0, _num(units["inout_unit"]))
        )
        delivery_unit = (
            abs(manual_delivery)
            if manual_delivery is not None
            else max(0.0, _num(units["delivery_unit"]))
        )

        if "inout_unit" in out.columns:
            out.at[idx, "inout_unit"] = inout_unit
        if "delivery_unit" in out.columns:
            out.at[idx, "delivery_unit"] = delivery_unit
        if "expected_inout" in out.columns:
            out.at[idx, "expected_inout"] = q * inout_unit
        if "expected_delivery" in out.columns:
            out.at[idx, "expected_delivery"] = q * delivery_unit

        # Keep raw profit fields coherent for any caller that consumes the raw
        # estimated_pnl output directly. The final UI performs its own sign guard.
        if all(
            col in out.columns
            for col in (
                "expected_revenue", "cogs", "expected_commission",
                "expected_inout", "expected_delivery",
                "expected_return_reserve", "ad_spend",
            )
        ):
            rev = _num(out.at[idx, "expected_revenue"])
            profit_ex = (
                rev
                - _num(out.at[idx, "cogs"])
                - _num(out.at[idx, "expected_commission"])
                - _num(out.at[idx, "expected_inout"])
                - _num(out.at[idx, "expected_delivery"])
                - _num(out.at[idx, "expected_return_reserve"])
            )
            out.at[idx, "profit_ex_ad"] = profit_ex
            profit = profit_ex - _num(out.at[idx, "ad_spend"])
            out.at[idx, "profit"] = profit
            out.at[idx, "margin_pct"] = (
                profit / rev * 100.0 if abs(rev) > 1e-12 else 0.0
            )

        oid = str(out.at[idx, "option_id"] or "") if "option_id" in out.columns else str(pid)
        diagnostics[oid] = {
            **units,
            "applied_inout_unit": inout_unit,
            "applied_delivery_unit": delivery_unit,
            "applied_total_unit": inout_unit + delivery_unit,
            "manual_inout": manual_inout is not None,
            "manual_delivery": manual_delivery is not None,
        }
    return out, diagnostics


def apply(core, snapshot_refresh_module=None):
    """Patch core.estimated_pnl idempotently and force stale snapshot refresh."""
    base = getattr(core, "_rg_estimated_pnl_base_v09156", None)
    if base is None:
        base = core.estimated_pnl
        core._rg_estimated_pnl_base_v09156 = base

    def estimated_pnl(sales_import_id, ad_import_id=None, db_path=None):
        db = db_path or core.DEFAULT_DB
        if db_path is None:
            raw, meta = base(sales_import_id, ad_import_id)
        else:
            raw, meta = base(sales_import_id, ad_import_id, db_path)
        fixed, diagnostics = _recalculate_raw(raw, core, db)
        meta = dict(meta or {})
        meta["logistics_unit_rule"] = RULE_VERSION
        meta["logistics_unit_diagnostics"] = diagnostics
        core._rg_last_logistics_unit_diagnostics_v09156 = diagnostics
        return fixed, meta

    core.estimated_pnl = estimated_pnl
    core._rg_provisional_logistics_unit_v09156_applied = True
    if snapshot_refresh_module is not None:
        snapshot_refresh_module._RULE_VERSION = RULE_VERSION
        snapshot_refresh_module._rg_provisional_logistics_unit_v09156_applied = True
    return {"ok": True, "rule_version": RULE_VERSION}
