from pathlib import Path
import shutil
import core
import base64
import hashlib
import importlib
import sys
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parent
MODULE_PATH = ROOT / "purchase_v06.py"
MODULE_SHA256 = "7d3ae65eb20f67d7817e5ff93b110c4dd9db532ac5cde39d9a28dfa83d5f83e8"
MODULE_URL = "https://raw.githubusercontent.com/yjw1023-cloud/coupang-rg-manager/main/update/purchase_v06.zlib.b64"

def _ensure_purchase_module():
    if MODULE_PATH.exists():
        try:
            if hashlib.sha256(MODULE_PATH.read_bytes()).hexdigest() == MODULE_SHA256:
                return
        except Exception:
            pass
    req = urllib.request.Request(MODULE_URL, headers={"User-Agent":"RG-Manager/0.6.1"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = resp.read().decode("ascii").strip()
    module_bytes = zlib.decompress(base64.b64decode(payload))
    digest = hashlib.sha256(module_bytes).hexdigest()
    if digest != MODULE_SHA256:
        raise RuntimeError("매입관리 모듈 검증에 실패했습니다. 업데이트 파일이 손상되었습니다.")
    tmp = MODULE_PATH.with_suffix(".tmp")
    tmp.write_bytes(module_bytes)
    tmp.replace(MODULE_PATH)

_ensure_purchase_module()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
purchase_v06 = importlib.import_module("purchase_v06")

# v0.6 loader: preserve the known-good v0.4 application source, then apply
# the cumulative dashboard + purchase-management patches in memory.
BASE_DIR = ROOT / "_code_base"
BASE_APP = BASE_DIR / "app_v0.4.py"
BACKUP_APP = ROOT / "_code_backup" / "app.py"

if not BASE_APP.exists():
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    if BACKUP_APP.exists():
        shutil.copy2(BACKUP_APP, BASE_APP)
    else:
        raise RuntimeError(
            "v0.6 기본 코드(app_v0.4.py)를 찾을 수 없습니다. "
            "프로그램 업데이트 메뉴에서 다시 업데이트해 주세요."
        )

purchase_v06.ensure_schema(core.DEFAULT_DB)
source = BASE_APP.read_text(encoding="utf-8")

old_monthly = '    # 월 정산자료는 일일 판매통계가 없어도 먼저 요약합니다.\n    months = core.monthly_available()\n    if months:\n        latest_month = months[0]\n        mdf, mmeta = core.confirmed_monthly_pnl(latest_month)\n        if not mdf.empty:\n            section(f"최근 확정자료 · {latest_month}", "상품원가를 제외한 쿠팡 정산 흐름을 먼저 보여줍니다.")\n            m_revenue = float(mdf["realized_sales"].sum())\n            m_comm = float(mdf["commission"].sum())\n            m_rg = float(mdf[["inout","delivery","return_pickup","return_restock"]].sum().sum())\n            m_ad = float(mmeta.get("ad_billable_total", 0))\n            m_after = m_revenue - m_comm - m_rg - m_ad\n            c1,c2,c3,c4 = st.columns(4)\n            kpi(c1, "실현매출", money(m_revenue), "판매자 쿠폰 반영", "primary")\n            kpi(c2, "판매수수료", money(m_comm), "VAT 포함")\n            kpi(c3, "RG·반품비", money(m_rg), "입출고·배송·반품")\n            kpi(c4, "쿠팡비용 차감 후", money(m_after), f"광고 {money(m_ad)}", "positive" if m_after >= 0 else "negative")\n'
new_monthly = '    # 월 확정 손익: 상품원가와 광고비를 포함한 실제 경영손익을 먼저 보여줍니다.\n    months = core.monthly_available()\n    if months:\n        latest_month = months[0]\n        mdf, mmeta = core.confirmed_monthly_pnl(latest_month)\n        if not mdf.empty:\n            section(\n                f"최근 확정손익 · {latest_month}",\n                "최종이익 = 실현매출 - 상품원가 - 판매수수료 - RG·반품비 - 광고비",\n            )\n            m_revenue = float(mdf["realized_sales"].sum())\n            m_cogs = float(mdf["cogs"].sum())\n            m_comm = float(mdf["commission"].sum())\n            m_rg = float(mdf[["inout", "delivery", "return_pickup", "return_restock"]].sum().sum())\n            m_ad = float(mmeta.get("ad_billable_total", 0))\n            m_profit = float(mmeta.get("overall_profit", m_revenue - m_cogs - m_comm - m_rg - m_ad))\n            m_margin = (m_profit / m_revenue * 100) if m_revenue else 0\n            missing_costs = int(mmeta.get("missing_cost_products", 0))\n\n            c1, c2, c3 = st.columns(3)\n            kpi(c1, "실현매출", money(m_revenue), "판매자 쿠폰 반영", "primary")\n            kpi(c2, "상품원가", money(m_cogs), f"원가 미입력 상품 {missing_costs}개", "negative" if missing_costs else "")\n            kpi(c3, "최종이익", money(m_profit), f"이익률 {pct(m_margin)}", "positive" if m_profit >= 0 else "negative")\n\n            c1, c2, c3 = st.columns(3)\n            kpi(c1, "판매수수료", money(m_comm), "VAT 포함")\n            kpi(c2, "RG·반품비", money(m_rg), "입출고·배송·반품회수·재입고")\n            kpi(c3, "광고비", money(m_ad), "월 광고 정산서 청구가능 광고비")\n\n            if missing_costs:\n                st.warning(\n                    f"{latest_month}에 원가가 0원으로 등록된 판매상품이 {missing_costs}개 있습니다. "\n                    "해당 상품의 원가를 입력하기 전까지 최종이익은 실제보다 높게 보일 수 있습니다."\n                )\n\n        section("월별 실적", "최근 12개월의 매출·비용·최종이익을 월별로 비교합니다. 정산자료가 추가되면 자동으로 행이 늘어납니다.")\n        monthly_rows = []\n        for month in months[:12]:\n            xdf, xmeta = core.confirmed_monthly_pnl(month)\n            if xdf.empty:\n                continue\n            revenue = float(xdf["realized_sales"].sum())\n            cogs = float(xdf["cogs"].sum())\n            commission = float(xdf["commission"].sum())\n            rg = float(xdf[["inout", "delivery", "return_pickup", "return_restock"]].sum().sum())\n            ad = float(xmeta.get("ad_billable_total", 0))\n            profit = float(xmeta.get("overall_profit", revenue - cogs - commission - rg - ad))\n            margin = (profit / revenue * 100) if revenue else 0\n            monthly_rows.append({\n                "월": month,\n                "실현매출": revenue,\n                "상품원가": cogs,\n                "판매수수료": commission,\n                "RG·반품비": rg,\n                "광고비": ad,\n                "최종이익": profit,\n                "이익률(%)": margin,\n                "원가미입력": int(xmeta.get("missing_cost_products", 0)),\n            })\n\n        if monthly_rows:\n            month_df = pd.DataFrame(monthly_rows)\n            display_df = month_df.copy()\n            for col in ["실현매출", "상품원가", "판매수수료", "RG·반품비", "광고비", "최종이익"]:\n                display_df[col] = display_df[col].map(money)\n            display_df["이익률(%)"] = display_df["이익률(%)"].map(pct)\n            display_df["원가미입력"] = display_df["원가미입력"].map(lambda x: f"{int(x):,}개")\n            st.dataframe(\n                display_df,\n                use_container_width=True,\n                hide_index=True,\n                height=min(500, 38 * (len(display_df) + 1)),\n            )\n\n            chart_df = month_df.sort_values("월").set_index("월")[["실현매출", "최종이익"]]\n            section("월별 매출·이익 추이", "정산자료가 쌓일수록 월별 성장과 수익성 변화를 한눈에 볼 수 있습니다.")\n            st.bar_chart(chart_df, height=320)\n    else:\n        section("월별 실적")\n        st.info("월 정산자료를 업로드하면 매출·상품원가·수수료·RG비용·광고비·최종이익을 월별로 비교할 수 있습니다.")\n'
if old_monthly not in source:
    raise RuntimeError("v0.6 월별 대시보드 패치를 적용할 기준 코드를 찾지 못했습니다.")
source = source.replace(old_monthly, new_monthly, 1)

menu_old = '        "📈  판매·손익",\n        "📦  재고관리",\n        "🏷️  상품·원가",\n'
menu_new = '        "📈  판매·손익",\n        "🧾  매입관리",\n        "📦  재고관리",\n        "🏷️  상품·원가",\n'
if menu_old not in source:
    raise RuntimeError("v0.6 매입관리 메뉴를 추가할 위치를 찾지 못했습니다.")
source = source.replace(menu_old, menu_new, 1)

purchase_marker = '# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
purchase_insert = '# ------------------------------\n# Purchase import / matching\n# ------------------------------\nelif page == "🧾  매입관리":\n    purchase_v06.render_purchase_page(\n        st=st, pd=pd, date=date, core=core,\n        page_header=page_header, section=section, kpi=kpi, money=money,\n        fmt_date=fmt_date, latest_updated_text=latest_updated_text,\n    )\n\n\n# ------------------------------\n# Inventory\n# ------------------------------\nelif page == "📦  재고관리":\n'
if purchase_marker not in source:
    raise RuntimeError("v0.6 매입관리 화면을 추가할 위치를 찾지 못했습니다.")
source = source.replace(purchase_marker, purchase_insert, 1)

source = source.replace('st.sidebar.caption("v0.4 · auto updater")', 'st.sidebar.caption("v0.6.1 · purchase matcher")', 1)

# Make the added module visible inside the executed base application.
globals()["purchase_v06"] = purchase_v06
exec(compile(source, str(BASE_APP), "exec"), globals(), globals())
