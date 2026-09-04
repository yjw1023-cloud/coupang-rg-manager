"""v0.9.153 robust sales-stat quantity parsing and validation.

Fixes a bad interpretation where net sales quantity could be reused as cancel quantity,
producing impossible values such as sales 104 / cancel 52 / net 52 for a source row
that is actually sales 55 / cancel 3 / net 52.

Rules:
- identify option/gross/cancel/net quantity columns from the actual workbook headers;
- support one-row or two-row header layouts;
- require three quantity columns to be distinct;
- require gross - cancel == net for the source rows before anything is written;
- overwrite only sales_qty/cancel_qty on the existing sales_stats import;
- never change net_qty or inventory deductions in this patch.
"""
from __future__ import annotations

from io import BytesIO
import math
import re
from typing import Any

import pandas as pd


def _num(v: Any):
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        s = str(v).strip().replace(",", "")
        if not s:
            return None
        return float(s)
    except Exception:
        return None


def _norm(v: Any) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", "", str(v or "").lower())


def _header_score(headers) -> int:
    ns = [_norm(x) for x in headers]
    score = 0
    if any("옵션id" in n or "vendoritemid" in n for n in ns):
        score += 20
    if any(("순판매" in n or n in {"netqty", "netsalesqty"}) and ("상품수" in n or "수량" in n or n in {"netqty", "netsalesqty"}) for n in ns):
        score += 8
    if any("판매" in n and "순판매" not in n and "취소" not in n and "반품" not in n and ("상품수" in n or "수량" in n) for n in ns):
        score += 8
    if any(("취소" in n or "반품" in n or "환불" in n) and ("상품수" in n or "수량" in n) for n in ns):
        score += 8
    return score


def _unique_headers(headers):
    used = {}
    out = []
    for i, value in enumerate(headers):
        name = str(value or "").strip()
        if not name or name.lower() == "nan":
            name = f"col_{i}"
        count = used.get(name, 0)
        used[name] = count + 1
        out.append(name if count == 0 else f"{name}__{count+1}")
    return out


def _read_sales_sheet(base, source):
    raw = base._source_bytes(source)
    if not raw:
        raise ValueError("판매통계 Excel 파일을 읽지 못했습니다.")
    xl = pd.ExcelFile(BytesIO(raw))
    sheet = "판매통계" if "판매통계" in xl.sheet_names else xl.sheet_names[0]
    raw_df = pd.read_excel(BytesIO(raw), sheet_name=sheet, header=None)
    if raw_df.empty:
        raise ValueError("판매통계 Excel에 데이터가 없습니다.")

    best = None
    limit = min(10, len(raw_df))
    for i in range(limit):
        row = ["" if pd.isna(x) else str(x).strip() for x in raw_df.iloc[i].tolist()]
        s1 = _header_score(row)
        candidate = (s1, i, 1, row)
        if best is None or candidate[0] > best[0]:
            best = candidate
        if i + 1 < len(raw_df):
            row2 = ["" if pd.isna(x) else str(x).strip() for x in raw_df.iloc[i + 1].tolist()]
            combined = []
            for a, b in zip(row, row2):
                if a and b and not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", b):
                    combined.append(f"{a} {b}")
                else:
                    combined.append(a or b)
            s2 = _header_score(combined)
            # Use two header rows only when it materially improves semantic recognition.
            if s2 >= s1 + 4 and (best is None or s2 > best[0]):
                best = (s2, i, 2, combined)

    if best is None or best[0] < 20:
        raise ValueError("판매통계 Excel에서 옵션ID 헤더를 찾지 못했습니다.")

    _score, header_row, header_count, headers = best
    df = raw_df.iloc[header_row + header_count :].copy()
    df.columns = _unique_headers(headers)
    df = df.dropna(how="all")
    return df, sheet


def _is_qty_name(n: str) -> bool:
    if any(x in n for x in ("금액", "amount", "매출", "rate", "비율", "퍼센트", "율")):
        return False
    return any(x in n for x in ("상품수", "수량", "개수", "qty", "count"))


def _role_score(name: str, role: str) -> int:
    n = _norm(name)
    if role == "oid":
        return 100 if n in {"옵션id", "vendoritemid"} else 80 if "옵션id" in n else 0
    if not _is_qty_name(n):
        return 0
    if role == "net":
        if "순판매" in n:
            return 100
        if n in {"netqty", "netsalesqty", "netsalesquantity"}:
            return 100
        return 0
    if role == "cancel":
        if any(x in n for x in ("취소", "반품", "환불")):
            return 100
        if n in {"cancelqty", "returnqty", "refundqty"}:
            return 100
        return 0
    if role == "gross":
        if "판매" in n and not any(x in n for x in ("순판매", "취소", "반품", "환불")):
            return 100
        if n in {"salesqty", "grossqty", "grosssalesqty", "soldqty"}:
            return 100
        return 0
    return 0


def _best_col(columns, role):
    scored = sorted(((_role_score(str(c), role), c) for c in columns), key=lambda x: x[0], reverse=True)
    return scored[0][1] if scored and scored[0][0] > 0 else None


def _numeric_series(df, col):
    return pd.to_numeric(
        df[col].astype(str).str.replace(",", "", regex=False).str.strip(),
        errors="coerce",
    )


def _relation_score(df, gcol, ccol, ncol):
    if len({gcol, ccol, ncol}) != 3:
        return 0.0, 0
    g = _numeric_series(df, gcol)
    c = _numeric_series(df, ccol).abs()
    n = _numeric_series(df, ncol)
    mask = g.notna() & c.notna() & n.notna()
    if int(mask.sum()) == 0:
        return 0.0, 0
    ok = ((g[mask] - c[mask] - n[mask]).abs() <= 1e-9) & (g[mask] >= -1e-9) & (c[mask] >= -1e-9)
    return float(ok.mean()), int(mask.sum())


def _choose_quantity_columns(df, oid_col):
    cols = list(df.columns)
    gross = _best_col(cols, "gross")
    cancel = _best_col(cols, "cancel")
    net = _best_col(cols, "net")

    if gross is not None and cancel is not None and net is not None:
        ratio, count = _relation_score(df, gross, cancel, net)
        if ratio >= 0.98 and count > 0:
            return gross, cancel, net, ratio

    qty_candidates = []
    for col in cols:
        if col == oid_col:
            continue
        n = _norm(col)
        if any(x in n for x in ("id", "금액", "amount", "비율", "rate", "퍼센트", "%", "율")):
            continue
        series = _numeric_series(df, col)
        if int(series.notna().sum()) > 0:
            qty_candidates.append(col)

    net_candidates = [c for c in qty_candidates if _role_score(str(c), "net") > 0] or qty_candidates
    best = None
    for ncol in net_candidates:
        for gcol in qty_candidates:
            if gcol == ncol:
                continue
            for ccol in qty_candidates:
                if ccol in {ncol, gcol}:
                    continue
                ratio, count = _relation_score(df, gcol, ccol, ncol)
                if count == 0:
                    continue
                semantic = (
                    _role_score(str(gcol), "gross")
                    + _role_score(str(ccol), "cancel")
                    + _role_score(str(ncol), "net")
                )
                score = ratio * 1000 + semantic + min(count, 100) / 1000
                if best is None or score > best[0]:
                    best = (score, ratio, gcol, ccol, ncol)

    if best is None or best[1] < 0.98:
        raise ValueError(
            "판매통계 Excel에서 판매수량/취소·반품수량/순판매수량 열을 안전하게 판별하지 못했습니다. "
            "수량은 '판매수량 - 취소·반품수량 = 순판매수량' 검증을 통과해야 합니다."
        )
    return best[2], best[3], best[4], best[1]


def parse_sales_quantities(base, source):
    df, sheet = _read_sales_sheet(base, source)
    oid_col = _best_col(list(df.columns), "oid")
    if oid_col is None:
        raise ValueError("판매통계 Excel에서 옵션ID 열을 찾지 못했습니다.")

    gross_col, cancel_col, net_col, ratio = _choose_quantity_columns(df, oid_col)
    if len({gross_col, cancel_col, net_col}) != 3:
        raise ValueError("판매/취소·반품/순판매 수량 열이 서로 다르지 않아 저장을 중단했습니다.")

    by_oid = {}
    invalid = []
    for _, r in df.iterrows():
        oid = base._oid(r.get(oid_col))
        if not oid or not str(oid).isdigit():
            continue
        gross = _num(r.get(gross_col))
        cancel = _num(r.get(cancel_col))
        net = _num(r.get(net_col))
        if gross is None and cancel is None and net is None:
            continue
        gross = float(gross or 0)
        cancel = abs(float(cancel or 0))
        net = float(net or 0)
        if abs((gross - cancel) - net) > 1e-9:
            invalid.append((oid, gross, cancel, net))
            continue
        target = by_oid.setdefault(
            str(oid),
            {"option_id": str(oid), "sales_qty": 0.0, "cancel_qty": 0.0, "net_qty": 0.0},
        )
        target["sales_qty"] += gross
        target["cancel_qty"] += cancel
        target["net_qty"] += net

    if invalid:
        sample = ", ".join(
            f"{oid}: 판매 {g:g}/취소·반품 {c:g}/순판매 {n:g}"
            for oid, g, c, n in invalid[:5]
        )
        raise ValueError(
            "판매통계 수량 검증에 실패해 저장하지 않았습니다. "
            "판매수량 - 취소·반품수량 = 순판매수량이 맞지 않습니다. " + sample
        )
    if not by_oid:
        raise ValueError("판매통계 Excel에서 유효한 옵션별 수량행을 찾지 못했습니다.")

    return list(by_oid.values()), {
        "available": True,
        "rows": len(by_oid),
        "gross_col": str(gross_col),
        "cancel_col": str(cancel_col),
        "net_col": str(net_col),
        "sheet": sheet,
        "relation_match": ratio,
    }


def enrich_import(base, core, db, import_id: int, parsed):
    base.ensure_schema(core, db)
    result = {"matched_options": 0, "unmatched_options": 0, "sales_qty": 0.0, "cancel_qty": 0.0}
    if not parsed:
        return result

    with core._conn(db) as c:
        pcols = base._cols(c, "products")
        if not {"id", "option_id"}.issubset(pcols):
            return result
        code_expr = "item_code" if "item_code" in pcols else "'' AS item_code"
        direct = {}
        for r in c.execute(f"SELECT id,option_id,{code_expr} FROM products"):
            for raw in (r["option_id"], r["item_code"]):
                key = base._oid(raw)
                if key:
                    direct.setdefault(key, int(r["id"]))
        aliases = {}
        if base._exists(c, "return_discount_aliases"):
            aliases = {
                base._oid(r["discount_option_id"]): int(r["parent_product_id"])
                for r in c.execute("SELECT discount_option_id,parent_product_id FROM return_discount_aliases")
            }

        agg = {}
        for row in parsed:
            oid = base._oid(row.get("option_id"))
            pid = aliases.get(oid) or direct.get(oid)
            if pid is None:
                result["unmatched_options"] += 1
                continue
            target = agg.setdefault(int(pid), {"sales_qty": 0.0, "cancel_qty": 0.0, "net_qty": 0.0})
            target["sales_qty"] += float(row.get("sales_qty") or 0)
            target["cancel_qty"] += abs(float(row.get("cancel_qty") or 0))
            target["net_qty"] += float(row.get("net_qty") or 0)
            result["matched_options"] += 1

        bad = [
            (pid, v) for pid, v in agg.items()
            if abs((v["sales_qty"] - v["cancel_qty"]) - v["net_qty"]) > 1e-9
        ]
        if bad:
            raise ValueError("판매/취소·반품/순판매 수량 관계가 맞지 않아 DB 저장을 중단했습니다.")

        # Only after every row is validated do we replace the two presentation fields.
        c.execute("UPDATE sales_stats SET sales_qty=0,cancel_qty=0 WHERE import_id=?", (int(import_id),))
        for pid, values in agg.items():
            rows = c.execute(
                """SELECT rowid AS _rg_rowid FROM sales_stats
                   WHERE import_id=? AND product_id=? ORDER BY rowid""",
                (int(import_id), int(pid)),
            ).fetchall()
            if not rows:
                result["unmatched_options"] += 1
                continue
            rowid = int(rows[0]["_rg_rowid"])
            c.execute(
                "UPDATE sales_stats SET sales_qty=?,cancel_qty=? WHERE rowid=?",
                (float(values["sales_qty"]), float(values["cancel_qty"]), rowid),
            )
            result["sales_qty"] += float(values["sales_qty"])
            result["cancel_qty"] += float(values["cancel_qty"])
    return result


def apply(base):
    # Intentionally reapply on every Streamlit rerun because base is reloaded.
    def parse(source):
        return parse_sales_quantities(base, source)

    def enrich(core, db, import_id, parsed):
        return enrich_import(base, core, db, import_id, parsed)

    base.parse_sales_quantities = parse
    base.enrich_import = enrich
    base._rg_sales_stats_quantity_guard_v09153 = True
    return base
