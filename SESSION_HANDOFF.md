# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 최신 작업 기준이다.

## 현재 기준
- main 배포 버전: **v0.9.20**
- 현재 개발 버전: **v0.9.20**
- 현재 개발 브랜치: **`feature/v0.9.16-monthly-closing`**
- v0.9.16 월 결산 + v0.9.17 그룹형 사이드바 + v0.9.18 브랜딩 + v0.9.19 사이드바 표시 오류 수정 + v0.9.20 접힘 복구 버튼 수정까지 main 배포 기준으로 본다.
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
- 재고: 자체창고 / 쿠팡RG / 반품창고 / 불량·폐기
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
- 월 결산: `monthly_closing_v0916.py`
- 그룹형 사이드바: `sidebar_groups_v0917.py`
- 사이드바 접힘 복구: `sidebar_reopen_v0920.py`
- JD SYSTEMS 로고 원본: `jd_systems_logo.b64`

## 손익 구조
- `📈 잠정손익`: 월간 잠정 관리손익.
- `📄 자료별 잠정손익`: 판매통계 파일별 잠정손익.
- `✅ 상품 확정손익`: 쿠팡 월 정산 + ERP 상품원가를 이용한 상품별 확정 관리손익.
- `📒 월 결산`: 사업 전체 한 달 결산손익.
- `🔍 손익차이분석`: 잠정↔확정 차이.

## v0.9.16 월 결산
관련 모듈: `monthly_closing_v0916.py`
문서: `MONTHLY_CLOSING.md`

핵심 원칙:
- 한 달 매입액 전체를 비용 처리하지 않는다.
- `재고식 매출원가 = 월초재고 + 당월매입 - 월말재고`.
- `결산이익 = 실현매출 - 재고식 매출원가 - 수수료 - RG비용 - 반품비 - 광고비 - 기타비용`.
- 월초/월말 재고는 `inventory_txns`를 시간순으로 재생해 수량/이동평균원가로 평가.
- 불량·폐기 창고는 재고자산 제외.
- 마이너스 재고는 자산금액 0으로 평가하고 경고.
- 당월 매입은 `purchase_lines.purchase_date` 기준.
- 기타비용 월별 직접 추가/삭제 가능. 테이블 `monthly_closing_expenses`.
- 상품 확정손익 원가와 재고식 원가의 차이를 표시.
- 실제 은행 입출금이 아닌 발생기준 자금수지를 참고치로 별도 표시.

## v0.9.17 사이드바 메뉴 그룹화
관련 모듈: `sidebar_groups_v0917.py`

사이드바 구조:
- `🏠 대시보드`는 단독.
- `💰 손익·정산`: 잠정손익 / 상품 확정손익 / 월 결산 / 손익차이분석 / 자료별 잠정손익.
- `📦 재고·생산`: 재고관리 / 생산자료 / 반품관리.
- `🛒 매입·상품`: 매입관리 / 매입이력 / 품목관리 / 상품·원가.
- `📥 데이터·관리`: 기존ERP 이관 / 업로드 / 업데이트 / 설정 등 관리성 메뉴.

구현 원칙:
- 기존 페이지 handler와 page label은 바꾸지 않는다.
- 최종 flat menu 목록을 AST로 읽은 뒤 그룹 UI로 치환한다.
- 현재 선택 페이지가 속한 그룹만 기본 펼침.
- 새 미분류 메뉴도 사라지지 않고 `데이터·관리`에 자동 배치.
- v0.9.15의 안전한 `pnl_month_default_v0915.patch_source()` 마지막에 `sidebar_groups_v0917.patch_source()`를 적용한다.
- bootstrap `app.py`를 직접 크게 수정하지 않는다.

## v0.9.18 JD SYSTEMS 브랜딩
- 사용자 제공 원본 로고를 `jd_systems_logo.b64`로 저장하고 업데이트 manifest에 포함한다.
- 좌측 사이드바에 JD SYSTEMS 로고와 **`주식회사 제이디씨스템즈`** 표시.
- JD SYSTEMS 레드 톤을 선택 메뉴/hover/구분선에 사용.

## v0.9.19 사이드바 표시 오류 수정
사용자 실제 화면 확인 후 다음 문제를 수정했다.
- 브랜드 HTML이 코드블록처럼 보여 `<div class=...>`가 노출되던 문제.
- 그룹 내부 비선택 메뉴가 흰색 둥근 버튼으로 표시되던 문제.
- 기존 `RG Manager`, `쿠팡 로켓그로스 경영관리`, `v0.9.17 · grouped navigation` 문구가 새 브랜딩과 중복 표시되던 문제.

수정 방식:
- 로고는 HTML data-URI `<img>` 대신 `st.sidebar.image()`로 직접 렌더링. `jd_systems_logo.b64`를 bytes로 decode해 사용한다.
- 회사명은 한 줄 HTML로 렌더링해 Markdown 코드블록 오인 가능성을 제거.
- 모든 사이드바 버튼 기본 배경/테두리를 투명으로 강제하고 선택된 primary 메뉴만 JD SYSTEMS 레드 배경 + 흰 글씨.
- expander 그룹 헤더는 어두운 사이드바에 맞는 반투명 배경/흰 글씨로 통일.
- source AST 패치에서 구형 브랜드/버전 요소만 제거한다.
- `데이터는 이 PC의 SQLite에 저장됩니다.` 같은 일반 안내 caption은 유지한다.

## v0.9.20 사이드바 접힘 복구
관련 모듈: `sidebar_reopen_v0920.py`

사용자가 사이드바를 접은 뒤 다시 펼치는 컨트롤이 사라지는 실제 화면을 확인해 수정했다.
- 기존 ERP 테마가 Streamlit header를 숨겨도 접힌 사이드바 복구 컨트롤은 항상 보이도록 별도 CSS를 메인 영역에서 주입한다.
- Streamlit의 `collapsedControl`과 `stSidebarCollapsedControl` 두 selector를 모두 대응한다.
- 접힌 상태의 복구 버튼은 좌측 상단 40×40px JD SYSTEMS 레드 버튼으로 고정한다.
- header 자체는 높이 0/투명 상태로 두고 toolbar/status/main menu는 숨겨 기존 화면 레이아웃을 해치지 않는다.
- `pnl_month_default_v0915.render_grouped_sidebar()`가 먼저 `sidebar_reopen_v0920.apply(st_obj)`를 적용한 뒤 기존 그룹형 사이드바를 렌더링한다.

## 검증
- `monthly_closing_v0916.py` syntax compile 확인.
- `sidebar_groups_v0917.py` v0.9.19 syntax compile 확인.
- `sidebar_reopen_v0920.py`는 단순 Streamlit CSS 주입 모듈로 별도 DB/상태 변경 없음.
- 그룹 메뉴는 기존 canonical page label을 그대로 반환하므로 기존 page handler 분기와 호환된다.

## 배포 규칙
1. feature branch에서 코드/문서/manifest 검증.
2. `VERSION.txt`와 `update/latest.json` 버전 일치.
3. 새 모듈/로고 자산을 manifest files에 포함.
4. main 반영 후 ERP 프로그램 업데이트에서 새 버전을 적용.
5. 사용자 DB는 절대 배포파일로 덮어쓰지 않는다.