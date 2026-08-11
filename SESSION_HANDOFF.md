# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 최신 작업 기준이다.

## 현재 기준
- main 배포 버전: **v0.9.15**
- 현재 개발 버전: **v0.9.16**
- 현재 개발 브랜치: **`feature/v0.9.16-monthly-closing`**
- v0.9.16은 아직 main 배포 전 단계다.
- 새 세션은 `PROJECT_CONTEXT.md` → `SESSION_HANDOFF.md` → `VERSION.txt` → `update/latest.json` → 최신 patch module 순서로 확인한다.

## 프로젝트 기본 구조
- Windows 로컬 ERP / Streamlit / SQLite `data/rocketgrowth.db`
- 실행 `run.bat`
- 자동 업데이트 GitHub `yjw1023-cloud/coupang-rg-manager`
- 업데이트에서 사용자 DB/data 폴더를 덮어쓰지 않는다.
- 쿠팡 연결키는 가능한 경우 옵션ID 우선.
- 마이너스 재고 허용: 실제 생산/판매 기록을 재고부족 때문에 막지 않는다.

## 현재 주요 기능
- 기존 Claude ERP 이관/복구: `legacy_repair_v082.py`, `erp_import_guard_v082.py`
- 재고 창고: 자체창고 / 쿠팡RG / 반품창고 / 불량·폐기
- 생산/BOM: `production_v085.py`, `inventory_flow_v088.py`
- 판매통계 기간/재고차감: `sales_period_v087.py`, `inventory_flow_v088.py`
- 매입 Excel W열 원가 / AB열 수량 / 자체창고 고정: `purchase_v08.py`
- 매입차수: `purchase_batch_v089.py`
- 품목관리: `item_ui_v086.py`
- 매입 매칭: `purchase_match_ui_v090.py`, `purchase_match_ui_v091.py`
- 매입이력: `purchase_history_v092.py`, `purchase_history_v094.py`
- 반품관리: `return_management_v093.py`
- 생산자료 일괄생산: `production_batch_v095.py`
- 검색 UI: `search_ui_v096.py`
- 반품 할인판매: `return_discount_v099.py`
- 이동평균원가/수수료 fallback: `pnl_cost_commission_v0911.py`
- 손익 메뉴 분리/차이분석: `pnl_views_v0912.py`
- 잠정손익 UI: `provisional_pnl_ui_v0913.py`
- 월간 잠정손익: `pnl_month_default_v0914.py`, `pnl_month_default_v0915.py`

## v0.9.15 기준 손익 구조
- `📈 잠정손익`: 월간 잠정 관리손익.
- `📄 자료별 잠정손익`: 판매통계 파일별 잠정손익.
- `✅ 확정손익`: 쿠팡 월 정산 + ERP 상품원가를 섞어 상품 수익성을 보는 관리손익.
- `🔍 손익차이분석`: 잠정↔확정 차이.
- v0.9.14의 source 이동 방식에서 SyntaxError가 발생하여 v0.9.15에서 안전한 라우팅으로 교체했다.

## v0.9.16 월 결산 — 현재 개발 내용
사용자 요구에 따라 손익 성격을 명확히 분리한다.

### 메뉴 변경
- `✅ 확정손익` → **`✅ 상품 확정손익`**
- 신규 **`📒 월 결산`** 추가
- `📈 잠정손익`, `📄 자료별 잠정손익`, `🔍 손익차이분석`은 유지

### 상품 확정손익
- 기존 확정손익 계산을 유지하되 이름과 설명을 상품별 확정 관리손익으로 명확히 한다.
- 실제 쿠팡 실현매출/수수료/RG/반품/광고비 + ERP 상품원가 구조다.
- 사업 전체 결산과 혼동하지 않는다.

### 월 결산
관련 모듈: `monthly_closing_v0916.py`
문서: `MONTHLY_CLOSING.md`

핵심 원칙:
- 한 달 매입액 전체를 비용으로 처리하지 않는다.
- `재고식 매출원가 = 월초재고 + 당월매입 - 월말재고`
- `결산이익 = 실현매출 - 재고식 매출원가 - 수수료 - RG비용 - 반품비 - 광고비 - 기타비용`
- 월초/월말 재고는 `inventory_txns`를 시간순으로 재생해 수량/이동평균원가로 평가한다.
- 불량·폐기 창고는 재고자산 제외.
- 마이너스 재고는 자산금액 0으로 평가하고 경고.
- 당월 매입은 `purchase_lines`의 선택월 `purchase_date` 기준.
- 기타비용을 월 결산 화면에서 직접 추가/삭제 가능.
- 새 테이블: `monthly_closing_expenses`.
- 상품 확정손익의 매출원가와 재고식 매출원가 차이를 같이 표시해 데이터 누락을 찾는다.
- 실제 은행 입출금이 아닌 `발생기준 자금수지`를 참고치로 별도 표시한다.

### v0.9.16 연결 방식
- 기존 `app.py` 부트스트랩은 v0.9.15 그대로 유지한다.
- 이미 로드되는 `pnl_month_default_v0915.py`가 `monthly_closing_v0916.py`를 지연 import하고 메뉴/화면 라우팅을 추가한다.
- 이렇게 하면 v0.9.15에서 검증된 안전한 source-routing 구조를 유지하면서 새 결산 기능만 추가할 수 있다.

### v0.9.16 검증
임시 SQLite 테스트:
- 월초재고 100개×1,000원
- 당월매입 50개×1,200원
- 80개 판매
- 월말 70개×이동평균 1,066.67원
- 재고식 매출원가 약 85,333원 계산 확인.
- `monthly_closing_v0916.py`, 수정 `pnl_month_default_v0915.py` Python syntax compile 확인.

## 배포 규칙
1. feature branch에서 코드/문서/manifest 검증.
2. `VERSION.txt`와 `update/latest.json` 버전 일치.
3. 새 모듈을 manifest files에 포함.
4. main 반영은 별도 배포 단계에서 수행.
5. 사용자 DB는 절대 배포파일로 덮어쓰지 않는다.
