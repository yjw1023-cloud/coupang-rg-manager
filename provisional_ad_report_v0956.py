"""RG Manager v0.9.56 provisional advertising report upload.

Replaces manual monthly ad-spend allocation with Coupang advertising performance
reports. Advertising spend is attributed by `광고집행 옵션ID`, never by sales ratio.
"""
from __future__ import annotations

import hashlib
import io
import re
from datetime import date
from typing import Any

import pandas as pd

REQUIRED_COLUMNS = {"광고집행 옵션ID", "광고비"}
OPTION_NAME_COLUMN = "광고집행 상품명"


def _num(v: Any) -> float:
    try:
        if pd.isna(v):
            return 0.0
    except Exception:
        pass
    try:
        if isinstance(v, str):
            v = v.replace(",", "").replace("원", "").strip()
        return float(v or 0)
    except Exception:
        return 0.0


def _oid(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    try:
        x = float(v)
        if abs(x - round(x)) < 1e-9:
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
            """CREATE TABLE IF NOT EXISTS provisional_ad_report_imports(
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   file_name TEXT NOT NULL,
                   file_hash TEXT NOT NULL UNIQUE,
                   period_start TEXT NOT NULL,
                   period_end TEXT NOT NULL,
                   total_ad_spend REAL NOT NULL,
                   imported_at TEXT NOT NULL
               )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS provisional_ad_report_items(
                   import_id INTEGER NOT NULL,
                   option_id TEXT NOT NULL,
                   product_name TEXT,
                   ad_spend REAL NOT NULL,
                   PRIMARY KEY(import_id, option_id)
               )"""
        )


def _period_from_filename(name: str):
    dates = re.findall(r"(?<!\d)(20\d{6})(?!\d)", str(name or ""))
    if len(dates) < 2:
        return None
    try:
        a = date(int(dates[0][:4]), int(dates[0][4:6]), int(dates[0][6:8]))
        b = date(int(dates[1][:4]), int(dates[1][4:6]), int(dates[1][6:8]))
    except Exception:
        return None
    return (a, b) if a <= b else (b, a)


def _parse_excel(raw: bytes):
    df = pd.read_excel(io.BytesIO(raw))
    cols = {str(c).strip(): c for c in df.columns}
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    if missing:
        raise ValueError("쿠팡 광고성과보고서가 아닙니다. 필요한 열: " + ", ".join(missing))

    oid_col = cols["광고집행 옵션ID"]
    spend_col = cols["광고비"]
    name_col = cols.get(OPTION_NAME_COLUMN)

    work = pd.DataFrame()
    work["option_id"] = df[oid_col].map(_oid)
    work["ad_spend"] = df[spend_col].map(_num).clip(lower=0)
    if name_col is not None:
        work["product_name"] = df[name_col].fillna("").astype(str).str.strip()
    else:
        work["product_name"] = ""

    work = work[work["option_id"].astype(bool)].copy()
    if work.empty:
        raise ValueError("광고집행 옵션ID가 있는 행을 찾지 못했습니다.")

    grouped = (
        work.groupby("option_id", as_index=False)
        .agg(ad_spend=("ad_spend", "sum"), product_name=("product_name", "first"))
    )
    grouped = grouped[grouped["ad_spend"] > 0].copy()
    return grouped, float(grouped["ad_spend"].sum())


def _overlaps(core, db, start: date, end: date):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute(
            """SELECT id,file_name,period_start,period_end,total_ad_spend
               FROM provisional_ad_report_imports
               WHERE period_end>=? AND period_start<=?
               ORDER BY period_start,period_end,id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
    return [dict(r) for r in rows]


def _save(core, db, file_name: str, raw: bytes, start: date, end: date, grouped, replace_overlap=False):
    _ensure_schema(core, db)
    if start > end:
        raise ValueError("광고자료 시작일은 종료일보다 늦을 수 없습니다.")
    if start.strftime("%Y-%m") != end.strftime("%Y-%m"):
        raise ValueError("광고성과보고서는 한 달 안의 기간으로 나눠 업로드해 주세요.")
    digest = hashlib.sha256(raw).hexdigest()
    overlaps = _overlaps(core, db, start, end)
    with core._conn(db) as c:
        dup = c.execute(
            "SELECT id FROM provisional_ad_report_imports WHERE file_hash=?", (digest,)
        ).fetchone()
        if dup:
            raise ValueError("이미 업로드한 동일한 광고성과보고서입니다.")
        if overlaps and not replace_overlap:
            raise ValueError("기존 광고자료와 기간이 겹칩니다. '겹치는 기존 광고자료 교체'를 체크해 주세요.")

        if replace_overlap:
            for r in overlaps:
                c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (int(r["id"]),))
                c.execute("DELETE FROM provisional_ad_report_imports WHERE id=?", (int(r["id"]),))

        total = float(grouped["ad_spend"].sum())
        cur = c.execute(
            """INSERT INTO provisional_ad_report_imports
               (file_name,file_hash,period_start,period_end,total_ad_spend,imported_at)
               VALUES(?,?,?,?,?,?)""",
            (str(file_name), digest, start.isoformat(), end.isoformat(), total, core.now_iso()),
        )
        import_id = int(cur.lastrowid)
        c.executemany(
            """INSERT INTO provisional_ad_report_items(import_id,option_id,product_name,ad_spend)
               VALUES(?,?,?,?)""",
            [
                (import_id, str(r.option_id), str(r.product_name or ""), float(r.ad_spend))
                for r in grouped.itertuples(index=False)
            ],
        )
    return {"import_id": import_id, "total": total, "options": len(grouped)}


def load_month(core, month: str, db_path=None):
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    y, m = [int(x) for x in str(month).split("-")]
    start = date(y, m, 1)
    if m == 12:
        next_month = date(y + 1, 1, 1)
    else:
        next_month = date(y, m + 1, 1)
    end = date.fromordinal(next_month.toordinal() - 1)
    with core._conn(db) as c:
        imports = c.execute(
            """SELECT id,file_name,period_start,period_end,total_ad_spend,imported_at
               FROM provisional_ad_report_imports
               WHERE period_end>=? AND period_start<=?
               ORDER BY period_start,period_end,id""",
            (start.isoformat(), end.isoformat()),
        ).fetchall()
        if not imports:
            return {"imports": [], "items": {}, "total": 0.0}
        ids = [int(r["id"]) for r in imports]
        q = ",".join("?" for _ in ids)
        items = c.execute(
            f"""SELECT option_id,MAX(product_name) product_name,SUM(ad_spend) ad_spend
                FROM provisional_ad_report_items
                WHERE import_id IN ({q})
                GROUP BY option_id""",
            ids,
        ).fetchall()
    return {
        "imports": [dict(r) for r in imports],
        "items": {
            str(r["option_id"]): {
                "option_id": str(r["option_id"]),
                "product_name": str(r["product_name"] or ""),
                "ad_spend": float(r["ad_spend"] or 0),
            }
            for r in items
        },
        "total": float(sum(float(r["ad_spend"] or 0) for r in items)),
    }


def _delete_import(core, db, import_id: int):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        c.execute("DELETE FROM provisional_ad_report_items WHERE import_id=?", (int(import_id),))
        c.execute("DELETE FROM provisional_ad_report_imports WHERE id=?", (int(import_id),))


def render_input(st, core, month: str, db_path=None):
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)

    box = st.container(border=True)
    with box:
        st.markdown("### 광고성과보고서 업로드")
        st.caption(
            "쿠팡 광고성과보고서의 `광고집행 옵션ID`별 광고비를 합산해 해당 상품 잠정손익에 직접 반영합니다. "
            "매출액 비율 배분은 사용하지 않습니다."
        )
        uploaded = st.file_uploader(
            "광고성과보고서 Excel",
            type=["xlsx", "xls"],
            key=f"provisional_ad_report_upload_{month}",
        )
        if uploaded is not None:
            raw = uploaded.getvalue()
            try:
                grouped, total = _parse_excel(raw)
                parsed = _period_from_filename(uploaded.name)
                if parsed:
                    start, end = parsed
                    st.info(
                        f"파일 기간: {start.isoformat()} ~ {end.isoformat()} · "
                        f"옵션 {len(grouped):,}개 · 광고비 {int(round(total)):,}원"
                    )
                else:
                    y, m = [int(x) for x in month.split("-")]
                    default_start = date(y, m, 1)
                    default_end = date.today() if date.today().strftime("%Y-%m") == month else default_start
                    c1, c2 = st.columns(2)
                    start = c1.date_input("광고자료 시작일", value=default_start, key=f"ad_report_start_{month}")
                    end = c2.date_input("광고자료 종료일", value=default_end, key=f"ad_report_end_{month}")
                    st.info(f"옵션 {len(grouped):,}개 · 광고비 {int(round(total)):,}원")

                if start.strftime("%Y-%m") != month or end.strftime("%Y-%m") != month:
                    st.error(f"선택한 조회 월({month})과 파일 기간이 다릅니다. 해당 월을 선택한 뒤 업로드해 주세요.")
                    overlaps = []
                    period_ok = False
                else:
                    overlaps = _overlaps(core, db, start, end)
                    period_ok = True
                replace = False
                if overlaps:
                    names = ", ".join(
                        f"{r['period_start']}~{r['period_end']} {r['file_name']}" for r in overlaps[:3]
                    )
                    st.warning("기존 광고자료와 기간이 겹칩니다: " + names)
                    replace = st.checkbox(
                        "겹치는 기존 광고자료를 삭제하고 이 파일로 교체",
                        key=f"ad_report_replace_{month}",
                    )

                if st.button(
                    "광고성과보고서 저장",
                    type="primary",
                    key=f"ad_report_save_{month}",
                    disabled=not period_ok,
                ):
                    try:
                        result = _save(core, db, uploaded.name, raw, start, end, grouped, replace)
                        st.success(
                            f"광고자료를 저장했습니다. 옵션 {result['options']:,}개 · "
                            f"광고비 {int(round(result['total'])):,}원"
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
            except Exception as exc:
                st.error(str(exc))

        current = load_month(core, month, db)
        if current["imports"]:
            st.markdown("**현재 이 달에 반영되는 광고자료**")
            for r in current["imports"]:
                c1, c2 = st.columns([5, 1])
                c1.caption(
                    f"{r['period_start']} ~ {r['period_end']} · {r['file_name']} · "
                    f"{int(round(float(r['total_ad_spend']))):,}원"
                )
                if c2.button("삭제", key=f"delete_ad_report_{r['id']}"):
                    _delete_import(core, db, int(r["id"]))
                    st.success("광고자료를 삭제했습니다.")
                    st.rerun()
        else:
            st.caption("현재 선택 월에 업로드된 광고성과보고서가 없습니다.")

    return load_month(core, month, db)


def apply_to_view(view: pd.DataFrame, dataset: dict):
    if view is None:
        view = pd.DataFrame()
    out = view.copy()
    items = dict((dataset or {}).get("items") or {})

    if out.empty and not items:
        return out, {"matched": 0, "unmatched": 0, "total": 0.0}

    if "광고비" not in out.columns:
        out["광고비"] = 0.0
    else:
        out["광고비"] = 0.0

    matched_ids = set()
    for idx in out.index:
        oid = _oid(out.at[idx, "옵션ID"] if "옵션ID" in out.columns else "")
        item = items.get(oid)
        ad = float(item["ad_spend"]) if item else 0.0
        if item:
            matched_ids.add(oid)
        out.at[idx, "광고비"] = -abs(ad)
        no_ad = _num(out.at[idx, "광고제외이익"]) if "광고제외이익" in out.columns else 0.0
        revenue = _num(out.at[idx, "예상매출"]) if "예상매출" in out.columns else 0.0
        profit = no_ad - abs(ad)
        if "예상이익" in out.columns:
            out.at[idx, "예상이익"] = profit
        if "이익률(%)" in out.columns:
            out.at[idx, "이익률(%)"] = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0

    extra = []
    cols = list(out.columns)
    for oid, item in items.items():
        if oid in matched_ids or _num(item.get("ad_spend")) <= 0:
            continue
        row = {c: 0.0 for c in cols}
        if "옵션ID" in row:
            row["옵션ID"] = oid
        if "상품명" in row:
            row["상품명"] = item.get("product_name") or f"광고집행 옵션 {oid}"
        ad = abs(_num(item.get("ad_spend")))
        if "광고비" in row:
            row["광고비"] = -ad
        if "예상이익" in row:
            row["예상이익"] = -ad
        if "광고제외이익" in row:
            row["광고제외이익"] = 0.0
        extra.append(row)

    if extra:
        out = pd.concat([out, pd.DataFrame(extra)], ignore_index=True)

    return out, {
        "matched": len(matched_ids),
        "unmatched": len(extra),
        "total": float((dataset or {}).get("total") or 0),
        "imports": len((dataset or {}).get("imports") or []),
    }


def render_applied_notice(st, meta: dict, dataset: dict):
    imports = (dataset or {}).get("imports") or []
    if not imports:
        st.warning("광고성과보고서가 없어 현재 잠정손익 광고비는 0원으로 표시됩니다.")
        return
    periods = ", ".join(f"{r['period_start']}~{r['period_end']}" for r in imports)
    st.caption(
        f"광고성과보고서 반영: {periods} · 총 {int(round(float(meta.get('total') or 0))):,}원 · "
        f"판매상품 직접매칭 {int(meta.get('matched') or 0):,}개"
        + (f" · 판매 0/미매칭 광고옵션 {int(meta.get('unmatched') or 0):,}개" if meta.get("unmatched") else "")
    )
