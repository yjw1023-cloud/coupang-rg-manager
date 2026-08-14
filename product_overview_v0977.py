"""RG Manager v0.9.118 product overview UX refresh.

- Show only active finished products.
- Default period to recent 90 days (completed days only).
- Replace oversized Streamlit metrics with compact 7-item KPI cards.
- Add purchase history for the selected finished product.
- Sort sales/provisional/confirmed/purchase histories newest first.
- Render readable tables with colored sticky headers and compact typography.
"""
from __future__ import annotations

from datetime import date, timedelta
import html
import importlib
import math
from typing import Any

import pandas as pd


_base = importlib.import_module("product_overview_v0976")
PAGE_LABEL = _base.PAGE_LABEL
apply_sidebar = _base.apply_sidebar
patch_source = _base.patch_source


_UI_CSS = r"""
<style>
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background: #ffffff !important;
    border: 2px solid #7b899b !important;
    border-radius: 8px !important;
    box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06) !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
    border-color: #52657c !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div {
    border-color: #2f6db5 !important;
    box-shadow: 0 0 0 2px rgba(47, 109, 181, 0.15) !important;
}

.rg-kpi-grid {
    display:grid;
    grid-template-columns:repeat(4,minmax(0,1fr));
    gap:10px;
    margin:12px 0 18px 0;
}
.rg-kpi-card {
    background:#ffffff;
    border:1px solid #d8e1ec;
    border-radius:12px;
    padding:14px 16px 13px 16px;
    min-height:76px;
    box-shadow:0 2px 8px rgba(15,23,42,.045);
}
.rg-kpi-label {
    font-size:12px;
    font-weight:700;
    color:#607086;
    margin-bottom:6px;
    letter-spacing:-.1px;
}
.rg-kpi-value {
    font-size:22px;
    line-height:1.15;
    font-weight:750;
    color:#102a4c;
    letter-spacing:-.5px;
}
.rg-kpi-value.negative { color:#b42318; }

.rg-section-head {
    display:flex;
    align-items:center;
    justify-content:space-between;
    margin:16px 0 8px 0;
    padding:9px 12px;
    background:#eaf2fb;
    border-left:4px solid #2f6db5;
    border-radius:7px;
    color:#173a63;
    font-size:15px;
    font-weight:800;
}
.rg-section-head small {
    color:#718197;
    font-size:11px;
    font-weight:600;
}
.rg-table-wrap {
    width:100%;
    max-height:430px;
    overflow:auto;
    border:1px solid #d7e0eb;
    border-radius:9px;
    background:white;
    margin-bottom:16px;
}
table.rg-overview-table {
    width:100%;
    border-collapse:separate;
    border-spacing:0;
    font-size:12.5px;
    color:#24364b;
}
table.rg-overview-table thead th {
    position:sticky;
    top:0;
    z-index:1;
    background:#dce9f7;
    color:#173a63;
    font-size:12px;
    font-weight:800;
    padding:10px 11px;
    text-align:center;
    border-bottom:1px solid #c2d3e6;
    white-space:nowrap;
}
table.rg-overview-table tbody td {
    padding:9px 11px;
    border-bottom:1px solid #edf1f5;
    text-align:right;
    white-space:nowrap;
}
table.rg-overview-table tbody td:first-child,
table.rg-overview-table tbody td:nth-child(2) { text-align:left; }
table.rg-overview-table tbody tr:nth-child(even) { background:#f8fafc; }
table.rg-overview-table tbody tr:hover { background:#eef5fc; }

/* Make the page tabs look like deliberate navigation instead of bare text. */
div[data-baseweb="tab-list"] { gap:6px !important; }
button[data-baseweb="tab"] {
    border-radius:7px 7px 0 0 !important;
    padding:7px 13px !important;
    font-size:13px !important;
    color:#4b6178 !important;
    background:#edf2f7 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color:#ffffff !important;
    background:#2f5f91 !important;
    font-weight:800 !important;
}

@media (max-width: 900px) {
    .rg-kpi-grid { grid-template-columns:repeat(2,minmax(0,1fr)); }
}
</style>
"""


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = (
                v.replace(",", "")
                .replace("원", "")
                .replace("개", "")
                .replace("건", "")
                .replace("%", "")
                .strip()
            )
        x = float(v or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _period_bounds(label: str):
    today = date.today()
    yesterday = today - timedelta(days=1)
    if label == "최근 90일":
        return yesterday - timedelta(days=89), yesterday
    if label == "최근 30일":
        return yesterday - timedelta(days=29), yesterday
    if label == "이번 달":
        start = today.replace(day=1)
        return start, yesterday if yesterday >= start else start
    if label == "지난 달":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    return None, None


def _money(v: Any) -> str:
    return f"{int(round(_num(v))):,}원"


def _qty(v: Any) -> str:
    return _base._fmt_qty(v)


def _pct(v: Any) -> str:
    return _base._fmt_pct(v)


def _card(label: str, value: str, negative: bool = False) -> str:
    cls = "rg-kpi-value negative" if negative else "rg-kpi-value"
    return (
        '<div class="rg-kpi-card">'
        f'<div class="rg-kpi-label">{html.escape(label)}</div>'
        f'<div class="{cls}">{html.escape(value)}</div>'
        '</div>'
    )


def _render_cards(st, items):
    cards = "".join(_card(label, value, negative) for label, value, negative in items)
    st.markdown(f'<div class="rg-kpi-grid">{cards}</div>', unsafe_allow_html=True)


def _render_table(st, title: str, df: pd.DataFrame, empty_text: str, note: str = "최신순"):
    st.markdown(
        f'<div class="rg-section-head"><span>{html.escape(title)}</span>'
        f'<small>{html.escape(note)}</small></div>',
        unsafe_allow_html=True,
    )
    if df is None or df.empty:
        st.caption(empty_text)
        return
    table = df.to_html(index=False, classes="rg-overview-table", border=0, escape=True)
    st.markdown(f'<div class="rg-table-wrap">{table}</div>', unsafe_allow_html=True)


def _filtered_purchases(pd_obj, core, db, product_id: int, start, end):
    try:
        purchase = importlib.import_module("purchase_history_v092")
        hist = purchase._history(pd_obj, core, db, int(product_id))
    except Exception:
        return pd_obj.DataFrame()
    if hist is None or hist.empty:
        return pd_obj.DataFrame()
    out = hist.copy()
    if "purchase_date" in out.columns:
        dates = pd_obj.to_datetime(out["purchase_date"], errors="coerce")
        mask = pd_obj.Series(True, index=out.index)
        if start is not None:
            mask &= dates.dt.date >= start
        if end is not None:
            mask &= dates.dt.date <= end
        out = out.loc[mask].copy()
        out["_purchase_sort"] = pd_obj.to_datetime(out["purchase_date"], errors="coerce")
        out = out.sort_values(["_purchase_sort", "id"], ascending=[False, False], kind="stable")
        out = out.drop(columns=["_purchase_sort"], errors="ignore")
    return out


def _purchase_view(pd_obj, hist):
    if hist is None or hist.empty:
        return pd_obj.DataFrame()
    rows = []
    for r in hist.itertuples(index=False):
        rows.append(
            {
                "매입일": str(getattr(r, "purchase_date", "") or ""),
                "차수": str(getattr(r, "purchase_batch", "") or ""),
                "매입수량": _qty(getattr(r, "qty", 0)),
                "개당 매입가": _money(getattr(r, "unit_cost", 0)),
                "매입금액": _money(getattr(r, "amount", 0)),
                "매입자료 상품명": str(getattr(r, "source_name", "") or ""),
            }
        )
    return pd_obj.DataFrame(rows)


def render_page(st, pd_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    st.markdown(_UI_CSS, unsafe_allow_html=True)
    st.markdown("## 📊 상품 통합현황")
    st.caption("완제품 하나를 선택하면 판매·매입·재고·BOM·반품·광고·손익을 한 화면에서 확인합니다.")

    products = _base._finished_products(core, db)
    if products is None or products.empty:
        st.info("조회할 완제품이 없습니다.")
        return

    # v0.9.117+: archived products stay in DB for history but never clutter this selector.
    if "active" in products.columns:
        products = products[pd_obj.to_numeric(products["active"], errors="coerce").fillna(1).astype(int).eq(1)].copy()
    if products.empty:
        st.info("사용중인 완제품이 없습니다.")
        return

    q = st.text_input(
        "완제품 검색",
        placeholder="상품명 또는 쿠팡 옵션ID 입력",
        key="product_overview_search_v09118",
    )
    filtered = products.copy()
    if str(q or "").strip():
        terms = [x for x in str(q).strip().lower().split() if x]
        hay = (
            filtered[["name", "item_code", "option_id"]]
            .fillna("")
            .astype(str)
            .agg(" ".join, axis=1)
            .str.lower()
        )
        mask = pd_obj.Series(True, index=filtered.index)
        for term in terms:
            mask &= hay.str.contains(term, regex=False, na=False)
        filtered = filtered.loc[mask].copy()
    if filtered.empty:
        st.warning("검색 조건에 맞는 완제품이 없습니다.")
        return

    ids = filtered["id"].astype(int).tolist()
    labels = {int(r.id): _base._product_label(r) for r in filtered.itertuples(index=False)}
    selected_id = st.selectbox(
        "완제품 선택",
        ids,
        format_func=lambda x: labels.get(int(x), str(x)),
        key="product_overview_product_v09118",
    )

    row = products[products["id"].astype(int).eq(int(selected_id))].iloc[0]
    option_id = _base._oid(row.get("option_id")) or _base._oid(row.get("item_code"))

    period = st.selectbox(
        "조회기간",
        ["최근 90일", "이번 달", "지난 달", "최근 30일", "전체"],
        index=0,
        key="product_overview_period_v09118",
    )
    start, end = _period_bounds(str(period))

    inv = _base._inventory(core, db, int(selected_id))
    bom, max_make = _base._bom(core, db, int(selected_id))
    sales, sales_meta = _base._sales_history(core, db, int(selected_id), start, end)
    returns = _base._return_txns(core, db, int(selected_id), start, end)
    ads = _base._ad_history(core, db, option_id, start, end)
    prov = _base._provisional_history(core, db, option_id, start, end)
    confirmed = _base._confirmed_history(core, db, int(selected_id), option_id, start, end)
    purchases = _filtered_purchases(pd_obj, core, db, int(selected_id), start, end)

    rg_stock = 0.0
    own_stock = 0.0
    if inv is not None and not inv.empty:
        rg_stock = float(pd_obj.to_numeric(inv.loc[inv["창고"].eq("쿠팡RG"), "현재고"], errors="coerce").fillna(0).sum())
        own_stock = float(pd_obj.to_numeric(inv.loc[inv["창고"].eq("자체창고"), "현재고"], errors="coerce").fillna(0).sum())

    gross = float(pd_obj.to_numeric(sales["판매수량"], errors="coerce").fillna(0).sum()) if sales is not None and not sales.empty else 0.0
    ret_signal = float(pd_obj.to_numeric(sales["반품신호"], errors="coerce").fillna(0).sum()) if sales is not None and not sales.empty else 0.0
    return_rate = (ret_signal / gross * 100) if gross > 0 else 0.0
    ad_total = float(pd_obj.to_numeric(ads["ad_spend"], errors="coerce").fillna(0).sum()) if ads is not None and not ads.empty else 0.0

    prov_revenue = float(pd_obj.to_numeric(prov["예상매출"], errors="coerce").fillna(0).sum()) if prov is not None and not prov.empty else None
    prov_profit = float(pd_obj.to_numeric(prov["예상이익"], errors="coerce").fillna(0).sum()) if prov is not None and not prov.empty else None
    conf_revenue = float(pd_obj.to_numeric(confirmed["실현매출"], errors="coerce").fillna(0).sum()) if confirmed is not None and not confirmed.empty else None
    conf_profit = float(pd_obj.to_numeric(confirmed["확정이익"], errors="coerce").fillna(0).sum()) if confirmed is not None and not confirmed.empty else None
    revenue_value = prov_revenue if prov_revenue is not None else conf_revenue
    profit_value = prov_profit if prov_profit is not None else conf_profit

    st.markdown(f"### {str(row.get('name') or '')}")
    st.caption(f"쿠팡 옵션ID {option_id or '-'} · 품목코드 {str(row.get('item_code') or '-')}")

    _render_cards(
        st,
        [
            ("판매수량", _qty(gross), False),
            ("매출액", _money(revenue_value) if revenue_value is not None else "-", False),
            ("이익", _money(profit_value) if profit_value is not None else "-", bool(profit_value is not None and profit_value < 0)),
            ("쿠팡RG재고", _qty(rg_stock), bool(rg_stock < 0)),
            ("자체창고재고", _qty(own_stock), bool(own_stock < 0)),
            ("취소반품률", _pct(return_rate), False),
            ("광고비", _money(ad_total), False),
        ],
    )

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["판매·손익", "매입", "재고·BOM", "반품", "광고"])

    with tab1:
        if sales is None or sales.empty:
            sview = pd_obj.DataFrame()
        else:
            sales = sales.sort_values(["기간시작", "기간종료"], ascending=[False, False], kind="stable")
            sview = sales[["기간시작", "기간종료", "판매수량", "취소수량", "순판매수량", "반품률"]].copy()
            sview.columns = ["시작일", "종료일", "판매수량", "취소수량", "순판매수량", sales_meta.get("label", "취소·반품률")]
            for col in ("판매수량", "취소수량", "순판매수량"):
                sview[col] = sview[col].map(_qty)
            sview[sales_meta.get("label", "취소·반품률")] = sview[sales_meta.get("label", "취소·반품률")].map(_pct)
        _render_table(st, "판매 이력", sview, "선택 기간의 판매자료가 없습니다.")

        if prov is None or prov.empty:
            pview = pd_obj.DataFrame()
        else:
            prov = prov.sort_values(["기간시작", "기간종료"], ascending=[False, False], kind="stable")
            pview = prov[["기간시작", "기간종료", "판매수량", "예상매출", "광고비", "예상이익", "이익률"]].copy()
            pview.columns = ["시작일", "종료일", "판매수량", "예상매출", "광고비", "예상이익", "이익률"]
            pview["판매수량"] = pview["판매수량"].map(_qty)
            for col in ("예상매출", "광고비", "예상이익"):
                pview[col] = pview[col].map(_money)
            pview["이익률"] = pview["이익률"].map(_pct)
        _render_table(st, "잠정 매출·이익 이력", pview, "선택 기간에 저장된 잠정손익 자료가 없습니다.")

        if confirmed is None or confirmed.empty:
            cview = pd_obj.DataFrame()
        else:
            confirmed = confirmed.sort_values("월", ascending=False, kind="stable")
            cview = confirmed[["월", "실현매출", "상품원가", "판매수수료", "입출고·배송비", "반품비", "광고비", "확정이익", "이익률"]].copy()
            for col in ("실현매출", "상품원가", "판매수수료", "입출고·배송비", "반품비", "광고비", "확정이익"):
                cview[col] = cview[col].map(_money)
            cview["이익률"] = cview["이익률"].map(_pct)
        _render_table(st, "월별 확정 매출·이익", cview, "선택 기간에 월 확정자료가 없습니다.", note="최근 월 순")

    with tab2:
        puchase_view = _purchase_view(pd_obj, purchases)
        _render_table(
            st,
            "매입 이력",
            puchase_view,
            "선택 기간에 이 완제품의 매입이력이 없습니다.",
            note="최근 매입일 순",
        )

    with tab3:
        if inv is None or inv.empty:
            iview = pd_obj.DataFrame()
        else:
            iview = inv.copy()
            iview["현재고"] = iview["현재고"].map(_qty)
        _render_table(st, "현재 창고별 재고", iview, "재고자료가 없습니다.", note="현재 기준")

        if bom is None or bom.empty:
            bview = pd_obj.DataFrame()
        else:
            bview = bom[["item_code", "name", "qty_per", "own_stock", "possible", "bottleneck"]].copy()
            bview.columns = ["품목코드", "구성품", "완제품 1개당 필요수량", "자체창고 재고", "생산가능수량", "병목"]
            bview["완제품 1개당 필요수량"] = bview["완제품 1개당 필요수량"].map(_qty)
            bview["자체창고 재고"] = bview["자체창고 재고"].map(_qty)
            bview["생산가능수량"] = bview["생산가능수량"].map(_qty)
            bview["병목"] = bview["병목"].map(lambda x: "⚠️" if bool(x) else "")
        _render_table(st, "BOM · 생산가능", bview, "등록된 BOM이 없습니다.", note=(f"최대 {int(max_make or 0):,}개 생산 가능" if max_make is not None else ""))

    with tab4:
        if returns is None or returns.empty:
            rview = pd_obj.DataFrame()
        else:
            rview = returns.copy()
            rename = {"txn_date": "일자", "qty_delta": "수량", "txn_type": "구분", "ref_no": "참조번호", "memo": "메모", "note": "메모"}
            rview = rview.rename(columns=rename)
            if "일자" in rview.columns:
                rview = rview.sort_values("일자", ascending=False, kind="stable")
            if "수량" in rview.columns:
                rview["수량"] = rview["수량"].map(_qty)
            if list(rview.columns).count("메모") > 1:
                rview = rview.loc[:, ~rview.columns.duplicated()]
        _render_table(st, "반품창고 입고 이력", rview, "선택 기간의 반품창고 입고 이력이 없습니다.")

        if sales is None or sales.empty:
            rsum = pd_obj.DataFrame()
        else:
            rsum = sales[["기간시작", "기간종료", "판매수량", "반품신호", "반품률"]].copy()
            rsum.columns = ["시작일", "종료일", "판매수량", "반품·취소수량", sales_meta.get("label", "취소·반품률")]
            rsum["판매수량"] = rsum["판매수량"].map(_qty)
            rsum["반품·취소수량"] = rsum["반품·취소수량"].map(_qty)
            rsum[sales_meta.get("label", "취소·반품률")] = rsum[sales_meta.get("label", "취소·반품률")].map(_pct)
        _render_table(st, "판매자료 기준 반품·취소 이력", rsum, "선택 기간의 판매자료가 없습니다.")

    with tab5:
        if ads is None or ads.empty:
            aview = pd_obj.DataFrame()
        else:
            ads = ads.sort_values(["period_start", "period_end"], ascending=[False, False], kind="stable")
            aview = ads[["period_start", "period_end", "ad_spend", "file_name"]].copy()
            aview.columns = ["시작일", "종료일", "광고비", "자료"]
            aview["광고비"] = aview["광고비"].map(_money)
        _render_table(st, "광고비 사용내역", aview, "선택 기간의 광고성과보고서에서 이 상품 광고비를 찾지 못했습니다.")
