"""Previous-month actual prefill for the target Excel workbook.

When a target workbook is downloaded for month M:
- keep already-saved goals for M unchanged;
- for products without a saved goal, use actual performance from M-1;
- prefer confirmed actuals per product, falling back to the same final provisional
  calculation path used by the goal/performance screen;
- v0.9.177 reads confirmed sold quantity directly from confirmed_monthly_pnl when
  available, because the legacy confirmed goal helper could carry a zero quantity
  while still containing confirmed revenue/cost figures;
- if confirmed quantity is unavailable, fall back to previous-month provisional
  sold quantity;
- leave products with no previous-month activity blank.

This changes only the downloaded template defaults. It does not write goals until
an operator uploads the edited workbook and presses the save button.
"""
from __future__ import annotations


def _num(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _fill_from_metrics(row, metrics):
    qty = _num(metrics.get("qty"))
    revenue = _num(metrics.get("revenue"))
    commission = _num(metrics.get("commission"))
    rg = _num(metrics.get("rg"))
    returns = _num(metrics.get("returns"))
    ad = _num(metrics.get("ad"))
    cogs = _num(metrics.get("cogs"))
    profit = _num(metrics.get("profit"))

    row["매출"] = revenue
    row["단가"] = revenue / qty if abs(qty) > 1e-12 else 0.0
    row["수량"] = qty
    row["수수료"] = commission
    row["수수료단가"] = commission / qty if abs(qty) > 1e-12 else 0.0
    row["입출고배송비"] = rg
    row["입출고배송비단가"] = rg / qty if abs(qty) > 1e-12 else 0.0
    row["반품처리비"] = returns
    row["광고비"] = ad
    row["상품원가"] = cogs
    row["상품원가단가"] = cogs / qty if abs(qty) > 1e-12 else 0.0
    row["매출이익"] = profit


def _previous_provisional(upload_module, core, db, month, base, old):
    """Use the same final provisional calculation as the visible goal table."""
    try:
        status = upload_module.importlib.import_module("goal_data_status_v0985")
        return status._fresh_provisional(core, db, month, base, old)
    except Exception:
        return old._provisional_details(core, db, month, base)


def _confirmed_qty_by_pid(core, db, month, base, old):
    """Read confirmed sold quantity directly from the confirmed P&L dataframe.

    The old goal helper initializes confirmed quantity from provisional data. For
    closed months that provisional quantity can be unavailable/zero even though the
    confirmed P&L already contains product-level quantity. Prefer the confirmed
    dataframe itself and use option-id mapping only when product_id is absent.
    """
    try:
        mdf, _meta = core.confirmed_monthly_pnl(month)
    except Exception:
        return {}
    if mdf is None or getattr(mdf, "empty", True):
        return {}

    _by_pid, by_oid = old._product_maps(core, db, base)
    qty_columns = [
        col for col in ("qty", "quantity", "판매수량", "sales_quantity")
        if col in mdf.columns
    ]
    if not qty_columns:
        return {}

    out = {}
    for _, r in mdf.iterrows():
        pid = 0
        if "product_id" in mdf.columns:
            try:
                pid = int(round(_num(r.get("product_id"))))
            except Exception:
                pid = 0
        if not pid:
            for col in ("option_id", "옵션ID", "쿠팡 옵션ID"):
                if col not in mdf.columns:
                    continue
                oid = base._oid(r.get(col))
                if oid:
                    pid = int(by_oid.get(oid, 0) or 0)
                    if pid:
                        break
        if not pid:
            continue

        qty = None
        for col in qty_columns:
            value = r.get(col)
            try:
                if upload_module_pd_isna(value):
                    continue
            except Exception:
                pass
            qty = _num(value)
            break
        if qty is None:
            continue
        out[int(pid)] = out.get(int(pid), 0.0) + float(qty)
    return out


def upload_module_pd_isna(value):
    """Small local NaN check without importing pandas at module import time."""
    try:
        import pandas as pd
        return bool(pd.isna(value))
    except Exception:
        return value is None


def apply(upload_module):
    if upload_module is None or getattr(upload_module, "_rg_goal_prev_actual_v09174_applied", False):
        return upload_module

    original = upload_module._goal_template_dataframe

    def goal_template_dataframe(core, db, month: str, base, old):
        df = original(core, db, month, base, old)
        if df is None or df.empty:
            return df

        # Existing goals for the selected target month always win. This prevents
        # a later download from silently replacing targets the operator already saved.
        current_goals = old._detail_goals(core, db, month, base)
        goal_pids = {
            int(r["product_id"])
            for r in current_goals.to_dict("records")
        } if current_goals is not None and not current_goals.empty else set()

        prev_month = base._add_month(month, -1)
        provisional = _previous_provisional(upload_module, core, db, prev_month, base, old)
        confirmed = old._confirmed_details(core, db, prev_month, provisional, base)
        confirmed_qty = _confirmed_qty_by_pid(core, db, prev_month, base, old)

        # Map the option IDs in the target workbook back to the managed product IDs.
        scope = upload_module.importlib.import_module("goal_scope_v0994")
        products = scope.managed_products(core, db, base)
        oid_to_pid = {}
        if products is not None and not products.empty:
            for p in products.itertuples(index=False):
                oid = base._oid(getattr(p, "option_id", "")) or base._oid(getattr(p, "item_code", ""))
                if oid:
                    oid_to_pid[str(oid)] = int(p.id)

        records = df.to_dict("records")
        for row in records:
            oid = str(base._oid(row.get("옵션ID")) or "")
            pid = oid_to_pid.get(oid)
            if pid is None or pid in goal_pids:
                continue

            if confirmed and pid in confirmed:
                metrics = dict(confirmed[pid])
                qty = confirmed_qty.get(pid)
                # If the confirmed dataframe has no quantity column/value, use the
                # final provisional sold quantity for that same previous month.
                if qty is None or (abs(_num(qty)) <= 1e-12 and abs(_num(metrics.get("revenue"))) > 1e-12):
                    qty = _num((provisional.get(pid) or {}).get("qty"))
                metrics["qty"] = _num(qty)
            else:
                metrics = provisional.get(pid)

            if not metrics:
                continue
            _fill_from_metrics(row, metrics)

        return upload_module.pd.DataFrame(records, columns=list(df.columns))

    upload_module._goal_template_dataframe = goal_template_dataframe
    upload_module._rg_goal_prev_actual_v09174_applied = True
    return upload_module
