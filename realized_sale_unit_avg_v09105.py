"""v0.9.105 weighted-average realized sale unit for provisional P&L.

Rules
-----
- Manual expected sale unit remains the highest-priority override.
- When no manual sale unit exists, use cumulative confirmed settlement history:
  total realized sales / total positive sold quantity for 주문 정산 rows.
- Do not use only the latest settlement row, which can be distorted by a coupon
  or one-off discount.
"""
from __future__ import annotations

import functools

import pandas as pd

_APPLIED = False


def apply(core):
    global _APPLIED
    if _APPLIED or getattr(core, "_rg_realized_sale_unit_avg_v09105", False):
        return core

    original_get_products = core.get_products

    @functools.wraps(original_get_products)
    def get_products(db_path=core.DEFAULT_DB):
        df = original_get_products(db_path)
        if not isinstance(df, pd.DataFrame) or df.empty or "id" not in df.columns:
            return df

        try:
            with core._conn(db_path) as con:
                hist = pd.read_sql_query(
                    """SELECT product_id,
                              COALESCE(SUM(realized_sales),0) AS total_realized_sales,
                              COALESCE(SUM(qty),0) AS total_sales_qty
                       FROM settlement_sales
                       WHERE product_id IS NOT NULL
                         AND qty > 0
                         AND transaction_type = '주문 정산'
                       GROUP BY product_id""",
                    con,
                )
        except Exception:
            return df

        if hist.empty:
            return df

        hist["hist_sale_unit_avg"] = hist.apply(
            lambda r: float(r["total_realized_sales"] or 0) / float(r["total_sales_qty"] or 0)
            if float(r["total_sales_qty"] or 0) > 0
            else 0.0,
            axis=1,
        )
        avg_map = {
            int(r["product_id"]): float(r["hist_sale_unit_avg"] or 0)
            for _, r in hist.iterrows()
            if float(r["hist_sale_unit_avg"] or 0) > 0
        }

        if "hist_sale_unit" not in df.columns:
            df["hist_sale_unit"] = 0.0
        for idx in df.index:
            try:
                pid = int(df.at[idx, "id"])
            except Exception:
                continue
            if pid in avg_map:
                df.at[idx, "hist_sale_unit"] = avg_map[pid]
        return df

    core.get_products = get_products
    core._rg_realized_sale_unit_avg_v09105 = True
    _APPLIED = True
    return core
