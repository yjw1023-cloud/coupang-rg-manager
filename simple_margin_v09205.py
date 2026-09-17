"""v0.9.209 quick Coupang product margin view."""
from __future__ import annotations
from datetime import date, timedelta
import html, math
import pandas as pd

PAGE_LABEL = "간략이익률"
VAT_RATE = 0.04


def _n(v):
    try:
        if isinstance(v, str): v = v.replace(",", "").replace("원", "").replace("개", "").replace("%", "").strip()
        x = float(v or 0); return x if math.isfinite(x) else 0.0
    except Exception: return 0.0


def _nullable(v):
    if v is None: return None
    try:
        if pd.isna(v): return None
    except Exception: pass
    try:
        x=float(v); return x if math.isfinite(x) else None
    except Exception: return None


def _oid(v):
    try:
        x=float(v)
        if math.isfinite(x) and abs(x-round(x))<1e-9: return str(int(round(x)))
    except Exception: pass
    s=str(v or "").strip()
    if s.upper().startswith("CP-"): s=s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _exists(c,t): return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone() is not None

def _cols(c,t): return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{t}")').fetchall()} if _exists(c,t) else set()

def _money(v): return f"{int(round(_n(v))):,}원"

def _pct(v): return f"{_n(v):,.1f}%"

def _qty(v):
    x=_n(v); return f"{int(round(x)):,}" if abs(x-round(x))<1e-9 else f"{x:,.1f}"

def _shift(month,delta):
    y,m=[int(x) for x in str(month).split("-")]; i=y*12+m-1+delta; return f"{i//12:04d}-{i%12+1:02d}"


def _aliases(core,db,pid,oid):
    out={_oid(oid)} if _oid(oid) else set()
    try:
        with core._conn(db) as c:
            if _exists(c,"return_discount_aliases"):
                out|={_oid(r["discount_option_id"]) for r in c.execute("SELECT discount_option_id FROM return_discount_aliases WHERE parent_product_id=?",(int(pid),)) if _oid(r["discount_option_id"])}
    except Exception: pass
    return out


def _products(core,db):
    core.init_db(db)
    try:
        import product_visibility_v0995 as visibility
        hidden=set(visibility.hidden_ids(core,db))
    except Exception: hidden=set()
    with core._conn(db) as c:
        alias_ids={_oid(r["discount_option_id"]) for r in c.execute("SELECT discount_option_id FROM return_discount_aliases")} if _exists(c,"return_discount_aliases") else set()
        pc=_cols(c,"products")
        fields=["id", "item_code" if "item_code" in pc else "'' item_code", "option_id" if "option_id" in pc else "'' option_id", "name" if "name" in pc else "'' name", "item_type" if "item_type" in pc else "'' item_type", "unit_cost" if "unit_cost" in pc else "0 unit_cost", "active" if "active" in pc else "1 active"]
        rows=c.execute("SELECT "+",".join(fields)+" FROM products ORDER BY name,id").fetchall()
    out=[]
    for r in rows:
        pid=int(r["id"]); oid=_oid(r["option_id"])
        if pid in hidden or not oid or oid in alias_ids or int(_n(r["active"]))==0 or str(r["item_type"] or "").lower()=="raw": continue
        out.append({"product_id":pid,"item_code":str(r["item_code"] or ""),"option_id":oid,"name":str(r["name"] or "").strip() or oid,"unit_cost":max(0,_n(r["unit_cost"]))})
    return out


def _rank_qty(core,db,products):
    end=date.today(); start=end-timedelta(days=29); oid_to_pid={}
    for p in products:
        for oid in _aliases(core,db,p["product_id"],p["option_id"]): oid_to_pid[oid]=p["product_id"]
    out={p["product_id"]:0.0 for p in products}; api=set()
    try:
        with core._conn(db) as c:
            if _exists(c,"coupang_rg_order_items") and {"paid_date","vendor_item_id","sales_quantity"}.issubset(_cols(c,"coupang_rg_order_items")):
                for r in c.execute("SELECT vendor_item_id,SUM(COALESCE(sales_quantity,0)) qty FROM coupang_rg_order_items WHERE paid_date>=? AND paid_date<=? GROUP BY vendor_item_id",(start.isoformat(),end.isoformat())):
                    pid=oid_to_pid.get(_oid(r["vendor_item_id"])); q=max(0,_n(r["qty"]))
                    if pid is not None and q>0: out[pid]+=q; api.add(pid)
    except Exception: pass
    try:
        with core._conn(db) as c:
            if _exists(c,"sales_stats") and _exists(c,"imports"):
                sc,ic=_cols(c,"sales_stats"),_cols(c,"imports")
                if {"product_id","net_qty","import_id"}.issubset(sc) and {"id","data_type","period_start","period_end"}.issubset(ic):
                    for r in c.execute("SELECT s.product_id,SUM(COALESCE(s.net_qty,0)) qty FROM sales_stats s JOIN imports i ON i.id=s.import_id WHERE i.data_type='sales_stats' AND i.period_start>=? AND i.period_end<=? GROUP BY s.product_id",(start.isoformat(),end.isoformat())):
                        pid=int(_n(r["product_id"]));
                        if pid in out and pid not in api: out[pid]=max(0,_n(r["qty"]))
    except Exception: pass
    return out


def _production_cost(core,db,p):
    fallback=max(0,_n(p.get("unit_cost")))
    try:
        with core._conn(db) as c:
            pc=_cols(c,"production_orders") if _exists(c,"production_orders") else set()
            if not {"parent_product_id","produced_unit_cost"}.issubset(pc): raise LookupError
            d="production_date" if "production_date" in pc else "''"; i="id" if "id" in pc else "rowid"
            r=c.execute(f"SELECT {d} production_date,produced_unit_cost FROM production_orders WHERE parent_product_id=? AND COALESCE(produced_unit_cost,0)>0 ORDER BY {d} DESC,{i} DESC LIMIT 1",(p["product_id"],)).fetchone()
        if r and _n(r["produced_unit_cost"])>0:
            dt=str(r["production_date"] or "")[:10]; return {"value":_n(r["produced_unit_cost"]),"note":f"{dt} 생산한 완제품 원가" if dt else "최근 생산한 완제품 원가"}
    except Exception: pass
    return {"value":fallback,"note":"최근 생산원가가 없어 품목관리의 현재 원가 사용" if fallback>0 else "생산원가 자료 없음"}


def _sales_imports_desc(core,db):
    try:
        with core._conn(db) as c:
            if not _exists(c,"imports"): return []
            ic=_cols(c,"imports"); fn="file_name" if "file_name" in ic else "''"; ps="period_start" if "period_start" in ic else "''"; pe="period_end" if "period_end" in ic else "''"
            rows=c.execute(f"SELECT id,{fn} file_name,{ps} period_start,{pe} period_end FROM imports WHERE data_type='sales_stats' ORDER BY id DESC").fetchall()
        out=[]
        for r in rows:
            a=str(r["period_start"] or "")[:10]; b=str(r["period_end"] or "")[:10]; month=(b[:7] or a[:7] or date.today().strftime("%Y-%m"))
            out.append({"id":int(r["id"]),"file_name":str(r["file_name"] or ""),"start":a,"end":b,"month":month})
        return out
    except Exception: return []


def _sale_price(core,db,p):
    imports=_sales_imports_desc(core,db)
    if not imports: return {"value":0,"qty":0,"month":date.today().strftime("%Y-%m"),"note":"입력된 판매자료 없음"}
    try:
        with core._conn(db) as c:
            sc=_cols(c,"sales_stats") if _exists(c,"sales_stats") else set()
            if not {"import_id","net_qty","displayed_net_sales"}.issubset(sc): raise LookupError
            for imp in imports:
                if "option_id" in sc:
                    r=c.execute("SELECT SUM(COALESCE(net_qty,0)) qty,SUM(COALESCE(displayed_net_sales,0)) amount FROM sales_stats WHERE import_id=? AND CAST(option_id AS TEXT)=?",(imp["id"],p["option_id"])).fetchone()
                else:
                    r=c.execute("SELECT SUM(COALESCE(net_qty,0)) qty,SUM(COALESCE(displayed_net_sales,0)) amount FROM sales_stats WHERE import_id=? AND product_id=?",(imp["id"],p["product_id"])).fetchone()
                q=max(0,_n(r["qty"] if r else 0)); a=max(0,_n(r["amount"] if r else 0))
                if q<=0 or a<=0:
                    continue
                period=f"{imp['start']}~{imp['end']}" if imp["start"] or imp["end"] else "최근 입력 판매자료"
                return {"value":a/q,"qty":q,"month":imp["month"],"note":f"{period} 판매자료 · 신상품 {_qty(q)}개 기준"}
    except Exception:
        pass
    newest=imports[0]
    return {"value":0,"qty":0,"month":newest["month"],"note":"입력된 판매자료 전체에서 이 상품의 신상품 판매 없음"}


def _prior_qty(core,db,month,p,oids):
    try:
        with core._conn(db) as c:
            if not _exists(c,"settlement_sales"): return 0
            rows=c.execute("SELECT * FROM settlement_sales WHERE settlement_month=? ORDER BY id",(month,)).fetchall()
        d={}
        for r in rows:
            k=set(r.keys()); pid=int(_n(r["product_id"])) if "product_id" in k else 0; oid=_oid(r["option_id"]) if "option_id" in k else ""
            if pid!=p["product_id"] and oid not in oids: continue
            order=str(r["order_id"] or "") if "order_id" in k else ""; typ=str(r["transaction_type"] or "") if "transaction_type" in k else ""; rid=int(_n(r["id"])) if "id" in k else len(d); d[(order or f"#{rid}",typ)]=r
        return sum(max(0,_n(r["qty"])) for r in d.values())
    except Exception: return 0


def _commission_logistics(core,db,p,current,prev):
    try: import provisional_sales_basis_v09196 as basis
    except Exception: basis=None
    manual={}
    if basis:
        try: manual=basis._manual(core,db,current).get(p["option_id"],{})
        except Exception: pass
    pc=pl=None
    if basis:
        try:
            x=basis._prior_comm(core,db,current,p["option_id"],p["product_id"])
            if x and str(x.get("month") or "")==prev: pc=x
        except Exception: pass
        try:
            x=basis._prior_logi(core,db,current,p["option_id"],p["product_id"])
            if x and str(x.get("month") or "")==prev: pl=x
        except Exception: pass

    manual_comm=_nullable(manual.get("commission_unit_override"))
    if manual_comm is not None:
        comm={"value":max(0,_n(manual_comm)),"note":f"{current} 잠정실적에서 직접 입력한 평균 수수료"}
    elif pc:
        comm={"value":max(0,_n(pc.get("unit"))),"note":f"{prev} 정산기록의 평균 수수료"}
    else:
        comm={"value":0,"note":f"{current} 잠정실적 입력값·{prev} 정산기록 없음"}

    manual_logi=_nullable(manual.get("logistics_unit_override"))
    if manual_logi is not None:
        logi={"value":max(0,_n(manual_logi)),"note":f"{current} 잠정실적에서 직접 입력한 평균 입출고배송비"}
    elif pl:
        logi={"value":max(0,_n(pl.get("unit"))),"note":f"{prev} 정산기록의 평균 입출고배송비"}
    else:
        logi={"value":0,"note":f"{current} 잠정실적 입력값·{prev} 정산기록 없음"}
    return comm,logi


def _other_cost(core,db,p,oids,prev,prev_qty):
    total=0
    try:
        with core._conn(db) as c:
            if _exists(c,"logistics_fees"):
                rows=c.execute("SELECT * FROM logistics_fees WHERE settlement_month=? ORDER BY id",(prev,)).fetchall(); d={}
                for r in rows:
                    k=set(r.keys()); pid=int(_n(r["product_id"])) if "product_id" in k else 0; oid=_oid(r["option_id"]) if "option_id" in k else ""
                    if pid!=p["product_id"] and oid not in oids: continue
                    typ=str(r["fee_type"] or "") if "fee_type" in k else ""
                    if typ in {"입출고비","배송비"}: continue
                    order=str(r["order_id"] or "") if "order_id" in k else ""; rid=int(_n(r["id"])) if "id" in k else len(d); d[(order or f"#{rid}",typ)]=r
                total=sum(abs(_n(r["final_cost_prevat"])) for r in d.values() if "final_cost_prevat" in set(r.keys()))
        if total>0 and prev_qty>0: return {"value":total/prev_qty,"note":f"{prev} 반품비 등 기타 쿠팡비용 ÷ 판매 {_qty(prev_qty)}개"}
    except Exception: pass
    return {"value":0,"note":f"{prev} 기타 쿠팡비용 기록 없음 → 0원"}


def _month_sales_qty(core,db,month,p):
    try:
        with core._conn(db) as c:
            if not (_exists(c,"sales_stats") and _exists(c,"imports")): return 0
            sc,ic=_cols(c,"sales_stats"),_cols(c,"imports"); qc="sales_qty" if "sales_qty" in sc else ("net_qty" if "net_qty" in sc else None)
            if not qc or not {"id","data_type","period_start","period_end"}.issubset(ic): return 0
            if "option_id" in sc: where="CAST(s.option_id AS TEXT)=?"; key=p["option_id"]
            else: where="s.product_id=?"; key=p["product_id"]
            r=c.execute(f"SELECT SUM(COALESCE(s.{qc},0)) qty FROM sales_stats s JOIN imports i ON i.id=s.import_id WHERE i.data_type='sales_stats' AND substr(i.period_start,1,7)=? AND substr(i.period_end,1,7)=? AND {where}",(month,month,key)).fetchone()
        return max(0,_n(r["qty"] if r else 0))
    except Exception: return 0


def _ad_cost(core,db,p,month,fallback_qty):
    try:
        import provisional_ad_report_v0956 as ad
        data=ad.load_month(core,month,db); item=(data.get("items") or {}).get(p["option_id"]) or {}; total=abs(_n(item.get("ad_spend")))
        if total<=0: return {"value":0,"note":f"{month} 잠정실적 광고보고서의 상품 광고비 없음 → 0원"}
        q=_month_sales_qty(core,db,month,p) or max(0,_n(fallback_qty))
        if q<=0: return {"value":0,"note":f"{month} 광고비 {_money(total)} · 판매수 없음"}
        return {"value":total/q,"note":f"{month} 잠정실적 광고보고서 {_money(total)} ÷ 판매 {_qty(q)}개"}
    except Exception: return {"value":0,"note":f"{month} 잠정실적 광고보고서 확인 불가 → 0원"}


def calculate(core,p,db_path=None):
    db=db_path or core.DEFAULT_DB; current=date.today().strftime("%Y-%m"); prev=_shift(current,-1); oids=_aliases(core,db,p["product_id"],p["option_id"])
    sale=_sale_price(core,db,p); cost=_production_cost(core,db,p); comm,logi=_commission_logistics(core,db,p,current,prev); pq=_prior_qty(core,db,prev,p,oids); other=_other_cost(core,db,p,oids,prev,pq); ad=_ad_cost(core,db,p,sale.get("month") or current,sale.get("qty")); vat=max(0,_n(sale["value"]))*VAT_RATE
    margin=_n(sale["value"])-_n(cost["value"])-_n(comm["value"])-_n(logi["value"])-_n(other["value"])-_n(ad["value"])-vat; rate=margin/_n(sale["value"])*100 if _n(sale["value"])>0 else 0
    return {"sale":sale,"cost":cost,"commission":comm,"logistics":logi,"other":other,"ad":ad,"vat":{"value":vat,"note":f"판매단가의 {VAT_RATE*100:.0f}%"},"margin":{"value":margin,"note":""},"margin_rate":{"value":rate,"note":"마진 ÷ 판매단가 × 100"},"prev_month":prev,"current_month":current}


def _table(rows):
    trs=[]
    for r in rows:
        label=html.escape(str(r["항목"])); cls=" class='hi'" if label in {"마진","마진률"} else ""; trs.append(f"<tr{cls}><td>{label}</td><td><b>{html.escape(str(r['단가']))}</b></td><td>{html.escape(str(r['비고'] or ''))}</td></tr>")
    return """<style>.smt{border:1px solid #d9e2ef;border-radius:12px;overflow:hidden;background:white}.smt table{width:100%;border-collapse:collapse;table-layout:fixed}.smt th{background:#edf3fb;color:#17324f;font-weight:800;padding:11px 8px;text-align:center;border-bottom:1px solid #ccd8e8}.smt td{padding:10px 8px;text-align:center;vertical-align:middle;border-bottom:1px solid #e3e9f1;color:#1e2d3d}.smt tr:nth-child(even) td{background:#f8fafc}.smt tr:last-child td{border-bottom:0}.smt th:nth-child(1),.smt td:nth-child(1){width:22%}.smt th:nth-child(2),.smt td:nth-child(2){width:22%}.smt th:nth-child(3),.smt td:nth-child(3){width:56%}.smt tr.hi td{background:#eef6ff!important;font-weight:700}</style><div class='smt'><table><thead><tr><th>항목</th><th>단가</th><th>비고</th></tr></thead><tbody>"""+"".join(trs)+"</tbody></table></div>"


def render_page(st,core,db_path=None):
    db=db_path or core.DEFAULT_DB; st.markdown("## 간략이익률"); st.caption("왼쪽 상품목록은 최근 30일 판매량순입니다. 판매단가는 입력한 판매자료 중 이 상품의 신상품 판매가 있는 가장 최근 자료를 사용합니다.")
    try: products=_products(core,db); rank=_rank_qty(core,db,products)
    except Exception as e: st.error(f"쿠팡 판매상품 목록을 불러오지 못했습니다: {e}"); return
    if not products: st.info("선택할 쿠팡 판매상품이 없습니다."); return
    products=sorted(products,key=lambda p:(-_n(rank.get(p["product_id"],0)),p["name"],p["option_id"])); key="simple_margin_product_v09205"; valid={p["product_id"] for p in products}; selected=int(st.session_state.get(key) or products[0]["product_id"])
    if selected not in valid: selected=products[0]["product_id"]; st.session_state[key]=selected
    left,right=st.columns([0.38,0.62],gap="large")
    with left:
        st.markdown("### 쿠팡 판매상품"); q=str(st.text_input("상품 검색",placeholder="상품명 또는 옵션ID",key="simple_margin_search_v09206",label_visibility="collapsed") or "").strip().lower(); visible=[p for p in products if not q or q in p["name"].lower() or q in p["option_id"].lower() or q in p["item_code"].lower()]
        if not visible: st.info("검색 결과가 없습니다.")
        else:
            try: box=st.container(height=690,border=True)
            except TypeError: box=st.container(border=True)
            with box:
                ranks={p["product_id"]:i+1 for i,p in enumerate(products)}
                for p in visible:
                    pid=p["product_id"]; label=f"{ranks[pid]}. {p['name']}\n30일 판매 {_qty(rank.get(pid,0))}개 · 옵션ID {p['option_id']}"
                    if st.button(label,key=f"simple_margin_pick_{pid}_v09206",use_container_width=True,type="primary" if pid==selected else "secondary"): st.session_state[key]=pid; st.rerun()
    p=next(x for x in products if x["product_id"]==selected); r=calculate(core,p,db); sale=_n(r["sale"]["value"]); margin=_n(r["margin"]["value"]); rate=_n(r["margin_rate"]["value"])
    with right:
        st.markdown(f"### {p['name']}"); st.caption(f"옵션ID {p['option_id']}"); a,b,c=st.columns(3); a.metric("판매단가",_money(sale)); b.metric("마진",_money(margin)); c.metric("마진률",_pct(rate))
        rows=[{"항목":"판매단가","단가":_money(r["sale"]["value"]),"비고":r["sale"]["note"]},{"항목":"상품원가","단가":_money(r["cost"]["value"]),"비고":r["cost"]["note"]},{"항목":"수수료","단가":_money(r["commission"]["value"]),"비고":r["commission"]["note"]},{"항목":"입출고배송비","단가":_money(r["logistics"]["value"]),"비고":r["logistics"]["note"]},{"항목":"기타 쿠팡비용","단가":_money(r["other"]["value"]),"비고":r["other"]["note"]},{"항목":"광고비","단가":_money(r["ad"]["value"]),"비고":r["ad"]["note"]},{"항목":f"부가세({VAT_RATE*100:.0f}%)","단가":_money(r["vat"]["value"]),"비고":r["vat"]["note"]},{"항목":"마진","단가":_money(r["margin"]["value"]),"비고":""},{"항목":"마진률","단가":_pct(r["margin_rate"]["value"]),"비고":r["margin_rate"]["note"]}]
        st.markdown(_table(rows),unsafe_allow_html=True)
        if sale<=0: st.warning("입력한 판매자료 전체를 확인했지만 이 상품의 신상품 판매자료를 찾지 못했습니다.")
        st.caption(f"수수료·입출고배송비 기준: {r['current_month']} 잠정실적 직접입력값 우선 · 없으면 {r['prev_month']} 정산 평균")