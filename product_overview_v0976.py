"""RG Manager v0.9.76 single-product integrated overview.

Shows one finished product's sales/return/inventory/BOM/ad/profit history in one page.
All calculations are read-only and reuse existing ERP data. No inventory/P&L data is mutated.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
import json
import math
import re
from typing import Any

import pandas as pd


PAGE_LABEL = "📊  상품 통합현황"


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


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _fmt_money(v: Any) -> str:
    return f"{int(round(_num(v))):,}원"


def _fmt_qty(v: Any) -> str:
    n = _num(v)
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}개"
    return f"{n:,.2f}".rstrip("0").rstrip(".") + "개"


def _fmt_pct(v: Any) -> str:
    return f"{_num(v):,.1f}%"


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    safe = str(table).replace('"', '""')
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{safe}")').fetchall()}


def _pick(cols: set[str], candidates: tuple[str, ...]) -> str | None:
    low = {str(x).lower(): str(x) for x in cols}
    for cand in candidates:
        if cand in cols:
            return cand
        hit = low.get(cand.lower())
        if hit:
            return hit
    return None


def _period_bounds(label: str):
    today = date.today()
    if label == "이번 달":
        return today.replace(day=1), today
    if label == "지난 달":
        first = today.replace(day=1)
        end = first - timedelta(days=1)
        return end.replace(day=1), end
    if label == "최근 30일":
        return today - timedelta(days=29), today
    if label == "최근 90일":
        return today - timedelta(days=89), today
    return None, None


def _overlap(start_text: Any, end_text: Any, start: date | None, end: date | None) -> bool:
    if start is None or end is None:
        return True
    try:
        a = date.fromisoformat(str(start_text)[:10])
        b = date.fromisoformat(str(end_text or start_text)[:10])
    except Exception:
        return True
    return b >= start and a <= end


def _finished_products(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        pc = _cols(c, "products")
        need = {"id", "name"}
        if not need.issubset(pc):
            return pd.DataFrame(columns=["id", "item_code", "option_id", "name", "active"])
        item_code = "item_code" if "item_code" in pc else "''"
        option_id = "option_id" if "option_id" in pc else "''"
        active = "active" if "active" in pc else "1"
        unit_cost = "unit_cost" if "unit_cost" in pc else "0"
        where = "WHERE item_type='finished'" if "item_type" in pc else ""
        return pd.read_sql_query(
            f"""SELECT id,{item_code} item_code,{option_id} option_id,name,
                       {active} active,{unit_cost} unit_cost
                FROM products {where}
                ORDER BY CASE WHEN COALESCE({active},1)=1 THEN 0 ELSE 1 END,name,item_code""",
            c,
        )


def _inventory(core, db, product_id: int):
    with core._conn(db) as c:
        if not (_exists(c, "inventory_txns") and _exists(c, "warehouses")):
            return pd.DataFrame(columns=["창고", "현재고"])
        tc = _cols(c, "inventory_txns")
        wc = _cols(c, "warehouses")
        if not {"product_id", "warehouse_id", "qty_delta"}.issubset(tc) or not {"id", "name"}.issubset(wc):
            return pd.DataFrame(columns=["창고", "현재고"])
        df = pd.read_sql_query(
            """SELECT w.name AS 창고,COALESCE(SUM(t.qty_delta),0) AS 현재고
               FROM warehouses w
               LEFT JOIN inventory_txns t
                 ON t.warehouse_id=w.id AND t.product_id=?
               GROUP BY w.id,w.name
               ORDER BY CASE w.name
                   WHEN '쿠팡RG' THEN 1
                   WHEN '반품창고' THEN 2
                   WHEN '자체창고' THEN 3
                   WHEN '불량·폐기' THEN 4
                   ELSE 9 END,w.name""",
            c,
            params=(int(product_id),),
        )
    if not df.empty:
        df["현재고"] = pd.to_numeric(df["현재고"], errors="coerce").fillna(0.0)
    return df


def _bom(core, db, product_id: int):
    with core._conn(db) as c:
        if not _exists(c, "bom_items"):
            return pd.DataFrame(), None
        bc = _cols(c, "bom_items")
        if not {"parent_product_id", "component_product_id", "qty_per"}.issubset(bc):
            return pd.DataFrame(), None
        has_inv = _exists(c, "inventory_txns") and _exists(c, "warehouses")
        if has_inv:
            sql = """
                SELECT b.component_product_id,p.item_code,p.name,b.qty_per,
                       COALESCE(SUM(CASE WHEN w.name='자체창고' THEN t.qty_delta ELSE 0 END),0) own_stock
                FROM bom_items b
                JOIN products p ON p.id=b.component_product_id
                LEFT JOIN inventory_txns t ON t.product_id=p.id
                LEFT JOIN warehouses w ON w.id=t.warehouse_id
                WHERE b.parent_product_id=?
                GROUP BY b.component_product_id,p.item_code,p.name,b.qty_per
                ORDER BY p.name,p.item_code
            """
        else:
            sql = """
                SELECT b.component_product_id,p.item_code,p.name,b.qty_per,0 own_stock
                FROM bom_items b
                JOIN products p ON p.id=b.component_product_id
                WHERE b.parent_product_id=?
                ORDER BY p.name,p.item_code
            """
        df = pd.read_sql_query(sql, c, params=(int(product_id),))
    if df.empty:
        return df, None
    df["qty_per"] = pd.to_numeric(df["qty_per"], errors="coerce").fillna(0.0)
    df["own_stock"] = pd.to_numeric(df["own_stock"], errors="coerce").fillna(0.0)
    possible = []
    for r in df.itertuples(index=False):
        q = float(r.qty_per)
        stock = float(r.own_stock)
        possible.append(max(0, math.floor(stock / q))) if q > 0 else possible.append(0)
    df["possible"] = possible
    max_make = min(possible) if possible else None
    if max_make is not None:
        df["bottleneck"] = df["possible"].eq(max_make)
    return df, max_make


def _sales_history(core, db, product_id: int, start: date | None, end: date | None):
    with core._conn(db) as c:
        if not _exists(c, "sales_stats"):
            return pd.DataFrame(), {"label": "반품률", "available": False}
        sc = _cols(c, "sales_stats")
        if "product_id" not in sc:
            return pd.DataFrame(), {"label": "반품률", "available": False}
        raw = pd.read_sql_query("SELECT * FROM sales_stats WHERE product_id=?", c, params=(int(product_id),))
        imports = pd.DataFrame()
        if _exists(c, "imports"):
            ic = _cols(c, "imports")
            wanted = [x for x in ("id", "file_name", "period_start", "period_end", "data_type") if x in ic]
            if "id" in wanted:
                imports = pd.read_sql_query(
                    "SELECT " + ",".join(wanted) + " FROM imports",
                    c,
                )
    if raw.empty:
        return pd.DataFrame(), {"label": "반품률", "available": True}

    net_col = _pick(sc, ("net_qty", "net_sales_qty", "순판매수량"))
    gross_col = _pick(sc, ("sales_qty", "sold_qty", "gross_qty", "gross_sales_qty", "order_qty", "판매수량", "주문수량"))
    return_col = _pick(sc, ("return_qty", "returned_qty", "returns_qty", "refund_qty", "refunded_qty", "반품수량", "환불수량"))
    cancel_col = _pick(sc, ("cancel_qty", "cancelled_qty", "canceled_qty", "cancel_count", "취소수량", "취소건수"))

    group_col = "import_id" if "import_id" in raw.columns else None
    groups = raw.groupby(group_col, dropna=False) if group_col else [(None, raw)]
    imp_map = {}
    if not imports.empty and "id" in imports.columns:
        imp_map = {int(r.id): r for r in imports.itertuples(index=False) if pd.notna(r.id)}

    out = []
    for gid, g in groups:
        net = float(pd.to_numeric(g[net_col], errors="coerce").fillna(0).sum()) if net_col else 0.0
        ret_exact = float(pd.to_numeric(g[return_col], errors="coerce").fillna(0).abs().sum()) if return_col else 0.0
        cancel = float(pd.to_numeric(g[cancel_col], errors="coerce").fillna(0).abs().sum()) if cancel_col else 0.0
        if gross_col:
            gross = float(pd.to_numeric(g[gross_col], errors="coerce").fillna(0).sum())
        else:
            gross = max(0.0, net + (ret_exact if return_col else cancel))
        signal = ret_exact if return_col else cancel if cancel_col else max(gross - net, 0.0)

        imp = None
        if group_col and pd.notna(gid):
            try:
                imp = imp_map.get(int(gid))
            except Exception:
                imp = None
        ps = str(getattr(imp, "period_start", "") or "") if imp is not None else ""
        pe = str(getattr(imp, "period_end", "") or "") if imp is not None else ""
        fn = str(getattr(imp, "file_name", "") or "") if imp is not None else ""
        if not _overlap(ps, pe, start, end):
            continue
        out.append(
            {
                "기간시작": ps,
                "기간종료": pe,
                "파일": fn,
                "판매수량": gross,
                "취소수량": cancel,
                "반품수량": ret_exact,
                "반품신호": signal,
                "순판매수량": net,
                "반품률": (signal / gross * 100) if gross > 0 else 0.0,
            }
        )
    df = pd.DataFrame(out)
    if not df.empty:
        df = df.sort_values(["기간시작", "기간종료"], ascending=[False, False], kind="stable")
    label = "반품률" if return_col else "취소·반품률" if cancel_col else "판매-순판매 차감률"
    return df, {"label": label, "available": True, "exact_return": bool(return_col)}


def _return_txns(core, db, product_id: int, start: date | None, end: date | None):
    with core._conn(db) as c:
        if not (_exists(c, "inventory_txns") and _exists(c, "warehouses")):
            return pd.DataFrame()
        tc = _cols(c, "inventory_txns")
        need = {"product_id", "warehouse_id", "qty_delta"}
        if not need.issubset(tc):
            return pd.DataFrame()
        fields = ["t.qty_delta"]
        for col in ("txn_date", "txn_type", "ref_no", "memo", "note"):
            if col in tc:
                fields.append(f"t.{col}")
        sql = (
            "SELECT " + ",".join(fields) +
            " FROM inventory_txns t JOIN warehouses w ON w.id=t.warehouse_id "
            "WHERE t.product_id=? AND w.name='반품창고' AND t.qty_delta>0 "
        )
        params: list[Any] = [int(product_id)]
        if "txn_type" in tc:
            sql += "AND COALESCE(t.txn_type,'') LIKE '%반품%' "
        if "txn_date" in tc and start is not None and end is not None:
            sql += "AND COALESCE(t.txn_date,'')>=? AND COALESCE(t.txn_date,'')<=? "
            params.extend([start.isoformat(), end.isoformat()])
        sql += "ORDER BY " + ("COALESCE(t.txn_date,'') DESC," if "txn_date" in tc else "") + "t.rowid DESC"
        return pd.read_sql_query(sql, c, params=tuple(params))


def _ad_history(core, db, option_id: str, start: date | None, end: date | None):
    with core._conn(db) as c:
        if not (_exists(c, "provisional_ad_report_imports") and _exists(c, "provisional_ad_report_items")):
            return pd.DataFrame()
        df = pd.read_sql_query(
            """SELECT i.period_start,i.period_end,i.file_name,x.option_id,x.product_name,x.ad_spend
               FROM provisional_ad_report_items x
               JOIN provisional_ad_report_imports i ON i.id=x.import_id
               ORDER BY i.period_start DESC,i.period_end DESC,i.id DESC""",
            c,
        )
    if df.empty:
        return df
    target = _oid(option_id)
    df = df[df["option_id"].map(_oid) == target].copy()
    if start is not None and end is not None and not df.empty:
        mask = df.apply(lambda r: _overlap(r.get("period_start"), r.get("period_end"), start, end), axis=1)
        df = df.loc[mask].copy()
    if "ad_spend" in df.columns:
        df["ad_spend"] = pd.to_numeric(df["ad_spend"], errors="coerce").fillna(0.0)
    return df


def _provisional_history(core, db, option_id: str, start: date | None, end: date | None):
    with core._conn(db) as c:
        if not _exists(c, "provisional_pnl_snapshots"):
            return pd.DataFrame()
        rows = c.execute(
            """SELECT import_id,file_name,period_start,period_end,captured_at,rows_json
               FROM provisional_pnl_snapshots
               ORDER BY period_start DESC,period_end DESC,import_id DESC"""
        ).fetchall()
    target = _oid(option_id)
    out = []
    for s in rows:
        if not _overlap(s["period_start"], s["period_end"], start, end):
            continue
        try:
            items = json.loads(str(s["rows_json"] or "[]"))
        except Exception:
            continue
        agg = {
            "판매수량": 0.0,
            "예상매출": 0.0,
            "매출원가": 0.0,
            "판매수수료": 0.0,
            "입출고비": 0.0,
            "배송비": 0.0,
            "반품충당": 0.0,
            "광고비": 0.0,
            "예상이익": 0.0,
        }
        found = False
        for r in items:
            if _oid(r.get("옵션ID")) != target:
                continue
            found = True
            for k in agg:
                agg[k] += _num(r.get(k))
        if found:
            profit = agg["예상이익"]
            revenue = agg["예상매출"]
            out.append(
                {
                    "기간시작": str(s["period_start"] or ""),
                    "기간종료": str(s["period_end"] or ""),
                    "파일": str(s["file_name"] or ""),
                    **agg,
                    "이익률": (profit / revenue * 100) if revenue else 0.0,
                }
            )
    return pd.DataFrame(out)


def _month_overlap(month: str, start: date | None, end: date | None) -> bool:
    if start is None or end is None:
        return True
    try:
        y, m = [int(x) for x in month.split("-")]
        a = date(y, m, 1)
        b = date(y, m, calendar.monthrange(y, m)[1])
    except Exception:
        return True
    return b >= start and a <= end


def _confirmed_history(core, db, product_id: int, option_id: str, start: date | None, end: date | None):
    try:
        months = list(core.monthly_available() or [])
    except Exception:
        months = []
    target = _oid(option_id)
    out = []
    for month in months:
        month = str(month)
        if not _month_overlap(month, start, end):
            continue
        try:
            mdf, _meta = core.confirmed_monthly_pnl(month)
        except Exception:
            continue
        if mdf is None or mdf.empty:
            continue
        mask = pd.Series(False, index=mdf.index)
        if "product_id" in mdf.columns:
            mask = mask | (pd.to_numeric(mdf["product_id"], errors="coerce").fillna(-1).astype(int) == int(product_id))
        for col in ("option_id", "옵션ID", "쿠팡 옵션ID"):
            if col in mdf.columns:
                mask = mask | mdf[col].map(_oid).eq(target)
        sub = mdf.loc[mask].copy()
        if sub.empty:
            continue

        def s(col, absolute=True):
            if col not in sub.columns:
                return 0.0
            vals = pd.to_numeric(sub[col], errors="coerce").fillna(0.0)
            return float(vals.abs().sum()) if absolute else float(vals.sum())

        revenue = s("realized_sales", absolute=False)
        cogs = s("cogs")
        comm = s("commission")
        inout = s("inout")
        delivery = s("delivery")
        returns = s("return_pickup") + s("return_restock")
        ad_known = "ad_cost" in sub.columns
        ad = s("ad_cost") if ad_known else 0.0
        pre_ad_profit = revenue - cogs - comm - inout - delivery - returns
        profit = pre_ad_profit - ad
        out.append(
            {
                "월": month,
                "실현매출": revenue,
                "상품원가": cogs,
                "판매수수료": comm,
                "입출고·배송비": inout + delivery,
                "반품비": returns,
                "광고비": ad,
                "광고비귀속": ad_known,
                "광고전이익": pre_ad_profit,
                "확정이익": profit if ad_known else pre_ad_profit,
                "이익률": (profit / revenue * 100) if ad_known and revenue else ((pre_ad_profit / revenue * 100) if revenue else 0.0),
            }
        )
    return pd.DataFrame(out)


def _covered_days(sales: pd.DataFrame, start: date | None, end: date | None) -> int:
    if start is not None and end is not None:
        return max(1, (end - start).days + 1)
    dates = []
    if sales is not None and not sales.empty:
        for col in ("기간시작", "기간종료"):
            for v in sales[col].tolist():
                try:
                    dates.append(date.fromisoformat(str(v)[:10]))
                except Exception:
                    pass
    if len(dates) >= 2:
        return max(1, (max(dates) - min(dates)).days + 1)
    return 1


def _product_label(row) -> str:
    code = str(row.item_code or "").strip()
    oid = _oid(row.option_id)
    if re.fullmatch(r"CP-\d+", code, flags=re.IGNORECASE):
        code = oid or code[3:]
    bits = [str(row.name or "").strip()]
    if oid:
        bits.append(f"옵션ID {oid}")
    elif code:
        bits.append(code)
    if int(_num(row.active)) != 1:
        bits.append("보관")
    return " · ".join(x for x in bits if x)


def render_page(st, pd_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    st.markdown("## 📊 상품 통합현황")
    st.caption("완제품 하나를 선택하면 판매·반품·재고·BOM·광고·손익을 한 화면에서 확인합니다.")

    products = _finished_products(core, db)
    if products.empty:
        st.info("조회할 완제품이 없습니다.")
        return

    q = st.text_input(
        "완제품 검색",
        placeholder="상품명 또는 쿠팡 옵션ID 입력",
        key="product_overview_search_v0976",
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
        mask = pd.Series(True, index=filtered.index)
        for term in terms:
            mask &= hay.str.contains(term, regex=False, na=False)
        filtered = filtered.loc[mask].copy()

    if filtered.empty:
        st.warning("검색 조건에 맞는 완제품이 없습니다.")
        return

    ids = filtered["id"].astype(int).tolist()
    labels = {int(r.id): _product_label(r) for r in filtered.itertuples(index=False)}
    selected_id = st.selectbox(
        "완제품 선택",
        ids,
        format_func=lambda x: labels.get(int(x), str(x)),
        key="product_overview_product_v0976",
    )
    row = products[products["id"].astype(int) == int(selected_id)].iloc[0]
    option_id = _oid(row.get("option_id")) or _oid(row.get("item_code"))

    period = st.selectbox(
        "조회기간",
        ["최근 30일", "이번 달", "지난 달", "최근 90일", "전체"],
        index=0,
        key="product_overview_period_v0976",
    )
    start, end = _period_bounds(period)

    inv = _inventory(core, db, int(selected_id))
    bom, max_make = _bom(core, db, int(selected_id))
    sales, sales_meta = _sales_history(core, db, int(selected_id), start, end)
    returns = _return_txns(core, db, int(selected_id), start, end)
    ads = _ad_history(core, db, option_id, start, end)
    prov = _provisional_history(core, db, option_id, start, end)
    confirmed = _confirmed_history(core, db, int(selected_id), option_id, start, end)

    rg_stock = 0.0
    return_stock = 0.0
    if not inv.empty:
        rg_stock = float(inv.loc[inv["창고"].eq("쿠팡RG"), "현재고"].sum())
        return_stock = float(inv.loc[inv["창고"].eq("반품창고"), "현재고"].sum())

    gross = float(sales["판매수량"].sum()) if not sales.empty else 0.0
    net = float(sales["순판매수량"].sum()) if not sales.empty else 0.0
    ret_signal = float(sales["반품신호"].sum()) if not sales.empty else 0.0
    return_rate = (ret_signal / gross * 100) if gross > 0 else 0.0
    ad_total = float(pd.to_numeric(ads["ad_spend"], errors="coerce").fillna(0).sum()) if not ads.empty else 0.0

    prov_revenue = float(pd.to_numeric(prov["예상매출"], errors="coerce").fillna(0).sum()) if not prov.empty else None
    prov_profit = float(pd.to_numeric(prov["예상이익"], errors="coerce").fillna(0).sum()) if not prov.empty else None

    conf_revenue = float(pd.to_numeric(confirmed["실현매출"], errors="coerce").fillna(0).sum()) if not confirmed.empty else None
    conf_profit = float(pd.to_numeric(confirmed["확정이익"], errors="coerce").fillna(0).sum()) if not confirmed.empty else None
    all_ad_known = bool(not confirmed.empty and confirmed["광고비귀속"].all())

    revenue_value = prov_revenue if prov_revenue is not None else conf_revenue
    profit_value = prov_profit if prov_profit is not None else conf_profit
    revenue_label = "예상매출" if prov_revenue is not None else "확정매출"
    profit_label = "예상이익" if prov_profit is not None else ("확정이익" if all_ad_known else "광고전이익")

    days = _covered_days(sales, start, end)
    daily_net = max(net, 0.0) / days if days > 0 else 0.0
    stock_days = (max(rg_stock, 0.0) / daily_net) if daily_net > 0 else None

    st.markdown(f"### {str(row.get('name') or '')}")
    code = str(row.get("item_code") or "")
    st.caption(
        f"쿠팡 옵션ID {option_id or '-'} · 품목코드 {code or '-'}"
        + (" · 보관상품" if int(_num(row.get("active"))) != 1 else "")
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("판매수량", _fmt_qty(gross))
    c2.metric(revenue_label, _fmt_money(revenue_value) if revenue_value is not None else "-")
    c3.metric("쿠팡RG 재고", _fmt_qty(rg_stock))
    c4.metric("현재 생산가능", _fmt_qty(max_make) if max_make is not None else "BOM 없음")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(sales_meta.get("label", "반품률"), _fmt_pct(return_rate))
    c2.metric("반품창고 재고", _fmt_qty(return_stock))
    c3.metric("광고비", _fmt_money(ad_total))
    c4.metric(profit_label, _fmt_money(profit_value) if profit_value is not None else "-")

    status = []
    if stock_days is not None:
        if stock_days < 7:
            status.append(f"🔴 RG재고 약 {stock_days:.1f}일분")
        elif stock_days < 14:
            status.append(f"🟠 RG재고 약 {stock_days:.1f}일분")
        else:
            status.append(f"🟢 RG재고 약 {stock_days:.1f}일분")
    else:
        status.append("판매속도 기준 재고일수 계산 없음")
    if max_make is not None:
        status.append(f"기초재고로 {int(max_make):,}개 생산 가능")
    status.append(f"{sales_meta.get('label','반품률')} {_fmt_pct(return_rate)}")
    status.append(f"광고비 {_fmt_money(ad_total)}")
    st.info(" · ".join(status))

    tab1, tab2, tab3, tab4 = st.tabs(["판매·손익", "재고·BOM", "반품", "광고"])

    with tab1:
        st.markdown("### 판매 이력")
        if sales.empty:
            st.caption("선택 기간의 판매자료가 없습니다.")
        else:
            sview = sales[["기간시작", "기간종료", "판매수량", "취소수량", "순판매수량", "반품률"]].copy()
            sview.columns = ["시작일", "종료일", "판매수량", "취소수량", "순판매수량", sales_meta.get("label", "반품률")]
            for col in ("판매수량", "취소수량", "순판매수량"):
                sview[col] = sview[col].map(_fmt_qty)
            sview[sales_meta.get("label", "반품률")] = sview[sales_meta.get("label", "반품률")].map(_fmt_pct)
            st.dataframe(sview, use_container_width=True, hide_index=True)

        st.markdown("### 잠정 매출·이익 이력")
        if prov.empty:
            st.caption("선택 기간에 저장된 잠정손익 스냅샷이 없습니다.")
        else:
            pview = prov[["기간시작", "기간종료", "판매수량", "예상매출", "광고비", "예상이익", "이익률"]].copy()
            pview.columns = ["시작일", "종료일", "판매수량", "예상매출", "광고비", "예상이익", "이익률"]
            pview["판매수량"] = pview["판매수량"].map(_fmt_qty)
            for col in ("예상매출", "광고비", "예상이익"):
                pview[col] = pview[col].map(_fmt_money)
            pview["이익률"] = pview["이익률"].map(_fmt_pct)
            st.dataframe(pview, use_container_width=True, hide_index=True)

        st.markdown("### 월별 확정 매출·이익")
        if confirmed.empty:
            st.caption("선택 기간에 월 확정자료가 없습니다.")
        else:
            cview = confirmed[["월", "실현매출", "상품원가", "판매수수료", "입출고·배송비", "반품비", "광고비", "확정이익", "이익률", "광고비귀속"]].copy()
            for col in ("실현매출", "상품원가", "판매수수료", "입출고·배송비", "반품비", "광고비", "확정이익"):
                cview[col] = cview[col].map(_fmt_money)
            cview["이익률"] = cview["이익률"].map(_fmt_pct)
            cview["광고비귀속"] = cview["광고비귀속"].map(lambda x: "상품귀속" if bool(x) else "미귀속")
            st.dataframe(cview, use_container_width=True, hide_index=True)
            if not bool(confirmed["광고비귀속"].all()):
                st.caption("월 정산서에서 상품별 광고비가 직접 귀속되지 않은 월은 확정이익 열이 광고전이익 기준입니다.")

    with tab2:
        st.markdown("### 현재 창고별 재고")
        if inv.empty:
            st.caption("재고자료가 없습니다.")
        else:
            iview = inv.copy()
            iview["현재고"] = iview["현재고"].map(_fmt_qty)
            st.dataframe(iview, use_container_width=True, hide_index=True)

        st.markdown("### 생산에 필요한 기초재고")
        if bom.empty:
            st.warning("등록된 BOM이 없어 생산가능수량을 계산할 수 없습니다.")
        else:
            bview = bom[["item_code", "name", "qty_per", "own_stock", "possible", "bottleneck"]].copy()
            bview.columns = ["품목코드", "구성품", "완제품 1개당 필요수량", "자체창고 재고", "생산가능수량", "병목"]
            bview["완제품 1개당 필요수량"] = bview["완제품 1개당 필요수량"].map(_fmt_qty)
            bview["자체창고 재고"] = bview["자체창고 재고"].map(_fmt_qty)
            bview["생산가능수량"] = bview["생산가능수량"].map(_fmt_qty)
            bview["병목"] = bview["병목"].map(lambda x: "⚠️" if bool(x) else "")
            st.dataframe(bview, use_container_width=True, hide_index=True)
            st.success(f"현재 기초재고 기준 최대 **{int(max_make or 0):,}개** 생산 가능합니다.")

    with tab3:
        st.markdown("### 반품창고 입고 이력")
        if returns.empty:
            st.caption("선택 기간의 반품창고 입고 이력이 없습니다.")
        else:
            rview = returns.copy()
            rename = {"txn_date": "일자", "qty_delta": "수량", "txn_type": "구분", "ref_no": "참조번호", "memo": "메모", "note": "메모"}
            rview = rview.rename(columns=rename)
            if "수량" in rview.columns:
                rview["수량"] = rview["수량"].map(_fmt_qty)
            if list(rview.columns).count("메모") > 1:
                rview = rview.loc[:, ~rview.columns.duplicated()]
            st.dataframe(rview, use_container_width=True, hide_index=True)

        st.markdown("### 판매자료 기준 반품·취소 이력")
        if sales.empty:
            st.caption("선택 기간의 판매자료가 없습니다.")
        else:
            cols = ["기간시작", "기간종료", "판매수량", "반품신호", "반품률"]
            rsum = sales[cols].copy()
            rsum.columns = ["시작일", "종료일", "판매수량", "반품·취소수량", sales_meta.get("label", "반품률")]
            rsum["판매수량"] = rsum["판매수량"].map(_fmt_qty)
            rsum["반품·취소수량"] = rsum["반품·취소수량"].map(_fmt_qty)
            rsum[sales_meta.get("label", "반품률")] = rsum[sales_meta.get("label", "반품률")].map(_fmt_pct)
            st.dataframe(rsum, use_container_width=True, hide_index=True)
            if not sales_meta.get("exact_return"):
                st.caption("판매통계에 별도 반품수량이 없는 경우 취소수량 또는 판매-순판매 차이를 반품 신호로 표시합니다.")

    with tab4:
        st.markdown("### 광고비 사용내역")
        if ads.empty:
            st.caption("선택 기간의 광고성과보고서에서 이 상품 광고비를 찾지 못했습니다.")
        else:
            aview = ads[["period_start", "period_end", "ad_spend", "file_name"]].copy()
            aview.columns = ["시작일", "종료일", "광고비", "자료"]
            aview["광고비"] = aview["광고비"].map(_fmt_money)
            st.dataframe(aview, use_container_width=True, hide_index=True)
            chart = ads.groupby("period_start", as_index=True)["ad_spend"].sum().sort_index()
            if len(chart) >= 2:
                st.bar_chart(chart, height=260)


def apply_sidebar(sidebar_module):
    """Place the page under the existing 매입·상품 group without changing sidebar code."""
    try:
        groups = getattr(sidebar_module, "_GROUPS", [])
        for title, items in groups:
            if str(title) == "🛒 매입·상품":
                if PAGE_LABEL not in items:
                    items.insert(0, PAGE_LABEL)
                return sidebar_module
    except Exception:
        pass
    return sidebar_module


def patch_source(source: str) -> str:
    if PAGE_LABEL not in source:
        menu_pat = re.compile(r'(?m)^(\s*)"🏷️  상품·원가",\s*$')
        m = menu_pat.search(source)
        if not m:
            raise RuntimeError("v0.9.76 상품 통합현황 메뉴를 추가할 위치를 찾지 못했습니다.")
        indent = m.group(1)
        line_end = "\r\n" if "\r\n" in m.group(0) else "\n"
        replacement = m.group(0) + line_end + indent + f'"{PAGE_LABEL}",'
        source = source[:m.start()] + replacement + source[m.end():]

    handler = f'elif page == "{PAGE_LABEL}":'
    if handler not in source:
        anchor = 'elif page == "📦  재고관리":'
        pos = source.find(anchor)
        if pos < 0:
            raise RuntimeError("v0.9.76 상품 통합현황 화면을 추가할 위치를 찾지 못했습니다.")
        block = (
            f'{handler}\n'
            '    pnl_month_default_v0915.render_product_overview_page(st, pd, core)\n\n\n'
        )
        source = source[:pos] + block + source[pos:]
    return source
