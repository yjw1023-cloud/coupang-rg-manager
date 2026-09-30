"""RG Manager v0.9.254 generic ERP DB access for ChatGPT.

Purpose:
- Let the AI inspect the full ERP SQLite schema.
- Read any normal ERP table.
- Insert/update/delete rows in any normal ERP table with automatic DB backup.
- No arbitrary SQL execution and no access to SQLite internal tables.
"""
from __future__ import annotations

import importlib
import re

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(value):
    s = str(value or "").strip()
    if not _IDENT.match(s):
        raise ValueError(f"잘못된 식별자입니다: {s}")
    if s.lower().startswith("sqlite_"):
        raise ValueError("SQLite 내부 테이블에는 접근할 수 없습니다.")
    return s


def _tables(core):
    with core._conn(core.DEFAULT_DB) as c:
        rows = c.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        ).fetchall()
    return [str(r[0]) for r in rows]


def schema(core, payload=None):
    payload = payload or {}
    only = str(payload.get("table") or "").strip()
    tables = [_ident(only)] if only else _tables(core)
    out = {}
    with core._conn(core.DEFAULT_DB) as c:
        for table in tables:
            if table not in _tables(core):
                continue
            cols = c.execute(f'PRAGMA table_info("{table}")').fetchall()
            out[table] = [
                {
                    "cid": r[0],
                    "name": r[1],
                    "type": r[2],
                    "notnull": bool(r[3]),
                    "default": r[4],
                    "pk": bool(r[5]),
                }
                for r in cols
            ]
    return {"tables": out}


def _where(filters):
    if not filters:
        return "", []
    if not isinstance(filters, dict):
        raise ValueError("filters는 객체 형식이어야 합니다.")
    parts, vals = [], []
    for k, v in filters.items():
        col = _ident(k)
        if isinstance(v, dict):
            op = str(v.get("op") or "=").strip().lower()
            val = v.get("value")
            if op in {"=", "!=", ">", ">=", "<", "<="}:
                parts.append(f'"{col}" {op} ?')
                vals.append(val)
            elif op == "like":
                parts.append(f'CAST("{col}" AS TEXT) LIKE ?')
                vals.append(str(val))
            elif op == "contains":
                parts.append(f'CAST("{col}" AS TEXT) LIKE ?')
                vals.append(f"%{val}%")
            elif op == "in":
                arr = val if isinstance(val, list) else []
                if not arr:
                    parts.append("1=0")
                else:
                    parts.append(f'"{col}" IN ({",".join(["?"]*len(arr))})')
                    vals.extend(arr)
            elif op == "isnull":
                parts.append(f'"{col}" IS NULL' if bool(val) else f'"{col}" IS NOT NULL')
            else:
                raise ValueError(f"지원하지 않는 필터 연산자입니다: {op}")
        else:
            parts.append(f'"{col}" = ?')
            vals.append(v)
    return (" WHERE " + " AND ".join(parts)) if parts else "", vals


def read(core, payload):
    table = _ident(payload.get("table"))
    if table not in _tables(core):
        raise ValueError(f"테이블을 찾지 못했습니다: {table}")
    cols = payload.get("columns")
    if cols:
        if not isinstance(cols, list):
            raise ValueError("columns는 배열이어야 합니다.")
        select_cols = ", ".join(f'"{_ident(x)}"' for x in cols)
    else:
        select_cols = "*"
    where_sql, vals = _where(payload.get("filters"))
    order = payload.get("order_by") or []
    order_sql = ""
    if order:
        if isinstance(order, str):
            order = [order]
        bits=[]
        for item in order:
            if isinstance(item, dict):
                col=_ident(item.get("column"))
                direction=str(item.get("direction") or "asc").lower()
            else:
                col=_ident(item)
                direction="asc"
            if direction not in {"asc","desc"}:
                raise ValueError("order_by direction은 asc 또는 desc만 가능합니다.")
            bits.append(f'"{col}" {direction.upper()}')
        order_sql=" ORDER BY "+", ".join(bits)
    limit=max(1,min(int(payload.get("limit") or 200),2000))
    offset=max(0,int(payload.get("offset") or 0))
    sql=f'SELECT {select_cols} FROM "{table}"{where_sql}{order_sql} LIMIT ? OFFSET ?'
    vals.extend([limit,offset])
    with core._conn(core.DEFAULT_DB) as c:
        rows=c.execute(sql,vals).fetchall()
        data=[dict(r) for r in rows]
    return {"table":table,"rows":data,"count":len(data),"limit":limit,"offset":offset}


def _backup(core, reason):
    return importlib.import_module("db_backup_v09248").backup_db(
        core, reason, core.DEFAULT_DB
    )


def insert(core, payload):
    table=_ident(payload.get("table"))
    values=payload.get("values")
    if table not in _tables(core):
        raise ValueError(f"테이블을 찾지 못했습니다: {table}")
    if not isinstance(values,dict) or not values:
        raise ValueError("values가 필요합니다.")
    cols=[_ident(k) for k in values]
    vals=[values[k] for k in values]
    backup=_backup(core,f"ai_insert_{table}")
    qmarks=",".join(["?"]*len(cols))
    sql=f'INSERT INTO "{table}" ({",".join([f""""{c}"""" for c in cols])}) VALUES ({qmarks})'
    with core._conn(core.DEFAULT_DB) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            cur=c.execute(sql,vals)
            rowid=cur.lastrowid
            c.commit()
        except Exception:
            c.rollback()
            raise
    return {"table":table,"inserted":1,"rowid":rowid,"backup":backup}


def update(core,payload):
    table=_ident(payload.get("table"))
    values=payload.get("values")
    filters=payload.get("filters")
    if table not in _tables(core):
        raise ValueError(f"테이블을 찾지 못했습니다: {table}")
    if not isinstance(values,dict) or not values:
        raise ValueError("values가 필요합니다.")
    if not isinstance(filters,dict) or not filters:
        raise ValueError("전체 업데이트 방지를 위해 filters가 필요합니다.")
    set_cols=[_ident(k) for k in values]
    set_vals=[values[k] for k in values]
    where_sql,where_vals=_where(filters)
    backup=_backup(core,f"ai_update_{table}")
    sql=f'UPDATE "{table}" SET '+", ".join([f'"{c}"=?' for c in set_cols])+where_sql
    with core._conn(core.DEFAULT_DB) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            cur=c.execute(sql,set_vals+where_vals)
            count=cur.rowcount
            c.commit()
        except Exception:
            c.rollback()
            raise
    return {"table":table,"updated":count,"backup":backup}


def delete(core,payload):
    table=_ident(payload.get("table"))
    filters=payload.get("filters")
    if table not in _tables(core):
        raise ValueError(f"테이블을 찾지 못했습니다: {table}")
    if not isinstance(filters,dict) or not filters:
        raise ValueError("전체 삭제 방지를 위해 filters가 필요합니다.")
    where_sql,where_vals=_where(filters)
    backup=_backup(core,f"ai_delete_{table}")
    sql=f'DELETE FROM "{table}"'+where_sql
    with core._conn(core.DEFAULT_DB) as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            cur=c.execute(sql,where_vals)
            count=cur.rowcount
            c.commit()
        except Exception:
            c.rollback()
            raise
    return {"table":table,"deleted":count,"backup":backup}
