"""RG Manager v0.9.83 styled Excel-like goal/performance comparison.

Presentation-only layer over goal_excel_view_v0981:
- merged item cell across 목표/잠정실적/확정실적
- wider item column and compact numeric columns
- centered/bold colored headers and centered cells
- subtle row colors for target/provisional/confirmed states
"""
from __future__ import annotations

import html
import importlib


_COLUMNS = (
    ("매출", "money"),
    ("단가", "money"),
    ("수량", "qty"),
    ("수수료", "money"),
    ("입출고배송비", "money"),
    ("반품처리비", "money"),
    ("광고비", "money"),
    ("상품원가", "money"),
    ("매출이익", "money"),
)


def _esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def _fmt(old, value, kind: str) -> str:
    if kind == "qty":
        return old._fmt_qty(value)
    return old._fmt_money(value)


def _css() -> str:
    return """
<style>
.rg-goal-wrap {
    width:100%;
    overflow-x:auto;
    margin:4px 0 14px 0;
    border:1px solid #cbd5e1;
    border-radius:10px;
    background:#ffffff;
}
.rg-goal-table {
    width:100%;
    min-width:1080px;
    border-collapse:separate;
    border-spacing:0;
    table-layout:fixed;
    font-size:13px;
    color:#172033;
}
.rg-goal-table th,
.rg-goal-table td {
    border-right:1px solid #d4dbe5;
    border-bottom:1px solid #d4dbe5;
    padding:8px 5px;
    text-align:center !important;
    vertical-align:middle !important;
    line-height:1.25;
    white-space:normal;
}
.rg-goal-table th:last-child,
.rg-goal-table td:last-child { border-right:0; }
.rg-goal-table tbody tr:last-child td { border-bottom:0; }
.rg-goal-table thead th {
    background:#dce8f6;
    color:#172b4d;
    font-weight:800;
    text-align:center !important;
    white-space:nowrap;
}
.rg-goal-table .kind {
    font-weight:800;
    white-space:nowrap;
}
.rg-goal-table .item {
    font-weight:700;
    background:#f8fafc;
    padding:9px 10px;
    word-break:keep-all;
    overflow-wrap:anywhere;
}
.rg-goal-table tr.target td:not(.item) { background:#eef5ff; }
.rg-goal-table tr.provisional td:not(.item) { background:#fff7df; }
.rg-goal-table tr.confirmed td:not(.item) { background:#eef8f0; }
.rg-goal-table tr.target .kind { background:#dbeafe !important; color:#174a85; }
.rg-goal-table tr.provisional .kind { background:#ffedb5 !important; color:#6d4b00; }
.rg-goal-table tr.confirmed .kind { background:#dcefdc !important; color:#28623a; }
.rg-goal-table tr.group-start td { border-top:2px solid #aeb9c8; }
.rg-goal-table tbody tr:first-child td { border-top:0; }
.rg-goal-total { min-width:760px; }
</style>
"""


def _total_table(old, target, provisional, confirmed, confirmed_available: bool) -> str:
    headers = ["구분", "매출", "수수료", "입출고배송비", "반품처리비", "광고비", "상품원가", "매출이익"]
    rows = [
        ("목표", "target", target),
        ("잠정실적", "provisional", provisional),
        ("확정실적", "confirmed", confirmed if confirmed_available else None),
    ]
    keys = ["revenue", "commission", "rg", "returns", "ad", "cogs", "profit"]
    parts = [
        '<div class="rg-goal-wrap">',
        '<table class="rg-goal-table rg-goal-total">',
        '<colgroup><col style="width:92px">'
        '<col style="width:110px"><col style="width:100px"><col style="width:125px">'
        '<col style="width:115px"><col style="width:95px"><col style="width:105px">'
        '<col style="width:110px"></colgroup>',
        '<thead><tr>' + ''.join(f'<th>{_esc(h)}</th>' for h in headers) + '</tr></thead><tbody>',
    ]
    for label, cls, metrics in rows:
        parts.append(f'<tr class="{cls}"><td class="kind">{_esc(label)}</td>')
        for key in keys:
            value = None if metrics is None else metrics.get(key)
            parts.append(f'<td>{_esc(old._fmt_money(value))}</td>')
        parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def _detail_table(old, groups) -> str:
    parts = [
        '<div class="rg-goal-wrap">',
        '<table class="rg-goal-table">',
        '<colgroup>'
        '<col style="width:78px">'
        '<col style="width:310px">'
        '<col style="width:88px"><col style="width:78px"><col style="width:62px">'
        '<col style="width:82px"><col style="width:108px"><col style="width:98px">'
        '<col style="width:78px"><col style="width:88px"><col style="width:90px">'
        '</colgroup>',
        '<thead><tr><th>구분</th><th>아이템</th>'
        + ''.join(f'<th>{_esc(name)}</th>' for name, _kind in _COLUMNS)
        + '</tr></thead><tbody>',
    ]
    for item_label, target, provisional, confirmed, confirmed_available in groups:
        row_defs = [
            ("목표", "target", target),
            ("잠정실적", "provisional", provisional),
            ("확정실적", "confirmed", confirmed if confirmed_available else None),
        ]
        for idx, (label, cls, metrics) in enumerate(row_defs):
            group_cls = " group-start" if idx == 0 else ""
            parts.append(f'<tr class="{cls}{group_cls}">')
            parts.append(f'<td class="kind">{_esc(label)}</td>')
            if idx == 0:
                parts.append(f'<td class="item" rowspan="3">{_esc(item_label)}</td>')
            if metrics is None:
                values = {name: "" for name, _kind in _COLUMNS}
            else:
                qty = old._num(metrics.get("qty"))
                revenue = old._num(metrics.get("revenue"))
                values = {
                    "매출": metrics.get("revenue"),
                    "단가": revenue / qty if abs(qty) > 1e-12 else 0.0,
                    "수량": metrics.get("qty"),
                    "수수료": metrics.get("commission"),
                    "입출고배송비": metrics.get("rg"),
                    "반품처리비": metrics.get("returns"),
                    "광고비": metrics.get("ad"),
                    "상품원가": metrics.get("cogs"),
                    "매출이익": metrics.get("profit"),
                }
            for name, kind in _COLUMNS:
                parts.append(f'<td>{_esc(_fmt(old, values.get(name), kind))}</td>')
            parts.append('</tr>')
    parts.append('</tbody></table></div>')
    return ''.join(parts)


def _render_excel_comparison(st, core, db, month: str, base, old):
    goals = old._detail_goals(core, db, month, base)
    provisional = old._provisional_details(core, db, month, base)
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

    st.markdown(_css(), unsafe_allow_html=True)
    st.markdown("### 합계")
    st.markdown(
        _total_table(old, target_total, provisional_total, confirmed_total, confirmed_available),
        unsafe_allow_html=True,
    )

    st.markdown("### 아이템별")
    q = st.text_input(
        "아이템 검색",
        placeholder="상품명 또는 옵션ID 입력",
        key=f"goal_excel_search_v0983_{month}",
    )
    words = str(q or "").strip().lower().split()

    def _sort_key(pid):
        meta = product_map.get(int(pid), {})
        return (str(meta.get("name") or ""), str(meta.get("option_id") or ""))

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

    st.markdown(_detail_table(old, groups), unsafe_allow_html=True)
    if confirmed_available:
        st.caption("잠정실적은 판매자료 기반 예상손익, 확정실적은 월 정산자료 기반 확정손익입니다.")
    else:
        st.caption("아직 확정 정산자료가 없는 월은 확정실적 행을 빈칸으로 표시합니다.")


def render_page(st, pd_obj, core, db_path=None):
    base = importlib.import_module("goal_management_v0979")
    old = importlib.import_module("goal_excel_view_v0981")
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
        key="goal_management_month_v0983",
    )

    tabs = st.tabs(["목표·실적표", "목표 입력", "월말검증", "목표이력"])
    with tabs[0]:
        _render_excel_comparison(st, core, db, month, base, old)
    with tabs[1]:
        old._render_goal_editor(st, core, db, month, base)
    with tabs[2]:
        goals = base._goals(core, db, month)
        actuals, source_label = base._actuals(core, db, month)
        progress, _meta = base._build_progress(goals, actuals, month, core, db)
        base._render_review(st, core, db, month, progress, source_label)
    with tabs[3]:
        base._render_history(st, core, db)
