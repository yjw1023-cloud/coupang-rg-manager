"""RG Manager v0.9.255 remote inbound command poller.

Reads one public command file from raw.githubusercontent.com without GitHub API
credentials. ChatGPT updates that file through the connected GitHub tool.
ERP writes execution results to the existing Google Drive outbox.
"""
from __future__ import annotations
import importlib
import json
import time
import urllib.parse
import urllib.request

RAW_URL="https://raw.githubusercontent.com/yjw1023-cloud/coupang-rg-manager/main/ai_command.json"

def _fetch():
    req=urllib.request.Request(
        RAW_URL+"?_rgcb="+str(time.time_ns()),
        headers={
            "User-Agent":"RG-Manager/0.9.255",
            "Cache-Control":"no-cache, no-store, max-age=0",
            "Pragma":"no-cache",
        },
    )
    with urllib.request.urlopen(req,timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def poll(core, base):
    cmd=_fetch()
    if not isinstance(cmd,dict):
        return {"processed":0}
    cid=str(cmd.get("id") or "").strip()
    if not cid:
        return {"processed":0}
    state_path=importlib.import_module("ai_drive_relay_v09252")._state_path(core)
    try:
        state=json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(state,dict):
            state={"processed":[]}
    except Exception:
        state={"processed":[]}
    processed=set(map(str,state.get("processed",[])))
    remote_key="remote:"+cid
    if remote_key in processed:
        return {"processed":0,"id":cid}
    relay=importlib.import_module("ai_drive_relay_v09252")
    try:
        result=relay._execute(core,base,cmd)
        out={"id":cid,"ok":True,"type":cmd.get("type"),"completed_at":time.strftime("%Y-%m-%d %H:%M:%S"),"result":result}
    except Exception as exc:
        out={"id":cid,"ok":False,"type":cmd.get("type"),"completed_at":time.strftime("%Y-%m-%d %H:%M:%S"),"error":str(exc)}
    tmp=base/"outbox"/f"{cid}.json.tmp"
    final=base/"outbox"/f"{cid}.json"
    tmp.write_text(json.dumps(out,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
    tmp.replace(final)
    processed.add(remote_key)
    state["processed"]=sorted(processed)[-5000:]
    relay._save_state(core,state)
    return {"processed":1,"id":cid}
