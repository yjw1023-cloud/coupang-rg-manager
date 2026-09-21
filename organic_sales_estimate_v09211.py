"""Organic sales estimate page for Sales Analysis (v0.9.231).

Authoritative normal-product source: the user's uploaded product master copied to
canonical_product_rules_v09214.CURRENT_IDS (132 option IDs). The DB registry is
not used to decide normal vs return.
"""
from __future__ import annotations

from datetime import date, timedelta
from difflib import SequenceMatcher
import importlib
import sys
from typing import Any

import pandas as pd


PAGE_TEXT = "🌱  오가닉판매 추정"
PAGE_LABEL = PAGE_TEXT


def _period_days(start: date, end: date) -> set[str]:
    out: set[str] = set()
    cur = start
    while cur <= end:
        out.add(cur.isoformat())
        cur += timedelta(days=1)
    return out


def _import_days(row: Any) -> set[str]:
    try:
        start = date.fromisoformat(str(row["period_start"] or "")[:10])
        end = date.fromisoformat(str(row["period_end"] or "")[:10])
    except Exception:
        return set()
    if end < start:
        start, end = end, start
    return _period_days(start, end)


def _contained_imports(con, sales_module, data_type: str, start: date, end: date) -> list[Any]:
    if not sales_module._exists(con, "imports"):
        return []
    cols = sales_module._cols(con, "imports")
    if not {"id", "data_type", "period_start", "period_end"}.issubset(cols):
        return []
    return list(con.execute(
        """SELECT id,period_start,period_end FROM imports
           WHERE data_type=? AND period_start>=? AND period_end<=?
           ORDER BY period_start,period_end,id""",
        (str(data_type), start.isoformat(), end.isoformat()),
    ).fetchall())


def _coverage(imports: list[Any]) -> set[str]:
    out: set[str] = set()
    for row in imports:
        out |= _import_days(row)
    return out


def _aligned_imports(sales_imports: list[Any], ad_imports: list[Any]) -> tuple[list[Any], list[Any], set[str]]:
    sales = list(sales_imports)
    ads = list(ad_imports)
    for _ in range(8):
        common = _coverage(sales) & _coverage(ads)
        new_sales = [r for r in sales if _import_days(r) and _import_days(r).issubset(common)]
        new_ads = [r for r in ads if _import_days(r) and _import_days(r).issubset(common)]
        if [int(r["id"]) for r in new_sales] == [int(r["id"]) for r in sales] and [int(r["id"]) for r in new_ads] == [int(r["id"]) for r in ads]:
            sales, ads = new_sales, new_ads
            break
        sales, ads = new_sales, new_ads
    return sales, ads, _coverage(sales) & _coverage(ads)


def _oid(sales_module, value: Any) -> str:
    try:
        return str(sales_module._oid(value) or "").strip()
    except Exception:
        if value is None:
            return ""
        s = str(value).strip()
        if s.upper().startswith("CP-"):
            s = s[3:]
        return s[:-2] if s.endswith(".0") and s[:-2].isdigit() else s


def _table_exists(con, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _normal_ids(con, sales_module) -> set[str]:
    """Normal products come directly from the uploaded-master constants, never DB registry."""
    rules = importlib.import_module("canonical_product_rules_v09214")
    return {
        _oid(sales_module, oid)
        for oid in set(getattr(rules, "CURRENT_IDS", set()))
        if _oid(sales_module, oid)
    }


def _confirmed_method(method: Any) -> bool:
    m = str(method or "").strip().lower()
    return (
        m.startswith("manual")
        or m.startswith("explicit_user")
        or m in {"user_confirmed", "manual_user_shared", "manual_user"}
    )


def _product_rows(con) -> list[dict[str, Any]]:
    if not _table_exists(con, "products"):
        return []
    return [
        dict(r)
        for r in con.execute(
            """SELECT id,item_code,option_id,name,item_type,unit_cost,active
               FROM products"""
        ).fetchall()
    ]


def _canonical_normal_products(con, sales_module, normal_ids: set[str]):
    grouped: dict[str, list[dict[str, Any]]] = {}
    for p in _product_rows(con):
        oid = _oid(sales_module, p.get("option_id"))
        if oid in normal_ids:
            p["option_id"] = oid
            grouped.setdefault(oid, []).append(p)

    def score(p: dict[str, Any]):
        oid = _oid(sales_module, p.get("option_id"))
        code = _oid(sales_module, p.get("item_code"))
        try:
            unit_cost = float(p.get("unit_cost") or 0)
        except Exception:
            unit_cost = 0.0
        return (
            1 if str(p.get("item_type") or "") == "finished" else 0,
            1 if code != oid else 0,
            1 if unit_cost > 0 else 0,
            1 if int(p.get("active") or 0) else 0,
            -int(p.get("id") or 0),
        )

    by_oid: dict[str, int] = {}
    products: list[dict[str, Any]] = []
    for oid, rows in grouped.items():
        rep = max(rows, key=score)
        by_oid[oid] = int(rep["id"])
        products.append(rep)
    return by_oid, products


def _return_state(con, sales_module, normal_by_oid: dict[str, int]):
    confirmed: dict[str, tuple[int, str]] = {}
    suggested: dict[str, int] = {}

    if not _table_exists(con, "return_discount_aliases"):
        return confirmed, suggested

    rows = con.execute(
        """SELECT a.discount_option_id,a.parent_product_id,a.match_method,
                  p.option_id AS parent_option_id
           FROM return_discount_aliases a
           LEFT JOIN products p ON p.id=a.parent_product_id"""
    ).fetchall()
    for r in rows:
        child_oid = _oid(sales_module, r["discount_option_id"])
        parent_oid = _oid(sales_module, r["parent_option_id"])
        if not child_oid:
            continue
        try:
            parent_pid = int(r["parent_product_id"] or 0)
        except Exception:
            parent_pid = 0
        if parent_oid in normal_by_oid:
            parent_pid = int(normal_by_oid[parent_oid])
        if parent_pid <= 0:
            continue
        if _confirmed_method(r["match_method"]):
            confirmed[child_oid] = (parent_pid, parent_oid)
        else:
            suggested[child_oid] = parent_pid
    return confirmed, suggested


def _sales_qty(sales_module, row: Any, cols: set[str]) -> float:
    if "sales_qty" in cols and row["sales_qty"] is not None:
        return max(0.0, sales_module._num(row["sales_qty"]))
    if "gross_qty" in cols and row["gross_qty"] is not None:
        return max(0.0, sales_module._num(row["gross_qty"]))
    net = sales_module._num(row["net_qty"]) if "net_qty" in cols else 0.0
    cancel = abs(sales_module._num(row["cancel_qty"])) if "cancel_qty" in cols else 0.0
    return max(0.0, net + cancel)


def _pending_return_options(core, sales_module, db, sales_imports):
    if not sales_imports:
        return [], []
    with core._conn(db) as con:
        normal_ids = _normal_ids(con, sales_module)
        normal_by_oid, normal_products = _canonical_normal_products(
            con, sales_module, normal_ids
        )
        confirmed, suggested = _return_state(con, sales_module, normal_by_oid)
        if not _table_exists(con, "sales_stats"):
            return [], normal_products

        cols = set(sales_module._cols(con, "sales_stats"))
        ids = [int(r["id"]) for r in sales_imports]
        marks = ",".join("?" for _ in ids)
        fields = ["product_id"]
        fields.append("option_id" if "option_id" in cols else "'' AS option_id")
        for col in ("sales_qty", "gross_qty", "net_qty", "cancel_qty"):
            fields.append(col if col in cols else f"NULL AS {col}")
        rows = con.execute(
            f"SELECT {','.join(fields)} FROM sales_stats WHERE import_id IN ({marks})",
            ids,
        ).fetchall()
        name_by_pid = {
            int(r["id"]): str(r["name"] or "")
            for r in con.execute("SELECT id,name FROM products").fetchall()
        } if _table_exists(con, "products") else {}

    pending: dict[str, dict[str, Any]] = {}
    for row in rows:
        oid = _oid(sales_module, row["option_id"])
        if not oid or oid in normal_ids or oid in confirmed:
            continue
        qty = _sales_qty(sales_module, row, cols)
        if qty <= 0:
            continue
        try:
            pid = int(row["product_id"] or 0)
        except Exception:
            pid = 0
        item = pending.setdefault(oid, {
            "option_id": oid,
            "name": name_by_pid.get(pid) or f"옵션ID {oid}",
            "qty": 0.0,
            "suggested_parent_id": suggested.get(oid),
        })
        item["qty"] += float(qty)
    return list(pending.values()), normal_products


def _candidate_score(item: dict[str, Any], p: dict[str, Any]) -> float:
    a = str(item.get("name") or "").lower().strip()
    b = str(p.get("name") or "").lower().strip()
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def _save_user_alias(core, sales_module, db, item: dict[str, Any], parent_pid: int):
    child_oid = _oid(sales_module, item.get("option_id"))
    child_name = str(item.get("name") or "")
    now = core.now_iso()
    with core._conn(db) as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS return_discount_aliases(
               discount_option_id TEXT PRIMARY KEY,
               parent_product_id INTEGER NOT NULL,
               discount_name TEXT,
               match_method TEXT NOT NULL,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL)"""
        )
        con.execute(
            """INSERT INTO return_discount_aliases
               (discount_option_id,parent_product_id,discount_name,match_method,created_at,updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(discount_option_id) DO UPDATE SET
                 parent_product_id=excluded.parent_product_id,
                 discount_name=excluded.discount_name,
                 match_method='manual_user_shared',
                 updated_at=excluded.updated_at""",
            (
                child_oid,
                int(parent_pid),
                child_name,
                "manual_user_shared",
                now,
                now,
            ),
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS return_alias_user_confirmations(
               discount_option_id TEXT PRIMARY KEY,
               parent_product_id INTEGER NOT NULL,
               confirmed_at TEXT NOT NULL)"""
        )
        con.execute(
            """INSERT INTO return_alias_user_confirmations
               (discount_option_id,parent_product_id,confirmed_at)
               VALUES(?,?,?)
               ON CONFLICT(discount_option_id) DO UPDATE SET
                 parent_product_id=excluded.parent_product_id,
                 confirmed_at=excluded.confirmed_at""",
            (child_oid, int(parent_pid), now),
        )
        con.execute(
            """CREATE TABLE IF NOT EXISTS system_hidden_products(
               product_id INTEGER PRIMARY KEY,
               reason TEXT NOT NULL,
               hidden_at TEXT NOT NULL)"""
        )
        if _table_exists(con, "products"):
            for r in con.execute(
                "SELECT id FROM products WHERE CAST(option_id AS TEXT)=?",
                (child_oid,),
            ).fetchall():
                child_pid = int(r["id"])
                if child_pid == int(parent_pid):
                    continue
                con.execute(
                    """INSERT INTO system_hidden_products(product_id,reason,hidden_at)
                       VALUES(?,?,?)
                       ON CONFLICT(product_id) DO UPDATE SET
                         reason=excluded.reason,hidden_at=excluded.hidden_at""",
                    (child_pid, "return_alias_manual_organic_v09226", now),
                )
                con.execute("UPDATE products SET active=0 WHERE id=?", (child_pid,))


def _require_return_confirmation(core, sales_module, db, sales_imports):
    pending, products = _pending_return_options(
        core, sales_module, db, sales_imports
    )
    if not pending:
        return
    import streamlit as st

    if not products:
        st.error("정상 원장상품을 불러오지 못해 반품상품 매칭을 진행할 수 없습니다.")
        st.stop()

    item = pending[0]
    ordered = sorted(
        products,
        key=lambda p: _candidate_score(item, p),
        reverse=True,
    )
    suggested = int(item.get("suggested_parent_id") or 0)
    if suggested:
        ordered.sort(key=lambda p: 0 if int(p["id"]) == suggested else 1)

    ids = [int(p["id"]) for p in ordered]
    by_id = {int(p["id"]): p for p in ordered}
    st.warning(
        "원장에 없는 판매 옵션을 발견했습니다. "
        "이 반품상품의 원상품을 확인해야 집계를 계속할 수 있습니다."
    )
    with st.container(border=True):
        st.markdown(f"**반품 매칭 확인: {item['name']}**")
        note = " · 기존 자동추천값" if suggested else ""
        st.caption(
            f"옵션ID {item['option_id']} · 판매수량 {item['qty']:g}{note} "
            f"· 미확정 {len(pending)}건 중 1건"
        )
        selected = st.selectbox(
            "이 반품상품의 원상품",
            ids,
            format_func=lambda pid: (
                f"{by_id[int(pid)].get('name','')} · "
                f"옵션ID {by_id[int(pid)].get('option_id','')}"
                + (
                    " · 판매중단/보관"
                    if not int(by_id[int(pid)].get("active") or 0)
                    else ""
                )
            ),
            key=f"_organic_return_confirm_v09226_{item['option_id']}",
        )
        if st.button(
            "이 원상품으로 확정",
            type="primary",
            key=f"_organic_return_save_v09226_{item['option_id']}",
        ):
            _save_user_alias(
                core, sales_module, db, item, int(selected)
            )
            st.rerun()
    st.stop()


def _identity_maps(con, sales_module):
    normal_ids = _normal_ids(con, sales_module)
    normal_by_oid, _ = _canonical_normal_products(
        con, sales_module, normal_ids
    )
    confirmed, _ = _return_state(con, sales_module, normal_by_oid)
    return normal_ids, normal_by_oid, confirmed


def _canonical_identity(
    sales_module,
    normal_ids: set[str],
    normal_by_oid: dict[str, int],
    confirmed: dict[str, tuple[int, str]],
    pid_value: Any,
    oid_value: Any,
):
    oid = _oid(sales_module, oid_value)
    if oid in confirmed:
        parent_pid, parent_oid = confirmed[oid]
        return f"p:{int(parent_pid)}", int(parent_pid), parent_oid or oid
    if oid in normal_ids and oid in normal_by_oid:
        pid = int(normal_by_oid[oid])
        return f"p:{pid}", pid, oid
    try:
        pid = int(sales_module._num(pid_value))
    except Exception:
        pid = 0
    if pid > 0:
        return f"p:{pid}", pid, oid
    if oid:
        return f"o:{oid}", 0, oid
    return "", 0, ""


def _organic_estimate_data(core, sales_module, db, start: date, end: date):
    with core._conn(db) as con:
        sales_imports = _contained_imports(
            con, sales_module, "sales_stats", start, end
        )
        ad_imports = _contained_imports(
            con, sales_module, "ad_performance", start, end
        )
        sales_imports, ad_imports, covered = _aligned_imports(
            sales_imports, ad_imports
        )

    _require_return_confirmation(
        core, sales_module, db, sales_imports
    )

    with core._conn(db) as con:
        master = sales_module._product_master(con)
        product_names = {
            int(r["id"]): str(r["name"] or "")
            for r in con.execute("SELECT id,name FROM products").fetchall()
        } if _table_exists(con, "products") else {}
        normal_ids, normal_by_oid, confirmed = _identity_maps(
            con, sales_module
        )
        totals: dict[str, dict[str, Any]] = {}

        def get_item(key: str, pid: int, oid: str):
            p = master.get(pid, {}) if pid > 0 else {}
            name = str(p.get("상품명") or "").strip()
            if not name and pid > 0:
                name = product_names.get(pid, "")
            if not name:
                name = f"옵션ID {oid}" if oid else "미매칭 상품"
            return totals.setdefault(key, {
                "product_id": pid,
                "option_id": oid,
                "아이템": name,
                "판매량": 0.0,
                "광고 판매량": 0.0,
            })

        if sales_imports and sales_module._exists(con, "sales_stats"):
            cols = set(sales_module._cols(con, "sales_stats"))
            if "import_id" in cols:
                ids = [int(r["id"]) for r in sales_imports]
                marks = ",".join("?" for _ in ids)
                fields = [
                    "product_id" if "product_id" in cols else "0 AS product_id",
                    "option_id" if "option_id" in cols else "'' AS option_id",
                ]
                for col in ("sales_qty", "gross_qty", "net_qty", "cancel_qty"):
                    fields.append(
                        col if col in cols else f"NULL AS {col}"
                    )
                rows = con.execute(
                    f"SELECT {','.join(fields)} "
                    f"FROM sales_stats WHERE import_id IN ({marks})",
                    ids,
                ).fetchall()
                for row in rows:
                    key, pid, oid = _canonical_identity(
                        sales_module,
                        normal_ids,
                        normal_by_oid,
                        confirmed,
                        row["product_id"],
                        row["option_id"],
                    )
                    if key:
                        get_item(key, pid, oid)["판매량"] += _sales_qty(
                            sales_module, row, cols
                        )

        ad_qty_available = False
        if ad_imports and sales_module._exists(con, "ad_performance"):
            cols = set(sales_module._cols(con, "ad_performance"))
            ad_qty_available = "sales_qty_14" in cols
            if ad_qty_available and "import_id" in cols:
                ids = [int(r["id"]) for r in ad_imports]
                marks = ",".join("?" for _ in ids)
                product_expr = (
                    "product_id"
                    if "product_id" in cols
                    else "0 AS product_id"
                )
                option_expr = (
                    "option_id"
                    if "option_id" in cols
                    else "'' AS option_id"
                )
                rows = con.execute(
                    f"""SELECT {product_expr},{option_expr},sales_qty_14
                        FROM ad_performance
                        WHERE import_id IN ({marks})""",
                    ids,
                ).fetchall()
                for row in rows:
                    key, pid, oid = _canonical_identity(
                        sales_module,
                        normal_ids,
                        normal_by_oid,
                        confirmed,
                        row["product_id"],
                        row["option_id"],
                    )
                    if key:
                        get_item(key, pid, oid)[
                            "광고 판매량"
                        ] += max(
                            0.0,
                            sales_module._num(row["sales_qty_14"]),
                        )

    rows_out = []
    for item in totals.values():
        total_qty = float(item["판매량"])
        ad_qty = float(item["광고 판매량"])
        if total_qty <= 0 and ad_qty <= 0:
            continue
        organic_qty = total_qty - ad_qty
        rows_out.append({
            "아이템": item["아이템"],
            "판매량": total_qty,
            "광고 판매량": ad_qty,
            "Organic 판매량": organic_qty,
            "Organic 판매 비율": (
                organic_qty / total_qty * 100.0
                if total_qty > 0
                else 0.0
            ),
        })

    columns = [
        "아이템",
        "판매량",
        "광고 판매량",
        "Organic 판매량",
        "Organic 판매 비율",
    ]
    if not rows_out:
        frame = pd.DataFrame(columns=columns)
    else:
        frame = pd.DataFrame(
            rows_out, columns=columns
        ).sort_values(
            ["판매량", "아이템"],
            ascending=[False, True],
            kind="stable",
        ).reset_index(drop=True)
    return frame, covered, ad_qty_available


def _fmt_count(value: Any) -> str:
    try:
        n = float(value or 0)
    except Exception:
        n = 0.0
    if abs(n - round(n)) < 1e-9:
        return f"{int(round(n)):,}"
    return f"{n:,.2f}"


def _style_table(frame: pd.DataFrame):
    numeric_cols = [
        "판매량",
        "광고 판매량",
        "Organic 판매량",
        "Organic 판매 비율",
    ]
    styler = frame.style.format({
        "판매량": _fmt_count,
        "광고 판매량": _fmt_count,
        "Organic 판매량": _fmt_count,
        "Organic 판매 비율": lambda v: f"{float(v):.1f}%",
    })
    styler = styler.set_properties(
        subset=numeric_cols,
        **{"text-align": "center", "vertical-align": "middle"},
    )
    styler = styler.set_properties(
        subset=["아이템"],
        **{"text-align": "left", "vertical-align": "middle"},
    )
    styler = styler.set_properties(
        subset=["Organic 판매량", "Organic 판매 비율"],
        **{"background-color": "#f2fbf5", "font-weight": "650"},
    )
    styler = styler.set_table_styles([
        {
            "selector": "th",
            "props": [
                ("text-align", "center"),
                ("vertical-align", "middle"),
                ("font-weight", "700"),
                ("background-color", "#eef3f8"),
                ("color", "#17324d"),
                ("border-bottom", "1px solid #cfd8e3"),
            ],
        },
        {
            "selector": "td",
            "props": [
                ("padding", "8px 10px"),
                ("border-bottom", "1px solid #e7ecf2"),
            ],
        },
    ])
    return styler


def render_page(st_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    st_obj.session_state["_rg_sales_stats_period_active"] = False

    st_obj.markdown("## 🌱 오가닉판매 추정")
    st_obj.caption(
        "입력한 판매자료의 판매량에서 같은 기간 광고성과보고서의 "
        "광고 판매량을 빼 Organic 판매량을 추정합니다."
    )
    days = st_obj.radio(
        "기간",
        (7, 15, 30, 60, 90),
        index=2,
        horizontal=True,
        format_func=lambda n: f"최근 {n}일",
        key="organic_sales_period_v09212",
    )
    end = date.today()
    start = end - timedelta(days=int(days) - 1)
    st_obj.caption(
        f"조회기간: {start.isoformat()} ~ {end.isoformat()}"
    )

    import sales_analysis_v09186 as sales_module
    frame, covered, ad_qty_available = _organic_estimate_data(
        core, sales_module, db, start, end
    )
    wanted = _period_days(start, end)

    if not ad_qty_available:
        st_obj.warning(
            "선택 기간의 광고성과보고서에서 광고 판매량을 확인할 수 없습니다."
        )
    if len(covered) < len(wanted):
        st_obj.warning(
            f"선택한 최근 {int(days)}일 중 판매자료와 광고자료가 "
            f"모두 있는 날짜는 {len(covered)}일입니다. "
            "표는 두 자료의 날짜가 함께 확인되는 입력 구간만 합산합니다."
        )
    else:
        st_obj.caption(
            f"판매자료와 광고자료가 최근 {int(days)}일 전체에 대해 확인됩니다."
        )

    if frame.empty:
        st_obj.info(
            "선택 기간에 판매자료와 광고자료가 함께 확인되는 상품이 없습니다."
        )
        return

    if (
        pd.to_numeric(
            frame["Organic 판매량"], errors="coerce"
        ).fillna(0) < 0
    ).any():
        st_obj.warning(
            "Organic 판매량이 음수인 상품이 있습니다. "
            "판매자료와 광고보고서의 상품 매칭 또는 입력기간을 확인해 주세요."
        )

    show = frame[
        [
            "아이템",
            "판매량",
            "광고 판매량",
            "Organic 판매량",
            "Organic 판매 비율",
        ]
    ].copy()
    styled = _style_table(show)
    column_config = None
    try:
        column_config = {
            "아이템": st_obj.column_config.TextColumn(
                "아이템", width="large"
            ),
            "판매량": st_obj.column_config.NumberColumn(
                "판매량", width="small"
            ),
            "광고 판매량": st_obj.column_config.NumberColumn(
                "광고 판매량", width="small"
            ),
            "Organic 판매량": st_obj.column_config.NumberColumn(
                "Organic 판매량", width="small"
            ),
            "Organic 판매 비율": st_obj.column_config.NumberColumn(
                "Organic 판매 비율", width="small"
            ),
        }
    except Exception:
        column_config = None

    kwargs = {
        "use_container_width": True,
        "hide_index": True,
        "height": min(760, max(220, 38 * (len(show) + 1))),
    }
    if column_config is not None:
        kwargs["column_config"] = column_config
    st_obj.dataframe(styled, **kwargs)


def apply(sales_module, core):
    """Install sidebar routing without changing the existing sales-analysis page body."""
    if sales_module is None:
        return {"ok": False, "reason": "sales module missing"}
    if getattr(
        sales_module,
        "_rg_organic_sidebar_route_v09231_installed",
        False,
    ):
        return {
            "ok": True,
            "already_applied": True,
            "sidebar_page": PAGE_TEXT,
        }

    base_installer = getattr(
        sales_module, "_install_sidebar_route", None
    )
    sales_label = str(
        getattr(sales_module, "PAGE_TEXT", "📊  판매분석")
    )
    organic_label = PAGE_TEXT
    sales_title = "📊 판매분석"

    def install_sidebar_route():
        sidebar = sys.modules.get("sidebar_groups_v0917")
        if sidebar is not None:
            current_render = getattr(
                sidebar, "render_sidebar", None
            )
            if (
                callable(current_render)
                and getattr(
                    current_render,
                    "_rg_organic_sidebar_route_v09231",
                    False,
                )
            ):
                return

        if callable(base_installer):
            base_installer()

        sidebar = sys.modules.get("sidebar_groups_v0917")
        if sidebar is None:
            return

        groups = getattr(sidebar, "_GROUPS", None)
        if isinstance(groups, list):
            for title, items in groups:
                if (
                    organic_label in items
                    and str(title) != sales_title
                ):
                    items[:] = [
                        x for x in items if x != organic_label
                    ]
            target = None
            for title, items in groups:
                if str(title) == sales_title:
                    target = items
                    break
            if target is None:
                target = [sales_label, organic_label]
                groups.insert(
                    1 if groups else 0,
                    (sales_title, target),
                )
            else:
                if sales_label not in target:
                    target.append(sales_label)
                if organic_label not in target:
                    target.append(organic_label)

        original = getattr(sidebar, "render_sidebar", None)
        if (
            not callable(original)
            or getattr(
                original,
                "_rg_organic_sidebar_route_v09231",
                False,
            )
        ):
            return

        def wrapped(st_obj, options, default_page=None):
            runtime_options = [
                str(x) for x in list(options or [])
            ]
            if organic_label not in runtime_options:
                runtime_options.append(organic_label)
            current = original(
                st_obj, runtime_options, default_page
            )
            if current == organic_label:
                try:
                    render_page(st_obj, core)
                except Exception as exc:
                    st_obj.error(
                        f"오가닉판매 추정 화면을 여는 중 오류가 발생했습니다: {exc}"
                    )
                return "__RG_ORGANIC_SALES_RENDERED__"
            return current

        wrapped._rg_organic_sidebar_route_v09231 = True
        sidebar.render_sidebar = wrapped

    sales_module._install_sidebar_route = install_sidebar_route
    sales_module._rg_organic_sidebar_route_v09231_installed = True
    return {
        "ok": True,
        "already_applied": False,
        "sidebar_page": PAGE_TEXT,
        "core_return_gate": "v0.9.226",
    }