# 평가·성과 실제화 2단계 ① — 진행 기록 · 일정 변경 요청 · 나의 업무 연동 구현 계획

(2026-10-21 main #165 급여명세서가 판 11을 먼저 써서, 이 계획의 DB는 판 12 `012_kpi_progress.sql`로 합쳤다)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 확정된 KPI가 있는 직원이 다음을 서버에 적는다.
- 정기 KPI: 실적(또는 진행률)과 월 한 줄
- 비정기 KPI: 예정 기한마다 결과 한 줄
- 모든 KPI: 늦어지는 이유

예정 기한은 일정 변경 요청을 바로 위 관리자가 확인하면 바뀐다. 비정기 예정 기한은 워크스페이스 나의 업무에 "KPI- 일정 이름"으로 보이고, 홈 KPI 칸에는 종합 진행률이 나온다.

**Architecture:**
- **규칙과 엔드포인트:** 순수 규칙은 `hr_eval/domain/kpi_progress.py`(달 상태 · 진행률 · 기대 진행률 · 실제 예정 기한 · 일정 상태)에 둔다. 엔드포인트는 1단계 블루프린트 `backend/kpi.py`에 더한다.
- **일정 변경:** 확정본(`kpi_versions`)은 고치지 않는다. 확인된 일정 변경은 별도 표에 두고 보여 줄 때 덮어쓴다. 해제할 때 초안에 넣어 다음 판으로 이어 간다.
- **나의 업무:** KPI 일정은 워크스페이스 저장 목록에 **복사하지 않고** 화면에서 합친다. `wsTasks()`는 저장용으로 그대로 두고, 읽는 곳만 `wsAll()`을 쓴다.
- **계산 위치:** 진행률 · 기대 · 할 일 수 같은 숫자는 서버가 계산해 내려 준다. 2단계 ②의 관리자 화면도 같은 응답을 쓴다.

**Tech Stack:** Python 3 + Flask + SQLite(`backend/`), 단일 HTML · 바닐라 JS(`public/index.html`), 검사 `hr_eval.selfcheck` · `auth_check` · `hr_eval.sql.check_schema`, 화면 확인 `클로드 코드/devlive.py`(가짜 하이웍스) + playwright.

**Spec:** `docs/superpowers/specs/2026-10-20-kpi-progress-and-live-evaluation-design.md` 중 다음을 다룬다.
- 6.1 · 6.2 · 6.3(부진 알람 · 알람 끄기는 빼고)
- 6.5
- 7장
- 11장 3 · 5 · 6번 화면
- 홈 KPI 카드

**나눈 것 (인사담당자 결정 2026-10-21):**
- 2단계는 PR 둘로 올린다. 이 계획은 ①(직원 쪽)이다.
- ②(관리자 쪽 — 8장 KPI 현황 · 월간 점검 · 6.4 팀장 확인 · 확인 메모 · 부진 알람 · 알람 끄기 · HR 엑셀 · **경영계획 목표**)는 ①이 끝난 뒤 계획을 따로 쓴다. 맨 아래 "다음 계획" 참고.

## Global Constraints

**1단계에서 그대로 이어지는 것**
- **시연 모드는 그대로:** `AUTH.live`가 거짓이면 지금 화면(가상 자료 · 역할 전환 · 지금의 진행률 입력 · 데모 나의 업무)이 바뀌지 않는다.
- **판단은 서버가 다시 한다:** 기간 · 바로 위 관리자 · 상태 · 볼 수 있는 범위. 화면의 단추 막기는 안내다.
- **날짜는 한국 날짜:** `auth._today()` / `auth._now()`(서버 시계는 UTC).
- **공개 저장소:** 시험 자료 이름은 전부 가상이다. 휴대폰 번호 모양의 문자열을 쓰지 않는다.
- **줄 끝과 패치:** 작업 폴더 파일은 CRLF다. `index.html`의 여러 곳을 고칠 때는 `assert s.count(old)==1` 패치 스크립트를 쓴다.
- **화면 문구:** 쉬운 한국어("~해요")로 쓴다. 상태 글자 앞의 아이콘(✓ 등)은 **고정 폭 자리**를 늘 비워 둔다.

**2단계 ①에서 새로 정하는 것**
- **감사로그:** 일정 변경 요청 · 취소 · 확인 · 돌려보내기만 남긴다(행동 · 대상 id · 연도 · sid · 날짜). 진행 기록(실적 · 한 줄 · 결과 · 이유)은 남기지 않는다(내용 문장이라서 — 1단계 임시 저장과 같다). HR이 대신 하면 `on_behalf_of` = 원래 관리자.
- **DB:** 지금 판 10 → `011_kpi_progress.sql`을 더한다. 확정본은 고치지 않는다.
- **PC 전용과 예외:** 평가 · 성과는 PC 전용이다. 단 나의 업무의 KPI- 업무는 휴대폰 워크스페이스에도 보인다(설계 2장).
- **글자 한도(서버 · 화면 같게):**
  - 월 한 줄 · 결과 한 줄 · 일정 변경 사유 · 돌려보내기 이유: 200
  - 늦어지는 이유: 100
  - 링크: 300, `http://` 또는 `https://`로 시작하는 것만, 빈칸 없이
- **숫자:**
  - 실적: 0 이상, 유한수, 1e9 이하
  - 진행률 직접 입력: 0~100 정수
- **기록 열쇠:** 실행목표 = `rid`, 일정 = `sid`(1단계 `assign_ids` — 한 계획서 안에서 다시 쓰지 않는다). 기록은 **지금(마지막) 확정본**에 있는 rid · sid에만 쓴다.
- **기록 기간:** 진행 기록은 그 사람 · 그해 확정본이 있을 때만 쓴다(작성 기간 · 계획서 상태와 상관없다). 그해 기록은 **다음 해 1월 7일(KST)까지** 쓴다. 일정 변경은 **그해 평가 시작일부터** 막힌다.

## Review Focus

1. **월 한 줄 마감 — 한국 자정:**
   - M월 한 줄은 (M+1)월 7일 23:59(KST)까지 써지고, 8일 00:00(KST, UTC로는 7일 15:00)부터 막힌다.
   - 12월 한 줄은 다음 해 1월 7일까지 써진다.
   - → Task 2 `달_한_줄은_다음_달_7일까지`, Task 3 `월_한_줄_마감은_한국_자정`
2. **일정 변경과 다시 확정:**
   - 확인된 새 기한은 해제 → 다시 확정 뒤에도 남는다(해제할 때 초안에 새 기한이 들어간다).
   - 해제하면 대기 중인 요청은 취소된다.
   - → Task 4 `해제하면_바뀐_기한이_초안에_들어간다`
3. **사라진 일정:**
   - 다시 확정하며 지운 일정의 결과는 화면과 나의 업무에서 빠지고, 서버에는 남는다.
   - 그 sid로 결과를 쓰면 400이다.
   - → Task 3 `지금_확정본에_없는_일정은_못_쓴다`
4. **KPI 업무는 저장되지 않는다:**
   - 나의 업무에 합친 KPI 업무는 워크스페이스 서버 저장(`wsPush`)에 절대 들어가지 않는다.
   - 누르거나 끌거나 체크해도 바뀌지 않는다. 안내 후 목표설정으로 간다.
   - → Task 7 `KPI_업무는_저장되지_않는다`
5. **빈 일정 이름:** 일정 이름이 비어 있으면 나의 업무 제목은 `KPI- ` + 실행목표다. → Task 2 `빈_일정_이름은_실행목표로`

---

### Task 1: DB 판 11 — 진행 기록 · 결과 · 일정 변경

**Files:**
- Create: `backend/hr_eval/sql/011_kpi_progress.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` (`expected` 표 목록 + 새 절)

**Interfaces:**
- Produces (모두 `FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT`):
  - `kpi_progress(user_id, year, rid INTEGER NOT NULL CHECK (rid >= 1), actual REAL NULL CHECK (actual IS NULL OR actual >= 0), pct INTEGER NULL CHECK (pct BETWEEN 0 AND 100), why TEXT NULL CHECK (why IS NULL OR length(why) <= 100), updated_at TEXT NOT NULL, PRIMARY KEY (user_id, year, rid))`
  - `kpi_month_notes(user_id, year, rid, month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12), text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 200), link TEXT NULL CHECK (link IS NULL OR length(link) <= 300), updated_at TEXT NOT NULL, PRIMARY KEY (user_id, year, rid, month))`
  - `kpi_results(user_id, year, sid INTEGER NOT NULL CHECK (sid >= 1), text … 1~200, link … ≤300, updated_at, PRIMARY KEY (user_id, year, sid))`
  - `kpi_sched_changes(id INTEGER PRIMARY KEY, user_id, year, sid, from_due TEXT NOT NULL, to_due TEXT NOT NULL, reason TEXT NOT NULL CHECK (length 1~200), status TEXT NOT NULL CHECK (status IN ('PENDING','CONFIRMED','RETURNED','CANCELLED')), note TEXT NULL CHECK (note IS NULL OR length(note) <= 200), requested_at TEXT NOT NULL, decided_at TEXT NULL, decided_by INTEGER NULL REFERENCES users(id), on_behalf_of INTEGER NULL REFERENCES users(id), CHECK ((status = 'PENDING') = (decided_at IS NULL)), CHECK (status <> 'RETURNED' OR note IS NOT NULL), CHECK (date(from_due) IS from_due AND date(to_due) IS to_due))`
  - `CREATE UNIQUE INDEX kpi_sched_one_pending ON kpi_sched_changes(user_id, year, sid) WHERE status = 'PENDING'`
- 맨 위 주석은 010처럼 쉬운 한국어로 쓴다. 표마다 무엇을 담는지 적는다.

- [ ] **Step 1: 실패하는 검사 쓰기** — `check_schema.py`의 `expected`에 표 넷을 더하고, 절 `print("23. KPI 진행 기록 (011)")`을 넣는다. 다음이 거절되는지 본다.
  - `pct` 101
  - 빈 `text`
  - `month` 13
  - 같은 sid의 PENDING 둘
  - note 없는 RETURNED
  - PENDING인데 `decided_at` 있음
  - `from_due` '2027-02-30'
  - 계획서 없는 (user, year)
- [ ] **Step 2: 실패 확인** — `python -m hr_eval.sql.check_schema`
- [ ] **Step 3: 구현** — 위 Interfaces 그대로 쓴다.
- [ ] **Step 4: 통과 확인** — `검사 N건 전부 통과`. `python -m auth_check`도 그대로 통과해야 한다(DB 판 11 적용).
- [ ] **Step 5: 커밋** — `git commit -m "DB 판 11: KPI 진행 기록 · 결과 한 줄 · 일정 변경 요청"`

---

### Task 2: 진행 규칙 — `hr_eval/domain/kpi_progress.py`

**Files:**
- Create: `backend/hr_eval/domain/kpi_progress.py`
- Modify: `backend/hr_eval/selfcheck.py`

**Interfaces:**
- Produces (모두 순수 함수, `date`는 `datetime.date`):
  - 상수: `NOTE_MAX = 200` · `WHY_MAX = 100` · `LINK_MAX = 300` · `GRACE_DAY = 7` · `LATE_DAYS = 14`
  - `def month_open(year: int, month: int, today: date) -> bool` — `date(year, month, 1) <= today <= (다음 달 7일)`. 12월이면 다음 해 1월 7일까지다.
  - `def records_open(year: int, today: date) -> bool` — `today <= date(year + 1, 1, 7)`
  - `def month_state(year: int, month: int, today: date, first_month: int | None, hire: date | None, written: bool) -> str` — 아래 순서로 정한다.
    1. `first_month`가 None이거나 `month < first_month`이면 `"NA"`(해당 없음).
    2. 그 달 말일 < `hire`이면 `"NA"`.
    3. `written`이면 `"DONE"`.
    4. `month_open`이면 `"OPEN"`.
    5. 마감이 지났으면 `"MISSED"`.
    6. 그 밖은 `"FUTURE"`.
  - `def effective_dues(content: list[dict], changes: Sequence[tuple[int, str]]) -> dict[int, str]` — 확정본의 `{sid: due}`에 확인된 변경 `(sid, to_due)`(오래된 것부터)를 덮어쓴다.
  - `def sched_state(due: str, today: date, done: bool) -> str` — 아래 넷 중 하나다.
    - `"DONE"`
    - `"UPCOMING"`(today ≤ due)
    - `"LATE"`(due < today ≤ due + 14일, 주황)
    - `"OVERDUE"`(today > due + 14일, 빨강)
  - `def item_pct(item: dict, actual: float | None, pct: int | None, done_sids: Collection[int]) -> float` — 막대에 쓰는 0~100 값이다.
    - 정기이면서 목표가 있으면 `min(100, actual / target * 100)`(없으면 0).
    - 정기이면서 목표가 없으면 `pct or 0`.
    - 비정기는 결과 쓴 일정 수 ÷ 일정 수 × 100.
  - `def item_expected(item: dict, year: int, today: date, dues: dict[int, str]) -> float`
    - 정기는 `(today - 1월 1일 + 1일) / 그해 날수 × 100`. 그해 전이면 0, 그해 뒤면 100(화면 `kvExpect`와 같은 식).
    - 비정기는 `today > due + 14일`인 일정 수 ÷ 일정 수 × 100.
  - `def overall(items: Sequence[dict], values: Sequence[float]) -> float` — Σ(weight × value) / 100
  - `def task_title(sched_name: str, action: str) -> str` — `"KPI- " + (sched_name.strip() or action.strip())`
  - `def valid_link(s: str) -> bool` — 다음을 모두 만족해야 참이다: `http://` 또는 `https://`로 시작, 빈칸 · 제어문자 없음, 300자 이하.

- [ ] **Step 1: 실패하는 검사 쓰기** (`selfcheck.py`, `CHECKS`에 등록)
  ```python
  def 달_한_줄은_다음_달_7일까지():
      assert month_open(2027, 3, date(2027, 4, 7)) and not month_open(2027, 3, date(2027, 4, 8))
      assert month_open(2027, 12, date(2028, 1, 7)) and not month_open(2027, 3, date(2027, 2, 28))
  def 처음_확정_전_달과_입사_전_달은_해당_없음():
      assert month_state(2027, 1, date(2027, 3, 10), 2, None, False) == "NA"
      assert month_state(2027, 2, date(2027, 3, 10), 2, date(2027, 3, 2), False) == "NA"
      assert month_state(2027, 3, date(2027, 3, 10), 2, date(2027, 3, 2), False) == "OPEN"
      assert month_state(2027, 1, date(2027, 3, 10), 1, None, False) == "MISSED"
      assert month_state(2027, 2, date(2027, 3, 5), 1, None, True) == "DONE"
  def 실적은_목표로_나누고_100에서_자른다(): ...  # target 12: actual 10 → 83.33…, 15 → 100, None → 0; 목표 없음 pct 70 → 70
  def 비정기_진행률은_결과_쓴_일정_비율(): ...   # 일정 4개 중 sid 2개 결과 → 50
  def 비정기_기대는_기한_14일_뒤부터():          # due 2027-09-24
      it = {"kind": "irr", "scheds": [{"sid": 1, "due": "2027-09-24", "name": ""}]}
      assert item_expected(it, 2027, date(2027, 10, 8), {1: "2027-09-24"}) == 0
      assert item_expected(it, 2027, date(2027, 10, 9), {1: "2027-09-24"}) == 100
  def 정기_기대는_그해_날수_비율(): assert round(item_expected({"kind": "reg"}, 2026, date(2026, 10, 12), {})) == 78
  def 일정_상태는_주황_빨강():   # 9/24: 9/24 UPCOMING, 9/25 LATE, 10/8 LATE, 10/9 OVERDUE, 결과 있으면 DONE
  def 바뀐_기한이_원래_기한을_덮는다(): ...       # 확정본 sid1 9/24 + 변경 [(1, "2027-12-17")] → {1: "2027-12-17"}, 나중 변경이 이김
  def 빈_일정_이름은_실행목표로(): assert task_title("  ", "분기별 웨비나 개최") == "KPI- 분기별 웨비나 개최"
  def 링크는_http_https만(): ...                 # "https://a.b/c" 참, "javascript:alert(1)" · "http://a b" · 301자 거짓
  ```
- [ ] **Step 2: 실패 확인** — `python -m hr_eval.selfcheck`
- [ ] **Step 3: 구현** — 날짜 문자열은 `date.fromisoformat`로 읽는다. 그해 날수는 `date(year + 1, 1, 1) - date(year, 1, 1)`이다.
- [ ] **Step 4: 통과 확인** — `N/N 통과`
- [ ] **Step 5: 커밋** — `git commit -m "KPI 진행 규칙: 달 마감 · 진행률 · 기대 · 일정 상태 · 바뀐 기한"`

---

### Task 3: API — 진행 기록 읽기 · 쓰기

**Files:**
- Modify: `backend/kpi.py`, `backend/auth_check.py` (새 `check_kpi_progress(hr)`)

**Interfaces:**
- Consumes:
  - Task 1 표, Task 2 함수
  - 1단계의 `plan_year` · `load_org` · `_Refuse` · `_txn` · `_body` · `_text` · `decider`/`manager_of`
  - `users.hire_date`
- Produces:
  - **`def _dues(conn, uid: int, year: int) -> dict[int, str]`** — 실제 기한이다.
    - 지금 확정본의 `{sid: due}`에 확인된 일정 변경을 덮어쓴다. 덮어쓰는 것은 상태가 CONFIRMED이고 `decided_at >= 지금 판의 confirmed_at`인 변경이며, `decided_at` 오래된 것부터 `effective_dues`로 넣는다.
    - Task 4의 해제도 이것을 쓴다.
  - **`def progress_json(conn, uid: int, year: int) -> dict | None`**
    - 확정본이 없으면 None이다. 2단계 ②의 관리자 화면도 이것을 쓴다.
    - 응답 모양:
      ```
      {"year", "version", "open": records_open, "schedOpen": 평가 시작일 전,
       "items": [{"rid", "kind", "action", "weight", "target", "unit",
                  "actual", "pct", "why", "at", "value": item_pct, "expected": item_expected,
                  "months": {"1": {"state", "text", "link", "at"}, …}   ← 정기만, 1~12월
                  "scheds": [{"sid", "name", "task": task_title, "due": 실제 기한, "origDue": 확정본 기한,
                              "state", "result": {"text", "link", "at"} | null,
                              "pending": {"id", "to", "reason", "at"} | null,
                              "returned": {"to", "note", "at"} | null}]   ← 비정기만
                 }],
       "total", "totalExpected",
       "todo": {"months", "prev", "results"},
       "next": {"sid", "due", "name"} | null}
      ```
    - `first_month`는 그 rid가 처음 들어간 판의 `confirmed_at`(KST) 달이다.
    - `returned`는 가장 최근에 돌려받은 요청이고, 그 뒤에 새 요청이 있으면 null이다.
    - `todo`는 다음 셋이다.
      - `months` = 이번 달 + (1~7일이면) 지난달의 OPEN 정기 칸 수
      - `prev` = 그중 지난달 것
      - `results` = LATE · OVERDUE인데 결과가 없는 일정 수
    - `next` = 결과가 없고 실제 기한이 오늘 이후인 가장 가까운 일정
  - **`GET /api/kpi/progress/<uid>?year=`** (기본 `plan_year()`)
    - 주인 · 바로 위 관리자 · HR만 볼 수 있다(1단계 `plan_get`과 같은 범위이고, 2단계 ②에서 넓힌다). 그 밖은 403이다.
    - 확정본이 없으면 404 `{"error": "확정된 KPI가 없습니다."}`
  - **`POST /api/kpi/me/progress`** `{"year", "rid", "actual"?, "pct"?, "why"?}`
    - 보낸 칸만 바꾼다.
    - `actual`은 목표 있는 정기에만, `pct`는 목표 없는 정기에만 받는다. 다른 데 보내면 400이다.
    - `why`는 모든 행에 받는다. 빈 문자열이면 NULL로 둔다.
  - **`POST /api/kpi/me/month`** `{"year", "rid", "month", "text", "link"}`
    - 정기에만 쓴다. `month_open`이 아니면 403 `{"error": "이 달은 적는 기간이 아닙니다."}`
    - `text`가 ""이면 그 달 줄을 지운다.
  - **`POST /api/kpi/me/result`** `{"year", "sid", "text", "link"}`
    - 지금 확정본의 비정기 일정에만 쓴다.
    - `text`가 ""이면 지운다(나의 업무 상태가 다시 예정이 된다).
  - **쓰기 공통**
    - 거절 순서:
      1. KPI 작성이 꺼진 사람: 403
      2. 그해 확정본 없음: 404
      3. `records_open`이 아님: 403 `{"error": "기록 기간이 지났습니다."}`
      4. rid · sid가 지금 확정본에 없거나 종류가 안 맞음: 400
      5. 글자 · 링크 · 숫자 한도를 넘음: 400
    - 요청 하나는 `_txn` 한 트랜잭션이다. 감사로그는 없다.

- [ ] **Step 1: 실패하는 검사 쓰기** — 가상 팀원 `emp`와 팀장 `lead`를 쓴다. 1단계 검사처럼 제출 · 확정한 계획서(정기 목표 있음 1 · 정기 목표 없음 1 · 비정기 일정 3)를 만든 뒤 아래를 확인한다.
  - `확정_전에는_못_쓴다`(404)
  - `실적_쓰면_진행률이_바뀐다`: 목표 12에 실적 6 → `value` 50. `total`도 바뀐다.
  - `목표_없는_정기는_진행률을_직접`: pct 70은 200, 그 rid에 actual을 보내면 400
  - `월_한_줄_쓰고_지우기`: DONE → "" → OPEN
  - `지난_달은_못_쓴다` · `다음_달은_못_쓴다`(403)
  - `월_한_줄_마감은_한국_자정`: `auth._now`를 바꿔 가며 확인한다. (M+1)-07T14:59Z는 200, 15:00Z는 403(`finally`로 되돌린다).
  - `결과_쓰면_일정이_완료`: state DONE, `todo.results`가 준다. ""로 지우면 다시 예정이다.
  - `지금_확정본에_없는_일정은_못_쓴다`:
    1. 수정 요청 → 해제 → 일정 하나를 지우고 다시 확정한다.
    2. 옛 sid로 결과를 쓰면 400이다.
    3. 옛 결과는 DB에 남는다.
  - `링크는_http만`: `javascript:` → 400
  - `늦어지는_이유는_100자`(101자 → 400)
  - `남의_기록은_못_본다`: 다른 팀원 403, 팀장 · HR 200
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — `progress_json`은 표 넷과 확정본, 바뀐 기한을 한 번에 읽는다. 날짜 판정은 `auth._today()`로 한다.
- [ ] **Step 4: 통과 확인** — `로그인 검사 N건 전부 통과`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: 진행 기록 읽기 · 실적 · 월 한 줄 · 결과 한 줄 · 늦어지는 이유"`

---

### Task 4: API — 일정 변경 요청 · 확인 · 돌려보내기

**Files:**
- Modify: `backend/kpi.py`, `backend/auth_check.py` (새 `check_kpi_sched(hr)`)

**Interfaces:**
- Consumes: Task 3 `progress_json`, 1단계 `decider`(HR 대신 · 대표이사 맡김 규칙 그대로) · `_manage`의 확인 순서
- Produces:
  - **실제 기한:** Task 3의 `_dues(conn, uid, year)`를 쓴다. 확인되면 그 변경이 바로 덮어쓴다.
  - **`POST /api/kpi/me/sched-change`** `{"year", "sid", "due", "reason"}` → `{"ok", "id"}`
    - 조건:
      - 계획서 상태가 CONFIRMED 또는 CHANGE_REQUESTED다.
      - sid가 지금 확정본의 비정기 일정이다.
      - 그 일정에 결과가 아직 없다.
      - `due`는 그해 안이고 지금 실제 기한과 다르다.
      - 사유는 1~200자다.
      - 오늘이 평가 시작일 전이다.
    - 같은 sid에 대기 중인 요청이 있으면 409다.
    - 감사로그 `KPI_SCHED_REQUESTED {year, sid, from, to}`를 남긴다.
  - **`POST /api/kpi/me/sched-change/<id>/cancel`** — 본인의 PENDING을 CANCELLED로 바꾼다. 감사로그는 `KPI_SCHED_CANCELLED`다.
  - **`POST /api/kpi/sched-changes/<id>/confirm`** · **`/return`** `{"note"}`
    - 확인 순서:
      1. `decider(주인)`: 아니면 403
      2. 평가 시작일 전: 아니면 403 `{"error": "평가가 시작되어 일정을 바꿀 수 없습니다."}`
      3. PENDING: 아니면 409 `{"error", "status"}`
      4. note 1~200: 아니면 400
    - 작성 기간과는 상관없다.
    - 감사로그는 `KPI_SCHED_CONFIRMED` · `KPI_SCHED_RETURNED` `{year, sid, from, to}`이고, HR이 대신 하면 `on_behalf_of`를 남긴다.
  - **1단계 `unlock` 고침**
    - 초안 = 마지막 확정본 내용에 **실제 기한을 넣은 것**이다.
    - 그 계획서의 PENDING 요청은 모두 CANCELLED로 바꾸고, `decided_at` = 지금이다.
    - `KPI_UNLOCKED` 감사로그에 `cancelled` 수를 더한다.
  - **`GET /api/kpi/inbox` 고침**
    - 지금 줄에 `"kind": "PLAN"`을 더한다.
    - 일정 변경 줄을 더한다: `{"kind": "SCHED", "id", "userId", "name", "dept", "title", "action", "sched", "from", "to", "reason", "at", "proxy"}`. 누가 보는지는 PLAN 줄과 같다(관리자 · HR 대신 · 활성 KPI 작성자만).
    - 평가 시작일이 지난 요청은 넣지 않는다.

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `일정_변경_요청_확인되면_기한이_바뀐다`:
    - 팀장 처리함에 SCHED 1건이 뜬다.
    - 확인하면 `progress_json`의 due가 새 기한이고 origDue는 옛 기한이다.
    - 처리함이 비고, 감사로그 `KPI_SCHED_CONFIRMED`가 남는다.
  - `요청은_일정마다_하나`(409) · `결과_쓴_일정은_요청_못_한다`(400) · `같은_기한으로는_요청_못_한다`(400) · `그해_밖_기한은_못_한다`(400)
  - `돌려보내기는_이유_필수`: 이유 없으면 400. 이유가 있으면 RETURNED이고, 주인 화면 `returned.note`에 보인다.
  - `본인이_취소한다` · `남의_요청은_취소_못_한다`(403)
  - `관리자가_아니면_못_한다`(다른 팀원 403) · `HR이_대신_확인하면_원래_관리자가_남는다`
  - `평가_시작일부터_막힌다`: HR이 평가 시작일 = 오늘로 둔다. 그러면 요청 403 · 확인 403이고, 처리함에서 빠진다.
  - `해제하면_바뀐_기한이_초안에_들어간다`:
    1. 확인된 변경이 있는 상태에서 수정 요청 → 해제한다. `GET /api/kpi/me`의 draft 기한이 새 기한이다.
    2. 그 전에 걸어 둔 다른 PENDING은 CANCELLED가 된다.
    3. 다시 제출 · 확정하면 판 2 content의 기한이 새 기한이고, `progress_json`의 origDue도 새 기한이다.
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — 처리 하나는 `BEGIN IMMEDIATE` 한 트랜잭션이다(상태 읽기 → 쓰기 → 감사로그). `id` 경로 변환기는 1단계 `UID`처럼 큰 수를 404로 막는다.
- [ ] **Step 4: 통과 확인** — `python -m auth_check`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: 일정 변경 요청 · 확인 · 돌려보내기, 해제 때 바뀐 기한 이어 가기"`

---

### Task 5: 화면 — 직원 진행 관리 (시안 3)

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 3 · 4 API, 1단계 `KPI_LIVE` · `kpiLiveLoad` · `kpiLiveDo` · `kpiLiveKeep` · `kpiTable` · `kpiKindSection` · `orgModal` · `wsNotice` · `escapeHtml`
- Produces:
  - **`KPI_LIVE.prog`**
    - `kpiLiveLoad`가 내 확정본이 있을 때 `GET /api/kpi/progress/<내 id>`로 채운다. 없으면 null이다.
    - 화면이 다시 보일 때(`visibilitychange`)도 1분에 한 번까지 다시 읽는다. 나의 업무가 휴대폰 · PC 사이에서 맞도록 하기 위해서다.
  - **`function kpiProgSheet()`** — 시안 3을 그린다.
    - 띠: "작성 기간이 끝났습니다 (m/d ~ m/d) · 목표 · 예정 기한은 고칠 수 없어요 · 실적과 결과만 적어요". 작성 기간 안이면 "확정됨 · 고치려면 수정 요청". 오른쪽에 "KPI 계획서 보기 (읽기 전용)"가 있고, 누르면 확정본(`kpiTable` + `kpiKindSection(…, false)`)이 창으로 뜬다.
    - 요약 세 칸:
      - 종합 진행률 + 기대(막대에 세로선, 기대보다 낮으면 붉은색)
      - 지금 적을 것(`todo`)
      - 다가오는 예정 기한(`next` + 그다음 하나)
    - 정기 카드:
      - 실적: 목표가 있으면 "지금까지 [ ] / 목표 n 단위 → n% 자동", 없으면 진행률 [ ]%. 숫자 칸은 바뀌면 바로 저장한다.
      - 이번 달 한 줄: 200자 + 링크(선택), [저장], "(M+1)/7까지"
      - 1~7일에는 지난달 칸도 함께 보인다.
      - "지난 달 기록 n개" 접힘: 달마다 씀 / 안 씀 / 해당 없음
    - 비정기 카드:
      - 예정 기한 · 일정 · 결과 한 줄 표를 그린다.
      - 결과가 없으면 [결과 적기]가 있고, 누르면 그 줄에 200자 칸 + 링크 + [저장]이 열린다. 결과가 있으면 그 글이 보이고 고치기 · 지우기를 할 수 있다.
      - [일정 변경 요청](`schedOpen`이고 결과가 없을 때)이 있다.
      - 대기 중이면 "변경 요청 중 → 12/17 [취소]"가 보인다. 돌려받았으면 붉은 한 줄 "팀장이 돌려보냈어요: …"이 보인다.
      - LATE는 주황, OVERDUE는 빨강 기한 글자다.
      - 카드 아래에 "나의 업무에 KPI- 일정 n건 · 결과를 적으면 그 업무가 완료로 바뀝니다 · 워크스페이스에서 보기 ›"가 있다.
    - 늦어지는 이유: 실행목표마다 한 줄(100), 비어 있어도 된다.
    - 카드 머리: 실행목표 · 업무 종류 배지 · 팀목표 · 측정식 · 가중치 · 진행률 막대(기대 세로선) · 큰 %
    - "기대보다 n%p 늦음" 글은 쓴다. 종 · 알람 끄기는 2단계 ②에서 한다.
    - 맨 아래에 1단계 수정 요청 단추를 둔다(작성 기간 밖이면 꺼짐 + "작성 기간에만").
  - **상태별로 무엇을 보이나**
    - CONFIRMED · CHANGE_REQUESTED이면 `kpiProgSheet`를 보인다.
    - 확정본이 있는데 DRAFT · SUBMITTED(해제 뒤 다시 쓰는 중)이면 맨 위에 두 단추 [진행 관리] [계획서 (작성 중)]를 둔다. 작성 기간 안이면 계획서, 밖이면 진행 관리가 먼저다. 진행 기록은 마지막 확정본으로 계속 적는다(설계 5장).
    - 확정본이 없으면 1단계 화면 그대로다.
  - **저장 안 한 칸:** 한 줄 · 결과 · 이유 칸에 쳐 놓고 저장 안 한 글은 R21 방식(`dirty` 표시 + `kpiLiveKeep` + `beforeunload`)으로 다른 처리 · 탭 이동에도 지킨다.
  - **`kpiLiveSelfCheck()`에 더할 것**
    - 정기(목표 있음)는 실적 칸, 정기(목표 없음)는 진행률 칸이 있다.
    - 1~7일에는 지난달 칸이 하나 더 있다.
    - 비정기 LATE 줄은 주황, OVERDUE 줄은 빨강이다.
    - 결과가 있으면 [결과 적기]가 없다.
    - `schedOpen`이 거짓이면 [일정 변경 요청]이 없다.
    - 해제 뒤 두 단추가 보인다.

- [ ] **Step 1: 화면 검사 함수 확장** — 위 항목들을 넣는다. 가짜 `prog`를 넣어 그림만 본다.
- [ ] **Step 2: 실패 확인** — devlive 콘솔에서 `kpiLiveSelfCheck()`를 돌려 새 줄이 실패하는지 본다.
- [ ] **Step 3: 구현** — 시안 3 모양을 따른다. 기존 클래스와 변수를 다시 쓰고, 파랑 한 색으로만 칠하지 않는다(붉은 강조는 `--coral-text`).
- [ ] **Step 4: 확인** — devlive + playwright 1440px로 아래 순서를 끝까지 해 본다. `kpiLiveSelfCheck()`가 통과하고 콘솔 오류가 없어야 한다.
  1. 직원이 작성 → 제출 → 팀장이 확정한다.
  2. 직원 화면이 진행 관리로 바뀐다.
  3. 실적 · 이번 달 한 줄 · 결과를 쓴다.
  4. 새로 고침해도 남는다.
  5. 결과를 지운다.
  6. 수정 요청 → 해제 → 두 단추가 보인다.
  - 그림: `뉴디자인/KPI 실제 - 5 직원 진행 관리.png`(저장소 밖)
- [ ] **Step 5: 커밋** — `git commit -m "목표설정: 확정 뒤 진행 관리 — 실적 · 월 한 줄 · 결과 한 줄 · 늦어지는 이유"`

---

### Task 6: 화면 — 일정 변경 요청 창 · 팀장 처리함 · 처리할 일 숫자 · 홈 KPI

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 4 API, Task 5 `kpiProgSheet`, 1단계 `kpiLiveInbox` · `KPI_ACT` · `kpiLiveAsk` · `kpiLiveBack` · `homeKpiLive` · `TODO.e1` · `homePortalTasks`
- Produces:
  - **`function kpiSchedAsk(sid)`** — 시안 6 창
    - 보이는 것: KPI · 일정 · 지금 기한 · 바꿀 기한(`<input type="date">`, 그해 min/max) · 이유(200, 글자 수)
    - 안내 "○○ 팀장이 확인하면 나의 업무의 "KPI- …" 마감일도 바뀌고, 기대 진행률도 새 기한으로 다시 셉니다"
    - [요청 보내기] [취소]. 서버가 OK한 뒤에 닫는다.
  - **처리함 SCHED 줄** — 1단계 `KPI_ACT`에 `sched_confirm` · `sched_return`을 더하고, 같은 창 · 같은 보내는 함수를 쓴다.
    - 배지 "일정 변경"(붉은 계열)
    - "이름 직급 · 실행목표 › 일정 · 9/24 → 12/17", 요청 시각, 사유 상자
    - [돌려보내기(이유)] [확인]. HR이 대신 하면 "대신 처리" 배지를 단다.
  - **처리함 카드 머리**
    - 작성 기간 밖이어도 SCHED 줄이 있으면 카드를 보인다.
    - 머리 배지는 "작성 기간 밖 — 일정 변경만 받습니다"다(시안 4).
    - 아래 안내 "확인하면 예정 기한과 ○○ 님 나의 업무의 마감일이 바뀝니다"
  - **처리할 일 숫자(실제 로그인)**
    - `TODO.e1` = 처리함 줄 수(PLAN + SCHED) + `kpiLiveBack()` + 내 `todo.prev`(마감 전 지난달 한 줄)
    - 홈 오늘의 업무에 "KPI 지난달 한 줄 쓰기 n건"(→ 목표설정)을 더한다. 메뉴 숫자 = 홈 합계 규칙을 지킨다.
  - **`homeKpiLive()`** — 확정본이 있으면 다음을 보인다.
    - 종합 진행률 % + 기대 세로선
    - 실행목표마다 작은 막대(데모 `homePct` 모양)
    - "다가오는 예정 기한 m/d 일정 이름"
    - 확정본이 없으면 지금(상태 · 기간) 그대로다.
  - **`kpiLiveSelfCheck()`에 더할 것**
    - SCHED 줄 단추 이름
    - 대신 처리 표시
    - `TODO.e1()` = 처리함 + 돌려받음 + `todo.prev`
    - 홈 카드에 진행률이 보인다.

- [ ] **Step 1: 화면 검사 함수 확장**
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 확인** — devlive로 아래를 해 본다.
  1. 직원이 [일정 변경 요청]을 보낸다(지금 기한 · 이유).
  2. 팀장 로그인: 메뉴 숫자 1, 처리함 SCHED 줄
  3. 돌려보내기(이유) → 직원 화면에 붉은 한 줄
  4. 다시 요청 → 팀장 확인 → 직원 화면 기한이 바뀐다.
  5. HR 대신 확인을 해 본다.
  6. 홈 KPI 카드 진행률을 본다.
  - 그림: `뉴디자인/KPI 실제 - 6 일정 변경 · 처리함.png`
- [ ] **Step 5: 커밋** — `git commit -m "목표설정: 일정 변경 요청 창 · 팀장 처리함 일정 변경 · 처리할 일 숫자 · 홈 KPI 진행률"`

---

### Task 7: 화면 — 나의 업무에 KPI- 일정 합치기 (시안 5)

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 5 `KPI_LIVE.prog`. 워크스페이스 `wsTasks` · `wsSave` · `wsPush` · `wsRow` · `wsBoard` · `wsCalendar` · `homeTaskCard` · `wsEdit` · `wsToggle` · `wsSetState` · `wsDrop` · `wsDropDay` · `wsCarry`
- Produces:
  - **`function kpiWsTasks()`** — 실제 로그인이고 `KPI_LIVE.prog`가 있을 때, 비정기 일정마다 읽기 전용 업무 하나를 만든다.
    ```
    {id: -sid, title: s.task, due: s.due, time: '', pri: '보통',
     state: s.result ? '완료' : '예정', doneAt: s.result?.at.slice(0,10) || '',
     tags: ['KPI'], memo: '목표설정(KPI)의 예정 기한입니다. 결과는 KPI 화면에서 적어 주세요.',
     subs: [], repeat: '', kpi: true}
    ```
    시연 모드 · 확정본 없음이면 `[]`이다.
  - **`function wsAll()`** — `wsTasks().concat(kpiWsTasks())`. **읽기만 하는 곳**은 모두 이것으로 바꾼다: `homeDue` · `homeRate` · `homeTodayPop` · `homeTaskCard` · `homeDayMap` 기본값 · `homeCalendar`의 다가오는 목록 · `wsTodayCount` · `wsReport` · `wsBody` · `wsContent`. **쓰는 곳**(`wsSave` · `wsPush` · `wsDelete` · `wsSetState`의 filter · `wsCarry` · `wsSubmit` · `wsMerge`)은 `wsTasks()` 그대로다.
  - **막기**(`x.kpi` 또는 `id < 0`)
    - `wsEdit` · `wsToggle` · `wsSetState`는 아무것도 바꾸지 않고 안내만 한다. PC에서는 목표설정으로 간다(`goTo('perf','e1')`). 휴대폰에서는 `wsNotice('KPI 결과는 PC의 평가·성과 → 목표설정에서 적어 주세요')`를 띄운다.
    - `wsDrop` · `wsDropDay`는 KPI 업무를 무시한다.
    - 목록 · 보드 · 달력 · 홈 카드에서 KPI 줄은 체크칸 · 상태 고르기 · 끌기(`draggable`)를 그리지 않는다. 그 자리에 KPI 태그 + 자물쇠 글자를 둔다.
  - **함께 세기:** 기한 초과 · 이번 주 통계 · 진척도는 KPI 업무를 **센다**(설계 2장 "매일 보는 할 일 목록에 섞인다").
  - **`kpiLiveSelfCheck()` / 워크스페이스 검사에 더할 것**
    - `KPI_업무는_저장되지_않는다`: 가짜 prog를 넣으면 `wsAll()`에는 있고, `wsSave`가 보낼 목록에는 음수 id가 없다.
    - `wsEdit(-1)` · `wsToggle(-1)`이 오류 없이 안내만 한다.
    - 결과가 있는 일정은 완료다.
    - 빈 이름은 실행목표 제목이다.

- [ ] **Step 1: 검사 쓰기**
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 확인** — devlive로 확인한다. 시연 모드 나의 업무는 해시가 그대로여야 한다.
  1. 확정된 직원의 워크스페이스 목록 · 보드 · 달력에 "KPI- …"가 마감일 · KPI 태그와 함께 보인다.
  2. 홈 오늘의 업무 · 일정 달력에도 보인다.
  3. 결과를 쓰면 완료가 된다.
  4. 일정 변경이 확인되면 마감일이 바뀐다.
  5. 끌기 · 체크가 안 된다.
  6. 휴대폰 폭(390px)에서도 보이고, 누르면 안내가 뜬다.
  7. `POST /api/workspace/tasks` 요청 본문에 KPI 업무가 없다(네트워크 기록).
  - 그림: `뉴디자인/KPI 실제 - 7 나의 업무 KPI.png`
- [ ] **Step 5: 커밋** — `git commit -m "워크스페이스: 확정된 KPI 예정 기한을 나의 업무에 KPI- 로 (읽기 전용 · 저장 안 함)"`

---

### Task 8: 문서 · 전체 검사 · PR

**Files:**
- Modify:
  - `CLAUDE.md`: 평가·성과 "실제 로그인이면 목표설정은 서버 계획서" 절 아래에 진행 기록 · 일정 변경 · 나의 업무 연동 규칙, DB 판 11, 시안과 달라진 점, 2단계 ②에서 할 것
  - 설계 문서 상태 줄: "2단계 ① 구현 (PR 준비 중)"
  - `README.md` 메뉴 표(평가 · 성과, 워크스페이스 줄)

- [ ] **Step 1: 로컬 전체 검사** — `export PYTHONIOENCODING=utf-8; for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py; cd backend && python -m hr_eval.selfcheck && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features && python -m auth_check && python -m hr_eval.roster_sync --check` → 모두 통과해야 한다.
- [ ] **Step 2: 시연 모드 확인** — 역할 × 평가 · 성과 탭 × 화면과 홈 · 워크스페이스의 HTML이 이 계획 시작 커밋과 같아야 한다(1단계 Task 8 · 9처럼 해시로 비교). 데모 selfcheck도 모두 통과해야 한다.
- [ ] **Step 3: 운영 DB 올리기 시험** — 판 10 자료가 든 DB에서 `app`을 띄워 "DB 준비 완료 (판 11)"이 나오는지 본다. 기존 계획서 · 확정본이 그대로여야 한다(1단계 최종 검토의 probe 방식).
- [ ] **Step 4: 문서 고치기**
- [ ] **Step 5: 커밋 · PR**
  - 인사담당자가 "올려줘"라고 하면 push한다.
  - PR 본문에는 템플릿 항목과 그림을 넣고, `@holee9`에게 보안 확인을 요청한다(새 API · 링크 입력 · 나의 업무 합치기).
  - merge는 "merge 해줘"를 받은 뒤에만 한다.
  - 합친 뒤 운영 반영과 "DB 준비 완료 (판 11)"을 확인한다.

---

## 다음 계획 — 2단계 ② (이 계획이 끝난 뒤 따로 쓴다)

①이 만든 `progress_json`과 일정 변경 처리를 쓴다. 범위는 다음과 같다(설계 8장 · 6.4 · 6.3 알람, 옛 KPI 설계 7장, 인사담당자 2026-10-21 추가).

- **KPI 현황 탭 실제화:** 보는 범위는 팀장 = 팀원, 본부장 = 본부, 대표이사 = 본부장 · 팀장(팀원은 인원 · 평균 · 부진 수만), HR = 전체다. 서버는 `access.can(VIEW_KPI)` + DB에서 대상 만들기로 판단한다. `GET /api/kpi/progress/<uid>`의 보는 범위도 같이 넓힌다.
- **처리할 요청:** 일정 변경과 1단계 처리함을 KPI 현황 맨 위에도 보인다.
- **월간 점검 표와 "M월 확인":**
  - 확인은 바로 위 관리자 본인만 한다(HR 대신 없음).
  - 본인이 고치면 확인이 풀린다.
  - 본부장 · 직속 팀장은 확인 단계 없이 대표이사가 보기만 한다.
  - 새 표 `kpi_month_checks`가 생긴다.
- **확인 메모:** 쓰기 · 확인했습니다 · 답글 · 같은 날만 고치기(옛 7장 규칙)와 메뉴 숫자.
- **부진 알람 · 알람 끄기:** HR 기준(20%p) 설정, "갱신 필요"는 없앤다.
- **HR 엑셀:** "이번 달 기록 상태" 열을 더한다.
- **경영계획 목표:** HR이 입력하고, 대표이사는 목표설정 탭에서, 본부장은 자기 KPI 위에서 본다.
- **KPI 현황 "이 화면 사용법"과 사이트 둘러보기 단계.**
