"""v0.9.102 restore user-confirmed normal Coupang products to user-facing scope.

These option IDs are normal Item Master products, not Coupang return-resale aliases.
The repair is idempotent and runs before goal-scope filtering so they remain visible
in goal/performance and target-input Excel templates.
"""
from __future__ import annotations


USER_CONFIRMED_NORMAL_OPTIONS = {
    "95834379201": "보조거울 백미러 사이드미러 2p 보조미러",
    "95251380743": "색소폰 넥스트랩 목걸이 숄더",
}


def _exists(con, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _force_visible(core, db, oid: str):
    with core._conn(db) as con:
        row = con.execute(
            """SELECT id,name FROM products
               WHERE CAST(option_id AS TEXT)=? ORDER BY id ASC LIMIT 1""",
            (str(oid),),
        ).fetchone()
        if not row:
            return None
        pid = int(row["id"])
        con.execute(
            """UPDATE products SET active=1,item_type='finished',updated_at=?
               WHERE id=?""",
            (core.now_iso(), pid),
        )
        if _exists(con, "system_hidden_products"):
            con.execute("DELETE FROM system_hidden_products WHERE product_id=?", (pid,))
        # The user explicitly confirmed these two belong in goal management.
        if _exists(con, "goal_management_exclusions"):
            con.execute("DELETE FROM goal_management_exclusions WHERE product_id=?", (pid,))
        return pid, str(row["name"] or "").strip()


def _install_visibility_guard(visibility):
    """Never allow the generic return/report hiding sync to hide these originals."""
    if getattr(visibility, "_rg_v09102_normal_option_guard", False):
        return
    original_sync = visibility.sync_hidden

    def sync_hidden(core, db=None):
        target = original_sync(core, db)
        for oid in USER_CONFIRMED_NORMAL_OPTIONS:
            _force_visible(core, target, oid)
        return target

    visibility.sync_hidden = sync_hidden
    visibility._rg_v09102_normal_option_guard = True


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)

    # Treat the same IDs as authoritative originals in return-sale matching.
    try:
        import canonical_rg_cleanup_v0947 as canonical
        canonical.CANONICAL_RG.update(USER_CONFIRMED_NORMAL_OPTIONS)
    except Exception:
        canonical = None

    try:
        import product_visibility_v0995 as visibility
        visibility.ensure_schema(core, db)
        _install_visibility_guard(visibility)
    except Exception:
        visibility = None

    repaired = []
    for oid, fallback_name in USER_CONFIRMED_NORMAL_OPTIONS.items():
        with core._conn(db) as con:
            row = con.execute(
                """SELECT id,name FROM products
                   WHERE CAST(option_id AS TEXT)=? ORDER BY id ASC LIMIT 1""",
                (str(oid),),
            ).fetchone()
            if not row:
                continue
            current_name = str(row["name"] or "").strip() or fallback_name
            alias_exists = _exists(con, "return_discount_aliases") and con.execute(
                "SELECT 1 FROM return_discount_aliases WHERE discount_option_id=?",
                (str(oid),),
            ).fetchone() is not None

        # Undo an old false return-alias classification using the existing proven
        # restoration routine. It also restores normal RG sales inventory postings.
        if alias_exists:
            try:
                import return_discount_v099 as rd
                import canonical_rg_restore_v0948 as restore
                restore._repair_one(core, rd, str(oid), current_name)
            except Exception:
                pass

        result = _force_visible(core, db, oid)
        if result:
            repaired.append({
                "option_id": str(oid),
                "product_id": int(result[0]),
                "alias_repaired": bool(alias_exists),
            })

    return repaired
