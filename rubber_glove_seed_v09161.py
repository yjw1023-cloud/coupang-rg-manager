"""RG Manager v0.9.162 rubber-glove finished-product/BOM seed.

Hotfix for v0.9.161: the purchase matching screen can show proposed JDS codes
before the operator has finished committing those new raw items to the DB.  A
missing JDS0020/JDS0021/JDS0022 is therefore a normal pending state, not an app-
startup error.

Behavior:
- always ensure the three Coupang RG finished products exist;
- always store the user-confirmed commercial fallback values;
- link S/M/L to JDS0020/JDS0021/JDS0022 x 5 only when that exact raw item exists;
- if a component is not committed yet, leave BOM pending and retry on the next
  Streamlit rerun/app start;
- never create, move, or change inventory quantity here.
"""
from __future__ import annotations

import math
from typing import Any


REQUESTS = [
    {
        "option_id": "96012086788",
        "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 소(S)",
        "component_code": "JDS0020",
        "qty_per": 5.0,
    },
    {
        "option_id": "96012086789",
        "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 중(M)",
        "component_code": "JDS0021",
        "qty_per": 5.0,
    },
    {
        "option_id": "96012086790",
        "finished_name": "두툼한 작업용 고무장갑 두꺼운, 5세트 노랑 대(L)",
        "component_code": "JDS0022",
        "qty_per": 5.0,
    },
]

SELLING_PRICE = 13900.0
COMMISSION_RATE = 0.108
COMMISSION_UNIT = SELLING_PRICE * COMMISSION_RATE
LOGISTICS_UNIT_TOTAL = 2800.0
_RULE = "v0.9.162-rubber-glove-deferred-bom"


def _num(v: Any) -> float:
    try:
        x = float(v or 0)
        return x if math.isfinite(x) else 0.0
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
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def _ensure_tables(con) -> None:
    con.execute(
        """CREATE TABLE IF NOT EXISTS bom_items(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               parent_product_id INTEGER NOT NULL,
               component_product_id INTEGER NOT NULL,
               qty_per REAL NOT NULL,
               UNIQUE(parent_product_id,component_product_id)
           )"""
    )
    con.execute(
        """CREATE TABLE IF NOT EXISTS product_commercial_defaults(
               option_id TEXT PRIMARY KEY,
               product_id INTEGER,
               selling_price REAL NOT NULL,
               commission_rate REAL NOT NULL,
               commission_unit REAL NOT NULL,
               logistics_unit_total REAL NOT NULL,
               source TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )"""
    )


def _ensure_finished(core, con, req):
    oid = str(req["option_id"])
    name = str(req["finished_name"])
    code = f"CP-{oid}"
    now = core.now_iso()
    row = con.execute(
        """SELECT id,item_code,option_id,name,item_type,active
           FROM products WHERE CAST(option_id AS TEXT)=?
           ORDER BY CASE WHEN item_type='finished' THEN 0 ELSE 1 END,id LIMIT 1""",
        (oid,),
    ).fetchone()
    if row:
        pid = int(row["id"])
        con.execute(
            """UPDATE products SET item_code=?,option_id=?,name=?,item_type='finished',
                      active=1,updated_at=? WHERE id=?""",
            (code, oid, name, now, pid),
        )
        return pid, "existing"

    by_code = con.execute(
        "SELECT id,option_id FROM products WHERE item_code=? ORDER BY id LIMIT 1",
        (code,),
    ).fetchone()
    if by_code:
        existing_oid = _oid(by_code["option_id"])
        if existing_oid and existing_oid != oid:
            raise RuntimeError(f"{code}가 다른 옵션ID {existing_oid}에 이미 연결되어 있습니다.")
        pid = int(by_code["id"])
        con.execute(
            """UPDATE products SET option_id=?,name=?,item_type='finished',active=1,
                      updated_at=? WHERE id=?""",
            (oid, name, now, pid),
        )
        return pid, "reused"

    cur = con.execute(
        """INSERT INTO products(item_code,option_id,name,item_type,unit_cost,active,updated_at)
           VALUES(?,?,?,'finished',0,1,?)""",
        (code, oid, name, now),
    )
    return int(cur.lastrowid), "created"


def _component_if_ready(con, code: str):
    row = con.execute(
        """SELECT id,item_code,option_id,name,item_type,unit_cost,active
           FROM products WHERE item_code=? ORDER BY id LIMIT 1""",
        (str(code),),
    ).fetchone()
    if not row:
        return None, "pending_missing"
    if _oid(row["option_id"]):
        return None, "pending_not_raw"
    if str(row["item_type"] or "") != "raw":
        return None, "pending_not_raw"
    return row, "ready"


def _upsert_bom(con, parent_id: int, component_id: int, qty_per: float):
    con.execute(
        "DELETE FROM bom_items WHERE parent_product_id=? AND component_product_id<>?",
        (int(parent_id), int(component_id)),
    )
    row = con.execute(
        """SELECT id,qty_per FROM bom_items
           WHERE parent_product_id=? AND component_product_id=? ORDER BY id LIMIT 1""",
        (int(parent_id), int(component_id)),
    ).fetchone()
    if row:
        old = float(row["qty_per"] or 0)
        con.execute(
            "UPDATE bom_items SET qty_per=? WHERE id=?",
            (float(qty_per), int(row["id"])),
        )
        return int(row["id"]), "unchanged" if abs(old - float(qty_per)) <= 1e-12 else "updated"
    cur = con.execute(
        "INSERT INTO bom_items(parent_product_id,component_product_id,qty_per) VALUES(?,?,?)",
        (int(parent_id), int(component_id), float(qty_per)),
    )
    return int(cur.lastrowid), "created"


def _upsert_commercial(core, con, option_id: str, product_id: int) -> None:
    con.execute(
        """INSERT INTO product_commercial_defaults
           (option_id,product_id,selling_price,commission_rate,commission_unit,
            logistics_unit_total,source,updated_at)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(option_id) DO UPDATE SET
             product_id=excluded.product_id,
             selling_price=excluded.selling_price,
             commission_rate=excluded.commission_rate,
             commission_unit=excluded.commission_unit,
             logistics_unit_total=excluded.logistics_unit_total,
             source=excluded.source,
             updated_at=excluded.updated_at""",
        (
            str(option_id), int(product_id), SELLING_PRICE, COMMISSION_RATE,
            COMMISSION_UNIT, LOGISTICS_UNIT_TOTAL, _RULE, core.now_iso(),
        ),
    )


def _commercial_map(core, db):
    try:
        with core._conn(db) as con:
            if not con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_commercial_defaults'"
            ).fetchone():
                return {}, {}
            rows = con.execute(
                """SELECT option_id,product_id,selling_price,commission_rate,
                          commission_unit,logistics_unit_total
                   FROM product_commercial_defaults"""
            ).fetchall()
        by_oid = {str(r["option_id"]): dict(r) for r in rows}
        by_pid = {int(r["product_id"]): dict(r) for r in rows if r["product_id"] is not None}
        return by_oid, by_pid
    except Exception:
        return {}, {}


def _apply_defaults_to_df(df, core, db):
    try:
        import pandas as pd
    except Exception:
        return df
    if not isinstance(df, pd.DataFrame) or df.empty:
        return df
    by_oid, by_pid = _commercial_map(core, db)
    if not by_oid and not by_pid:
        return df
    out = df.copy()
    for idx in out.index:
        default = None
        for col in ("option_id", "옵션ID"):
            if col in out.columns:
                default = by_oid.get(_oid(out.at[idx, col]))
                if default:
                    break
        if default is None and "product_id" in out.columns:
            try:
                default = by_pid.get(int(_num(out.at[idx, "product_id"])))
            except Exception:
                default = None
        if not default:
            continue

        q = 0.0
        for col in ("net_qty", "판매수량", "sales_qty", "net_sales_qty"):
            if col in out.columns:
                q = _num(out.at[idx, col])
                break
        aq = abs(q)
        if aq <= 0:
            continue

        for col in ("expected_revenue", "예상매출"):
            if col in out.columns and abs(_num(out.at[idx, col])) <= 1e-9:
                out.at[idx, col] = aq * _num(default["selling_price"])
        for col in ("expected_unit_price", "예상 실현단가"):
            if col in out.columns and abs(_num(out.at[idx, col])) <= 1e-9:
                out.at[idx, col] = _num(default["selling_price"])
        for col in ("expected_commission", "판매수수료", "commission"):
            if col in out.columns and abs(_num(out.at[idx, col])) <= 1e-9:
                value = aq * _num(default["commission_unit"])
                out.at[idx, col] = -value if col == "판매수수료" else value

        exp_inout = _num(out.at[idx, "expected_inout"]) if "expected_inout" in out.columns else 0.0
        exp_delivery = _num(out.at[idx, "expected_delivery"]) if "expected_delivery" in out.columns else 0.0
        manual_inout = out.at[idx, "manual_expected_inout"] if "manual_expected_inout" in out.columns else None
        manual_delivery = out.at[idx, "manual_expected_delivery"] if "manual_expected_delivery" in out.columns else None
        if manual_inout is None and manual_delivery is None and abs(exp_inout) <= 1e-9 and abs(exp_delivery) <= 1e-9:
            total = aq * _num(default["logistics_unit_total"])
            if "delivery_unit" in out.columns:
                out.at[idx, "delivery_unit"] = _num(default["logistics_unit_total"])
            if "expected_delivery" in out.columns:
                out.at[idx, "expected_delivery"] = total
            if "배송비" in out.columns and abs(_num(out.at[idx, "배송비"])) <= 1e-9:
                out.at[idx, "배송비"] = -total
    return out


def _patch_estimated_pnl(core) -> None:
    if getattr(core, "_rg_rubber_glove_defaults_v09162_applied", False):
        return
    base = getattr(core, "estimated_pnl", None)
    if not callable(base):
        core._rg_rubber_glove_defaults_v09162_applied = True
        return

    def estimated_pnl(*args, **kwargs):
        result = base(*args, **kwargs)
        db = kwargs.get("db_path") or core.DEFAULT_DB
        if isinstance(result, tuple) and result:
            parts = list(result)
            parts[0] = _apply_defaults_to_df(parts[0], core, db)
            if len(parts) >= 2 and isinstance(parts[1], dict):
                meta = dict(parts[1])
                meta["rubber_glove_default_rule"] = _RULE
                parts[1] = meta
            return tuple(parts)
        return _apply_defaults_to_df(result, core, db)

    core.estimated_pnl = estimated_pnl
    core._rg_rubber_glove_defaults_v09162_applied = True


def apply(core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    results = []
    pending = []
    with core._conn(db) as con:
        _ensure_tables(con)
        for req in REQUESTS:
            parent_id, product_status = _ensure_finished(core, con, req)
            _upsert_commercial(core, con, req["option_id"], parent_id)
            component, component_status = _component_if_ready(con, req["component_code"])
            if component is None:
                pending.append(req["component_code"])
                results.append(
                    {
                        "option_id": req["option_id"],
                        "name": req["finished_name"],
                        "product_id": parent_id,
                        "product_status": product_status,
                        "component_code": req["component_code"],
                        "component_status": component_status,
                        "qty_per": float(req["qty_per"]),
                        "bom_status": "pending",
                    }
                )
                continue

            bom_id, bom_status = _upsert_bom(
                con, parent_id, int(component["id"]), float(req["qty_per"])
            )
            results.append(
                {
                    "option_id": req["option_id"],
                    "name": req["finished_name"],
                    "product_id": parent_id,
                    "product_status": product_status,
                    "component_code": req["component_code"],
                    "component_name": str(component["name"] or ""),
                    "component_status": component_status,
                    "qty_per": float(req["qty_per"]),
                    "bom_id": bom_id,
                    "bom_status": bom_status,
                }
            )
    _patch_estimated_pnl(core)
    return {
        "ok": True,
        "rule": _RULE,
        "items": results,
        "pending_components": pending,
        "inventory_changed": False,
    }
