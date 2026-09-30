"""RG Manager v0.9.252 Google Drive desktop-sync AI relay.

No cloud credentials are read or stored by ERP. The user points ERP at the local
Google Drive synced JDSmall_AI_ERP folder. Commands placed in inbox are executed
locally; JSON results are written to outbox and synced by Google Drive for desktop.
"""
from __future__ import annotations

import base64
import importlib
import json
import os
from pathlib import Path
import string
import threading
import time

POLL_SECONDS = 10
_CORE = None
_THREAD = None
_STOP = threading.Event()
_STATUS = {"running":False,"ready":False,"last_poll":None,"last_error":"","last_command":""}


def _cfg_path(core):
    return Path(core.DEFAULT_DB).expanduser().resolve().parent / "ai_drive_relay_config.json"


def _state_path(core):
    return Path(core.DEFAULT_DB).expanduser().resolve().parent / "ai_drive_relay_state.json"


def _load_cfg(core):
    try:
        obj=json.loads(_cfg_path(core).read_text(encoding="utf-8"))
        return obj if isinstance(obj,dict) else {}
    except Exception:
        return {}


def save_base_path(core, value):
    p=Path(str(value or "").strip()).expanduser()
    if not p.exists() or not p.is_dir():
        raise ValueError("지정한 폴더를 찾지 못했습니다.")
    needed=[p/"inbox",p/"outbox",p/"files"]
    missing=[x.name for x in needed if not x.exists()]
    if missing:
        raise ValueError("JDSmall_AI_ERP 폴더가 아닙니다. 없는 하위폴더: "+", ".join(missing))
    cfg={"base_path":str(p.resolve())}
    tmp=_cfg_path(core).with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg,ensure_ascii=False,indent=2),encoding="utf-8")
    tmp.replace(_cfg_path(core))
    return cfg["base_path"]


def configured_base(core):
    cfg=_load_cfg(core)
    p=Path(str(cfg.get("base_path") or "")).expanduser()
    if p.exists() and (p/"inbox").exists() and (p/"outbox").exists() and (p/"files").exists():
        return p
    return None


def discover_candidates():
    out=[]
    home=Path.home()
    for p in [
        home/"My Drive"/"JDSmall_AI_ERP",
        home/"Google Drive"/"My Drive"/"JDSmall_AI_ERP",
        home/"Google Drive"/"JDSmall_AI_ERP",
    ]:
        if p.exists():
            out.append(str(p))
    if os.name=="nt":
        for letter in string.ascii_uppercase[3:]:
            root=Path(f"{letter}:/")
            for rel in ["My Drive/JDSmall_AI_ERP","JDSmall_AI_ERP"]:
                p=root/rel
                try:
                    if p.exists():
                        out.append(str(p))
                except Exception:
                    pass
    return list(dict.fromkeys(out))


def _load_state(core):
    try:
        d=json.loads(_state_path(core).read_text(encoding="utf-8"))
        return d if isinstance(d,dict) else {"processed":[]}
    except Exception:
        return {"processed":[]}


def _save_state(core,d):
    p=_state_path(core)
    t=p.with_suffix(".tmp")
    t.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding="utf-8")
    t.replace(p)


def _attachment(base,spec):
    if not isinstance(spec,dict):
        raise ValueError("attachment 형식이 올바르지 않습니다.")
    name=str(spec.get("file_name") or "").strip()
    if not name or "/" in name or "\\" in name or name in {".",".."}:
        raise ValueError("attachment.file_name이 올바르지 않습니다.")
    p=base/"files"/name
    if not p.exists() or not p.is_file():
        raise ValueError("첨부파일을 찾지 못했습니다: "+name)
    return {"filename":name,"file_base64":base64.b64encode(p.read_bytes()).decode("ascii")}


def _execute(core,base,cmd):
    bridge=importlib.import_module("ai_bridge_v09250")
    typ=str(cmd.get("type") or "").strip()
    payload=dict(cmd.get("payload") or {})
    if cmd.get("attachment"):
        payload.update(_attachment(base,cmd["attachment"]))
    routes={
        "health":lambda:{"status":bridge.status()},
        "product_search":lambda:{"products":bridge._product_search(core,payload.get("q",""),payload.get("limit",50))},
        "product_get":lambda:{"product":bridge._product_by_option(core,payload.get("option_id"))},
        "bom_get":lambda:{"bom":bridge._bom_by_option(core,payload.get("option_id"))},
        "backup":lambda:bridge._manual_backup(core),
        "rg_preview":lambda:bridge._rg_preview(core,payload),
        "rg_register":lambda:bridge._rg_register(core,payload),
        "bom_set":lambda:bridge._set_bom(core,payload),
        "sales_import":lambda:bridge._sales_import(core,payload),
        "ads_import":lambda:bridge._ad_import(core,payload),
        "db_schema":lambda:importlib.import_module("ai_db_access_v09254").schema(core,payload),
        "table_read":lambda:importlib.import_module("ai_db_access_v09254").read(core,payload),
    }
    if typ not in routes:
        raise ValueError("허용되지 않은 AI 명령입니다: "+typ)
    return routes[typ]()



def _filename_commands(base):
    """Decode content-free commands from filenames.

    Format: CMD__<id>__<type>__<base64url-json>.rgcmd
    The file bytes are ignored. This lets ChatGPT create commands by copying
    any existing Drive file and choosing a new filename.
    """
    out=[]
    for p in sorted((base/"inbox").glob("CMD__*.rgcmd"))[:50]:
        stem=p.name[:-6] if p.name.lower().endswith(".rgcmd") else p.stem
        parts=stem.split("__",3)
        if len(parts)!=4 or parts[0]!="CMD":
            continue
        _tag,cid,typ,enc=parts
        try:
            pad="="*((4-len(enc)%4)%4)
            payload=json.loads(base64.urlsafe_b64decode((enc+pad).encode("ascii")).decode("utf-8"))
            if not isinstance(payload,dict):
                payload={}
            out.append((p,cid,{"id":cid,"type":typ,"payload":payload}))
        except Exception:
            out.append((p,cid,{"id":cid,"type":typ,"payload":{},"_decode_error":True}))
    return out

def poll_once(core=None):
    core=core or _CORE
    base=configured_base(core)
    _STATUS["last_poll"]=time.strftime("%Y-%m-%d %H:%M:%S")
    if base is None:
        _STATUS["ready"]=False
        return {"ready":False,"processed":0}
    state=_load_state(core)
    processed=set(map(str,state.get("processed",[])))
    count=0
    queue=[]
    for p in sorted((base/"inbox").glob("*.json"))[:50]:
        cid=p.stem
        cmd=None
        try:
            cmd=json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            cmd={}
        queue.append((p,cid,cmd))
    queue.extend(_filename_commands(base))
    for p,cid,cmd in queue[:100]:
        if cid in processed:
            continue
        try:
            if cmd.get("_decode_error"):
                raise ValueError("파일명 명령을 해석하지 못했습니다.")
            if str(cmd.get("id") or "")!=cid:
                raise ValueError("명령 파일명과 id가 일치하지 않습니다.")
            result=_execute(core,base,cmd)
            out={"id":cid,"ok":True,"type":cmd.get("type"),"completed_at":time.strftime("%Y-%m-%d %H:%M:%S"),"result":result}
        except Exception as exc:
            out={"id":cid,"ok":False,"type":(cmd or {}).get("type"),"completed_at":time.strftime("%Y-%m-%d %H:%M:%S"),"error":str(exc)}
        tmp=(base/"outbox"/f"{cid}.json.tmp")
        final=(base/"outbox"/f"{cid}.json")
        tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        tmp.replace(final)
        processed.add(cid)
        _STATUS["last_command"]=cid
        count+=1
    state["processed"]=sorted(processed)[-5000:]
    _save_state(core,state)
    _STATUS["ready"]=True
    _STATUS["last_error"]=""
    return {"ready":True,"processed":count,"base_path":str(base)}


def _loop():
    _STATUS["running"]=True
    while not _STOP.is_set():
        try:
            poll_once(_CORE)
        except Exception as exc:
            _STATUS["ready"]=False
            _STATUS["last_error"]=str(exc)
            _STATUS["last_poll"]=time.strftime("%Y-%m-%d %H:%M:%S")
        _STOP.wait(POLL_SECONDS)
    _STATUS["running"]=False


def start_background(core):
    global _CORE,_THREAD
    _CORE=core
    if _THREAD is None or not _THREAD.is_alive():
        _STOP.clear()
        _THREAD=threading.Thread(target=_loop,name="rg-ai-drive-relay",daemon=True)
        _THREAD.start()
    return status(core)


def status(core=None):
    core=core or _CORE
    base=configured_base(core) if core is not None else None
    return {**_STATUS,"running":bool(_THREAD and _THREAD.is_alive()),"ready":bool(base) and bool(_STATUS.get("ready")),"base_path":str(base) if base else "","poll_seconds":POLL_SECONDS}


def render_setup(st,core):
    st.subheader("ChatGPT ERP 연결")
    s=status(core)
    if s.get("base_path"):
        st.success("Google Drive 동기화 폴더 연결됨: "+s["base_path"])
    else:
        st.warning("Google Drive 동기화 폴더 경로를 한 번 지정해야 합니다.")
    candidates=discover_candidates()
    if candidates:
        st.caption("자동 발견: "+" | ".join(candidates))
    value=st.text_input(
        "JDSmall_AI_ERP 로컬 폴더 경로",
        value=s.get("base_path") or (candidates[0] if candidates else ""),
        placeholder=r"G:\My Drive\JDSmall_AI_ERP",
        key="rg_ai_drive_path_v09252",
    )
    c1,c2=st.columns(2)
    if c1.button("Drive 폴더 연결",key="rg_ai_drive_save_v09252",use_container_width=True):
        try:
            saved=save_base_path(core,value)
            st.success("연결 완료: "+saved)
            st.rerun()
        except Exception as exc:
            st.error("연결 실패: "+str(exc))
    if c2.button("중계 테스트",key="rg_ai_drive_test_v09252",use_container_width=True):
        try:
            r=poll_once(core)
            if r.get("ready"):
                st.success(f"중계 정상 · 새 명령 {int(r.get('processed') or 0)}개 처리")
            else:
                st.error("Drive 폴더가 아직 연결되지 않았습니다.")
        except Exception as exc:
            st.error("중계 테스트 실패: "+str(exc))
    s=status(core)
    st.caption(f"중계 실행: {'예' if s.get('running') else '아니오'} · 확인주기 {s.get('poll_seconds')}초 · 최근 확인 {s.get('last_poll') or '-'} · 최근 명령 {s.get('last_command') or '-'}")
    if s.get("last_error"):
        st.error("최근 중계 오류: "+str(s["last_error"]))
