# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 새 ChatGPT 세션 인계 기준이다.

## 현재 기준
- 저장소: `yjw1023-cloud/coupang-rg-manager`
- 현재 개발/배포 브랜치: **main**
- 현재 GitHub 배포 버전: **v0.9.80**
- Windows 로컬 ERP / Streamlit / SQLite `data/rocketgrowth.db`
- 자동 업데이트: GitHub `main`의 `update/latest.json`
- 사용자 데이터(`data`, `.venv`, `sample_data`)는 업데이트로 덮어쓰지 않는다.
- 새 세션은 반드시 `PROJECT_CONTEXT.md` → `SESSION_HANDOFF.md` → 최신 `SESSION_LOG_*.md` → `VERSION.txt` → `update/latest.json` → 현재 이슈 관련 최신 모듈 순서로 확인한다.

## 현재 최우선 확인 사항
### **사용자 로컬이 아직 v0.9.79이면 v0.9.80으로 업데이트 후 `🎯 목표·실적관리` 메뉴 표시 여부 확인**

v0.9.79에서 목표·실적관리 기능 자체는 추가됐지만, grouped sidebar가 최종 `options`에 실제 존재하는 메뉴만 표시하는 구조 때문에 새 메뉴가 사용자 화면에서 누락될 수 있었다.

v0.9.80 수정:
- `pnl_month_default_v0915.render_grouped_sidebar()`에서 기존 options를 `runtime_options`로 복사.
- `product_overview_v0977.PAGE_LABEL`, `goal_management_v0979.PAGE_LABEL`이 없으면 런타임 options에 강제로 추가.
- 이후 `sidebar_groups_v0917.render_sidebar()`로 전달.
- 따라서 legacy 메뉴 목록이 이전 상태로 남아 있어도 `🎯 목표·실적관리`가 손익·정산 그룹에서 누락되지 않도록 보강.
- `VERSION.txt`와 `update/latest.json`은 **0.9.80**으로 배포 완료.

현재 GitHub `main` 기준 관련 커밋:
- `cf7741f` — `fix: force goal page into grouped sidebar options`
- `bc01539` — `release: bump ERP to v0.9.80`
- `f72df45` — `release: publish v0.9.80 goal menu visibility fix`

다음 확인 순서:
1. 사용자 로컬 하단 버전이 `RG Manager v0.9.80`인지 확인.
2. `💰 손익·정산`을 펼쳐 `🎯 목표·실적관리`가 보이는지 확인.
3. 메뉴 진입 후 `진행현황 / 목표 설정 / 월말검증 / 목표이력` 4개 탭 표시 확인.
4. 테스트 목표 1건 저장 후 재진입하여 저장 유지 확인.
5. v0.9.80인데도 메뉴가 없으면 updater가 `pnl_month_default_v0915.py`를 실제 교체했는지, 실행 프로세스가 새 모듈을 읽는지 진단한다. `app.py`는 해당 모듈을 rerun마다 `sys.modules`에서 제거하도록 되어 있다.

## v0.9.79~0.9.80 — 목표·실적관리
관련 파일:
- `goal_management_v0979.py`
- `pnl_month_default_v0915.py`
- `sidebar_groups_v0917.py`
- `sidebar_lock_v0921.py`
- `app.py`
- `VERSION.txt`
- `update/latest.json`

의도한 메뉴 위치:
- `💰 손익·정산`
  - `📈 잠정손익`
  - `🎯 목표·실적관리`
  - `✅ 상품 확정손익`
  - `📒 월 결산`
  - `🔍 손익차이분석`
  - `📄 자료별 잠정손익`

화면 탭:
1. `진행현황`
2. `목표 설정`
3. `월말검증`
4. `목표이력`

목표 저장 테이블:
- `monthly_product_goals`
- `monthly_goal_reviews`

기능:
- 당월/익월/과거월 목표 설정
- 상품별 목표 판매수량, 목표매출, 광고예산, 목표이익, 메모
- 목표이익률, 목표 ROAS 자동 계산
- 전월 목표 복사
- 당월 잠정실적 / 과거월 확정실적 연결
- 판매자료 연속 입력일 기준 월말 예상 판매/이익
- 목표 대비 진행상태
- 월말 목표 vs 실제 비교
- 미달사유/검토메모 저장
- 상품별 월별 목표이력

## v0.9.78 — 대시보드 당월 자료 입력 현황
신규 파일: `dashboard_data_status_v0978.py`

대시보드 `월별 실적` 위에:
- 판매 Excel이 당월 1일부터 며칠까지 연속 입력됐는지
- 광고비 Excel이 당월 1일부터 며칠까지 연속 입력됐는지
- 다음 입력 시작일
- 전일 기준 미입력 일수
- 당월 인식 파일 수

중간 날짜가 빠지면 더 뒤 날짜 파일이 존재해도 최초 누락일부터 안내한다.

## v0.9.76~0.9.77 — 상품 통합현황
완제품 하나를 검색해서 한 화면에서 아래 내용을 보는 메뉴를 추가함.
- 판매/매출
- 반품/취소 및 비율
- 쿠팡RG 재고
- 반품창고 재고
- BOM 기초재고
- 현재 생산가능수량 및 병목 구성품
- 광고비 사용이력
- 잠정/확정 이익

관련 파일:
- `product_overview_v0976.py`
- `product_overview_v0977.py`

v0.9.77 UX 원칙:
- 완제품 선택/조회기간 selectbox에 진한 2px 테두리
- 기본 조회기간 `이번 달`
- 이번 달 = 당월 1일 ~ 어제
- 최근 30/90일도 오늘 제외
- 판매이력과 잠정 매출·이익 이력은 원본 업로드 파일 조각별 기간이 아니라 사용자가 선택한 조회기간 기준으로 집계

## v0.9.75 — ERP 전체 검색창 테두리
`search_ui_v096.py`에서 ERP 전체 `st.text_input` 검색영역을 강하게 표시.
- 흰 배경
- 2px 회색 외곽선
- hover 진해짐
- focus 파란 테두리/ring
- Streamlit DOM 차이를 고려해 wrapper + 실제 input fallback

## BOM
신규 3상품 BOM 문제는 사용자 확인 기준 **해결됨**.
사용자가 다시 요청하지 않는 한 seed/repair/reseed 작업을 하지 않는다.

완제품:
- `95912623408` — 어항용 뜰채 플라스틱 2p 수족관 새우 베타 구피, Free 2개
- `95912717676` — 프로 야구 포토카드 앨범 바인더, 화이트 50매
- `95912816721` — 대형 견출지 라벨 스티커 300장 라벨지, 혼합 300개입 1개

관련 과거 모듈:
- `requested_product_seed_v0970.py`
- `requested_product_bom_seed_v0971.py`
- `requested_product_bom_repair_v0972.py`
- `requested_product_bom_force_v0973.py`

## 판매/손익 고정 원칙
- `판매수량` = gross 실제 판매수량
- `취소수량` = 취소/환불
- `순판매수량` = signed net
- 손익/원가/재고 계산은 signed net 기준
- 반품판매 옵션은 원상품 손익에 합산
- 반품판매 원가는 원상품 원가 사용
- 광고비는 광고성과보고서 `광고집행 옵션ID` 직접 귀속
- 과거 수동 총광고비 매출비율 배분 방식으로 되돌리지 않는다.

확정 반품판매 연결:
- `95119299567` → `94475454519` 글라스 네일 파일 5P
- `95156135112` → `94350296878` 휴대용 가죽 구두주걱 미니 2P

## 별도 월별 손익 Excel 작업에서 확정한 계산 기준
- 파일명으로 매출 월 판단 금지.
- **발생일(결제완료일)** 기준으로 월 판단.
- 동일 상품명인데 ID가 다른 판매건은 반품상품 판매로 보고 원상품에 합산.
- 상품원가 = 월 판매수량 × ERP 개당 상품원가.
- 평균판매단가 = 매출액 ÷ 판매건수.
- 이익 = 매출액 - 판매수수료 - 입출고배송비 - 반품처리비용 - 광고비 - 상품원가.
- 7월 입출고배송/반품비는 사용자 허용 하에 ERP DB 확정자료 사용 가능.

## 재고실사 유지 원칙
- 재고 balance 직접 덮어쓰기 금지.
- `실사수량 - 업로드 시점 현재 ERP 재고` 차이만 `inventory_txns`의 `재고실사조정`으로 기록.
- 같은 파일 중복적용 차단.
- 자체창고 raw / 쿠팡RG finished / 반품창고 finished 필터 유지.

## 사용자 작업 방식/커뮤니케이션
- 반드시 존댓말.
- 수정 요청이 명확하면 GitHub 연결로 직접 수정하고 사용자에게 수동 코드 편집을 시키지 말 것.
- `수정해/만들어/해결해`는 해당 범위 GitHub write 승인으로 본다.
- main 배포 시 코드 먼저 → `VERSION.txt` → `update/latest.json` 마지막 순서.
- 실제 검증하지 않은 화면은 검증했다고 말하지 않는다.
- UI는 네이비/블루 계열, 검색/선택 입력영역은 경계가 분명하게.
- 상품/손익 화면은 내부 개발 필드 노출 금지.

## 다음 세션 시작 문구
`쿠팡 RG ERP 계속 개발하자. GitHub의 PROJECT_CONTEXT.md, SESSION_HANDOFF.md와 최신 SESSION_LOG를 읽고 현재 배포 버전과 마지막 미해결 이슈부터 확인해서 이어서 작업해.`

## 상세 로그
2026-08-13 후반 작업 기록은 `SESSION_LOG_2026-08-13_PART2.md`에 있다. 해당 로그의 v0.9.79 메뉴 미표시 이슈는 이후 v0.9.80에서 수정됐으므로 최신 상태 판단은 이 `SESSION_HANDOFF.md`, `VERSION.txt`, `update/latest.json`을 우선한다.
