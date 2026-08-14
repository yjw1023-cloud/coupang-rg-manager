"""RG Manager v0.9.95 user-facing product visibility guard.

Rules
- Coupang return/re-sale option IDs and report-only auto-created products may remain
  in the DB for matching, settlement and historical calculations.
- They are never shown as normal ERP items to the user.
- Only products explicitly registered in Item Master are user-facing.
- A report-only hidden product can be promoted to a normal Item Master product when
  the user explicitly registers that option ID.
"""
from __future__ import annotations

from datetime import datetime
import sqlite3
import sys


# IDs marked red by the user in the 2026-08-14 target-input workbook.
KNOWN_RETURN_OPTION_IDS = {
    "94679965319",
    "95551289967",
    "95593762217",
    "95594235700",
    "95644866786",
    "95140327852",
    "94533105408",
    "94731669021",
    "95265534972",
    "95321950215",
    "94391011068",
    "94566989635",
    "94285787287",
    "94845793700",
    "94125499117",
    "95251457883",
    "94272018620",
    "94758590295",
    "95190832227",
    "94138635141",
    "94138679981",
    "94948187475",
    "95373907752",
    "95615771344",
    "95060856477",
    "95371029296",
    "95697280722",
    "95749158342",
    "95864153283",
}


def _now(core) -> str:
    try:
        return str(core.now_iso())
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _db(core, db=None):
    return db or core.DEFAULT_DB


def ensure_schema(core, db=None):
    db = _db(core, db)
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS system_hidden_products(
                   product_id INTEGER PRIMARY KEY,
                   reason TEXT NOT NULL,
                   hidden_at TEXT NOT NULL
               )"""
        )
    return db


def _hide_product_ids(core, db, product_ids, reason: str):
    ids = sorted({int(x) for x in product_ids if x is not None})
    if not ids:
        return 0
    ensure_schema(core, db)
    now = _now(core)
    with core._conn(db) as c:
        for pid in ids:
            c.execute(
                """INSERT INTO system_hidden_products(product_id,reason,hidden_at)
                   VALUES(?,?,?)
                   ON CONFLICT(product_id) DO UPDATE SET
                     reason=excluded.reason, hidden_at=excluded.hidden_at""",
                (pid, str(reason), now),
            )
            # active=0 keeps these rows out of normal ERP selectors while retaining
            # all DB links needed by settlement/returns/history calculations.
            c.execute(
                "UPDATE products SET active=0 WHERE id=?",
                (pid,),
            )
    return len(ids)


def hide_option_ids(core, db, option_ids, reason="return_option"):
    db = ensure_schema(core, db)
    ids = {str(x or "").strip() for x in option_ids if str(x or "").strip()}
    if not ids:
        return 0
    with core._conn(db) as c:
        q = ",".join("?" for _ in ids)
        rows = c.execute(
            f"SELECT id FROM products WHERE option_id IN ({q})",
            tuple(sorted(ids)),
        ).fetchall()
    return _hide_product_ids(core, db, [r["id"] for r in rows], reason)


def sync_hidden(core, db=None):
    """Seed known return IDs and any IDs already identified by return-sale matching."""
    db = ensure_schema(core, db)
    hide_option_ids(core, db, KNOWN_RETURN_OPTION_IDS, "user_marked_return_option")
    try:
        with core._conn(db) as c:
            exists = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='return_discount_aliases'"
            ).fetchone()
            if exists:
                rows = c.execute(
                    "SELECT discount_option_id FROM return_discount_aliases"
                ).fetchall()
                aliases = {str(r["discount_option_id"] or "").strip() for r in rows}
            else:
                aliases = set()
        if aliases:
            hide_option_ids(core, db, aliases, "return_discount_alias")
    except Exception:
        pass
    return db


def hidden_ids(core, db=None) -> set[int]:
    db = sync_hidden(core, db)
    with core._conn(db) as c:
        rows = c.execute("SELECT product_id FROM system_hidden_products").fetchall()
    return {int(r["product_id"]) for r in rows}


def visible_products_df(core, db, df):
    if df is None or getattr(df, "empty", True):
        return df
    hidden = hidden_ids(core, db)
    if not hidden or "id" not in df.columns:
        return df
    return df[~df["id"].astype(int).isin(hidden)].copy()


def _promote_hidden_item(core, db, product_id: int, name: str, unit_cost: float, item_code: str = ""):
    db = ensure_schema(core, db)
    pid = int(product_id)
    name = str(name or "").strip()
    supplied_code = str(item_code or "").strip()
    if not name:
        raise ValueError("상품명을 입력해 주세요.")
    with core._conn(db) as c:
        if supplied_code:
            conflict = c.execute(
                "SELECT id FROM products WHERE item_code=? AND id<>?",
                (supplied_code, pid),
            ).fetchone()
            if conflict:
                raise ValueError(f"이미 존재하거나 과거에 사용한 품목코드입니다: {supplied_code}")
            c.execute(
                """UPDATE products
                   SET item_code=?,name=?,item_type='finished',unit_cost=?,active=1,updated_at=?
                   WHERE id=?""",
                (supplied_code, name, float(unit_cost or 0), _now(core), pid),
            )
        else:
            c.execute(
                """UPDATE products
                   SET name=?,item_type='finished',unit_cost=?,active=1,updated_at=?
                   WHERE id=?""",
                (name, float(unit_cost or 0), _now(core), pid),
            )
        c.execute("DELETE FROM system_hidden_products WHERE product_id=?", (pid,))
    return pid


def apply_core(core):
    """Hide report-created products by default without breaking internal joins."""
    db = sync_hidden(core, core.DEFAULT_DB)
    if getattr(core, "_rg_product_visibility_v0995_applied", False):
        return
    original = getattr(core, "upsert_product", None)
    if not callable(original):
        core._rg_product_visibility_v0995_applied = True
        return

    def wrapped(*args, **kwargs):
        oid = kwargs.get("option_id")
        if oid is None and args:
            oid = args[0]
        oid = str(oid or "").strip()
        target_db = kwargs.get("db_path") or kwargs.get("db") or core.DEFAULT_DB
        existed = False
        if oid:
            try:
                with core._conn(target_db) as c:
                    existed = c.execute(
                        "SELECT 1 FROM products WHERE option_id=?", (oid,)
                    ).fetchone() is not None
            except Exception:
                existed = False
        pid = original(*args, **kwargs)
        if oid and not existed:
            try:
                _hide_product_ids(core, target_db, [int(pid)], "report_auto_created")
            except Exception:
                pass
        return pid

    core.upsert_product = wrapped
    core._rg_product_visibility_v0995_applied = True


def apply_item_ui(item_ui, core):
    if item_ui is None or getattr(item_ui, "_rg_product_visibility_v0995_applied", False):
        return
    original_load = getattr(item_ui, "_load_products", None)
    original_create = getattr(item_ui, "_create_product", None)

    if callable(original_load):
        def load_products(core_obj):
            sync_hidden(core_obj, core_obj.DEFAULT_DB)
            df = original_load(core_obj)
            return visible_products_df(core_obj, core_obj.DEFAULT_DB, df)
        item_ui._load_products = load_products

    if callable(original_create):
        def create_product(core_obj, kind, name, item_code, option_id, unit_cost):
            oid = str(option_id or "").strip()
            if str(kind) != "자체창고 품목" and oid:
                sync_hidden(core_obj, core_obj.DEFAULT_DB)
                with core_obj._conn(core_obj.DEFAULT_DB) as c:
                    row = c.execute(
                        """SELECT p.id
                           FROM products p
                           JOIN system_hidden_products h ON h.product_id=p.id
                           WHERE p.option_id=?""",
                        (oid,),
                    ).fetchone()
                if row:
                    return _promote_hidden_item(
                        core_obj, core_obj.DEFAULT_DB, int(row["id"]),
                        str(name or ""), float(unit_cost or 0), str(item_code or ""),
                    )
            return original_create(core_obj, kind, name, item_code, option_id, unit_cost)
        item_ui._create_product = create_product

    item_ui._rg_product_visibility_v0995_applied = True


def apply_goal_module(goal_module, core):
    """Keep system-hidden products out of the goal-history product selector."""
    if goal_module is None or getattr(goal_module, "_rg_goal_visibility_v0995_applied", False):
        return
    original = getattr(goal_module, "_render_history", None)
    if not callable(original):
        goal_module._rg_goal_visibility_v0995_applied = True
        return

    def render_history(st, core_obj, db):
        sync_hidden(core_obj, db)
        hidden = hidden_ids(core_obj, db)
        st.markdown("### 상품별 목표 이력")
        goal_module._ensure_schema(core_obj, db)
        with core_obj._conn(db) as c:
            rows = c.execute(
                """SELECT DISTINCT g.product_id,p.name,p.option_id,p.item_code
                   FROM monthly_product_goals g JOIN products p ON p.id=g.product_id
                   ORDER BY p.name"""
            ).fetchall()
        rows = [r for r in rows if int(r["product_id"]) not in hidden]
        if not rows:
            st.info("저장된 목표 이력이 없습니다.")
            return
        ids = [int(r["product_id"]) for r in rows]
        labels = {
            int(r["product_id"]): f"{str(r['name'] or '')} · {goal_module._oid(r['option_id']) or goal_module._oid(r['item_code'])}"
            for r in rows
        }
        pid = st.selectbox("상품 선택", ids, format_func=lambda x: labels.get(int(x), str(x)), key="goal_history_product")
        with core_obj._conn(db) as c:
            goals = c.execute(
                """SELECT month,target_qty,target_revenue,target_ad_spend,target_profit,memo
                   FROM monthly_product_goals
                   WHERE product_id=? ORDER BY month DESC LIMIT 18""",
                (int(pid),),
            ).fetchall()
        history = []
        cache = {}
        for g in goals:
            mon = str(g["month"])
            if mon not in cache:
                cache[mon] = goal_module._actuals(core_obj, db, mon)
            actuals, source = cache[mon]
            a = actuals.get(int(pid), {})
            history.append({
                "월": mon, "기준": source,
                "목표판매": goal_module._num(g["target_qty"]), "실제판매": goal_module._num(a.get("qty")),
                "판매달성률": goal_module._num(a.get("qty")) / goal_module._num(g["target_qty"]) * 100 if goal_module._num(g["target_qty"]) > 0 else 0,
                "목표매출": goal_module._num(g["target_revenue"]), "실제매출": goal_module._num(a.get("revenue")),
                "목표이익": goal_module._num(g["target_profit"]), "실제이익": goal_module._num(a.get("profit")),
                "목표광고": goal_module._num(g["target_ad_spend"]), "실제광고": goal_module._num(a.get("ad")),
                "메모": str(g["memo"] or ""),
            })
        df = goal_module.pd.DataFrame(history)
        show = df.copy()
        for c in ("목표판매", "실제판매"):
            show[c] = show[c].map(goal_module._fmt_qty)
        show["판매달성률"] = show["판매달성률"].map(goal_module._fmt_pct)
        for c in ("목표매출", "실제매출", "목표이익", "실제이익", "목표광고", "실제광고"):
            show[c] = show[c].map(goal_module._fmt_money)
        st.dataframe(show, use_container_width=True, hide_index=True)
        if len(df) >= 2:
            chart = df.sort_values("월").set_index("월")[["목표판매", "실제판매"]]
            st.markdown("#### 판매수량 목표 대비 실제")
            st.line_chart(chart, height=280)

    goal_module._render_history = render_history
    goal_module._rg_goal_visibility_v0995_applied = True


def apply_runtime(core):
    """Convenience hook for a currently-running Streamlit process."""
    apply_core(core)
    apply_item_ui(sys.modules.get("item_ui_v086"), core)
    apply_goal_module(sys.modules.get("goal_management_v0979"), core)
