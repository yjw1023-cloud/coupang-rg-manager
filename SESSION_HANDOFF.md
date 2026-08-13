# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 새 ChatGPT 세션 인계 기준이다.

## 현재 기준
- main 배포 버전: **v0.9.74**
- 현재 개발 기준 브랜치: **main**
- 저장소: `yjw1023-cloud/coupang-rg-manager`
- Windows 로컬 ERP / Streamlit / SQLite `data/rocketgrowth.db`
- 자동 업데이트: GitHub `main`의 `update/latest.json`
- 사용자 데이터(`data`, `.venv`, `sample_data`)는 업데이트로 덮어쓰지 않는다.
- 새 세션은 반드시 `PROJECT_CONTEXT.md` → `SESSION_HANDOFF.md` → `SESSION_LOG_2026-08-13.md` → `VERSION.txt` → `update/latest.json` → 최근 관련 모듈 순서로 확인한다.

## 가장 중요한 현재 미해결 이슈 — 신규 3상품 BOM
사용자가 아래 3개 상품의 품목등록과 BOM 등록을 요청했다.

완제품:
- `95912623408` — 어항용 뜰채 플라스틱 2p 수족관 새우 베타 구피, Free 2개
- `95912717676` — 프로 야구 포토카드 앨범 바인더, 화이트 50매
- `95912816721` — 대형 견출지 라벨 스티커 300장 라벨지, 혼합 300개입 1개

v0.9.70에서:
- 위 3개 `finished` 완제품 등록
- 각각 동일명 `raw` 자체창고 품목 생성
- 자체창고 코드는 로컬 DB의 다음 비어 있는 `JDS####` 자동부여
- 중복 생성 방지
- 사용자가 **품목 등록 자체는 성공했다고 확인함**

요청 BOM:
- `95912623408` → 동일명 JDS 자체창고 품목 **2개**
- `95912717676` → 동일명 JDS 자체창고 품목 **1개**
- `95912816721` → 동일명 JDS 자체창고 품목 **1개**

BOM 시도:
- v0.9.71 `requested_product_bom_seed_v0971.py`: 직접 `bom_items` 등록 → 사용자 화면에 BOM 없음
- v0.9.72 `requested_product_bom_repair_v0972.py`: startup 강제복구 + 저장검증 → 사용자 화면에 BOM 없음
- v0.9.73 `requested_product_bom_force_v0973.py`: 직접 INSERT 대신 ERP 정식 `core.add_bom()` 사용 + DB 재검증 방식으로 변경

**현재 상태:** v0.9.73 이후 사용자가 `현재 BOM` 화면을 캡처했는데 검색창 아래 BOM 행이 보이지 않았다. 그 직후 검색창 UI 개선으로 넘어가 BOM 확인이 끝나지 않았다. 따라서 새 세션의 첫 확인사항은 **v0.9.74 현재 DB/화면에 위 3개 BOM이 실제 존재하는지 확인하는 것**이다.

중요:
- 품목 master 6개는 이미 등록된 것으로 보고 중복 생성하지 말 것.
- BOM이 계속 없으면 또 추측성 버전업부터 하지 말고, 실제 `core.add_bom()` 저장 결과와 현재 BOM 화면이 읽는 DB/쿼리/필터를 먼저 대조할 것.
- 필요하면 오류를 화면에 직접 노출해서 로컬 DB의 실제 실패 이유를 확인할 것.

관련 파일:
- `requested_product_seed_v0970.py`
- `requested_product_bom_seed_v0971.py`
- `requested_product_bom_repair_v0972.py`
- `requested_product_bom_force_v0973.py`
- `bom_candidate_filter_v0927.py`
- `bom_current_list_ui_v0935.py`
- `production_v085.py`
- `production_batch_v095.py`

## v0.9.74 — ERP 전체 검색 입력창 테두리
사용자가 `현재 BOM 검색`을 포함해 ERP 전체 검색창이 흰 배경에 묻혀 흐리다고 지적함.

현재 구현:
- `search_ui_v096.py`에서 전역 CSS 주입
- Streamlit `st.text_input` 기반 입력영역 기본 회색 1.5px 테두리
- hover 시 더 진한 회색
- focus 시 파란 2px 테두리 + 약한 focus ring
- placeholder도 진하게 표시
- BOM, 재고, 품목, 매입이력 등 text_input 기반 검색영역 전체에 적용

`VERSION.txt`와 `update/latest.json` 모두 **0.9.74** 확인 완료.

## 재고관리 — 창고별 Excel / 재고실사
사용자 요구:
- ERP 재고를 창고별 Excel로 출력
- 실제 재고실사 후 Excel의 `실사수량` 입력
- 같은 Excel 업로드 후 ERP 재고 조정

v0.9.68 고정 원칙:
- 재고를 직접 UPDATE/덮어쓰기 하지 않는다.
- `실사수량 - 업로드 시점 현재 ERP 재고` 차이만 `inventory_txns`에 `재고실사조정`으로 기록한다.
- 엑셀 다운로드 후 판매/입고/생산이 발생했더라도 업로드 시 실제 ERP 재고를 다시 읽는다.
- 실사하지 않은 행은 빈칸으로 두고 무시.
- 실사수량 음수 차단.
- 동일 상품+창고 중복행 차단.
- 동일 파일 해시 중복 적용 차단.
- 적용 전 변경행/증가/감소 preview + 사용자 확인 체크.
- 실사일/파일명/참조번호/증감수량을 `inventory_stocktake_imports`에 기록.

Excel 구조:
- `사용방법` + 창고별 sheet
- 열: ERP상품ID(숨김), 창고, 품목코드, 쿠팡 옵션ID, 상품명, 상태, ERP현재고, 실사수량, 차이, 비고

v0.9.69 창고별 출력 필터:
- `자체창고` → `raw` 품목만
- `쿠팡RG` → `finished` 완제품만
- `반품창고` → 완제품만
- 기타 창고 → 해당 창고에 실제 현재고가 있는 품목만
- 보관품목은 해당 창고 재고가 있을 때만 표시

관련 파일:
- `inventory_stocktake_v0968.py`
- `inventory_stocktake_v0969.py`
- `inventory_ui_v084.py`
- `inventory_flow_v088.py`

## 잠정손익 — 판매수량/반품판매/월 snapshot

### 판매수량 고정 원칙
- `판매수량` = 쿠팡 원자료의 실제 판매수량(gross)
- `취소수량` = 취소/환불 수량
- `순판매수량` = signed net quantity
- 화면의 판매수량을 다시 net_qty로 표시하지 말 것.
- 매출원가/손익/재고 계산은 순판매수량 기준.
- 수동 예상 실현단가 조정도 순판매수량 기준.

### 반품판매 원상품 연결
사용자가 아래 2개 옵션을 반품판매 데이터라고 직접 확인함.
- `95119299567` → `94475454519` 글라스 네일 파일 5P
- `95156135112` → `94350296878` 휴대용 가죽 구두주걱 미니 2P

고정 원칙:
- 반품 옵션을 독립 잠정손익 행으로 두지 않고 원상품 행에 합산.
- 원 판매통계 이력은 감사 추적을 위해 유지.
- 반품판매 재고는 원상품 `반품창고`에서 처리.
- `반품판매수량` = gross 반품상품 판매수량.
- `반품판매취소` = 반품판매 취소/환불 수량.
- `반품판매매출` = 쿠팡 자료의 signed 순매출.
- 원가는 연결된 원상품 원가.
- 반품판매의 매출원가도 signed 순판매수량으로 처리: 판매 시 차감, 취소 시 환입.
- `net_qty`의 부호만 보고 판매/취소를 추정하지 말 것.

### 실제 8월 판매통계 두 파일 검증값
사용자가 입력한 판매통계 Excel은 아래 두 개가 전부라고 확인함.
- `Statistics-20260801~20260802_(0).xlsx`
- `Statistics-20260803~20260809_(0)(1).xlsx`

검증 완료:
- `94350296878` 구두주걱: 판매 3 / 취소 1 / 순판매 2 / 반품판매수량 0 / 반품판매취소 1 / 예상매출 **23,940원** / 반품판매매출 **-10,260원**
- `94475454519` 네일파일: 판매 4 / 취소 1 / 순판매 3 / 반품판매수량 1 / 반품판매취소 0 / 예상매출 **24,360원** / 반품판매매출 **7,560원**

사용자가 v0.9.67 화면에서 위 숫자가 맞음을 확인함.

### 월 snapshot stale 문제
- v0.9.66에서 존재하지 않는 `_snapshot_rows_for_month` 호출 오류 발생.
- v0.9.67에서 함수 monkey-patch 제거.
- 월 화면 진입 전에 현재 `sales_stats` fingerprint/calculation version을 보고 stale snapshot이면 다시 생성.
- 수량은 최신 DB, 예상매출은 과거 snapshot 식으로 서로 다른 기준을 섞지 말 것.

관련 파일:
- `sales_quantity_v0965.py`
- `return_sale_pnl_v0965.py`
- `provisional_manual_netqty_v0965.py`
- `pnl_snapshot_refresh_v0966.py`
- `pnl_month_v0967.py`

## 광고비 처리
기존 월 광고비 수동 총액 + 매출비율 배분 방식은 폐기.
- 잠정손익에서 쿠팡 `광고성과보고서 Excel` 업로드
- `광고집행 옵션ID` 기준으로 광고비 직접 귀속
- 동일 옵션ID 광고행 합산
- 판매가 없고 광고만 집행된 옵션도 광고비 손실행 표시
- 과거 수동 광고비는 계산에서 사용하지 않음
- 상품별 예상 실현단가 / 입출고비 / 배송비 수동조정은 유지

검증 파일: 2026-08-01~2026-08-11 광고집행 옵션 19개, 광고비 합계 2,478,464원.

## 잠정손익 표 UI 유지사항
- 파란 헤더 + 굵은 글씨
- 상품명 좌측 정렬, 숫자 중앙 정렬
- 가로 스크롤 유지
- 내부 세로 스크롤 없이 전체 행 표시
- 헤더 클릭 정렬은 embedded HTML/JavaScript 내부 처리
- URL/query parameter 정렬 방식으로 되돌리지 말 것

## 중요한 금지/유지사항
- 광고비를 수동 총액 + 매출비율 배분으로 되돌리지 말 것.
- 반품판매 옵션을 다시 독립 잠정손익 행으로 되돌리지 말 것.
- 판매수량/취소수량/순판매수량을 다시 혼용하지 말 것.
- 손익 원가는 gross가 아니라 signed 순판매수량 기준.
- 재고실사 때 balance 직접 덮어쓰기 금지. 반드시 `재고실사조정` transaction.
- 월 결산은 당월 전체 매입액 비용처리가 아니라 재고식 매출원가 원칙.
- 사용자 로컬 SQLite DB에 직접 접근할 수 없으므로 DB 정리가 필요하면 업데이트 코드가 실행되도록 구현.
- 신규 3상품 master는 이미 생성 성공했으므로 중복생성 금지.

## 기존 기능 유지
- 품목코드 JDS 자동부여 및 과거 코드 영구예약
- 품목 보관삭제/복원
- 생산/BOM 후보 필터와 BOM 삭제 정책
- 월 잠정손익 snapshot 자동 backfill
- 사이드바 그룹화/로고/8517 포트 안정화

## 다음 세션 시작 시
1. `PROJECT_CONTEXT.md`
2. `SESSION_HANDOFF.md`
3. `SESSION_LOG_2026-08-13.md`
4. `VERSION.txt`, `update/latest.json`이 모두 **v0.9.74**인지 확인
5. **신규 3상품 BOM 존재 여부부터 확인**
6. BOM 문제면 `requested_product_bom_force_v0973.py` → `bom_candidate_filter_v0927.py` → `bom_current_list_ui_v0935.py` → `production_batch_v095.py` → 실제 `core.add_bom()` 순서로 확인
7. 재고실사 문제면 `inventory_stocktake_v0969.py` → `inventory_stocktake_v0968.py` → `inventory_ui_v084.py`
8. 잠정손익 문제면 `sales_quantity_v0965.py` → `return_sale_pnl_v0965.py` → `pnl_snapshot_refresh_v0966.py` → `pnl_month_v0967.py`

## 최신 주요 파일
- `app.py`
- `VERSION.txt`
- `update/latest.json`
- `SESSION_LOG_2026-08-13.md`
- `search_ui_v096.py`
- `inventory_stocktake_v0968.py`
- `inventory_stocktake_v0969.py`
- `requested_product_seed_v0970.py`
- `requested_product_bom_seed_v0971.py`
- `requested_product_bom_repair_v0972.py`
- `requested_product_bom_force_v0973.py`
- `sales_quantity_v0965.py`
- `return_sale_pnl_v0965.py`
- `pnl_snapshot_refresh_v0966.py`
- `pnl_month_v0967.py`
- `provisional_ad_report_v0956.py`
- `monthly_closing_v0916.py`
