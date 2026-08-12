# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 새 ChatGPT 세션 인계 기준이다.

## 현재 기준
- main 배포 버전: **v0.9.68**
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

## 2026-08-12 최신 변경 — 창고별 재고 Excel / 재고실사 (v0.9.68)
사용자 요구:
- ERP 재고를 창고별 Excel로 출력
- 실제 재고실사 후 Excel에 실사수량을 입력
- 같은 Excel을 ERP에 업로드해 재고량 조정

**현재 고정 원칙**
- 재고를 직접 UPDATE/덮어쓰기 하지 않는다.
- `실사수량 - 업로드 시점의 현재 ERP 재고` 차이만 `inventory_txns`에 `재고실사조정`으로 기록한다.
- 엑셀 다운로드 시점 이후 판매/입고/생산으로 ERP 재고가 변했으면 이를 감지하여 경고한다.
- 조정 계산은 엑셀의 오래된 `ERP현재고`가 아니라 업로드 시점의 실시간 ERP 현재고 기준이다.
- 실사하지 않은 행은 `실사수량`을 빈칸으로 두며 조정하지 않는다.
- 동일 상품+창고가 한 파일에 중복되면 차단한다.
- 실사수량 음수는 차단한다.
- 동일 파일 해시는 두 번 적용할 수 없다.
- 적용 전 변경행/증가수량/감소수량을 미리 보여주고 사용자가 확인 체크 후 적용한다.
- 실사일, 파일명, 참조번호, 입력행, 조정행, 증감수량을 `inventory_stocktake_imports`에 보관한다.

Excel 구조:
- 한 workbook 안에 `사용방법` + 창고별 sheet
- 기본 창고는 자체창고 / 쿠팡RG / 반품창고이며 DB에 다른 창고가 있으면 추가 sheet 생성
- 열: ERP상품ID(숨김), 창고, 품목코드, 쿠팡 옵션ID, 상품명, 상태, ERP현재고, 실사수량, 차이, 비고
- 사용자는 노란색 `실사수량` 열만 입력
- `차이`는 Excel에서 확인할 수 있도록 수식 표시

관련 파일:
- `inventory_stocktake_v0968.py`
- `inventory_ui_v084.py`
- `inventory_flow_v088.py`
- `app.py`

## 판매수량 의미 수정 (v0.9.65)
사용자 확인으로 잠정손익의 `판매수량`을 순판매수량으로 보여주던 방식이 잘못됐음을 확인했다.

**현재 고정 원칙**
- `판매수량` = 쿠팡 판매통계의 실제 판매수량(gross sales)
- `취소수량` = 취소/환불 수량
- `순판매수량` = signed net quantity. 손익/재고/원가 역분개 계산용
- 화면의 판매수량을 다시 net_qty로 표시하지 말 것.
- 매출원가/손익 계산은 반드시 `순판매수량` 기준으로 유지한다.
- 수동 예상 실현단가 조정도 판매수량이 아니라 `순판매수량`을 곱해 예상매출을 계산한다.

관련 파일:
- `sales_quantity_v0965.py`
- `provisional_manual_netqty_v0965.py`
- `pnl_month_v0965.py`

## 월 잠정손익 stale snapshot 수정 (v0.9.66~0.9.67)
사용자가 실제 입력한 2026-08 판매통계 Excel 두 개를 직접 대조하여 월 잠정손익에서 수량은 최신 DB를 읽지만 예상매출/비용은 과거 snapshot을 읽어 서로 다른 기준이 섞이는 문제를 확인했다.

검증 원자료:
- `Statistics-20260801~20260802_(0).xlsx`
- `Statistics-20260803~20260809_(0)(1).xlsx`

검증 기준:
- 원상품 `94350296878` 구두주걱: 8월 판매 3, 취소 1, 순판매 2, 예상매출 23,940원, 반품판매취소 1, 반품판매매출 -10,260원
- 원상품 `94475454519` 글라스 네일 파일: 판매 4, 취소 1, 순판매 3, 반품판매 1, 예상매출 24,360원, 반품판매매출 7,560원
- 사용자가 v0.9.67 화면에서 위 값이 맞는 것을 확인함.

현재 방식:
- `pnl_snapshot_refresh_v0966.py`가 판매통계 import fingerprint + 계산 버전으로 stale snapshot 여부 판단
- 필요하면 해당 월의 기존 snapshot을 현재 `sales_stats` 기준으로 다시 계산
- v0.9.66은 존재하지 않는 함수 호출 오류가 있었고 v0.9.67에서 함수 monkey-patch를 제거하고 화면 진입 전에 refresh를 먼저 실행하는 방식으로 수정

관련 파일:
- `pnl_snapshot_refresh_v0966.py`
- `pnl_month_v0967.py`
- `pnl_month_default_v0915.py`

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

관련 파일:
- `return_sale_pnl_v0963.py`
- `return_sale_pnl_v0964.py`
- `return_sale_pnl_v0965.py`
- `sales_quantity_v0965.py`
- `pnl_month_v0965.py`

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
- 재고실사 때 inventory balance를 직접 덮어쓰지 말 것. 반드시 `재고실사조정` transaction으로 차이를 남긴다.
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
4. `VERSION.txt`와 `update/latest.json`이 모두 **v0.9.68 이상**인지 확인
5. 재고 Excel/실사 문제면 `inventory_stocktake_v0968.py` → `inventory_ui_v084.py` → `inventory_flow_v088.py` 순서로 확인
6. 잠정손익 수량/매출 문제면 `sales_quantity_v0965.py` → `return_sale_pnl_v0965.py` → `pnl_snapshot_refresh_v0966.py` → `pnl_month_v0967.py` 순서로 확인

## 최신 주요 파일
- `app.py`
- `VERSION.txt`
- `update/latest.json`
- `inventory_stocktake_v0968.py`
- `inventory_ui_v084.py`
- `inventory_flow_v088.py`
- `sales_quantity_v0965.py`
- `return_sale_pnl_v0965.py`
- `provisional_manual_netqty_v0965.py`
- `pnl_snapshot_refresh_v0966.py`
- `pnl_month_v0967.py`
- `pnl_month_default_v0915.py`
- `return_discount_v099.py`
- `return_sale_match_v0944.py`
- `canonical_rg_cleanup_v0947.py`
- `provisional_ad_report_v0956.py`
- `provisional_manual_adjust_v0952.py`
- `pnl_month_autobackfill_v0932.py`
- `monthly_closing_v0916.py`
