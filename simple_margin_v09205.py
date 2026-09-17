"""v0.9.210 quick Coupang product margin view."""
from __future__ import annotations

from datetime import date, timedelta
import html
import math

import pandas as pd

PAGE_LABEL = "간략이익률"
VAT_RATE = 0.04


def _n(v):
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").replace("개", "").replace("%", "").strip()
        x = float(v or 0)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0


def _nullable(v):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _oid(v):
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


def _exists(c, table):
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table):
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _money(v):
    return f"{int(round(_n(v))):,}원"


def _pct(v):
    return f"{_n(v):,.1f}%"


def _qty(v):
    x = _n(v)
    return f"{int(round(x)):,}" if abs(x - round(x)) < 1e-9 else f"{x:,.1f}"


def _shift(month, delta):
    y, m = [int(x) for x in str(month).split("-")]
    idx = y * 12 + m - 1 + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _aliases(core, db, product_id, option_id):
    out = {_oid(option_id)} if _oid(option_id) else set()
    try:
        with core._conn(db) as c:
            if _exists(c, "return_discount_aliases"):
                rows = c.execute(
                    "SELECT discount_option_id FROM return_discount_aliases WHERE parent_product_id=?",
                    (int(product_id),),
                ).fetchall()
                out |= {_oid(r["discount_option_id"]) for r in rows if _oid(r["discount_option_id"])}
    except Exception:
        pass
    return out


def _products(core, db):
    core.init_db(db)
    try:
        import product_visibility_v0995 as visibility
        hidden = set(visibility.hidden_ids(core, db))
    except Exception:
        hidden = set()

    with core._conn(db) as c:
        alias_ids = set()
        if _exists(c, "return_discount_aliases"):
            alias_ids = {
                _oid(r["discount_option_id"])
                for r in c.execute("SELECT discount_option_id FROM return_discount_aliases")
                if _oid(r["discount_option_id"])
            }
        pc = _cols(c, "products")
        fields = [
            "id",
            "item_code" if "item_code" in pc else "'' item_code",
            "option_id" if "option_id" in pc else "'' option_id",
            "name" if "name" in pc else "'' name",
            "item_type" if "item_type" in pc else "'' item_type",
            "unit_cost" if "unit_cost" in pc else "0 unit_cost",
            "active" if "active" in pc else "1 active",
        ]
        rows = c.execute("SELECT " + ",".join(fields) + " FROM products ORDER BY name,id").fetchall()

    out = []
    for r in rows:
        pid = int(r["id"])
        oid = _oid(r["option_id"])
        if pid in hidden or not oid or oid in alias_ids:
            continue
        if int(_n(r["active"])) == 0 or str(r["item_type"] or "").strip().lower() == "raw":
            continue
        out.append({
            "product_id": pid,
            "item_code": str(r["item_code"] or ""),
            "option_id": oid,
            "name": str(r["name"] or "").strip() or oid,
            "unit_cost": max(0.0, _n(r["unit_cost"])),
        })
    return out


def _rank_qty(core, db, products):
    end = date.today()
    start = end - timedelta(days=29)
    oid_to_pid = {}
    for p in products:
        for oid in _aliases(core, db, p["product_id"], p["option_id"]):
            oid_to_pid[oid] = p["product_id"]

    out = {p["product_id"]: 0.0 for p in products}
    api_found = set()
    try:
        with core._conn(db) as c:
            if _exists(c, "coupang_rg_order_items") and {
                "paid_date", "vendor_item_id", "sales_quantity"
            }.issubset(_cols(c, "coupang_rg_order_items")):
                rows = c.execute(
                    """SELECT vendor_item_id,SUM(COALESCE(sales_quantity,0)) qty
                       FROM coupang_rg_order_items
                       WHERE paid_date>=? AND paid_date<=?
                       GROUP BY vendor_item_id""",
                    (start.isoformat(), end.isoformat()),
                ).fetchall()
                for r in rows:
                    pid = oid_to_pid.get(_oid(r["vendor_item_id"]))
                    q = max(0.0, _n(r["qty"]))
                    if pid is not None and q > 0:
                        out[pid] += q
                        api_found.add(pid)
    except Exception:
        pass

    try:
        with core._conn(db) as c:
            if _exists(c, "sales_stats") and _exists(c, "imports"):
                sc, ic = _cols(c, "sales_stats"), _cols(c, "imports")
                if {"product_id", "net_qty", "import_id"}.issubset(sc) and {
                    "id", "data_type", "period_start", "period_end"
                }.issubset(ic):
                    rows = c.execute(
                        """SELECT s.product_id,SUM(COALESCE(s.net_qty,0)) qty
                           FROM sales_stats s JOIN imports i ON i.id=s.import_id
                           WHERE i.data_type='sales_stats'
                             AND i.period_start>=? AND i.period_end<=?
                           GROUP BY s.product_id""",
                        (start.isoformat(), end.isoformat()),
                    ).fetchall()
                    for r in rows:
                        pid = int(_n(r["product_id"]))
                        if pid in out and pid not in api_found:
                            out[pid] = max(0.0, _n(r["qty"]))
    except Exception:
        pass
    return out


def _production_cost(core, db, product):
    fallback = max(0.0, _n(product.get("unit_cost")))
    try:
        with core._conn(db) as c:
            pc = _cols(c, "production_orders") if _exists(c, "production_orders") else set()
            if not {"parent_product_id", "produced_unit_cost"}.issubset(pc):
                raise LookupError
            date_expr = "production_date" if "production_date" in pc else "''"
            id_expr = "id" if "id" in pc else "rowid"
            row = c.execute(
                f"""SELECT {date_expr} production_date,produced_unit_cost
                    FROM production_orders
                    WHERE parent_product_id=? AND COALESCE(produced_unit_cost,0)>0
                    ORDER BY {date_expr} DESC,{id_expr} DESC LIMIT 1""",
                (product["product_id"],),
            ).fetchone()
        if row and _n(row["produced_unit_cost"]) > 0:
            dt = str(row["production_date"] or "")[:10]
            return {
                "value": _n(row["produced_unit_cost"]),
                "note": f"{dt} 생산한 완제품 원가" if dt else "최근 생산한 완제품 원가",
            }
    except Exception:
        pass
    return {
        "value": fallback,
        "note": "최근 생산원가가 없어 품목관리의 현재 원가 사용" if fallback > 0 else "생산원가 자료 없음",
    }


def _sales_imports_desc(core, db):
    try:
        with core._conn(db) as c:
            if not _exists(c, "imports"):
                return []
            ic = _cols(c, "imports")
            file_expr = "file_name" if "file_name" in ic else "''"
            start_expr = "period_start" if "period_start" in ic else "''"
            end_expr = "period_end" if "period_end" in ic else "''"
            rows = c.execute(
                f"""SELECT id,{file_expr} file_name,{start_expr} period_start,{end_expr} period_end
                    FROM imports WHERE data_type='sales_stats' ORDER BY id DESC"""
            ).fetchall()
        out = []
        for r in rows:
            start = str(r["period_start"] or "")[:10]
            end = str(r["period_end"] or "")[:10]
            month = end[:7] or start[:7] or date.today().strftime("%Y-%m")
            out.append({
                "id": int(r["id"]),
                "file_name": str(r["file_name"] or ""),
                "start": start,
                "end": end,
                "month": month,
            })
        return out
    except Exception:
        return []


def _sale_price(core, db, product):
    imports = _sales_imports_desc(core, db)
    if not imports:
        return {
            "value": 0.0,
            "qty": 0.0,
            "month": date.today().strftime("%Y-%m"),
            "note": "입력된 판매자료 없음",
        }

    try:
        with core._conn(db) as c:
            sc = _cols(c, "sales_stats") if _exists(c, "sales_stats") else set()
            if not {"import_id", "net_qty", "displayed_net_sales"}.issubset(sc):
                raise LookupError
            for imp in imports:
                if "option_id" in sc:
                    row = c.execute(
                        """SELECT SUM(COALESCE(net_qty,0)) qty,
                                  SUM(COALESCE(displayed_net_sales,0)) amount
                           FROM sales_stats
                           WHERE import_id=? AND CAST(option_id AS TEXT)=?""",
                        (imp["id"], product["option_id"]),
                    ).fetchone()
                else:
                    row = c.execute(
                        """SELECT SUM(COALESCE(net_qty,0)) qty,
                                  SUM(COALESCE(displayed_net_sales,0)) amount
                           FROM sales_stats
                           WHERE import_id=? AND product_id=?""",
                        (imp["id"], product["product_id"]),
                    ).fetchone()
                qty = max(0.0, _n(row["qty"] if row else 0))
                amount = max(0.0, _n(row["amount"] if row else 0))
                if qty <= 0 or amount <= 0:
                    continue
                period = (
                    f"{imp['start']}~{imp['end']}"
                    if imp["start"] or imp["end"]
                    else "최근 입력 판매자료"
                )
                return {
                    "value": amount / qty,
                    "qty": qty,
                    "month": imp["month"],
                    "note": f"{period} 판매자료 · 신상품 {_qty(qty)}개 기준",
                }
    except Exception:
        pass

    newest = imports[0]
    return {
        "value": 0.0,
        "qty": 0.0,
        "month": newest["month"],
        "note": "입력된 판매자료 전체에서 이 상품의 신상품 판매 없음",
    }


def _prior_qty(core, db, month, product, option_ids):
    try:
        with core._conn(db) as c:
            if not _exists(c, "settlement_sales"):
                return 0.0
            rows = c.execute(
                "SELECT * FROM settlement_sales WHERE settlement_month=? ORDER BY id",
                (month,),
            ).fetchall()
        dedup = {}
        for r in rows:
            keys = set(r.keys())
            pid = int(_n(r["product_id"])) if "product_id" in keys else 0
            oid = _oid(r["option_id"]) if "option_id" in keys else ""
            if pid != product["product_id"] and oid not in option_ids:
                continue
            order_id = str(r["order_id"] or "") if "order_id" in keys else ""
            typ = str(r["transaction_type"] or "") if "transaction_type" in keys else ""
            rid = int(_n(r["id"])) if "id" in keys else len(dedup)
            dedup[(order_id or f"#{rid}", typ)] = r
        return sum(max(0.0, _n(r["qty"])) for r in dedup.values())
    except Exception:
        return 0.0


def _provisional_commission_rate():
    try:
        import coupang_api_sync_v09140 as api
        rate = _n(getattr(api, "PROVISIONAL_COMMISSION_RATE", 0.108))
        return rate if rate > 0 else 0.108
    except Exception:
        return 0.108


def _commission_logistics(core, db, product, current, prev, sale):
    try:
        import provisional_sales_basis_v09196 as basis
    except Exception:
        basis = None

    manual = {}
    if basis:
        try:
            manual = basis._manual(core, db, current).get(product["option_id"], {})
        except Exception:
            manual = {}

    prior_comm = None
    prior_logi = None
    if basis:
        try:
            x = basis._prior_comm(core, db, current, product["option_id"], product["product_id"])
            if x and str(x.get("month") or "") == prev:
                prior_comm = x
        except Exception:
            pass
        try:
            x = basis._prior_logi(core, db, current, product["option_id"], product["product_id"])
            if x and str(x.get("month") or "") == prev:
                prior_logi = x
        except Exception:
            pass

    manual_comm = _nullable(manual.get("commission_unit_override"))
    if manual_comm is not None:
        commission = {
            "value": max(0.0, _n(manual_comm)),
            "note": f"{current} 잠정실적에서 직접 입력한 평균 수수료",
        }
    elif prior_comm:
        commission = {
            "value": max(0.0, _n(prior_comm.get("unit"))),
            "note": f"{prev} 정산기록의 평균 수수료",
        }
    elif _n((sale or {}).get("value")) > 0:
        rate = _provisional_commission_rate()
        commission = {
            "value": _n(sale.get("value")) * rate,
            "note": f"{current} 잠정실적 자동 예상 수수료율 {rate * 100:.1f}% 적용",
        }
    else:
        commission = {
            "value": 0.0,
            "note": f"{current} 잠정실적·{prev} 정산 수수료 기준값 없음",
        }

    manual_logi = _nullable(manual.get("logistics_unit_override"))
    if manual_logi is not None:
        logistics = {
            "value": max(0.0, _n(manual_logi)),
            "note": f"{current} 잠정실적에서 직접 입력한 평균 입출고배송비",
        }
    elif prior_logi:
        logistics = {
            "value": max(0.0, _n(prior_logi.get("unit"))),
            "note": f"{prev} 정산기록의 평균 입출고배송비",
        }
    else:
        logistics = {
            "value": 0.0,
            "note": f"{current} 잠정실적 입력값·{prev} 정산기록 없음",
        }
    return commission, logistics


def _other_cost(core, db, product, option_ids, prev, prev_qty):
    total = 0.0
    try:
        with core._conn(db) as c:
            if _exists(c, "logistics_fees"):
                rows = c.execute(
                    "SELECT * FROM logistics_fees WHERE settlement_month=? ORDER BY id",
                    (prev,),
                ).fetchall()
                dedup = {}
                for r in rows:
                    keys = set(r.keys())
                    pid = int(_n(r["product_id"])) if "product_id" in keys else 0
                    oid = _oid(r["option_id"]) if "option_id" in keys else ""
                    if pid != product["product_id"] and oid not in option_ids:
                        continue
                    typ = str(r["fee_type"] or "") if "fee_type" in keys else ""
                    if typ in {"입출고비", "배송비"}:
                        continue
                    order_id = str(r["order_id"] or "") if "order_id" in keys else ""
                    rid = int(_n(r["id"])) if "id" in keys else len(dedup)
                    dedup[(order_id or f"#{rid}", typ)] = r
                total = sum(
                    abs(_n(r["final_cost_prevat"]))
                    for r in dedup.values()
                    if "final_cost_prevat" in set(r.keys())
                )
        if total > 0 and prev_qty > 0:
            return {
                "value": total / prev_qty,
                "note": f"{prev} 반품비 등 기타 쿠팡비용 ÷ 판매 {_qty(prev_qty)}개",
            }
    except Exception:
        pass
    return {"value": 0.0, "note": f"{prev} 기타 쿠팡비용 기록 없음 → 0원"}


def _month_sales_qty(core, db, month, product):
    try:
        with core._conn(db) as c:
            if not (_exists(c, "sales_stats") and _exists(c, "imports")):
                return 0.0
            sc, ic = _cols(c, "sales_stats"), _cols(c, "imports")
            qty_col = "sales_qty" if "sales_qty" in sc else ("net_qty" if "net_qty" in sc else None)
            if not qty_col or not {"id", "data_type", "period_start", "period_end"}.issubset(ic):
                return 0.0
            if "option_id" in sc:
                where = "CAST(s.option_id AS TEXT)=?"
                key = product["option_id"]
            else:
                where = "s.product_id=?"
                key = product["product_id"]
            row = c.execute(
                f"""SELECT SUM(COALESCE(s.{qty_col},0)) qty
                    FROM sales_stats s JOIN imports i ON i.id=s.import_id
                    WHERE i.data_type='sales_stats'
                      AND substr(i.period_start,1,7)=?
                      AND substr(i.period_end,1,7)=?
                      AND {where}""",
                (month, month, key),
            ).fetchone()
        return max(0.0, _n(row["qty"] if row else 0))
    except Exception:
        return 0.0


def _ad_cost(core, db, product, month, fallback_qty):
    try:
        import provisional_ad_report_v0956 as ad
        data = ad.load_month(core, month, db)
        item = (data.get("items") or {}).get(product["option_id"]) or {}
        total = abs(_n(item.get("ad_spend")))
        if total <= 0:
            return {
                "value": 0.0,
                "note": f"{month} 잠정실적 광고보고서의 상품 광고비 없음 → 0원",
            }
        qty = _month_sales_qty(core, db, month, product) or max(0.0, _n(fallback_qty))
        if qty <= 0:
            return {"value": 0.0, "note": f"{month} 광고비 {_money(total)} · 판매수 없음"}
        return {
            "value": total / qty,
            "note": f"{month} 잠정실적 광고보고서 {_money(total)} ÷ 판매 {_qty(qty)}개",
        }
    except Exception:
        return {"value": 0.0, "note": f"{month} 잠정실적 광고보고서 확인 불가 → 0원"}


def calculate(core, product, db_path=None):
    db = db_path or core.DEFAULT_DB
    current = date.today().strftime("%Y-%m")
    prev = _shift(current, -1)
    option_ids = _aliases(core, db, product["product_id"], product["option_id"])

    sale = _sale_price(core, db, product)
    cost = _production_cost(core, db, product)
    commission, logistics = _commission_logistics(core, db, product, current, prev, sale)
    prev_qty = _prior_qty(core, db, prev, product, option_ids)
    other = _other_cost(core, db, product, option_ids, prev, prev_qty)
    ad = _ad_cost(core, db, product, sale.get("month") or current, sale.get("qty"))
    vat = max(0.0, _n(sale["value"])) * VAT_RATE

    margin = (
        _n(sale["value"])
        - _n(cost["value"])
        - _n(commission["value"])
        - _n(logistics["value"])
        - _n(other["value"])
        - _n(ad["value"])
        - vat
    )
    rate = margin / _n(sale["value"]) * 100 if _n(sale["value"]) > 0 else 0.0
    return {
        "sale": sale,
        "cost": cost,
        "commission": commission,
        "logistics": logistics,
        "other": other,
        "ad": ad,
        "vat": {"value": vat, "note": f"판매단가의 {VAT_RATE * 100:.0f}%"},
        "margin": {"value": margin, "note": ""},
        "margin_rate": {"value": rate, "note": "마진 ÷ 판매단가 × 100"},
        "prev_month": prev,
        "current_month": current,
    }


def _table(rows):
    trs = []
    for row in rows:
        label = html.escape(str(row["항목"]))
        cls = " class='hi'" if label in {"마진", "마진률"} else ""
        trs.append(
            f"<tr{cls}><td>{label}</td><td><b>{html.escape(str(row['단가']))}</b></td>"
            f"<td>{html.escape(str(row['비고'] or ''))}</td></tr>"
        )
    return (
        "<style>.smt{border:1px solid #d9e2ef;border-radius:12px;overflow:hidden;background:white}"
        ".smt table{width:100%;border-collapse:collapse;table-layout:fixed}"
        ".smt th{background:#edf3fb;color:#17324f;font-weight:800;padding:11px 8px;text-align:center;border-bottom:1px solid #ccd8e8}"
        ".smt td{padding:10px 8px;text-align:center;vertical-align:middle;border-bottom:1px solid #e3e9f1;color:#1e2d3d}"
        ".smt tr:nth-child(even) td{background:#f8fafc}.smt tr:last-child td{border-bottom:0}"
        ".smt th:nth-child(1),.smt td:nth-child(1){width:22%}.smt th:nth-child(2),.smt td:nth-child(2){width:22%}"
        ".smt th:nth-child(3),.smt td:nth-child(3){width:56%}.smt tr.hi td{background:#eef6ff!important;font-weight:700}</style>"
        "<div class='smt'><table><thead><tr><th>항목</th><th>단가</th><th>비고</th></tr></thead><tbody>"
        + "".join(trs)
        + "</tbody></table></div>"
    )


def render_page(st, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    st.markdown("## 간략이익률")
    st.caption(
        "왼쪽 상품목록은 최근 30일 판매량순입니다. 판매단가는 입력한 판매자료 중 이 상품의 신상품 판매가 있는 가장 최근 자료를 사용합니다."
    )

    try:
        products = _products(core, db)
        rank = _rank_qty(core, db, products)
    except Exception as exc:
        st.error(f"쿠팡 판매상품 목록을 불러오지 못했습니다: {exc}")
        return
    if not products:
        st.info("선택할 쿠팡 판매상품이 없습니다.")
        return

    products = sorted(
        products,
        key=lambda p: (-_n(rank.get(p["product_id"], 0)), p["name"], p["option_id"]),
    )
    key = "simple_margin_product_v09205"
    valid = {p["product_id"] for p in products}
    selected = int(st.session_state.get(key) or products[0]["product_id"])
    if selected not in valid:
        selected = products[0]["product_id"]
        st.session_state[key] = selected

    left, right = st.columns([0.38, 0.62], gap="large")
    with left:
        st.markdown("### 쿠팡 판매상품")
        query = str(
            st.text_input(
                "상품 검색",
                placeholder="상품명 또는 옵션ID",
                key="simple_margin_search_v09206",
                label_visibility="collapsed",
            )
            or ""
        ).strip().lower()
        visible = [
            p for p in products
            if not query
            or query in p["name"].lower()
            or query in p["option_id"].lower()
            or query in p["item_code"].lower()
        ]
        if not visible:
            st.info("검색 결과가 없습니다.")
        else:
            try:
                box = st.container(height=690, border=True)
            except TypeError:
                box = st.container(border=True)
            with box:
                ranks = {p["product_id"]: i + 1 for i, p in enumerate(products)}
                for p in visible:
                    pid = p["product_id"]
                    label = (
                        f"{ranks[pid]}. {p['name']}\n"
                        f"30일 판매 {_qty(rank.get(pid, 0))}개 · 옵션ID {p['option_id']}"
                    )
                    if st.button(
                        label,
                        key=f"simple_margin_pick_{pid}_v09206",
                        use_container_width=True,
                        type="primary" if pid == selected else "secondary",
                    ):
                        st.session_state[key] = pid
                        st.rerun()

    product = next(x for x in products if x["product_id"] == selected)
    result = calculate(core, product, db)
    sale = _n(result["sale"]["value"])
    margin = _n(result["margin"]["value"])
    margin_rate = _n(result["margin_rate"]["value"])

    with right:
        st.markdown(f"### {product['name']}")
        st.caption(f"옵션ID {product['option_id']}")
        a, b, c = st.columns(3)
        a.metric("판매단가", _money(sale))
        b.metric("마진", _money(margin))
        c.metric("마진률", _pct(margin_rate))

        rows = [
            {"항목": "판매단가", "단가": _money(result["sale"]["value"]), "비고": result["sale"]["note"]},
            {"항목": "상품원가", "단가": _money(result["cost"]["value"]), "비고": result["cost"]["note"]},
            {"항목": "수수료", "단가": _money(result["commission"]["value"]), "비고": result["commission"]["note"]},
            {"항목": "입출고배송비", "단가": _money(result["logistics"]["value"]), "비고": result["logistics"]["note"]},
            {"항목": "기타 쿠팡비용", "단가": _money(result["other"]["value"]), "비고": result["other"]["note"]},
            {"항목": "광고비", "단가": _money(result["ad"]["value"]), "비고": result["ad"]["note"]},
            {"항목": f"부가세({VAT_RATE * 100:.0f}%)", "단가": _money(result["vat"]["value"]), "비고": result["vat"]["note"]},
            {"항목": "마진", "단가": _money(result["margin"]["value"]), "비고": ""},
            {"항목": "마진률", "단가": _pct(result["margin_rate"]["value"]), "비고": result["margin_rate"]["note"]},
        ]
        st.markdown(_table(rows), unsafe_allow_html=True)
        if sale <= 0:
            st.warning("입력한 판매자료 전체를 확인했지만 이 상품의 신상품 판매자료를 찾지 못했습니다.")
        st.caption(
            f"수수료·입출고배송비 기준: {result['current_month']} 잠정실적 직접입력값 우선 · "
            f"없으면 {result['prev_month']} 정산 평균 · 수수료 기준도 없으면 잠정실적 자동 예상값 사용"
        )
