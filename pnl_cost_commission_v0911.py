"""Sales/P&L costing rules for RG Manager v0.9.11.

- Use moving weighted-average inventory cost for product P&L.
- Fall back to purchase/production weighted history only when inventory events do
  not contain enough cost information.
- If commission information itself is absent, use 10.8% of sales. 10.8% is the
  final rate; VAT is not added on top.
"""
from __future__ import annotations

import functools
import math
from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False
FALLBACK_COMMISSION_RATE = 0.108


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").replace("개", "").replace("%", "").strip()
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


def _exists(c, table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(c, table):
    try:
        return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _products(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        rows = c.execute("SELECT id,item_code,option_id,name,unit_cost FROM products").fetchall()
    by_id, by_oid = {}, {}
    for r in rows:
        p = {"id": int(r["id"]), "item_code": str(r["item_code"] or ""),
             "option_id": _oid(r["option_id"]), "name": str(r["name"] or ""),
             "unit_cost": _num(r["unit_cost"])}
        by_id[p["id"]] = p
        if p["option_id"]:
            by_oid[p["option_id"]] = p
        if p["item_code"]:
            by_oid.setdefault(_oid(p["item_code"]), p)
    try:
        with core._conn(db) as c:
            if _exists(c, "return_discount_aliases"):
                for r in c.execute("SELECT discount_option_id,parent_product_id FROM return_discount_aliases"):
                    p = by_id.get(int(r["parent_product_id"]))
                    if p:
                        by_oid[_oid(r["discount_option_id"])] = p
    except Exception:
        pass
    return by_id, by_oid


def _history_costs(core, db, by_id):
    totals = {pid: [0.0, 0.0] for pid in by_id}
    with core._conn(db) as c:
        if _exists(c, "production_orders"):
            pc = _cols(c, "production_orders")
            if {"parent_product_id", "qty", "produced_unit_cost"}.issubset(pc):
                for r in c.execute("SELECT parent_product_id,qty,produced_unit_cost FROM production_orders WHERE COALESCE(qty,0)>0"):
                    pid, q, u = int(r["parent_product_id"]), _num(r["qty"]), _num(r["produced_unit_cost"])
                    if pid in totals and q > 0 and u > 0:
                        totals[pid][0] += q; totals[pid][1] += q * u
        if _exists(c, "purchase_lines"):
            pc = _cols(c, "purchase_lines")
            if "product_id" in pc:
                for r in c.execute("SELECT * FROM purchase_lines WHERE product_id IS NOT NULL"):
                    pid = int(r["product_id"])
                    if pid not in totals:
                        continue
                    q = _num(r["qty_receipt"] if "qty_receipt" in pc else None) or _num(r["qty_source"] if "qty_source" in pc else None)
                    if q <= 0:
                        continue
                    u = _num(r["landed_unit_cost_krw"] if "landed_unit_cost_krw" in pc else None)
                    if u <= 0 and "landed_total_krw" in pc:
                        u = _num(r["landed_total_krw"]) / q if q else 0
                    if u <= 0 and "unit_price" in pc:
                        u = _num(r["unit_price"])
                    if u > 0:
                        totals[pid][0] += q; totals[pid][1] += q * u
    return {pid: (v / q if q > 0 else by_id[pid]["unit_cost"]) for pid, (q, v) in totals.items()}


def _moving_costs(core, db):
    by_id, by_oid = _products(core, db)
    fallback = _history_costs(core, db, by_id)
    avg = {pid: (fallback.get(pid) or p["unit_cost"] or 0.0) for pid, p in by_id.items()}
    qty = {pid: 0.0 for pid in by_id}
    value = {pid: 0.0 for pid in by_id}
    with core._conn(db) as c:
        if not _exists(c, "inventory_txns"):
            return avg, by_id, by_oid
        tc = _cols(c, "inventory_txns")
        if not {"id", "product_id", "qty_delta"}.issubset(tc):
            return avg, by_id, by_oid
        fields = ["id", "product_id", "qty_delta"] + [x for x in ("txn_date", "ref_no", "unit_cost") if x in tc]
        rows = c.execute(f"SELECT {','.join(fields)} FROM inventory_txns ORDER BY COALESCE(txn_date,''),id").fetchall()

    groups = {}
    for r in rows:
        pid = int(r["product_id"])
        if pid not in by_id:
            continue
        keys = r.keys()
        date = str(r["txn_date"] or "") if "txn_date" in keys else ""
        ref = str(r["ref_no"] or "") if "ref_no" in keys else f"ID-{r['id']}"
        g = groups.setdefault((pid, date, ref), {"id": int(r["id"]), "net": 0.0, "cq": 0.0, "cv": 0.0})
        d = _num(r["qty_delta"]); g["net"] += d
        u = _num(r["unit_cost"] if "unit_cost" in keys else None)
        if d > 0 and u > 0:
            g["cq"] += d; g["cv"] += d * u

    for (pid, _date, _ref), g in sorted(groups.items(), key=lambda x: (x[0][1], x[1]["id"])):
        d = g["net"]
        if abs(d) <= 1e-12:
            continue
        cur_avg = avg.get(pid, 0.0) or fallback.get(pid, 0.0) or by_id[pid]["unit_cost"]
        if d > 0:
            receipt = g["cv"] / g["cq"] if g["cq"] > 0 else cur_avg
            if receipt <= 0:
                receipt = fallback.get(pid, 0.0) or by_id[pid]["unit_cost"]
            base_q = max(qty[pid], 0.0)
            base_v = max(value[pid], 0.0) if base_q > 0 else 0.0
            qty[pid] = base_q + d; value[pid] = base_v + d * receipt
            if qty[pid] > 0:
                avg[pid] = value[pid] / qty[pid]
        else:
            qty[pid] += d; value[pid] = qty[pid] * cur_avg; avg[pid] = cur_avg
    for pid in avg:
        if avg[pid] <= 0:
            avg[pid] = fallback.get(pid, 0.0) or by_id[pid]["unit_cost"]
    return avg, by_id, by_oid


def _commission_history(core, db, by_oid):
    known_ids = set()
    with core._conn(db) as c:
        tables = [str(r["name"]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        for table in tables:
            if table.startswith("sqlite_"):
                continue
            cols = _cols(c, table)
            fee_cols = [x for x in cols if "commission" in x.lower() or "수수료" in x]
            if not fee_cols:
                continue
            pid_col = "product_id" if "product_id" in cols else None
            oid_col = next((x for x in cols if x.lower() in {"option_id", "optionid"} or "옵션id" in x.lower()), None)
            if not pid_col and not oid_col:
                continue
            for fc in fee_cols:
                try:
                    if pid_col:
                        for r in c.execute(f'SELECT DISTINCT "{pid_col}" v FROM "{table}" WHERE "{fc}" IS NOT NULL'):
                            if r["v"] is not None:
                                known_ids.add(int(r["v"]))
                    if oid_col:
                        for r in c.execute(f'SELECT DISTINCT "{oid_col}" v FROM "{table}" WHERE "{fc}" IS NOT NULL'):
                            p = by_oid.get(_oid(r["v"]))
                            if p:
                                known_ids.add(p["id"])
                except Exception:
                    pass
    return known_ids


def _pick(df, *names):
    lower = {str(c).lower(): c for c in df.columns}
    for n in names:
        if n in df.columns:
            return n
        if str(n).lower() in lower:
            return lower[str(n).lower()]
    return None


def _like(old, value, pct=False):
    if isinstance(old, str):
        if pct or "%" in old:
            return f"{value:,.1f}%"
        if "원" in old:
            return f"{int(round(value)):,}원"
        if "," in old:
            return f"{int(round(value)):,}"
    return value


def _adjust(df, avg, by_id, by_oid, known_commission):
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    qcol = _pick(df, "판매수량", "net_qty", "net_sales_qty", "sales_qty")
    rcol = _pick(df, "예상매출", "expected_sales", "expected_revenue", "revenue")
    ucol = _pick(df, "원가/개", "unit_cost", "avg_unit_cost")
    ccol = _pick(df, "매출원가", "cogs")
    fcol = _pick(df, "판매수수료", "commission")
    pidcol = _pick(df, "product_id", "상품ID")
    oidcol = _pick(df, "옵션ID", "option_id", "optionid", "쿠팡 옵션ID")
    if qcol is None or rcol is None or (ucol is None and ccol is None) or (pidcol is None and oidcol is None):
        return df
    out = df.copy()
    for i in out.index:
        pid = None
        if pidcol is not None:
            x = int(_num(out.at[i, pidcol]))
            pid = x if x in by_id else None
        if pid is None and oidcol is not None:
            p = by_oid.get(_oid(out.at[i, oidcol])); pid = p["id"] if p else None
        if pid not in by_id:
            continue
        q, revenue = _num(out.at[i, qcol]), _num(out.at[i, rcol])
        unit = avg.get(pid, 0.0) or by_id[pid]["unit_cost"]
        cogs = -abs(q) * unit
        if ucol is not None: out.at[i, ucol] = _like(out.at[i, ucol], unit)
        if ccol is not None: out.at[i, ccol] = _like(out.at[i, ccol], cogs)
        if fcol is not None:
            fee = _num(out.at[i, fcol])
            if abs(revenue) > 1e-12 and abs(fee) <= 1e-12 and pid not in known_commission:
                out.at[i, fcol] = _like(out.at[i, fcol], -abs(revenue) * FALLBACK_COMMISSION_RATE)
        inout = _pick(out, "입출고비", "inout"); delivery = _pick(out, "배송비", "delivery")
        ret = _pick(out, "반품충당", "return_reserve", "return_cost"); ad = _pick(out, "광고비", "ad_cost")
        noad = _pick(out, "광고제외이익", "profit_no_ad"); profit = _pick(out, "예상이익", "profit", "expected_profit")
        margin = _pick(out, "이익률(%)", "margin_pct", "profit_rate")
        fee = _num(out.at[i, fcol]) if fcol is not None else 0.0
        no_ad = revenue + cogs + fee + (_num(out.at[i, inout]) if inout else 0) + (_num(out.at[i, delivery]) if delivery else 0) + (_num(out.at[i, ret]) if ret else 0)
        pft = no_ad + (_num(out.at[i, ad]) if ad else 0)
        if noad: out.at[i, noad] = _like(out.at[i, noad], no_ad)
        if profit: out.at[i, profit] = _like(out.at[i, profit], pft)
        if margin: out.at[i, margin] = _like(out.at[i, margin], pft / revenue * 100 if revenue else 0, True)
    return out


def _result(res, avg, by_id, by_oid, known):
    if isinstance(res, pd.DataFrame):
        return _adjust(res, avg, by_id, by_oid, known)
    if isinstance(res, tuple) and res and isinstance(res[0], pd.DataFrame):
        x = list(res); x[0] = _adjust(x[0], avg, by_id, by_oid, known); return tuple(x)
    return res


def apply(core, db_path=None):
    global _APPLIED
    if _APPLIED or getattr(core, "_rg_pnl_cost_commission_v0911", False):
        return core
    db = db_path or core.DEFAULT_DB
    avg, by_id, by_oid = _moving_costs(core, db)
    known = _commission_history(core, db, by_oid)

    # Upstream adjustment so P&L functions and their KPI totals receive the rule.
    for name in list(dir(core)):
        if not any(x in name.lower() for x in ("pnl", "profit")):
            continue
        fn = getattr(core, name, None)
        if not callable(fn) or getattr(fn, "_rg_v0911_wrapped", False):
            continue
        @functools.wraps(fn)
        def wrapped(*args, __fn=fn, **kwargs):
            return _result(__fn(*args, **kwargs), avg, by_id, by_oid, known)
        wrapped._rg_v0911_wrapped = True
        setattr(core, name, wrapped)

    # Final table safety net.
    previous_dataframe = st.dataframe
    def dataframe(data=None, *args, **kwargs):
        if isinstance(data, pd.DataFrame):
            data = _adjust(data, avg, by_id, by_oid, known)
        return previous_dataframe(data, *args, **kwargs)
    st.dataframe = dataframe
    core._rg_pnl_cost_commission_v0911 = True
    _APPLIED = True
    return core
