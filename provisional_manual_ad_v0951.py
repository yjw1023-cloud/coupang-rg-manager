"""RG Manager v0.9.51 manual provisional advertising spend.

Allows one user-entered cumulative ad-spend record per month. The amount is
VAT-exclusive and can cover any date range inside the selected month.

Monthly P&L application rule:
- manual amount overrides automatic ad-performance spend only for the overlapping
  dates, so the two sources are never double-counted;
- source snapshot rows are weighted by expected revenue and overlap-day fraction;
- for a partially overlapping source period, only the overlapping share of the
  automatic ad amount is replaced;
- profit is recalculated after the manual ad allocation, while no-ad profit stays
  unchanged.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Any


def _num(v: Any) -> float:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").strip()
        return float(v or 0)
    except Exception:
        return 0.0


def _month_bounds(month: str):
    y, m = [int(x) for x in str(month).split("-")]
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def _default_range(month: str):
    start, month_end = _month_bounds(month)
    yesterday = date.today() - timedelta(days=1)
    if yesterday < start:
        end = start
    elif yesterday > month_end:
        end = month_end
    else:
        end = yesterday
    return start, end


def _ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS provisional_manual_ad_spend(
                   month TEXT PRIMARY KEY,
                   period_start TEXT NOT NULL,
                   period_end TEXT NOT NULL,
                   amount_ex_vat REAL NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )


def load(core, month: str, db_path=None):
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    with core._conn(db) as c:
        r = c.execute(
            """SELECT month,period_start,period_end,amount_ex_vat,updated_at
               FROM provisional_manual_ad_spend WHERE month=?""",
            (str(month),),
        ).fetchone()
    return dict(r) if r else None


def save(core, month: str, period_start: date, period_end: date, amount: float, db_path=None):
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    month_start, month_end = _month_bounds(month)
    if period_start > period_end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    if period_start < month_start or period_end > month_end:
        raise ValueError("광고비 입력 기간은 선택한 조회 월 안에서 지정해 주세요.")
    if float(amount) < 0:
        raise ValueError("광고비는 0원 이상으로 입력해 주세요.")
    with core._conn(db) as c:
        c.execute(
            """INSERT INTO provisional_manual_ad_spend
               (month,period_start,period_end,amount_ex_vat,updated_at)
               VALUES(?,?,?,?,?)
               ON CONFLICT(month) DO UPDATE SET
                 period_start=excluded.period_start,
                 period_end=excluded.period_end,
                 amount_ex_vat=excluded.amount_ex_vat,
                 updated_at=excluded.updated_at""",
            (
                str(month),
                period_start.isoformat(),
                period_end.isoformat(),
                float(amount),
                core.now_iso(),
            ),
        )


def delete(core, month: str, db_path=None):
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    with core._conn(db) as c:
        c.execute("DELETE FROM provisional_manual_ad_spend WHERE month=?", (str(month),))


def render_input(st, core, month: str, db_path=None):
    db = db_path or core.DEFAULT_DB
    current = load(core, month, db)
    default_start, default_end = _default_range(month)
    if current:
        try:
            default_start = date.fromisoformat(str(current["period_start"]))
            default_end = date.fromisoformat(str(current["period_end"]))
        except Exception:
            pass
    amount_default = float(current["amount_ex_vat"]) if current else 0.0

    with st.expander("광고비 수동입력 · 부가세 제외", expanded=current is None):
        st.caption(
            "선택 기간의 쿠팡 광고비 총액을 입력합니다. 같은 날짜에 광고성과보고서가 있어도 "
            "수동입력 금액을 우선 적용해 중복 차감하지 않습니다."
        )
        c1, c2, c3 = st.columns([1, 1, 1.25])
        start_value = c1.date_input(
            "시작일",
            value=default_start,
            key=f"manual_ad_start_{month}",
        )
        end_value = c2.date_input(
            "종료일",
            value=default_end,
            key=f"manual_ad_end_{month}",
        )
        amount_value = c3.number_input(
            "광고비 총액 (부가세 제외)",
            min_value=0,
            value=int(round(amount_default)),
            step=1000,
            format="%d",
            key=f"manual_ad_amount_{month}",
        )

        b1, b2 = st.columns([1, 1])
        if b1.button("광고비 저장", type="primary", key=f"manual_ad_save_{month}"):
            try:
                save(core, month, start_value, end_value, float(amount_value), db)
                st.success(
                    f"{start_value.isoformat()} ~ {end_value.isoformat()} 광고비 "
                    f"{int(round(float(amount_value))):,}원(부가세 제외)을 저장했습니다."
                )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        if current and b2.button("수동 광고비 삭제", key=f"manual_ad_delete_{month}"):
            delete(core, month, db)
            st.success("이 달의 수동 광고비 입력값을 삭제했습니다.")
            st.rerun()

        if current:
            st.info(
                f"현재 적용값: {current['period_start']} ~ {current['period_end']} · "
                f"{int(round(_num(current['amount_ex_vat']))):,}원 (부가세 제외)"
            )

    return current


def _period_overlap_fraction(row: dict, manual_start: date, manual_end: date) -> float:
    try:
        a = date.fromisoformat(str(row.get("_period_start") or ""))
        b = date.fromisoformat(str(row.get("_period_end") or ""))
    except Exception:
        return 0.0
    if b < a:
        return 0.0
    overlap_start = max(a, manual_start)
    overlap_end = min(b, manual_end)
    if overlap_end < overlap_start:
        return 0.0
    total_days = (b - a).days + 1
    overlap_days = (overlap_end - overlap_start).days + 1
    return overlap_days / total_days if total_days > 0 else 0.0


def apply_to_rows(rows: list[dict], record: dict | None):
    if not rows or not record:
        return rows, {"applied": False, "amount": 0.0, "eligible_rows": 0}
    amount = max(0.0, _num(record.get("amount_ex_vat")))
    try:
        manual_start = date.fromisoformat(str(record.get("period_start") or ""))
        manual_end = date.fromisoformat(str(record.get("period_end") or ""))
    except Exception:
        return rows, {"applied": False, "amount": 0.0, "eligible_rows": 0}

    out = [dict(r) for r in rows]
    eligible = []
    weights = []
    for idx, r in enumerate(out):
        frac = _period_overlap_fraction(r, manual_start, manual_end)
        if frac <= 0:
            continue
        revenue = abs(_num(r.get("예상매출")))
        qty = abs(_num(r.get("판매수량")))
        weight = revenue * frac
        if weight <= 0:
            weight = qty * frac
        eligible.append((idx, frac))
        weights.append(weight)

    if not eligible:
        return out, {
            "applied": False,
            "amount": amount,
            "eligible_rows": 0,
            "warning": "입력 기간과 겹치는 잠정손익 판매자료가 없습니다.",
        }

    total_weight = sum(weights)
    if total_weight <= 0:
        weights = [1.0 for _ in eligible]
        total_weight = float(len(weights))

    for (idx, frac), weight in zip(eligible, weights):
        r = out[idx]
        old_ad = _num(r.get("광고비"))
        # Snapshot ad is stored as a negative expense. Keep the non-overlap share,
        # and replace only the overlapping share with the manual total allocation.
        kept_auto_ad = old_ad * (1.0 - frac)
        manual_alloc = -amount * (weight / total_weight)
        new_ad = kept_auto_ad + manual_alloc
        r["광고비"] = new_ad

        no_ad = _num(r.get("광고제외이익"))
        if "광고제외이익" not in r:
            revenue = _num(r.get("예상매출"))
            no_ad = (
                revenue
                + _num(r.get("매출원가"))
                + _num(r.get("판매수수료"))
                + _num(r.get("입출고비"))
                + _num(r.get("배송비"))
                + _num(r.get("반품충당"))
            )
            r["광고제외이익"] = no_ad
        profit = no_ad + new_ad
        r["예상이익"] = profit
        revenue = _num(r.get("예상매출"))
        r["이익률(%)"] = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0

    return out, {
        "applied": True,
        "amount": amount,
        "eligible_rows": len(eligible),
        "period_start": manual_start.isoformat(),
        "period_end": manual_end.isoformat(),
    }


def render_applied_notice(st, meta: dict):
    if not meta:
        return
    if meta.get("applied"):
        st.caption(
            f"수동 광고비 적용: {meta.get('period_start')} ~ {meta.get('period_end')} · "
            f"{int(round(_num(meta.get('amount')))):,}원(부가세 제외) · "
            "상품별 예상매출 비중으로 배분"
        )
    elif meta.get("warning"):
        st.warning(str(meta["warning"]))
