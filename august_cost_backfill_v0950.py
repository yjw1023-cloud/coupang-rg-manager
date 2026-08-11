"""RG Manager v0.9.50 August provisional P&L missing-cost backfill.

User intent
-----------
The user supplied baseline unit costs for five Rocket Growth products on
2026-08-11.  Some August 2026 sales were already captured into provisional P&L
snapshots while those products still had zero/missing cost.  Repair only those
missing-cost August snapshot rows; do not rewrite rows that already have a
positive cost and do not touch prior months.

This migration is deliberately narrow and idempotent:
- target snapshots wholly inside 2026-08;
- target only option IDs in canonical_rg_restore_v0948.USER_BASELINE_COSTS;
- target only non-zero sales rows whose stored unit cost is <= 0;
- recalculate COGS, no-ad profit, after-ad profit and margin from the existing
  snapshot expenses;
- recalculate snapshot totals;
- keep an audit row per import/option.

Future provisional calculations already use the repaired product master costs, so
this module exists to correct snapshots that were captured before v0.9.49.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Any

_APPLIED = False
TARGET_MONTH = "2026-08"


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = (
                v.replace(",", "")
                .replace("원", "")
                .replace("개", "")
                .replace("건", "")
                .replace("%", "")
                .strip()
            )
        x = float(v or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    def s(col: str, absolute: bool = False) -> float:
        vals = [_num(r.get(col)) for r in rows]
        return float(sum(abs(v) for v in vals)) if absolute else float(sum(vals))

    return {
        "revenue": s("예상매출"),
        "cogs": s("매출원가", True),
        "commission": s("판매수수료", True),
        "inout": s("입출고비", True),
        "delivery": s("배송비", True),
        "returns": s("반품충당", True),
        "ad": s("광고비", True),
        "profit": s("예상이익"),
    }


def _repair_row(row: dict[str, Any], cost: float) -> bool:
    qty = _num(row.get("판매수량"))
    if abs(qty) <= 1e-12:
        return False
    old_cost = _num(row.get("원가/개"))
    if old_cost > 0:
        return False

    revenue = _num(row.get("예상매출"))
    commission = _num(row.get("판매수수료"))
    inout = _num(row.get("입출고비"))
    delivery = _num(row.get("배송비"))
    returns = _num(row.get("반품충당"))
    raw_ad = _num(row.get("광고비"))
    ad = -abs(raw_ad) if abs(raw_ad) > 1e-12 else 0.0

    # Positive sales consume cost; negative net sales reverse it.
    cogs_effect = -qty * float(cost)
    no_ad = revenue + cogs_effect + commission + inout + delivery + returns
    profit = no_ad + ad
    margin = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0

    row["원가/개"] = float(cost)
    row["매출원가"] = float(cogs_effect)
    row["광고비"] = float(ad)
    row["광고제외이익"] = float(no_ad)
    row["예상이익"] = float(profit)
    row["이익률(%)"] = float(margin)
    return True


def apply(core_module) -> dict:
    global _APPLIED
    if _APPLIED or getattr(core_module, "_rg_august_cost_backfill_v0950_applied", False):
        return getattr(core_module, "AUGUST_COST_BACKFILL_RESULT", {})

    import canonical_rg_restore_v0948 as costs

    db = core_module.DEFAULT_DB
    core_module.init_db(db)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    result = {"snapshots_checked": 0, "snapshots_updated": 0, "rows_updated": 0, "details": []}

    with core_module._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS provisional_pnl_snapshots(
                   import_id INTEGER PRIMARY KEY,
                   file_name TEXT,
                   period_start TEXT,
                   period_end TEXT,
                   captured_at TEXT NOT NULL,
                   rows_json TEXT NOT NULL,
                   totals_json TEXT NOT NULL
               )"""
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS provisional_cost_backfill_log(
                   import_id INTEGER NOT NULL,
                   option_id TEXT NOT NULL,
                   old_unit_cost REAL NOT NULL DEFAULT 0,
                   new_unit_cost REAL NOT NULL,
                   applied_at TEXT NOT NULL,
                   PRIMARY KEY(import_id, option_id)
               )"""
        )

        snaps = con.execute(
            """SELECT import_id,file_name,period_start,period_end,rows_json
               FROM provisional_pnl_snapshots
               WHERE substr(period_start,1,7)=? AND substr(period_end,1,7)=?
               ORDER BY import_id""",
            (TARGET_MONTH, TARGET_MONTH),
        ).fetchall()

        for snap in snaps:
            result["snapshots_checked"] += 1
            try:
                rows = json.loads(str(snap["rows_json"] or "[]"))
            except Exception:
                continue
            if not isinstance(rows, list):
                continue

            changed = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                oid = _oid(row.get("옵션ID") or row.get("쿠팡 옵션ID"))
                if oid not in costs.USER_BASELINE_COSTS:
                    continue
                old_cost = _num(row.get("원가/개"))
                if _repair_row(row, float(costs.USER_BASELINE_COSTS[oid])):
                    changed.append((oid, old_cost, float(costs.USER_BASELINE_COSTS[oid])))

            if not changed:
                continue

            con.execute(
                """UPDATE provisional_pnl_snapshots
                   SET captured_at=?,rows_json=?,totals_json=?
                   WHERE import_id=?""",
                (
                    now,
                    json.dumps(rows, ensure_ascii=False),
                    json.dumps(_totals(rows), ensure_ascii=False),
                    int(snap["import_id"]),
                ),
            )
            for oid, old_cost, new_cost in changed:
                con.execute(
                    """INSERT INTO provisional_cost_backfill_log
                       (import_id,option_id,old_unit_cost,new_unit_cost,applied_at)
                       VALUES(?,?,?,?,?)
                       ON CONFLICT(import_id,option_id) DO UPDATE SET
                         old_unit_cost=excluded.old_unit_cost,
                         new_unit_cost=excluded.new_unit_cost,
                         applied_at=excluded.applied_at""",
                    (int(snap["import_id"]), str(oid), float(old_cost), float(new_cost), now),
                )
                result["details"].append({
                    "import_id": int(snap["import_id"]),
                    "option_id": str(oid),
                    "old_unit_cost": float(old_cost),
                    "new_unit_cost": float(new_cost),
                })
            result["snapshots_updated"] += 1
            result["rows_updated"] += len(changed)

    core_module.AUGUST_COST_BACKFILL_RESULT = result
    core_module._rg_august_cost_backfill_v0950_applied = True
    _APPLIED = True
    return result
