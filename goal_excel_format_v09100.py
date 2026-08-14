"""v0.9.100 target Excel display/rounding fix.

- Remove trailing decimal points from numeric cells by using integer display format.
- Round Excel-only helper unit columns (G/I/M) to whole won with ROUND(...,0).
- Keep helper columns as display-only aids; upload/save behavior remains unchanged.
"""
from __future__ import annotations

from io import BytesIO


def apply(upload_module):
    if upload_module is None or getattr(upload_module, "_rg_goal_excel_format_v09100_applied", False):
        return upload_module

    original = upload_module._template_bytes

    def template_bytes(core, db, month: str, base, old) -> bytes:
        from openpyxl import load_workbook

        data = original(core, db, month, base, old)
        bio = BytesIO(data)
        wb = load_workbook(bio)
        ws = wb["목표입력"] if "목표입력" in wb.sheetnames else wb.active

        # Whole-number presentation for target-entry convenience.
        # Underlying stored target values are not altered here except the three
        # helper formulas, which are explicitly rounded to whole won.
        numeric_cols = ("C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N")
        for row in range(2, ws.max_row + 1):
            ws[f"G{row}"] = f"=IFERROR(ROUND(F{row}/E{row},0),0)"
            ws[f"I{row}"] = f"=IFERROR(ROUND(H{row}/E{row},0),0)"
            ws[f"M{row}"] = f"=IFERROR(ROUND(L{row}/E{row},0),0)"
            for col in numeric_cols:
                ws[f"{col}{row}"].number_format = "#,##0"

        try:
            wb.calculation.fullCalcOnLoad = True
            wb.calculation.forceFullCalc = True
            wb.calculation.calcMode = "auto"
        except Exception:
            pass

        out = BytesIO()
        wb.save(out)
        return out.getvalue()

    upload_module._template_bytes = template_bytes
    upload_module._rg_goal_excel_format_v09100_applied = True
    return upload_module
