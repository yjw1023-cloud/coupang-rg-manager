"""RG Manager v0.9.2 purchase history UI.

Adds a dedicated item-by-item purchase-history page. Existing purchase/import
logic and persisted rows are not modified.
"""
from __future__ import annotations

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


def _display_code(item_code: Any, option_id: Any = None) -> str:
    code = "" if item_code is None else str(item_code).strip()
    if re.fullmatch(r"CP-\d+", code):
        return str(option_id or code[3:])
    return code


def _schema_info(core_module, db_path):
    core_module.init_db(db_path)
    with core_module._conn(db_path) as c:
        tables = {
            str(r["name"])
            for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        purchase_cols = set()
        if "purchase_lines" in tables:
            purchase_cols = {
                str(r["name"]) for r in c.execute("PRAGMA table_info(purchase_lines)").fetchall()
            }
    return tables, purchase_cols


def _products_with_history(pd_obj, core_module, db_path):
    tables, _ = _schema_info(core_module, db_path)
    if "purchase_lines" not in tables:
        return pd_obj.DataFrame()
    with core_module._conn(db_path) as c:
        return pd_obj.read_sql_query(
            """
            SELECT
                p.id,
                p.item_code,
                p.option_id,
                p.name,
                COUNT(pl.id) AS purchase_count,
                MAX(pl.purchase_date) AS last_purchase_date,
                COALESCE(SUM(pl.qty_receipt), 0) AS total_qty,
                COALESCE(SUM(pl.landed_total_krw), 0) AS total_amount
            FROM products p
            JOIN purchase_lines pl ON pl.product_id=p.id
            GROUP BY p.id,p.item_code,p.option_id,p.name
            ORDER BY MAX(pl.purchase_date) DESC, p.name, p.item_code
            """,
            c,
        )


def _history(pd_obj, core_module, db_path, product_id: int | None = None):
    tables, cols = _schema_info(core_module, db_path)
    if "purchase_lines" not in tables:
        return pd_obj.DataFrame()

    batch_expr = "COALESCE(pl.purchase_batch,'')" if "purchase_batch" in cols else "''"
    file_expr = "COALESCE(i.file_name,'')" if "imports" in tables else "''"
    import_join = "LEFT JOIN imports i ON i.id=pl.import_id" if "imports" in tables else ""

    sql = f"""
        SELECT
            pl.id,
            pl.purchase_date,
            {batch_expr} AS purchase_batch,
            pl.product_id,
            p.item_code,
            p.option_id,
            p.name AS product_name,
            COALESCE(pl.source_name,'') AS source_name,
            COALESCE(pl.source_detail,'') AS source_detail,
            COALESCE(pl.qty_receipt, pl.qty_source, 0) AS qty,
            CASE
                WHEN COALESCE(pl.landed_unit_cost_krw,0) <> 0
                    THEN pl.landed_unit_cost_krw
                WHEN COALESCE(pl.qty_receipt, pl.qty_source,0) <> 0
                     AND COALESCE(pl.landed_total_krw,0) <> 0
                    THEN pl.landed_total_krw / COALESCE(pl.qty_receipt, pl.qty_source)
                ELSE COALESCE(pl.unit_price,0)
            END AS unit_cost,
            CASE
                WHEN COALESCE(pl.landed_total_krw,0) <> 0
                    THEN pl.landed_total_krw
                ELSE COALESCE(pl.total_amount,0)
            END AS amount,
            {file_expr} AS file_name,
            COALESCE(pl.sheet_name,'') AS sheet_name,
            COALESCE(pl.source_row,0) AS source_row
        FROM purchase_lines pl
        JOIN products p ON p.id=pl.product_id
        {import_join}
    """
    params: tuple[Any, ...] = ()
    if product_id is not None:
        sql += " WHERE pl.product_id=?"
        params = (int(product_id),)
    sql += " ORDER BY pl.purchase_date DESC, pl.id DESC"

    with core_module._conn(db_path) as c:
        return pd_obj.read_sql_query(sql, c, params=params)


def _product_labels(df):
    labels = {}
    for r in df.itertuples():
        code = _display_code(r.item_code, r.option_id)
        labels[int(r.id)] = f"{code} | {r.name}" if code else str(r.name)
    return labels


def _selected_product(st_obj, df, key_prefix: str):
    if df.empty:
        return None, {}
    labels = _product_labels(df)
    ids = [int(x) for x in df["id"].tolist()]
    pid = st_obj.selectbox(
        "상품 선택",
        ids,
        index=0,
        format_func=lambda x: labels.get(int(x), str(x)),
        key=f"{key_prefix}_product_id",
        help="상품명이나 품목코드를 입력해 빠르게 찾을 수 있습니다.",
    )
    return int(pid), labels


def _summary(hist):
    if hist.empty:
        return {}
    qty = float(hist["qty"].fillna(0).sum())
    amount = float(hist["amount"].fillna(0).sum())
    latest = hist.iloc[0]
    return {
        "count": int(len(hist)),
        "qty": qty,
        "amount": amount,
        "latest_date": str(latest.get("purchase_date") or "-"),
        "latest_cost": float(latest.get("unit_cost") or 0),
        "latest_batch": str(latest.get("purchase_batch") or ""),
    }


def _history_display(pd_obj, hist):
    if hist.empty:
        return pd_obj.DataFrame()
    rows = []
    for r in hist.itertuples():
        rows.append(
            {
                "매입일": str(r.purchase_date or ""),
                "차수": str(r.purchase_batch or ""),
                "매입수량": _fmt_qty(r.qty),
                "개당 매입가": _fmt_money(r.unit_cost),
                "매입금액": _fmt_money(r.amount),
                "매입자료 상품명": str(r.source_name or ""),
                "비고": str(r.source_detail or ""),
            }
        )
    return pd_obj.DataFrame(rows)


def _render_kpis(st_obj, hist):
    s = _summary(hist)
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("최근 매입가", _fmt_money(s["latest_cost"]))
    c2.metric("누적 매입수량", _fmt_qty(s["qty"]))
    c3.metric("누적 매입액", _fmt_money(s["amount"]))
    c4.metric("최근 매입일", s["latest_date"])
    batch_text = f" · 최근 차수 {s['latest_batch']}" if s["latest_batch"] else ""
    st_obj.caption(f"총 {s['count']:,}건의 매입이력{batch_text}")


def _render_trend(st_obj, pd_obj, hist):
    if len(hist) < 2:
        return
    trend = hist[["purchase_date", "qty", "amount"]].copy()
    trend["purchase_date"] = pd_obj.to_datetime(trend["purchase_date"], errors="coerce")
    trend = trend.dropna(subset=["purchase_date"]).sort_values("purchase_date")
    if trend.empty:
        return

    grouped = trend.groupby("purchase_date", as_index=False).agg({"qty": "sum", "amount": "sum"})
    grouped["개당 매입가"] = grouped.apply(
        lambda r: (float(r["amount"]) / float(r["qty"])) if float(r["qty"] or 0) else 0,
        axis=1,
    )
    chart = grouped.set_index("purchase_date")[["개당 매입가"]]
    st_obj.markdown("#### 매입단가 추이")
    st_obj.line_chart(chart, height=250)


def render_purchase_history_page(st, pd, core, page_header, section, **_kwargs):
    db_path = core.DEFAULT_DB
    page_header(
        "매입이력",
        "상품별로 언제, 몇 개를, 얼마에 매입했는지 과거 이력을 한눈에 확인합니다.",
        eyebrow="PURCHASE HISTORY",
    )

    products = _products_with_history(pd, core, db_path)
    if products.empty:
        st.info("아직 저장된 매입이력이 없습니다. 매입관리에서 매입자료를 먼저 등록해 주세요.")
        return

    tabs = st.tabs(["상품별 이력", "전체 매입내역"])

    with tabs[0]:
        pid, labels = _selected_product(st, products, key_prefix="purchase_history")
        hist = _history(pd, core, db_path, pid)
        if hist.empty:
            st.info("선택한 상품의 매입이력이 없습니다.")
        else:
            selected_label = labels.get(pid, "")
            section(selected_label, "최근 매입가와 누적 매입, 날짜별 단가 변화를 확인합니다.")
            _render_kpis(st, hist)

            section("매입 내역", "최신 매입순으로 표시합니다.")
            st.dataframe(
                _history_display(pd, hist),
                use_container_width=True,
                hide_index=True,
                height=min(650, max(220, 38 * (len(hist) + 1))),
            )
            _render_trend(st, pd, hist)

            with st.expander("원본 매입자료 정보"):
                raw = hist[
                    [
                        "purchase_date",
                        "purchase_batch",
                        "source_name",
                        "source_detail",
                        "file_name",
                        "sheet_name",
                        "source_row",
                    ]
                ].copy()
                raw.columns = ["매입일", "차수", "매입자료 상품명", "상세정보", "파일", "시트", "원본행"]
                st.dataframe(raw, use_container_width=True, hide_index=True)

    with tabs[1]:
        all_hist = _history(pd, core, db_path)
        if all_hist.empty:
            st.info("저장된 매입이력이 없습니다.")
            return

        c1, c2, c3 = st.columns(3)
        c1.metric("매입이력", f"{len(all_hist):,}건")
        c2.metric("매입상품", f"{all_hist['product_id'].nunique():,}개")
        c3.metric("누적 매입액", _fmt_money(all_hist["amount"].fillna(0).sum()))

        batches = [x for x in all_hist["purchase_batch"].fillna("").astype(str).unique().tolist() if x]
        batch_options = ["전체"] + sorted(
            batches,
            key=lambda x: int(re.search(r"\d+", x).group()) if re.search(r"\d+", x) else -1,
            reverse=True,
        )
        selected_batch = st.selectbox("차수", batch_options, key="purchase_history_all_batch")
        view_hist = all_hist if selected_batch == "전체" else all_hist[all_hist["purchase_batch"] == selected_batch]

        rows = []
        for r in view_hist.itertuples():
            rows.append(
                {
                    "매입일": str(r.purchase_date or ""),
                    "차수": str(r.purchase_batch or ""),
                    "품목코드": _display_code(r.item_code, r.option_id),
                    "상품명": str(r.product_name or ""),
                    "매입수량": _fmt_qty(r.qty),
                    "개당 매입가": _fmt_money(r.unit_cost),
                    "매입금액": _fmt_money(r.amount),
                }
            )
        st.dataframe(
            pd.DataFrame(rows),
            use_container_width=True,
            hide_index=True,
            height=min(700, max(240, 38 * (len(rows) + 1))),
        )


def patch_source(source: str) -> str:
    menu_label = '        "🗂️  매입이력",\n'
    if menu_label not in source:
        anchor = '        "🧾  매입관리",\n'
        if anchor not in source:
            raise RuntimeError("매입이력 메뉴를 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, anchor + menu_label, 1)

    handler = '''# ------------------------------
# Purchase history
# ------------------------------
elif page == "🗂️  매입이력":
    purchase_history_v092.render_purchase_history_page(
        st=st, pd=pd, core=core, page_header=page_header, section=section,
        kpi=kpi, money=money, fmt_date=fmt_date, latest_updated_text=latest_updated_text,
    )


'''
    if 'elif page == "🗂️  매입이력":' not in source:
        anchor = '# ------------------------------\n# Legacy ERP migration\n# ------------------------------\nelif page == "📥  기존ERP 이관":\n'
        if anchor not in source:
            raise RuntimeError("매입이력 화면을 추가할 위치를 찾지 못했습니다.")
        source = source.replace(anchor, handler + anchor, 1)
    return source
