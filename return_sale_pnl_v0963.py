"""v0.9.63 returned-item sale linkage and monthly provisional P&L consolidation.

User-confirmed return-sale aliases:
- 95119299567 -> 94475454519 (글라스 네일 파일 5P)
- 95156135112 -> 94350296878 (휴대용 가죽 구두주걱 미니 2P)

Rules
- Return-sale option IDs are aliases of the managed original product, not SKUs.
- Existing sales history remains on the original imported child row for auditability.
- Inventory movement is repaired to 반품창고 via return_discount_v099._post_discount.
- Monthly provisional P&L hides the alias row and rolls it into the original row.
- The original row keeps total 판매수량/예상매출/예상이익 and additionally exposes
  반품판매수량 and 반품판매매출 so the returned-item contribution stays visible.
"""
from __future__ import annotations

import calendar
import math
from typing import Any

import pandas as pd


_REPAIR_CACHE = {}

KNOWN_RETURN_ALIASES = {
    "95119299567": {
        "parent_option_id": "94475454519",
        "discount_name": "글라스 네일 파일 5p 유리 손톱 샤이너",
    },
    "95156135112": {
        "parent_option_id": "94350296878",
        "discount_name": "휴대용 가죽 구두주걱 미니 2p 스텐",
    },
}

_BASE_MONEY_COLS = [
    "예상매출",
    "매출원가",
    "판매수수료",
    "입출고비",
    "배송비",
    "반품충당",
    "광고비",
    "광고제외이익",
    "예상이익",
]


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
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def ensure_known_aliases(core, db, rd):
    """Persist the two user-confirmed aliases and repair already-imported sales."""
    cache_key = str(db)
    if cache_key in _REPAIR_CACHE:
        return dict(_REPAIR_CACHE[cache_key])
    rd._ensure_schema(core, db)
    now = core.now_iso()
    linked = []
    missing_parent = []

    with core._conn(db) as con:
        for discount_oid, spec in KNOWN_RETURN_ALIASES.items():
            parent = con.execute(
                """SELECT id,option_id,name,unit_cost,active
                   FROM products WHERE CAST(option_id AS TEXT)=?
                   ORDER BY active DESC,id LIMIT 1""",
                (spec["parent_option_id"],),
            ).fetchone()
            if not parent:
                missing_parent.append(spec["parent_option_id"])
                continue
            con.execute(
                """INSERT INTO return_discount_aliases
                   (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)
                   ON CONFLICT(discount_option_id) DO UPDATE SET
                     parent_product_id=excluded.parent_product_id,
                     discount_name=excluded.discount_name,
                     match_method=excluded.match_method,
                     updated_at=excluded.updated_at""",
                (
                    discount_oid,
                    int(parent["id"]),
                    spec["discount_name"],
                    "explicit_user_mapping_v0963",
                    now,
                    now,
                ),
            )
            linked.append((discount_oid, int(parent["id"])))

    amount_col = rd._amount_column(core, db)
    repaired_imports = 0
    archived_children = 0

    for discount_oid, parent_pid in linked:
        with core._conn(db) as con:
            child = con.execute(
                """SELECT id,name FROM products WHERE CAST(option_id AS TEXT)=?
                   ORDER BY active DESC,id DESC LIMIT 1""",
                (discount_oid,),
            ).fetchone()
            if not child or not _table_exists(con, "sales_stats"):
                continue
            child_pid = int(child["id"])
            if amount_col:
                rows = con.execute(
                    f'''SELECT import_id,COALESCE(SUM(net_qty),0) qty,
                               COALESCE(SUM("{amount_col}"),0) amount
                        FROM sales_stats WHERE product_id=? GROUP BY import_id''',
                    (child_pid,),
                ).fetchall()
            else:
                rows = con.execute(
                    """SELECT import_id,COALESCE(SUM(net_qty),0) qty
                       FROM sales_stats WHERE product_id=? GROUP BY import_id""",
                    (child_pid,),
                ).fetchall()

        for sr in rows:
            qty = _num(sr["qty"])
            if abs(qty) <= 1e-12:
                continue
            parsed = [{
                "option_id": discount_oid,
                "name": str(child["name"] or KNOWN_RETURN_ALIASES[discount_oid]["discount_name"]),
                "name_key": rd._name_key(child["name"]),
                "qty": qty,
                "amount": _num(sr["amount"]) if amount_col and "amount" in sr.keys() else None,
                "amount_known": bool(amount_col),
            }]
            rd._post_discount(core, db, int(sr["import_id"]), parsed, {discount_oid: parent_pid})
            repaired_imports += 1

        with core._conn(db) as con:
            con.execute(
                "DELETE FROM inventory_txns WHERE product_id=? AND txn_type='판매차감'",
                (child_pid,),
            )
            cur = con.execute(
                "UPDATE products SET active=0,updated_at=? WHERE id=? AND active<>0",
                (core.now_iso(), child_pid),
            )
            archived_children += int(cur.rowcount or 0)

    result = {
        "aliases": len(linked),
        "repaired_imports": repaired_imports,
        "archived_children": archived_children,
        "missing_parent": missing_parent,
    }
    _REPAIR_CACHE[cache_key] = dict(result)
    return result


def _alias_map(core, db):
    with core._conn(db) as con:
        if not _table_exists(con, "return_discount_aliases"):
            return {}
        rows = con.execute(
            """SELECT a.discount_option_id,
                      p.id parent_product_id,p.option_id parent_option_id,
                      p.name parent_name,p.unit_cost parent_unit_cost
               FROM return_discount_aliases a
               JOIN products p ON p.id=a.parent_product_id"""
        ).fetchall()
    return {
        _oid(r["discount_option_id"]): {
            "parent_product_id": int(r["parent_product_id"]),
            "parent_option_id": _oid(r["parent_option_id"]),
            "parent_name": str(r["parent_name"] or ""),
            "parent_unit_cost": abs(_num(r["parent_unit_cost"])),
        }
        for r in rows
        if _oid(r["discount_option_id"]) and _oid(r["parent_option_id"])
    }


def _month_bounds(month: str):
    year, mon = (int(x) for x in str(month).split("-"))
    last = calendar.monthrange(year, mon)[1]
    return f"{year:04d}-{mon:02d}-01", f"{year:04d}-{mon:02d}-{last:02d}"


def _return_totals(core, db, month: str):
    start, end = _month_bounds(month)
    with core._conn(db) as con:
        if not _table_exists(con, "return_discount_sales"):
            return {}
        rows = con.execute(
            """SELECT discount_option_id,
                      COALESCE(SUM(qty),0) qty,
                      COALESCE(SUM(CASE WHEN amount_known=1 THEN net_sales_amount ELSE 0 END),0) amount,
                      COALESCE(MIN(amount_known),0) all_amount_known
               FROM return_discount_sales
               WHERE period_start>=? AND period_end<=?
               GROUP BY discount_option_id""",
            (start, end),
        ).fetchall()
    return {
        _oid(r["discount_option_id"]): {
            "qty": _num(r["qty"]),
            "amount": _num(r["amount"]),
            "all_amount_known": bool(r["all_amount_known"]),
        }
        for r in rows
    }


def _sum_rows(df: pd.DataFrame, indices, col: str) -> float:
    if col not in df.columns:
        return 0.0
    return sum(_num(df.at[i, col]) for i in indices)


def _set_numeric(df: pd.DataFrame, idx, col: str, value: float):
    if col in df.columns:
        df.at[idx, col] = float(value)


def _recalc_row(df: pd.DataFrame, idx):
    qty = _num(df.at[idx, "판매수량"]) if "판매수량" in df.columns else 0.0
    revenue = _num(df.at[idx, "예상매출"]) if "예상매출" in df.columns else 0.0
    cogs = _num(df.at[idx, "매출원가"]) if "매출원가" in df.columns else 0.0
    if "예상 실현단가" in df.columns:
        df.at[idx, "예상 실현단가"] = revenue / qty if abs(qty) > 1e-12 else 0.0
    if "원가/개" in df.columns:
        df.at[idx, "원가/개"] = abs(cogs / qty) if abs(qty) > 1e-12 else _num(df.at[idx, "원가/개"])
    if "RG비용" in df.columns:
        df.at[idx, "RG비용"] = sum(
            _num(df.at[idx, c]) for c in ("입출고비", "배송비", "반품충당") if c in df.columns
        )
    if "이익률(%)" in df.columns:
        profit = _num(df.at[idx, "예상이익"]) if "예상이익" in df.columns else 0.0
        df.at[idx, "이익률(%)"] = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0


def consolidate_month(core, db, month: str, view: pd.DataFrame):
    """Roll every linked return-sale alias into its managed original product row."""
    if not isinstance(view, pd.DataFrame) or view.empty or "옵션ID" not in view.columns:
        return view, {"rows": 0, "qty": 0.0, "revenue": 0.0}

    import return_discount_v099 as rd

    repair = ensure_known_aliases(core, db, rd)
    aliases = _alias_map(core, db)
    if not aliases:
        return view, {"rows": 0, "qty": 0.0, "revenue": 0.0, "repair": repair}

    totals = _return_totals(core, db, month)
    out = view.copy()
    if "반품판매수량" not in out.columns:
        out["반품판매수량"] = 0.0
    if "반품판매매출" not in out.columns:
        out["반품판매매출"] = 0.0

    oid_series = out["옵션ID"].map(_oid)
    drop_indices = set()
    merged_rows = 0
    total_qty = 0.0
    total_revenue = 0.0

    for discount_oid, info in aliases.items():
        alias_indices = [i for i in out.index if i not in drop_indices and oid_series.loc[i] == discount_oid]
        if not alias_indices:
            continue

        parent_oid = info["parent_option_id"]
        parent_indices = [
            i for i in out.index
            if i not in drop_indices and i not in alias_indices and oid_series.loc[i] == parent_oid
        ]
        parent_idx = parent_indices[0] if parent_indices else None

        fallback_qty = _sum_rows(out, alias_indices, "판매수량")
        rt = totals.get(discount_oid, {})
        qty = _num(rt.get("qty")) if rt else fallback_qty
        if abs(qty) <= 1e-12 and abs(fallback_qty) > 1e-12:
            qty = fallback_qty

        fallback_revenue = _sum_rows(out, alias_indices, "예상매출")
        revenue = (
            _num(rt.get("amount"))
            if rt and rt.get("all_amount_known")
            else fallback_revenue
        )
        cost = info["parent_unit_cost"]
        if cost <= 1e-12 and parent_idx is not None and "원가/개" in out.columns:
            cost = abs(_num(out.at[parent_idx, "원가/개"]))

        alias_ad = _sum_rows(out, alias_indices, "광고비")
        alias_commission = _sum_rows(out, alias_indices, "판매수수료")
        alias_inout = _sum_rows(out, alias_indices, "입출고비")
        alias_delivery = _sum_rows(out, alias_indices, "배송비")
        alias_return = _sum_rows(out, alias_indices, "반품충당")

        parent_qty = abs(_num(out.at[parent_idx, "판매수량"])) if parent_idx is not None else 0.0
        parent_revenue = _num(out.at[parent_idx, "예상매출"]) if parent_idx is not None and "예상매출" in out.columns else 0.0

        if parent_idx is not None and abs(parent_revenue) > 1e-12 and "판매수수료" in out.columns:
            commission = (_num(out.at[parent_idx, "판매수수료"]) / parent_revenue) * revenue
        elif abs(fallback_revenue) > 1e-12 and abs(alias_commission) > 1e-12:
            commission = (alias_commission / fallback_revenue) * revenue
        else:
            commission = -abs(revenue) * 0.108

        def per_unit_or_alias(col: str, alias_value: float) -> float:
            if parent_idx is not None and parent_qty > 1e-12 and col in out.columns:
                return (_num(out.at[parent_idx, col]) / parent_qty) * abs(qty)
            return alias_value

        inout = per_unit_or_alias("입출고비", alias_inout)
        delivery = per_unit_or_alias("배송비", alias_delivery)
        ret = per_unit_or_alias("반품충당", alias_return)
        cogs = -abs(qty) * abs(cost)
        no_ad = revenue + cogs + commission + inout + delivery + ret
        profit = no_ad + alias_ad

        metrics = {
            "판매수량": qty,
            "예상매출": revenue,
            "매출원가": cogs,
            "판매수수료": commission,
            "입출고비": inout,
            "배송비": delivery,
            "반품충당": ret,
            "광고비": alias_ad,
            "광고제외이익": no_ad,
            "예상이익": profit,
        }

        if parent_idx is None:
            target = alias_indices[0]
            out.at[target, "옵션ID"] = parent_oid
            if "상품명" in out.columns:
                out.at[target, "상품명"] = info["parent_name"]
            for col, value in metrics.items():
                _set_numeric(out, target, col, value)
            out.at[target, "반품판매수량"] = qty
            out.at[target, "반품판매매출"] = revenue
            if "원가/개" in out.columns:
                out.at[target, "원가/개"] = cost
            for extra in alias_indices[1:]:
                drop_indices.add(extra)
            parent_idx = target
        else:
            for col, value in metrics.items():
                if col in out.columns:
                    out.at[parent_idx, col] = _num(out.at[parent_idx, col]) + value
            out.at[parent_idx, "반품판매수량"] = _num(out.at[parent_idx, "반품판매수량"]) + qty
            out.at[parent_idx, "반품판매매출"] = _num(out.at[parent_idx, "반품판매매출"]) + revenue
            drop_indices.update(alias_indices)

        _recalc_row(out, parent_idx)
        merged_rows += len(alias_indices)
        total_qty += qty
        total_revenue += revenue

    if drop_indices:
        out = out.drop(index=list(drop_indices)).copy()

    desired = []
    for col in out.columns:
        if col not in {"반품판매수량", "반품판매매출"}:
            desired.append(col)
        if col == "판매수량":
            desired.append("반품판매수량")
        if col == "예상매출":
            desired.append("반품판매매출")
    for col in ("반품판매수량", "반품판매매출"):
        if col not in desired and col in out.columns:
            desired.append(col)
    out = out[[c for c in desired if c in out.columns]]

    return out, {
        "rows": merged_rows,
        "qty": total_qty,
        "revenue": total_revenue,
        "repair": repair,
    }
