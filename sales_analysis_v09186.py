"""Item sales analysis page for RG Manager v0.9.186."""
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
    return f"{int(round(n)):,}개" if abs(n - round(n)) < 1e-9 else f"{n:,.2f}개"


def _fmt_money(value: Any) -> str:
    return f"{int(round(_num(value))):,}원"


def _days_between(start: date, end: date) -> set[str]:
    out: set[str] = set()
    d = start
    while d <= end:
        out.add(d.isoformat())
        d += timedelta(days=1)
    return out


def _order_sync_coverage(con, start: date, end: date) -> set[str]:
    if not _exists(con, "coupang_api_sync_runs"):
        return set()
    cols = _cols(con, "coupang_api_sync_runs")
    needed = {"sync_type", "period_start", "period_end", "status"}
    if not needed.issubset(cols):
        return set()
    rows = con.execute(
        """SELECT period_start,period_end
           FROM coupang_api_sync_runs
           WHERE sync_type='orders' AND status='success'
             AND period_end>=? AND period_start<=?""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    covered: set[str] = set()
    for row in rows:
        try:
            a = max(start, date.fromisoformat(str(row["period_start"])[:10]))
            b = min(end, date.fromisoformat(str(row["period_end"])[:10]))
        except Exception:
            continue
        covered |= _days_between(a, b)
    return covered


def _product_master(con) -> dict[int, dict[str, str]]:
    if not _exists(con, "products"):
        return {}
    cols = _cols(con, "products")
    fields = ["id"]
    fields.append("item_code" if "item_code" in cols else "'' AS item_code")
    fields.append("option_id" if "option_id" in cols else "'' AS option_id")
    fields.append("name" if "name" in cols else "'' AS name")
    rows = con.execute("SELECT " + ",".join(fields) + " FROM products").fetchall()
    return {
        int(r["id"]): {
            "item_code": str(r["item_code"] or ""),
            "option_id": _oid(r["option_id"]),
            "name": str(r["name"] or ""),
        }
        for r in rows
    }


def _return_qty_by_order(con, order_ids: list[str]) -> dict[tuple[str, str], dict[str, float]]:
    if not order_ids or not _exists(con, "coupang_return_items"):
        return {}
    cols = _cols(con, "coupang_return_items")
    needed = {"order_id", "vendor_item_id", "receipt_type", "cancel_count"}
    if not needed.issubset(cols):
        return {}
    out: dict[tuple[str, str], dict[str, float]] = {}
    for pos in range(0, len(order_ids), 400):
        batch = order_ids[pos:pos + 400]
        marks = ",".join("?" for _ in batch)
        sql = f"""SELECT order_id,vendor_item_id,receipt_type,
                         SUM(COALESCE(cancel_count,0)) qty
                  FROM coupang_return_items
                  WHERE order_id IN ({marks})
                  GROUP BY order_id,vendor_item_id,receipt_type"""
        for r in con.execute(sql, batch).fetchall():
            key = (str(r["order_id"] or ""), _oid(r["vendor_item_id"]))
            bucket = out.setdefault(key, {"cancel": 0.0, "return": 0.0})
            kind = str(r["receipt_type"] or "").upper()
            if "CANCEL" in kind:
                bucket["cancel"] += abs(_num(r["qty"]))
            else:
                bucket["return"] += abs(_num(r["qty"]))
    return out


def _api_sales(core, db, start: date, end: date):
    with core._conn(db) as con:
        coverage = _order_sync_coverage(con, start, end)
        if not _exists(con, "coupang_rg_order_items"):
            return pd.DataFrame(), coverage
        cols = _cols(con, "coupang_rg_order_items")
        needed = {
            "order_id", "paid_date", "vendor_item_id", "product_id",
            "product_name", "sales_quantity", "unit_sales_price",
        }
        if not needed.issubset(cols):
            return pd.DataFrame(), coverage

        rows = con.execute(
            """SELECT order_id,vendor_item_id,product_id,product_name,
                      sales_quantity,unit_sales_price
               FROM coupang_rg_order_items
               WHERE paid_date>=? AND paid_date<=?""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if not rows:
            return pd.DataFrame(), coverage

        products = _product_master(con)
        order_ids = sorted({str(r["order_id"] or "") for r in rows if str(r["order_id"] or "")})
        returns = _return_qty_by_order(con, order_ids)

        grouped: dict[tuple[int, str], dict[str, Any]] = {}
        order_sets: dict[tuple[int, str], set[str]] = {}
        return_applied: set[tuple[str, str]] = set()

        for r in rows:
            pid = int(r["product_id"]) if r["product_id"] is not None else 0
            oid = _oid(r["vendor_item_id"])
            key = (pid, oid)
            product = products.get(pid, {})
            qty = max(0.0, _num(r["sales_quantity"]))
            price = max(0.0, _num(r["unit_sales_price"]))
            item = grouped.setdefault(
                key,
                {
                    "상품코드": product.get("item_code", ""),
                    "옵션ID": oid or product.get("option_id", ""),
                    "상품명": product.get("name") or str(r["product_name"] or ""),
                    "판매수량": 0.0,
                    "취소수량": 0.0,
                    "반품수량": 0.0,
                    "주문금액": 0.0,
                },
            )
            item["판매수량"] += qty
            item["주문금액"] += qty * price
            order_id = str(r["order_id"] or "")
            order_sets.setdefault(key, set()).add(order_id)

            return_key = (order_id, oid)
            if return_key not in return_applied:
                rq = returns.get(return_key, {})
                item["취소수량"] += _num(rq.get("cancel"))
                item["반품수량"] += _num(rq.get("return"))
                return_applied.add(return_key)

        out = []
        for key, item in grouped.items():
            sold = _num(item["판매수량"])
            canceled = min(sold, _num(item["취소수량"]))
            returned = min(max(0.0, sold - canceled), _num(item["반품수량"]))
            net = max(0.0, sold - canceled - returned)
            out.append(
                {
                    **item,
                    "실판매수량": net,
                    "주문건수": len(order_sets.get(key, set())),
                    "평균판매가": _num(item["주문금액"]) / sold if sold else 0.0,
                }
            )
        return pd.DataFrame(out), coverage


def _sales_stats_fallback(core, db, start: date, end: date):
    with core._conn(db) as con:
        if not (_exists(con, "imports") and _exists(con, "sales_stats")):
            return pd.DataFrame(), set()
        cols = _cols(con, "sales_stats")
        if not {"import_id", "product_id", "net_qty"}.issubset(cols):
            return pd.DataFrame(), set()

        imports = con.execute(
            """SELECT id,period_start,period_end
               FROM imports
               WHERE data_type='sales_stats'
                 AND period_start>=? AND period_end<=?
               ORDER BY period_start,period_end,id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if not imports:
            return pd.DataFrame(), set()

        ids = [int(r["id"]) for r in imports]
        marks = ",".join("?" for _ in ids)
        products = _product_master(con)
        rows = con.execute(
            f"""SELECT product_id,SUM(COALESCE(net_qty,0)) net_qty
                FROM sales_stats
                WHERE import_id IN ({marks})
                GROUP BY product_id""",
            ids,
        ).fetchall()

        covered: set[str] = set()
        for r in imports:
            try:
                a = date.fromisoformat(str(r["period_start"])[:10])
                b = date.fromisoformat(str(r["period_end"])[:10])
            except Exception:
                continue
            covered |= _days_between(a, b)

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
                    "판매수량": qty,
                    "취소수량": None,
                    "반품수량": None,
                    "실판매수량": qty,
                    "주문건수": None,
                    "주문금액": None,
                    "평균판매가": None,
                }
            )
        return pd.DataFrame(out), covered


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

    # sales_period_v087 globally wraps date_input while its upload screen is active.
    # A page switch can leave that session flag behind, so explicitly disable it here.
    st_obj.session_state["_rg_sales_stats_period_active"] = False

    st_obj.markdown("## 📊 판매분석")
    st_obj.caption("품목별 판매현황 · 고객 결제일 기준으로 특정 기간에 어떤 상품이 몇 개 팔렸는지 확인합니다.")
    st_obj.radio(
        "판매분석 메뉴", ["품목별 판매현황"], horizontal=True,
        label_visibility="collapsed", key="sales_analysis_submenu_v09186",
    )

    today = date.today()
    c1, c2 = st_obj.columns(2)
    start = c1.date_input("조회 시작일", value=today.replace(day=1), key="sales_analysis_start_v09186")
    end = c2.date_input("조회 종료일", value=today, key="sales_analysis_end_v09186")
    if start > end:
        st_obj.error("조회 시작일은 종료일보다 늦을 수 없습니다.")
        return

    wanted_days = _days_between(start, end)
    df, api_coverage = _api_sales(core, db, start, end)
    using_api = bool(api_coverage or not df.empty)

    if not using_api:
        df, stat_coverage = _sales_stats_fallback(core, db, start, end)
    else:
        stat_coverage = set()

    query = st_obj.text_input(
        "상품 검색", placeholder="상품명, ERP 상품코드 또는 쿠팡 옵션ID 입력",
        key="sales_analysis_search_v09186",
    )
    view = _filter(df, query)

    total_qty = float(pd.to_numeric(view.get("판매수량", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    net_qty = float(pd.to_numeric(view.get("실판매수량", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    orders = float(pd.to_numeric(view.get("주문건수", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())
    amount = float(pd.to_numeric(view.get("주문금액", pd.Series(dtype=float)), errors="coerce").fillna(0).sum())

    k1, k2, k3, k4 = st_obj.columns(4)
    k1.metric("판매수량", _fmt_qty(total_qty))
    k2.metric("실판매수량", _fmt_qty(net_qty))
    k3.metric("주문건수", f"{int(round(orders)):,}건" if using_api else "-")
    k4.metric("주문금액", _fmt_money(amount) if using_api else "-")

    if using_api:
        missing = wanted_days - api_coverage
        if missing:
            st_obj.warning(
                f"선택한 {len(wanted_days)}일 중 주문 API 동기화가 확인된 날짜는 {len(api_coverage)}일입니다. "
                f"나머지 {len(missing)}일은 판매 0개가 아니라 '미동기화'일 수 있습니다. "
                "`데이터·관리 > 쿠팡 API 연동`에서 해당 기간 주문을 동기화하면 정확해집니다."
            )
        else:
            st_obj.caption(
                "기준: 쿠팡 RG 주문 API 고객 결제일. 동기화된 취소·반품 접수수량을 반영해 실판매수량을 계산합니다."
            )
    elif stat_coverage:
        missing = wanted_days - stat_coverage
        st_obj.warning(
            "주문 API 자료가 없어 재고현황 판매통계의 기간 집계값을 사용합니다. "
            f"선택한 {len(wanted_days)}일 중 판매통계가 덮는 날짜는 {len(stat_coverage)}일입니다."
            + (f" {len(missing)}일은 자료가 없습니다." if missing else "")
            + " 판매통계는 일별로 임의 분할하지 않습니다."
        )
    else:
        st_obj.info(
            "선택한 기간의 판매자료가 없습니다. `데이터·관리 > 쿠팡 API 연동`에서 주문 동기화를 실행하거나 "
            "해당 기간의 재고현황 판매통계를 입력해 주세요."
        )
        return

    if view.empty:
        st_obj.info("현재 검색조건에 해당하는 판매상품이 없습니다.")
        return

    view = view.sort_values("실판매수량", ascending=False, kind="stable").reset_index(drop=True)
    show = view.copy()
    for col in ("판매수량", "취소수량", "반품수량", "실판매수량"):
        show[col] = show[col].map(lambda x: "-" if pd.isna(x) else _fmt_qty(x))
    show["주문건수"] = show["주문건수"].map(
        lambda x: "-" if pd.isna(x) else f"{int(round(_num(x))):,}건"
    )
    for col in ("주문금액", "평균판매가"):
        show[col] = show[col].map(lambda x: "-" if pd.isna(x) else _fmt_money(x))

    st_obj.dataframe(
        show[[
            "상품코드", "옵션ID", "상품명", "판매수량", "취소수량", "반품수량",
            "실판매수량", "주문건수", "주문금액", "평균판매가",
        ]],
        use_container_width=True, hide_index=True,
        height=min(720, max(240, 38 * (len(show) + 1))),
    )

    chart = view.loc[view["실판매수량"] > 0, ["상품명", "실판매수량"]].head(20)
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
    if f'elif page == "{PAGE_LABEL}":' not in source:
        insert = (
            '# ------------------------------\n'
            '# Sales analysis\n'
            '# ------------------------------\n'
            f'elif page == "{PAGE_LABEL}":\n'
            '    sales_analysis_v09186.render_page(st, pd, core)\n\n\n'
        )
        source = source.replace(marker, insert + marker, 1)
    return source
