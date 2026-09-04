# SESSION LOG — 2026-09-04

## v0.9.148 — 당월 잠정실적 초기화

### 사용자 요청
- 잠정실적에서 **당월에 입력한 판매실적을 초기화하는 버튼** 추가.
- 판매통계 Excel로 입력했든 쿠팡 API로 입력했든 한 번에 다시 시작할 수 있어야 함.

### 구현 범위
- `잠정손익` 화면에 `YYYY-MM 당월 잠정실적 초기화` 영역 추가.
- 확인 체크 후에만 초기화 버튼이 활성화됨.
- 현재 달만 초기화 가능하며 과거 달은 이 기능으로 삭제하지 않음.

### Excel 판매통계 초기화
- 선택한 당월 안에 완전히 포함되는 `sales_stats` import 삭제.
- 해당 import의 `sales_stats` 상세행 삭제.
- 해당 import의 `provisional_pnl_snapshots` 삭제.
- 판매통계 업로드 때 생성된 `inventory_txns`의 `판매차감 / SALESSTAT-{import_id}`도 같이 삭제하여 재업로드 시 재고가 이중 차감되지 않도록 처리.
- 월을 걸친 판매통계는 일자별로 안전하게 분해할 수 없으므로 자동 삭제하지 않고 화면에 경고.

### 쿠팡 API 잠정자료 초기화
- 고객 결제일이 당월인 `coupang_rg_order_items` 삭제.
- 접수일이 당월인 `coupang_return_items`, `coupang_return_requests` 삭제.
- 철회일이 당월인 `coupang_return_withdrawals` 삭제.
- 주문/반품 API의 기존 성공 동기화 이력은 삭제하지 않고 `status='reset'`으로 변경해 감사이력을 보존.
- `status='success'`만 사용하는 API 조회기간 계산에서 초기화 전 동기화 이력이 제외되므로, 초기화 후 일부 기간만 재동기화했을 때 과거 전체월 동기화가 남아 있는 것처럼 보이지 않음.

### 삭제하지 않는 자료
- 확정손익용 `coupang_revenue_items` 매출·수수료 API 자료.
- 쿠팡 API 재고 스냅샷 및 재고조정.
- 지급내역/확정 정산자료.
- 매입, 생산, BOM, 품목정보.
- 광고성과보고서 및 광고자료.
- 잠정손익 수동조정.
- API 연결정보 및 API 동기화 감사이력 자체.

### 감사기록
- `provisional_month_reset_log` 테이블에 월, 초기화 시각, 처리건수를 JSON으로 남김.

### 관련 파일
- `provisional_month_reset_v09148.py`
- `pnl_month_v0965.py`
- `test_provisional_month_reset_v09148.py`
- `VERSION.txt`
- `update/latest.json`

### 검증 항목
- 당월 판매통계/주문/반품/철회만 삭제되는지.
- 전월 판매통계와 전월 API 주문은 유지되는지.
- 월경계 판매통계는 유지되는지.
- Excel 판매차감 원장이 함께 제거되는지.
- 확정 매출 API와 광고자료는 유지되는지.
- 당월 주문/반품 성공 sync run은 `reset`, 매출 sync run과 전월 sync run은 `success`로 유지되는지.
- 과거 월 초기화 호출은 거부되는지.
