"""v0.9.216 Organic sales display hardening.

Guarantees the rightmost Organic sales ratio column and a clean centered table
regardless of which older organic-sales module a local updater previously kept.
"""
from __future__ import annotations

from datetime import date, timedelta
import html
import importlib
import pandas as pd


def _period_days(start: date, end: date) -> set[str]:
    out = set()
    cur = start
    while cur <= end:
        out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _count(v) -> str:
    try:
        n = float(v or 0)
    except Exception:
        n = 0.0
    return f"{int(round(n)):,}" if abs(n - round(n)) < 1e-9 else f"{n:,.2f}"


def _table_html(frame: pd.DataFrame) -> str:
    rows = []
    for _, r in frame.iterrows():
        name = html.escape(str(r.get("아이템") or ""))
        total = _count(r.get("판매량"))
        ad = _count(r.get("광고 판매량"))
        organic = _count(r.get("Organic 판매량"))
        try:
            ratio = float(r.get("Organic 판매 비율") or 0)
        except Exception:
            ratio = 0.0
        rows.append(
            f"<tr><td class='item'>{name}</td><td>{total}</td><td>{ad}</td>"
            f"<td class='organic'>{organic}</td><td class='ratio'>{ratio:.1f}%</td></tr>"
        )
    body = "".join(rows)
    return f"""
<style>
.rg-organic-wrap{{border:1px solid #dfe6ee;border-radius:12px;overflow:auto;max-height:760px;background:#fff}}
.rg-organic-table{{width:100%;border-collapse:separate;border-spacing:0;font-size:14px;color:#172033;table-layout:fixed}}
.rg-organic-table th{{position:sticky;top:0;z-index:2;background:#edf3f8;color:#334155;font-weight:750;text-align:center;padding:11px 10px;border-bottom:1px solid #cbd5e1;border-right:1px solid #dde5ed;white-space:nowrap}}
.rg-organic-table td{{text-align:center;vertical-align:middle;padding:10px 10px;border-bottom:1px solid #e7edf3;border-right:1px solid #edf1f5;font-variant-numeric:tabular-nums}}
.rg-organic-table th:first-child,.rg-organic-table td.item{{width:44%;text-align:left}}
.rg-organic-table th:nth-child(2),.rg-organic-table th:nth-child(3),.rg-organic-table th:nth-child(4){{width:13%}}
.rg-organic-table th:nth-child(5){{width:17%}}
.rg-organic-table td.organic,.rg-organic-table td.ratio{{background:#f2fbf5;font-weight:700;color:#176b3a}}
.rg-organic-table tr:hover td{{background:#f8fafc}}
.rg-organic-table tr:hover td.organic,.rg-organic-table tr:hover td.ratio{{background:#eaf8ef}}
.rg-organic-table th:last-child,.rg-organic-table td:last-child{{border-right:none}}
</style>
<div class="rg-organic-wrap"><table class="rg-organic-table">
<thead><tr><th>아이템</th><th>판매량</th><th>광고 판매량</th><th>Organic 판매량</th><th>Organic 판매 비율</th></tr></thead>
<tbody>{body}</tbody></table></div>
"""


def apply(core, db=None):
    module = importlib.import_module("organic_sales_estimate_v09211")
    if getattr(module, "_rg_display_v09216_applied", False):
        return {"ok": True, "already_applied": True}

    def render_page(st_obj, core_obj, db_path=None):
        target = db_path or db or core_obj.DEFAULT_DB
        core_obj.init_db(target)
        st_obj.session_state["_rg_sales_stats_period_active"] = False

        st_obj.markdown("## 🌱 오가닉판매 추정")
        st_obj.caption("입력한 판매자료의 판매량에서 같은 기간 광고성과보고서의 광고 판매량을 빼 Organic 판매량을 추정합니다.")
        days = st_obj.radio(
            "기간", (30, 60, 90), index=0, horizontal=True,
            format_func=lambda n: f"최근 {n}일", key="organic_sales_period_v09212",
        )
        end = date.today()
        start = end - timedelta(days=int(days) - 1)
        st_obj.caption(f"조회기간: {start.isoformat()} ~ {end.isoformat()}")

        sales_module = importlib.import_module("sales_analysis_v09186")
        frame, covered, ad_qty_available = module._organic_estimate_data(
            core_obj, sales_module, target, start, end
        )
        wanted = _period_days(start, end)

        if not ad_qty_available:
            st_obj.warning("선택 기간의 광고성과보고서에서 광고 판매량을 확인할 수 없습니다.")
        if len(covered) < len(wanted):
            st_obj.warning(
                f"선택한 최근 {int(days)}일 중 판매자료와 광고자료가 모두 있는 날짜는 {len(covered)}일입니다. "
                "표는 두 자료의 날짜가 함께 확인되는 입력 구간만 합산합니다."
            )
        else:
            st_obj.caption(f"판매자료와 광고자료가 최근 {int(days)}일 전체에 대해 확인됩니다.")

        if frame is None or frame.empty:
            st_obj.info("선택 기간에 판매자료와 광고자료가 함께 확인되는 상품이 없습니다.")
            return

        frame = frame.copy()
        for col in ("판매량", "광고 판매량", "Organic 판매량"):
            if col not in frame.columns:
                frame[col] = 0.0
            frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
        # Older local organic modules did not have this column. Always calculate it here.
        frame["Organic 판매 비율"] = frame.apply(
            lambda r: (float(r["Organic 판매량"]) / float(r["판매량"]) * 100.0)
            if float(r["판매량"]) > 0 else 0.0,
            axis=1,
        )
        if (frame["Organic 판매량"] < 0).any():
            st_obj.warning("Organic 판매량이 음수인 상품이 있습니다. 판매자료와 광고보고서의 상품 매칭 또는 입력기간을 확인해 주세요.")

        frame = frame.sort_values(["판매량", "아이템"], ascending=[False, True], kind="stable").reset_index(drop=True)
        st_obj.markdown(_table_html(frame), unsafe_allow_html=True)

    module.render_page = render_page
    module._rg_display_v09216_applied = True
    return {"ok": True, "already_applied": False, "columns": 5, "numeric_alignment": "center"}
