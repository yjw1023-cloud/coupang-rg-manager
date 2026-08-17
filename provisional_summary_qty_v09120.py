"""v0.9.120 provisional summary quantity semantics.

The monthly provisional P&L table intentionally exposes three quantities:
- 판매수량: gross sold units before cancellations
- 취소수량: cancelled/refunded units
- 순판매수량: signed net units used for P&L and inventory arithmetic

Before this patch the top summary card summed the visible gross 판매수량 while
labelling it as 순판매수량.  This patch keeps the detailed table unchanged but
makes the summary use 순판매수량, shows gross/cancel counts underneath, and
surfaces a warning if gross - cancel != net.
"""
from __future__ import annotations

from typing import Any


_APPLIED = False


def _sum(ui, df, col: str) -> float:
    if df is None or getattr(df, "empty", True) or col not in getattr(df, "columns", []):
        return 0.0
    try:
        return float(ui._series(df, col).sum())
    except Exception:
        total = 0.0
        try:
            for v in df[col]:
                total += float(ui._num(v))
        except Exception:
            return 0.0
        return total


def apply(ui_module: Any):
    global _APPLIED
    if _APPLIED or getattr(ui_module, "_rg_provisional_summary_qty_v09120_applied", False):
        return ui_module

    original_summary = ui_module._summary
    original_summary_html = ui_module._summary_html

    def _summary(df):
        s = dict(original_summary(df))
        cols = set(map(str, getattr(df, "columns", [])))

        gross = _sum(ui_module, df, "판매수량")
        cancel = _sum(ui_module, df, "취소수량") if "취소수량" in cols else 0.0

        if "순판매수량" in cols:
            net = _sum(ui_module, df, "순판매수량")
            verifiable = "취소수량" in cols
        elif "취소수량" in cols:
            net = gross - cancel
            verifiable = True
        else:
            net = gross
            verifiable = False

        gap = (gross - cancel) - net if verifiable else 0.0

        # The financial values in the provisional P&L are based on signed net
        # quantity.  The summary quantity must therefore use the same basis.
        s["qty"] = net
        s["gross_qty"] = gross
        s["cancel_qty"] = cancel
        s["net_qty"] = net
        s["qty_gap"] = gap
        s["qty_verifiable"] = verifiable
        s["qty_consistent"] = (not verifiable) or abs(gap) <= 1e-9
        return s

    def _summary_html(s):
        out = original_summary_html(s)
        try:
            gross_text = ui_module._qty(float(s.get("gross_qty", s.get("qty", 0)) or 0))
            cancel_text = ui_module._qty(float(s.get("cancel_qty", 0) or 0))
            net_text = ui_module._qty(float(s.get("net_qty", s.get("qty", 0)) or 0))
        except Exception:
            gross_text = str(s.get("gross_qty", ""))
            cancel_text = str(s.get("cancel_qty", ""))
            net_text = str(s.get("net_qty", ""))

        out = out.replace("<span>판매수량</span>", "<span>순판매수량</span>", 1)
        out = out.replace(
            "<small>순판매수량</small>",
            f"<small>총 판매 {gross_text} · 취소/환불 {cancel_text}</small>",
            1,
        )

        if s.get("qty_verifiable") and not s.get("qty_consistent"):
            gap = float(s.get("qty_gap", 0) or 0)
            warning = (
                '<div style="margin-top:10px;padding:9px 11px;border:1px solid #fecaca;'
                'background:#fff1f2;border-radius:10px;color:#b91c1c;font-size:12px;font-weight:700;">'
                f'수량 검산 오류: 총 판매 {gross_text} - 취소/환불 {cancel_text} ≠ 순판매 {net_text} '
                f'(차이 {gap:,.1f}개). 판매통계 원본을 확인하세요.'</n                'div>'
            )
            # Insert the warning inside the summary wrapper, just before its
            # final closing div.
            pos = out.rfind("</div>")
            if pos >= 0:
                out = out[:pos] + warning + out[pos:]
            else:
                out += warning
        return out

    ui_module._summary = _summary
    ui_module._summary_html = _summary_html
    ui_module._rg_provisional_summary_qty_v09120_applied = True
    _APPLIED = True
    return ui_module
