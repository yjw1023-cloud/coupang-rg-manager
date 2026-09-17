"""v0.9.206 quick Coupang product margin view.

Shows a 30-day sales-ranked product list on the left and the selected product's
unit economics on the right. The calculation rules remain the same as v0.9.205.
"""
from __future__ import annotations

from datetime import date, timedelta
import math
from typing import Any

import pandas as pd

PAGE_LABEL = "간략이익률"
VAT_RATE = 0.04


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").replace("개", "").replace("%", "").strip()
        x = float(v or 0)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0


def _nullable(v: Any):
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


def _oid(v: Any) -> str:
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


def _exists(c, table: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _month_shift(month: str, delta: int) -> str:
    y, m = [int(x) for x in str(month).split("-")]
    idx = y * 12 + (m - 1) + int(delta)
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _format_money(v: Any) -> str:
    return f"{int(round(_num(v))):,}원"


def _format_pct(v: Any) -> str:
    return f"{_num(v):,.1f}%"


def _alias_oids(core, db, product_id: int, option_id: str) -> set[str]:
    out = {_oid(option_id)} if _oid(option_id) else set()
    try:
        with core._conn(db) as c:
            if not _exists(c, "return_discount_aliases"):
                return out
            rows = c.execute(
                "SELECT discount_option_id FROM return_discount_aliases WHERE parent_product_id=?",
                (int(product_id),),
            ).fetchall()
        out.update(_oid(r["discount_option_id"]) for r in rows if _oid(r["discount_option_id"]))
    except Exception:
        pass
    return out


def _products(core, db) -> list[dict]:
    core.init_db(db)
    try:
        import product_visibility_v0995 as visibility
        hidden = set(visibility.hidden_ids(core, db))
    except Exception:
        hidden = set()
    aliases = set()
    with core._conn(db) as c:
        if _exists(c, "return_discount_aliases"):
            aliases = {_oid(r["discount_option_id"]) for r in c.execute("SELECT discount_option_id FROM return_discount_aliases") if _oid(r["discount_option_id"])}
        pc = _cols(c, "products")
        fields = ["id"]
        fields.append("item_code" if "item_code" in pc else "'' AS item_code")
        fields.append("option_id" if "option_id" in pc else "'' AS option_id")
        fields.append("name" if "name" in pc else "'' AS name")
        fields.append("item_type" if "item_type" in pc else "'' AS item_type")
        fields.append("unit_cost" if "unit_cost" in pc else "0 AS unit_cost")
        fields.append("active" if "active" in pc else "1 AS active")
        rows = c.execute("SELECT " + ",".join(fields) + " FROM products ORDER BY name,id").fetchall()
    out = []
    for r in rows:
        pid = int(r["id"]); oid = _oid(r["option_id"])
        if pid in hidden or not oid or oid in aliases or int(_num(r["active"])) == 0:
            continue
        if str(r["item_type"] or "").strip().lower() == "raw":
            continue
        out.append({"product_id": pid, "item_code": str(r["item_code"] or ""), "option_id": oid,
                    "name": str(r["name"] or "").strip() or oid, "unit_cost": max(0.0, _num(r["unit_cost"]))})
    return out


def _recent_30d_qty_map(core, db, products: list[dict]) -> dict[int, float]:
    """Rank visible parent products by the same recent sources used for sale price."""
    end = date.today(); start = end - timedelta(days=29)
    oid_to_pid = {}
    for p in products:
        for oid in _alias_oids(core, db, int(p["product_id"]), p["option_id"]):
            if oid:
                oid_to_pid[oid] = int(p["product_id"])
    qty = {int(p["product_id"]): 0.0 for p in products}
    api_found = set()
    try:
        with core._conn(db) as c:
            if _exists(c, "coupang_rg_order_items"):
                cols = _cols(c, "coupang_rg_order_items")
                if {"paid_date", "vendor_item_id", "sales_quantity"}.issubset(cols):
                    rows = c.execute(
                        """SELECT vendor_item_id,SUM(COALESCE(sales_quantity,0)) qty
                           FROM coupang_rg_order_items WHERE paid_date>=? AND paid_date<=?
                           GROUP BY vendor_item_id""",
                        (start.isoformat(), end.isoformat()),
                    ).fetchall()
                    for r in rows:
                        pid = oid_to_pid.get(_oid(r["vendor_item_id"]))
                        q = max(0.0, _num(r["qty"]))
                        if pid is not None and q > 0:
                            qty[pid] += q; api_found.add(pid)
    except Exception:
        pass
    # Use sales-stat quantity only for products without recent API quantity.
    try:
        with core._conn(db) as c:
            if _exists(c, "sales_stats") and _exists(c, "imports"):
                sc, ic = _cols(c, "sales_stats"), _cols(c, "imports")
                if {"product_id", "net_qty", "import_id"}.issubset(sc) and {"id", "data_type", "period_start", "period_end"}.issubset(ic):
                    rows = c.execute(
                        """SELECT s.product_id,SUM(COALESCE(s.net_qty,0)) qty
                           FROM sales_stats s JOIN imports i ON i.id=s.import_id
                           WHERE i.data_type='sales_stats' AND i.period_start>=? AND i.period_end<=?
                           GROUP BY s.product_id""",
                        (start.isoformat(), end.isoformat()),
                    ).fetchall()
                    for r in rows:
                        pid = int(_num(r["product_id"]))
                        if pid in qty and pid not in api_found:
                            qty[pid] = max(0.0, _num(r["qty"]))
    except Exception:
        pass
    return qty


def _latest_production_cost(core, db, product: dict) -> dict:
    pid = int(product["product_id"]); fallback = max(0.0, _num(product.get("unit_cost")))
    try:
        with core._conn(db) as c:
            if not _exists(c, "production_orders"): raise LookupError
            cols = _cols(c, "production_orders")
            if not {"parent_product_id", "produced_unit_cost"}.issubset(cols): raise LookupError
            date_expr = "production_date" if "production_date" in cols else "''"
            id_expr = "id" if "id" in cols else "rowid"
            row = c.execute(f"""SELECT {date_expr} production_date, produced_unit_cost FROM production_orders
                WHERE parent_product_id=? AND COALESCE(produced_unit_cost,0)>0
                ORDER BY {date_expr} DESC, {id_expr} DESC LIMIT 1""", (pid,)).fetchone()
        if row:
            cost = max(0.0, _num(row["produced_unit_cost"])); dt = str(row["production_date"] or "")[:10]
            if cost > 0: return {"value": cost, "note": f"{dt} 생산한 완제품 원가" if dt else "최근 생산한 완제품 원가"}
    except Exception:
        pass
    return {"value": fallback, "note": "최근 생산원가가 없어 품목관리의 현재 원가 사용" if fallback > 0 else "생산원가 자료 없음"}


def _recent_avg_sale_price(core, db, product: dict, oids: set[str]) -> dict:
    end = date.today(); start = end - timedelta(days=29); qty = amount = 0.0
    try:
        with core._conn(db) as c:
            if _exists(c, "coupang_rg_order_items") and {"paid_date", "vendor_item_id", "sales_quantity", "unit_sales_price"}.issubset(_cols(c, "coupang_rg_order_items")):
                rows = c.execute("SELECT vendor_item_id,sales_quantity,unit_sales_price FROM coupang_rg_order_items WHERE paid_date>=? AND paid_date<=?", (start.isoformat(), end.isoformat())).fetchall()
                for r in rows:
                    if _oid(r["vendor_item_id"]) not in oids: continue
                    q = max(0.0, _num(r["sales_quantity"])); p = max(0.0, _num(r["unit_sales_price"]))
                    if q > 0 and p > 0: qty += q; amount += q*p
        if qty > 0: return {"value": amount/qty, "qty": qty, "note": f"최근 30일 평균 판매단가 · 주문 API {int(round(qty)):,}개 기준"}
    except Exception:
        qty = amount = 0.0
    try:
        with core._conn(db) as c:
            if not (_exists(c, "sales_stats") and _exists(c, "imports")): raise LookupError
            sc, ic = _cols(c, "sales_stats"), _cols(c, "imports")
            if not {"product_id", "net_qty", "import_id", "displayed_net_sales"}.issubset(sc): raise LookupError
            if not {"id", "data_type", "period_start", "period_end"}.issubset(ic): raise LookupError
            r = c.execute("""SELECT SUM(COALESCE(s.net_qty,0)) qty,SUM(COALESCE(s.displayed_net_sales,0)) amount
                FROM sales_stats s JOIN imports i ON i.id=s.import_id WHERE s.product_id=? AND i.data_type='sales_stats'
                AND i.period_start>=? AND i.period_end<=?""", (int(product["product_id"]), start.isoformat(), end.isoformat())).fetchone()
        qty = _num(r["qty"]) if r else 0; amount = _num(r["amount"]) if r else 0
        if qty > 0 and amount > 0: return {"value": amount/qty, "qty": qty, "note": f"최근 30일 평균 판매단가 · 판매통계 {int(round(qty)):,}개 기준"}
    except Exception: pass
    return {"value": 0.0, "qty": 0.0, "note": "최근 30일 판매자료 없음"}


def _prior_month_qty(core, db, month: str, product: dict, oids: set[str]) -> float:
    try:
        with core._conn(db) as c:
            if not _exists(c, "settlement_sales"): return 0.0
            rows = c.execute("SELECT * FROM settlement_sales WHERE settlement_month=? ORDER BY id", (month,)).fetchall()
        dedup = {}
        for r in rows:
            keys=set(r.keys()); pid=int(_num(r["product_id"])) if "product_id" in keys else 0; oid=_oid(r["option_id"]) if "option_id" in keys else ""
            if pid != int(product["product_id"]) and oid not in oids: continue
            order_id=str(r["order_id"] or "") if "order_id" in keys else ""; typ=str(r["transaction_type"] or "") if "transaction_type" in keys else ""; rid=int(_num(r["id"])) if "id" in keys else len(dedup)
            dedup[(order_id or f"#{rid}",typ)] = r
        return sum(max(0.0,_num(r["qty"])) for r in dedup.values())
    except Exception: return 0.0


def _commission_and_logistics(core, db, product: dict, current_month: str, prev_month: str) -> tuple[dict,dict]:
    oid=product["option_id"]; pid=int(product["product_id"])
    try: import provisional_sales_basis_v09196 as basis
    except Exception: basis=None
    manual={}
    if basis is not None:
        try: manual=basis._manual(core,db,current_month).get(oid,{})
        except Exception: pass
    prior_comm=prior_logi=None
    if basis is not None:
        try:
            x=basis._prior_comm(core,db,current_month,oid,pid)
            if x and str(x.get("month") or "")==prev_month: prior_comm=x
        except Exception: pass
        try:
            x=basis._prior_logi(core,db,current_month,oid,pid)
            if x and str(x.get("month") or "")==prev_month: prior_logi=x
        except Exception: pass
    if prior_comm: commission={"value":max(0.0,_num(prior_comm.get("unit"))),"note":f"{prev_month} 정산기록의 평균 수수료"}
    else:
        v=_nullable(manual.get("commission_unit_override")); commission={"value":max(0.0,_num(v)) if v is not None else 0.0,"note":f"{current_month} 잠정실적에서 입력한 평균 수수료" if v is not None else f"{prev_month} 정산·잠정 수수료 입력값 없음"}
    if prior_logi: logistics={"value":max(0.0,_num(prior_logi.get("unit"))),"note":f"{prev_month} 정산기록의 평균 입출고배송비"}
    else:
        v=_nullable(manual.get("logistics_unit_override")); logistics={"value":max(0.0,_num(v)) if v is not None else 0.0,"note":f"{current_month} 잠정실적에서 입력한 평균 입출고배송비" if v is not None else f"{prev_month} 정산·잠정 입출고배송비 입력값 없음"}
    return commission,logistics


def _other_coupang_cost(core, db, product: dict, oids:set[str], prev_month:str, prev_qty:float)->dict:
    total=0.0
    try:
        with core._conn(db) as c:
            if _exists(c,"logistics_fees"):
                rows=c.execute("SELECT * FROM logistics_fees WHERE settlement_month=? ORDER BY id",(prev_month,)).fetchall(); dedup={}
                for r in rows:
                    keys=set(r.keys()); pid=int(_num(r["product_id"])) if "product_id" in keys else 0; oid=_oid(r["option_id"]) if "option_id" in keys else ""
                    if pid != int(product["product_id"]) and oid not in oids: continue
                    typ=str(r["fee_type"] or "") if "fee_type" in keys else ""
                    if typ in {"입출고비","배송비"}: continue
                    order_id=str(r["order_id"] or "") if "order_id" in keys else ""; rid=int(_num(r["id"])) if "id" in keys else len(dedup); dedup[(order_id or f"#{rid}",typ)]=r
                for r in dedup.values():
                    if "final_cost_prevat" in set(r.keys()): total += abs(_num(r["final_cost_prevat"]))
        if total>0 and prev_qty>0: return {"value":total/prev_qty,"note":f"{prev_month} 반품비 등 기타 쿠팡비용 ÷ 판매 {int(round(prev_qty)):,}개"}
    except Exception: pass
    try:
        mdf,_=core.confirmed_monthly_pnl(prev_month)
        if mdf is not None and not mdf.empty and prev_qty>0:
            total=0.0
            for _,r in mdf.iterrows():
                pid=int(_num(r.get("product_id"))) if "product_id" in mdf.columns else 0; oid=_oid(r.get("option_id")) if "option_id" in mdf.columns else ""
                if pid != int(product["product_id"]) and oid not in oids: continue
                total += abs(_num(r.get("return_pickup")))+abs(_num(r.get("return_restock")))
            if total>0: return {"value":total/prev_qty,"note":f"{prev_month} 확정 반품비 평균 · 판매 {int(round(prev_qty)):,}개 기준"}
    except Exception: pass
    return {"value":0.0,"note":f"{prev_month} 기타 쿠팡비용 기록 없음 → 0원"}


def _ad_unit_cost(core, db, product:dict, oids:set[str], prev_month:str, prev_qty:float)->dict:
    if prev_qty<=0: return {"value":0.0,"note":f"{prev_month} 판매수량이 없어 광고 단가 0원"}
    try:
        mdf,_=core.confirmed_monthly_pnl(prev_month)
        if mdf is not None and not mdf.empty and "ad_cost" in mdf.columns:
            total=0.0
            for _,r in mdf.iterrows():
                pid=int(_num(r.get("product_id"))) if "product_id" in mdf.columns else 0; oid=_oid(r.get("option_id")) if "option_id" in mdf.columns else ""
                if pid != int(product["product_id"]) and oid not in oids: continue
                total += abs(_num(r.get("ad_cost")))
            if total>0: return {"value":total/prev_qty,"note":f"{prev_month} 확정 광고비 {int(round(total)):,}원 ÷ 판매 {int(round(prev_qty)):,}개"}
    except Exception: pass
    try:
        import provisional_ad_report_v0956 as ad_report
        data=ad_report.load_month(core,prev_month,db); items=dict((data or {}).get("items") or {}); total=sum(abs(_num((items.get(oid) or {}).get("ad_spend"))) for oid in oids)
        if total>0: return {"value":total/prev_qty,"note":f"{prev_month} 상품 광고비 {int(round(total)):,}원 ÷ 판매 {int(round(prev_qty)):,}개"}
    except Exception: pass
    return {"value":0.0,"note":f"{prev_month} 상품 광고비 기록 없음 → 0원"}


def calculate(core, product:dict, db_path=None)->dict:
    db=db_path or core.DEFAULT_DB; current_month=date.today().strftime("%Y-%m"); prev_month=_month_shift(current_month,-1); oids=_alias_oids(core,db,int(product["product_id"]),product["option_id"])
    sale=_recent_avg_sale_price(core,db,product,oids); cost=_latest_production_cost(core,db,product); commission,logistics=_commission_and_logistics(core,db,product,current_month,prev_month); prev_qty=_prior_month_qty(core,db,prev_month,product,oids); other=_other_coupang_cost(core,db,product,oids,prev_month,prev_qty); ad=_ad_unit_cost(core,db,product,oids,prev_month,prev_qty); vat=max(0.0,_num(sale["value"]))*VAT_RATE
    margin=_num(sale["value"])-_num(cost["value"])-_num(commission["value"])-_num(logistics["value"])-_num(other["value"])-_num(ad["value"])-vat
    rate=margin/_num(sale["value"])*100.0 if _num(sale["value"])>0 else 0.0
    return {"sale":sale,"cost":cost,"commission":commission,"logistics":logistics,"other":other,"ad":ad,"vat":{"value":vat,"note":f"평균 판매단가의 {VAT_RATE*100:.0f}%"},"margin":{"value":margin,"note":"판매단가 - 상품원가 - 수수료 - 입출고배송비 - 기타 쿠팡비용 - 광고비 - 부가세"},"margin_rate":{"value":rate,"note":"마진 ÷ 평균 판매단가 × 100"},"prev_month":prev_month,"current_month":current_month}


def _render_margin_table(st_obj, result:dict):
    rows=[
        {"항목":"평균 판매단가","단가":_format_money(result["sale"]["value"]),"비고":result["sale"]["note"]},
        {"항목":"상품원가","단가":_format_money(result["cost"]["value"]),"비고":result["cost"]["note"]},
        {"항목":"수수료","단가":_format_money(result["commission"]["value"]),"비고":result["commission"]["note"]},
        {"항목":"입출고배송비","단가":_format_money(result["logistics"]["value"]),"비고":result["logistics"]["note"]},
        {"항목":"기타 쿠팡비용","단가":_format_money(result["other"]["value"]),"비고":result["other"]["note"]},
        {"항목":"광고비","단가":_format_money(result["ad"]["value"]),"비고":result["ad"]["note"]},
        {"항목":f"부가세({VAT_RATE*100:.0f}%)","단가":_format_money(result["vat"]["value"]),"비고":result["vat"]["note"]},
        {"항목":"마진","단가":_format_money(result["margin"]["value"]),"비고":result["margin"]["note"]},
        {"항목":"마진률","단가":_format_pct(result["margin_rate"]["value"]),"비고":result["margin_rate"]["note"]},
    ]
    html=['<div style="border:1px solid #d9e2ef;border-radius:12px;overflow:hidden;background:white">',
          '<table style="width:100%;border-collapse:collapse;text-align:center;font-size:14px">',
          '<thead><tr style="background:#f4f7fb;color:#425466"><th style="padding:11px;border-bottom:1px solid #d9e2ef;width:22%">항목</th><th style="padding:11px;border-bottom:1px solid #d9e2ef;width:20%">단가</th><th style="padding:11px;border-bottom:1px solid #d9e2ef">비고</th></tr></thead><tbody>']
    for i,r in enumerate(rows):
        bg='#fbfcfe' if i%2 else '#ffffff'; strong=r['항목'] in {'마진','마진률'}
        weight='700' if strong else '500'
        html.append(f'<tr style="background:{bg};font-weight:{weight}"><td style="padding:11px 8px;border-bottom:1px solid #e8edf4">{r["항목"]}</td><td style="padding:11px 8px;border-bottom:1px solid #e8edf4">{r["단가"]}</td><td style="padding:11px 8px;border-bottom:1px solid #e8edf4;color:#596579">{r["비고"]}</td></tr>')
    html.append('</tbody></table></div>')
    st_obj.markdown(''.join(html),unsafe_allow_html=True)


def render_page(st_obj, core, db_path=None):
    db=db_path or core.DEFAULT_DB
    st_obj.markdown("## 간략이익률")
    st_obj.caption("왼쪽 상품목록은 최근 30일 판매량 순입니다. 상품을 누르면 오른쪽에서 1개당 마진을 바로 확인할 수 있습니다.")
    try: products=_products(core,db)
    except Exception as exc: st_obj.error(f"쿠팡 판매상품 목록을 불러오지 못했습니다: {exc}"); return
    if not products: st_obj.info("선택할 쿠팡 판매상품이 없습니다."); return
    qty_map=_recent_30d_qty_map(core,db,products)
    products=sorted(products,key=lambda p:(-qty_map.get(int(p['product_id']),0.0),p['name'],p['option_id']))
    by_id={int(p['product_id']):p for p in products}
    state_key='simple_margin_selected_pid_v09206'
    selected=int(st_obj.session_state.get(state_key) or products[0]['product_id'])
    if selected not in by_id: selected=int(products[0]['product_id']); st_obj.session_state[state_key]=selected

    left,right=st_obj.columns([0.38,0.62],gap="large")
    with left:
        st_obj.markdown("### 쿠팡 판매상품")
        q=st_obj.text_input("상품검색",placeholder="상품명 또는 옵션ID",label_visibility="collapsed",key="simple_margin_search_v09206")
        words=[x.lower() for x in str(q or '').split() if x]
        shown=[]
        for rank,p in enumerate(products,1):
            hay=f"{p['name']} {p['option_id']} {p['item_code']}".lower()
            if words and not all(w in hay for w in words): continue
            shown.append((rank,p))
        st_obj.caption(f"최근 30일 판매량 내림차순 · {len(shown):,}개 상품")
        with st_obj.container(height=610,border=True):
            for rank,p in shown:
                pid=int(p['product_id']); qty=qty_map.get(pid,0.0); active=pid==selected
                label=f"{rank}. {p['name']}\n{int(round(qty)):,}개 · 옵션ID {p['option_id']}"
                if st_obj.button(label,key=f"simple_margin_pick_{pid}",use_container_width=True,type="primary" if active else "secondary"):
                    st_obj.session_state[state_key]=pid; st_obj.rerun()
        if not shown: st_obj.info("검색 결과가 없습니다.")

    product=by_id[selected]; result=calculate(core,product,db); sale=_num(result['sale']['value']); margin=_num(result['margin']['value']); rate=_num(result['margin_rate']['value'])
    with right:
        st_obj.markdown(f"### {product['name']}")
        st_obj.caption(f"옵션ID {product['option_id']} · 최근 30일 판매 {int(round(qty_map.get(selected,0.0))):,}개")
        c1,c2,c3=st_obj.columns(3)
        c1.metric("평균 판매단가",_format_money(sale)); c2.metric("마진",_format_money(margin)); c3.metric("마진률",_format_pct(rate))
        _render_margin_table(st_obj,result)
        if sale<=0: st_obj.warning("최근 30일 판매단가가 없어 마진률을 계산할 수 없습니다. 주문 API 또는 판매통계 자료를 확인해 주세요.")
        st_obj.caption(f"비용 기준월: {result['prev_month']} 정산 우선 · 정산값이 없으면 {result['current_month']} 잠정실적 수동입력값 사용")
