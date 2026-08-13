"""RG Manager goal screen data coverage + immediate ad refresh.

- Shows sales/ad coverage dates for the selected month directly above the goal table.
- Sales provisional values use the normal refreshed sales snapshot pipeline.
- Advertising cost is always re-bound from the currently saved ad-performance reports,
  so a newly uploaded/deleted ad report is reflected immediately without waiting for
  a sales snapshot rebuild.
- v0.9.92: item rows default to target quantity descending.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
import importlib


def _month_bounds(month: str):
    y, m = [int(x) for x in str(month).split("-")]
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


def _coverage_for_month(core, db, month: str, kind: str):
    status_mod = importlib.import_module("dashboard_data_status_v0978")
    start, end = _month_bounds(month)
    today = date.today()
    current = today.strftime("%Y-%m")
    if month < current:
        limit = end
    elif month == current:
        limit = min(end, today)
    else:
        limit = start - timedelta(days=1)

    covered = set()
    files = set()
    try:
        periods = status_mod._load_periods(core, db, kind)
    except Exception:
        periods = []

    if limit >= start:
        for a, b, file_name in periods:
            if b < start or a > limit:
                continue
            left = max(a, start)
            right = min(b, limit)
            if left > right:
                continue
            if file_name:
                files.add(str(file_name))
            d = left
            while d <= right:
                covered.add(d)
                d += timedelta(days=1)

    cursor = start
    continuous_end = None
    while cursor <= limit and cursor in covered:
        continuous_end = cursor
        cursor += timedelta(days=1)
    latest = max(covered) if covered else None
    return {
        "continuous_end": continuous_end,
        "latest": latest,
        "files": len(files),
    }


def _coverage_text(status, month: str) -> str:
    end = status.get("continuous_end")
    latest = status.get("latest")
    if end is None:
        return "입력 없음"
    text = f"{end.month}월 {end.day}일까지"
    if latest is not None and latest > end:
        text += f" · 이후 {latest.month}월 {latest.day}일까지 자료 있으나 중간 누락"
    return text


def _render_coverage(st, core, db, month: str):
    sales = _coverage_for_month(core, db, month, "sales")
    ads = _coverage_for_month(core, db, month, "ads")
    st.markdown("#### 잠정실적 반영범위")
    c1, c2 = st.columns(2)
    with c1:
        st.info(f"📊 매출·판매자료: **{_coverage_text(sales, month)}**")
    with c2:
        st.info(f"📣 광고비자료: **{_coverage_text(ads, month)}**")


def _fresh_provisional(core, db, month: str, base, old):
    # Sales/revenue/cost/profit base: existing snapshot logic.  This call itself
    # invokes pnl_snapshot_refresh_v0966.refresh_month(), so new sales imports are
    # rebuilt before the table is rendered.
    out = old._provisional_details(core, db, month, base)

    # Replace whatever ad value is embedded in the snapshot with the ad reports
    # currently stored in provisional_ad_report_* tables.
    ad_mod = importlib.import_module("provisional_ad_report_v0956")
    try:
        dataset = ad_mod.load_month(core, month, db)
    except Exception:
        dataset = {"items": {}, "imports": []}

    # First remove the snapshot ad effect from profit.
    for x in out.values():
        old_ad = abs(old._num(x.get("ad")))
        x["profit"] = old._num(x.get("profit")) + old_ad
        x["ad"] = 0.0

    _by_pid, by_oid = old._product_maps(core, db, base)
    for oid, item in dict(dataset.get("items") or {}).items():
        pid = by_oid.get(base._oid(oid))
        if pid is None:
            continue
        ad = abs(old._num(item.get("ad_spend")))
        x = out.setdefault(
            int(pid),
            {"qty":0.0,"revenue":0.0,"commission":0.0,"rg":0.0,
             "returns":0.0,"ad":0.0,"cogs":0.0,"profit":0.0,"source":"잠정"},
        )
        x["ad"] = ad
        x["profit"] = old._num(x.get("profit")) - ad
    return out


def _render_comparison(st, core, db, month: str, base, old, styled):
    goals = old._detail_goals(core, db, month, base)
    provisional = _fresh_provisional(core, db, month, base, old)
    confirmed = old._confirmed_details(core, db, month, provisional, base)
    confirmed_available = bool(confirmed)
    product_map, _ = old._product_maps(core, db, base)
    goal_map = {int(r["product_id"]): r for r in goals.to_dict("records")}

    pids = set(goal_map) | set(provisional) | set(confirmed)
    if not pids:
        st.info("선택한 월에 목표 또는 실적 자료가 없습니다. '목표 입력' 탭에서 목표를 먼저 입력하세요.")
        return

    target_total = old._sum_metrics(old._target_metrics(goal_map[pid]) for pid in pids if pid in goal_map)
    provisional_total = old._sum_metrics(provisional.get(pid) for pid in pids)
    confirmed_total = old._sum_metrics(confirmed.get(pid) for pid in pids) if confirmed_available else None

    st.markdown(styled._css(), unsafe_allow_html=True)
    st.markdown("### 합계")
    st.markdown(
        styled._total_table(old, target_total, provisional_total, confirmed_total, confirmed_available),
        unsafe_allow_html=True,
    )

    st.markdown("### 아이템별")
    q = st.text_input(
        "아이템 검색",
        placeholder="상품명 또는 옵션ID 입력",
        key=f"goal_excel_search_v0985_{month}",
    )
    words = str(q or "").strip().lower().split()

    def _sort_key(pid):
        meta = product_map.get(int(pid), {})
        target_qty = old._num(goal_map.get(int(pid), {}).get("target_qty"))
        # Default order: larger target quantity first.  Ties use product name/option ID.
        return (-target_qty, str(meta.get("name") or ""), str(meta.get("option_id") or ""))

    groups = []
    for pid in sorted(pids, key=_sort_key):
        meta = product_map.get(int(pid), {})
        name = str(meta.get("name") or f"상품 {pid}")
        oid = str(meta.get("option_id") or "")
        hay = f"{name} {oid}".lower()
        if words and not all(w in hay for w in words):
            continue
        item_label = f"{name} · {oid}" if oid else name
        target = old._target_metrics(goal_map[pid]) if pid in goal_map else old._blank_metrics()
        prov = provisional.get(pid, old._blank_metrics())
        conf = confirmed.get(pid) if confirmed_available else None
        groups.append((item_label, target, prov, conf, confirmed_available))

    if not groups:
        st.info("검색 조건에 맞는 아이템이 없습니다.")
        return
    st.markdown(styled._detail_table(old, groups), unsafe_allow_html=True)
    if confirmed_available:
        st.caption("잠정실적은 판매자료와 현재 광고성과보고서를 기준으로 계산하며, 확정실적은 월 정산자료 기준입니다.")
    else:
        st.caption("잠정실적은 판매자료와 현재 광고성과보고서를 즉시 반영합니다. 아직 확정 정산자료가 없으면 확정실적은 빈칸으로 표시됩니다.")


def render_page(st, pd_obj, core, db_path=None):
    base = importlib.import_module("goal_management_v0979")
    old = importlib.import_module("goal_excel_view_v0981")
    styled = importlib.import_module("goal_excel_view_v0983")
    upload = importlib.import_module("goal_excel_upload_v0984")
    db = db_path or core.DEFAULT_DB
    old._ensure_detail_schema(core, db, base)

    st.markdown(base._SELECT_CSS, unsafe_allow_html=True)
    st.markdown("## 🎯 목표·실적관리")
    st.caption("목표와 잠정실적·확정실적을 엑셀처럼 한 표에서 비교합니다.")

    months = base._month_options()
    month = st.selectbox(
        "목표·검증 월",
        months,
        index=0,
        format_func=base._month_label,
        key="goal_management_month_v0985",
    )

    tabs = st.tabs(["목표·실적표", "목표 입력", "월말검증", "목표이력"])
    with tabs[0]:
        _render_coverage(st, core, db, month)
        _render_comparison(st, core, db, month, base, old, styled)
    with tabs[1]:
        upload._render_excel_goal_input(st, core, db, month, base, old)
    with tabs[2]:
        goals = base._goals(core, db, month)
        actuals, source_label = base._actuals(core, db, month)
        progress, _meta = base._build_progress(goals, actuals, month, core, db)
        base._render_review(st, core, db, month, progress, source_label)
    with tabs[3]:
        base._render_history(st, core, db)
