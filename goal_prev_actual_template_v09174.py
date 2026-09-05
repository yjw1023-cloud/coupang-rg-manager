"""Previous-month actual prefill for the target Excel workbook.

v0.9.180 policy:
- downloading target month M always starts from actual performance of M-1;
- existing saved goals for M do NOT block this prefill; the workbook is a fresh
  previous-month baseline that the operator can edit and upload as the new target;
- confirmed previous-month amounts/costs are preferred, with provisional amounts
  used only when confirmed details are unavailable;
- sold quantity is read from imported sales_stats through sales_quantity_v0965;
- the sales_quantity module is force-reloaded on every workbook generation so an
  in-app update cannot keep an old cached order-API quantity implementation alive;
- legacy Coupang order/return API tables are not used for the target workbook.

Downloading the workbook never writes goals. Goals change only after upload/save.
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
    try:
        status = upload_module.importlib.import_module("goal_data_status_v0985")
        return status._fresh_provisional(core, db, month, base, old)
    except Exception:
        return old._provisional_details(core, db, month, base)


def _confirmed_qty_by_pid(core, db, month, base, old):
    """Last-resort fallback quantity from confirmed P&L if such a column exists."""
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
                import pandas as pd
                if pd.isna(value):
                    continue
            except Exception:
                if value is None:
                    continue
            qty = _num(value)
            break
        if qty is not None:
            out[int(pid)] = out.get(int(pid), 0.0) + float(qty)
    return out


def _sales_qty_by_oid(upload_module, core, db, month, base):
    """Previous-month gross sold quantity from imported sales_stats by option ID."""
    try:
        qty_mod = upload_module.importlib.import_module("sales_quantity_v0965")
        # Critical for in-app updates: do not keep the pre-v0.9.179 module cached.
        qty_mod = upload_module.importlib.reload(qty_mod)
        counts, _meta = qty_mod.month_counts(core, db, month)
    except Exception:
        return {}

    out = {}
    for raw_oid, info in (counts or {}).items():
        oid = str(base._oid(raw_oid) or "")
        if oid:
            out[oid] = _num((info or {}).get("sales_qty"))
    return out


def apply(upload_module):
    if upload_module is None or getattr(upload_module, "_rg_goal_prev_actual_v09174_applied", False):
        return upload_module

    original = upload_module._goal_template_dataframe

    def goal_template_dataframe(core, db, month: str, base, old):
        df = original(core, db, month, base, old)
        if df is None or df.empty:
            return df

        prev_month = base._add_month(month, -1)
        provisional = _previous_provisional(upload_module, core, db, prev_month, base, old)
        confirmed = old._confirmed_details(core, db, prev_month, provisional, base)
        confirmed_qty = _confirmed_qty_by_pid(core, db, prev_month, base, old)
        direct_sales_qty = _sales_qty_by_oid(upload_module, core, db, prev_month, base)

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
            if pid is None:
                continue

            # Always replace the downloaded row with previous-month actuals.
            # Existing current-month targets are intentionally not preserved here.
            if confirmed and pid in confirmed:
                metrics = dict(confirmed[pid])
            else:
                source_metrics = provisional.get(pid)
                metrics = dict(source_metrics) if source_metrics else None
            if not metrics:
                continue

            if oid in direct_sales_qty:
                qty = direct_sales_qty[oid]
            else:
                qty = confirmed_qty.get(pid)
                if qty is None:
                    qty = _num((provisional.get(pid) or {}).get("qty"))
            metrics["qty"] = _num(qty)
            _fill_from_metrics(row, metrics)

        return upload_module.pd.DataFrame(records, columns=list(df.columns))

    upload_module._goal_template_dataframe = goal_template_dataframe
    upload_module._rg_goal_prev_actual_v09174_applied = True
    return upload_module
