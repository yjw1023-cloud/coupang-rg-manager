"""v0.9.15 safe monthly-default P&L routing.

Fixes v0.9.14 source-generation SyntaxError by never moving/copying the legacy
provisional branch body. Instead:
- keep the existing legacy provisional branch intact under a new menu label
  `📄  자료별 잠정손익`
- insert a new `📈  잠정손익` branch that renders the monthly aggregate
- reuse v0.9.14's monthly aggregation helpers (the module itself is valid; only
  its old patch_source strategy was unsafe)
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

    cov = m._coverage(core, db, month)
    m._period_strip(st_obj, month, cov)
    rows, excluded = m._snapshot_rows_for_month(core, db, month)
    view = m._aggregate(rows)

    if cov.get("missing_snapshots", 0):
        st_obj.warning(
            f"이 달의 판매자료 중 잠정손익 계산값이 아직 저장되지 않은 자료가 {cov['missing_snapshots']:,}개 있습니다. "
            "왼쪽 메뉴의 '자료별 잠정손익'에서 해당 판매자료를 한 번 열면 월간 잠정손익에 포함됩니다."
        )
    if excluded:
        st_obj.warning(
            f"월을 걸쳐 있는 판매자료 {len(excluded):,}개는 월별로 정확히 나눌 수 없어 월간 합계에서 제외했습니다. "
            "월 경계에서는 판매자료 기간을 나눠 입력해 주세요."
        )

    if view.empty:
        st_obj.info(
            f"{month}에 저장된 잠정손익 자료가 아직 없습니다. "
            "왼쪽 메뉴의 '자료별 잠정손익'에서 해당 월 판매자료를 한 번 열면 월간 합계가 생성됩니다."
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


def patch_source(source: str) -> str:
    """Route monthly and source-level provisional P&L without moving code blocks."""
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
    return source
