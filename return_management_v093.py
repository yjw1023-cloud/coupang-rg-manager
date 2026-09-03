"""RG Manager v0.9.3 return management UI.

Purpose
- Show current return-warehouse stock exactly from inventory transactions.
- Show cumulative return/cancel quantity and rate only when sales_stats keeps a
  trustworthy gross-sales + cancel/return quantity signal.
- Show return pickup/restock costs from confirmed monthly P&L when available.
- Never infer customer returns from fee-row counts.
"""
from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _fmt_qty(value: Any) -> str:
    n = _num(value)
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}개"
    return f"{n:,.2f}".rstrip("0").rstrip(".") + "개"


def _fmt_money(value: Any) -> str:
    return f"{int(round(_num(value))):,}원"


def _fmt_pct(value: Any) -> str:
    return f"{_num(value):,.1f}%"


def _display_code(item_code: Any, option_id: Any = None) -> str:
    code = "" if item_code is None else str(item_code).strip()
    if re.fullmatch(r"CP-\d+", code):
        return str(option_id or code[3:])
    return code


def _schema(core_module, db_path):
    core_module.init_db(db_path)
    with core_module._conn(db_path) as c:
        tables = {
            str(r["name"]): {
                str(x["name"]) for x in c.execute(f'PRAGMA table_info("{str(r["name"]).replace(chr(34), chr(34)*2)}")').fetchall()
            }
            for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    return tables


def _pick(cols: set[str], candidates: tuple[str, ...]) -> str | None:
    lower = {str(c).lower(): str(c) for c in cols}
    for cand in candidates:
        if cand in cols:
            return cand
        hit = lower.get(cand.lower())
        if hit:
            return hit
    return None


def _product_master(pd_obj, core_module, db_path):
    with core_module._conn(db_path) as c:
        return pd_obj.read_sql_query(
            """
            SELECT p.id,p.item_code,p.option_id,p.name,p.active,
                   COALESCE(SUM(CASE WHEN w.name='반품창고' THEN t.qty_delta ELSE 0 END),0) AS return_stock,
                   COALESCE(SUM(CASE
                       WHEN w.name='반품창고' AND t.qty_delta>0
                            AND COALESCE(t.txn_type,'') LIKE '%반품%'
                            AND COALESCE(t.txn_type,'') <> '기존ERP현재고이관'
                       THEN t.qty_delta ELSE 0 END),0) AS return_inbound
            FROM products p
            LEFT JOIN inventory_txns t ON t.product_id=p.id
            LEFT JOIN warehouses w ON w.id=t.warehouse_id
            GROUP BY p.id,p.item_code,p.option_id,p.name,p.active
            ORDER BY p.name,p.item_code
            """,
            c,
        )


def _sales_signal(pd_obj, core_module, db_path, start_iso: str | None, end_iso: str | None):
    """Return per-product sales/return signal plus source metadata.

    Preferred fields are explicit return/cancel quantities. If only gross + net are
    present, the difference is used. If only net exists, return quantity is unknown.
    """
    tables = _schema(core_module, db_path)
    api_tables = {
        "coupang_rg_order_items", "coupang_return_items",
        "coupang_return_withdrawals", "coupang_api_sync_runs",
    }
    if api_tables.issubset(tables):
        try:
            import coupang_api_sync_v09140 as coupang_api

            with core_module._conn(db_path) as c:
                run_where = ["sync_type='returns'", "status='success'"]
                run_params: list[Any] = []
                if start_iso:
                    run_where.append("period_end>=?")
                    run_params.append(start_iso)
                if end_iso:
                    run_where.append("period_start<=?")
                    run_params.append(end_iso)
                return_synced = int(c.execute(
                    "SELECT COUNT(*) n FROM coupang_api_sync_runs WHERE " + " AND ".join(run_where),
                    tuple(run_params),
                ).fetchone()["n"]) > 0
                if return_synced:
                    order_where = ["product_id IS NOT NULL"]
                    order_params: list[Any] = []
                    if start_iso:
                        order_where.append("paid_date>=?")
                        order_params.append(start_iso)
                    if end_iso:
                        order_where.append("paid_date<=?")
                        order_params.append(end_iso)
                    order_rows = c.execute(
                        """SELECT product_id,SUM(ABS(sales_quantity)) gross_qty
                           FROM coupang_rg_order_items WHERE """
                        + " AND ".join(order_where)
                        + " GROUP BY product_id",
                        tuple(order_params),
                    ).fetchall()
                    events = coupang_api._matched_return_events(c, start_iso, end_iso)
                    combined: dict[int, dict[str, float]] = {}
                    for row in order_rows:
                        combined[int(row["product_id"])] = {
                            "gross_qty": _num(row["gross_qty"]),
                            "return_qty": 0.0,
                            "withdrawal_qty": 0.0,
                        }
                    for product_id, event in events.items():
                        target = combined.setdefault(int(product_id), {
                            "gross_qty": 0.0,
                            "return_qty": 0.0,
                            "withdrawal_qty": 0.0,
                        })
                        target["return_qty"] = _num(event.get("return_qty"))
                        target["withdrawal_qty"] = _num(event.get("withdrawal_qty"))
                    api_rows = []
                    for product_id, values in combined.items():
                        gross = _num(values["gross_qty"])
                        returns = _num(values["return_qty"])
                        withdrawals = _num(values["withdrawal_qty"])
                        api_rows.append({
                            "product_id": product_id,
                            "gross_qty": gross,
                            "return_qty": returns,
                            "withdrawal_qty": withdrawals,
                            "net_qty": gross - returns + withdrawals,
                            "return_rate": returns / gross * 100 if gross else 0.0,
                        })
                    return pd_obj.DataFrame(api_rows), {
                        "available": True,
                        "label": "반품·취소수량",
                        "period_filter": True,
                        "exact_return": True,
                        "source": "coupang_return_api",
                    }
        except Exception:
            # A legacy DB remains readable even if the API tables are incomplete.
            pass

    cols = tables.get("sales_stats", set())
    if not cols or "product_id" not in cols:
        return pd_obj.DataFrame(), {
            "available": False,
            "reason": "sales_stats 테이블 또는 product_id가 없습니다.",
            "label": "반품수량",
        }

    net_col = _pick(cols, ("net_qty", "net_sales_qty", "순판매수량"))
    gross_col = _pick(cols, (
        "sales_qty", "sold_qty", "gross_qty", "gross_sales_qty", "order_qty", "판매수량", "주문수량"
    ))
    return_col = _pick(cols, (
        "return_qty", "returned_qty", "returns_qty", "refund_qty", "refunded_qty", "반품수량", "환불수량"
    ))
    cancel_col = _pick(cols, (
        "cancel_qty", "cancelled_qty", "canceled_qty", "cancel_count", "취소수량", "취소건수"
    ))
    signal_col = return_col or cancel_col

    if signal_col is None and not (gross_col and net_col):
        return pd_obj.DataFrame(), {
            "available": False,
            "reason": "판매통계에 반품/취소수량 또는 판매수량-순판매수량 조합이 없습니다.",
            "label": "반품수량",
            "net_col": net_col,
            "gross_col": gross_col,
        }

    imports_cols = tables.get("imports", set())
    can_period = "import_id" in cols and {"id", "period_start", "period_end"}.issubset(imports_cols)

    select_parts = ["s.product_id"]
    if gross_col:
        select_parts.append(f'SUM(COALESCE(s."{gross_col}",0)) AS gross_qty')
    elif net_col and signal_col:
        select_parts.append(f'SUM(COALESCE(s."{net_col}",0) + ABS(COALESCE(s."{signal_col}",0))) AS gross_qty')
    else:
        select_parts.append("0 AS gross_qty")

    if signal_col:
        select_parts.append(f'SUM(ABS(COALESCE(s."{signal_col}",0))) AS return_qty')
    else:
        select_parts.append(f'SUM(MAX(COALESCE(s."{gross_col}",0) - COALESCE(s."{net_col}",0),0)) AS return_qty')

    if net_col:
        select_parts.append(f'SUM(COALESCE(s."{net_col}",0)) AS net_qty')
    else:
        select_parts.append("0 AS net_qty")

    sql = "SELECT " + ",".join(select_parts) + " FROM sales_stats s"
    params: list[Any] = []
    if can_period:
        sql += " JOIN imports i ON i.id=s.import_id"
        where = []
        if start_iso:
            where.append("COALESCE(i.period_end,i.period_start) >= ?")
            params.append(start_iso)
        if end_iso:
            where.append("COALESCE(i.period_start,i.period_end) <= ?")
            params.append(end_iso)
        if where:
            sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY s.product_id"

    with core_module._conn(db_path) as c:
        df = pd_obj.read_sql_query(sql, c, params=tuple(params))

    if not df.empty:
        df["gross_qty"] = pd_obj.to_numeric(df["gross_qty"], errors="coerce").fillna(0)
        df["return_qty"] = pd_obj.to_numeric(df["return_qty"], errors="coerce").fillna(0)
        df["net_qty"] = pd_obj.to_numeric(df["net_qty"], errors="coerce").fillna(0)
        df["return_qty"] = df["return_qty"].clip(lower=0)
        df["gross_qty"] = df[["gross_qty", "return_qty"]].max(axis=1)
        df["return_rate"] = df.apply(
            lambda r: (float(r["return_qty"]) / float(r["gross_qty"]) * 100) if float(r["gross_qty"] or 0) else 0,
            axis=1,
        )

    label = "반품수량" if return_col else "취소·반품수량" if cancel_col else "판매-순판매 차감수량"
    return df, {
        "available": True,
        "label": label,
        "gross_col": gross_col,
        "net_col": net_col,
        "signal_col": signal_col,
        "period_filter": can_period,
        "exact_return": bool(return_col),
    }


def _monthly_return_costs(pd_obj, core_module, start_iso: str | None, end_iso: str | None):
    """Best-effort product-level confirmed return cost aggregation."""
    rows = []
    try:
        months = list(core_module.monthly_available() or [])
    except Exception:
        return pd_obj.DataFrame(columns=["product_id", "return_cost"]), 0.0

    for month in months:
        month_text = str(month)
        month_start = month_text + "-01" if re.fullmatch(r"\d{4}-\d{2}", month_text) else None
        if start_iso and month_start and month_text < start_iso[:7]:
            continue
        if end_iso and month_start and month_text > end_iso[:7]:
            continue
        try:
            mdf, _ = core_module.confirmed_monthly_pnl(month)
        except Exception:
            continue
        if mdf is None or mdf.empty:
            continue
        if "return_pickup" not in mdf.columns and "return_restock" not in mdf.columns:
            continue
        tmp = mdf.copy()
        tmp["_return_cost"] = 0.0
        for col in ("return_pickup", "return_restock"):
            if col in tmp.columns:
                tmp["_return_cost"] += pd_obj.to_numeric(tmp[col], errors="coerce").fillna(0)
        rows.append(tmp)

    if not rows:
        return pd_obj.DataFrame(columns=["product_id", "return_cost"]), 0.0

    all_df = pd_obj.concat(rows, ignore_index=True)
    total = float(all_df["_return_cost"].sum())
    if "product_id" in all_df.columns:
        agg = all_df.groupby("product_id", as_index=False)["_return_cost"].sum()
        agg.columns = ["product_id", "return_cost"]
        return agg, total
    return pd_obj.DataFrame(columns=["product_id", "return_cost"]), total


def _period_bounds(label: str):
    today = date.today()
    if label == "이번 달":
        return today.replace(day=1).isoformat(), today.isoformat()
    if label == "최근 30일":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()
    if label == "최근 90일":
        return (today - timedelta(days=89)).isoformat(), today.isoformat()
    return None, None


def _build_view(pd_obj, core_module, db_path, period_label: str):
    products = _product_master(pd_obj, core_module, db_path)
    start_iso, end_iso = _period_bounds(period_label)
    sales, meta = _sales_signal(pd_obj, core_module, db_path, start_iso, end_iso)
    costs, total_return_cost = _monthly_return_costs(pd_obj, core_module, start_iso, end_iso)

    out = products.copy()
    if not sales.empty:
        out = out.merge(sales, how="left", left_on="id", right_on="product_id")
    else:
        for col in ("gross_qty", "return_qty", "withdrawal_qty", "net_qty", "return_rate"):
            out[col] = 0.0
    if not costs.empty:
        out = out.merge(costs, how="left", left_on="id", right_on="product_id", suffixes=("", "_cost"))
    if "return_cost" not in out.columns:
        out["return_cost"] = 0.0

    if "withdrawal_qty" not in out.columns:
        out["withdrawal_qty"] = 0.0
    for col in ("return_stock", "return_inbound", "gross_qty", "return_qty", "withdrawal_qty", "net_qty", "return_rate", "return_cost"):
        out[col] = pd_obj.to_numeric(out[col], errors="coerce").fillna(0)

    keep = (out["return_stock"].abs() > 1e-12) | (out["return_qty"] > 0) | (out["gross_qty"] > 0) | (out["return_cost"] != 0)
    out = out.loc[keep].copy()
    out = out.sort_values(["return_rate", "return_qty", "return_stock"], ascending=[False, False, False], kind="stable")
    return out, meta, total_return_cost, start_iso, end_iso


def _render_summary(st_obj, df, meta, total_return_cost):
    current_units = float(df["return_stock"].sum()) if not df.empty else 0.0
    current_skus = int((df["return_stock"].abs() > 1e-12).sum()) if not df.empty else 0
    total_returns = float(df["return_qty"].sum()) if not df.empty else 0.0
    gross = float(df["gross_qty"].sum()) if not df.empty else 0.0
    rate = total_returns / gross * 100 if gross else 0.0

    withdrawals = float(df["withdrawal_qty"].sum()) if "withdrawal_qty" in df.columns and not df.empty else 0.0
    columns = st_obj.columns(6 if meta.get("source") == "coupang_return_api" else 5)
    c1, c2, c3, c4 = columns[:4]
    c1.metric("반품창고 현재수량", _fmt_qty(current_units))
    c2.metric("반품 보유상품", f"{current_skus:,}개")
    if meta.get("available"):
        c3.metric("반품·취소 접수", _fmt_qty(total_returns))
        if meta.get("source") == "coupang_return_api":
            c4.metric("반품철회", _fmt_qty(withdrawals))
            columns[4].metric("반품 접수율", _fmt_pct(rate))
            columns[5].metric("반품비용", _fmt_money(total_return_cost))
        else:
            c4.metric("반품률", _fmt_pct(rate))
            columns[4].metric("반품비용", _fmt_money(total_return_cost))
    else:
        c3.metric("누적 반품수", "계산 불가")
        c4.metric("반품률", "계산 불가")
        columns[4].metric("반품비용", _fmt_money(total_return_cost))


def _display_table(pd_obj, df, meta):
    rows = []
    return_label = "반품수량" if meta.get("exact_return") else "취소·반품수량"
    for r in df.itertuples():
        rows.append({
            "품목코드": _display_code(r.item_code, r.option_id),
            "상품명": str(r.name or ""),
            "판매수량": _fmt_qty(r.gross_qty) if meta.get("available") else "-",
            return_label: _fmt_qty(r.return_qty) if meta.get("available") else "-",
            **({"반품철회": _fmt_qty(r.withdrawal_qty)} if meta.get("source") == "coupang_return_api" else {}),
            "반품률": _fmt_pct(r.return_rate) if meta.get("available") else "-",
            "현재 반품창고": _fmt_qty(r.return_stock),
            "반품비용": _fmt_money(r.return_cost),
        })
    return pd_obj.DataFrame(rows)


def _product_labels(df):
    return {
        int(r.id): f"{_display_code(r.item_code, r.option_id)} | {r.name}"
        for r in df.itertuples()
    }


def _render_product_detail(st_obj, pd_obj, core_module, db_path, df, meta, period_label):
    if df.empty:
        return
    st_obj.markdown("#### 상품별 상세")
    labels = _product_labels(df)
    ids = list(labels.keys())
    pid = st_obj.selectbox(
        "상품 선택",
        ids,
        format_func=lambda x: labels.get(int(x), str(x)),
        key="return_mgmt_product",
    )
    row = df[df["id"] == int(pid)].iloc[0]
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("판매수량", _fmt_qty(row["gross_qty"]) if meta.get("available") else "계산 불가")
    c2.metric("반품수량", _fmt_qty(row["return_qty"]) if meta.get("available") else "계산 불가")
    c3.metric("반품률", _fmt_pct(row["return_rate"]) if meta.get("available") else "계산 불가")
    c4.metric("현재 반품창고", _fmt_qty(row["return_stock"]))
    if meta.get("source") == "coupang_return_api":
        st_obj.caption(
            f"선택기간 반품철회 {_fmt_qty(row.get('withdrawal_qty', 0))} · "
            f"순판매 {_fmt_qty(row.get('net_qty', 0))}"
        )

    if not meta.get("available") or not meta.get("period_filter"):
        return
    if meta.get("source") == "coupang_return_api":
        return
    tables = _schema(core_module, db_path)
    cols = tables.get("sales_stats", set())
    gross_col = meta.get("gross_col")
    net_col = meta.get("net_col")
    signal_col = meta.get("signal_col")
    if not ("import_id" in cols and (signal_col or (gross_col and net_col))):
        return

    gross_expr = (
        f'SUM(COALESCE(s."{gross_col}",0))' if gross_col else
        f'SUM(COALESCE(s."{net_col}",0) + ABS(COALESCE(s."{signal_col}",0)))'
    )
    return_expr = (
        f'SUM(ABS(COALESCE(s."{signal_col}",0)))' if signal_col else
        f'SUM(MAX(COALESCE(s."{gross_col}",0)-COALESCE(s."{net_col}",0),0))'
    )
    sql = f"""
        SELECT substr(COALESCE(i.period_end,i.period_start),1,7) AS month,
               {gross_expr} AS gross_qty,
               {return_expr} AS return_qty
        FROM sales_stats s JOIN imports i ON i.id=s.import_id
        WHERE s.product_id=?
        GROUP BY substr(COALESCE(i.period_end,i.period_start),1,7)
        ORDER BY month
    """
    with core_module._conn(db_path) as c:
        trend = pd_obj.read_sql_query(sql, c, params=(int(pid),))
    if trend.empty:
        return
    trend["gross_qty"] = pd_obj.to_numeric(trend["gross_qty"], errors="coerce").fillna(0)
    trend["return_qty"] = pd_obj.to_numeric(trend["return_qty"], errors="coerce").fillna(0)
    trend["반품률"] = trend.apply(
        lambda r: float(r["return_qty"]) / float(r["gross_qty"]) * 100 if float(r["gross_qty"] or 0) else 0,
        axis=1,
    )
    st_obj.markdown("#### 월별 반품률 추이")
    st_obj.line_chart(trend.set_index("month")[["반품률"]], height=250)


def render_return_management_page(st, pd, core, page_header, section, **_kwargs):
    db_path = core.DEFAULT_DB
    page_header(
        "반품관리",
        "상품별 반품수량·반품률·반품창고 현재고·반품비용을 한눈에 확인합니다.",
        eyebrow="RETURNS",
    )

    period = st.selectbox(
        "조회기간",
        ["전체", "이번 달", "최근 30일", "최근 90일"],
        index=0,
        key="return_mgmt_period",
    )
    df, meta, total_return_cost, start_iso, end_iso = _build_view(pd, core, db_path, period)

    _render_summary(st, df, meta, total_return_cost)
    if start_iso and end_iso:
        st.caption(f"조회기간: {start_iso} ~ {end_iso}")

    if meta.get("available"):
        if meta.get("source") == "coupang_return_api":
            st.caption(
                "반품수량은 수동 동기화한 쿠팡 반품·취소 API의 접수일 기준입니다. "
                "로켓그로스 주문번호·옵션ID와 일치한 건만 집계하며 철회는 철회일에 순판매수량으로 복원합니다."
            )
        elif meta.get("exact_return"):
            st.caption("반품수량은 판매통계에 저장된 명시적 반품수량 컬럼을 사용합니다.")
        else:
            st.warning(
                "현재 판매통계에는 별도 '반품수량' 대신 취소수량 또는 판매수량-순판매수량 정보가 저장되어 있습니다. "
                "따라서 화면의 누적 반품수·반품률은 '취소·반품' 기준입니다. 비용행 개수를 반품건수로 추정하지는 않습니다."
            )
    else:
        st.warning(
            "현재 DB의 판매통계만으로는 누적 반품수와 반품률을 정확히 계산할 수 없습니다. "
            "반품창고 현재수량과 반품비용은 그대로 표시하며, 반품수량 원자료가 추가되면 자동으로 계산하도록 구성했습니다."
        )
        if meta.get("reason"):
            st.caption(str(meta["reason"]))

    section("상품별 반품 현황", "반품률이 높은 상품부터 확인할 수 있도록 정렬합니다.")
    if df.empty:
        st.info("선택한 기간에 표시할 반품/판매 자료가 없습니다.")
    else:
        st.dataframe(
            _display_table(pd, df, meta),
            use_container_width=True,
            hide_index=True,
            height=min(700, max(240, 38 * (len(df) + 1))),
        )
        _render_product_detail(st, pd, core, db_path, df, meta, period)

    with st.expander("지표 기준"):
        st.markdown(
            "- **반품창고 현재수량**: 반품창고 재고원장의 현재 합계\n"
            "- **누적 반품수 / 반품률**: API 동기화 시 접수일 기준, 아니면 판매통계의 반품·취소수량 기준\n"
            "- **반품철회**: 철회일에 순판매수량과 잠정매출을 복원\n"
            "- **반품비용**: 월 확정손익의 반품회수비 + 반품재입고비\n"
            "- 반품회수비 정산의 행 개수는 반품건수로 간주하지 않음"
        )


def patch_source(source: str) -> str:
    menu_label = '        "↩️  반품관리",\n'
    if menu_label not in source:
        anchor = '        "📈  판매·손익",\n'
        if anchor not in source:
            raise RuntimeError("반품관리 메뉴를 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, anchor + menu_label, 1)

    handler = '''# ------------------------------
# Return management
# ------------------------------
elif page == "↩️  반품관리":
    return_management_v093.render_return_management_page(
        st=st, pd=pd, core=core, page_header=page_header, section=section,
        kpi=kpi, money=money, fmt_date=fmt_date, latest_updated_text=latest_updated_text,
    )


'''
    if 'elif page == "↩️  반품관리":' not in source:
        anchor = '# ------------------------------\n# Purchase import / matching\n# ------------------------------\nelif page == "🧾  매입관리":\n'
        if anchor not in source:
            raise RuntimeError("반품관리 화면을 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, handler + anchor, 1)
    return source
