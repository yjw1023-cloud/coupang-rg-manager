"""RG Manager v0.9.16 monthly business closing.

Separates product-level confirmed management P&L from whole-business monthly
closing. Monthly closing uses:
- Coupang confirmed settlement revenue/fees/RG/returns/ads
- inventory-formula COGS: opening inventory + monthly purchases - closing inventory
- monthly purchase totals from purchase_lines
- manual non-Coupang operating expenses
- an accrual-basis funding view (not bank-deposit cash accounting)
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
import math
from typing import Any

import pandas as pd

_APPLIED = False

EXPENSE_CATEGORIES = [
    "포장·소모품",
    "택배·운송",
    "외주·수수료",
    "임차·관리비",
    "사무·업무비",
    "기타",
]


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


def _fmt_money(v: Any) -> str:
    return f"{int(round(_num(v))):,}원"


def _fmt_pct(v: Any) -> str:
    return f"{_num(v):,.1f}%"


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _month_bounds(month: str):
    y, m = [int(x) for x in str(month).split("-")]
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def _ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS monthly_closing_expenses(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                expense_date TEXT NOT NULL,
                category TEXT NOT NULL,
                amount REAL NOT NULL,
                memo TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_monthly_closing_expenses_date "
            "ON monthly_closing_expenses(expense_date)"
        )


def _available_months(core, db) -> list[str]:
    months = {_current_month()}
    try:
        months.update(str(x) for x in (core.monthly_available() or []) if x)
    except Exception:
        pass
    with core._conn(db) as c:
        if _exists(c, "purchase_lines"):
            pc = _cols(c, "purchase_lines")
            if "purchase_date" in pc:
                for r in c.execute(
                    """SELECT DISTINCT substr(purchase_date,1,7) m
                       FROM purchase_lines
                       WHERE purchase_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-*'"""
                ):
                    if r["m"]:
                        months.add(str(r["m"]))
        if _exists(c, "monthly_closing_expenses"):
            for r in c.execute(
                """SELECT DISTINCT substr(expense_date,1,7) m
                   FROM monthly_closing_expenses
                   WHERE expense_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-*'"""
            ):
                if r["m"]:
                    months.add(str(r["m"]))
    return sorted(months, reverse=True)


def _confirmed(core, month: str):
    try:
        mdf, meta = core.confirmed_monthly_pnl(month)
    except Exception:
        return pd.DataFrame(), {}
    if mdf is None:
        mdf = pd.DataFrame()
    return mdf, (meta or {})


def _confirmed_totals(mdf: pd.DataFrame, meta: dict) -> dict[str, float]:
    def total(col: str) -> float:
        if col not in mdf.columns:
            return 0.0
        return float(pd.to_numeric(mdf[col], errors="coerce").fillna(0).abs().sum())

    revenue = (
        float(pd.to_numeric(mdf["realized_sales"], errors="coerce").fillna(0).sum())
        if "realized_sales" in mdf.columns
        else 0.0
    )
    return {
        "revenue": revenue,
        "product_cogs": total("cogs"),
        "commission": total("commission"),
        "inout": total("inout"),
        "delivery": total("delivery"),
        "return_pickup": total("return_pickup"),
        "return_restock": total("return_restock"),
        "ad": abs(_num(meta.get("ad_billable_total", 0))),
    }


def _purchase_rows(core, db, start: date, end: date):
    with core._conn(db) as c:
        if not _exists(c, "purchase_lines"):
            return []
        cols = _cols(c, "purchase_lines")
        if "purchase_date" not in cols:
            return []
        return c.execute(
            "SELECT * FROM purchase_lines WHERE purchase_date>=? AND purchase_date<=? "
            "ORDER BY purchase_date,id",
            (start.isoformat(), end.isoformat()),
        ).fetchall()


def _purchase_amount(row, cols: set[str]) -> tuple[float, float]:
    q = _num(row["qty_receipt"]) if "qty_receipt" in cols else 0.0
    if abs(q) <= 1e-12 and "qty_source" in cols:
        q = _num(row["qty_source"])
    amount = _num(row["landed_total_krw"]) if "landed_total_krw" in cols else 0.0
    if abs(amount) <= 1e-12 and "total_amount" in cols:
        amount = _num(row["total_amount"])
    if abs(amount) <= 1e-12:
        unit = _num(row["landed_unit_cost_krw"]) if "landed_unit_cost_krw" in cols else 0.0
        if unit <= 0 and "unit_price" in cols:
            unit = _num(row["unit_price"])
        amount = q * unit
    return q, amount


def _purchase_summary(core, db, start: date, end: date):
    rows = _purchase_rows(core, db, start, end)
    with core._conn(db) as c:
        cols = _cols(c, "purchase_lines") if _exists(c, "purchase_lines") else set()
    qty = 0.0
    amount = 0.0
    products = set()
    for r in rows:
        q, a = _purchase_amount(r, cols)
        qty += q
        amount += a
        if "product_id" in cols and r["product_id"] is not None:
            products.add(int(r["product_id"]))
    return {
        "rows": len(rows),
        "qty": qty,
        "amount": amount,
        "products": len(products),
        "table_exists": bool(cols),
    }


def _fallback_costs(core, db, as_of: date, product_rows):
    totals = {int(r["id"]): [0.0, 0.0] for r in product_rows}
    with core._conn(db) as c:
        if _exists(c, "purchase_lines"):
            pc = _cols(c, "purchase_lines")
            if {"product_id", "purchase_date"}.issubset(pc):
                rows = c.execute(
                    "SELECT * FROM purchase_lines WHERE product_id IS NOT NULL AND purchase_date<=?",
                    (as_of.isoformat(),),
                ).fetchall()
                for r in rows:
                    pid = int(r["product_id"])
                    if pid not in totals:
                        continue
                    q, amount = _purchase_amount(r, pc)
                    if q > 0 and amount > 0:
                        totals[pid][0] += q
                        totals[pid][1] += amount
        if _exists(c, "production_orders"):
            pc = _cols(c, "production_orders")
            if {"parent_product_id", "qty", "produced_unit_cost", "production_date"}.issubset(pc):
                for r in c.execute(
                    """SELECT parent_product_id,qty,produced_unit_cost
                       FROM production_orders
                       WHERE production_date<=? AND COALESCE(qty,0)>0""",
                    (as_of.isoformat(),),
                ):
                    pid = int(r["parent_product_id"])
                    q = _num(r["qty"])
                    u = _num(r["produced_unit_cost"])
                    if pid in totals and q > 0 and u > 0:
                        totals[pid][0] += q
                        totals[pid][1] += q * u
    out = {}
    for r in product_rows:
        pid = int(r["id"])
        q, value = totals.get(pid, (0.0, 0.0))
        out[pid] = (value / q) if q > 0 else _num(r["unit_cost"])
    return out


def _inventory_state(core, db, as_of: date):
    with core._conn(db) as c:
        products = c.execute("SELECT id,name,unit_cost FROM products ORDER BY id").fetchall()
        by_id = {
            int(r["id"]): {"name": str(r["name"] or ""), "unit_cost": _num(r["unit_cost"])}
            for r in products
        }
        if not _exists(c, "inventory_txns"):
            return {"value": 0.0, "qty": 0.0, "negative_products": 0, "fallback_products": 0, "ledger_exists": False}
        tc = _cols(c, "inventory_txns")
        need = {"id", "product_id", "warehouse_id", "qty_delta"}
        if not need.issubset(tc):
            return {"value": 0.0, "qty": 0.0, "negative_products": 0, "fallback_products": 0, "ledger_exists": False}
        fields = ["id", "product_id", "warehouse_id", "qty_delta"]
        for x in ("txn_date", "ref_no", "unit_cost"):
            if x in tc:
                fields.append(x)
        date_where = "WHERE COALESCE(txn_date,'')<=?" if "txn_date" in tc else ""
        params = (as_of.isoformat(),) if date_where else ()
        order_sql = "COALESCE(txn_date,''), id" if "txn_date" in tc else "id"
        txns = c.execute(
            f"SELECT {','.join(fields)} FROM inventory_txns {date_where} ORDER BY {order_sql}",
            params,
        ).fetchall()
        excluded_wh = set()
        if _exists(c, "warehouses"):
            wc = _cols(c, "warehouses")
            if {"id", "name"}.issubset(wc):
                excluded_wh = {
                    int(r["id"])
                    for r in c.execute("SELECT id,name FROM warehouses")
                    if str(r["name"] or "") == "불량·폐기"
                }

    fallback = _fallback_costs(core, db, as_of, products)
    avg = {pid: (fallback.get(pid) or p["unit_cost"] or 0.0) for pid, p in by_id.items()}
    qty_cost = {pid: 0.0 for pid in by_id}
    value_cost = {pid: 0.0 for pid in by_id}
    asset_qty = {pid: 0.0 for pid in by_id}
    groups = {}

    for r in txns:
        pid = int(r["product_id"])
        if pid not in by_id:
            continue
        keys = r.keys()
        d = _num(r["qty_delta"])
        if int(r["warehouse_id"]) not in excluded_wh:
            asset_qty[pid] += d
        dt = str(r["txn_date"] or "") if "txn_date" in keys else ""
        ref = str(r["ref_no"] or "") if "ref_no" in keys else f"ID-{r['id']}"
        g = groups.setdefault((pid, dt, ref), {"id": int(r["id"]), "net": 0.0, "receipt_qty": 0.0, "receipt_value": 0.0})
        g["net"] += d
        unit = _num(r["unit_cost"] if "unit_cost" in keys else None)
        if d > 0 and unit > 0:
            g["receipt_qty"] += d
            g["receipt_value"] += d * unit

    fallback_used = set()
    for (pid, _dt, _ref), g in sorted(groups.items(), key=lambda x: (x[0][1], x[1]["id"])):
        d = g["net"]
        if abs(d) <= 1e-12:
            continue
        cur = avg.get(pid, 0.0) or fallback.get(pid, 0.0) or by_id[pid]["unit_cost"]
        if d > 0:
            if g["receipt_qty"] > 0:
                receipt = g["receipt_value"] / g["receipt_qty"]
            else:
                receipt = cur
                if receipt > 0:
                    fallback_used.add(pid)
            base_q = max(qty_cost[pid], 0.0)
            base_v = max(value_cost[pid], 0.0) if base_q > 0 else 0.0
            qty_cost[pid] = base_q + d
            value_cost[pid] = base_v + d * max(receipt, 0.0)
            if qty_cost[pid] > 0:
                avg[pid] = value_cost[pid] / qty_cost[pid]
        else:
            qty_cost[pid] += d
            value_cost[pid] = qty_cost[pid] * max(cur, 0.0)
            avg[pid] = cur

    total_value = 0.0
    total_qty = 0.0
    negative_products = 0
    for pid, q in asset_qty.items():
        total_qty += q
        if q < -1e-9:
            negative_products += 1
        total_value += max(q, 0.0) * max(avg.get(pid, 0.0), 0.0)

    return {
        "value": total_value,
        "qty": total_qty,
        "negative_products": negative_products,
        "fallback_products": len(fallback_used),
        "ledger_exists": True,
    }


def _expense_rows(core, db, start: date, end: date):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        return c.execute(
            """SELECT id,expense_date,category,amount,memo,created_at
               FROM monthly_closing_expenses
               WHERE expense_date>=? AND expense_date<=?
               ORDER BY expense_date,id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()


def _expense_total(rows) -> float:
    return sum(abs(_num(r["amount"])) for r in rows)


def _insert_expense(core, db, expense_date, category, amount, memo):
    if _num(amount) <= 0:
        raise ValueError("비용은 0원보다 커야 합니다.")
    with core._conn(db) as c:
        c.execute(
            """INSERT INTO monthly_closing_expenses
               (expense_date,category,amount,memo,created_at)
               VALUES(?,?,?,?,?)""",
            (
                str(expense_date),
                str(category or "기타"),
                float(amount),
                str(memo or "").strip(),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def _delete_expense(core, db, expense_id: int):
    with core._conn(db) as c:
        c.execute("DELETE FROM monthly_closing_expenses WHERE id=?", (int(expense_id),))


def _render_status(st_obj, confirmed_ok, purchase, opening, closing, meta):
    rows = [
        {"자료": "쿠팡 월 정산", "상태": "완료" if confirmed_ok else "미입력", "비고": "실현매출·수수료·RG·반품비"},
        {"자료": "광고 월 정산", "상태": "완료" if "ad_billable_total" in meta else "확인 필요", "비고": "청구가능 광고비"},
        {"자료": "매입자료", "상태": "연결" if purchase["table_exists"] else "미입력", "비고": f"선택월 {purchase['rows']:,}건"},
        {"자료": "재고원장", "상태": "연결" if opening["ledger_exists"] and closing["ledger_exists"] else "미입력", "비고": "월초·월말 재고 평가"},
        {"자료": "기타비용", "상태": "선택 입력", "비고": "쿠팡/상품원가에 포함되지 않은 비용만 입력"},
    ]
    st_obj.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def render_monthly_closing_page(st_obj, pd_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)

    st_obj.markdown("## 📒 월 결산")
    st_obj.caption(
        "상품 하나의 마진이 아니라 사업 전체의 한 달 성적을 정산합니다. "
        "매입액 전부를 비용 처리하지 않고 월초·월말 재고를 반영해 매출원가를 계산합니다."
    )

    months = _available_months(core, db)
    cur = _current_month()
    idx = months.index(cur) if cur in months else 0
    month = st_obj.selectbox("결산 월", months, index=idx, key="monthly_closing_month_v0916")
    start, end = _month_bounds(month)
    opening_date = start - timedelta(days=1)

    mdf, meta = _confirmed(core, month)
    actual = _confirmed_totals(mdf, meta)
    purchase = _purchase_summary(core, db, start, end)
    opening = _inventory_state(core, db, opening_date)
    closing = _inventory_state(core, db, end)
    expenses = _expense_rows(core, db, start, end)
    other_expense = _expense_total(expenses)

    inventory_cogs = opening["value"] + purchase["amount"] - closing["value"]
    commission = actual["commission"]
    rg = actual["inout"] + actual["delivery"]
    returns = actual["return_pickup"] + actual["return_restock"]
    operating_profit = actual["revenue"] - inventory_cogs - commission - rg - returns - actual["ad"] - other_expense
    gross_profit = actual["revenue"] - inventory_cogs
    margin = operating_profit / actual["revenue"] * 100 if actual["revenue"] else 0.0
    cogs_gap = inventory_cogs - actual["product_cogs"]

    ready = (not mdf.empty) and opening["ledger_exists"] and closing["ledger_exists"]
    if ready:
        st_obj.success(f"{month} 월 결산 계산 가능 · {start.isoformat()} ~ {end.isoformat()}")
    else:
        st_obj.warning(f"{month} 결산자료가 아직 완전하지 않습니다. 아래 자료 상태를 확인해 주세요.")

    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("실현매출", _fmt_money(actual["revenue"]))
    c2.metric("매출총이익", _fmt_money(gross_profit))
    c3.metric("결산이익", _fmt_money(operating_profit))
    c4.metric("결산이익률", _fmt_pct(margin))

    st_obj.markdown("### 월 손익")
    pnl_rows = [
        {"구분": "실현매출", "금액": actual["revenue"], "설명": "쿠팡 월 정산 기준"},
        {"구분": "(-) 재고식 매출원가", "금액": inventory_cogs, "설명": "월초재고 + 당월매입 - 월말재고"},
        {"구분": "매출총이익", "금액": gross_profit, "설명": "실현매출 - 매출원가"},
        {"구분": "(-) 판매수수료", "금액": commission, "설명": "실제 정산 수수료"},
        {"구분": "(-) 입출고·배송비", "금액": rg, "설명": "실제 RG 정산비용"},
        {"구분": "(-) 반품비", "금액": returns, "설명": "반품회수·재입고비"},
        {"구분": "(-) 광고비", "금액": actual["ad"], "설명": "월 광고 청구가능액"},
        {"구분": "(-) 기타비용", "금액": other_expense, "설명": "월 결산에서 직접 입력한 비용"},
        {"구분": "결산이익", "금액": operating_profit, "설명": "사업 전체 월 결산 관리이익"},
    ]
    show = pd_obj.DataFrame(pnl_rows)
    show["금액"] = show["금액"].map(_fmt_money)
    st_obj.dataframe(show, use_container_width=True, hide_index=True)

    st_obj.markdown("### 매입·재고")
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("월초 재고금액", _fmt_money(opening["value"]))
    c2.metric("당월 매입액", _fmt_money(purchase["amount"]), f"{purchase['rows']:,}건")
    c3.metric("월말 재고금액", _fmt_money(closing["value"]))
    c4.metric("재고식 매출원가", _fmt_money(inventory_cogs))

    compare = pd_obj.DataFrame([
        {"원가 기준": "월 결산 재고식 매출원가", "금액": inventory_cogs},
        {"원가 기준": "상품 확정손익 매출원가", "금액": actual["product_cogs"]},
        {"원가 기준": "차이", "금액": cogs_gap},
    ])
    comp_show = compare.copy()
    comp_show["금액"] = comp_show["금액"].map(_fmt_money)
    st_obj.dataframe(comp_show, use_container_width=True, hide_index=True)
    st_obj.caption(
        "두 원가가 크게 다르면 월초재고·매입·월말재고 연결 또는 과거 재고원장을 확인해야 합니다. "
        "상품 확정손익은 상품 수익성 관리용, 재고식 원가는 월 전체 결산 검증용입니다."
    )

    if opening["negative_products"] or closing["negative_products"]:
        st_obj.warning(
            f"마이너스 재고 상품이 월초 {opening['negative_products']:,}개, 월말 {closing['negative_products']:,}개 있습니다. "
            "마이너스 수량은 재고자산 0으로 평가했습니다."
        )
    if opening["fallback_products"] or closing["fallback_products"]:
        st_obj.warning("일부 재고 입고원장에 원가가 없어 과거 매입/생산원가 또는 상품 기준원가를 보조값으로 사용했습니다.")

    st_obj.markdown("### 기타비용")
    st_obj.caption(
        "쿠팡 정산비용이나 상품 매입원가에 이미 포함된 금액은 다시 입력하지 마세요. "
        "중복 입력하면 결산이익이 실제보다 낮아집니다."
    )
    with st_obj.expander("기타비용 추가", expanded=False):
        default_date = end if end < date.today() else date.today()
        with st_obj.form("monthly_closing_expense_form_v0916"):
            expense_date = st_obj.date_input("비용일", value=default_date, min_value=start, max_value=end)
            category = st_obj.selectbox("구분", EXPENSE_CATEGORIES)
            amount = st_obj.number_input("금액", min_value=0, step=1000, format="%d")
            memo = st_obj.text_input("메모", placeholder="예: 포장재, 사무용품, 외주작업")
            add = st_obj.form_submit_button("비용 추가")
        if add:
            try:
                _insert_expense(core, db, expense_date.isoformat(), category, amount, memo)
                st_obj.success("기타비용을 추가했습니다.")
                try:
                    st_obj.rerun()
                except Exception:
                    pass
            except Exception as e:
                st_obj.error(str(e))

    if expenses:
        expense_df = pd_obj.DataFrame([
            {"ID": int(r["id"]), "비용일": str(r["expense_date"]), "구분": str(r["category"]), "금액": _num(r["amount"]), "메모": str(r["memo"] or "")}
            for r in expenses
        ])
        total_by_cat = expense_df.groupby("구분", as_index=False)["금액"].sum().sort_values("금액", ascending=False)
        cat_show = total_by_cat.copy()
        cat_show["금액"] = cat_show["금액"].map(_fmt_money)
        st_obj.dataframe(cat_show, use_container_width=True, hide_index=True)

        with st_obj.expander("입력한 기타비용 상세 / 삭제", expanded=False):
            detail = expense_df.copy()
            detail["금액"] = detail["금액"].map(_fmt_money)
            st_obj.dataframe(detail, use_container_width=True, hide_index=True)
            options = expense_df["ID"].astype(int).tolist()
            labels = {
                int(r.ID): f"{r.비용일} · {r.구분} · {_fmt_money(r.금액)} · {r.메모}"
                for r in expense_df.itertuples()
            }
            selected = st_obj.selectbox("삭제할 비용", options, format_func=lambda x: labels.get(int(x), str(x)), key="monthly_closing_delete_expense_v0916")
            if st_obj.button("선택 비용 삭제", key="monthly_closing_delete_button_v0916"):
                _delete_expense(core, db, int(selected))
                st_obj.success("선택한 기타비용을 삭제했습니다.")
                try:
                    st_obj.rerun()
                except Exception:
                    pass
    else:
        st_obj.info("이 달에 직접 입력한 기타비용이 없습니다.")

    st_obj.markdown("### 발생기준 자금수지 참고")
    after_coupang = actual["revenue"] - commission - rg - returns - actual["ad"]
    funding_delta = after_coupang - purchase["amount"] - other_expense
    c1, c2, c3 = st_obj.columns(3)
    c1.metric("쿠팡비용 차감 후", _fmt_money(after_coupang))
    c2.metric("당월 매입·기타지출", _fmt_money(purchase["amount"] + other_expense))
    c3.metric("발생기준 자금수지", _fmt_money(funding_delta))
    st_obj.caption("이 수치는 실제 은행 입금일/지급일 기준 현금흐름이 아니라 선택월에 발생한 매출·비용을 같은 달에 놓고 보는 관리용 참고치입니다.")

    st_obj.markdown("### 결산자료 상태")
    _render_status(st_obj, not mdf.empty, purchase, opening, closing, meta)


def render_product_confirmed_page(st_obj, pd_obj, core, pnl_module, db_path=None):
    """Product-level confirmed management P&L, explicitly separated from monthly closing."""
    db = db_path or core.DEFAULT_DB
    st_obj.markdown("## ✅ 상품 확정손익")
    st_obj.caption(
        "쿠팡 월 정산자료의 실제 매출·수수료·RG·반품·광고비와 ERP 상품원가를 연결해 "
        "상품별 실제 수익성을 보는 관리손익입니다. 사업 전체 월 결산은 '월 결산' 메뉴에서 확인합니다."
    )
    try:
        months = list(core.monthly_available() or [])
    except Exception:
        months = []
    if not months:
        st_obj.info("월 정산자료를 업로드하면 상품 확정손익을 확인할 수 있습니다.")
        return
    month = st_obj.selectbox("확정 월", months, key="product_confirmed_pnl_month_v0916")
    mdf, meta = core.confirmed_monthly_pnl(month)
    if mdf is None or mdf.empty:
        st_obj.info(f"{month} 상품 확정손익 데이터가 없습니다.")
        return

    totals = pnl_module._actual_totals(mdf, meta or {})
    margin = totals["profit"] / totals["revenue"] * 100 if totals["revenue"] else 0.0
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("확정 실현매출", pnl_module._fmt_money(totals["revenue"]))
    c2.metric("상품 확정이익", pnl_module._fmt_money(totals["profit"]))
    c3.metric("상품 이익률", pnl_module._fmt_pct(margin))
    c4.metric("확정 광고비", pnl_module._fmt_money(totals["ad"]))
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("상품원가", pnl_module._fmt_money(totals["cogs"]))
    c2.metric("판매수수료", pnl_module._fmt_money(totals["commission"]))
    c3.metric("입출고·배송비", pnl_module._fmt_money(totals["inout"] + totals["delivery"]))
    c4.metric("반품비", pnl_module._fmt_money(totals["returns"]))

    by_id, _ = pnl_module._product_master(core, db)
    rows = []
    for _, r in mdf.iterrows():
        pid = int(pnl_module._num(r.get("product_id"))) if "product_id" in mdf.columns else 0
        p = by_id.get(pid, {})
        oid = p.get("oid", "")
        name = p.get("name", "")
        if not oid:
            for col in ("option_id", "옵션ID"):
                if col in mdf.columns:
                    oid = pnl_module._oid(r.get(col))
                    if oid:
                        break
        rev = pnl_module._num(r.get("realized_sales"))
        cogs = abs(pnl_module._num(r.get("cogs")))
        comm = abs(pnl_module._num(r.get("commission")))
        inout = abs(pnl_module._num(r.get("inout")))
        delivery = abs(pnl_module._num(r.get("delivery")))
        ret = abs(pnl_module._num(r.get("return_pickup"))) + abs(pnl_module._num(r.get("return_restock")))
        rows.append(
            {
                "옵션ID": oid,
                "상품명": name,
                "실현매출": rev,
                "매출원가": cogs,
                "판매수수료": comm,
                "입출고비": inout,
                "배송비": delivery,
                "반품비": ret,
                "광고전이익": rev - cogs - comm - inout - delivery - ret,
            }
        )
    view = pd_obj.DataFrame(rows)
    q = st_obj.text_input(
        "상품 검색", placeholder="상품명 또는 옵션ID 입력", key="product_confirmed_search_v0916"
    )
    view = pnl_module._search_filter(view, q)
    show = view.copy()
    for col in ("실현매출", "매출원가", "판매수수료", "입출고비", "배송비", "반품비", "광고전이익"):
        if col in show.columns:
            show[col] = show[col].map(pnl_module._fmt_money)
    st_obj.dataframe(
        show,
        use_container_width=True,
        hide_index=True,
        height=min(700, max(220, 38 * (len(show) + 1))),
    )
    st_obj.caption(
        "상품별 광고비가 월 정산서에서 직접 귀속되지 않는 경우 상품표는 광고전이익으로 표시하고, "
        "최종 광고비와 상품 확정이익은 상단 월 합계에 반영합니다."
    )


def patch_source(source: str) -> str:
    source = source.replace('"✅  확정손익",', '"✅  상품 확정손익",', 1)
    source = source.replace('elif page == "✅  확정손익":', 'elif page == "✅  상품 확정손익":', 1)
    source = source.replace(
        '    pnl_views_v0912.render_confirmed_page(st, pd, core)\n',
        '    monthly_closing_v0916.render_product_confirmed_page(st, pd, core, pnl_views_v0912)\n',
        1,
    )

    menu_anchor = '        "✅  상품 확정손익",\n'
    if menu_anchor not in source:
        raise RuntimeError("v0.9.16 상품 확정손익 메뉴 위치를 찾지 못했습니다.")
    if '        "📒  월 결산",\n' not in source:
        source = source.replace(menu_anchor, menu_anchor + '        "📒  월 결산",\n', 1)

    handler_anchor = (
        '# ------------------------------\n'
        '# Provisional vs confirmed variance\n'
        '# ------------------------------\n'
        'elif page == "🔍  손익차이분석":\n'
    )
    handler = (
        '# ------------------------------\n'
        '# Monthly business closing\n'
        '# ------------------------------\n'
        'elif page == "📒  월 결산":\n'
        '    monthly_closing_v0916.render_monthly_closing_page(st, pd, core)\n\n\n'
    )
    if 'elif page == "📒  월 결산":' not in source:
        if handler_anchor not in source:
            raise RuntimeError("v0.9.16 월 결산 화면을 추가할 위치를 찾지 못했습니다.")
        source = source.replace(handler_anchor, handler + handler_anchor, 1)
    return source


def apply(core, db_path=None):
    global _APPLIED
    if _APPLIED:
        return
    _ensure_schema(core, db_path or core.DEFAULT_DB)
    _APPLIED = True
