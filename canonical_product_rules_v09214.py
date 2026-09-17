"""v0.9.214 ERP-wide normal-product and returned-item resale rules.

Source of truth: the user's 2026-09-17 Coupang RG workbook.
- non-yellow option IDs: current normal products
- yellow option IDs: discontinued normal products (history kept, UI hidden)
- returned-item resale option IDs: aliases of an original normal product
"""
from __future__ import annotations
import importlib
from typing import Any

ACTIVE_IDS = set("""96032176717 96032069225 96032025464 96031958640 96031916854 96031868051 96031828212 96031753512 96031662095 96031591807 96031499411 96031454711 96031403084 96031368439 96012086788 96012086789 96012086790 95995366301 95997140556 95985900965 95985864521 95985792307 95985756966 95985697006 95985636464 95985636462 95985636463 95912816721 95912717676 95912623408 95861208739 95861208738 95849578032 95849578033 95834379201 95828314407 95648063867 95631138188 95631138189 95612444686 95251584939 95251268814 94948167737 94947803716 94605540426 94481130957 94481093156 94481001229 94475502655 94475454519 94475426058 94387597514 94351514099 94351150317 94351031953 94350959619 94350361655 94350296878 94350207284 94191004835 94189988104 94138813047 94138340319 94125954092 94125499117 94121729622 94121677686 94103975794 94185578349 94138655933 94124510649 94126073739""".split())
DISCONTINUED_IDS = set("""95594235700 95300185444 95300124056 95300023745 95299992627 95251627706 95251561252 95251539739 95251506673 95251457883 95251395964 95251380743 95251343038 95251291917 95251239349 95251182427 95251153533 94948372033 94948187475 94948001300 94947945120 94947761982 94947729423 94731787590 94731728302 94731669021 94731598307 94731539901 94727233411 94727122078 94727013681 94726972326 94726938812 94605875832 94605843381 94605808352 94475401002 94475374150 94351561733 94351260885 94350929667 94350133631 94190872040 94190659796 94190104777 94189960562 94275608236 94189864610 94188001488 94187834020 94138635141 94138475132 94138407210 94138407215 94125738354 94124784104 94121991902 94121840688 94121776475 94104136247""".split())
CURRENT_IDS = ACTIVE_IDS | DISCONTINUED_IDS


def _oid(v: Any) -> str:
    if v is None:
        return ""
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


def _exists(c, table):
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _cols(c, table):
    return {str(r["name"]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()} if _exists(c, table) else set()


def _seed_registry(core, db):
    now = core.now_iso()
    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS coupang_normal_option_registry(
          vendor_item_id TEXT PRIMARY KEY, product_name TEXT, option_name TEXT,
          exposure_product_id TEXT, seller_product_id TEXT, source_file TEXT,
          first_seen_at TEXT, last_seen_at TEXT)""")
        cols = _cols(c, "coupang_normal_option_registry")
        if "status" not in cols:
            c.execute("ALTER TABLE coupang_normal_option_registry ADD COLUMN status TEXT")
        if "status_source" not in cols:
            c.execute("ALTER TABLE coupang_normal_option_registry ADD COLUMN status_source TEXT")

        # This workbook is the authoritative normal-product list.
        q = ",".join("?" for _ in CURRENT_IDS)
        c.execute(
            f"DELETE FROM coupang_normal_option_registry WHERE vendor_item_id NOT IN ({q})",
            tuple(sorted(CURRENT_IDS)),
        )
        for oid, status in [(x, "active") for x in ACTIVE_IDS] + [(x, "discontinued") for x in DISCONTINUED_IDS]:
            p = c.execute(
                "SELECT name FROM products WHERE CAST(option_id AS TEXT)=? ORDER BY active DESC,id DESC LIMIT 1",
                (oid,),
            ).fetchone() if _exists(c, "products") else None
            name = str(p["name"] or "") if p else ""
            c.execute("""INSERT INTO coupang_normal_option_registry
              (vendor_item_id,product_name,source_file,first_seen_at,last_seen_at,status,status_source)
              VALUES(?,?,?,?,?,?,?) ON CONFLICT(vendor_item_id) DO UPDATE SET
              product_name=CASE WHEN excluded.product_name<>'' THEN excluded.product_name ELSE coupang_normal_option_registry.product_name END,
              source_file=excluded.source_file,last_seen_at=excluded.last_seen_at,status=excluded.status,status_source=excluded.status_source""",
              (oid, name, "user_rg_workbook_2026-09-17", now, now, status, "user_highlight"))

        # Workbook IDs are verified ORIGINAL options and must never be return-sale aliases.
        if _exists(c, "return_discount_aliases"):
            c.execute(
                f"DELETE FROM return_discount_aliases WHERE discount_option_id IN ({q})",
                tuple(sorted(CURRENT_IDS)),
            )
            if _exists(c, "return_discount_sales"):
                c.execute(
                    f"DELETE FROM return_discount_sales WHERE discount_option_id IN ({q})",
                    tuple(sorted(CURRENT_IDS)),
                )


def _sync_visibility(core, db):
    now = core.now_iso()
    with core._conn(db) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS system_hidden_products(
          product_id INTEGER PRIMARY KEY,reason TEXT NOT NULL,hidden_at TEXT NOT NULL)""")
        q = ",".join("?" for _ in ACTIVE_IDS)
        ids = [int(r["id"]) for r in c.execute(
            f"SELECT id FROM products WHERE CAST(option_id AS TEXT) IN ({q})",
            tuple(sorted(ACTIVE_IDS)),
        ).fetchall()]
        if ids:
            z = ",".join("?" for _ in ids)
            c.execute(f"DELETE FROM system_hidden_products WHERE product_id IN ({z})", tuple(ids))
            c.execute(f"UPDATE products SET active=1 WHERE id IN ({z})", tuple(ids))

        q = ",".join("?" for _ in DISCONTINUED_IDS)
        for r in c.execute(
            f"SELECT id FROM products WHERE CAST(option_id AS TEXT) IN ({q})",
            tuple(sorted(DISCONTINUED_IDS)),
        ).fetchall():
            pid = int(r["id"])
            c.execute("""INSERT INTO system_hidden_products(product_id,reason,hidden_at)
              VALUES(?,?,?) ON CONFLICT(product_id) DO UPDATE SET reason=excluded.reason,hidden_at=excluded.hidden_at""",
              (pid, "user_discontinued_2026-09-17", now))
            c.execute("UPDATE products SET active=0 WHERE id=?", (pid,))
    try:
        pv = importlib.import_module("product_visibility_v0995")
        pv.KNOWN_RETURN_OPTION_IDS = set(getattr(pv, "KNOWN_RETURN_OPTION_IDS", set())) - CURRENT_IDS
    except Exception:
        pass


def _alias_maps(core, db):
    with core._conn(db) as c:
        if not _exists(c, "return_discount_aliases"):
            return {}, {}
        rows = c.execute("""SELECT a.discount_option_id,a.parent_product_id,p.option_id parent_option_id
          FROM return_discount_aliases a LEFT JOIN products p ON p.id=a.parent_product_id""").fetchall()
        child = c.execute("""SELECT id,option_id FROM products
          WHERE option_id IN (SELECT discount_option_id FROM return_discount_aliases)""").fetchall()
    by_oid = {
        _oid(r["discount_option_id"]): (int(r["parent_product_id"]), _oid(r["parent_option_id"]))
        for r in rows if _oid(r["discount_option_id"]) not in CURRENT_IDS
    }
    by_pid = {int(r["id"]): by_oid[_oid(r["option_id"])] for r in child if _oid(r["option_id"]) in by_oid}
    return by_oid, by_pid


def _repair_existing_return_children_once(core, db):
    flag = "v0.9.214_canonical_return_repair"
    with core._conn(db) as c:
        c.execute("CREATE TABLE IF NOT EXISTS rg_patch_flags(patch_key TEXT PRIMARY KEY,applied_at TEXT NOT NULL)")
        if c.execute("SELECT 1 FROM rg_patch_flags WHERE patch_key=?", (flag,)).fetchone():
            return 0
    try:
        rd = importlib.import_module("return_discount_v099")
    except Exception:
        return 0
    aliases, _ = _alias_maps(core, db)
    repaired = 0
    try:
        products = rd._load_products(core, db)
        amount_col = rd._amount_column(core, db)
        for child in products:
            oid = _oid(child.get("option_id"))
            if not oid or oid in aliases or oid in CURRENT_IDS:
                continue
            try:
                if not rd._placeholder(child):
                    continue
            except Exception:
                continue
            with core._conn(db) as c:
                if amount_col:
                    rows = c.execute(
                        f'SELECT import_id,COALESCE(SUM(net_qty),0) qty,COALESCE(SUM("{amount_col}"),0) amount FROM sales_stats WHERE product_id=? GROUP BY import_id',
                        (int(child["id"]),),
                    ).fetchall()
                else:
                    rows = c.execute(
                        "SELECT import_id,COALESCE(SUM(net_qty),0) qty FROM sales_stats WHERE product_id=? GROUP BY import_id",
                        (int(child["id"]),),
                    ).fetchall()
            for r in rows:
                qty = float(r["qty"] or 0)
                if abs(qty) <= 1e-12:
                    continue
                parsed = [{
                    "option_id": oid,
                    "name": str(child.get("name") or ""),
                    "name_key": "",
                    "qty": qty,
                    "amount": float(r["amount"] or 0) if amount_col else None,
                    "amount_known": bool(amount_col),
                }]
                try:
                    mappings = rd._resolve(core, db, parsed)
                except Exception:
                    continue
                if oid not in mappings:
                    continue
                rd._post_discount(core, db, int(r["import_id"]), parsed, mappings)
                repaired += 1
    finally:
        with core._conn(db) as c:
            c.execute("INSERT OR REPLACE INTO rg_patch_flags(patch_key,applied_at) VALUES(?,?)", (flag, core.now_iso()))
    _sync_visibility(core, db)
    return repaired


def _patch_return_hide(core):
    try:
        rd = importlib.import_module("return_discount_v099")
    except Exception:
        return
    original = getattr(rd, "_hide_alias_rows", None)
    if not callable(original) or getattr(original, "_rg_user_registry_v09214", False):
        return
    def wrapped(core_obj, db, data):
        out = original(core_obj, db, data)
        if out is None or getattr(out, "empty", True):
            return out
        oidcol = "옵션ID" if "옵션ID" in out.columns else ("쿠팡 옵션ID" if "쿠팡 옵션ID" in out.columns else None)
        if oidcol:
            out = out.loc[~out[oidcol].map(_oid).isin(DISCONTINUED_IDS)].copy()
        return out
    wrapped._rg_user_registry_v09214 = True
    rd._hide_alias_rows = wrapped


def _canonical_frame(core, db, df):
    if df is None or getattr(df, "empty", True):
        return df
    out = df.copy()
    by_oid, by_pid = _alias_maps(core, db)
    with core._conn(db) as c:
        prows = c.execute("SELECT id,item_code,option_id,name FROM products").fetchall()
    pm = {int(r["id"]): dict(r) for r in prows}
    for idx in out.index:
        try:
            pid = int(float(out.at[idx, "product_id"])) if "product_id" in out.columns else 0
        except Exception:
            pid = 0
        oid = _oid(out.at[idx, "옵션ID"]) if "옵션ID" in out.columns else (_oid(out.at[idx, "option_id"]) if "option_id" in out.columns else "")
        target = by_oid.get(oid) or by_pid.get(pid)
        if target:
            pid, parent_oid = target
            p = pm.get(pid, {})
            if "product_id" in out.columns:
                out.at[idx, "product_id"] = pid
            if "옵션ID" in out.columns:
                out.at[idx, "옵션ID"] = parent_oid or _oid(p.get("option_id"))
            if "option_id" in out.columns:
                out.at[idx, "option_id"] = parent_oid or _oid(p.get("option_id"))
            if "상품명" in out.columns:
                out.at[idx, "상품명"] = str(p.get("name") or out.at[idx, "상품명"])
            if "상품코드" in out.columns:
                out.at[idx, "상품코드"] = str(p.get("item_code") or out.at[idx, "상품코드"])
        oid2 = _oid(out.at[idx, "옵션ID"]) if "옵션ID" in out.columns else (_oid(out.at[idx, "option_id"]) if "option_id" in out.columns else oid)
        if oid2 in DISCONTINUED_IDS:
            out.at[idx, "__hide214"] = 1
    if "__hide214" in out.columns:
        out = out.loc[out["__hide214"].fillna(0) != 1].drop(columns=["__hide214"])
    return out.reset_index(drop=True)


def _patch_sales_analysis():
    try:
        s = importlib.import_module("sales_analysis_v09186")
    except Exception:
        return False
    for name in ("_sales_stats", "_api_sales"):
        original = getattr(s, name, None)
        if not callable(original) or getattr(original, "_rg_user_registry_v09214", False):
            continue
        if name == "_sales_stats":
            def wrapped(core_obj, db, start, end, _o=original):
                df, covered = _o(core_obj, db, start, end)
                return _canonical_frame(core_obj, db, df), covered
        else:
            def wrapped(core_obj, db, start, end, allowed_days=None, _o=original):
                return _canonical_frame(core_obj, db, _o(core_obj, db, start, end, allowed_days))
        wrapped._rg_user_registry_v09214 = True
        setattr(s, name, wrapped)
    return True


def _patch_organic():
    try:
        o = importlib.import_module("organic_sales_estimate_v09211")
    except Exception:
        return False
    original_data = getattr(o, "_organic_estimate_data", None)
    original_identity = getattr(o, "_identity", None)
    if not callable(original_data) or not callable(original_identity) or getattr(original_data, "_rg_user_registry_v09214", False):
        return bool(callable(original_data))
    state = {}
    def ident(sm, master, opt2pid, pidv, oidv):
        key, pid, oid = original_identity(sm, master, opt2pid, pidv, oidv)
        target = state.get("oid", {}).get(_oid(oidv)) or state.get("pid", {}).get(int(pid or 0))
        if target:
            pid, parent_oid = target
            oid = parent_oid or _oid(master.get(int(pid), {}).get("옵션ID"))
            key = f"p:{int(pid)}"
        if _oid(oid) in DISCONTINUED_IDS:
            return "", 0, ""
        return key, int(pid or 0), oid
    def data(core_obj, sm, db, start, end):
        state["oid"], state["pid"] = _alias_maps(core_obj, db)
        old = o._identity
        o._identity = ident
        try:
            return original_data(core_obj, sm, db, start, end)
        finally:
            o._identity = old
            state.clear()
    data._rg_user_registry_v09214 = True
    o._organic_estimate_data = data
    return True


def apply(core, db=None):
    db = db or core.DEFAULT_DB
    core.init_db(db)
    _seed_registry(core, db)
    _sync_visibility(core, db)
    repaired = _repair_existing_return_children_once(core, db)
    _patch_return_hide(core)
    sales = _patch_sales_analysis()
    organic = _patch_organic()
    core.rg_current_normal_option_ids = lambda: set(CURRENT_IDS)
    core.rg_active_normal_option_ids = lambda: set(ACTIVE_IDS)
    core.rg_discontinued_normal_option_ids = lambda: set(DISCONTINUED_IDS)
    core.rg_canonicalize_sales_frame = lambda df, db_path=None: _canonical_frame(core, db_path or core.DEFAULT_DB, df)
    return {"ok": True, "active": len(ACTIVE_IDS), "discontinued": len(DISCONTINUED_IDS), "return_repaired": repaired, "sales_analysis": sales, "organic": organic}
