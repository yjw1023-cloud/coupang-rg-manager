"""RG Manager v0.9.94 goal-management product scope.

Stores a non-destructive list of products excluded from goal/performance management.
ERP product, inventory, BOM, sales and historical goal data are never deleted.
"""
from __future__ import annotations

from datetime import datetime


def _now(core) -> str:
    try:
        return str(core.now_iso())
    except Exception:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS goal_management_exclusions(
                   product_id INTEGER PRIMARY KEY,
                   excluded_at TEXT NOT NULL
               )"""
        )


def excluded_ids(core, db) -> set[int]:
    ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute(
            "SELECT product_id FROM goal_management_exclusions"
        ).fetchall()
    return {int(r["product_id"]) for r in rows}


def set_excluded(core, db, product_id: int, excluded: bool):
    ensure_schema(core, db)
    pid = int(product_id)
    with core._conn(db) as c:
        if excluded:
            c.execute(
                """INSERT INTO goal_management_exclusions(product_id,excluded_at)
                   VALUES(?,?)
                   ON CONFLICT(product_id) DO UPDATE SET
                     excluded_at=excluded.excluded_at""",
                (pid, _now(core)),
            )
        else:
            c.execute(
                "DELETE FROM goal_management_exclusions WHERE product_id=?",
                (pid,),
            )


def managed_products(core, db, base):
    products = base._products(core, db, active_only=True)
    if products is None or products.empty:
        return products
    excluded = excluded_ids(core, db)
    if not excluded:
        return products
    return products[~products["id"].astype(int).isin(excluded)].copy()


def _product_label(p, base) -> str:
    name = str(getattr(p, "name", "") or "")
    oid = base._oid(getattr(p, "option_id", "")) or base._oid(getattr(p, "item_code", ""))
    return f"{name} · {oid}" if oid else name


def render_controls(st, core, db, base):
    """Render global exclude/restore controls without deleting any ERP/history data."""
    ensure_schema(core, db)
    products = base._products(core, db, active_only=True)
    excluded = excluded_ids(core, db)

    with st.expander("⚙️ 목표관리 상품 설정"):
        st.caption(
            "목표관리가 필요 없는 상품만 목록에서 제외합니다. "
            "ERP 상품·재고·매출·BOM·과거 목표이력은 삭제되지 않습니다."
        )
        if products is None or products.empty:
            st.info("등록된 활성 완제품이 없습니다.")
            return

        included_options = []
        excluded_options = []
        labels = {}
        for p in products.itertuples(index=False):
            pid = int(p.id)
            labels[pid] = _product_label(p, base)
            if pid in excluded:
                excluded_options.append(pid)
            else:
                included_options.append(pid)

        included_options.sort(key=lambda pid: labels.get(pid, ""))
        excluded_options.sort(key=lambda pid: labels.get(pid, ""))

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**목표관리에서 제외**")
            if included_options:
                pid = st.selectbox(
                    "제외할 상품",
                    included_options,
                    format_func=lambda x: labels.get(int(x), str(x)),
                    key="goal_scope_exclude_product_v0994",
                )
                if st.button(
                    "선택 상품 제외",
                    use_container_width=True,
                    key="goal_scope_exclude_button_v0994",
                ):
                    set_excluded(core, db, int(pid), True)
                    st.success(f"{labels.get(int(pid), '')}을(를) 목표관리에서 제외했습니다.")
                    st.rerun()
            else:
                st.info("현재 목표관리 대상 상품이 없습니다.")

        with c2:
            st.markdown("**제외상품 복원**")
            if excluded_options:
                pid = st.selectbox(
                    "복원할 상품",
                    excluded_options,
                    format_func=lambda x: labels.get(int(x), str(x)),
                    key="goal_scope_restore_product_v0994",
                )
                if st.button(
                    "선택 상품 복원",
                    use_container_width=True,
                    key="goal_scope_restore_button_v0994",
                ):
                    set_excluded(core, db, int(pid), False)
                    st.success(f"{labels.get(int(pid), '')}을(를) 목표관리 대상으로 복원했습니다.")
                    st.rerun()
            else:
                st.info("제외된 상품이 없습니다.")
