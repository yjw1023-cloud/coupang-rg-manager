"""v0.9.60 monthly provisional P&L sortable HTML table.

Keeps v0.9.59 deterministic HTML styling while restoring clickable header sorting.
Clicking a header toggles ascending/descending order through query parameters.
"""
from __future__ import annotations

import html
import importlib
import math
from urllib.parse import urlencode


_NUMERIC_COLS = {
    "판매수량", "예상 실현단가", "예상매출", "원가/개", "매출원가", "판매수수료",
    "입출고비", "배송비", "반품충당", "광고비", "광고제외이익", "예상이익", "이익률(%)", "RG비용",
}


def _num(v):
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").replace("%", "").strip()
        x = float(v or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _fmt(col, v):
    if col == "판매수량":
        x = _num(v)
        return f"{int(round(x)):,}" if abs(x-round(x)) < 1e-9 else f"{x:,.1f}"
    if col == "이익률(%)":
        return f"{_num(v):,.1f}%"
    if col in _NUMERIC_COLS:
        return f"{int(round(_num(v))):,}"
    return str(v if v is not None else "")


def _query_value(st_obj, key, default=""):
    try:
        v = st_obj.query_params.get(key, default)
        if isinstance(v, (list, tuple)):
            v = v[-1] if v else default
        return str(v or default)
    except Exception:
        try:
            params = st_obj.experimental_get_query_params()
            v = params.get(key, [default])
            if isinstance(v, (list, tuple)):
                v = v[-1] if v else default
            return str(v or default)
        except Exception:
            return str(default)


def _sort_df(pd_obj, df, sort_col, sort_dir):
    if df is None or df.empty or sort_col not in df.columns:
        return df
    ascending = str(sort_dir).lower() != "desc"
    out = df.copy()
    try:
        if sort_col in _NUMERIC_COLS:
            out["__rg_sort__"] = pd_obj.to_numeric(out[sort_col], errors="coerce").fillna(0.0)
        else:
            out["__rg_sort__"] = out[sort_col].fillna("").astype(str).str.lower()
        out = out.sort_values("__rg_sort__", ascending=ascending, kind="mergesort").drop(columns=["__rg_sort__"])
    except Exception:
        try:
            out = out.sort_values(sort_col, ascending=ascending, kind="mergesort")
        except Exception:
            pass
    return out


def _render_table(st_obj, df, month, q, sort_col, sort_dir):
    if df is None or df.empty:
        st_obj.info("표시할 상품이 없습니다.")
        return

    cols = list(df.columns)
    head_cells = []
    for c in cols:
        active = c == sort_col
        next_dir = "desc" if active and str(sort_dir).lower() != "desc" else "asc"
        arrow = " ▲" if active and str(sort_dir).lower() != "desc" else (" ▼" if active else "")
        params = {
            "pnl_sort": c,
            "pnl_dir": next_dir,
            "pnl_month": month,
        }
        if str(q or "").strip():
            params["pnl_q"] = str(q).strip()
        href = "?" + urlencode(params)
        cls = ' class="product"' if c == "상품명" else ""
        label = html.escape(str(c) + arrow)
        head_cells.append(f'<th{cls}><a href="{html.escape(href, quote=True)}" target="_self">{label}</a></th>')

    rows = []
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            text = html.escape(_fmt(c, r.get(c)))
            cls = ' class="product"' if c == "상품명" else ""
            cells.append(f"<td{cls}>{text}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    st_obj.markdown(
        """
        <style>
        .rg-pnl-table-wrap{width:100%;overflow-x:auto;overflow-y:visible;border:1px solid #cfd8e6;border-radius:10px;background:#fff}
        .rg-pnl-table{border-collapse:collapse;min-width:1520px;width:max-content;font-size:13px;color:#10233f}
        .rg-pnl-table th{position:sticky;top:0;z-index:1;background:#cfe3ff;color:#0d3768;font-weight:900;text-align:center;white-space:nowrap;padding:0;border-right:1px solid #b9cce5;border-bottom:2px solid #8fb3dc}
        .rg-pnl-table th a{display:block;padding:11px 10px;color:#0d3768!important;text-decoration:none!important;font-weight:900;cursor:pointer}
        .rg-pnl-table th a:hover{background:#b9d7ff;color:#062f5e!important}
        .rg-pnl-table td{text-align:center;white-space:nowrap;padding:9px 10px;border-right:1px solid #d8e0ea;border-bottom:1px solid #d8e0ea;background:#fff}
        .rg-pnl-table tbody tr:nth-child(even) td{background:#f8fbff}
        .rg-pnl-table th.product,.rg-pnl-table td.product{text-align:left!important;min-width:430px}
        .rg-pnl-table th.product a{padding-left:24px;text-align:left}
        .rg-pnl-table td.product{padding-left:24px!important}
        .rg-pnl-table tbody tr:hover td{background:#eef6ff}
        </style>
        """ +
        '<div class="rg-pnl-table-wrap"><table class="rg-pnl-table"><thead><tr>' +
        "".join(head_cells) + '</tr></thead><tbody>' + "".join(rows) + '</tbody></table></div>',
        unsafe_allow_html=True,
    )


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
    requested_month = _query_value(st_obj, "pnl_month", cur)
    default_idx = months.index(requested_month) if requested_month in months else (months.index(cur) if cur in months else 0)
    month = st_obj.selectbox("조회 월", months, index=default_idx, key="provisional_month_v0915")

    backfill = {"attempted": 0, "saved": 0, "failed": []}
    try:
        autobackfill = importlib.import_module("pnl_month_autobackfill_v0932")
        backfill = autobackfill.backfill_month(core, month, db)
    except Exception as exc:
        backfill = {"attempted": 0, "saved": 0, "failed": [{"error": str(exc)}]}

    cov = m._coverage(core, db, month)
    m._period_strip(st_obj, month, cov)

    try:
        cleanup = importlib.import_module("provisional_manual_cleanup_v0957")
        cleanup.run_once(core, db)
    except Exception:
        pass

    ad_report = importlib.import_module("provisional_ad_report_v0956")
    ad_dataset = ad_report.render_input(st_obj, core, month, db)

    rows, excluded = m._snapshot_rows_for_month(core, db, month)
    auto_view = m._aggregate(rows)

    if backfill.get("failed"):
        details = "; ".join(str(x.get("error") or "알 수 없는 오류") for x in backfill["failed"][:3])
        st_obj.warning("일부 판매자료의 잠정손익 자동 계산에 실패했습니다. 오류: " + details)
    if cov.get("missing_snapshots", 0):
        st_obj.warning(f"이 달의 판매자료 중 잠정손익 계산값을 아직 만들지 못한 자료가 {cov['missing_snapshots']:,}개 있습니다.")
    if excluded:
        st_obj.warning(f"월을 걸쳐 있는 판매자료 {len(excluded):,}개는 월별로 정확히 나눌 수 없어 월간 합계에서 제외했습니다.")

    if auto_view.empty:
        st_obj.info(f"{month}의 잠정손익을 생성하지 못했습니다.")
        return

    auto_view, ad_meta = ad_report.apply_to_view(auto_view, ad_dataset)
    ad_report.render_applied_notice(st_obj, ad_meta, ad_dataset)

    manual_blocks = importlib.import_module("pnl_manual_blocks_v0955")
    manual_adjust = importlib.import_module("provisional_manual_adjust_v0952")
    adjustments = manual_blocks.render_adjust(st_obj, manual_adjust, core, month, auto_view, db)
    view, adjust_meta = manual_adjust.apply_to_view(auto_view, adjustments)
    if adjust_meta.get("applied"):
        st_obj.caption(f"이 달의 상품별 수동조정 {int(adjust_meta['applied']):,}개가 잠정손익에 반영되어 있습니다.")

    default_q = _query_value(st_obj, "pnl_q", "")
    q = st_obj.text_input("상품 검색", value=default_q, placeholder="상품명 또는 옵션ID 입력", key="provisional_month_search_v0915")
    filtered = m._search(view, q)
    if q.strip():
        st_obj.caption(f"검색 결과 {len(filtered):,}개 / 전체 {len(view):,}개")

    try:
        ui = importlib.import_module("provisional_pnl_ui_v0913")
        ui._inject_css()
        st_obj.markdown(ui._summary_html(ui._summary(filtered)), unsafe_allow_html=True)
    except Exception:
        pass

    sort_col = _query_value(st_obj, "pnl_sort", "")
    sort_dir = _query_value(st_obj, "pnl_dir", "asc")
    sorted_view = _sort_df(pd_obj, filtered, sort_col, sort_dir)
    _render_table(st_obj, sorted_view, month, q, sort_col, sort_dir)

    if cov.get("imports"):
        with st_obj.expander("이 달에 합산되는 판매자료 확인"):
            src = pd_obj.DataFrame([
                {
                    "기간": f"{x['period_start']} ~ {x['period_end']}",
                    "파일": x["file_name"],
                    "잠정손익 저장": "완료" if x["snapshot"] else "미생성",
                }
                for x in cov["imports"]
            ])
            st_obj.dataframe(src, use_container_width=True, hide_index=True)


def apply(target_module):
    target_module.render_provisional_month_page = render_provisional_month_page
    return target_module
