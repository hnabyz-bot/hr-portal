# 평가 규칙 손질 — 등급 비율 HR 설정 · 대표이사 정원 늘려 주기 · 가점 없앰 · 등급 안내 · 점수 역전 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 연말 평가의 등급 비율을 HR이 해마다 정하고, 대표이사가 묶음 정원을 늘려 줄 수 있게 하며, 가점을 없애고, 점수와 등급이 어긋날 때 직원 · 팀장 · 본부장에게 이유가 보이게 한다 — 실제 로그인과 시연 화면 모두.

**Architecture:** 정원 숫자는 `hr_eval/domain/quota.calculate_department_quota`가 비율(그해 회차) · 늘린 정원(묶음 기록)을 받아 셈하고, 정원 검사 `eval_rules.check_pool`이 점수 역전 규칙을 더한다. 서버는 회차(`ev_rounds`)에 비율 세 칸, 묶음 기록(`ev_pools`)에 늘린 정원 칸을 두고(DB 판 23), 화면은 시연 규칙(`evQuota` · `evValidate`)을 같은 셈으로 고친다. 가점은 입력 길 · 응답 칸 · 화면에서 모두 뺀다(DB 칸은 남기고 쓰지 않는다).

**Tech Stack:** Python 3 + Flask + SQLite(`backend/`), 단일 HTML · 바닐라 JS(`public/index.html`), 검사 `python -m hr_eval.selfcheck` · `python -m auth_check` · `python -m hr_eval.sql.check_schema` · `python -m hr_eval.sql.check_sqlite_features` · `.ci/*` · 화면 자체 검사(브라우저 콘솔 `evSelfCheck()` · `evLiveSelfCheck()` · `evLineSelfCheck()` · `evrSelfCheck()`).

**Spec (인사담당자 결정, 2026-10-27 대화 — 이 계획의 근거):**
- **S1 등급 비율 HR 설정** — 그해 회차에 `S·A` · `B` · `D` 세 칸(묶음 인원의 %, 정수). C는 나머지. 처음 값 10 · 10 · 10(지금 규칙 그대로). 세 칸 합은 70 이하(반올림으로 정원 합이 인원을 넘지 않게 — 5명 이상 묶음에서 Σ반올림 ≤ 0.7N + 1.5 ≤ N). **평가 시작 전까지만** 바꿀 수 있다(시작하면 잠김). **모든 묶음**(본부 팀원 · 전사 팀장 · 직속부서 팀원)에 같은 비율. 4명 이하 묶음 규칙(S·A·B 합쳐 1명, 1명 묶음 0명)은 그대로. 진행표 기준 점수 칸 밑, [마감일 저장]으로 함께 저장. 시안 `클로드 코드/뉴디자인/등급 비율 HR 설정 - 시안 (2026-10-27).png`.
- **S2 대표이사 정원 늘려 주기** — 대표이사가 본부 팀원 묶음을 돌려보낼 때 등급(S·A 또는 B) · 몇 명 · 이유를 함께 보내 그 묶음 그해 정원을 늘린다 → 본부장이 그만큼 더 주고 다시 마감. 대표이사가 직접 등급을 정하는 묶음(전사 팀장 · 직속부서 · 본부장 없는 본부)은 자기 배정 화면에서 바로 늘린다. 맡김이면 HR이 대신(자기가 든 묶음 빼고). 점수 기준 · 연차 규칙은 예외로도 못 넘는다. 늘린 정원과 이유는 본부장 화면 · HR 화면 · 결과 보고서에 보인다. 시안 `뉴디자인/대표이사 정원 예외 - 시안 1 돌려보내기 창` · `시안 2 본부장 화면 (2026-10-27).png`.
- **S3 가점 없앰** — 평가에서 가점을 모두 없앤다(본부장 · 대표이사 · 팀장 평가 다). 이유: 연봉 계산의 개인 포상 · 자격증 추가 인상률과 중복 혜택. 본부장 · 대표이사가 팀장 점수(실행목표 등급)를 직접 고치는 것은 **그대로**(돌려보내기 새로 안 만듦).
- **S4 등급 안내** — ① 직원: 자기평가 화면 · 결과 화면에 "등급은 점수 기준과 정원으로 정해요 · 점수 기준은 받을 수 있는 가장 높은 등급" 규칙, 결과 화면에 "점수로는 A까지 가능했고, 본부 정원에 따라 C로 정해졌어요" 한 줄. ② 팀장(평가자) 점수 화면에 그해 정원과 "최종 등급은 본부장이 본부 정원 안에서 정해요. 등급 의견은 점수로 받을 수 있는 가장 높은 등급이에요".
- **S5 점수 역전** — 점수가 낮은 사람에게 더 높은 등급을 주면 경고하고, 밀린 사람(점수가 더 높은데 등급이 낮은 사람)에게 윗사람 코멘트(이유)가 꼭 있어야 마감 · 확인된다. 이유는 대표이사 · HR이 본다.
- **S6 시연 화면도 같은 규칙으로** 고친다(이 묶음은 "시연 해시 그대로" 규칙이 아니다 — 평가 · 성과 밖 화면은 그대로).

## Global Constraints

- 갈래 `feature/eval-rules`(작업 폴더 `scratchpad/wt-rules`, `feature/eval-live-2` + main a99d9fd 합친 4fe9302 위). **push 금지** — 인사담당자의 "올려줘" 뒤에만. merge는 "merge 해줘"로만.
- 커밋 · PR · 이슈 · 코드 · 시험에 실명 금지(가상 이름만). 휴대폰 번호 모양 글자(`010-xxxx-xxxx`) 금지.
- 작업 폴더 파일은 **CRLF**. 고칠 때는 `scratchpad/crlf_patch.py`의 `apply(path, [(old, new)])`(옛 글자가 정확히 한 번 있어야 한다) 또는 CRLF를 지키는 방법으로. 새 파일도 CRLF.
- **공개 전 등급은 서버 응답 · 감사로그 · 서버 로그 · 알림 글 어디에도 새지 않는다** — 사람 줄은 `_row_json` 한 곳. 감사로그 `after_data`에는 해 · 묶음 열쇠 · 늘린 정원 숫자(묶음 단위)까지만, 이유 글 · 등급 · 점수 없음.
- 사용자에게 보이는 새 글은 이 계획이 적은 글자 그대로. 실제 로그인 글은 "~해요"체, 오류 문구는 그 파일의 이웃 문구 말투(서버 `…습니다.`).
- DB 판 23 = 새 파일 `backend/hr_eval/sql/023_eval_rules.sql` 하나(022 다음). 이미 있는 표를 다시 만들지 않는다 — 칸은 `ALTER TABLE … ADD COLUMN`(한 칸 CHECK), 여러 칸에 걸친 규칙은 트리거로.
- `ev_targets.bonus_x2` · `bonus_reason` 칸은 지우지 않는다(SQLite CHECK · 판 규칙) — 023 주석에 "판 23부터 쓰지 않음, 늘 0"을 적고 서버는 읽지도 쓰지도 않는다(총점은 가점 0).
- 검사는 모두 통과한 채로 커밋한다: `cd backend && python -m hr_eval.selfcheck && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features && python -m auth_check` · 저장소 뿌리에서 `for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py` (Windows Git Bash는 `export PYTHONIOENCODING=utf-8` 먼저). 파이썬은 `scratchpad/venv/Scripts/python.exe`(CI와 같은 판).
- 화면 자체 검사는 `scratchpad/kf_checks.js`를 `scratchpad/ff_run.js`로 돌린다(정적 서버 `python -m http.server 8812 --directory <작업 폴더>/public`). 모든 줄이 실패 없이 끝나야 한다.

## Review Focus

1. **D가 점수로 정해지는 사람(총점 70점 이하)** — 정원 검사가 이들을 막아 묶음이 영영 마감되지 않는 일은 이번 작업이 새로 만들지 않는다(지금 규칙 그대로 — 인사담당자에게 따로 묻는 중). D 비율을 0으로 저장하는 것은 허용하되 진행표 예시 줄에 그대로 보인다.
2. **늘린 정원이 묶음 인원을 넘는 요청**(예: 5명 묶음에 S·A +5) — 서버 400, 화면 고르기 칸은 넘는 숫자를 내놓지 않는다(Task 2 · 4 · 6 검사).
3. **평가 시작 뒤 비율을 바꾸려는 요청** — 날짜 · 기준 점수 저장은 되고 비율만 409(같은 값이면 통과) — 진행표가 시작 뒤 [마감일 저장]을 눌러도 막히지 않아야 한다(Task 3 검사).
4. **옛 화면 · 옛 요청이 가점을 보냄** — `bonus` · `bonusReason` 칸이 있으면 400 "가점은 주지 않습니다." · 0을 보내도 400(Task 3 검사).
5. **동점 · KPI 없는 사람 · 연차 초과자** — 점수 역전으로 치지 않는다(동점, 점수 없음, 밀린 사람이 연차 규칙 때문에 그 등급을 받을 수 없는 경우)(Task 2 검사).

---

### Task 1: DB 판 23 — `023_eval_rules.sql`

**Files:**
- Create: `backend/hr_eval/sql/023_eval_rules.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` (판 22 다음에 "인사평가 규칙 (023)" 검사 묶음 · 판 번호 기대값)
- Modify: `CLAUDE.md` (DB 판 목록에 판 23 한 줄)

**Interfaces:**
- Produces (칸):
  - `ev_rounds.ratio_sa` · `ratio_b` · `ratio_d` — `INTEGER NOT NULL DEFAULT 10 CHECK (… BETWEEN 0 AND 70)`. 트리거 `trg_ev_rounds_ratio_ins` · `trg_ev_rounds_ratio_upd`(BEFORE INSERT · BEFORE UPDATE OF ratio_sa, ratio_b, ratio_d): 합 > 70 이면 `RAISE(ABORT, 'RATIO_SUM: 등급 비율 세 칸 합은 70 이하여야 합니다.')`.
  - `ev_pools.extra_sa` · `extra_b` — `INTEGER NOT NULL DEFAULT 0 CHECK (… BETWEEN 0 AND 5)`; `extra_reason TEXT NULL CHECK (extra_reason IS NULL OR length(extra_reason) BETWEEN 1 AND 200)`; `extra_at TEXT NULL`; `extra_by INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT`; `extra_behalf INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT`. 트리거 `trg_ev_pools_extra_ins` · `trg_ev_pools_extra_upd`: `extra_sa + extra_b > 0` 인데 `extra_reason IS NULL` 이면 `RAISE(ABORT, 'EXTRA_REASON: 정원을 늘렸으면 이유가 있어야 합니다.')`.
- 머리 주석: 무엇 · 왜(인사담당자 2026-10-27 결정 S1 · S2 · S3) · 판 23 · `bonus_x2` · `bonus_reason`은 판 23부터 쓰지 않음(늘 0).

- [ ] **Step 1: check_schema에 실패하는 검사 묶음을 쓴다** — 새 DB(판 23까지)에서: 기본값 10 · 10 · 10 / 0 · 0 · NULL, `ratio_sa = 71` CHECK 오류, `40 · 20 · 11`(합 71) INSERT · UPDATE 모두 `RATIO_SUM`, `40 · 20 · 10` 통과, `extra_sa = 6` CHECK 오류, `extra_sa = 1` · 이유 NULL → `EXTRA_REASON`, `extra_sa = 1` · 이유 있음 통과, 둘 다 0 · 이유 NULL 통과, 이유 201자 CHECK 오류. 판 22 DB에서 023만 더해도 옛 줄이 기본값을 받는지(판 올리기 길).
- [ ] **Step 2: 돌려 실패를 본다** — `python -m hr_eval.sql.check_schema` → 새 묶음 실패(칸 없음).
- [ ] **Step 3: `023_eval_rules.sql`을 쓴다** (위 칸 · 트리거, 주석은 020 · 022처럼 한국어).
- [ ] **Step 4: 통과를 본다** — `check_schema` · `check_sqlite_features` · `auth_check` 첫 줄 "DB 준비 완료 (판 23)".
- [ ] **Step 5: 커밋** — `DB 판 23: 등급 비율 · 늘린 정원 칸 (평가 규칙 손질)`.

### Task 2: 규칙 — 비율 · 늘린 정원 · 점수 역전

**Files:**
- Modify: `backend/hr_eval/domain/quota.py`
- Modify: `backend/hr_eval/domain/eval_rules.py` (`Member` · `check_pool` · 모듈 설명)
- Test: `backend/hr_eval/selfcheck.py`

**Interfaces:**
- Produces:
  - `quota.DEFAULT_RATIOS = (10, 10, 10)` · `quota.EXTRA_MAX = 5` · `quota.RATIO_SUM_MAX = 70`.
  - `calculate_department_quota(headcount: int, ratios: tuple[int, int, int] = DEFAULT_RATIOS, extra: tuple[int, int] = (0, 0)) -> Quota` — ratios = (S·A %, B %, D %), extra = (S·A +명, B +명).
    - N ≥ 5: `a = round_half_up(N·ra/100) + extra_sa`, `b = round_half_up(N·rb/100) + extra_b`, `d = round_half_up(N·rd/100)`, `cap_s = a`, `c = N − a − b − d`.
    - N ≤ 4: `base = 1 if N ≥ 2 else 0`, `upper = base + extra_sa + extra_b`, `cap_s = base + extra_sa`, `lower = N − upper` (비율은 쓰지 않는다).
    - `c < 0` 또는 `lower < 0` → `ValueError("늘린 정원이 묶음 인원보다 많습니다.")`. 비율 · 늘린 정원의 범위는 부르는 쪽(서버 · DB)이 본다.
  - `eval_rules.Member`에 칸 `note: bool = False`(윗사람 코멘트가 있나 — 앞뒤 공백 뺀 글자가 있으면 참).
  - `check_pool(members, bands, full, ratios=DEFAULT_RATIOS, extra=(0, 0), need_note=True) -> tuple[list[str], list[str], dict[str, int]]` — 정원은 `calculate_department_quota(len(members), ratios, extra)`. 정원 문구 · 차례는 그대로이고 끝에 **점수 역전**을 더한다:
    - 대상: 등급이 있고 점수(score)가 있는 사람끼리. x가 밀렸다 = 어떤 y가 `y.score < x.score` 이고 `above(y.grade, x.grade)` 이며 `not above(y.grade, cap(x.score, x.leave_x2, bands))`(x도 y의 등급을 받을 수 있었다). 동점은 역전이 아니다.
    - 밀린 x마다 한 줄, y는 밀린 x보다 점수가 낮은 사람 중 등급이 가장 높은 사람(같으면 점수가 높은 사람, 그다음 명단 차례). 문구(숫자는 `_num`):
      - `need_note` 참이고 `x.note` 거짓 → 오류 `"{x} 님(총점 {sx}점)이 총점이 더 낮은 {y} 님({sy}점)보다 등급이 낮습니다. 코멘트에 이유를 적어 주세요."`
      - 그 밖 → 경고 `"{x} 님(총점 {sx}점)이 총점이 더 낮은 {y} 님({sy}점)보다 등급이 낮습니다."`
    - 역전 줄은 `full`과 상관없이 낸다(배정하는 중에도 보인다).

- [ ] **Step 1: 실패하는 selfcheck를 쓴다** (이름은 이웃처럼 한국어 함수 이름):
  - `비율을_바꾸면_정원이_바뀐다`: `calculate_department_quota(10, (20, 10, 0))` → a 2 · b 1 · d 0 · c 7 · cap_s 2; `(20, (25, 15, 10))` → a 5 · b 3 · d 2 · c 10.
  - `비율_기본값은_지금_규칙과_같다`: 0~60명 모두 `calculate_department_quota(n) == calculate_department_quota(n, (10, 10, 10), (0, 0))`이고 지금 기대값(10명 A1 B1 C7 D1)과 같다.
  - `비율_합_70이면_5명_이상_어디서나_정원_합이_인원을_넘지_않는다`: 5~200명 × 합 ≤ 70 인 비율 몇 벌((70,0,0) · (35,35,0) · (30,30,10) · (25,25,20) · (0,0,70))에서 c ≥ 0.
  - `늘린_정원은_SA와_B에_더해지고_S상한도_오른다`: `(10, extra=(1, 0))` → a 2 · cap_s 2 · c 6; `(10, extra=(0, 2))` → b 3 · cap_s 1; 4명 `extra=(1, 0)` → upper 2 · cap_s 2 · lower 2; 1명 `extra=(1, 0)` → upper 1 · lower 0.
  - `늘린_정원이_인원을_넘으면_ValueError`: 5명 `extra=(5, 5)`, 3명 `extra=(2, 1)`.
  - `점수_역전은_코멘트가_없으면_오류_있으면_경고`: 6명 묶음 기본 비율, 95점 C(note 없음) · 91점 A → 오류 한 줄(문구 그대로); 같은 것을 note 참으로 → 경고 한 줄 · 오류 없음; `need_note=False` → 경고.
  - `동점_점수없음_연차초과는_역전이_아니다`: 92점 C · 92점 A → 역전 없음; 점수 None(KPI 없음) C · 91점 A → 없음; 95점 연차 초과(leave_x2 10) C · 91점 B → 없음.
  - `정원_검사가_비율과_늘린_정원을_쓴다`: 10명 `ratios=(20,10,10)`에서 A 2명은 통과, 기본 비율이면 "A는 1명까지입니다. 현재 2명"; `extra=(1,0)`이면 기본 비율로도 통과.
- [ ] **Step 2: 돌려 실패를 본다** — `python -m hr_eval.selfcheck`.
- [ ] **Step 3: `quota.py` · `eval_rules.py`를 고친다** (모듈 설명 · `check_pool` 설명에 비율 · 늘린 정원 · 역전을 적는다. `grading.py` 등 다른 호출은 기본값으로 그대로 돈다).
- [ ] **Step 4: 통과를 본다** — `selfcheck` 전부 · `auth_check` 전부(아직 서버는 기본값이라 그대로 통과).
- [ ] **Step 5: 커밋** — `평가 규칙: 등급 비율 · 늘린 정원 · 점수 역전 (정원 검사)`.

### Task 3: 서버 — 회차 비율 · 가점 없앰 · 결과 · 보고서 정원 글자

**Files:**
- Modify: `backend/evaluation.py` (`_round` · `round_set` · 새 `_ratios` · `_bands_of` 옆 `_ratios_of` · `_total` · `_can` · `_row_json` · `person_edit` · `results` · `_pool` · `_pool_json` · 모듈 설명)
- Modify: `backend/eval_report.py` (`_from_db` 정원 글자 · 아래 설명 줄 · `clean`의 `ratio`)
- Test: `backend/auth_check.py` (`check_eval_admin` · `check_eval_view` · `check_eval_score` · `check_eval_report` — 가점 검사는 없앰 검사로 바꾼다)

**Interfaces:**
- Consumes: Task 1 칸, Task 2 `calculate_department_quota(n, ratios, extra)` · `check_pool(..., ratios, extra, need_note)` · `Member.note`.
- Produces:
  - `_round(...)["ratio"] = {"SA": int, "B": int, "D": int}` — `/api/eval/state`의 `round`, 새로 `/api/eval/results` 응답 맨 위 칸 `"ratio"`(그해 회차 비율, 회차가 없으면 `{"SA": 10, "B": 10, "D": 10}`).
  - `_ratios(v: object) -> tuple[int, int, int]` — `{"SA","B","D"}` 정수(참/거짓 아님) 0~70 · 합 ≤ 70, 아니면 400 `"등급 비율은 0~70 사이 정수로, 세 칸 합이 70 이하가 되게 넣어 주세요."`. `round_set`의 `ratio`는 **필수**(화면이 늘 보낸다). 시작한 해에 옛 값과 다르면 409 `"평가가 시작되어 등급 비율을 바꿀 수 없습니다."`(같으면 통과 — 날짜 · 기준 점수 저장은 그대로).
  - `_ratios_of(ctx) -> tuple[int, int, int]`.
  - `_pool(ctx, key)`: `check_pool(members, bands, full, _ratios_of(ctx), (p["extra_sa"], p["extra_b"]), need_note=key != "LEADERS")`, `Member(..., note=bool(r["head_comment"].strip()))`.
  - `_pool_json(...)["quota"]` — 정원 글자(화면 `evQuotaText`와 같은 글자): INDIVIDUAL `"S 최대 {cap_s}명 · A {a}명 (S만큼 줄어듦) · B {b}명 · D {d}명 · C 나머지"`, GROUPED `"S 최대 {cap_s}명 · A·B 합계 {upper}명 (S만큼 줄어듦) · C·D 나머지"`, 늘린 정원이 있으면 뒤에 `" · 대표이사 허용 S·A +{x}명 · B +{y}명"`(0인 쪽은 뺀다). 숫자는 늘린 정원이 들어간 값.
  - **가점 없앰:** `_can`에서 `"bonus"` 열쇠를 뺀다. `_row_json`에서 `"bonus"` · `"bonusReason"` 칸을 뺀다(mine 줄의 덮어쓰기도). `person_edit`: 몸통에 `"bonus"` 나 `"bonusReason"` 열쇠가 있으면(값이 0이어도) 400 `"가점은 주지 않습니다."` — 권한(403)보다 먼저 보지 않는다: 순서는 지금처럼 `can.score`/`can.assign` 403 → 이 400 → 칸 권한 403 …. `_total`은 가점 0(`total(rows, 0)`), 저장 UPDATE는 `bonus_x2` · `bonus_reason`을 건드리지 않는다. `BONUS_NO` · `BONUS_MAX_X2` 쓰임새를 없앤다(`eval_rules.BONUS_MAX_X2` · `TEXT_MAX["bonusReason"]`도 지운다 — 다른 데서 안 쓰면).
  - 보고서 `_from_db`: 묶음 정원 글자 INDIVIDUAL `"S·A {a}명 · B {b}명 · D {d}명"`, GROUPED `"S·A·B 합계 {upper}명"`, 늘린 정원이 있으면 `" (대표이사 허용 +{x+y}명)"`. 설명 줄 `"· 정원 기준 = 묶음 인원의 S·A {sa}% · B {b}% · D {d}%(반올림), 4명 이하 묶음은 S·A·B 합계 1명. 대표이사가 늘린 정원은 묶음 줄에 적습니다."` — 비율은 `d["ratio"]`(`_from_db`가 넣고, 시연 길 `clean`은 몸통의 `"ratio"` `{"SA","B","D"}` 정수 0~70을 받되 없으면 10 · 10 · 10, 모양이 틀리면 지금 다른 칸처럼 400).

- [ ] **Step 1: 실패하는 auth_check를 쓴다:**
  - `check_eval_admin`: 회차 저장에 `ratio` 없음 · `{"SA":71,...}` · 합 71 · `True` · `10.0` · 글자 → 400(문구 그대로) · 아무것도 안 바뀜; `{"SA":20,"B":10,"D":5}` 저장 → state `round.ratio` 같음 · `ev_rounds` 칸 같음; 시작 뒤 같은 비율 + 날짜 바꿈 → 200, 다른 비율 → 409(문구) · 날짜도 안 바뀜.
  - `check_eval_view`: 줄 칸 목록에서 `bonus` · `bonusReason`이 없고 `can`에 `bonus`가 없다(기대 dict 고침).
  - `check_eval_score`: 지금 가점 검사(`BONUS_NO` · `REASON` · `BONUS_BAD` · `NO_KPI_BONUS` · 대표이사 가점)를 "가점은 주지 않습니다." 400 검사로 바꾼다 — 본부장 · 대표이사 · 평가자 · 맡은 HR 모두, `bonus=0` · `bonusReason=""`만 보내도 400, 다른 칸과 함께 보내면 아무것도 저장 안 됨, 권한 없는 사람은 여전히 403 먼저. 총점이 `bonus_x2`를 DB에 넣어 둬도(직접 UPDATE 3) 읽지 않는다(총점 그대로).
  - 비율이 정원에 쓰이는지: 묶음 정원 글자가 비율 `20/10/10`에서 10명 묶음이면 `"S 최대 2명 · A 2명 (S만큼 줄어듦) · B 1명 · D 1명 · C 나머지"`, 그 비율로 A 2명 마감 200(기본 비율이면 409 "A는 1명까지입니다. 현재 2명").
  - `/api/eval/results`에 `ratio` 칸.
  - `check_eval_report`: 비율 `20/10/10`인 해의 보고서 요약에 설명 줄 글자 · 묶음 정원 글자.
- [ ] **Step 2: 돌려 실패를 본다** — `python -m auth_check`.
- [ ] **Step 3: `evaluation.py` · `eval_report.py`를 고친다** (모듈 설명의 회차 몸통 · 거절 순서 · 보는 수준 · 감사로그 설명에서 가점을 빼고 비율을 더한다).
- [ ] **Step 4: 통과를 본다** — `auth_check` 전부 · `selfcheck` · `check_schema`.
- [ ] **Step 5: 커밋** — `평가 서버: HR 등급 비율(시작 전까지) · 가점 없앰 · 결과 · 보고서 정원 글자`.

### Task 4: 서버 — 대표이사 정원 늘려 주기 · 점수 역전 코멘트

**Files:**
- Modify: `backend/evaluation.py` (`pool_back` · 새 `pool_extra` · `_pool_auth` · `_pool_can` · `_pool_json` · 새 `_extra` · 모듈 설명)
- Test: `backend/auth_check.py` (`check_eval_close` 끝 부분 · `check_eval_score`의 마감 검사)

**Interfaces:**
- Consumes: Task 1 칸 · Task 2 규칙 · Task 3 `_pool` · `_ratios_of`.
- Produces:
  - `_extra(v: object, size: int, ratios) -> tuple[int, int]` — `{"SA","B"}` 정수(참/거짓 아님) 0~`EXTRA_MAX`, 아니면 400 `"늘린 정원은 0~5명 정수로 넣어 주세요."`; `calculate_department_quota(size, ratios, extra)`가 ValueError면 400 `"늘린 정원이 묶음 인원보다 많습니다."`.
  - `POST /api/eval/pools/back` 몸통에 선택 칸 `"extra": {"SA","B"}` — 있으면 이유(`back_reason`과 같은 글)와 함께 `extra_sa · extra_b · extra_reason · extra_at · extra_by · extra_behalf`를 쓴다(0 · 0이면 늘린 정원을 지우고 `extra_reason` 등은 NULL). 없으면 늘린 정원은 그대로. 거절 순서: 지금 순서(…묶음 상태 409 → 이유 400) 다음에 extra 모양 400. 감사로그 `EVAL_POOL_RETURNED`에 `extra=[sa, b]`(보냈을 때만).
  - `POST /api/eval/pools/extra` `{"year","pool","extra":{"SA","B"},"reason"}` — 대표이사가 직접 등급을 정하는 묶음(`_is_ceo(ctx, _pool_who(ctx, key))` — 전사 팀장 · 직속부서 · 본부장 없는 본부)의 정원을 늘린다. 거절 순서: 해 400 → 열쇠 400 → 묶음 없음 404 → 권한 403 `"이 묶음의 정원을 늘릴 권한이 없습니다."`(활성 대표이사, 또는 맡김이고 그 묶음에 들지 않은 HR — `_behalf_ok`, 그리고 배정자가 대표이사인 묶음) → 공개 뒤 409 → 묶음 상태가 HR · DONE 이면 409 `"대표이사 확인을 마친 묶음입니다."` → extra 400 → 늘린 정원이 하나라도 있으면 이유 1~200자 400 `"정원을 늘리는 이유를 1~200자로 써 주세요."`(0 · 0이면 이유 없어도 되고 지운다). 저장 · 감사로그 `EVAL_POOL_EXTRA` `{year, pool, extra:[sa, b]}`(HR이 대신이면 on_behalf_of = 대표이사) · 알림 없음 · 응답 `{"ok": true}`.
  - `_pool_auth(...)["extra"]` = 위 권한, `_pool_can`은 공개 전 · 상태가 HR · DONE 아님일 때 참(POOL_NEED에 `"extra": ("WAIT", "FIX", "CEO")`).
  - `_pool_json(...)["extra"]` = `{"SA": int, "B": int, "reason": str, "at": ISO, "by": 이름}` 또는 `None`(둘 다 0) — 배정자 · 대표이사 · HR에게만 가는 묶음 줄이라 이유 글을 넣어도 된다(돌려보낸 이유와 같다).
  - 역전 코멘트는 Task 3의 `_pool` 연결로 이미 마감(409 첫 오류) · 대표이사 확인(FIX)에 걸린다 — 여기서는 검사만 더한다.

- [ ] **Step 1: 실패하는 auth_check를 쓴다** (`check_eval_close`의 가상 묶음 D1 · D2 · LD · DR 사용):
  - 돌려보내기 + `extra {"SA":1,"B":0}` → 200 · `ev_pools` 칸 · 묶음 줄 `extra` · 정원 글자 끝 `" · 대표이사 허용 S·A +1명"` · 감사로그 `extra [1, 0]`(이유 글 없음) · 본부장이 그만큼 더 주고 마감 200.
  - extra 모양 400(`{"SA":-1}` · `6` · `True` · `1.5` · 글자 · 목록) · 묶음 인원보다 많음 400 · 이때 돌려보내기 자체도 안 됨(단계 · 기록 그대로).
  - 다시 돌려보내며 `extra {"SA":0,"B":0}` → 늘린 정원 지움. extra 없이 돌려보내면 그대로.
  - `pools/extra`: 대표이사가 LD · DR · D2(본부장 없음)에 200, D1(본부장 있음)은 403; 본부장 · 일반 HR(맡김 아님) 403; 맡긴 HR은 자기가 든 묶음 403 · 아닌 묶음 200(on_behalf_of); 확인한 묶음(HR) 409; 공개 뒤 409; 이유 없음 400; 0 · 0 이유 없이 200(지움).
  - 역전: 본부 팀원 묶음에서 높은 점수 C · 낮은 점수 A이고 코멘트 없음 → 마감 409(문구 그대로) · 코멘트 넣은 뒤 마감 200 · 묶음 줄 `warnings`에 경고 문구; 전사 팀장 묶음(LD)은 코멘트 없이도 대표이사 확인 200(경고만).
- [ ] **Step 2: 돌려 실패를 본다.**
- [ ] **Step 3: `evaluation.py`를 고친다** (모듈 설명의 끝점 목록 · 거절 순서 · 감사로그 목록에 `pools/extra` · `EVAL_POOL_EXTRA`).
- [ ] **Step 4: 통과를 본다** — `auth_check` 전부.
- [ ] **Step 5: 커밋** — `평가 서버: 대표이사 정원 늘려 주기 · 점수 역전 코멘트`.

### Task 5: 화면 — 비율 칸 · 정원 셈 · 가점 없앰 · 등급 안내

**Files:**
- Modify: `public/index.html` (평가 진행 · 평가 결과 영역 — `evRatio` · `evQuota` · `evQuotaText` · `evValidate` · `evRoster` · `evBoard` · `evRoundSave` · `evLiveLoad` · `evResLoad` · `evGradeTable` · `evScorePanel` · `evSelfCard` · `evResultCard` · `evBonus`/`evSaveBonus` · 시연 자료 · 자체 검사)

**Interfaces:**
- Consumes: 서버 `round.ratio` · 결과 `ratio` · 묶음 `extra` · 줄에서 빠진 `bonus` · `can.bonus`.
- Produces (Task 6이 쓴다):
  - `const evRatio = { SA:10, B:10, D:10 }` — 시연은 HR이 진행표에서 바꾼다(평가 시작 전까지), 실제 로그인은 `evLiveLoad`가 `round.ratio`로 채운다.
  - `evQuota(n, extra={ SA:0, B:0 })` → `{ mode, A, B, C, D, capS }` 또는 `{ mode, upper, lower, capS }` — Task 2와 같은 셈(`evRatio` 사용), 인원을 넘으면 `null`.
  - `evQuotaText(q, extra)` → Task 3 `_pool_json.quota`와 같은 글자.
  - `evValidate(roster, assign, quota, full, needNote=true)` — roster 줄 `{ pid, name, score, kpi, blocked, note }`, Task 2와 같은 역전 줄(문구 그대로) · 정원은 받은 `quota`로.
  - `evQuotaWord(k)` → `'본부'`(본부 팀원) · `'전사 팀장'` · `'직속부서'`; `evRatioText()` → `` `S·A ${SA}% · B ${B}% · D ${D}% · C 나머지` ``.
- 화면 글자(그대로):
  - 진행표(기준 점수 칸 밑, 새 `field-row`): 라벨 `등급별 비율 — 묶음 인원의 몇 %까지 줄 수 있음 (반올림 · C는 나머지 · 5명 이상 묶음)`, 칸 `S·A` · `B` · `D` 각각 숫자 칸 + `% 까지`, 뒤에 `C 나머지` 표시, 아래 두 줄 `` `예: 묶음 10명 → S·A ${a}명 · B ${b}명 · D ${d}명 · C ${c}명 / 20명 → S·A ${a}명 · B ${b}명 · D ${d}명 · C ${c}명` `` · `S는 S·A 정원 안에서 줘요 (S를 주면 A가 줄어요) · 세 칸 합은 70%까지 · 4명 이하 묶음은 S·A·B 합쳐 1명 · 평가를 시작하면 바꿀 수 없어요`. 평가가 시작되면 칸이 `disabled`(제목 `평가가 시작되어 바꿀 수 없어요`). 실제 로그인은 바꾼 값을 `EV_LIVE.typed`의 `'r-SA'` · `'r-B'` · `'r-D'`에 두고 [마감일 저장]이 `ratio`로 함께 보낸다(바뀐 값 알림 줄 `바꾼 날짜 · 기준 점수 · 비율은 <b>마감일 저장</b>을 눌러야 저장돼요.`); 시연은 바로 바꾸고 합이 70을 넘으면 `등급 비율은 세 칸 합이 70%를 넘을 수 없습니다.`(빨강, 저장 안 함).
  - 정원 줄(`evGradeTable`의 `ev-quota`)은 `evQuotaText(evQuota(n, extra), extra)`.
  - ② 평가자 점수 화면(`evScorePanel`, 점수를 내는 중일 때만 — 표 아래): `` `최종 등급은 ${evUpper(p)}${kpiGa(evUpper(p))} ${evQuotaWord(k)} 정원(올해 ${evRatioText()}) 안에서 정해요. 등급 의견은 점수로 받을 수 있는 가장 높은 등급이에요.` `` (KPI 없는 사람도 같은 줄).
  - ① 자기평가 화면(`evSelfCard`, 증빙 안내 줄 밑): `` `등급은 점수 기준과 ${evQuotaWord(k)} 정원(올해 ${evRatioText()})으로 정해요. 점수 기준(S 100 · A ${A} · B ${B} · C ${C}점 초과)은 받을 수 있는 가장 높은 등급이에요.` ``
  - ① 결과 화면(`evResultCard`, 올해 결과 · 등급이 정해진 뒤 · 지난 결과 아님): 같은 규칙 줄(결과 해의 비율 — 실제 로그인은 결과 응답 `ratio`) + KPI 있는 사람이고 `evAbove(cap, 등급)`이면 `` `점수로는 ${cap}까지 가능했고, ${evQuotaWord(k)} 정원에 따라 ${등급}로 정해졌어요.` `` (cap = 실제 로그인 줄 `cap`, 시연 `evCap(p)`).
- 가점 없앰: 가점 단추 · 창 칸 · `+ 가점` 글자 · 결과 표 가점 줄 · 총점 설명의 가점을 모두 뺀다. `evBonus`/`evSaveBonus`는 **윗사람 코멘트 창** `evHeadAsk(pid)`/`evHeadSave(pid)`로 바꾸고 단추 글자는 `코멘트`(전사 팀장 묶음은 윗사람 코멘트가 없어 단추 없음). `evScore(rows)`는 가점 인자를 없앤다. 시연 자료의 가점 값은 지운다.

- [ ] **Step 1: 자체 검사에 실패하는 줄을 쓴다** (`evSelfCheck` · `evLiveSelfCheck` · `evrSelfCheck` 중 맞는 곳, 끝나면 `evRatio` 되돌림):
  - `evQuota` 가 Task 2 selfcheck의 숫자들과 같다(비율 · 늘린 정원 · 넘으면 null), 기본값은 0~60명 모두 옛 셈과 같다.
  - `evQuotaText` 가 Task 3 · 4의 서버 글자와 같다(늘린 정원 있음 · 없음, 두 모드).
  - `evValidate` 역전: Task 2의 세 경우와 동점 · 점수 없음 · 연차 초과가 같은 결과 · 같은 문구.
  - 진행표: 비율 칸 셋 · 예시 줄 숫자(`20/10/10` → 10명 2 · 1 · 1 · 6), 시작 뒤 `disabled`, 실제 로그인 [마감일 저장] 몸통에 `ratio`(가짜 fetch로 잡는 이웃 검사처럼).
  - 평가자 화면 · 자기평가 화면 · 결과 화면 안내 글자(본부장 · 대표이사, 결과 줄은 cap > 등급일 때만).
  - 가점: 화면 어디에도 `가점` 글자가 없다(평가 진행 · 평가 결과 탭을 역할마다 그려 `contentArea` 글자에서 찾는다), `코멘트` 창이 `headComment`만 보낸다.
- [ ] **Step 2: 돌려 실패를 본다** — `node scratchpad/ff_run.js scratchpad/kf_checks.js`(정적 서버 8812).
- [ ] **Step 3: `index.html`을 고친다** (규칙 설명 주석 블록(`평가 진행` 머리 주석)의 정원 · 가점 줄도 고친다).
- [ ] **Step 4: 통과를 본다** — 화면 자체 검사 전부 · `.ci` 검사 · `auth_check`(화면 글자를 읽는 검사가 있으면).
- [ ] **Step 5: 커밋** — `평가 화면: 등급 비율 칸 · 정원 셈 · 가점 없앰 · 등급 안내 (실제 로그인 · 시연)`.

### Task 6: 화면 — 정원 늘려 주기 · 점수 역전 · 보고서

**Files:**
- Modify: `public/index.html` (`evReturnAsk` · `evReturn` · `evPoolBack` · 새 `evExtraAsk` · `evExtraSet` · `evHeadCard` · `evGradeTable` · `evRoster` · `evPoolLog` · `evLiveLoad`(묶음 extra) · 보고서 시연 몸통 · 자체 검사)

**Interfaces:**
- Consumes: Task 4 `pools/back` `extra` · `pools/extra` · 묶음 `extra` · `can.extra`; Task 5 `evQuota` · `evQuotaText` · `evValidate(…, needNote)`.
- Produces: 시연 `evPoolLog[k].extra = { SA, B, reason, at, by } | null`(실제 로그인은 묶음 `extra`를 같은 모양으로 — `at`은 한국 날짜).
- 화면 글자(그대로 — 시안 1 · 2):
  - 돌려보내기 창(본부 팀원 묶음, 본부장 있음): 이유 칸 밑에 라벨 `정원 늘려 주기 (필요할 때만)`, `S·A 지금 {n}명 → [+0명 ~ +k명]` · `B 지금 {n}명 → [+0명 ~ +k명]`(고르기 칸은 지금 늘린 값이 처음 값, k는 `evQuota`가 null이 아닌 데까지 · 5까지), 설명 `올해 이 본부만 늘어나요 · 본부장이 그만큼 더 주고 다시 마감해요 · 점수 기준과 연차 규칙은 그대로예요 · 늘린 정원과 이유는 HR 화면 · 결과 보고서에 남아요`. 보내면 `extra`를 함께(실제 로그인 `pools/back`, 시연 `evPoolBack(k, reason, by, extra)`).
  - 본부장 배정 카드의 돌려보낸 줄 끝: 늘린 정원이 있으면 `` ` · <b>S·A 정원 +${x}명 허용</b> (올해 이 본부만)` `` (B면 `B 정원 +y명`, 둘 다면 ` · ` 로 이어서).
  - 대표이사가 직접 정하는 묶음(전사 팀장 · 직속부서 · 본부장 없는 본부)의 등급 표 정원 줄 끝에 작은 단추 `정원 늘리기`(실제 로그인 `can.extra`, 시연 대표이사 또는 대신 입력 HR · 확인 전) → 창 제목 `정원 늘리기 · {묶음 이름}`, 같은 고르기 칸 둘 + 이유 칸(라벨 `늘리는 이유 (HR에게 보입니다)`) + [저장] — 실제 로그인 `pools/extra`, 시연은 `evPoolLog[k].extra`에 바로. 성공 글 `정원을 바꿨어요.`
- 점수 역전: `evRoster`가 줄마다 `note`(윗사람 코멘트 있음)를 넣고, `evGradeTable`의 검사(`evValidate`)가 `needNote = kind!=='lead'` — 오류는 빨간 줄(마감 단추가 막힌다), 경고는 노란 줄. 실제 로그인은 서버 `errors` · `warnings` 그대로.
- 보고서 시연 몸통: 묶음 정원 글자를 Task 3 서버 글자와 같게(`S·A {a}명 · B {b}명 · D {d}명` · 늘린 정원 ` (대표이사 허용 +{n}명)`)하고 `ratio`를 보낸다.

- [ ] **Step 1: 자체 검사에 실패하는 줄을 쓴다:** 돌려보내기 창의 고르기 칸 숫자(5명 묶음 기본 비율이면 S·A +0~+2 — 넘는 숫자 없음), 실제 로그인 `pools/back` 몸통 `{year, pool, reason, extra:{SA:1,B:0}}`(가짜 fetch), 시연 돌려보내기 뒤 `evPoolLog.div.extra` · 본부장 카드 글자 · 정원 줄 글자, `정원 늘리기` 단추가 대표이사 직접 묶음에만 · 본부장 있는 본부엔 없음, `pools/extra` 몸통, 역전 오류면 마감 단추 `disabled` · 코멘트 넣으면 풀림(시연), 보고서 몸통 `ratio` · 정원 글자.
- [ ] **Step 2: 돌려 실패를 본다.**
- [ ] **Step 3: `index.html`을 고친다.**
- [ ] **Step 4: 통과를 본다** — 화면 자체 검사 전부 · `.ci` 검사 · `auth_check`.
- [ ] **Step 5: 커밋** — `평가 화면: 대표이사 정원 늘려 주기 · 점수 역전 · 보고서 정원 글자`.

### Task 7: 문서 · 전체 검사

**Files:**
- Modify: `CLAUDE.md` (평가 항목: 비율 · 늘린 정원 · 가점 없음 · 역전 · 안내 · `pools/extra` · 판 23 · 알려진 한계 "D가 점수로 정해지는 사람이 D 정원보다 많으면 마감이 막힌다 — 인사담당자 확인 중")
- Modify: `docs/superpowers/specs/2026-09-21-kpi-goal-screen-design.md` 8장 가점 · 정원 줄에 "2026-10-27 바뀜" 한 줄씩(옛 글은 지우지 않고 바뀐 것을 적는다)

- [ ] **Step 1: 문서를 고친다.**
- [ ] **Step 2: 전체 검사** — Global Constraints의 검사 명령 전부 · 화면 자체 검사 전부 · `git diff --stat 4fe9302` 로 바뀐 파일이 이 계획의 파일뿐인지.
- [ ] **Step 3: 커밋** — `문서: 평가 규칙 손질 (비율 · 정원 늘려 주기 · 가점 없앰 · 안내 · 역전)`.
