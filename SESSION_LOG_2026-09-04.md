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

## v0.9.156 — 잠정손익 입출고·배송비 자동단가 수정

### 사용자 확인값
- 차 커피 수납 정리함 `94138813047`: 1개당 입출고배송비 2,800원.
- 수납 지퍼백 여행용 `94121677686`: 1개당 입출고배송비 2,800원.
- 나뭇잎 점착식 메모지 `94103975794`: 1개당 입출고배송비 2,625원.
- v0.9.155 화면에서는 각각 3,080원 / 3,080원 / 1,557원으로 표시되어 원천 계산을 재검증함.

### 확인한 기존 원인
- 기존 `core.get_products()`의 잠정 물류단가는 `logistics_fees.final_cost_vat / ABS(qty)`를 사용해 VAT 포함 금액을 자동 예상단가로 사용하고 있었음.
- `hist_inout_unit`과 `hist_delivery_unit`을 각각 `ORDER BY event_date DESC,id DESC LIMIT 1`로 독립 조회해 서로 다른 주문의 입출고비와 배송비가 섞일 수 있었음.
- `import_logistics()`는 원본 상세의 VAT 제외 최종비용을 `final_cost_prevat`에 보존하고, 별도로 월 합계 보정을 적용한 VAT 포함값을 `final_cost_vat`에 저장하고 있음.

### v0.9.156 변경 원칙
- 잠정손익 자동 물류단가는 `final_cost_prevat` 기준으로 계산.
- 입출고비와 배송비를 동일 `order_id`의 한 쌍으로 묶고, 두 비용이 모두 존재하는 가장 최근 정상 주문을 사용.
- 같은 주문/비용이 중복 저장된 경우 최신 행 한 건만 사용해 중복 정산자료가 단가를 배가시키지 않도록 함.
- 현재 정상상품과 상품명이 정확히 같은 과거 옵션ID 및 명시적 반품판매 alias의 정산이력을 같은 상품군으로 조회.
- 완전한 동일 주문 쌍이 없을 때만 각 비용의 최신 VAT 제외 행을 fallback으로 사용.
- 상품/월 수동 입출고·배송비 조정은 기존처럼 자동값보다 우선.
- 정산 원본 `logistics_fees`는 수정하지 않고 잠정손익 예상값만 변경.
- 기존 이동평균원가/수수료 보정 wrapper chain을 보존한 상태에서 `core.estimated_pnl` 반환값의 물류비 부분만 교정.
- `pnl_snapshot_refresh_v0966` 규칙 버전을 `0.9.156-logistics-preVAT-same-order`로 올려 기존 당월 snapshot이 새 규칙으로 재계산되도록 함.

### 관련 파일
- `provisional_logistics_unit_v09156.py`
- `provisional_logistics_runtime_v09156.py`
- `provisional_pnl_expense_guard_v09154.py`
- `VERSION.txt`
- `update/latest.json`

## v0.9.157 — 보조거울 반품 재판매 옵션 통합

### 사용자 확인 화면
- 정상 옵션 `95834379201`: `보조거울 백미러 사이드미러 2p 보조미러`
- 별도 표시된 옵션 `95928633818`: `보조거울 백미러 사이드미러 2p 보조미러, 2개, 천차종`
- 두 번째 옵션은 반품 재판매 자식 옵션으로 처리해야 하며 잠정손익에서 독립 상품행으로 표시하지 않음.

### 원인
- 기존 자동 반품판매 매칭은 안전한 동일 상품명 규칙을 사용함.
- 쿠팡이 반품 재판매 옵션명 뒤에 `, 2개, 천차종`을 붙여 정상 옵션과 이름키가 달라져 자동매칭에서 제외됨.
- 그 결과 자식 옵션이 별도 판매상품 행으로 남음.

### 수정
- `95928633818 -> 95834379201` 명시적 반품판매 alias 추가.
- 다음 잠정손익 렌더 시 기존 `ensure_known_aliases()` 경로가 로컬 DB alias를 저장하고 기존 판매자료를 복구.
- 자식 옵션의 일반 쿠팡RG `판매차감`을 제거하고 반품창고 `반품할인판매차감`으로 교정.
- 자식 옵션은 일반 관리품목에서 보관처리.
- 잠정손익에서는 자식 행을 삭제하고 정상 보조거울 행에 판매수량, 반품판매수량, 매출, 수수료, 물류비, 원가, 이익을 합산.

### 관련 파일
- `return_sale_alias_v09157.py`
- `return_sale_pnl_v0965.py`
- `app.py`
- `VERSION.txt`
- `update/latest.json`
