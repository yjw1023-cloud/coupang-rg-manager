"""v0.9.150 preserve gross/cancel quantities from Coupang sales-stat Excel."""
from __future__ import annotations
from io import BytesIO
import math
import re
from typing import Any

import pandas as pd

_APPLIED = False


def _num(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        x = float(str(v).replace(",", "").strip())
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _source_bytes(source) -> bytes | None:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "getvalue"):
        try:
            return bytes(source.getvalue())
        except Exception:
            pass
    if hasattr(source, "read"):
        try:
            pos = source.tell() if hasattr(source, "tell") else None
            data = source.read()
            if pos is not None and hasattr(source, "seek"):
                source.seek(pos)
            return bytes(data)
        except Exception:
            pass
    return None


def _norm_col(v: Any) -> str:
    return re.sub(r"[\s_()\[\]\-]+", "", str(v or "")).lower()


def _pick(columns, exact=(), contains=(), excludes=()):
    norm = {c: _norm_col(c) for c in columns}
    exact = {_norm_col(x) for x in exact}
    contains = tuple(_norm_col(x) for x in contains)
    excludes = tuple(_norm_col(x) for x in excludes)
    for c, n in norm.items():
        if n in exact and not any(x and x in n for x in excludes):
            return c
    for c, n in norm.items():
        if any(x and x in n for x in contains) and not any(x and x in n for x in excludes):
            return c
    return None


def parse_sales_quantities(source):
    raw = _source_bytes(source)
    if not raw:
        return [], {"available": False, "reason": "파일을 읽지 못했습니다."}
    try:
        xl = pd.ExcelFile(BytesIO(raw))
        sheet = "판매통계" if "판매통계" in xl.sheet_names else xl.sheet_names[0]
        df = pd.read_excel(BytesIO(raw), sheet_name=sheet)
    except Exception as exc:
        return [], {"available": False, "reason": f"Excel 읽기 실패: {exc}"}

    cols = list(df.columns)
    oid_col = _pick(cols, exact=("옵션 ID", "옵션ID", "vendorItemId"), contains=("옵션id",))
    gross_col = _pick(
        cols,
        exact=("판매상품수", "판매수량", "총판매수량", "sales_qty", "gross_qty", "gross_sales_qty", "sold_qty"),
        contains=("판매상품수", "총판매수량"),
        excludes=("순판매",),
    )
    cancel_col = _pick(
        cols,
        exact=("취소상품수", "취소수량", "반품상품수", "반품수량", "환불수량", "cancel_qty", "return_qty"),
        contains=("취소상품수", "취소수량", "반품상품수", "반품수량", "환불수량"),
    )
    net_col = _pick(
        cols,
        exact=("순판매상품수", "순판매수량", "net_qty", "net_sales_qty"),
        contains=("순판매상품수", "순판매수량"),
    )
    if oid_col is None:
        return [], {"available": False, "reason": "옵션 ID 컬럼을 찾지 못했습니다."}
    if gross_col is None and cancel_col is None:
        return [], {
            "available": False,
            "reason": "판매상품수/취소상품수 컬럼을 찾지 못했습니다.",
            "columns": [str(x) for x in cols],
        }

    by_oid = {}
    for _, r in df.iterrows():
        oid = _oid(r.get(oid_col))
        if not oid:
            continue
        gross = _num(r.get(gross_col)) if gross_col is not None else None
        cancel = abs(_num(r.get(cancel_col))) if cancel_col is not None else None
        net = _num(r.get(net_col)) if net_col is not None else None
        if gross is None and net is not None and cancel is not None:
            gross = max(0.0, net + cancel)
        if cancel is None and gross is not None and net is not None:
            cancel = max(0.0, gross - net)
        if net is None and gross is not None and cancel is not None:
            net = gross - cancel
        if gross is None:
            gross = max(0.0, net or 0.0)
        if cancel is None:
            cancel = 0.0
        if net is None:
            net = gross - cancel
        target = by_oid.setdefault(oid, {"option_id": oid, "sales_qty": 0.0, "cancel_qty": 0.0, "net_qty": 0.0})
        target["sales_qty"] += float(gross)
        target["cancel_qty"] += float(cancel)
        target["net_qty"] += float(net)

    return list(by_oid.values()), {
        "available": True,
        "rows": len(by_oid),
        "gross_col": str(gross_col or ""),
        "cancel_col": str(cancel_col or ""),
        "net_col": str(net_col or ""),
        "sheet": sheet,
    }


def _exists(c, table: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        if not _exists(c, "sales_stats"):
            return
        cols = _cols(c, "sales_stats")
        if "sales_qty" not in cols:
            c.execute("ALTER TABLE sales_stats ADD COLUMN sales_qty REAL")
        if "cancel_qty" not in cols:
            c.execute("ALTER TABLE sales_stats ADD COLUMN cancel_qty REAL")


def _find_import_id(core, db, result, source, period_start, period_end):
    if isinstance(result, dict) and result.get("import_id"):
        return int(result["import_id"])
    digest = None
    try:
        digest = core.file_hash(source)
    except Exception:
        pass
    ps = core.norm_date(period_start) if callable(getattr(core, "norm_date", None)) else str(period_start)[:10]
    pe = core.norm_date(period_end) if callable(getattr(core, "norm_date", None)) else str(period_end)[:10]
    with core._conn(db) as c:
        if digest:
            r = c.execute("""SELECT id FROM imports
                WHERE data_type='sales_stats' AND file_hash=? AND period_start=? AND period_end=?
                ORDER BY id DESC LIMIT 1""", (digest, ps, pe)).fetchone()
            if r:
                return int(r["id"])
        r = c.execute("""SELECT id FROM imports
            WHERE data_type='sales_stats' AND period_start=? AND period_end=?
            ORDER BY id DESC LIMIT 1""", (ps, pe)).fetchone()
        return int(r["id"]) if r else None


def enrich_import(core, db, import_id: int, parsed):
    ensure_schema(core, db)
    result = {"matched_options": 0, "unmatched_options": 0, "sales_qty": 0.0, "cancel_qty": 0.0}
    if not parsed:
        return result
    with core._conn(db) as c:
        pcols = _cols(c, "products")
        if not {"id", "option_id"}.issubset(pcols):
            return result
        code_expr = "item_code" if "item_code" in pcols else "'' AS item_code"
        direct = {}
        for r in c.execute(f"SELECT id,option_id,{code_expr} FROM products"):
            for raw in (r["option_id"], r["item_code"]):
                key = _oid(raw)
                if key:
                    direct.setdefault(key, int(r["id"]))
        aliases = {}
        if _exists(c, "return_discount_aliases"):
            aliases = {
                _oid(r["discount_option_id"]): int(r["parent_product_id"])
                for r in c.execute("SELECT discount_option_id,parent_product_id FROM return_discount_aliases")
            }

        agg = {}
        for row in parsed:
            oid = _oid(row.get("option_id"))
            pid = aliases.get(oid) or direct.get(oid)
            if pid is None:
                result["unmatched_options"] += 1
                continue
            target = agg.setdefault(int(pid), {"sales_qty": 0.0, "cancel_qty": 0.0, "net_qty": 0.0})
            for key in target:
                target[key] += _num(row.get(key))
            result["matched_options"] += 1

        c.execute("UPDATE sales_stats SET sales_qty=0,cancel_qty=0 WHERE import_id=?", (int(import_id),))
        for pid, values in agg.items():
            rows = c.execute(
                "SELECT rowid FROM sales_stats WHERE import_id=? AND product_id=? ORDER BY rowid",
                (int(import_id), int(pid)),
            ).fetchall()
            if not rows:
                result["unmatched_options"] += 1
                continue
            rowid = int(rows[0]["rowid"])
            c.execute(
                "UPDATE sales_stats SET sales_qty=?,cancel_qty=? WHERE rowid=?",
                (float(values["sales_qty"]), float(values["cancel_qty"]), rowid),
            )
            result["sales_qty"] += float(values["sales_qty"])
            result["cancel_qty"] += float(values["cancel_qty"])
    return result


def _patch_return_management_excel_only(return_module):
    if getattr(return_module, "_rg_excel_only_returns_v09150", False):
        return

    def sales_signal(pd_obj, core_module, db_path, start_iso, end_iso):
        tables = return_module._schema(core_module, db_path)
        cols = tables.get("sales_stats", set())
        if not cols or "product_id" not in cols:
            return pd_obj.DataFrame(), {"available": False, "reason": "sales_stats 테이블 또는 product_id가 없습니다.", "label": "취소·반품수량", "source": "sales_stats_excel"}
        net_col = return_module._pick(cols, ("net_qty", "net_sales_qty", "순판매수량"))
        gross_col = return_module._pick(cols, ("sales_qty", "sold_qty", "gross_qty", "gross_sales_qty", "order_qty", "판매수량", "판매상품수", "주문수량"))
        return_col = return_module._pick(cols, ("return_qty", "returned_qty", "returns_qty", "refund_qty", "refunded_qty", "반품수량", "환불수량"))
        cancel_col = return_module._pick(cols, ("cancel_qty", "cancelled_qty", "canceled_qty", "cancel_count", "취소수량", "취소상품수", "취소건수"))
        signal_col = return_col or cancel_col
        if signal_col is None and not (gross_col and net_col):
            return pd_obj.DataFrame(), {"available": False, "reason": "판매통계에 판매수량과 취소·반품수량이 저장되어 있지 않습니다. 동일 판매통계 Excel을 다시 업로드하세요.", "label": "취소·반품수량", "source": "sales_stats_excel"}
        imports_cols = tables.get("imports", set())
        can_period = "import_id" in cols and {"id", "period_start", "period_end"}.issubset(imports_cols)
        select_parts = ["s.product_id"]
        if gross_col:
            select_parts.append(f'SUM(COALESCE(s."{gross_col}",0)) AS gross_qty')
        elif net_col and signal_col:
            select_parts.append(f'SUM(COALESCE(s."{net_col}",0) + ABS(COALESCE(s."{signal_col}",0))) AS gross_qty')
        else:
            select_parts.append("0 AS gross_qty")
        if signal_col:
            select_parts.append(f'SUM(ABS(COALESCE(s."{signal_col}",0))) AS return_qty')
        else:
            select_parts.append(f'SUM(MAX(COALESCE(s."{gross_col}",0) - COALESCE(s."{net_col}",0),0)) AS return_qty')
        if net_col:
            select_parts.append(f'SUM(COALESCE(s."{net_col}",0)) AS net_qty')
        else:
            select_parts.append("0 AS net_qty")
        sql = "SELECT " + ",".join(select_parts) + " FROM sales_stats s"
        params = []
        if can_period:
            sql += " JOIN imports i ON i.id=s.import_id"
            where = ["i.data_type='sales_stats'"] if "data_type" in imports_cols else []
            if start_iso:
                where.append("COALESCE(i.period_end,i.period_start) >= ?")
                params.append(start_iso)
            if end_iso:
                where.append("COALESCE(i.period_start,i.period_end) <= ?")
                params.append(end_iso)
            if where:
                sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY s.product_id"
        with core_module._conn(db_path) as c:
            df = pd_obj.read_sql_query(sql, c, params=tuple(params))
        if not df.empty:
            for col in ("gross_qty", "return_qty", "net_qty"):
                df[col] = pd_obj.to_numeric(df[col], errors="coerce").fillna(0)
            df["return_qty"] = df["return_qty"].clip(lower=0)
            df["gross_qty"] = df[["gross_qty", "return_qty"]].max(axis=1)
            df["return_rate"] = df.apply(lambda r: float(r["return_qty"]) / float(r["gross_qty"]) * 100 if float(r["gross_qty"] or 0) else 0.0, axis=1)
        return df, {"available": True, "label": "반품수량" if return_col else "취소·반품수량", "gross_col": gross_col, "net_col": net_col, "signal_col": signal_col, "period_filter": can_period, "exact_return": bool(return_col), "source": "sales_stats_excel"}

    return_module._sales_signal = sales_signal
    return_module._rg_excel_only_returns_v09150 = True


def _patch_sales_quantity_excel_only(sales_quantity_module):
    if getattr(sales_quantity_module, "_rg_excel_only_qty_v09150", False):
        return

    def month_counts(core, db, month):
        start, end = sales_quantity_module._month_bounds(month)
        core.init_db(db)
        with core._conn(db) as con:
            sc = sales_quantity_module._cols(con, "sales_stats")
            ic = sales_quantity_module._cols(con, "imports")
            pc = sales_quantity_module._cols(con, "products")
            if not {"product_id", "import_id"}.issubset(sc) or not {"id", "period_start", "period_end"}.issubset(ic):
                return {}, {"exact": False, "reason": "판매통계 수량 구조 없음", "source": "sales_stats_excel"}
            if not {"id", "option_id", "item_code"}.issubset(pc):
                return {}, {"exact": False, "reason": "상품 옵션ID 구조 없음", "source": "sales_stats_excel"}
            net_col = sales_quantity_module._pick(sc, ("net_qty", "net_sales_qty", "순판매수량", "순판매상품수"))
            gross_col = sales_quantity_module._pick(sc, ("sales_qty", "sold_qty", "gross_qty", "gross_sales_qty", "order_qty", "판매수량", "판매상품수", "주문수량"))
            cancel_col = sales_quantity_module._pick(sc, ("cancel_qty", "cancelled_qty", "canceled_qty", "cancel_count", "취소수량", "취소상품수", "취소건수"))
            if not (net_col or gross_col):
                return {}, {"exact": False, "reason": "판매수량 컬럼 없음", "source": "sales_stats_excel"}
            q = sales_quantity_module._q
            gross_expr = f"SUM(CASE WHEN COALESCE(s.{q(gross_col)},0)>0 THEN COALESCE(s.{q(gross_col)},0) ELSE 0 END)" if gross_col else "0"
            cancel_expr = f"SUM(ABS(COALESCE(s.{q(cancel_col)},0)))" if cancel_col else "0"
            net_expr = f"SUM(COALESCE(s.{q(net_col)},0))" if net_col else "0"
            rows = con.execute(f"""SELECT s.product_id,p.option_id,p.item_code,{gross_expr} AS gross_qty,{cancel_expr} AS cancel_qty,{net_expr} AS net_qty
                    FROM sales_stats s JOIN products p ON p.id=s.product_id JOIN imports i ON i.id=s.import_id
                    WHERE i.data_type='sales_stats' AND i.period_start>=? AND i.period_end<=?
                    GROUP BY s.product_id,p.option_id,p.item_code""", (start, end)).fetchall()
        out = {}
        for r in rows:
            oid = sales_quantity_module._oid(r["option_id"]) or sales_quantity_module._oid(r["item_code"])
            if not oid:
                continue
            gross = sales_quantity_module._num(r["gross_qty"])
            cancel = abs(sales_quantity_module._num(r["cancel_qty"]))
            net = sales_quantity_module._num(r["net_qty"])
            if gross_col is None and net_col is not None and cancel_col is not None:
                gross = max(0.0, net + cancel)
            elif gross_col is None and net_col is not None:
                gross = max(0.0, net)
                cancel = max(0.0, -net)
            if cancel_col is None and gross_col is not None and net_col is not None:
                cancel = max(0.0, gross - net)
            if net_col is None:
                net = gross - cancel
            out[oid] = {"product_id": int(r["product_id"]), "sales_qty": gross, "cancel_qty": cancel, "withdrawal_qty": 0.0, "net_qty": net}
        return out, {"exact": bool(gross_col or (net_col and cancel_col)), "gross_col": gross_col, "cancel_col": cancel_col, "net_col": net_col, "rows": len(out), "source": "sales_stats_excel"}

    sales_quantity_module.month_counts = month_counts
    sales_quantity_module._rg_excel_only_qty_v09150 = True


def apply(core, db_path=None, return_module=None, sales_quantity_module=None):
    global _APPLIED
    if _APPLIED or getattr(core, "_rg_sales_stats_returns_v09150_applied", False):
        return core
    db = db_path or core.DEFAULT_DB
    ensure_schema(core, db)
    previous = core.import_sales_stats

    def import_sales_stats(source, file_name, period_start, period_end, db_path=None):
        target = db_path or db
        parsed, meta = parse_sales_quantities(source)
        result = previous(source, file_name, period_start, period_end, target)
        import_id = _find_import_id(core, target, result, source, period_start, period_end)
        if import_id is not None and meta.get("available"):
            stats = enrich_import(core, target, import_id, parsed)
            if isinstance(result, dict):
                result = dict(result)
                result["sales_qty_preserved"] = stats["sales_qty"]
                result["cancel_qty_preserved"] = stats["cancel_qty"]
                result["sales_qty_source"] = meta.get("gross_col", "")
                result["cancel_qty_source"] = meta.get("cancel_col", "")
        return result

    core.import_sales_stats = import_sales_stats
    core._rg_sales_stats_returns_v09150_applied = True
    if return_module is not None:
        _patch_return_management_excel_only(return_module)
    if sales_quantity_module is not None:
        _patch_sales_quantity_excel_only(sales_quantity_module)
    _APPLIED = True
    return core
