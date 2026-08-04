import hashlib, os, re, sqlite3, tempfile
from pathlib import Path
from datetime import date
import pandas as pd

SYS='claude_erp'

def txt(v):
    try:
        if v is None or pd.isna(v): return ''
    except: pass
    return str(v).strip()

def num(v):
    try: return float(v or 0)
    except: return 0.0

def ensure(core,db=None):
    db=db or core.DEFAULT_DB; core.init_db(db)
    with core._conn(db) as c: c.executescript('''
    CREATE TABLE IF NOT EXISTS legacy_v07_mappings(source_system TEXT,legacy_item_code TEXT,legacy_item_name TEXT,legacy_item_type TEXT,product_id INTEGER,match_method TEXT,updated_at TEXT,PRIMARY KEY(source_system,legacy_item_code));
    CREATE TABLE IF NOT EXISTS legacy_v07_runs(id INTEGER PRIMARY KEY AUTOINCREMENT,source_hash TEXT UNIQUE,source_name TEXT,imported_at TEXT,products_created INTEGER,purchases_imported INTEGER,bom_imported INTEGER,production_imported INTEGER,inventory_adjustments INTEGER);
    CREATE TABLE IF NOT EXISTS legacy_v07_purchase_links(legacy_id INTEGER PRIMARY KEY,purchase_line_id INTEGER,imported_at TEXT);
    CREATE TABLE IF NOT EXISTS legacy_v07_production_links(legacy_id INTEGER PRIMARY KEY,production_order_id INTEGER,imported_at TEXT);
    ''')

def analyze(path):
    p=Path(os.path.expandvars(path.strip().strip('"'))); p=p/'erp.db' if p.is_dir() else p
    if not p.exists(): raise FileNotFoundError(f'ERP DB를 찾지 못했습니다: {p}')
    h=hashlib.sha256(p.read_bytes()+(Path(str(p)+'-wal').read_bytes() if Path(str(p)+'-wal').exists() else b'')).hexdigest()
    fd,tmp=tempfile.mkstemp(suffix='.db'); os.close(fd)
    src=sqlite3.connect(str(p),timeout=8); dst=sqlite3.connect(tmp); src.backup(dst); src.close(); dst.close()
    c=sqlite3.connect(tmp)
    try:
        names={x[0] for x in c.execute("select name from sqlite_master where type='table'")}
        need={'items','purchases','bom','production','inventory'}
        if not need<=names: raise ValueError('기존 ERP 필수 테이블이 없습니다: '+','.join(sorted(need-names)))
        T={t:pd.read_sql_query(f'select * from {t}',c) if t in names else pd.DataFrame() for t in ['items','purchases','bom','production','inventory','sales']}
    finally: c.close(); os.unlink(tmp)
    latest=[]
    for t,col in [('purchases','purchase_date'),('production','production_date'),('sales','sale_date')]:
        if not T[t].empty:
            d=pd.to_datetime(T[t][col],errors='coerce').dropna()
            if len(d): latest.append(d.max().date().isoformat())
    return {'hash':h,'name':str(p),'T':T,'latest':max(latest) if latest else '-'}

def prep(snap,core,pur,db=None):
    db=db or core.DEFAULT_DB; ensure(core,db); pur.ensure_schema(db)
    with core._conn(db) as c:
        P=pd.read_sql_query('select id,item_code,option_id,name,option_name,item_type,unit_cost from products',c)
        M={str(r['legacy_item_code']):int(r['product_id']) for r in c.execute('select legacy_item_code,product_id from legacy_v07_mappings where source_system=?',(SYS,))}
    byc={txt(r.item_code):int(r.id) for _,r in P.iterrows()}; byo={txt(r.option_id):int(r.id) for _,r in P.iterrows() if txt(r.option_id)}; byid={int(r.id):r for _,r in P.iterrows()}
    rows=[]; C={}
    for _,r in snap['T']['items'].sort_values(['item_type','item_name']).iterrows():
        code,name,typ=txt(r.item_code),txt(r.item_name),txt(r.item_type); pid=None; act='review'; method='review'; status='확인 필요'; score=0
        if code in M and M[code] in byid: pid=M[code]; act='existing'; method='remembered'; status='기억된 연결'; score=1
        elif code in byc: pid=byc[code]; act='existing'; method='item_code'; status='코드 일치'; score=1
        elif code in byo: pid=byo[code]; act='existing'; method='option_id'; status='옵션ID 일치'; score=1
        elif typ=='raw': act='create'; method='create_raw'; status='신규 원재료'; score=1
        elif re.fullmatch(r'\d{8,15}',code or ''): act='create'; method='create_option'; status='옵션ID 신규등록'; score=1
        else:
            m=pur.purchase_match_candidates(name,'',6,db); cand=m.get('candidates',pd.DataFrame()); C[code]=cand
            if m.get('selected_product_id') and m.get('status') in ('auto','remembered'):
                pid=int(m['selected_product_id']); act='existing'; method='name_auto'; status='상품명 자동매칭'; score=float(cand.iloc[0].get('score',0)) if len(cand) else 0
            else: score=float(cand.iloc[0].get('score',0)) if len(cand) else 0
        dname=''; opt=''
        if pid in byid: dname=txt(byid[pid]['name']); opt=txt(byid[pid]['option_id'])
        elif act=='create': dname=name; opt=code if method=='create_option' else ''
        rows.append(dict(code=code,name=name,typ=typ,status=status,action=act,method=method,pid=pid,dest=dname,opt=opt,score=score,active=int(num(r.get('is_active',1)))))
    return pd.DataFrame(rows),C,P

def _new_product(c,r,core):
    code=txt(r.code); opt=code if r.method=='create_option' and code.isdigit() else None; base=('CP-'+code) if opt else code
    item=base; n=1
    while c.execute('select 1 from products where item_code=?',(item,)).fetchone(): n+=1; item=f'LEGACY-{base}-{n}'
    q=c.execute('insert into products(item_code,option_id,name,item_type,unit_cost,active,updated_at) values(?,?,?,?,0,?,?)',(item,opt,txt(r.name),txt(r.typ) or 'raw',1 if r.active else 0,core.now_iso()))
    return int(q.lastrowid)

def commit(snap,match,choices,core,pur,db=None,imp_rg=False,fill_cost=True,inv_date=None):
    db=db or core.DEFAULT_DB; ensure(core,db); pur.ensure_schema(db); inv_date=inv_date or date.today().isoformat(); T=snap['T']; work=match.copy()
    for code,ch in choices.items():
        m=work.code.astype(str)==str(code); work.loc[m,'action']=ch['action']; work.loc[m,'method']='user_'+ch['action']; work.loc[m,'pid']=ch.get('pid')
    if (work.action=='review').any(): raise ValueError('확인되지 않은 상품 매칭이 있습니다.')
    latest={}
    if len(T['purchases']):
        x=T['purchases'].copy(); x['_d']=pd.to_datetime(x.purchase_date,errors='coerce'); x=x.sort_values(['_d','id'])
        for code,g in x.groupby(x.item_code.astype(str)): latest[code]=num(g.iloc[-1].unit_price)
    cnt=dict(products=0,purchases=0,bom=0,production=0,inventory=0,existing=0)
    with core._conn(db) as c:
        oldrun=c.execute('select id from legacy_v07_runs where source_hash=?',(snap['hash'],)).fetchone()
        if oldrun: return {'status':'duplicate',**cnt}
        imp=c.execute("select id from imports where file_hash=? and data_type='legacy_erp'",(snap['hash'],)).fetchone()
        iid=int(imp['id']) if imp else int(c.execute("insert into imports(file_name,file_hash,data_type,created_at,notes) values(?,?,'legacy_erp',?,?)",(snap['name'],snap['hash'],core.now_iso(),'기존 Claude ERP 이관')).lastrowid)
        mp={}
        for _,r in work.iterrows():
            if r.action=='skip': continue
            pid=None if pd.isna(r.pid) else int(r.pid)
            if r.action=='create':
                z=c.execute('select product_id from legacy_v07_mappings where source_system=? and legacy_item_code=?',(SYS,r.code)).fetchone(); pid=int(z['product_id']) if z else _new_product(c,r,core); cnt['products']+=0 if z else 1
            else: cnt['existing']+=1
            mp[str(r.code)]=pid
            c.execute('''insert into legacy_v07_mappings(source_system,legacy_item_code,legacy_item_name,legacy_item_type,product_id,match_method,updated_at) values(?,?,?,?,?,?,?) on conflict(source_system,legacy_item_code) do update set legacy_item_name=excluded.legacy_item_name,legacy_item_type=excluded.legacy_item_type,product_id=excluded.product_id,match_method=excluded.match_method,updated_at=excluded.updated_at''',(SYS,r.code,r.name,r.typ,pid,r.method,core.now_iso()))
            try:
                k=pur.purchase_source_key(r.name,''); c.execute('''insert into purchase_aliases(source_key,source_name,source_detail,product_id,confirmed,updated_at) values(?,?,\'\',?,1,?) on conflict(source_key) do update set product_id=excluded.product_id,updated_at=excluded.updated_at''',(k,r.name,pid,core.now_iso()))
            except: pass
            if fill_cost and latest.get(str(r.code),0)>0:
                u=c.execute('select unit_cost from products where id=?',(pid,)).fetchone()['unit_cost'] or 0
                if u<=0: c.execute('update products set unit_cost=?,updated_at=? where id=?',(latest[str(r.code)],core.now_iso(),pid))
        wh={r['name']:int(r['id']) for r in c.execute('select id,name from warehouses')}; own,rg,ret=wh['자체창고'],wh['쿠팡RG'],wh['반품창고']; names={str(r.item_code):txt(r.item_name) for _,r in T['items'].iterrows()}
        for _,p in T['purchases'].sort_values(['purchase_date','id']).iterrows():
            lid=int(num(p.id));
            if c.execute('select 1 from legacy_v07_purchase_links where legacy_id=?',(lid,)).fetchone(): continue
            pid=mp.get(txt(p.item_code));
            if not pid: continue
            q,u=num(p.quantity),num(p.unit_price); total=q*u; detail='기존ERP 코드 '+txt(p.item_code)+((' / 거래처 '+txt(p.supplier)) if txt(p.get('supplier')) else '')+((' / '+txt(p.note)) if txt(p.get('note')) else '')
            cur=c.execute('''insert into purchase_lines(import_id,purchase_date,sheet_name,source_row,source_name,source_detail,marking,product_id,qty_source,qty_receipt,unit,currency,unit_price,total_amount,fx_rate,allocated_extra_krw,landed_total_krw,landed_unit_cost_krw,warehouse_id,created_at) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(iid,txt(p.purchase_date),'기존ERP',lid,names.get(txt(p.item_code),txt(p.item_code)),detail,'',pid,q,q,'EA','KRW',u,total,1,0,total,u,own,core.now_iso()))
            c.execute('insert into legacy_v07_purchase_links values(?,?,?)',(lid,int(cur.lastrowid),core.now_iso())); cnt['purchases']+=1
        for _,b in T['bom'].iterrows():
            a,z=mp.get(txt(b.finished_item_code)),mp.get(txt(b.raw_item_code))
            if a and z: c.execute('''insert into bom_items(parent_product_id,component_product_id,qty_per) values(?,?,?) on conflict(parent_product_id,component_product_id) do update set qty_per=excluded.qty_per''',(a,z,num(b.quantity))); cnt['bom']+=1
        for _,p in T['production'].sort_values(['production_date','id']).iterrows():
            lid=int(num(p.id));
            if c.execute('select 1 from legacy_v07_production_links where legacy_id=?',(lid,)).fetchone(): continue
            pid=mp.get(txt(p.finished_item_code));
            if not pid: continue
            cur=c.execute('insert into production_orders(production_date,parent_product_id,qty,warehouse_id,produced_unit_cost,memo,created_at) values(?,?,?,?,0,?,?)',(txt(p.production_date),pid,num(p.quantity),rg,f'기존 ERP 생산이력 #{lid} - 재고는 현재고 스냅샷으로 이관',core.now_iso()))
            c.execute('insert into legacy_v07_production_links values(?,?,?)',(lid,int(cur.lastrowid),core.now_iso())); cnt['production']+=1
        wm={'basic':own,'returns':ret};
        if imp_rg: wm['rocketgrowth']=rg
        for _,r in T['inventory'].iterrows():
            if txt(r.warehouse) not in wm: continue
            pid=mp.get(txt(r.item_code));
            if not pid: continue
            wid=wm[txt(r.warehouse)]; target=num(r.quantity); cur=num(c.execute('select coalesce(sum(qty_delta),0) q from inventory_txns where product_id=? and warehouse_id=?',(pid,wid)).fetchone()['q']); d=target-cur
            if abs(d)>1e-6: c.execute('insert into inventory_txns(txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at) values(?,?,?,?,?,?,?,?)',(inv_date,pid,wid,d,'기존ERP현재고이관','LEGACY-'+snap['hash'][:10],f'현재고 맞춤 {cur:g}→{target:g}',core.now_iso())); cnt['inventory']+=1
        c.execute('insert into legacy_v07_runs(source_hash,source_name,imported_at,products_created,purchases_imported,bom_imported,production_imported,inventory_adjustments) values(?,?,?,?,?,?,?,?)',(snap['hash'],snap['name'],core.now_iso(),cnt['products'],cnt['purchases'],cnt['bom'],cnt['production'],cnt['inventory']))
    return {'status':'imported',**cnt}

def runs(core,db=None):
    db=db or core.DEFAULT_DB; ensure(core,db)
    with core._conn(db) as c: return pd.read_sql_query('select * from legacy_v07_runs order by id desc limit 20',c)

def render_legacy_erp_page(st,pd,date,core,purchase_module,page_header,section,kpi,money,fmt_date,latest_updated_text):
    page_header('기존 ERP 가져오기','기존 Claude ERP의 상품·매입·BOM·생산·현재고를 RG Manager로 옮깁니다. 옵션ID가 같으면 상품명이 달라도 자동 연결합니다.',updated=latest_updated_text())
    ensure(core); purchase_module.ensure_schema(core.DEFAULT_DB)
    st.info('이관 전 기존 ERP 프로그램을 종료하는 것을 권장합니다. 판매·광고 이력은 쿠팡 원정산과 중복될 수 있어 가져오지 않습니다.')
    section('1. 기존 ERP 분석','같은 PC에서 실행 중이므로 C:\\ERP 폴더를 직접 읽습니다.')
    path=st.text_input('ERP 폴더 또는 erp.db 경로',value=st.session_state.get('legacy_path',r'C:\ERP'),key='legacy_path')
    snap=st.session_state.get('legacy_snap')
    if st.button('기존 ERP 분석',type='primary'):
        try: snap=analyze(path); st.session_state['legacy_snap']=snap; st.success('분석 완료')
        except Exception as e: st.error(f'분석 실패: {e}'); snap=None
    if snap:
        T=snap['T']; c1,c2,c3,c4,c5=st.columns(5); kpi(c1,'품목',f"{len(T['items']):,}개",f"완제품 {(T['items'].item_type=='finished').sum():,} · 원재료 {(T['items'].item_type=='raw').sum():,}",'primary'); kpi(c2,'매입',f"{len(T['purchases']):,}건",'원가 이력'); kpi(c3,'BOM',f"{len(T['bom']):,}건",'구성'); kpi(c4,'생산',f"{len(T['production']):,}건",'생산이력'); kpi(c5,'현재고',f"{len(T['inventory']):,}행",f"최근활동 {snap['latest']}")
        M,C,P=prep(snap,core,purchase_module); ex=int(M.action.eq('existing').sum()); cr=int(M.action.eq('create').sum()); rv=int(M.action.eq('review').sum()); section('2. 상품 연결','자동 연결하고 애매한 품목만 직접 선택합니다.'); a,b,c=st.columns(3); kpi(a,'기존상품 연결',f'{ex:,}개','코드·옵션ID·상품명','positive'); kpi(b,'신규등록',f'{cr:,}개','원재료/과거 옵션'); kpi(c,'직접 확인',f'{rv:,}개','애매한 것만','negative' if rv else 'positive')
        with st.expander('전체 연결 결과 보기'):
            V=M[['code','name','typ','status','dest','opt','score']].copy(); V.columns=['기존코드','기존상품명','구분','처리','연결/생성상품','옵션ID','일치도']; V['일치도']=V['일치도'].map(lambda x:f'{x*100:.0f}%'); st.dataframe(V,use_container_width=True,hide_index=True,height=460)
        choices={}
        for _,r in M[M.action=='review'].iterrows():
            cand=C.get(r.code,pd.DataFrame());
            with st.expander(f'{r.code} · {r.name}',expanded=True):
                labels=[]; mp={}
                for _,x in cand.iterrows():
                    lab=f"{x['name']} | {x.get('option_name') or '-'} | 옵션ID {x.get('option_id') or '-'} | {float(x.get('score',0))*100:.0f}%"; labels.append(lab); mp[lab]=int(x.id)
                labels+=['➕ 새 완제품으로 등록','⏭ 이관하지 않음']; pick=st.selectbox('연결할 상품',labels,key='legacy_'+snap['hash']+r.code)
                choices[r.code]={'action':'create'} if pick.startswith('➕') else ({'action':'skip'} if pick.startswith('⏭') else {'action':'existing','pid':mp[pick]})
        section('3. 이관 범위','매입/BOM/생산은 이력으로, 재고는 기존 ERP 현재고에 맞추는 조정으로 넣습니다.'); x,y=st.columns(2); imp_rg=x.checkbox('쿠팡RG 현재고도 이관',False,help='쿠팡 재고현황이 더 최신이면 체크하지 마세요.'); fill=x.checkbox('원가 0원 상품만 기존 ERP 최신 매입원가로 채우기',True); invd=y.date_input('현재고 기준일',date.today()); st.caption('자체창고와 반품창고는 기본 이관합니다. 기존 판매 737건·광고·월손익은 이관하지 않습니다.')
        ok=st.checkbox('상품 연결과 이관 범위를 확인했습니다.',key='legacy_ok_'+snap['hash'])
        if st.button('기존 ERP 이관 실행',type='primary',disabled=not ok,key='legacy_go_'+snap['hash']):
            try:
                r=commit(snap,M,choices,core,purchase_module,imp_rg=imp_rg,fill_cost=fill,inv_date=str(invd));
                if r['status']=='duplicate': st.warning('같은 ERP 스냅샷은 이미 이관되었습니다.')
                else: st.success(f"완료: 신규상품 {r['products']:,} · 기존연결 {r['existing']:,} · 매입 {r['purchases']:,} · BOM {r['bom']:,} · 생산 {r['production']:,} · 재고조정 {r['inventory']:,}"); st.session_state.pop('legacy_snap',None)
            except Exception as e: st.error(f'이관 오류: {e}')
    section('이관 이력','같은 매입/생산 ID는 다시 넣지 않습니다.'); R=runs(core); st.dataframe(R,use_container_width=True,hide_index=True) if len(R) else st.info('아직 이관 이력이 없습니다.')
