"""Coupang returned-item discount resale handling for RG Manager v0.9.9.

Rules:
- An unmanaged/auto-created Coupang option sold in sales stats is treated as a
  returned item discount resale only when it can be linked unambiguously to one
  managed original product.
- It is not a new managed SKU.
- Its quantity moves out of 반품창고, not 쿠팡RG.
- The original product's unit cost is used.
- The discount option's actual net sales amount from the sales-stat file is used
  in the expected sales/P&L table when available.
- Ambiguous matches are blocked instead of guessed.
"""
from __future__ import annotations

from io import BytesIO
import math
import re
from typing import Any

import pandas as pd
import streamlit as st

_APPLIED = False


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        x = float(v)
        return default if math.isnan(x) else x
    except Exception:
        return default


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
    return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _name_key(v: Any) -> str:
    s = str(v or "").strip().lower()
    # Only remove terminal quantity/package notation. Variant text is preserved.
    s = re.sub(r"[,/\s]+\d+\s*(?:개입|개|p|pcs?|세트|set)\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,-/")
    return re.sub(r"[\s,·_/\-]+", "", s)


def _source_bytes(source) -> bytes | None:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    if hasattr(source, "getvalue"):
        try:
            return bytes(source.getvalue())
        except Exception:
            pass
    if hasattr(source, "read"):
        try:
            pos = source.tell() if hasattr(source, "tell") else None
            data = source.read()
            if pos is not None and hasattr(source, "seek"):
                source.seek(pos)
            return bytes(data)
        except Exception:
            pass
    return None


def _pick(columns, tests):
    for col in columns:
        n = re.sub(r"[\s_()\-]+", "", str(col)).lower()
        if any(t(n) for t in tests):
            return col
    return None


def _parse_sales_file(source):
    raw = _source_bytes(source)
    if not raw:
        return []
    try:
        xl = pd.ExcelFile(BytesIO(raw))
        sheet = "판매통계" if "판매통계" in xl.sheet_names else xl.sheet_names[0]
        df = pd.read_excel(BytesIO(raw), sheet_name=sheet)
    except Exception:
        return []

    cols = list(df.columns)
    c_oid = _pick(cols, [lambda x: x == "옵션id", lambda x: x.endswith("옵션id")])
    c_name = _pick(cols, [lambda x: x in {"옵션명", "상품명"}, lambda x: x.endswith("옵션명")])
    c_qty = _pick(cols, [lambda x: "순판매상품수" in x, lambda x: x in {"netqty", "순판매수량"}])
    c_amt = _pick(cols, [lambda x: "순판매금액" in x, lambda x: x in {"netsalesamount", "netamount", "순매출"}])
    if c_oid is None or c_name is None or c_qty is None:
        return []

    out = []
    for _, r in df.iterrows():
        oid = _oid(r.get(c_oid))
        qty = _num(r.get(c_qty))
        if not oid.isdigit() or abs(qty) <= 1e-12:
            continue
        amount_known = c_amt is not None and pd.notna(r.get(c_amt))
        out.append({
            "option_id": oid,
            "name": str(r.get(c_name) or "").strip(),
            "name_key": _name_key(r.get(c_name)),
            "qty": qty,
            "amount": _num(r.get(c_amt)) if amount_known else None,
            "amount_known": amount_known,
        })
    return out


def _ensure_schema(core, db):
    core.init_db(db)
    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS return_discount_aliases(
            discount_option_id TEXT PRIMARY KEY,
            parent_product_id INTEGER NOT NULL,
            discount_name TEXT,
            match_method TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS return_discount_sales(
            import_id INTEGER NOT NULL,
            discount_option_id TEXT NOT NULL,
            child_product_id INTEGER,
            parent_product_id INTEGER NOT NULL,
            qty REAL NOT NULL,
            net_sales_amount REAL,
            amount_known INTEGER NOT NULL DEFAULT 0,
            period_start TEXT,
            period_end TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY(import_id, discount_option_id)
        )""")


def _load_products(core, db):
    with core._conn(db) as c:
        rows = c.execute("SELECT id,item_code,option_id,name,unit_cost,active FROM products").fetchall()
    return [{
        "id": int(r["id"]),
        "item_code": str(r["item_code"] or ""),
        "option_id": _oid(r["option_id"]),
        "name": str(r["name"] or ""),
        "name_key": _name_key(r["name"]),
        "unit_cost": _num(r["unit_cost"]),
        "active": int(r["active"] or 0),
    } for r in rows]


def _placeholder(p):
    oid = p.get("option_id", "")
    code = p.get("item_code", "")
    return bool(
        oid
        and re.fullmatch(rf"(?:CP-)?{re.escape(oid)}", code, flags=re.I)
        and abs(_num(p.get("unit_cost"))) <= 1e-12
    )


def _alias_map(core, db):
    _ensure_schema(core, db)
    with core._conn(db) as c:
        rows = c.execute("SELECT discount_option_id,parent_product_id FROM return_discount_aliases").fetchall()
    return {str(r["discount_option_id"]): int(r["parent_product_id"]) for r in rows}


def _resolve(core, db, parsed):
    products = _load_products(core, db)
    by_oid = {p["option_id"]: p for p in products if p["option_id"]}
    aliases = _alias_map(core, db)

    # A managed normal option with the same normalized name in the same weekly
    # file is the strongest automatic match.
    same_file = {}
    for row in parsed:
        p = by_oid.get(row["option_id"])
        if p and not _placeholder(p):
            same_file.setdefault(row["name_key"], set()).add(p["id"])

    master = {}
    for p in products:
        if p["option_id"] and not _placeholder(p):
            master.setdefault(p["name_key"], set()).add(p["id"])

    mappings, unresolved = {}, []
    for row in parsed:
        oid = row["option_id"]
        if oid in aliases:
            mappings[oid] = aliases[oid]
            continue
        p = by_oid.get(oid)
        if p and not _placeholder(p):
            continue

        cand = set(same_file.get(row["name_key"], set()))
        if not cand:
            cand = set(master.get(row["name_key"], set()))
        if p:
            cand.discard(p["id"])
        if len(cand) == 1:
            mappings[oid] = next(iter(cand))
        else:
            unresolved.append((oid, row["name"], len(cand)))

    if unresolved:
        lines = []
        for oid, name, n in unresolved[:8]:
            reason = "원상품 후보 없음" if n == 0 else f"원상품 후보 {n}개"
            lines.append(f"{oid} | {name} ({reason})")
        more = "" if len(unresolved) <= 8 else f" 외 {len(unresolved)-8}개"
        raise ValueError(
            "품목관리에 없는 판매 옵션은 반품 할인판매로 처리해야 하지만 "
            "원상품을 안전하게 자동 매칭할 수 없습니다. 임의 처리하지 않았습니다.\n"
            + "\n".join(lines) + more
        )
    return mappings


def _find_import_id(core, db, result, source, start, end):
    if isinstance(result, dict) and result.get("import_id"):
        return int(result["import_id"])
    ps, pe = core.norm_date(start), core.norm_date(end)
    digest = None
    try:
        digest = core.file_hash(source)
    except Exception:
        pass
    with core._conn(db) as c:
        if digest:
            r = c.execute("""SELECT id FROM imports
                WHERE data_type='sales_stats' AND file_hash=? AND period_start=? AND period_end=?
                ORDER BY id DESC LIMIT 1""", (digest, ps, pe)).fetchone()
            if r:
                return int(r["id"])
        r = c.execute("""SELECT id FROM imports
            WHERE data_type='sales_stats' AND period_start=? AND period_end=?
            ORDER BY id DESC LIMIT 1""", (ps, pe)).fetchone()
        return int(r["id"]) if r else None


def _post_discount(core, db, import_id, parsed, mappings):
    if not mappings:
        return 0
    by_oid = {r["option_id"]: r for r in parsed}
    now = core.now_iso()
    with core._conn(db) as c:
        wh = c.execute("SELECT id FROM warehouses WHERE name='반품창고'").fetchone()
        if not wh:
            raise ValueError("반품창고를 찾지 못했습니다.")
        wh_id = int(wh["id"])
        imp = c.execute("SELECT period_start,period_end FROM imports WHERE id=?", (import_id,)).fetchone()
        ps = str(imp["period_start"] or "") if imp else ""
        pe = str(imp["period_end"] or "") if imp else ""

        count = 0
        for oid, parent_pid in mappings.items():
            row = by_oid.get(oid)
            if not row:
                continue
            child = c.execute("SELECT id,name FROM products WHERE option_id=?", (oid,)).fetchone()
            child_pid = int(child["id"]) if child else None
            dname = row["name"] or (str(child["name"] or "") if child else "")

            c.execute("""INSERT INTO return_discount_aliases
                (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(discount_option_id) DO UPDATE SET
                  parent_product_id=excluded.parent_product_id,
                  discount_name=excluded.discount_name,
                  updated_at=excluded.updated_at""",
                (oid, int(parent_pid), dname, "auto_name_unique", now, now))

            c.execute("""INSERT INTO return_discount_sales
                (import_id,discount_option_id,child_product_id,parent_product_id,qty,
                 net_sales_amount,amount_known,period_start,period_end,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(import_id,discount_option_id) DO UPDATE SET
                  child_product_id=excluded.child_product_id,
                  parent_product_id=excluded.parent_product_id,
                  qty=excluded.qty,
                  net_sales_amount=excluded.net_sales_amount,
                  amount_known=excluded.amount_known,
                  period_start=excluded.period_start,
                  period_end=excluded.period_end""",
                (import_id, oid, child_pid, int(parent_pid), float(row["qty"]),
                 float(row["amount"]) if row["amount_known"] else None,
                 1 if row["amount_known"] else 0, ps, pe, now))

            # v0.8.8 posted it as ordinary RG sale. Undo only that option's posting.
            if child_pid is not None:
                c.execute("""DELETE FROM inventory_txns
                    WHERE txn_type='판매차감' AND ref_no=? AND product_id=?""",
                    (f"SALESSTAT-{import_id}", child_pid))

            ref = f"RETSALE-{import_id}-{oid}"
            c.execute("DELETE FROM inventory_txns WHERE txn_type='반품할인판매차감' AND ref_no=?", (ref,))
            c.execute("""INSERT INTO inventory_txns
                (txn_date,product_id,warehouse_id,qty_delta,txn_type,ref_no,memo,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (pe or ps, int(parent_pid), wh_id, -float(row["qty"]),
                 "반품할인판매차감", ref, f"쿠팡 반품 할인판매 옵션ID {oid}", now))
            count += 1
    return count


def _amount_column(core, db):
    with core._conn(db) as c:
        cols = [str(r["name"]) for r in c.execute("PRAGMA table_info(sales_stats)").fetchall()]
    exact = [
        "net_sales_amount", "net_amount", "sales_amount", "net_sales",
        "순판매금액", "순_판매_금액"
    ]
    for x in exact:
        if x in cols:
            return x
    for x in cols:
        n = x.lower()
        if ("net" in n or "순" in n) and ("amount" in n or "sales" in n or "금액" in n):
            return x
    return None


def _repair_existing(core, db):
    """Convert existing zero-cost auto-created sales options when match is unique."""
    products = _load_products(core, db)
    originals = {}
    for p in products:
        if p["option_id"] and not _placeholder(p):
            originals.setdefault(p["name_key"], set()).add(p["id"])
    amount_col = _amount_column(core, db)
    repaired = 0

    for child in products:
        if not _placeholder(child) or not child["option_id"]:
            continue
        cand = set(originals.get(child["name_key"], set()))
        cand.discard(child["id"])
        if len(cand) != 1:
            continue
        parent_pid = next(iter(cand))
        with core._conn(db) as c:
            rows = c.execute("""SELECT import_id,COALESCE(SUM(net_qty),0) qty
                FROM sales_stats WHERE product_id=? GROUP BY import_id""", (child["id"],)).fetchall()
        for sr in rows:
            import_id = int(sr["import_id"])
            qty = _num(sr["qty"])
            amount, known = None, False
            if amount_col:
                with core._conn(db) as c:
                    ar = c.execute(
                        f'SELECT COALESCE(SUM("{amount_col}"),0) amount FROM sales_stats '
                        'WHERE import_id=? AND product_id=?',
                        (import_id, child["id"])
                    ).fetchone()
                amount, known = _num(ar["amount"]) if ar else 0.0, True
            parsed = [{
                "option_id": child["option_id"], "name": child["name"],
                "name_key": child["name_key"], "qty": qty,
                "amount": amount, "amount_known": known,
            }]
            _post_discount(core, db, import_id, parsed, {child["option_id"]: parent_pid})
            repaired += 1
    return repaired


def _clean_stale(core, db):
    with core._conn(db) as c:
        stale = c.execute("""SELECT import_id,discount_option_id FROM return_discount_sales
            WHERE import_id NOT IN (SELECT id FROM imports)""").fetchall()
        for r in stale:
            c.execute("""DELETE FROM inventory_txns
                WHERE txn_type='반품할인판매차감' AND ref_no=?""",
                (f"RETSALE-{int(r['import_id'])}-{r['discount_option_id']}",))
        c.execute("""DELETE FROM return_discount_sales
            WHERE import_id NOT IN (SELECT id FROM imports)""")


def _discount_info(core, db):
    with core._conn(db) as c:
        rows = c.execute("""SELECT a.discount_option_id,p.option_id parent_option_id,
                    p.name parent_name,p.unit_cost parent_unit_cost,
                    s.import_id,s.qty,s.net_sales_amount,s.amount_known
            FROM return_discount_aliases a
            JOIN products p ON p.id=a.parent_product_id
            LEFT JOIN return_discount_sales s ON s.discount_option_id=a.discount_option_id
            ORDER BY s.import_id DESC""").fetchall()
    out = {}
    for r in rows:
        oid = str(r["discount_option_id"])
        info = out.setdefault(oid, {
            "parent_option_id": _oid(r["parent_option_id"]),
            "parent_name": str(r["parent_name"] or ""),
            "unit_cost": _num(r["parent_unit_cost"]),
            "sales": [],
        })
        if r["import_id"] is not None:
            info["sales"].append({
                "import_id": int(r["import_id"]), "qty": _num(r["qty"]),
                "amount": _num(r["net_sales_amount"]), "known": bool(r["amount_known"]),
            })
    return out


def _snum(df, col):
    return pd.to_numeric(
        df[col].fillna(0).astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("원", "", regex=False)
        .str.replace("%", "", regex=False),
        errors="coerce"
    ).fillna(0.0)


def _like(original, value, pct=False):
    if isinstance(original, str):
        if pct or "%" in original:
            return f"{value:,.1f}%"
        if "원" in original:
            return f"{int(round(value)):,}원"
        if "," in original:
            return f"{int(round(value)):,}"
    return value


def _choose_record(info, qty):
    sales = info.get("sales", [])
    if not sales:
        return None
    same = [x for x in sales if abs(x["qty"] - qty) <= 1e-9]
    return (same or sales)[0]


def _enhance_pnl(core, db, df):
    req = {"옵션ID", "상품명", "판매수량", "예상 실현단가", "예상매출", "원가/개", "매출원가", "예상이익"}
    if not isinstance(df, pd.DataFrame) or not req.issubset(df.columns):
        return df
    info_map = _discount_info(core, db)
    if not info_map:
        return df

    out = df.copy()
    oids = out["옵션ID"].map(_oid)
    qtys = _snum(out, "판매수량")
    parent_row = {}
    for i, oid in oids.items():
        parent_row.setdefault(oid, i)

    for idx in out.index:
        oid = oids.loc[idx]
        info = info_map.get(oid)
        if not info:
            continue
        qty = _num(qtys.loc[idx])
        rec = _choose_record(info, qty)
        amount_known = bool(rec and rec["known"])
        revenue = rec["amount"] if amount_known else _num(_snum(out.loc[[idx]], "예상매출").iloc[0])
        unit_price = revenue / qty if amount_known and abs(qty) > 1e-12 else _num(_snum(out.loc[[idx]], "예상 실현단가").iloc[0])
        cost = _num(info["unit_cost"])
        cogs = -abs(qty) * cost

        if "[반품 할인판매]" not in str(out.at[idx, "상품명"]):
            out.at[idx, "상품명"] = str(out.at[idx, "상품명"]) + " [반품 할인판매]"
        out.at[idx, "예상 실현단가"] = _like(out.at[idx, "예상 실현단가"], unit_price)
        out.at[idx, "예상매출"] = _like(out.at[idx, "예상매출"], revenue)
        out.at[idx, "원가/개"] = _like(out.at[idx, "원가/개"], cost)
        out.at[idx, "매출원가"] = _like(out.at[idx, "매출원가"], cogs)

        commission = _num(_snum(out.loc[[idx]], "판매수수료").iloc[0]) if "판매수수료" in out.columns else 0.0
        inout = _num(_snum(out.loc[[idx]], "입출고비").iloc[0]) if "입출고비" in out.columns else 0.0
        delivery = _num(_snum(out.loc[[idx]], "배송비").iloc[0]) if "배송비" in out.columns else 0.0
        ret = _num(_snum(out.loc[[idx]], "반품충당").iloc[0]) if "반품충당" in out.columns else 0.0
        ad = _num(_snum(out.loc[[idx]], "광고비").iloc[0]) if "광고비" in out.columns else 0.0

        pidx = parent_row.get(info["parent_option_id"])
        if pidx is not None and pidx != idx:
            pq = abs(_num(qtys.loc[pidx]))
            prev = _num(_snum(out.loc[[pidx]], "예상매출").iloc[0])
            if amount_known and "판매수수료" in out.columns and abs(prev) > 1e-12:
                pcomm = _num(_snum(out.loc[[pidx]], "판매수수료").iloc[0])
                commission = (pcomm / prev) * revenue
            if pq > 1e-12:
                if "입출고비" in out.columns:
                    inout = (_num(_snum(out.loc[[pidx]], "입출고비").iloc[0]) / pq) * abs(qty)
                if "배송비" in out.columns:
                    delivery = (_num(_snum(out.loc[[pidx]], "배송비").iloc[0]) / pq) * abs(qty)

        for col, val in (("판매수수료", commission), ("입출고비", inout), ("배송비", delivery)):
            if col in out.columns:
                out.at[idx, col] = _like(out.at[idx, col], val)
        rg = inout + delivery + ret
        if "RG비용" in out.columns:
            out.at[idx, "RG비용"] = _like(out.at[idx, "RG비용"], rg)

        no_ad = revenue + cogs + commission + rg
        profit = no_ad + ad
        if "광고제외이익" in out.columns:
            out.at[idx, "광고제외이익"] = _like(out.at[idx, "광고제외이익"], no_ad)
        out.at[idx, "예상이익"] = _like(out.at[idx, "예상이익"], profit)
        if "이익률(%)" in out.columns:
            margin = profit / revenue * 100 if abs(revenue) > 1e-12 else 0.0
            out.at[idx, "이익률(%)"] = _like(out.at[idx, "이익률(%)"], margin, pct=True)
    return out


def _hide_alias_rows(core, db, df):
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    aliases = set(_alias_map(core, db))
    if not aliases:
        return df
    if "쿠팡 옵션ID" in df.columns:
        return df.loc[~df["쿠팡 옵션ID"].map(_oid).isin(aliases)].copy()
    inv_cols = {"품목코드", "상품명", "자체창고", "쿠팡RG", "반품창고"}
    if inv_cols.issubset(df.columns):
        codes = df["품목코드"].astype(str).str.replace("CP-", "", regex=False)
        return df.loc[~codes.isin(aliases)].copy()
    return df


def apply(core, db_path=None):
    global _APPLIED
    if _APPLIED or getattr(core, "_rg_return_discount_v099_applied", False):
        return core
    db = db_path or core.DEFAULT_DB
    _ensure_schema(core, db)
    _repair_existing(core, db)
    _clean_stale(core, db)

    previous_import = core.import_sales_stats
    previous_dataframe = st.dataframe

    def import_sales_stats(source, file_name, period_start, period_end, db_path=None):
        target = db_path or db
        _ensure_schema(core, target)
        parsed = _parse_sales_file(source)
        mappings = _resolve(core, target, parsed) if parsed else {}
        result = previous_import(source, file_name, period_start, period_end, target)
        import_id = _find_import_id(core, target, result, source, period_start, period_end)
        if import_id is not None and mappings:
            count = _post_discount(core, target, import_id, parsed, mappings)
            if isinstance(result, dict):
                result = dict(result)
                result["return_discount_rows"] = count
        _clean_stale(core, target)
        return result

    def dataframe(data=None, *args, **kwargs):
        if isinstance(data, pd.DataFrame):
            data = _hide_alias_rows(core, db, data)
            data = _enhance_pnl(core, db, data)
        return previous_dataframe(data, *args, **kwargs)

    core.import_sales_stats = import_sales_stats
    st.dataframe = dataframe
    core._rg_return_discount_v099_applied = True
    _APPLIED = True
    return core
