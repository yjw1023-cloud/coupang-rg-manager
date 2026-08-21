"""RG Manager v0.9.123: confirm before skipping unmatched sales options.

If a Coupang sales-stat file contains option IDs that cannot be safely matched to
an ERP product, show the user those rows and ask whether to exclude them. Only
explicitly confirmed option IDs are ignored. All other sales rows are imported
through the existing pipeline unchanged.
"""
from __future__ import annotations

from io import BytesIO
from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False
_ACTIVE_IGNORE_IDS: set[str] = set()
_CORE = None
_RD = None
_PREVIOUS_IMPORT = None
_DEFAULT_DB = None
_PENDING_KEY = "_rg_v09123_unmatched_sales_pending"
_FLASH_KEY = "_rg_v09123_unmatched_sales_flash"


def _oid(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    try:
        x = float(s)
        if x == x and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
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


def _parse_unmatched_error(exc: Any, parsed: list[dict] | None = None) -> list[dict]:
    text = str(exc or "")
    signals = (
        "원상품을 안전하게 자동 매칭할 수 없습니다",
        "ERP에 없는 쿠팡 옵션ID",
        "품목관리에 없는 판매 옵션",
    )
    if not any(x in text for x in signals):
        return []

    parsed_by_oid = {
        _oid(r.get("option_id")): r for r in (parsed or []) if _oid(r.get("option_id"))
    }
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if "|" not in line:
            continue
        left, right = line.split("|", 1)
        oid = _oid(left)
        if not oid.isdigit():
            continue
        name = right.strip()
        reason = "원상품 자동 매칭 불가"
        if name.endswith(")") and " (" in name:
            name, tail = name.rsplit(" (", 1)
            reason = tail[:-1].strip() or reason
        src = parsed_by_oid.get(oid, {})
        qty = src.get("qty")
        try:
            qty = float(qty) if qty is not None else None
        except Exception:
            qty = None
        out.append({
            "option_id": oid,
            "name": str(src.get("name") or name).strip(),
            "qty": qty,
            "reason": reason,
        })
    return out


def _walk_functions(fn, seen=None):
    if seen is None:
        seen = set()
    if not callable(fn) or id(fn) in seen:
        return
    seen.add(id(fn))
    yield fn
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except Exception:
            continue
        if callable(value):
            yield from _walk_functions(value, seen)


def _wrapped_resolver(previous):
    if getattr(previous, "_rg_v09123_ignore_wrapper", False):
        return previous

    def resolve(core_arg, db_arg, parsed, _previous=previous):
        if _ACTIVE_IGNORE_IDS:
            parsed = [
                r for r in parsed
                if _oid(r.get("option_id")) not in _ACTIVE_IGNORE_IDS
            ]
        return _previous(core_arg, db_arg, parsed)

    resolve._rg_v09123_ignore_wrapper = True
    return resolve


def _patch_live_resolvers(core, rd):
    current = getattr(rd, "_resolve", None)
    if callable(current) and not getattr(current, "_rg_v09123_ignore_wrapper", False):
        rd._resolve = _wrapped_resolver(current)

    for fn in _walk_functions(getattr(core, "import_sales_stats", None)):
        g = getattr(fn, "__globals__", None)
        if not isinstance(g, dict):
            continue
        current = g.get("_resolve")
        if not callable(current) or getattr(current, "_rg_v09123_ignore_wrapper", False):
            continue
        if "_parse_sales_file" not in g and "_load_products" not in g:
            continue
        g["_resolve"] = _wrapped_resolver(current)


def _find_import_id(core, db, result, file_name, period_start, period_end):
    if isinstance(result, dict) and result.get("import_id"):
        try:
            return int(result["import_id"])
        except Exception:
            pass
    try:
        ps, pe = core.norm_date(period_start), core.norm_date(period_end)
    except Exception:
        ps, pe = str(period_start or ""), str(period_end or "")
    with core._conn(db) as c:
        row = c.execute(
            """SELECT id FROM imports
               WHERE data_type='sales_stats' AND file_name=?
                 AND period_start=? AND period_end=?
               ORDER BY id DESC LIMIT 1""",
            (str(file_name or ""), ps, pe),
        ).fetchone()
        if not row:
            row = c.execute(
                """SELECT id FROM imports
                   WHERE data_type='sales_stats' AND period_start=? AND period_end=?
                   ORDER BY id DESC LIMIT 1""",
                (ps, pe),
            ).fetchone()
    return int(row["id"]) if row else None


def _cleanup_ignored(core, rd, db, import_id: int, ids: set[str]) -> dict:
    out = {"sales_rows": 0, "inventory_rows": 0, "placeholders_hidden": 0}
    if not ids:
        return out
    marks = ",".join("?" for _ in ids)
    params = tuple(sorted(ids))
    now = core.now_iso()

    with core._conn(db) as c:
        products = c.execute(
            f"SELECT id,item_code,option_id,name,unit_cost,active FROM products "
            f"WHERE CAST(option_id AS TEXT) IN ({marks})",
            params,
        ).fetchall()
        pids = []
        for r in products:
            p = {
                "id": int(r["id"]),
                "item_code": str(r["item_code"] or ""),
                "option_id": _oid(r["option_id"]),
                "name": str(r["name"] or ""),
                "unit_cost": r["unit_cost"],
                "active": int(r["active"] or 0),
            }
            try:
                placeholder = bool(rd._placeholder(p))
            except Exception:
                placeholder = False
            if placeholder:
                pids.append(int(p["id"]))

        if pids:
            pm = ",".join("?" for _ in pids)
            cur = c.execute(
                f"DELETE FROM sales_stats WHERE import_id=? AND product_id IN ({pm})",
                (int(import_id), *pids),
            )
            out["sales_rows"] = max(int(cur.rowcount or 0), 0)
            cur = c.execute(
                f"DELETE FROM inventory_txns WHERE ref_no=? AND product_id IN ({pm})",
                (f"SALESSTAT-{int(import_id)}", *pids),
            )
            out["inventory_rows"] = max(int(cur.rowcount or 0), 0)
            c.execute(
                f"UPDATE products SET active=0,updated_at=? WHERE id IN ({pm})",
                (now, *pids),
            )
            out["placeholders_hidden"] = len(pids)

        if c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='return_discount_sales'"
        ).fetchone():
            c.execute(
                f"DELETE FROM return_discount_sales WHERE import_id=? "
                f"AND discount_option_id IN ({marks})",
                (int(import_id), *params),
            )

        cols = {str(r["name"]) for r in c.execute("PRAGMA table_info(imports)").fetchall()}
        if "row_count" in cols and out["sales_rows"]:
            c.execute(
                "UPDATE imports SET row_count=MAX(COALESCE(row_count,0)-?,0) WHERE id=?",
                (int(out["sales_rows"]), int(import_id)),
            )
    return out


def _pending(source, file_name, period_start, period_end, db_path, rows):
    raw = _source_bytes(source)
    if not raw:
        raise ValueError("업로드한 판매통계 파일을 다시 읽지 못했습니다.")
    return {
        "source": raw,
        "file_name": str(file_name or ""),
        "period_start": str(period_start or ""),
        "period_end": str(period_end or ""),
        "db_path": str(db_path or _DEFAULT_DB),
        "rows": rows,
    }


def _merge_rows(old, new):
    merged = {_oid(r.get("option_id")): dict(r) for r in old if _oid(r.get("option_id"))}
    for r in new:
        oid = _oid(r.get("option_id"))
        if oid:
            merged[oid] = dict(r)
    return list(merged.values())


@st.dialog("매칭되지 않는 판매 데이터 확인", width="large")
def _dialog():
    global _ACTIVE_IGNORE_IDS
    pending = st.session_state.get(_PENDING_KEY)
    if not pending:
        st.info("확인할 미매칭 판매 데이터가 없습니다.")
        return

    rows = list(pending.get("rows") or [])
    ids = {_oid(r.get("option_id")) for r in rows if _oid(r.get("option_id"))}
    st.warning(f"품목관리에 안전하게 매칭되지 않는 판매 옵션 {len(ids)}개가 있습니다.")
    st.write(
        "아래 옵션을 제외하고 나머지 판매통계만 입력할 수 있습니다. "
        "제외한 옵션은 이 파일의 판매수량·매출·재고·손익에 반영되지 않습니다."
    )
    st.dataframe(pd.DataFrame([
        {
            "옵션ID": r.get("option_id", ""),
            "상품명": r.get("name", ""),
            "판매수량": r.get("qty", ""),
            "매칭 실패 이유": r.get("reason", ""),
        }
        for r in rows
    ]), use_container_width=True, hide_index=True)
    st.caption("필요한 판매라면 취소한 뒤 품목관리에서 원상품/옵션을 먼저 등록하세요.")

    c1, c2 = st.columns(2)
    if c2.button("취소", use_container_width=True, key="_rg_v09123_cancel"):
        st.session_state.pop(_PENDING_KEY, None)
        st.rerun()
    if not c1.button(
        f"매칭 안 되는 {len(ids)}개 제외하고 입력",
        type="primary",
        use_container_width=True,
        key="_rg_v09123_confirm",
    ):
        return

    source = BytesIO(bytes(pending["source"]))
    try:
        source.name = pending.get("file_name") or "sales.xlsx"
    except Exception:
        pass

    _ACTIVE_IGNORE_IDS = set(ids)
    try:
        _patch_live_resolvers(_CORE, _RD)
        result = _PREVIOUS_IMPORT(
            source,
            pending.get("file_name", ""),
            pending.get("period_start", ""),
            pending.get("period_end", ""),
            pending.get("db_path") or _DEFAULT_DB,
        )
    except ValueError as exc:
        try:
            parsed = _RD._parse_sales_file(bytes(pending["source"]))
        except Exception:
            parsed = []
        extra = _parse_unmatched_error(exc, parsed)
        if extra:
            pending["rows"] = _merge_rows(rows, extra)
            st.session_state[_PENDING_KEY] = pending
            st.error("추가로 매칭되지 않는 옵션이 있습니다. 목록을 다시 확인해 주세요.")
            return
        st.error(f"자료를 반영하지 못했습니다. {exc}")
        return
    except Exception as exc:
        st.error(f"자료를 반영하지 못했습니다. {exc}")
        return
    finally:
        _ACTIVE_IGNORE_IDS = set()

    db = pending.get("db_path") or _DEFAULT_DB
    import_id = _find_import_id(
        _CORE, db, result,
        pending.get("file_name", ""),
        pending.get("period_start", ""),
        pending.get("period_end", ""),
    )
    if import_id is None:
        st.error("자료 입력 기록을 찾지 못해 제외 데이터 정리를 완료하지 못했습니다.")
        return

    try:
        _cleanup_ignored(_CORE, _RD, db, int(import_id), ids)
    except Exception as exc:
        st.error(f"미매칭 옵션 정리 중 오류가 발생했습니다: {exc}")
        return

    st.session_state.pop(_PENDING_KEY, None)
    st.session_state[_FLASH_KEY] = (
        f"매칭되지 않는 판매 옵션 {len(ids)}개를 제외하고 나머지 판매통계를 반영했습니다."
    )
    st.rerun()


def apply(core, return_discount_module, db_path=None):
    global _APPLIED, _CORE, _RD, _PREVIOUS_IMPORT, _DEFAULT_DB
    _CORE = core
    _RD = return_discount_module
    _DEFAULT_DB = db_path or core.DEFAULT_DB

    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        try:
            st.toast(str(flash), icon="✅")
        except Exception:
            st.success(str(flash))

    _patch_live_resolvers(core, return_discount_module)
    if _APPLIED or getattr(core, "_rg_sales_unmatched_confirm_v09123_applied", False):
        return core

    previous_import = core.import_sales_stats
    _PREVIOUS_IMPORT = previous_import

    def import_sales_stats(source, file_name, period_start, period_end, db_path=None):
        target = db_path or _DEFAULT_DB
        _patch_live_resolvers(core, return_discount_module)
        try:
            parsed = return_discount_module._parse_sales_file(source)
        except Exception:
            parsed = []

        if parsed:
            try:
                return_discount_module._resolve(core, target, parsed)
            except ValueError as exc:
                rows = _parse_unmatched_error(exc, parsed)
                if rows:
                    st.session_state[_PENDING_KEY] = _pending(
                        source, file_name, period_start, period_end, target, rows
                    )
                    _dialog()
                    st.stop()

        try:
            return previous_import(source, file_name, period_start, period_end, target)
        except ValueError as exc:
            rows = _parse_unmatched_error(exc, parsed)
            if not rows:
                raise
            st.session_state[_PENDING_KEY] = _pending(
                source, file_name, period_start, period_end, target, rows
            )
            _dialog()
            st.stop()

    core.import_sales_stats = import_sales_stats
    core._rg_sales_unmatched_confirm_v09123_applied = True
    _APPLIED = True
    return core
