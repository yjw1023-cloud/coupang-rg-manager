# ERP SESSION HANDOFF — latest working context

이 문서는 `yjw1023-cloud/coupang-rg-manager`의 새 ChatGPT 세션 인계 기준이다.

## 현재 기준
- main 배포 버전: **v0.9.62**
- 현재 개발 기준 브랜치: **main**
- 저장소: `yjw1023-cloud/coupang-rg-manager`
- 새 세션은 반드시 `PROJECT_CONTEXT.md` → `SESSION_HANDOFF.md` → `SESSION_LOG_2026-08-12.md` → `VERSION.txt` → `update/latest.json` → 최근 관련 모듈 순서로 확인한다.
- 오늘 상세 작업기록은 `SESSION_LOG_2026-08-12.md`에 저장했다.

## 프로젝트 기본 구조
- Windows 로컬 ERP / Streamlit / SQLite `data/rocketgrowth.db`
- 실행: `run.bat`
- 자동 업데이트: GitHub `main`의 `update/latest.json`
- 사용자 데이터(`data`, `.venv`, `sample_data`)는 업데이트로 덮어쓰지 않는다.
- 기본 실행 구조는 안정된 v0.7 loader를 `_code_base/app_loader_v07.py`에 유지하고 최신 기능은 별도 patch module을 순차 적용하는 방식이다.
- 쿠팡 연결키는 가능한 경우 옵션ID를 우선한다.
- 실제 생산/판매는 재고 부족 때문에 막지 않고 마이너스 재고를 허용한다.

## 현재 핵심 손익 메뉴
- `📈 잠정손익`: 월 단위 잠정 관리손익
- `📄 자료별 잠정손익`: 판매통계 파일별 잠정손익
- `✅ 상품 확정손익`
- `📒 월 결산`
- `🔍 손익차이분석`

## 2026-08-12 최신 변경 — 광고비 처리
기존 월 광고비 수동입력 + 매출비율 배분 방식은 **폐기**했다.

현재 방식:
- 잠정손익에서 쿠팡 `광고성과보고서 Excel` 업로드
- `광고집행 옵션ID` 기준으로 광고비를 해당 상품에 직접 귀속
- 동일 옵션ID 여러 광고행 합산
- 판매가 없고 광고만 집행된 옵션도 광고비 손실행으로 표시
- 광고성과보고서 파일명의 `YYYYMMDD_YYYYMMDD` 기간 자동 인식
- 동일 파일 중복 업로드 차단
- 기간 중복 시 교체 가능
- 과거 수동 광고비는 계산에서 사용하지 않음
- 과거 `provisional_manual_ad_spend` 레코드는 잠정손익 진입 시 자동 정리
- 상품별 예상 실현단가 / 입출고비 / 배송비 수동조정 기능은 유지

실제 검증 파일:
- 2026-08-01~2026-08-11
- 광고집행 옵션 19개
- 광고비 합계 2,478,464원
- 옵션ID별 귀속 후 총액 일치 확인

관련 파일:
- `provisional_ad_report_v0956.py`
- `provisional_manual_cleanup_v0957.py`
- `pnl_manual_blocks_v0955.py`
- `provisional_manual_adjust_v0952.py`

## 2026-08-12 최신 변경 — 잠정손익 표
사용자 요구:
- 헤더 배경색 + 굵은 글씨
- 상품명 좌측 정렬 + 들여쓰기
- 숫자 중앙 정렬
- 좌우 가로 스크롤 허용
- 내부 세로 스크롤 제거
- 헤더 클릭 오름차순/내림차순 정렬

진행 이력:
- v0.9.58: `st.dataframe` CSS/Styler 방식 실패
- v0.9.59: HTML 테이블로 디자인 문제 해결, 정렬 기능 사라짐
- v0.9.60: URL/query parameter 헤더 정렬 → ERP 전체 재로드 문제 발생
- v0.9.61: embedded HTML/JavaScript 내부 정렬로 변경
- v0.9.62: 업데이트 후 import 캐시에 구버전 렌더러가 남지 않도록 모듈 캐시 무효화/재로드 보강

**현재 사용자 최종 확인 상태:** 헤더 클릭 정렬이 정상 동작한다고 확인함 (`아니다 된다`).

현재 표 상태:
- 파란 헤더 + 굵은 글씨
- 상품명 좌측 정렬
- 숫자 중앙 정렬
- 전체 행 세로 표시
- 가로 스크롤 유지
- 헤더 클릭 시 ERP 재시작 없이 클라이언트 내부에서 정렬

관련 파일:
- `pnl_month_v0959.py`
- `pnl_month_v0960.py`
- `pnl_month_v0961.py`
- `pnl_month_default_v0915.py`
- `app.py`

## 중요한 금지/유지사항
- 헤더 정렬을 다시 `<a href>` / query parameter 방식으로 구현하지 말 것. ERP 재로드 문제가 있었다.
- 광고비를 다시 수동 총액 + 매출비율 배분 방식으로 되돌리지 말 것.
- 광고성과보고서 업로드 메뉴는 잠정손익에서 계속 보여야 한다.
- 사용자의 로컬 SQLite DB에 직접 접근할 수 없으므로 필요한 DB 정리는 업데이트 코드가 실행되도록 구현한다.

## 기존 기능 중 계속 유지해야 할 것
- 품목코드 JDS 자동부여 및 과거 코드 영구예약
- 품목 보관삭제/복원
- 생산/BOM 후보 필터와 BOM 전체삭제 정책
- 월 잠정손익 snapshot 자동 backfill
- 사이드바 그룹화/로고/8517 포트 관련 기존 안정화
- 월 결산은 매입액 전체 비용처리가 아니라 재고식 매출원가 원칙 유지

## 다음 세션에서 가장 먼저 할 것
1. `PROJECT_CONTEXT.md`
2. 이 `SESSION_HANDOFF.md`
3. `SESSION_LOG_2026-08-12.md`
4. `VERSION.txt`와 `update/latest.json`이 모두 **v0.9.62 이상**인지 확인
5. 사용자가 새 요구를 주면 현재 `main` 기준으로 이어서 작업

## 최신 주요 파일
- `app.py`
- `VERSION.txt`
- `update/latest.json`
- `provisional_ad_report_v0956.py`
- `provisional_manual_cleanup_v0957.py`
- `provisional_manual_adjust_v0952.py`
- `pnl_manual_blocks_v0955.py`
- `pnl_month_v0959.py`
- `pnl_month_v0960.py`
- `pnl_month_v0961.py`
- `pnl_month_default_v0915.py`
- `pnl_month_autobackfill_v0932.py`
- `provisional_pnl_ui_v0913.py`
- `monthly_closing_v0916.py`
- `item_ui_v086.py`
- `production_batch_v095.py`
- `bom_candidate_filter_v0927.py`
- `bom_delete_cleanup_v0933.py`
