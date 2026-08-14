"""v0.9.101 target Excel display/rounding fix.

- Remove trailing decimal points from numeric cells by using integer display format.
- Write Excel-only helper unit columns (G/I/M) as pre-calculated whole-won numbers,
  not formulas.
- Keep helper columns as display-only aids; upload/save behavior remains unchanged.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO


def _num(value) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _whole(value) -> int:
    try:
        return int(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        return 0


def _unit(total, qty) -> int:
    q = _num(qty)
    if abs(q) <= 1e-12:
        return 0
    return _whole(_num(total) / q)


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

        # G/I/M are convenience values calculated once when the workbook is made.
        # They intentionally contain plain numbers rather than Excel formulas.
        numeric_cols = ("C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N")
        for row in range(2, ws.max_row + 1):
            qty = ws[f"E{row}"].value
            ws[f"G{row}"] = _unit(ws[f"F{row}"].value, qty)
            ws[f"I{row}"] = _unit(ws[f"H{row}"].value, qty)
            ws[f"M{row}"] = _unit(ws[f"L{row}"].value, qty)
            for col in numeric_cols:
                ws[f"{col}{row}"].number_format = "#,##0"

        out = BytesIO()
        wb.save(out)
        return out.getvalue()

    upload_module._template_bytes = template_bytes
    upload_module._rg_goal_excel_format_v09100_applied = True
    return upload_module
