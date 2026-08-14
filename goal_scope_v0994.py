"""RG Manager v0.9.102 goal-management product scope.

Stores a non-destructive list of products excluded from goal/performance management.
System-only return/report option IDs are filtered from all goal selectors, while
ERP inventory, sales, settlement and historical calculation data remain intact.

v0.9.102 restores the user-confirmed side-mirror and sax-neck-strap normal option
IDs before filtering so they remain visible in the goal table and target Excel.
"""
from __future__ import annotations

from datetime import datetime
import importlib


def _visibility():
    return importlib.import_module("product_visibility_v0995")


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

    # v0.9.102: these two option IDs are user-confirmed normal ERP products.
    # Repair any stale system-hidden/false-return state before visibility filters run.
    try:
        importlib.import_module("canonical_visible_products_v09102").apply(core, db)
    except Exception:
        pass

    # Also apply the system-level visibility guard in a currently-running
    # Streamlit process so an updater rerun does not require a full restart.
    _visibility().apply_runtime(core)


def excluded_ids(core, db) -> set[int]:
    ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute(
            "SELECT product_id FROM goal_management_exclusions"
        ).fetchall()
    manual = {int(r["product_id"]) for r in rows}
    system_hidden = _visibility().hidden_ids(core, db)
    return manual | system_hidden


def set_excluded(core, db, product_id: int, excluded: bool):
    ensure_schema(core, db)
    pid = int(product_id)
    # System-hidden return/report IDs cannot be restored through goal settings.
    # They are not user items and must remain invisible.
    if pid in _visibility().hidden_ids(core, db):
        return
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
    ensure_schema(core, db)
    products = base._products(core, db, active_only=True)
    products = _visibility().visible_products_df(core, db, products)
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
    visibility = _visibility()
    visibility.apply_goal_module(base, core)

    products = base._products(core, db, active_only=True)
    products = visibility.visible_products_df(core, db, products)
    excluded = excluded_ids(core, db)

    with st.expander("⚙️ 목표관리 상품 설정"):
        st.caption(
            "목표관리가 필요 없는 등록상품만 목록에서 제외합니다. "
            "반품 재판매용 옵션ID와 보고서 전용 ID는 시스템에서 자동으로 숨깁니다."
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
                    key="goal_scope_exclude_product_v0995",
                )
                if st.button(
                    "선택 상품 제외",
                    use_container_width=True,
                    key="goal_scope_exclude_button_v0995",
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
                    key="goal_scope_restore_product_v0995",
                )
                if st.button(
                    "선택 상품 복원",
                    use_container_width=True,
                    key="goal_scope_restore_button_v0995",
                ):
                    set_excluded(core, db, int(pid), False)
                    st.success(f"{labels.get(int(pid), '')}을(를) 목표관리 대상으로 복원했습니다.")
                    st.rerun()
            else:
                st.info("제외된 상품이 없습니다.")
