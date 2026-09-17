"""v0.9.223 Organic sales page.

This page does not rely on the old product_id/monkey-patch chain. It first forces
explicit confirmation for every non-master sales option, then aggregates raw sales
and ad rows by the confirmed original product before rendering.
"""
from __future__ import annotations

from datetime import date, timedelta
import html
import importlib
import pandas as pd


def _period_days(start: date, end: date) -> set[str]:
    out = set(); cur = start
    while cur <= end:
        out.add(cur.isoformat()); cur += timedelta(days=1)
    return out


def _count(v) -> str:
    try: n = float(v or 0)
    except Exception: n = 0.0
    return f"{int(round(n)):,}" if abs(n-round(n)) < 1e-9 else f"{n:,.2f}"


def _table_html(frame: pd.DataFrame) -> str:
    rows = []
    for _, r in frame.iterrows():
        name = html.escape(str(r.get("아이템") or ""))
        total = _count(r.get("판매량")); ad = _count(r.get("광고 판매량")); organic = _count(r.get("Organic 판매량"))
        try: ratio = float(r.get("Organic 판매 비율") or 0)
        except Exception: ratio = 0.0
        rows.append(f"<tr><td class='item'>{name}</td><td class='num'>{total}</td><td class='num'>{ad}</td><td class='num organic'>{organic}</td><td class='num ratio'>{ratio:.1f}%</td></tr>")
    body = "".join(rows)
    return f"""
<style>
.rg-organic-wrap{{border:1px solid #dfe6ee;border-radius:12px;overflow:auto;max-height:760px;background:#fff}}
.rg-organic-table{{width:100%;border-collapse:separate;border-spacing:0;font-size:14px;color:#172033;table-layout:fixed}}
.rg-organic-table thead th{{position:sticky;top:0;z-index:2;background:#edf3f8!important;color:#334155;font-weight:750;text-align:center!important;vertical-align:middle!important;padding:11px 10px;border-bottom:1px solid #cbd5e1;border-right:1px solid #dde5ed;white-space:nowrap}}
.rg-organic-table tbody td{{text-align:center!important;vertical-align:middle!important;padding:10px 10px;border-bottom:1px solid #e7edf3;border-right:1px solid #edf1f5;font-variant-numeric:tabular-nums}}
.rg-organic-table thead th:first-child,.rg-organic-table tbody td.item{{width:44%;text-align:left!important}}
.rg-organic-table thead th:nth-child(2),.rg-organic-table thead th:nth-child(3),.rg-organic-table thead th:nth-child(4){{width:13%}}
.rg-organic-table thead th:nth-child(5){{width:17%}}
.rg-organic-table tbody td.organic,.rg-organic-table tbody td.ratio{{background:#f2fbf5!important;font-weight:700;color:#176b3a}}
.rg-organic-table tbody tr:hover td{{background:#f8fafc!important}}
.rg-organic-table tbody tr:hover td.organic,.rg-organic-table tbody tr:hover td.ratio{{background:#eaf8ef!important}}
.rg-organic-table thead th:last-child,.rg-organic-table tbody td:last-child{{border-right:none}}
</style>
<div class="rg-organic-wrap"><table class="rg-organic-table"><thead><tr><th>아이템</th><th>판매량</th><th>광고 판매량</th><th>Organic 판매량</th><th>Organic 판매 비율</th></tr></thead><tbody>{body}</tbody></table></div>
"""


def _num(sales_module, v) -> float:
    try: return float(sales_module._num(v))
    except Exception:
        try: return float(v or 0)
        except Exception: return 0.0


def _sales_qty(row, cols, sales_module) -> float:
    if "sales_qty" in cols and row["sales_qty"] is not None:
        v = _num(sales_module,row["sales_qty"])
        if abs(v) > 1e-12:
            return max(0.0,v)
    if "gross_qty" in cols and row["gross_qty"] is not None:
        v = _num(sales_module,row["gross_qty"])
        if abs(v) > 1e-12:
            return max(0.0,v)
    net = _num(sales_module,row["net_qty"]) if "net_qty" in cols else 0.0
    cancel = abs(_num(sales_module,row["cancel_qty"])) if "cancel_qty" in cols else 0.0
    return max(0.0,net + cancel)


def _canonical_data(core, sales_module, source_module, db, start, end):
    matcher = importlib.import_module("shared_return_match_ui_v09217")
    confirmed = matcher.confirmed_alias_map(core, db)
    normal_map = matcher.normal_option_to_product(core, db)

    with core._conn(db) as con:
        product_rows = con.execute("SELECT id,name,option_id FROM products").fetchall()
        products = {int(r["id"]): {"name":str(r["name"] or ""),"option_id":matcher._oid(r["option_id"])} for r in product_rows}

        sales_imports = source_module._contained_imports(con,sales_module,"sales_stats",start,end)
        ad_imports = source_module._contained_imports(con,sales_module,"ad_performance",start,end)
        sales_imports, ad_imports, covered = source_module._aligned_imports(sales_imports,ad_imports)

        totals = {}

        def resolve(oid_value):
            oid = matcher._oid(oid_value)
            pid = int(confirmed.get(oid) or normal_map.get(oid) or 0)
            if pid <= 0:
                return "",0,""
            p = products.get(pid,{})
            name = str(p.get("name") or f"옵션ID {oid}")
            return f"p:{pid}",pid,name

        def item(key,pid,name):
            return totals.setdefault(key,{"product_id":pid,"아이템":name,"판매량":0.0,"광고 판매량":0.0})

        if sales_imports and sales_module._exists(con,"sales_stats"):
            cols = sales_module._cols(con,"sales_stats")
            ids = [int(r["id"]) for r in sales_imports]
            marks = ",".join("?" for _ in ids)
            fields = ["option_id" if "option_id" in cols else "'' AS option_id"]
            for col in ("sales_qty","gross_qty","net_qty","cancel_qty"):
                fields.append(col if col in cols else f"NULL AS {col}")
            rows = con.execute(f"SELECT {','.join(fields)} FROM sales_stats WHERE import_id IN ({marks})",ids).fetchall()
            for row in rows:
                key,pid,name = resolve(row["option_id"])
                if key:
                    item(key,pid,name)["판매량"] += _sales_qty(row,cols,sales_module)

        ad_qty_available = False
        if ad_imports and sales_module._exists(con,"ad_performance"):
            cols = sales_module._cols(con,"ad_performance")
            ad_qty_available = "sales_qty_14" in cols
            if ad_qty_available and "import_id" in cols:
                ids = [int(r["id"]) for r in ad_imports]
                marks = ",".join("?" for _ in ids)
                option_expr = "option_id" if "option_id" in cols else "'' AS option_id"
                rows = con.execute(f"SELECT {option_expr},sales_qty_14 FROM ad_performance WHERE import_id IN ({marks})",ids).fetchall()
                for row in rows:
                    key,pid,name = resolve(row["option_id"])
                    if key:
                        item(key,pid,name)["광고 판매량"] += max(0.0,_num(sales_module,row["sales_qty_14"]))

    rows_out = []
    for x in totals.values():
        total = float(x["판매량"]); ad = float(x["광고 판매량"])
        if total <= 0 and ad <= 0: continue
        organic = total-ad
        rows_out.append({"아이템":x["아이템"],"판매량":total,"광고 판매량":ad,"Organic 판매량":organic,"Organic 판매 비율":organic/total*100.0 if total>0 else 0.0})
    cols = ["아이템","판매량","광고 판매량","Organic 판매량","Organic 판매 비율"]
    frame = pd.DataFrame(rows_out,columns=cols) if rows_out else pd.DataFrame(columns=cols)
    if not frame.empty:
        frame = frame.sort_values(["판매량","아이템"],ascending=[False,True],kind="stable").reset_index(drop=True)
    return frame,covered,ad_qty_available


def apply(core, db=None):
    module = importlib.import_module("organic_sales_estimate_v09211")

    def render_page(st_obj, core_obj, db_path=None):
        target = db_path or db or core_obj.DEFAULT_DB
        core_obj.init_db(target)
        st_obj.session_state["_rg_sales_stats_period_active"] = False
        st_obj.markdown("## 🌱 오가닉판매 추정")
        st_obj.caption("입력한 판매자료의 판매량에서 같은 기간 광고성과보고서의 광고 판매량을 빼 Organic 판매량을 추정합니다.")
        days = st_obj.radio("기간",(30,60,90),index=0,horizontal=True,format_func=lambda n:f"최근 {n}일",key="organic_sales_period_v09212")
        end = date.today(); start = end - timedelta(days=int(days)-1)
        st_obj.caption(f"조회기간: {start.isoformat()} ~ {end.isoformat()}")

        matcher = importlib.import_module("shared_return_match_ui_v09217")
        matcher.ensure_period_mappings(core_obj,target,start,end,"오가닉판매 추정")

        sales_module = importlib.import_module("sales_analysis_v09186")
        frame,covered,ad_qty_available = _canonical_data(core_obj,sales_module,module,target,start,end)
        wanted = _period_days(start,end)
        if not ad_qty_available:
            st_obj.warning("선택 기간의 광고성과보고서에서 광고 판매량을 확인할 수 없습니다.")
        if len(covered) < len(wanted):
            st_obj.warning(f"선택한 최근 {int(days)}일 중 판매자료와 광고자료가 모두 있는 날짜는 {len(covered)}일입니다. 표는 두 자료의 날짜가 함께 확인되는 입력 구간만 합산합니다.")
        else:
            st_obj.caption(f"판매자료와 광고자료가 최근 {int(days)}일 전체에 대해 확인됩니다.")
        if frame.empty:
            st_obj.info("선택 기간에 판매자료와 광고자료가 함께 확인되는 상품이 없습니다.")
            return
        if (pd.to_numeric(frame["Organic 판매량"],errors="coerce").fillna(0)<0).any():
            st_obj.warning("Organic 판매량이 음수인 상품이 있습니다. 판매자료와 광고보고서의 상품 매칭 또는 입력기간을 확인해 주세요.")
        st_obj.markdown(_table_html(frame),unsafe_allow_html=True)

    module.render_page = render_page
    module._rg_display_v09223_direct_canonical = True
    return {"ok":True,"aggregation":"raw_option_to_confirmed_master_before_grouping","confirmation":"required"}
