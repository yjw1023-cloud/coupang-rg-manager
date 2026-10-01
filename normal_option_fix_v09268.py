"""v0.9.268: treat real ERP finished products as normal Coupang options."""

from __future__ import annotations


def apply(core):
    import shared_return_match_ui_v09217 as matcher

    original = matcher._normal_ids

    def fixed_normal_ids(core_obj, db):
        out = set(original(core_obj, db))
        with core_obj._conn(db) as c:
            tables = {
                str(r[0])
                for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "products" not in tables:
                return out
            rows = c.execute(
                """SELECT option_id
                   FROM products
                   WHERE item_type='finished'
                     AND COALESCE(active,1)=1
                     AND COALESCE(TRIM(CAST(option_id AS TEXT)),'')<>''"""
            ).fetchall()
        for r in rows:
            oid = matcher._oid(r["option_id"])
            if oid:
                out.add(oid)
        return out

    matcher._normal_ids = fixed_normal_ids
    return {"ok": True}
