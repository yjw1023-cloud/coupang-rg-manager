"""RG Manager v0.9.14 monthly-default P&L periods.

Rules
- Provisional P&L opens as a monthly view by default.
- The selected month always represents first day through last day of that month.
- Monthly provisional totals aggregate saved provisional snapshots that belong
  wholly to that month. Cross-month source periods are never prorated.
- The legacy source-file provisional screen remains available in a secondary tab.
- Confirmed P&L and variance month selectors default to the current month when
  that month exists, and display the month-start/month-end period explicitly.
"""
from __future__ import annotations

import calendar
from datetime import date
import importlib
import json
import re
from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False


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
        return float(v or 0)
    except Exception:
        return 0.0


def _month_bounds(month: str):
    y, m = [int(x) for x in str(month).split("-")]
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def _current_month() -> str:
    return date.today().strftime("%Y-%m")


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _available_months(core, db) -> list[str]:
    months = {_current_month()}
    with core._conn(db) as c:
        if _exists(c, "imports"):
            ic = _cols(c, "imports")
            if {"data_type", "period_start", "period_end"}.issubset(ic):
                rows = c.execute(
                    """SELECT period_start,period_end FROM imports
                       WHERE data_type='sales_stats'
                       ORDER BY period_start DESC, id DESC"""
                ).fetchall()
                for r in rows:
                    ps = str(r["period_start"] or "")
                    pe = str(r["period_end"] or "")
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ps):
                        months.add(ps[:7])
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", pe):
                        months.add(pe[:7])
        if _exists(c, "provisional_pnl_snapshots"):
            rows = c.execute(
                "SELECT period_start,period_end FROM provisional_pnl_snapshots"
            ).fetchall()
            for r in rows:
                for x in (str(r["period_start"] or ""), str(r["period_end"] or "")):
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", x):
                        months.add(x[:7])
    return sorted(months, reverse=True)


def _snapshot_rows_for_month(core, db, month: str):
    start, end = _month_bounds(month)
    rows = []
    excluded_cross = []
    with core._conn(db) as c:
        if not _exists(c, "provisional_pnl_snapshots"):
            return rows, excluded_cross
        snaps = c.execute(
            """SELECT import_id,file_name,period_start,period_end,captured_at,rows_json
               FROM provisional_pnl_snapshots
               WHERE period_end>=? AND period_start<=?
               ORDER BY period_start,period_end,import_id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()

    for s in snaps:
        ps = str(s["period_start"] or "")
        pe = str(s["period_end"] or "")
        try:
            a = date.fromisoformat(ps)
            b = date.fromisoformat(pe)
        except Exception:
            continue
        if a < start or b > end:
            excluded_cross.append(
                {
                    "file_name": str(s["file_name"] or ""),
                    "period_start": ps,
                    "period_end": pe,
                }
            )
            continue
        try:
            payload = json.loads(str(s["rows_json"] or "[]"))
        except Exception:
            payload = []
        for r in payload:
            if not isinstance(r, dict):
                continue
            x = dict(r)
            x["_period_start"] = ps
            x["_period_end"] = pe
            rows.append(x)
    return rows, excluded_cross


def _coverage(core, db, month: str):
    start, end = _month_bounds(month)
    days = {}
    imports = []
    with core._conn(db) as c:
        if not _exists(c, "imports"):
            return {"covered": 0, "expected": (end-start).days+1, "missing_snapshots": 0, "imports": []}
        ic = _cols(c, "imports")
        if not {"id", "data_type", "period_start", "period_end"}.issubset(ic):
            return {"covered": 0, "expected": (end-start).days+1, "missing_snapshots": 0, "imports": []}
        file_expr = "file_name" if "file_name" in ic else "''"
        imps = c.execute(
            f"""SELECT id,{file_expr} file_name,period_start,period_end
                FROM imports
                WHERE data_type='sales_stats' AND period_end>=? AND period_start<=?
                ORDER BY period_start,period_end,id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        snap_ids = set()
        if _exists(c, "provisional_pnl_snapshots"):
            snap_ids = {
                int(r["import_id"])
                for r in c.execute("SELECT import_id FROM provisional_pnl_snapshots").fetchall()
            }

    missing = 0
    for r in imps:
        ps, pe = str(r["period_start"] or ""), str(r["period_end"] or "")
        try:
            a, b = date.fromisoformat(ps), date.fromisoformat(pe)
        except Exception:
            continue
        imports.append(
            {
                "id": int(r["id"]),
                "file_name": str(r["file_name"] or ""),
                "period_start": ps,
                "period_end": pe,
                "snapshot": int(r["id"]) in snap_ids,
            }
        )
        if int(r["id"]) not in snap_ids:
            missing += 1
        d = max(a, start)
        stop = min(b, end)
        while d <= stop:
            days[d] = days.get(d, 0) + 1
            d = date.fromordinal(d.toordinal() + 1)
    expected = (end - start).days + 1
    covered = sum(
        1
        for n in range(expected)
        if date.fromordinal(start.toordinal() + n) in days
    )
    return {
        "covered": covered,
        "expected": expected,
        "missing_snapshots": missing,
        "imports": imports,
    }


def _aggregate(rows: list[dict[str, Any]]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    money_cols = [
        "예상매출",
        "매출원가",
        "판매수수료",
        "입출고비",
        "배송비",
        "반품충당",
        "광고비",
        "광고제외이익",
        "예상이익",
    ]
    for r in rows:
        oid = str(r.get("옵션ID") or "").strip()
        name = str(r.get("상품명") or "").strip()
        if not oid and not name:
            continue
        key = (oid, name)
        x = grouped.setdefault(
            key,
            {
                "옵션ID": oid,
                "상품명": name,
                "판매수량": 0.0,
                **{c: 0.0 for c in money_cols},
            },
        )
        x["판매수량"] += _num(r.get("판매수량"))
        for c in money_cols:
            x[c] += _num(r.get(c))

    out = []
    for x in grouped.values():
        qty = _num(x["판매수량"])
        if abs(qty) <= 1e-12:
            continue
        revenue = _num(x["예상매출"])
        cogs = _num(x["매출원가"])
        x["예상 실현단가"] = revenue / qty if abs(qty) > 1e-12 else 0.0
        x["원가/개"] = abs(cogs / qty) if abs(qty) > 1e-12 else 0.0
        x["이익률(%)"] = _num(x["예상이익"]) / revenue * 100 if abs(revenue) > 1e-12 else 0.0
        x["RG비용"] = _num(x["입출고비"]) + _num(x["배송비"]) + _num(x["반품충당"])
        out.append(x)
    if not out:
        return pd.DataFrame()
    df = pd.DataFrame(out)
    order = [
        "옵션ID",
        "상품명",
        "판매수량",
        "예상 실현단가",
        "예상매출",
        "원가/개",
        "매출원가",
        "판매수수료",
        "입출고비",
        "배송비",
        "반품충당",
        "광고비",
        "광고제외이익",
        "예상이익",
        "이익률(%)",
        "RG비용",
    ]
    return df[[c for c in order if c in df.columns]]


def _search(df: pd.DataFrame, q: str):
    q = str(q or "").strip().lower()
    if not q or df.empty:
        return df
    words = [x for x in re.split(r"\s+", q) if x]
    hay = df.fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    mask = pd.Series(True, index=df.index)
    for word in words:
        mask &= hay.str.contains(word, regex=False, na=False)
    return df.loc[mask].copy()


def _format(df: pd.DataFrame):
    show = df.copy()
    if show.empty:
        return show
    money_cols = [
        "예상 실현단가",
        "예상매출",
        "원가/개",
        "매출원가",
        "판매수수료",
        "입출고비",
        "배송비",
        "반품충당",
        "광고비",
        "광고제외이익",
        "예상이익",
        "RG비용",
    ]
    show["판매수량"] = show["판매수량"].map(
        lambda v: f"{int(round(_num(v))):,}" if abs(_num(v)-round(_num(v))) < 1e-9 else f"{_num(v):,.1f}"
    )
    for c in money_cols:
        if c in show.columns:
            show[c] = show[c].map(lambda v: f"{int(round(_num(v))):,}")
    if "이익률(%)" in show.columns:
        show["이익률(%)"] = show["이익률(%)"].map(lambda v: f"{_num(v):,.1f}%")
    return show


def _period_strip(st_obj, month: str, cov: dict):
    start, end = _month_bounds(month)
    st_obj.markdown(
        f"""
        <div style="display:flex;align-items:center;justify-content:space-between;
                    gap:12px;padding:12px 15px;margin:5px 0 12px;
                    background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;">
          <div>
            <span style="font-size:12px;color:#64748b;font-weight:700;">기본 조회기간</span>
            <span style="margin-left:10px;font-size:15px;color:#0f172a;font-weight:800;">
              {start.isoformat()} ~ {end.isoformat()}
            </span>
          </div>
          <div style="font-size:12px;color:#64748b;">
            판매자료 확보 {cov.get('covered',0)}/{cov.get('expected',0)}일
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_provisional_page(st_obj, pd_obj, core, legacy_renderer, db_path=None):
    db = db_path or core.DEFAULT_DB
    tabs = st_obj.tabs(["월간 잠정손익", "자료별 보기"])

    with tabs[0]:
        months = _available_months(core, db)
        cur = _current_month()
        default_idx = months.index(cur) if cur in months else 0
        month = st_obj.selectbox(
            "조회 월",
            months,
            index=default_idx,
            key="provisional_month_v0914",
        )
        cov = _coverage(core, db, month)
        _period_strip(st_obj, month, cov)

        rows, excluded = _snapshot_rows_for_month(core, db, month)
        view = _aggregate(rows)

        if cov.get("missing_snapshots", 0):
            st_obj.warning(
                f"이 달의 판매자료 중 잠정손익이 아직 계산·저장되지 않은 자료가 "
                f"{cov['missing_snapshots']:,}개 있습니다. "
                "오른쪽 '자료별 보기' 탭에서 해당 판매자료를 한 번 열면 월간 잠정손익에 포함됩니다."
            )
        if excluded:
            st_obj.warning(
                f"월을 걸쳐 있는 판매자료 {len(excluded):,}개는 월별로 정확히 나눌 수 없어 "
                "월간 합계에서 제외했습니다. 판매자료 입력 시 월 경계에서 기간을 나눠 입력해 주세요."
            )

        if view.empty:
            st_obj.info(
                f"{month}에 저장된 잠정손익 자료가 아직 없습니다. "
                "'자료별 보기'에서 해당 월 판매자료를 한 번 확인하면 월간 화면에 누적됩니다."
            )
        else:
            q = st_obj.text_input(
                "상품 검색",
                placeholder="상품명 또는 옵션ID 입력",
                key="provisional_month_search_v0914",
            )
            filtered = _search(view, q)
            if q.strip():
                st_obj.caption(f"검색 결과 {len(filtered):,}개 / 전체 {len(view):,}개")
            try:
                ui = importlib.import_module("provisional_pnl_ui_v0913")
                ui._inject_css()
                st_obj.markdown(ui._summary_html(ui._summary(filtered)), unsafe_allow_html=True)
            except Exception:
                pass

            show = _format(filtered)
            try:
                show_obj = show.style.set_properties(**{"text-align": "center"}).set_table_styles(
                    [
                        {"selector": "th", "props": [("text-align", "center"), ("font-weight", "700")]},
                        {"selector": "td", "props": [("text-align", "center")]},
                    ]
                )
            except Exception:
                show_obj = show
            st_obj.dataframe(
                show_obj,
                use_container_width=True,
                hide_index=True,
                height=min(760, max(230, 38 * (len(filtered) + 1))),
                key="_rg_monthly_provisional_v0914",
            )

            if cov.get("imports"):
                with st_obj.expander("이 달에 합산되는 판매자료 확인"):
                    src = pd_obj.DataFrame(
                        [
                            {
                                "기간": f"{x['period_start']} ~ {x['period_end']}",
                                "파일": x["file_name"],
                                "잠정손익 저장": "완료" if x["snapshot"] else "미생성",
                            }
                            for x in cov["imports"]
                        ]
                    )
                    st_obj.dataframe(src, use_container_width=True, hide_index=True)

    with tabs[1]:
        st_obj.caption(
            "특정 판매자료 한 건의 잠정손익을 확인하는 화면입니다. "
            "월간 잠정손익은 이 자료별 계산 결과를 해당 월에 누적합니다."
        )
        legacy_renderer()


def _wrap_month_selectboxes():
    if getattr(st, "_rg_month_selectbox_v0914", False):
        return
    previous = st.selectbox

    def selectbox(label, options, *args, **kwargs):
        if str(label) in {"확정 월", "분석 월"}:
            vals = list(options)
            if vals and "index" not in kwargs:
                cur = _current_month()
                kwargs["index"] = vals.index(cur) if cur in vals else 0
            result = previous(label, options, *args, **kwargs)
            try:
                start, end = _month_bounds(str(result))
                st.caption(f"조회기간 {start.isoformat()} ~ {end.isoformat()}")
            except Exception:
                pass
            return result
        return previous(label, options, *args, **kwargs)

    st.selectbox = selectbox
    st._rg_month_selectbox_v0914 = True


def patch_source(source: str) -> str:
    branch = 'elif page == "📈  잠정손익":'
    confirmed_marker = (
        '# ------------------------------\n'
        '# Confirmed P&L\n'
        '# ------------------------------\n'
    )
    start = source.find(branch)
    marker = source.find(confirmed_marker, start if start >= 0 else 0)
    if start < 0 or marker < 0:
        raise RuntimeError("v0.9.14 잠정손익 월간 기본 화면을 적용할 위치를 찾지 못했습니다.")

    line_end = source.find("\n", start)
    body = source[line_end + 1 : marker].rstrip("\n")
    if not body.strip():
        raise RuntimeError("v0.9.14 기존 잠정손익 본문을 찾지 못했습니다.")

    helper_name = "_rg_render_legacy_provisional_v0914"
    helper = f"def {helper_name}():\n{body}\n\n\n"

    dispatch = source.find("if page ==")
    if dispatch < 0:
        raise RuntimeError("v0.9.14 페이지 분기 시작점을 찾지 못했습니다.")
    source = source[:dispatch] + helper + source[dispatch:]

    start = source.find(branch)
    marker = source.find(confirmed_marker, start)
    replacement = (
        'elif page == "📈  잠정손익":\n'
        f'    pnl_month_default_v0914.render_provisional_page(st, pd, core, {helper_name})\n\n\n'
    )
    source = source[:start] + replacement + source[marker:]
    return source


def apply():
    global _APPLIED
    if _APPLIED or getattr(st, "_rg_month_default_v0914", False):
        return
    _wrap_month_selectboxes()
    st._rg_month_default_v0914 = True
    _APPLIED = True
