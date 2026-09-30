"""v0.9.234 RG product/BOM registration page shell.

The first step is to expose the dedicated workflow under the Purchase/Product
sidebar group. Excel parsing and BOM-confirmation logic will be added on this
page without changing the surrounding ERP navigation.
"""
from __future__ import annotations

PAGE_LABEL = "RG상품/BOM 등록"


def render_page(st, core):
    st.title("RG상품/BOM 등록")
    st.caption("로켓그로스 신규상품 등록과 BOM 확인/등록을 한 화면에서 처리합니다.")

    st.info(
        "쿠팡 로켓그로스 상품 엑셀을 기준으로 상품명(B열+ C열), "
        "상품코드(G열 옵션 ID), 상품바코드(AB열)를 읽고 BOM 후보를 확인한 뒤 "
        "최종 등록하는 기능을 이 메뉴에서 제공합니다."
    )

    st.subheader("등록 흐름")
    st.write("1. RG 상품 엑셀 업로드")
    st.write("2. 상품정보 확인")
    st.write("3. BOM 기초상품·수량 추론 결과 확인/수정")
    st.write("4. 확인 후 상품과 BOM 일괄 등록")

    st.warning("BOM은 사용자 확인 전에는 DB에 확정 등록하지 않습니다.")
