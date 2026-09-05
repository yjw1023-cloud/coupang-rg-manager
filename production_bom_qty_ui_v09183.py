"""Show actual BOM requirement quantities on the production batch preview.

v0.9.183
- Replace the low-value `N개 구성품` preview with per-finished-unit BOM quantities.
- A one-component BOM shows e.g. `2개 소요`.
- Multi-component BOMs show each component code/name with its own qty so operators
  can judge whether the recipe is correct before executing production.
- This is presentation/validation metadata only; production and inventory writes
  are unchanged.
"""
from __future__ import annotations


def _fmt_qty(value) -> str:
    try:
        x = float(value or 0)
        if abs(x - round(x)) < 1e-9:
            return f"{int(round(x)):,}"
        return f"{x:,.2f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value or "")


def _component_label(row) -> str:
    code = str(row["item_code"] or "").strip()
    name = str(row["name"] or "").strip()
    return code or name or f"품목 {int(row['component_product_id'])}"


def _attach_bom_qty(core_module, db_path, rows):
    targets = [r for r in list(rows or []) if r.get("product_id")]
    if not targets:
        return rows

    with core_module._conn(db_path) as con:
        for r in targets:
            pid = int(r["product_id"])
            bom = con.execute(
                """SELECT b.component_product_id,b.qty_per,p.item_code,p.name
                   FROM bom_items b
                   JOIN products p ON p.id=b.component_product_id
                   WHERE b.parent_product_id=?
                   ORDER BY b.id""",
                (pid,),
            ).fetchall()
            if not bom:
                r["bom_qty_display"] = "없음"
                continue
            if len(bom) == 1:
                r["bom_qty_display"] = f"{_fmt_qty(bom[0]['qty_per'])}개 소요"
                continue
            r["bom_qty_display"] = " / ".join(
                f"{_component_label(x)} × {_fmt_qty(x['qty_per'])}"
                for x in bom
            )
    return rows


def apply(production_module, core_module):
    marker = "_rg_production_bom_qty_ui_v09183_applied"
    if production_module is None or getattr(production_module, marker, False):
        return production_module

    original_validate = production_module.validate_rows
    original_preview = production_module._preview_frame

    def validate_rows(core, parsed_rows, db_path=None):
        db = db_path or core.DEFAULT_DB
        result = original_validate(core, parsed_rows, db_path)
        _attach_bom_qty(core, db, result.get("rows") or [])
        return result

    def preview_frame(pd_obj, rows):
        frame = original_preview(pd_obj, rows)
        if "BOM" in frame.columns:
            values = []
            for r in rows:
                display = str(r.get("bom_qty_display") or "").strip()
                if not display:
                    display = "없음" if not r.get("bom_count") else f"{_fmt_qty(r.get('bom_count'))}개 구성품"
                values.append(display)
            frame["BOM"] = values
            frame = frame.rename(columns={"BOM": "BOM (완제품 1개당 소요)"})
        return frame

    production_module.validate_rows = validate_rows
    production_module._preview_frame = preview_frame
    setattr(production_module, marker, True)
    return production_module
