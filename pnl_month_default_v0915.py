"""v0.9.56 safe monthly-default P&L routing.

v0.9.56:
- remove manual monthly advertising total allocation
- upload Coupang advertising performance reports instead
- attribute advertising spend directly by `광고집행 옵션ID`
- uploaded advertising data is authoritative; sales-ratio allocation is not used
"""
from __future__ import annotations

import importlib


def _sortable_pnl_style(pd_obj, df):
    """Return a Styler whose underlying values remain numeric for grid sorting."""
    show = df.copy()
    if show.empty:
        return show

    qty_cols = ["판매수량"]
    money_cols = [
        "예상 실현단가", "예상매출", "원가/개", "매출원가", "판매수수료",
        "입출고비", "배송비", "반품충당", "광고비", "광고제외이익",
        "예상이익", "RG비용",
    ]
    pct_cols = ["이익률(%)"]

    for col in qty_cols + money_cols + pct_cols:
        if col in show.columns:
            show[col] = pd_obj.to_numeric(show[col], errors="coerce").fillna(0.0)

    formatters = {}
    for col in qty_cols:
        if col in show.columns:
            formatters[col] = lambda v: (
                f"{int(round(v)):,}"
                if abs(float(v) - round(float(v))) < 1e-9
                else f"{float(v):,.1f}"
            )
    for col in money_cols:
        if col in show.columns:
            formatters[col] = lambda v: f"{int(round(float(v))):,}"
    for col in pct_cols:
        if col in show.columns:
            formatters[col] = lambda v: f"{float(v):,.1f}%"

    try:
        return (
            show.style
            .format(formatters, na_rep="")
            .set_properties(**{"text-align": "center"})
            .set_table_styles(
                [
                    {"selector": "th", "props": [("text-align", "center"), ("font-weight", "700")]},
                    {"selector": "td", "props": [("text-align", "center")]},
                ]
            )
        )
    except Exception:
        return show


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    m = importlib.import_module("pnl_month_default_v0914")
    db = db_path or core.DEFAULT_DB

    st_obj.markdown("## 📈 잠정손익")
    st_obj.caption(
        "평소 입력한 판매통계를 월 단위로 합산한 잠정 손익입니다. "
        "광고비는 쿠팡 광고성과보고서의 광고집행 옵션ID 기준으로 직접 반영합니다."
    )

    months = m._available_months(core, db)
    cur = m._current_month()
    default_idx = months.index(cur) if cur in months else 0
    month = st_obj.selectbox("조회 월", months, index=default_idx, key="provisional_month_v0915")

    backfill = {"attempted": 0, "saved": 0, "failed": []}
    try:
        autobackfill = importlib.import_module("pnl_month_autobackfill_v0932")
        backfill = autobackfill.backfill_month(core, month, db)
    except Exception as exc:
        backfill = {"attempted": 0, "saved": 0, "failed": [{"error": str(exc)}]}

    cov = m._coverage(core, db, month)
    m._period_strip(st_obj, month, cov)

    manual_blocks = importlib.import_module("pnl_manual_blocks_v0955")

    # v0.9.56: advertising report upload replaces manual advertising total input.
    ad_report = importlib.import_module("provisional_ad_report_v0956")
    ad_dataset = manual_blocks.render_ad(st_obj, ad_report, core, month, db)

    rows, excluded = m._snapshot_rows_for_month(core, db, month)
    auto_view = m._aggregate(rows)

    if backfill.get("failed"):
        details = "; ".join(
            str(x.get("error") or "알 수 없는 오류") for x in backfill["failed"][:3]
        )
        st_obj.warning(
            "일부 판매자료의 잠정손익 자동 계산에 실패했습니다. "
            f"오류: {details}"
        )

    if cov.get("missing_snapshots", 0):
        st_obj.warning(
            f"이 달의 판매자료 중 잠정손익 계산값을 아직 만들지 못한 자료가 "
            f"{cov['missing_snapshots']:,}개 있습니다. "
            "월 잠정손익 화면에서 자동 계산을 시도했지만 완료되지 않은 자료입니다."
        )
    if excluded:
        st_obj.warning(
            f"월을 걸쳐 있는 판매자료 {len(excluded):,}개는 월별로 정확히 나눌 수 없어 "
            "월간 합계에서 제외했습니다. 월 경계에서는 판매자료 기간을 나눠 입력해 주세요."
        )

    if auto_view.empty:
        st_obj.info(
            f"{month}의 잠정손익을 생성하지 못했습니다. "
            "판매자료는 존재하지만 자동 계산 과정에서 오류가 발생했는지 위 안내를 확인해 주세요."
        )
        return

    # Advertising report is authoritative. This also zeros any old snapshot ad
    # values so historical/manual ratio allocation cannot leak into the new view.
    auto_view, ad_meta = ad_report.apply_to_view(auto_view, ad_dataset)
    ad_report.render_applied_notice(st_obj, ad_meta, ad_dataset)

    # Product-level unit-price / RG-fee manual overrides remain available.
    manual_adjust = importlib.import_module("provisional_manual_adjust_v0952")
    adjustments = manual_blocks.render_adjust(
        st_obj, manual_adjust, core, month, auto_view, db
    )
    view, adjust_meta = manual_adjust.apply_to_view(auto_view, adjustments)
    if adjust_meta.get("applied"):
        st_obj.caption(
            f"이 달의 상품별 수동조정 {int(adjust_meta['applied']):,}개가 잠정손익에 반영되어 있습니다."
        )

    q = st_obj.text_input(
        "상품 검색",
        placeholder="상품명 또는 옵션ID 입력",
        key="provisional_month_search_v0915",
    )
    filtered = m._search(view, q)
    if q.strip():
        st_obj.caption(f"검색 결과 {len(filtered):,}개 / 전체 {len(view):,}개")

    try:
        ui = importlib.import_module("provisional_pnl_ui_v0913")
        ui._inject_css()
        st_obj.markdown(
            ui._summary_html(ui._summary(filtered)),
            unsafe_allow_html=True,
        )
    except Exception:
        pass

    show_obj = _sortable_pnl_style(pd_obj, filtered)
    st_obj.dataframe(
        show_obj,
        use_container_width=True,
        hide_index=True,
        height=min(760, max(230, 38 * (len(filtered) + 1))),
    )

    if cov.get("imports"):
        with st_obj.expander("이 달에 합산되는 판매자료 확인"):
            src = pd_obj.DataFrame(
                [
                    {
                        "기간": f"{x['period_start']} ~ {x['period_end']}",
                        "파일": x["file_name"],
                        "잠정손익 저장": "완료" if x["snapshot"] else "미생성",
                    }
                    for x in cov["imports"]
                ]
            )
            st_obj.dataframe(src, use_container_width=True, hide_index=True)


def render_product_confirmed_page(st_obj, pd_obj, core, pnl_module, db_path=None):
    m = importlib.import_module("monthly_closing_v0916")
    return m.render_product_confirmed_page(st_obj, pd_obj, core, pnl_module, db_path)


def render_monthly_closing_page(st_obj, pd_obj, core, db_path=None):
    m = importlib.import_module("monthly_closing_v0916")
    return m.render_monthly_closing_page(st_obj, pd_obj, core, db_path)


def render_grouped_sidebar(st_obj, options, default_page=None):
    lock = importlib.import_module("sidebar_lock_v0921")
    lock.apply(st_obj)
    m = importlib.import_module("sidebar_groups_v0917")
    return m.render_sidebar(st_obj, options, default_page)


def patch_source(source: str) -> str:
    """Route P&L pages, monthly closing, navigation, BOM UI, and snapshot binding."""
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

    sidebar = importlib.import_module("sidebar_groups_v0917")
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
