# 평가·성과 실제화 3단계 — 평가 진행 · 평가 결과 · 이의 신청 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 로그인의 평가 진행 · 평가 결과 탭을 서버 자료로 바꾼다. HR이 회차를 정하고 시작하면 서버가 평가 대상 · 묶음 · 평가자 · 배정자 · 확정 KPI를 떠 두고, 자기평가 → 평가자 점수·등급 의견 → 등급 배정·마감 → 대표이사 확인 → HR 확정 → 결과 공개 → 이의 신청이 서버에서 돈다. 공개 전 등급은 서버 응답에서부터 새지 않는다.

**Architecture:**
- **규칙(순수 함수):** 새 파일 `hr_eval/domain/eval_rules.py` — 총점 · 점수 기준 · 연차 규칙 · 줄 수 있는 가장 높은 등급 · 묶음 정원 검사 · 단계 순서 · 묶음 상태 · 묶음 열쇠 · 공개 전 보는 수준 · 고친 내용. 정원 숫자는 이미 있는 `quota.calculate_department_quota`(화면 `evQuota`와 같은 규칙 — N ≤ 4 묶음 정원, N ≥ 5 A·B·D 각 N×10% 사사오입)를 쓴다. 8월의 `grading.py`는 쓰지 않는다(점수 기준 · 연차 · KPI 없는 사람이 없다 — selfcheck가 쓰므로 지우지 않는다).
- **서버:** 새 블루프린트 `backend/evaluation.py`(`/api/eval/…`, `app.py`가 붙인다). **읽기는 `GET /api/eval/state` 하나**이고 보는 사람에 맞춰 줄을 깎는 곳도 한 곳(`_row_json`)이다 — 공개 전 숨김을 한 곳에서 지킨다. 쓰기는 단계마다 작은 길. `kpi.py`의 `_Refuse` · `_txn` · `_body` · `_chars` · `load_org` · `plan_year` · `YEARS` · `UID`를 가져다 쓴다(새로 만들지 않는다).
- **떠 두기:** HR이 [평가 시작]을 누르는 순간 한 트랜잭션에서 대상 · 묶음 · 평가자 · 배정자 · 확정 KPI 판을 `ev_targets`에 적는다. 그 뒤 조직 · 구성원 설정이 바뀌어도 이번 평가는 그대로다(설계 8장 "규칙만 정한 것" 3 — 평가 시작일의 소속).
- **누가 하나:** 점수(평가자)와 배정(배정자)은 **떠 둔 사람**으로 판단한다 — `access.can`의 `SCORE` · `ASSIGN` 표는 "한 단계 위" 평가자(팀장 없는 팀의 팀원을 본부장이 평가하는 등)를 담지 못해 쓰지 않는다. 묶음 확인 · 돌려보내기 · 확정 · 관리 · 이의 신청 · 결과 조회는 `access.can`(`CONFIRM` · `CONFIRM_EDIT` · `SEND_BACK` · `FINALIZE` · `MANAGE` · `APPEAL_*` · `VIEW_RESULT`)을 그대로 쓴다. 판단은 `_can` · `_pool_can` 두 함수에만 있고, 화면에 보내는 칸과 쓰기 길의 검사가 같은 함수를 쓴다.
- **화면:** 시연 화면(`ev*`)을 그대로 쓴다 — 인사담당자가 이미 고른 모양이다. 실제 로그인이면 `evPeople` 자리를 서버 줄로 채우고(`evFromLive`), **판단 함수**(누가 고칠 수 있나 · 숨기나)는 서버가 줄마다 보낸 `can` · `hidden`을 읽는다. **동작 함수**는 서버에 보내고 다시 읽는다(`evLiveDo`). 새 화면 모양은 만들지 않는다.

**Tech Stack:** Python 3 + Flask + SQLite(`backend/`), 단일 HTML · 바닐라 JS(`public/index.html`), 검사 `hr_eval.selfcheck` · `auth_check` · `hr_eval.sql.check_schema`, 화면 확인 `클로드 코드/devlive.py`(가짜 하이웍스 — 저장소 밖) + playwright.

**Spec:**
- `docs/superpowers/specs/2026-10-20-kpi-progress-and-live-evaluation-design.md` 9장(3단계 원칙) · 10장(서버 · 보안) · 3장(시기)
- 규칙 원본: `docs/superpowers/specs/2026-09-21-kpi-goal-screen-design.md` **8장 전부**(묶음 셋 · 순서 · 점수 기준 · 정원 · 연차 · 근태 잠금 · 단계별 마감일 · 공개 전 숨김 · HR에 맡기기 · 이의 신청)
- 권한 표: `docs/superpowers/specs/2026-09-29-hiworks-sso-login-design.md` 6장 "평가에서 누가 무엇을 하나" · "HR에 맡기기" · "결과 공개 전 열람 제한"
- 시연 화면(같아야 할 동작): `public/index.html`의 `/* ---------------- 평가 진행` ~ `/* ---------------- 휴대폰에 설치` 앞 (`evSelfCheck` · `evLineSelfCheck` · `evrSelfCheck`가 규칙을 보여 준다)

**PR 둘 (인사담당자 결정 2026-10-26)** — **Part A = PR ① 평가 진행**(Task 1~8: 회차 정하기 · 시작 · 근태 · 자기평가 · 증빙 · 평가자 · 등급 배정 · 본부장 마감), **Part B = PR ② 확인 · 확정 · 공개 · 결과 · 이의 신청**(Task 9~13). ①을 merge한 뒤 ②를 시작하고, 그때 Part B의 이름이 ①에서 실제로 만든 것과 같은지 한 번 맞춘다. ①만 올라가도 운영 화면은 그대로다 — 2026년에는 평가를 시작할 수 없다(R2).

**인사담당자 결정 (2026-10-26, "정할 것은 기본값으로")**
- **중도 입사자**는 HR이 그 사람에게 KPI 작성 기간을 따로 열어(이미 있는 사람별 기간 `kpi_windows`) KPI를 쓴다. **연중에 부서를 옮긴 사람**은 평가 시작일의 소속으로 평가자를 정한다(= 시작 때 떠 둔다, R1).
- HR의 평가·성과 화면을 전환 단추 둘로 나눈 방식(처음은 "내 KPI · 평가")은 **그대로**.
- **2026년 결과(포털 밖에서 한 평가)는 포털 "지난 결과"에 넣지 않는다** — 지난 결과는 포털에서 공개한 해만 보인다.

**내 판단 (기본값 — PR 본문과 정리 이슈로 인사담당자에게 알린다)**
- **R1 시작은 HR이 누른다:** 진행표의 [평가 시작]은 평가 시작일 당일부터 누를 수 있다. 누르는 순간 명단 · 묶음 · 평가자 · 배정자 · KPI 판을 떠 둔다. 시연처럼 날짜만으로 저절로 시작하지 않는다 — 떠 두는 때가 분명해야 해서다.
- **R2 포털 평가는 2027년부터:** `FIRST_YEAR = 2027`. 그 전 해는 회차를 정할 수도 시작할 수도 없고(403) 화면은 지금 안내("2026년도 평가는 기존 방식으로 진행됩니다")를 그대로 보인다. 로컬 확인용으로만 환경 변수 `HR_EVAL_FIRST_YEAR`(devlive가 2026으로 띄운다)를 읽는다.
- **R3 평가 대상** = 시작할 때 활성 · 구성원 설정 "평가 대상" 켜짐 · 본부장 · 대표이사 아님. 포털 사용이 꺼진 사람도 들어간다(HR 명단에 "포털 사용 꺼짐" 표시 — HR이 "자기평가 없이 진행"으로 넘긴다).
- **R4 KPI를 쓰는데 확정 KPI가 없는 사람**은 KPI 없이 평가자가 등급만 매기는 방식(현장직과 같은 길)으로 떠 두고, HR 명단에 "확정 KPI 없음"을 표시한다. 시연이 HR에게 권하던 길("KPI 작성을 꺼 팀장 평가로")과 같다. 빼려면 HR이 "평가에서 제외"한다.
- **R5 묶음 · 평가자 · 배정자:**
  - 묶음: 팀장 → 전사 팀장(`LEADERS`) · 대표이사 직속 팀(지원·총무) 소속, '임원' 같은 장 없는 부서 소속, 본부가 없는 사람 → 직속부서 팀원(`DIRECT`) · 그 밖 → 그 본부의 본부 팀원(`DIV:<본부 번호>`).
  - 평가자 = 시작 때의 바로 위 관리자(`manager_of` — 팀장이 없으면 본부장, 본부장도 없으면 대표이사인 "한 단계 위" 규칙이 이미 들어 있다). HR 담당자는 HR 팀장(지원팀 팀장)이 평가자다.
  - 배정자 = 본부 팀원 묶음은 그 본부를 맡은 본부장(없으면 대표이사), 전사 팀장 · 직속 묶음은 대표이사.
- **R6 평가자가 비활성이 되면** HR이 명단에서 평가자를 바꾼다(점수를 내기 전까지). 배정자(본부장)가 비활성이 되면 그 묶음은 대표이사가 배정한다.
- **R7 단계별 마감일은 순서가 맞아야 저장된다**(시연은 경고만): 평가 시작일 ≤ 자기평가 ≤ 평가자 평가 ≤ 등급 배정 ≤ 대표이사 확인 ≤ HR 확정, 평가 시작일 ≤ 증빙 ≤ 평가자 평가. 평가 시작일은 그해 안, 나머지는 다음 해 2월 말일까지(3월 연봉계약 전).
- **R8 증빙 파일:** 실행목표마다 5개 · 한 파일 10MB · PDF · 그림(PNG · JPG) · 엑셀 · 워드 · 한글(xlsx · xls · docx · doc · hwp · hwpx). 형식은 파일 이름이 아니라 첫 바이트로 서버가 다시 본다. 서버 `data/eval_files/`(공개 폴더 밖)에 두고 잠그지 않는다(급여 자료가 아니다). 내려받기는 본인 · 평가자 · 배정자 · 그 본부의 본부장 · 대표이사 · HR.
- **R9 글자 한도(서버 · 화면 · DB 같게):** 실적 500 · 평가자 코멘트 500 · 윗사람 코멘트 500 · 가점 사유 100 · 제외 사유 100 · 돌려보내는 이유 200 · 이의 신청 사유 500 · HR 답변 500. 가점 0~10점(0.5점 단위) · 연차 초과 0~365일(0.5일 단위) · 지각 0~999회.
- **R10 감사로그:** 회차 정하기 · 시작 · 근태 입력(사람 수만) · 근태 마침 · 제외 · 되돌림 · 자기평가 없이 · 평가자 바꾸기 · 자기평가 제출 · 평가자 제출 · 본부장 마감 · 확인 · 돌려보냄 · 확정 · 되돌리기 · 공개 · 이의 신청 · 답변 · 이의 확인 · 이의 돌려보냄. **등급 · 점수 · 근태 숫자 · 글은 넣지 않는다**(공개 전 등급이 감사로그로 새지 않게). 임시 저장 · 고치기 하나하나는 남기지 않는다. HR이 대표이사 대신 하면 `on_behalf_of`.
- **R11 HR에 맡기기는 KPI와 같은 스위치 하나**(`ceo_delegation`, `/api/kpi/delegation`)를 쓴다 — 시연의 `evProxy`도 KPI 승인과 평가를 함께 맡기는 하나다.
- **R12 휴대폰 알림:** 평가 시작 → KPI가 있는 평가 대상 · 자기평가 제출 · 자기평가 없이 진행 → 평가자 · 평가자 제출 → 배정자(전사 팀장 묶음은 대표이사) · 본부장 마감 → 대표이사(맡겼으면 HR) + HR · 돌려보냄 → 본부장 · 대표이사 확인 → HR · 결과 공개 → 평가받은 사람 전원 · 이의 신청 → HR · 등급을 바꾸는 답변 → 대표이사(맡겼으면 HR) · 답변 끝 → 신청한 사람. 글에 등급 · 이름이 없고 내용 줄은 "PC에서 확인해 주세요"(평가·성과는 PC 전용). 처리할 일 숫자(`push.pending`)에 평가 몫을 더한다 — 화면 `TODO.e2` · `TODO.e3`와 같은 셈.
- **R13 화면에 보일 해:** 그해 회차가 시작돼 있으면 그해. 1~2월에 지난해 회차가 아직 안 끝났으면(공개 전 · 이의 신청 7일 안 · 처리 중인 이의 신청) 지난해. 그 밖은 올해.
- **R14 실제 로그인의 평가 결과에는 연봉계약 이야기가 없다**(인사담당자 2026-10-22 — 계약서는 등급과 잇지 않는다): 결과 카드의 "능력급 인상률" 칸과 "이 등급이 연봉계약서 능력급에 반영됩니다" 줄을 실제 로그인에서 뺀다.

## Global Constraints

**1 · 2단계에서 그대로 이어지는 것**
- **시연 모드는 그대로:** `AUTH.live`가 거짓이면 평가 진행 · 평가 결과 화면이 바뀌지 않는다. 시연 HTML 해시(역할 7 × HR 화면 둘 × 평가 진행 · 평가 결과 × 시연 단추 세 상태)가 이 계획 시작 커밋과 같아야 한다. `evSelfCheck()` · `evLineSelfCheck()` · `evrSelfCheck()`가 그대로 통과한다.
- **판단은 서버가 다시 한다:** 단계 · 마감일 · 평가자 · 배정자 · 묶음 · 정원 · 점수 기준 · 연차 · 보는 범위 · 글자 한도. 화면의 단추 막기는 안내다.
- **날짜는 한국 날짜:** `auth._today()` / `auth._now()`(서버 시계는 UTC). 시각 글자는 `auth._iso` 모양 `YYYY-MM-DDTHH:MM:SSZ`. 마감일 "까지"는 그날 한국 23:59:59까지다.
- **공개 저장소:** 시험 자료 이름은 전부 가상(`가상…`)이다. 휴대폰 번호 모양의 문자열을 쓰지 않는다. 커밋 · PR에 실명을 쓰지 않는다.
- **줄 끝과 패치:** 작업 폴더 파일은 CRLF다. `index.html`의 여러 곳을 고칠 때는 `assert s.count(old)==1` 패치 스크립트를 쓴다(한글이 든 바꾸기는 Write로 만든 .py 파일로).
- **화면 문구:** 쉬운 한국어("~해요"). 상태 글자 앞의 아이콘은 고정 폭 자리를 비워 둔다. 평가·성과는 PC 전용이다(그대로).
- **글 규칙:** 사람이 쓴 글은 `kpi._chars`(제어문자 · 짝 없는 서로게이트 400)를 지나고, 앞뒤 공백을 뺀 길이로 센다.

**3단계에서 새로 정하는 것**
- **DB:** 지금 판 19 → `020_eval.sql`(판 20). 다른 PR이 020을 먼저 merge하면 main을 합치고 다음 번호로 옮긴다(`check_schema` 0번이 막는다). 8월의 `evaluation_periods` · `department_quotas` · `evaluations`(001)는 지금 규칙과 맞지 않고 운영에 자료가 없어 **쓰지도 건드리지도 않는다** — 새 표는 이름 앞에 `ev_`를 붙인다.
- **공개 전 등급은 어디에도 새지 않는다:** 서버 응답(어느 길이든) · 감사로그 · 서버 로그 · 알림 글. 깎는 곳은 `_row_json` 한 곳이고 다른 길은 그 함수를 거친다.
- **점수:** 총점 = Σ(가중치 × 등급 비율 S 110 · A 100 · B 90 · C 80 · D 70 %) + 가점, `Decimal`로 셈하고 소수 첫째 자리 사사오입(화면 `evScore`의 `Math.round(x*10)/10`과 같다), 상한 110. **점수 기준은 "초과"** — S 100 초과 · A `band_a` 초과 · B `band_b` 초과 · C `band_c` 초과 · 그 아래 D(기본 90 · 80 · 70, HR이 바꾼다). 90.5점은 A, 딱 90점은 B. 점수보다 높은 등급은 줄 수 없고 낮게는 줄 수 있다. KPI가 없는 사람은 점수가 없어 연차 규칙만 따진다.
- **연차:** 초과 5일 이상(`leave_x2 >= 10`)이면 S·A·B 불가 — 평가받는 모든 사람. 지각은 참고(KPI 없는 사람만 화면에 보인다).
- **근태 잠금:** 그 사람이 배정 단계를 지나면(`at(stage, assign_stage(pool)) > 0`) 또는 제외되면 근태를 못 고친다.
- **마감일:** 자기평가만 마감일 다음 날부터 막는다(403). 증빙은 증빙 마감일까지 · 평가자가 점수를 내기 전까지. 나머지 단계는 지나도 하되 "기한 지남"으로 보인다.
- **숫자 칸은 정수로 저장:** 가점 `bonus_x2`(0~20) · 연차 초과 `leave_x2`(0~730) · 평가자가 낸 총점 `sub_score_x10`(0~1100). API는 소수(2.5 · 5.5 · 90.5)로 주고받는다.
- **알림:** `push.notify(uids, kind)`는 저장(commit)한 뒤 부른다. 새 종류는 넷 — `eval_turn`("인사평가에서 처리할 일이 있어요" / "PC에서 확인해 주세요" / `perf.e2`) · `eval_open`("인사평가 결과가 공개됐어요" / "PC에서 확인해 주세요" / `perf.e3`) · `eval_appeal`("처리할 이의 신청이 있어요" / "PC에서 확인해 주세요" / `perf.e3`) · `eval_answer`("이의 신청에 답변이 왔어요" / "PC에서 확인해 주세요" / `perf.e3`). ①은 `eval_turn`만 쓴다.

## Review Focus

1. **공개 전 등급이 평가자에게 새지 않는다:** 팀장이 B로 낸 사람을 본부장이 C로 고치면, 팀장이 받는 `GET /api/eval/state`의 그 줄 어디에도(`grade` · `ev[].grade` · `subScore` · `bonus` · `headComment` · `edited` 글자) C나 고친 총점이 없고 `edited`는 `true`(고쳤다는 사실)뿐이다. 본인 줄에는 공개 전 등급 · 점수 · 코멘트가 아예 없다. 감사로그 `after_data`에 등급 · 점수 칸이 없다. → Task 4 `공개_전_평가자는_낸_것만`, `본인은_자기평가만`, Task 6 `고친_것은_사실만_알린다`, Task 9 `공개하면_보인다`
2. **마감일 · 한국 날짜 경계:** 자기평가 마감일이 12/5이면 UTC 12/5 14:59(한국 23:59)에 제출 200, 15:00(한국 12/6 0시)에 403. 증빙 마감도 같다. 이의 신청은 공개일 + 7일 한국 23:59까지. → Task 5 `자기평가는_마감일_밤까지`, `증빙은_마감일까지_점수_전까지`, Task 10 `이의_신청은_7일`
3. **점수 기준 · 연차 경계:** 총점 90.5 → A 줄 수 있음, 90.0 → B까지(A는 400). 가점을 더해 110을 넘지 않는다. 연차 초과 정확히 5일 → S·A·B 400, 4.5일 → 됨. 연차를 5일로 고치면 이미 준 A가 지워진다. → Task 2 `점수_기준은_초과`, `연차는_5일부터`, Task 6 `점수보다_높은_등급은_못_준다`, Task 3 `연차를_넣으면_SAB를_지운다`
4. **두 번 눌러도 한 번:** 본부장 마감 · 대표이사 확인 · HR 확정 · 공개를 두 창에서 동시에 눌러도 단계가 한 번만 넘어가고 감사로그 · 알림도 한 번이다(`_txn` 안에서 단계를 다시 본다). → Task 6 `마감은_한_번`, Task 9 `확정은_한_번`
5. **대신 입력의 경계:** 대표이사가 맡기면 HR이 직속부서 팀장 평가 · 직속 배정 · 확인을 하되, **자기가 든 묶음(HR 담당자가 든 직속부서 팀원 묶음)은 확인 · 배정하지 못하고 자기 줄은 못 고친다**. 맡김을 끄면 그 순간 403. HR 팀장이 HR 담당자를 채점하는 것은 평가자 본인 일이라 "대리 입력" 표시가 없다. → Task 6 `대신_입력은_맡긴_동안_본인_것_빼고`, Task 9 `HR은_자기_묶음을_확인_못_한다`

---

# Part A — PR ① 평가 진행 (회차 · 시작 · 근태 · 자기평가 · 증빙 · 평가자 · 배정 · 마감)

### Task 1: DB 판 20 — `020_eval.sql`

**Files:**
- Create: `backend/hr_eval/sql/020_eval.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` (`expected` 표 목록 + 새 절 `32. 인사평가 (020)` `check_eval_tables`)

**Interfaces:**
- Produces (맨 위 주석은 019처럼 쉬운 한국어 — 표마다 무엇을 담는지, 감사로그에 무엇이 남고 무엇이 안 남는지, 001의 옛 평가 표를 쓰지 않는 이유):
  - `ev_rounds` — 해마다 한 줄. `year INTEGER PRIMARY KEY CHECK (year BETWEEN 2026 AND 2100) REFERENCES kpi_years(year) ON DELETE RESTRICT`(평가 시작일은 `kpi_years.eval_start` 한 곳에 둔다) · `self_to` · `evid_due` · `score_to` · `grade_to` · `ceo_to` · `final_to TEXT NOT NULL`(모두 `date(x) IS x`) · `band_a INTEGER NOT NULL DEFAULT 90` · `band_b DEFAULT 80` · `band_c DEFAULT 70`(`CHECK (band_a <= 100 AND band_a > band_b AND band_b > band_c AND band_c >= 1)`) · `started_at` · `started_by` · `att_done_at` · `released_at` · `released_by`(NULL 가능, `*_by REFERENCES users(id)`) · `updated_at TEXT NOT NULL` · `updated_by`. CHECK: `self_to <= score_to <= grade_to <= ceo_to <= final_to`, `evid_due <= score_to`, `released_at IS NULL OR started_at IS NOT NULL`.
  - `ev_targets` — 평가 대상 한 사람 한 줄, 시작 때 떠 둔다. `PRIMARY KEY (year, user_id)`, `year REFERENCES ev_rounds(year) ON DELETE RESTRICT`, `user_id REFERENCES users(id) ON DELETE RESTRICT`.
    - 떠 둔 칸: `pool TEXT NOT NULL CHECK (pool IN ('LEADERS','DIRECT') OR pool GLOB 'DIV:[1-9]*')` · `division_id INTEGER NULL` · `team_id INTEGER NULL`(보는 범위 — 부서가 지워져도 남게 FK 없음) · `div_name TEXT NOT NULL` · `team_name TEXT NOT NULL`(보고서 · 화면 — "본부 직속"도 글자로) · `title TEXT NULL`(직급) · `is_leader INTEGER NOT NULL CHECK IN (0,1)` · `rater_id INTEGER NULL REFERENCES users(id)`(NULL = 평가자를 못 찾음) · `assigner_id INTEGER NULL REFERENCES users(id)`(본부 팀원 묶음의 본부장, NULL = 대표이사) · `writes_kpi INTEGER NOT NULL CHECK IN (0,1)` · `kpi_version INTEGER NULL` + `FOREIGN KEY (user_id, year, kpi_version) REFERENCES kpi_versions(user_id, year, version)`(NULL = KPI 없이 등급만)
    - 진행 칸: `stage TEXT NOT NULL CHECK (stage IN ('SELF','SCORE','GRADE','CEO','HR','DONE'))` · `out_at` · `out_reason`(1~100자) · `out_by` · `no_self_at` · `no_self_by` · `self_at`(자기평가 제출) · `late INTEGER NOT NULL DEFAULT 0 CHECK (late BETWEEN 0 AND 999)` · `leave_x2 INTEGER NOT NULL DEFAULT 0 CHECK (leave_x2 BETWEEN 0 AND 730)`
    - 점수 칸: `grade TEXT NULL CHECK (grade IN ('S','A','B','C','D'))` · `comment TEXT NOT NULL DEFAULT ''`(≤ 500) · `head_comment TEXT NOT NULL DEFAULT ''`(≤ 500) · `bonus_x2 INTEGER NOT NULL DEFAULT 0 CHECK (bonus_x2 BETWEEN 0 AND 20)` · `bonus_reason TEXT NOT NULL DEFAULT ''`(≤ 100) · `proposed TEXT NULL`(SABCD) · `sub_score_x10 INTEGER NULL CHECK (sub_score_x10 BETWEEN 0 AND 1100)` · `sub_marks TEXT NULL CHECK (sub_marks IS NULL OR (json_valid(sub_marks) AND json_type(sub_marks) = 'object'))` · `scored_at` · `scored_by` · `proxy_by INTEGER NULL REFERENCES users(id)`(마지막으로 대표이사 대신 넣은 HR) · `edit_seen TEXT NULL`(평가자가 "확인"을 누른 고친 내용)
    - CHECK: `(out_at IS NULL) = (out_reason IS NULL)` · `bonus_x2 = 0 OR length(trim(bonus_reason)) > 0` · `stage IN ('SELF','SCORE') OR proposed IS NOT NULL` · `stage NOT IN ('HR','DONE') OR grade IS NOT NULL` · `kpi_version IS NULL OR writes_kpi = 1`
    - `CREATE INDEX ev_targets_pool ON ev_targets(year, pool)`
  - `ev_items` — 떠 둔 KPI의 실행목표마다 한 줄. `PRIMARY KEY (year, user_id, rid)`, `FOREIGN KEY (year, user_id) REFERENCES ev_targets(year, user_id) ON DELETE RESTRICT`, `rid INTEGER NOT NULL CHECK (rid >= 1)` · `note TEXT NOT NULL DEFAULT ''`(≤ 500) · `self_grade TEXT NULL` · `grade TEXT NULL`(둘 다 SABCD).
  - `ev_files` — 증빙. `id INTEGER PRIMARY KEY AUTOINCREMENT`(지운 번호를 다시 쓰지 않는다 — 파일 이름이 번호라서, 최종 검토 2026-10-26) · `year` · `user_id` · `rid` + `FOREIGN KEY (year, user_id, rid) REFERENCES ev_items(year, user_id, rid) ON DELETE RESTRICT` · `name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 120)` · `kind TEXT NOT NULL CHECK (kind IN ('pdf','png','jpg','xlsx','xls','docx','doc','hwp','hwpx'))` · `size INTEGER NOT NULL CHECK (size BETWEEN 1 AND 10485760)` · `sha256 TEXT NOT NULL` · `at TEXT NOT NULL`. 파일 내용은 DB에 넣지 않는다(서버 폴더 — Task 5).
  - `ev_pools` — 묶음마다 기록(상태는 사람들의 단계로 센다 — 저장하지 않는다). `PRIMARY KEY (year, pool)`, `year REFERENCES ev_rounds(year)` · `closed_at` · `closed_by`(본부장 마감) · `hr_seen TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(hr_seen) AND json_type(hr_seen) = 'array')`(마감 알림을 확인한 HR 번호) · `ceo_at` · `ceo_by` · `ceo_behalf` · `back_at` · `back_by` · `back_behalf` · `back_reason`(1~200자, `(back_at IS NULL) = (back_reason IS NULL)`) · `undo_at` · `undo_by` · `undo_from TEXT NULL CHECK (undo_from IN ('HR','CEO'))`.
  - `ev_appeals` — 한 사람 한 해 한 번(②가 쓴다 — 번호를 하나만 쓰려고 ①에서 함께 만든다). `PRIMARY KEY (year, user_id)` + `FOREIGN KEY (year, user_id) REFERENCES ev_targets` · `reason TEXT NOT NULL`(1~500) · `at TEXT NOT NULL` · `status TEXT NOT NULL CHECK (status IN ('OPEN','CEO','DONE'))` · `before TEXT NOT NULL`(SABCD) · `after TEXT NULL` · `marks TEXT NULL`(JSON 객체) · `answer TEXT NULL`(≤ 500) · `answered_at` · `answered_by` · `decided_at` · `decided_by` · `decided_behalf`. CHECK: `status = 'OPEN' OR (answer IS NOT NULL AND after IS NOT NULL)`.
  - 지우기 막기: `ev_targets` · `ev_items` 줄은 지울 수 없다(트리거 `RAISE(ABORT, …)` — 평가 기록 보존, 010의 `trg_kpi_versions_no_delete`와 같은 모양). `ev_files`는 지울 수 있다(본인이 마감 전에 지운다).

- [ ] **Step 1: 실패하는 검사 쓰기** — `check_schema.py`에 표 여섯과 `print("32. 인사평가 (020)")` 절. 다음이 거절되는지 본다:
  - `ev_rounds`: 순서가 거꾸로인 마감일(`self_to > score_to` · `evid_due > score_to`) · `band_a` 101 · `band_b >= band_a` · `kpi_years` 줄 없는 해 · 공개했는데 시작 안 함
  - `ev_targets`: `pool` 'DIV:0' · 'TEAM' · `stage` 'X' · `leave_x2` 731 · `bonus_x2` 21 · 가점이 있는데 사유가 빈칸 · `HR` 단계인데 등급 없음 · `GRADE` 단계인데 `proposed` 없음 · `kpi_version`이 있는데 `writes_kpi` 0 · 없는 판 번호 · 줄 지우기
  - `ev_items`: 대상 줄 없는 (해, 사람) · 501자 실적 · 줄 지우기
  - `ev_files`: 없는 실행목표 · 10MB + 1 · `kind` 'exe'
  - `ev_pools`: 이유 없는 돌려보냄 · `undo_from` 'X' · `hr_seen` '{}'
  - `ev_appeals`: `DONE`인데 답변 없음 · `status` 'X'
- [ ] **Step 2: 실패 확인** — `cd backend && python -m hr_eval.sql.check_schema`
- [ ] **Step 3: 구현** — 위 Interfaces 그대로.
- [ ] **Step 4: 통과 확인** — `검사 N건 전부 통과`. `python -m auth_check`도 그대로 통과(판 20 적용). 판 19 DB(main의 001~019 · 계획서 · 확정본 · 급여명세서 · 계약서 줄 포함)를 만들어 올려 보고 기존 줄이 그대로인지 본다(KPI 2단계 ②와 같은 방법, 시험 파일은 scratchpad).
- [ ] **Step 5: 커밋** — `git commit -m "DB 판 20: 인사평가 회차 · 대상 · 실행목표 · 증빙 · 묶음 기록 · 이의 신청"`

---

### Task 2: 규칙 — `hr_eval/domain/eval_rules.py`

**Files:**
- Create: `backend/hr_eval/domain/eval_rules.py`
- Modify: `backend/hr_eval/selfcheck.py` (`CHECKS`에 등록)

**Interfaces:**
- Consumes: `quota.calculate_department_quota(n) -> GroupedQuota | IndividualQuota`, `access.Target` · `access.Pool` · `access.can` · `Act`, `models.Actor` · `Role`
- Produces (모두 순수 함수):
  - 상수: `GRADES = ("S","A","B","C","D")` · `PCT = {"S":110,"A":100,"B":90,"C":80,"D":70}` · `BANDS = (90, 80, 70)` · `LEAVE_LIMIT_X2 = 10` · `BONUS_MAX_X2 = 20` · `TOTAL_MAX = Decimal("110")` · `ORDER = ("SELF","SCORE","GRADE","CEO","HR","DONE")` · `APPEAL_DAYS = 7` · `TEXT_MAX = {"note":500,"comment":500,"head":500,"bonusReason":100,"out":100,"back":200,"appeal":500,"answer":500}` · `FILE_MAX = 10 * 1024 * 1024` · `FILES_PER_ITEM = 5`
  - `def total(rows: Sequence[tuple[object, str | None]], bonus_x2: int) -> Decimal` — `(가중치, 등급)` 줄. 가중치는 `Decimal(str(w))`. 등급 없는 줄은 0. 소수 첫째 자리 `ROUND_HALF_UP`, `min(…, TOTAL_MAX)`.
  - `def band(score: Decimal, bands: tuple[int, int, int]) -> str` — `>100` S, `>a` A, `>b` B, `>c` C, 그 밖 D.
  - `def above(g: str, cap: str) -> bool` — g가 cap보다 높은 등급인가.
  - `def cap(score: Decimal | None, leave_x2: int, bands) -> str` — 점수 기준(점수가 None이면 S)과 연차 규칙(`leave_x2 >= LEAVE_LIMIT_X2`면 C) 중 낮은 쪽.
  - `def pool_key(t: Target) -> str` — 팀장 `"LEADERS"`, `t.direct` 또는 `t.division_id is None` → `"DIRECT"`, 그 밖 `f"DIV:{t.division_id}"`.
  - `def pool_ref(key: str, member_ids: frozenset[int]) -> PoolRef` — `access.can`에 넘길 모양(`DIV:n` → `Pool.DIVISION`, `division_id=n`).
  - `def after_score(key) -> str` · `def assign_stage(key) -> str` — 둘 다 `LEADERS`면 `"CEO"`, 아니면 `"GRADE"`. `def ready_stage(key) -> str` — `DIRECT`면 `"GRADE"`, 아니면 `"CEO"`(대표이사 확인을 기다리는 단계).
  - `def at(stage: str, ref: str) -> int` — `ORDER.index(stage) - ORDER.index(ref)`.
  - `@dataclass(frozen=True) class Member: uid: int; name: str; score: Decimal | None; grade: str | None; leave_x2: int`
  - `def check_pool(members: Sequence[Member], bands, full: bool) -> tuple[list[str], list[str], dict[str, int]]` — `(errors, warnings, counts)`. 정원은 `calculate_department_quota(len(members))`. 문구와 순서는 화면 `evValidate` 그대로(이름 · 총점 · 등급이 든 문구 — 이 결과는 배정자 · 대표이사 · HR에게만 간다). `full`이 거짓이면 등급 누락 · 미달을 따지지 않는다.
  - `def pool_state(stages: Sequence[str], key: str, has_errors: bool) -> str` — 제외된 사람을 뺀 단계들로: 모두 DONE → `"DONE"`, 모두 HR → `"HR"`, 하나라도 `ready_stage(key)`가 아니면 `"WAIT"`, 오류가 있으면 `"FIX"`, 아니면 `"CEO"`. 빈 묶음은 `"WAIT"`.
  - `def level(me: Actor, t: Target, *, rater: int | None, assigner: int | None, stage: str, released: bool) -> str` — 이 사람의 평가를 얼마나 보나: `"full"` · `"mine"`(평가자 — 자기가 낸 것만) · `"self"`(본인 — 자기평가만) · `"none"`. 순서:
    1. HR · 대표이사 → `"full"`
    2. `assigner`(대표이사면 None이 아니라 대표이사 번호를 넘긴다) == 나 → `"full"`
    3. `rater` == 나 → 공개됐거나 `at(stage, "SCORE") <= 0`이면 `"full"`, 아니면 `"mine"`
    4. 본인 → 공개됐으면 `"full"`, 아니면 `"self"`
    5. 공개됐고 `can(me, Act.VIEW_RESULT, t)`(팀장 = 팀원, 본부장 = 본부) → `"full"`
    6. 그 밖 `"none"`
  - `def edited(proposed, sub_score_x10, grade, score: Decimal | None, *, past: bool) -> str` — 평가자가 낸 것과 지금이 다르면 "총점 88.0 → 90.0 · 등급 B → C"(화면 `evEdited` 문구), 같거나 `past`(배정 단계를 지났나)가 거짓이면 `""`.

- [ ] **Step 1: 실패하는 검사 쓰기** (`selfcheck.py`) — 값은 화면 `evSelfCheck`에서 그대로 가져온다.
  ```python
  def 총점은_가중치와_가점():
      rows = [(40, "S"), (30, "A"), (30, "B")]
      assert total(rows, 0) == Decimal("101.0") and total(rows, 40) == Decimal("110") and total([(100, None)], 0) == 0
      assert total([(33.3, "A"), (33.3, "B"), (33.4, "C")], 1) == Decimal("90.5")   # 33.3 + 29.97 + 26.72 + 0.5 = 90.49 → 90.5 (둘째 자리에서 사사오입)
  def 점수_기준은_초과():
      b = BANDS
      assert [band(Decimal(x), b) for x in ("100.5","100","90.5","90","80.5","80","70.5","70")] == ["S","A","A","B","B","C","C","D"]
  def 연차는_5일부터():
      assert cap(Decimal("105"), 10, BANDS) == "C" and cap(Decimal("105"), 9, BANDS) == "S" and cap(None, 10, BANDS) == "C" and cap(None, 0, BANDS) == "S"
  def 정원_검사는_화면과_같다():   # evSelfCheck 의 R (101 · 99 · 96 · 92 · 88 · 80) — 6명이라 A·B·D 각 1명
      R = [Member(i, f"p{i}", Decimal(s), None, 0) for i, s in zip(range(1, 7), ("101", "99", "96", "92", "88", "80"))]
      g = lambda a, rows=R: [replace(m, grade=a.get(m.uid)) for m in rows]  # noqa: E731
      assert check_pool(g({1: "S", 3: "B", 6: "D", 2: "C", 4: "C", 5: "C"}), BANDS, True)[0] == []      # S 1명이면 A 0명으로 통과
      assert len(check_pool(g({1: "S", 2: "A"}), BANDS, False)[0]) == 1                                  # S가 있는데 A까지 주면 초과
      assert any("100점" in e for e in check_pool(g({2: "S"}), BANDS, False)[0])                          # 총점 100 이하 S 불가
      assert any("최대" in e for e in check_pool(g({1: "S", 2: "S"}), BANDS, False)[0])                   # S 상한
      assert check_pool(g({2: "A"}), BANDS, False)[:2] == ([], [])                                        # 배정 중에는 누락 · 미달을 안 막음
      e, w, _ = check_pool(g({i: "C" for i in range(1, 7)}), BANDS, True); assert e == [] and len(w) == 3  # 미달은 경고만
      assert any("등급이 없" in e for e in check_pool(g({1: "A"}), BANDS, True)[0])                       # 마감 · 확정은 누락을 막음
      assert len(check_pool(g({2: "A", 3: "B"}, R[:3]), BANDS, False)[0]) == 1                            # N ≤ 4 는 A·B 합계
      assert any("연차" in e for e in check_pool([Member(1, "가", Decimal("95"), "A", 10)], BANDS, False)[0])
      assert check_pool([Member(1, "가", None, "S", 0)], BANDS, False)[0] == []                           # KPI 없는 사람은 점수 기준 없음
  def 묶음_상태():
      assert pool_state([], "DIRECT", False) == "WAIT"
      assert pool_state(["GRADE","GRADE"], "DIRECT", False) == "CEO" and pool_state(["GRADE","GRADE"], "DIV:3", False) == "WAIT"
      assert pool_state(["CEO","CEO"], "DIV:3", True) == "FIX" and pool_state(["HR","HR"], "LEADERS", False) == "HR" and pool_state(["DONE"], "LEADERS", False) == "DONE"
  def 묶음_열쇠():   # 팀장 · 직속 팀 · '임원'(본부 없음) · 본부 팀원 · 본부 바로 아래
      assert pool_key(Target(1, 11, 1, Role.TEAM_LEADER)) == "LEADERS" and pool_key(Target(2, 9, None, direct=True)) == "DIRECT"
      assert pool_key(Target(3, None, None)) == "DIRECT" and pool_key(Target(4, 11, 1)) == "DIV:1" and pool_key(Target(5, None, 1)) == "DIV:1"
  def 보는_수준():   # 팀장 21(팀 11) · 팀원 22 · 본부장 20(본부 1) · 대표 6 · HR 7 · 다른 팀장 31(팀 12)
      T = Target(22, 11, 1)
      assert level(Actor(7, None, True), T, rater=21, assigner=20, stage="GRADE", released=False) == "full"
      assert level(Actor(20, Role.DIVISION_HEAD, led_division_id=1), T, rater=21, assigner=20, stage="GRADE", released=False) == "full"
      assert level(Actor(21, Role.TEAM_LEADER, led_team_id=11), T, rater=21, assigner=20, stage="SCORE", released=False) == "full"
      assert level(Actor(21, Role.TEAM_LEADER, led_team_id=11), T, rater=21, assigner=20, stage="GRADE", released=False) == "mine"
      assert level(Actor(22, None), T, rater=21, assigner=20, stage="DONE", released=False) == "self"
      assert level(Actor(22, None), T, rater=21, assigner=20, stage="DONE", released=True) == "full"
      assert level(Actor(31, Role.TEAM_LEADER, led_team_id=12), T, rater=21, assigner=20, stage="DONE", released=True) == "none"
      # 팀장을 평가한 본부장: 전사 팀장 묶음이라 배정자가 아니다 → 공개 전 "mine"
      L = Target(21, 11, 1, Role.TEAM_LEADER)
      assert level(Actor(20, Role.DIVISION_HEAD, led_division_id=1), L, rater=20, assigner=6, stage="HR", released=False) == "mine"
  def 고친_내용():
      assert edited("B", 880, "C", Decimal("90.0"), past=True) == "총점 88.0 → 90.0 · 등급 B → C"
      assert edited("B", 880, "B", Decimal("88.0"), past=True) == "" and edited("B", 880, "C", Decimal("88.0"), past=False) == ""
  ```
- [ ] **Step 2: 실패 확인** — `python -m hr_eval.selfcheck`
- [ ] **Step 3: 구현** — `check_pool`의 문구는 화면 문구를 그대로 옮긴다(`evBandText` 포함). `level`은 `access.can`만 부르고 DB를 보지 않는다.
- [ ] **Step 4: 통과 확인** — `N/N 통과`
- [ ] **Step 5: 커밋** — `git commit -m "평가 규칙: 총점 · 점수 기준 · 연차 · 정원 검사 · 묶음 상태 · 보는 수준"`

---

### Task 3: 서버 — 회차 정하기 · 평가 시작(떠 두기) · HR 명단 일

**Files:**
- Create: `backend/evaluation.py`
- Modify: `backend/app.py`(블루프린트 붙이기), `backend/kpi.py`(`year_set` — 시작한 해의 평가 시작일 막기), `backend/push.py`(`MESSAGES["eval_turn"] = ("인사평가에서 처리할 일이 있어요", "PC에서 확인해 주세요", "perf.e2")` — 이 작업이 처음 쓴다 · `check()`의 `MESSAGES` 목록 단언), `backend/auth_check.py`(새 `check_eval_admin(hr)` — 가상 조직은 이 검사만의 번호 81~89 부서 · 101~120 사람)

**Interfaces:**
- Consumes: Task 2 전부, `kpi._Refuse` · `_txn` · `_body` · `_chars` · `load_org` · `plan_year` · `YEARS` · `UID`, `org.manager_of` · `target_of` · `led_dept`, `auth._members` · `_depts` · `_hr_only` · `_audit` · `_today` · `_now` · `_iso` · `_db`
- Produces (`evaluation.py`):
  - `FIRST_YEAR = int(os.environ.get("HR_EVAL_FIRST_YEAR", "2027"))  # ponytail: 로컬 확인용 환경 변수 — 운영에는 넣지 않는다`
  - `def cur_year(conn) -> int` — R13: 올해(`plan_year()`) 회차가 시작돼 있으면 올해. 아니면 지난해 회차가 시작돼 있고 아직 안 끝났으면(공개 전 · 오늘(한국) ≤ 공개일 + 7일 · `OPEN`/`CEO` 이의 신청이 있음) 지난해. 그 밖은 올해.
  - `def _round(conn, year) -> dict | None` — `ev_rounds` 줄 + `kpi_years.eval_start` → `{"year", "evalStart", "selfTo", "evidDue", "scoreTo", "gradeTo", "ceoTo", "finalTo", "bands": {"A","B","C"}, "started", "attDone", "released"}`
  - `def _ceo(conn) -> int | None` — 활성 대표이사 번호. `def _deleg(conn, ceo) -> bool` — `ceo_delegation`에 줄이 있나.
  - **`POST /api/eval/admin/round`** `{"year", "evalStart", "selfTo", "evidDue", "scoreTo", "gradeTo", "ceoTo", "finalTo", "bands": {"A","B","C"}}` → `{"ok": true}`. 거절 순서:
    1. HR 아님 403 (`auth._hr_only`)
    2. `year`가 `YEARS` 정수 아님 400 `{"error": "해가 올바르지 않습니다."}`
    3. `year < FIRST_YEAR` 403 `{"error": f"{year}년 평가는 포털 밖에서 진행합니다."}`
    4. 이미 공개한 해 409 `{"error": "결과를 공개한 평가는 바꿀 수 없습니다."}`
    5. 날짜 모양 · 범위 · 순서(R7) 400 `{"error": 쉬운 문구}` · 기준 점수(정수 1~100, A > B > C) 400
    6. 시작한 뒤 평가 시작일을 바꿈 409 `{"error": "평가가 시작되어 평가 시작일을 바꿀 수 없습니다."}`
    - `kpi_years` 줄이 없으면 작성 기간 없이 만들고(`edit_from` · `edit_to` NULL) `eval_start`를 넣는다. 한 트랜잭션. 감사로그 `EVAL_ROUND_SET {year}`.
  - **`POST /api/eval/admin/start`** `{"year"}` → `{"ok", "count", "noKpi", "noRater"}`. 거절: HR 403 → 해 400 → `FIRST_YEAR` 403 → `year != plan_year()` 403 `{"error": "올해 평가만 시작할 수 있습니다."}` → 회차 줄 없음 409 `{"error": "단계별 마감일을 먼저 정해 주세요."}` → 오늘(한국) < 평가 시작일 403 `{"error": f"평가 시작일({M}/{D})부터 시작할 수 있습니다."}` → 이미 시작 409 `{"error": "이미 시작했습니다."}` → 대상 0명 409 `{"error": "평가 대상이 없습니다."}`.
    - 떠 두기(한 트랜잭션): 대상(R3)마다 `t = target_of(person, depts)` · `key = pool_key(t)` · `rater = manager_of(uid, …)` · 배정자(R5 — `DIV:n`이면 활성이고 `led_dept == n`인 본부장, 없으면 NULL) · `kpi_version`(`writes_kpi`이면 그해 마지막 확정 판, 없으면 NULL — R4) · 이름 칸(`team_name`은 팀이 없으면 `"본부 직속"`) · `stage = "SELF" if kpi_version else "SCORE"` · 실행목표마다 `ev_items` 빈 줄 · 묶음마다 `ev_pools` 줄 · `ev_rounds.started_at/by`.
    - 감사로그 `EVAL_STARTED {year, count}`. 저장 뒤 `push.notify([KPI가 있는 대상], "eval_turn")`.
  - **`POST /api/eval/admin/att-done`** `{"year"}` — HR · 시작한 해 · 공개 전(409). `att_done_at` = 지금(이미 있으면 그대로 200). 감사로그 `EVAL_ATT_DONE`.
  - **`POST /api/eval/admin/attendance`** `{"year", "rows": [{"userId", "late"?, "leaveOver"?}]}` → `{"ok", "done": [uid…], "locked": [uid…], "missing": [uid…]}`. HR · 시작한 해 · 공개 전. `rows`는 목록 2000줄까지(400). 줄마다 `late` 0~999 정수 · `leaveOver` 0~365이고 0.5 단위(`bool` 아님) — 하나라도 모양이 틀리면 400이고 아무것도 저장하지 않는다. 대상이 아닌 번호는 `missing`, 근태 잠금(Global Constraints)이면 `locked`, 나머지는 저장해 `done`. **연차 초과가 5일 이상이 되면 이미 준 S·A·B 등급을 지운다**(`grade = NULL` — 화면 `evSetAttendance`). 감사로그 `EVAL_ATTENDANCE {year, count}`(숫자 없음).
  - **`POST /api/eval/admin/<uid>/exclude`** `{"year", "reason"}` — HR → 대상 404 → 이미 제외 409 → `at(stage, "SCORE") > 0` 409 `{"error": "평가자가 점수를 낸 뒤에는 제외할 수 없습니다."}` → 사유 1~100자 400. 감사로그 `EVAL_EXCLUDED {year}`(사유 없음).
  - **`/include`** `{"year"}` — HR → 404 → 제외가 아님 409 → 그 묶음 상태가 `WAIT` · `FIX`가 아님 409 `{"error": "이 묶음은 이미 다음 단계로 넘어가 되돌릴 수 없습니다. 먼저 묶음을 되돌려 주세요."}`. 감사로그 `EVAL_INCLUDED`.
  - **`/skip-self`** `{"year"}` — HR → 404 → 제외됨 409 → `stage != "SELF"` 409 → KPI 없음 409. `stage = "SCORE"`, `no_self_at/by`. 감사로그 `EVAL_SKIP_SELF`. 저장 뒤 평가자에게 `eval_turn`.
  - **`/rater`** `{"year", "raterId"}` — HR → 404 → 평가자가 이미 냈음(`stage` SELF · SCORE가 아님) 409 → `raterId`가 활성이고 본인이 아닌 사람이 아님 400. 감사로그 `EVAL_RATER_SET {year, raterId}`.
  - `kpi.year_set`: 그해 `ev_rounds.started_at`이 있으면 `evalStart`를 바꾸는 요청은 409 `{"error": "평가가 시작되어 평가 시작일을 바꿀 수 없습니다."}`(작성 기간만 바꾸는 것은 그대로 된다).

- [ ] **Step 1: 실패하는 검사 쓰기** — 가상 조직: 본부(81) > 팀(82, 팀장 있음) · 팀(83, 팀장 없음), 본부(84, 본부장 없음) > 팀(85), 직속 팀(86 — HR 팀장 · HR 담당자), '임원'(87). 사람: 본부장(81) · 팀장(82) · 팀원 둘(82 — 하나는 KPI 없음) · 팀원(83) · 본부 직속(81) · 팀장(85) · 팀원(85) · HR 팀장(86) · HR 담당자(86) · 임원 소속 · 대표이사 · 평가 대상 꺼진 사람 · 비활성인 사람 · KPI를 쓰는데 확정본이 없는 사람. 시계는 `at()`으로 정한다(KPI 검사와 같은 방법), 해는 `FIRST_YEAR`로 바꿔 둔 해.
  - `회차는_HR이_2027년부터`: 팀장 403 · 2026 403(문구) · 순서가 거꾸로 400 · 기준 점수 A ≤ B 400 · HR 200 → `kpi_years.eval_start`가 들어감 · 감사로그 `EVAL_ROUND_SET`
  - `시작은_평가_시작일부터_한_번`: 회차 없이 409 · 시작일 전날(한국) 403 · 당일 200 · 또 409
  - `떠_둔_묶음과_평가자`: 팀원(82) → `DIV:81`, 평가자 = 팀장 · 팀원(83, 팀장 없음) → 평가자 = 본부장 · 본부 직속 → 본부장 · 팀원(85) → `DIV:84`, 평가자 = 팀장(85), 배정자 NULL(대표이사) · 팀장(82) → `LEADERS`, 평가자 = 본부장 · 팀장(85) → `LEADERS`, 평가자 = 대표이사 · HR 담당자 → `DIRECT`, 평가자 = HR 팀장 · HR 팀장 → `LEADERS`, 평가자 = 대표이사 · 임원 소속 → `DIRECT`, 평가자 = 대표이사 · 본부장 · 대표이사 · 평가 대상 꺼짐 · 비활성은 없음
  - `확정_KPI가_없으면_등급만`: KPI를 쓰는데 확정본이 없는 사람 → `kpi_version` NULL · `stage` SCORE · 응답 `noKpi` 1 · KPI 없는 사람도 SCORE
  - `시작_뒤에는_조직이_바뀌어도_그대로`: 시작 뒤 팀원(82)을 팀(83)으로 옮기고 본부장을 바꿔도 `ev_targets`의 묶음 · 평가자가 그대로
  - `시작한_해의_평가_시작일은_못_바꾼다`: `/api/kpi/admin/year`로 `evalStart`를 바꾸면 409, 작성 기간만 바꾸면 200 · `/api/eval/admin/round`의 `evalStart` 변경도 409
  - `연차를_넣으면_SAB를_지운다`: A 등급을 넣어 둔 사람에게 연차 5 → 등급 NULL, 4.5 → 그대로 · 0.3 400 · 366 400 · 모양이 하나라도 틀리면 아무것도 저장 안 함 · 없는 번호 `missing`
  - `근태는_넘어가면_잠긴다`: `GRADE`인 본부 팀원은 됨, `CEO`면 `locked` · `CEO`인 팀장은 됨(팀장의 배정 단계 = CEO), `HR`이면 `locked` · 제외된 사람 `locked`
  - `제외와_되돌림`: 점수를 낸 뒤(GRADE) 제외 409 · 사유 없이 400 · 제외 → 되돌림 200 · 묶음이 CEO로 넘어간 뒤 되돌림 409 · 감사로그에 사유 없음
  - `자기평가_없이_진행`: SELF → SCORE · `no_self_at` · KPI 없는 사람 409 · 평가자에게 알림 하나(`push.SYNC`로 본다 — 휴대폰 알림 검사의 가짜 푸시 서버)
  - `평가자_바꾸기`: 비활성 · 본인 400 · GRADE 409 · 200 → `rater_id`
  - `감사로그에_숫자와_글이_없다`: 이 절에서 남긴 `EVAL_*` 줄의 `after_data`에 `reason` · `late` · `leaveOver` · `grade` 열쇠가 없다
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — 시작 때 `rater`가 대상 본인이면 NULL로 둔다(있을 수 없지만 `manager_of`가 본인을 돌려주지 않는다는 것을 믿지 말고 막는다). 평가자를 못 찾은 사람 수가 응답 `noRater`.
- [ ] **Step 4: 통과 확인** — `로그인 검사 N건 전부 통과`
- [ ] **Step 5: 커밋** — `git commit -m "평가 API: 회차 · 시작(대상 · 묶음 · 평가자 · KPI 떠 두기) · 근태 · 제외 · 자기평가 없이 · 평가자 바꾸기"`

---

### Task 4: 서버 — 읽기 `GET /api/eval/state` · 보는 수준 · 증빙 내려받기

**Files:**
- Modify: `backend/evaluation.py`, `backend/auth_check.py` (`check_eval_view(hr)` — Task 3의 가상 조직을 이어 쓴다)

**Interfaces:**
- Consumes: Task 2 `level` · `edited` · `total` · `cap` · `check_pool` · `pool_state` · `at`, Task 3 `cur_year` · `_round` · `_ceo` · `_deleg`
- Produces:
  - `class _Ctx(NamedTuple)` — 한 요청에서 한 번 읽는 것: `conn, year, round, ceo, deleg, rows: dict[int, sqlite3.Row]`(대상), `items: dict[int, list[dict]]`(떠 둔 KPI 실행목표 — `kpi_versions.content`의 줄에 `team` · `personal`을 붙인 것, `rid` 순서는 확정본 순서), `entries: dict[(uid, rid), Row]`, `files: dict[(uid, rid), list[Row]]`, `pools: dict[str, Row]`, `names: dict[int, (name, title)]`, `active: set[int]`. `def _ctx(conn, year) -> _Ctx`.
  - `def _assigner(ctx, row) -> int | None` — `DIV:` 묶음은 `assigner_id`가 활성이면 그 사람, 아니면 대표이사. 그 밖은 대표이사(R6).
  - `def _behalf_ok(ctx, me, uid) -> bool` — 대표이사 몫을 이 HR이 대신할 수 있나: `me.is_hr` and `ctx.deleg` and `me.user_id != uid`.
  - `def _can(ctx, me: Actor, row) -> dict` — 화면이 쓰는 판단(서버의 쓰기 길도 이 함수를 쓴다 — 같은 판단 두 벌을 두지 않는다):
    - `self`: 본인 · `stage == "SELF"` · 시작함 · 오늘 ≤ `selfTo` · 제외 안 됨 · KPI 있음
    - `upload`: 본인 · 평가 시작일 ≤ 오늘 ≤ `evidDue` · `stage in ("SELF","SCORE")` · 제외 안 됨 · KPI 있음
    - `score`: `stage == "SCORE"` · 제외 안 됨 · 시작함 · (평가자 == 나, 또는 평가자가 대표이사이고 `_behalf_ok`)
    - `assign`: 공개 전 · 제외 안 됨 · `stage == assign_stage(pool)` · (`DIV:`이면 `_assigner` == 나, 또는 `_assigner`가 대표이사이고 (내가 대표이사 또는 `_behalf_ok`)) / (`LEADERS` · `DIRECT`이면 내가 대표이사 또는 `_behalf_ok`)
    - `bonus`: (`score` and `pool == "LEADERS"`) or `assign` · `head`: `assign` and `pool != "LEADERS"`
    - `evidence`: 본인 · 평가자 · `_assigner` · 그 본부의 본부장(`led_division_id == division_id`) · 대표이사 · HR
    - `behalf`: 위 `score` · `assign`이 `_behalf_ok`로 참이 됐나(화면의 "대표이사 대신" 표시)
  - **`def _row_json(ctx, me, row) -> dict | None`** — 보는 사람에 맞춰 깎은 한 줄. `level`이 `"none"`이면 None(명단에서 빠진다). 칸 이름은 화면 `evPeople`과 같다:
    ```
    {"pid": uid, "name", "title", "team", "div", "poolKey", "poolName", "kpi": bool, "stage",
     "out": {"at", "why"} | null, "noSelf": "YYYY-MM-DD" | "", "late", "leaveOver",
     "items": [{"rid", "team", "personal", "action", "formula", "weight", "S","A","B","C","D"}],
     "ev": [{"rid", "note", "selfGrade", "grade", "files": [{"id", "name", "size"}]}],   ← items 와 같은 순서
     "grade", "proposed", "subScore", "subMarks", "comment", "headComment", "bonus", "bonusReason",
     "total": 소수 | null, "cap": "S".."D", "proxy": "이름 직급" | "",
     "edited": "" | 글자 | true, "editSeen": bool,
     "rater": {"id", "name", "title"} | null, "raterIsCeo": bool,
     "can": _can(...), "hidden": bool, "appeal": null}                                ← appeal 은 ② 가 채운다
    ```
    - `"self"`(본인, 공개 전): `grade` · `proposed` · `subScore` · `subMarks` · `comment` · `headComment` · `bonus` · `bonusReason` · `total` · `cap` · `ev[].grade` · `proxy` · `edited` · `rater` 밖의 평가 칸을 **응답에 넣지 않는다**(`null` · `""` · `0`이 아니라 열쇠를 빼고, 화면 `evFromLive`가 빈 값을 채운다). `late` · `leaveOver`는 본인 것이라 둔다.
    - `"mine"`(평가자, 낸 뒤 · 공개 전): `grade = proposed` · `ev[].grade = subMarks[rid]` · `total = subScore` · `bonus` 0 · `bonusReason` · `headComment` `""` · `proxy` `""` · `edited = true if 고쳤으면 else ""` · `hidden = true`. 지금 `grade` · 지금 항목 등급 · 지금 가점은 어디에도 없다.
    - `"full"`: 모두. `edited`는 `edited()`의 글자.
    - `files`는 `can.evidence`일 때만, 아니면 빈 목록.
  - **`GET /api/eval/state?year=`**(없으면 `cur_year`) → 
    ```
    {"year", "offline": year < FIRST_YEAR, "round": _round | null, "deleg": bool, "ceoId",
     "me": _row_json(나) | null,
     "people": [_row_json 이 None 이 아닌 다른 사람 — 이름순이 아니라 본부 → 팀 → 팀장 먼저 → 이름],
     "pools": [{"key", "name", "size", "state", "counts": {"S".."D"}, "errors": [...], "warnings": [...], "quota": 글자,
                "log": {"closed", "ceo", "back": {"at", "by", "reason"}, "undo"}, "hrSeen": bool,
                "can": {"close", "confirm", "back", "finalize", "undo"}}],   ← 배정자 · 대표이사 · HR 에게만 (보는 묶음만)
     "todo": {"me": n, "admin": n}}
    ```
    - `people`에 드는 사람: HR · 대표이사 = 전원 · 본부장 = 자기가 배정자인 묶음 + 자기가 평가자인 사람 · 그 밖 = 자기가 평가자인 사람. (공개 뒤 결과 조회는 ②의 `GET /api/eval/results`.)
    - `pools.counts`는 배정 단계에 온 사람의 등급만 센다(화면 `evPoolCard`와 같다). `pools.name`: `DIV:n` = `div_name + " 팀원"`, `LEADERS` = "전사 팀장", `DIRECT` = "직속부서 팀원".
    - `todo`(화면 `evTodo`와 같은 셈, 시작 전 · 공개 뒤 0): `me` = 낼 자기평가(`can.self`면 1) + 내가 점수 낼 사람(대신 아님) + 고친 것을 아직 확인 안 한 사람 + (배정자로서 배정 단계 사람이 있는 묶음 수) + (대표이사로서 `CEO` · `FIX`인 묶음 수) / `admin`(HR만) = `HR`인 묶음 수 + 확인 안 한 마감 알림 수 + (근태 안 마쳤으면 1) + (맡김이면 `CEO` · `FIX`인 묶음 수 + 대표이사가 평가자인 `SCORE` 사람 중 내가 대신할 수 있는 수). (`HR` 묶음은 ②의 확인이 생겨야 나오므로 ①에서는 저절로 0이다.)
    - `def eval_todo(conn, actor) -> dict` — 위 `todo` 셈을 따로 부를 수 있게(Task 6의 `push.pending`이 쓴다).
  - **`GET /api/eval/files/<int(max=2147483647):fid>`** — 없음 404 → `can.evidence`가 아님 403 `{"error": "이 증빙을 볼 권한이 없습니다."}` → 파일 없음(폴더에서 사라짐) 404. `Content-Disposition: attachment; filename*=UTF-8''…` · 형식별 `Content-Type` · `X-Content-Type-Options: nosniff` · `Cache-Control: no-store`. 감사로그는 남기지 않는다.

- [ ] **Step 1: 실패하는 검사 쓰기** — Task 3 조직을 시작해 두고, `ev_targets`에 단계를 직접 넣어 상황을 만든다(본부 팀원 하나를 팀장이 B로 내고 본부장이 C로 고친 상태 등).
  - `공개_전_평가자는_낸_것만`: 그 사람의 팀장 응답 줄 → `grade` "B" · `ev[].grade`가 낸 그대로 · `subScore`가 낸 총점 · `edited is True` · `hidden is True` · `bonus` 0 · `headComment` ""
  - `새는지_모든_칸에서`: 팀장이 받은 응답 전체를 재귀로 훑어 고친 등급 "C"가 `grade`류 열쇠(`grade` · `proposed` · `selfGrade` 제외 없음 — 열쇠 이름에 grade가 든 모든 칸)에 없고, 고친 총점(소수 · ×10 정수 · 글자 모두)이 어떤 값으로도 없다
  - `본인은_자기평가만`: 본인 `me` 줄에 `grade` · `proposed` · `subScore` · `subMarks` · `comment` · `headComment` · `bonus` · `total` · `cap` · `edited` 열쇠가 없고 `ev[]`에 `grade` 열쇠가 없다 · `note` · `selfGrade`는 있다
  - `본부장은_자기_본부를_배정자로`: 본부장 → 본부 팀원 `full`(고친 C가 보임) · 자기가 평가한 팀장은 `mine` · 다른 본부 사람은 `people`에 없음 · `pools`에 자기 본부 묶음만
  - `대표이사와_HR은_전부`: 대표이사 · HR → 전원 `full` · `pools` 전부
  - `팀장은_자기_평가_대상만`: 다른 팀장 응답 `people`에 그 팀원이 없음 · 직원은 `people` 빈 목록 · `pools` 없음
  - `증빙은_볼_사람만`: 본인 · 평가자 · 본부장 · 대표이사 · HR 200, 같은 팀 동료 · 다른 팀장 403 · 없는 번호 404 · 머리에 `nosniff` · `no-store`
  - `할_일_숫자`: 본인 자기평가 1(마감 다음 날 0) · 팀장 점수 낼 사람 수 · 본부장 배정 단계 묶음 1 · HR 근태 안 마침 1
  - `2026년은_안내만`: `FIRST_YEAR` 전 해로 부르면 `offline` 참 · `people` 빈 목록
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — `_row_json`이 깎는 곳 하나뿐이다. 쓰기 길이 응답에 줄을 돌려줄 때도 이 함수를 거친다.
- [ ] **Step 4: 통과 확인** — `python -m auth_check`
- [ ] **Step 5: 커밋** — `git commit -m "평가 API: 보는 사람에 맞춘 상태 읽기 · 공개 전 숨김 · 증빙 내려받기 · 할 일 숫자"`

---

### Task 5: 서버 — 자기평가 · 증빙 올리기

**Files:**
- Modify: `backend/evaluation.py`, `nginx.conf`(증빙 올리기만 본문 11MB), `backend/auth_check.py`(`check_eval_self(hr)`)

**Interfaces:**
- Consumes: Task 4 `_ctx` · `_can` · `_row_json`
- Produces:
  - **`POST /api/eval/me/self`** `{"year", "entries": [{"rid", "note", "selfGrade"}]}` → `{"ok", "me": _row_json}` — 임시 저장. 거절: 대상 아님 404 `{"error": "이번 평가 대상이 아닙니다."}` → `can.self` 거짓 403(마감일이 지났으면 `{"error": f"자기평가 마감일({selfTo})이 지났습니다. HR에 문의해 주세요."}`, 그 밖 `{"error": "지금은 자기평가를 쓸 수 없습니다."}`) → 모양 400(없는 `rid` · 등급 아님 · 실적 500자 넘음 · `_chars`). 보낸 줄만 바꾼다. 감사로그 없음.
  - **`POST /api/eval/me/submit`** `{"year"}` — 위와 같은 403 → 실행목표마다 실적(공백 뺀 1자 이상)과 자기 등급이 다 있어야 400 `{"error": "모든 KPI에 실적과 달성 등급을 넣어 주세요."}`. `stage = "SCORE"` · `self_at`. 감사로그 `EVAL_SELF_SUBMITTED {year}`. 저장 뒤 평가자(대표이사가 평가자이고 맡겼으면 HR 전원 — 그 사람 본인 빼고)에게 `eval_turn`.
  - **`POST /api/eval/files`** (multipart: `year`, `rid`, `file`) → `{"ok", "id"}` — `can.upload` 거짓 403 `{"error": "증빙 마감일이 지났거나 올릴 수 없는 단계입니다."}` → `rid`가 떠 둔 실행목표 아님 400 → 그 실행목표에 5개 400 → 크기 0 · 10MB 넘음 400 → 형식(`_kind`) 아님 400 `{"error": "PDF · 그림(PNG·JPG) · 엑셀 · 워드 · 한글 파일만 올릴 수 있습니다."}` → 저장.
    - `def _kind(head: bytes, name: str) -> str | None` — 확장자(소문자)가 허용 목록에 있고 첫 바이트가 그 종류와 맞아야: `pdf` = `%PDF-` · `png` = `\x89PNG\r\n\x1a\n` · `jpg`/`jpeg` = `\xff\xd8\xff`(종류 이름은 `jpg`) · `xlsx`/`docx`/`hwpx` = `PK\x03\x04` · `xls`/`doc`/`hwp` = `\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1`.
    - 이름: 경로를 떼고(`/` · `\` 뒤만) 제어문자를 빼고 120자로 자른다. 비면 `증빙.<종류>`.
    - 저장 위치 `def _files_dir() -> Path` = `Path(os.environ["HR_DB_PATH"]).parent / "eval_files"`(없으면 만든다), 파일 이름은 `<id>` 하나(사람이 준 이름을 경로에 쓰지 않는다). 줄을 넣어 id를 받은 뒤 임시 이름으로 쓰고 바꿔 단다(`os.replace`) — 쓰다 실패하면 줄도 되돌린다(같은 트랜잭션).
  - **`POST /api/eval/files/<fid>/delete`** — 없음 404 → 주인이 아님 403 → `can.upload` 거짓 403 → 줄을 지우고 파일을 지운다(파일이 이미 없어도 200).
  - `nginx.conf`: `location = /api/eval/files { client_max_body_size 11m; … /api/ 와 같은 proxy 설정 }` — 전체 한도 5MB는 그대로.

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `자기평가는_마감일_밤까지`: 마감일 12/5(올해가 아니라 시험 해) — UTC 12/5 14:59 저장 · 제출 200, 다른 사람으로 15:00 403(문구)
  - `제출은_다_채워야`: 실적 하나 빔 400 · 공백만 400 · 다 채움 200 → `stage` SCORE · 다시 저장 403 · 감사로그에 글 없음
  - `자기평가_모양`: 없는 rid 400 · 등급 'E' 400 · 501자 400 · NUL 400 · 남의 것을 쓸 길이 없다(본문에 사람 번호가 없다)
  - `KPI_없는_사람은_자기평가가_없다`: 403
  - `증빙은_마감일까지_점수_전까지`: 증빙 마감 다음 날 403 · 평가자가 제출한 뒤(GRADE) 403 · SCORE 단계(자기평가 낸 뒤)에는 200
  - `증빙_형식은_첫_바이트로`: `.pdf`인데 PNG 머리 400 · `.exe` 400 · `.hwp` OLE 머리 200 · 10MB + 1 400 · 6번째 400 · 경로가 든 이름 `../../x.pdf` → 저장 이름 `x.pdf`, 폴더 밖에 아무것도 없음
  - `증빙_지우기`: 주인 200 · 파일도 사라짐 · 남의 것 403 · 마감 뒤 403
  - `nginx_증빙만_11MB`: `nginx.conf`에 `location = /api/eval/files`와 `client_max_body_size 11m`가 있고 전체 `5m`는 그대로(글자로 본다 — 기존 nginx 검사와 같은 방법)
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — 검사에서 `HR_DB_PATH`가 임시 폴더라 증빙 폴더도 그 밑에 생긴다.
- [ ] **Step 4: 통과 확인** — `python -m auth_check`
- [ ] **Step 5: 커밋** — `git commit -m "평가 API: 자기평가 저장 · 제출 · 증빙 올리기 · 지우기"`

---

### Task 6: 서버 — 평가자 점수 · 등급 배정 · 본부장 마감 · 알림 · 처리할 일 숫자

**Files:**
- Modify: `backend/evaluation.py`, `backend/push.py`(`LABELS`에 `"e2": "평가 진행"` · `pending()`에 평가 몫), `backend/auth_check.py`(`check_eval_score(hr)` · `check_push_pending`에 평가 줄)

**Interfaces:**
- Consumes: Task 4 `_ctx` · `_can` · `_row_json` · `eval_todo` · `_assigner` · `_behalf_ok`, Task 2 `total` · `cap` · `above` · `check_pool` · `pool_state` · `after_score` · `edited`
- Produces (`PID = UID`):
  - **`POST /api/eval/people/<uid>/edit`** `{"year", "marks"?: {"<rid>": 등급 | null}, "grade"?: 등급 | null, "comment"?, "bonus"?, "bonusReason"?, "headComment"?}` → `{"ok", "row": _row_json}` — 보낸 칸만 바꾼다.
    - 거절 순서: 대상 404 → `can.score`도 `can.assign`도 아님 403 `{"error": "지금 이 분의 평가를 고칠 수 없습니다."}` → 칸마다 권한(`comment`는 `can.score`만 · `headComment`는 `can.head` · `bonus` · `bonusReason`은 `can.bonus`) 403 → 모양 400(KPI 없는 사람에게 `marks` · `bonus` · 없는 rid · 등급 아님 · 가점 0~10 · 0.5 단위 · 가점이 있는데 사유 없음 · 글자 한도 · `_chars`) → **등급이 줄 수 있는 가장 높은 등급(`cap`)보다 높음 400**(문구: 연차면 `"연차 초과 5일 이상이라 S·A·B를 줄 수 없습니다."`, 점수면 `f"총점 {총점}점으로는 {등급}를 줄 수 없습니다. ({기준 문구}) 점수를 다시 봐 주세요."`).
    - 자동 등급(화면 `evFit`): `SCORE` 단계에서 항목 등급이 다 찼고 이번 요청이 `marks`를 바꿨으면 `grade = cap`. 그 밖 단계에서 바뀐 점수 때문에 지금 등급이 `cap`보다 높아지면 `grade = cap`. (`grade`를 함께 보내면 그 값이 이긴다 — 검사는 위 400.)
    - `can.behalf`로 했으면 `proxy_by = 나`. 감사로그 없음(임시 단계).
  - **`POST /api/eval/people/<uid>/submit`** `{"year"}` — 대상 404 → `can.score` 아님 403 → KPI 있으면 항목 등급이 다 있어야 400 `{"error": "모든 KPI에 달성 등급을 골라 주세요."}` · 코멘트 1자 이상 400 `{"error": "평가자 코멘트를 입력해 주세요. 직원에게 공개됩니다."}` / KPI 없으면 코멘트 선택 → 등급 없음 400 → 등급 > `cap` 400. 그대로 떠 둔다: `proposed = grade` · `sub_score_x10`(KPI 있을 때) · `sub_marks` · `scored_at/by` · `stage = after_score(pool)`. 감사로그 `EVAL_SCORED {year}`(`on_behalf_of` = 대신이면 대표이사). 저장 뒤 다음 사람에게 `eval_turn`: `DIV:`/`DIRECT` → 배정자(대표이사면 맡겼을 때 HR, 아니면 대표이사), `LEADERS` → 대표이사(맡겼으면 HR).
  - **`POST /api/eval/people/<uid>/seen`** `{"year"}` — 평가자 본인만(403), `edit_seen` = 지금 `edited()` 글자. 화면은 공개 전에도 이 길로 "확인"을 누른다(서버가 글자를 들고 있고 응답은 `editSeen` 참/거짓만).
  - **`POST /api/eval/pools/close`** `{"year", "pool"}` — 본부 팀원 묶음 마감. 거절: `pool`이 `DIV:` 아님 400 → 묶음 없음 404 → 배정자가 아님(`_assigner` == 나, 또는 그가 대표이사이고 나는 대표이사 · `_behalf_ok`(묶음 사람 중 내가 없어야)) 403 → 공개 뒤 409 → 제외 안 된 사람 중 `GRADE`가 아닌 사람 409 `{"error": "아직 이전 단계에 있는 분이 있습니다."}` → `check_pool(full=True)` 오류 409 `{"error": 첫 오류, "errors": [...]}`. 모두 `CEO`로 · `ev_pools.closed_at/by` · `back_*` 비움 · `hr_seen` `[]`. 감사로그 `EVAL_POOL_CLOSED {year, pool}`. 저장 뒤 대표이사(맡겼으면 HR)와 HR 전원에게 `eval_turn`.
  - **`POST /api/eval/pools/seen`** `{"year", "pool"}` — HR만, `hr_seen`에 나를 더한다(이미 있으면 그대로).
  - `push.LABELS`에 `"e2": "평가 진행"`(KPI 다음) · `pending()`에 `n["e2"] = eval_todo 합` — 화면 `TODO.e2`와 같은 셈(`eval_todo`는 평가 해 · 시작 전 · 공개 뒤를 스스로 본다).

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `점수와_자동_등급`: 팀장이 항목 등급을 다 고르면 `grade` = `cap` · 낮은 등급으로 고치면 그대로 · 높은 등급 400(문구) · 가점 3 + 사유 없음 400(본부 팀원은 팀장에게 가점 칸 자체가 403)
  - `점수보다_높은_등급은_못_준다`: 총점 90.0인 사람에게 A 400 · B 200 · 90.5인 사람에게 A 200 · 연차 5일인 사람에게 B 400 · C 200
  - `제출하면_떠_둔다`: 코멘트 없이 400 · 제출 → `proposed` · `sub_score_x10` · `sub_marks` · 본부 팀원 GRADE · 팀장 CEO · 직속 GRADE · 다시 고치기 403 · 감사로그 `EVAL_SCORED`에 등급 · 점수 없음 · 배정자 휴대폰에 `eval_turn` 한 통(글에 등급 · 이름 없음)
  - `KPI_없는_사람은_등급만`: `marks` 400 · 코멘트 없이 제출 200
  - `배정자는_배정_단계에서만`: 본부장이 GRADE인 본부 팀원의 항목 등급 · 가점 · 윗사람 코멘트를 고침 200 · 고친 점수가 등급보다 낮아지면 등급이 내려감 · SCORE 단계 사람은 403 · 다른 본부장 403 · 대표이사가 본부 팀원을 고침 403(돌려보내기만 — ②)
  - `대표이사는_팀장과_직속을`: 대표이사가 CEO 단계 팀장 · GRADE 단계 직속 팀원을 고침 200 · 대표이사가 평가자인 직속 팀장(SCORE) 채점 200
  - `대신_입력은_맡긴_동안_본인_것_빼고`: 맡김 전 HR이 직속 팀장 채점 403 · 맡김 뒤 200 · `proxy` "이름 직급" · HR 담당자 본인 줄 고치기 403 · 맡김을 끄면 다음 요청 403 · HR 팀장이 HR 담당자를 채점 → `proxy` ""(평가자 본인 일)
  - `고친_것은_사실만_알린다`: 본부장이 고친 뒤 팀장 응답 `edited` 참 · seen → `editSeen` 참 · 본부장이 또 고치면 다시 거짓 · 공개 전 팀장 응답에 고친 내용 글자 없음
  - `마감은_전원_배정과_정원`: GRADE 아닌 사람이 있으면 409 · 정원 초과 409(오류 목록) · 미달은 마감 200 · 마감 → 모두 CEO · `closed_at` · 대표이사 · HR에게 알림 · 다른 본부장 403 · 팀장 묶음 열쇠 400
  - `마감은_한_번`: 두 스레드가 동시에 마감 → 하나 200 · 하나 409 · 감사로그 하나 · 알림 한 번씩
  - `본부장_없는_본부는_대표이사가`: 본부(84) 묶음 — 대표이사가 GRADE 사람을 고치고 마감 200, 맡김 켠 HR도 200(묶음에 본인이 없을 때)
  - `HR_마감_알림_확인`: 마감 뒤 HR `pools[].hrSeen` 거짓 → seen → 참 · `todo.admin` 1 줄어듦 · 다른 HR은 그대로
  - `check_push_pending`에 한 줄: 평가할 사람이 둘인 팀장의 `pending()`에 `("e2", 2)` — 화면 숫자와 같다
- [ ] **Step 2: 실패 확인** — `python -m auth_check` · `python -m push check`
- [ ] **Step 3: 구현** — 쓰기마다 `_txn` 안에서 `_ctx`를 다시 읽고 `_can`으로 판단한다(먼저 읽은 상태를 믿지 않는다).
- [ ] **Step 4: 통과 확인** — `python -m auth_check` · `python -m push check`
- [ ] **Step 5: 커밋** — `git commit -m "평가 API: 평가자 점수 · 등급 의견 · 배정 · 본부장 마감 · 대신 입력 · 알림"`

---

### Task 7: 화면 — 평가 진행 탭을 서버 자료로

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 4 `GET /api/eval/state`, Task 3 · 5 · 6 쓰기 길, `/api/kpi/delegation`
- Produces:
  - `const EV_LIVE = { state:'', year:null, offline:false, round:null, pools:[], todo:{ me:0, admin:0 }, deleg:false, ceoId:null, busy:false, typed:{}, prog:{} }` — `typed`: 적고 저장 안 한 글(`'self-rid'` · `'cmt-pid'`), KPI의 `KPI_LIVE.typed`와 같은 쓰임(적는 중 다시 그리지 않기 · 창 닫기 전에 묻기 — `beforeunload` 조건에 더한다). `prog`: 사람마다 받아 둔 진행 기록(아래).
  - `async function evLiveLoad(year)` — `/api/eval/state`를 받아 **`evPeople`의 내용을 서버 줄로 바꾼다**(`evPeople.length = 0; evPeople.push(...rows.map(evFromLive))` — `// ponytail: 시연 명단 자리를 그대로 쓴다. 실제 로그인에서는 시연 인물이 남지 않게 로그인 직후 비운다`), `kpiPeriod`의 평가 날짜(`evalFrom` · `selfTo` · `evidDue` · `scoreTo` · `gradeTo` · `ceoTo` · `evalTo`) · `evBands` · `evAttDone` · `evRelease.at` · `evProxy`(맡김이면 켠 날 글자) · `evPoolLog`를 서버 값으로 채운다. 로그인 직후(`kpiLiveLoad`를 부르는 곳)와 창으로 돌아올 때 함께 부른다. 실제 로그인이 확인되면 그 자리에서 `evPeople.length = 0`.
  - `function evFromLive(row)` — 서버 줄을 시연 줄 모양으로: 빠진 칸은 시연 기본값(`grade:null, proposed:null, comment:'', bonus:0, …`), `key`는 내 줄이면 `kpiRole`, 아니면 null, `ev` 줄에 `files:[{ id, name, live:true }]`.
  - **판단 함수의 실제 로그인 갈래**(한 줄씩 — `if(AUTH.live) return …`): `evIsEvaluator(p)` → `p.can.score` · `evCanAssign(p)` → `p.can.assign` · `evCanUpload(p)` → `p.can.upload` · `evCanSeeEvidence(p)` → `p.can.evidence` · `evHidden(p)` → `p.hidden` · `evCeoActs(p)` → `p ? p.can.score || p.can.assign : EV_LIVE.pools.some(x=>x.can.confirm)`(묶음 단위 단추는 `pools[].can`을 직접 읽는다) · `evEvaluatorOf(p)` → `p.rater && p.rater.id===AUTH.me.id ? kpiRole : null` · `evEvaluatorName(p)` → `p.rater ? 이름 직급 : '평가자 없음'` · `evPool(p)` → `p.poolKey` · `evEdited(p)` → `p.edited===true ? '고쳤습니다' : p.edited` · `evEditNews()` → `p.edited && !p.editSeen`인 내가 평가한 사람 · `evStarted()` → `!!EV_LIVE.round?.started` · `evSelfOpen()` → 내 줄 `can.self` · `evPoolState(k)` → 서버 `pools[].state` · `evPoolCheck(k)` → 서버 `errors` · `warnings` · `evNewsFor()` → `pools`의 마감 알림 중 `!hrSeen`.
  - **묶음 이름 · 목록**: `function evPoolKeys()` = 실제 로그인이면 `EV_LIVE.pools.map(x=>x.key)`, 아니면 `EV_POOLS` · `function evPoolName(k)` = 서버 `name` 또는 `EV_POOL_NAME[k]`. `EV_POOLS` · `EV_POOL_NAME[k]`를 쓰는 곳을 이 둘로 바꾼다(15 · 13곳). 본부장 카드 `evHeadCard`는 실제 로그인이면 `pools` 중 `can.close`가 있는 묶음마다 하나(`'div'` 대신 그 열쇠).
  - **진행 기록을 근거로 함께 보여 주기(설계 9장 — 이번 개편의 목적)**: 자기평가 카드(`evSelfCard`)와 평가 패널(`evScorePanel`)의 KPI 줄마다 접힌 "진행 기록" — 그 실행목표(`rid`)의 월 한 줄(정기) · 일정과 결과 한 줄(비정기) · 늦어지는 이유. 자료는 이미 있는 `GET /api/kpi/progress/<uid>?year=`(보는 범위 `_sees` 그대로 — 못 보면 403이고 줄을 그리지 않는다)를 사람마다 한 번 받아 둔다(`EV_LIVE.prog[uid]`). 새 서버 길은 없다. 시연에는 없다.
  - **동작 함수의 실제 로그인 갈래**: `async function evLiveDo(path, body, okMsg)` — 보내고(`EV_LIVE.busy`), 오류면 서버 문구를 `kpiFlash(…, 'danger')`, 되면 `evLiveLoad()` 뒤 `kpiFlash(okMsg)`. 갈래를 넣는 함수: `evSetSelf` · 실적 글 칸(`EV_LIVE.typed`에 모았다가 새 단추 [임시 저장] `evSaveSelf(pid)`와 제출 때 `me/self`) · `evSubmitSelf`(저장 → 제출) · `evAddFiles`(`FormData`로 하나씩) · `evDelFile` · `evSetGrade` · `evAssign` · `evSetLine` · 코멘트 칸(`typed` → [저장] · 제출 때 `edit`) · `evSaveBonus` · `evSubmitScore` · `evSubmitLine` · `evSubmitGrades`(→ `pools/close`) · `evEditSeen` · `evNewsSeen` · `evAttSet` · `evPasteApply`(이름은 `evPeople` 그대로 찾고 번호로 보낸다) · `evAttFinish` · `evSkipSelf` · `evExclude` · `evInclude` · `evSetBand` · 진행표 날짜 칸(실제 로그인은 바로 바꾸지 않고 [마감일 저장] 단추가 `admin/round` — 서버가 순서를 다시 본다) · `evProxyToggle`(→ `/api/kpi/delegation`).
  - **진행표(`evBoard`)의 실제 로그인**: "시연:" 단추 셋을 빼고 [마감일 저장] · [평가 시작](시작 전이고 오늘 ≥ 평가 시작일 — 확인 창 "지금 명단 · 평가자 · KPI를 이번 평가에 고정합니다. 시작한 뒤 바뀐 조직은 이번 평가에 쓰지 않아요.") · 평가 대상 명단에 "포털 사용 꺼짐" · "확정 KPI 없음" · "평가자 없음" 표시와 [평가자 바꾸기](조직도 실제 명단 `orgPeople()`의 활성 직원에서 고르는 창).
  - **평가 진행 탭 맨 위**: `evContent()`의 첫 줄은 `if(AUTH.live && (EV_LIVE.state!=='ok' || EV_LIVE.offline)) return …` — 불러오는 중 · 못 불러옴 안내, `offline`이면 지금 안내("2026년도 평가는 기존 방식으로 진행됩니다." — 해는 서버 `year`). ①에서는 실제 로그인의 `evPoolCard('ceo'|'hr')`에 확인 · 확정 · 돌려보내기 · 되돌리기 단추를 그리지 않는다(②에서 연다).
  - **메뉴 숫자 · 홈**: `evTodo(part)`의 실제 로그인 = `EV_LIVE.todo.me` / `.admin` / 합(`hrTodo`가 그대로 나눠 쓴다). `homeDates`의 평가 날짜는 실제 로그인이면 `EV_LIVE.round`가 있고 `offline`이 아닐 때만. `TODO.e3`는 ②까지 0 그대로.
  - **사이트 둘러보기**: `TOURS.ev`(자기평가 방법)의 가리키는 곳이 실제 화면에도 있는지 본다 — [임시 저장] 단계 하나를 더한다(`#evSelf button[onclick^="evSaveSelf"]`, 문구 "적은 실적과 등급을 저장해요. 제출하기 전까지 고칠 수 있어요.").
  - **`evLiveSelfCheck()`를 고친다**(비동기 `await evLiveSelfCheck()`): `fetch`를 잠깐 바꿔 `/api/eval/state`를 흉내 — ① `offline`이면 지금 안내 그대로 · 평가 숫자 0 ② 평가자 줄(`hidden` · `edited:true`)이면 패널에 "내가 낸 평가입니다"와 낸 등급만, 고친 등급 글자가 화면에 없음 ③ 본인 줄(등급 칸 없음)이면 자기평가 카드만 · 등급 · 총점 글자 없음 ④ `can.score` 거짓이면 등급 단추가 막힘 ⑤ 동작 하나(`evSubmitScore`)가 `people/<uid>/submit`으로 가고 다시 읽음 ⑥ 실제 로그인이 끝나면(`AUTH.live` 되돌림) 시연 `evPeople` 20명이 돌아옴(검사가 바꾼 것을 되돌린다).

- [ ] **Step 1: 시연 해시 떠 두기** — 이 작업 전 커밋에서 역할 7 × HR 화면 둘 × 탭 둘 × 시연 단추(없음 · `evDemoToCeo` · `evDemoFill` → 공개) 화면의 `innerHTML` 해시를 scratchpad에 적는다(KPI 2단계와 같은 방법 — playwright로 PC 미리보기를 연다).
- [ ] **Step 2: `evLiveSelfCheck` 새 판을 먼저 쓴다** — 지금 코드에서 실패하는지 콘솔로 본다.
- [ ] **Step 3: 구현** — 위 Interfaces. 패치 스크립트(`assert count == 1`).
- [ ] **Step 4: 확인**
  - 콘솔: `await evLiveSelfCheck()` · `evSelfCheck()` · `evLineSelfCheck()` · `evrSelfCheck()` · `homeSelfCheck()` · `tourSelfCheck()` 통과
  - 시연 해시가 Step 1과 같다
  - `devlive.py`(환경 변수 `HR_EVAL_FIRST_YEAR=2026`)로 가상인사 · 가상팀장 · 가상직원 · 가상본부장 · 가상대표로 로그인해 PC 폭에서: HR 마감일 저장 → 평가 시작 → 직원 자기평가 저장 · 증빙 · 제출 → 팀장 채점 · 제출 → 본부장 배정 · 고치기 · 마감 → 팀장 화면 "고쳤습니다"(내용 없음). 그림을 `클로드 코드/뉴디자인/평가 실제 - 1 HR 진행표 · 2 직원 자기평가 · 3 팀장 평가 · 4 본부장 배정.png`로 남긴다.
  - `.ci` 검사(`check-html` · 개인정보 · 한글) 통과
- [ ] **Step 5: 커밋** — `git commit -m "평가 진행 화면: 실제 로그인이면 서버 자료 · 판단은 서버 칸 · 동작은 서버로"`

---

### Task 8: 문서 · 검사 · PR ①

**Files:**
- Modify: `CLAUDE.md`(평가·성과 항목에 "실제 로그인이면 평가 진행 (3단계 ①)" — 코드가 지키는 약속만, 규칙 원본은 설계 8장을 가리킨다 · "§7.1" 안내 줄 · 다음 DB 번호 021), `docs/superpowers/specs/2026-10-20-kpi-progress-and-live-evaluation-design.md`(맨 위 상태 줄 · 12장에 "인사담당자 결정 (2026-10-26)" · "내 판단 R1~R14" 요약), `DEPLOY.md`(증빙 폴더 `data/eval_files/`를 백업에 넣기 — 서버 백업 스크립트에 한 줄, 인사담당자 ssh · nginx 설정이 바뀌므로 반영 뒤 증빙 10MB 올리기를 한 번 해 본다), `README.md`(평가·성과 줄)

- [ ] **Step 1: 전체 검사** — `cd backend && python -m hr_eval.selfcheck && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features && python -m auth_check && python -m push check && python -m contract_doc`, 저장소 루트에서 `.ci` 스크립트들. 판 19 → 20 올리기 시험(Task 1 Step 4).
- [ ] **Step 2: 최종 검토** — 브랜치 전체를 새 검토자(가장 좋은 모델)에게: 공개 전 숨김(`_row_json`을 안 거치는 응답이 있나) · 쓰기 길의 판단이 `_can`과 같은가 · 감사로그 · 알림 글 · 파일 경로 · 시연 해시.
- [ ] **Step 3: 문서** — 위 Files.
- [ ] **Step 4: push · PR** — 사용자 "올려줘" 뒤. 본문: 무엇을 · 왜 · R1~R14 · **배포 때 할 일**(nginx 설정 반영 확인, 백업 스크립트에 증빙 폴더, 서버 로그 "DB 준비 완료 (판 20)") · @holee9 보안 확인 요청(새 API · 파일 올리기 · 공개 전 숨김). CI 6종 확인.
- [ ] **Step 5: merge** — 사용자 "merge 해줘"(본부장 확인 뒤)로만. 운영 반영 · 서버 로그 판 20 확인 · 워크트리 · 브랜치 정리.

---

# Part B — PR ② 대표이사 확인 · HR 확정 · 결과 공개 · 평가 결과 · 이의 신청

> ①을 merge한 뒤 main을 합쳐 시작한다. 아래 이름이 ①에서 실제로 만든 것과 다르면 ① 쪽 이름을 따른다.
> **② 구현 완료 (2026-10-27, 로컬 — feature/eval-live-2).** 계획 글과 다른 결정은 작업 기록(ledger)의 Ruling B1~B12에 있고, CLAUDE.md의 "실제 로그인이면 평가 결과 · 이의 신청" 항목이 그 결과다.

### Task 9: 서버 — 대표이사 확인 · 돌려보내기 · HR 확정 · 되돌리기 · 결과 공개

**Files:**
- Modify: `backend/evaluation.py`, `backend/push.py`(`eval_open`), `backend/auth_check.py`(`check_eval_close(hr)`)

**Interfaces:**
- Consumes: ① `_ctx` · `_can` · `pool_state` · `check_pool` · `pool_ref` · `access.can(…, Act.CONFIRM | SEND_BACK | FINALIZE, PoolRef, delegation=)`
- Produces:
  - `def _pool_can(ctx, me, key) -> dict` — `pools[].can`: `close`(①) · `confirm` = 상태 `CEO` · `can(me, Act.CONFIRM, ref, delegation)` · `back` = `DIV:` · 상태 `CEO` · `can(…, Act.SEND_BACK, …)` · `finalize` = 상태 `HR` · HR · 공개 전 · `undo` = 상태 `HR` · `DONE` · HR · 공개 전. (`access.can`은 맡긴 HR이 묶음 사람 중 자기가 있으면 거절한다 — `_ids`.)
  - **`POST /api/eval/pools/confirm`** `{"year", "pools": [열쇠…]}` → `{"ok", "done": [열쇠…]}` — 거절: 목록이 아니거나 비었음 400 → 하나라도 `can.confirm`이 아님 403(어느 묶음인지 문구에) — **모두 되거나 아무것도 안 된다**. 모두 `HR`로 · `ev_pools.ceo_at/by/behalf`. 감사로그 묶음마다 `EVAL_POOL_CONFIRMED {year, pool}`. 저장 뒤 HR 전원에게 `eval_turn`.
  - **`POST /api/eval/pools/back`** `{"year", "pool", "reason"}` — `can.back` 403 → 이유 1~200자 400. 모두 `GRADE`로 · `back_*` · `closed_at` 비움. 감사로그 `EVAL_POOL_RETURNED`(이유 없음). 저장 뒤 배정자에게 `eval_turn`.
  - **`POST /api/eval/pools/finalize`** `{"year", "pools"}` — `can.finalize`(모두 아니면 403) → 모두 `DONE`. 감사로그 `EVAL_POOL_FINALIZED`.
  - **`POST /api/eval/pools/undo`** `{"year", "pool"}` — `can.undo` 403. `DONE` → `HR` / `HR` → `ready_stage`(직속 `GRADE` · 그 밖 `CEO`)이고 `ceo_*` 비움. `undo_at/by/from`. 감사로그 `EVAL_POOL_UNDONE {year, pool, from}`.
  - **`POST /api/eval/admin/release`** `{"year"}` — HR 403 → 이미 공개 409 → 제외 안 된 사람 중 `DONE`이 아닌 사람 409 `{"error": "아직 확정되지 않은 분이 있습니다."}`. `released_at/by`. 감사로그 `EVAL_RELEASED {year, count}`. 저장 뒤 제외 안 된 대상 전원에게 `eval_open`.
  - `_row_json`: 공개 뒤에는 `level`이 `full`을 주므로 그대로. `todo`: ①에서 0이던 대표이사 `CEO` · `FIX` 묶음 · HR `HR` 묶음 · 맡김 몫을 센다.

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `확인은_대표이사가_묶음째로`: 세 묶음이 CEO 대기일 때 대표이사가 둘을 확인 → 둘 HR · 하나 그대로 · 상태가 CEO가 아닌 묶음을 섞으면 403이고 아무것도 안 바뀜 · 본부장 403
  - `HR은_자기_묶음을_확인_못_한다`: 맡김 뒤 HR 담당자가 직속 묶음(본인 있음) 확인 403 · 전사 팀장 묶음 200(`on_behalf_of` = 대표이사)
  - `돌려보내기는_본부_팀원만`: 팀장 묶음 403 · 이유 없이 400 · 본부 팀원 → GRADE · 본부장에게 알림 · 다시 마감하면 `back` 기록이 지워짐
  - `확정은_한_번`: 두 HR이 동시에 확정 → 하나만 · 감사로그 하나
  - `되돌리기`: DONE → HR → (직속) GRADE · (본부) CEO · 공개 뒤 409
  - `공개는_모두_확정된_뒤`: 하나라도 DONE이 아니면 409 · 공개 → 대상 휴대폰에 `eval_open`(제외된 사람 빼고) · 또 409
  - `공개하면_보인다`: 공개 뒤 팀장 응답의 그 사람 `hidden` 거짓 · 고친 등급 C가 보임 · 본인 `me`에 최종 등급
- [ ] **Step 2~4** — `python -m auth_check` 실패 → 구현 → 통과
- [ ] **Step 5: 커밋** — `git commit -m "평가 API: 대표이사 확인 · 돌려보내기 · HR 확정 · 되돌리기 · 결과 공개"`

---

### Task 10: 서버 — 평가 결과 읽기 · 이의 신청

**Files:**
- Modify: `backend/evaluation.py`, `backend/push.py`(`eval_appeal` · `eval_answer` · `LABELS["e3"] = "이의 신청"` · `pending()`), `backend/auth_check.py`(`check_eval_appeal(hr)`)

**Interfaces:**
- Consumes: Task 9, Task 2 `total` · `band` · `cap` · `above`
- Produces:
  - **`GET /api/eval/results?year=`**(없으면 공개된 해 중 가장 최근) → `{"years": [공개된 해, 최근부터], "year", "released", "appealDue", "mine": _row_json(나) | null, "team": [_row_json …], "appeals": [...]}`.
    - `team`: 공개 뒤에만 — 팀장 = 자기 팀원 · 본부장 = 자기 본부 · 대표이사 · HR = 전원(Task 2 `level` 5번과 같은 범위). HR은 공개 전에도 전원(결과 공개 · 이의 신청 화면).
    - 줄의 `appeal`: 본인 · HR · 대표이사에게만 `{"reason", "at", "status", "answer", "before", "after", "marks"}` — 본인에게는 `CEO` 단계의 `after` · `marks` · `answer`를 주지 않는다(화면 `evAppealBox`의 `full`과 같다), 그 밖의 사람에게는 `{"status"}`만.
    - `appeals`: HR = `OPEN`, 대표이사(또는 맡긴 HR) = `CEO` 줄.
  - **`POST /api/eval/me/appeal`** `{"year", "reason"}` — 대상 404 → 공개 전 403 → 오늘(한국) > 공개일 + 7일 403 `{"error": "이의 신청 기간이 끝났습니다."}` → 이미 신청 409 → 사유 1~500자 400. `status OPEN` · `before = grade`. 감사로그 `EVAL_APPEALED`(사유 없음). 저장 뒤 HR 전원(본인 빼고)에게 `eval_appeal`.
  - **`POST /api/eval/appeals/<uid>/answer`** `{"year", "grade", "answer", "marks"?}` — HR 403 → 없음 404 → `OPEN` 아님 409 → 답변 1~500자 400 → `marks` 모양 400 → 고친 점수로 `cap` 넘음 · 연차 규칙 400(정원은 따지지 않는다). 등급 그대로면 `marks`를 반영하고 `DONE`(저장 뒤 신청한 사람에게 `eval_answer`), 바뀌면 `CEO`로(점수 · 등급은 아직 그대로 — 대표이사(맡겼으면 HR, 본인 빼고)에게 `eval_appeal`). 감사로그 `EVAL_APPEAL_ANSWERED {year, changed: bool}`.
  - **`/confirm`** `{"year"}` — `can(me, Act.APPEAL_CONFIRM, target, delegation)` 403 → `CEO` 아님 409 → 다시 `cap` 검사 400. `marks` · `grade = after` 반영 · `DONE` · `decided_*`. 감사로그 `EVAL_APPEAL_CONFIRMED`. 신청한 사람에게 `eval_answer`. **`/back`** — 같은 403 · 409 → `OPEN`으로 · `after` · `marks` 비움. 감사로그 `EVAL_APPEAL_RETURNED`.
  - `todo`에 `e3`: HR = `OPEN` 수, 대표이사(또는 맡긴 HR, 본인 것 빼고) = `CEO` 수 — `push.pending`의 `e3`도 같은 셈.
  - `cur_year`(R13)는 이 표의 `OPEN` · `CEO`를 이미 본다(①에서 표가 있었다).

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `이의_신청은_7일`: 공개일 + 7일 한국 23:59 200 · 다음 날 0시 403 · 공개 전 403 · 두 번째 409 · 남의 것을 신청할 길이 없다
  - `유지는_바로_끝`: HR 답변(등급 그대로) → DONE · 신청한 사람 알림 · 결과 `appeal.status` DONE
  - `바꾸려면_점수와_대표이사`: 점수를 안 고치고 올리면 400 · 고쳐서 올림 → CEO · 그때까지 `grade` 그대로 · 본인 응답에 `after` · `answer` 없음 · 대표이사 확인 → 등급 · 항목 등급 반영 · 돌려보내기 → OPEN
  - `정원은_따지지_않는다`: 정원이 찬 묶음에서도 C → B 200
  - `HR_본인_이의는_대신_확인_못_한다`: HR 담당자의 이의(CEO 단계)를 맡긴 HR 담당자 본인이 확인 403 · 대표이사 200
  - `결과를_보는_범위`: 공개 뒤 팀장 `team` = 자기 팀원 · 본부장 = 자기 본부 · 다른 팀장에게 그 팀원 없음 · 직원 `team` 빈 목록 · 공개 전 팀장 `team` 빈 목록 · HR은 공개 전에도 전원
  - `지난_해`: 공개한 두 해 → `years` 둘(최근부터) · `?year=` 지난해 결과
- [ ] **Step 2~4** — 실패 → 구현 → 통과
- [ ] **Step 5: 커밋** — `git commit -m "평가 API: 결과 읽기 · 이의 신청 · HR 답변 · 대표이사 확인"`

---

### Task 11: 서버 — 보고서 엑셀을 DB에서

**Files:**
- Modify: `backend/eval_report.py`, `backend/auth_check.py`(`check_eval_report`에 절)

**Interfaces:**
- Consumes: `eval_report.clean` · `build`(그대로), Task 10의 `_ctx`
- Produces:
  - **`GET /api/eval/report?year=`** — HR만(403) → 시작 안 한 해 404. 서버가 DB에서 `clean()`이 받는 모양을 만들어(`div` · `team` · `no` = 구성원 사원코드(`users.employee_no`, 없으면 "") · `name` · `title` · `pool` · `total`(KPI 없으면 None) · `grade`(이의 처리 중이면 "" — 화면 `evExportGrade`) · `stage` 글자(화면 `EV_STAGES`, '등급' → '배정') · `appeal` 글자) **같은 `build()`**로 만든다. 공개 전에도 HR은 받는다(설계 9장). 파일 이름 `{year}년 인사평가 결과.xlsx`, `no-store`. 감사로그 `EVAL_REPORT {year}`.
  - `POST /api/eval/report`(화면이 시연 결과를 보내는 길)는 시연을 위해 그대로 둔다.
- [ ] **Step 1: 실패하는 검사 쓰기** — `보고서는_DB에서`: HR 200 · 팀장 403 · openpyxl로 열어 '명단' 시트 제목 줄이 사번 · 이름 · 최종등급 · 이의 처리 중인 사람 등급 빈칸 · 요약 시트에 이름이 없음 · 사원코드가 든 사람 `no` 칸
- [ ] **Step 2~4** — 실패 → 구현 → 통과
- [ ] **Step 5: 커밋** — `git commit -m "평가 결과 보고서: 실제 평가는 서버가 DB에서 만든다"`

---

### Task 12: 화면 — 확인 · 확정 카드 · 평가 결과 탭 · 이의 신청

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 9 · 10 · 11
- Produces:
  - `evPoolCard('ceo'|'hr')`의 실제 로그인: 체크 · [선택한 n개 확인 (HR로)] · [선택한 n개 확정] · [돌려보내기] · [되돌리기]를 `pools[].can`대로 그리고 `evPoolDone` · `evReturn` · `evUndo`가 `pools/confirm` · `back` · `finalize` · `undo`로 간다.
  - `EV_RES = { state:'', data:null, year:null }` · `async function evResLoad(year)` — `/api/eval/results`, 받은 줄은 `evFromLive`로. 평가 결과 탭 실제 로그인 갈래:
    - `evResultContent()`: 불러오는 중 · `offline`(그해가 `FIRST_YEAR` 전이고 공개한 해가 없음)이면 "아직 포털에서 공개한 평가 결과가 없어요." 안내. 맨 아래 "시연용 가상 결과입니다…" 줄과 `roleSwitcher`의 시연 안내를 뺀다.
    - `evMyResult` · `evTeamResults` · `evResHrPanel` · `evAppealCeoCard`가 `EV_RES.data`를 읽는다. 연도 단추는 `years`(R14 — 2026년 포털 밖 결과는 없다). `EV_HISTORY` · 시연 단추("시연: 확정 상태로 채우기")는 실제 로그인에서 쓰지 않는다.
    - 결과 카드(`evResultCard`): 실제 로그인이면 "능력급 인상률" 칸과 "연봉계약서 능력급에 반영" 줄을 빼고 `공개일 YYYY-MM-DD`만(R14). 평가자 이름은 서버 `rater`.
    - `evPublish` → `admin/release` · `evAppealSubmit` → `me/appeal` · `evAnswerSubmit` → `appeals/<uid>/answer` · `evAppealCeo` → `confirm` · `back` · `evReport` → 실제 로그인이면 `GET /api/eval/report?year=`(받은 Blob을 지금처럼 `rzSave`).
    - `evSyncContracts`는 실제 로그인에서 부르지 않는다(계약서와 잇지 않는다).
  - `TODO.e3`의 실제 로그인 = 서버 `todo.e3`(평가 진행 `state`의 `todo`에 `e3`를 더해 함께 받는다).
  - 평가 진행 탭 맨 위의 안내: 공개 뒤에는 진행표 "결과 공개 · 이의 신청" 줄이 서버 값.
  - `evLiveSelfCheck`에 절: 결과 탭 — 공개 전 본인 "아직 공개 전" · 공개 뒤 등급 · 능력급 칸 없음 · 이의 신청 D-n · 대표이사 확인 카드 · `TODO.e3`.
- [ ] **Step 1~4** — Task 7과 같은 순서(시연 해시 · 새 검사 먼저 · 구현 · 콘솔 검사 · devlive로 대표이사 확인 → HR 확정 → 공개 → 직원 결과 → 이의 신청 → HR 답변(등급 변경) → 대표이사 확인 → 보고서 엑셀, 그림 `뉴디자인/평가 실제 - 5 대표이사 확인 · 6 HR 확정 공개 · 7 직원 결과 · 8 이의 신청.png`)
- [ ] **Step 5: 커밋** — `git commit -m "평가 결과 화면: 실제 로그인이면 서버 결과 · 확인 · 확정 · 공개 · 이의 신청"`

---

### Task 13: 문서 · 검사 · PR ②

**Files:**
- Modify: `CLAUDE.md`(평가 결과 항목 · §7.1 안내 문구 정리 · "평가 3단계에서 같은 함수로 붙인다"던 알림 줄 · 다음 DB 번호), 설계 문서 상태 줄(3단계 끝), `클로드 코드/휴대폰 알림 항목 정리 (2026-10-26).md`(저장소 밖 — 평가 알림 줄 넷 더하기)

- [ ] **Step 1~5** — Task 8과 같다(전체 검사 · 최종 검토 · 문서 · "올려줘" 뒤 push · PR · @holee9 · "merge 해줘"로만 merge · 정리). PR 본문에 **2026년에는 실제로 쓰지 않는다**(R2 — 2027년 12월 첫 평가 전에 HR이 devlive가 아닌 운영에서 마감일 저장까지 한 번 해 보기를 권한다)를 적는다.
