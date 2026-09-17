"""Organic sales estimate page for Sales Analysis (v0.9.212).

Organic estimate = uploaded total sales quantity - advertising report sales quantity.
Both sides are compared only on date coverage available in both uploaded sources.
The page is exposed as a real sidebar submenu under the Sales Analysis group.
"""
from __future__ import annotations

from datetime import date, timedelta
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
    """Keep only complete aggregate-report ranges supported by both sources."""
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


def _identity(sales_module, master: dict[int, dict[str, Any]], option_to_pid: dict[str, int], pid_value: Any, oid_value: Any):
    pid = int(sales_module._num(pid_value))
    oid = sales_module._oid(oid_value)
    if (pid <= 0 or pid not in master) and oid in option_to_pid:
        pid = int(option_to_pid[oid])
    if pid > 0:
        return f"p:{pid}", pid, oid
    if oid:
        return f"o:{oid}", 0, oid
    return "", 0, ""


def _sales_qty(sales_module, row: Any, cols: set[str]) -> float:
    if "sales_qty" in cols and row["sales_qty"] is not None:
        return max(0.0, sales_module._num(row["sales_qty"]))
    if "gross_qty" in cols and row["gross_qty"] is not None:
        return max(0.0, sales_module._num(row["gross_qty"]))
    net = sales_module._num(row["net_qty"]) if "net_qty" in cols else 0.0
    cancel = abs(sales_module._num(row["cancel_qty"])) if "cancel_qty" in cols else 0.0
    return max(0.0, net + cancel)


def _organic_estimate_data(core, sales_module, db, start: date, end: date):
    with core._conn(db) as con:
        master = sales_module._product_master(con)
        option_to_pid = {
            sales_module._oid(p.get("옵션ID")): int(pid)
            for pid, p in master.items()
            if sales_module._oid(p.get("옵션ID"))
        }

        sales_imports = _contained_imports(con, sales_module, "sales_stats", start, end)
        ad_imports = _contained_imports(con, sales_module, "ad_performance", start, end)
        sales_imports, ad_imports, covered = _aligned_imports(sales_imports, ad_imports)

        totals: dict[str, dict[str, Any]] = {}

        def get_item(key: str, pid: int, oid: str):
            p = master.get(pid, {}) if pid > 0 else {}
            name = str(p.get("상품명") or "").strip()
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
            cols = sales_module._cols(con, "sales_stats")
            if {"import_id", "product_id"}.issubset(cols):
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
                for row in rows:
                    key, pid, oid = _identity(sales_module, master, option_to_pid, row["product_id"], row["option_id"])
                    if not key:
                        continue
                    get_item(key, pid, oid)["판매량"] += _sales_qty(sales_module, row, cols)

        ad_qty_available = False
        if ad_imports and sales_module._exists(con, "ad_performance"):
            cols = sales_module._cols(con, "ad_performance")
            ad_qty_available = "sales_qty_14" in cols
            if ad_qty_available and "import_id" in cols:
                ids = [int(r["id"]) for r in ad_imports]
                marks = ",".join("?" for _ in ids)
                product_expr = "product_id" if "product_id" in cols else "0 AS product_id"
                option_expr = "option_id" if "option_id" in cols else "'' AS option_id"
                rows = con.execute(
                    f"""SELECT {product_expr},{option_expr},sales_qty_14
                        FROM ad_performance WHERE import_id IN ({marks})""",
                    ids,
                ).fetchall()
                for row in rows:
                    key, pid, oid = _identity(sales_module, master, option_to_pid, row["product_id"], row["option_id"])
                    if not key:
                        continue
                    get_item(key, pid, oid)["광고 판매량"] += max(0.0, sales_module._num(row["sales_qty_14"]))

    rows_out = []
    for item in totals.values():
        total_qty = float(item["판매량"])
        ad_qty = float(item["광고 판매량"])
        if total_qty <= 0 and ad_qty <= 0:
            continue
        rows_out.append({
            "아이템": item["아이템"],
            "판매량": total_qty,
            "광고 판매량": ad_qty,
            "Organic 판매량": total_qty - ad_qty,
        })

    if not rows_out:
        frame = pd.DataFrame(columns=["아이템", "판매량", "광고 판매량", "Organic 판매량"])
    else:
        frame = pd.DataFrame(rows_out).sort_values(
            ["판매량", "아이템"], ascending=[False, True], kind="stable"
        ).reset_index(drop=True)
    return frame, covered, ad_qty_available


def _fmt_count(value: Any):
    try:
        n = float(value or 0)
    except Exception:
        n = 0.0
    if abs(n - round(n)) < 1e-9:
        return int(round(n))
    return round(n, 2)


def render_page(st_obj, core, db_path=None):
    db = db_path or core.DEFAULT_DB
    core.init_db(db)
    st_obj.session_state["_rg_sales_stats_period_active"] = False

    st_obj.markdown("## 🌱 오가닉판매 추정")
    st_obj.caption("입력한 판매자료의 판매량에서 같은 기간 광고성과보고서의 광고 판매량을 빼 Organic 판매량을 추정합니다.")
    days = st_obj.radio(
        "기간",
        (30, 60, 90),
        index=0,
        horizontal=True,
        format_func=lambda n: f"최근 {n}일",
        key="organic_sales_period_v09212",
    )
    end = date.today()
    start = end - timedelta(days=int(days) - 1)
    st_obj.caption(f"조회기간: {start.isoformat()} ~ {end.isoformat()}")

    import sales_analysis_v09186 as sales_module
    frame, covered, ad_qty_available = _organic_estimate_data(core, sales_module, db, start, end)
    wanted = _period_days(start, end)

    if not ad_qty_available:
        st_obj.warning("선택 기간의 광고성과보고서에서 광고 판매량을 확인할 수 없습니다.")
    if len(covered) < len(wanted):
        st_obj.warning(
            f"선택한 최근 {int(days)}일 중 판매자료와 광고자료가 모두 있는 날짜는 {len(covered)}일입니다. "
            "표는 두 자료의 날짜가 함께 확인되는 입력 구간만 합산합니다."
        )
    else:
        st_obj.caption(f"판매자료와 광고자료가 최근 {int(days)}일 전체에 대해 확인됩니다.")

    if frame.empty:
        st_obj.info("선택 기간에 판매자료와 광고자료가 함께 확인되는 상품이 없습니다.")
        return

    if (pd.to_numeric(frame["Organic 판매량"], errors="coerce").fillna(0) < 0).any():
        st_obj.warning("Organic 판매량이 음수인 상품이 있습니다. 판매자료와 광고보고서의 상품 매칭 또는 입력기간을 확인해 주세요.")

    show = frame[["아이템", "판매량", "광고 판매량", "Organic 판매량"]].copy()
    for col in ("판매량", "광고 판매량", "Organic 판매량"):
        show[col] = show[col].map(_fmt_count)
    st_obj.dataframe(
        show,
        use_container_width=True,
        hide_index=True,
        height=min(760, max(220, 38 * (len(show) + 1))),
    )


def apply(sales_module, core):
    """Install sidebar routing without changing the existing sales-analysis page body."""
    if sales_module is None:
        return {"ok": False, "reason": "sales module missing"}
    if getattr(sales_module, "_rg_organic_sidebar_route_v09212_installed", False):
        return {"ok": True, "already_applied": True, "sidebar_page": PAGE_TEXT}

    base_installer = getattr(sales_module, "_install_sidebar_route", None)
    sales_label = str(getattr(sales_module, "PAGE_TEXT", "📊  판매분석"))
    organic_label = PAGE_TEXT
    sales_title = "📊 판매분석"

    def install_sidebar_route():
        sidebar = sys.modules.get("sidebar_groups_v0917")
        if sidebar is not None:
            current_render = getattr(sidebar, "render_sidebar", None)
            if callable(current_render) and getattr(current_render, "_rg_organic_sidebar_route_v09212", False):
                return

        if callable(base_installer):
            base_installer()

        sidebar = sys.modules.get("sidebar_groups_v0917")
        if sidebar is None:
            return

        groups = getattr(sidebar, "_GROUPS", None)
        if isinstance(groups, list):
            for title, items in groups:
                if organic_label in items and str(title) != sales_title:
                    items[:] = [x for x in items if x != organic_label]
            target = None
            for title, items in groups:
                if str(title) == sales_title:
                    target = items
                    break
            if target is None:
                target = [sales_label, organic_label]
                groups.insert(1 if groups else 0, (sales_title, target))
            else:
                if sales_label not in target:
                    target.append(sales_label)
                if organic_label not in target:
                    target.append(organic_label)

        original = getattr(sidebar, "render_sidebar", None)
        if not callable(original) or getattr(original, "_rg_organic_sidebar_route_v09212", False):
            return

        def wrapped(st_obj, options, default_page=None):
            runtime_options = [str(x) for x in list(options or [])]
            if organic_label not in runtime_options:
                runtime_options.append(organic_label)
            current = original(st_obj, runtime_options, default_page)
            if current == organic_label:
                try:
                    render_page(st_obj, core)
                except Exception as exc:
                    st_obj.error(f"오가닉판매 추정 화면을 여는 중 오류가 발생했습니다: {exc}")
                return "__RG_ORGANIC_SALES_RENDERED__"
            return current

        wrapped._rg_organic_sidebar_route_v09212 = True
        sidebar.render_sidebar = wrapped

    sales_module._install_sidebar_route = install_sidebar_route
    sales_module._rg_organic_sidebar_route_v09212_installed = True
    return {"ok": True, "already_applied": False, "sidebar_page": PAGE_TEXT}
