"""RG Manager v0.9.81 Excel-style goals vs provisional/confirmed performance view.

Keeps the v0.9.79 goal/review/history logic, but presents the main screen as:
- overall totals: 목표 / 잠정실적 / 확정실적
- item detail: three rows per product with the same comparison structure
- detailed target-cost inputs matching the comparison columns
"""
from __future__ import annotations

import importlib
from typing import Any

import pandas as pd


_TARGET_COST_COLUMNS = (
    ("target_commission", "REAL NOT NULL DEFAULT 0"),
    ("target_rg_cost", "REAL NOT NULL DEFAULT 0"),
    ("target_return_cost", "REAL NOT NULL DEFAULT 0"),
    ("target_cogs", "REAL NOT NULL DEFAULT 0"),
)


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
        return float(v or 0)
    except Exception:
        return 0.0


def _fmt_money(v: Any) -> str:
    if v is None:
        return ""
    return f"{int(round(_num(v))):,}"


def _fmt_qty(v: Any) -> str:
    if v is None:
        return ""
    n = _num(v)
    return f"{int(round(n)):,}" if abs(n - round(n)) < 1e-9 else f"{n:,.1f}"


def _ensure_detail_schema(core, db, base):
    base._ensure_schema(core, db)
    with core._conn(db) as c:
        existing = base._cols(c, "monthly_product_goals")
        for col, definition in _TARGET_COST_COLUMNS:
            if col not in existing:
                c.execute(f"ALTER TABLE monthly_product_goals ADD COLUMN {col} {definition}")


def _detail_goals(core, db, month: str, base) -> pd.DataFrame:
    _ensure_detail_schema(core, db, base)
    with core._conn(db) as c:
        return pd.read_sql_query(
            """SELECT g.month,g.product_id,g.target_qty,g.target_revenue,
                      g.target_commission,g.target_rg_cost,g.target_return_cost,
                      g.target_ad_spend,g.target_cogs,g.target_profit,g.memo,g.updated_at,
                      p.option_id,p.item_code,p.name,p.active
               FROM monthly_product_goals g
               JOIN products p ON p.id=g.product_id
               WHERE g.month=?
               ORDER BY p.name,p.item_code""",
            c,
            params=(month,),
        )


def _save_detail_goal(core, db, month: str, product_id: int, row: dict, base):
    _ensure_detail_schema(core, db, base)
    vals = {
        "target_qty": _num(row.get("목표수량")),
        "target_revenue": _num(row.get("목표매출")),
        "target_commission": _num(row.get("목표수수료")),
        "target_rg_cost": _num(row.get("목표입출고배송비")),
        "target_return_cost": _num(row.get("목표반품처리비")),
        "target_ad_spend": _num(row.get("목표광고비")),
        "target_cogs": _num(row.get("목표상품원가")),
        "target_profit": _num(row.get("목표매출이익")),
    }
    memo = str(row.get("메모") or "").strip()
    with core._conn(db) as c:
        if sum(abs(x) for x in vals.values()) <= 1e-12 and not memo:
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
            """INSERT INTO monthly_product_goals(
                   month,product_id,target_qty,target_revenue,target_commission,
                   target_rg_cost,target_return_cost,target_ad_spend,target_cogs,
                   target_profit,memo,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(month,product_id) DO UPDATE SET
                   target_qty=excluded.target_qty,
                   target_revenue=excluded.target_revenue,
                   target_commission=excluded.target_commission,
                   target_rg_cost=excluded.target_rg_cost,
                   target_return_cost=excluded.target_return_cost,
                   target_ad_spend=excluded.target_ad_spend,
                   target_cogs=excluded.target_cogs,
                   target_profit=excluded.target_profit,
                   memo=excluded.memo,
                   updated_at=excluded.updated_at""",
            (
                month, int(product_id), vals["target_qty"], vals["target_revenue"],
                vals["target_commission"], vals["target_rg_cost"], vals["target_return_cost"],
                vals["target_ad_spend"], vals["target_cogs"], vals["target_profit"],
                memo, base._now(core),
            ),
        )


def _copy_previous_goals(core, db, month: str, overwrite: bool, base) -> int:
    _ensure_detail_schema(core, db, base)
    prev = base._add_month(month, -1)
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT product_id,target_qty,target_revenue,target_commission,
                      target_rg_cost,target_return_cost,target_ad_spend,target_cogs,
                      target_profit,memo
               FROM monthly_product_goals WHERE month=?""",
            (prev,),
        ).fetchall()
        count = 0
        for r in rows:
            exists = c.execute(
                "SELECT 1 FROM monthly_product_goals WHERE month=? AND product_id=?",
                (month, int(r["product_id"])),
            ).fetchone()
            if exists and not overwrite:
                continue
            c.execute(
                """INSERT INTO monthly_product_goals(
                       month,product_id,target_qty,target_revenue,target_commission,
                       target_rg_cost,target_return_cost,target_ad_spend,target_cogs,
                       target_profit,memo,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(month,product_id) DO UPDATE SET
                       target_qty=excluded.target_qty,
                       target_revenue=excluded.target_revenue,
                       target_commission=excluded.target_commission,
                       target_rg_cost=excluded.target_rg_cost,
                       target_return_cost=excluded.target_return_cost,
                       target_ad_spend=excluded.target_ad_spend,
                       target_cogs=excluded.target_cogs,
                       target_profit=excluded.target_profit,
                       memo=excluded.memo,
                       updated_at=excluded.updated_at""",
                (
                    month, int(r["product_id"]), _num(r["target_qty"]),
                    _num(r["target_revenue"]), _num(r["target_commission"]),
                    _num(r["target_rg_cost"]), _num(r["target_return_cost"]),
                    _num(r["target_ad_spend"]), _num(r["target_cogs"]),
                    _num(r["target_profit"]), str(r["memo"] or ""), base._now(core),
                ),
            )
            count += 1
    return count


def _product_maps(core, db, base):
    products = base._products(core, db, active_only=False)
    by_oid = {}
    by_pid = {}
    for r in products.itertuples(index=False):
        pid = int(r.id)
        oid = base._oid(getattr(r, "option_id", "")) or base._oid(getattr(r, "item_code", ""))
        by_pid[pid] = {
            "name": str(getattr(r, "name", "") or ""),
            "option_id": oid,
            "active": int(_num(getattr(r, "active", 1))),
        }
        if oid:
            by_oid[oid] = pid
    return by_pid, by_oid


def _provisional_details(core, db, month: str, base):
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
    _by_pid, by_oid = _product_maps(core, db, base)
    out = {}
    if df is None or df.empty:
        return out
    for r in df.to_dict("records"):
        oid = base._oid(r.get("옵션ID"))
        pid = by_oid.get(oid)
        if pid is None:
            continue
        x = out.setdefault(
            int(pid),
            {"qty":0.0,"revenue":0.0,"commission":0.0,"rg":0.0,
             "returns":0.0,"ad":0.0,"cogs":0.0,"profit":0.0,"source":"잠정"},
        )
        x["qty"] += _num(r.get("판매수량"))
        x["revenue"] += _num(r.get("예상매출"))
        x["commission"] += abs(_num(r.get("판매수수료")))
        x["rg"] += abs(_num(r.get("입출고비"))) + abs(_num(r.get("배송비")))
        x["returns"] += abs(_num(r.get("반품충당")))
        x["ad"] += abs(_num(r.get("광고비")))
        x["cogs"] += abs(_num(r.get("매출원가")))
        x["profit"] += _num(r.get("예상이익"))
    return out


def _confirmed_details(core, db, month: str, provisional: dict, base):
    return base._confirmed_actuals(core, db, month, provisional)


def _blank_metrics():
    return {
        "qty": 0.0, "revenue": 0.0, "commission": 0.0, "rg": 0.0,
        "returns": 0.0, "ad": 0.0, "cogs": 0.0, "profit": 0.0,
    }


def _target_metrics(goal: dict):
    return {
        "qty": _num(goal.get("target_qty")),
        "revenue": _num(goal.get("target_revenue")),
        "commission": _num(goal.get("target_commission")),
        "rg": _num(goal.get("target_rg_cost")),
        "returns": _num(goal.get("target_return_cost")),
        "ad": _num(goal.get("target_ad_spend")),
        "cogs": _num(goal.get("target_cogs")),
        "profit": _num(goal.get("target_profit")),
    }


def _row_metrics(label: str, item: str, m: dict | None, blank_if_none=False):
    if m is None and blank_if_none:
        return {
            "구분": label, "아이템": item, "매출": None, "단가": None, "수량": None,
            "수수료": None, "입출고배송비": None, "반품처리비": None, "광고비": None,
            "상품원가": None, "매출이익": None,
        }
    m = m or _blank_metrics()
    qty = _num(m.get("qty"))
    revenue = _num(m.get("revenue"))
    return {
        "구분": label,
        "아이템": item,
        "매출": revenue,
        "단가": revenue / qty if abs(qty) > 1e-12 else 0.0,
        "수량": qty,
        "수수료": _num(m.get("commission")),
        "입출고배송비": _num(m.get("rg")),
        "반품처리비": _num(m.get("returns")),
        "광고비": _num(m.get("ad")),
        "상품원가": _num(m.get("cogs")),
        "매출이익": _num(m.get("profit")),
    }


def _format_comparison(df: pd.DataFrame) -> pd.DataFrame:
    show = df.copy()
    if show.empty:
        return show
    for c in ("매출","단가","수수료","입출고배송비","반품처리비","광고비","상품원가","매출이익"):
        if c in show.columns:
            show[c] = show[c].map(_fmt_money)
    if "수량" in show.columns:
        show["수량"] = show["수량"].map(_fmt_qty)
    return show


def _sum_metrics(records):
    total = _blank_metrics()
    for m in records:
        if not m:
            continue
        for k in total:
            total[k] += _num(m.get(k))
    return total


def _render_excel_comparison(st, core, db, month: str, base):
    goals = _detail_goals(core, db, month, base)
    provisional = _provisional_details(core, db, month, base)
    confirmed = _confirmed_details(core, db, month, provisional, base)
    confirmed_available = bool(confirmed)
    product_map, _ = _product_maps(core, db, base)
    goal_map = {int(r["product_id"]): r for r in goals.to_dict("records")}

    pids = set(goal_map) | set(provisional) | set(confirmed)
    if not pids:
        st.info("선택한 월에 목표 또는 실적 자료가 없습니다. '목표 입력' 탭에서 목표를 먼저 입력하세요.")
        return

    target_total = _sum_metrics(_target_metrics(goal_map[pid]) for pid in pids if pid in goal_map)
    provisional_total = _sum_metrics(provisional.get(pid) for pid in pids)
    confirmed_total = _sum_metrics(confirmed.get(pid) for pid in pids) if confirmed_available else None

    st.markdown("### 합계")
    total_rows = [
        _row_metrics("목표", "", target_total),
        _row_metrics("잠정실적", "", provisional_total),
        _row_metrics("확정실적", "", confirmed_total, blank_if_none=True),
    ]
    total_df = pd.DataFrame(total_rows).drop(columns=["아이템","단가","수량"])
    st.dataframe(_format_comparison(total_df), use_container_width=True, hide_index=True)

    st.markdown("### 아이템별")
    q = st.text_input(
        "아이템 검색",
        placeholder="상품명 또는 옵션ID 입력",
        key=f"goal_excel_search_{month}",
    )
    words = str(q or "").strip().lower().split()

    detail_rows = []
    def _sort_key(pid):
        meta = product_map.get(int(pid), {})
        return (str(meta.get("name") or ""), str(meta.get("option_id") or ""))

    for pid in sorted(pids, key=_sort_key):
        meta = product_map.get(int(pid), {})
        name = str(meta.get("name") or f"상품 {pid}")
        oid = str(meta.get("option_id") or "")
        hay = f"{name} {oid}".lower()
        if words and not all(w in hay for w in words):
            continue
        item_label = f"{name} · {oid}" if oid else name
        target = _target_metrics(goal_map[pid]) if pid in goal_map else _blank_metrics()
        detail_rows.append(_row_metrics("목표", item_label, target))
        detail_rows.append(_row_metrics("잠정실적", "", provisional.get(pid, _blank_metrics())))
        detail_rows.append(
            _row_metrics(
                "확정실적", "", confirmed.get(pid) if confirmed_available else None,
                blank_if_none=not confirmed_available,
            )
        )

    if not detail_rows:
        st.info("검색 조건에 맞는 아이템이 없습니다.")
        return
    detail_df = pd.DataFrame(detail_rows)
    st.dataframe(
        _format_comparison(detail_df),
        use_container_width=True,
        hide_index=True,
        height=min(760, max(260, 35 * (len(detail_df) + 1))),
    )
    if confirmed_available:
        st.caption("잠정실적은 판매자료 기반 예상손익, 확정실적은 월 정산자료 기반 확정손익입니다.")
    else:
        st.caption("아직 확정 정산자료가 없는 월은 확정실적 행을 빈칸으로 표시합니다.")


def _render_goal_editor(st, core, db, month: str, base):
    st.markdown("### 상품별 목표 입력")
    st.caption("엑셀의 '목표' 행에 들어갈 값을 입력합니다. 단가는 목표매출 ÷ 목표수량으로 비교표에서 자동 계산됩니다.")

    c1, c2, _ = st.columns([1.2, 1.5, 4])
    overwrite = c1.checkbox("기존 목표 덮어쓰기", key=f"goal981_overwrite_{month}")
    if c2.button("전월 목표 복사", key=f"goal981_copy_{month}", use_container_width=True):
        n = _copy_previous_goals(core, db, month, overwrite, base)
        st.success(f"전월 목표 {n:,}개 상품을 복사했습니다.")
        st.rerun()

    q = st.text_input(
        "상품 검색",
        placeholder="상품명 또는 옵션ID 입력",
        key=f"goal981_editor_search_{month}",
    )
    products = base._products(core, db, active_only=True)
    existing = _detail_goals(core, db, month, base)
    by_pid = {int(r["product_id"]): r for r in existing.to_dict("records")}
    rows = []
    words = str(q or "").strip().lower().split()
    for p in products.itertuples(index=False):
        oid = base._oid(getattr(p, "option_id", "")) or base._oid(getattr(p, "item_code", ""))
        hay = f"{str(p.name or '')} {oid}".lower()
        if words and not all(w in hay for w in words):
            continue
        g = by_pid.get(int(p.id), {})
        rows.append({
            "product_id": int(p.id),
            "상품명": str(p.name or ""),
            "옵션ID": oid,
            "목표매출": _num(g.get("target_revenue")),
            "목표수량": _num(g.get("target_qty")),
            "목표수수료": _num(g.get("target_commission")),
            "목표입출고배송비": _num(g.get("target_rg_cost")),
            "목표반품처리비": _num(g.get("target_return_cost")),
            "목표광고비": _num(g.get("target_ad_spend")),
            "목표상품원가": _num(g.get("target_cogs")),
            "목표매출이익": _num(g.get("target_profit")),
            "메모": str(g.get("memo") or ""),
        })
    frame = pd.DataFrame(rows)
    if frame.empty:
        st.info("검색 조건에 맞는 완제품이 없습니다.")
        return

    edited = st.data_editor(
        frame,
        use_container_width=True,
        hide_index=True,
        disabled=["product_id","상품명","옵션ID"],
        column_config={"product_id": None},
        height=min(720, max(300, 36 * (len(frame) + 1))),
        key=f"goal981_editor_{month}",
    )
    if st.button("목표 저장", type="primary", key=f"goal981_save_{month}"):
        for r in edited.to_dict("records"):
            _save_detail_goal(core, db, month, int(r["product_id"]), r, base)
        st.success(f"{base._month_label(month)} 목표를 저장했습니다.")
        st.rerun()


def render_page(st, pd_obj, core, db_path=None):
    base = importlib.import_module("goal_management_v0979")
    db = db_path or core.DEFAULT_DB
    _ensure_detail_schema(core, db, base)

    st.markdown(base._SELECT_CSS, unsafe_allow_html=True)
    st.markdown("## 🎯 목표·실적관리")
    st.caption("목표와 잠정실적·확정실적을 엑셀처럼 한 표에서 비교합니다.")

    months = base._month_options()
    month = st.selectbox(
        "목표·검증 월",
        months,
        index=0,
        format_func=base._month_label,
        key="goal_management_month_v0981",
    )

    tabs = st.tabs(["목표·실적표", "목표 입력", "월말검증", "목표이력"])
    with tabs[0]:
        _render_excel_comparison(st, core, db, month, base)
    with tabs[1]:
        _render_goal_editor(st, core, db, month, base)
    with tabs[2]:
        goals = base._goals(core, db, month)
        actuals, source_label = base._actuals(core, db, month)
        progress, _meta = base._build_progress(goals, actuals, month, core, db)
        base._render_review(st, core, db, month, progress, source_label)
    with tabs[3]:
        base._render_history(st, core, db)
