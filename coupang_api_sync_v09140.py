"""Manual Coupang Open API synchronization for RG Manager v0.9.140.

The module deliberately performs no network work at import/startup.  Every API
request is initiated by an explicit Streamlit button click.

Supported official endpoints:
- Rocket Growth order list
- Rocket warehouse inventory summaries
- Revenue/sales details
- Settlement/payment histories

Raw API facts are preserved in dedicated SQLite tables.  Rows are linked to the
existing product master by immutable Coupang vendorItemId (ERP option_id).  The
inventory action also reconciles the existing ``쿠팡RG`` warehouse ledger to the
API's ``totalOrderableQuantity`` with one auditable delta transaction per change.
"""
from __future__ import annotations

import base64
import calendar
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import functools
import sqlite3
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


PAGE_LABEL = "🔗  쿠팡 API 연동"
API_HOST = "https://api-gateway.coupang.com"
CONFIG_FILE = "coupang_api_credentials.dat"
_MARKER = "# _rg_coupang_api_sync_v09140"


class CoupangAPIError(RuntimeError):
    """A user-facing, secret-safe Coupang API failure."""


@dataclass(frozen=True)
class Credentials:
    vendor_id: str
    access_key: str
    secret_key: str

    def validate(self) -> None:
        if not self.vendor_id.strip().startswith("A"):
            raise ValueError("판매자 ID는 A로 시작하는 값을 입력해 주세요.")
        if not self.access_key.strip():
            raise ValueError("Access Key를 입력해 주세요.")
        if not self.secret_key.strip():
            raise ValueError("Secret Key를 입력해 주세요.")


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return default


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _oid(value: Any) -> str:
    text = _text(value)
    if text.upper().startswith("CP-"):
        text = text[3:]
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    return text


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _local_now(core: Any | None = None) -> str:
    if core is not None:
        try:
            return str(core.now_iso())
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _to_date(value: date | str) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _date_chunks(start: date | str, end: date | str, max_days: int):
    left, right = _to_date(start), _to_date(end)
    if left > right:
        raise ValueError("조회 시작일은 종료일보다 늦을 수 없습니다.")
    cursor = left
    while cursor <= right:
        chunk_end = min(right, cursor + timedelta(days=max_days - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("content", "items", "orders", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    for key in ("content", "items", "orders", "results"):
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]
    return []


def _next_token(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for container in (payload, payload.get("data")):
        if not isinstance(container, dict):
            continue
        for key in ("nextToken", "next_token", "token"):
            value = _text(container.get(key))
            if value:
                return value
    return ""


class CoupangClient:
    def __init__(
        self,
        credentials: Credentials,
        opener: Callable[..., Any] = urlopen,
        now: Callable[[], datetime] | None = None,
        timeout: int = 30,
    ):
        credentials.validate()
        self.credentials = credentials
        self.opener = opener
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.timeout = int(timeout)

    def _signed_date(self) -> str:
        current = self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).strftime("%y%m%dT%H%M%SZ")

    def authorization(self, method: str, path: str, query: str, signed_date: str | None = None) -> str:
        stamp = signed_date or self._signed_date()
        message = stamp + method.upper() + path + query
        signature = hmac.new(
            self.credentials.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return (
            "CEA algorithm=HmacSHA256, "
            f"access-key={self.credentials.access_key}, "
            f"signed-date={stamp}, signature={signature}"
        )

    def request(self, path: str, params: Iterable[tuple[str, Any]] | dict[str, Any] | None = None) -> Any:
        items = list(params.items()) if isinstance(params, dict) else list(params or [])
        clean = [(str(k), str(v)) for k, v in items if v is not None]
        query = urlencode(clean)
        stamp = self._signed_date()
        uri = path + (("?" + query) if query else "")
        req = Request(
            API_HOST + uri,
            method="GET",
            headers={
                "Authorization": self.authorization("GET", path, query, stamp),
                "X-Requested-By": self.credentials.vendor_id,
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": "RG-Manager/0.9.140",
            },
        )
        try:
            response = self.opener(req, timeout=self.timeout)
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else {}
        except HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                detail = _text(parsed.get("message") or parsed.get("error"))
            except Exception:
                detail = ""
            suffix = f": {detail}" if detail else ""
            raise CoupangAPIError(f"쿠팡 API 요청 실패 (HTTP {exc.code}){suffix}") from None
        except URLError as exc:
            raise CoupangAPIError(f"쿠팡 API에 연결할 수 없습니다: {_text(exc.reason)}") from None
        except TimeoutError:
            raise CoupangAPIError("쿠팡 API 응답 시간이 초과되었습니다.") from None
        except json.JSONDecodeError:
            raise CoupangAPIError("쿠팡 API 응답을 읽을 수 없습니다.") from None

    def _paged(self, path: str, params: list[tuple[str, Any]], token_name: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        token = ""
        seen: set[str] = set()
        for _page in range(500):
            request_params = [(k, v) for k, v in params if k != token_name]
            if token or token_name == "token":
                request_params.append((token_name, token))
            payload = self.request(path, request_params)
            rows.extend(_extract_rows(payload))
            nxt = _next_token(payload)
            if not nxt:
                return rows
            if nxt in seen:
                raise CoupangAPIError("쿠팡 API가 동일한 다음 페이지 값을 반복했습니다.")
            seen.add(nxt)
            token = nxt
        raise CoupangAPIError("쿠팡 API 페이지 수가 안전 한도를 초과했습니다.")

    def orders(self, start: date | str, end: date | str) -> list[dict[str, Any]]:
        path = (
            "/v2/providers/rg_open_api/apis/api/v1/vendors/"
            f"{self.credentials.vendor_id}/rg/orders"
        )
        rows: list[dict[str, Any]] = []
        for a, b in _date_chunks(start, end, 30):
            rows.extend(self._paged(path, [
                ("paidDateFrom", a.strftime("%Y%m%d")),
                ("paidDateTo", b.strftime("%Y%m%d")),
            ], "nextToken"))
        return rows

    def inventory(self) -> list[dict[str, Any]]:
        path = (
            "/v2/providers/rg_open_api/apis/api/v1/vendors/"
            f"{self.credentials.vendor_id}/rg/inventory/summaries"
        )
        return self._paged(path, [], "nextToken")

    def revenue(self, start: date | str, end: date | str) -> list[dict[str, Any]]:
        path = "/v2/providers/openapi/apis/api/v1/revenue-history"
        rows: list[dict[str, Any]] = []
        for a, b in _date_chunks(start, end, 31):
            rows.extend(self._paged(path, [
                ("vendorId", self.credentials.vendor_id),
                ("recognitionDateFrom", a.isoformat()),
                ("recognitionDateTo", b.isoformat()),
                ("maxPerPage", 50),
            ], "token"))
        return rows

    def settlements(self, month: str) -> list[dict[str, Any]]:
        path = "/v2/providers/marketplace_openapi/apis/api/v1/settlement-histories"
        payload = self.request(path, [("revenueRecognitionYearMonth", str(month))])
        return _extract_rows(payload) if isinstance(payload, dict) else _extract_rows(payload)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes):
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_encrypt(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("API 키 암호화 저장은 Windows 실행 환경에서만 지원합니다.")
    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(b"RG-Manager-Coupang-API-v09140")
    out_blob = _DataBlob()
    protect = ctypes.windll.crypt32.CryptProtectData
    protect.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    protect.restype = wintypes.BOOL
    ok = protect(
        ctypes.byref(in_blob),
        "RG Manager Coupang API",
        ctypes.byref(entropy_blob),
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _dpapi_decrypt(data: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("API 키 암호화 해제는 Windows 실행 환경에서만 지원합니다.")
    in_blob, in_buffer = _blob(data)
    entropy_blob, entropy_buffer = _blob(b"RG-Manager-Coupang-API-v09140")
    out_blob = _DataBlob()
    unprotect = ctypes.windll.crypt32.CryptUnprotectData
    unprotect.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    unprotect.restype = wintypes.BOOL
    ok = unprotect(
        ctypes.byref(in_blob), None, ctypes.byref(entropy_blob), None, None, 0, ctypes.byref(out_blob)
    )
    _ = (in_buffer, entropy_buffer)
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        local_free = ctypes.windll.kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p
        local_free(ctypes.cast(out_blob.pbData, ctypes.c_void_p))


def _config_path(core: Any) -> Path:
    db = Path(str(core.DEFAULT_DB)).resolve()
    return db.parent / CONFIG_FILE


def save_credentials(core: Any, credentials: Credentials) -> None:
    credentials.validate()
    payload = _json({
        "vendor_id": credentials.vendor_id.strip(),
        "access_key": credentials.access_key.strip(),
        "secret_key": credentials.secret_key.strip(),
    }).encode("utf-8")
    encrypted = base64.b64encode(_dpapi_encrypt(payload))
    path = _config_path(core)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(encrypted)
    tmp.replace(path)


def load_credentials(core: Any) -> Credentials | None:
    env = Credentials(
        _text(os.getenv("COUPANG_VENDOR_ID")),
        _text(os.getenv("COUPANG_ACCESS_KEY")),
        _text(os.getenv("COUPANG_SECRET_KEY")),
    )
    if env.vendor_id and env.access_key and env.secret_key:
        return env
    path = _config_path(core)
    if not path.exists():
        return None
    try:
        payload = json.loads(_dpapi_decrypt(base64.b64decode(path.read_bytes())).decode("utf-8"))
        result = Credentials(
            _text(payload.get("vendor_id")),
            _text(payload.get("access_key")),
            _text(payload.get("secret_key")),
        )
        result.validate()
        return result
    except Exception as exc:
        raise RuntimeError("저장된 쿠팡 API 키를 읽을 수 없습니다. 연결정보를 다시 저장해 주세요.") from exc


def delete_credentials(core: Any) -> None:
    path = _config_path(core)
    if path.exists():
        path.unlink()


def _exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(con: sqlite3.Connection, table: str) -> set[str]:
    if not _exists(con, table):
        return set()
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def ensure_schema(core: Any, db_path: Any | None = None) -> None:
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    with core._conn(db) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS coupang_api_sync_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sync_type TEXT NOT NULL,
                period_start TEXT,
                period_end TEXT,
                status TEXT NOT NULL,
                rows_received INTEGER NOT NULL DEFAULT 0,
                rows_matched INTEGER NOT NULL DEFAULT 0,
                message TEXT,
                started_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS ix_coupang_api_sync_runs_started
              ON coupang_api_sync_runs(started_at DESC);

            CREATE TABLE IF NOT EXISTS coupang_rg_order_items(
                order_id TEXT NOT NULL,
                item_index INTEGER NOT NULL,
                paid_at TEXT,
                paid_date TEXT,
                vendor_item_id TEXT NOT NULL,
                product_id INTEGER,
                product_name TEXT,
                sales_quantity REAL NOT NULL DEFAULT 0,
                unit_sales_price REAL NOT NULL DEFAULT 0,
                currency TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY(order_id,item_index)
            );
            CREATE INDEX IF NOT EXISTS ix_coupang_rg_order_items_paid
              ON coupang_rg_order_items(paid_date,vendor_item_id);

            CREATE TABLE IF NOT EXISTS coupang_rg_inventory(
                vendor_item_id TEXT PRIMARY KEY,
                product_id INTEGER,
                external_sku_id TEXT,
                orderable_qty REAL NOT NULL DEFAULT 0,
                sales_30d REAL NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS coupang_rg_inventory_snapshots(
                run_id INTEGER NOT NULL,
                vendor_item_id TEXT NOT NULL,
                product_id INTEGER,
                orderable_qty REAL NOT NULL DEFAULT 0,
                sales_30d REAL NOT NULL DEFAULT 0,
                captured_at TEXT NOT NULL,
                PRIMARY KEY(run_id,vendor_item_id)
            );

            CREATE TABLE IF NOT EXISTS coupang_revenue_items(
                order_id TEXT NOT NULL,
                sale_type TEXT NOT NULL,
                recognition_date TEXT NOT NULL,
                transaction_index INTEGER NOT NULL,
                item_index INTEGER NOT NULL,
                sale_date TEXT,
                settlement_date TEXT,
                vendor_item_id TEXT NOT NULL,
                product_id INTEGER,
                product_name TEXT,
                vendor_item_name TEXT,
                quantity REAL NOT NULL DEFAULT 0,
                sale_price REAL NOT NULL DEFAULT 0,
                coupang_discount_coupon REAL NOT NULL DEFAULT 0,
                seller_discount_coupon REAL NOT NULL DEFAULT 0,
                downloadable_coupon REAL NOT NULL DEFAULT 0,
                sale_amount REAL NOT NULL DEFAULT 0,
                service_fee REAL NOT NULL DEFAULT 0,
                service_fee_vat REAL NOT NULL DEFAULT 0,
                settlement_amount REAL NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY(order_id,sale_type,recognition_date,transaction_index,item_index)
            );
            CREATE INDEX IF NOT EXISTS ix_coupang_revenue_items_recognition
              ON coupang_revenue_items(recognition_date,vendor_item_id);

            CREATE TABLE IF NOT EXISTS coupang_settlement_histories(
                revenue_month TEXT NOT NULL,
                settlement_type TEXT NOT NULL,
                settlement_date TEXT NOT NULL,
                item_index INTEGER NOT NULL,
                recognition_date_from TEXT,
                recognition_date_to TEXT,
                total_sale REAL NOT NULL DEFAULT 0,
                service_fee REAL NOT NULL DEFAULT 0,
                settlement_target_amount REAL NOT NULL DEFAULT 0,
                settlement_amount REAL NOT NULL DEFAULT 0,
                last_amount REAL NOT NULL DEFAULT 0,
                pending_released_amount REAL NOT NULL DEFAULT 0,
                deduction_amount REAL NOT NULL DEFAULT 0,
                final_amount REAL NOT NULL DEFAULT 0,
                status TEXT,
                raw_json TEXT NOT NULL,
                synced_at TEXT NOT NULL,
                PRIMARY KEY(revenue_month,settlement_type,settlement_date,item_index)
            );
            """
        )


def _product_map(con: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    if _exists(con, "products"):
        cols = _columns(con, "products")
        if {"id", "option_id"}.issubset(cols):
            select_code = ",item_code" if "item_code" in cols else ",'' AS item_code"
            for row in con.execute("SELECT id,option_id" + select_code + " FROM products"):
                for key in (_oid(row["option_id"]), _oid(row["item_code"])):
                    if key and key.isdigit():
                        out.setdefault(key, int(row["id"]))
    if _exists(con, "return_discount_aliases"):
        cols = _columns(con, "return_discount_aliases")
        if {"discount_option_id", "parent_product_id"}.issubset(cols):
            for row in con.execute(
                "SELECT discount_option_id,parent_product_id FROM return_discount_aliases"
            ):
                key = _oid(row["discount_option_id"])
                if key:
                    out.setdefault(key, int(row["parent_product_id"]))
    return out


def _run_start(core: Any, db: Any, kind: str, start: str | None, end: str | None) -> int:
    ensure_schema(core, db)
    with core._conn(db) as con:
        cur = con.execute(
            """INSERT INTO coupang_api_sync_runs
               (sync_type,period_start,period_end,status,started_at)
               VALUES(?,?,?,?,?)""",
            (kind, start, end, "running", _local_now(core)),
        )
        return int(cur.lastrowid)


def _run_finish(
    core: Any,
    db: Any,
    run_id: int,
    status: str,
    received: int = 0,
    matched: int = 0,
    message: str = "",
) -> None:
    with core._conn(db) as con:
        con.execute(
            """UPDATE coupang_api_sync_runs
               SET status=?,rows_received=?,rows_matched=?,message=?,completed_at=?
               WHERE id=?""",
            (status, int(received), int(matched), _text(message), _local_now(core), int(run_id)),
        )


def _paid_parts(value: Any) -> tuple[str, str]:
    text = _text(value)
    if text.isdigit():
        try:
            dt = datetime.fromtimestamp(int(text) / 1000, tz=timezone.utc)
            return dt.isoformat(), dt.date().isoformat()
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt.isoformat(), dt.date().isoformat()
    except Exception:
        return text, text[:10] if len(text) >= 10 else ""


def sync_orders(core: Any, client: CoupangClient, start: date | str, end: date | str, db_path=None):
    db = db_path or core.DEFAULT_DB
    a, b = _to_date(start), _to_date(end)
    run_id = _run_start(core, db, "orders", a.isoformat(), b.isoformat())
    try:
        orders = client.orders(a, b)
        now = _local_now(core)
        flattened = []
        with core._conn(db) as con:
            mapping = _product_map(con)
            for order in orders:
                order_id = _text(order.get("orderId"))
                paid_at, paid_date = _paid_parts(order.get("paidAt"))
                items = order.get("orderItems") or []
                for idx, item in enumerate(items if isinstance(items, list) else []):
                    if not isinstance(item, dict):
                        continue
                    oid = _oid(item.get("vendorItemId"))
                    if not order_id or not oid:
                        continue
                    flattened.append((
                        order_id, idx, paid_at, paid_date, oid, mapping.get(oid),
                        _text(item.get("productName")),
                        _num(item.get("salesQuantity")),
                        _num(item.get("unitSalesPrice", item.get("salesPrice"))),
                        _text(item.get("currency")), _json(item), now,
                    ))
            con.execute(
                "DELETE FROM coupang_rg_order_items WHERE paid_date>=? AND paid_date<=?",
                (a.isoformat(), b.isoformat()),
            )
            con.executemany(
                """INSERT INTO coupang_rg_order_items
                   (order_id,item_index,paid_at,paid_date,vendor_item_id,product_id,
                    product_name,sales_quantity,unit_sales_price,currency,raw_json,synced_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                flattened,
            )
        matched = sum(1 for row in flattened if row[5] is not None)
        message = f"주문 {len(orders):,}건 · 상품행 {len(flattened):,}개 저장"
        _run_finish(core, db, run_id, "success", len(flattened), matched, message)
        return {"run_id": run_id, "orders": len(orders), "rows": len(flattened), "matched": matched}
    except Exception as exc:
        _run_finish(core, db, run_id, "failed", message=str(exc))
        raise


def _inventory_values(row: dict[str, Any]) -> tuple[float, float]:
    details = row.get("inventoryDetails") if isinstance(row.get("inventoryDetails"), dict) else {}
    sales = row.get("salesCountMap") if isinstance(row.get("salesCountMap"), dict) else {}
    return (
        _num(details.get("totalOrderableQuantity")),
        _num(sales.get("SALES_COUNT_LAST_THIRTY_DAYS")),
    )


def _reconcile_rg_inventory(core: Any, con: sqlite3.Connection, targets: list[tuple[str, int | None, float]], run_id: int):
    if not (_exists(con, "warehouses") and _exists(con, "inventory_txns")):
        return {"adjusted_rows": 0, "adjusted_qty": 0.0}
    warehouse = con.execute("SELECT id FROM warehouses WHERE name='쿠팡RG'").fetchone()
    if not warehouse:
        return {"adjusted_rows": 0, "adjusted_qty": 0.0}
    wid = int(warehouse["id"])
    current = {
        int(r["product_id"]): _num(r["qty"])
        for r in con.execute(
            """SELECT product_id,COALESCE(SUM(qty_delta),0) qty
               FROM inventory_txns WHERE warehouse_id=? GROUP BY product_id""",
            (wid,),
        )
    }
    now = _local_now(core)
    today = date.today().isoformat()
    adjusted_rows = 0
    adjusted_qty = 0.0
    for oid, pid, target in targets:
        if pid is None:
            continue
        live = current.get(int(pid), 0.0)
        delta = float(target) - live
        if abs(delta) <= 1e-9:
            continue
        con.execute(
            """INSERT INTO inventory_txns
               (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                today, int(pid), wid, delta, "쿠팡API재고조정",
                f"COUPANG-API-INVENTORY-{run_id}-{oid}",
                f"쿠팡 API 주문 가능 재고 {_integer(target):,}개로 대사", now,
            ),
        )
        current[int(pid)] = float(target)
        adjusted_rows += 1
        adjusted_qty += delta
    return {"adjusted_rows": adjusted_rows, "adjusted_qty": adjusted_qty}


def sync_inventory(core: Any, client: CoupangClient, db_path=None):
    db = db_path or core.DEFAULT_DB
    run_id = _run_start(core, db, "inventory", None, None)
    try:
        rows = client.inventory()
        now = _local_now(core)
        targets = []
        with core._conn(db) as con:
            mapping = _product_map(con)
            for row in rows:
                oid = _oid(row.get("vendorItemId"))
                if not oid:
                    continue
                qty, sales_30d = _inventory_values(row)
                targets.append((oid, mapping.get(oid), qty, sales_30d, row))
            # This table is a current snapshot.  Replace only after every API page
            # was received successfully, so partial failures never erase old data.
            con.execute("DELETE FROM coupang_rg_inventory")
            con.executemany(
                """INSERT INTO coupang_rg_inventory
                   (vendor_item_id,product_id,external_sku_id,orderable_qty,sales_30d,raw_json,synced_at)
                   VALUES(?,?,?,?,?,?,?)""",
                [(
                    oid, pid, _text(raw.get("externalSkuId")), qty, sales30,
                    _json(raw), now,
                ) for oid, pid, qty, sales30, raw in targets],
            )
            con.executemany(
                """INSERT INTO coupang_rg_inventory_snapshots
                   (run_id,vendor_item_id,product_id,orderable_qty,sales_30d,captured_at)
                   VALUES(?,?,?,?,?,?)""",
                [(run_id, oid, pid, qty, sales30, now) for oid, pid, qty, sales30, _raw in targets],
            )
            adjusted = _reconcile_rg_inventory(
                core, con, [(oid, pid, qty) for oid, pid, qty, _sales30, _raw in targets], run_id
            )
        matched = sum(1 for _oidv, pid, _q, _s, _r in targets if pid is not None)
        message = (
            f"재고 {len(targets):,}개 저장 · ERP 재고조정 {adjusted['adjusted_rows']:,}개"
        )
        _run_finish(core, db, run_id, "success", len(targets), matched, message)
        return {
            "run_id": run_id, "rows": len(targets), "matched": matched,
            **adjusted,
        }
    except Exception as exc:
        _run_finish(core, db, run_id, "failed", message=str(exc))
        raise


def sync_revenue(core: Any, client: CoupangClient, start: date | str, end: date | str, db_path=None):
    db = db_path or core.DEFAULT_DB
    a, b = _to_date(start), _to_date(end)
    run_id = _run_start(core, db, "revenue", a.isoformat(), b.isoformat())
    try:
        transactions = client.revenue(a, b)
        now = _local_now(core)
        flattened = []
        with core._conn(db) as con:
            mapping = _product_map(con)
            for transaction_index, transaction in enumerate(transactions):
                order_id = _text(transaction.get("orderId"))
                sale_type = _text(transaction.get("saleType")) or "UNKNOWN"
                recognition_date = _text(transaction.get("recognitionDate"))[:10]
                items = transaction.get("items") or []
                for idx, item in enumerate(items if isinstance(items, list) else []):
                    if not isinstance(item, dict):
                        continue
                    oid = _oid(item.get("vendorItemId"))
                    if not order_id or not recognition_date or not oid:
                        continue
                    flattened.append((
                        order_id, sale_type, recognition_date, transaction_index, idx,
                        _text(transaction.get("saleDate"))[:10],
                        _text(transaction.get("settlementDate"))[:10],
                        oid, mapping.get(oid), _text(item.get("productName")),
                        _text(item.get("vendorItemName")), _num(item.get("quantity")),
                        _num(item.get("salePrice")), _num(item.get("coupangDiscountCoupon")),
                        _num(item.get("sellerDiscountCoupon")), _num(item.get("downloadableCoupon")),
                        _num(item.get("saleAmount")), _num(item.get("serviceFee")),
                        _num(item.get("serviceFeeVat")), _num(item.get("settlementAmount")),
                        _json({"transaction": transaction, "item": item}), now,
                    ))
            con.execute(
                "DELETE FROM coupang_revenue_items WHERE recognition_date>=? AND recognition_date<=?",
                (a.isoformat(), b.isoformat()),
            )
            con.executemany(
                """INSERT INTO coupang_revenue_items
                   (order_id,sale_type,recognition_date,transaction_index,item_index,sale_date,settlement_date,
                    vendor_item_id,product_id,product_name,vendor_item_name,quantity,sale_price,
                    coupang_discount_coupon,seller_discount_coupon,downloadable_coupon,sale_amount,
                    service_fee,service_fee_vat,settlement_amount,raw_json,synced_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                flattened,
            )
        matched = sum(1 for row in flattened if row[7] is not None)
        message = f"매출 거래 {len(transactions):,}건 · 상품행 {len(flattened):,}개 저장"
        _run_finish(core, db, run_id, "success", len(flattened), matched, message)
        return {"run_id": run_id, "transactions": len(transactions), "rows": len(flattened), "matched": matched}
    except Exception as exc:
        _run_finish(core, db, run_id, "failed", message=str(exc))
        raise


def sync_settlements(core: Any, client: CoupangClient, month: str, db_path=None):
    db = db_path or core.DEFAULT_DB
    month = str(month)
    try:
        datetime.strptime(month, "%Y-%m")
    except Exception:
        raise ValueError("정산월은 YYYY-MM 형식이어야 합니다.") from None
    run_id = _run_start(core, db, "settlement", month, month)
    try:
        rows = client.settlements(month)
        now = _local_now(core)
        saved = []
        for idx, row in enumerate(rows):
            row_month = _text(row.get("revenueRecognitionYearMonth")) or month
            saved.append((
                row_month, _text(row.get("settlementType")) or "UNKNOWN",
                _text(row.get("settlementDate")), idx,
                _text(row.get("revenueRecognitionDateFrom"))[:10],
                _text(row.get("revenueRecognitionDateTo"))[:10],
                _num(row.get("totalSale")), _num(row.get("serviceFee")),
                _num(row.get("settlementTargetAmount")), _num(row.get("settlementAmount")),
                _num(row.get("lastAmount")), _num(row.get("pendingReleasedAmount")),
                _num(row.get("deductionAmount")), _num(row.get("finalAmount")),
                _text(row.get("status")), _json(row), now,
            ))
        with core._conn(db) as con:
            con.execute("DELETE FROM coupang_settlement_histories WHERE revenue_month=?", (month,))
            con.executemany(
                """INSERT INTO coupang_settlement_histories
                   (revenue_month,settlement_type,settlement_date,item_index,
                    recognition_date_from,recognition_date_to,total_sale,service_fee,
                    settlement_target_amount,settlement_amount,last_amount,pending_released_amount,
                    deduction_amount,final_amount,status,raw_json,synced_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                saved,
            )
        _run_finish(core, db, run_id, "success", len(saved), len(saved), f"지급내역 {len(saved):,}건 저장")
        return {"run_id": run_id, "rows": len(saved), "matched": len(saved)}
    except Exception as exc:
        _run_finish(core, db, run_id, "failed", message=str(exc))
        raise


def _summary(core: Any, db: Any) -> dict[str, Any]:
    ensure_schema(core, db)
    with core._conn(db) as con:
        last = con.execute(
            """SELECT sync_type,status,rows_received,rows_matched,message,completed_at
               FROM coupang_api_sync_runs ORDER BY id DESC LIMIT 20"""
        ).fetchall()
        counts = {}
        for table in (
            "coupang_rg_order_items", "coupang_rg_inventory",
            "coupang_revenue_items", "coupang_settlement_histories",
        ):
            counts[table] = int(con.execute(f'SELECT COUNT(*) n FROM "{table}"').fetchone()["n"])
        unmatched = {
            "orders": int(con.execute(
                "SELECT COUNT(*) n FROM coupang_rg_order_items WHERE product_id IS NULL"
            ).fetchone()["n"]),
            "inventory": int(con.execute(
                "SELECT COUNT(*) n FROM coupang_rg_inventory WHERE product_id IS NULL"
            ).fetchone()["n"]),
            "revenue": int(con.execute(
                "SELECT COUNT(*) n FROM coupang_revenue_items WHERE product_id IS NULL"
            ).fetchone()["n"]),
        }
        inventory = con.execute(
            """SELECT vendor_item_id,product_id,external_sku_id,orderable_qty,sales_30d,synced_at
               FROM coupang_rg_inventory ORDER BY orderable_qty DESC,vendor_item_id"""
        ).fetchall()
        revenue = con.execute(
            """SELECT substr(recognition_date,1,7) month,
                      SUM(CASE WHEN sale_type='REFUND' THEN -ABS(sale_amount) ELSE sale_amount END) sales,
                      SUM(CASE WHEN sale_type='REFUND' THEN -ABS(service_fee+service_fee_vat)
                               ELSE service_fee+service_fee_vat END) fee,
                      SUM(CASE WHEN sale_type='REFUND' THEN -ABS(settlement_amount)
                               ELSE settlement_amount END) settlement
               FROM coupang_revenue_items GROUP BY substr(recognition_date,1,7)
               ORDER BY month DESC LIMIT 12"""
        ).fetchall()
        settlements = con.execute(
            """SELECT revenue_month,settlement_type,settlement_date,total_sale,
                      service_fee,settlement_target_amount,final_amount,status,synced_at
               FROM coupang_settlement_histories
               ORDER BY revenue_month DESC,settlement_date DESC,item_index DESC LIMIT 30"""
        ).fetchall()
    return {
        "last": [dict(x) for x in last], "counts": counts, "unmatched": unmatched,
        "inventory": [dict(x) for x in inventory], "revenue": [dict(x) for x in revenue],
        "settlements": [dict(x) for x in settlements],
    }


def _month_bounds(month: str) -> tuple[date, date]:
    year, month_number = (int(x) for x in str(month).split("-"))
    return (
        date(year, month_number, 1),
        date(year, month_number, calendar.monthrange(year, month_number)[1]),
    )


def _revenue_month_coverage(core: Any, db: Any, month: str) -> dict[str, Any]:
    """Coverage is based on successful manual API runs, including empty days."""
    start, end = _month_bounds(month)
    covered: set[date] = set()
    with core._conn(db) as con:
        rows = con.execute(
            """SELECT period_start,period_end
               FROM coupang_api_sync_runs
               WHERE sync_type='revenue' AND status='success'
                 AND period_end>=? AND period_start<=?""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        for row in rows:
            try:
                left = max(start, _to_date(row["period_start"]))
                right = min(end, _to_date(row["period_end"]))
            except Exception:
                continue
            cursor = left
            while cursor <= right:
                covered.add(cursor)
                cursor += timedelta(days=1)
        unmatched = int(con.execute(
            """SELECT COUNT(*) n FROM coupang_revenue_items
               WHERE recognition_date>=? AND recognition_date<=? AND product_id IS NULL""",
            (start.isoformat(), end.isoformat()),
        ).fetchone()["n"])
        row_count = int(con.execute(
            """SELECT COUNT(*) n FROM coupang_revenue_items
               WHERE recognition_date>=? AND recognition_date<=?""",
            (start.isoformat(), end.isoformat()),
        ).fetchone()["n"])
    expected = (end - start).days + 1
    return {
        "complete": len(covered) == expected,
        "covered_days": len(covered),
        "expected_days": expected,
        "unmatched": unmatched,
        "rows": row_count,
    }


def _api_revenue_aggregate(core: Any, db: Any, month: str):
    start, end = _month_bounds(month)
    with core._conn(db) as con:
        rows = con.execute(
            """SELECT product_id,
                      SUM(CASE WHEN sale_type='REFUND' THEN -ABS(quantity)
                               ELSE ABS(quantity) END) qty,
                      SUM(CASE WHEN sale_type='REFUND' THEN -ABS(sale_amount)
                               ELSE sale_amount END) realized_sales,
                      SUM(CASE WHEN sale_type='REFUND'
                               THEN -ABS(service_fee + service_fee_vat)
                               ELSE ABS(service_fee + service_fee_vat) END) commission
               FROM coupang_revenue_items
               WHERE recognition_date>=? AND recognition_date<=? AND product_id IS NOT NULL
               GROUP BY product_id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        products = {
            int(r["id"]): {
                "option_id": _oid(r["option_id"]),
                "name": _text(r["name"]),
                "unit_cost": _num(r["unit_cost"]),
            }
            for r in con.execute(
                "SELECT id,option_id,name,unit_cost FROM products"
            )
        }
    return [
        {
            "product_id": int(r["product_id"]),
            "qty": _num(r["qty"]),
            "realized_sales": _num(r["realized_sales"]),
            "commission": max(0.0, _num(r["commission"])),
            **products.get(int(r["product_id"]), {}),
        }
        for r in rows
    ]


def _overlay_confirmed_month(core: Any, db: Any, month: str, original_result: Any):
    """Replace only sales/commission after a complete, fully matched API month.

    Existing Excel-derived RG logistics, return costs and ad settlement remain in
    place.  This prevents API/Excel revenue from being added together twice.
    """
    try:
        import pandas as pd

        base, meta = original_result
        meta = dict(meta or {})
        coverage = _revenue_month_coverage(core, db, month)
        if not coverage["complete"] or coverage["unmatched"] or not coverage["rows"]:
            return base, meta
        api_rows = _api_revenue_aggregate(core, db, month)
        if not api_rows:
            return base, meta

        expected_cols = [
            "product_id", "option_id", "qty", "realized_sales", "cogs",
            "commission", "inout", "delivery", "return_pickup", "return_restock",
        ]
        if base is None or not isinstance(base, pd.DataFrame):
            base = pd.DataFrame()
        if not base.empty and "product_id" not in base.columns:
            # Without a product key, replacing only revenue while retaining exact
            # logistics/return attribution cannot be done safely.
            return base, meta

        # Preserve existing product-level non-sales costs.  Multiple legacy rows
        # for one product are collapsed before the API sales facts are applied.
        preserved: dict[int, dict[str, Any]] = {}
        if not base.empty:
            for _, row in base.iterrows():
                try:
                    pid = int(row.get("product_id"))
                except Exception:
                    continue
                target = preserved.setdefault(pid, {
                    "product_id": pid,
                    "option_id": _oid(row.get("option_id")),
                    "qty": 0.0,
                    "realized_sales": 0.0,
                    "cogs": 0.0,
                    "commission": 0.0,
                    "inout": 0.0,
                    "delivery": 0.0,
                    "return_pickup": 0.0,
                    "return_restock": 0.0,
                })
                for col in ("cogs", "inout", "delivery", "return_pickup", "return_restock"):
                    target[col] += abs(_num(row.get(col)))

        for row in api_rows:
            pid = int(row["product_id"])
            target = preserved.setdefault(pid, {
                "product_id": pid,
                "option_id": row.get("option_id", ""),
                "qty": 0.0,
                "realized_sales": 0.0,
                "cogs": 0.0,
                "commission": 0.0,
                "inout": 0.0,
                "delivery": 0.0,
                "return_pickup": 0.0,
                "return_restock": 0.0,
            })
            target["option_id"] = row.get("option_id") or target.get("option_id", "")
            target["qty"] = _num(row.get("qty"))
            target["realized_sales"] = _num(row.get("realized_sales"))
            target["commission"] = _num(row.get("commission"))
            if target["cogs"] <= 0:
                target["cogs"] = max(0.0, target["qty"]) * _num(row.get("unit_cost"))

        # A legacy row that has no sale in the fully synchronized API month can
        # still carry RG/return costs.  Keep that cost row with zero revenue.
        result = pd.DataFrame(list(preserved.values()), columns=expected_cols)
        if result.empty:
            return base, meta

        revenue = float(pd.to_numeric(result["realized_sales"], errors="coerce").fillna(0).sum())
        cogs = float(pd.to_numeric(result["cogs"], errors="coerce").fillna(0).abs().sum())
        commission = float(pd.to_numeric(result["commission"], errors="coerce").fillna(0).abs().sum())
        rg = sum(
            float(pd.to_numeric(result[col], errors="coerce").fillna(0).abs().sum())
            for col in ("inout", "delivery", "return_pickup", "return_restock")
        )
        ad = abs(_num(meta.get("ad_billable_total")))
        meta["overall_profit"] = revenue - cogs - commission - rg - ad
        meta["api_revenue_source"] = True
        meta["api_revenue_coverage"] = f"{coverage['covered_days']}/{coverage['expected_days']}"
        return result, meta
    except Exception:
        # The legacy confirmed calculation is always the safe fallback.
        return original_result


def _patch_confirmed_pnl(core: Any, db: Any) -> None:
    # Store the unwrapped legacy functions on core.  app.py reloads this module
    # after an in-app update while the Streamlit process stays alive; rebuilding
    # from these originals activates the new module code without wrapper stacking.
    original_confirmed = getattr(core, "_rg_coupang_api_confirmed_original", None)
    if original_confirmed is None:
        original_confirmed = getattr(core, "confirmed_monthly_pnl", None)
        core._rg_coupang_api_confirmed_original = original_confirmed
    original_months = getattr(core, "_rg_coupang_api_monthly_available_original", None)
    if original_months is None:
        original_months = getattr(core, "monthly_available", None)
        core._rg_coupang_api_monthly_available_original = original_months
    if not callable(original_confirmed):
        return

    @functools.wraps(original_confirmed)
    def confirmed_monthly_pnl(month, *args, **kwargs):
        original_result = original_confirmed(month, *args, **kwargs)
        return _overlay_confirmed_month(core, db, str(month), original_result)

    core.confirmed_monthly_pnl = confirmed_monthly_pnl

    if callable(original_months):
        @functools.wraps(original_months)
        def monthly_available(*args, **kwargs):
            values = {str(x) for x in (original_months(*args, **kwargs) or []) if x}
            try:
                with core._conn(db) as con:
                    candidates = [
                        str(r["month"])
                        for r in con.execute(
                            """SELECT DISTINCT substr(recognition_date,1,7) month
                               FROM coupang_revenue_items ORDER BY month DESC"""
                        )
                        if r["month"]
                    ]
                for month in candidates:
                    coverage = _revenue_month_coverage(core, db, month)
                    if coverage["complete"] and coverage["unmatched"] == 0 and coverage["rows"]:
                        values.add(month)
            except Exception:
                pass
            return sorted(values, reverse=True)

        core.monthly_available = monthly_available

    core._rg_coupang_api_confirmed_v09140 = True


def _money(value: Any) -> str:
    return f"{_integer(value):,}원"


def _result_message(label: str, result: dict[str, Any]) -> str:
    rows = int(result.get("rows") or 0)
    matched = int(result.get("matched") or 0)
    extra = ""
    if "adjusted_rows" in result:
        extra = f" · ERP 재고조정 {int(result.get('adjusted_rows') or 0):,}개 상품"
    return f"{label} 완료: {rows:,}개 행 저장 · 품목 연결 {matched:,}개{extra}"


def render_page(st: Any, pd: Any, core: Any, db_path=None) -> None:
    """Render the manual-only API page."""
    db = db_path or core.DEFAULT_DB
    ensure_schema(core, db)
    try:
        st.markdown("# 쿠팡 API 연동")
        st.caption("버튼을 누를 때만 쿠팡에서 자료를 가져옵니다. 자동 동기화와 예약 실행은 사용하지 않습니다.")

        saved = None
        load_error = ""
        try:
            saved = load_credentials(core)
        except Exception as exc:
            load_error = str(exc)
        if load_error:
            st.error(load_error)

        with st.expander("API 연결정보", expanded=saved is None):
            st.caption("WING에서 발급한 판매자 ID, Access Key, Secret Key를 입력합니다. Windows 사용자 계정 암호화로 저장됩니다.")
            vendor_id = st.text_input("판매자 ID", value=saved.vendor_id if saved else "", placeholder="A00012345", key="coupang_api_vendor_v09140")
            access_key = st.text_input("Access Key", value=saved.access_key if saved else "", key="coupang_api_access_v09140")
            secret_key = st.text_input("Secret Key", value=saved.secret_key if saved else "", type="password", key="coupang_api_secret_v09140")
            c1, c2 = st.columns(2)
            if c1.button("연결정보 저장", type="primary", use_container_width=True, key="coupang_api_save_v09140"):
                try:
                    save_credentials(core, Credentials(vendor_id, access_key, secret_key))
                    st.success("API 연결정보를 암호화해 저장했습니다.")
                except Exception as exc:
                    st.error(str(exc))
            if c2.button("저장정보 삭제", use_container_width=True, key="coupang_api_delete_v09140"):
                delete_credentials(core)
                st.success("저장된 API 연결정보를 삭제했습니다.")

        credentials = Credentials(vendor_id, access_key, secret_key) if vendor_id or access_key or secret_key else saved
        ready = credentials is not None
        if ready:
            try:
                credentials.validate()
            except Exception:
                ready = False

        if not ready:
            st.info("연결정보를 입력하고 저장한 뒤 동기화 버튼을 사용하세요.")
            return

        client = CoupangClient(credentials)
        today = date.today()
        default_start = today - timedelta(days=7)
        st.markdown("### 수동 동기화")
        d1, d2, d3 = st.columns(3)
        start = d1.date_input("조회 시작일", value=default_start, key="coupang_api_start_v09140")
        end = d2.date_input("조회 종료일", value=today, key="coupang_api_end_v09140")
        month = d3.text_input("지급내역 정산월", value=today.strftime("%Y-%m"), key="coupang_api_month_v09140")
        st.caption("긴 기간은 쿠팡 제한에 맞춰 주문 30일, 매출내역 31일 단위로 자동 분할 조회합니다.")

        test_col, blank = st.columns([1, 2])
        if test_col.button("연결 확인", use_container_width=True, key="coupang_api_test_v09140"):
            try:
                # A read-only first request; it does not write any ERP data.
                path = f"/v2/providers/rg_open_api/apis/api/v1/vendors/{credentials.vendor_id}/rg/inventory/summaries"
                payload = client.request(path)
                st.success(f"쿠팡 API 연결 성공 · 첫 응답 {_text(payload.get('message') if isinstance(payload, dict) else 'SUCCESS') or 'SUCCESS'}")
            except Exception as exc:
                st.error(str(exc))

        cols = st.columns(4)
        actions = [
            (cols[0], "주문", "주문 동기화", lambda: sync_orders(core, client, start, end, db)),
            (cols[1], "재고", "재고 동기화", lambda: sync_inventory(core, client, db)),
            (cols[2], "매출·수수료", "매출·수수료 동기화", lambda: sync_revenue(core, client, start, end, db)),
            (cols[3], "지급내역", "지급내역 동기화", lambda: sync_settlements(core, client, month, db)),
        ]
        for col, short, label, action in actions:
            if col.button(label, use_container_width=True, key=f"coupang_api_{short}_v09140"):
                try:
                    with st.spinner(f"{short} 자료를 가져오는 중입니다..."):
                        result = action()
                    st.success(_result_message(short, result))
                except Exception as exc:
                    st.error(str(exc))

        if st.button("선택 기간 전체 동기화", type="primary", use_container_width=True, key="coupang_api_all_v09140"):
            completed = []
            try:
                with st.spinner("주문 자료를 가져오는 중입니다..."):
                    completed.append(_result_message("주문", sync_orders(core, client, start, end, db)))
                with st.spinner("매출·수수료 자료를 가져오는 중입니다..."):
                    completed.append(_result_message("매출·수수료", sync_revenue(core, client, start, end, db)))
                with st.spinner("로켓창고 재고를 가져와 ERP 재고와 대사하는 중입니다..."):
                    completed.append(_result_message("재고", sync_inventory(core, client, db)))
                with st.spinner("지급내역을 가져오는 중입니다..."):
                    completed.append(_result_message("지급내역", sync_settlements(core, client, month, db)))
                st.success("전체 동기화를 완료했습니다.\n\n" + "\n\n".join(completed))
            except Exception as exc:
                st.error("완료된 항목: " + (", ".join(x.split(" 완료:")[0] for x in completed) or "없음"))
                st.error(str(exc))

        st.info("광고비·노출·클릭·ROAS는 공개 판매자 API가 없어 기존 광고보고서 Excel 업로드 방식을 계속 사용합니다.")

        summary = _summary(core, db)
        st.markdown("### 연동 현황")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("주문 상품행", f"{summary['counts']['coupang_rg_order_items']:,}개")
        c2.metric("현재 재고 옵션", f"{summary['counts']['coupang_rg_inventory']:,}개")
        c3.metric("매출·수수료 상품행", f"{summary['counts']['coupang_revenue_items']:,}개")
        c4.metric("지급내역", f"{summary['counts']['coupang_settlement_histories']:,}건")

        missing_total = sum(summary["unmatched"].values())
        if missing_total:
            st.warning(
                "품목관리 옵션ID와 연결되지 않은 API 자료가 있습니다: "
                f"주문 {summary['unmatched']['orders']:,}개 · 재고 {summary['unmatched']['inventory']:,}개 · "
                f"매출 {summary['unmatched']['revenue']:,}개. 품목관리에서 해당 옵션ID를 등록한 뒤 다시 동기화하세요."
            )

        if summary["revenue"]:
            st.markdown("#### API 매출·수수료 월별 요약")
            rdf = pd.DataFrame(summary["revenue"])
            rdf = rdf.rename(columns={"month": "월", "sales": "매출인식액", "fee": "판매수수료(VAT포함)", "settlement": "정산대상액"})
            for col in ("매출인식액", "판매수수료(VAT포함)", "정산대상액"):
                rdf[col] = rdf[col].map(_money)
            st.dataframe(rdf, use_container_width=True, hide_index=True)
            try:
                coverage = _revenue_month_coverage(core, db, month)
                if coverage["complete"] and not coverage["unmatched"]:
                    st.success(f"{month} 매출·수수료는 월 전체 {coverage['expected_days']}일 동기화가 완료되어 기존 확정손익에 API 기준으로 반영됩니다.")
                else:
                    st.caption(
                        f"{month} 확정손익 API 반영 조건: 월 전체 동기화 "
                        f"{coverage['covered_days']}/{coverage['expected_days']}일 · "
                        f"미매칭 {coverage['unmatched']:,}개. 조건이 완료되기 전에는 기존 확정자료를 유지합니다."
                    )
            except Exception:
                pass

        if summary["inventory"]:
            with st.expander("최근 로켓창고 재고 보기"):
                idf = pd.DataFrame(summary["inventory"]).rename(columns={
                    "vendor_item_id": "옵션ID", "external_sku_id": "판매자 SKU",
                    "orderable_qty": "주문 가능 재고", "sales_30d": "최근 30일 판매",
                    "synced_at": "동기화 시각", "product_id": "ERP 상품ID",
                })
                st.dataframe(idf, use_container_width=True, hide_index=True)

        if summary["settlements"]:
            with st.expander("최근 지급내역 보기"):
                sdf = pd.DataFrame(summary["settlements"]).rename(columns={
                    "revenue_month": "매출인식월", "settlement_type": "정산유형",
                    "settlement_date": "지급일", "total_sale": "총판매액",
                    "service_fee": "판매수수료", "settlement_target_amount": "정산대상액",
                    "final_amount": "최종지급액", "status": "지급상태",
                    "synced_at": "동기화 시각",
                })
                for col in ("총판매액", "판매수수료", "정산대상액", "최종지급액"):
                    sdf[col] = sdf[col].map(_money)
                sdf["정산유형"] = sdf["정산유형"].map({
                    "MONTHLY": "월정산", "WEEKLY": "주정산",
                    "ADDITIONAL": "추가지급", "RESERVE": "최종액지급",
                }).fillna(sdf["정산유형"])
                sdf["지급상태"] = sdf["지급상태"].map({
                    "DONE": "지급완료", "SUBJECT": "지급예정",
                }).fillna(sdf["지급상태"])
                st.dataframe(sdf, use_container_width=True, hide_index=True)

        if summary["last"]:
            with st.expander("최근 동기화 이력", expanded=True):
                labels = {"orders": "주문", "inventory": "재고", "revenue": "매출·수수료", "settlement": "지급내역"}
                ldf = pd.DataFrame(summary["last"]).rename(columns={
                    "sync_type": "구분", "status": "상태", "rows_received": "수신행",
                    "rows_matched": "품목연결", "message": "결과", "completed_at": "완료시각",
                })
                ldf["구분"] = ldf["구분"].map(lambda x: labels.get(str(x), str(x)))
                ldf["상태"] = ldf["상태"].map(lambda x: "완료" if x == "success" else "실패" if x == "failed" else "진행 중")
                st.dataframe(ldf, use_container_width=True, hide_index=True)
    except Exception as exc:
        st.error("쿠팡 API 연동 화면 오류: " + str(exc))


def patch_source(source: str) -> str:
    """Add the API page to the legacy source before grouped-nav AST rewriting."""
    if _MARKER in source:
        return source

    label_line = f'        "{PAGE_LABEL}",\n'
    if label_line not in source:
        anchors = [
            '        "📥  기존ERP 이관",\n',
            '        "📦  재고관리",\n',
        ]
        for anchor in anchors:
            if anchor in source:
                source = source.replace(anchor, anchor + label_line, 1)
                break
        else:
            raise RuntimeError("v0.9.140 쿠팡 API 메뉴를 추가할 위치를 찾지 못했습니다.")

    handler_anchor = (
        "# ------------------------------\n"
        "# Inventory\n"
        "# ------------------------------\n"
        'elif page == "📦  재고관리":\n'
    )
    if handler_anchor not in source:
        raise RuntimeError("v0.9.140 쿠팡 API 화면을 추가할 위치를 찾지 못했습니다.")
    handler = (
        f"{_MARKER}\n"
        "# ------------------------------\n"
        "# Coupang Open API (manual sync only)\n"
        "# ------------------------------\n"
        f'elif page == "{PAGE_LABEL}":\n'
        "    coupang_api_sync_v09140.render_page(st, pd, core)\n\n\n"
    )
    return source.replace(handler_anchor, handler + handler_anchor, 1)


def apply(core: Any, db_path=None) -> None:
    """Create local tables only.  This function never calls Coupang."""
    db = db_path or core.DEFAULT_DB
    ensure_schema(core, db)
    _patch_confirmed_pnl(core, db)
