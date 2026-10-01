"""v0.9.269: recognize active ERP finished products as normal options."""

from __future__ import annotations


def apply(core):
    import shared_return_match_ui_v09217 as matcher

    original = matcher._normal_ids

    def fixed_normal_ids(core_obj, db):
        out = set(original(core_obj, db))
        try:
            frame = core_obj.get_products(db)
        except TypeError:
            frame = core_obj.get_products(db_path=db)
        except Exception:
            frame = None

        if frame is not None and not getattr(frame, "empty", True):
            for _, row in frame.iterrows():
                if str(row.get("item_type") or "") != "finished":
                    continue
                try:
                    if int(row.get("active") or 0) != 1:
                        continue
                except Exception:
                    continue
                try:
                    if float(row.get("unit_cost") or 0) <= 0:
                        continue
                except Exception:
                    continue
                oid = matcher._oid(row.get("option_id"))
                if oid:
                    out.add(oid)
        return out

    matcher._normal_ids = fixed_normal_ids
    matcher._rg_finished_normal_fix_v09269 = True
    return {"ok": True}
