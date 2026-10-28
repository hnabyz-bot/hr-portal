# 팀장 평가 채점 성향 — 평가자 채점 성향 표 · 보정 점수 · 본부장 자기 성향 · 팀장 묶음 역전은 보정 점수로 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 전사 팀장 묶음에서 본부장마다 후하게 · 짜게 준 정도(채점 성향)를 대표이사 · HR이 숫자로 보고, 그만큼 덜어 낸 보정 점수로 팀장들을 비교하게 한다. 본부장은 팀장 점수를 매길 때 자기 성향만 본다 — 실제 로그인과 시연 화면 모두.

**Architecture:** 서버는 응답에 새 칸을 내지 않는다 — 표 숫자는 화면이 보는 사람에게 이미 오는 줄(낸 점수 `subScore` · 낸 항목 등급 `subMarks` · 평가자 `rater`)로 센다. 서버가 바꾸는 것은 정원 검사 한 곳: 전사 팀장 묶음의 점수 역전 경고를 총점이 아니라 보정 점수로 비교한다(`eval_rules.adjusted` · `check_pool(calibrated=True)`). 시연 화면의 `evValidate`도 같은 셈 · 같은 문구.

**Tech Stack:** Python 3 + Flask + SQLite(`backend/`), 단일 HTML · 바닐라 JS(`public/index.html`), 검사 `python -m hr_eval.selfcheck` · `python -m auth_check` · `.ci/*` · 화면 자체 검사(`scratchpad/kf_checks.js`).

**Spec (인사담당자 결정, 2026-10-28 대화 — 이 계획의 근거):**
- 걱정: "본부장이 다 높은 등급으로 채점해 대표이사에게 보내면 대표이사의 주관적 판단이 많이 들어간다." 본부장 순위 매기기는 "한 본부 팀장이 2~4명이라 의미가 없다"며 빼고, 본부장이 준 점수의 평균을 넓히기로.
- **C1 평가자 채점 성향 표** (질문 1 = A) — 대표이사(또는 맡김 HR) · HR이 여는 '전사 팀장 명단' 위에 평가자마다: 채점한 팀장 수 · 평균 총점 · 전사 평균과 차이(5점 이상이면 '높게 줌' · '낮게 줌') · 실행목표 등급 중 S·A 비율, 맨 아래 전사 줄.
- **C2 보정 점수** (질문 1 = A) — 팀장 줄마다 보정 점수 = 총점 − (그 평가자 평균 − 전사 평균), [총점 순 / 보정 점수 순] 줄 세우기.
- **C3 추천 등급은 채우지 않는다** (질문 2 = B) — 숫자만 보이고 등급은 지금처럼 대표이사가 정한다(평가자 의견이 먼저 들어가 있는 것도 그대로).
- **C4 본부장은 자기 성향만** (질문 3 = A) — 팀장 점수를 매기는 화면에 "내 채점 성향" 한 줄. 다른 본부의 숫자는 공개 전 · 뒤 모두 보이지 않는다.
- **C5 팀장 묶음 점수 역전 경고는 보정 점수로 비교** ("a로 부탁해") — 경고만(지금처럼 막지 않음, 팀장 묶음에는 윗사람 코멘트 칸이 없다).
- 시안 그림: `클로드 코드/뉴디자인/팀장 평가 채점 성향 - 시안 1 대표이사 전사 팀장 (2026-10-28).png` · `… 시안 2 본부장 점수 화면 (2026-10-28).png` (인사담당자 승인).
- 내 판단(인사담당자에게 알림, 틀리면 숫자 · 한 줄): 셈은 전사 팀장 묶음만 · 평가자가 **낸** 점수로(대표이사가 고친 점수는 성향에 안 듦) · '높게 줌 / 낮게 줌' 기준 5점 · 줄 수 있는 가장 높은 등급(점수 기준 · 연차)은 지금처럼 **총점**으로.

## Global Constraints

- 갈래 `feature/eval-calib`(작업 폴더 `scratchpad/wt-rules`, `feature/eval-rules` 3b7c2c8 위). **push 금지** — 인사담당자의 "올려줘" 뒤에만. merge는 "merge 해줘"로만. DB 판은 그대로(23) — 새 SQL 파일 없음.
- 커밋 · 코드 · 시험에 실명 금지(가상 이름만 — 이웃 검사처럼 가상본부장 · 가상팀장 등). 휴대폰 번호 모양 글자 금지.
- 작업 폴더 파일은 **CRLF**. 고칠 때는 `scratchpad/crlf_patch.py`의 `apply(path, [(old, new)])`(옛 글자가 정확히 한 번 있어야 한다).
- **서버 응답 · 감사로그 · 알림에 새 칸 · 새 글을 더하지 않는다** — 성향 숫자는 화면이 보는 사람이 이미 받는 줄로만 센다(`_row_json` 그대로). 본부장은 자기가 평가자인 줄만 받으므로 다른 본부 숫자를 셀 수 없다.
- 새 글자는 이 계획이 적은 그대로. 화면 새 글은 "~해요"체(시연 · 실제 로그인 같게), 서버 문구는 `…습니다.`.
- 검사는 모두 통과한 채로 커밋한다: `cd backend && python -m hr_eval.selfcheck && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features && python -m auth_check` · 저장소 뿌리에서 `for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py` · 화면 자체 검사(`kf_checks.js` — FAIL 없음).

## 셈 (서버 · 화면 같게 — 정수와 분수로, 떠돌이 소수 없이)

- **셈에 드는 사람**: 전사 팀장 묶음에서 제외 안 됨 · KPI 있음 · 평가자가 낸 점수 있음(서버 `sub_score_x10`, 화면 `subScore`). 그 밖의 사람은 표 · 보정 점수 · 역전 검사에서 빠진다(보정 점수 칸 `—`).
- **평가자 열쇠**: 서버 `rater_id`(NULL은 NULL끼리), 화면 실제 로그인 `p.rater?.id ?? null`, 시연 `evEvaluatorName(p)`. 이름 글자는 `evEvaluatorName(p)`.
- 낸 점수 10배 정수 s. 평가자 r의 합 S_r · 수 n_r, 전사(셈에 드는 사람 모두) 합 S · 수 N. 지금 총점 10배 정수 T(서버 `_total` · 화면 `evTotal`).
- 평균(10배) = S_r / n_r · 차이(10배) = (S_r·N − S·n_r) / (n_r·N) · **보정 점수(10배) = (T·n_r·N − S_r·N + S·n_r) / (n_r·N)**.
- 비교는 분수 그대로(서버 `fractions.Fraction`, 화면 a.num·b.den ↔ b.num·a.den 정수 곱 — 수가 작아 정확하다).
- 글자 반올림(0.1점 사사오입, 0에서 먼 쪽): 10배 분수 num/den(den > 0) → q = sign(num) · ⌊(2·|num| + den) / (2·den)⌋ → q/10.
  - 표 · 보정 점수 칸: 소수 한 자리 늘 (`88.7`, `90.0`). 차이 · 보정 칸 아래: 부호 + 소수 한 자리 — q > 0 `+`, q < 0 `−`(U+2212), 0은 부호 없이 `0.0`.
  - 서버 · 시연 역전 문구의 숫자: 지금 문구의 `_num` 모양(`90.0` → `90`, `88.5` → `88.5`, 음수는 `-`).
- '높게 줌' = 차이 ≥ 5점(S_r·N − S·n_r ≥ 50·n_r·N), '낮게 줌' = 차이 ≤ −5점.
- 실행목표 등급 중 S·A = 셈에 드는 사람들의 낸 항목 등급(서버 `sub_marks` · 화면 `subMarks`)에서 값이 있는 것 중 S · A의 수 — `Math.round(100·sa/items)`%, items 0이면 `—`.

## Review Focus

1. **평가자가 한 명뿐인 해**(모든 팀장을 한 사람이 채점) — 차이 0, 보정 점수 = 총점, 역전 경고는 총점 역전과 같은 사람 · 같은 숫자(문구만 '보정 점수'). (Task 1 selfcheck · Task 2 화면 검사)
2. **아직 점수를 안 낸 팀장 · KPI 없는 팀장 · 제외된 팀장** — 셈 · 역전에서 빠지고 화면은 `—`로 그리며 멈추지 않는다(전원이 빠지면 표 대신 "아직 점수를 낸 평가자가 없어요."). (Task 1 · 2)
3. **대표이사가 팀장 점수를 고친 뒤** — 성향(평균 · 차이 · S·A)은 낸 점수 그대로, 보정 점수는 지금 총점 − 차이. (Task 1 selfcheck의 85 ≠ 84 줄 · Task 2)
4. **본부장이 받는 줄** — 실제 로그인에서 본부장 화면의 "내 채점 성향"은 자기가 평가자인 전사 팀장 줄만으로 센다(다른 줄이 와도 세지 않는다 — 가짜 응답에 남의 줄을 섞어 확인). 서버 응답 칸 이름 · 수는 그대로. (Task 2)
5. **동점 보정 점수** — 역전으로 치지 않는다(분수 그대로 비교해 반올림 글자가 같아도 다르면 다르다). (Task 1)

---

### Task 1: 규칙 · 서버 — 보정 점수와 팀장 묶음 역전 (C5)

**Files:**
- Modify: `backend/hr_eval/domain/eval_rules.py` (모듈 설명 한 줄 · `adjusted` · `adj_text` · `Member.adj` · `check_pool(calibrated=…)`)
- Modify: `backend/evaluation.py` (`_pool` — 전사 팀장 묶음만 · 모듈 설명의 묶음 마감 · 전원 검사 줄에 "전사 팀장 묶음의 역전은 보정 점수로 · 경고만")
- Test: `backend/hr_eval/selfcheck.py`, `backend/auth_check.py`

**Interfaces:**
- Produces:
  - `adjusted(rows: Sequence[tuple[object, int | None, Decimal | None]]) -> list[Fraction | None]` — rows = (평가자 열쇠, 낸 점수 10배 또는 None, 지금 총점 또는 None), 같은 차례로 보정 점수(점 단위 Fraction). 낸 점수 None 인 줄은 셈에 안 들고 None. 총점 None 이면 None.
  - `adj_text(x: Fraction) -> str` — 위 "글자 반올림" → `_num` 모양.
  - `Member.adj: Fraction | None = None` (맨 끝 칸 — 이미 쓰는 곳은 그대로).
  - `check_pool(..., calibrated: bool = False)` — 참이면 역전 줄이 `adj`로 비교: `rated` = 등급 있고 adj 있는 사람, `y.adj < x.adj`, y 고르기 열쇠 `(GRADES.index(y.grade), -y.adj)`, cap 은 지금처럼 `cap(x.score, …)`. 문구 `f"{x.name} 님(보정 점수 {adj_text(x.adj)}점)이 보정 점수가 더 낮은 {y.name} 님({adj_text(y.adj)}점)보다 등급이 낮습니다."` — need_note 이고 note 없으면 오류(+ 지금 꼬리 문장), 아니면 경고(전사 팀장 묶음은 need_note 거짓이라 늘 경고). 거짓이면 지금 그대로.
  - `evaluation._pool`: key 가 `"LEADERS"` 이면 `adjusted([(r["rater_id"], r["sub_score_x10"] if r["kpi_version"] is not None else None, _total(ctx, r)) for r in rows])`로 Member.adj 를 채우고 `calibrated=True`. 그 밖의 묶음은 그대로.

- [ ] **Step 1: 실패하는 selfcheck** — `보정_점수`: rows = `[("a", 900, D("90")), ("a", 960, D("96")), ("b", 800, D("80")), ("b", 840, D("85")), ("c", None, D("70"))]` → `[Fraction(169, 2), Fraction(181, 2), Fraction(171, 2), Fraction(181, 2), None]`(전사 87.5 · a +5.5 · b −5.5 · 넷째 줄은 낸 84 · 지금 85). 평가자 하나(`[("a", 900, D("90")), ("a", 800, D("80"))]`) → `[Fraction(90), Fraction(80)]`. `adj_text`: `Fraction(1774, 20)` → `"88.7"`, `Fraction(2603, 30)` → `"86.8"`, `Fraction(1735, 20)` → `"86.8"`, `Fraction(90)` → `"90"`, `Fraction(-1, 20)` → `"-0.1"`.
  `팀장_묶음_역전은_보정_점수로`: 위 넷을 Member 로(이름 가상팀장1~4, 연차 0) — 가상팀장4(총점 85 · 보정 90.5) C · 가상팀장1(총점 90 · 보정 84.5) B · 가상팀장2(96 · 90.5) A · 가상팀장3(80 · 85.5) C, `need_note=False, calibrated=True, full=False` → 경고에 `"가상팀장4 님(보정 점수 90.5점)이 보정 점수가 더 낮은 가상팀장1 님(84.5점)보다 등급이 낮습니다."` · `"가상팀장3 님(보정 점수 85.5점)이 보정 점수가 더 낮은 가상팀장1 님(84.5점)보다 등급이 낮습니다."`는 없다(가상팀장3은 총점 80이라 B를 못 받는다 — cap 은 총점) · 가상팀장2 · 가상팀장4 동점(90.5)끼리는 역전 아님. 같은 넷을 `calibrated=False`로 → 역전 줄 없음(총점으로는 가상팀장4 85 < 가상팀장1 90). 평가자 하나일 때 `calibrated=True`와 `False`의 역전 사람 · 숫자가 같다(문구만 다름).
- [ ] **Step 2: 돌려 실패를 본다.**
- [ ] **Step 3: `eval_rules`를 고친다** (위 Interfaces).
- [ ] **Step 4: 실패하는 auth_check** — 이웃 평가 검사처럼 가상 회사에서 두 본부의 본부장이 각자 팀장을 채점(한 본부장은 높게, 한 본부장은 낮게)해 전사 팀장 묶음이 대표이사 확인 단계에 온 뒤, 대표이사 상태 응답의 전사 팀장 묶음 `warnings`에 보정 점수 역전 문구(숫자까지 기대값으로)가 있고 `"총점이 더 낮은"`은 없다. 같은 응답의 사람 줄 열쇠 묶음이 이번 작업 전과 같다(새 칸 없음 — 칸 이름 목록을 기대값으로).
- [ ] **Step 5: `_pool`을 고치고 통과를 본다** — selfcheck · auth_check 전부.
- [ ] **Step 6: 커밋** — `평가 규칙: 전사 팀장 묶음 점수 역전은 보정 점수로`.

### Task 2: 화면 — 채점 성향 표 · 보정 점수 · 본부장 자기 성향 (C1~C5, 시연 · 실제 로그인)

**Files:**
- Modify: `public/index.html`
- Modify: `CLAUDE.md` (평가 규칙 손질 항목 아래 "팀장 평가 채점 성향 (2026-10-28)" 한 항목 · 시연 항목 한 줄), `docs/superpowers/specs/2026-09-21-kpi-goal-screen-design.md` 8장 표에 `| **2026-10-28 바뀜 — 팀장 채점 성향** | … |` 한 줄
- Test: 화면 자체 검사(이웃 `evSelfCheck` · `evLiveSelfCheck` 등 — 평가 화면 검사를 모은 곳에 더한다)

**Interfaces:**
- Consumes: Task 1의 셈 · 문구(서버 `adjusted` · `adj_text` · 역전 문구와 같은 숫자 · 같은 글자).
- Produces:
  - `evCalib(list) -> { raters: [{ key, name, divs, n, sum, items, sa }], n, sum, items, sa, adj(p) -> { num, den } | null }` — list 는 전사 팀장 묶음 사람들(evPoolList), 위 "셈" 그대로. raters 차례: 차이가 큰 평가자부터(분수 비교), 같으면 이름.
  - `evCalibText(num, den, signed)` — 위 "글자 반올림"(signed 면 `+` · `−` · 부호 없는 0).
  - `evCalibCard(k)` — 표 HTML(아래 글자). `evGradeTable` 이 `evKind(k)==='lead'` 이고 mode 가 `'ceo'` · `'hr'` 일 때 `.ev-quota` 다음에 넣는다.
  - `evCalibSort` — `'adj'`(처음 값) | `'total'`, 화면 상태(저장 안 함).
  - `evValidate(roster, assign, quota, full, needNote=true, calibrated=false)` — roster 줄에 `adj: { num, den } | null`. calibrated 면 Task 1과 같은 역전 셈 · 문구. `evGradeTable` · `evPoolCheck`(시연)는 전사 팀장 묶음에 `calibrated=true` 와 `evCalib` 의 adj 를 넘긴다. 실제 로그인은 서버 warnings 그대로.
- 글자 (그대로):
  - 표 제목 줄: `<b>평가자 채점 성향</b> 평가자가 낸 점수로 계산해요 · 평가자마다 팀장이 2~4명이라 실행목표 등급 비율을 함께 봐요`
  - 머리 칸: `평가자` · `채점한 팀장` · `평균 총점` · `전사 평균과 차이` · `실행목표 등급 중 S·A`. 평가자 칸 아래 작은 글자 = 그 평가자가 채점한 사람들의 본부 이름(겹치지 않게 ` · `). 칸 글자: `{n}명` · `{평균}점` · `{차이}점` + (`높게 줌` badge warning | `낮게 줌` badge neutral) · `{pct}% ({items}개 중 {sa}개)`. 전사 줄: `전사` · `{N}명` · `{평균}점` · `—` · 같은 모양.
  - 표 밑: `<b>보정 점수</b> = 총점 − (그 평가자 평균 − 전사 평균). 평가자가 높게 준 만큼 빼고 낮게 준 만큼 더한 참고 점수예요. 줄 수 있는 가장 높은 등급(점수 기준 · 연차)은 지금처럼 총점으로 정해지고, 등급은 대표이사가 정해요.`
  - 셈에 드는 사람이 없으면 표 대신: `아직 점수를 낸 평가자가 없어요.`
  - 줄 세우기: `줄 세우기` + 단추 `총점 순` · `보정 점수 순`(고른 것 `btn sm primary` · `aria-pressed="true"`).
  - 명단 머리에 `보정 점수` 칸(총점 다음, `num`): `<b>{보정}</b>` + 아래 작은 글자 `{−차이 부호 글자}`(예 `+8.0`), 셈에 안 들면 `—`. 보정 점수 순이면 보정 점수가 큰 사람부터(분수 비교), 없는 사람은 뒤에 총점 순. 펼친 줄(상세)의 colspan 은 칸 수에 맞게.
  - 본부장 줄 — `evQuotaNote(p)`가 전사 팀장 묶음 사람이고 보는 사람이 평가자이며 대표이사가 아닐 때(실제 로그인 `!p.raterIsCeo`, 시연 `kpiRole!=='ceo'`) 그 아래 `kpiNote(…)`:
    - 낸 팀장 n ≥ 1: `<b>내 채점 성향</b> · 지금까지 점수를 낸 팀장 {n}명 · 평균 총점 {평균}점 · 실행목표 등급 {items}개 중 S·A {sa}개({pct}%). 대표이사는 평가자마다 이 숫자를 전사 평균과 나란히 봐요. 다른 본부의 숫자는 보이지 않아요.`
    - n = 0: `<b>내 채점 성향</b> · 아직 점수를 낸 팀장이 없어요. 대표이사는 평가자마다 낸 점수의 평균을 전사 평균과 나란히 봐요.`
    - 센 줄 = 전사 팀장 묶음에서 그 평가자(위 평가자 열쇠가 p 와 같은 사람)의 셈에 드는 줄. 실제 로그인은 받은 줄 중 `p.rater?.id` 가 같은 것만.

- [ ] **Step 1: 실패하는 화면 자체 검사** — ① `evCalib`: Task 1 selfcheck 넷(시연 사람 넷에 낸 점수 · 지금 총점 · 평가자를 넣는다)의 보정 점수 글자 `84.5` · `90.5` · `85.5` · `90.5`, 차이 글자 `+5.5` · `−5.5`, 평균 `93.0` · `82.0`, 전사 `87.5`. ② `evValidate` 전사 팀장 calibrated 경고 문구가 Task 1의 기대 문구와 글자까지 같다. ③ CEO · HR 명단에 표 · `보정 점수` 칸 · 줄 세우기가 있고, 단추로 차례가 바뀐다(첫 줄 이름). ④ 본부장 줄: 낸 팀장 0명 · 2명 글자. ⑤ 실제 로그인(가짜 EV_LIVE): 대표이사 표가 rater.id 로 묶이고, 본부장 줄은 남의 줄을 섞어 넣어도 자기 줄만 센다. ⑥ 셈에 드는 사람이 없을 때 표 대신 안내 글 · 멈추지 않음.
- [ ] **Step 2: 돌려 실패를 본다.**
- [ ] **Step 3: 화면을 고친다** (위 Interfaces · 글자).
- [ ] **Step 4: 통과를 본다** — 화면 자체 검사 전부 · `.ci` · check-html · selfcheck · auth_check.
- [ ] **Step 5: 문서** — CLAUDE.md · 설계 8장 한 줄(위 C1~C5 · 내 판단 넷 · 서버 응답 새 칸 없음).
- [ ] **Step 6: 그림** — 시연 대표이사 '전사 팀장 명단'(점수를 시안처럼 높고 낮게 넣고 찍기) · 시연 본부장 팀장 점수 화면을 `.superpowers/sdd/2026-10-28-eval-leader-calibration/t2-*.png`로.
- [ ] **Step 7: 커밋** — `평가 화면: 팀장 평가 채점 성향 표 · 보정 점수 · 본부장 자기 성향`.
