"""RG Manager v0.9.12 P&L views.

- Rename the existing product P&L screen to 잠정손익.
- Add a dedicated 확정손익 page backed by Coupang monthly settlement data.
- Capture the *displayed* provisional P&L as a snapshot, so later confirmed
  settlement data can be compared against what the ERP actually estimated then.
- Add 잠정↔확정 variance analysis by revenue, COGS, commission, RG/return costs,
  advertising, and profit.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
import json
import math
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
        x = float(v or 0)
        return 0.0 if math.isnan(x) else x
    except Exception:
        return 0.0


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


def _fmt_money(v: Any) -> str:
    return f"{int(round(_num(v))):,}원"


def _fmt_pct(v: Any) -> str:
    return f"{_num(v):,.1f}%"


def _fmt_qty(v: Any) -> str:
    n = _num(v)
    return f"{int(round(n)):,}개" if abs(n - round(n)) < 1e-9 else f"{n:,.2f}개"


def _exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _cols(c, table: str) -> set[str]:
    if not _exists(c, table):
        return set()
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}


def _ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS provisional_pnl_snapshots(
                import_id INTEGER PRIMARY KEY,
                file_name TEXT,
                period_start TEXT,
                period_end TEXT,
                captured_at TEXT NOT NULL,
                rows_json TEXT NOT NULL,
                totals_json TEXT NOT NULL
            )"""
        )


def _is_provisional_table(df: Any) -> bool:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return False
    cols = set(map(str, df.columns))
    return {"상품명", "판매수량", "예상매출", "예상이익"}.issubset(cols) and bool(
        {"옵션ID", "쿠팡 옵션ID"} & cols
    )


def _numeric_col(df: pd.DataFrame, col: str) -> pd.Series:
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


def _clean_provisional(df: pd.DataFrame) -> pd.DataFrame:
    qty = _numeric_col(df, "판매수량")
    return df.loc[qty.abs() > 1e-12].copy()


def _signature_from_df(df: pd.DataFrame) -> dict[str, float]:
    oid_col = "옵션ID" if "옵션ID" in df.columns else "쿠팡 옵션ID"
    out: dict[str, float] = {}
    qty = _numeric_col(df, "판매수량")
    for idx, oidv in df[oid_col].items():
        oid = _oid(oidv)
        if not oid:
            continue
        q = float(qty.loc[idx])
        if abs(q) <= 1e-12:
            continue
        out[oid] = out.get(oid, 0.0) + q
    return out


def _signature_for_import(core, c, import_id: int) -> dict[str, float]:
    if not _exists(c, "sales_stats") or not _exists(c, "products"):
        return {}
    sc = _cols(c, "sales_stats")
    if not {"product_id", "net_qty", "import_id"}.issubset(sc):
        return {}
    rows = c.execute(
        """SELECT p.option_id,p.item_code,SUM(COALESCE(s.net_qty,0)) qty
           FROM sales_stats s
           JOIN products p ON p.id=s.product_id
           WHERE s.import_id=?
           GROUP BY s.product_id,p.option_id,p.item_code""",
        (int(import_id),),
    ).fetchall()
    out: dict[str, float] = {}
    for r in rows:
        oid = _oid(r["option_id"]) or _oid(r["item_code"])
        q = _num(r["qty"])
        if oid and abs(q) > 1e-12:
            out[oid] = out.get(oid, 0.0) + q
    return out


def _sig_equal(a: dict[str, float], b: dict[str, float]) -> bool:
    if set(a) != set(b):
        return False
    return all(abs(a[k] - b[k]) <= 1e-9 for k in a)


def _identify_sales_import(core, db, df: pd.DataFrame):
    signature = _signature_from_df(df)
    if not signature:
        return None
    with core._conn(db) as c:
        if not _exists(c, "imports"):
            return None
        ic = _cols(c, "imports")
        need = {"id", "data_type", "period_start", "period_end"}
        if not need.issubset(ic):
            return None
        file_expr = "file_name" if "file_name" in ic else "''"
        imports = c.execute(
            f"""SELECT id,{file_expr} file_name,period_start,period_end
                FROM imports WHERE data_type='sales_stats' ORDER BY id DESC"""
        ).fetchall()
        for imp in imports:
            sig = _signature_for_import(core, c, int(imp["id"]))
            if _sig_equal(signature, sig):
                return {
                    "id": int(imp["id"]),
                    "file_name": str(imp["file_name"] or ""),
                    "period_start": str(imp["period_start"] or ""),
                    "period_end": str(imp["period_end"] or ""),
                }
    return None


_SNAPSHOT_COLS = [
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
]


def _snapshot_rows(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    oid_col = "옵션ID" if "옵션ID" in df.columns else "쿠팡 옵션ID"
    numeric = set(_SNAPSHOT_COLS) - {"옵션ID", "상품명"}
    for idx, r in df.iterrows():
        item = {
            "옵션ID": _oid(r.get(oid_col)),
            "상품명": str(r.get("상품명") or ""),
        }
        for col in numeric:
            if col in df.columns:
                item[col] = _num(r.get(col))
        rows.append(item)
    return rows


def _provisional_totals(rows: list[dict[str, Any]]) -> dict[str, float]:
    def s(col: str, absolute: bool = False):
        vals = [_num(r.get(col)) for r in rows]
        return float(sum(abs(v) for v in vals)) if absolute else float(sum(vals))

    revenue = s("예상매출")
    cogs = s("매출원가", True)
    commission = s("판매수수료", True)
    inout = s("입출고비", True)
    delivery = s("배송비", True)
    returns = s("반품충당", True)
    ad = s("광고비", True)
    profit = s("예상이익")
    return {
        "revenue": revenue,
        "cogs": cogs,
        "commission": commission,
        "inout": inout,
        "delivery": delivery,
        "returns": returns,
        "ad": ad,
        "profit": profit,
    }


def _save_snapshot(core, db, df: pd.DataFrame):
    cleaned = _clean_provisional(df)
    imp = _identify_sales_import(core, db, cleaned)
    if not imp:
        return
    rows = _snapshot_rows(cleaned)
    totals = _provisional_totals(rows)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with core._conn(db) as c:
        c.execute(
            """INSERT INTO provisional_pnl_snapshots
               (import_id,file_name,period_start,period_end,captured_at,rows_json,totals_json)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(import_id) DO UPDATE SET
                 file_name=excluded.file_name,
                 period_start=excluded.period_start,
                 period_end=excluded.period_end,
                 captured_at=excluded.captured_at,
                 rows_json=excluded.rows_json,
                 totals_json=excluded.totals_json""",
            (
                imp["id"],
                imp["file_name"],
                imp["period_start"],
                imp["period_end"],
                now,
                json.dumps(rows, ensure_ascii=False),
                json.dumps(totals, ensure_ascii=False),
            ),
        )


def _clean_stale(core, db):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        if _exists(c, "imports"):
            c.execute(
                """DELETE FROM provisional_pnl_snapshots
                   WHERE import_id NOT IN
                     (SELECT id FROM imports WHERE data_type='sales_stats')"""
            )


def _month_bounds(month: str):
    y, m = [int(x) for x in month.split("-")]
    last = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, last)


def _snapshot_records(core, db, month: str):
    _clean_stale(core, db)
    start, end = _month_bounds(month)
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT * FROM provisional_pnl_snapshots
               WHERE period_end>=? AND period_start<=?
               ORDER BY period_start,period_end,import_id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    out = []
    for r in rows:
        try:
            data_rows = json.loads(str(r["rows_json"] or "[]"))
            totals = json.loads(str(r["totals_json"] or "{}"))
        except Exception:
            continue
        out.append(
            {
                "import_id": int(r["import_id"]),
                "file_name": str(r["file_name"] or ""),
                "period_start": str(r["period_start"] or ""),
                "period_end": str(r["period_end"] or ""),
                "captured_at": str(r["captured_at"] or ""),
                "rows": data_rows,
                "totals": totals,
            }
        )
    return out


def _coverage(month: str, snapshots):
    start, end = _month_bounds(month)
    all_days = []
    fully_inside = True
    for s in snapshots:
        try:
            a = date.fromisoformat(s["period_start"])
            b = date.fromisoformat(s["period_end"])
        except Exception:
            fully_inside = False
            continue
        if a < start or b > end:
            fully_inside = False
        a, b = max(a, start), min(b, end)
        d = a
        while d <= b:
            all_days.append(d)
            d += timedelta(days=1)
    counts = {}
    for d in all_days:
        counts[d] = counts.get(d, 0) + 1
    expected = (end - start).days + 1
    covered = sum(1 for d in (start + timedelta(days=i) for i in range(expected)) if d in counts)
    overlaps = sum(1 for n in counts.values() if n > 1)
    exact = fully_inside and covered == expected and overlaps == 0
    return {"exact": exact, "covered": covered, "expected": expected, "overlap_days": overlaps}


def _actual_totals(mdf: pd.DataFrame, meta: dict) -> dict[str, float]:
    def total(col):
        if col not in mdf.columns:
            return 0.0
        return float(pd.to_numeric(mdf[col], errors="coerce").fillna(0).abs().sum())

    revenue = (
        float(pd.to_numeric(mdf["realized_sales"], errors="coerce").fillna(0).sum())
        if "realized_sales" in mdf.columns
        else 0.0
    )
    cogs = total("cogs")
    commission = total("commission")
    inout = total("inout")
    delivery = total("delivery")
    returns = total("return_pickup") + total("return_restock")
    ad = abs(_num((meta or {}).get("ad_billable_total", 0)))
    profit = _num((meta or {}).get("overall_profit"))
    if abs(profit) <= 1e-12:
        profit = revenue - cogs - commission - inout - delivery - returns - ad
    return {
        "revenue": revenue,
        "cogs": cogs,
        "commission": commission,
        "inout": inout,
        "delivery": delivery,
        "returns": returns,
        "ad": ad,
        "profit": profit,
    }


def _aggregate_snapshots(snapshots):
    rows = []
    for s in snapshots:
        rows.extend(s["rows"])
    return rows, _provisional_totals(rows)


def _product_master(core, db):
    with core._conn(db) as c:
        rows = c.execute("SELECT id,option_id,item_code,name FROM products").fetchall()
        aliases = {}
        if _exists(c, "return_discount_aliases"):
            for r in c.execute(
                """SELECT a.discount_option_id,p.option_id,p.item_code
                   FROM return_discount_aliases a
                   JOIN products p ON p.id=a.parent_product_id"""
            ):
                aliases[_oid(r["discount_option_id"])] = _oid(r["option_id"]) or _oid(r["item_code"])
    by_id = {
        int(r["id"]): {
            "oid": _oid(r["option_id"]) or _oid(r["item_code"]),
            "name": str(r["name"] or ""),
        }
        for r in rows
    }
    return by_id, aliases


def _provisional_by_product(rows, aliases):
    out = {}
    for r in rows:
        oid = _oid(r.get("옵션ID"))
        oid = aliases.get(oid, oid)
        if not oid:
            continue
        x = out.setdefault(
            oid,
            {
                "옵션ID": oid,
                "상품명": str(r.get("상품명") or "").replace(" [반품 할인판매]", ""),
                "잠정판매수량": 0.0,
                "잠정매출": 0.0,
                "잠정원가": 0.0,
                "잠정수수료": 0.0,
                "잠정물류반품비": 0.0,
                "잠정광고비": 0.0,
                "잠정이익": 0.0,
            },
        )
        x["잠정판매수량"] += _num(r.get("판매수량"))
        x["잠정매출"] += _num(r.get("예상매출"))
        x["잠정원가"] += abs(_num(r.get("매출원가")))
        x["잠정수수료"] += abs(_num(r.get("판매수수료")))
        x["잠정물류반품비"] += (
            abs(_num(r.get("입출고비")))
            + abs(_num(r.get("배송비")))
            + abs(_num(r.get("반품충당")))
        )
        x["잠정광고비"] += abs(_num(r.get("광고비")))
        x["잠정이익"] += _num(r.get("예상이익"))
    return out


def _actual_by_product(core, db, mdf):
    by_id, aliases = _product_master(core, db)
    out = {}
    for _, r in mdf.iterrows():
        pid = int(_num(r.get("product_id"))) if "product_id" in mdf.columns else 0
        p = by_id.get(pid, {})
        oid = ""
        for col in ("option_id", "옵션ID", "쿠팡 옵션ID"):
            if col in mdf.columns:
                oid = _oid(r.get(col))
                if oid:
                    break
        oid = aliases.get(oid, oid) or p.get("oid", "")
        if not oid:
            continue
        x = out.setdefault(
            oid,
            {
                "옵션ID": oid,
                "상품명": p.get("name", ""),
                "확정매출": 0.0,
                "확정원가": 0.0,
                "확정수수료": 0.0,
                "확정물류반품비": 0.0,
                "확정광고비": 0.0,
                "확정광고전이익": 0.0,
            },
        )
        rev = _num(r.get("realized_sales"))
        cost = abs(_num(r.get("cogs")))
        comm = abs(_num(r.get("commission")))
        rg = (
            abs(_num(r.get("inout")))
            + abs(_num(r.get("delivery")))
            + abs(_num(r.get("return_pickup")))
            + abs(_num(r.get("return_restock")))
        )
        ad = abs(_num(r.get("ad_cost"))) if "ad_cost" in mdf.columns else 0.0
        x["확정매출"] += rev
        x["확정원가"] += cost
        x["확정수수료"] += comm
        x["확정물류반품비"] += rg
        x["확정광고비"] += ad
        x["확정광고전이익"] += rev - cost - comm - rg
    return out


def _search_filter(df: pd.DataFrame, q: str):
    q = str(q or "").strip().lower()
    if not q or df.empty:
        return df
    words = [x for x in q.split() if x]
    hay = df.fillna("").astype(str).agg(" ".join, axis=1).str.lower()
    mask = pd.Series(True, index=df.index)
    for word in words:
        mask &= hay.str.contains(re.escape(word), regex=True)
    return df.loc[mask].copy()


def render_confirmed_page(st_obj, pd_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    st_obj.markdown("## ✅ 확정손익")
    st_obj.caption("쿠팡 월 정산자료를 기준으로 쿠폰·실제 수수료·실제 RG비용·월 광고 청구액을 반영한 확정 손익입니다.")
    try:
        months = list(core.monthly_available() or [])
    except Exception:
        months = []
    if not months:
        st_obj.info("월 정산자료를 업로드하면 확정손익을 확인할 수 있습니다.")
        return
    month = st_obj.selectbox("확정 월", months, key="confirmed_pnl_month_v0912")
    mdf, meta = core.confirmed_monthly_pnl(month)
    if mdf is None or mdf.empty:
        st_obj.info(f"{month} 확정손익 데이터가 없습니다.")
        return
    totals = _actual_totals(mdf, meta or {})
    margin = totals["profit"] / totals["revenue"] * 100 if totals["revenue"] else 0.0
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("확정 실현매출", _fmt_money(totals["revenue"]))
    c2.metric("확정 최종이익", _fmt_money(totals["profit"]))
    c3.metric("확정 이익률", _fmt_pct(margin))
    c4.metric("확정 광고비", _fmt_money(totals["ad"]))
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("상품원가", _fmt_money(totals["cogs"]))
    c2.metric("판매수수료", _fmt_money(totals["commission"]))
    c3.metric("입출고·배송비", _fmt_money(totals["inout"] + totals["delivery"]))
    c4.metric("반품비", _fmt_money(totals["returns"]))

    by_id, _ = _product_master(core, db)
    rows = []
    for _, r in mdf.iterrows():
        pid = int(_num(r.get("product_id"))) if "product_id" in mdf.columns else 0
        p = by_id.get(pid, {})
        oid = p.get("oid", "")
        name = p.get("name", "")
        if not oid:
            for col in ("option_id", "옵션ID"):
                if col in mdf.columns:
                    oid = _oid(r.get(col))
                    if oid:
                        break
        rev = _num(r.get("realized_sales"))
        cogs = abs(_num(r.get("cogs")))
        comm = abs(_num(r.get("commission")))
        inout = abs(_num(r.get("inout")))
        delivery = abs(_num(r.get("delivery")))
        ret = abs(_num(r.get("return_pickup"))) + abs(_num(r.get("return_restock")))
        rows.append(
            {
                "옵션ID": oid,
                "상품명": name,
                "실현매출": rev,
                "매출원가": cogs,
                "판매수수료": comm,
                "입출고비": inout,
                "배송비": delivery,
                "반품비": ret,
                "광고전이익": rev - cogs - comm - inout - delivery - ret,
            }
        )
    view = pd_obj.DataFrame(rows)
    q = st_obj.text_input(
        "상품 검색", placeholder="상품명 또는 옵션ID 입력", key="confirmed_pnl_search_v0912"
    )
    view = _search_filter(view, q)
    money_cols = ["실현매출", "매출원가", "판매수수료", "입출고비", "배송비", "반품비", "광고전이익"]
    show = view.copy()
    for col in money_cols:
        if col in show.columns:
            show[col] = show[col].map(_fmt_money)
    st_obj.dataframe(show, use_container_width=True, hide_index=True, height=min(700, max(220, 38 * (len(show) + 1))))
    st_obj.caption("상품별 광고비가 월 정산서에서 직접 귀속되지 않는 경우 상품표는 광고전이익으로 표시하고, 최종 광고비와 최종이익은 상단 월 합계에 반영합니다.")


def render_variance_page(st_obj, pd_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    st_obj.markdown("## 🔍 잠정↔확정 차이분석")
    st_obj.caption("월말 확정자료가 들어온 뒤, 실제로 그 전에 보았던 잠정손익 스냅샷과 비교해 차이가 난 원인을 분해합니다.")
    try:
        months = list(core.monthly_available() or [])
    except Exception:
        months = []
    if not months:
        st_obj.info("확정 월 정산자료가 아직 없습니다.")
        return
    month = st_obj.selectbox("분석 월", months, key="variance_month_v0912")
    snapshots = _snapshot_records(core, db, month)
    if not snapshots:
        st_obj.warning(
            "이 월의 잠정손익 스냅샷이 없습니다. 앞으로는 잠정손익 화면을 볼 때 자동 저장됩니다. "
            "과거 자료는 잠정손익 메뉴에서 해당 판매자료를 한 번씩 열면 스냅샷이 생성됩니다."
        )
        return

    cov = _coverage(month, snapshots)
    c1, c2, c3 = st_obj.columns(3)
    c1.metric("잠정자료 기간", f"{len(snapshots):,}개")
    c2.metric("월 커버리지", f"{cov['covered']}/{cov['expected']}일")
    c3.metric("중복 기간", f"{cov['overlap_days']:,}일")

    with st_obj.expander("사용된 잠정자료 보기"):
        period_rows = [
            {
                "기간": f"{s['period_start']} ~ {s['period_end']}",
                "파일": s["file_name"],
                "잠정치 저장시각": s["captured_at"],
            }
            for s in snapshots
        ]
        st_obj.dataframe(pd_obj.DataFrame(period_rows), use_container_width=True, hide_index=True)

    if not cov["exact"]:
        st_obj.warning(
            "현재 저장된 잠정자료가 해당 월 전체를 겹침 없이 정확히 덮지 않아 "
            "월 확정손익과 총액을 직접 비교하면 왜곡됩니다. 총액 차이 계산은 보류합니다. "
            "월 전체 판매자료가 잠정손익에 저장되면 자동으로 비교가 활성화됩니다."
        )
        return

    mdf, meta = core.confirmed_monthly_pnl(month)
    if mdf is None or mdf.empty:
        st_obj.info(f"{month} 확정손익 데이터가 없습니다.")
        return
    prov_rows, provisional = _aggregate_snapshots(snapshots)
    actual = _actual_totals(mdf, meta or {})

    diff_profit = actual["profit"] - provisional["profit"]
    c1, c2, c3, c4 = st_obj.columns(4)
    c1.metric("잠정이익", _fmt_money(provisional["profit"]))
    c2.metric("확정이익", _fmt_money(actual["profit"]))
    c3.metric("이익 차이", _fmt_money(diff_profit))
    base = abs(provisional["profit"])
    c4.metric("이익 오차율", _fmt_pct(diff_profit / base * 100 if base else 0))

    items = [
        ("매출", "revenue", 1),
        ("상품원가", "cogs", -1),
        ("판매수수료", "commission", -1),
        ("입출고비", "inout", -1),
        ("배송비", "delivery", -1),
        ("반품비", "returns", -1),
        ("광고비", "ad", -1),
    ]
    rows = []
    for label, key, direction in items:
        p, a = provisional[key], actual[key]
        d = a - p
        rows.append(
            {
                "항목": label,
                "잠정": p,
                "확정": a,
                "차이": d,
                "차이율(%)": (d / abs(p) * 100) if abs(p) > 1e-12 else 0.0,
                "이익영향": direction * d,
            }
        )
    comp = pd_obj.DataFrame(rows)
    show = comp.copy()
    for col in ("잠정", "확정", "차이", "이익영향"):
        show[col] = show[col].map(_fmt_money)
    show["차이율(%)"] = show["차이율(%)"].map(_fmt_pct)
    st_obj.markdown("### 차이 원인")
    st_obj.dataframe(show, use_container_width=True, hide_index=True)

    driver = comp[["항목", "이익영향"]].copy().set_index("항목")
    st_obj.bar_chart(driver, height=300)
    ranked = comp.reindex(comp["이익영향"].abs().sort_values(ascending=False).index)
    if not ranked.empty:
        top = ranked.iloc[0]
        sign = "개선" if _num(top["이익영향"]) > 0 else "악화"
        st_obj.info(
            f"가장 큰 차이 요인은 **{top['항목']}**이며 확정손익을 "
            f"{_fmt_money(abs(_num(top['이익영향'])))} {sign}시키는 방향입니다."
        )

    by_id, aliases = _product_master(core, db)
    prov = _provisional_by_product(prov_rows, aliases)
    act = _actual_by_product(core, db, mdf)
    keys = sorted(set(prov) | set(act))
    prod_rows = []
    for oid in keys:
        p = prov.get(oid, {})
        a = act.get(oid, {})
        pre = _num(p.get("잠정매출"))
        arev = _num(a.get("확정매출"))
        pc = _num(p.get("잠정수수료"))
        ac = _num(a.get("확정수수료"))
        prg = _num(p.get("잠정물류반품비"))
        arg = _num(a.get("확정물류반품비"))
        pp = (
            _num(p.get("잠정매출"))
            - _num(p.get("잠정원가"))
            - _num(p.get("잠정수수료"))
            - _num(p.get("잠정물류반품비"))
        )
        ap = _num(a.get("확정광고전이익"))
        prod_rows.append(
            {
                "옵션ID": oid,
                "상품명": p.get("상품명") or a.get("상품명") or "",
                "잠정매출": pre,
                "확정매출": arev,
                "매출차이": arev - pre,
                "수수료차이": ac - pc,
                "물류·반품비차이": arg - prg,
                "광고전이익차이": ap - pp,
            }
        )
    pdf = pd_obj.DataFrame(prod_rows)
    if not pdf.empty:
        pdf = pdf.reindex(pdf["광고전이익차이"].abs().sort_values(ascending=False).index)
        q = st_obj.text_input(
            "상품 검색", placeholder="상품명 또는 옵션ID 입력", key="variance_search_v0912"
        )
        pdf = _search_filter(pdf, q)
        showp = pdf.copy()
        for col in ("잠정매출", "확정매출", "매출차이", "수수료차이", "물류·반품비차이", "광고전이익차이"):
            showp[col] = showp[col].map(_fmt_money)
        st_obj.markdown("### 상품별 차이")
        st_obj.dataframe(showp, use_container_width=True, hide_index=True, height=min(700, max(220, 38 * (len(showp) + 1))))
        st_obj.caption("상품별 표의 이익차이는 광고비를 제외한 비교입니다. 월 광고비는 상품 귀속이 불명확할 수 있어 위 총액 차이분석에서 확정 청구액으로 반영합니다.")


def patch_source(source: str) -> str:
    if '"📈  판매·손익",' in source:
        source = source.replace(
            '"📈  판매·손익",',
            '"📈  잠정손익",\n        "✅  확정손익",\n        "🔍  손익차이분석",',
            1,
        )
    if 'elif page == "📈  판매·손익":' in source:
        source = source.replace(
            'elif page == "📈  판매·손익":',
            'elif page == "📈  잠정손익":',
            1,
        )
    source = source.replace('page_header("판매·손익"', 'page_header("잠정손익"', 1)

    marker = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
    if marker not in source:
        raise RuntimeError("v0.9.12 손익 메뉴를 추가할 위치를 찾지 못했습니다.")
    insert = (
        '# ------------------------------\n'
        '# Confirmed P&L\n'
        '# ------------------------------\n'
        'elif page == "✅  확정손익":\n'
        '    pnl_views_v0912.render_confirmed_page(st, pd, core)\n\n\n'
        '# ------------------------------\n'
        '# Provisional vs confirmed variance\n'
        '# ------------------------------\n'
        'elif page == "🔍  손익차이분석":\n'
        '    pnl_views_v0912.render_variance_page(st, pd, core)\n\n\n'
    )
    return source.replace(marker, insert + marker, 1)


def apply(core, db_path=None):
    global _APPLIED
    if _APPLIED or getattr(st, "_rg_pnl_views_v0912", False):
        return
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    _clean_stale(core, db)

    previous_dataframe = st.dataframe

    def dataframe(data=None, *args, **kwargs):
        if _is_provisional_table(data):
            try:
                _save_snapshot(core, db, data)
            except Exception:
                pass
        return previous_dataframe(data, *args, **kwargs)

    st.dataframe = dataframe
    st._rg_pnl_views_v0912 = True
    _APPLIED = True
