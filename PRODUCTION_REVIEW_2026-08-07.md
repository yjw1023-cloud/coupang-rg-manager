# Production/BOM Review — 2026-08-07

사용자가 제공한 현재 `core.py`와 `app_loader_v07.py`를 기준으로 생산/BOM 로직을 검토한 결과.

## 확인된 구조
- `bom_items.parent_product_id`와 `bom_items.component_product_id`는 모두 `products.id`를 참조한다.
- `inventory_txns.product_id`도 동일한 `products.id`를 참조한다.
- 따라서 BOM 구성품과 재고는 동일 product_id 기준으로 연결된다.

## produce() 동작
- 생산수량이 0 이하이면 거부.
- parent_product_id의 BOM을 조회하고 BOM이 없으면 거부.
- 각 구성품 필요수량 = qty_per × 생산수량.
- 전달된 `warehouse_id`의 현재고를 합산해 필요수량과 비교한다.
- 하나라도 부족하면 생산 전체를 중단하며 재고 트랜잭션을 만들지 않는다.
- 충분하면 각 구성품에 `생산소모` 음수 재고 트랜잭션을 기록한다.
- 같은 `warehouse_id`에 완제품 `생산입고` 양수 트랜잭션을 기록한다.
- 완제품 원가를 BOM 구성품 원가 합계로 갱신하고 production_orders에 이력을 기록한다.

## 실제 복제 DB 테스트
- 테스트 생산 1개: 구성품 재고 461 → 459 (BOM 2개 소모), 완제품 0 → 1 정상 동작.
- 재고 부족 BOM 테스트: `구성품 재고 부족` 오류 발생, inventory_txns 행 수 변화 없음(부분 차감 없음).

## 중요 위험점
- 현재 `produce()`는 **구성품 차감 창고와 완제품 입고 창고에 동일한 `warehouse_id`를 사용한다.**
- 사용자의 업무 원칙은 `매입 -> 자체창고 -> 생산/BOM -> 쿠팡RG`이다.
- 따라서 생산은 자체창고에서만 수행하고, 생산 후 완제품을 별도의 창고이동으로 쿠팡RG에 보내는 것이 안전하다.
- 생산 UI가 쿠팡RG를 선택하도록 허용하면 RG 재고에서 구성품을 찾고 차감하려 하므로 업무 흐름과 맞지 않는다.
- 권장 수정: production은 자체창고 고정 + self-BOM 방어 검증 + RG 입고는 별도 창고이동 처리.
