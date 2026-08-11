# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 새 ChatGPT 세션 인계 기준이다.

## 현재 기준
- main 배포 버전: **v0.9.36**
- 현재 개발 기준 브랜치: **main**
- 저장소: `yjw1023-cloud/coupang-rg-manager`
- 새 세션은 반드시 `PROJECT_CONTEXT.md` → `SESSION_HANDOFF.md` → `VERSION.txt` → `update/latest.json` → 최근 관련 모듈 순서로 확인한다.
- 이번 긴 세션의 상세 작업기록은 `SESSION_LOG_2026-08-11.md`에 저장한다.

## 프로젝트 기본 구조
- Windows 로컬 ERP / Streamlit / SQLite `data/rocketgrowth.db`
- 실행: `run.bat`
- 자동 업데이트: GitHub `main`의 `update/latest.json`
- 사용자 데이터(`data`, `.venv`, `sample_data`)는 업데이트로 덮어쓰지 않는다.
- 기본 실행 구조는 안정된 v0.7 loader를 `_code_base/app_loader_v07.py`에 유지하고, 최신 기능은 별도 patch module을 순차 적용하는 방식이다.
- 쿠팡 연결키는 가능한 경우 옵션ID를 우선한다.
- 실제 생산/판매는 재고 부족 때문에 막지 않고 마이너스 재고를 허용한다.

## 현재 손익 메뉴 구조
- `📈 잠정손익`: 월 단위 잠정 관리손익
- `📄 자료별 잠정손익`: 판매통계 파일별 잠정손익
- `✅ 상품 확정손익`: 쿠팡 월 정산 실제값 + ERP 상품원가 기반 상품별 확정 관리손익
- `📒 월 결산`: 사업 전체 한 달 결산손익
- `🔍 손익차이분석`: 잠정↔확정 차이

### 월 결산 원칙
- 한 달 매입액 전체를 비용으로 빼지 않는다.
- 재고식 매출원가 개념을 사용한다: 월초재고 + 당월매입 - 월말재고.
- 결산손익과 현금흐름/매입지출을 구분한다.
- 기타비용은 월별 직접 입력 가능.
- 상품 확정손익은 상품별 관리손익이며 `월 결산`은 사업 전체 결산 화면이다.

## 현재 사이드바/브랜딩 상태
관련: `sidebar_groups_v0917.py`, `sidebar_reopen_v0920.py`, `sidebar_lock_v0921.py`, `jd_systems_logo.b64`

- 메뉴를 그룹화:
  - 대시보드
  - 손익·정산
  - 재고·생산
  - 매입·상품
  - 데이터·관리
- 좌측 상단에 JD SYSTEMS 로고 + `주식회사 제이디씨스템즈` 표시.
- 대시보드 버튼은 선택되어도 빨간색으로 채우지 않고 사이드바 배경과 같은 톤으로 유지.
- 사이드바 접기/복구 문제를 여러 차례 수정한 끝에 현재 코드는 사이드바를 강제로 표시하고 접기 버튼을 숨기는 방향이다.
- 사용자 브라우저가 기존 접힘 상태를 기억해 사이드바가 사라지는 문제가 있었고, **새 포트 8517로 실행하자 정상 복구됨**. 사용자 로컬 `run.bat`는 8517 포트로 바뀌었을 가능성이 높다. 새 세션에서 포트를 임의로 8504로 되돌리지 않는다.

## 품목관리 — v0.9.24~v0.9.26
관련: `item_ui_v086.py`

### 입력 UI
- 입력칸이 배경과 구분되지 않는 문제를 수정.
- 품목관리의 text/select/number 입력칸은 청회색 배경 + 진한 테두리 + focus 파란색 강조를 사용한다.

### 자체창고 신규 품목코드
- 자체창고 품목 신규등록 시 품목코드는 자동으로 `JDS0001`, `JDS0002` ... 형식으로 채운다.
- 현재 사용 중인 코드뿐 아니라 **삭제/보관된 과거 JDS 코드도 영구 예약**한다.
- 과거 형식 `JDS2`, `JDS002`도 숫자 2가 이미 사용된 것으로 간주한다.
- 첫 번째 미사용 번호를 선택한다.

### 품목 삭제/복원
- 실제 DB 행 삭제가 아니라 `products.active=0` **보관삭제** 방식.
- 과거 매입·생산·재고·손익 기록을 보존한다.
- 어느 창고든 현재 재고가 0이 아니면 삭제 차단.
- BOM 연결이 있으면 경고/확인을 요구.
- 삭제품목 보기 및 복원 가능.
- 품목수정 화면의 단순 사용중 체크로 안전절차를 우회하지 않게 했다.

## 생산·BOM — v0.9.27~v0.9.36
주요 모듈:
- `bom_candidate_filter_v0927.py`
- `bom_delete_v0928.py`
- `bom_delete_cleanup_v0933.py`
- `bom_current_list_ui_v0935.py`

### BOM 후보 필터
- **완제품 선택창**: active `finished` 품목만 추천.
- **구성품 선택창**: active `raw` 즉 자체창고 관리품목만 추천.
- 반품창고 재고나 반품 할인판매 child 상품은 BOM 후보에서 제외.
- 삭제/보관 품목 제외.
- `CP-<옵션ID>`는 DB 내부 item_code로 유지할 수 있지만 사용자 화면에서는 `CP-` 접두사를 숨기고 숫자 옵션ID만 표시.
- 저장 시에도 parent=finished, component=raw 검증을 다시 수행해 잘못된 stale UI 값 저장을 막는다.

### BOM 삭제
목적은 **더 이상 생산/판매하지 않는 완제품의 현재 BOM 목록 정리**다.
- 현재 `bom_items` 연결만 삭제하고 과거 생산수량·생산원가·재고차감 이력은 유지.
- 삭제 전 BOM 내용을 변경로그에 남기는 기존 엔진을 유지.
- 구성품 1개 삭제 기능은 사용 목적과 맞지 않아 제거.
- 한 완제품의 BOM 전체 삭제만 제공.
- BOM 삭제 대상은 드롭다운이 아니라 **등록된 BOM 완제품을 표로 쭉 보여주고 왼쪽 선택 체크박스로 고르는 방식**.
- 보관/판매중지 완제품을 목록 위쪽에 우선 표시.
- 검색 가능.
- 실수 방지를 위해 한 번에 1개만 선택/삭제.

### 현재 BOM 표 — 최신 v0.9.36
사용자 요구:
- `소요수량 50.00`이 아니라 `50`처럼 자연수 표시.
- `현재 BOM 검색` 창을 표 위에 추가.
- 위쪽 `완제품 1개당 소요수량`도 1 이상 자연수만 입력.

v0.9.35에서 패치를 만들었지만 사용자 화면에서는 **검색창이 없고 50.00이 그대로 보여 실패**했다.
원인:
- 현재 BOM 표가 일반 DataFrame뿐 아니라 pandas Styler 형태로 전달되는 경우가 있어 v0.9.35 패치가 표를 인식하지 못함.

v0.9.36 수정:
- DataFrame과 Styler 모두 인식.
- `소요수량` Streamlit NumberColumn format을 `%d`로 강제.
- `현재 BOM 검색`에서 완제품명/구성품명 검색.
- `완제품 1개당 소요수량` number_input은 min=1, step=1, format=`%d`.
- 관련 파일: `bom_current_list_ui_v0935.py` (파일명은 v0935지만 내부 구현은 v0.9.36).

**중요: v0.9.36은 배포 직후 세션을 종료하는 단계라 사용자가 실제 화면에서 성공 여부를 아직 최종 확인하지 않았다. 새 세션 첫 확인 대상 중 하나다.**

## 잠정손익 스냅샷 — v0.9.29~v0.9.32
관련:
- `pnl_snapshot_fix_v0929.py`
- `pnl_month_autobackfill_v0932.py`
- `pnl_month_default_v0915.py`

문제 흐름:
1. 2026-08 판매자료는 존재하고 `자료별 잠정손익` 화면에서는 계산값이 보였지만 `잠정손익` 월 화면에는 `계산값이 아직 저장되지 않은 자료 1개`라고 나옴.
2. 기존 snapshot 저장은 화면의 옵션ID별 판매수량과 원본 판매통계가 완전히 동일해야 import_id를 역추적하는 구조라 반품 할인판매 통합/0판매 제외 후 실패 가능.
3. v0.9.29에서 import_id 직접 binding 모듈을 만들었지만 처음에는 실제 실행 체인 연결이 누락됨.
4. v0.9.30에서 실행 연결을 보강.
5. 구조 자체가 불안정하다고 판단해 v0.9.32에서 **월 잠정손익 화면이 누락 snapshot을 DB에서 직접 자동 재계산/저장**하도록 변경.

v0.9.32 원칙:
- 사용자가 `자료별 잠정손익`을 일부러 열지 않아도 된다.
- 월 화면 진입 시 해당 월 `sales_stats` import 중 snapshot 없는 자료를 찾아 재계산.
- 기존 잠정손익 규칙(반품 할인판매 보정, 이동가중원가, 수수료, 0판매 제외 등)을 적용.
- 같은 기간 광고성과가 있으면 자동 연결, 없으면 광고 미반영으로 계산.
- 월경계 파일은 기존 정책대로 자동 월합산에서 제외.
- 자동 계산 실패 시 예전처럼 조용히 숨기지 않고 오류 내용을 화면에 보여주는 방향.

**중요: v0.9.32 적용 후 사용자가 월 잠정손익이 정상 생성되는 최종 화면을 아직 명시적으로 확인해 주지는 않았다. 새 세션에서 필요 시 먼저 검증한다.**

## BOM NameError 수정 — v0.9.31
- 오류: `NameError: name 'bom_candidate_filter_v0927' is not defined`.
- 동적 legacy source가 모듈 이름을 직접 참조하지만 exec globals에 등록되지 않아 발생.
- `app.py`에서 `bom_candidate_filter_v0927`을 명시적으로 import하고 globals에 노출하여 해결.
- 사용자가 이후 BOM 화면이 다시 열린 것을 확인함.

## 주요 최신 파일 목록
- `app.py`
- `VERSION.txt`
- `update/latest.json`
- `item_ui_v086.py`
- `pnl_views_v0912.py`
- `provisional_pnl_ui_v0913.py`
- `pnl_month_default_v0914.py`
- `pnl_month_default_v0915.py`
- `pnl_month_autobackfill_v0932.py`
- `monthly_closing_v0916.py`
- `sidebar_groups_v0917.py`
- `sidebar_reopen_v0920.py`
- `sidebar_lock_v0921.py`
- `bom_candidate_filter_v0927.py`
- `bom_delete_v0928.py`
- `bom_delete_cleanup_v0933.py`
- `bom_current_list_ui_v0935.py`
- `pnl_snapshot_fix_v0929.py`
- `jd_systems_logo.b64`

## 최근 실제 사용자 화면에서 확인된 것
- 새 포트 8517 사용 후 사이드바/로고/그룹 메뉴 정상 표시.
- JD SYSTEMS 로고와 회사명 정상 표시.
- 자체창고 신규 품목코드 `JDS0001` 자동입력 정상 작동 확인.
- BOM NameError 수정 후 BOM 화면 정상 진입 확인.
- BOM 삭제 화면은 목적을 “판매중지 완제품의 전체 BOM 정리”로 재정의.
- 현재 BOM v0.9.35 검색/정수표시는 실제 화면에서 실패 확인 → v0.9.36으로 재수정, 최종 사용자 검증 대기.

## 새 세션에서 가장 먼저 할 것
1. `VERSION.txt`와 `update/latest.json`이 모두 v0.9.36 이상인지 확인.
2. 사용자가 v0.9.36 업데이트를 실제 적용했는지 확인.
3. `생산·BOM > BOM 구성`에서:
   - `현재 BOM 검색`이 보이는지
   - `소요수량`이 50.00이 아닌 50으로 보이는지
   - 위쪽 `완제품 1개당 소요수량`이 자연수 입력인지
   를 먼저 확인.
4. 필요 시 `잠정손익 2026-08` 자동생성(v0.9.32)도 최종 검증.
5. 버그가 남아 있으면 기존과 같은 우회 패치를 반복하기보다 **실제 실행 source/호출 체인을 먼저 확인**한다.

## 이번 세션에서 얻은 중요한 개발 교훈
- 별도 `.py` 파일을 manifest에 추가한 것만으로는 기능이 실행되지 않는다. **실제 patch/apply 호출 체인 연결을 반드시 검증**한다.
- Streamlit 표는 DataFrame뿐 아니라 Styler가 전달될 수 있으므로 렌더링 훅의 입력 타입을 확인한다.
- 동적 `exec()` source가 모듈 이름을 직접 참조하면 해당 모듈을 exec globals에 반드시 노출하거나 source에서 직접 import한다.
- 브라우저가 Streamlit sidebar collapsed state를 origin별로 기억할 수 있다. CSS만 반복 수정하기 전에 새 포트/origin으로 상태 문제인지 구분한다.
- 사용자 스크린샷에서 변화가 없으면 “업데이트됐을 것”이라고 가정하지 말고 실제 실행 연결/렌더링 타입부터 확인한다.

## 배포 규칙
1. 코드 수정 전 `PROJECT_CONTEXT.md`, `SESSION_HANDOFF.md`, `VERSION.txt`, `update/latest.json` 확인.
2. 사용자 승인 범위 안에서만 GitHub 쓰기/배포.
3. 코드 수정 후 `VERSION.txt` 증가.
4. `update/latest.json` version/message/files 동기화.
5. 새 모듈은 반드시 manifest에 포함하고 **실제 실행 apply/patch 연결 여부도 확인**.
6. 사용자 DB/data 폴더를 덮어쓰지 않는다.
7. 가능한 경우 syntax/간단 검증 후 main 반영.
8. 사용자 실제 화면 검증이 끝나지 않은 기능은 handoff에 `검증 대기`로 명시한다.
