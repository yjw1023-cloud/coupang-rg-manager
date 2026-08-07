# v0.8.2 기존 ERP 이관 자동복구

2026-08-07 원본 Claude `erp.db`와 현재 `rocketgrowth.db` 전수 비교 후 확정한 복구 패치.

## 자동 복구 대상
- 2026-08-04 이관 실행 해시 `8ecb80fd3797...`인 사용자 DB에만 적용
- 적용 전 `data/backups/rocketgrowth-pre-v0.8.2-YYYYMMDD-HHMMSS.db` 자동 백업
- `repair_history`에 적용 이력을 남겨 재실행 시 중복 복구하지 않음

## 복구 내용
- pandas `Series.name` 버그로 숫자가 된 상품명 140개 원복
- `legacy_v07_mappings` 원본 상품명 199개 원복
- 숫자형 잘못된 `purchase_aliases` 199개 삭제
- 잘못 RG 상품에 합쳐진 JDS 내부품목 4개를 독립 상품으로 복원
  - JDS0477 점착식 나뭇잎 메모지
  - JDS0408 탁구공 수집기
  - JDS730 공 CD
  - JDS0159 부직포 신발 주머니
- 위 4개 품목의 매입 4건과 자체창고 재고 스냅샷 4건을 올바른 상품으로 이동
- self-BOM 3건 수정
- JDS730 병합 때문에 오염된 RG `95251561252` 원가를 9,950원으로 복구

## 검증
사용자가 업로드한 `rocketgrowth.db` 복제본에 패치를 실행해 확인:
- 상품명 복구 140개
- 매핑명 복구 199개
- JDS 분리 4개
- 매입 이동 4건
- 재고 이동 4건
- BOM 수정 3건
- alias 삭제 199개
- 매입 528건 / 재고 txn 222건 / BOM 41건 / 생산 97건의 행 수 유지
- 숫자 상품명 0개 / self-BOM 0개 / JDS→RG 오매칭 0개
- 두 번째 실행은 `already_repaired`로 종료하여 멱등성 확인

## 이후 이관 방지책
`erp_import_guard_v082.py`가 향후 기존 ERP 이관 시:
- JDS 코드를 RG 상품에 이름 유사도로 자동합치지 않음
- v0.7 코드의 `r.name` 행번호 버그를 실제 상품명 인덱스로 우회해 재발 방지
