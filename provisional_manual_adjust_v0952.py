"""RG Manager v0.9.54 reliable manual provisional P&L product overrides.

Monthly overrides are keyed by month + Coupang option ID.  Instead of relying on
editable dataframe cells, the UI uses ordinary Streamlit inputs for one selected
product at a time, which is reliable across Streamlit/browser versions.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd


def _num(v: Any) -> float:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return 0.0
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").replace("개", "").strip()
        x = float(v)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


def _nullable(v: Any):
    if v is None:
        return None
    try:
        if pd.isna(v):
            return None
    except Exception:
        pass
    if isinstance(v, str) and not v.strip():
        return None
    try:
        x = float(v)
        return None if math.isnan(x) else x
    except Exception:
        return None


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        x = float(v)
        if math.isfinite(x) and abs(x - round(x)) < 1e-9:
            return str(int(round(x)))
    except Exception:
        pass
    s = str(v).strip()
    if s.upper().startswith("CP-"):
        s = s[3:]
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS provisional_manual_adjustments(
                   month TEXT NOT NULL,
                   option_id TEXT NOT NULL,
                   unit_price_override REAL,
                   inout_cost_override REAL,
                   delivery_cost_override REAL,
                   updated_at TEXT NOT NULL,
                   PRIMARY KEY(month,option_id)
               )"""
        )


def load(core, month: str, db_path=None) -> dict[str, dict]:
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT month,option_id,unit_price_override,inout_cost_override,
                      delivery_cost_override,updated_at
               FROM provisional_manual_adjustments WHERE month=?""",
            (str(month),),
        ).fetchall()
    return {str(r["option_id"]): dict(r) for r in rows}


def _save_one(core, month: str, oid: str, unit_price, inout_cost, delivery_cost, db):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        if unit_price is None and inout_cost is None and delivery_cost is None:
            c.execute(
                "DELETE FROM provisional_manual_adjustments WHERE month=? AND option_id=?",
                (str(month), str(oid)),
            )
            return
        c.execute(
            """INSERT INTO provisional_manual_adjustments
               (month,option_id,unit_price_override,inout_cost_override,
                delivery_cost_override,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(month,option_id) DO UPDATE SET
                 unit_price_override=excluded.unit_price_override,
                 inout_cost_override=excluded.inout_cost_override,
                 delivery_cost_override=excluded.delivery_cost_override,
                 updated_at=excluded.updated_at""",
            (
                str(month), str(oid), unit_price, inout_cost, delivery_cost,
                core.now_iso(),
            ),
        )


def _delete_one(core, month: str, oid: str, db):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        c.execute(
            "DELETE FROM provisional_manual_adjustments WHERE month=? AND option_id=?",
            (str(month), str(oid)),
        )


def _filtered_products(auto_view: pd.DataFrame, q: str):
    work = auto_view.copy()
    q = str(q or "").strip().lower()
    if q:
        words = [x for x in re.split(r"\s+", q) if x]
        hay = work.fillna("").astype(str).agg(" ".join, axis=1).str.lower()
        mask = pd.Series(True, index=work.index)
        for word in words:
            mask &= hay.str.contains(word, regex=False, na=False)
        work = work.loc[mask].copy()
    result = []
    for _, r in work.iterrows():
        oid = _oid(r.get("옵션ID"))
        if not oid:
            continue
        result.append(
            {
                "oid": oid,
                "name": str(r.get("상품명") or ""),
                "unit": max(0.0, _num(r.get("예상 실현단가"))),
                "inout": abs(_num(r.get("입출고비"))),
                "delivery": abs(_num(r.get("배송비"))),
            }
        )
    return result


def render_editor(st, core, month: str, auto_view: pd.DataFrame, db_path=None):
    db = db_path or core.DEFAULT_DB
    current = load(core, month, db)

    if auto_view is None or auto_view.empty:
        return current

    st.markdown("### 예상값 수동조정")
    st.caption(
        "런칭 쿠폰·프로모션 때문에 자동 추정이 크게 빗나갈 때 상품별로 직접 수정합니다. "
        "체크한 항목만 수동값을 적용하며, 비용은 양수로 입력하세요."
    )

    q = st.text_input(
        "조정할 상품 검색",
        placeholder="상품명 또는 옵션ID 입력",
        key=f"manual_pnl_adjust_search_{month}",
    )
    products = _filtered_products(auto_view, q)
    if not products:
        st.info("검색 결과가 없습니다.")
        return current

    labels = [f"{p['name']} [{p['oid']}]" for p in products]
    selected_label = st.selectbox(
        "수정할 상품",
        labels,
        key=f"manual_pnl_adjust_product_{month}",
    )
    p = products[labels.index(selected_label)]
    oid = p["oid"]
    saved = current.get(oid, {})

    saved_unit = _nullable(saved.get("unit_price_override"))
    saved_inout = _nullable(saved.get("inout_cost_override"))
    saved_delivery = _nullable(saved.get("delivery_cost_override"))

    st.caption(
        f"자동값 · 실현단가 {int(round(p['unit'])):,}원 · "
        f"입출고비 {int(round(p['inout'])):,}원 · 배송비 {int(round(p['delivery'])):,}원"
    )

    c1, c2, c3 = st.columns(3)
    use_unit = c1.checkbox(
        "실현단가 수동적용",
        value=saved_unit is not None,
        key=f"manual_use_unit_{month}_{oid}",
    )
    unit_value = c1.number_input(
        "예상 실현단가",
        min_value=0,
        value=int(round(saved_unit if saved_unit is not None else p["unit"])),
        step=100,
        format="%d",
        disabled=not use_unit,
        key=f"manual_unit_{month}_{oid}",
    )

    use_inout = c2.checkbox(
        "입출고비 수동적용",
        value=saved_inout is not None,
        key=f"manual_use_inout_{month}_{oid}",
    )
    inout_value = c2.number_input(
        "입출고비 총액",
        min_value=0,
        value=int(round(saved_inout if saved_inout is not None else p["inout"])),
        step=100,
        format="%d",
        disabled=not use_inout,
        key=f"manual_inout_{month}_{oid}",
    )

    use_delivery = c3.checkbox(
        "배송비 수동적용",
        value=saved_delivery is not None,
        key=f"manual_use_delivery_{month}_{oid}",
    )
    delivery_value = c3.number_input(
        "배송비 총액",
        min_value=0,
        value=int(round(saved_delivery if saved_delivery is not None else p["delivery"])),
        step=100,
        format="%d",
        disabled=not use_delivery,
        key=f"manual_delivery_{month}_{oid}",
    )

    b1, b2 = st.columns([1, 1])
    if b1.button("이 상품 수동값 저장", type="primary", key=f"manual_save_{month}_{oid}"):
        _save_one(
            core,
            month,
            oid,
            float(unit_value) if use_unit else None,
            float(inout_value) if use_inout else None,
            float(delivery_value) if use_delivery else None,
            db,
        )
        st.success(f"{p['name']}의 수동값을 저장했습니다.")
        st.rerun()

    if oid in current and b2.button("이 상품 자동값으로 복원", key=f"manual_reset_{month}_{oid}"):
        _delete_one(core, month, oid, db)
        st.success(f"{p['name']}의 수동값을 삭제하고 자동값으로 복원했습니다.")
        st.rerun()

    if current:
        st.caption(f"현재 {len(current):,}개 상품에 수동조정값이 저장되어 있습니다.")

    return load(core, month, db)


def apply_to_view(view: pd.DataFrame, adjustments: dict[str, dict]):
    if view is None or view.empty or not adjustments:
        return view, {"applied": 0}

    out = view.copy()
    applied = 0
    for idx in out.index:
        oid = _oid(out.at[idx, "옵션ID"] if "옵션ID" in out.columns else "")
        adj = adjustments.get(oid)
        if not adj:
            continue

        qty = _num(out.at[idx, "판매수량"]) if "판매수량" in out.columns else 0.0
        old_revenue = _num(out.at[idx, "예상매출"]) if "예상매출" in out.columns else 0.0
        old_commission = _num(out.at[idx, "판매수수료"]) if "판매수수료" in out.columns else 0.0

        unit_override = _nullable(adj.get("unit_price_override"))
        if unit_override is not None:
            new_revenue = qty * max(0.0, unit_override)
            if "예상 실현단가" in out.columns:
                out.at[idx, "예상 실현단가"] = max(0.0, unit_override)
            if "예상매출" in out.columns:
                out.at[idx, "예상매출"] = new_revenue
            if "판매수수료" in out.columns and abs(old_revenue) > 1e-12:
                out.at[idx, "판매수수료"] = old_commission * (new_revenue / old_revenue)

        inout_override = _nullable(adj.get("inout_cost_override"))
        if inout_override is not None and "입출고비" in out.columns:
            out.at[idx, "입출고비"] = -abs(inout_override)

        delivery_override = _nullable(adj.get("delivery_cost_override"))
        if delivery_override is not None and "배송비" in out.columns:
            out.at[idx, "배송비"] = -abs(delivery_override)

        revenue = _num(out.at[idx, "예상매출"]) if "예상매출" in out.columns else 0.0
        cogs = _num(out.at[idx, "매출원가"]) if "매출원가" in out.columns else 0.0
        commission = _num(out.at[idx, "판매수수료"]) if "판매수수료" in out.columns else 0.0
        inout = _num(out.at[idx, "입출고비"]) if "입출고비" in out.columns else 0.0
        delivery = _num(out.at[idx, "배송비"]) if "배송비" in out.columns else 0.0
        returns = _num(out.at[idx, "반품충당"]) if "반품충당" in out.columns else 0.0
        ad = _num(out.at[idx, "광고비"]) if "광고비" in out.columns else 0.0

        no_ad = revenue + cogs + commission + inout + delivery + returns
        profit = no_ad + ad
        if "광고제외이익" in out.columns:
            out.at[idx, "광고제외이익"] = no_ad
        if "예상이익" in out.columns:
            out.at[idx, "예상이익"] = profit
        if "이익률(%)" in out.columns:
            out.at[idx, "이익률(%)"] = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0
        if "RG비용" in out.columns:
            out.at[idx, "RG비용"] = inout + delivery + returns
        applied += 1

    return out, {"applied": applied}
