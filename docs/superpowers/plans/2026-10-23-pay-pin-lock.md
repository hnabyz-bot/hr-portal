# 급여·계약 비밀번호 잠금 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 급여·계약 메뉴(급여명세서 · 전자연봉계약서 · 원천징수영수증 · HR 화면)를 본인이 정한 숫자 6자리로 잠그고, 잠긴 동안에는 서버가 금액·본문·PDF를 하나도 보내지 않게 한다. 1분 쉬면 가리고 5분 쉬면 다시 잠근다.

**Architecture:** 서버에 `backend/paypin.py` 하나(규칙 · 해시 · 세션별 풀림 · 길 · 숫자 · 초기화 · 명령)를 두고, `auth._guard`가 세션 검사 끝에 `paypin.gate()`를 한 번 부른다 — 세 앞머리(`/api/payslips` · `/api/withholding` · `/api/contracts`)로 오는 요청은 풀린 세션이 아니면 423. 메뉴 숫자는 잠그지 않는 `GET /api/pay/counts`로 받는다(휴대폰 알림 `push.pending()`도 같은 함수). 화면은 `renderContent()`가 급여·계약 메뉴에서 `pinGated()`이면 탭 내용 대신 잠금 창을 그리고, 풀린 뒤에는 띠 · 1분 가림 · 5분 잠금을 맡는다.

**Tech Stack:** Flask 3 · SQLite · 표준 라이브러리 `hmac`/`secrets`(새 의존성 없음) · 기존 검사 하네스(`backend/auth_check.py`, 화면 `*SelfCheck()`, `cdp_check.mjs`, `devlive.py`).

**Spec:** `docs/superpowers/specs/2026-10-22-pay-pin-lock-design.md` (승인 2026-10-22) · 시안 `클로드 코드/뉴디자인/급여 비밀번호 잠금 시안/` (그림 1~8 + `시안.html`, 저장소 밖)

**바탕:** 휴대폰 알림 PR #179(`feature/push-notifications`, 표 017)가 먼저 main에 들어간다. 구현은 그 뒤 최신 main에서 `feature/pay-pin` 브랜치 — 이 계획 · 설계 커밋을 가져온다. #179가 merge 전에 바뀌면 시작 전에 `push.pending()`의 p1~p3 부분을 다시 본다.

## Global Constraints

- 잠그는 길: `request.path`가 `paypin.PREFIXES = ("/api/payslips", "/api/withholding", "/api/contracts")` 중 하나로 시작하는 모든 요청(모든 method, HR 길 · 본부장 `/api/contracts/division` 포함). 이 목록은 이 한 곳에만 둔다.
- 잠그지 않는 길(로그인 필요, POST는 기존 같은 출처 검사): `GET·POST /api/pay/lock` · `POST /api/pay/pin` · `POST /api/pay/unlock` · `POST /api/pay/alive` · `GET /api/pay/counts` · `POST /api/members/<id>/pay-pin/reset`(HR).
- 잠긴 응답: **423** + `{"error": "급여·계약 메뉴는 비밀번호를 넣어야 볼 수 있어요.", "lock": "set" | "ask" | "locked"}` — 다른 칸 없음.
- 시간: 화면 가림 **60초** · 화면 잠금 **300초** · 서버 풀림 **360초**(마지막 급여·계약 사용부터) · alive는 **60초에 한 번 이하** · 화면 타이머 5초 간격. 5분을 세는 움직임은 포털 어디서든의 움직임이다.
- 틀림: 사람마다 연속 **5번** → 잠김(`pay_pins.fails`, 새 창으로 되돌릴 수 없다). 잠긴 사람은 맞아도 풀리지 않고, **이미 풀린 그 사람의 다른 창도 잠긴다**(내 판단 — PR에 적는다).
- 해시: `pin_hash = HMAC-SHA256(k, salt + pin).hexdigest()`, `k = HMAC-SHA256(HR_PAY_KEY의 32바이트, b"hr-pay-pin").digest()`, `salt = secrets.token_hex(16)`, 비교는 `hmac.compare_digest`. 원문은 DB · 로그 · 감사로그 어디에도 없다.
- 쓸 수 없는 번호(문구 그대로): 6자리 숫자 아님 `숫자 6자리로 정해 주세요.` · 두 번이 다름 `두 번 입력한 비밀번호가 달라요.` · 같은 숫자 6개 `같은 숫자 6개는 쓸 수 없어요.` · `0123456789`나 `9876543210` 안의 6자리 `이어지는 숫자는 쓸 수 없어요.` · 구성원 설정 생일(`users.birthday` `MM-DD`)의 `MMDD`가 들어감 `생일(월일)이 들어간 번호는 쓸 수 없어요.`
- 열쇠(`HR_PAY_KEY`)가 없을 때(내 판단 — 잠근 자료가 열쇠 없이는 없으므로): `GET /api/pay/lock` → `{"state": "off"}`, `gate()`는 통과(지금과 같은 "열쇠 없음" 안내가 그대로 보인다), `POST /api/pay/pin` · `unlock` → 503 `서버에 급여 잠금 열쇠(HR_PAY_KEY)가 없어 비밀번호를 쓸 수 없습니다. 서버 설정을 먼저 마쳐 주세요.`
- 감사로그: `PAY_PIN_SET` · `PAY_PIN_LOCKED`(5번째 틀림) · `PAY_PIN_RESET`(대상 직원 번호, 명령으로 하면 actor 없음). 비밀번호 · 틀린 숫자 · 맞힘/틀림 하나하나는 적지 않는다.
- 새 표 번호: **018** (`018_pay_pin.sql`, #179의 017 다음).
- 화면 잠금은 `AUTH.live && PIN.on`일 때만 — 시연 모드 · 시연 사이트 · 기존 화면 검사(`AUTH.live`만 켜는 것)는 그대로 눈 단추.
- 화면 문구는 설계 4장 그대로("~요"체). 잠금 창 모양은 `시안.html`의 `.pl-*` 규칙을 그대로 옮긴다(따뜻한 색 · `--coral-text`, 파랑 한 색 금지).
- 저장소는 공개 — 검사 자료는 가상 사람(가상1~5) · `example.invalid` · 가상 비밀번호.

## Review Focus

1. 같은 사람이 틀린 비밀번호를 동시에 8번 보내도 `fails`는 정확히 5, 400 응답은 4번뿐이다(`BEGIN IMMEDIATE`로 한 줄씩). Task 2 검사.
2. 풀림은 세션마다: PC에서 풀어도 휴대폰 · 다른 PC 창은 잠겨 있고, HR 초기화 · 5번 틀림은 그 사람의 **모든** 창의 풀림을 끝낸다. Task 2 · 3 검사.
3. 6분 경계: 풀고 359초 뒤 요청은 열리고(그때 다시 늘어남), 그 뒤 361초 쉬면 잠긴다. 잠긴 창의 alive는 창을 열지 않는다. Task 2 검사(`auth._now`를 바꿔서).
4. 1분 가림이 하던 입력을 지우지 않는다: 그린 서명(`#ctPad`, `ctPadDirty`) · 열린 확인 창(`#orgModal`) · 연봉 인상 계산 칸은 같은 요소 그대로 남고 가림막만 덮인다. Task 5 `pinSelfCheck()`.
5. 쓰던 중 잠김 응답(서버 재시작 · 다른 창에서 5번 틀림): 화면은 받아 둔 금액을 지우고 잠금 창으로 가며 다시 보내지 않는다. 풀린 뒤에 "불러오지 못했습니다"가 남지 않고 다시 받는다. 다른 앞머리(예 `/api/kpi/…`)의 423에는 반응하지 않는다. 메뉴 숫자는 그대로 보인다. Task 4 `pinSelfCheck()`.

---

### Task 1: 바탕 — 표 018 · 규칙 · 해시 · `python -m paypin check`

**Files:**
- Create: `backend/hr_eval/sql/018_pay_pin.sql`
- Create: `backend/paypin.py` (맨 위 설명에 길 목록 — `payslip.py` 맨 위 모양)
- Modify: `backend/hr_eval/sql/check_schema.py` (`check_pay_pin_tables` 추가 후 `main`의 `check_push_tables(conn)` 다음에 연결)
- Modify: `.github/workflows/ci.yml` (평가·계약 도메인 검사 job, "휴대폰 알림 문구 · 근무시간 · 키 자체 검증" 다음에 `- name: 급여 비밀번호 규칙 · 해시 자체 검증` / `run: python -m paypin check` — 앞 단계와 같은 모양)

**Interfaces:**
- Produces 표: `pay_pins(user_id INTEGER NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, pin_hash TEXT NOT NULL, salt TEXT NOT NULL, set_at TEXT NOT NULL, fails INTEGER NOT NULL DEFAULT 0 CHECK (fails BETWEEN 0 AND 5), locked_at TEXT NULL, CHECK ((locked_at IS NULL) = (fails < 5)))` · `ALTER TABLE sessions ADD COLUMN pay_used_at TEXT NULL` (풀린 세션의 마지막 급여·계약 사용, 잠기면 NULL). 파일 맨 위 설명은 `017_push.sql` 모양(번호 규칙 줄 포함).
- Produces (`backend/paypin.py`):
  - `PREFIXES` (Global Constraints), `FAILS_MAX = 5`, `OPEN_FOR = timedelta(seconds=360)`, `MSG_LOCKED`, `MSG_NO_KEY`, `MSG_WRONG = "비밀번호가 맞지 않아요."`
  - `problem(pin: object, again: object | None, birthday: str | None) -> str | None` — Global Constraints의 순서대로 첫 문구, 괜찮으면 None. `again`이 None이면 두 번 비교를 건너뛴다(확인할 때).
  - `derive(pay_key: bytes) -> bytes` · `pin_hash(k: bytes, salt: str, pin: str) -> str` · `pin_ok(k: bytes, salt: str, pin: str, stored: str) -> bool`
  - CLI `python -m paypin check` (아래 assert들).

- [ ] **Step 1: 자체 검증을 먼저 쓴다 (실패해야 함)**

```python
assert problem("111111", "111111", None) == "같은 숫자 6개는 쓸 수 없어요."
for p in ("123456", "654321", "012345", "456789", "987654", "543210"):
    assert problem(p, p, None) == "이어지는 숫자는 쓸 수 없어요.", p
assert problem("567890", "567890", None) is None         # 9 → 0 으로 넘어가는 것은 이어지는 숫자로 치지 않는다
assert problem("903141", "903141", "03-14") == "생일(월일)이 들어간 번호는 쓸 수 없어요."
assert problem("258036", "258037", None) == "두 번 입력한 비밀번호가 달라요."
for bad in ("25803", "2580367", "25a036", "２５８０３６", 258036, None):
    assert problem(bad, bad, None) == "숫자 6자리로 정해 주세요.", bad
assert problem("258036", "258036", "03-14") is None and problem("258036", None, None) is None
k1, k2 = derive(b"\x01" * 32), derive(b"\x02" * 32)
h = pin_hash(k1, "ab" * 16, "258036")
assert pin_ok(k1, "ab" * 16, "258036", h) and not pin_ok(k1, "ab" * 16, "258037", h)
assert not pin_ok(k2, "ab" * 16, "258036", h)          # 열쇠가 다르면 맞혀 볼 수 없다
assert pin_hash(k1, "cd" * 16, "258036") != h            # 사람마다 소금
assert "258036" not in h
```

- [ ] **Step 2:** `cd backend && python -m paypin check` → 실패(모듈 없음).
- [ ] **Step 3:** 표 018 · `paypin.py`의 위 Interfaces · check_schema · ci.yml을 만든다. `check_pay_pin_tables(conn)`는 `"30. 급여 비밀번호 (018)"`: 판 18 이상 · 줄 저장 · `fails` 6 거부(CHECK) · `fails=5`인데 `locked_at` 없음 거부 · `fails=2`인데 `locked_at` 있음 거부 · 없는 직원 거부(FOREIGN KEY) · `pin_hash`/`salt` NULL 거부 · 같은 사람 두 줄 거부(UNIQUE) · 직원을 지우면 줄도 사라짐 · `sessions.pay_used_at` 칸이 있고 기본 NULL. 가상 사람 · 값은 `check_push_tables` 모양.
- [ ] **Step 4:** `python -m paypin check` 통과 · `python -m hr_eval.sql.check_schema`(판 18) · `python -m hr_eval.sql.check_sqlite_features` 통과.
- [ ] **Step 5:** Commit `급여 비밀번호 바탕: 표 018 · 규칙 · 해시`

### Task 2: 서버 잠금 — `gate()` · 비밀번호 길 다섯 · 기존 검사 정리

**Files:**
- Modify: `backend/paypin.py` (`bp = Blueprint("paypin", __name__)` · `state_of` · `gate` · 길 다섯)
- Modify: `backend/app.py` (`import paypin` · `app.register_blueprint(paypin.bp)` — 다른 blueprint 줄 모양 그대로)
- Modify: `backend/auth.py` — `_guard` 끝(`g.actor = actor` 뒤)에서 `import paypin` 후 `return paypin.gate()` (함수 안 import — `_forget_push`와 같은 이유, 주석 한 줄) · `_no_store`의 `"/api/payslips"`를 `"/api/pay"`로(두 앞머리 모두 덮는다) · 맨 위 설명의 "그 밖의 /api/* 전부" 줄에 "급여·계약 세 앞머리는 비밀번호도(paypin.py)" 덧붙임
- Test: `backend/auth_check.py` — `pay_gate_off()` · `check_pay_pin(hr)`

**Interfaces:**
- Consumes: Task 1 전부, `payslip.pay_key()`, `auth._db` · `_now` · `_iso` · `_audit` · `_hr_only`, `g.session_hash`, `g.actor`.
- Produces:
  - `state_of(conn, uid: int, token_hash: str, now: datetime) -> str` — `"set"`(줄 없음) · `"locked"`(`locked_at` 있음, 풀림보다 먼저) · `"open"`(`pay_used_at > now - OPEN_FOR`) · `"ask"`.
  - `gate() -> Response | None` — 열쇠가 없거나 앞머리 밖이면 None. `"open"`이면 그 세션 `pay_used_at = now`로 늘리고 None. 아니면 423(Global Constraints). `auth._now()`를 모듈로 불러 검사가 시계를 바꿀 수 있게.
  - `GET /api/pay/lock` → `{"state", "left": FAILS_MAX - fails (줄 없으면 5), "hr": [{"mailNo", "email"}]}` — `hr` = 활성 HR 전원(`user_roles.role='HR_ADMIN'`, `is_active=1`)의 `hiworks_mail_no`(없으면 "") · `email`. 열쇠 없음이면 `{"state": "off"}`.
  - `POST /api/pay/pin {pin, again}` — 열쇠 없음 503 · 줄이 이미 있으면 409 `이미 비밀번호가 있어요.` · `problem(pin, again, 본인 birthday)`이면 400 `{"error": 문구}` · 아니면 줄 넣고 이 세션 풀림 · `PAY_PIN_SET` · 200 `{"ok": true}`. `BEGIN IMMEDIATE`.
  - `POST /api/pay/unlock {pin}` — 열쇠 없음 503 · 6자리 숫자가 아니면 400 `숫자 6자리로 정해 주세요.`(횟수 세지 않음) · 줄 없음 423 `lock:"set"` · 잠김 423 `lock:"locked"` · 맞음 → `fails=0`, 이 세션 풀림, 200 · 틀림 → `fails+1`, 5가 되면 `locked_at=now` · `PAY_PIN_LOCKED` · 423 `lock:"locked"`, 아니면 400 `{"error": MSG_WRONG, "left": 5-fails}`. 읽기부터 고치기까지 `BEGIN IMMEDIATE` 한 번.
  - `POST /api/pay/lock` → 이 세션 `pay_used_at = NULL` · 200. `POST /api/pay/alive` → `pay_used_at > now - OPEN_FOR`일 때만 now로 · 200 `{"open": bool}`.
- Produces (`auth_check.py`): `@contextmanager pay_gate_off()` — `paypin.gate`를 `lambda: None`으로 바꿨다가 되돌린다. `main()`에서 `check_payslips` ~ `check_push_events` 여섯 줄을 `with pay_gate_off():` 안으로 옮기고(이 검사들은 잠금 이전 동작을 본다), 끝에 `check_pay_pin(hr)`를 잠금을 켠 채로 부른다.

- [ ] **Step 1: 실패하는 검사** `check_pay_pin(hr)` — `HR_PAY_KEY`에 가상 32바이트를 넣고(끝나면 되돌림), 가상3 PC 창 A · B를 따로 로그인:
  - A `GET /api/pay/lock` → `state == "set"`, `left == 5`, `hr`에 가상1 메일
  - 잠긴 A로 `GET /api/payslips` · `GET /api/withholding` · `GET /api/withholding/2025/pdf` · `GET /api/contracts/mine` · `GET /api/contracts/division` · `POST /api/payslips/2026-10/seen` · `POST /api/contracts/1/sign` → 모두 423, `json == {"error": MSG_LOCKED, "lock": "set"}`(칸이 정확히 둘)
  - 경로 변형: `GET /api/payslips/` · `GET /api//payslips` → 200이면서 `slips`가 든 응답은 없다
  - `users.birthday='03-14'`로 두고 `POST /api/pay/pin`: `111111` · `123456` · `654321` · `903141` → 400 각 문구 / `again` 다름 → 400 / `25a036` → 400 / `258036`×2 → 200, A `GET /api/payslips` 200 / 다시 정하기 → 409 / `pay_pins` 어느 칸에도 `258036` 없음, 감사로그 `PAY_PIN_SET` 1줄 · 감사로그 전체에 `258036` 없음
  - B는 여전히 `GET /api/pay/lock` → `"ask"` · `GET /api/payslips` → 423 `lock:"ask"`
  - B 틀림 4번 → 400 `left` 4·3·2·1 / 5번째 → 423 `locked`, `PAY_PIN_LOCKED` 1줄 / B 맞는 번호 → 423 `locked` / 새로 로그인한 창 C → `"locked"` / **A도** `GET /api/payslips` → 423 `locked`
  - (줄을 지워 되돌린 뒤) 다시 정하고 동시에 틀린 번호 8번(스레드) → `fails == 5`, 400 응답 4개
  - 시계 (다시 줄을 지우고 A에서 정한 뒤): A를 t0에 풀고 `auth._now = t0+359초` → `GET /api/payslips` 200 / `auth._now = t0+359초+361초` → 423 `ask` / 다시 풀고 t1+300초에 `POST /api/pay/alive` → `open: true`, t1+300+359초 → 200 / 잠긴 창 B의 `alive` → `open: false`, 그 뒤 B 여전히 423
  - `POST /api/pay/lock` → 다음 요청 423 `ask` / 풀린 A 로그아웃 → `GET /api/payslips` 401
  - Origin 없이 `POST /api/pay/unlock` → 403(기존 같은 출처 검사)
  - `/api/pay/lock` 응답 `Cache-Control: no-store`
  - 열쇠를 빼면 `GET /api/pay/lock` → `{"state": "off"}` · `POST /api/pay/unlock` 503 · `GET /api/payslips` 200 빈 목록
  - 끝나면 가상3의 `pay_pins` 줄 · 생일 · 만든 세션을 되돌린다.
- [ ] **Step 2:** `python -m auth_check` → 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `python -m auth_check` 전부 통과(앞선 검사 그대로).
- [ ] **Step 5:** Commit `급여 비밀번호: 서버 잠금 · 정하기 · 확인 · 잠그기 · 아직 있어요`

### Task 3: 메뉴 숫자 길 · HR 초기화 · 구성원 설정 상태 · 서버 명령

**Files:**
- Modify: `backend/paypin.py` (`counts` · `GET /api/pay/counts` · 초기화 길 · `reset_by_email` · CLI `reset`)
- Modify: `backend/push.py` `pending()` — p1~p3을 세던 `for kind, sql in (...)` 묶음을 `n.update(paypin.counts(conn, uid))`로(같은 기준을 한 곳에, 함수 안 import)
- Modify: `backend/auth.py` `members_list` — 사람 줄에 `"payPin": "none" | "set" | "locked"`, `"payPinLockedAt": ISO | None` (`pay_pins` 부분 쿼리 둘)
- Test: `backend/auth_check.py` `check_pay_pin` 뒷부분

**Interfaces:**
- Consumes: Task 2.
- Produces:
  - `counts(conn, uid: int) -> dict[str, int]` → `{"p1", "p2", "p3"}` — `push.pending()`에 있던 세 쿼리 그대로(열쇠 없으면 셋 다 0).
  - `GET /api/pay/counts` → `{"p1", "p2", "p3", "due": "YYYY-MM-DD" | None}` — `due = MIN(due_on) FROM contracts WHERE user_id=? AND status='SENT'`(열쇠 없으면 None). 금액 · 달 · 본문 없음. `no-store`.
  - `POST /api/members/<int:user_id>/pay-pin/reset` — HR 아님 403 · 본인 403 `본인 비밀번호는 다른 HR이 초기화해요.` · 없는 사람 404 · 아니면 그 사람 `pay_pins` 줄 지움 + 그 사람 모든 세션 `pay_used_at = NULL` + `PAY_PIN_RESET`(`entity_type="user"`, `entity_id`) · 200 `{"ok": true}`(줄이 없어도).
  - `reset_by_email(conn, email: str) -> int | None` — `users.email`(대소문자 무시)이 같은 한 사람을 초기화(위와 같은 일, 감사로그 `actor_user_id=None`), 그 직원 번호 · 없으면 None.
  - CLI `python -m paypin reset <메일 주소>` — `HR_DB_PATH`의 DB로 `reset_by_email`, 출력은 `초기화했습니다 (직원 번호 N)` 또는 `그 메일 주소의 직원이 없습니다`(종료 코드 1). HR이 한 명뿐일 때 HR 본인 초기화용.

- [ ] **Step 1: 실패하는 검사** (`check_pay_pin` 뒷부분):
  - 자료는 `with pay_gate_off():` 안에서 HR 길로 만든다 — 명세서 한 달 올리기 · 공개, 영수증 한 해 올리기 · 공개(`check_push_pending`의 "(검사용) HR 이 명세서를 올림" 줄 모양), 가상3에게 계약서 한 장 발송(`check_push_events`의 발송 모양, 마감일 하나를 정해 둔다). 가상3은 열지 않는다. 다른 검사가 남긴 자료가 있을 수 있으니 기대값은 **만들기 전 `counts` + 1**로 본다(`check_pay_pin`은 `main` 맨 끝이라 만든 자료는 남겨도 된다).
  - 잠긴 창으로 `GET /api/pay/counts` → 200, p1 · p2 · p3이 각각 만들기 전 + 1, `due` = 정해 둔 마감일(그보다 이른 서명 대기가 없을 때), 응답 칸이 정확히 `{"p1","p2","p3","due"}` / `push.pending(app, 3)`의 p1 · p2 · p3이 같은 수 / 열쇠 빼면 셋 다 0 · `due` None
  - HR `GET /api/members` → 가상3 `payPin == "locked"` · `payPinLockedAt` 있음 (5번 틀린 뒤) / 초기화 뒤 `"none"`
  - HR `POST /api/members/3/pay-pin/reset` → 200, `PAY_PIN_RESET` entity 3 · 그 뒤 가상3의 모든 창 `"set"`(풀려 있던 창도) / HR 본인(1) → 403 / 가상2(팀장, HR 아님) → 403 / 999 → 404
  - `paypin.reset_by_email(conn, "U3@EXAMPLE.INVALID")` → 3, 감사로그 actor 없음 / 없는 주소 → None
- [ ] **Step 2:** `python -m auth_check` 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `python -m auth_check` 통과(특히 `check_push_pending` 그대로).
- [ ] **Step 5:** Commit `급여 비밀번호: 메뉴 숫자 길 · HR 초기화 · 서버 명령`

### Task 4: 화면 — 잠금 창 · 처음 정하기 · 5번 틀림 · 숫자 · 잠김 응답

**Files:**
- Modify: `public/index.html` — `<style>`에 `시안.html`의 `.pl-*` 규칙(마지막 넷은 기존 `@media (max-width:768px)` 안) · 급여·계약 화면 근처(`payContent` 앞)에 잠금 묶음 · `authBoot` · `TODO`(p1~p3) · `ctTaskTitle` · `renderContent` · `window.fetch` 감싸개

**Interfaces:**
- Consumes: Task 2 · 3의 길과 응답 모양.
- Produces:
  - `const PIN = { on:false, state:'', left:5, hr:[], err:'', typed:'', first:'', row:0, forgot:false, veiled:false, last:0, sent:0 }` — state: `''` 아직 · `set` · `ask` · `locked` · `open` · `err`(상태를 못 받음 — 잠긴 것으로 친다)
  - `const PAY_COUNTS = { p1:0, p2:0, p3:0, due:null }`
  - `pinGated() -> boolean` = `AUTH.live && PIN.on && PIN.state!=='open'`
  - `async pinLoad()` (`GET /api/pay/lock`; `off`면 `PIN.on=false`, 실패면 `on=true · state='err'`) · `async payCountsLoad()` (실패하면 그대로 둔다)
  - `pinRule(pin) -> string` — 같은 숫자 · 이어지는 숫자 두 규칙만 서버와 같은 문구(생일은 서버만), 괜찮으면 `''`
  - `pinView() -> string` — 상태별 `#pinBox`(`.pl-card`): `set` = 설계 4.2(두 줄 `#pinNew` · `#pinAgain`, "정하기"는 둘 다 6자리 · 같음 · `pinRule` 통과일 때만 눌림) · `ask` = 4.1(`.pl-pins` 6칸, PC는 숨은 `#pinIn` `inputmode="numeric"` 하나 · 붙여넣기 허용 · 숫자 아닌 것 무시, 휴대폰 `isPhone()`은 `.pl-keys` 1~9 · 0 · 지우기만, 입력 칸 없음) · `locked` = 4.4 · `err`/`''` = `kpiNote` 안내. 문구는 설계 그대로.
  - `pinType(v)` (숫자만 남기고 6자리면 바로 `pinSubmit()`) · `pinKey(k)` (휴대폰 자판, `'del'`) · `async pinSubmit()` · `async pinSetSubmit()` · `pinForgot()` (잊었어요 → 4.4의 요청 안내와 단추를 그 창 안에) · `pinMail()`
  - `async pinOpened()` — `pinForget()`(Task 5, 이 Task에서는 받아 둔 급여·계약 자료와 각 `state`를 비우는 최소판으로 먼저 만든다) → `PIN.state='open'` · `err/typed/first=''` · `veiled=false` · `last=Date.now()` · `payShow=true` → `await Promise.all([payLoadMine(), whFetchMine(), ctLoadMine()])` → `renderContent()`
  - `pinRefused(lock)` — `pinForget()` · `PIN.state = lock || 'ask'` · `payCountsLoad().then(renderSidebar)` · `renderContent()`
- 바뀌는 곳:
  - `authBoot`: `await Promise.all([payLoadMine(), whFetchMine(), ctLoadMine()])` 줄 → `await pinLoad(); await (pinGated() ? payCountsLoad() : Promise.all([payLoadMine(), whFetchMine(), ctLoadMine()]));`
  - `TODO.p1/p2/p3`: 실제 로그인에서 `pinGated()`면 `PAY_COUNTS.p1/p2/p3`, 아니면 지금 식 그대로. `ctTaskTitle`: `pinGated()`면 `PAY_COUNTS.due`.
  - `renderContent`: 급여·계약 메뉴(`currentNav.id==='pay'`)이고 `pinGated()`면 `currentTab.render()`를 부르지 않고 `pinView()`(탭 줄 · 탭 숫자는 그대로), PC `ask`면 그린 뒤 `#pinIn`에 초점.
  - `window.fetch` 감싸개: 401 줄 옆에 — 423이고 `AUTH.live`이고 주소가 `^/api/(payslips|withholding|contracts)`이면 `r.clone().json()`의 `lock`으로 `pinRefused`. 그 밖의 423은 그대로 돌려준다.
  - `pinMail()`: `PIN.hr`가 한 명이고 `mailNo`가 있으면 `HW_MAIL + '&user_nos=' + 번호` 새 창 + `wsNotice('제목은 "급여 비밀번호 초기화 요청"으로 적어 주세요.')`; 아니면 `ctMail`처럼 메일 주소를 `;`로 이어 `hwMailCopy(…, ' 제목은 "급여 비밀번호 초기화 요청"으로 적어 주세요.', n)`를 **창보다 먼저** 하고 `HW_MAIL` 새 창; 아무도 없으면 `wsNotice('HR 메일 주소가 없어요. HR에 직접 알려 주세요.')`. 제목을 주소로 채우는 길은 확인된 것이 없어 안내로 한다(내 판단).

- [ ] **Step 1: 실패하는 검사** `async pinSelfCheck()` 앞부분 — `ctLiveCheck`처럼 `window.fetch`를 가짜로(부른 주소 · 몸통을 적어 둠), `AUTH.live=true · PIN.on=true`, 끝나면 모두 되돌림:
  - `pinRule('111111')==='같은 숫자 6개는 쓸 수 없어요.'` · `pinRule('123456')` · `pinRule('654321')` → 이어지는 문구 · `pinRule('258036')===''`
  - 급여·계약 메뉴 · `state='set'` → `#pinBox`에 "급여 비밀번호를 정해 주세요", "정하기"가 꺼져 있음 / 두 줄에 `258036` → 켜짐 / `111111` → 꺼짐 · 이유 글
  - `state='ask'` → "비밀번호를 입력해 주세요" · `.pl-pins i` 6개 · 그리는 동안 부른 주소에 `/api/payslips` · `/api/withholding` · `/api/contracts` 없음 · `.pay-hero` 없음
  - `pinType('2580')` → `.on` 4개 · 부른 것 없음 / `pinType('258036')` → `POST /api/pay/unlock` 한 번, 몸통 `{"pin":"258036"}`
  - 가짜 답 400 `{left:3}` → `.pl-msg`가 "비밀번호가 맞지 않아요. 남은 기회 3번" · `.pl-pins.bad` · `PIN.typed===''`
  - 가짜 답 423 `{lock:'locked'}` → "비밀번호를 5번 틀려서 잠겼어요" · "HR에 초기화 요청 (하이웍스 메일)" 단추 / `pinMail()` (`window.open` 가짜): HR 한 명 · 번호 있음 → 주소에 `user_nos=` / 두 명 → 복사가 창보다 먼저
  - `isPhone`을 잠깐 `()=>true`로 → `.pl-keys`에 0~9 단추 · `#pinIn` 없음 / `pinKey('2')` 여섯 번 → unlock 한 번
  - 잠긴 동안 `PAY_COUNTS={p1:2,p2:1,p3:0,due:'2027-02-14'}` → `TODO.p1()===2 · TODO.p2()===1` · `homePortalTasks()`에 `ctMd('2027-02-14')`가 든 서명 줄
  - `state='open'`에서 가짜 `/api/payslips`가 423 `{lock:'ask'}` → 기다린 뒤 `PIN.state==='ask'` · `PAY_LIVE.mine.length===0` · 그 뒤 가짜 unlock 200 → `PAY_LIVE.state` 가 `'err'`가 아님(다시 받음)
  - `state='open'`에서 가짜 `/api/kpi/inbox`가 423 → `PIN.state==='open'` 그대로
  - `AUTH.live`만 켜고 `PIN.on=false`이면 `pinGated()===false` (기존 화면 검사 그대로)
- [ ] **Step 2:** `devlive.py`로 띄운 화면에서 `node cdp_check.mjs <주소> "pinSelfCheck()"` → 실패(함수 없음).
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `pinSelfCheck()` 통과 + `paySelfCheck()` · `await whSelfCheck()` · `await ctLiveCheck()` · `todoSelfCheck()` · `homeSelfCheck()` · `await pushSelfCheck()` 그대로 통과 · `python .ci/check-html.py` 통과.
- [ ] **Step 5:** Commit `급여 비밀번호 화면: 잠금 창 · 처음 정하기 · 5번 틀림 · 메뉴 숫자`

### Task 5: 화면 — 풀린 띠 · 1분 가림 · 5분 잠금 · 아직 있어요 · 로그아웃 정리

**Files:**
- Modify: `public/index.html` — 잠금 묶음 · `renderContent` · 눈 단추 둘(`payContent`의 `.pay-eye`, 원천징수영수증 `eye`) · `authLock` · `authBoot` · `visibilitychange`(지금 `wsPull(false); payWake(); …` 줄)

**Interfaces:**
- Consumes: Task 4.
- Produces:
  - `PIN_VEIL_MS = 60e3`, `PIN_LOCK_MS = 300e3`, `PIN_ALIVE_MS = 60e3`
  - `pinBand() -> string` — `#pinBand`(`.pl-bar`): 보통 "잠금 풀림 · 1분 동안 움직임이 없으면 금액을 가리고, 5분이면 다시 잠겨요" + `<button onclick="pinLockNow()">지금 잠그기</button>`, `PIN.veiled`면 `.pl-bar.warn` + "1분 동안 움직임이 없어 금액을 가리고 열린 명세서를 닫았어요. 눈 모양을 누르면 다시 보여요." `renderContent`가 급여·계약 메뉴 · `AUTH.live && PIN.on && !pinGated()`일 때 탭 내용 앞에 붙인다. `pinBandPaint()`는 `#pinBand`만 바꿔 그린다.
  - `pinTouch()` — `pointermove · pointerdown · keydown · wheel · touchstart`(document, passive) · `scroll`(capture)에서 `PIN.last=Date.now()`; 풀린 상태이고 `Date.now()-PIN.sent ≥ PIN_ALIVE_MS`면 `PIN.sent=지금` + `POST /api/pay/alive`(기다리지 않음).
  - `pinTick()` — 5초마다(`authBoot`에서 `PIN.on`이면 한 번 시작): 풀린 상태에서 쉰 시간 ≥ `PIN_LOCK_MS` → `pinLockNow()`, ≥ `PIN_VEIL_MS`이고 아직 안 가렸으면 → `pinVeil()`.
  - `pinVeil()` — `PIN.veiled=true` · `payShow=false` · `payOpen=null` · `whOrig=null`. 급여·계약 메뉴이고 (p1이고 `ps.view!=='admin'` 또는 p3이고 `wh.view!=='admin'`)이고 `#orgModal`이 없으면 → `renderContent()`. 급여·계약 메뉴의 그 밖의 화면(p2 · p4 · HR 올리기 · 확인 창이 떠 있음) → **다시 그리지 않고** `#pinVeil`(화면 전체 고정, 확인 창보다 위, 글 "1분 동안 움직임이 없어 가렸어요. 누르면 다시 보여요.")을 붙이고 `pinBandPaint()`. 다른 메뉴면 값만 바꾼다.
  - `pinVeilOff()` — `#pinVeil` 지움 · `PIN.veiled=false` · `pinBandPaint()`. `#pinVeil`을 누르면 부른다.
  - `payEye()` — 두 눈 단추의 `onclick`을 이것으로: `payShow=!payShow; if(payShow) PIN.veiled=false; renderContent()`.
  - `pinLockNow()` — `POST /api/pay/lock`(기다리지 않음) · `pinVeilOff()` · `pinForget()` · `PIN.state='ask'` · `payCountsLoad().then(renderSidebar)` · `renderContent()`.
  - `pinForget()` (Task 4 최소판을 채움) — `authLock`에 있던 급여·계약 지우기를 옮긴 것: `ps.pick=ps.prev=null` · `PAY_LIVE` 비움 · `ctDrop()` · `WH_LIVE` 비움 · `whOrig=null` · `whPrintGone()` · `whDrop()` · `wh.up=wh.prev=wh.run=null` · `ps.state=wh.state=''` · `payOpen=null` · `payShow=false` · **`rz`의 `pay · grade · adj · edited · pick · lock`도 처음 값으로**(HR PC에서 읽은 연봉 엑셀 — 지금 로그아웃에서도 남던 것) · 급여·계약 메뉴면 `orgCloseModal(true)`. `authLock`은 이 줄들 대신 `pinForget()`을 부르고 `PIN.state=''` · `PIN.veiled=false`.
  - `visibilitychange`: 보이지 않게 되면 풀린 상태일 때 `pinVeil()`. 다시 보이면 `pinTick()`을 먼저 하고, `pinGated()`면 `payCountsLoad().then(renderSidebar)`, 아니면 지금처럼 `payWake(); whWake(); ctWake();` (`wsPull` · `pushInit`은 그대로).

- [ ] **Step 1: 실패하는 검사** (`pinSelfCheck()` 뒷부분, 시계는 `PIN.last`를 과거로 놓아 흉내):
  - 풀린 p1 내 화면 · `PIN.last = 지금-61초` → `pinTick()` → `payShow===false · payOpen===null` · `#pinBand.warn` 글 "1분 동안 움직임이 없어" · `#pinVeil` 없음 / `payEye()` → `PIN.veiled===false`
  - p2에 가짜 `#ctPad`를 두고 `ctPadDirty=true` → `pinTick()` → `#pinVeil` 있음 · 같은 `#ctPad` 요소가 그대로 문서 안 · `ctPadDirty===true` / `#pinVeil` 누름 → 없어짐
  - p1에 `orgModal('<div class="ps-cf">…</div>')` 띄움 → `pinTick()` → 확인 창 그대로 · `#pinVeil`이 그 위
  - `PIN.last = 지금-301초` → `pinTick()` → `POST /api/pay/lock` 한 번 · `PIN.state==='ask'` · `PAY_LIVE.mine.length===0` · `rz.pay===null` · `#orgModal` 없음 · `#pinBox` 보임
  - `PIN.sent = 지금-61초` → `pinTouch()` → `/api/pay/alive` 한 번 / 곧바로 다시 `pinTouch()` → 늘지 않음 / 잠긴 상태의 `pinTouch()` → 부르지 않음
  - `authLock('out')` 뒤 `rz.pay===null · PIN.state===''`
- [ ] **Step 2:** `cdp_check.mjs`로 `pinSelfCheck()` → 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `pinSelfCheck()` 통과 + Task 4 Step 4의 화면 검사 전부 · `rzSelfCheck()` · `psSelfCheck()` 그대로.
- [ ] **Step 5:** Commit `급여 비밀번호 화면: 풀림 띠 · 1분 가림 · 5분 잠금 · 아직 있어요`

### Task 6: 화면 — HR 구성원 설정 줄 · 초기화 · 사용법 문구

**Files:**
- Modify: `public/index.html` — `msSee`(휴대폰 해제 줄 바로 아래) · `msPhoneRevoke` 옆 · `TOURS.pay` · `TOUR_MENU.pay`

**Interfaces:**
- Consumes: Task 3의 `payPin` · `payPinLockedAt` · 초기화 길.
- Produces:
  - `pinMemberRow(m) -> string` — 실제 로그인에서 휴대폰 줄과 같은 모양: `none` "급여 비밀번호: 아직 안 정함"(단추 없음) · `set` "급여 비밀번호: 정함" · `locked` "급여 비밀번호: **잠김**(5번 틀림, `ctKst(m.payPinLockedAt)`)". `set` · `locked`이면 `<button onclick="msPinReset(id)">비밀번호 초기화</button>`, 단 `m.me`면 단추 대신 "다른 HR에게 요청해 주세요." 아래 작은 글 "초기화하면 다음에 급여·계약 메뉴에 들어갈 때 본인이 새로 정해요. HR도 비밀번호는 볼 수 없어요."
  - `async msPinReset(id)` — `confirm(\`${이름} 님의 급여 비밀번호를 초기화할까요? 다음에 들어올 때 본인이 새로 정해요.\`)` 뒤 `POST /api/members/${id}/pay-pin/reset` → `msPhoneRevoke`와 같은 뒷일(`orgCloseModal()` · 실패 `kpiFlash('비밀번호를 초기화하지 못했습니다.', 'danger')` · `MS_LIVE.state=''` · `kpiFlash('급여 비밀번호를 초기화했습니다. 본인이 다음에 들어올 때 새로 정해요.')`).
  - `TOURS.pay`: 맨 앞에 `{ el:'#pinBox', title:'비밀번호', body:'급여·계약 메뉴는 <b>비밀번호 6자리</b>를 넣어야 금액이 보여요. 처음 한 번은 본인이 정해요.' }`, 눈 단추 단계 앞에 `{ el:'#pinBand', title:'지금 잠그기', body:'자리를 비울 때는 <b>지금 잠그기</b>를 눌러 주세요. 1분 쉬면 금액을 가리고, 5분 쉬면 다시 잠겨요.' }`, 눈 단추 단계 내용 → "비밀번호를 넣으면 금액이 보여요. 눈 모양으로 다시 가릴 수 있어요." `TOUR_MENU.pay`의 끝 문장 → "금액은 비밀번호를 넣어야 보입니다."

- [ ] **Step 1: 실패하는 검사** (`pinSelfCheck()` 끝부분): 가상 명단 줄로 `pinMemberRow` — `locked` + 시각 → "잠김" · "5번 틀림" · 단추 / `m.me` → "다른 HR에게 요청해 주세요." · 단추 없음 / `none` → "아직 안 정함" · 단추 없음 / `msPinReset(7)`: `confirm` 가짜 true → `POST /api/members/7/pay-pin/reset`, false → 부르지 않음 / `TOURS.pay.steps()`의 `el`에 `#pinBox` · `#pinBand` · 눈 단추 단계 새 문구 / `TOUR_MENU.pay`에 "비밀번호를 넣어야".
- [ ] **Step 2:** 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `pinSelfCheck()` · `msSelfCheck()` · `tourSelfCheck()` 통과.
- [ ] **Step 5:** Commit `급여 비밀번호 화면: HR 초기화 · 사용법 문구`

### Task 7: 문서 · 실제 흐름 확인 · 마무리 검사

**Files:**
- Modify: `DEPLOY.md` — `## 11. 급여 비밀번호 — HR이 한 명뿐일 때` (HR 본인 비밀번호는 다른 HR이 구성원 설정에서 초기화한다 · HR이 한 명뿐이면 서버에서 `docker compose exec -T api python -m paypin reset <그 HR의 회사 메일 주소>` · 출력 두 가지 · 새 열쇠 · 새 타이머 없음)
- Modify: `CLAUDE.md` "현재 사이트 상태"에 급여·계약 비밀번호 잠금 항목(날짜는 merge 날) — 무엇을 잠그나 · 세 앞머리 규칙(**급여·계약 금액을 보내는 새 길은 이 세 앞머리 안에 둔다. 다른 앞머리로 만들면 `paypin.PREFIXES`에 더한다**) · 시간 · 열쇠 없을 때 · 검사 이름(`check_pay_pin` · 30번 `check_pay_pin_tables` · `python -m paypin check` · `pinSelfCheck()`) · DB 판 18 · 자주 묻는 질문(`faq.json`)은 바꾸지 않음(인사담당자가 고칠 수 있음).

- [ ] **Step 1:** 실제 흐름(가짜 하이웍스 · 실제 Flask): `python devlive.py <작업 폴더> <빈 포트>`(포트는 `netstat`로 빈 것) → 가상 직원으로 `cdp_check.mjs`: 처음 정하기 → 금액 보임 → `/api/pay/lock` → 잠금 창 · 메뉴 숫자 그대로 → 틀림 5번 → 잠김 화면 → HR 창에서 초기화 → 다시 정하기. 휴대폰 폭(390px)에서 숫자 자판. 그림을 찍어 시안 그림 1~7과 나란히 본다(`--shot`).
- [ ] **Step 2:** 로컬 검사 전부: `.ci/*.sh` 넷 · `python .ci/check-html.py` · `python -m hr_eval.selfcheck` · `check_schema` · `check_sqlite_features` · `python -m auth_check` · `python -m paypin check` · `python -m push check` · 화면 `pinSelfCheck()` 외 Task 4 · 5 · 6의 검사.
- [ ] **Step 3:** PR 본문의 "내 판단" 목록: 열쇠 없을 때 동작 · 잠기면 이미 풀린 다른 창도 잠김 · 메일 제목은 안내로 · 1분 가림 화면별 방식(다시 그림 / 가림막) · `rz`도 로그아웃 · 5분 잠금에서 지움 · 서버 명령은 메일 주소로 · 빠른 확인 제한(`_rate_ok`)은 두지 않음(틀림 5번이 사람마다 막고 한 줄씩 처리) · HR 문의 숫자는 숫자 길에 넣지 않음(왼쪽 메뉴에 없는 숫자). "남은 일"에 운영 반영 뒤 인사담당자 확인(PC 한 번 · 갤럭시 · 아이폰에서 정하기 → 잠금 → 5분 뒤 다시 잠김 — 설계 8장)과 "새 서버 설정 · 타이머 없음(ssh 할 일 없음)"을 적는다.
- [ ] **Step 4:** Commit `급여 비밀번호: 서버 명령 안내 · 작업 문서`
