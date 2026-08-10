"""RG Manager v0.9.13 provisional P&L final presentation.

Goals
- One final transformation point for the provisional product P&L table.
- Hide sales quantity == 0 without relying on wrapper order.
- Re-apply moving weighted-average cost and include product cost in profit.
- Fix COGS sign for negative net sales (return/cancellation reverses COGS).
- Normalize advertising as an expense and recalculate no-ad / after-ad profit.
- Add a clean summary strip and center-align the P&L table.

This module changes presentation/calculated provisional P&L only. It does not edit
sales, inventory, purchase, production, or settlement source rows.
"""
from __future__ import annotations

import html
import importlib
import math
from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False

_REQUIRED = {"상품명", "판매수량", "예상매출", "원가/개", "매출원가", "예상이익"}


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = (
                v.replace(",", "")
                .replace("원", "")
                .replace("개", "")
                .replace("건", "")
                .replace("%", "")
                .strip()
            )
        x = float(v or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _series(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(0.0, index=df.index)
    if pd.api.types.is_numeric_dtype(df[col]):
        return pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return pd.to_numeric(
        df[col]
        .fillna("")
        .astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("원", "", regex=False)
        .str.replace("개", "", regex=False)
        .str.replace("건", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.strip(),
        errors="coerce",
    ).fillna(0.0)


def _like(old: Any, value: float, pct: bool = False):
    if isinstance(old, str):
        if pct or "%" in old:
            return f"{value:,.1f}%"
        if "원" in old:
            return f"{int(round(value)):,}원"
        if "개" in old:
            return f"{int(round(value)):,}개"
        if "," in old:
            return f"{int(round(value)):,}"
    return value


def _is_provisional(data: Any) -> bool:
    if not isinstance(data, pd.DataFrame) or data.empty:
        return False
    cols = set(map(str, data.columns))
    return _REQUIRED.issubset(cols) and bool({"옵션ID", "쿠팡 옵션ID"} & cols)


def _apply_existing_rules(core, db, data: pd.DataFrame) -> pd.DataFrame:
    """Run the business-rule transforms directly so wrapper order cannot undo them."""
    out = data.copy()

    try:
        rd = importlib.import_module("return_discount_v099")
        if hasattr(rd, "_hide_alias_rows"):
            out = rd._hide_alias_rows(core, db, out)
        if hasattr(rd, "_enhance_pnl"):
            out = rd._enhance_pnl(core, db, out)
    except Exception:
        pass

    try:
        pc = importlib.import_module("pnl_cost_commission_v0911")
        avg, by_id, by_oid = pc._moving_costs(core, db)
        known = pc._commission_history(core, db, by_oid)
        out = pc._adjust(out, avg, by_id, by_oid, known)
    except Exception:
        pass

    return out


def _recalculate(out: pd.DataFrame) -> pd.DataFrame:
    """Final, explicit provisional P&L arithmetic."""
    if out.empty:
        return out
    out = out.copy()
    qty = _series(out, "판매수량")

    # Requirement: do not show exactly-zero net sales. Negative values remain.
    out = out.loc[qty.abs() > 1e-12].copy()
    if out.empty:
        return out
    qty = _series(out, "판매수량")

    for idx in out.index:
        q = _num(qty.loc[idx])
        unit_cost = _num(out.at[idx, "원가/개"]) if "원가/개" in out.columns else 0.0

        # Positive sale consumes cost; negative net sale reverses cost.
        cogs_effect = -q * unit_cost
        if "매출원가" in out.columns:
            out.at[idx, "매출원가"] = _like(out.at[idx, "매출원가"], cogs_effect)

        revenue = _num(out.at[idx, "예상매출"]) if "예상매출" in out.columns else 0.0
        commission = _num(out.at[idx, "판매수수료"]) if "판매수수료" in out.columns else 0.0
        inout = _num(out.at[idx, "입출고비"]) if "입출고비" in out.columns else 0.0
        delivery = _num(out.at[idx, "배송비"]) if "배송비" in out.columns else 0.0
        returns = _num(out.at[idx, "반품충당"]) if "반품충당" in out.columns else 0.0

        # Advertising is a cost. Whatever sign the source used, the provisional
        # P&L shows it consistently as a negative expense.
        raw_ad = _num(out.at[idx, "광고비"]) if "광고비" in out.columns else 0.0
        ad = -abs(raw_ad) if abs(raw_ad) > 1e-12 else 0.0
        if "광고비" in out.columns:
            out.at[idx, "광고비"] = _like(out.at[idx, "광고비"], ad)

        no_ad = revenue + cogs_effect + commission + inout + delivery + returns
        after_ad = no_ad + ad

        if "광고제외이익" in out.columns:
            out.at[idx, "광고제외이익"] = _like(out.at[idx, "광고제외이익"], no_ad)
        if "예상이익" in out.columns:
            out.at[idx, "예상이익"] = _like(out.at[idx, "예상이익"], after_ad)
        if "이익률(%)" in out.columns:
            margin = after_ad / revenue * 100 if abs(revenue) > 1e-12 else 0.0
            out.at[idx, "이익률(%)"] = _like(out.at[idx, "이익률(%)"], margin, pct=True)

        if "RG비용" in out.columns:
            rg = inout + delivery + returns
            out.at[idx, "RG비용"] = _like(out.at[idx, "RG비용"], rg)

    return out


def _summary(df: pd.DataFrame) -> dict[str, float]:
    qty = float(_series(df, "판매수량").sum())
    revenue = float(_series(df, "예상매출").sum())
    cogs_effect = float(_series(df, "매출원가").sum())
    no_ad = float(_series(df, "광고제외이익").sum()) if "광고제외이익" in df.columns else 0.0
    profit = float(_series(df, "예상이익").sum())
    ad = float(_series(df, "광고비").sum()) if "광고비" in df.columns else 0.0
    margin = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0
    return {
        "qty": qty,
        "revenue": revenue,
        "product_cost": -cogs_effect,
        "no_ad_profit": no_ad,
        "ad_cost": abs(ad),
        "profit": profit,
        "margin": margin,
    }


def _money(v: float) -> str:
    return f"{int(round(v)):,}원"


def _qty(v: float) -> str:
    return f"{int(round(v)):,}개" if abs(v - round(v)) < 1e-9 else f"{v:,.1f}개"


def _summary_html(s: dict[str, float]) -> str:
    profit_class = "good" if s["profit"] >= 0 else "bad"
    return f"""
    <div class="rg-pnl-summary-wrap">
      <div class="rg-pnl-summary-head">
        <div>
          <div class="rg-pnl-badge">PROVISIONAL P&amp;L</div>
          <div class="rg-pnl-title">잠정 손익 요약</div>
          <div class="rg-pnl-sub">판매통계 기반 예상치 · 상품원가는 이동가중평균원가 반영</div>
        </div>
        <div class="rg-pnl-note">월말 정산자료 입력 후 확정손익과 비교하세요</div>
      </div>
      <div class="rg-pnl-grid">
        <div class="rg-pnl-card"><span>판매수량</span><strong>{html.escape(_qty(s['qty']))}</strong><small>순판매수량</small></div>
        <div class="rg-pnl-card primary"><span>예상매출</span><strong>{html.escape(_money(s['revenue']))}</strong><small>최근 실현단가 기준</small></div>
        <div class="rg-pnl-card cost"><span>상품원가</span><strong>{html.escape(_money(s['product_cost']))}</strong><small>이동가중평균원가</small></div>
        <div class="rg-pnl-card"><span>광고 제외 이익</span><strong>{html.escape(_money(s['no_ad_profit']))}</strong><small>상품 자체 수익성</small></div>
        <div class="rg-pnl-card {profit_class}"><span>광고 포함 이익</span><strong>{html.escape(_money(s['profit']))}</strong><small>이익률 {s['margin']:,.1f}% · 광고 {_money(s['ad_cost'])}</small></div>
      </div>
    </div>
    """


def _inject_css():
    st.markdown(
        """
        <style>
        .rg-pnl-summary-wrap{margin:8px 0 18px;padding:20px 20px 18px;background:linear-gradient(135deg,#ffffff 0%,#f7faff 100%);border:1px solid #e4eaf3;border-radius:18px;box-shadow:0 8px 24px rgba(15,23,42,.055)}
        .rg-pnl-summary-head{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:14px}
        .rg-pnl-badge{display:inline-block;font-size:10px;font-weight:800;letter-spacing:1.25px;color:#2563eb;background:#eff6ff;border:1px solid #dbeafe;border-radius:999px;padding:5px 9px;margin-bottom:6px}
        .rg-pnl-title{font-size:20px;font-weight:800;color:#0f172a;letter-spacing:-.5px}
        .rg-pnl-sub,.rg-pnl-note{font-size:12px;color:#64748b;margin-top:3px}
        .rg-pnl-note{text-align:right;background:#fff;border:1px solid #e7edf5;border-radius:10px;padding:8px 10px}
        .rg-pnl-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}
        .rg-pnl-card{position:relative;overflow:hidden;background:#fff;border:1px solid #e6ebf2;border-radius:14px;padding:14px 14px 13px;min-height:92px;box-shadow:0 3px 10px rgba(15,23,42,.03)}
        .rg-pnl-card:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:#cbd5e1}
        .rg-pnl-card.primary:before{background:#3b82f6}.rg-pnl-card.cost:before{background:#f59e0b}.rg-pnl-card.good:before{background:#10b981}.rg-pnl-card.bad:before{background:#ef4444}
        .rg-pnl-card span{display:block;font-size:11px;font-weight:700;color:#64748b;margin-bottom:8px}
        .rg-pnl-card strong{display:block;font-size:20px;line-height:1.12;font-weight:850;color:#0f172a;letter-spacing:-.6px;white-space:nowrap}
        .rg-pnl-card.primary strong{color:#2563eb}.rg-pnl-card.cost strong{color:#b45309}.rg-pnl-card.good strong{color:#047857}.rg-pnl-card.bad strong{color:#dc2626}
        .rg-pnl-card small{display:block;font-size:10px;color:#94a3b8;margin-top:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
        div[data-testid="stDataFrame"]{border:1px solid #e2e8f0;border-radius:14px;overflow:hidden;box-shadow:0 4px 14px rgba(15,23,42,.035)}
        div[data-testid="stDataFrame"] [role="columnheader"],div[data-testid="stDataFrame"] [role="gridcell"]{text-align:center!important;justify-content:center!important}
        div[data-testid="stTextInput"] input,div[data-testid="stSelectbox"]>div>div{border-radius:10px!important}
        @media(max-width:1100px){.rg-pnl-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
        @media(max-width:700px){.rg-pnl-grid{grid-template-columns:1fr 1fr}.rg-pnl-summary-head{display:block}.rg-pnl-note{margin-top:10px;text-align:left}}
        </style>
        """,
        unsafe_allow_html=True,
    )


def _styled(df: pd.DataFrame):
    # Streamlit supports pandas Styler. CSS above is a second safety net for
    # versions where the data-grid ignores part of Styler's alignment rules.
    try:
        return df.style.set_properties(**{"text-align": "center"}).set_table_styles(
            [
                {"selector": "th", "props": [("text-align", "center"), ("font-weight", "700")]},
                {"selector": "td", "props": [("text-align", "center")]},
            ]
        )
    except Exception:
        return df


def patch_source(source: str) -> str:
    # v0.9.12 renamed the menu branch, but the original page header/caption can
    # vary between old local base files. Replace both common title/caption forms.
    source = source.replace('page_header("판매·손익"', 'page_header("잠정손익"')
    source = source.replace("page_header('판매·손익'", "page_header('잠정손익'")
    source = source.replace(
        "상품별로 광고를 빼기 전과 뺀 후의 수익성을 비교합니다.",
        "판매통계로 보는 잠정 손익입니다. 월말 쿠팡 정산자료 입력 후 확정손익과 비교합니다.",
    )
    return source


def apply(core, db_path=None):
    global _APPLIED
    if _APPLIED or getattr(st, "_rg_provisional_pnl_ui_v0913", False):
        return
    db = db_path or core.DEFAULT_DB
    previous_dataframe = st.dataframe

    def dataframe(data=None, *args, **kwargs):
        if not _is_provisional(data):
            return previous_dataframe(data, *args, **kwargs)

        prepared = _apply_existing_rules(core, db, data)
        prepared = _recalculate(prepared)

        # Capture the same final numbers the user actually sees, not an older
        # intermediate dataframe.
        try:
            views = importlib.import_module("pnl_views_v0912")
            views._save_snapshot(core, db, prepared)
        except Exception:
            pass

        _inject_css()
        st.markdown(_summary_html(_summary(prepared)), unsafe_allow_html=True)

        # Old callers size the table before zero rows are removed.
        kwargs = dict(kwargs)
        if "height" in kwargs:
            try:
                kwargs["height"] = min(int(kwargs["height"]), min(760, max(230, 38 * (len(prepared) + 1))))
            except Exception:
                pass

        # Pass Styler through previous wrappers. Their dataframe-specific rules
        # are intentionally bypassed because all final P&L rules are already
        # consolidated above.
        return previous_dataframe(_styled(prepared), *args, **kwargs)

    st.dataframe = dataframe
    st._rg_provisional_pnl_ui_v0913 = True
    _APPLIED = True
