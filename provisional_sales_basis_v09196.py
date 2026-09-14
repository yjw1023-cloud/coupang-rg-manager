"""v0.9.196 provisional P&L: sales-file revenue + monthly manual unit costs."""
from __future__ import annotations
import math
from typing import Any
import pandas as pd
import streamlit as st

RULE_VERSION = "0.9.196-sales-file-revenue-manual-units"


def _n(v: Any) -> float:
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


def _oid(v) -> str:
    try:
        x = float(v)
        if math.isfinite(x) and abs(x-round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v or "").strip()
    return s[3:] if s.upper().startswith("CP-") else (s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s)


def _schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS provisional_month_unit_overrides(
            month TEXT NOT NULL, option_id TEXT NOT NULL,
            commission_unit_override REAL, logistics_unit_override REAL,
            updated_at TEXT NOT NULL, PRIMARY KEY(month,option_id))""")


def _month(core, db, import_id):
    try:
        with core._conn(db) as c:
            r = c.execute("SELECT data_type,period_start,period_end FROM imports WHERE id=?", (int(import_id),)).fetchone()
        s, e = str(r["period_start"] or "")[:7], str(r["period_end"] or "")[:7]
        return s if r and r["data_type"] == "sales_stats" and len(s) == 7 and s == e else None
    except Exception:
        return None


def _sales(core, db, import_id):
    try:
        with core._conn(db) as c:
            rows = c.execute("""SELECT s.product_id,s.option_id,
                SUM(COALESCE(s.displayed_net_sales,0)) revenue,
                SUM(COALESCE(s.net_qty,0)) qty
                FROM sales_stats s WHERE s.import_id=?
                GROUP BY s.product_id,s.option_id""", (int(import_id),)).fetchall()
    except Exception:
        return {}
    out = {}
    for r in rows:
        d = {"pid": int(r["product_id"]), "oid": _oid(r["option_id"]), "revenue": _n(r["revenue"]), "qty": _n(r["qty"])}
        out[("p", d["pid"])] = d
        if d["oid"]:
            out[("o", d["oid"])] = d
    return out


def _manual(core, db, month):
    _schema(core, db)
    with core._conn(db) as c:
        rows = c.execute("SELECT * FROM provisional_month_unit_overrides WHERE month=?", (month,)).fetchall()
    return {str(r["option_id"]): dict(r) for r in rows}


def _save(core, db, month, oid, comm, logi):
    _schema(core, db)
    comm, logi = _nullable(comm), _nullable(logi)
    with core._conn(db) as c:
        if comm is None and logi is None:
            c.execute("DELETE FROM provisional_month_unit_overrides WHERE month=? AND option_id=?", (month, oid))
        else:
            c.execute("""INSERT INTO provisional_month_unit_overrides
                (month,option_id,commission_unit_override,logistics_unit_override,updated_at)
                VALUES(?,?,?,?,?) ON CONFLICT(month,option_id) DO UPDATE SET
                commission_unit_override=excluded.commission_unit_override,
                logistics_unit_override=excluded.logistics_unit_override,
                updated_at=excluded.updated_at""", (month, oid, comm, logi, core.now_iso()))


def _pid(core, db, oid):
    try:
        with core._conn(db) as c:
            r = c.execute("SELECT id FROM products WHERE option_id=? ORDER BY active DESC,id DESC LIMIT 1", (oid,)).fetchone()
        return int(r["id"]) if r else None
    except Exception:
        return None


def _prior_comm(core, db, month, oid, pid=None):
    try:
        with core._conn(db) as c:
            if oid:
                r = c.execute("SELECT MAX(settlement_month) m FROM settlement_sales WHERE settlement_month<? AND option_id=? AND COALESCE(qty,0)>0", (month,oid)).fetchone()
            else:
                r = c.execute("SELECT MAX(settlement_month) m FROM settlement_sales WHERE settlement_month<? AND product_id=? AND COALESCE(qty,0)>0", (month,pid)).fetchone()
            m = str(r["m"] or "") if r else ""
            if not m:
                return None
            if oid:
                rows = c.execute("SELECT order_id,transaction_type,qty,commission_total,commission,commission_vat,id FROM settlement_sales WHERE settlement_month=? AND option_id=? AND COALESCE(qty,0)>0 ORDER BY id", (m,oid)).fetchall()
            else:
                rows = c.execute("SELECT order_id,transaction_type,qty,commission_total,commission,commission_vat,id FROM settlement_sales WHERE settlement_month=? AND product_id=? AND COALESCE(qty,0)>0 ORDER BY id", (m,pid)).fetchall()
    except Exception:
        return None
    dedup = {}
    for r in rows:
        key = (str(r["order_id"] or "") or f"#{r['id']}", str(r["transaction_type"] or ""))
        dedup[key] = r
    q = fee = 0.0
    for r in dedup.values():
        qty = _n(r["qty"])
        total = _nullable(r["commission_total"])
        if total is None:
            total = _n(r["commission"]) + _n(r["commission_vat"])
        if qty > 0:
            q += qty; fee += max(0.0, _n(total))
    return {"unit": fee/q, "month": m} if q > 0 else None


def _prior_logi(core, db, month, oid, pid=None):
    try:
        with core._conn(db) as c:
            if oid:
                r = c.execute("SELECT MAX(settlement_month) m FROM logistics_fees WHERE settlement_month<? AND option_id=? AND fee_type IN ('입출고비','배송비')", (month,oid)).fetchone()
            else:
                r = c.execute("SELECT MAX(settlement_month) m FROM logistics_fees WHERE settlement_month<? AND product_id=? AND fee_type IN ('입출고비','배송비')", (month,pid)).fetchone()
            m = str(r["m"] or "") if r else ""
            if not m:
                return None
            if oid:
                rows = c.execute("SELECT id,order_id,fee_type,qty,final_cost_prevat FROM logistics_fees WHERE settlement_month=? AND option_id=? AND fee_type IN ('입출고비','배송비') ORDER BY id", (m,oid)).fetchall()
            else:
                rows = c.execute("SELECT id,order_id,fee_type,qty,final_cost_prevat FROM logistics_fees WHERE settlement_month=? AND product_id=? AND fee_type IN ('입출고비','배송비') ORDER BY id", (m,pid)).fetchall()
    except Exception:
        return None
    units = {}
    for typ in ("입출고비", "배송비"):
        d = {}
        for r in rows:
            if r["fee_type"] != typ: continue
            d[str(r["order_id"] or "") or f"#{r['id']}"] = r
        q = cost = 0.0
        for r in d.values():
            qty, v = abs(_n(r["qty"])), _n(r["final_cost_prevat"])
            if qty > 0 and v >= 0:
                q += qty; cost += v
        units[typ] = cost/q if q > 0 else 0.0
    return {"inout": units.get("입출고비",0.0), "delivery": units.get("배송비",0.0), "unit": sum(units.values()), "month": m}


def _existing(row, qty):
    if abs(qty) <= 1e-12:
        return None, None, None, None
    c = abs(_n(row.get("expected_commission")))/abs(qty) if "expected_commission" in row.index else 0
    i = abs(_n(row.get("expected_inout")))/abs(qty) if "expected_inout" in row.index else 0
    d = abs(_n(row.get("expected_delivery")))/abs(qty) if "expected_delivery" in row.index else 0
    return (c if c > 0 else None, i+d if i+d > 0 else None, i if i+d > 0 else None, d if i+d > 0 else None)


def _units(core, db, month, oid, pid, saved=None, old=None):
    saved, old = saved or {}, old or (None,None,None,None)
    mc, ml = _nullable(saved.get("commission_unit_override")), _nullable(saved.get("logistics_unit_override"))
    pc, pl = _prior_comm(core,db,month,oid,pid), _prior_logi(core,db,month,oid,pid)
    if mc is not None:
        cu, cs = max(0,mc), f"{month} 수동"
    elif pc:
        cu, cs = max(0,_n(pc["unit"])), f"{pc['month']} 정산"
    else:
        cu, cs = old[0], ("기존 자동값" if old[0] is not None else "정산이력 없음")
    if ml is not None:
        total = max(0,ml)
        base_total = _n(pl.get("unit")) if pl else (_n(old[1]) if old[1] is not None else 0)
        base_in = _n(pl.get("inout")) if pl else (_n(old[2]) if old[2] is not None else 0)
        ratio = base_in/base_total if base_total > 0 else 1.0
        iu, du, lu, ls = total*ratio, total*(1-ratio), total, f"{month} 수동"
    elif pl:
        iu, du, lu, ls = _n(pl["inout"]), _n(pl["delivery"]), _n(pl["unit"]), f"{pl['month']} 정산"
    else:
        iu, du, lu, ls = old[2], old[3], old[1], ("기존 자동값" if old[1] is not None else "정산이력 없음")
    return {"comm":cu,"comm_source":cs,"logi":lu,"inout":iu,"delivery":du,"logi_source":ls}


def _fix_raw(core, db, import_id, raw):
    month = _month(core,db,import_id)
    if raw is None or getattr(raw,"empty",True): return raw,{"month":month,"products":{}}
    sales, manual = _sales(core,db,import_id), (_manual(core,db,month) if month else {})
    out, diag = raw.copy(), {}
    for idx in out.index:
        pid = int(_n(out.at[idx,"product_id"])) if "product_id" in out.columns else 0
        oid = _oid(out.at[idx,"option_id"]) if "option_id" in out.columns else ""
        s = sales.get(("p",pid)) or sales.get(("o",oid))
        q = _n(s["qty"]) if s else (_n(out.at[idx,"net_qty"]) if "net_qty" in out.columns else 0)
        if s:
            rev = _n(s["revenue"])
            if "net_qty" in out.columns: out.at[idx,"net_qty"] = q
            if "expected_revenue" in out.columns: out.at[idx,"expected_revenue"] = rev
            if "expected_unit_price" in out.columns: out.at[idx,"expected_unit_price"] = rev/q if abs(q)>1e-12 else 0
        u = _units(core,db,month,oid,pid,manual.get(oid),_existing(out.loc[idx],q)) if month and oid else None
        if u:
            if u["comm"] is not None and "expected_commission" in out.columns: out.at[idx,"expected_commission"] = q*u["comm"]
            if u["logi"] is not None:
                iu,du = _n(u["inout"]),_n(u["delivery"])
                if "expected_inout" in out.columns: out.at[idx,"expected_inout"] = q*iu
                if "expected_delivery" in out.columns: out.at[idx,"expected_delivery"] = q*du
            diag[oid] = u
        needed=("expected_revenue","cogs","expected_commission","expected_inout","expected_delivery","expected_return_reserve","ad_spend")
        if all(c in out.columns for c in needed):
            rev=_n(out.at[idx,"expected_revenue"])
            p0=rev-_n(out.at[idx,"cogs"])-_n(out.at[idx,"expected_commission"])-_n(out.at[idx,"expected_inout"])-_n(out.at[idx,"expected_delivery"])-_n(out.at[idx,"expected_return_reserve"])
            if "profit_ex_ad" in out.columns: out.at[idx,"profit_ex_ad"] = p0
            p=p0-_n(out.at[idx,"ad_spend"])
            if "profit" in out.columns: out.at[idx,"profit"] = p
            if "margin_pct" in out.columns: out.at[idx,"margin_pct"] = p/rev*100 if abs(rev)>1e-12 else 0
    return out,{"month":month,"products":diag}


def _view(data):
    return data if isinstance(data,pd.DataFrame) else getattr(data,"data",None)


def _is_pnl(df):
    return isinstance(df,pd.DataFrame) and not df.empty and {"옵션ID","상품명","판매수량","예상매출","예상이익"}.issubset(df.columns)


def _editor(core,db,month,df,nonce):
    current=_manual(core,db,month); rows=[]
    qtycol="순판매수량" if "순판매수량" in df.columns else "판매수량"
    for _,r in df.iterrows():
        oid=_oid(r.get("옵션ID"));
        if not oid: continue
        pid=_pid(core,db,oid); saved=current.get(oid,{})
        u=_units(core,db,month,oid,pid,saved)
        q,rev=_n(r.get(qtycol)),_n(r.get("예상매출"))
        rows.append((oid,str(r.get("상품명") or ""),rev/q if abs(q)>1e-12 else 0,u,saved))
    if not rows: return
    missing=sum(1 for x in rows if x[3]["comm"] is None or x[3]["logi"] is None)
    with st.expander(f"잠정 비용 수동입력 · {month}"+(f" · 미입력/이력없음 {missing}개" if missing else ""),expanded=bool(missing)):
        st.caption("판매단가는 판매자료의 순 판매 금액÷순판매수량을 사용합니다. 평균 수수료·평균 입출고배송비는 이 달에만 수동 적용하며, 월말 정산값은 다음 달부터 자동 기본값으로 사용합니다.")
        q=st.text_input("상품 검색",placeholder="상품명 또는 옵션ID",key=f"rg196_q_{month}_{nonce}").strip().lower()
        arr=[x for x in rows if not q or q in (x[1]+" "+x[0]).lower()]
        if not arr: st.info("검색 결과가 없습니다."); return
        labels=[f"{x[1]} [{x[0]}]" for x in arr]; label=st.selectbox("수정할 상품",labels,key=f"rg196_p_{month}_{nonce}")
        oid,name,price,u,saved=arr[labels.index(label)]
        sc,sl=_nullable(saved.get("commission_unit_override")),_nullable(saved.get("logistics_unit_override"))
        st.caption(f"판매자료 단가 {int(round(price)):,}원 · 수수료 {int(round(_n(u['comm']))):,}원/개 ({u['comm_source']}) · 입출고배송비 {int(round(_n(u['logi']))):,}원/개 ({u['logi_source']})")
        c1,c2=st.columns(2)
        uc=c1.checkbox("평균 수수료 수동적용",value=sc is not None,key=f"rg196_uc_{month}_{oid}_{nonce}")
        cv=c1.number_input("평균 수수료 (원/개)",min_value=0,value=int(round(sc if sc is not None else _n(u["comm"]))),step=10,disabled=not uc,key=f"rg196_c_{month}_{oid}_{nonce}")
        ul=c2.checkbox("평균 입출고배송비 수동적용",value=sl is not None,key=f"rg196_ul_{month}_{oid}_{nonce}")
        lv=c2.number_input("평균 입출고배송비 (원/개)",min_value=0,value=int(round(sl if sl is not None else _n(u["logi"]))),step=10,disabled=not ul,key=f"rg196_l_{month}_{oid}_{nonce}")
        b1,b2=st.columns(2)
        if b1.button("이 상품 잠정비용 저장",type="primary",key=f"rg196_s_{month}_{oid}_{nonce}"):
            _save(core,db,month,oid,float(cv) if uc else None,float(lv) if ul else None); st.success("저장했습니다."); st.rerun()
        if oid in current and b2.button("수동값 삭제 · 자동값 사용",key=f"rg196_d_{month}_{oid}_{nonce}"):
            _save(core,db,month,oid,None,None); st.success("수동값을 삭제했습니다."); st.rerun()


def _decorate(core,db,month,df):
    out=df.copy(); current=_manual(core,db,month); qtycol="순판매수량" if "순판매수량" in out.columns else "판매수량"
    for idx in out.index:
        oid=_oid(out.at[idx,"옵션ID"]); q=_n(out.at[idx,qtycol]); rev=_n(out.at[idx,"예상매출"])
        if "예상 실현단가" in out.columns and abs(q)>1e-12: out.at[idx,"예상 실현단가"] = rev/q
        u=_units(core,db,month,oid,_pid(core,db,oid),current.get(oid,{}))
        if "평균 수수료" in out.columns and u["comm"] is not None: out.at[idx,"평균 수수료"] = u["comm"]
        if "평균 입출고배송비" in out.columns and u["logi"] is not None: out.at[idx,"평균 입출고배송비"] = u["logi"]
    return out


def apply(core,db_path=None):
    db=db_path or core.DEFAULT_DB; _schema(core,db)
    if not getattr(core,"_rg_provisional_sales_basis_v09196_installed",False):
        base=core.estimated_pnl
        def estimated_pnl(sales_import_id,ad_import_id=None,db_path=None):
            active=db_path or core.DEFAULT_DB
            raw,meta=(base(sales_import_id,ad_import_id) if db_path is None else base(sales_import_id,ad_import_id,db_path))
            fixed,ctx=_fix_raw(core,active,int(sales_import_id),raw); meta=dict(meta or {})
            meta["provisional_sales_basis_rule"]=RULE_VERSION; meta["provisional_sales_basis"]=ctx
            core._rg_last_provisional_context_v09196=ctx
            return fixed,meta
        estimated_pnl._rg_rule_version_v09196=RULE_VERSION; core.estimated_pnl=estimated_pnl
        core._rg_provisional_sales_basis_v09196_installed=True
    if not getattr(st,"_rg_provisional_sales_basis_v09196",False):
        previous=st.dataframe
        def dataframe(data=None,*args,**kwargs):
            df=_view(data)
            if not _is_pnl(df): return previous(data,*args,**kwargs)
            month=str((getattr(core,"_rg_last_provisional_context_v09196",{}) or {}).get("month") or "")
            if not month: return previous(data,*args,**kwargs)
            nonce=str(id(df)); _editor(core,db,month,df,nonce); decorated=_decorate(core,db,month,df)
            if not isinstance(data,pd.DataFrame):
                try: decorated=decorated.style.set_properties(**{"text-align":"center"})
                except Exception: pass
            return previous(decorated,*args,**kwargs)
        st.dataframe=dataframe; st._rg_provisional_sales_basis_v09196=True
    core._rg_provisional_sales_basis_v09196_applied=True
    return {"ok":True,"rule_version":RULE_VERSION}
