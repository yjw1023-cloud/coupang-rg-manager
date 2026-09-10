"""Item sales analysis + safe routing bridge (v0.9.192).

Key behavior:
- product search always searches the ERP product master first;
- sales-stat Excel is the primary quantity source for periods it fully covers;
- Coupang order API supplements only dates not already covered by sales-stat periods;
- a matching ERP product is still shown with zero quantity when no sales row exists.
"""
from __future__ import annotations

from datetime import date, timedelta
import math
import re
import sys
from typing import Any

import pandas as pd
import streamlit as st

PAGE_TEXT = "📊  판매분석"


def _install_sidebar_route() -> None:
    """Render this page from grouped navigation even when an older app.py lacks its branch."""
    sidebar = sys.modules.get("sidebar_groups_v0917")
    if sidebar is None:
        return
    original = getattr(sidebar, "render_sidebar", None)
    if not callable(original) or getattr(original, "_rg_sales_route_v09192", False):
        return

    def wrapped(st_obj, options, default_page=None):
        current = original(st_obj, options, default_page)
        if "판매분석" not in str(current):
            return current
        try:
            import core as core_module
            render_page(st_obj, pd, core_module)
        except Exception as exc:
            st_obj.error(f"판매분석 화면을 여는 중 오류가 발생했습니다: {exc}")
            st_obj.info("다른 ERP 메뉴는 계속 사용할 수 있습니다. 프로그램 업데이트에서 최신 버전을 적용해 주세요.")
        return "__RG_SALES_ANALYSIS_RENDERED__"

    wrapped._rg_sales_route_v09192 = True
    wrapped._rg_sales_route_v09191 = True
    sidebar.render_sidebar = wrapped


class _SalesPageLabel(str):
    def __str__(self):
        _install_sidebar_route()
        return super().__str__()


PAGE_LABEL = _SalesPageLabel(PAGE_TEXT)


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


def _days(start: date, end: date) -> set[str]:
    out: set[str] = set()
    cur = start
    while cur <= end:
        out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _product_master(con) -> dict[int, dict[str, Any]]:
    if not _exists(con, "products"):
        return {}
    cols = _cols(con, "products")
    fields = ["id"]
    fields.append("item_code" if "item_code" in cols else "'' AS item_code")
    fields.append("option_id" if "option_id" in cols else "'' AS option_id")
    fields.append("name" if "name" in cols else "'' AS name")
    fields.append("active" if "active" in cols else "1 AS active")
    fields.append("item_type" if "item_type" in cols else "'' AS item_type")
    rows = con.execute("SELECT " + ",".join(fields) + " FROM products").fetchall()
    return {
        int(r["id"]): {
            "product_id": int(r["id"]),
            "상품코드": str(r["item_code"] or ""),
            "옵션ID": _oid(r["option_id"]),
            "상품명": str(r["name"] or ""),
            "_active": int(_num(r["active"])),
            "_item_type": str(r["item_type"] or ""),
        }
        for r in rows
    }


def _searchable_products(core, db) -> pd.DataFrame:
    """Active sellable ERP products, independent of whether they sold in the selected period."""
    with core._conn(db) as con:
        master = _product_master(con)
    rows = []
    for p in master.values():
        if int(p.get("_active", 1)) == 0:
            continue
        item_type = str(p.get("_item_type") or "").strip().lower()
        option_id = _oid(p.get("옵션ID"))
        if item_type == "raw" and not option_id:
            continue
        if not option_id and item_type not in {"finished", "product", "sale"}:
            continue
        rows.append({
            "product_id": int(p["product_id"]),
            "상품코드": str(p.get("상품코드") or ""),
            "옵션ID": option_id,
            "상품명": str(p.get("상품명") or ""),
        })
    if not rows:
        return pd.DataFrame(columns=["product_id", "상품코드", "옵션ID", "상품명"])
    return pd.DataFrame(rows).drop_duplicates(subset=["product_id"]).reset_index(drop=True)


def _filter_products(df: pd.DataFrame, query: str) -> pd.DataFrame:
    q = str(query or "").strip().lower()
    if not q or df.empty:
        return df.copy()
    words = [x for x in re.split(r"\s+", q) if x]
    hay = df[["상품코드", "옵션ID", "상품명"]].fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    mask = pd.Series(True, index=df.index)
    for word in words:
        mask &= hay.str.contains(re.escape(word), regex=True)
    return df.loc[mask].copy()


def _api_coverage(con, start: date, end: date) -> set[str]:
    if not _exists(con, "coupang_api_sync_runs"):
        return set()
    cols = _cols(con, "coupang_api_sync_runs")
    if not {"sync_type", "period_start", "period_end", "status"}.issubset(cols):
        return set()
    rows = con.execute(
        """SELECT period_start,period_end FROM coupang_api_sync_runs
           WHERE sync_type='orders' AND status='success'
             AND period_end>=? AND period_start<=?""",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    covered: set[str] = set()
    for r in rows:
        try:
            a = max(start, date.fromisoformat(str(r["period_start"])[:10]))
            b = min(end, date.fromisoformat(str(r["period_end"])[:10]))
            covered |= _days(a, b)
        except Exception:
            pass
    return covered


def _api_coverage_db(core, db, start: date, end: date) -> set[str]:
    with core._conn(db) as con:
        return _api_coverage(con, start, end)


def _return_map(con, order_ids: list[str]) -> dict[tuple[str, str], tuple[float, float]]:
    if not order_ids or not _exists(con, "coupang_return_items"):
        return {}
    cols = _cols(con, "coupang_return_items")
    if not {"order_id", "vendor_item_id", "receipt_type", "cancel_count"}.issubset(cols):
        return {}
    out: dict[tuple[str, str], list[float]] = {}
    for pos in range(0, len(order_ids), 400):
        batch = order_ids[pos:pos + 400]
        marks = ",".join("?" for _ in batch)
        rows = con.execute(
            f"""SELECT order_id,vendor_item_id,receipt_type,SUM(COALESCE(cancel_count,0)) qty
                FROM coupang_return_items WHERE order_id IN ({marks})
                GROUP BY order_id,vendor_item_id,receipt_type""",
            batch,
        ).fetchall()
        for r in rows:
            key = (str(r["order_id"] or ""), _oid(r["vendor_item_id"]))
            vals = out.setdefault(key, [0.0, 0.0])
            if "CANCEL" in str(r["receipt_type"] or "").upper():
                vals[0] += abs(_num(r["qty"]))
            else:
                vals[1] += abs(_num(r["qty"]))
    return {k: (v[0], v[1]) for k, v in out.items()}


def _api_sales(core, db, start: date, end: date, allowed_days: set[str] | None = None) -> pd.DataFrame:
    """Order API rows restricted to supplement days so quantities are never double-counted."""
    if allowed_days is not None and not allowed_days:
        return pd.DataFrame()
    with core._conn(db) as con:
        if not _exists(con, "coupang_rg_order_items"):
            return pd.DataFrame()
        needed = {
            "order_id", "paid_date", "vendor_item_id", "product_id",
            "product_name", "sales_quantity", "unit_sales_price",
        }
        if not needed.issubset(_cols(con, "coupang_rg_order_items")):
            return pd.DataFrame()
        rows = con.execute(
            """SELECT order_id,paid_date,vendor_item_id,product_id,product_name,
                      sales_quantity,unit_sales_price
               FROM coupang_rg_order_items
               WHERE paid_date>=? AND paid_date<=?""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if allowed_days is not None:
            rows = [r for r in rows if str(r["paid_date"] or "")[:10] in allowed_days]
        if not rows:
            return pd.DataFrame()

        master = _product_master(con)
        order_ids = sorted({str(r["order_id"] or "") for r in rows if str(r["order_id"] or "")})
        returns = _return_map(con, order_ids)
        grouped: dict[tuple[int, str], dict[str, Any]] = {}
        order_sets: dict[tuple[int, str], set[str]] = {}
        applied_returns: set[tuple[str, str]] = set()

        for r in rows:
            pid = int(r["product_id"]) if r["product_id"] is not None else 0
            oid = _oid(r["vendor_item_id"])
            key = (pid, oid)
            p = master.get(pid, {})
            qty = max(0.0, _num(r["sales_quantity"]))
            price = max(0.0, _num(r["unit_sales_price"]))
            item = grouped.setdefault(key, {
                "product_id": pid,
                "상품코드": p.get("상품코드", ""),
                "옵션ID": oid or p.get("옵션ID", ""),
                "상품명": p.get("상품명") or str(r["product_name"] or ""),
                "판매수량": 0.0,
                "취소·반품수량": 0.0,
                "실판매수량": 0.0,
                "API주문건수": 0.0,
                "API주문금액": 0.0,
                "_api_sales_qty": 0.0,
                "_source_api": 1,
                "_source_stats": 0,
            })
            item["판매수량"] += qty
            item["API주문금액"] += qty * price
            item["_api_sales_qty"] += qty
            order_id = str(r["order_id"] or "")
            order_sets.setdefault(key, set()).add(order_id)
            rk = (order_id, oid)
            if rk not in applied_returns:
                cancel, returned = returns.get(rk, (0.0, 0.0))
                item["취소·반품수량"] += cancel + returned
                applied_returns.add(rk)

        out = []
        for key, item in grouped.items():
            sold = _num(item["판매수량"])
            canceled = min(sold, _num(item["취소·반품수량"]))
            net = max(0.0, sold - canceled)
            item["취소·반품수량"] = canceled
            item["실판매수량"] = net
            item["API주문건수"] = len(order_sets.get(key, set()))
            out.append(item)
        return pd.DataFrame(out)


def _sales_stats(core, db, start: date, end: date) -> tuple[pd.DataFrame, set[str]]:
    """Use only sales-stat periods fully contained in the requested range."""
    with core._conn(db) as con:
        if not (_exists(con, "imports") and _exists(con, "sales_stats")):
            return pd.DataFrame(), set()
        cols = _cols(con, "sales_stats")
        if not {"import_id", "product_id", "net_qty"}.issubset(cols):
            return pd.DataFrame(), set()

        imports = con.execute(
            """SELECT id,period_start,period_end FROM imports
               WHERE data_type='sales_stats'
                 AND period_start>=? AND period_end<=?
               ORDER BY period_start,period_end,id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if not imports:
            return pd.DataFrame(), set()

        ids = [int(r["id"]) for r in imports]
        marks = ",".join("?" for _ in ids)
        master = _product_master(con)
        sales_expr = "SUM(COALESCE(sales_qty,0))" if "sales_qty" in cols else "0"
        cancel_expr = "SUM(COALESCE(cancel_qty,0))" if "cancel_qty" in cols else "0"
        rows = con.execute(
            f"""SELECT product_id,
                       {sales_expr} AS sales_qty,
                       {cancel_expr} AS cancel_qty,
                       SUM(COALESCE(net_qty,0)) AS net_qty
                FROM sales_stats
                WHERE import_id IN ({marks})
                GROUP BY product_id""",
            ids,
        ).fetchall()

        covered: set[str] = set()
        for r in imports:
            try:
                covered |= _days(
                    date.fromisoformat(str(r["period_start"])[:10]),
                    date.fromisoformat(str(r["period_end"])[:10]),
                )
            except Exception:
                pass

        out = []
        for r in rows:
            pid = int(r["product_id"])
            p = master.get(pid, {})
            net = _num(r["net_qty"])
            cancel = abs(_num(r["cancel_qty"]))
            gross = _num(r["sales_qty"])
            if gross <= 0 and net > 0:
                gross = net + cancel
            if abs(gross) <= 1e-12 and abs(net) <= 1e-12 and abs(cancel) <= 1e-12:
                continue
            out.append({
                "product_id": pid,
                "상품코드": p.get("상품코드", ""),
                "옵션ID": p.get("옵션ID", ""),
                "상품명": p.get("상품명", ""),
                "판매수량": gross,
                "취소·반품수량": cancel,
                "실판매수량": net,
                "API주문건수": 0.0,
                "API주문금액": 0.0,
                "_api_sales_qty": 0.0,
                "_source_api": 0,
                "_source_stats": 1,
            })
        return pd.DataFrame(out), covered


def _combine_sources(stats_df: pd.DataFrame, api_df: pd.DataFrame) -> pd.DataFrame:
    frames = [x for x in (stats_df, api_df) if x is not None and not x.empty]
    if not frames:
        return pd.DataFrame(columns=[
            "product_id", "상품코드", "옵션ID", "상품명", "판매수량", "취소·반품수량",
            "실판매수량", "API주문건수", "API주문금액", "평균판매가", "자료기준",
        ])
    raw = pd.concat(frames, ignore_index=True, sort=False)
    for col in ("판매수량", "취소·반품수량", "실판매수량", "API주문건수", "API주문금액",
                "_api_sales_qty", "_source_api", "_source_stats"):
        raw[col] = pd.to_numeric(raw.get(col, 0), errors="coerce").fillna(0)

    rows = []
    raw["_merge_key"] = raw.apply(
        lambda r: f"p:{int(r['product_id'])}" if int(_num(r.get("product_id"))) > 0
        else f"o:{_oid(r.get('옵션ID'))}",
        axis=1,
    )
    for _, g in raw.groupby("_merge_key", sort=False):
        first = g.iloc[0]
        api_sales_qty = float(g["_api_sales_qty"].sum())
        api_amount = float(g["API주문금액"].sum())
        has_stats = bool(g["_source_stats"].sum() > 0)
        has_api = bool(g["_source_api"].sum() > 0)
        source = "판매통계+주문API" if has_stats and has_api else "판매통계" if has_stats else "주문API"
        rows.append({
            "product_id": int(_num(first.get("product_id"))),
            "상품코드": next((str(x) for x in g["상품코드"] if str(x or "").strip()), ""),
            "옵션ID": next((_oid(x) for x in g["옵션ID"] if _oid(x)), ""),
            "상품명": next((str(x) for x in g["상품명"] if str(x or "").strip()), ""),
            "판매수량": float(g["판매수량"].sum()),
            "취소·반품수량": float(g["취소·반품수량"].sum()),
            "실판매수량": float(g["실판매수량"].sum()),
            "API주문건수": float(g["API주문건수"].sum()),
            "API주문금액": api_amount,
            "평균판매가": (api_amount / api_sales_qty) if api_sales_qty > 0 else None,
            "자료기준": source,
        })
    return pd.DataFrame(rows)


def _merge_master_matches(matches: pd.DataFrame, sales: pd.DataFrame) -> pd.DataFrame:
    """Keep all master matches, even when they have no sales row."""
    if matches.empty:
        return pd.DataFrame()
    base = matches[["product_id", "상품코드", "옵션ID", "상품명"]].copy()
    if sales.empty:
        for col in ("판매수량", "취소·반품수량", "실판매수량", "API주문건수", "API주문금액"):
            base[col] = 0.0
        base["평균판매가"] = None
        base["자료기준"] = "판매자료 없음"
        return base

    detail = sales.copy()
    detail = detail[detail["product_id"] > 0].drop_duplicates(subset=["product_id"], keep="last")
    merged = base.merge(
        detail[[
            "product_id", "판매수량", "취소·반품수량", "실판매수량",
            "API주문건수", "API주문금액", "평균판매가", "자료기준",
        ]],
        how="left",
        on="product_id",
    )
    for col in ("판매수량", "취소·반품수량", "실판매수량", "API주문건수", "API주문금액"):
        merged[col] = pd.to_numeric(merged[col], errors="coerce").fillna(0)
    merged["자료기준"] = merged["자료기준"].fillna("판매자료 없음")
    return merged


def render_page(st_obj, pd_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    st_obj.session_state["_rg_sales_stats_period_active"] = False

    st_obj.markdown("## 📊 판매분석")
    st_obj.caption("상품을 ERP 전체 상품목록에서 찾은 뒤, 선택 기간의 판매수량을 판매통계와 주문 API로 확인합니다.")

    today = date.today()
    c1, c2 = st_obj.columns(2)
    start = c1.date_input("조회 시작일", value=today.replace(day=1), key="sales_analysis_start_v09186")
    end = c2.date_input("조회 종료일", value=today, key="sales_analysis_end_v09186")
    if start > end:
        st_obj.error("조회 시작일은 종료일보다 늦을 수 없습니다.")
        return

    query = st_obj.text_input(
        "상품 검색",
        placeholder="상품명, ERP 상품코드 또는 쿠팡 옵션ID 입력",
        key="sales_analysis_search_v09186",
    )

    master_df = _searchable_products(core, db)
    matches = _filter_products(master_df, query) if str(query or "").strip() else pd.DataFrame()

    stats_df, stat_covered = _sales_stats(core, db, start, end)
    api_covered = _api_coverage_db(core, db, start, end)
    api_supplement_days = api_covered - stat_covered
    api_df = _api_sales(core, db, start, end, api_supplement_days)
    sales_df = _combine_sources(stats_df, api_df)

    if str(query or "").strip():
        if matches.empty:
            st_obj.warning("ERP 상품목록에서 검색어와 일치하는 판매상품을 찾지 못했습니다.")
            return
        view = _merge_master_matches(matches, sales_df)
        st_obj.caption(f"ERP 상품목록에서 {len(matches):,}개 상품이 검색되었습니다.")
    else:
        view = sales_df.copy()

    wanted = _days(start, end)
    covered = stat_covered | api_supplement_days
    missing = wanted - covered

    sold = pd.to_numeric(view.get("판매수량", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    net = pd.to_numeric(view.get("실판매수량", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    orders = pd.to_numeric(view.get("API주문건수", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()
    amount = pd.to_numeric(view.get("API주문금액", pd.Series(dtype=float)), errors="coerce").fillna(0).sum()

    a, b, c, d = st_obj.columns(4)
    a.metric("판매수량", _fmt_qty(sold))
    b.metric("실판매수량", _fmt_qty(net))
    c.metric("API 주문건수", f"{int(round(orders)):,}건")
    d.metric("API 주문금액", _fmt_money(amount))

    if missing:
        st_obj.warning(
            f"선택한 {len(wanted)}일 중 판매자료가 확인된 날짜는 {len(covered)}일입니다. "
            f"(판매통계 {len(stat_covered)}일, 주문 API 보완 {len(api_supplement_days)}일) "
            f"나머지 {len(missing)}일은 판매 0개가 아니라 자료 미입력/미동기화일 수 있습니다."
        )
    elif covered:
        st_obj.caption(
            f"선택 기간 전체가 판매자료로 확인됩니다. 판매통계 {len(stat_covered)}일"
            + (f" + 주문 API 보완 {len(api_supplement_days)}일" if api_supplement_days else "")
            + "."
        )
    else:
        st_obj.info(
            "선택한 기간에 판매자료가 없습니다. 상품 검색은 ERP 상품목록 기준으로 동작하므로 "
            "검색된 상품은 0개 판매로 표시할 수 있습니다."
        )

    if view.empty:
        st_obj.info("선택 기간에 표시할 판매상품이 없습니다. 상품명을 입력하면 ERP 상품목록에서 직접 찾을 수 있습니다.")
        return

    view = view.sort_values(["실판매수량", "상품명"], ascending=[False, True], kind="stable").reset_index(drop=True)
    show = view.copy()
    for col in ("판매수량", "취소·반품수량", "실판매수량"):
        show[col] = show[col].map(lambda x: _fmt_qty(x))
    show["API주문건수"] = show["API주문건수"].map(lambda x: f"{int(round(_num(x))):,}건")
    show["API주문금액"] = show["API주문금액"].map(lambda x: _fmt_money(x))
    show["평균판매가"] = show["평균판매가"].map(lambda x: "-" if pd.isna(x) else _fmt_money(x))

    st_obj.dataframe(
        show[[
            "상품코드", "옵션ID", "상품명", "판매수량", "취소·반품수량", "실판매수량",
            "API주문건수", "API주문금액", "평균판매가", "자료기준",
        ]],
        use_container_width=True,
        hide_index=True,
        height=min(720, max(240, 38 * (len(show) + 1))),
    )

    chart = view.loc[view["실판매수량"] > 0, ["상품명", "실판매수량"]].head(20)
    if not chart.empty:
        st_obj.markdown("### 판매수량 상위 상품")
        st_obj.bar_chart(chart.set_index("상품명"), height=320)


def patch_source(source: str) -> str:
    if f'"{PAGE_TEXT}",' not in source:
        anchor = '"📈  잠정손익",'
        if anchor in source:
            source = source.replace(anchor, f'"{PAGE_TEXT}",\n        ' + anchor, 1)

    source = source.replace(f'elif page == "{PAGE_TEXT}":', 'elif "판매분석" in str(page):')
    marker = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
    if marker in source and 'elif "판매분석" in str(page):' not in source:
        block = (
            '# ------------------------------\n'
            '# Sales analysis\n'
            '# ------------------------------\n'
            'elif "판매분석" in str(page):\n'
            '    sales_analysis_v09186.render_page(st, pd, core)\n\n\n'
        )
        source = source.replace(marker, block + marker, 1)
    return source
