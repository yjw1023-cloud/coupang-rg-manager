"""v0.9.102 restore user-confirmed normal Coupang products to user-facing scope.

These option IDs are normal Item Master products, not Coupang return-resale aliases.
The repair is intentionally idempotent and runs before goal-scope filtering so they
remain visible in goal/performance and target-input Excel templates.
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


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)

    # Make the same IDs authoritative originals for return-sale matching as well.
    try:
        import canonical_rg_cleanup_v0947 as canonical
        canonical.CANONICAL_RG.update(USER_CONFIRMED_NORMAL_OPTIONS)
    except Exception:
        canonical = None

    try:
        import product_visibility_v0995 as visibility
        visibility.ensure_schema(core, db)
    except Exception:
        visibility = None

    repaired = []
    with core._conn(db) as con:
        for oid, fallback_name in USER_CONFIRMED_NORMAL_OPTIONS.items():
            rows = con.execute(
                """SELECT id,name,active FROM products
                   WHERE CAST(option_id AS TEXT)=?
                   ORDER BY id ASC""",
                (str(oid),),
            ).fetchall()
            if not rows:
                continue

            primary = rows[0]
            pid = int(primary["id"])
            current_name = str(primary["name"] or "").strip() or fallback_name

            # If an older heuristic ever registered this normal ID as a return alias,
            # use the proven canonical-restoration routine to undo that safely and
            # restore normal RG sales postings.
            alias_exists = False
            if _exists(con, "return_discount_aliases"):
                alias_exists = con.execute(
                    "SELECT 1 FROM return_discount_aliases WHERE discount_option_id=?",
                    (str(oid),),
                ).fetchone() is not None

            if alias_exists:
                # Run outside this connection because the helper manages its own txn.
                pass
            else:
                con.execute(
                    """UPDATE products SET active=1,item_type='finished',updated_at=?
                       WHERE id=?""",
                    (core.now_iso(), pid),
                )
                if _exists(con, "system_hidden_products"):
                    con.execute(
                        "DELETE FROM system_hidden_products WHERE product_id=?",
                        (pid,),
                    )
                if _exists(con, "goal_management_exclusions"):
                    con.execute(
                        "DELETE FROM goal_management_exclusions WHERE product_id=?",
                        (pid,),
                    )
                repaired.append({"option_id": oid, "product_id": pid, "alias_repaired": False})

    # Alias repair must not run while the connection above is open.
    for oid, fallback_name in USER_CONFIRMED_NORMAL_OPTIONS.items():
        with core._conn(db) as con:
            row = con.execute(
                """SELECT id,name FROM products
                   WHERE CAST(option_id AS TEXT)=? ORDER BY id ASC LIMIT 1""",
                (str(oid),),
            ).fetchone()
            if not row:
                continue
            pid = int(row["id"])
            current_name = str(row["name"] or "").strip() or fallback_name
            alias_exists = _exists(con, "return_discount_aliases") and con.execute(
                "SELECT 1 FROM return_discount_aliases WHERE discount_option_id=?",
                (str(oid),),
            ).fetchone() is not None

        if alias_exists:
            try:
                import return_discount_v099 as rd
                import canonical_rg_restore_v0948 as restore
                restore._repair_one(core, rd, str(oid), current_name)
            except Exception:
                # Visibility still must be repaired even if historical alias cleanup
                # cannot complete in this run.
                pass

        with core._conn(db) as con:
            con.execute(
                """UPDATE products SET active=1,item_type='finished',updated_at=?
                   WHERE id=?""",
                (core.now_iso(), pid),
            )
            if _exists(con, "system_hidden_products"):
                con.execute("DELETE FROM system_hidden_products WHERE product_id=?", (pid,))
            if _exists(con, "goal_management_exclusions"):
                con.execute("DELETE FROM goal_management_exclusions WHERE product_id=?", (pid,))

        repaired.append({"option_id": oid, "product_id": pid, "alias_repaired": bool(alias_exists)})

    return repaired
