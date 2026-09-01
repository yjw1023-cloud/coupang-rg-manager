"""RG Manager v0.9.131 monthly closing UI.

Fix: monthly COGS is the product-level confirmed P&L COGS (net sold quantity x
product weighted-average unit cost). Inventory opening + purchases - closing is
no longer used as sales COGS. Inventory values remain reference-only.
"""
from __future__ import annotations

from datetime import date, timedelta
import html
import importlib
from typing import Any

import pandas as pd


def _num(v: Any) -> float:
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _money(v: Any) -> str:
    return f"{int(round(_num(v))):,}원"


def _pct(v: Any) -> str:
    return f"{_num(v):,.1f}%"


def _money_html(v: Any, *, strong=False, danger=False) -> str:
    color = "#dc2626" if danger else "#0f172a"
    weight = "800" if strong else "650"
    return (
        f"<div style='text-align:right;font-variant-numeric:tabular-nums;"
        f"font-weight:{weight};color:{color};white-space:nowrap'>{html.escape(_money(v))}</div>"
    )


def _render_source_cards(st_obj, mdf, meta, purchase, opening, closing):
    st_obj.markdown("### 계산 데이터 출처")
    st_obj.caption(
        "매출원가는 상품별 판매수량과 평균원가로 계산합니다. 월초·월말 재고금액의 차액은 매출원가에 사용하지 않습니다."
    )
    cols = st_obj.columns(4)
    cards = [
        (cols[0], "쿠팡 월 정산", "연결됨" if not mdf.empty else "미입력", "실현매출 · 수수료 · RG비 · 반품비"),
        (cols[1], "상품별 판매원가", "연결됨" if not mdf.empty else "미입력", "순판매수량 × 상품별 평균원가"),
        (cols[2], "광고 월 정산", "연결됨" if "ad_billable_total" in (meta or {}) else "확인 필요", "월 광고 청구가능액"),
        (cols[3], "ERP 재고/매입", "참고용", f"당월매입 {_money(purchase['amount'])} · 재고자산 검산"),
    ]
    for col, title, status, detail in cards:
        with col:
            with st_obj.container(border=True):
                st_obj.markdown(f"**{title}**")
                st_obj.markdown(f"### {status}")
                st_obj.caption(detail)


def _render_pnl_flow(st_obj, actual, cogs, gross_profit, commission, rg, returns, other_expense, operating_profit):
    st_obj.markdown("### 월 손익 계산 흐름")
    rows = [
        ("실현매출", actual["revenue"], "쿠팡 월 정산자료", "normal"),
        ("(-) 매출원가", cogs, "순판매수량 × 상품별 평균원가", "normal"),
        ("매출총이익", gross_profit, "실현매출 - 매출원가", "subtotal"),
        ("(-) 판매수수료", commission, "쿠팡 실제 정산 수수료", "normal"),
        ("(-) 입출고·배송비", rg, "쿠팡 RG 정산비용", "normal"),
        ("(-) 반품비", returns, "반품회수 · 재입고비", "normal"),
        ("(-) 광고비", actual["ad"], "월 광고 청구가능액", "normal"),
        ("(-) 기타비용", other_expense, "월 결산에서 직접 입력한 비용", "normal"),
        ("결산이익", operating_profit, "사업 전체 월 결산 관리이익", "total"),
    ]
    with st_obj.container(border=True):
        for i, (label, amount, desc, kind) in enumerate(rows):
            if i in (2, 8):
                st_obj.markdown("<hr style='margin:4px 0 10px;border:none;border-top:1px solid #cbd5e1'>", unsafe_allow_html=True)
            c1, c2, c3 = st_obj.columns([3.1, 2.0, 4.9])
            c1.markdown(f"**{label}**" if kind in ("subtotal", "total") else label)
            c2.markdown(
                _money_html(amount, strong=kind in ("subtotal", "total"), danger=_num(amount) < 0),
                unsafe_allow_html=True,
            )
            c3.caption(desc)


def _render_cogs_detail(st_obj, pd_obj, mdf, total_cogs):
    st_obj.markdown("### 매출원가 상세")
    st_obj.caption(
        "월 결산 매출원가는 재고 전체 수불차액이 아니라 상품 확정손익의 상품별 판매원가를 합산합니다. "
        "이 원가는 판매수량(취소·반품 제외 순판매 기준) × 상품별 평균원가 방식입니다."
    )
    st_obj.metric("총 매출원가", _money(total_cogs))

    if mdf is None or mdf.empty:
        return
    cols = list(mdf.columns)
    qcol = next((c for c in ("net_qty", "순판매수량", "qty", "판매수량", "sales_qty") if c in cols), None)
    keep = [c for c in ("product_id", qcol, "cogs", "realized_sales") if c and c in cols]
    if not keep:
        return
    detail = mdf[keep].copy()
    rename = {"product_id": "상품ID", "cogs": "매출원가", "realized_sales": "실현매출"}
    if qcol:
        rename[qcol] = "순판매수량"
    detail = detail.rename(columns=rename)
    if "매출원가" in detail.columns:
        detail["매출원가"] = pd_obj.to_numeric(detail["매출원가"], errors="coerce").fillna(0).abs()
    if "실현매출" in detail.columns:
        detail["실현매출"] = pd_obj.to_numeric(detail["실현매출"], errors="coerce").fillna(0)
    if "순판매수량" in detail.columns:
        detail["순판매수량"] = pd_obj.to_numeric(detail["순판매수량"], errors="coerce").fillna(0)
    if "매출원가" in detail.columns:
        detail = detail.sort_values("매출원가", ascending=False)

    with st_obj.expander("상품별 매출원가 보기", expanded=False):
        styled = detail.style
        if "매출원가" in detail.columns:
            styled = styled.format({"매출원가": lambda x: _money(x)})
            styled = styled.set_properties(subset=["매출원가"], **{"text-align": "right"})
        if "실현매출" in detail.columns:
            styled = styled.format({"실현매출": lambda x: _money(x), "매출원가": lambda x: _money(x)} if "매출원가" in detail.columns else {"실현매출": lambda x: _money(x)})
            styled = styled.set_properties(subset=["실현매출"], **{"text-align": "right"})
        if "순판매수량" in detail.columns:
            styled = styled.format({"순판매수량": lambda x: f"{_num(x):,.0f}개"})
            styled = styled.set_properties(subset=["순판매수량"], **{"text-align": "right"})
        st_obj.dataframe(styled, use_container_width=True, hide_index=True, height=min(650, max(220, 38 * (len(detail) + 1))))


def _render_inventory_reference(st_obj, opening, purchase, closing):
    st_obj.markdown("### 매입 · 재고 참고")
    st_obj.caption(
        "아래 금액은 재고자산과 매입 현황을 확인하기 위한 참고값입니다. "
        "월초재고 + 당월매입 - 월말재고를 매출원가로 계산하지 않습니다."
    )
    c1, c2, c3 = st_obj.columns(3)
    c1.metric("월초 재고금액", _money(opening["value"]))
    c2.metric("당월 매입액", _money(purchase["amount"]), f"{purchase['rows']:,}건")
    c3.metric("월말 재고금액", _money(closing["value"]))
    if opening["negative_products"] or closing["negative_products"]:
        st_obj.warning(
            f"마이너스 재고 상품: 월초 {opening['negative_products']:,}개 / 월말 {closing['negative_products']:,}개. "
            "이 경고는 재고원장 점검용이며 매출원가 산식과는 분리됩니다."
        )


def _render_expense_chart(st_obj, commission, rg, returns, ad, other_expense):
    chart = pd.DataFrame({
        "비용": ["판매수수료", "입출고·배송비", "반품비", "광고비", "기타비용"],
        "금액": [commission, rg, returns, ad, other_expense],
    })
    chart = chart[chart["금액"] > 0].sort_values("금액", ascending=False)
    if not chart.empty:
        st_obj.markdown("### 비용 구성")
        st_obj.bar_chart(chart.set_index("비용")["금액"], use_container_width=True)


def _render_other_expenses(st_obj, pd_obj, base, core, db, start, end, expenses):
    st_obj.markdown("### 기타비용")
    st_obj.caption("쿠팡 정산비용이나 상품원가에 이미 포함된 금액은 다시 입력하지 마세요.")
    with st_obj.expander("기타비용 추가", expanded=False):
        default_date = end if end < date.today() else date.today()
        with st_obj.form("monthly_closing_expense_form_v0916"):
            expense_date = st_obj.date_input("비용일", value=default_date, min_value=start, max_value=end)
            category = st_obj.selectbox("구분", base.EXPENSE_CATEGORIES)
            amount = st_obj.number_input("금액", min_value=0, step=1000, format="%d")
            memo = st_obj.text_input("메모", placeholder="예: 포장재, 사무용품, 외주작업")
            add = st_obj.form_submit_button("비용 추가")
        if add:
            try:
                base._insert_expense(core, db, expense_date.isoformat(), category, amount, memo)
                st_obj.success("기타비용을 추가했습니다.")
                st_obj.rerun()
            except Exception as e:
                st_obj.error(str(e))

    if not expenses:
        st_obj.info("이 달에 직접 입력한 기타비용이 없습니다.")
        return
    expense_df = pd_obj.DataFrame([
        {"ID": int(r["id"]), "비용일": str(r["expense_date"]), "구분": str(r["category"]), "금액": base._num(r["amount"]), "메모": str(r["memo"] or "")}
        for r in expenses
    ])
    total = expense_df.groupby("구분", as_index=False)["금액"].sum().sort_values("금액", ascending=False)
    st_obj.dataframe(total.style.format({"금액": lambda x: _money(x)}).set_properties(subset=["금액"], **{"text-align": "right"}), use_container_width=True, hide_index=True)
    with st_obj.expander("입력한 기타비용 상세 / 삭제", expanded=False):
        detail = expense_df.sort_values(["비용일", "ID"], ascending=[False, False])
        st_obj.dataframe(detail.style.format({"금액": lambda x: _money(x)}).set_properties(subset=["금액"], **{"text-align": "right"}), use_container_width=True, hide_index=True)
        options = expense_df["ID"].astype(int).tolist()
        labels = {int(r.ID): f"{r.비용일} · {r.구분} · {_money(r.금액)} · {r.메모}" for r in expense_df.itertuples()}
        selected = st_obj.selectbox("삭제할 비용", options, format_func=lambda x: labels.get(int(x), str(x)), key="monthly_closing_delete_expense_v0916")
        if st_obj.button("선택 비용 삭제", key="monthly_closing_delete_button_v0916"):
            base._delete_expense(core, db, int(selected))
            st_obj.success("선택한 기타비용을 삭제했습니다.")
            st_obj.rerun()


def render_monthly_closing_page(st_obj, pd_obj, core, db_path=None):
    base = importlib.import_module("monthly_closing_v0916")
    db = db_path or core.DEFAULT_DB
    base._ensure_schema(core, db)

    st_obj.markdown("## 📒 월 결산")
    st_obj.caption(
        "매출원가는 판매된 상품의 원가만 반영합니다. 기준: 순판매수량(판매-취소/반품) × 상품별 평균원가. "
        "재고 전체 수불차액은 매출원가로 사용하지 않습니다."
    )

    months = base._available_months(core, db)
    cur = base._current_month()
    idx = months.index(cur) if cur in months else 0
    month = st_obj.selectbox("결산 월", months, index=idx, key="monthly_closing_month_v0916")
    start, end = base._month_bounds(month)
    opening_date = start - timedelta(days=1)

    mdf, meta = base._confirmed(core, month)
    actual = base._confirmed_totals(mdf, meta)
    purchase = base._purchase_summary(core, db, start, end)
    opening = base._inventory_state(core, db, opening_date)
    closing = base._inventory_state(core, db, end)
    expenses = base._expense_rows(core, db, start, end)
    other_expense = base._expense_total(expenses)

    # v0.9.131: use product-level sales COGS, never inventory asset difference.
    cogs = actual["product_cogs"]
    commission = actual["commission"]
    rg = actual["inout"] + actual["delivery"]
    returns = actual["return_pickup"] + actual["return_restock"]
    gross_profit = actual["revenue"] - cogs
    operating_profit = gross_profit - commission - rg - returns - actual["ad"] - other_expense
    margin = operating_profit / actual["revenue"] * 100 if actual["revenue"] else 0.0

    if not mdf.empty:
        st_obj.success(f"{month} 월 결산 계산 · {start.isoformat()} ~ {end.isoformat()}")
    else:
        st_obj.warning(f"{month} 쿠팡 월 정산자료가 없습니다.")

    c1, c2, c3, c4, c5 = st_obj.columns(5)
    c1.metric("실현매출", _money(actual["revenue"]))
    c2.metric("매출원가", _money(cogs))
    c3.metric("매출총이익", _money(gross_profit))
    c4.metric("결산이익", _money(operating_profit))
    c5.metric("결산이익률", _pct(margin))

    _render_source_cards(st_obj, mdf, meta, purchase, opening, closing)
    _render_pnl_flow(st_obj, actual, cogs, gross_profit, commission, rg, returns, other_expense, operating_profit)
    _render_cogs_detail(st_obj, pd_obj, mdf, cogs)
    _render_expense_chart(st_obj, commission, rg, returns, actual["ad"], other_expense)
    _render_inventory_reference(st_obj, opening, purchase, closing)
    _render_other_expenses(st_obj, pd_obj, base, core, db, start, end, expenses)

    st_obj.markdown("### 발생기준 자금수지 참고")
    after_coupang = actual["revenue"] - commission - rg - returns - actual["ad"]
    funding_delta = after_coupang - purchase["amount"] - other_expense
    f1, f2, f3 = st_obj.columns(3)
    f1.metric("쿠팡비용 차감 후", _money(after_coupang))
    f2.metric("당월 매입·기타지출", _money(purchase["amount"] + other_expense))
    f3.metric("발생기준 자금수지", _money(funding_delta))
    st_obj.caption("자금수지는 실제 은행 입출금일 기준이 아니라 선택월 발생 기준의 관리용 참고치입니다.")

    st_obj.markdown("### 결산자료 상태")
    status = pd_obj.DataFrame([
        {"자료": "쿠팡 월 정산", "상태": "완료" if not mdf.empty else "미입력", "사용": "실현매출·수수료·RG·반품비"},
        {"자료": "상품별 판매원가", "상태": "완료" if not mdf.empty else "미입력", "사용": "순판매수량 × 평균원가"},
        {"자료": "광고 월 정산", "상태": "완료" if "ad_billable_total" in (meta or {}) else "확인 필요", "사용": "청구가능 광고비"},
        {"자료": "당월 매입", "상태": f"{purchase['rows']:,}건", "사용": "재고/자금 참고"},
        {"자료": "재고원장", "상태": "연결" if opening["ledger_exists"] and closing["ledger_exists"] else "확인 필요", "사용": "재고자산 참고(매출원가 산식 제외)"},
    ])
    st_obj.dataframe(status, use_container_width=True, hide_index=True)
