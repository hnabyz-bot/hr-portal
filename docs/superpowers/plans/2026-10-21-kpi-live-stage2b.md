# 평가·성과 실제화 2단계 ② — KPI 현황 · 월간 점검 · 확인 메모 · 부진 알람 · 경영계획 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 실제 로그인의 KPI 현황 탭을 서버 자료로 바꾼다. 관리자는 맨 위에서 처리할 요청을 처리하고, 바로 아래 사람의 그 달 기록을 보고 "M월 확인"을 누르고, 확인 메모를 주고받는다. 부진 알람(HR 기준 · 본인이 끄기)이 서버에서 계산되고, HR은 엑셀로 내려받고, 경영계획 목표를 HR이 넣으면 대표이사 · 본부장이 본다.

**Architecture:**
- **규칙:** 순수 규칙은 새 파일 `hr_eval/domain/kpi_review.py`(그 달 요약 · 확인 상태 · 알람 · 알람 끄기 · 반올림 · 한국 날짜 · 경영계획 검사)와 `org.py`의 `target_of`에 둔다. 엔드포인트는 1단계 블루프린트 `backend/kpi.py`에 더한다.
- **보는 범위:** `_can_view` 한 곳을 넓힌다 — HR · 본인 · 바로 위 관리자 · `access.can(actor, Act.VIEW_KPI, target)`. 계획서 · 진행 기록 · 메모 읽기와 KPI 현황 명단이 모두 이것을 쓴다.
- **확인이 풀리는 방식:** 본인이 그 달 한 줄이나 그 달 기한의 결과를 쓰거나 지우면 서버가 그 달 확인 줄을 지운다(시각 비교가 아니라 지우기 — 지운 기록도 "고친 것"이라서).
- **KPI 현황 화면:** 인사담당자 결정(2026-10-21)대로 **모든 직책이 시안 4의 표 하나**를 쓴다. 팀장은 시안 4 그대로, 본부장 · 대표이사 · HR은 같은 표를 부서별로 묶는다. 사람을 누르면 표 아래에 그 사람의 그 달 기록 · 확인 메모가 나온다. 실제 로그인에서는 시연의 왼쪽 명단 · 오른쪽 상세(`kvList` · `kvDetail`)를 쓰지 않는다.
- **숫자는 서버가 센다:** 명단 한 번(`GET /api/kpi/board`)에 사람마다 종합 진행률 · 기대 · 부진 · 그 달 요약 · 확인 상태 · 메모 수가 온다. 사람을 펼치면 ①의 `progress_json` 그대로와 메모를 따로 읽는다.

**Tech Stack:** Python 3 + Flask + SQLite(`backend/`), 단일 HTML · 바닐라 JS(`public/index.html`), 검사 `hr_eval.selfcheck` · `auth_check` · `hr_eval.sql.check_schema`, 화면 확인 `클로드 코드/devlive.py`(가짜 하이웍스 — 가상인사 · 가상팀장 · 가상직원 · 가상본부장 · 가상대표 · 가상해외) + playwright.

**Spec:** `docs/superpowers/specs/2026-10-20-kpi-progress-and-live-evaluation-design.md` 중 다음을 다룬다.
- 6.3 부진 알람 · 알람 끄기
- 6.4 팀장 확인 표시
- 8장 KPI 현황 · 월간 점검 전부
- 11장 4번 화면(시안 `뉴디자인/KPI 진행 관리 시안 - 4 팀장 월간 점검.png`)
- 12장 (2026-10-21) 경영계획 목표

함께 보는 규칙: 옛 [KPI 설계](../specs/2026-09-21-kpi-goal-screen-design.md) 7장(누가 무엇을 보나 · 확인 메모 · 확인 · 답글 · 알람 끄기 · 경영계획 · HR 엑셀). ①에서 미룬 것 둘(관리자 "내용 보기"의 바뀐 기한 · CLAUDE.md HR 대신 범위 한 줄)도 이 계획에 넣는다.

**인사담당자 결정 (2026-10-21, 이 계획을 쓰며 받은 답):**
- KPI 현황은 **표 하나로 모두**(위 Architecture).
- 메뉴 숫자의 "확인할 사람"은 **이번 달 + 지난달**에 확인 안 한 기록이 있는 바로 아래 사람이다.
- **해 넘김 화면(1월 1~7일에 지난해 12월 기록)은 만들지 않는다** — "KPI는 해마다 2~3월에 쓰고 평가는 11~12월에 시작하니, 1월은 새해 KPI를 정하는 달이고 지난해 KPI는 평가로 마감된다." 그래서 KPI 현황 · 월간 확인 · 메모 · 알람 끄기는 **올해(`plan_year`) 것만** 다룬다.

## Global Constraints

**1단계 · ①에서 그대로 이어지는 것**
- **시연 모드는 그대로:** `AUTH.live`가 거짓이면 지금 화면(가상 자료 · 역할 전환 · `kv*` 명단 · 시연 메모 · 시연 경영계획)이 바뀌지 않는다. 시연 HTML 해시가 이 계획 시작 커밋과 같아야 한다.
- **판단은 서버가 다시 한다:** 바로 위 관리자 · 보는 범위 · 달 · 글자 한도. 화면의 단추 막기는 안내다.
- **날짜는 한국 날짜:** `auth._today()` / `auth._now()`(서버 시계는 UTC). 시각 글자는 `auth._iso` 모양 `YYYY-MM-DDTHH:MM:SSZ`(UTC)다.
- **공개 저장소:** 시험 자료 이름은 전부 가상이다. 휴대폰 번호 모양의 문자열을 쓰지 않는다. 커밋 · PR에 실명을 쓰지 않는다.
- **줄 끝과 패치:** 작업 폴더 파일은 CRLF다. `index.html`의 여러 곳을 고칠 때는 `assert s.count(old)==1` 패치 스크립트를 쓴다.
- **화면 문구:** 쉬운 한국어("~해요"). 상태 글자 앞의 아이콘(✓ 등)은 **고정 폭 자리**를 늘 비워 둔다(`kpiLiveSt`처럼 `<i>` 칸). 새 화면을 파랑 한 색으로만 칠하지 않는다(붉은 강조는 `--coral-text`).
- **글 규칙:** 사람이 쓴 글은 `kpi._chars`(제어문자 · 짝 없는 서로게이트 400)를 지난다. 앞뒤 공백을 뺀 길이로 센다.

**2단계 ②에서 새로 정하는 것**
- **DB:** 지금 판 12 → `013_kpi_review.sql`을 더한다. **다른 창의 원천징수영수증 브랜치(`feature/withholding-live`)가 `012_withholding.sql`을 쓰고 있다** — 그쪽이 먼저 합치면 그 번호가 013이 되므로, 우리 것은 main을 합치고 `014_kpi_review.sql`로 옮긴다(check_schema가 번호 빠짐 · 겹침을 막는다). **→ 10/22 원천징수영수증이 `013_withholding.sql`로 먼저 합쳐져, 우리 것은 `014_kpi_review.sql`(판 14)로 옮겼다.** 아래도 판 14로 맞춰 두었다.
- **누가 무엇을 하나:**
  - 남의 KPI 보기(계획서 · 진행 기록 · 메모 · 명단): `_can_view` = HR · 본인 · 바로 위 관리자 · `can(actor, Act.VIEW_KPI, target_of(그 사람))`. 팀장 = 팀원, 본부장 = 본부의 팀장 · 팀원, 대표이사 = 본부장 · 팀장(+ 대표이사가 바로 위 관리자인 사람), HR = 전체.
  - **대표이사는 팀원의 이름 · KPI를 응답으로 받지 않는다** — 명단에는 팀마다 인원 · 평균 · 부진 수만 온다.
  - 월간 확인 · 확인 메모 쓰기: **바로 위 관리자 본인만.** 바로 위 관리자가 대표이사인 사람(본부장 · 직속 팀장 등)은 확인 · 메모가 없다. HR 대신 없음, 대표이사 맡김과 상관없음.
  - 메모 고치기 · 지우기: 쓴 사람 본인, **쓴 날(한국 날짜)에만.** 답글: 본인과 그 메모를 쓴 사람. 답글 지우기: 단 사람, 단 날에만. "확인했습니다": 본인만.
  - 알람 끄기: 본인. 다시 켜기: 본인 · 바로 위 관리자.
  - 알람 기준 · 경영계획 넣기: HR. 경영계획 보기: 대표이사 · 본부장 · HR.
- **감사로그:** 월간 확인(`KPI_MONTH_CHECKED {year, month}`) · 알람 기준(`KPI_ALARM_SET {year, gap}`) · 경영계획(`KPI_BIZ_PLAN_SET {year, count}` — 목표 문장은 넣지 않음). 확인 메모 · 답글 · 알람 끄기는 남기지 않는다(내용 문장 · 본인 설정 — ①의 진행 기록과 같은 이유).
- **글자 한도(서버 · 화면 · DB 같게):** 확인 메모 · 답글 200, 알람 끄기 "직접 적기" 50, 경영계획 목표 60 · 내용 300 · 목표 20개까지.
- **반올림:** 부진 판정은 화면이 보여 주는 반올림한 숫자로 한다 — 서버 `js_round(x) = floor(x + 0.5)`(JS `Math.round`와 같음. 파이썬 `round`는 56.5 → 56이라 쓰지 않는다). 부진 = `js_round(기대) − js_round(진행률) ≥ 기준`.
- **알람 기준:** 해마다(`kpi_years.alarm_gap`, 기본 20, 1~100 정수). 기간 줄이 없는 해는 20.
- **"갱신 필요(30일)"는 실제 로그인에 없다**(설계 6.3). 시연은 그대로 둔다.
- **명단에 나오는 사람:** 활성 · KPI 작성 대상(`writes_kpi`)인 사람, 보는 사람 본인은 뺀다(본인 KPI는 목표설정 탭). 현장직(KPI 작성 꺼짐)은 나오지 않는다.

## Review Focus

1. **대표이사에게 팀원이 새지 않는다:**
   - 명단(`/api/kpi/board`) 응답에 팀원(직책 없음)의 `userId` · 이름 · KPI가 없고 팀마다 인원 · 평균 · 부진 수만 있다. 단, 대표이사가 바로 위 관리자인 사람('임원' 소속 등)은 줄로 나온다.
   - 대표이사가 팀원의 `/api/kpi/progress/<uid>` · `/api/kpi/plans/<uid>` · `/api/kpi/memos/<uid>`를 부르면 403이다.
   - → Task 3 `보는_범위가_넓어졌다`, Task 6 `대표이사는_팀원_줄을_못_받는다`
2. **확인 뒤 고치면 풀린다 — 결과의 달은 바뀐 기한으로 센다:**
   - 확인한 달의 한 줄을 고치거나 지우면 그 달 확인이 사라진다. 결과도 같다.
   - 일정 변경으로 기한이 5월 → 7월이 된 일정의 결과를 쓰면 7월 확인이 풀리고 5월 확인은 그대로다.
   - → Task 3 `고치면_확인이_풀린다`, `바뀐_기한의_달로_센다`
3. **반올림 경계:** 진행률 37.4 · 기대 56.5 · 기준 20이면 화면은 37 · 57(차이 20)을 보이므로 부진이어야 한다(반올림 없이 19.1, 파이썬 `round`로 56이면 둘 다 부진이 아니게 된다). → Task 2 `반올림은_화면과_같다` · `부진은_반올림한_숫자로`, Task 5 `부진은_반올림한_숫자로`
4. **한국 날짜 경계:**
   - UTC 2027-03-10T15:30Z(한국 3/11 00:30)에 쓴 메모는 한국 3/11에 고칠 수 있고 3/12에는 403이다.
   - 10월까지 끈 알람은 UTC 10/31 14:59(한국 23:59)에는 꺼져 있고 15:00(한국 11/1 0시)부터 다시 켜진다.
   - → Task 2 `한국_날짜로_같은_날`, Task 4 `쓴_날에만_고친다`, Task 5 `고른_달_말일까지`
5. **대신 하지 않는다:** HR은 계획서 확정은 대신하지만 월간 확인 · 메모 쓰기 · 다시 켜기는 403이다. 대표이사가 맡김을 켜도 같다. → Task 3 `HR은_월간_확인을_대신_못_한다`, Task 4 `대표이사_HR은_메모를_못_쓴다`, Task 5 `본인과_팀장이_다시_켠다`

---

### Task 1: DB 판 14 — 월간 확인 · 확인 메모 · 알람 끄기 · 알람 기준 · 경영계획

**Files:**
- Create: `backend/hr_eval/sql/014_kpi_review.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` (`expected` 표 목록 + 새 절 `26. KPI 현황 · 월간 확인 (014)`)

**Interfaces:**
- Produces:
  - `ALTER TABLE kpi_years ADD COLUMN alarm_gap INTEGER NOT NULL DEFAULT 20 CHECK (alarm_gap BETWEEN 1 AND 100)`
  - `ALTER TABLE kpi_years ADD COLUMN biz_plan TEXT NULL CHECK (biz_plan IS NULL OR (json_valid(biz_plan) AND json_type(biz_plan) = 'array'))` — `[{"goal", "detail"}]`
  - `kpi_month_checks(user_id, year, month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12), checked_at TEXT NOT NULL, checked_by INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT, PRIMARY KEY (user_id, year, month))`
  - `kpi_memos(id INTEGER PRIMARY KEY, user_id, year, author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT, text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 200), created_at TEXT NOT NULL, edited_at TEXT NULL, ack_at TEXT NULL, seen_at TEXT NULL, CHECK (author_id <> user_id))` + `CREATE INDEX kpi_memos_owner ON kpi_memos(user_id, year)`
    - `ack_at`: 본인이 "확인했습니다"를 누른 시각. `seen_at`: 쓴 사람이 본인 답글을 마지막으로 본 시각.
  - `kpi_memo_replies(id INTEGER PRIMARY KEY, memo_id INTEGER NOT NULL REFERENCES kpi_memos(id) ON DELETE CASCADE, author_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT, who TEXT NOT NULL CHECK (who IN ('owner','writer')), text TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 200), created_at TEXT NOT NULL)`
  - `kpi_mutes(user_id, year, rid INTEGER NOT NULL CHECK (rid >= 1), why TEXT NOT NULL CHECK (why IN ('year','month','wait','etc')), text TEXT NULL CHECK (text IS NULL OR length(text) BETWEEN 1 AND 50), until_month INTEGER NOT NULL CHECK (until_month BETWEEN 1 AND 12), set_at TEXT NOT NULL, PRIMARY KEY (user_id, year, rid), CHECK ((why = 'etc') = (text IS NOT NULL)), CHECK (why <> 'year' OR until_month = 12))` — `why` 글자는 화면 `KV_MUTE`의 열쇠와 같다.
  - `kpi_month_checks` · `kpi_memos` · `kpi_mutes`는 `FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT`(012와 같다).
- 맨 위 주석은 012처럼 쉬운 한국어로 쓴다. 표마다 무엇을 담는지, 감사로그에 무엇이 남고 무엇이 안 남는지 적는다.

- [ ] **Step 1: 실패하는 검사 쓰기** — `check_schema.py`의 `expected`에 표 넷을 더하고 절 `print("26. KPI 현황 · 월간 확인 (014)")`을 넣는다. 다음이 거절되는지 본다.
  - `alarm_gap` 0 · 101, `biz_plan` `'{}'`(배열 아님)
  - `month` 13, 같은 (사람, 해, 달) 확인 둘
  - 빈 메모 `text` · 201자, `author_id = user_id`
  - 답글 `who` 'boss'
  - `why` 'etc'인데 `text` NULL · 'wait'인데 `text` 있음 · 'year'인데 `until_month` 11
  - 계획서 없는 (user, year)의 확인 · 메모 · 알람 끄기
  - 메모를 지우면 답글도 지워진다(CASCADE)
  - 기간 줄을 새로 넣으면 `alarm_gap`이 20이다
- [ ] **Step 2: 실패 확인** — `cd backend && python -m hr_eval.sql.check_schema`
- [ ] **Step 3: 구현** — 위 Interfaces 그대로 쓴다.
- [ ] **Step 4: 통과 확인** — `검사 N건 전부 통과`. `python -m auth_check`도 그대로 통과해야 한다(DB 판 14 적용).
- [ ] **Step 5: 커밋** — `git commit -m "DB 판 14: KPI 월간 확인 · 확인 메모 · 알람 끄기 · 알람 기준 · 경영계획"`

---

### Task 2: 규칙 — `hr_eval/domain/kpi_review.py` · `org.target_of`

**Files:**
- Create: `backend/hr_eval/domain/kpi_review.py`
- Modify: `backend/hr_eval/domain/org.py`, `backend/hr_eval/selfcheck.py`

**Interfaces:**
- Produces (`kpi_review.py`, 모두 순수 함수. `prog`는 ①의 `progress_json` 응답 모양 그대로):
  - 상수: `GAP_DEFAULT = 20` · `MEMO_MAX = 200` · `MUTE_TEXT_MAX = 50` · `MUTE_WHY = ("year", "month", "wait", "etc")` · `BIZ_GOAL_MAX = 60` · `BIZ_DETAIL_MAX = 300` · `BIZ_MAX = 20`
  - `KST = timezone(timedelta(hours=9))` (도메인은 `auth`를 들여오지 않는다)
  - `def js_round(x: float) -> int` — `math.floor(x + 0.5)`
  - `def month_summary(prog: dict, month: int) -> dict` → `{"reg": [done, total], "irr": [done, total], "records": int, "noSched": bool}`
    - 정기: `months[str(month)].state`가 `"NA"`가 아닌 실행목표 수가 total, 그중 `"DONE"`이 done
    - 비정기: 실제 기한(`due`)이 `prog["year"]`년 `month`월인 일정 수가 total, 그중 `result`가 있는 것이 done
    - `records` = 두 done의 합, `noSched` = 비정기 실행목표가 있는데 그 달 일정이 0개
  - `def check_state(step: bool, summary: dict | None, checked: bool) -> str` — 아래 순서로 정한다.
    1. `step`이 거짓이면 `"NOSTEP"`(확인 단계 없음)
    2. `summary`가 None(확정본 없음)이거나 records · 두 total이 모두 0이면 `"NA"`(적을 것 없음)
    3. records가 0이면 `"NONE"`(안 씀)
    4. `checked`이면 `"DONE"`(확인함)
    5. 그 밖은 `"TODO"`(확인 전)
  - `def is_muted(year: int, until_month: int, today: date) -> bool` — `today.year == year and today.month <= until_month`
  - `def mute_until(why: object, month: object, today: date) -> int` — `why`가 `MUTE_WHY`가 아니면 `ValueError("이유를 하나 골라 주세요.")`. `"year"`이면 12. 그 밖은 `month`가 `today.month`~12 정수(`bool` 아님)여야 하고, 아니면 `ValueError("이번 달부터 12월 사이에서 골라 주세요.")`
  - `def alarm(items: Sequence[dict], values: Sequence[float], expects: Sequence[float], muted: Sequence[bool], gap: int) -> dict` → `{"slow": bool, "pct": float | None, "expected": float | None}`
    - 꺼지지 않은 실행목표만 가중 평균한다: `pct = Σw·v / Σw`, `expected = Σw·e / Σw`
    - 남는 실행목표가 없으면 `{"slow": False, "pct": None, "expected": None}`
    - `slow = js_round(expected) - js_round(pct) >= gap`
  - `def same_kst_day(iso_utc: str, today: date) -> bool` — `"%Y-%m-%dT%H:%M:%SZ"`를 UTC로 읽어 한국 날짜가 `today`와 같은지
  - `def clean_biz_plan(goals: object) -> list[dict]` — 목록이 아니면 `ValueError`. 줄마다 `{"goal", "detail"}` 글자(아니면 `ValueError`). 앞뒤 공백을 빼고, 둘 다 빈 줄은 버린다. 남은 줄이 `BIZ_MAX` 넘음 · 목표가 비었는데 내용 있음 · 목표 60자 넘음 · 내용 300자 넘음이면 `ValueError(쉬운 한국어 문구)`. 제어문자 검사는 엔드포인트가 `_chars`로 한다.
- Produces (`org.py`):
  - `def target_of(p: Person, depts: Sequence[Dept]) -> Target` — `access.Target`을 만든다. 부서를 못 찾으면 `Target(p.id, None, None, p.position)`.

    | 그 사람 | Target |
    |---|---|
    | 대표이사 | `(id, None, None, CEO)` |
    | 본부장 | `(id, None, led_dept(p), DH)` |
    | 팀장 · 팀원, 기준 부서(팀장은 `led_dept`, 그 밖은 소속)가 팀 | `(id, 그 팀, 팀의 본부, 직책)` |
    | 기준 부서가 직속 팀(`is_direct_team`) | `(id, 그 팀, None, 직책, direct=True)` |
    | 기준 부서가 `NO_COL` | `(id, None, None, 직책)` |
    | 기준 부서가 본부(맨 위, 직속 팀 · NO_COL 아님) | `(id, None, 그 본부, 직책)` |

- [ ] **Step 1: 실패하는 검사 쓰기** (`selfcheck.py`, `CHECKS`에 등록)
  ```python
  def 반올림은_화면과_같다():
      assert js_round(56.5) == 57 and js_round(57.49) == 57 and js_round(0.5) == 1
  def 그_달_요약():   # 정기 2개(3월 DONE · 3월 OPEN) + 정기 1개(3월 NA) + 비정기 일정 3/20(결과) · 3/28(없음) · 5/2
      s = month_summary(prog, 3)
      assert s == {"reg": [1, 2], "irr": [1, 2], "records": 2, "noSched": False}
      assert month_summary(prog, 4)["noSched"] and month_summary(prog, 4)["irr"] == [0, 0]
  def 확인_상태는_순서대로():
      assert check_state(False, s, True) == "NOSTEP" and check_state(True, None, False) == "NA"
      assert check_state(True, {"reg": [0, 0], "irr": [0, 0], "records": 0, "noSched": True}, False) == "NA"
      assert check_state(True, {"reg": [0, 2], "irr": [0, 0], "records": 0, "noSched": False}, True) == "NONE"
      assert check_state(True, s, True) == "DONE" and check_state(True, s, False) == "TODO"
  def 알람_끄기는_고른_달까지():
      assert is_muted(2027, 10, date(2027, 10, 31)) and not is_muted(2027, 10, date(2027, 11, 1)) and not is_muted(2027, 12, date(2028, 1, 2))
      assert mute_until("year", None, date(2027, 4, 2)) == 12 and mute_until("month", 4, date(2027, 4, 2)) == 4
      # mute_until("month", 3, 4/2) · ("etc", 13, …) · ("month", True, …) · ("later", 5, …) 는 ValueError
  ```
  - `끈_항목은_빼고_따진다` — 가중치 50 · 50, 기대 60 · 60, 기준 20:
    - 진행률 10 · 80, 둘 다 켜짐 → pct 45 · expected 60 · 차이 15 → `slow` 거짓
    - 첫째를 끔 → pct 80 → `slow` 거짓
    - 둘째를 끔 → pct 10 → `slow` 참
    - 둘 다 끔 → `{"slow": False, "pct": None, "expected": None}`
  - `부진은_반올림한_숫자로` — 한 줄, 진행률 37.4 · 기대 56.5 · 기준 20 → `slow` 참. 화면은 37 · 57(차이 20)을 보인다. 반올림 없이 비교하면(19.1) 거짓이고, 파이썬 `round`(56)로도 거짓이 되는 경계다.
  ```python
  def 한국_날짜로_같은_날():
      assert same_kst_day("2027-03-10T15:30:00Z", date(2027, 3, 11)) and not same_kst_day("2027-03-10T14:59:00Z", date(2027, 3, 11))
  def 경영계획_검사():   # 둘 다 빈 줄은 버림 · 앞뒤 공백 뺌 · 21개 · 목표 없이 내용 · 61자 · 301자 · 글자 아님은 ValueError
      assert clean_biz_plan([{"goal": " 매출 성장 ", "detail": ""}, {"goal": "", "detail": " "}]) == [{"goal": "매출 성장", "detail": ""}]
  def 보는_대상_모양():   # 본부 1 > 팀 11 · 직속 팀 9 · 임원 8, 팀장 21(팀 11) · 팀원 22(팀 11) · 본부 직속 23(본부 1) · 본부장 20 · 직속 팀원 42(팀 9) · 임원 소속 81
      assert target_of(P[21], D) == Target(21, 11, 1, Role.TEAM_LEADER) and target_of(P[22], D) == Target(22, 11, 1)
      assert target_of(P[23], D) == Target(23, None, 1) and target_of(P[20], D) == Target(20, None, 1, Role.DIVISION_HEAD)
      assert target_of(P[42], D) == Target(42, 9, None, direct=True) and target_of(P[81], D) == Target(81, None, None)
      assert can(Actor(21, Role.TEAM_LEADER, led_team_id=11), Act.VIEW_KPI, target_of(P[22], D))
      assert can(Actor(20, Role.DIVISION_HEAD, led_division_id=1), Act.VIEW_KPI, target_of(P[23], D))
      assert not can(Actor(6, Role.CEO), Act.VIEW_KPI, target_of(P[22], D)) and can(Actor(6, Role.CEO), Act.VIEW_KPI, target_of(P[21], D))
  ```
- [ ] **Step 2: 실패 확인** — `python -m hr_eval.selfcheck`
- [ ] **Step 3: 구현** — `org.py`는 `from hr_eval.domain.access import Target`을 들여온다(`access`는 `models`만 들여오므로 돌고 도는 import가 없다).
- [ ] **Step 4: 통과 확인** — `N/N 통과`
- [ ] **Step 5: 커밋** — `git commit -m "KPI 현황 규칙: 그 달 요약 · 확인 상태 · 알람과 알람 끄기 · 반올림 · 보는 대상"`

---

### Task 3: API — 보는 범위 넓히기 · 월간 확인 · 고치면 풀리기

**Files:**
- Modify: `backend/kpi.py`, `backend/auth_check.py` (새 `check_kpi_review(hr)` — Task 3~6이 같은 함수에 절을 더한다. 가상 조직은 이 검사만의 번호 71~79 부서 · 사람)

**Interfaces:**
- Consumes: Task 2 함수, ①의 `progress_json` · `_dues` · `_record` · `month_set` · `result_set`, 1단계 `load_org` · `manager_of` · `_Refuse` · `_txn` · `_body`
- Produces:
  - **`def _sees(uid: int, people: list[Person], depts: list[Dept]) -> bool`** — `g.actor`가 이 사람의 KPI를 볼 수 있나. HR · 본인 · `manager_of(uid) == 나` · `can(g.actor, Act.VIEW_KPI, target_of(그 사람))`. 명단에 없는 사람은 HR만 참.
    - `_can_view(conn, uid)`는 `_sees(uid, *load_org(conn))`가 된다(1단계 `plan_get` · ① `progress_get`이 자동으로 넓어진다).
  - **`def _step(uid: int, people, depts) -> bool`** — 확인 · 메모 단계가 있나 = 바로 위 관리자가 있고 대표이사가 아니다.
  - **`progress_json(conn, uid, year, org=None)`** — `org`는 `(people, depts)`. 없으면 읽는다(명단 표가 사람마다 다시 읽지 않게). 응답에 더하는 것:
    - `"checkStep": _step(…)`
    - `"checks": {"3": checked_at, …}` — 그 사람 · 그해 확인 줄 전부
  - **`def _uncheck(conn, uid: int, year: int, month: int) -> None`** — 그 달 확인 줄을 지운다.
    - `month_set`: 쓰기 · 지우기 뒤에 `_uncheck(그 달)`
    - `result_set`: 쓰기 · 지우기 뒤에 `_uncheck(int(_dues(conn, uid, year)[sid][5:7]))` — 실제 기한의 달
  - **`POST /api/kpi/plans/<uid>/check`** `{"year", "month"}` → `{"ok": true}`. 거절 순서:
    1. 바로 위 관리자가 아님: 403 `{"error": "바로 위 관리자만 확인할 수 있습니다."}` (HR 대신 없음)
    2. 내가 대표이사: 403 `{"error": "대표이사에게 보고하는 분은 월간 확인이 없습니다."}`
    3. `year`가 올해 `plan_year()` 정수가 아님 · `month`가 1~12 정수가 아님: 400
    4. 오늘(한국)보다 뒤의 달: 403 `{"error": "아직 오지 않은 달입니다."}`
    5. 그해 확정본 없음: 404 `{"error": "확정된 KPI가 없습니다."}`
    6. `month_summary(progress_json(…), month)["records"] == 0`: 409 `{"error": "확인할 기록이 없습니다."}`
    7. 이미 확인한 달: 200 `{"ok": true}` — 줄 · 감사로그를 더하지 않는다(두 번 눌러도 한 번)
    8. 줄을 넣고 감사로그 `KPI_MONTH_CHECKED {year, month}`(entity `kpi_plan`, 대상 = 주인)
    - 한 트랜잭션(`_txn`)이다. 작성 기간 · 평가 시작일과는 상관없다.

- [ ] **Step 1: 실패하는 검사 쓰기** — 가상 조직: 본부(71) > 팀(72) · 팀(73), 직속 팀(74), '임원'(75). 사람: 본부장 · 팀장(72) · 팀원 둘(72) · 다른 팀장(73) · 그 팀원(73) · 직속 팀장(74) · '임원' 소속 직원(75) · 대표이사 · HR. 팀원에게 정기 2 · 비정기(일정 3/20 · 5/20) 계획서를 확정해 두고, 시계는 ①처럼 `at()`으로 정한다.
  - `보는_범위가_넓어졌다`:
    - 본부장 → 팀원 · 다른 팀원의 progress · plans 200
    - 대표이사 → 팀장 · 본부장 · 직속 팀장 · '임원' 소속 200, 팀원 403
    - 다른 팀장 → 팀원 403, 동료 팀원 403
  - `팀장이_확인하면_본인_화면에_붙는다`: 3월 한 줄을 쓰고 팀장이 3월 확인 → 본인 progress `checks["3"]`, 감사로그 `KPI_MONTH_CHECKED` 하나 · 내용 문장 없음
  - `두_번_눌러도_한_번`: 같은 확인을 또 보내면 200, 줄 하나 · 감사로그 하나
  - `고치면_확인이_풀린다`:
    - 3월 한 줄을 고치면 `checks`에 3이 없다. 다시 확인 → 지우면 또 없다.
    - 3월 기한 일정의 결과를 쓰면 3월 확인이 풀리고, 5월 기한 결과를 써도 3월 확인은 그대로다.
    - 실적 · 늦어지는 이유를 고쳐도 확인은 그대로다.
  - `바뀐_기한의_달로_센다`: 5/20 일정을 7/10으로 일정 변경 확인 → 5월 · 7월 둘 다 확인해 둔다 → 그 일정 결과를 쓰면 7월만 풀린다
  - `HR은_월간_확인을_대신_못_한다`: HR · 대표이사가 맡김을 켠 뒤의 HR 모두 403(1번 문구)
  - `대표이사에게_보고하는_분은_확인이_없다`: 대표이사가 본부장 · 직속 팀장 확인 → 403(2번 문구), 본부장 progress `checkStep` 거짓
  - `확인_거절_순서`: 다른 팀장 403 · 13월 400 · 지난해 400 · 다음 달 403 · 기록 없는 달 409
  - `팀을_옮기면_새_관리자가_확인한다`: 팀원을 팀(73)으로 옮기면 그 팀장이 200, 옛 팀장은 403
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — `from hr_eval.domain.access import Act, can`, `from hr_eval.domain.org import target_of`. `_sees`는 명단을 이미 읽은 쪽(명단 표 · 메모)도 같이 쓴다.
- [ ] **Step 4: 통과 확인** — `로그인 검사 N건 전부 통과`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: 보는 범위 넓히기 · 월간 확인 · 고치면 확인 풀림"`

---

### Task 4: API — 확인 메모 · 확인했습니다 · 답글

**Files:**
- Modify: `backend/kpi.py`, `backend/auth_check.py` (`check_kpi_review`에 절 더하기)

**Interfaces:**
- Consumes: Task 3 `_sees` · `_step`, Task 2 `same_kst_day` · `MEMO_MAX`
- Produces (`MID = "<int(max=2147483647):mid>"`, `RPID = "<int(max=2147483647):rpid>"` — `CID`와 같은 이유. 실행목표 번호 `rid`와 헷갈리지 않게 답글은 `rpid`):
  - **`GET /api/kpi/memos/<uid>`** (올해) — `_sees`가 아니면 403. 응답:
    ```
    {"role": "owner" | "writer" | "",          ← writer = 지금 바로 위 관리자(대표이사 아님)
     "canWrite": bool,                          ← writer 이고 그해 확정본이 있음
     "writer": {"id", "name", "title"} | null,  ← 확인 단계가 없으면 null
     "memos": [{"id", "at", "authorId", "by", "text", "edited": bool, "ack": 시각 | null,
                "fresh": 쓴 사람이 아직 못 본 본인 답글 수 (보는 사람이 쓴 사람일 때만, 아니면 0),
                "canEdit": 쓴 사람 본인이고 쓴 날,
                "replies": [{"id", "at", "who", "by", "text", "canDelete"}]}]}   ← 메모는 새것부터, 답글은 오래된 것부터
    ```
    `by` = 이름 + " " + 직급(`users.title`, 없으면 이름만).
  - **`POST /api/kpi/memos/<uid>`** `{"year", "text"}` → `{"ok", "id"}`. 거절 순서: 지금 바로 위 관리자가 아니거나 그 관리자가 대표이사 403 `{"error": "바로 위 관리자만 메모를 남길 수 있습니다."}` → 올해가 아님 400 → 확정본 없음 404 → 글 1~200자 400.
  - **`POST /api/kpi/memo/<mid>/edit`** `{"text"}` · **`/delete`** — 메모 404 → 쓴 사람이 아님 403 → 쓴 날(한국)이 아님 403 `{"error": "쓴 날에만 고치거나 지울 수 있습니다."}` → (고치기) 글 400. 고치면 `edited_at` = 지금, **`ack_at` = NULL**(본인이 다시 확인). 지우면 답글도 지워진다.
  - **`POST /api/kpi/memo/<mid>/ack`** — 메모 404 → 본인이 아님 403 → 이미 확인했으면 200(바꾸지 않음) → `ack_at` = 지금. 취소는 없다.
  - **`POST /api/kpi/memo/<mid>/reply`** `{"text"}` → `{"ok", "id"}` — 메모 404 → 본인도 쓴 사람도 아님 403 → 글 400. 본인 답글이면 `ack_at`이 비었을 때 지금으로(답글 = 확인), 쓴 사람 답글이면 `ack_at` = NULL.
  - **`POST /api/kpi/memo-reply/<rpid>/delete`** — 답글 404 → 단 사람이 아님 403 → 단 날이 아님 403(위와 같은 문구).
  - **`POST /api/kpi/memos/<uid>/seen`** `{"year"}` — 내가 쓴 그 사람의 메모 모두 `seen_at` = 지금. 없으면 아무것도 안 하고 200.
  - 감사로그는 남기지 않는다. 요청 하나는 `_txn` 한 트랜잭션이다.

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `팀장만_쓴다`: 팀장 200 · 본부장(윗사람) 403 · 다른 팀장 403 · 본인 403
  - `대표이사_HR은_메모를_못_쓴다`: HR 403, 대표이사가 본부장에게 403, 본부장 GET의 `writer` null · `canWrite` 거짓
  - `윗사람_HR은_읽기만`: 본부장 · HR GET 200, `role` "" · `canWrite` 거짓 · `canEdit` 모두 거짓
  - `본인이_확인했습니다`: 본인 ack → `ack` 시각, 또 ack → 같은 시각. 팀장 ack 403
  - `답글_규칙`: 본인 답글 → 확인됨, 팀장 답글 → `ack` null, 다른 팀원 답글 403
  - `쓴_날에만_고친다`: UTC 3/10 15:30(한국 3/11)에 쓴 메모 → 한국 3/11 23:59(UTC 14:59)에 고치기 200 · `ack` null · `edited` 참 → 한국 3/12 0시 403. 답글 지우기도 같다
  - `새_답글은_열어_보면_사라진다`: 본인 답글 2개 → 팀장 GET `fresh` 2 → seen → 0. 본인 GET에서는 늘 0
  - `메모를_지우면_답글도`: 같은 날 지우면 두 표 모두 빈다
  - `감사로그_없음`: 메모 일을 해도 `audit_log` 줄 수가 그대로
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현** — 글은 `_line`이 아니라 1자 이상이어야 하므로 `_text`처럼 쓰되 한도는 `MEMO_MAX`(같은 200이지만 상수를 쓴다).
- [ ] **Step 4: 통과 확인** — `python -m auth_check`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: 확인 메모 · 확인했습니다 · 답글 · 새 답글 표시"`

---

### Task 5: API — 알람 끄기 · 부진 · 알람 기준 · 경영계획

**Files:**
- Modify: `backend/kpi.py`, `backend/auth_check.py` (`check_kpi_review`에 절 더하기)

**Interfaces:**
- Consumes: Task 2 `alarm` · `is_muted` · `mute_until` · `clean_biz_plan`, Task 3 `progress_json(…, org)`, ① `_record` · `_item` · `_line`
- Produces:
  - **`def _gap(conn, year: int) -> int`** — `kpi_years.alarm_gap`, 줄이 없으면 `GAP_DEFAULT`.
  - **`progress_json`에 더하는 것:**
    - 실행목표마다 `"mute": {"why", "text", "until", "at"} | null` — 지금 꺼져 있는 것만(`is_muted`). 기한이 지난 줄은 null로 보인다(지우지 않는다).
    - `"gap": _gap(…)`, `"alarm": alarm(rows, values, expects, [mute 있음 …], gap)`
  - **`POST /api/kpi/me/mute`** `{"year", "rid", "why", "month"?, "text"?}` — `_record()`의 순서(작성 대상 403 → 해 400 → 확정본 404 → 기록 기간 403) 뒤에: 올해가 아님 400 `{"error": "올해 KPI만 알람을 끌 수 있습니다."}` → `_item` 400 → `mute_until` 400(그 문구) → `why`가 `"etc"`면 `text` 1~50자(`_line`) 400, 아니면 `text`는 버린다 → 넣거나 바꾼다.
  - **`POST /api/kpi/plans/<uid>/unmute`** `{"year", "rid"}` — 본인도 바로 위 관리자도 아님 403 `{"error": "본인과 바로 위 관리자만 다시 켤 수 있습니다."}`(HR 대신 없음) → 해 400 → 줄을 지운다(없어도 200).
  - **`POST /api/kpi/admin/alarm`** `{"year", "gap"}` — HR만(`auth._hr_only`). `year` 2026~2100 정수 · `gap` 1~100 정수(`bool` 아님)가 아니면 400. 그해 줄이 없으면 날짜 없이 만든다(`updated_at` · `updated_by`). 감사로그 `KPI_ALARM_SET {year, gap}`(entity `kpi_year`).
  - **`GET /api/kpi/admin`** 응답에 `"alarmGap"`을 더한다.
  - **`GET /api/kpi/biz-plan?year=`** — 대표이사 · 본부장 · HR만(아니면 403 `{"error": "경영계획 목표는 대표이사 · 본부장 · HR만 봅니다."}`). → `{"year", "goals": [{"goal", "detail"}]}`(없으면 빈 목록).
  - **`POST /api/kpi/admin/biz-plan`** `{"year", "goals"}` — HR만. `clean_biz_plan` + 모든 글 `_chars` → 400 `{"error": 문구}`. 그해 줄이 없으면 만든다. 감사로그 `KPI_BIZ_PLAN_SET {year, count}`.

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `알람_끄기는_본인이`: 본인 200 → progress `mute`. 팀장이 같은 본문을 보내면 팀장 자기 계획서에 쓰는 것이라(본문에 사람 번호가 없다) 확정본이 없어 404 — 남의 알람을 끄는 길은 없다
  - `알람_끄기_검사`: 지난달 400 · 13월 400 · 이유 없음 400 · `etc` 글 없음 400 · `etc` 51자 400 · 지금 확정본에 없는 rid 400 · 작성 대상 아님 403
  - `고른_달_말일까지`: 10월까지 끔 → UTC 10/31 14:59 progress `mute` 있음 → 15:00 null(줄은 DB에 남음)
  - `끈_항목은_빼고_따진다`: 기대보다 많이 늦은 실행목표 하나를 끄면 `alarm.slow`가 참 → 거짓, 다 끄면 `pct` null
  - `부진은_반올림한_숫자로`: 기준을 바꿔 가며 경계(차이가 정확히 기준)에서 참인지 본다
  - `본인과_팀장이_다시_켠다`: 팀장 200 · 본인 200 · HR 403 · 본부장(윗사람) 403
  - `알람_기준은_HR이`: 팀장 403 · 0 · 101 · "20" · true 400 · HR 30 → progress `gap` 30, `/api/kpi/admin`의 `alarmGap` 30, 감사로그 `KPI_ALARM_SET`. 기간 줄이 없던 해에도 된다. 그 뒤 `POST /api/kpi/admin/year`로 기간을 바꿔도 30이 남는다
  - `경영계획은_대표이사_본부장_HR만`: 팀장 · 팀원 GET 403, 대표이사 · 본부장 · HR 200, HR 아닌 POST 403
  - `경영계획_검사`: 21개 · 목표 없이 내용 · 61자 · 제어문자 400, 둘 다 빈 줄은 버리고 저장, 감사로그 `count`만 · 목표 문장 없음
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m auth_check`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: 알람 끄기 · 부진 계산 · 알람 기준 · 경영계획 목표"`

---

### Task 6: API — KPI 현황 명단 · 처리할 일 숫자

**Files:**
- Modify: `backend/kpi.py`, `backend/auth_check.py` (`check_kpi_review`에 절 더하기)

**Interfaces:**
- Consumes: Task 3~5 전부
- Produces:
  - **`GET /api/kpi/board?month=`** (해 = `plan_year()`, `month` 기본 = 오늘의 달, 1~오늘의 달 정수가 아니면 400)
    - 쓰는 사람: 팀장 · 본부장 · 대표이사 · HR. 아니면 403 `{"error": "KPI 현황을 볼 수 있는 분이 아닙니다."}`
    - 응답:
      ```
      {"year", "month", "gap",
       "depts": [{"id", "name", "parentId"}],               ← 묶음 이름용 (부서 이름은 조직도에서 누구나 본다)
       "people": [{"userId", "name", "title", "deptId", "position": "DIVISION_HEAD" | "TEAM_LEADER" | null,
                   "status": "NONE" | "DRAFT" | "SUBMITTED" | "CONFIRMED" | "CHANGE_REQUESTED",
                   "direct": 내가 바로 위 관리자, "checkStep": _step,
                   "total", "totalExpected", "alarm",          ← 확정본이 없으면 null
                   "items": [{"rid", "action", "kind", "weight", "value", "expected", "why", "mute"}],   ← 확정본이 없으면 []
                   "month": {"reg", "irr", "records", "noSched", "check", "checkedAt"} | null,
                   "memo": {"count", "open": 본인이 확인 안 한 수, "fresh": 내가 쓴 메모의 못 본 본인 답글 수}}],
       "teams": [{"deptId", "count", "confirmed", "avg", "slow"}],   ← 대표이사만: 줄로 못 받는 팀원을 부서마다 묶은 것
       "todo": {"check": n}}                                         ← 아래 "확인할 사람"
      ```
    - `deptId` = 팀장 · 본부장은 맡은 부서(`led_dept`), 그 밖은 소속 부서.
    - 사람 = 활성 · KPI 작성 대상 · 본인 아님 · `_sees`가 참. 순서 = 부서 번호 → 본부장 · 팀장 · 그 밖 → 이름.
    - `month.check` = `check_state(checkStep, month_summary(prog, month), 그 달 확인 줄 있음)`.
    - `teams`의 `avg` = 확정본이 있는 사람 `total`의 평균(없으면 null), `slow` = `alarm.slow` 수.
    - 사람마다 `progress_json(conn, uid, year, org)`을 한 트랜잭션 안에서 부른다. `# ponytail: 사람마다 따로 읽음 — 100명에 수백 번, 느려지면 표마다 한 번에 읽기로`.
  - **`def _review(conn, people, depts) -> dict`** → `{"check", "replies", "memos"}` — 명단 표와 처리함이 같이 쓴다.
    - `check` = 내가 바로 위 관리자이고 `checkStep`인 사람 중, **이번 달 또는 지난달**(올해 안)의 확인 상태가 `"TODO"`인 사람 수 (인사담당자 결정 2026-10-21)
    - `replies` = 내가 쓴 메모에 못 본 본인 답글이 있는 사람 수
    - `memos` = 내 올해 메모 중 `ack_at`이 빈 것의 수
  - **`GET /api/kpi/inbox`** 응답에 `"review": _review(…)`를 더한다(로그인할 때 불러 메뉴 · 홈 숫자에 쓴다).

- [ ] **Step 1: 실패하는 검사 쓰기**
  - `팀장은_팀원만`: 팀장 명단 = 그 팀 팀원 둘(`direct` 참), `teams` 없음(빈 목록)
  - `본부장은_본부_전체`: 본부의 팀장 둘 + 팀원들, 팀장 줄만 `direct`
  - `대표이사는_팀원_줄을_못_받는다`: 대표이사 명단에 본부장 · 팀장 · 직속 팀장 · '임원' 소속만 있고 팀원 `userId` · 이름이 응답 어디에도 없다(응답 JSON 글자 검색). `teams`에 팀(72)이 인원 2 · 확정 1 · 평균 · 부진 수로 있다. 본부장 · 직속 팀장 줄은 `month.check` `"NOSTEP"`
  - `HR은_전체`: 비활성 · KPI 작성 꺼짐 · HR 본인은 없다
  - `직원은_못_본다`: 팀원 403
  - `이번_달_적을_것`: 3월 — 정기 `[1, 2]` · 비정기 `[0, 1]` · `noSched` 거짓, 4월 — `noSched` 참
  - `확인_상태가_바뀐다`: `NONE` → 한 줄 쓰기 `TODO` → 확인 `DONE` → 고치기 `TODO`. 확정 전인 사람은 `month` null · `items` []
  - `처리할_일_숫자`: 3월에 팀원 둘이 지난달(2월) · 이번 달(3월)에 확인 안 한 기록 → 팀장 inbox `review.check` 2. 1월 기록은 세지 않는다. 메모에 본인 답글 → `replies` 1, 본인 inbox `memos` 1 → ack 뒤 0
  - `달_검사`: `month=13` · `month=0` · 다음 달 · `month=a` 400
- [ ] **Step 2: 실패 확인** — `python -m auth_check`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m auth_check`
- [ ] **Step 5: 커밋** — `git commit -m "KPI API: KPI 현황 명단(직책별 범위 · 그 달 요약 · 확인 상태) · 처리할 일 숫자"`

---

### Task 7: 화면 — KPI 현황 표 · 펼친 기록 · M월 확인 · HR 엑셀 (시안 4)

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 3 · 6 API, 1단계 `KPI_LIVE` · `kpiLiveLoad` · `kpiLiveInbox` · `kpiLivePost` · `kpiLiveView` · `kpiLiveSt` · `KPI_LIVE_ST` · `orgModal` · `wsNotice` · `csvText` · `escapeHtml`, ① `kpiProgBar` · `kpiMd`
- Produces:
  - **`const KV_LIVE = { state:'', board:null, month:0, sel:null, prog:null, memos:null, open:{}, q:'', onlySlow:false, memoUi:{ reply:null, edit:null } }`** — `month` 0 = 이번 달.
  - **`kvCanUse()`** — 실제 로그인이면 `['TEAM_LEADER','DIVISION_HEAD','CEO'].includes(AUTH.me.position) || AUTH.me.is_hr`, 아니면 지금 그대로.
  - **`kvContent()`** — 맨 앞에 `if(AUTH.live) return kvLiveContent();` (시연 줄은 그대로).
  - **`async function kvLiveLoad(month)`** — `GET /api/kpi/board?month=`(0이면 안 붙임). 처리함이 아직이면 `kpiLiveLoad()`도 부른다. 고른 사람이 명단에 없으면 `sel`을 비운다. 탭에 들어올 때마다 새로(`kvLiveFresh` — `kpiLiveFresh`와 같은 자리에서 부른다).
  - **`function kvLiveContent()`** — 차례대로:
    1. 처리할 요청 = `kpiLiveInbox(KPI_LIVE.inbox.filter(i=>!i.proxy))`(있을 때만, 1단계 · ① 그대로) — `id="kvlInbox"`로 감싼다
    2. 월간 점검 카드 `kvLiveTable()` — `id="kvlTable"`
    3. 펼친 사람 `kvLivePerson()` — `id="kvlPerson"`
    4. 맨 아래 한 줄 "진행률은 본인이 적은 실적 · 결과로 계산하며 평가 점수와는 별개예요."
    - "시연용 역할 전환" · "시연용 가상 자료" 안내를 그리지 않는다.
  - **`function kvLiveTable()`** — 시안 4 모양:
    - 제목 "{M}월 월간 점검 · {팀장: 팀 이름 / 본부장: 본부 이름 / 대표이사: 본부장 · 팀장 / HR: 전 직원}" + 달 넘기기 `‹ {M-1}월` · `{M+1}월 ›`(`kvLiveMonth(±1)`, 1월 ~ 이번 달 사이만, `id="kvlMonth"`)
    - 오른쪽 배지 "확인할 사람 n명 · 안 쓴 사람 n명" — 그 달에 `direct`이면서 `TODO` · `NONE`인 수(0이면 그 말은 뺀다, 둘 다 0이면 배지 없음)
    - HR만 도구 줄(`id="kvlTools"`): 이름 검색(`kvRenderList`처럼 표만 다시 그려 한글 조합을 지킨다) · [부진만 보기] · [엑셀 내려받기]
    - 열: 이름(+ 직급, 종 = `alarm.slow`, 말풍선 = 메모 수 · `fresh`면 붉게) · 종합 진행률(`kpiProgBar(total, totalExpected, alarm.slow)` + `n%` + `기대 n%`) · {M}월에 적을 것(`kvMonthText`) · 확인(`kvCheckText`) · [펼침]/[보기]
      - 확인 열 머리: 보는 사람이 팀장이면 "팀장 확인", 아니면 "확인". **모든 줄이 `NOSTEP`이면 열을 그리지 않는다**(대표이사).
      - 확정본이 없는 사람: 진행률 자리에 상태 배지(`KPI_LIVE_ST`), 나머지 "—", 단추 없음
      - 단추: `direct`이고 `TODO`면 [펼침](강조), 그 밖은 [보기]. 고른 줄은 강조.
    - **묶음:** 사람을 `deptId`로 묶는다. 묶음이 하나(팀장)면 머리 줄을 그리지 않는다. 머리 줄 = "▾ {본부 › }부서 이름 · {장 이름} {팀장|본부장} {장의 total}% · 부진 n명", 눌러 접고 편다(`KV_LIVE.open`). 처음에는 HR만 접어 두고, 검색 · 부진만 보기 중에는 모두 편다. 묶음 순서 = `depts` 순서(본부 다음에 그 본부의 팀).
    - 대표이사: 각 팀 묶음 끝에 `teams` 한 줄 "팀원 n명 · 평균 n% · 부진 n명 — 팀원 개인 KPI는 팀장 · 본부장이 챙겨요"(누를 수 없음). 팀장이 없어 그 부서 묶음이 없으면 이 한 줄만으로 묶음을 만든다.
  - **`function kvMonthText(m)`** — `정기 d/t`(정기 total > 0) · `비정기 결과 d/t`(비정기 total > 0) · `이번 달 일정 없음`(`noSched`)을 " · "로 잇는다. 아무것도 없으면 "적을 것 없음". `m`이 null이면 "—".
  - **`function kvCheckText(st, at)`** — `DONE` "✓ 확인함"(초록) · `TODO` "확인 전"(붉은 계열) · `NONE` "안 씀"(빨강) · `NA` · `NOSTEP` "—". 앞의 아이콘 자리는 늘 같은 폭이다.
  - **`async function kvLivePick(uid)`** — 같은 사람을 또 누르면 닫는다. `GET /api/kpi/progress/<uid>`와 `GET /api/kpi/memos/<uid>`를 함께 읽어 `KV_LIVE.prog` · `memos`에 넣는다. 내가 쓴 사람이고 `fresh`가 있으면 `POST /api/kpi/memos/<uid>/seen` 뒤 명단을 다시 읽는다. 그린 뒤 펼친 카드가 보이게 스크롤한다.
  - **`function kvLivePerson()`** — 시안 4 아래 카드:
    - 제목 "{이름} · {M}월 기록" · 오른쪽 [계획서 보기](`kpiLiveView(uid)`) · [확인 메모 쓰기](`memos.canWrite`일 때, 메모 칸으로 포커스) · [{M}월 확인](`id="kvlCheck"`, `direct && checkStep && check==='TODO'`일 때만) — 이미 확인했으면 단추 대신 "✓ {M}월 확인함 m/d"
    - 실행목표마다 한 줄(시안 4):
      - 왼쪽: 실행목표 · "정기적 · {actual} / {target}{unit} · n%" 또는 "비정기적 · {결과 쓴 수} / {일정 수}회 · n%". `Math.round(expected) − Math.round(value) ≥ gap`이고 꺼지지 않았으면 붉은 "기대보다 n%p 늦음"(서버 부진 판정과 같은 반올림)
      - 가운데: 정기 = 그 달 한 줄(+ 링크 `kpiProgLink`) · 없으면 상태(`MISSED` "안 씀" 빨강 · `OPEN` "아직 안 씀 · {M+1}/7까지" · `NA` "해당 없음"). 비정기 = 그 달 기한 일정마다 "m/d 일정 — 결과" · 결과 없이 `LATE`/`OVERDUE`면 "m/d 일정 — 결과 안 씀 (기한 지남)"(주황 · 빨강) · `UPCOMING`이면 "m/d 일정 예정". 그 달 일정이 없으면 "{M}월은 적을 것 없음 · 다음 기한 m/d 일정". 그 아래 "늦어지는 이유: …"(있을 때) · 알람을 껐으면 "본인이 알람 끔 · 이유 (n월 말까지)" + `direct`면 [다시 켜기](Task 8)
      - 오른쪽: 그 달 한 줄 · 결과를 쓴 날 "m/d 작성" 또는 "—"
    - 아래 안내(시안 4 문구): "비정기 KPI는 예정 기한이 지난 달에만 적을 것이 생겨요. "{M}월 확인"을 누르면 {이름} 님 화면에 ✓ 팀장 확인이 붙고, 하고 싶은 말은 확인 메모로 남겨요." — `direct`가 아니면 "읽기만 해요 · 확인과 메모는 바로 위 관리자가 해요"
    - 맨 아래 `${kvLiveMemos()}` — 이 작업에서는 `function kvLiveMemos(){ return ''; }`(Task 8이 채운다)
  - **`async function kvLiveCheck(uid)`** — `kpiLivePost('/api/kpi/plans/'+uid+'/check', {year, month})` → 200이면 알림 "{M}월 확인했어요." + 명단 · 펼친 사람을 다시 읽는다.
  - **`function kvLiveExport()`** — HR만. 머리 16칸: `['본부','부서','이름','직급','구분','KPI 상태','종합 진행률(%)','기대 진행률(%)','차이(%p)','부진','이번 달 기록 상태','확인 메모(건)','확인 안 한 메모(건)','늦어지는 이유','알람 끈 항목(개)','알람 끈 이유']`. 숫자는 반올림, "이번 달 기록 상태" = `kvMonthText` + " · " + `kvCheckText` 글자, 늦어지는 이유 · 알람 끈 이유는 "실행목표: 이유" / 로 잇는다. 파일 이름 `KPI진행현황_{오늘}.csv`, 저장은 지금 `kvExport`와 같은 방법(`csvText` — 수식 글자 막기 그대로).
  - **`async function kvLiveSelfCheck()`** — 가짜 명단을 넣어 그림만 본다(`fetch`는 가짜). 확인할 것:
    - 팀장 명단: 묶음 머리 없음 · 확인 열 머리 "팀장 확인" · `direct && TODO`인 줄만 [펼침]
    - 본부장 명단: 팀마다 머리 줄 · 접고 펴기
    - 대표이사 명단: 확인 열 없음 · `teams` 한 줄에 사람 이름이 없음
    - HR: 도구 줄 · 처음엔 접힘 · 검색하면 펼침 · 부진만 보기
    - ✓ 자리: 확인함 · 확인 전 줄의 상태 글자 시작 위치가 같다(`<i>` 칸이 늘 있음)
    - 확정 전 줄은 상태 배지와 "—"
    - 달 넘기기: 1월에는 ‹ 없음, 이번 달에는 › 없음
    - 엑셀 머리 16칸 · `=`로 시작하는 이름에 `'`가 붙음
    - 펼친 카드: 이미 확인한 달은 [M월 확인] 대신 "✓ … 확인함", `direct`가 아니면 확인 단추 없음
- [ ] **Step 1: 화면 검사 함수 쓰기** — 위 항목을 넣는다.
- [ ] **Step 2: 실패 확인** — devlive 콘솔에서 `await kvLiveSelfCheck()`가 실패하는지 본다.
- [ ] **Step 3: 구현** — 시안 4 모양을 따른다. 새 모양은 `.kvl-*`로 두고, 있는 `.kv-bar` · `.kv-mk` · `.badge` · `.card`를 다시 쓴다.
- [ ] **Step 4: 확인** — devlive + playwright 1440px로 해 본다. `await kvLiveSelfCheck()` · `await kpiLiveSelfCheck()` · `kvSelfCheck()` 통과, 콘솔 오류 없음.
  1. 가상팀장 로그인: KPI 현황 탭이 보이고, 처리할 요청 · 가상직원 줄이 나온다.
  2. 가상직원이 이번 달 한 줄을 쓴다 → 팀장 표에 "확인 전" → [펼침] → [{M}월 확인] → "✓ 확인함".
  3. 가상직원이 한 줄을 고친다 → 팀장 표가 다시 "확인 전".
  4. 지난달로 넘겨 본다.
  5. 가상본부장 · 가상대표 · 가상인사로 같은 탭을 연다(대표이사 화면에 팀원 이름이 없음, HR 엑셀 내려받기).
  6. 가상직원에게는 탭이 없다.
  - 그림: `뉴디자인/KPI 실제 - 8 팀장 월간 점검 · 8b 본부장 · 8c 대표이사 · 8d HR.png`(저장소 밖)
- [ ] **Step 5: 커밋** — `git commit -m "KPI 현황(실제 로그인): 월간 점검 표 · 펼친 기록 · M월 확인 · HR 엑셀"`

---

### Task 8: 화면 — 확인 메모 · 본인 화면의 팀장 확인 · 부진 알람 · 알람 끄기

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 4 · 5 API, Task 7 `KV_LIVE` · `kvLivePick` · `kvLivePerson`, ① `kpiProgSheet` · `kpiProgReg` · `kpiProgIrr` · `kpiProgCard` · `kpiProgSum` · `homeKpiLive` · `kpiBoss`, 시연 메모 모양(`.kv-memo` · `.kv-reply` · `.kv-ack` · `.kv-wait` · `.kv-old` · `.kv-memo-in`)과 알람 끄기 창(`kvMuteAsk`의 모양 · `KV_MUTE`)
- Produces:
  - **`function kvLiveThread(memos, ownerName, uid)`** — 시연 `kvThread` · `kvMemoOne`과 같은 모양을 서버 자료로 그린다. 펼쳐 두는 메모 = 맨 새것 · 본인이 확인 안 한 것(본인일 때) · `fresh`가 있는 것(쓴 사람일 때) · 고치거나 답글을 다는 중인 것. 나머지는 "지난 메모 N건"으로 접는다. 단추는 서버 값대로: [수정][삭제](`canEdit`, "오늘까지 고칠 수 있음") · [확인했습니다](본인 · `ack` 없음) · [답글](본인 · 쓴 사람) · 답글 [삭제](`canDelete`).
  - **`function kvLiveMemos()`** (Task 7의 빈 함수를 채움) — 펼친 사람 카드 아래 "{writer 직책} 확인 메모" + "…이 쓰고 본인 · {윗사람} · HR이 함께 봐요" + `kvLiveThread` + `canWrite`면 입력 칸(`id="kvMemo"`, 200자, Enter · 한글 조합 중 아님)과 [남기기]. `writer`가 null이면 "대표이사에게 보고하는 분은 확인 메모가 없어요."
  - **`async function kvLiveMemoDo(act, id, uid)`** — `act` = `add` · `edit` · `del` · `ack` · `reply` · `rdel`. 서버가 받은 뒤 메모 · 명단(숫자)을 다시 읽는다. 400 · 403은 아래쪽 알림(`kpiLivePost` 규칙), 지우기는 한 번 묻는다.
  - **`function kvLiveMyMemoCard()`** — 본인 진행 관리(`kpiProgSheet`) 맨 아래 "{팀장|본부장} 확인 메모" 카드(`id="kvMyMemo"`). 확인 안 한 메모가 있으면 "확인할 메모 n건" 배지. 자료는 `kpiLiveLoad`가 확정본이 있을 때 `GET /api/kpi/memos/<내 id>`로 채우는 `KPI_LIVE.memos`. `writer`가 null이고 메모가 없으면 그리지 않는다.
  - **본인 화면의 팀장 확인** `function kpiProgChk(prog, month)` — `prog.checkStep && prog.checks[month]`이면 `<span class="kp-chk"><i>✓</i>{팀장|본부장} 확인 m/d</span>`, 아니면 같은 폭의 빈 자리. 붙는 곳: 정기 이번 달 · 지난달 칸, "지난 달 기록" 접힘의 달마다, 비정기 결과 줄(그 일정 실제 기한의 달). 직책 이름은 `kpiBoss`와 같은 조직도 명단에서 고른다. `checkStep`이 거짓이면 칸을 그리지 않는다(설계 6.4).
  - **부진 알람(본인)** `function kpiProgAlarm(prog)` — `prog.alarm.slow`이면 요약 세 칸 위에 붉은 한 줄(`id="kpiSlow"`): "진행이 기대보다 늦어요 · 종합 n% (기대 n%)", 끈 항목이 있으면 "알람을 끈 n개 항목을 빼고 n% (기대 n%)". `homeKpiLive` 제목 옆에도 종.
  - **알람 끄기(본인)** — 실행목표 카드의 "늦어지는 이유" 아래:
    - 꺼져 있지 않으면 [알람 끄기] → `kvLiveMuteAsk(rid)` 창(시연 창과 같은 모양: 이유 넷 · 달 고르기(이번 달 ~ 12월) · 직접 적기 50자) → [끄기] `POST /api/kpi/me/mute` → 진행 기록을 다시 읽는다. 오류는 창 안에, 창은 서버가 받은 뒤 닫힌다.
    - 꺼져 있으면 "알람 꺼짐 · {이유} ({n월 말까지 | 올해 말까지})" + [다시 켜기] → `kvLiveUnmute(uid, rid)`
  - **`async function kvLiveUnmute(uid, rid)`** — `POST /api/kpi/plans/<uid>/unmute` → 본인 화면이면 진행 기록, KPI 현황이면 펼친 사람 · 명단을 다시 읽는다. KPI 현황의 [다시 켜기]는 `direct`일 때만 그린다.
  - **`kvLiveSelfCheck()` · `kpiLiveSelfCheck()`에 더할 것**
    - 메모: 본인은 [확인했습니다] · [답글], 쓴 사람은 같은 날 [수정][삭제], 윗사람 · HR은 단추 없음, 새 답글 표시
    - 지난 메모 접기: 확인한 옛 메모는 "지난 메모 N건" 안에
    - `kpiProgChk`: `checkStep` 거짓이면 칸 없음, 확인한 달 · 안 한 달의 글자 시작 위치가 같음
    - 알람: `alarm.slow`면 `#kpiSlow`, 끈 항목은 [다시 켜기], 켜진 항목은 [알람 끄기]
- [ ] **Step 1: 화면 검사 함수 확장**
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 시연 메모 · 알람 끄기 모양 클래스를 그대로 쓴다(새 칸 · 새 카드를 늘리지 않는다 — 인사담당자의 걱정).
- [ ] **Step 4: 확인** — devlive로 해 본다.
  1. 가상팀장이 펼친 가상직원에게 메모를 남긴다.
  2. 가상직원 목표설정: "확인할 메모 1건" · 메뉴 숫자 → [확인했습니다] → 답글.
  3. 가상팀장 KPI 현황: 붉은 말풍선 · 메뉴 숫자 → 펼치면 사라진다.
  4. 가상팀장이 같은 날 메모를 고친다 → 가상직원 화면 다시 "확인할 메모".
  5. 가상직원이 알람을 끈다 → 가상팀장이 [다시 켜기].
  6. 가상팀장이 월간 확인한 달에 가상직원 화면 "✓ 팀장 확인", 가상본부장 진행 관리에는 확인 칸이 없다.
  - 그림: `뉴디자인/KPI 실제 - 9 확인 메모 · 알람.png`
- [ ] **Step 5: 커밋** — `git commit -m "KPI 확인 메모 주고받기 · 본인 화면 팀장 확인 · 부진 알람 · 알람 끄기 (실제 로그인)"`

---

### Task 9: 화면 — 경영계획 목표 · 알람 기준 · 처리할 일 숫자 · 내용 보기의 바뀐 기한 · 사용법

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 5 · 6 API, 1단계 `kpiLiveHr` · `kpiLiveContent` · `kpiLiveLoad` · `kpiLiveView` · `kpiLiveKeep`(R21) · `hrTodo`, 시연 `kpiPlanCard` · `TODO` · `homePortalTasks` · `TOURS`
- Produces:
  - **`function kpiPlanList(rows, ceo)`** — 지금 `kpiPlanCard`의 모양을 `rows`(`[[목표, 내용]]`)로 받게 떼어 낸다. 시연 `kpiPlanCard()`는 `kpiPlanList(kpiPlan…, kpiRole==='ceo')`를 부른다(시연 HTML 해시 그대로).
  - **`KPI_LIVE.biz`** — `kpiLiveLoad`가 대표이사 · 본부장 · HR이면 `GET /api/kpi/biz-plan`으로 채운다. `KPI_LIVE.review` — 처리함 응답의 `review`.
  - **대표이사 목표설정 탭:** 처리할 KPI · 우리 팀 · HR에 맡기기 아래에 `kpiPlanList(biz, true)`. 탭 이름을 **"KPI 승인 · 경영계획"**으로(시연과 같게).
  - **본부장 목표설정 탭:** 내 KPI 위에 `kpiPlanList(biz, false)`(시연과 같은 자리).
  - **HR 관리 화면(`kpiLiveHr`)에 두 카드:**
    - "KPI 현황 알람 기준" — "부진 — 기대 진행률보다 이만큼 낮으면 빨간 종" 숫자 칸(1~100, %p) + [저장] → `POST /api/kpi/admin/alarm`, 값은 `admin.alarmGap`
    - "{해}년 경영계획 목표" — 줄마다 목표(60자) · 내용(300자) · [삭제], [＋ 목표 추가](20개까지), [저장] → `POST /api/kpi/admin/biz-plan`. "대표이사 · 본부장 · HR만 봐요."
    - 적고 저장 안 한 것은 R21 방식(`KPI_LIVE.bizDirty` + `kpiLiveKeep`에서 지킴 + `beforeunload`)
  - **처리할 일 숫자(실제 로그인)**
    - `TODO.e1` = 지금 + `review.memos`(확인할 메모)
    - `TODO.e4` = `review.check + review.replies`
    - `homePortalTasks`: "확인 메모 확인"(e1) = `review.memos` · "KPI 월간 확인"(e4, 배지 "확인 대기") = `review.check` · "확인 메모에 새 답글"(e4) = `review.replies`. **메뉴 숫자 합 = 홈 합**(`homeSelfCheck`)을 지킨다.
    - 시연 숫자는 그대로다.
  - **내용 보기의 바뀐 기한(①에서 미룬 것):** `kpiLiveView(uid)`가 확정본을 보일 때(`old`) `GET /api/kpi/progress/<uid>`의 일정 `due`로 `{sid: due}`를 만들어 `kpiKindSection(goals, false, dues)`에 넘긴다. 못 읽으면 지금처럼 dues 없이.
  - **사용법:** `TOURS.kv`가 실제 로그인이면 새 단계를 쓴다 — 처리할 요청(`#kvlInbox`) · 월간 점검 표(`#kvlTable`) · 달 넘기기(`#kvlMonth`) · 펼친 기록(`#kvlPerson`) · M월 확인(`#kvlCheck`) · 확인 메모(`#kvMemo`) · (HR) 도구 줄(`#kvlTools`). 없는 칸의 단계는 지금처럼 건너뛴다. `TOURS.kpi`에 본인 화면의 확인 메모(`#kvMyMemo`) · 알람(`#kpiSlow`) 단계를 더한다(실제 로그인만).
  - **`kpiLiveSelfCheck()`에 더할 것:** `TODO.e1/e4` = 처리함 · review 합, 홈 합 = 메뉴 합, 대표이사 탭 이름, HR 화면 두 카드, 내용 보기에 "(바뀜)".
- [ ] **Step 1: 화면 검사 함수 확장**
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 확인** — devlive로 해 본다.
  1. 가상인사가 알람 기준 30 · 경영계획 목표 둘을 저장한다.
  2. 가상대표 목표설정 "KPI 승인 · 경영계획"에 목표 둘, 가상본부장 내 KPI 위에 목표 둘, 가상팀장 · 가상직원에게는 없다.
  3. 메뉴 · 홈 숫자가 Task 7 · 8의 일에 맞게 오르내린다.
  4. 일정 변경이 확인된 계획서에 수정 요청 → 팀장 처리함 [내용 보기]에 "(바뀜)".
  5. KPI 현황 "이 화면 사용법"을 팀장 · HR로 끝까지 넘긴다.
  - 그림: `뉴디자인/KPI 실제 - 10 경영계획 · 알람 기준.png`
- [ ] **Step 5: 커밋** — `git commit -m "경영계획 목표(HR 입력 · 대표이사 · 본부장 보기) · 알람 기준 · 처리할 일 숫자 · 내용 보기 바뀐 기한 · 사용법"`

---

### Task 10: 문서 · 전체 검사 · PR

**Files:**
- Modify:
  - `CLAUDE.md`: 평가·성과에 "실제 로그인이면 KPI 현황 · 월간 점검 (2단계 ②)" 항목(코드가 지키는 약속만 — 보는 범위 · 대표이사 팀원 묶음 · 확인이 풀리는 방식 · 메모 규칙 · 알람 반올림 · 감사로그 · DB 판 14 · 시안과 달라진 점). ①에서 미룬 한 줄: 1단계 "HR 대신 처리" 줄의 범위에 "맡기지 않은 대표이사 몫 제외"를 넣는다. 2단계 ① 항목 끝의 "2단계 ②(다음 PR)" 줄을 "끝남"으로. 시연 KPI 현황 항목에 "실제 로그인은 위 2단계 ② 항목".
  - 설계 문서 상태 줄: "2단계 ② 구현 (PR 준비 중)", 12장에 2026-10-21 결정 셋(표 하나로 · 이번 달 + 지난달 · 해 넘김 화면 없음)
  - `README.md` 메뉴 표(평가 · 성과 줄)
- [ ] **Step 1: 로컬 전체 검사** — `export PYTHONIOENCODING=utf-8; for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py; cd backend && python -m hr_eval.selfcheck && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features && python -m auth_check && python -m hr_eval.roster_sync --check` → 모두 통과
- [ ] **Step 2: 시연 모드 확인** — 역할 일곱 × 평가 · 성과 탭 넷 × 화면, 홈 · 워크스페이스의 HTML이 이 계획 시작 커밋과 같다(①처럼 해시 비교). `kvSelfCheck()` · `kpiSelfCheck()` · `homeSelfCheck()` · `todoSelfCheck()` 통과.
- [ ] **Step 3: 운영 DB 올리기 시험** — 판 13 자료(계획서 · 확정본 · 진행 기록 · 일정 변경 · 급여명세서 · 원천징수영수증)가 든 DB에서 `app`을 띄워 "DB 준비 완료 (판 14)"과 기존 줄이 그대로인지, 기존 `kpi_years` 줄의 `alarm_gap`이 20인지 본다.
- [ ] **Step 4: 문서 고치기**
- [ ] **Step 5: 커밋 · PR**
  - main이 그새 바뀌었으면(특히 원천징수영수증의 DB 판) 합치고 번호를 옮긴 뒤 Step 1부터 다시.
  - 인사담당자가 "올려줘"라고 하면 push한다.
  - PR 본문: 템플릿 항목 + 그림, `@holee9` 보안 확인 요청(넓어진 보는 범위 · 대표이사 팀원 묶음 · 메모 권한 · 새 API 열다섯).
  - merge는 "merge 해줘"를 받은 뒤에만 한다. 합친 뒤 운영 반영과 "DB 준비 완료 (판 14)"을 확인한다.

---

## 하지 않는 것 (이 계획 밖)

- 해 넘김 화면(1월에 지난해 기록 · 확인) — 인사담당자 설명(2026-10-21)대로 만들지 않는다.
- 하이웍스 메신저 알림 — 설계 2장.
- 평가 진행 · 평가 결과 탭 실제화 — 3단계(2027년 12월 평가 전).
- 엑셀 진짜 `.xlsx` — 지금처럼 엑셀에서 열리는 CSV.
