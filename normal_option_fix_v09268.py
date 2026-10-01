"""v0.9.270: recognize active finished ERP products as normal options."""

from __future__ import annotations


def apply(core):
    import shared_return_match_ui_v09217 as matcher

    original = matcher._normal_ids

    def fixed_normal_ids(core_obj, db):
        out = set(original(core_obj, db))
        with core_obj._conn(db) as con:
            rows = con.execute(
                "SELECT option_id FROM products WHERE item_type=? AND active=? AND unit_cost>?",
                ("finished", 1, 0),
            ).fetchall()
        for row in rows:
            oid = matcher._oid(row["option_id"])
            if oid:
                out.add(oid)
        return out

    matcher._normal_ids = fixed_normal_ids
    matcher._rg_finished_normal_fix_v09270 = True
    return {"ok": True}
