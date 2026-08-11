"""v0.9.15 safe monthly-default P&L routing.

v0.9.32 compatibility:
- keep v0.9.15's safe provisional routing
- lazily load monthly_closing_v0916
- route product confirmed P&L and whole-business monthly closing
- apply grouped sidebar navigation and permanently lock the sidebar expanded
- apply Production/BOM finished/component candidate filtering
- add a dedicated BOM delete tab after the candidate-filter patch
- activate the v0.9.29 provisional snapshot import-id binding fix
- auto-calculate missing monthly provisional snapshots directly from DB
"""
from __future__ import annotations

import importlib


def render_provisional_month_page(st_obj, pd_obj, core, db_path=None):
    m = importlib.import_module("pnl_month_default_v0914")
    db = db_path or core.DEFAULT_DB

    st_obj.markdown("## 📈 잠정손익")
    st_obj.caption("평소 입력한 판매통계를 월 단위로 합산한 잠정 손익입니다. 기본 조회기간은 선택 월의 1일~말일입니다.")

    months = m._available_months(core, db)
    cur = m._current_month()
    default_idx = months.index(cur) if cur in months else 0
    month = st_obj.selectbox("조회 월", months, index=default_idx, key="provisional_month_v0915")

    # v0.9.32: monthly P&L no longer depends on the user opening the per-file
    # provisional page. Missing snapshots are calculated from DB immediately.
    backfill = {"attempted": 0, "saved": 0, "failed": []}
    try:
        autobackfill = importlib.import_module("pnl_month_autobackfill_v0932")
        backfill = autobackfill.backfill_month(core, month, db)
    except Exception as exc:
        backfill = {"attempted": 0, "saved": 0, "failed": [{"error": str(exc)}]}

    cov = m._coverage(core, db, month)
    m._period_strip(st_obj, month, cov)
    rows, excluded = m._snapshot_rows_for_month(core, db, month)
    view = m._aggregate(rows)

    if backfill.get("failed"):
        details = "; ".join(str(x.get("error") or "알 수 없는 오류") for x in backfill["failed"][:3])
        st_obj.warning(
            "일부 판매자료의 잠정손익 자동 계산에 실패했습니다. "
            f"오류: {details}"
        )

    if cov.get("missing_snapshots", 0):
        st_obj.warning(
            f"이 달의 판매자료 중 잠정손익 계산값을 아직 만들지 못한 자료가 {cov['missing_snapshots']:,}개 있습니다. "
            "월 잠정손익 화면에서 자동 계산을 시도했지만 완료되지 않은 자료입니다. 위 오류 내용을 확인해 주세요."
        )
    if excluded:
        st_obj.warning(
            f"월을 걸쳐 있는 판매자료 {len(excluded):,}개는 월별로 정확히 나눌 수 없어 월간 합계에서 제외했습니다. "
            "월 경계에서는 판매자료 기간을 나눠 입력해 주세요."
        )

    if view.empty:
        st_obj.info(
            f"{month}의 잠정손익을 생성하지 못했습니다. "
            "판매자료는 존재하지만 자동 계산 과정에서 오류가 발생했는지 위 안내를 확인해 주세요."
        )
        return

    q = st_obj.text_input("상품 검색", placeholder="상품명 또는 옵션ID 입력", key="provisional_month_search_v0915")
    filtered = m._search(view, q)
    if q.strip():
        st_obj.caption(f"검색 결과 {len(filtered):,}개 / 전체 {len(view):,}개")

    try:
        ui = importlib.import_module("provisional_pnl_ui_v0913")
        ui._inject_css()
        st_obj.markdown(ui._summary_html(ui._summary(filtered)), unsafe_allow_html=True)
    except Exception:
        pass

    show = m._format(filtered)
    try:
        show_obj = show.style.set_properties(**{"text-align": "center"}).set_table_styles(
            [
                {"selector": "th", "props": [("text-align", "center"), ("font-weight", "700")]},
                {"selector": "td", "props": [("text-align", "center")]},
            ]
        )
    except Exception:
        show_obj = show

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
    source = bom_delete.patch_source(source)
    return source
