"""RG Manager v0.9.79 monthly product goals and performance review.

Provides a dedicated read/write planning screen for finished products:
- set current/next-month sales, revenue, ad-budget and profit goals
- track current performance and pace using ERP provisional/confirmed data
- forecast month-end results from continuously loaded sales-data coverage
- review month-end goal vs actual and record reasons/notes
- inspect product goal history

Goal tables are additive. Existing sales, inventory, BOM and P&L data are read-only.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
import importlib
import math
import re
from typing import Any

import pandas as pd

PAGE_LABEL = "🎯  목표·실적관리"

_SELECT_CSS = r"""
<style>
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {
    background:#ffffff !important;
    border:2px solid #7b899b !important;
    border-radius:8px !important;
    box-shadow:0 1px 2px rgba(15,23,42,.06) !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:hover {
    border-color:#52657c !important;
}
div[data-testid="stSelectbox"] div[data-baseweb="select"]:focus-within > div {
    border-color:#2f6db5 !important;
    box-shadow:0 0 0 2px rgba(47,109,181,.16) !important;
}
</style>
"""

def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = (
                v.replace(",", "")
                .replace("원", "")
                .replace("개", "")
                .replace("건", "")
                .replace("%", "")
                .strip()
            )
        x = float(v or 0)
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
    s = str(v or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s

def _fmt_money(v: Any) -> str:
    return f"{int(round(_num(v))):,}원"

def _fmt_qty(v: Any) -> str:
    n = _num(v)
    return f"{int(round(n)):,}개" if abs(n-round(n)) < 1e-9 else f"{n:,.1f}개"

def _fmt_pct(v: Any) -> str:
    return f"{_num(v):,.1f}%"

def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None

def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    safe = str(table).replace('"', '""')
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{safe}")').fetchall()}

def _now(core) -> str:
    try:
        return str(core.now_iso())
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def _ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS monthly_product_goals(
                month TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                target_qty REAL NOT NULL DEFAULT 0,
                target_revenue REAL NOT NULL DEFAULT 0,
                target_ad_spend REAL NOT NULL DEFAULT 0,
                target_profit REAL NOT NULL DEFAULT 0,
                memo TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(month, product_id)
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS monthly_goal_reviews(
                month TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                reason TEXT,
                note TEXT,
                reviewed_at TEXT NOT NULL,
                PRIMARY KEY(month, product_id)
            )"""
        )
        c.execute(
            "CREATE INDEX IF NOT EXISTS ix_monthly_product_goals_month "
            "ON monthly_product_goals(month)"
        )

def _add_month(month: str, delta: int) -> str:
    y, m = [int(x) for x in month.split("-")]
    n = y * 12 + (m - 1) + int(delta)
    return f"{n//12:04d}-{n%12+1:02d}"

def _month_options() -> list[str]:
    cur = date.today().strftime("%Y-%m")
    values = [cur, _add_month(cur, 1), _add_month(cur, 2)]
    values += [_add_month(cur, -i) for i in range(1, 13)]
    out = []
    for x in values:
        if x not in out:
            out.append(x)
    return out

def _month_label(month: str) -> str:
    y, m = [int(x) for x in month.split("-")]
    return f"{y}년 {m}월"

def _month_bounds(month: str):
    y, m = [int(x) for x in month.split("-")]
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])

def _products(core, db, active_only=True) -> pd.DataFrame:
    _ensure_schema(core, db)
    with core._conn(db) as c:
        pc = _cols(c, "products")
        if not {"id", "name"}.issubset(pc):
            return pd.DataFrame(columns=["id", "option_id", "item_code", "name", "active"])
        option = "option_id" if "option_id" in pc else "''"
        code = "item_code" if "item_code" in pc else "''"
        active = "active" if "active" in pc else "1"
        cond = []
        if "item_type" in pc:
            cond.append("item_type='finished'")
        if active_only and "active" in pc:
            cond.append("COALESCE(active,1)=1")
        where = (" WHERE " + " AND ".join(cond)) if cond else ""
        return pd.read_sql_query(
            f"""SELECT id,{option} option_id,{code} item_code,name,{active} active
                FROM products{where}
                ORDER BY name,item_code""",
            c,
        )

def _goals(core, db, month: str) -> pd.DataFrame:
    _ensure_schema(core, db)
    with core._conn(db) as c:
        rows = pd.read_sql_query(
            """SELECT g.month,g.product_id,g.target_qty,g.target_revenue,
                      g.target_ad_spend,g.target_profit,g.memo,g.updated_at,
                      p.option_id,p.item_code,p.name,p.active
               FROM monthly_product_goals g
               JOIN products p ON p.id=g.product_id
               WHERE g.month=?
               ORDER BY p.name,p.item_code""",
            c,
            params=(month,),
        )
    return rows

def _save_goal(core, db, month: str, product_id: int, qty, revenue, ad, profit, memo):
    _ensure_schema(core, db)
    q, r, a, p = map(_num, (qty, revenue, ad, profit))
    memo = str(memo or "").strip()
    with core._conn(db) as c:
        if abs(q)+abs(r)+abs(a)+abs(p) <= 1e-12 and not memo:
            c.execute(
                "DELETE FROM monthly_product_goals WHERE month=? AND product_id=?",
                (month, int(product_id)),
            )
            c.execute(
                "DELETE FROM monthly_goal_reviews WHERE month=? AND product_id=?",
                (month, int(product_id)),
            )
            return
        c.execute(
            """INSERT INTO monthly_product_goals
               (month,product_id,target_qty,target_revenue,target_ad_spend,target_profit,memo,updated_at)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(month,product_id) DO UPDATE SET
                 target_qty=excluded.target_qty,
                 target_revenue=excluded.target_revenue,
                 target_ad_spend=excluded.target_ad_spend,
                 target_profit=excluded.target_profit,
                 memo=excluded.memo,
                 updated_at=excluded.updated_at""",
            (month, int(product_id), q, r, a, p, memo, _now(core)),
        )

def _copy_previous_goals(core, db, month: str, overwrite=False) -> int:
    prev = _add_month(month, -1)
    _ensure_schema(core, db)
    with core._conn(db) as c:
        prev_rows = c.execute(
            """SELECT product_id,target_qty,target_revenue,target_ad_spend,target_profit,memo
               FROM monthly_product_goals WHERE month=?""",
            (prev,),
        ).fetchall()
        count = 0
        for r in prev_rows:
            exists = c.execute(
                "SELECT 1 FROM monthly_product_goals WHERE month=? AND product_id=?",
                (month, int(r["product_id"])),
            ).fetchone()
            if exists and not overwrite:
                continue
            c.execute(
                """INSERT INTO monthly_product_goals
                   (month,product_id,target_qty,target_revenue,target_ad_spend,target_profit,memo,updated_at)
                   VALUES(?,?,?,?,?,?,?,?)
                   ON CONFLICT(month,product_id) DO UPDATE SET
                     target_qty=excluded.target_qty,
                     target_revenue=excluded.target_revenue,
                     target_ad_spend=excluded.target_ad_spend,
                     target_profit=excluded.target_profit,
                     memo=excluded.memo,
                     updated_at=excluded.updated_at""",
                (
                    month, int(r["product_id"]), _num(r["target_qty"]),
                    _num(r["target_revenue"]), _num(r["target_ad_spend"]),
                    _num(r["target_profit"]), str(r["memo"] or ""), _now(core)
                ),
            )
            count += 1
    return count

def _ad_by_option(core, db, month: str) -> dict[str, float]:
    start, end = _month_bounds(month)
    with core._conn(db) as c:
        if not (_exists(c, "provisional_ad_report_imports") and _exists(c, "provisional_ad_report_items")):
            return {}
        rows = c.execute(
            """SELECT x.option_id,SUM(COALESCE(x.ad_spend,0)) ad_spend
               FROM provisional_ad_report_items x
               JOIN provisional_ad_report_imports i ON i.id=x.import_id
               WHERE i.period_end>=? AND i.period_start<=?
               GROUP BY x.option_id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return {_oid(r["option_id"]): _num(r["ad_spend"]) for r in rows if _oid(r["option_id"])}

def _provisional_actuals(core, db, month: str) -> dict[int, dict[str, float]]:
    try:
        importlib.import_module("pnl_snapshot_refresh_v0966").refresh_month(core, month, db)
    except Exception:
        pass
    helper = importlib.import_module("pnl_month_default_v0914")
    try:
        rows, _excluded = helper._snapshot_rows_for_month(core, db, month)
        df = helper._aggregate(rows)
    except Exception:
        df = pd.DataFrame()
    products = _products(core, db, active_only=False)
    by_oid = {}
    for r in products.itertuples(index=False):
        oid = _oid(getattr(r, "option_id", "")) or _oid(getattr(r, "item_code", ""))
        if oid:
            by_oid[oid] = int(r.id)
    out = {}
    if not df.empty:
        for r in df.to_dict("records"):
            oid = _oid(r.get("옵션ID"))
            pid = by_oid.get(oid)
            if pid is None:
                continue
            x = out.setdefault(pid, {"qty":0.0,"revenue":0.0,"ad":0.0,"profit":0.0,"source":"잠정"})
            x["qty"] += _num(r.get("판매수량"))
            x["revenue"] += _num(r.get("예상매출"))
            x["ad"] += abs(_num(r.get("광고비")))
            x["profit"] += _num(r.get("예상이익"))
    return out

def _confirmed_actuals(core, db, month: str, provisional: dict[int, dict[str, float]]):
    try:
        mdf, _meta = core.confirmed_monthly_pnl(month)
    except Exception:
        return {}
    if mdf is None or mdf.empty:
        return {}
    products = _products(core, db, active_only=False)
    by_id = {}
    by_oid = {}
    for r in products.itertuples(index=False):
        oid = _oid(getattr(r, "option_id", "")) or _oid(getattr(r, "item_code", ""))
        by_id[int(r.id)] = oid
        if oid:
            by_oid[oid] = int(r.id)
    ad_report = _ad_by_option(core, db, month)
    out = {}
    for _, r in mdf.iterrows():
        pid = int(_num(r.get("product_id"))) if "product_id" in mdf.columns else 0
        oid = by_id.get(pid, "")
        if not pid:
            for col in ("option_id","옵션ID","쿠팡 옵션ID"):
                if col in mdf.columns:
                    oid = _oid(r.get(col))
                    if oid:
                        pid = by_oid.get(oid, 0)
                        break
        if not pid:
            continue
        x = out.setdefault(
            int(pid),
            {"qty": _num((provisional.get(int(pid)) or {}).get("qty")),
             "revenue":0.0,"cogs":0.0,"commission":0.0,"rg":0.0,
             "returns":0.0,"ad_cost_rows":0.0,"source":"확정"},
        )
        x["revenue"] += _num(r.get("realized_sales"))
        x["cogs"] += abs(_num(r.get("cogs")))
        x["commission"] += abs(_num(r.get("commission")))
        x["rg"] += abs(_num(r.get("inout"))) + abs(_num(r.get("delivery")))
        x["returns"] += abs(_num(r.get("return_pickup"))) + abs(_num(r.get("return_restock")))
        if "ad_cost" in mdf.columns:
            x["ad_cost_rows"] += abs(_num(r.get("ad_cost")))
    for pid, x in out.items():
        oid = by_id.get(pid, "")
        if oid in ad_report:
            ad = abs(_num(ad_report[oid]))
        else:
            ad = abs(_num(x.get("ad_cost_rows")))
            if ad <= 1e-12:
                ad = abs(_num((provisional.get(pid) or {}).get("ad")))
        x["ad"] = ad
        x["profit"] = x["revenue"] - x["cogs"] - x["commission"] - x["rg"] - x["returns"] - ad
    return out

def _actuals(core, db, month: str):
    provisional = _provisional_actuals(core, db, month)
    current = date.today().strftime("%Y-%m")
    if month < current:
        confirmed = _confirmed_actuals(core, db, month, provisional)
        if confirmed:
            merged = dict(provisional)
            merged.update(confirmed)
            return merged, "확정"
    return provisional, "잠정"

def _coverage_days(core, db, month: str):
    start, end = _month_bounds(month)
    today = date.today()
    current = today.strftime("%Y-%m")
    if month > current:
        return 0, None
    limit = min(end, today - timedelta(days=1)) if month == current else end
    if limit < start:
        return 0, None
    covered = set()
    with core._conn(db) as c:
        if not _exists(c, "imports"):
            return 0, None
        cols = _cols(c, "imports")
        if not {"period_start","period_end"}.issubset(cols):
            return 0, None
        where = ""
        params = [start.isoformat(), limit.isoformat()]
        if "data_type" in cols:
            where = " AND data_type='sales_stats'"
        rows = c.execute(
            """SELECT period_start,period_end FROM imports
               WHERE period_end>=? AND period_start<=?""" + where,
            tuple(params),
        ).fetchall()
    for r in rows:
        try:
            a = max(start, date.fromisoformat(str(r["period_start"])[:10]))
            b = min(limit, date.fromisoformat(str(r["period_end"])[:10]))
        except Exception:
            continue
        d = a
        while d <= b:
            covered.add(d)
            d += timedelta(days=1)
    cursor = start
    last = None
    while cursor <= limit and cursor in covered:
        last = cursor
        cursor += timedelta(days=1)
    return ((last-start).days+1 if last else 0), last

def _status(target, actual, time_ratio: float):
    checks = []
    for tk, ak in (("target_qty","qty"),("target_profit","profit")):
        t = _num(target.get(tk))
        if t > 0:
            checks.append(_num(actual.get(ak)) / t)
    if not checks:
        t = _num(target.get("target_revenue"))
        if t > 0:
            checks.append(_num(actual.get("revenue")) / t)
    if not checks:
        return "⚪ 목표없음"
    ratio = min(checks)
    if time_ratio >= 0.999:
        return "🟢 달성" if ratio >= 1 else ("🟠 근접" if ratio >= 0.85 else "🔴 미달")
    expected = max(time_ratio, 0.01)
    pace = ratio / expected
    return "🟢 목표초과 진행" if pace >= 1.0 else ("🟠 정상범위" if pace >= 0.8 else "🔴 목표대비 지연")

def _build_progress(goals: pd.DataFrame, actuals: dict[int, dict[str, float]], month: str, core, db):
    start, end = _month_bounds(month)
    days_in_month = (end-start).days+1
    current = date.today().strftime("%Y-%m")
    if month < current:
        covered_days = days_in_month
        coverage_end = end
    elif month == current:
        covered_days, coverage_end = _coverage_days(core, db, month)
    else:
        covered_days, coverage_end = 0, None
    time_ratio = covered_days / days_in_month if days_in_month else 0
    scale = days_in_month / covered_days if (month == current and covered_days > 0) else 1.0
    rows = []
    for r in goals.to_dict("records"):
        pid = int(r["product_id"])
        a = actuals.get(pid, {})
        tq = _num(r["target_qty"])
        tr = _num(r["target_revenue"])
        ta = _num(r["target_ad_spend"])
        tp = _num(r["target_profit"])
        aq = _num(a.get("qty"))
        ar = _num(a.get("revenue"))
        aa = _num(a.get("ad"))
        ap = _num(a.get("profit"))
        rows.append({
            "product_id": pid,
            "상품명": str(r.get("name") or ""),
            "옵션ID": _oid(r.get("option_id")) or _oid(r.get("item_code")),
            "목표판매": tq,
            "현재판매": aq,
            "판매달성률": aq/tq*100 if tq>0 else 0.0,
            "목표매출": tr,
            "현재매출": ar,
            "매출달성률": ar/tr*100 if tr>0 else 0.0,
            "목표이익": tp,
            "현재이익": ap,
            "이익달성률": ap/tp*100 if tp>0 else 0.0,
            "광고예산": ta,
            "광고사용": aa,
            "광고소진률": aa/ta*100 if ta>0 else 0.0,
            "월말예상판매": aq*scale if month == current else aq,
            "월말예상이익": ap*scale if month == current else ap,
            "상태": _status(r, a, time_ratio),
        })
    return pd.DataFrame(rows), {
        "days_in_month":days_in_month,
        "covered_days":covered_days,
        "coverage_end":coverage_end,
        "time_ratio":time_ratio,
    }

def _format_progress(df: pd.DataFrame):
    show = df.drop(columns=["product_id"], errors="ignore").copy()
    if show.empty:
        return show
    for c in ("목표판매","현재판매","월말예상판매"):
        show[c] = show[c].map(_fmt_qty)
    for c in ("목표매출","현재매출","목표이익","현재이익","광고예산","광고사용","월말예상이익"):
        show[c] = show[c].map(_fmt_money)
    for c in ("판매달성률","매출달성률","이익달성률","광고소진률"):
        show[c] = show[c].map(_fmt_pct)
    return show

def _render_overall(st, progress: pd.DataFrame, meta: dict, source_label: str):
    if progress.empty:
        st.info("선택 월에 등록된 상품별 목표가 없습니다.")
        return
    tq = float(progress["목표판매"].sum())
    aq = float(progress["현재판매"].sum())
    tr = float(progress["목표매출"].sum())
    ar = float(progress["현재매출"].sum())
    tp = float(progress["목표이익"].sum())
    ap = float(progress["현재이익"].sum())
    ta = float(progress["광고예산"].sum())
    aa = float(progress["광고사용"].sum())
    c1,c2,c3,c4 = st.columns(4)
    c1.metric("판매 목표", f"{_fmt_qty(aq)} / {_fmt_qty(tq)}", _fmt_pct(aq/tq*100 if tq else 0))
    c2.metric("매출 목표", f"{_fmt_money(ar)} / {_fmt_money(tr)}", _fmt_pct(ar/tr*100 if tr else 0))
    c3.metric("이익 목표", f"{_fmt_money(ap)} / {_fmt_money(tp)}", _fmt_pct(ap/tp*100 if tp else 0))
    c4.metric("광고 예산", f"{_fmt_money(aa)} / {_fmt_money(ta)}", _fmt_pct(aa/ta*100 if ta else 0))
    cov_end = meta.get("coverage_end")
    if cov_end:
        st.caption(
            f"실적 기준: {source_label} · 판매자료가 당월 1일부터 {cov_end.month}월 {cov_end.day}일까지 연속 입력됨 "
            f"({meta.get('covered_days',0)}/{meta.get('days_in_month',0)}일). 월말예상은 이 연속 입력기간의 일평균을 기준으로 계산합니다."
        )
    else:
        st.caption(f"실적 기준: {source_label} · 연속 입력된 판매자료가 없어 월말예상은 계산하지 않습니다.")

def _render_goal_setting(st, core, db, month: str):
    st.markdown("### 상품별 목표 설정")
    st.caption("목표판매수량·목표매출·광고예산·목표이익을 입력합니다. 목표이익률과 목표 ROAS는 자동 계산됩니다.")
    c1,c2,c3 = st.columns([1.2,1.2,4])
    overwrite = c1.checkbox("기존 목표 덮어쓰기", key=f"goal_copy_overwrite_{month}")
    if c2.button("전월 목표 복사", key=f"goal_copy_prev_{month}", use_container_width=True):
        n = _copy_previous_goals(core, db, month, overwrite)
        st.success(f"전월 목표 {n:,}개 상품을 복사했습니다.")
        st.rerun()
    q = st.text_input("상품 검색", placeholder="상품명 또는 옵션ID 입력", key=f"goal_setting_search_{month}")

    products = _products(core, db, active_only=True)
    existing = _goals(core, db, month)
    by_pid = {int(r.product_id): r for r in existing.itertuples(index=False)}
    rows = []
    for p in products.itertuples(index=False):
        if str(q or "").strip():
            hay = f"{p.name} {getattr(p,'option_id','')} {getattr(p,'item_code','')}".lower()
            if not all(x in hay for x in str(q).lower().split()):
                continue
        g = by_pid.get(int(p.id))
        rec = {
            "product_id":int(p.id),
            "상품명":str(p.name or ""),
            "옵션ID":_oid(getattr(p,"option_id","")) or _oid(getattr(p,"item_code","")),
            "목표판매수량":_num(getattr(g,"target_qty",0) if g else 0),
            "목표매출":_num(getattr(g,"target_revenue",0) if g else 0),
            "광고예산":_num(getattr(g,"target_ad_spend",0) if g else 0),
            "목표이익":_num(getattr(g,"target_profit",0) if g else 0),
            "메모":str(getattr(g,"memo","") or "") if g else "",
        }
        rec["목표이익률"] = rec["목표이익"]/rec["목표매출"]*100 if rec["목표매출"]>0 else 0.0
        rec["목표ROAS"] = rec["목표매출"]/rec["광고예산"]*100 if rec["광고예산"]>0 else 0.0
        rows.append(rec)
    frame = pd.DataFrame(rows)
    if frame.empty:
        st.info("검색 조건에 맞는 완제품이 없습니다.")
        return
    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        disabled=["product_id","상품명","옵션ID","목표이익률","목표ROAS"],
        height=min(720, max(280, 36*(len(frame)+1))),
        key=f"goal_editor_{month}",
    )
    if st.button("목표 저장", type="primary", key=f"goal_save_{month}"):
        for r in edited.to_dict("records"):
            _save_goal(
                core, db, month, int(r["product_id"]),
                r.get("목표판매수량"), r.get("목표매출"),
                r.get("광고예산"), r.get("목표이익"), r.get("메모"),
            )
        st.success(f"{_month_label(month)} 목표를 저장했습니다.")
        st.rerun()

def _load_reviews(core, db, month: str) -> dict[int, dict[str,str]]:
    _ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute(
            "SELECT product_id,reason,note FROM monthly_goal_reviews WHERE month=?",
            (month,),
        ).fetchall()
    return {int(r["product_id"]):{"reason":str(r["reason"] or ""),"note":str(r["note"] or "")} for r in rows}

def _save_reviews(core, db, month: str, rows):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        for r in rows:
            pid = int(r["product_id"])
            reason = str(r.get("미달사유") or "").strip()
            note = str(r.get("검토메모") or "").strip()
            if not reason and not note:
                c.execute("DELETE FROM monthly_goal_reviews WHERE month=? AND product_id=?", (month,pid))
                continue
            c.execute(
                """INSERT INTO monthly_goal_reviews(month,product_id,reason,note,reviewed_at)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(month,product_id) DO UPDATE SET
                     reason=excluded.reason,note=excluded.note,reviewed_at=excluded.reviewed_at""",
                (month,pid,reason,note,_now(core)),
            )

def _render_review(st, core, db, month: str, progress: pd.DataFrame, source_label: str):
    st.markdown("### 월말 목표 검증")
    current = date.today().strftime("%Y-%m")
    if month >= current:
        st.warning("월말 전이므로 현재 입력된 자료 기준의 잠정 검증입니다. 월 정산자료가 들어오면 과거 월은 확정손익 기준으로 자동 전환됩니다.")
    elif source_label != "확정":
        st.warning("이 월의 확정 정산자료가 없어 잠정손익 기준으로 검증합니다.")
    if progress.empty:
        st.info("검증할 목표가 없습니다.")
        return
    rows=[]
    reviews=_load_reviews(core,db,month)
    for r in progress.to_dict("records"):
        tq,tr,tp = _num(r["목표판매"]),_num(r["목표매출"]),_num(r["목표이익"])
        aq,ar,ap = _num(r["현재판매"]),_num(r["현재매출"]),_num(r["현재이익"])
        checks=[]
        if tq>0: checks.append(aq>=tq)
        if tp>0: checks.append(ap>=tp)
        if not checks and tr>0: checks.append(ar>=tr)
        state="달성" if checks and all(checks) else "미달"
        saved=reviews.get(int(r["product_id"]),{})
        rows.append({
            "product_id":int(r["product_id"]),
            "상품명":r["상품명"],
            "상태":state,
            "목표판매":tq,"실제판매":aq,"판매차이":aq-tq,
            "목표매출":tr,"실제매출":ar,"매출차이":ar-tr,
            "목표이익":tp,"실제이익":ap,"이익차이":ap-tp,
            "미달사유":saved.get("reason",""),
            "검토메모":saved.get("note",""),
        })
    df=pd.DataFrame(rows)
    show=df.drop(columns=["product_id","미달사유","검토메모"]).copy()
    for c in ("목표판매","실제판매","판매차이"):
        show[c]=show[c].map(_fmt_qty)
    for c in ("목표매출","실제매출","매출차이","목표이익","실제이익","이익차이"):
        show[c]=show[c].map(_fmt_money)
    st.dataframe(show,use_container_width=True,hide_index=True)
    st.markdown("#### 검토 메모")
    review_edit=st.data_editor(
        df[["product_id","상품명","상태","미달사유","검토메모"]],
        use_container_width=True,hide_index=True,
        disabled=["product_id","상품명","상태"],
        key=f"goal_review_editor_{month}",
    )
    if st.button("검토내용 저장", type="primary", key=f"goal_review_save_{month}"):
        _save_reviews(core,db,month,review_edit.to_dict("records"))
        st.success("월말 검토내용을 저장했습니다.")
        st.rerun()

def _render_history(st, core, db):
    st.markdown("### 상품별 목표 이력")
    _ensure_schema(core,db)
    with core._conn(db) as c:
        rows=c.execute(
            """SELECT DISTINCT g.product_id,p.name,p.option_id,p.item_code
               FROM monthly_product_goals g JOIN products p ON p.id=g.product_id
               ORDER BY p.name"""
        ).fetchall()
    if not rows:
        st.info("저장된 목표 이력이 없습니다.")
        return
    ids=[int(r["product_id"]) for r in rows]
    labels={
        int(r["product_id"]): f"{str(r['name'] or '')} · {_oid(r['option_id']) or _oid(r['item_code'])}"
        for r in rows
    }
    pid=st.selectbox("상품 선택",ids,format_func=lambda x:labels.get(int(x),str(x)),key="goal_history_product")
    with core._conn(db) as c:
        goals=c.execute(
            """SELECT month,target_qty,target_revenue,target_ad_spend,target_profit,memo
               FROM monthly_product_goals
               WHERE product_id=? ORDER BY month DESC LIMIT 18""",
            (int(pid),),
        ).fetchall()
    history=[]
    cache={}
    for g in goals:
        mon=str(g["month"])
        if mon not in cache:
            cache[mon]=_actuals(core,db,mon)
        actuals,source=cache[mon]
        a=actuals.get(int(pid),{})
        history.append({
            "월":mon,"기준":source,
            "목표판매":_num(g["target_qty"]),"실제판매":_num(a.get("qty")),
            "판매달성률":_num(a.get("qty"))/_num(g["target_qty"])*100 if _num(g["target_qty"])>0 else 0,
            "목표매출":_num(g["target_revenue"]),"실제매출":_num(a.get("revenue")),
            "목표이익":_num(g["target_profit"]),"실제이익":_num(a.get("profit")),
            "목표광고":_num(g["target_ad_spend"]),"실제광고":_num(a.get("ad")),
            "메모":str(g["memo"] or ""),
        })
    df=pd.DataFrame(history)
    show=df.copy()
    for c in ("목표판매","실제판매"):
        show[c]=show[c].map(_fmt_qty)
    show["판매달성률"]=show["판매달성률"].map(_fmt_pct)
    for c in ("목표매출","실제매출","목표이익","실제이익","목표광고","실제광고"):
        show[c]=show[c].map(_fmt_money)
    st.dataframe(show,use_container_width=True,hide_index=True)
    if len(df)>=2:
        chart=df.sort_values("월").set_index("월")[["목표판매","실제판매"]]
        st.markdown("#### 판매수량 목표 대비 실제")
        st.line_chart(chart,height=280)

def render_page(st, pd_obj, core, db_path=None):
    db=db_path or core.DEFAULT_DB
    _ensure_schema(core,db)
    st.markdown(_SELECT_CSS,unsafe_allow_html=True)
    st.markdown("## 🎯 목표·실적관리")
    st.caption("판매상품별 월 목표를 세우고, 월중 진행속도와 월말 실제 성과를 한 화면에서 검증합니다.")

    months=_month_options()
    month=st.selectbox(
        "목표·검증 월",months,index=0,
        format_func=_month_label,
        key="goal_management_month_v0979",
    )
    goals=_goals(core,db,month)
    actuals,source_label=_actuals(core,db,month)
    progress,meta=_build_progress(goals,actuals,month,core,db)

    tabs=st.tabs(["진행현황","목표 설정","월말검증","목표이력"])
    with tabs[0]:
        _render_overall(st,progress,meta,source_label)
        st.markdown("### 상품별 진행현황")
        if progress.empty:
            st.info("이 달에 등록된 목표가 없습니다. '목표 설정' 탭에서 상품별 목표를 입력하세요.")
        else:
            q=st.text_input("진행현황 상품 검색",placeholder="상품명 또는 옵션ID 입력",key=f"goal_progress_search_{month}")
            view=progress.copy()
            if str(q or "").strip():
                words=str(q).lower().split()
                hay=(view["상품명"].fillna("")+" "+view["옵션ID"].fillna("")).str.lower()
                mask=pd.Series(True,index=view.index)
                for w in words:
                    mask &= hay.str.contains(w,regex=False,na=False)
                view=view.loc[mask]
            st.dataframe(_format_progress(view),use_container_width=True,hide_index=True)
            st.caption("상태는 판매수량과 이익 목표의 달성속도를 우선 비교합니다. 월말예상은 당월 1일부터 연속 입력된 판매자료의 실제 경과일을 기준으로 계산합니다.")
    with tabs[1]:
        _render_goal_setting(st,core,db,month)
    with tabs[2]:
        _render_review(st,core,db,month,progress,source_label)
    with tabs[3]:
        _render_history(st,core,db)

def apply_sidebar(sidebar_module):
    try:
        groups=getattr(sidebar_module,"_GROUPS",[])
        for title,items in groups:
            if str(title)=="💰 손익·정산":
                if PAGE_LABEL not in items:
                    try:
                        idx=items.index("📈  잠정손익")+1
                    except ValueError:
                        idx=0
                    items.insert(idx,PAGE_LABEL)
                return sidebar_module
    except Exception:
        pass
    return sidebar_module

def patch_source(source: str) -> str:
    if PAGE_LABEL not in source:
        anchor='        "📈  잠정손익",\n'
        if anchor not in source:
            raise RuntimeError("v0.9.79 목표·실적관리 메뉴 위치를 찾지 못했습니다.")
        source=source.replace(anchor,anchor+f'        "{PAGE_LABEL}",\n',1)
    handler=f'elif page == "{PAGE_LABEL}":'
    if handler not in source:
        anchor='elif page == "📄  자료별 잠정손익":'
        pos=source.find(anchor)
        if pos<0:
            raise RuntimeError("v0.9.79 목표·실적관리 화면 위치를 찾지 못했습니다.")
        block=(
            f'{handler}\n'
            '    pnl_month_default_v0915.render_goal_management_page(st, pd, core)\n\n\n'
        )
        source=source[:pos]+block+source[pos:]
    return source
