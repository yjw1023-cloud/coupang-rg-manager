"""v0.9.174 prefill target Excel with previous-month actual performance.

When a target workbook is downloaded for month M:
- keep already-saved goals for M unchanged;
- for products without a saved goal, use actual performance from M-1;
- prefer confirmed actuals per product, falling back to provisional actuals;
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
        provisional = old._provisional_details(core, db, prev_month, base)
        confirmed = old._confirmed_details(core, db, prev_month, provisional, base)

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

            # Confirmed month-end numbers are preferred. If a product has no
            # confirmed row, use the provisional actuals available for that month.
            metrics = confirmed.get(pid) if confirmed and pid in confirmed else provisional.get(pid)
            if not metrics:
                continue
            _fill_from_metrics(row, metrics)

        return upload_module.pd.DataFrame(records, columns=list(df.columns))

    upload_module._goal_template_dataframe = goal_template_dataframe
    upload_module._rg_goal_prev_actual_v09174_applied = True
    return upload_module
