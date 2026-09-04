"""Current-month provisional sales input reset for RG Manager v0.9.148.

The reset is intentionally narrow:
- remove current-month sales-stat Excel imports and their provisional snapshots;
- reverse only the inventory deductions created by those sales-stat imports;
- remove current-month Coupang API order / return / withdrawal rows used by provisional P&L;
- preserve confirmed revenue/commission API facts, inventory API snapshots/adjustments,
  purchases, production, advertising reports, manual adjustments and product master;
- preserve API sync audit rows but mark overlapping successful order/return runs as
  ``reset`` so old coverage is not mistaken for newly synchronized coverage.

Cross-month sales-stat imports are not split or deleted because the source does not
contain a reliable daily decomposition. They are reported to the operator and left
untouched.
"""
from __future__ import annotations

import calendar
from datetime import date
import json
from typing import Any


def _exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(con, table: str) -> set[str]:
    if not _exists(con, table):
        return set()
    return {str(r["name"]) for r in con.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _month_bounds(month: str) -> tuple[str, str]:
    year, month_no = [int(x) for x in str(month).split("-")]
    last = calendar.monthrange(year, month_no)[1]
    return date(year, month_no, 1).isoformat(), date(year, month_no, last).isoformat()


def current_month() -> str:
    return date.today().strftime("%Y-%m")


def _sales_import_rows(con, month: str):
    if not _exists(con, "imports"):
        return [], []
    cols = _cols(con, "imports")
    need = {"id", "data_type", "period_start", "period_end"}
    if not need.issubset(cols):
        return [], []
    start, end = _month_bounds(month)
    file_expr = "file_name" if "file_name" in cols else "''"
    rows = con.execute(
        f"""SELECT id,{file_expr} file_name,period_start,period_end
            FROM imports
            WHERE data_type='sales_stats' AND period_end>=? AND period_start<=?
            ORDER BY period_start,period_end,id""",
        (start, end),
    ).fetchall()
    inside, cross = [], []
    for row in rows:
        ps = str(row["period_start"] or "")
        pe = str(row["period_end"] or "")
        item = {
            "id": int(row["id"]),
            "file_name": str(row["file_name"] or ""),
            "period_start": ps,
            "period_end": pe,
        }
        if ps >= start and pe <= end:
            inside.append(item)
        else:
            cross.append(item)
    return inside, cross


def inspect_month(core: Any, month: str | None = None, db_path=None) -> dict[str, Any]:
    month = str(month or current_month())
    start, end = _month_bounds(month)
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    result: dict[str, Any] = {
        "month": month,
        "sales_imports": 0,
        "sales_rows": 0,
        "inventory_deductions": 0,
        "inventory_deduction_qty": 0.0,
        "cross_month_sales_imports": 0,
        "cross_month_files": [],
        "api_orders": 0,
        "api_returns": 0,
        "api_withdrawals": 0,
    }
    with core._conn(db) as con:
        inside, cross = _sales_import_rows(con, month)
        ids = [int(x["id"]) for x in inside]
        result["sales_imports"] = len(ids)
        result["cross_month_sales_imports"] = len(cross)
        result["cross_month_files"] = cross
        if ids:
            q = ",".join("?" for _ in ids)
            if _exists(con, "sales_stats") and "import_id" in _cols(con, "sales_stats"):
                result["sales_rows"] = int(con.execute(
                    f"SELECT COUNT(*) n FROM sales_stats WHERE import_id IN ({q})", ids
                ).fetchone()["n"] or 0)
            if _exists(con, "inventory_txns"):
                refs = [f"SALESSTAT-{x}" for x in ids]
                rq = ",".join("?" for _ in refs)
                row = con.execute(
                    f"""SELECT COUNT(*) n,COALESCE(SUM(ABS(qty_delta)),0) qty
                        FROM inventory_txns
                        WHERE txn_type='판매차감' AND ref_no IN ({rq})""",
                    refs,
                ).fetchone()
                result["inventory_deductions"] = int(row["n"] or 0)
                result["inventory_deduction_qty"] = float(row["qty"] or 0)
        api_specs = [
            ("api_orders", "coupang_rg_order_items", "paid_date"),
            ("api_returns", "coupang_return_items", "created_date"),
            ("api_withdrawals", "coupang_return_withdrawals", "created_date"),
        ]
        for key, table, col in api_specs:
            if _exists(con, table) and col in _cols(con, table):
                result[key] = int(con.execute(
                    f'SELECT COUNT(*) n FROM "{table}" WHERE "{col}">=? AND "{col}"<=?',
                    (start, end),
                ).fetchone()["n"] or 0)
    return result


def _ensure_audit_table(con) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS provisional_month_reset_log(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               month TEXT NOT NULL,
               reset_at TEXT NOT NULL,
               details_json TEXT NOT NULL
           )"""
    )


def _mark_api_runs_reset(con, month: str, start: str, end: str) -> int:
    """Preserve API audit rows but exclude old order/return runs from coverage."""
    if not _exists(con, "coupang_api_sync_runs"):
        return 0
    cols = _cols(con, "coupang_api_sync_runs")
    required = {"sync_type", "period_start", "period_end", "status"}
    if not required.issubset(cols):
        return 0
    count = int(con.execute(
        """SELECT COUNT(*) n FROM coupang_api_sync_runs
           WHERE sync_type IN ('orders','returns') AND status='success'
             AND period_end>=? AND period_start<=?""",
        (start, end),
    ).fetchone()["n"] or 0)
    if not count:
        return 0
    if "message" in cols:
        note = f"[당월 잠정실적 초기화 {month}]"
        con.execute(
            """UPDATE coupang_api_sync_runs
               SET status='reset',
                   message=CASE
                     WHEN COALESCE(message,'')='' THEN ?
                     ELSE message || ' ' || ?
                   END
               WHERE sync_type IN ('orders','returns') AND status='success'
                 AND period_end>=? AND period_start<=?""",
            (note, note, start, end),
        )
    else:
        con.execute(
            """UPDATE coupang_api_sync_runs SET status='reset'
               WHERE sync_type IN ('orders','returns') AND status='success'
                 AND period_end>=? AND period_start<=?""",
            (start, end),
        )
    return count


def reset_month(core: Any, month: str | None = None, db_path=None) -> dict[str, Any]:
    month = str(month or current_month())
    if month != current_month():
        raise ValueError("당월 잠정실적 초기화는 현재 달만 실행할 수 있습니다.")
    start, end = _month_bounds(month)
    db = db_path or core.DEFAULT_DB
    core.init_db(db)

    with core._conn(db) as con:
        inside, cross = _sales_import_rows(con, month)
        ids = [int(x["id"]) for x in inside]
        result: dict[str, Any] = {
            "month": month,
            "sales_imports": len(ids),
            "sales_rows": 0,
            "inventory_deductions": 0,
            "inventory_deduction_qty": 0.0,
            "cross_month_sales_imports": len(cross),
            "cross_month_files": cross,
            "api_orders": 0,
            "api_returns": 0,
            "api_withdrawals": 0,
            "api_sync_runs_reset": 0,
        }

        if ids:
            q = ",".join("?" for _ in ids)
            if _exists(con, "sales_stats") and "import_id" in _cols(con, "sales_stats"):
                result["sales_rows"] = int(con.execute(
                    f"SELECT COUNT(*) n FROM sales_stats WHERE import_id IN ({q})", ids
                ).fetchone()["n"] or 0)
            if _exists(con, "inventory_txns"):
                refs = [f"SALESSTAT-{x}" for x in ids]
                rq = ",".join("?" for _ in refs)
                row = con.execute(
                    f"""SELECT COUNT(*) n,COALESCE(SUM(ABS(qty_delta)),0) qty
                        FROM inventory_txns
                        WHERE txn_type='판매차감' AND ref_no IN ({rq})""",
                    refs,
                ).fetchone()
                result["inventory_deductions"] = int(row["n"] or 0)
                result["inventory_deduction_qty"] = float(row["qty"] or 0)
                con.execute(
                    f"DELETE FROM inventory_txns WHERE txn_type='판매차감' AND ref_no IN ({rq})",
                    refs,
                )
            if _exists(con, "provisional_pnl_snapshots") and "import_id" in _cols(con, "provisional_pnl_snapshots"):
                con.execute(
                    f"DELETE FROM provisional_pnl_snapshots WHERE import_id IN ({q})", ids
                )
            if _exists(con, "sales_stats") and "import_id" in _cols(con, "sales_stats"):
                con.execute(f"DELETE FROM sales_stats WHERE import_id IN ({q})", ids)
            con.execute(
                f"DELETE FROM imports WHERE data_type='sales_stats' AND id IN ({q})", ids
            )

        if _exists(con, "coupang_rg_order_items") and "paid_date" in _cols(con, "coupang_rg_order_items"):
            result["api_orders"] = int(con.execute(
                "SELECT COUNT(*) n FROM coupang_rg_order_items WHERE paid_date>=? AND paid_date<=?",
                (start, end),
            ).fetchone()["n"] or 0)
            con.execute(
                "DELETE FROM coupang_rg_order_items WHERE paid_date>=? AND paid_date<=?",
                (start, end),
            )

        if _exists(con, "coupang_return_items") and "created_date" in _cols(con, "coupang_return_items"):
            result["api_returns"] = int(con.execute(
                "SELECT COUNT(*) n FROM coupang_return_items WHERE created_date>=? AND created_date<=?",
                (start, end),
            ).fetchone()["n"] or 0)
            con.execute(
                "DELETE FROM coupang_return_items WHERE created_date>=? AND created_date<=?",
                (start, end),
            )
        if _exists(con, "coupang_return_requests") and "created_date" in _cols(con, "coupang_return_requests"):
            con.execute(
                "DELETE FROM coupang_return_requests WHERE created_date>=? AND created_date<=?",
                (start, end),
            )
        if _exists(con, "coupang_return_withdrawals") and "created_date" in _cols(con, "coupang_return_withdrawals"):
            result["api_withdrawals"] = int(con.execute(
                "SELECT COUNT(*) n FROM coupang_return_withdrawals WHERE created_date>=? AND created_date<=?",
                (start, end),
            ).fetchone()["n"] or 0)
            con.execute(
                "DELETE FROM coupang_return_withdrawals WHERE created_date>=? AND created_date<=?",
                (start, end),
            )

        # Coverage helpers count only status='success'. Keep the historical run
        # rows for audit, but mark the cleared period as reset so a later partial
        # resync cannot inherit stale full-month coverage from before this reset.
        result["api_sync_runs_reset"] = _mark_api_runs_reset(con, month, start, end)

        _ensure_audit_table(con)
        reset_at = str(core.now_iso())
        con.execute(
            "INSERT INTO provisional_month_reset_log(month,reset_at,details_json) VALUES(?,?,?)",
            (month, reset_at, json.dumps(result, ensure_ascii=False, default=str)),
        )
        result["reset_at"] = reset_at
        return result


def _qty_text(v: Any) -> str:
    try:
        x = float(v or 0)
        return f"{int(round(x)):,}개" if abs(x - round(x)) < 1e-9 else f"{x:,.1f}개"
    except Exception:
        return str(v)


def render_current_month_reset(st_obj: Any, core: Any, db_path=None) -> None:
    month = current_month()
    db = db_path or core.DEFAULT_DB
    info = inspect_month(core, month, db)
    total_sources = (
        int(info.get("sales_imports") or 0)
        + int(info.get("api_orders") or 0)
        + int(info.get("api_returns") or 0)
        + int(info.get("api_withdrawals") or 0)
    )

    with st_obj.expander(f"🧹 {month} 당월 잠정실적 초기화", expanded=False):
        st_obj.caption(
            "현재 달에 입력한 판매실적만 다시 시작할 때 사용합니다. "
            "판매통계 Excel과 쿠팡 API 주문·반품·철회 자료를 초기화하며, "
            "Excel 판매통계 때문에 발생한 쿠팡RG 판매차감도 함께 되돌립니다. "
            "확정손익용 매출·수수료, API 재고, 매입·생산, 광고자료, 수동조정은 삭제하지 않습니다."
        )
        st_obj.write(
            f"현재 초기화 대상: 판매통계 Excel {int(info.get('sales_imports') or 0):,}개 파일 · "
            f"판매행 {int(info.get('sales_rows') or 0):,}개 · "
            f"API 주문 {int(info.get('api_orders') or 0):,}행 · "
            f"반품/취소 {int(info.get('api_returns') or 0):,}행 · "
            f"철회 {int(info.get('api_withdrawals') or 0):,}행"
        )
        if int(info.get("inventory_deductions") or 0):
            st_obj.caption(
                "판매통계 재고차감 되돌림: "
                f"{int(info['inventory_deductions']):,}개 재고원장 · "
                f"{_qty_text(info.get('inventory_deduction_qty'))}"
            )
        if int(info.get("cross_month_sales_imports") or 0):
            names = ", ".join(
                f"{x['period_start']}~{x['period_end']} {x['file_name']}"
                for x in (info.get("cross_month_files") or [])[:3]
            )
            st_obj.warning(
                "월을 걸친 판매통계는 하루 단위로 안전하게 분리할 수 없어 초기화하지 않습니다: " + names
            )

        confirm_key = f"provisional_reset_confirm_v09148_{month}"
        button_key = f"provisional_reset_button_v09148_{month}"
        confirmed = st_obj.checkbox(
            f"{month} 당월 잠정실적 입력자료를 초기화하는 데 동의합니다.",
            key=confirm_key,
        )
        if st_obj.button(
            "당월 잠정실적 초기화",
            use_container_width=True,
            disabled=(not confirmed or total_sources == 0),
            key=button_key,
        ):
            result = reset_month(core, month, db)
            st_obj.success(
                f"{month} 잠정실적을 초기화했습니다. "
                f"Excel {int(result['sales_imports']):,}개 파일 · "
                f"API 주문 {int(result['api_orders']):,}행 · "
                f"반품/취소 {int(result['api_returns']):,}행 · "
                f"철회 {int(result['api_withdrawals']):,}행을 정리했습니다."
            )
            if int(result.get("api_sync_runs_reset") or 0):
                st_obj.caption(
                    f"기존 주문/반품 API 동기화 이력 {int(result['api_sync_runs_reset']):,}건은 "
                    "삭제하지 않고 '초기화됨' 상태로 보존했습니다."
                )
            try:
                st_obj.session_state.pop(confirm_key, None)
            except Exception:
                pass
            st_obj.rerun()
        elif total_sources == 0:
            st_obj.caption("현재 당월에 초기화할 판매실적 입력자료가 없습니다.")
