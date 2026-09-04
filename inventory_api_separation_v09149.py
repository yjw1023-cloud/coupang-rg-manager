"""v0.9.149: separate ERP book stock from Coupang orderable stock."""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any
import pandas as pd


def _num(v):
    try:
        x=float(v or 0); return 0.0 if pd.isna(x) else x
    except Exception: return 0.0


def _reverse_old_api_adjustments(core, db):
    """Keep old API-adjustment rows, add same-date opposite rows once."""
    result={"source_rows":0,"reversal_rows":0,"reversal_qty":0.0}
    try:
        with core._conn(db) as c:
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inventory_txns'").fetchone():
                return result
            cols={str(r["name"]) for r in c.execute("PRAGMA table_info(inventory_txns)")}
            need={"id","txn_date","product_id","warehouse_id","qty_delta","txn_type"}
            if not need.issubset(cols): return result
            ref="ref_no" if "ref_no" in cols else "'' AS ref_no"
            rows=c.execute(f"""SELECT id,txn_date,product_id,warehouse_id,qty_delta,txn_type,{ref}
                FROM inventory_txns
                WHERE txn_type IN ('쿠팡API재고조정','쿠팡API반품재고조정') ORDER BY id""").fetchall()
            result["source_rows"]=len(rows)
            for r in rows:
                rid=int(r["id"]); undo=f"V09149-UNDO-APIINV-{rid}"
                if "ref_no" in cols and c.execute("SELECT 1 FROM inventory_txns WHERE ref_no=?",(undo,)).fetchone():
                    continue
                qty=-_num(r["qty_delta"])
                if abs(qty)<=1e-12: continue
                typ="쿠팡API반품재고조정취소" if r["txn_type"]=="쿠팡API반품재고조정" else "쿠팡API재고조정취소"
                fields=["txn_date","product_id","warehouse_id","qty_delta","txn_type"]
                vals=[r["txn_date"],int(r["product_id"]),int(r["warehouse_id"]),qty,typ]
                if "ref_no" in cols: fields.append("ref_no"); vals.append(undo)
                if "memo" in cols: fields.append("memo"); vals.append(f"v0.9.149 API 재고대사 장부영향 제거 · 원거래 #{rid}")
                if "created_at" in cols:
                    fields.append("created_at"); vals.append(core.now_iso() if callable(getattr(core,"now_iso",None)) else "")
                c.execute(f"INSERT INTO inventory_txns ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",tuple(vals))
                result["reversal_rows"]+=1; result["reversal_qty"]+=qty
    except Exception as e: result["error"]=str(e)
    return result


def _api_lookup(core, ui, db):
    out={}
    try:
        with core._conn(db) as c:
            p={int(r["id"]): str(ui._display_code(r["item_code"],r["option_id"]) or "").strip()
               for r in c.execute("SELECT id,item_code,option_id FROM products")}
            rows=c.execute("""SELECT product_id,SUM(COALESCE(orderable_qty,0)) qty,MAX(synced_at) synced_at
                FROM coupang_rg_inventory
                WHERE product_id IS NOT NULL AND COALESCE(stock_type,'normal')='normal'
                GROUP BY product_id""").fetchall()
            for r in rows:
                code=p.get(int(r["product_id"]));
                if code: out[code]={"qty":_num(r["qty"]),"synced_at":str(r["synced_at"] or "")}
    except Exception: pass
    return out


def _patch_inventory_ui(core, ui, db):
    if getattr(ui,"_rg_api_book_split_v09149",False): return
    old_enrich,old_tab=ui._enrich_view,ui._tab_frame
    def enrich(df):
        view,item_master=old_enrich(df)
        if "품목코드" not in view.columns or "쿠팡RG" not in view.columns: return view,item_master
        lookup=_api_lookup(core,ui,db); view=view.copy()
        view["쿠팡RG 장부재고"]=pd.to_numeric(view["쿠팡RG"],errors="coerce").fillna(0)
        view["쿠팡 판매가능재고"]=pd.to_numeric(view["품목코드"].map(lambda x: lookup.get(str(x or '').strip(),{}).get("qty")),errors="coerce")
        view["재고차이"]=view["쿠팡 판매가능재고"]-view["쿠팡RG 장부재고"]
        view["API 조회시각"]=view["품목코드"].map(lambda x: lookup.get(str(x or '').strip(),{}).get("synced_at", ""))
        view=view.drop(columns=["쿠팡RG"])
        cols=list(view.columns)
        rgcols=["쿠팡RG 장부재고","쿠팡 판매가능재고","재고차이","API 조회시각"]
        cols=[x for x in cols if x not in rgcols]
        pos=cols.index("자체창고")+1 if "자체창고" in cols else len(cols)
        cols[pos:pos]=rgcols; view=view[[x for x in cols if x in view.columns]]
        for x in rgcols[:3]: view[x]=pd.to_numeric(view[x],errors="coerce").round().astype("Int64")
        return view,item_master
    def tab(df,warehouse,item_master):
        if warehouse!="쿠팡RG" or "쿠팡RG 장부재고" not in df.columns: return old_tab(df,warehouse,item_master)
        book=pd.to_numeric(df["쿠팡RG 장부재고"],errors="coerce").fillna(0)
        apiq=pd.to_numeric(df["쿠팡 판매가능재고"],errors="coerce")
        mask=df["구분"].fillna("").astype(str).eq("쿠팡RG") if item_master and "구분" in df.columns else ((book.abs()>1e-12)|(apiq.fillna(0).abs()>1e-12))
        base=["품목코드","상품명","기준원가"]
        if item_master:
            base += [x for x in ("최근생산원가","생산평균원가","원가상태") if x in df.columns]
        base += ["쿠팡RG 장부재고","쿠팡 판매가능재고","재고차이","API 조회시각"]
        out=df.loc[mask,[x for x in base if x in df.columns]].copy().rename(columns={"쿠팡RG 장부재고":"장부재고","재고차이":"차이(API-장부)"})
        if not item_master: out["재고금액"]=(pd.to_numeric(out["장부재고"],errors="coerce").fillna(0)*pd.to_numeric(out["기준원가"],errors="coerce").fillna(0)).round().astype("Int64")
        return out.sort_values(["상품명","품목코드"],kind="stable").reset_index(drop=True) if not out.empty else out
    ui._enrich_view=enrich
    ui._tab_frame=tab; ui._rg_api_book_split_v09149=True


def _patch_inventory_sync(api):
    if getattr(api,"_rg_inventory_read_only_v09149",False): return
    api._reconcile_warehouse_inventory=lambda *a,**k:{"adjusted_rows":0,"adjusted_qty":0.0}
    old=api.sync_inventory
    def sync(core,client,db_path=None):
        db=db_path or core.DEFAULT_DB; r=dict(old(core,client,db) or {})
        r.update({"book_inventory_unchanged":True,"adjusted_rows":0,"adjusted_qty":0.0,"normal_adjusted_rows":0,"normal_adjusted_qty":0.0,"return_adjusted_rows":0,"return_adjusted_qty":0.0})
        if r.get("run_id"):
            try:
                with core._conn(db) as c: c.execute("UPDATE coupang_api_sync_runs SET message=? WHERE id=?",(f"재고 {int(r.get('rows') or 0):,}개 저장 · 쿠팡 판매가능재고 스냅샷만 갱신 · ERP 장부재고 변경 없음",int(r["run_id"])))
            except Exception: pass
        return r
    api.sync_inventory=sync; api._rg_inventory_read_only_v09149=True


def _disable_provisional_api(api):
    api.provisional_months_from_api=lambda core,db_path=None:[]
    api.provisional_rows_from_api=lambda core,month,db_path=None:([],{"source":"sales_stats_excel_only","rows":0,"activity_rows":0,"matched_rows":0,"unmatched_rows":0,"covered_days":0,"expected_days":0,"return_rows":0,"return_matched_rows":0,"return_unmatched_rows":0,"return_price_missing_rows":0,"return_withdrawal_rows":0,"return_covered_days":0,"return_expected_days":0})


def _patch_api_page(api):
    def render(st,pd,core,db_path=None):
        db=db_path or core.DEFAULT_DB; api.ensure_schema(core,db)
        st.markdown("# 쿠팡 API 연동"); st.caption("판매수량은 판매통계 Excel로 입력합니다. API는 판매가능재고·매출수수료·지급내역 확인에 사용합니다.")
        saved=None
        try: saved=api.load_credentials(core)
        except Exception as e: st.error(str(e))
        with st.expander("API 연결정보",expanded=saved is None):
            v=st.text_input("판매자 ID",value=saved.vendor_id if saved else "",key="api_v149_v")
            a=st.text_input("Access Key",value=saved.access_key if saved else "",key="api_v149_a")
            s=st.text_input("Secret Key",value=saved.secret_key if saved else "",type="password",key="api_v149_s")
            c1,c2=st.columns(2)
            if c1.button("연결정보 저장",type="primary",use_container_width=True,key="api_v149_save"):
                try: api.save_credentials(core,api.Credentials(v,a,s)); st.success("API 연결정보를 저장했습니다.")
                except Exception as e: st.error(str(e))
            if c2.button("저장정보 삭제",use_container_width=True,key="api_v149_del"): api.delete_credentials(core); st.success("저장정보를 삭제했습니다.")
        with st.expander("정상상품 기준표",expanded=False):
            f=st.file_uploader("로켓그로스 입고 Excel",type=["xlsx"],key="api_v149_inbound")
            if st.button("G열 옵션 ID를 정상상품으로 등록",disabled=f is None,use_container_width=True,key="api_v149_reg"):
                try:
                    x=api.register_normal_options(core,api.parse_rg_inbound_options(f),getattr(f,"name",""),db); st.success(f"정상 등록 {x['registered']:,}개 · 제외 {x['unmatched']:,}개")
                except Exception as e: st.error(str(e))
        cred=api.Credentials(v,a,s) if v or a or s else saved
        try: cred.validate()
        except Exception: st.info("연결정보를 저장한 뒤 동기화하세요."); return
        client=api.CoupangClient(cred); today=date.today(); start=today-timedelta(days=7)
        with core._conn(db) as c:
            registry_count=len(api._normal_option_ids(c))
        if registry_count==0: st.warning("정상상품 기준표가 비어 있습니다. 재고 동기화 전에 로켓그로스 입고 Excel 등록을 권장합니다.")
        test_col,_=st.columns([1,2])
        if test_col.button("연결 확인",use_container_width=True,key="api_v149_test"):
            try:
                path=f"/v2/providers/rg_open_api/apis/api/v1/vendors/{cred.vendor_id}/rg/inventory/summaries"
                payload=client.request(path); msg=api._text(payload.get("message")) if isinstance(payload,dict) else "SUCCESS"
                st.success(f"쿠팡 API 연결 성공 · {msg or 'SUCCESS'}")
            except Exception as e: st.error(str(e))
        st.markdown("### 수동 동기화")
        d1,d2,d3=st.columns(3); start=d1.date_input("매출 조회 시작일",start,key="api_v149_start"); end=d2.date_input("매출 조회 종료일",today,key="api_v149_end"); month=d3.text_input("지급내역 정산월",today.strftime("%Y-%m"),key="api_v149_month")
        st.caption("재고 동기화는 쿠팡 판매가능재고 스냅샷만 갱신하며 ERP 장부재고를 수정하지 않습니다.")
        cols=st.columns(3); acts=[(cols[0],"재고",lambda:api.sync_inventory(core,client,db)),(cols[1],"매출·수수료",lambda:api.sync_revenue(core,client,start,end,db)),(cols[2],"지급내역",lambda:api.sync_settlements(core,client,month,db))]
        for col,label,fn in acts:
            if col.button(label+" 동기화",use_container_width=True,key="api_v149_"+label):
                try:
                    with st.spinner(label+" 자료를 가져오는 중입니다..."): r=fn()
                    suffix=" · ERP 장부재고 변경 없음" if label=="재고" else ""; st.success(f"{label} 완료: {int(r.get('rows') or 0):,}개 행 저장{suffix}")
                except Exception as e: st.error(str(e))
        if st.button("재고·매출·지급 전체 동기화",type="primary",use_container_width=True,key="api_v149_all"):
            try:
                api.sync_inventory(core,client,db); api.sync_revenue(core,client,start,end,db); api.sync_settlements(core,client,month,db); st.success("재고·매출·지급 동기화를 완료했습니다.")
            except Exception as e: st.error(str(e))
        st.info("주문 동기화와 반품·취소 동기화는 사용하지 않습니다. 잠정 판매자료와 판매차감은 판매통계 Excel 기준입니다.")
        sm=api._summary(core,db); st.markdown("### 연동 현황")
        c1,c2,c3=st.columns(3); c1.metric("쿠팡 재고 옵션",f"{sm['counts']['coupang_rg_inventory']:,}개"); c2.metric("매출·수수료 상품행",f"{sm['counts']['coupang_revenue_items']:,}개"); c3.metric("지급내역",f"{sm['counts']['coupang_settlement_histories']:,}건")
        missing_inv=int(sm["unmatched"].get("inventory",0)); missing_rev=int(sm["unmatched"].get("revenue",0))
        if missing_inv or missing_rev: st.warning(f"ERP 상품과 연결되지 않은 API 자료: 재고 {missing_inv:,}개 · 매출 {missing_rev:,}개")
        if sm.get("return_mapping_candidates"):
            with st.expander("미분류 재고 옵션을 반품상품으로 연결",expanded=False):
                st.caption("이 연결은 API 재고 분류에만 사용하며 ERP 장부재고를 변경하지 않습니다.")
                unknown={str(x["vendor_item_id"]):x for x in sm["return_mapping_candidates"]}; oid=st.selectbox("미분류 쿠팡 옵션",list(unknown),key="api_v149_unknown")
                products=api._return_mapping_products(core,db)
                if products:
                    by={int(x["id"]):x for x in products}; pid=st.selectbox("연결할 정상 원상품",list(by),format_func=lambda x:f"{by[x]['name']} · 옵션ID {by[x]['option_id']}",key="api_v149_parent")
                    if st.button("선택 옵션을 반품상품으로 연결",use_container_width=True,key="api_v149_map"):
                        api.save_return_mapping(core,oid,pid,unknown[oid].get("api_product_name") or "",db); st.success("반품상품 연결을 저장했습니다. 다음 재고 동기화부터 API 재고 분류에 반영됩니다.")
        if sm["inventory"]:
            with st.expander("최근 쿠팡 판매가능재고 보기",expanded=True):
                x=pd.DataFrame(sm["inventory"]).rename(columns={"vendor_item_id":"옵션ID","orderable_qty":"쿠팡 판매가능재고","sales_30d":"최근 30일 판매","synced_at":"API 조회시각","api_product_name":"쿠팡 상품명","erp_product_name":"ERP 원상품","stock_type":"재고구분"}); st.dataframe(x,use_container_width=True,hide_index=True)
        if sm["revenue"]:
            with st.expander("API 매출·수수료 월별 요약"):
                x=pd.DataFrame(sm["revenue"]).rename(columns={"month":"월","sales":"매출인식액","fee":"판매수수료(VAT포함)","settlement":"정산대상액"}); st.dataframe(x,use_container_width=True,hide_index=True)
                try:
                    cov=api._revenue_month_coverage(core,db,month); st.caption(f"{month} 확정손익 API 월전체 동기화 {cov['covered_days']}/{cov['expected_days']}일 · 미매칭 {cov['unmatched']:,}개")
                except Exception: pass
        if sm["settlements"]:
            with st.expander("최근 지급내역 보기"): st.dataframe(pd.DataFrame(sm["settlements"]),use_container_width=True,hide_index=True)
        allowed=[r for r in sm.get("last",[]) if str(r.get("sync_type")) in {"inventory","revenue","settlement"}]
        if allowed:
            with st.expander("최근 동기화 이력",expanded=True): st.dataframe(pd.DataFrame(allowed),use_container_width=True,hide_index=True)
    api.render_page=render
    old=getattr(api,"_rg_patch_source_before_v09149",None) or api.patch_source; api._rg_patch_source_before_v09149=old
    def patch_source(source):
        out=old(source); return out.replace('st.sidebar.caption("v0.9.148 · 당월 잠정실적 초기화")','st.sidebar.caption("v0.9.149 · 장부재고·쿠팡재고 분리")')
    api.patch_source=patch_source


def apply(core,api,inventory_ui,db_path=None):
    db=db_path or core.DEFAULT_DB; api.ensure_schema(core,db)
    rev=_reverse_old_api_adjustments(core,db); _patch_inventory_sync(api); _disable_provisional_api(api); _patch_api_page(api); _patch_inventory_ui(core,inventory_ui,db)
    return {"api_inventory_read_only":True,"provisional_sales_source":"sales_stats_excel","historical_api_adjustment_rows_preserved":int(rev.get("source_rows") or 0),"historical_api_adjustment_reversal_rows":int(rev.get("reversal_rows") or 0),"historical_api_adjustment_reversal_qty":_num(rev.get("reversal_qty")),"error":str(rev.get("error") or "")}
