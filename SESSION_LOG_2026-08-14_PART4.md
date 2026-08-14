# SESSION LOG — 2026-08-14 PART4

## v0.9.106 배포

사용자 요청으로 이번 3개 쿠팡RG 상품을 업데이트 직후 1회 자동 생산하도록 배포함.

대상:
- 옵션ID 95912816721 대형 견출지 라벨 스티커 300장: 46개
- 옵션ID 95912717676 프로 야구 포토카드 앨범: 30개
- 옵션ID 95912623408 어항용 뜰채 2P: 48개

처리 규칙:
- 생산에 부족한 BOM 원재료만 자체창고에 `불용재고전환입고` 처리
- 매입이력은 생성하지 않음
- 전환입고 원가는 원재료 `products.unit_cost`의 ERP 등록원가 사용
- 완제품 원가는 현재 BOM 소요량 × 원재료 ERP 등록원가 합계
- 원재료 ERP 등록원가가 0원이거나 BOM/상품 연결이 비정상이면 전체 자동생산 중단
- 불용재고 전환입고 + BOM 생산소모 + 쿠팡RG 완제품 입고 + 생산이력을 단일 SQLite 트랜잭션으로 처리
- `rg_one_time_operations`의 `v0.9.106-auto-produce-requested-3` 키와 synthetic production batch hash로 재실행/재업데이트 중복생산 차단

관련 파일:
- auto_produce_requested_v09106.py
- production_dormant_stock_v09106.py
- app.py
- update/latest.json
- VERSION.txt = 0.9.106

main 배포 완료.
