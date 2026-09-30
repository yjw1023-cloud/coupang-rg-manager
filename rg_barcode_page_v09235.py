"""RG Manager v0.9.235 — standalone barcode printing page.

Exposes the existing 50x30 mm RG barcode-label workflow as an independent page
under the Inventory/Production sidebar group. The proven barcode storage,
Coupang manual lookup, editable print quantity, Code128 renderer and browser
printing remain in rg_barcode_print_v09193.
"""
from __future__ import annotations

import importlib

PAGE_LABEL = "바코드 인쇄"


def render_page(st, core):
    st.title("바코드 인쇄")
    st.caption("로켓그로스 상품 바코드를 50×30mm 라벨로 인쇄합니다.")

    st.info(
        "쿠팡 로켓그로스 입고 Excel을 업로드하면 옵션ID와 V열 입고수량을 읽습니다. "
        "바코드와 출력수량은 인쇄 전에 직접 확인·수정할 수 있습니다."
    )

    uploaded = st.file_uploader(
        "RG 입고 Excel",
        type=["xlsx"],
        key="rg_barcode_page_v09235_upload",
        help="쿠팡 로켓그로스 입고요청 Excel 양식을 사용합니다.",
    )
    if uploaded is None:
        st.caption("Excel을 업로드하면 상품별 바코드와 출력수량 편집 화면이 표시됩니다.")
        return

    pd = importlib.import_module("pandas")
    barcode = importlib.import_module("rg_barcode_print_v09193")
    production = importlib.import_module("production_batch_v095")

    try:
        barcode.render_inbound_barcode_section(
            st=st,
            pd=pd,
            core_module=core,
            batch_module=production,
            uploaded=uploaded,
        )
    except Exception as exc:
        st.error(f"바코드 인쇄 화면을 여는 중 오류가 발생했습니다: {exc}")
