"""One-time repair for the audited 2026-08-04 Claude ERP migration bug."""
from __future__ import annotations
import re, sqlite3
from datetime import datetime
from pathlib import Path

SOURCE_HASH='8ecb80fd3797bc565412e51dc138ae6e0c6ba2e96fb3774a3538442c72bfbac4'
REPAIR_KEY="legacy_v082_repair_2026_08_07"
MISSING_NAMES={'94121991902': '안경닦이 4p', '94125738354': '탁구라켓 보호 테이프 2p', '94350994710': '줄눈 제거기 10개', '94351200362': '글러브 길들이기 밴드 2개', '94351561733': '뷰러 리필 고무 2p', '94948023270': '자전거 핸드폰 보관 가방', '94948038084': '집게형 스탠드 램프', '95612444686': '고급 반짇고리 바느질 미니 키트 세트, 1개, 와인레드', '95631138188': '휴대용 에어 방석 비행기 캠핑 등산, Free, 그레이', '95631138189': '휴대용 에어 방석 비행기 캠핑 등산, Free, 카키그린', '95648063867': '스텝 드릴 비트 세트 육각 구멍뚫기 파우치, 1세트', '95828314407': '남자 소가죽 벨트 허리띠 진짜 가죽 혁대', '95834379201': '보조거울 백미러 사이드미러 2p 보조미러', '95849578032': '대치동 필통 블랙', '95849578033': '대치동 필통 그레이', 'JDS700': '대형이사가방 [墨魅黑【70*35*48】甄选牛津布+可飞机托运]'}
SPLIT={'JDS0477': {'name': '점착식 나뭇잎 메모지', 'item_type': 'finished', 'active': 0, 'unit_cost': 401.0, 'own_qty': 80.0, 'old_product_id': 25, 'boms': [('94103975794', 3.0)]}, 'JDS0408': {'name': '탁구공 수집기', 'item_type': 'finished', 'active': 1, 'unit_cost': 4700.0, 'own_qty': -24.0, 'old_product_id': 4, 'boms': [('94185578349', 1.0)]}, 'JDS730': {'name': '공 CD', 'item_type': 'finished', 'active': 0, 'unit_cost': 174.0, 'own_qty': 600.0, 'old_product_id': 10, 'boms': [('95251561252', 50.0)]}, 'JDS0159': {'name': '부직포 신발 주머니', 'item_type': 'finished', 'active': 0, 'unit_cost': 24.0, 'own_qty': 1575.0, 'old_product_id': 50, 'boms': []}}

def _now(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _backup(db_path):
    out=Path(db_path).parent/"backups"; out.mkdir(parents=True,exist_ok=True)
    target=out/("rocketgrowth-pre-v0.8.2-"+datetime.now().strftime("%Y%m%d-%H%M%S-%f")+".db")
    src=sqlite3.connect(str(db_path)); dst=sqlite3.connect(str(target))
    try: src.backup(dst)
    finally: dst.close(); src.close()
    return target

def _correct_names(con):
    names=dict(MISSING_NAMES)
    for r in con.execute("select source_name,source_detail from purchase_lines where source_detail like '기존ERP 코드 %' order by id"):
        m=re.search(r"기존ERP 코드 ([^ /]+)",str(r["source_detail"] or ""))
        if m: names.setdefault(m.group(1),str(r["source_name"]))
    return names

def apply(db_path):
    db_path=Path(db_path)
    if not db_path.exists(): return {"status":"no_db"}
    con=sqlite3.connect(str(db_path)); con.row_factory=sqlite3.Row
    try:
        tables={r[0] for r in con.execute("select name from sqlite_master where type='table'")}
        need={"products","legacy_v07_mappings","legacy_v07_runs","purchase_aliases","purchase_lines","inventory_txns","bom_items","warehouses"}
        if not need.issubset(tables): return {"status":"not_applicable"}
        con.execute("create table if not exists repair_history(repair_key text primary key,applied_at text not null,backup_path text,summary text)")
        done=con.execute("select * from repair_history where repair_key=?",(REPAIR_KEY,)).fetchone()
        if done: return {"status":"already_repaired","backup_path":done["backup_path"],"summary":done["summary"]}
        if not con.execute("select 1 from legacy_v07_runs where source_hash=?",(SOURCE_HASH,)).fetchone():
            return {"status":"not_target_db"}
        mapped=[r[0] for r in con.execute("select legacy_item_code from legacy_v07_mappings where source_system='claude_erp'")]
        names=_correct_names(con)
        missing=sorted(set(mapped)-set(names))
        if len(mapped)!=199 or missing:
            raise RuntimeError(f"복구용 원본 상품명 검증 실패: 매핑 {len(mapped)}개, 이름 누락 {missing}")

        con.commit(); con.close(); backup=_backup(db_path)
        con=sqlite3.connect(str(db_path),timeout=30); con.row_factory=sqlite3.Row; con.execute("begin immediate")

        # Streamlit can execute startup code concurrently. Re-check only after
        # obtaining the write lock so a second runner cannot repeat the repair.
        done=con.execute("select * from repair_history where repair_key=?",(REPAIR_KEY,)).fetchone()
        if done:
            con.rollback()
            return {"status":"already_repaired","backup_path":done["backup_path"],"summary":done["summary"]}

        now=_now(); own=con.execute("select id from warehouses where name='자체창고'").fetchone()
        if not own: raise RuntimeError("자체창고를 찾지 못했습니다.")
        own_id=int(own["id"]); split_created=moved_purchase=moved_inventory=fixed_bom=0

        for code,info in SPLIT.items():
            m=con.execute("select product_id from legacy_v07_mappings where source_system='claude_erp' and legacy_item_code=?",(code,)).fetchone()
            if not m: raise RuntimeError(f"매핑을 찾지 못했습니다: {code}")
            old_pid=int(m["product_id"]); ex=con.execute("select id from products where item_code=?",(code,)).fetchone()
            if ex: new_pid=int(ex["id"])
            else:
                cur=con.execute("insert into products(item_code,option_id,name,item_type,unit_cost,active,updated_at) values(?,NULL,?,?,?,?,?)",
                    (code,info["name"],info["item_type"],float(info["unit_cost"]),int(info["active"]),now))
                new_pid=int(cur.lastrowid); split_created+=1
            cur=con.execute("update purchase_lines set product_id=? where product_id=? and source_detail like ?",
                (new_pid,old_pid,f"%기존ERP 코드 {code}%")); moved_purchase+=int(cur.rowcount or 0)
            cur=con.execute("""update inventory_txns set product_id=? where product_id=? and warehouse_id=?
                and txn_type='기존ERP현재고이관' and ref_no like 'LEGACY-8ecb80fd37%' and abs(qty_delta-?)<0.000001""",
                (new_pid,old_pid,own_id,float(info["own_qty"]))); moved_inventory+=int(cur.rowcount or 0)
            for parent_code,qty in info["boms"]:
                pm=con.execute("select product_id from legacy_v07_mappings where source_system='claude_erp' and legacy_item_code=?",(parent_code,)).fetchone()
                if not pm: raise RuntimeError(f"BOM 완제품 매핑을 찾지 못했습니다: {parent_code}")
                cur=con.execute("""update bom_items set component_product_id=? where parent_product_id=? and component_product_id=? and abs(qty_per-?)<0.000001""",
                    (new_pid,int(pm["product_id"]),old_pid,float(qty))); fixed_bom+=int(cur.rowcount or 0)
            con.execute("""update legacy_v07_mappings set product_id=?,legacy_item_name=?,match_method='repair_v082_split_jds',updated_at=?
                where source_system='claude_erp' and legacy_item_code=?""",(new_pid,info["name"],now,code))

        mapping_names=product_names=0
        for code,name in names.items():
            cur=con.execute("update legacy_v07_mappings set legacy_item_name=?,updated_at=? where source_system='claude_erp' and legacy_item_code=?",
                (name,now,code)); mapping_names+=int(cur.rowcount or 0)
            m=con.execute("select product_id from legacy_v07_mappings where source_system='claude_erp' and legacy_item_code=?",(code,)).fetchone()
            if not m: continue
            p=con.execute("select name from products where id=?",(int(m["product_id"]),)).fetchone()
            if p and re.fullmatch(r"\d+",str(p["name"] or "")):
                cur=con.execute("update products set name=?,updated_at=? where id=?",(name,now,int(m["product_id"])))
                product_names+=int(cur.rowcount or 0)

        cur=con.execute("""delete from purchase_aliases where coalesce(source_detail,'')='' and source_name GLOB '[0-9]*'
            and source_key=source_name||'|'"""); aliases_deleted=int(cur.rowcount or 0)
        rg=con.execute("select product_id from legacy_v07_mappings where source_system='claude_erp' and legacy_item_code='95251561252'").fetchone()
        if rg: con.execute("update products set unit_cost=9950,updated_at=? where id=? and abs(unit_cost-174)<0.000001",(now,int(rg["product_id"])))

        numeric=con.execute("""select count(*) from products p join legacy_v07_mappings m on m.product_id=p.id
            where m.source_system='claude_erp' and p.name GLOB '[0-9]*' and p.name NOT GLOB '*[^0-9]*'""").fetchone()[0]
        self_bom=con.execute("select count(*) from bom_items where parent_product_id=component_product_id").fetchone()[0]
        bad_jds=con.execute("""select count(*) from legacy_v07_mappings m join products p on p.id=m.product_id
            where m.source_system='claude_erp' and m.legacy_item_code like 'JDS%' and p.option_id is not null""").fetchone()[0]
        if numeric or self_bom or bad_jds:
            raise RuntimeError(f"복구 검증 실패: 숫자상품명={numeric}, self-BOM={self_bom}, JDS→RG={bad_jds}")

        summary=(f"상품명 {product_names}개 복구, 매핑명 {mapping_names}개 복구, JDS {split_created}개 분리, "
                 f"매입 {moved_purchase}건 이동, 재고 {moved_inventory}건 이동, BOM {fixed_bom}건 수정, "
                 f"잘못된 alias {aliases_deleted}개 삭제")
        con.execute("insert or ignore into repair_history(repair_key,applied_at,backup_path,summary) values(?,?,?,?)",(REPAIR_KEY,now,str(backup),summary))
        con.commit(); return {"status":"repaired","backup_path":str(backup),"summary":summary}
    except Exception:
        try: con.rollback()
        except Exception: pass
        raise
    finally:
        try: con.close()
        except Exception: pass
