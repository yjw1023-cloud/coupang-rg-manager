"""v0.9.129 safe monthly-default routing + live hot-update patches.

This module is explicitly purged from sys.modules by app.py on every rerun.
Existing P&L/BOM/dashboard-status routing remains unchanged; product overview,
provisional summary quantity, item cleanup, live overview P&L patches, and
user-confirmed unmatched-sales skipping are applied after updater refreshes.
"""
from __future__ import annotations

import importlib
import sys


# v0.9.115: app.py imports/patches return_discount_v099 and return_sale_match_v0944
# before this module. Because this module is forcibly reloaded on every Streamlit
# rerun, applying the patch here reaches the actual live core.import_sales_stats
# wrapper chain even after hot updates.
try:
    _rg_core_v09115 = importlib.import_module("core")
    _rg_ignore_v09115 = importlib.import_module("sales_ignore_unmanaged_v09115")
    _rg_ignore_v09115.apply(_rg_core_v09115)
    _rg_core_v09115._rg_sales_ignore_unmanaged_v09115_error = ""
except Exception as _rg_ignore_exc_v09115:
    try:
        _rg_core_v09115._rg_sales_ignore_unmanaged_v09115_error = str(_rg_ignore_exc_v09115)
    except Exception:
        pass


# v0.9.124: reload this patch on every Streamlit rerun so an updater refresh can
# replace the v0.9.123 implementation without requiring a full Python restart.
# The v0.9.124 wrapper physically removes user-approved unmatched rows from an
# in-memory workbook copy before the normal sales-import pipeline runs.
try:
    sys.modules.pop("sales_unmatched_confirm_v09123", None)
    importlib.invalidate_caches()
    _rg_core_v09124 = importlib.import_module("core")
    _rg_return_v09124 = importlib.import_module("return_discount_v099")
    _rg_unmatched_v09124 = importlib.import_module("sales_unmatched_confirm_v09123")
    _rg_unmatched_v09124.apply(_rg_core_v09124, _rg_return_v09124)
    _rg_core_v09124._rg_sales_unmatched_confirm_v09124_error = ""
except Exception as _rg_unmatched_exc_v09124:
    try:
        _rg_core_v09124._rg_sales_unmatched_confirm_v09124_error = str(_rg_unmatched_exc_v09124)
    except Exception:
        pass


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


# v0.9.121: obsolete items imported from the old ERP may still carry accounting
# stock even though physical stock is actually zero. Reload and patch the item
# cleanup page on every rerun so updater changes take effect without restart.
try:
    sys.modules.pop("item_archive_cleanup_v09121", None)
    importlib.invalidate_caches()
    _rg_item_delete_v09121 = importlib.import_module("item_delete_ui_v0944")
    _rg_item_archive_v09121 = importlib.import_module("item_archive_cleanup_v09121")
    _rg_item_archive_v09121.apply(_rg_item_delete_v09121)
    _rg_item_delete_v09121._rg_item_archive_cleanup_v09121_error = ""
except Exception as _rg_item_archive_exc_v09121:
    try:
        _rg_item_delete_v09121._rg_item_archive_cleanup_v09121_error = str(_rg_item_archive_exc_v09121)
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
    try:
        ad = importlib.import_module("provisional_ad_report_v0956")
        ad_patch = importlib.import_module("ad_upload_unify_v09103")
        ad_patch.apply(ad)
    except Exception:
        pass

    # v0.9.120: the visible 판매수량 is gross while financial arithmetic uses
    # 순판매수량. Patch the already-loaded presentation module on every rerun so
    # the summary card uses the same net quantity basis as revenue/profit.
    try:
        ui = importlib.import_module("provisional_pnl_ui_v0913")
        qty_patch = importlib.import_module("provisional_summary_qty_v09120")
        qty_patch.apply(ui)
    except Exception:
        pass

    # v0.9.126: pnl_month_v0965 is normally cached after the first page visit.
    # Reload it on every rerun so the return-count/rate presentation is applied
    # immediately after an in-app update without requiring a Python restart.
    try:
        sys.modules.pop("pnl_month_v0965", None)
        importlib.invalidate_caches()
    except Exception:
        pass

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
    # Hot updater replaces these files while Streamlit keeps its Python process
    # alive. Reload the overview and v0.9.122 P&L patch every time the page opens.
    sys.modules.pop("product_overview_v0977", None)
    sys.modules.pop("product_overview_live_pnl_v09122", None)
    importlib.invalidate_caches()
    m = importlib.import_module("product_overview_v0977")

    # v0.9.122: product overview revenue/profit must use the same current
    # provisional-P&L pipeline as the monthly 잠정손익 screen, including current
    # ad-performance reports, return-sale consolidation and manual adjustments.
    live_pnl_patch = importlib.import_module("product_overview_live_pnl_v09122")
    live_pnl_patch.apply(m)

    # v0.9.119: top '자체창고재고' must summarize the selected product's BOM/raw
    # stock, not the finished product's own-warehouse balance.
    stock_patch = importlib.import_module("product_overview_stock_v09119")
    stock_patch.apply(m)
    return m.render_page(st_obj, pd_obj, core, db_path)


def render_dashboard_data_status(st_obj, core, db_path=None):
    # v0.9.129: a new calendar month must not hide the previous month's last
    # entered date. Reload the small dashboard module on every render so an
    # in-app updater refresh is visible without restarting the ERP process.
    sys.modules.pop("dashboard_data_status_v09129", None)
    importlib.invalidate_caches()
    m = importlib.import_module("dashboard_data_status_v09129")
    return m.render(st_obj, core, db_path)


def render_goal_management_page(st_obj, pd_obj, core, db_path=None):
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
