"""RG Manager v0.9.130 monthly closing presentation + accounting sanity guard.

Improves the monthly-closing screen without changing the underlying settlement,
inventory, purchase, or expense ledgers.

Key behavior:
- clearly labels which figures come from Coupang monthly settlement vs ERP ledgers
- blocks gross/closing profit presentation when inventory-formula COGS is negative
- explains the inventory mismatch instead of presenting impossible profit
- renders the P&L as an aligned calculation flow plus expense composition chart
- keeps existing manual other-expense entry/delete and funding-reference features
"""
from __future__ import annotations

from datetime import date, timedelta
import html
import importlib

import pandas as pd


def _money(v) -> str:
    try:
        return f"{int(round(float(v or 0))):,}원"
    except Exception:
        return "0원"


def _pct(v) -> str:
    try:
        return f"{float(v or 0):,.1f}%"
    except Exception:
        return "0.0%"


def _money_html(v, *, strong=False, danger=False, muted=False) -> str:
    color = "#dc2626" if danger else "#0f172a"
    if muted:
        color = "#64748b"
    weight = "800" if strong else "650"
    return (
        f"<div style='text-align:right;font-variant-numeric:tabular-nums;"
        f"font-weight:{weight};color:{color};white-space:nowrap'>{html.escape(_money(v))}</div>"
    )


def _render_source_summary(st_obj, month, mdf, meta, purchase, opening, closing):
    st_obj.markdown("### 계산 데이터 출처")
    st_obj.caption("월 결산은 한 파일만으로 계산하지 않습니다. 아래 자료를 서로 연결해 계산합니다.")

    cols = st_obj.columns(4)
    items = [
        (
            cols[0],
            "쿠팡 월 정산",
            "연결됨" if not mdf.empty else "미입력",
            "실현매출 · 판매수수료 · RG비 · 반품비",
        ),
        (
            cols[1],
            "광고 월 정산",
            "연결됨" if "ad_billable_total" in (meta or {}) else "확인 필요",
            "월 광고 청구가능액",
        ),
        (
            cols[2],
            "ERP 매입장부",
            f"{purchase['rows']:,}건",
            f"{month} 당월매입 {_money(purchase['amount'])}",
        ),
        (
            cols[3],
            "ERP 재고원장",
            "연결됨" if opening["ledger_exists"] and closing["ledger_exists"] else "미입력",
            "월초 · 월말 재고 평가",
        ),
    ]
    for col, title, status, detail in items:
        with col:
            with st_obj.container(border=True):
                st_obj.markdown(f"**{title}**")
                st_obj.markdown(f"### {status}")
                st_obj.caption(detail)


def _render_pnl_flow(st_obj, actual, inventory_cogs, gross_profit, commission, rg, returns, other_expense, operating_profit, cogs_valid):
    st_obj.markdown("### 월 손익 계산 흐름")
    rows = [
        ("실현매출", actual["revenue"], "쿠팡 월 정산자료", "normal"),
        ("(-) 재고식 매출원가", inventory_cogs, "월초재고 + 당월매입 - 월말재고", "danger" if inventory_cogs < 0 else "normal"),
        ("매출총이익", gross_profit, "실현매출 - 재고식 매출원가", "subtotal"),
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
                st_obj.markdown("<hr style='margin:4px 0 10px 0;border:none;border-top:1px solid #cbd5e1'>", unsafe_allow_html=True)
            c1, c2, c3 = st_obj.columns([3.2, 2.0, 4.8])
            if kind in ("subtotal", "total"):
                c1.markdown(f"**{label}**")
            else:
                c1.markdown(label)

            invalid_profit = (not cogs_valid) and kind in ("subtotal", "total")
            if invalid_profit:
                c2.markdown(
                    "<div style='text-align:right;font-weight:800;color:#b45309;white-space:nowrap'>계산 보류</div>",
                    unsafe_allow_html=True,
                )
            else:
                c2.markdown(
                    _money_html(
                        amount,
                        strong=kind in ("subtotal", "total"),
                        danger=(amount < 0),
                    ),
                    unsafe_allow_html=True,
                )
            c3.caption(desc)


def _render_cost_diagnosis(st_obj, opening, purchase, closing, inventory_cogs, actual):
    stock_growth = closing["value"] - opening["value"]
    available_before_sales = opening["value"] + purchase["amount"]
    cogs_valid = inventory_cogs >= -0.5

    st_obj.markdown("### 매입 · 재고")
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("월초 재고금액", _money(opening["value"]))
    c2.metric("당월 매입액", _money(purchase["amount"]), f"{purchase['rows']:,}건")
    c3.metric("월말 재고금액", _money(closing["value"]))
    c4.metric("재고식 매출원가", _money(inventory_cogs))

    if not cogs_valid:
        gap = closing["value"] - available_before_sales
        st_obj.error(
            "⚠️ 재고식 매출원가가 음수라서 매출총이익과 결산이익 계산을 보류했습니다. "
            f"월초재고 {_money(opening['value'])} + 당월매입 {_money(purchase['amount'])}보다 "
            f"월말재고 {_money(closing['value'])}가 {_money(max(gap, 0))} 더 큽니다."
        )
        reasons = []
        if purchase["rows"] == 0 or abs(purchase["amount"]) < 0.5:
            reasons.append("선택월 매입자료가 0건/0원입니다")
        if stock_growth > 0:
            reasons.append(f"월말 재고가 월초보다 {_money(stock_growth)} 증가했습니다")
        if opening["negative_products"] or closing["negative_products"]:
            reasons.append(
                f"마이너스 재고 상품이 월초 {opening['negative_products']:,}개 / 월말 {closing['negative_products']:,}개 있습니다"
            )
        if reasons:
            st_obj.warning("점검 필요: " + " · ".join(reasons))
    else:
        st_obj.success("재고식 매출원가가 0원 이상으로 계산되어 기본 산술 검증을 통과했습니다.")

    cogs_gap = inventory_cogs - actual["product_cogs"]
    compare = pd.DataFrame(
        [
            {"원가 기준": "월 결산 재고식 매출원가", "금액": inventory_cogs},
            {"원가 기준": "상품 확정손익 매출원가", "금액": actual["product_cogs"]},
            {"원가 기준": "두 원가의 차이", "금액": cogs_gap},
        ]
    )
    styled = (
        compare.style
        .format({"금액": lambda x: _money(x)})
        .set_properties(subset=["금액"], **{"text-align": "right", "font-variant-numeric": "tabular-nums"})
        .set_properties(subset=["원가 기준"], **{"text-align": "left"})
    )
    st_obj.dataframe(styled, use_container_width=True, hide_index=True)
    st_obj.caption(
        "상품 확정손익 원가는 비교·검산용입니다. 재고식 원가가 비정상이어도 자동 대체하지 않습니다. "
        "원장 오류를 먼저 확인해야 월 결산 수치가 왜곡되지 않습니다."
    )
    return cogs_valid


def _render_expense_chart(st_obj, commission, rg, returns, ad, other_expense):
    chart_df = pd.DataFrame(
        {
            "비용": ["판매수수료", "입출고·배송비", "반품비", "광고비", "기타비용"],
            "금액": [commission, rg, returns, ad, other_expense],
        }
    )
    chart_df = chart_df[chart_df["금액"] > 0].sort_values("금액", ascending=False)
    if chart_df.empty:
        return
    st_obj.markdown("### 비용 구성")
    st_obj.caption("선택월 결산비용을 항목별 크기로 비교합니다.")
    st_obj.bar_chart(chart_df.set_index("비용")["금액"], use_container_width=True)


def _render_other_expenses(st_obj, pd_obj, base, core, db, start, end, expenses):
    st_obj.markdown("### 기타비용")
    st_obj.caption(
        "쿠팡 정산비용이나 상품 매입원가에 이미 포함된 금액은 다시 입력하지 마세요. "
        "중복 입력하면 결산이익이 실제보다 낮아집니다."
    )
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
                try:
                    st_obj.rerun()
                except Exception:
                    pass
            except Exception as e:
                st_obj.error(str(e))

    if expenses:
        expense_df = pd_obj.DataFrame(
            [
                {
                    "ID": int(r["id"]),
                    "비용일": str(r["expense_date"]),
                    "구분": str(r["category"]),
                    "금액": base._num(r["amount"]),
                    "메모": str(r["memo"] or ""),
                }
                for r in expenses
            ]
        )
        total_by_cat = (
            expense_df.groupby("구분", as_index=False)["금액"]
            .sum()
            .sort_values("금액", ascending=False)
        )
        styled = (
            total_by_cat.style
            .format({"금액": lambda x: _money(x)})
            .set_properties(subset=["금액"], **{"text-align": "right", "font-variant-numeric": "tabular-nums"})
        )
        st_obj.dataframe(styled, use_container_width=True, hide_index=True)

        with st_obj.expander("입력한 기타비용 상세 / 삭제", expanded=False):
            detail = expense_df.sort_values(["비용일", "ID"], ascending=[False, False]).copy()
            detail_styled = (
                detail.style
                .format({"금액": lambda x: _money(x)})
                .set_properties(subset=["금액"], **{"text-align": "right", "font-variant-numeric": "tabular-nums"})
            )
            st_obj.dataframe(detail_styled, use_container_width=True, hide_index=True)
            options = expense_df["ID"].astype(int).tolist()
            labels = {
                int(r.ID): f"{r.비용일} · {r.구분} · {_money(r.금액)} · {r.메모}"
                for r in expense_df.itertuples()
            }
            selected = st_obj.selectbox(
                "삭제할 비용",
                options,
                format_func=lambda x: labels.get(int(x), str(x)),
                key="monthly_closing_delete_expense_v0916",
            )
            if st_obj.button("선택 비용 삭제", key="monthly_closing_delete_button_v0916"):
                base._delete_expense(core, db, int(selected))
                st_obj.success("선택한 기타비용을 삭제했습니다.")
                try:
                    st_obj.rerun()
                except Exception:
                    pass
    else:
        st_obj.info("이 달에 직접 입력한 기타비용이 없습니다.")


def _render_status_cards(st_obj, mdf, meta, purchase, opening, closing):
    st_obj.markdown("### 결산자료 상태")
    rows = [
        ("쿠팡 월 정산", "완료" if not mdf.empty else "미입력", "실현매출·수수료·RG·반품비"),
        ("광고 월 정산", "완료" if "ad_billable_total" in (meta or {}) else "확인 필요", "청구가능 광고비"),
        ("매입자료", "연결" if purchase["table_exists"] else "미입력", f"선택월 {purchase['rows']:,}건"),
        ("재고원장", "연결" if opening["ledger_exists"] and closing["ledger_exists"] else "미입력", "월초·월말 재고 평가"),
    ]
    cols = st_obj.columns(4)
    for col, (title, status, note) in zip(cols, rows):
        with col:
            with st_obj.container(border=True):
                st_obj.markdown(f"**{title}**")
                st_obj.markdown(f"### {status}")
                st_obj.caption(note)


def render_monthly_closing_page(st_obj, pd_obj, core, db_path=None):
    base = importlib.import_module("monthly_closing_v0916")
    db = db_path or core.DEFAULT_DB
    base._ensure_schema(core, db)

    st_obj.markdown("## 📒 월 결산")
    st_obj.caption(
        "쿠팡 월 정산자료와 ERP 매입·재고원장을 연결해 사업 전체 한 달 성적을 계산합니다. "
        "재고식 매출원가가 비정상일 때는 이익을 그대로 확정 표시하지 않습니다."
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

    inventory_cogs = opening["value"] + purchase["amount"] - closing["value"]
    commission = actual["commission"]
    rg = actual["inout"] + actual["delivery"]
    returns = actual["return_pickup"] + actual["return_restock"]
    gross_profit = actual["revenue"] - inventory_cogs
    operating_profit = (
        actual["revenue"]
        - inventory_cogs
        - commission
        - rg
        - returns
        - actual["ad"]
        - other_expense
    )
    margin = operating_profit / actual["revenue"] * 100 if actual["revenue"] else 0.0

    base_ready = (not mdf.empty) and opening["ledger_exists"] and closing["ledger_exists"]
    cogs_valid = inventory_cogs >= -0.5
    ready = base_ready and cogs_valid

    if ready:
        st_obj.success(f"{month} 월 결산 계산 가능 · {start.isoformat()} ~ {end.isoformat()}")
    elif not base_ready:
        st_obj.warning(f"{month} 결산자료가 아직 완전하지 않습니다. 아래 자료 상태를 확인해 주세요.")
    else:
        st_obj.error(
            f"{month} 쿠팡 월 정산자료는 연결됐지만 재고식 매출원가가 음수라 "
            "매출총이익·결산이익을 확정 표시하지 않습니다."
        )

    _render_source_summary(st_obj, month, mdf, meta, purchase, opening, closing)

    st_obj.markdown("### 결산 요약")
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("실현매출", _money(actual["revenue"]))
    if cogs_valid:
        c2.metric("매출총이익", _money(gross_profit))
        c3.metric("결산이익", _money(operating_profit))
        c4.metric("결산이익률", _pct(margin))
    else:
        c2.metric("매출총이익", "계산 보류")
        c3.metric("결산이익", "계산 보류")
        c4.metric("결산이익률", "계산 보류")

    _render_pnl_flow(
        st_obj,
        actual,
        inventory_cogs,
        gross_profit,
        commission,
        rg,
        returns,
        other_expense,
        operating_profit,
        cogs_valid,
    )

    cogs_valid = _render_cost_diagnosis(
        st_obj, opening, purchase, closing, inventory_cogs, actual
    )

    if opening["negative_products"] or closing["negative_products"]:
        st_obj.warning(
            f"마이너스 재고 상품이 월초 {opening['negative_products']:,}개, "
            f"월말 {closing['negative_products']:,}개 있습니다. "
            "마이너스 수량은 재고자산 0으로 평가했습니다."
        )
    if opening["fallback_products"] or closing["fallback_products"]:
        st_obj.warning(
            "일부 재고 입고원장에 원가가 없어 과거 매입/생산원가 또는 상품 기준원가를 보조값으로 사용했습니다."
        )

    _render_expense_chart(st_obj, commission, rg, returns, actual["ad"], other_expense)
    _render_other_expenses(st_obj, pd_obj, base, core, db, start, end, expenses)

    st_obj.markdown("### 발생기준 자금수지 참고")
    after_coupang = actual["revenue"] - commission - rg - returns - actual["ad"]
    funding_delta = after_coupang - purchase["amount"] - other_expense
    c1, c2, c3 = st_obj.columns(3)
    c1.metric("쿠팡비용 차감 후", _money(after_coupang))
    c2.metric("당월 매입·기타지출", _money(purchase["amount"] + other_expense))
    c3.metric("발생기준 자금수지", _money(funding_delta))
    st_obj.caption(
        "이 수치는 실제 은행 입금일/지급일 기준 현금흐름이 아니라 선택월에 발생한 매출·비용을 "
        "같은 달에 놓고 보는 관리용 참고치입니다."
    )

    _render_status_cards(st_obj, mdf, meta, purchase, opening, closing)
