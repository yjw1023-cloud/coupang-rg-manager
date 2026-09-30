"""RG Manager v0.9.250 local AI bridge.

Local-only HTTP command server for a future ChatGPT connector.
- Binds to 127.0.0.1 only.
- Read operations: health, product search, product/BOM lookup.
- Write operations: RG product/BOM register, BOM replace, sales import,
  advertising report import, manual backup.
- Every material write creates a full SQLite backup first.
"""
from __future__ import annotations

import base64
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import io
import json
from pathlib import Path
import secrets
import threading
from urllib.parse import parse_qs, urlparse

HOST = "127.0.0.1"
PORT = 8765
_SERVER = None
_THREAD = None
_TOKEN = None
_CORE = None


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


def _token_path(core):
    db = Path(core.DEFAULT_DB).expanduser().resolve()
    folder = db.parent
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "ai_bridge_token.txt"


def ensure_token(core):
    global _TOKEN
    if _TOKEN:
        return _TOKEN
    path = _token_path(core)
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                _TOKEN = value
                return value
    except Exception:
        pass
    value = secrets.token_urlsafe(48)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(value, encoding="utf-8")
    tmp.replace(path)
    _TOKEN = value
    return value


def _auth_ok(handler):
    expected = ensure_token(_CORE)
    supplied = str(handler.headers.get("X-RG-AI-Token") or "").strip()
    return secrets.compare_digest(supplied, expected)


def _read_json(handler):
    n = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(n) if n else b"{}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"JSON 형식이 올바르지 않습니다: {exc}")
    return data if isinstance(data, dict) else {}


def _decode_file(payload):
    name = str(payload.get("filename") or "").strip()
    data = str(payload.get("file_base64") or "").strip()
    if not name or not data:
        raise ValueError("filename과 file_base64가 필요합니다.")
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        raise ValueError("file_base64 디코딩에 실패했습니다.")
    if not raw:
        raise ValueError("빈 파일입니다.")
    return name, raw


def _oid(v):
    s = str(v or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _product_search(core, q="", limit=50):
    q = str(q or "").strip()
    limit = max(1, min(int(limit or 50), 200))
    core.init_db(core.DEFAULT_DB)
    with core._conn(core.DEFAULT_DB) as c:
        if q:
            like = f"%{q}%"
            rows = c.execute(
                """SELECT id,item_code,option_id,name,item_type,unit_cost,active
                   FROM products
                   WHERE CAST(item_code AS TEXT) LIKE ?
                      OR CAST(option_id AS TEXT) LIKE ?
                      OR name LIKE ?
                   ORDER BY COALESCE(active,1) DESC,name,item_code
                   LIMIT ?""",
                (like, like, like, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT id,item_code,option_id,name,item_type,unit_cost,active
                   FROM products
                   ORDER BY COALESCE(active,1) DESC,name,item_code
                   LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def _product_by_option(core, option_id):
    oid = _oid(option_id)
    with core._conn(core.DEFAULT_DB) as c:
        r = c.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products WHERE CAST(option_id AS TEXT)=?
               ORDER BY COALESCE(active,1) DESC,id DESC LIMIT 1""",
            (oid,),
        ).fetchone()
    return dict(r) if r else None


def _bom_by_option(core, option_id):
    product = _product_by_option(core, option_id)
    if not product:
        return None
    with core._conn(core.DEFAULT_DB) as c:
        rows = c.execute(
            """SELECT b.component_product_id,c.item_code,c.option_id,c.name,
                      b.qty_per,c.unit_cost
               FROM bom_items b
               JOIN products c ON c.id=b.component_product_id
               WHERE b.parent_product_id=?
               ORDER BY c.name,c.item_code""",
            (int(product["id"]),),
        ).fetchall()
    return {"product": product, "components": [dict(r) for r in rows]}


def _component_by_code(core, item_code):
    code = str(item_code or "").strip()
    with core._conn(core.DEFAULT_DB) as c:
        r = c.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products
               WHERE item_code=? AND COALESCE(active,1)=1
               ORDER BY id DESC LIMIT 1""",
            (code,),
        ).fetchone()
    return dict(r) if r else None


def _set_bom(core, payload):
    oid = _oid(payload.get("option_id"))
    components = payload.get("components")
    if not oid or not isinstance(components, list) or not components:
        raise ValueError("option_id와 components가 필요합니다.")
    product = _product_by_option(core, oid)
    if not product:
        raise ValueError(f"ERP 상품을 찾지 못했습니다: {oid}")

    resolved = []
    seen = set()
    for row in components:
        if not isinstance(row, dict):
            continue
        comp = _component_by_code(core, row.get("item_code"))
        if not comp:
            raise ValueError(f"BOM 구성품을 찾지 못했습니다: {row.get('item_code')}")
        if int(comp["id"]) == int(product["id"]):
            raise ValueError("완제품 자신을 BOM 구성품으로 지정할 수 없습니다.")
        try:
            qty = float(row.get("qty"))
        except Exception:
            qty = 0
        if qty <= 0:
            raise ValueError(f"BOM 수량은 0보다 커야 합니다: {comp['item_code']}")
        if int(comp["id"]) in seen:
            raise ValueError(f"중복 BOM 구성품입니다: {comp['item_code']}")
        seen.add(int(comp["id"]))
        resolved.append((int(comp["id"]), qty, comp))

    backup = importlib.import_module("db_backup_v09248").backup_db(
        core, "ai_bom_replace", core.DEFAULT_DB
    )
    with core._conn(core.DEFAULT_DB) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute("DELETE FROM bom_items WHERE parent_product_id=?", (int(product["id"]),))
            c.executemany(
                """INSERT INTO bom_items(parent_product_id,component_product_id,qty_per)
                   VALUES(?,?,?)""",
                [(int(product["id"]), cid, qty) for cid, qty, _ in resolved],
            )
            c.commit()
        except Exception:
            c.rollback()
            raise
    return {
        "option_id": oid,
        "backup": backup,
        "components": [
            {"item_code": x[2]["item_code"], "name": x[2]["name"], "qty": x[1]}
            for x in resolved
        ],
    }


def _rg_preview(core, payload):
    name, raw = _decode_file(payload)
    mod = importlib.import_module("rg_product_bom_v09234")

    class Uploaded:
        def __init__(self, b, n):
            self._b = b
            self.name = n
        def getvalue(self):
            return self._b

    parsed = mod._parse_excel(Uploaded(raw, name))
    prepared, raw_rows = mod._prepare_rows(core, parsed)
    return {
        "filename": name,
        "products": prepared,
        "raw_components": [
            {"item_code": r.get("item_code"), "name": r.get("name"), "unit_cost": r.get("unit_cost")}
            for r in raw_rows
        ],
    }


def _rg_register(core, payload):
    rows = payload.get("products")
    if not isinstance(rows, list) or not rows:
        raise ValueError("products가 필요합니다.")
    mod = importlib.import_module("rg_product_bom_v09234")
    raw_rows = mod._load_raw_products(core)
    cmap = {str(r.get("item_code")): r for r in raw_rows}
    review = []
    for row in rows:
        name = str(row.get("name") or "").strip()
        oid = _oid(row.get("option_id"))
        barcode = str(row.get("barcode") or "").strip()
        bom = row.get("bom")
        if not name or not oid or not barcode or not isinstance(bom, list) or not bom:
            raise ValueError(f"상품 등록 정보가 부족합니다: {name or oid}")
        lines = []
        for b in bom:
            code = str(b.get("item_code") or "").strip()
            comp = cmap.get(code)
            if not comp:
                raise ValueError(f"BOM 기초상품을 찾지 못했습니다: {code}")
            try:
                qty = int(b.get("qty"))
            except Exception:
                qty = 0
            if qty < 1:
                raise ValueError(f"BOM 소요수량은 1 이상이어야 합니다: {code}")
            lines.append({
                "BOM 구성품": mod._candidate_label(comp),
                "component_id": int(comp["id"]),
                "소요수량": qty,
            })
        review.append({"상품명": name, "옵션ID": oid, "바코드": barcode, "BOM": lines})
    created = mod._register(core, review)
    return {
        "created": len(created),
        "products": [{"option_id": r["옵션ID"], "name": r["상품명"]} for r in created],
    }


def _sales_import(core, payload):
    name, raw = _decode_file(payload)
    start = str(payload.get("period_start") or "").strip()
    end = str(payload.get("period_end") or "").strip()
    if not start or not end:
        period_mod = importlib.import_module("sales_period_v087")
        parsed = period_mod._period_from_filename(name)
        if not parsed:
            raise ValueError("판매자료 기간을 파일명에서 찾지 못했습니다. period_start/period_end를 지정해 주세요.")
        start, end = parsed[0].isoformat(), parsed[1].isoformat()
    backup = importlib.import_module("db_backup_v09248").backup_db(
        core, "ai_sales_stats_import", core.DEFAULT_DB
    )
    result = core.import_sales_stats(io.BytesIO(raw), name, start, end, core.DEFAULT_DB)
    return {"backup": backup, "result": result, "period_start": start, "period_end": end}


def _ad_import(core, payload):
    name, raw = _decode_file(payload)
    mod = importlib.import_module("provisional_ad_report_v0956")
    unify = importlib.import_module("ad_upload_unify_v09103")
    unify.apply(mod)
    grouped, total = mod._parse_excel(raw)
    start = str(payload.get("period_start") or "").strip()
    end = str(payload.get("period_end") or "").strip()
    if start and end:
        try:
            ds, de = date.fromisoformat(start), date.fromisoformat(end)
        except Exception:
            raise ValueError("period_start/period_end는 YYYY-MM-DD 형식이어야 합니다.")
    else:
        p = mod._period_from_filename(name)
        if not p:
            raise ValueError("광고보고서 기간을 파일명에서 찾지 못했습니다. period_start/period_end를 지정해 주세요.")
        ds, de = p
    # ad_upload_unify._save itself also creates a safety backup.
    result = mod._save(
        core, core.DEFAULT_DB, name, raw, ds, de, grouped,
        bool(payload.get("replace_overlap", False)),
    )
    return {"result": result, "total": total, "period_start": ds.isoformat(), "period_end": de.isoformat()}


def _manual_backup(core):
    path = importlib.import_module("db_backup_v09248").backup_db(
        core, "ai_manual_backup", core.DEFAULT_DB
    )
    return {"backup": path}


class Handler(BaseHTTPRequestHandler):
    server_version = "RG-AI-Bridge/0.9.250"

    def log_message(self, fmt, *args):
        return

    def _send(self, code, obj):
        raw = _json_bytes(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/health":
                return self._send(200, {"ok": True, "version": "0.9.250", "host": HOST, "port": PORT})
            if not _auth_ok(self):
                return self._send(401, {"ok": False, "error": "unauthorized"})
            if u.path == "/products":
                rows = _product_search(_CORE, q.get("q", [""])[0], q.get("limit", [50])[0])
                return self._send(200, {"ok": True, "products": rows})
            if u.path == "/product":
                row = _product_by_option(_CORE, q.get("option_id", [""])[0])
                return self._send(200, {"ok": True, "product": row})
            if u.path == "/bom":
                row = _bom_by_option(_CORE, q.get("option_id", [""])[0])
                return self._send(200, {"ok": True, "bom": row})
            return self._send(404, {"ok": False, "error": "not_found"})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        try:
            if not _auth_ok(self):
                return self._send(401, {"ok": False, "error": "unauthorized"})
            payload = _read_json(self)
            u = urlparse(self.path)
            if u.path == "/backup":
                result = _manual_backup(_CORE)
            elif u.path == "/rg/preview":
                result = _rg_preview(_CORE, payload)
            elif u.path == "/rg/register":
                result = _rg_register(_CORE, payload)
            elif u.path == "/bom/set":
                result = _set_bom(_CORE, payload)
            elif u.path == "/sales/import":
                result = _sales_import(_CORE, payload)
            elif u.path == "/ads/import":
                result = _ad_import(_CORE, payload)
            else:
                return self._send(404, {"ok": False, "error": "not_found"})
            return self._send(200, {"ok": True, **(result or {})})
        except ValueError as exc:
            return self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            return self._send(500, {"ok": False, "error": str(exc)})


def start_background(core):
    global _SERVER, _THREAD, _CORE
    _CORE = core
    ensure_token(core)
    if _THREAD is not None and _THREAD.is_alive():
        return {"ok": True, "host": HOST, "port": PORT, "running": True}
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        return {"ok": False, "host": HOST, "port": PORT, "running": False, "error": str(exc)}
    _SERVER = server
    _THREAD = threading.Thread(target=server.serve_forever, name="rg-ai-bridge", daemon=True)
    _THREAD.start()
    return {"ok": True, "host": HOST, "port": PORT, "running": True}


def status():
    return {
        "ok": bool(_THREAD is not None and _THREAD.is_alive()),
        "host": HOST,
        "port": PORT,
        "running": bool(_THREAD is not None and _THREAD.is_alive()),
    }
