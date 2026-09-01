"""Inventory/item-master presentation for RG Manager v0.9.137.

- Keep warehouse tabs: all / own / Coupang RG / returns.
- Hide internal CP- prefix from user-facing product codes.
- Item master shows basis cost plus source-specific cost history:
  * own/raw items: latest purchase cost + quantity-weighted average purchase cost
  * Coupang RG/finished items: latest production cost + quantity-weighted average production cost
- Item Master warehouse tabs are registration/category tabs, not positive-stock-only tabs:
  * 자체창고 shows every registered raw/self-warehouse item even when stock is 0.
  * 쿠팡RG shows every registered finished/Coupang item even when RG stock is 0.
  * 반품창고 remains a stock-state tab and shows only non-zero return stock.
- Inventory-page warehouse tabs continue to show only products with non-zero stock.
- If a Coupang RG item has basis cost 0 but has a positive production-cost history,
  show a visible warning/status instead of silently hiding that information.
- Inventory warehouse tabs show basis cost and inventory value.

No persisted cost, production history or inventory value is rewritten by this
presentation module.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any

import pandas as pd
import streamlit as st

_REQUIRED = {"품목코드", "상품명", "반품창고", "자체창고", "쿠팡RG"}
_WAREHOUSES = ["자체창고", "쿠팡RG", "반품창고"]


def _display_code(value, option_id=None):
    text = "" if value is None else str(value).strip()
    if re.fullmatch(r"CP-\d+", text):
        return str(option_id or text[3:])
    return text


def _num(v: Any) -> float:
    try:
        x = float(v or 0)
        return 0.0 if pd.isna(x) else x
    except Exception:
        return 0.0


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {
            str(r["name"])
            for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()
        }
    except Exception:
        return set()


def _cost_lookup() -> dict[str, dict[str, Any]]:
    """Return basis, purchase and production cost facts by displayed product code.

    Purchase average = total linked purchase amount / total positive purchase qty.
    Production average = sum(production qty * produced unit cost) / total positive
    production qty. Latest costs use production/purchase date then row id.
    """
    try:
        import core

        core.init_db(core.DEFAULT_DB)
        con = sqlite3.connect(str(core.DEFAULT_DB))
        con.row_factory = sqlite3.Row
    except Exception:
        return {}

    try:
        products = con.execute(
            "SELECT id,item_code,option_id,name,item_type,unit_cost FROM products"
        ).fetchall()
        stats: dict[int, dict[str, Any]] = {
            int(r["id"]): {
                "purchase_qty": 0.0,
                "purchase_amount": 0.0,
                "purchase_latest_key": ("", -1),
                "latest_purchase_cost": None,
                "production_qty": 0.0,
                "production_amount": 0.0,
                "production_latest_key": ("", -1),
                "latest_production_cost": None,
            }
            for r in products
        }

        if _table_exists(con, "purchase_lines"):
            cols = _cols(con, "purchase_lines")
            if "product_id" in cols:
                rows = con.execute(
                    "SELECT * FROM purchase_lines WHERE product_id IS NOT NULL "
                    "ORDER BY COALESCE(purchase_date,''),id"
                ).fetchall()
                for r in rows:
                    pid = int(r["product_id"])
                    if pid not in stats:
                        continue
                    qty = _num(r["qty_receipt"]) if "qty_receipt" in cols else 0.0
                    if abs(qty) <= 1e-12 and "qty_source" in cols:
                        qty = _num(r["qty_source"])
                    if qty <= 0:
                        continue

                    unit = _num(r["landed_unit_cost_krw"]) if "landed_unit_cost_krw" in cols else 0.0
                    landed_total = _num(r["landed_total_krw"]) if "landed_total_krw" in cols else 0.0
                    total_amount = _num(r["total_amount"]) if "total_amount" in cols else 0.0
                    if unit <= 0 and landed_total > 0:
                        unit = landed_total / qty
                    if unit <= 0 and "unit_price" in cols:
                        unit = _num(r["unit_price"])

                    amount = landed_total if landed_total > 0 else total_amount
                    if amount <= 0 and unit > 0:
                        amount = unit * qty

                    if amount > 0:
                        stats[pid]["purchase_qty"] += qty
                        stats[pid]["purchase_amount"] += amount

                    date_text = str(r["purchase_date"] or "") if "purchase_date" in cols else ""
                    row_id = int(r["id"] or 0) if "id" in cols else 0
                    key = (date_text, row_id)
                    if unit > 0 and key >= stats[pid]["purchase_latest_key"]:
                        stats[pid]["purchase_latest_key"] = key
                        stats[pid]["latest_purchase_cost"] = unit

        if _table_exists(con, "production_orders"):
            cols = _cols(con, "production_orders")
            required = {"parent_product_id", "qty", "produced_unit_cost"}
            if required.issubset(cols):
                date_col = "production_date" if "production_date" in cols else None
                id_col = "id" if "id" in cols else None
                order_parts = []
                if date_col:
                    order_parts.append(f"COALESCE({date_col},'')")
                if id_col:
                    order_parts.append(id_col)
                order_sql = " ORDER BY " + ",".join(order_parts) if order_parts else ""
                rows = con.execute(
                    "SELECT * FROM production_orders WHERE parent_product_id IS NOT NULL" + order_sql
                ).fetchall()
                for r in rows:
                    pid = int(r["parent_product_id"])
                    if pid not in stats:
                        continue
                    qty = _num(r["qty"])
                    unit = _num(r["produced_unit_cost"])
                    if qty <= 0 or unit <= 0:
                        continue

                    stats[pid]["production_qty"] += qty
                    stats[pid]["production_amount"] += qty * unit

                    date_text = str(r[date_col] or "") if date_col else ""
                    row_id = int(r[id_col] or 0) if id_col else 0
                    key = (date_text, row_id)
                    if key >= stats[pid]["production_latest_key"]:
                        stats[pid]["production_latest_key"] = key
                        stats[pid]["latest_production_cost"] = unit

        out: dict[str, dict[str, Any]] = {}
        for r in products:
            pid = int(r["id"])
            code = _display_code(r["item_code"], r["option_id"])
            if not code:
                continue
            s = stats.get(pid, {})
            pq = _num(s.get("purchase_qty"))
            pa = _num(s.get("purchase_amount"))
            rq = _num(s.get("production_qty"))
            ra = _num(s.get("production_amount"))
            out[code] = {
                "basis_cost": _num(r["unit_cost"]),
                "latest_purchase_cost": s.get("latest_purchase_cost"),
                "average_purchase_cost": (pa / pq) if pq > 0 and pa > 0 else None,
                "latest_production_cost": s.get("latest_production_cost"),
                "average_production_cost": (ra / rq) if rq > 0 and ra > 0 else None,
                "has_production_cost": bool(rq > 0 and ra > 0),
            }
        return out
    except Exception:
        return {}
    finally:
        try:
            con.close()
        except Exception:
            pass


def _money_int_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce").round().astype("Int64")


def _enrich_view(df: pd.DataFrame) -> tuple[pd.DataFrame, bool]:
    out = df.copy()
    out["품목코드"] = out["품목코드"].map(_display_code)
    item_master = {"구분", "상태"}.issubset(set(out.columns))
    lookup = _cost_lookup()

    def fact(code, key):
        return lookup.get(str(code or "").strip(), {}).get(key)

    if "기준원가" not in out.columns:
        out["기준원가"] = out["품목코드"].map(lambda x: fact(x, "basis_cost"))
    else:
        missing = pd.to_numeric(out["기준원가"], errors="coerce").isna()
        if missing.any():
            out.loc[missing, "기준원가"] = out.loc[missing, "품목코드"].map(
                lambda x: fact(x, "basis_cost")
            )
    out["기준원가"] = _money_int_series(out["기준원가"])

    if item_master:
        is_raw = out["구분"].fillna("").astype(str).eq("자체창고")
        is_finished = out["구분"].fillna("").astype(str).eq("쿠팡RG")

        purchase_latest = out["품목코드"].map(lambda x: fact(x, "latest_purchase_cost"))
        purchase_avg = out["품목코드"].map(lambda x: fact(x, "average_purchase_cost"))
        production_latest = out["품목코드"].map(lambda x: fact(x, "latest_production_cost"))
        production_avg = out["품목코드"].map(lambda x: fact(x, "average_production_cost"))

        out["최근매입가"] = _money_int_series(purchase_latest.where(is_raw))
        out["매입평균원가"] = _money_int_series(purchase_avg.where(is_raw))
        out["최근생산원가"] = _money_int_series(production_latest.where(is_finished))
        out["생산평균원가"] = _money_int_series(production_avg.where(is_finished))

        basis = pd.to_numeric(out["기준원가"], errors="coerce").fillna(0)
        has_prod = out["품목코드"].map(
            lambda x: bool(fact(x, "has_production_cost"))
        )
        bad_prod_cost = is_finished & basis.le(0) & has_prod
        out["원가상태"] = ""
        out.loc[bad_prod_cost, "원가상태"] = "⚠ 생산원가 존재"

        preferred = [
            "품목코드", "상품명", "구분", "쿠팡 옵션ID", "기준원가",
            "최근매입가", "매입평균원가", "최근생산원가", "생산평균원가",
            "원가상태", "자체창고", "쿠팡RG", "반품창고", "상태",
        ]
        out = out[
            [c for c in preferred if c in out.columns]
            + [c for c in out.columns if c not in preferred]
        ]
    else:
        qty_cols = [c for c in [*_WAREHOUSES, "불량/폐기"] if c in out.columns]
        total_qty = sum(
            (pd.to_numeric(out[c], errors="coerce").fillna(0) for c in qty_cols),
            start=pd.Series(0.0, index=out.index),
        )
        out["재고금액"] = (
            total_qty * pd.to_numeric(out["기준원가"], errors="coerce").fillna(0)
        ).round().astype("Int64")
        preferred = ["품목코드", "상품명", "기준원가", "재고금액"] + qty_cols
        out = out[
            [c for c in preferred if c in out.columns]
            + [c for c in out.columns if c not in preferred]
        ]

    return out, item_master


def _tab_frame(df: pd.DataFrame, warehouse: str, item_master: bool) -> pd.DataFrame:
    qty = pd.to_numeric(df[warehouse], errors="coerce").fillna(0)
    base_cols = ["품목코드", "상품명", "기준원가"]
    if item_master:
        for c in (
            "최근매입가", "매입평균원가", "최근생산원가", "생산평균원가", "원가상태"
        ):
            if c in df.columns:
                base_cols.append(c)
    base_cols.append(warehouse)

    if item_master and warehouse == "자체창고":
        mask = df["구분"].fillna("").astype(str).eq("자체창고")
    elif item_master and warehouse == "쿠팡RG":
        mask = df["구분"].fillna("").astype(str).eq("쿠팡RG")
    else:
        mask = qty.abs() > 1e-12

    out = df.loc[
        mask,
        [c for c in base_cols if c in df.columns],
    ].copy()
    out = out.rename(columns={warehouse: "현재고"})
    if "현재고" in out.columns:
        out["현재고"] = pd.to_numeric(out["현재고"], errors="coerce").fillna(0)
    if not item_master:
        out["재고금액"] = (
            pd.to_numeric(out["현재고"], errors="coerce").fillna(0)
            * pd.to_numeric(out["기준원가"], errors="coerce").fillna(0)
        ).round().astype("Int64")
    if not out.empty:
        out = out.sort_values(["상품명", "품목코드"], kind="stable").reset_index(drop=True)
    return out


def _frame_kwargs(kwargs, rows: int):
    out = dict(kwargs)
    out["hide_index"] = True
    out["use_container_width"] = True
    out["height"] = min(650, max(180, 38 * (int(rows) + 1)))
    return out


def apply():
    if getattr(st, "_rg_inventory_tabs_v084", False):
        return

    original_dataframe = st.dataframe

    def dataframe_with_inventory_tabs(data=None, *args, **kwargs):
        if isinstance(data, pd.DataFrame) and _REQUIRED.issubset(set(data.columns)):
            view, item_master = _enrich_view(data)

            if item_master and "원가상태" in view.columns:
                issue_count = int((view["원가상태"] == "⚠ 생산원가 존재").sum())
                if issue_count:
                    st.warning(
                        f"기준원가가 0원이지만 생산원가 이력이 있는 쿠팡RG 상품이 {issue_count:,}개 있습니다. "
                        "최근생산원가·생산평균원가와 원가상태 열을 확인하세요."
                    )

            tabs = st.tabs(["전체", "자체창고", "쿠팡RG", "반품창고"])
            with tabs[0]:
                original_dataframe(view, *args, **_frame_kwargs(kwargs, len(view)))

            for tab, warehouse in zip(tabs[1:], _WAREHOUSES):
                sub = _tab_frame(view, warehouse, item_master)
                with tab:
                    if sub.empty:
                        if item_master and warehouse == "자체창고":
                            st.info("등록된 자체창고 품목이 없습니다.")
                        elif item_master and warehouse == "쿠팡RG":
                            st.info("등록된 쿠팡RG 판매상품이 없습니다.")
                        else:
                            st.info(f"{warehouse}에 현재 재고가 있는 상품이 없습니다.")
                    else:
                        original_dataframe(
                            sub, *args, **_frame_kwargs(kwargs, len(sub))
                        )
            return None

        return original_dataframe(data, *args, **kwargs)

    st.dataframe = dataframe_with_inventory_tabs
    st._rg_inventory_tabs_v084 = True
