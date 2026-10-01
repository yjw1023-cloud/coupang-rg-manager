"""RG Manager v0.9.274 new-product growth management."""
from __future__ import annotations

from datetime import date, timedelta
import importlib
import math
from typing import Any

import pandas as pd
import streamlit as st

PAGE_TEXT = "🚀  신규상품 육성관리"
STATUSES = ["신규", "광고육성", "관찰", "안정화", "정리/처분"]
PERIODS = (7, 14, 30, 60, 90)


def _num(v: Any) -> float:
    try:
        x = float(v or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    s = "" if v is None else str(v).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _ensure_schema(core, db):
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS product_growth_management(
                product_id INTEGER PRIMARY KEY,
                launch_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT '신규',
                target_daily_organic REAL NOT NULL DEFAULT 4,
                memo TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS product_growth_seen(
                product_id INTEGER PRIMARY KEY,
                first_seen_at TEXT NOT NULL
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS product_growth_snapshots(
                snapshot_date TEXT NOT NULL,
                product_id INTEGER NOT NULL,
                sales_7 REAL NOT NULL DEFAULT 0,
                organic_7 REAL NOT NULL DEFAULT 0,
                ad_sales_7 REAL NOT NULL DEFAULT 0,
                ad_spend_7 REAL NOT NULL DEFAULT 0,
                sales_30 REAL NOT NULL DEFAULT 0,
                organic_30 REAL NOT NULL DEFAULT 0,
                ad_sales_30 REAL NOT NULL DEFAULT 0,
                ad_spend_30 REAL NOT NULL DEFAULT 0,
                organic_ratio_30 REAL NOT NULL DEFAULT 0,
                captured_at TEXT NOT NULL,
                PRIMARY KEY(snapshot_date, product_id)
            )"""
        )



def _sync_new_products_after_baseline(core, db) -> dict:
    """Seed today's baseline once, then auto-enroll every newly registered finished product.

    This deliberately does not depend on a products.created_at column. On the first
    run after this feature is installed, every existing finished product is marked
    as already seen without adding it to growth management. On later runs, any new
    finished product ID is automatically added with today's date as launch_date.
    A product the user later removes from growth management stays removed because
    it remains in product_growth_seen.
    """
    _ensure_schema(core, db)
    now = core.now_iso()
    today = date.today().isoformat()
    with core._conn(db) as c:
        products = c.execute(
            """SELECT id
               FROM products
               WHERE item_type='finished'
                 AND COALESCE(active,1)=1
                 AND COALESCE(TRIM(CAST(option_id AS TEXT)),'')<>''"""
        ).fetchall()
        pids = [int(r["id"]) for r in products]
        seen_count = int(c.execute("SELECT COUNT(*) n FROM product_growth_seen").fetchone()["n"] or 0)

        # First execution after installing this version: establish the baseline only.
        if seen_count == 0:
            c.executemany(
                "INSERT OR IGNORE INTO product_growth_seen(product_id,first_seen_at) VALUES(?,?)",
                [(pid, now) for pid in pids],
            )
            return {"baseline_seeded": len(pids), "auto_added": 0}

        seen = {
            int(r["product_id"])
            for r in c.execute("SELECT product_id FROM product_growth_seen").fetchall()
        }
        new_ids = [pid for pid in pids if pid not in seen]
        added = 0
        for pid in new_ids:
            c.execute(
                "INSERT OR IGNORE INTO product_growth_seen(product_id,first_seen_at) VALUES(?,?)",
                (pid, now),
            )
            exists = c.execute(
                "SELECT 1 FROM product_growth_management WHERE product_id=?", (pid,)
            ).fetchone()
            if exists:
                continue
            c.execute(
                """INSERT INTO product_growth_management
                   (product_id,launch_date,status,target_daily_organic,memo,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (pid, today, "신규", 4.0, "자동등록", now, now),
            )
            added += 1
    return {"baseline_seeded": 0, "auto_added": added}


def _products(core, db) -> list[dict]:
    with core._conn(db) as c:
        if not _exists(c, "products"):
            return []
        rows = c.execute(
            """SELECT id,item_code,option_id,name,active,unit_cost,item_type
               FROM products
               WHERE item_type='finished'
                 AND COALESCE(TRIM(CAST(option_id AS TEXT)),'')<>''
               ORDER BY COALESCE(active,1) DESC,name,id"""
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["id"] = int(d["id"])
        d["option_id"] = _oid(d.get("option_id"))
        out.append(d)
    return out


def _first_sale_date(core, db, product_id: int) -> str | None:
    with core._conn(db) as c:
        if _exists(c, "coupang_rg_order_items"):
            cols = _cols(c, "coupang_rg_order_items")
            if {"product_id", "paid_date"}.issubset(cols):
                r = c.execute(
                    """SELECT MIN(paid_date) d FROM coupang_rg_order_items
                       WHERE product_id=? AND paid_date IS NOT NULL AND paid_date<>''""",
                    (int(product_id),),
                ).fetchone()
                if r and r["d"]:
                    return str(r["d"])[:10]
        if _exists(c, "sales_stats") and _exists(c, "imports"):
            sc = _cols(c, "sales_stats")
            if {"product_id", "import_id"}.issubset(sc):
                r = c.execute(
                    """SELECT MIN(i.period_start) d
                       FROM sales_stats s JOIN imports i ON i.id=s.import_id
                       WHERE s.product_id=? AND i.data_type='sales_stats'
                         AND i.period_start IS NOT NULL""",
                    (int(product_id),),
                ).fetchone()
                if r and r["d"]:
                    return str(r["d"])[:10]
    return None


def _managed(core, db) -> list[dict]:
    _ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT g.product_id,g.launch_date,g.status,g.target_daily_organic,g.memo,
                      p.item_code,p.option_id,p.name,p.active,p.unit_cost
               FROM product_growth_management g
               JOIN products p ON p.id=g.product_id
               ORDER BY g.launch_date DESC,p.name"""
        ).fetchall()
    return [dict(r) for r in rows]


def _save_management(core, db, product_id: int, launch_date, status: str, target: float, memo: str):
    if status not in STATUSES:
        status = "신규"
    now = core.now_iso()
    with core._conn(db) as c:
        c.execute(
            """INSERT INTO product_growth_management
               (product_id,launch_date,status,target_daily_organic,memo,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(product_id) DO UPDATE SET
                 launch_date=excluded.launch_date,
                 status=excluded.status,
                 target_daily_organic=excluded.target_daily_organic,
                 memo=excluded.memo,
                 updated_at=excluded.updated_at""",
            (int(product_id), str(launch_date)[:10], status, float(target), str(memo or ""), now, now),
        )


def _remove_management(core, db, product_id: int):
    with core._conn(db) as c:
        c.execute("DELETE FROM product_growth_management WHERE product_id=?", (int(product_id),))


def _auto_register_recent(core, db, days: int = 120) -> int:
    cutoff = date.today() - timedelta(days=max(1, int(days)))
    existing = {int(x["product_id"]) for x in _managed(core, db)}
    count = 0
    for p in _products(core, db):
        pid = int(p["id"])
        if pid in existing or int(_num(p.get("active"))) == 0:
            continue
        first = _first_sale_date(core, db, pid)
        if not first:
            continue
        try:
            first_d = date.fromisoformat(first[:10])
        except Exception:
            continue
        if first_d < cutoff:
            continue
        _save_management(core, db, pid, first_d.isoformat(), "신규", 4.0, "")
        count += 1
    return count


def _normal_maps(core, db):
    matcher = importlib.import_module("shared_return_match_ui_v09217")
    confirmed = matcher.confirmed_alias_map(core, db)
    normal_map = matcher.normal_option_to_product(core, db)
    direct = {}
    products = {}
    with core._conn(db) as c:
        rows = c.execute("SELECT id,option_id,item_type,unit_cost,active FROM products").fetchall()
    for r in rows:
        pid = int(r["id"])
        oid = matcher._oid(r["option_id"])
        d = {
            "item_type": str(r["item_type"] or ""),
            "unit_cost": _num(r["unit_cost"]),
            "active": int(_num(r["active"])),
        }
        products[pid] = d
        if not oid or d["item_type"] != "finished" or d["unit_cost"] <= 0:
            continue
        prev = direct.get(oid)
        if prev is None:
            direct[oid] = pid
        else:
            old = products.get(prev, {})
            if (d["active"], -pid) > (int(old.get("active") or 0), -int(prev)):
                direct[oid] = pid
    return matcher, confirmed, normal_map, direct


def _ad_spend(core, db, start: date, end: date) -> dict[int, float]:
    source = importlib.import_module("organic_sales_estimate_v09211")
    sales = importlib.import_module("sales_analysis_v09186")
    matcher, confirmed, normal_map, direct = _normal_maps(core, db)
    totals: dict[int, float] = {}
    with core._conn(db) as c:
        imports = source._contained_imports(c, sales, "ad_performance", start, end)
        if not imports or not _exists(c, "ad_performance"):
            return totals
        cols = _cols(c, "ad_performance")
        if not {"import_id", "option_id", "spend"}.issubset(cols):
            return totals
        ids = [int(r["id"]) for r in imports]
        marks = ",".join("?" for _ in ids)
        rows = c.execute(
            f"""SELECT option_id,SUM(COALESCE(spend,0)) spend
                FROM ad_performance
                WHERE import_id IN ({marks})
                GROUP BY option_id""",
            ids,
        ).fetchall()
    for r in rows:
        oid = matcher._oid(r["option_id"])
        pid = int(confirmed.get(oid) or direct.get(oid) or normal_map.get(oid) or 0)
        if pid > 0:
            totals[pid] = totals.get(pid, 0.0) + max(0.0, _num(r["spend"]))
    return totals


def _cumulative_ad_spend(core, db, managed) -> dict[int, float]:
    launch_by_pid = {int(x["product_id"]): str(x["launch_date"])[:10] for x in managed}
    if not launch_by_pid:
        return {}
    matcher, confirmed, normal_map, direct = _normal_maps(core, db)
    totals = {pid: 0.0 for pid in launch_by_pid}
    with core._conn(db) as c:
        if not (_exists(c, "imports") and _exists(c, "ad_performance")):
            return totals
        ac = _cols(c, "ad_performance")
        if not {"import_id", "option_id", "spend"}.issubset(ac):
            return totals
        rows = c.execute(
            """SELECT a.import_id,a.option_id,SUM(COALESCE(a.spend,0)) spend,
                      i.period_start,i.period_end
               FROM ad_performance a
               JOIN imports i ON i.id=a.import_id
               WHERE i.data_type='ad_performance'
                 AND i.period_start IS NOT NULL
                 AND i.period_end IS NOT NULL
                 AND i.period_end<=?
               GROUP BY a.import_id,a.option_id,i.period_start,i.period_end""",
            (date.today().isoformat(),),
        ).fetchall()
    for r in rows:
        oid = matcher._oid(r["option_id"])
        pid = int(confirmed.get(oid) or direct.get(oid) or normal_map.get(oid) or 0)
        launch = launch_by_pid.get(pid)
        if not launch or str(r["period_start"])[:10] < launch:
            continue
        totals[pid] = totals.get(pid, 0.0) + max(0.0, _num(r["spend"]))
    return totals


def _period_data(core, db, start: date, end: date):
    organic = importlib.import_module("organic_sales_display_v09216")
    source = importlib.import_module("organic_sales_estimate_v09211")
    sales = importlib.import_module("sales_analysis_v09186")
    frame, covered, ad_qty_available = organic._canonical_data(core, sales, source, db, start, end)
    spend = _ad_spend(core, db, start, end)
    by_pid = {}
    if frame is not None and not frame.empty:
        for row in frame.to_dict("records"):
            pid = int(_num(row.get("product_id")))
            if pid <= 0:
                continue
            by_pid[pid] = {
                "sales": _num(row.get("판매량")),
                "ad_sales": _num(row.get("광고 판매량")),
                "organic": _num(row.get("Organic 판매량")),
                "organic_ratio": _num(row.get("Organic 판매 비율")),
                "ad_spend": _num(spend.get(pid)),
            }
    return by_pid, len(covered or []), bool(ad_qty_available)


def _all_metrics(core, db):
    out, coverage, ad_ok = {}, {}, {}
    end = date.today()
    for days in PERIODS:
        start = end - timedelta(days=days - 1)
        out[days], coverage[days], ad_ok[days] = _period_data(core, db, start, end)
    prev_end = end - timedelta(days=30)
    prev_start = prev_end - timedelta(days=29)
    out["prev30"], coverage["prev30"], ad_ok["prev30"] = _period_data(core, db, prev_start, prev_end)
    return out, coverage, ad_ok


def _metric(metrics, period, pid):
    return metrics.get(period, {}).get(
        int(pid),
        {"sales": 0.0, "ad_sales": 0.0, "organic": 0.0, "organic_ratio": 0.0, "ad_spend": 0.0},
    )


def _save_daily_snapshots(core, db, managed, metrics):
    today = date.today().isoformat()
    now = core.now_iso()
    with core._conn(db) as c:
        for row in managed:
            pid = int(row["product_id"])
            m7 = _metric(metrics, 7, pid)
            m30 = _metric(metrics, 30, pid)
            c.execute(
                """INSERT INTO product_growth_snapshots(
                     snapshot_date,product_id,sales_7,organic_7,ad_sales_7,ad_spend_7,
                     sales_30,organic_30,ad_sales_30,ad_spend_30,organic_ratio_30,captured_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(snapshot_date,product_id) DO UPDATE SET
                     sales_7=excluded.sales_7,organic_7=excluded.organic_7,
                     ad_sales_7=excluded.ad_sales_7,ad_spend_7=excluded.ad_spend_7,
                     sales_30=excluded.sales_30,organic_30=excluded.organic_30,
                     ad_sales_30=excluded.ad_sales_30,ad_spend_30=excluded.ad_spend_30,
                     organic_ratio_30=excluded.organic_ratio_30,captured_at=excluded.captured_at""",
                (
                    today, pid,
                    m7["sales"], m7["organic"], m7["ad_sales"], m7["ad_spend"],
                    m30["sales"], m30["organic"], m30["ad_sales"], m30["ad_spend"],
                    m30["organic_ratio"], now,
                ),
            )


def _snapshot_frame(core, db, product_id: int) -> pd.DataFrame:
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT snapshot_date,sales_30,organic_30,ad_sales_30,ad_spend_30,organic_ratio_30
               FROM product_growth_snapshots
               WHERE product_id=?
               ORDER BY snapshot_date""",
            (int(product_id),),
        ).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def _fmt_qty(v):
    x = _num(v)
    return f"{int(round(x)):,}" if abs(x - round(x)) < 1e-9 else f"{x:,.1f}"


def _fmt_money(v):
    return f"{int(round(_num(v))):,}원"


def _status_hint(m30, prev30, target):
    daily = _num(m30["organic"]) / 30.0
    prev_daily = _num(prev30["organic"]) / 30.0
    target = max(0.1, _num(target) or 4.0)
    if daily >= target and _num(m30["organic_ratio"]) >= 60:
        return "안정화 후보"
    if daily >= target:
        return "목표 도달"
    if daily > prev_daily * 1.25 and daily >= target * 0.5:
        return "육성 계속"
    if _num(m30["sales"]) <= 0:
        return "자료 확인"
    if daily < target * 0.35 and prev_daily > 0 and daily <= prev_daily * 1.05:
        return "정리 검토"
    return "관찰"


def _overview_frame(managed, metrics):
    rows = []
    today = date.today()
    for g in managed:
        pid = int(g["product_id"])
        m30 = _metric(metrics, 30, pid)
        prev = _metric(metrics, "prev30", pid)
        try:
            launch = date.fromisoformat(str(g["launch_date"])[:10])
            age = max(0, (today - launch).days + 1)
        except Exception:
            age = 0
        org_daily = m30["organic"] / 30.0
        prev_daily = prev["organic"] / 30.0
        rows.append({
            "상태": str(g["status"]),
            "상품명": str(g["name"]),
            "상품코드": str(g.get("item_code") or ""),
            "런칭일": str(g["launch_date"])[:10],
            "경과일": age,
            "30일 판매": m30["sales"],
            "30일 일평균": m30["sales"] / 30.0,
            "30일 광고비": m30["ad_spend"],
            "광고판매": m30["ad_sales"],
            "오가닉판매": m30["organic"],
            "오가닉 일평균": org_daily,
            "오가닉비율": m30["organic_ratio"],
            "이전30일 오가닉": prev["organic"],
            "오가닉 일평균증감": org_daily - prev_daily,
            "판단": _status_hint(m30, prev, g["target_daily_organic"]),
            "product_id": pid,
        })
    return pd.DataFrame(rows)


def _render_add(core, db):
    products = _products(core, db)
    managed_ids = {int(x["product_id"]) for x in _managed(core, db)}
    candidates = [p for p in products if int(p["id"]) not in managed_ids and int(_num(p.get("active"))) == 1]
    with st.expander("➕ 육성상품 추가", expanded=not bool(managed_ids)):
        c1, c2 = st.columns([2, 1])
        if c1.button("최근 120일 첫 판매 상품 자동등록", use_container_width=True, key="growth_auto_recent_v09274"):
            n = _auto_register_recent(core, db, 120)
            st.success(f"{n:,}개 상품을 육성관리 목록에 추가했습니다.")
            st.rerun()
        c2.caption("주문API/판매자료에서 확인되는 첫 판매일을 런칭일로 사용")
        if not candidates:
            st.info("추가할 활성 완제품이 없습니다.")
            return
        by_id = {int(p["id"]): p for p in candidates}
        pid = st.selectbox(
            "상품",
            list(by_id),
            format_func=lambda x: f"{by_id[int(x)]['name']} · {by_id[int(x)].get('item_code') or '-'} · 옵션ID {by_id[int(x)].get('option_id') or '-'}",
            key="growth_add_product_v09274",
        )
        suggested = _first_sale_date(core, db, int(pid)) or date.today().isoformat()
        try:
            suggested_date = date.fromisoformat(suggested[:10])
        except Exception:
            suggested_date = date.today()
        launch = st.date_input("런칭일", value=suggested_date, key="growth_add_launch_v09274")
        target = st.number_input(
            "목표 오가닉 일판매", min_value=0.1, max_value=1000.0,
            value=4.0, step=0.5, key="growth_add_target_v09274",
        )
        if st.button("육성관리 시작", type="primary", key="growth_add_save_v09274"):
            _save_management(core, db, int(pid), launch.isoformat(), "신규", float(target), "")
            st.success("육성관리 목록에 추가했습니다.")
            st.rerun()


def _render_detail(core, db, managed, metrics, cumulative_ad):
    st.markdown("### 상품 상세")
    by_id = {int(x["product_id"]): x for x in managed}
    pid = st.selectbox(
        "상세 상품", list(by_id),
        format_func=lambda x: by_id[int(x)]["name"],
        key="growth_detail_pid_v09274",
    )
    g = by_id[int(pid)]
    m30 = _metric(metrics, 30, pid)
    prev = _metric(metrics, "prev30", pid)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("최근 30일 판매", f"{_fmt_qty(m30['sales'])}개")
    c2.metric("오가닉 일평균", f"{m30['organic']/30.0:,.1f}개", delta=f"{m30['organic']/30.0 - prev['organic']/30.0:+.1f}개")
    c3.metric("30일 오가닉", f"{_fmt_qty(m30['organic'])}개")
    c4.metric("오가닉 비율", f"{m30['organic_ratio']:,.1f}%")
    c5.metric("30일 광고비", _fmt_money(m30["ad_spend"]))
    c6.metric("런칭 후 광고비", _fmt_money(cumulative_ad.get(int(pid), 0.0)))

    rows = []
    for d in PERIODS:
        m = _metric(metrics, d, pid)
        rows.append({
            "기간": f"최근 {d}일",
            "판매량": m["sales"],
            "일평균 판매": m["sales"] / d,
            "광고판매": m["ad_sales"],
            "오가닉판매": m["organic"],
            "오가닉 일평균": m["organic"] / d,
            "오가닉 비율(%)": m["organic_ratio"],
            "광고비": m["ad_spend"],
        })
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
        column_config={
            "판매량": st.column_config.NumberColumn(format="%.0f"),
            "일평균 판매": st.column_config.NumberColumn(format="%.1f"),
            "광고판매": st.column_config.NumberColumn(format="%.0f"),
            "오가닉판매": st.column_config.NumberColumn(format="%.0f"),
            "오가닉 일평균": st.column_config.NumberColumn(format="%.1f"),
            "오가닉 비율(%)": st.column_config.NumberColumn(format="%.1f%%"),
            "광고비": st.column_config.NumberColumn(format="%d원"),
        },
    )

    st.markdown("#### 육성 설정")
    c1, c2, c3 = st.columns([1, 1, 2])
    launch = c1.date_input(
        "런칭일", value=date.fromisoformat(str(g["launch_date"])[:10]),
        key=f"growth_launch_{pid}_v09274",
    )
    status = c2.selectbox(
        "현재 상태", STATUSES,
        index=STATUSES.index(g["status"]) if g["status"] in STATUSES else 0,
        key=f"growth_status_{pid}_v09274",
    )
    target = c3.number_input(
        "목표 오가닉 일판매", min_value=0.1, max_value=1000.0,
        value=float(g["target_daily_organic"] or 4.0), step=0.5,
        key=f"growth_target_{pid}_v09274",
    )
    memo = st.text_area(
        "메모", value=str(g.get("memo") or ""),
        placeholder="예: 가격경쟁력 좋음 / 광고 2만원 유지 / 1페이지 진입 후 감액",
        key=f"growth_memo_{pid}_v09274",
    )
    s1, s2 = st.columns([1, 1])
    if s1.button("설정 저장", type="primary", use_container_width=True, key=f"growth_save_{pid}_v09274"):
        _save_management(core, db, int(pid), launch.isoformat(), status, float(target), memo)
        st.success("저장했습니다.")
        st.rerun()
    if s2.button("육성관리에서 제외", use_container_width=True, key=f"growth_remove_{pid}_v09274"):
        _remove_management(core, db, int(pid))
        st.success("육성관리 목록에서 제외했습니다. 판매자료는 삭제하지 않았습니다.")
        st.rerun()

    snap = _snapshot_frame(core, db, int(pid))
    if len(snap) >= 2:
        st.markdown("#### 30일 이동지표 추세")
        chart = snap[["snapshot_date", "sales_30", "organic_30", "ad_sales_30"]].copy()
        chart["snapshot_date"] = pd.to_datetime(chart["snapshot_date"])
        chart = chart.set_index("snapshot_date")
        chart.columns = ["30일 판매", "30일 오가닉", "30일 광고판매"]
        st.line_chart(chart, use_container_width=True)
        st.caption("육성관리 화면을 사용할 때 하루 1회 저장되는 30일 이동합계입니다.")


def render_page(st_obj, core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)
    _ensure_schema(core, db)
    st_obj.markdown("## 🚀 신규상품 육성관리")
    st_obj.caption(
        "신규 SKU가 광고를 통해 자리를 잡고 오가닉 판매로 전환되는 과정을 기간별로 추적합니다. "
        "판매·광고 원본자료는 수정하지 않습니다."
    )

    _render_add(core, db)
    managed = _managed(core, db)
    if not managed:
        st_obj.info("육성관리 중인 상품이 없습니다. 위에서 상품을 추가하거나 최근 120일 상품을 자동등록하세요.")
        return

    with st_obj.spinner("기간별 판매·광고·오가닉 데이터를 계산하는 중..."):
        metrics, coverage, ad_ok = _all_metrics(core, db)
        cumulative_ad = _cumulative_ad_spend(core, db, managed)
        _save_daily_snapshots(core, db, managed, metrics)

    overview = _overview_frame(managed, metrics)
    if not overview.empty:
        overview["누적 광고비"] = overview["product_id"].map(
            lambda pid: _num(cumulative_ad.get(int(pid), 0.0))
        )

    m30_total_sales = float(overview["30일 판매"].sum()) if not overview.empty else 0.0
    m30_total_org = float(overview["오가닉판매"].sum()) if not overview.empty else 0.0
    m30_total_ad = float(overview["30일 광고비"].sum()) if not overview.empty else 0.0

    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("육성관리 SKU", f"{len(managed):,}개")
    c2.metric("30일 총판매", f"{_fmt_qty(m30_total_sales)}개")
    c3.metric("30일 오가닉", f"{_fmt_qty(m30_total_org)}개")
    c4.metric("30일 광고비", _fmt_money(m30_total_ad))

    st_obj.markdown("### 육성 현황")
    f1, f2 = st_obj.columns([2, 1])
    q = f1.text_input("상품 검색", placeholder="상품명 / 상품코드", key="growth_search_v09274")
    status_filter = f2.multiselect("상태", STATUSES, default=STATUSES, key="growth_status_filter_v09274")
    view = overview.copy()
    if q.strip():
        mask = (
            view["상품명"].astype(str).str.contains(q.strip(), case=False, regex=False)
            | view["상품코드"].astype(str).str.contains(q.strip(), case=False, regex=False)
        )
        view = view.loc[mask]
    if status_filter:
        view = view[view["상태"].isin(status_filter)]
    else:
        view = view.iloc[0:0]

    show_cols = [
        "상태", "상품명", "런칭일", "경과일", "30일 판매", "30일 일평균",
        "30일 광고비", "누적 광고비", "광고판매", "오가닉판매", "오가닉 일평균",
        "오가닉비율", "이전30일 오가닉", "오가닉 일평균증감", "판단",
    ]
    st_obj.dataframe(
        view[show_cols],
        use_container_width=True,
        hide_index=True,
        height=min(760, max(230, 38 * (len(view) + 1))),
        column_config={
            "30일 판매": st.column_config.NumberColumn(format="%.0f"),
            "30일 일평균": st.column_config.NumberColumn(format="%.1f"),
            "30일 광고비": st.column_config.NumberColumn(format="%d원"),
            "누적 광고비": st.column_config.NumberColumn(format="%d원"),
            "광고판매": st.column_config.NumberColumn(format="%.0f"),
            "오가닉판매": st.column_config.NumberColumn(format="%.0f"),
            "오가닉 일평균": st.column_config.NumberColumn(format="%.1f"),
            "오가닉비율": st.column_config.NumberColumn(format="%.1f%%"),
            "이전30일 오가닉": st.column_config.NumberColumn(format="%.0f"),
            "오가닉 일평균증감": st.column_config.NumberColumn(format="%+.1f"),
        },
    )

    cdays = int(coverage.get(30, 0))
    if cdays < 30:
        st_obj.warning(
            f"최근 30일 중 판매자료와 광고자료가 함께 확인되는 날짜는 {cdays}일입니다. "
            "오가닉 수치는 두 자료가 함께 있는 입력구간 기준입니다."
        )
    elif not ad_ok.get(30):
        st_obj.warning("최근 30일 광고성과보고서에서 광고 판매수량을 확인할 수 없습니다.")
    else:
        st_obj.caption("최근 30일 판매자료와 광고자료가 모두 확인됩니다.")

    _render_detail(core, db, managed, metrics, cumulative_ad)


def _install_sidebar_route():
    sidebar = importlib.import_module("sidebar_groups_v0917")
    groups = getattr(sidebar, "_GROUPS", None)
    if isinstance(groups, list):
        for title, items in groups:
            if str(title) == "📊 판매분석" and PAGE_TEXT not in items:
                items.append(PAGE_TEXT)

    original = getattr(sidebar, "render_sidebar", None)
    if not callable(original) or getattr(original, "_rg_growth_v09274", False):
        return

    def wrapped(st_obj, options, default_page=None):
        runtime = [str(x) for x in list(options or [])]
        if PAGE_TEXT not in runtime:
            runtime.append(PAGE_TEXT)
        current = original(st_obj, runtime, default_page)
        if current == PAGE_TEXT:
            try:
                import core as core_module
                render_page(st_obj, core_module, core_module.DEFAULT_DB)
            except Exception as exc:
                st_obj.error(f"신규상품 육성관리 화면을 여는 중 오류가 발생했습니다: {exc}")
            return "__RG_GROWTH_RENDERED__"
        return current

    wrapped._rg_growth_v09274 = True
    sidebar.render_sidebar = wrapped


def apply(core=None):
    _install_sidebar_route()
    sync = {"baseline_seeded": 0, "auto_added": 0}
    if core is not None:
        _ensure_schema(core, core.DEFAULT_DB)
        sync = _sync_new_products_after_baseline(core, core.DEFAULT_DB)
    return {"ok": True, "page": PAGE_TEXT, **sync}
