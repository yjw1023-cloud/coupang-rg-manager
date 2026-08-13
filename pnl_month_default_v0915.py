"""v0.9.92 safe monthly-default routing + goal data coverage/immediate ad refresh.

This module is explicitly purged from sys.modules by app.py on every rerun.
Existing P&L/BOM/product-overview/dashboard-status routing remains unchanged;
v0.9.80 guarantees dynamic pages remain present in grouped sidebar options,
v0.9.83 keeps the styled merged comparison table, v0.9.84 adds Excel target
upload, v0.9.85 shows sales/ad coverage while rebinding current ad reports
immediately in goal provisional performance, and v0.9.92 reloads the goal view
so target-quantity default sorting is picked up immediately after update.
"""
from __future__ import annotations

import importlib


# v0.9.73: apply the BOM candidate guard first so core.add_bom uses the same
# validation rules as the production/BOM screen, then force/verify the requested
# BOM rows through the official core writer.
try:
    _rg_core_v0973 = importlib.import_module("core")
    _rg_bom_guard_v0973 = importlib.import_module("bom_candidate_filter_v0927")
    _rg_bom_guard_v0973.apply(_rg_core_v0973)
    _rg_bom_force_v0973 = importlib.import_module("requested_product_bom_force_v0973")
    _rg_core_v0973._rg_requested_bom_force_v0973_result = _rg_bom_force_v0973.apply(_rg_core_v0973)
    _rg_core_v0973._rg_requested_bom_force_v0973_error = ""
except Exception as _rg_bom_exc_v0973:
    try:
        _rg_core_v0973._rg_requested_bom_force_v0973_error = str(_rg_bom_exc_v0973)
    except Exception:
        pass


def _show_bom_repair_error(st_obj, core):
    err = str(getattr(core, "_rg_requested_bom_force_v0973_error", "") or "").strip()
    if err:
        st_obj.error(
            "신규 3개 상품의 BOM 자동등록에 실패했습니다. "
            "아래 오류를 그대로 알려주세요: " + err
        )


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    _show_bom_repair_error(st_obj, core)
    m = importlib.import_module("pnl_month_v0967")
    return m.render_provisional_month_page(st_obj, pd_obj, core, db_path)


def render_product_confirmed_page(st_obj, pd_obj, core, pnl_module, db_path=None):
    _show_bom_repair_error(st_obj, core)
    m = importlib.import_module("monthly_closing_v0916")
    return m.render_product_confirmed_page(st_obj, pd_obj, core, pnl_module, db_path)


def render_monthly_closing_page(st_obj, pd_obj, core, db_path=None):
    _show_bom_repair_error(st_obj, core)
    m = importlib.import_module("monthly_closing_v0916")
    return m.render_monthly_closing_page(st_obj, pd_obj, core, db_path)


def render_product_overview_page(st_obj, pd_obj, core, db_path=None):
    m = importlib.import_module("product_overview_v0977")
    return m.render_page(st_obj, pd_obj, core, db_path)


def render_dashboard_data_status(st_obj, core, db_path=None):
    m = importlib.import_module("dashboard_data_status_v0978")
    return m.render(st_obj, core, db_path)


def render_goal_management_page(st_obj, pd_obj, core, db_path=None):
    # Goal screen changes should be visible immediately after the updater replaces
    # the module, even when Streamlit keeps the Python process alive.
    import sys
    sys.modules.pop("goal_data_status_v0985", None)
    importlib.invalidate_caches()
    m = importlib.import_module("goal_data_status_v0985")
    return m.render_page(st_obj, pd_obj, core, db_path)


def render_grouped_sidebar(st_obj, options, default_page=None):
    lock = importlib.import_module("sidebar_lock_v0921")
    lock.apply(st_obj)
    m = importlib.import_module("sidebar_groups_v0917")
    overview = importlib.import_module("product_overview_v0977")
    overview.apply_sidebar(m)
    goals = importlib.import_module("goal_management_v0979")
    goals.apply_sidebar(m)

    # The grouped sidebar only renders labels that are present in `options`.
    # Older patched legacy menu lists can survive without newly-added labels,
    # so force the current dynamic pages into the runtime option list here.
    runtime_options = [str(x) for x in list(options or [])]
    for label in (overview.PAGE_LABEL, goals.PAGE_LABEL):
        if label not in runtime_options:
            runtime_options.append(label)

    return m.render_sidebar(st_obj, runtime_options, default_page)


def patch_source(source: str) -> str:
    """Route P&L/pages/navigation/BOM and add dashboard status + goal management."""
    snapshot_fix = importlib.import_module("pnl_snapshot_fix_v0929")
    core_module = importlib.import_module("core")
    views_module = importlib.import_module("pnl_views_v0912")
    snapshot_fix.apply(core_module, views_module)

    legacy_branch = 'elif page == "📈  잠정손익":'
    if legacy_branch not in source:
        raise RuntimeError("v0.9.15 기존 잠정손익 분기를 찾지 못했습니다.")

    menu = '        "📈  잠정손익",\n'
    if menu in source and '        "📄  자료별 잠정손익",\n' not in source:
        source = source.replace(menu, menu + '        "📄  자료별 잠정손익",\n', 1)

    source = source.replace(legacy_branch, 'elif page == "📄  자료별 잠정손익":', 1)
    renamed = 'elif page == "📄  자료별 잠정손익":'
    monthly = (
        'elif page == "📈  잠정손익":\n'
        '    pnl_month_default_v0915.render_provisional_month_page(st, pd, core)\n\n\n'
    )
    source = source.replace(renamed, monthly + renamed, 1)

    closing = importlib.import_module("monthly_closing_v0916")
    source = closing.patch_source(source)
    source = source.replace(
        'monthly_closing_v0916.render_product_confirmed_page(st, pd, core, pnl_views_v0912)',
        'pnl_month_default_v0915.render_product_confirmed_page(st, pd, core, pnl_views_v0912)',
        1,
    )
    source = source.replace(
        'monthly_closing_v0916.render_monthly_closing_page(st, pd, core)',
        'pnl_month_default_v0915.render_monthly_closing_page(st, pd, core)',
        1,
    )

    overview = importlib.import_module("product_overview_v0977")
    source = overview.patch_source(source)

    dashboard_status = importlib.import_module("dashboard_data_status_v0978")
    source = dashboard_status.patch_source(source)

    goals = importlib.import_module("goal_management_v0979")
    source = goals.patch_source(source)

    sidebar = importlib.import_module("sidebar_groups_v0917")
    overview.apply_sidebar(sidebar)
    goals.apply_sidebar(sidebar)
    source = sidebar.patch_source(source)
    lock = importlib.import_module("sidebar_lock_v0921")
    source = lock.patch_source(source)

    bom = importlib.import_module("bom_candidate_filter_v0927")
    bom.apply(core_module)
    source = bom.patch_source(source)

    bom_delete = importlib.import_module("bom_delete_v0928")
    cleanup = importlib.import_module("bom_delete_cleanup_v0933")
    cleanup.apply(bom_delete)
    source = bom_delete.patch_source(source)
    return source
