"""RG Manager v0.9.52 manual provisional P&L product overrides.

User-entered monthly overrides for launch/promotion periods where automatic estimates
can be materially wrong.  Overrides are keyed by selected month + Coupang option ID.

Editable fields:
- expected realized unit price
- inbound/outbound fee total
- delivery fee total

Blank override cells keep the automatic estimate.  Fee inputs are entered as
positive costs in the editor and stored as positive values; P&L applies them as
negative expenses.  Reset removes all overrides for that product/month.

When realized unit price is overridden, expected revenue is recalculated and the
existing effective commission rate is preserved so commission scales with revenue.
All downstream profit fields are recalculated immediately.
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
               FROM provisional_manual_adjustments
               WHERE month=?""",
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


def render_editor(st, core, month: str, auto_view: pd.DataFrame, db_path=None):
    db = db_path or core.DEFAULT_DB
    current = load(core, month, db)
    count = len(current)

    title = "예상값 수동조정 · 실현단가 / 입출고비 / 배송비"
    if count:
        title += f" · {count}개 상품 적용중"

    with st.expander(title, expanded=False):
        st.caption(
            "런칭 쿠폰·프로모션 등으로 자동 추정이 맞지 않을 때 사용합니다. "
            "수동값이 있는 항목만 자동값을 덮어씁니다. 입출고비와 배송비는 비용 총액을 양수로 입력하세요."
        )

        if auto_view is None or auto_view.empty:
            st.info("조정할 잠정손익 상품이 없습니다.")
            return current

        q = st.text_input(
            "조정할 상품 검색",
            placeholder="상품명 또는 옵션ID 입력",
            key=f"manual_pnl_adjust_search_{month}",
        )
        work = auto_view.copy()
        if q.strip():
            words = [x for x in re.split(r"\s+", q.strip().lower()) if x]
            hay = work.fillna("").astype(str).agg(" ".join, axis=1).str.lower()
            mask = pd.Series(True, index=work.index)
            for word in words:
                mask &= hay.str.contains(word, regex=False, na=False)
            work = work.loc[mask].copy()

        rows = []
        for _, r in work.iterrows():
            oid = _oid(r.get("옵션ID"))
            if not oid:
                continue
            saved = current.get(oid, {})
            rows.append(
                {
                    "옵션ID": oid,
                    "상품명": str(r.get("상품명") or ""),
                    "자동 실현단가": int(round(_num(r.get("예상 실현단가")))),
                    "수동 실현단가": saved.get("unit_price_override"),
                    "자동 입출고비": int(round(abs(_num(r.get("입출고비"))))),
                    "수동 입출고비": saved.get("inout_cost_override"),
                    "자동 배송비": int(round(abs(_num(r.get("배송비"))))),
                    "수동 배송비": saved.get("delivery_cost_override"),
                    "자동값 복원": False,
                }
            )

        if not rows:
            st.info("검색 결과가 없습니다.")
            return current

        edit_df = pd.DataFrame(rows)
        column_config = {
            "옵션ID": st.column_config.TextColumn("옵션ID", disabled=True),
            "상품명": st.column_config.TextColumn("상품명", disabled=True, width="large"),
            "자동 실현단가": st.column_config.NumberColumn("자동 실현단가", disabled=True, format="%d"),
            "수동 실현단가": st.column_config.NumberColumn("수동 실현단가", min_value=0, step=100, format="%d"),
            "자동 입출고비": st.column_config.NumberColumn("자동 입출고비", disabled=True, format="%d"),
            "수동 입출고비": st.column_config.NumberColumn("수동 입출고비", min_value=0, step=100, format="%d"),
            "자동 배송비": st.column_config.NumberColumn("자동 배송비", disabled=True, format="%d"),
            "수동 배송비": st.column_config.NumberColumn("수동 배송비", min_value=0, step=100, format="%d"),
            "자동값 복원": st.column_config.CheckboxColumn("자동값 복원"),
        }
        edited = st.data_editor(
            edit_df,
            use_container_width=True,
            hide_index=True,
            column_config=column_config,
            disabled=["옵션ID", "상품명", "자동 실현단가", "자동 입출고비", "자동 배송비"],
            key=f"manual_pnl_adjust_editor_{month}",
        )

        if st.button("수동조정 저장", type="primary", key=f"manual_pnl_adjust_save_{month}"):
            changed = 0
            reset = 0
            for _, r in edited.iterrows():
                oid = _oid(r.get("옵션ID"))
                if not oid:
                    continue
                if bool(r.get("자동값 복원")):
                    _delete_one(core, month, oid, db)
                    reset += 1
                    continue
                unit_price = _nullable(r.get("수동 실현단가"))
                inout_cost = _nullable(r.get("수동 입출고비"))
                delivery_cost = _nullable(r.get("수동 배송비"))
                if unit_price is not None and unit_price < 0:
                    unit_price = 0.0
                if inout_cost is not None:
                    inout_cost = abs(inout_cost)
                if delivery_cost is not None:
                    delivery_cost = abs(delivery_cost)
                _save_one(core, month, oid, unit_price, inout_cost, delivery_cost, db)
                changed += 1
            st.success(f"수동조정 {changed:,}개 저장 · 자동값 복원 {reset:,}개")
            st.rerun()

        if current:
            st.caption("수동값을 비우고 저장하면 해당 항목은 자동추정값으로 돌아갑니다. 세 항목 모두 비우면 그 상품의 수동조정 기록이 삭제됩니다.")

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
            # Preserve the automatic effective commission rate when sales value changes.
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
