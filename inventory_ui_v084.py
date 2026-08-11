"""Inventory/item-master presentation for RG Manager v0.9.42.

- Keep warehouse tabs: all / own / Coupang RG / returns.
- Hide internal CP- prefix from user-facing product codes.
- Item master warehouse tabs show basis cost, latest purchase cost and weighted
  average purchase cost together with current stock.
- Inventory warehouse tabs show basis cost and inventory value.
- Purchase averages are quantity-weighted: total purchase amount / total qty.

No persisted cost or inventory value is rewritten by this presentation module.
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


def _purchase_cost_lookup() -> dict[str, dict[str, Any]]:
    """Return cost facts keyed by user-facing product code.

    latest_purchase_cost is the latest historical landed unit cost.
    average_purchase_cost is quantity-weighted over all linked purchase rows.
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
            "SELECT id,item_code,option_id,name,unit_cost FROM products"
        ).fetchall()
        stats: dict[int, dict[str, Any]] = {
            int(r["id"]): {
                "qty": 0.0,
                "amount": 0.0,
                "latest_key": ("", -1),
                "latest_cost": None,
            }
            for r in products
        }

        if _table_exists(con, "purchase_lines"):
            cols = {
                str(r["name"])
                for r in con.execute("PRAGMA table_info(purchase_lines)").fetchall()
            }
            if "product_id" in cols:
                rows = con.execute(
                    "SELECT * FROM purchase_lines WHERE product_id IS NOT NULL ORDER BY COALESCE(purchase_date,''),id"
                ).fetchall()
                for r in rows:
                    pid = int(r["product_id"])
                    if pid not in stats:
                        continue
                    qty = 0.0
                    if "qty_receipt" in cols:
                        qty = _num(r["qty_receipt"])
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
                        stats[pid]["qty"] += qty
                        stats[pid]["amount"] += amount

                    date_text = str(r["purchase_date"] or "") if "purchase_date" in cols else ""
                    row_id = int(r["id"] or 0) if "id" in cols else 0
                    key = (date_text, row_id)
                    if unit > 0 and key >= stats[pid]["latest_key"]:
                        stats[pid]["latest_key"] = key
                        stats[pid]["latest_cost"] = unit

        out: dict[str, dict[str, Any]] = {}
        for r in products:
            pid = int(r["id"])
            code = _display_code(r["item_code"], r["option_id"])
            if not code:
                continue
            s = stats.get(pid, {})
            qty = _num(s.get("qty"))
            amount = _num(s.get("amount"))
            out[code] = {
                "basis_cost": _num(r["unit_cost"]),
                "latest_purchase_cost": s.get("latest_cost"),
                "average_purchase_cost": (amount / qty) if qty > 0 and amount > 0 else None,
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
    lookup = _purchase_cost_lookup()

    def fact(code, key):
        return lookup.get(str(code or "").strip(), {}).get(key)

    if "기준원가" not in out.columns:
        out["기준원가"] = out["품목코드"].map(lambda x: fact(x, "basis_cost"))
    else:
        # Preserve the item-master value already loaded from products.unit_cost.
        missing = pd.to_numeric(out["기준원가"], errors="coerce").isna()
        if missing.any():
            out.loc[missing, "기준원가"] = out.loc[missing, "품목코드"].map(
                lambda x: fact(x, "basis_cost")
            )
    out["기준원가"] = _money_int_series(out["기준원가"])

    if item_master:
        out["최근매입가"] = _money_int_series(
            out["품목코드"].map(lambda x: fact(x, "latest_purchase_cost"))
        )
        out["매입평균원가"] = _money_int_series(
            out["품목코드"].map(lambda x: fact(x, "average_purchase_cost"))
        )
        preferred = [
            "품목코드", "상품명", "구분", "쿠팡 옵션ID", "기준원가",
            "최근매입가", "매입평균원가", "자체창고", "쿠팡RG", "반품창고", "상태",
        ]
        out = out[[c for c in preferred if c in out.columns] + [c for c in out.columns if c not in preferred]]
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
        out = out[[c for c in preferred if c in out.columns] + [c for c in out.columns if c not in preferred]]

    return out, item_master


def _tab_frame(df: pd.DataFrame, warehouse: str, item_master: bool) -> pd.DataFrame:
    qty = pd.to_numeric(df[warehouse], errors="coerce").fillna(0)
    base_cols = ["품목코드", "상품명", "기준원가"]
    if item_master:
        for c in ("최근매입가", "매입평균원가"):
            if c in df.columns:
                base_cols.append(c)
    base_cols.append(warehouse)
    out = df.loc[qty.abs() > 1e-12, [c for c in base_cols if c in df.columns]].copy()
    out = out.rename(columns={warehouse: "현재고"})
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

            tabs = st.tabs(["전체", "자체창고", "쿠팡RG", "반품창고"])
            with tabs[0]:
                original_dataframe(view, *args, **_frame_kwargs(kwargs, len(view)))

            for tab, warehouse in zip(tabs[1:], _WAREHOUSES):
                sub = _tab_frame(view, warehouse, item_master)
                with tab:
                    if sub.empty:
                        st.info(f"{warehouse}에 현재 재고가 있는 상품이 없습니다.")
                    else:
                        original_dataframe(sub, *args, **_frame_kwargs(kwargs, len(sub)))
            return None

        return original_dataframe(data, *args, **kwargs)

    st.dataframe = dataframe_with_inventory_tabs
    st._rg_inventory_tabs_v084 = True
