# 평가·성과 실제화 1단계 — 조직 관계 · KPI 계획서 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 로그인한 직원이 2027년 KPI 계획서(정기·비정기 포함)를 쓰고 제출하면, 서버가 정한 "바로 위 관리자"가 확정 · 돌려보내기 · 해제 · 거절을 하고, HR이 작성 기간을 열고 닫는다.

**Architecture:** 서버는 순수 규칙(`hr_eval/domain/org.py` · `kpi_plan.py`)과 Flask 블루프린트(`backend/kpi.py`, `game.py`와 같은 모양)로 나눈다. 계획서 내용은 사람·연도마다 JSON(작성본 1 + 확정본 판들)으로 둔다. 화면은 `public/index.html` 한 파일에서 `AUTH.live`일 때만 서버 자료(`KPI_LIVE`)로 그리고, 시연 모드(가상 자료 · 역할 전환)는 손대지 않는다.

**Tech Stack:** Python 3 + Flask + SQLite(`backend/`), 단일 HTML·바닐라 JS(`public/index.html`), 검사는 프레임워크 없이 `hr_eval.selfcheck` · `auth_check` · `hr_eval.sql.check_schema`, 화면 확인은 `클로드 코드/devlive.py`(가짜 하이웍스) + playwright.

**Spec:** `docs/superpowers/specs/2026-10-20-kpi-progress-and-live-evaluation-design.md` (이 계획은 그 문서의 3장 1단계 · 4장 · 5장 · 11장 1·2번 화면). 2·3단계는 각자 계획을 따로 쓴다.

## Global Constraints

- 시연 모드는 그대로: `AUTH.live`가 거짓이면 지금 화면(가상 자료 · 역할 전환 · 지금의 승인 흐름)이 한 글자도 바뀌지 않는다.
- 모든 판단은 서버가 다시 한다(기간 · 바로 위 관리자 · 상태 · 볼 수 있는 범위). 화면의 단추 막기는 안내다.
- 날짜 판정은 **한국 날짜**: `auth._today()`(서버 시계는 UTC).
- 감사로그에 목표 · 사유 문장을 넣지 않는다 — 행동 이름 · 대상 id · 숫자만. HR이 대신 하면 `on_behalf_of` = 원래 관리자.
- DB는 판 파일을 더한다: 지금 판 9 → `010_kpi.sql`. `001_init.sql`의 `kpi_sheets` · `kpis` · `kpi_change_requests`는 건드리지 않고 쓰지 않는다(운영에 자료 없음).
- 공개 저장소: 시험 자료 이름은 전부 가상(`가상1` 등). 휴대폰 번호 모양 문자열을 파일에 쓰지 않는다(`check-pii`).
- 작업 폴더 파일은 CRLF — 고칠 때 줄 끝을 맞춘다. `index.html`의 여러 곳을 고칠 때는 `assert s.count(old)==1` 패치 스크립트.
- 평가·성과는 PC 전용(768px 이하에서 열리지 않음) — 휴대폰 모양은 만들지 않는다.
- 화면 문구는 쉬운 한국어. 상태 글자 앞의 아이콘(✓ 등)은 **고정 폭 자리**를 늘 비워 둬 글자 시작을 맞춘다(인사담당자 지적).
- 글자 길이 한도(서버 · 화면 같게): 팀목표 · 개인목표 · 실행목표 · 측정식 200, 담당자 30, S~D 기준 각 100, 단위 10, 일정 이름 40, 돌려보내기 · 거절 · 수정 요청 사유 200. 계획서 JSON 200KB.
- 실행목표 최소 3개, 가중치 각 0 초과 · 합 정확히 100(소수 둘째 자리까지), 비정기 일정 1~24개 · 날짜는 그해 안.

## Review Focus

1. **작성 기간의 끝 — 한국 날짜 자정**: 종료일 23:59(KST)에는 제출되고, 다음 날 00:10(KST, UTC로는 같은 날 15:10)에는 막혀야 한다 → Task 5 `기간_끝은_한국_자정이다`.
2. **소수 가중치**: 33.33 + 33.33 + 33.34 = 100이 통과하고 33.33 × 3 = 99.99는 막혀야 한다(부동소수 오차로 거꾸로 되면 안 됨) → Task 3 `가중치_소수_합은_정확히_100`.
3. **늦은 단추**: 관리자가 이미 돌려보낸 계획서를 다른 창에서 다시 "확정"하거나, 본인이 확정된 뒤 옛 화면에서 "제출"하면 409와 지금 상태 → Task 6 `이미_처리된_것은_409`.
4. **관리자가 없거나 바뀜**: 대표이사가 없어 관리자가 비면 HR만 처리할 수 있어야 하고, 제출 뒤 팀을 옮기면 새 관리자 처리함에 뜨고 옛 관리자는 못 한다 → Task 6 `팀을_옮기면_새_관리자가_처리한다`, Task 2 `대표이사가_없으면_관리자가_없다`.
5. **본부장이 '임원' 부서 소속**: 맡은 부서를 본부로 지정하면 그 본부 팀장 · 직속 직원의 관리자가 대표이사가 아니라 그 본부장이어야 한다 → Task 2 `맡은_부서로_본부장을_찾는다`.

---

### Task 1: DB 판 10 — 맡은 부서 · KPI 기간 · 계획서 · 확정본 · 대표이사 맡김

**Files:**
- Create: `backend/hr_eval/sql/010_kpi.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` (expected 표 목록 + 새 절 "22. KPI 계획서 (010)")

**Interfaces:**
- Produces (표 · 칸 — 뒤 Task가 이 이름을 쓴다):
  - `users.led_department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL` — 맡은 부서. NULL = 본인 소속
  - `kpi_years(year INTEGER PRIMARY KEY CHECK (year BETWEEN 2026 AND 2100), edit_from TEXT NULL, edit_to TEXT NULL, eval_start TEXT NULL, updated_at TEXT NOT NULL, updated_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL)` — `edit_from`·`edit_to`는 둘 다 있거나 둘 다 없음, 날짜 모양(`date(x) IS x`), `edit_to >= edit_from`
  - `kpi_windows(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, year INTEGER NOT NULL, edit_from TEXT NOT NULL, edit_to TEXT NOT NULL, set_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL, set_at TEXT NOT NULL, PRIMARY KEY (user_id, year))` — 한 사람만 따로 연 기간
  - `kpi_plans(user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT, year INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('DRAFT','SUBMITTED','CONFIRMED','CHANGE_REQUESTED')), draft TEXT NOT NULL CHECK (json_valid(draft) AND json_type(draft)='array'), confirmed_version INTEGER NULL CHECK (confirmed_version >= 1), note TEXT NULL, note_kind TEXT NULL CHECK (note_kind IN ('RETURNED','REJECTED')), change_reason TEXT NULL, submitted_at TEXT NULL, updated_at TEXT NOT NULL, PRIMARY KEY (user_id, year))` + CHECK: `status IN ('CONFIRMED','CHANGE_REQUESTED')` → `confirmed_version IS NOT NULL` · `status='CHANGE_REQUESTED'` → `change_reason IS NOT NULL` · `(note IS NULL) = (note_kind IS NULL)`
  - `kpi_versions(user_id INTEGER NOT NULL, year INTEGER NOT NULL, version INTEGER NOT NULL CHECK (version >= 1), content TEXT NOT NULL CHECK (json_valid(content) AND json_type(content)='array'), confirmed_at TEXT NOT NULL, confirmed_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT, on_behalf_of INTEGER NULL REFERENCES users(id) ON DELETE SET NULL, PRIMARY KEY (user_id, year, version), FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT)` + 트리거 `trg_kpi_versions_frozen`(UPDATE 막음) · `trg_kpi_versions_no_delete`(DELETE 막음)
  - `ceo_delegation(ceo_user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, since TEXT NOT NULL)` — 줄이 있으면 "HR에 맡기기" 켜짐

- [ ] **Step 1: `check_schema.py`에 실패하는 검사 쓰기** — `expected`에 `"kpi_years","kpi_windows","kpi_plans","kpi_versions","ceo_delegation"`를 더하고, `check_login_tables` 끝에 절 `print("22. KPI 계획서 (010)")`:
  - `_report(True, ...)` 정상: 2027년 기간 저장 · DRAFT 계획서 저장(`draft='[]'`) · 확정본 판 1 저장 후 계획서를 CONFIRMED · confirmed_version=1로 바꿈
  - 거부(`_rejects`): 기간 한쪽만(`edit_from`만), 종료일 < 시작일, `'2027-02-30'`, 상태 `'APPROVED'`, CONFIRMED인데 confirmed_version NULL, CHANGE_REQUESTED인데 change_reason NULL, note만 있고 note_kind 없음, `draft='{}'`, 확정본 판 0, 확정본 UPDATE, 확정본 DELETE, 없는 계획서의 확정본
- [ ] **Step 2: 실패 확인** — `cd backend && python -m hr_eval.sql.check_schema` → `테이블 … 생성` 실패와 새 절의 실패
- [ ] **Step 3: `010_kpi.sql` 쓰기** — 위 Interfaces 그대로. 파일 머리에 지금 판 파일들처럼 "왜 · 언제(2026-10-20 인사담당자 결정)" 주석, `users.led_department_id`는 "하이웍스에서 본부장이 '임원' 부서에 들어가도 맡은 본부를 따로 둔다" 주석
- [ ] **Step 4: 통과 확인** — `python -m hr_eval.sql.check_schema` → `검사 N건 전부 통과` · `판 10`
- [ ] **Step 5: 커밋** — `git add backend/hr_eval/sql/010_kpi.sql backend/hr_eval/sql/check_schema.py && git commit -m "DB 판 10: 맡은 부서 · KPI 작성 기간 · 계획서 · 확정본 · 대표이사 맡김"`

### Task 2: 바로 위 관리자 — `hr_eval/domain/org.py`

**Files:**
- Create: `backend/hr_eval/domain/org.py`
- Modify: `backend/hr_eval/selfcheck.py` (검사 함수 + `CHECKS`)

**Interfaces:**
- Produces:
  - `NO_COL: tuple[str, ...] = ("임원", "비상근")` — 화면 `ORG_NO_COL`과 같은 값
  - `@dataclass(frozen=True) class Dept: id: int; name: str; parent_id: int | None`
  - `@dataclass(frozen=True) class Person: id: int; department_id: int; position: Role | None = None; led_department_id: int | None = None; is_active: bool = True`
  - `def is_direct_team(dept: Dept, depts: Sequence[Dept]) -> bool` — 맨 위(`parent_id is None`)이면서 아래 팀이 없고 이름이 `NO_COL`에 없다
  - `def led_dept(p: Person) -> int | None` — 팀장 · 본부장이면 `led_department_id or department_id`, 아니면 None
  - `def manager_of(user_id: int, people: Sequence[Person], depts: Sequence[Dept]) -> int | None` — 설계 4장 표. 비활성인 사람은 관리자가 될 수 없다. 자기 자신은 돌려주지 않는다

- [ ] **Step 1: 실패하는 검사 쓰기** (`selfcheck.py`, 가상 조직 하나를 `_가상조직()`으로 만들어 함께 쓴다): 본부 B(10) 아래 팀 T1(11) · T2(12), 맨 위 직속 팀 S "지원팀"(20, 아래 없음), 맨 위 "임원"(30). 사람: 대표이사 1(소속 30), 본부장 2(**소속 30, 맡은 부서 10**), 팀장 3(맡은 11), 직원 4(11), 직원 5(12, 팀장 없음), 직원 6(본부 10 바로 아래), HR 팀장 7(팀장, 맡은 20), HR 직원 8(20), 상무이사 9(소속 30, 직책 없음)
  ```python
  def 맡은_부서로_본부장을_찾는다():
      p, d = _가상조직()
      assert manager_of(3, p, d) == 2 and manager_of(6, p, d) == 2 and manager_of(5, p, d) == 2
  def 팀원의_관리자는_팀장이다():
      p, d = _가상조직(); assert manager_of(4, p, d) == 3 and manager_of(8, p, d) == 7
  def 직속팀_팀장과_본부장과_임원의_관리자는_대표이사다():
      p, d = _가상조직(); assert manager_of(7, p, d) == 1 and manager_of(2, p, d) == 1 and manager_of(9, p, d) == 1
  def 대표이사의_관리자는_없다():
      p, d = _가상조직(); assert manager_of(1, p, d) is None
  def 대표이사가_없으면_관리자가_없다():
      p, d = _가상조직(); p = [x for x in p if x.id != 1]; assert manager_of(2, p, d) is None and manager_of(9, p, d) is None
  def 비활성_팀장은_건너뛰고_본부장이다():
      p, d = _가상조직(); p = [replace(x, is_active=False) if x.id == 3 else x for x in p]; assert manager_of(4, p, d) == 2
  def 직속팀_판별():
      _, d = _가상조직(); by = {x.id: x for x in d}
      assert is_direct_team(by[20], d) and not is_direct_team(by[10], d) and not is_direct_team(by[30], d) and not is_direct_team(by[11], d)
  ```
- [ ] **Step 2: 실패 확인** — `cd backend && python -m hr_eval.selfcheck` → 새 검사들 `[실패]` (ImportError)
- [ ] **Step 3: `org.py` 구현** — 위 시그니처. 관리자를 찾는 순서는 설계 4장 표 그대로(팀 → 그 본부 → 대표이사), 본부장 · 팀장은 `led_dept`로 찾는다
- [ ] **Step 4: 통과 확인** — `python -m hr_eval.selfcheck` → 마지막 줄 `N/N 통과`
- [ ] **Step 5: 커밋** — `git commit -m "바로 위 관리자 규칙 (맡은 부서 · 직속 팀 · 임원 부서)"`

### Task 3: 맡은 부서 저장 — 구성원 설정 (서버 + 화면)

**Files:**
- Modify: `backend/hr_eval/domain/members.py` (`Member`에 `led_department_id: int | None = None`, `validate_members`에 `depts: Sequence[Dept] = ()` 키워드)
- Modify: `backend/auth.py` (`_members` · `actor_of` · `members_list` · `members_save` · `_save_members`)
- Modify: `backend/hr_eval/selfcheck.py`, `backend/auth_check.py`
- Modify: `public/index.html` (구성원 설정 이름 창 `msSee` · 저장 `msAskSave` 쪽 보내는 값)

**Interfaces:**
- Consumes: Task 2 `Dept`, `is_direct_team`, `led_dept`, `NO_COL`
- Produces:
  - `validate_members(after, *, before=(), kpi_locked=(), depts=())` — 새 문제 코드 `LED_DEPT_INVALID`(팀장의 맡은 부서는 팀 또는 직속 팀, 본부장은 직속 팀 · `NO_COL`이 아닌 맨 위 부서). 한 부서의 장 한 명 검사(`POSITION_TAKEN`)는 **맡은 부서 기준**
  - `GET /api/members` 응답에 사람마다 `"ledDept": int | None`, 그리고 맨 위에 `"depts": [{"id", "name", "parent", "kind": "TEAM"|"DIVISION"|"DIRECT"|"NOCOL"}]`
  - `POST /api/members` 의 `changes[id]`에 `"ledDept": int | None` (직책이 팀장 · 본부장이 아니면 서버가 NULL로 바꿈)
  - `actor_of`: `led_team_id` · `led_division_id`를 `led_department_id or department_id`로

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `selfcheck.py`: `맡은_부서가_본부가_아니면_본부장이_될_수_없다`(본부장의 맡은 부서 = 팀 11 → `LED_DEPT_INVALID`), `한_본부의_본부장은_맡은_부서_기준으로_한_명`(소속은 다르고 맡은 부서가 둘 다 10 → `POSITION_TAKEN`)
  - `auth_check.py` 새 `check_led_dept(hr)`: 본부장으로 지정하며 `ledDept`=본부 → 200 · `/api/members`에 `ledDept` 보임 · `ledDept`=팀 → 400 · 직책을 없애면 `ledDept`가 None · HR 아닌 사람 403
- [ ] **Step 2: 실패 확인** — `python -m hr_eval.selfcheck`, `python -m auth_check`
- [ ] **Step 3: 서버 구현** — 위 Interfaces. 감사로그 `MEMBER_UPDATED`의 바뀐 칸에 `led_department_id` 포함(id 숫자라 그대로 남겨도 된다)
- [ ] **Step 4: 화면 구현** — 실제 로그인일 때만, 이름 창(`msSee`)에서 직책이 팀장 · 본부장이면 "맡은 부서" 고르기(`/api/members`의 `depts`에서 팀장은 TEAM · DIRECT, 본부장은 DIVISION만, 기본 = 본인 소속). 고르면 `msDraft[id].ledDept`, 저장 때 함께 보낸다. 표의 이름 아래 작은 글씨에 "맡은 부서: ○○"(본인 소속과 다를 때만)
- [ ] **Step 5: 통과 확인** — 두 검사 통과 + `devlive.py`로 HR 로그인 → 구성원 설정 → 본부장 이름 창에서 맡은 부서 바꾸고 저장 → 새로 고침해도 남는다
- [ ] **Step 6: 커밋** — `git commit -m "구성원 설정: 팀장·본부장의 맡은 부서"`

### Task 4: KPI 계획서 규칙 — `hr_eval/domain/kpi_plan.py`

**Files:**
- Create: `backend/hr_eval/domain/kpi_plan.py`
- Modify: `backend/hr_eval/selfcheck.py`

**Interfaces:**
- Produces:
  - 계획서 JSON 모양(서버 저장 · API · 화면 공통): `[{"team": str, "personal": str, "items": [{"rid": int?, "action": str, "formula": str, "weight": number, "owner": str, "S": str, "A": str, "B": str, "C": str, "D": str, "kind": "reg"|"irr", "target": number|null, "unit": str, "scheds": [{"sid": int?, "due": "YYYY-MM-DD", "name": str}]}]}]` — 정기(`reg`)는 `scheds`가 빈 목록, 비정기(`irr`)는 `target` null · `unit` ""
  - `def validate_plan(goals: object, year: int) -> list[Issue]` — 코드: `SHAPE`, `ITEMS_MIN`, `WEIGHT_POSITIVE`, `WEIGHT_SUM`, `GRADE_REQUIRED`, `KIND`, `TARGET`, `SCHED_MIN`, `SCHED_MAX`, `SCHED_DATE`, `TEXT_LONG`. 빈 목록 = 문제 없음
  - `def assign_ids(goals: list[dict], history: Sequence[list[dict]]) -> list[dict]` — `history` = 이 계획서의 확정본들(오래된 것부터). 새 행 · 새 일정에 번호를 준다(그 계획서에서 쓴 적 없는 가장 작은 다음 수). 같은 `rid`가 마지막 확정본에서 **업무 종류가 달랐으면** 새 `rid`. 중복 번호는 뒤의 것에 새 번호
  - `ACTIONS = ("save", "submit", "cancel", "confirm", "return", "request_change", "unlock", "reject")`
  - `def next_status(status: str, action: str) -> str` — 설계 5.3 그림. 안 되는 조합은 `StateConflictError`. `save`는 `DRAFT`에서만, `confirm`·`return`은 `SUBMITTED`에서만, `unlock`·`reject`는 `CHANGE_REQUESTED`에서만, `request_change`는 `CONFIRMED`에서만, `cancel`은 `SUBMITTED`에서만. `unlock` → `DRAFT`, `reject` → `CONFIRMED`, `return` · `cancel` → `DRAFT`
  - `def window_open(today: date, year_row: tuple[str|None, str|None, str|None] | None, person_row: tuple[str, str] | None) -> bool` — `(edit_from, edit_to, eval_start)`와 `(edit_from, edit_to)`. 사람 기간이 있으면 그것이 우선. `eval_start`가 있고 `today >= eval_start`면 거짓

- [ ] **Step 1: 실패하는 검사 쓰기** (`_좋은계획()` = 정기 2 · 비정기 1, 가중치 40/30/30, S~D 모두 채움)
  ```python
  def 좋은_계획서는_문제가_없다(): assert validate_plan(_좋은계획(), 2027) == []
  def 실행목표는_세_개부터(): assert "ITEMS_MIN" in _codes(_행빼기(_좋은계획(), 1))
  def 가중치_소수_합은_정확히_100():
      assert validate_plan(_가중치(_좋은계획(), [33.33, 33.33, 33.34]), 2027) == []
      assert "WEIGHT_SUM" in _codes(_가중치(_좋은계획(), [33.33, 33.33, 33.33]))
  def 달성기준은_모두_필요(): ...   # 한 행의 "C" = "" → GRADE_REQUIRED
  def 비정기는_그해_안의_일정이_하나_이상():   # scheds [] → SCHED_MIN, "2028-01-03" → SCHED_DATE, 25개 → SCHED_MAX
  def 정기의_목표_숫자는_양수거나_비움():   # target 0 → TARGET, None → 문제 없음
  def 모양이_틀리면_SHAPE():   # {} / [{"items": "x"}] / weight "40"
  def 새_행에_번호를_주고_종류를_바꾸면_새_번호(): ...   # history 마지막 판의 rid 1이 reg, 지금 rid 1이 irr → 새 rid
  def 상태_흐름():
      assert next_status("DRAFT", "submit") == "SUBMITTED" and next_status("SUBMITTED", "return") == "DRAFT"
      assert next_status("CONFIRMED", "request_change") == "CHANGE_REQUESTED" and next_status("CHANGE_REQUESTED", "reject") == "CONFIRMED"
      assert next_status("CHANGE_REQUESTED", "unlock") == "DRAFT"
      for bad in (("CONFIRMED", "confirm"), ("DRAFT", "unlock"), ("SUBMITTED", "save"), ("CHANGE_REQUESTED", "return")):
          _raises(StateConflictError, next_status, *bad)
  def 작성_기간_판정():
      y = ("2027-01-04", "2027-01-22", "2027-12-01")
      assert window_open(date(2027, 1, 22), y, None) and not window_open(date(2027, 1, 23), y, None)
      assert window_open(date(2027, 3, 2), y, ("2027-03-02", "2027-03-10"))
      assert not window_open(date(2027, 12, 1), ("2027-11-01", "2027-12-31", "2027-12-01"), None)
  ```
- [ ] **Step 2: 실패 확인** — `python -m hr_eval.selfcheck`
- [ ] **Step 3: 구현** — 가중치 합은 `Decimal(str(w))`로 더해 `== Decimal(100)`. 글자 한도는 Global Constraints 값
- [ ] **Step 4: 통과 확인** — `python -m hr_eval.selfcheck` → `N/N 통과`
- [ ] **Step 5: 커밋** — `git commit -m "KPI 계획서 규칙: 모양·가중치·정기/비정기·번호·상태 흐름·작성 기간"`

### Task 5: KPI API — 본인 쓰기 · HR 작성 기간

**Files:**
- Create: `backend/kpi.py` (블루프린트 `bp`, `game.py`처럼 `auth`의 `_db` · `_now` · `_today` · `_iso` · `_audit`을 `auth.`로 부른다 — 검사가 `auth._now`를 바꿔 끼운다)
- Modify: `backend/app.py` (`app.register_blueprint(kpi.bp)`)
- Modify: `backend/auth_check.py` (새 `check_kpi_owner(hr)`)

**Interfaces:**
- Consumes: Task 1 표, Task 2 `manager_of`, Task 4 `validate_plan` · `assign_ids` · `next_status` · `window_open`
- Produces (모두 로그인 필요 — `auth._guard`):
  - `def plan_year() -> int` — 한국 날짜의 올해
  - `def load_org(conn) -> tuple[list[Person], list[Dept]]` — 활성 · 비활성 모두 담음(Person.is_active)
  - `GET /api/kpi/me` → `{"year", "writesKpi": bool, "window": {"from", "to", "evalStart", "open": bool}, "plan": {"status", "draft", "note", "noteKind", "changeReason", "confirmed": {"version", "content", "at", "by"} | null} | null, "manager": {"id", "name"} | null}`
  - `POST /api/kpi/me/draft` `{"draft": [...]}` → `{"ok": true}` — 계획서가 없으면 DRAFT로 만든다. `DRAFT`일 때만, 기간 안에서만. 모양 검사는 `SHAPE` · 글자 한도만(나머지는 제출 때)
  - `POST /api/kpi/me/submit` → 전체 `validate_plan` 통과해야 `SUBMITTED` · `submitted_at`, 아니면 400 `{"error", "issues": [메시지]}`
  - `POST /api/kpi/me/cancel` → `SUBMITTED` → `DRAFT`
  - `POST /api/kpi/me/request-change` `{"reason"}` → `CONFIRMED` → `CHANGE_REQUESTED`
  - `GET /api/kpi/admin/year` · `POST /api/kpi/admin/year` `{"year", "editFrom", "editTo", "evalStart"}` (HR) — 빈 값 허용(기간 닫기 = 둘 다 비움)
  - 공통 오류: 기간 밖 403 `{"error": "KPI 작성 기간이 아닙니다."}`, 상태가 안 맞으면 409 `{"error", "status": 지금 상태}`, KPI 작성이 꺼진 사람 403
  - 감사로그 action: `KPI_SUBMITTED`, `KPI_CANCELLED`, `KPI_CHANGE_REQUESTED`, `KPI_YEAR_SET`. 임시 저장(draft)은 남기지 않는다(내용 없는 기록만 쌓인다)

- [ ] **Step 1: 실패하는 검사 쓰기** (`auth_check.py`, 가상 직원 `emp`(팀원) · `lead`(팀장)를 쓰고 2027년 대신 `plan_year()`)
  - `로그인_없이는_401` · `기간이_없으면_쓸_수_없다`(draft 403) · HR이 오늘을 포함한 기간을 열면 draft 200 · `GET /api/kpi/me`에 draft가 그대로, manager = 팀장
  - `틀린_계획서는_제출되지_않는다`(가중치 99 → 400, issues에 가중치 문구) · 좋은 계획서 제출 → `SUBMITTED` · 제출 뒤 draft 저장 409 · 취소 → `DRAFT`
  - `기간_끝은_한국_자정이다`: `auth._now`를 종료일 14:59Z(= 23:59 KST)로 → 제출 200, 종료일 15:10Z로 → 403
  - `HR_아니면_기간을_못_연다`(403) · 날짜 모양 틀리면 400
  - `큰_계획서는_막는다`(201KB → 400)
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — 위 Interfaces. 계획서 내용 문장은 감사로그에 넣지 않는다
- [ ] **Step 4: 통과 확인** — `python -m auth_check` → `로그인 검사 N건 전부 통과`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: 본인 작성·제출·취소·수정 요청, HR 작성 기간"`

### Task 6: KPI API — 관리자 처리 · HR 대신 · 처리함 · 사람별 기간 · 대표이사 맡김

**Files:**
- Modify: `backend/kpi.py`, `backend/auth_check.py` (새 `check_kpi_manager(hr)`)

**Interfaces:**
- Consumes: Task 5
- Produces:
  - `def decider(conn, actor: Actor, owner_id: int) -> tuple[bool, int | None]` — `(할 수 있나, on_behalf_of)`. 관리자 본인 → `(True, None)`. HR 권한자이고 본인 계획서가 아니면 → 관리자가 대표이사면 `ceo_delegation`에 줄이 있을 때만 `(True, 대표이사 id)`, 그 밖에는 `(True, 관리자 id 또는 None)`. 나머지 `(False, None)`
  - `POST /api/kpi/plans/<uid>/confirm` → `SUBMITTED` → `CONFIRMED`: `assign_ids` 뒤 새 판을 `kpi_versions`에(판 번호 +1, `confirmed_by` = 실제로 누른 사람, `on_behalf_of`), `confirmed_version` · note 비움
  - `POST /api/kpi/plans/<uid>/return` `{"note"}` → `DRAFT`, `note_kind='RETURNED'`
  - `POST /api/kpi/plans/<uid>/unlock` → `CHANGE_REQUESTED` → `DRAFT` (draft = 마지막 확정본 내용, note 비움, 확정본은 그대로 유효)
  - `POST /api/kpi/plans/<uid>/reject` `{"note"}` → `CONFIRMED`, `note_kind='REJECTED'`
  - 네 가지 모두 **계획서 주인의 작성 기간 안에서만**(본인 기준 `window_open`) — 밖이면 403
  - `GET /api/kpi/plans/<uid>` → 주인 · 관리자 · HR만, `GET /api/kpi/me`의 `plan`과 같은 모양 + `{"owner": {"id", "name", "dept"}}`
  - `GET /api/kpi/inbox` → `{"items": [{"userId", "name", "dept", "title", "status": "SUBMITTED"|"CHANGE_REQUESTED", "at", "reason", "items": n, "reg": n, "irr": n, "scheds": n, "proxy": bool}], "team": {"NONE": n, "DRAFT": n, "SUBMITTED": n, "CONFIRMED": n, "CHANGE_REQUESTED": n}}` — `items` = 내가 관리자인 것. HR이면 관리자가 없거나(None) 대표이사가 맡긴 것도 `proxy: true`로. `team` = 내가 바로 위 관리자인 KPI 작성자들의 상태 수(시안 2 "우리 팀 KPI 상태")
  - `GET /api/kpi/admin` (HR) → `{"year", "window", "people": [{"userId", "name", "dept", "manager", "status": "NONE"|DRAFT…, "confirmedVersion", "window": {...}|null}]}` — KPI 작성이 켜진 활성 직원 전원
  - `POST /api/kpi/admin/window` `{"userId", "year", "editFrom", "editTo"}` · `{"userId", "year", "clear": true}` (HR)
  - `GET /api/kpi/delegation` → `{"on": bool}` · `POST /api/kpi/delegation` `{"on": bool}` (대표이사 본인만, 감사로그 `CEO_DELEGATION_ON/OFF`)
  - 감사로그: `KPI_CONFIRMED` · `KPI_RETURNED` · `KPI_UNLOCKED` · `KPI_REJECTED` · `KPI_WINDOW_SET`, HR이 대신 하면 `on_behalf_of`

- [ ] **Step 1: 실패하는 검사 쓰기** (`check_kpi_manager`)
  - 팀원 제출 → 팀장 처리함에 1건 · `team["SUBMITTED"] == 1` · 다른 팀장 · 팀원 처리함 0건
  - `관리자가_아니면_처리_못_한다`: 다른 팀원이 confirm → 403
  - 확정 → `GET /api/kpi/me`의 confirmed.version 1 · 각 행 `rid`, 일정 `sid`가 붙음 · 감사로그 `KPI_CONFIRMED`
  - `이미_처리된_것은_409`: 같은 confirm 두 번째 → 409, return → 409
  - 돌려보내기 note 없으면 400 · 있으면 본인 `noteKind == "RETURNED"`
  - 수정 요청 → 해제 → draft = 확정본, 확정본 판 1 그대로 → 다시 제출 · 확정 → 판 2. 수정 요청 → 거절 → `CONFIRMED`, `noteKind == "REJECTED"`
  - `기간_밖에서는_관리자도_못_한다`: HR이 기간을 닫음 → confirm 403
  - `사람별_기간이_우선이다`: 전체 기간 닫힌 채 그 팀원에게만 오늘 포함 기간 → 그 팀원 제출 200, 다른 팀원 403
  - `HR이_대신_확정하면_원래_관리자가_남는다`: HR confirm → `kpi_versions.on_behalf_of` = 팀장 id, 감사로그 on_behalf_of
  - `HR은_본인_것을_대신_못_한다` · `대표이사_몫은_맡겨야_대신한다`: 본부장 제출 → HR confirm 403 → 대표이사가 맡김 켬 → HR confirm 200, on_behalf_of = 대표이사
  - `팀을_옮기면_새_관리자가_처리한다`: 제출 뒤 팀원의 부서를 다른 팀으로(`x("UPDATE users SET department_id …")`) → 옛 팀장 confirm 403, 새 팀장 처리함에 1건
  - `남의_계획서는_못_본다`: 다른 팀원 `GET /api/kpi/plans/<uid>` 403, HR 200
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — 위 Interfaces. 처리 하나는 `BEGIN IMMEDIATE` 한 트랜잭션(상태 읽기 → `next_status` → 쓰기 → 감사로그)
- [ ] **Step 4: 통과 확인** — `python -m auth_check`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: 관리자 확정·돌려보내기·해제·거절, HR 대신, 처리함, 사람별 기간, 대표이사 맡김"`

### Task 7: 화면 — 직원의 KPI 계획서 (실제 로그인)

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 5 · 6 API
- Produces:
  - `const KPI_LIVE = { state:'', me:null, inbox:[], admin:null }` · `async function kpiLiveLoad()` — `GET /api/kpi/me` · `GET /api/kpi/inbox`(+ HR이면 `/api/kpi/admin`), `ORG_LIVE`·`MS_LIVE`와 같은 state 방식
  - `function kpiLiveSheet()` — 실제 로그인일 때 `kpiMySheet()` 대신 그린다(시안 1)
  - `kpiDraft()`가 실제 로그인이면 `KPI_LIVE.me.plan.draft`를 돌려준다 → `kpiTable(goals, {edit:true})` · `kpiAddGoal` · `kpiAddItem` · `kpiDelItem`을 그대로 쓴다. 새 행 담당자 = `AUTH.me.name`. 계획서가 아직 없으면(`plan` null) 화면에서 `{status:'DRAFT', draft:[kpiNewGoal(AUTH.me.name, 3)]}`(행마다 `kind:'reg'`, `target:null`, `unit:''`, `scheds:[]`)로 시작하고, 처음 저장할 때 서버에 생긴다
  - `function kpiKindSection(goals, edit)` — "업무 종류와 진행 기록" 칸(시안 1): 실행목표마다 정기적/비정기적 단추, 정기 = 목표 숫자 · 단위, 비정기 = 예정 기한 · 일정 이름 줄들 + "＋ 예정 기한". **"확정되면 나의 업무에 KPI- … 가 들어갑니다" 안내는 1단계에 넣지 않는다** — 나의 업무 연동은 2단계라 그 전에는 사실이 아니다
  - `function kpiSteps(status)` — 단계 표시 네 칸(작성 → 제출 · 팀장 확인 → 확정 → 수정 요청 → 해제), `function kpiWindowBand(win)` — 기간 띠(열림: 초록 "작성 기간 m/d ~ m/d · N일 남음 · 기간이 끝나면 제출 · 수정 요청 · 확정 · 해제가 모두 막힙니다", 닫힘: 회색)
  - 단추: 임시 저장(`POST draft`) · 제출 · 제출 취소 · 수정 요청(사유 창). 돌려보내기 · 거절 사유는 띠 아래 붉은 한 줄 "김○○ 팀장이 돌려보냈어요: …"
  - 실제 로그인이면 평가·성과의 `roleSwitcher()` 안내("시연용 가상 자료") 대신 이 화면. 다른 탭(KPI 현황 · 평가 진행 · 평가 결과)은 1단계에서는 지금처럼 "시연용 가상 자료" 안내를 둔다
  - "이 화면 사용법"(`TOURS.kpi`): 실제 로그인이면 단계 표시 · 업무 종류 칸 · 제출 단추 단계를 더한다(없는 칸은 지금처럼 건너뜀)

- [ ] **Step 1: 화면 검사 함수 쓰기** — `kpiLiveSelfCheck()`(브라우저 콘솔): `kpiKindSection`이 정기 행에 목표 · 단위 칸, 비정기 행에 일정 줄 수만큼 칸을 그린다 · `kpiSteps('SUBMITTED')`의 둘째 칸이 `on` · 기간 닫힘이면 저장 · 제출 단추가 `disabled`
- [ ] **Step 2: 실패 확인** — devlive로 띄워 콘솔 `kpiLiveSelfCheck()` → `is not defined`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 확인** — devlive(가짜 하이웍스) + playwright 1440px: 직원 로그인 → 기간 없음 안내 → (HR로 기간 열기) → 작성 · 비정기 일정 둘 · 임시 저장 → 새로 고침해도 남음 → 제출 → 단계 표시 2 · 제출 취소 → 다시 제출. `kpiLiveSelfCheck()` 통과, 콘솔 오류 없음. 그림 `뉴디자인/KPI 실제 - 1 직원 작성.png`
- [ ] **Step 5: 커밋** — `git commit -m "목표설정: 실제 로그인이면 서버 계획서 — 업무 종류(정기·비정기)·단계 표시·작성 기간"`

### Task 8: 화면 — 관리자 처리함 · HR 기간 · 대표이사 맡김 · 처리할 일 숫자

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 6 API, Task 7 `KPI_LIVE`
- Produces:
  - `function kpiLiveInbox()` — "처리할 KPI n건"(시안 2): 줄마다 [제출 | 수정 요청] 배지 · 이름 · KPI 수 · 정기 n · 비정기 n(예정 기한 n개) · 제출 시각 · 수정 요청 사유, 단추 [내용 보기] [확정 | 돌려보내기] / [해제 | 거절]. 돌려보내기 · 거절은 사유 창(200자). HR이 대신 하는 줄에는 "대신 처리" 표시
  - `function kpiLiveView(uid)` — 내용 보기 창: `kpiTable(content)` 읽기 + `kpiKindSection(content, false)`
  - 우리 팀 KPI 상태(시안 2 아래 칸) — `GET /api/kpi/inbox`의 `team` 수로 배지 다섯 개(확정 · 제출 · 확인 대기 · 수정 요청 · 작성 중 · 아직 안 씀)
  - HR 관리 화면(`hrView==='admin'`)의 KPI 칸: 작성 기간 설정(시작 · 종료 · 평가 시작일), 전 직원 표(이름 · 부서 · 관리자 · 상태 · 사람별 기간 [열기/지우기]), "관리자가 없음" 줄은 붉은 글씨
  - 대표이사 화면: "HR에 맡기기" 스위치(`GET/POST /api/kpi/delegation`)
  - 처리할 일 숫자: 실제 로그인이면 `TODO.e1` = 처리함 건수 + 내 계획서가 돌려보내짐(1), `homePortalTasks()`의 '팀원 KPI 승인' 줄 = 처리함 건수('확정 대기'), '반려된 KPI 다시 작성' = 돌려보내짐 · 거절 안내

- [ ] **Step 1: 화면 검사 함수 확장** — `kpiLiveSelfCheck()`에: 처리함 두 종류 줄의 단추 이름 · 대신 처리 표시 · 실제 로그인일 때 `TODO.e1()`이 처리함 건수와 같다
- [ ] **Step 2: 실패 확인** — 콘솔 `kpiLiveSelfCheck()` → 새 줄 실패
- [ ] **Step 3: 구현**
- [ ] **Step 4: 확인** — devlive: 팀원 제출 → 팀장 로그인 → 메뉴 숫자 1 · 홈 오늘의 업무 → 돌려보내기(사유) → 팀원 화면에 사유 → 다시 제출 → 확정 → 팀원 단계 표시 3 → 팀원 수정 요청 → 팀장 해제 → 팀원 다시 작성. HR: 기간 설정 · 사람별 기간 · 대신 확정. 대표이사: 맡기기 켜고 끔. 그림 `뉴디자인/KPI 실제 - 2 팀장 처리함.png` · `3 HR 기간.png`
- [ ] **Step 5: 커밋** — `git commit -m "목표설정: 관리자 처리함(확정·돌려보내기·해제·거절)·HR 작성 기간·대표이사 맡김·처리할 일 숫자"`

### Task 9: 문서 · 전체 검사 · PR

**Files:**
- Modify: `CLAUDE.md` (평가·성과 항목에 "실제 로그인이면 목표설정은 서버 계획서" 절 — 바로 위 관리자 · 맡은 부서 · 상태 흐름 · 작성 기간 · HR 대신 · DB 판 10, 시안과 달라진 점)
- Modify: `docs/superpowers/specs/2026-10-20-kpi-progress-and-live-evaluation-design.md` (상태 줄: "1단계 구현 — PR #…")
- Modify: `README.md` (메뉴 표의 평가·성과 줄)

- [ ] **Step 1: 로컬 전체 검사** — `export PYTHONIOENCODING=utf-8; for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py; cd backend && python -m hr_eval.selfcheck && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features && python -m auth_check && python -m hr_eval.roster_sync --check` → 모두 통과
- [ ] **Step 2: 시연 모드 확인** — 정적 서버(`python -m http.server`)로 열어 역할 일곱 개 × 평가·성과 세 탭이 지금과 같은지, `kpiSelfCheck()` · `kvSelfCheck()` · `evSelfCheck()` 통과
- [ ] **Step 3: 문서 고치기** — 위 Files
- [ ] **Step 4: 커밋 · PR** — 인사담당자 "올려줘" 뒤 push, PR 본문은 `.github/pull_request_template.md` 항목 + 그림, `@holee9` 보안 확인 요청(새 API · 권한 · 감사로그). merge는 "merge 해줘"로만, 합친 뒤 운영 반영과 "DB 준비 완료 (판 10)" 확인
