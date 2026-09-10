"""Item sales analysis page for RG Manager v0.9.186.

Primary source: Coupang RG order API rows, keyed by customer paid_date.
Fallback source: imported sales-stat periods, only when the selected date range
fully contains each source period (period aggregate data cannot be split by day).
"""
from __future__ import annotations

from datetime import date, timedelta
import math
import re
from typing import Any

import pandas as pd
import streamlit as st

PAGE_LABEL = "📊  판매분석"


def _num(value: Any) -> float:
    try:
        x = float(value or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _oid(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    if text.upper().startswith("CP-"):
        text = text[3:]
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(con, table: str) -> set[str]:
    if not _exists(con, table):
        return set()
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _fmt_qty(value: Any) -> str:
    n = _num(value)
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}개"
    return f"{n:,.2f}개"


def _fmt_money(value: Any) -> str:
    return f"{int(round(_num(value))):,}원"


def _product_master(con) -> dict[int, dict[str, Any]]:
    if not _exists(con, "products"):
        return {}
    cols = _cols(con, "products")
    fields = ["id"]
    fields.append("item_code" if "item_code" in cols else "'' AS item_code")
    fields.append("option_id" if "option_id" in cols else "'' AS option_id")
    fields.append("name" if "name" in cols else "'' AS name")
    fields.append("unit_cost" if "unit_cost" in cols else "0 AS unit_cost")
    rows = con.execute("SELECT " + ",".join(fields) + " FROM products").fetchall()
    return {
        int(r["id"]): {
            "item_code": str(r["item_code"] or ""),
            "option_id": _oid(r["option_id"]),
            "name": str(r["name"] or ""),
            "unit_cost": _num(r["unit_cost"]),
        }
        for r in rows
    }


def _api_rows(core, db, start: date, end: date) -> tuple[pd.DataFrame, dict[str, Any]]:
    with core._conn(db) as con:
        if not _exists(con, "coupang_rg_order_items"):
            return pd.DataFrame(), {"source": "none"}
        oc = _cols(con, "coupang_rg_order_items")
        needed = {
            "order_id", "paid_date", "vendor_item_id", "product_id",
            "product_name", "sales_quantity", "unit_sales_price",
        }
        if not needed.issubset(oc):
            return pd.DataFrame(), {"source": "none"}

        products = _product_master(con)
        orders = con.execute(
            """SELECT order_id,paid_date,vendor_item_id,product_id,product_name,
                      sales_quantity,unit_sales_price
               FROM coupang_rg_order_items
               WHERE paid_date>=? AND paid_date<=?
               ORDER BY paid_date,order_id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if not orders:
            return pd.DataFrame(), {"source": "api", "orders": 0}

        returns: dict[tuple[str, str], dict[str, float]] = {}
        if _exists(con, "coupang_return_items"):
            rc = _cols(con, "coupang_return_items")
            if {"order_id", "vendor_item_id", "receipt_type", "cancel_count"}.issubset(rc):
                order_ids = sorted({str(r["order_id"] or "") for r in orders if str(r["order_id"] or "")})
                for pos in range(0, len(order_ids), 500):
                    batch = order_ids[pos:pos + 500]
                    placeholders = ",".join("?" for _ in batch)
                    sql = f"""SELECT order_id,vendor_item_id,receipt_type,
                                     SUM(COALESCE(cancel_count,0)) qty
                              FROM coupang_return_items
                              WHERE order_id IN ({placeholders})
                              GROUP BY order_id,vendor_item_id,receipt_type"""
                    for rr in con.execute(sql, batch).fetchall():
                        key = (str(rr["order_id"] or ""), _oid(rr["vendor_item_id"]))
                        bucket = returns.setdefault(key, {"cancel": 0.0, "return": 0.0})
                        kind = str(rr["receipt_type"] or "").upper()
                        if "CANCEL" in kind:
                            bucket["cancel"] += abs(_num(rr["qty"]))
                        else:
                            bucket["return"] += abs(_num(rr["qty"]))

        grouped: dict[tuple[int, str], dict[str, Any]] = {}
        order_sets: dict[tuple[int, str], set[str]] = {}
        for r in orders:
            pid = int(r["product_id"]) if r["product_id"] is not None else 0
            oid = _oid(r["vendor_item_id"])
            key = (pid, oid)
            p = products.get(pid, {})
            qty = max(0.0, _num(r["sales_quantity"]))
            price = max(0.0, _num(r["unit_sales_price"]))
            item = grouped.setdefault(
                key,
                {
                    "상품코드": p.get("item_code", ""),
                    "옵션ID": oid or p.get("option_id", ""),
                    "상품명": p.get("name") or str(r["product_name"] or ""),
                    "판매수량": 0.0,
                    "취소수량": 0.0,
                    "반품수량": 0.0,
                    "매출액": 0.0,
                    "상품원가": 0.0,
                },
            )
            item["판매수량"] += qty
            item["매출액"] += qty * price
            item["상품원가"] += qty * max(0.0, _num(p.get("unit_cost")))
            order_id = str(r["order_id"] or "")
            order_sets.setdefault(key, set()).add(order_id)
            ret = returns.get((order_id, oid), {})
            item["취소수량"] += _num(ret.get("cancel"))
            item["반품수량"] += _num(ret.get("return"))

        out = []
        for key, item in grouped.items():
            sold = _num(item["판매수량"])
            cancel = min(sold, _num(item["취소수량"]))
            remain = max(0.0, sold - cancel)
            returned = min(remain, _num(item["반품수량"]))
            net = max(0.0, sold - cancel - returned)
            avg = _num(item["매출액"]) / sold if sold else 0.0
            cost_per = _num(item["상품원가"]) / sold if sold else 0.0
            out.append(
                {
                    **item,
                    "주문건수": len(order_sets.get(key, set())),
                    "취소수량": cancel,
                    "반품수량": returned,
                    "실판매수량": net,
                    "평균판매가": avg,
                    "실판매원가": net * cost_per,
                    "매출-원가": _num(item["매출액"]) - net * cost_per,
                }
            )
        return pd.DataFrame(out), {"source": "api", "orders": len(orders)}


def _sales_stats_rows(core, db, start: date, end: date) -> tuple[pd.DataFrame, dict[str, Any]]:
    with core._conn(db) as con:
        if not (_exists(con, "imports") and _exists(con, "sales_stats")):
            return pd.DataFrame(), {"source": "none"}
        sc = _cols(con, "sales_stats")
        if not {"import_id", "product_id", "net_qty"}.issubset(sc):
            return pd.DataFrame(), {"source": "none"}
        imports = con.execute(
            """SELECT id,period_start,period_end
               FROM imports
               WHERE data_type='sales_stats'
                 AND period_start>=? AND period_end<=?
               ORDER BY period_start,period_end,id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if not imports:
            return pd.DataFrame(), {"source": "sales_stats", "imports": 0}
        ids = [int(r["id"]) for r in imports]
        placeholders = ",".join("?" for _ in ids)
        products = _product_master(con)
        rows = con.execute(
            f"""SELECT product_id,SUM(COALESCE(net_qty,0)) net_qty
                FROM sales_stats
                WHERE import_id IN ({placeholders})
                GROUP BY product_id""",
            ids,
        ).fetchall()
        out = []
        for r in rows:
            pid = int(r["product_id"])
            p = products.get(pid, {})
            qty = _num(r["net_qty"])
            if abs(qty) <= 1e-12:
                continue
            out.append(
                {
                    "상품코드": p.get("item_code", ""),
                    "옵션ID": p.get("option_id", ""),
                    "상품명": p.get("name", ""),
                    "주문건수": None,
                    "판매수량": qty,
                    "취소수량": None,
                    "반품수량": None,
                    "실판매수량": qty,
                    "매출액": None,
                    "평균판매가": None,
                    "상품원가": None,
                    "실판매원가": None,
                    "매출-원가": None,
                }
            )
        covered_days: set[str] = set()
        for r in imports:
            try:
                a = date.fromisoformat(str(r["period_start"])[:10])
                b = date.fromisoformat(str(r["period_end"])[:10])
            except Exception:
                continue
            d = a
            while d <= b:
                covered_days.add(d.isoformat())
                d += timedelta(days=1)
        return pd.DataFrame(out), {
            "source": "sales_stats",
            "imports": len(imports),
            "covered_days": len(covered_days),
        }


def _filter(df: pd.DataFrame, query: str) -> pd.DataFrame:
    q = str(query or "").strip().lower()
    if not q or df.empty:
        return df
    words = [w for w in re.split(r"\s+", q) if w]
    hay = df[["상품코드", "옵션ID", "상품명"]].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    mask = pd.Series(True, index=df.index)
    for word in words:
        mask &= hay.str.contains(re.escape(word), regex=True)
    return df.loc[mask].copy()


def render_page(st_obj, pd_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)

    st_obj.markdown("## 📊 판매분석")
    st_obj.caption("품목별 판매현황 · 고객 결제일 기준으로 특정 기간에 어떤 상품이 몇 개 팔렸는지 확인합니다.")
    st_obj.radio(
        "판매분석 메뉴",
        ["품목별 판매현황"],
        horizontal=True,
        label_visibility="collapsed",
        key="sales_analysis_submenu_v09186",
    )

    today = date.today()
    default_start = today.replace(day=1)
    c1, c2 = st_obj.columns(2)
    start = c1.date_input("조회 시작일", value=default_start, key="sales_analysis_start_v09186")
    end = c2.date_input("조회 종료일", value=today, key="sales_analysis_end_v09186")
    if start > end:
        st_obj.error("조회 시작일은 종료일보다 늦을 수 없습니다.")
        return

    df, meta = _api_rows(core, db, start, end)
    source = meta.get("source")
    if df.empty:
        fallback, fallback_meta = _sales_stats_rows(core, db, start, end)
        if not fallback.empty:
            df, meta, source = fallback, fallback_meta, "sales_stats"

    if df.empty:
        st_obj.info(
            "선택한 기간의 판매자료가 없습니다. `데이터·관리 > 쿠팡 API 연동`에서 주문 동기화를 실행하거나 "
            "해당 기간의 재고현황 판매통계를 입력해 주세요."
        )
        return

    query = st_obj.text_input(
        "상품 검색",
        placeholder="상품명, ERP 상품코드 또는 쿠팡 옵션ID 입력",
        key="sales_analysis_search_v09186",
    )
    view = _filter(df, query)

    total_qty = float(pd.to_numeric(view["판매수량"], errors="coerce").fillna(0).sum())
    net_qty = float(pd.to_numeric(view["실판매수량"], errors="coerce").fillna(0).sum())
    total_orders = pd.to_numeric(view["주문건수"], errors="coerce").fillna(0).sum()
    revenue = pd.to_numeric(view["매출액"], errors="coerce").fillna(0).sum()

    k1, k2, k3, k4 = st_obj.columns(4)
    k1.metric("판매수량", _fmt_qty(total_qty))
    k2.metric("실판매수량", _fmt_qty(net_qty))
    k3.metric("주문건수", f"{int(round(total_orders)):,}건" if source == "api" else "-")
    k4.metric("주문금액", _fmt_money(revenue) if source == "api" else "-")

    if source == "api":
        st_obj.caption(
            "기준: 쿠팡 RG 주문 API의 고객 결제일. 취소·반품 동기화 자료가 있으면 실판매수량에서 차감합니다. "
            "`매출-원가`는 수수료·RG비·광고비를 뺀 최종이익이 아니라 단순 참고값입니다."
        )
    else:
        expected_days = (end - start).days + 1
        covered = int(meta.get("covered_days", 0))
        st_obj.warning(
            f"이 기간은 주문 API 자료가 없어 재고현황 판매통계의 기간 집계값으로 표시합니다. "
            f"선택 기간 {expected_days}일 중 입력자료가 덮는 날짜는 {covered}일입니다. "
            "판매통계는 일별 자료가 아니므로 일부 기간을 임의 분할하지 않습니다."
        )

    sort_col = "실판매수량"
    view = view.sort_values(sort_col, ascending=False, kind="stable").reset_index(drop=True)
    show = view.copy()
    for col in ("판매수량", "취소수량", "반품수량", "실판매수량"):
        if col in show.columns:
            show[col] = show[col].map(lambda x: "-" if pd.isna(x) else _fmt_qty(x))
    if "주문건수" in show.columns:
        show["주문건수"] = show["주문건수"].map(
            lambda x: "-" if pd.isna(x) else f"{int(round(_num(x))):,}건"
        )
    for col in ("매출액", "평균판매가", "상품원가", "실판매원가", "매출-원가"):
        if col in show.columns:
            show[col] = show[col].map(lambda x: "-" if pd.isna(x) else _fmt_money(x))

    columns = [
        "상품코드", "옵션ID", "상품명", "판매수량", "취소수량", "반품수량", "실판매수량",
        "주문건수", "매출액", "평균판매가", "실판매원가", "매출-원가",
    ]
    columns = [c for c in columns if c in show.columns]
    st_obj.dataframe(
        show[columns],
        use_container_width=True,
        hide_index=True,
        height=min(720, max(240, 38 * (len(show) + 1))),
    )

    chart = view[["상품명", "실판매수량"]].copy()
    chart = chart[chart["실판매수량"] > 0].head(20)
    if not chart.empty:
        st_obj.markdown("### 판매수량 상위 상품")
        st_obj.bar_chart(chart.set_index("상품명"), height=320)


def patch_source(source: str) -> str:
    if f'"{PAGE_LABEL}",' not in source:
        anchor = '"📈  잠정손익",'
        if anchor not in source:
            raise RuntimeError("v0.9.186 판매분석 메뉴를 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, f'"{PAGE_LABEL}",\n        ' + anchor, 1)

    marker = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
    if marker not in source:
        raise RuntimeError("v0.9.186 판매분석 화면을 추가할 위치를 찾지 못했습니다.")
    insert = (
        '# ------------------------------\n'
        '# Sales analysis\n'
        '# ------------------------------\n'
        f'elif page == "{PAGE_LABEL}":\n'
        '    sales_analysis_v09186.render_page(st, pd, core)\n\n\n'
    )
    if f'elif page == "{PAGE_LABEL}":' not in source:
        source = source.replace(marker, insert + marker, 1)
    return source
