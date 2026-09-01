# SESSION LOG — 2026-09-01 PART 3

## v0.9.136 — 신규 매입품목 DB 영구저장 + 신규 쿠팡 완제품 실등록 + import 재실행 안정화

### 사용자 보고
- 16차 매입 Excel에서 신규 아이템을 등록했다고 생각했으나 최근 매입내역에 새 매입이 보이지 않음.
- 품목관리 `자체창고`에도 신규 JDS 품목이 보이지 않음.
- 별도로 요청한 신규 쿠팡 완제품 10개도 `쿠팡RG`에 보이지 않음.
- v0.9.134에서 `KeyError: 'dashboard_data_status_v09129'` 발생.

### 확인된 원인
1. `update/latest.json`은 v0.9.135를 설명했지만 실제 `app.py`에는 신규 10개/BOM seed의 런타임 실행 코드가 반영되어 있지 않았음.
2. v0.9.132 신규 매입품목 흐름은 새 JDS 생성 후 매칭 override를 Streamlit session_state에만 의존했고, 영구 source→product 매칭 테이블이 없었음.
3. `app.py`가 Streamlit rerun마다 `_original_import_module = importlib.import_module`로 다시 래핑하여 이전 실행의 `_rg_import_module`을 다시 감쌀 수 있었고, 반복 실행 후 일반 import가 중첩되면서 dashboard 모듈 import에서 KeyError가 발생할 수 있었음.

### v0.9.136 변경
- `app.py`
  - builtin `__import__` 기반 `_plain_import_module`을 안정적인 바닥 importer로 사용.
  - Streamlit rerun마다 이전 app import wrapper를 다시 감싸지 않음.
  - `product_visibility_v0995.apply_runtime(core)`를 app에서 명시 실행.
  - `requested_product_seed_v09133.apply(core)`를 매 app 실행 시 idempotent하게 실행하여 신규 쿠팡 완제품 10개/BOM을 실제 로컬 DB에 확인/등록.
  - purchase v0.9.1 패치 뒤 v0.9.136 영구매칭 overlay 적용.

- `purchase_new_item_persist_v09136.py` 신규
  - 신규 JDS raw 생성과 source-name/detail→product_id 매핑을 한 DB transaction으로 저장.
  - 명시적 `commit()` 추가.
  - `purchase_source_product_map` 테이블에 매칭 영구 저장.
  - 동일 매입파일 재업로드/페이지 이동/Streamlit rerun 후에도 새 JDS 매칭 자동 복원.
  - 과거 v0.9.132에서 품목만 생성되고 session 매칭만 유실된 경우, 동일 이름 raw 품목이 정확히 1개이면 재사용하여 중복 JDS 생성을 방지.

### 중요한 업무 규칙
- 신규 JDS 품목 등록/매칭과 **최종 매입확정**은 구분됨.
- 매입이력과 자체창고 입고수량은 기존 정상 `최종 매입확정` 파이프라인이 실행되어야 생성됨.
- 이전 실패 시도에서 최종 매입확정 DB 행이 남지 않았다면 원본 매입 Excel을 다시 올려 최종 확정해야 함.

### 배포
- VERSION.txt = `0.9.136`
- update/latest.json = `0.9.136`
- 신규 배포 파일: `purchase_new_item_persist_v09136.py`
