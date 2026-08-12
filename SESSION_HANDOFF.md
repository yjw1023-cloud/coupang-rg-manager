# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 새 ChatGPT 세션 인계 기준이다.

## 현재 기준
- main 배포 버전: **v0.9.65**
- 현재 개발 기준 브랜치: **main**
- 저장소: `yjw1023-cloud/coupang-rg-manager`
- 새 세션은 반드시 `PROJECT_CONTEXT.md` → `SESSION_HANDOFF.md` → `SESSION_LOG_2026-08-12.md` → `VERSION.txt` → `update/latest.json` → 최근 관련 모듈 순서로 확인한다.
- Windows 로컬 ERP / Streamlit / SQLite `data/rocketgrowth.db`
- 자동 업데이트: GitHub `main`의 `update/latest.json`
- 사용자 데이터(`data`, `.venv`, `sample_data`)는 업데이트로 덮어쓰지 않는다.

## 손익 메뉴
- `📈 잠정손익`: 월 단위 잠정 관리손익
- `📄 자료별 잠정손익`
- `✅ 상품 확정손익`
- `📒 월 결산`
- `🔍 손익차이분석`

## 2026-08-12 최신 변경 — 판매수량 의미 수정 (v0.9.65)
사용자 확인으로 잠정손익의 `판매수량`을 순판매수량으로 보여주던 방식이 잘못됐음을 확인했다.

**현재 고정 원칙**
- `판매수량` = 쿠팡 판매통계의 실제 판매수량(gross sales)
- `취소수량` = 취소/환불 수량
- `순판매수량` = 판매수량 - 취소수량에 해당하는 signed net quantity. 손익/재고/원가 역분개 계산용
- 화면의 판매수량을 다시 net_qty로 표시하지 말 것.
- 매출원가/손익 계산은 반드시 `순판매수량` 기준으로 유지한다.
- 수동 예상 실현단가 조정도 판매수량이 아니라 `순판매수량`을 곱해 예상매출을 계산한다.

구현:
- `sales_quantity_v0965.py`가 sales_stats 스키마를 동적으로 확인하여 실제 판매수량/취소수량/순판매수량을 월별로 집계
- 가능한 경우 `sales_qty`/`판매수량` 및 `cancel_qty`/`취소수량` 계열 컬럼을 직접 사용
- 구형 DB에서 gross 컬럼이 없으면 net+cancel 조합으로 복원하고, net만 있으면 안전한 fallback 사용
- `provisional_manual_netqty_v0965.py`가 수동 실현단가 계산 시 순판매수량을 사용하도록 보정
- `pnl_month_v0965.py`에서 월 잠정손익에 적용

## 반품판매 원상품 연결
사용자가 아래 2개 옵션을 반품판매 데이터라고 직접 확인했다.
- `95119299567` → 원상품 `94475454519` 글라스 네일 파일 5P
- `95156135112` → 원상품 `94350296878` 휴대용 가죽 구두주걱 미니 2P

현재 원칙:
- 반품 옵션ID는 `return_discount_aliases`로 원상품에 연결
- 반품 옵션은 독립 관리상품/독립 잠정손익 행으로 두지 않음
- 원 판매통계 이력은 감사 추적을 위해 유지
- 재고는 원상품 `반품창고`에서 `반품할인판매차감`으로 처리
- 월 잠정손익에서는 원상품 행으로 합산
- `반품판매수량` = 실제 반품상품 판매수량(gross)
- `반품판매취소` = 반품판매의 취소/환불 수량
- `반품판매매출` = 쿠팡 자료의 취소/환불을 포함한 반품판매 순매출
- 원가는 연결된 원상품 원가 사용
- 반품판매의 매출원가는 signed `순판매수량`으로 계산: +판매는 원가 차감, 취소/환불은 원가 환입
- 반품판매 여부를 net_qty의 양/음 부호만 보고 판정하지 말 것. 실제 판매수량/취소수량 컬럼을 우선한다.

관련 최신 파일:
- `return_sale_pnl_v0963.py`
- `return_sale_pnl_v0964.py`
- `return_sale_pnl_v0965.py`
- `sales_quantity_v0965.py`
- `provisional_manual_netqty_v0965.py`
- `pnl_month_v0965.py`
- `pnl_month_default_v0915.py`

## 광고비 처리
기존 월 광고비 수동입력 + 매출비율 배분 방식은 폐기.
- 잠정손익에서 쿠팡 `광고성과보고서 Excel` 업로드
- `광고집행 옵션ID` 기준으로 광고비 직접 귀속
- 동일 옵션ID 여러 광고행 합산
- 판매가 없고 광고만 집행된 옵션도 광고비 손실행 표시
- 과거 수동 광고비는 계산에서 사용하지 않음
- 상품별 예상 실현단가 / 입출고비 / 배송비 수동조정은 유지

검증 파일: 2026-08-01~2026-08-11, 광고집행 옵션 19개, 광고비 합계 2,478,464원.

## 잠정손익 표 UI 유지사항
- 파란 헤더 + 굵은 글씨
- 상품명 좌측 정렬, 숫자 중앙 정렬
- 가로 스크롤 유지
- 내부 세로 스크롤 없이 전체 행 표시
- 헤더 클릭 정렬은 embedded HTML/JavaScript 내부에서 처리
- URL/query parameter 정렬 방식으로 되돌리지 말 것.

## 중요한 금지/유지사항
- 광고비를 수동 총액 + 매출비율 배분 방식으로 되돌리지 말 것.
- 반품판매 옵션을 다시 독립 잠정손익 행으로 되돌리지 말 것.
- `판매수량`에 순판매수량을 넣지 말 것. 취소수량과 순판매수량을 별도로 유지한다.
- 손익 원가 계산은 gross 판매수량이 아니라 signed 순판매수량 기준이다.
- 월 결산은 매입액 전체 비용처리가 아니라 재고식 매출원가 원칙 유지.
- 사용자의 로컬 SQLite DB에 직접 접근할 수 없으므로 필요한 DB 정리는 업데이트 코드가 실행되도록 구현한다.

## 기존 기능 유지
- 품목코드 JDS 자동부여 및 과거 코드 영구예약
- 품목 보관삭제/복원
- 생산/BOM 후보 필터와 BOM 전체삭제 정책
- 월 잠정손익 snapshot 자동 backfill
- 사이드바 그룹화/로고/8517 포트 안정화

## 다음 세션 시작 시
1. `PROJECT_CONTEXT.md`
2. `SESSION_HANDOFF.md`
3. `SESSION_LOG_2026-08-12.md`
4. `VERSION.txt`와 `update/latest.json`이 모두 **v0.9.65 이상**인지 확인
5. 잠정손익 수량 문제면 `sales_quantity_v0965.py` → `return_sale_pnl_v0965.py` → `pnl_month_v0965.py` 순서로 확인

## 최신 주요 파일
- `app.py`
- `VERSION.txt`
- `update/latest.json`
- `sales_quantity_v0965.py`
- `return_sale_pnl_v0965.py`
- `provisional_manual_netqty_v0965.py`
- `pnl_month_v0965.py`
- `pnl_month_default_v0915.py`
- `return_discount_v099.py`
- `return_sale_match_v0944.py`
- `canonical_rg_cleanup_v0947.py`
- `provisional_ad_report_v0956.py`
- `provisional_manual_adjust_v0952.py`
- `pnl_month_v0961.py`
- `pnl_month_autobackfill_v0932.py`
- `monthly_closing_v0916.py`
