"""v0.9.197: make v0.9.196 rules affect the actual monthly provisional P&L.

The monthly screen aggregates saved snapshots and renders a custom HTML table, so
v0.9.196's core.estimated_pnl/dataframe hooks did not change already-saved rows.
This module patches the exact monthly-page adjustment extension points instead.
Confirmed settlement source rows are never edited.
"""
from __future__ import annotations

import calendar
import importlib
import math
from typing import Any

import pandas as pd

RULE_VERSION = "0.9.197-month-page-sales-stat-basis"
_SENTINEL = "__rg197_month_page__"


def _n(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").replace("개", "").replace("%", "").strip()
        x = float(v or 0)
        return x if math.isfinite(x) else 0.0
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v or "").strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _pick(cols: set[str], candidates: tuple[str, ...]) -> str | None:
    lower = {str(c).lower(): str(c) for c in cols}
    for cand in candidates:
        if cand in cols:
            return cand
        hit = lower.get(str(cand).lower())
        if hit:
            return hit
    return None


def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def _month_sales_values(core, db, month: str):
    month = str(month or "")
    try:
        y, m = (int(x) for x in month.split("-"))
        start = f"{y:04d}-{m:02d}-01"
        end = f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"
    except Exception:
        return {}

    try:
        with core._conn(db) as c:
            sc = _cols(c, "sales_stats")
            ic = _cols(c, "imports")
            if not {"import_id", "product_id"}.issubset(sc):
                return {}
            amount_col = _pick(
                sc,
                (
                    "displayed_net_sales", "net_sales_amount", "net_sales",
                    "net_revenue", "순판매금액", "순 판매 금액",
                ),
            )
            qty_col = _pick(sc, ("net_qty", "net_sales_qty", "순판매수량", "순판매상품수"))
            if amount_col is None or qty_col is None:
                return {}
            oid_expr = "s.option_id" if "option_id" in sc else "p.option_id"
            if {"id", "data_type", "period_start", "period_end"}.issubset(ic):
                join = "JOIN imports i ON i.id=s.import_id"
                where = "i.data_type='sales_stats' AND i.period_start>=? AND i.period_end<=?"
            elif {"period_start", "period_end"}.issubset(sc):
                join = ""
                where = "s.period_start>=? AND s.period_end<=?"
            else:
                return {}
            rows = c.execute(
                f"""SELECT s.product_id,{oid_expr} AS option_id,
                           SUM(COALESCE(s.{_q(amount_col)},0)) AS net_sales,
                           SUM(COALESCE(s.{_q(qty_col)},0)) AS net_qty
                    FROM sales_stats s
                    LEFT JOIN products p ON p.id=s.product_id
                    {join}
                    WHERE {where}
                    GROUP BY s.product_id,{oid_expr}""",
                (start, end),
            ).fetchall()
    except Exception:
        return {}

    out = {}
    for r in rows:
        info = {
            "pid": int(r["product_id"]),
            "oid": _oid(r["option_id"]),
            "revenue": _n(r["net_sales"]),
            "qty": _n(r["net_qty"]),
        }
        out[("p", info["pid"])] = info
        if info["oid"]:
            out[("o", info["oid"])] = info
    return out


def _existing_units(row: pd.Series, qty: float):
    if abs(qty) <= 1e-12:
        return (None, None, None, None)
    comm = abs(_n(row.get("판매수수료"))) / abs(qty) if "판매수수료" in row.index else 0.0
    i = abs(_n(row.get("입출고비"))) / abs(qty) if "입출고비" in row.index else 0.0
    d = abs(_n(row.get("배송비"))) / abs(qty) if "배송비" in row.index else 0.0
    total = i + d
    return (
        comm if comm > 1e-12 else None,
        total if total > 1e-12 else None,
        i if total > 1e-12 else None,
        d if total > 1e-12 else None,
    )


def _effective_units(basis, core, db, month: str, oid: str, pid, saved, existing):
    u = basis._units(core, db, month, oid, pid, saved, existing)
    return {
        "comm": u.get("comm"),
        "comm_source": u.get("comm_source"),
        "logi": u.get("logi"),
        "inout": u.get("inout"),
        "delivery": u.get("delivery"),
        "logi_source": u.get("logi_source"),
    }


def apply_month_view(basis, core, db, month: str, data: pd.DataFrame):
    if not isinstance(data, pd.DataFrame) or data.empty:
        return data, {"matched_sales": 0, "manual_products": 0}

    basis._schema(core, db)
    sales = _month_sales_values(core, db, month)
    manual = basis._manual(core, db, month)
    out = data.copy()
    for col in ("평균 수수료", "평균 입출고배송비"):
        if col not in out.columns:
            out[col] = 0.0

    matched = 0
    unit_rows = 0
    for idx in out.index:
        oid = _oid(out.at[idx, "옵션ID"]) if "옵션ID" in out.columns else ""
        pid = basis._pid(core, db, oid) if oid else None
        sinfo = (sales.get(("o", oid)) if oid else None) or (sales.get(("p", pid)) if pid else None)
        qty_col = "순판매수량" if "순판매수량" in out.columns else "판매수량"
        qty = _n(out.at[idx, qty_col]) if qty_col in out.columns else 0.0

        if sinfo is not None:
            qty = _n(sinfo["qty"])
            revenue = _n(sinfo["revenue"])
            if "순판매수량" in out.columns:
                out.at[idx, "순판매수량"] = qty
            elif "판매수량" in out.columns:
                out.at[idx, "판매수량"] = qty
            if "예상매출" in out.columns:
                out.at[idx, "예상매출"] = revenue
            if "예상 실현단가" in out.columns:
                out.at[idx, "예상 실현단가"] = revenue / qty if abs(qty) > 1e-12 else 0.0
            matched += 1
        else:
            revenue = _n(out.at[idx, "예상매출"]) if "예상매출" in out.columns else 0.0
            if "예상 실현단가" in out.columns and abs(qty) > 1e-12:
                out.at[idx, "예상 실현단가"] = revenue / qty

        if "원가/개" in out.columns and "매출원가" in out.columns:
            out.at[idx, "매출원가"] = -qty * abs(_n(out.at[idx, "원가/개"]))

        old = _existing_units(out.loc[idx], qty)
        u = _effective_units(basis, core, db, month, oid, pid, manual.get(oid, {}), old) if oid else {
            "comm": old[0], "comm_source": "기존 자동값",
            "logi": old[1], "inout": old[2], "delivery": old[3],
            "logi_source": "기존 자동값",
        }

        if u["comm"] is not None:
            comm_unit = max(0.0, _n(u["comm"]))
            out.at[idx, "평균 수수료"] = comm_unit
            if "판매수수료" in out.columns:
                out.at[idx, "판매수수료"] = -qty * comm_unit
            unit_rows += 1
        else:
            out.at[idx, "평균 수수료"] = 0.0

        if u["logi"] is not None:
            logi_unit = max(0.0, _n(u["logi"]))
            iu = max(0.0, _n(u["inout"])) if u["inout"] is not None else logi_unit
            du = max(0.0, _n(u["delivery"])) if u["delivery"] is not None else max(0.0, logi_unit - iu)
            split = iu + du
            if split > 1e-12 and abs(split - logi_unit) > 1e-8:
                scale = logi_unit / split
                iu, du = iu * scale, du * scale
            elif split <= 1e-12 and logi_unit > 0:
                iu, du = logi_unit, 0.0
            out.at[idx, "평균 입출고배송비"] = logi_unit
            if "입출고비" in out.columns:
                out.at[idx, "입출고비"] = -qty * iu
            if "배송비" in out.columns:
                out.at[idx, "배송비"] = -qty * du
        else:
            out.at[idx, "평균 입출고배송비"] = 0.0

        revenue = _n(out.at[idx, "예상매출"]) if "예상매출" in out.columns else 0.0
        cogs = _n(out.at[idx, "매출원가"]) if "매출원가" in out.columns else 0.0
        comm = _n(out.at[idx, "판매수수료"]) if "판매수수료" in out.columns else 0.0
        inout = _n(out.at[idx, "입출고비"]) if "입출고비" in out.columns else 0.0
        delivery = _n(out.at[idx, "배송비"]) if "배송비" in out.columns else 0.0
        ret = _n(out.at[idx, "반품충당"]) if "반품충당" in out.columns else 0.0
        ad = _n(out.at[idx, "광고비"]) if "광고비" in out.columns else 0.0
        no_ad = revenue + cogs + comm + inout + delivery + ret
        profit = no_ad + ad
        if "광고제외이익" in out.columns:
            out.at[idx, "광고제외이익"] = no_ad
        if "예상이익" in out.columns:
            out.at[idx, "예상이익"] = profit
        if "이익률(%)" in out.columns:
            out.at[idx, "이익률(%)"] = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0
        if "RG비용" in out.columns:
            out.at[idx, "RG비용"] = inout + delivery + ret

    return out, {
        "matched_sales": matched,
        "manual_products": len(manual),
        "unit_rows": unit_rows,
        "rule_version": RULE_VERSION,
    }


def render_month_editor(st_obj, basis, core, db, month: str, data: pd.DataFrame):
    current = basis._manual(core, db, month)
    rows = []
    qty_col = "순판매수량" if "순판매수량" in data.columns else "판매수량"
    for _, r in data.iterrows():
        oid = _oid(r.get("옵션ID"))
        if not oid:
            continue
        pid = basis._pid(core, db, oid)
        saved = current.get(oid, {})
        u = _effective_units(basis, core, db, month, oid, pid, saved, (None, None, None, None))
        qty, revenue = _n(r.get(qty_col)), _n(r.get("예상매출"))
        rows.append({
            "oid": oid,
            "name": str(r.get("상품명") or ""),
            "unit_price": revenue / qty if abs(qty) > 1e-12 else 0.0,
            "u": u,
            "saved": saved,
        })
    if not rows:
        return

    missing = sum(1 for x in rows if x["u"]["comm"] is None or x["u"]["logi"] is None)
    with st_obj.expander(
        f"잠정 비용 수동입력 · {month}" + (f" · 미입력/이력없음 {missing}개" if missing else ""),
        expanded=bool(missing),
    ):
        st_obj.caption(
            "예상매출·판매단가는 판매통계의 순 판매 금액과 순판매수량을 직접 사용합니다. "
            "평균 수수료·평균 입출고배송비는 이 달에만 수동 적용할 수 있고, "
            "확정 정산값은 다음 달부터 자동 기본값으로 사용합니다."
        )
        query = st_obj.text_input(
            "상품 검색", placeholder="상품명 또는 옵션ID", key=f"rg197_q_{month}"
        ).strip().lower()
        arr = [x for x in rows if not query or query in (x["name"] + " " + x["oid"]).lower()]
        if not arr:
            st_obj.info("검색 결과가 없습니다.")
            return
        labels = [f"{x['name']} [{x['oid']}]" for x in arr]
        label = st_obj.selectbox("수정할 상품", labels, key=f"rg197_p_{month}")
        item = arr[labels.index(label)]
        oid, u, saved = item["oid"], item["u"], item["saved"]
        sc = basis._nullable(saved.get("commission_unit_override"))
        sl = basis._nullable(saved.get("logistics_unit_override"))
        st_obj.caption(
            f"판매자료 단가 {int(round(item['unit_price'])):,}원 · "
            f"수수료 {int(round(_n(u['comm']))):,}원/개 ({u['comm_source']}) · "
            f"입출고배송비 {int(round(_n(u['logi']))):,}원/개 ({u['logi_source']})"
        )
        c1, c2 = st_obj.columns(2)
        use_c = c1.checkbox("평균 수수료 수동적용", value=sc is not None, key=f"rg197_uc_{month}_{oid}")
        cv = c1.number_input(
            "평균 수수료 (원/개)", min_value=0,
            value=int(round(sc if sc is not None else _n(u["comm"]))),
            step=10, format="%d", disabled=not use_c, key=f"rg197_c_{month}_{oid}"
        )
        use_l = c2.checkbox("평균 입출고배송비 수동적용", value=sl is not None, key=f"rg197_ul_{month}_{oid}")
        lv = c2.number_input(
            "평균 입출고배송비 (원/개)", min_value=0,
            value=int(round(sl if sl is not None else _n(u["logi"]))),
            step=10, format="%d", disabled=not use_l, key=f"rg197_l_{month}_{oid}"
        )
        b1, b2 = st_obj.columns(2)
        if b1.button("이 상품 잠정비용 저장", type="primary", key=f"rg197_s_{month}_{oid}"):
            basis._save(core, db, month, oid, float(cv) if use_c else None, float(lv) if use_l else None)
            st_obj.success("저장했습니다. 이 달 잠정손익에 바로 반영합니다.")
            st_obj.rerun()
        if oid in current and b2.button("수동값 삭제 · 자동값 사용", key=f"rg197_d_{month}_{oid}"):
            basis._save(core, db, month, oid, None, None)
            st_obj.success("수동값을 삭제했습니다.")
            st_obj.rerun()


def apply(core, db_path=None):
    basis = importlib.import_module("provisional_sales_basis_v09196")
    blocks = importlib.import_module("pnl_manual_blocks_v0955")
    adjust = importlib.import_module("provisional_manual_adjust_v0952")

    if not hasattr(blocks, "_rg197_original_render_adjust"):
        blocks._rg197_original_render_adjust = blocks.render_adjust

    def render_adjust(st_obj, manual_adjust_module, core_arg, month: str, auto_view, db):
        active_db = db or db_path or core_arg.DEFAULT_DB
        fixed, meta = apply_month_view(basis, core_arg, active_db, str(month), auto_view)
        render_month_editor(st_obj, basis, core_arg, active_db, str(month), fixed)
        matched = int(meta.get("matched_sales") or 0)
        if matched:
            st_obj.caption(
                f"판매통계 순 판매 금액을 직접 적용한 상품 {matched:,}개 · "
                "수수료/입출고배송비는 이 달 수동값 → 최근 확정월 평균 → 기존 자동값 순서로 적용합니다."
            )
        return {_SENTINEL: True, "month": str(month), "db": active_db, "meta": meta}

    blocks.render_adjust = render_adjust
    blocks._rg197_month_page_bridge = RULE_VERSION

    if not hasattr(adjust, "_rg197_original_apply_to_view"):
        adjust._rg197_original_apply_to_view = adjust.apply_to_view
    original_apply = adjust._rg197_original_apply_to_view

    def apply_to_view(view, adjustments):
        if isinstance(adjustments, dict) and adjustments.get(_SENTINEL):
            month = str(adjustments.get("month") or "")
            active_db = adjustments.get("db") or db_path or core.DEFAULT_DB
            fixed, meta = apply_month_view(basis, core, active_db, month, view)
            return fixed, {"applied": int(meta.get("manual_products") or 0), "rg197": meta}
        return original_apply(view, adjustments)

    adjust.apply_to_view = apply_to_view
    adjust._rg197_month_page_bridge = RULE_VERSION

    try:
        month_ui = importlib.import_module("pnl_month_v0961")
        month_ui._NUMERIC_COLS.update({"평균 수수료", "평균 입출고배송비"})
        order = list(month_ui._PRIMARY_COL_ORDER)
        for col in ("평균 수수료", "평균 입출고배송비"):
            while col in order:
                order.remove(col)
        anchor = order.index("예상 실현단가") + 1 if "예상 실현단가" in order else min(6, len(order))
        order[anchor:anchor] = ["평균 수수료", "평균 입출고배송비"]
        month_ui._PRIMARY_COL_ORDER[:] = order
        month_ui._rg197_month_page_bridge = RULE_VERSION
    except Exception:
        pass

    core._rg_pnl_month_sales_basis_v09197 = RULE_VERSION
    return {"ok": True, "rule_version": RULE_VERSION}
