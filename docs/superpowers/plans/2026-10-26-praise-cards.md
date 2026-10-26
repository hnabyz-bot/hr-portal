# 축하 · 칭찬 카드 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 동료에게 그림 · 손글씨 글 카드를 보내고(이번 주 소식 축하 · 조사 위로 · 언제든 칭찬 · 감사), 받으면 알림 · 봉투가 열리는 도착 창 · 워크스페이스 "받은 카드"에 모이게 한다.

**Architecture:** 서버는 `backend/cards.py` 한 파일(규칙 · 잠금 · API · 자체 검사)과 DB 판 21(`021_cards.sql`)의 `cards` 표. 받는 사람 조건은 지금 이번 주 소식 규칙(`auth.news_items` · `celeb_events`)을 그 순간 다시 계산한다. 글은 급여명세서와 같은 `HR_PAY_KEY` AES-GCM으로 잠근다. 알림 · 9시 묶음 · 메뉴 숫자는 지금 `push.py` · `TODO` 틀에 한 종류씩 더한다. 화면은 `public/index.html`에 시안 `cards-mock.js`를 실제 자료로 옮겨 심는다.

**Tech Stack:** Python 3 · Flask · SQLite(`hr_eval/sql`) · cryptography(AESGCM, 이미 있음) · 단일 파일 화면(인라인 CSS/JS, Web Animations API). 새 라이브러리 없음.

**Spec:** `docs/superpowers/specs/2026-10-26-praise-cards-design.md` (이 계획은 설계를 근거로 한다 — 둘 다 읽는다)

**시안(저장소 밖, 읽기만):** `C:\Users\user\OneDrive\바탕 화면\AI 인사시스템 구축\클로드 코드\뉴디자인\칭찬 카드 시안\cards-mock.js` — 화면 모양 · CSS(`.cm-*`) · 움직임(`openFx` · `burst` · `heartPop` · `cardSent`)의 원본. 그림 원본은 같은 폴더 `그림/카드/*.png`, 화면용 JPEG는 스크래치패드 `wt-mock/public/card-art/*.jpg`(768×432, 28장).

## Global Constraints

- 사용자에게 보이는 글은 모두 한국어 "~해요"체, 짧게. 아래 각 작업의 문구는 그대로 쓴다.
- 저장소는 Public — 실제 직원 이름 · 사진을 넣지 않는다. 검사 · 시연 자료의 사람은 "가상…".
- DB 판은 **021**(`backend/hr_eval/sql/021_cards.sql`). merge 전에 main에 021이 생기면 다음 번호로 옮긴다(`check_schema` 0번이 막는다).
- 글 잠금 열쇠는 `payslip.pay_key()` 그대로(새 열쇠 없음). 열쇠가 없으면 목록은 빈 채 `ready:false`, 보내기는 503.
- 카드 글: 앞뒤 빈칸을 떼고 1~150자, 줄바꿈(`\n`) 2번까지, 글자 규칙은 `kpi._chars`와 같은 기준(짝 없는 서로게이트 · 탭/줄바꿈/CR 밖의 제어문자 → 400). `\r\n`은 `\n`으로 바꿔 센다.
- 같은 보낸 사람 → 같은 받는 사람은 **한국 날짜 하루 1장**(`auth._today()`).
- 받을 수 없는 사람: 본인 · 비활성(`is_active=0`) · 조직도에서 뺀 사람(`org_hidden=1`) · 없는 번호. **포털 사용이 꺼진 사람은 받을 수 있다.**
- HR 권한으로도 남의 카드는 API로 못 본다. 감사로그(`audit_logs`)에 카드 일을 남기지 않는다.
- 알림 문구에 이름 · 글을 넣지 않는다. 알림은 지금 `push.notify` 규칙(평일 9~18시 · 등록한 휴대폰)만 따른다.
- 화면의 사람 글은 모두 `escapeHtml`. 움직임은 `matchMedia('(prefers-reduced-motion: reduce)')`면 하지 않는다. 라이브러리를 더하지 않는다.
- 그림 주소 `img/cards/{set}-{n}.jpg?v=1` — set ∈ birthday · welcome · anniv · joy · condol · praise · thanks, n ∈ 1~4.

## Review Focus

1. **보내기 창을 연 뒤 받는 사람에게 조사가 게시됨** — 서버가 축하 · 칭찬 · 감사를 403으로 막고, 화면은 창 안에 "지금은 이 카드를 보낼 수 없어요."를 띄운다(창은 그대로). → Task 3 테스트 `조사_게시_뒤_축하_거부`, Task 7 `cardSelfCheck` 항목.
2. **[보내기]를 빠르게 두 번 · 두 창에서 동시에** — 한 장만 저장(나머지 409), 화면 단추는 보내는 동안 막힌다. → Task 3 `하루_한장_동시`, Task 7 단추 막힘 항목.
3. **빈칸 · 줄바꿈만 있는 글, 151자, 줄바꿈 3번** — 400과 각 문구, 화면은 [보내기] 전에 같은 규칙으로 막는다. → Task 2 `clean_text` 테스트, Task 7 항목.
4. **HR이 남의 카드 번호로 읽음 · 고마워요 · 숨기기를 부름** — 404(남의 카드는 없는 것과 같게), 아무것도 바뀌지 않음. → Task 3 `남의_카드_404`.
5. **서버에 열쇠가 없을 때(PC 시험 · 설정 빠짐)** — 목록 `ready:false` · 빈 목록, 보내기 503 문구, 화면은 [카드 쓰기]를 막고 안내 한 줄. → Task 3 `열쇠_없음`, Task 6 항목.

---

### Task 1: DB 판 21 — `cards` 표

**Files:**
- Create: `backend/hr_eval/sql/021_cards.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` (새 `check_cards_table`, `main()`에 한 줄)

**Interfaces:**
- Produces: 표 `cards(id, sender_id, recipient_id, kind, reason, art, body, sent_on, created_at, read_at, thanked_at, hidden_at)` — 이후 작업이 이 이름을 쓴다.

- [ ] **Step 1: `check_cards_table(conn)` 먼저 쓰기** (기존 `check_pay_ask_table`과 같은 꼴, 사람은 `T040`·`T041` 가상). 확인할 것:
  - `PRAGMA user_version >= 21`
  - 정상 줄 저장(kind `praise`, reason NULL, art 1, body 29바이트 blob, sent_on `2027-03-02`)
  - 거부: 같은 (보낸 · 받는 · 날짜) 두 번 → `UNIQUE` / 보낸=받는 → `CHECK` / kind `love` → `CHECK` / kind `cheer`인데 reason NULL → `CHECK` / kind `praise`인데 reason `birthday` → `CHECK` / reason `xmas` → `CHECK` / art 0 · 5 · `'1'`(글자) → `CHECK` / body 글자 또는 28바이트 이하 → `CHECK` / sent_on `2027-02-30` → `CHECK` / 없는 직원 → `FOREIGN KEY` / `thanked_at`이 있는데 `read_at` NULL → `CHECK`
  - 내용 칸(sender_id · recipient_id · kind · reason · art · body · sent_on · created_at) UPDATE → `RAISE` 문구 `READ_ONLY`, `read_at` · `thanked_at` · `hidden_at` UPDATE는 됨
  - 카드가 있는 직원 DELETE → `FOREIGN KEY`
  - 카드 줄 DELETE는 **된다**(지우는 API가 없을 뿐 — 검사 정리용으로 막지 않는다)
- [ ] **Step 2: 실행해 실패 확인** — `cd backend && python -m hr_eval.sql.check_schema` → `cards` 표가 없어 실패.
- [ ] **Step 3: `021_cards.sql` 쓰기** — 위 제약 그대로. 머리 주석은 019와 같은 꼴(무엇 · 판 21 · 번호 규칙). `CHECK (sender_id <> recipient_id)`, `CHECK ((kind = 'cheer') = (reason IS NOT NULL))`, `CHECK (thanked_at IS NULL OR read_at IS NOT NULL)`, `UNIQUE (sender_id, recipient_id, sent_on)`, 색인 `(recipient_id, created_at)` · `(sender_id, created_at)`, 트리거 `BEFORE UPDATE OF sender_id, recipient_id, kind, reason, art, body, sent_on, created_at` → `RAISE(ABORT, 'READ_ONLY: 보낸 카드는 고칠 수 없어요.')`. FK는 `ON DELETE` 없이(지우지 못하게).
- [ ] **Step 4: 통과 확인** — `python -m hr_eval.sql.check_schema` · `python -m hr_eval.sql.check_sqlite_features` · `python -m auth_check` 모두 "전부 통과".
- [ ] **Step 5: 커밋** — `git commit -m "DB 판 21: 축하 · 칭찬 카드 (cards)"`

### Task 2: 카드 규칙 (순수 함수) · 잠금 · 자체 검사

**Files:**
- Create: `backend/cards.py` (이 작업은 규칙 · 잠금 · `check()`까지 — API는 Task 3)
- Modify: `.github/workflows/ci.yml` (`python -m paypin check` 다음에 `python -m cards check` 단계, 이름 "카드 규칙 검사")

**Interfaces:**
- Produces (Task 3 · 4 · 6이 씀):
  - `KINDS = ("cheer", "praise", "thanks", "comfort")`, `REASONS = ("birthday", "welcome", "anniv", "joy")`, `TEXT_MAX = 150`, `LINES_MAX = 3`
  - `class CardError(Exception)` — `.msg: str`, `.status: int`
  - `reasons_for(conn, uid: int, today: date) -> tuple[list[str], bool]` — (그 사람의 지금 축하 사유 목록 REASONS 순서, 조사 게시 중인가). 사람 줄 `(id, hire_date, birthday, birthday_public)`을 `auth.news_items([row], today)`에 넣어 kind `birthday`→birthday · `welcome`→welcome · `anniversary`→anniv, `celeb_events`(removed_at NULL · until_date ≥ today)의 joy → joy, condol → True. 대상은 `is_active=1 AND org_hidden=0`인 사람만(아니면 `([], False)`).
  - `options(reasons: list[str], condol: bool) -> list[dict]` — 조사면 `[{"kind":"comfort","reason":None}]`만, 아니면 사유마다 `{"kind":"cheer","reason":r}` + `{"kind":"praise","reason":None}` + `{"kind":"thanks","reason":None}`
  - `clean_text(v: object) -> str` — Global Constraints의 글 규칙. 실패 문구: 글자가 아니거나 비면 `"글을 적어 주세요."`, 150자 넘음 `"글은 150자까지 쓸 수 있어요."`, 줄 넘음 `"글은 세 줄까지 쓸 수 있어요."`, 글자 규칙 `"글에 쓸 수 없는 글자가 있어요."` — 모두 status 400
  - `seal_text(key: bytes, sender: int, recipient: int, sent_on: str, text: str) -> bytes` / `unseal_text(key, sender, recipient, sent_on, blob) -> str` — nonce 12바이트 + AESGCM, aad `f"card|{sender}|{recipient}|{sent_on}"`
  - `unread(conn, uid: int) -> int` — `recipient_id = uid AND read_at IS NULL`, 열쇠가 없으면 0
  - `check() -> None`

- [ ] **Step 1: `check()`에 테스트 먼저**
  ```python
  assert options(["birthday", "anniv"], False) == [{"kind": "cheer", "reason": "birthday"}, {"kind": "cheer", "reason": "anniv"},
                                                   {"kind": "praise", "reason": None}, {"kind": "thanks", "reason": None}]
  assert options(["birthday"], True) == [{"kind": "comfort", "reason": None}]   # 조사면 위로만
  assert options([], False) == [{"kind": "praise", "reason": None}, {"kind": "thanks", "reason": None}]
  assert clean_text("  고마워요 \r\n덕분이에요 ") == "고마워요\n덕분이에요"
  for bad, msg in ((" \n ", "글을 적어 주세요."), (None, "글을 적어 주세요."), ("가" * 151, "글은 150자까지 쓸 수 있어요."),
                   ("a\nb\nc\nd", "글은 세 줄까지 쓸 수 있어요."), ("a\x00b", "글에 쓸 수 없는 글자가 있어요."), ("a\ud800", "글에 쓸 수 없는 글자가 있어요.")):
      ...  # CardError 이고 .msg == msg, .status == 400
  assert clean_text("가" * 150) and clean_text("a\nb\nc") == "a\nb\nc"
  k = bytes(range(32)); b = seal_text(k, 1, 2, "2027-03-02", "고마워요")
  assert unseal_text(k, 1, 2, "2027-03-02", b) == "고마워요" and "고마워요".encode() not in b
  # 다른 날 · 다른 사람 자리에 옮겨 붙이면 안 풀림 (InvalidTag)
  ```
  `reasons_for`는 DB가 필요해 Task 3의 통합 검사에서 본다.
- [ ] **Step 2: 실행해 실패 확인** — `cd backend && python -m cards check` → 함수가 없어 실패.
- [ ] **Step 3: 위 Interfaces대로 구현.** `__main__`은 `paypin`과 같은 꼴(`check` → "cards 검사 통과", 그 밖은 사용법).
- [ ] **Step 4: 통과 확인** — `python -m cards check` → `cards 검사 통과`.
- [ ] **Step 5: 커밋** — `git commit -m "카드 규칙: 보낼 수 있는 종류 · 글 검사 · 잠금"`

### Task 3: 카드 API · 통합 검사

**Files:**
- Modify: `backend/cards.py` (Blueprint `bp` · 길 6개), `backend/app.py` (`import cards`, `app.register_blueprint(cards.bp)` — 주석 한 줄, push 다음)
- Modify: `backend/auth_check.py` (새 `check_cards(hr)`, `main()`의 `check_pay_pin(hr)` 다음에 부름)

**Interfaces:**
- Consumes: Task 2 함수 전부, `auth._db` · `auth._today` · `auth._now` · `auth._iso` · `g.actor.user_id`, `payslip.pay_key`
- Produces (Task 6~8 화면이 씀):
  - `GET /api/cards` → `{"ready": bool, "unread": int, "received": [..], "sent": [..]}` — 최근이 위, 각 500장까지.
    받은 줄 `{"id","kind","reason","art","text","date","from":{"id","name","title","dept"},"read":bool,"thanked":bool,"hidden":bool}`
    보낸 줄 `{"id","kind","reason","art","text","date","to":{"id","name","title","dept"},"read":bool,"thanked":bool}` (`title`은 `users.title`, 없으면 "")
  - `GET /api/cards/options?to=<uid>` → `{"options": options(...), "sentToday": bool}` · 받을 수 없는 사람 404 `"카드를 받을 수 없는 분이에요."`
  - `POST /api/cards` `{"to","kind","reason","art","text"}` → 201 `{"id"}` · 오류: 모양 400 · 받을 수 없음 404(위 문구) · 종류/사유가 지금 options에 없음 403 `"지금은 이 카드를 보낼 수 없어요."` · 오늘 이미 보냄 409 `"오늘은 이미 이 분께 카드를 보냈어요. 내일 다시 보내 주세요."` · 열쇠 없음 503 `"서버 설정이 아직 끝나지 않아 카드를 보낼 수 없어요."`
  - `POST /api/cards/<id>/read` → 200 `{"unread"}` (받은 사람만, 이미 읽었으면 그대로 200)
  - `POST /api/cards/<id>/thanks` → 200 (받은 사람만, 읽음도 함께 채움, 두 번째는 200이되 아무것도 안 바뀜)
  - `POST /api/cards/<id>/hide` `{"hidden": bool}` → 200 (받은 사람만, 숨길 때 읽음도 채움)
  - 받은 사람이 아닌 사람의 `<id>` 길은 모두 404 `"카드를 찾을 수 없어요."`(HR 포함)

- [ ] **Step 1: `check_cards(hr)` 먼저** — 가상 사람(6301~6306, 부서는 기존 seed 팀)을 `x(...)`로 넣고 `new_token`/`login`으로 로그인(가짜 하이웍스 `PEOPLE`에 더하는 방식은 `check_push_events` 참고), `HR_PAY_KEY`는 `check_payslips`처럼 이 검사 안에서 만들고 끝에 되돌린다. 테스트(이름은 출력 라벨):
  - `칭찬_보내기_받기`: A→B praise 201 → B 목록 received 1 · text 같음 · unread 1, A 목록 sent 1 · read False, DB `body`에 글이 그대로 없음
  - `하루_한장_동시`: A→B 같은 날 두 번째 409(문구) · 스레드 두 개로 동시에 보내도 1장(`check_phone`의 동시 패턴)
  - `생일_사유`: B 생일을 오늘로(`users.birthday` · `birthday_public=1`) → options에 cheer/birthday, 생일 알림 끈 사람(`birthday_public=0`)은 cheer 없음 · cheer/birthday 보내기 403
  - `새식구_입사기념_경사`: hire_date 오늘-10일 → welcome, 3년 전 이번 달 → anniv, `celeb_events` joy → joy
  - `조사_게시_뒤_축하_거부`: B에게 condol 게시 → options는 comfort만 · praise 403 · comfort 201
  - `받을_수_없는_사람`: 본인 · 비활성 · `org_hidden=1` · 없는 번호 → options/보내기 404 · **포털 사용 꺼진 사람은 201**, 그 사람이 포털 사용이 켜진 뒤 로그인하면 unread 1
  - `남의_카드_404`: C(HR 포함)가 B의 카드 `<id>`로 read/thanks/hide → 404, DB 그대로 · `GET /api/cards`에 남의 카드 없음
  - `읽음_고마워요_숨기기`: read → unread 0 / thanks → A 목록 thanked True, 두 번째도 200 · `thanked_at` 그대로 / hide true → received hidden True · unread 그대로 0 / hide false → hidden False
  - `글_검사`: 151자 · 줄바꿈 3번 · 빈칸만 → 400(각 문구), art 0/5/"1" · kind 이상 · cheer인데 reason 없음 → 400
  - `열쇠_없음`: 열쇠를 빼면 `GET` ready False · 빈 목록 · unread 0, `POST` 503(문구)
  - `감사로그_없음`: 위 일들 뒤 `audit_logs`에 `CARD`가 든 action 없음
  - 끝에 카드 줄을 지우고(Task 1에서 DELETE는 됨) 가상 사람은 비활성으로 둔다.
- [ ] **Step 2: 실행해 실패 확인** — `cd backend && python -m auth_check` → `/api/cards` 404로 실패.
- [ ] **Step 3: 길 6개 구현.** 쓰기는 `BEGIN IMMEDIATE` 한 트랜잭션(보내기는 options 재계산 → 409 검사 → INSERT, UNIQUE 위반도 409로). 모양 검사 실패는 400 `"보낸 내용의 모양이 올바르지 않아요."`. 오류 응답은 `{"error": 문구}`.
- [ ] **Step 4: 통과 확인** — `python -m auth_check` 전부 통과 · `python -m cards check` 통과.
- [ ] **Step 5: 커밋** — `git commit -m "카드 API: 보내기 · 목록 · 보낼 수 있는 종류 · 읽음 · 고마워요 · 숨기기"`

### Task 4: 알림 · 9시 묶음 · 처리할 일 숫자

**Files:**
- Modify: `backend/push.py` (`MESSAGES` 두 줄, `LABELS` 한 줄, `pending`에 한 줄, 머리 설명에 카드 한 줄)
- Modify: `backend/cards.py` (보내기 · 고마워요 commit 뒤 `push.notify`)
- Modify: `backend/auth_check.py` (`check_cards`에 알림 부분 — 가짜 `push.SEND`, `push.SYNC=True`, `auth._now` 고정은 `check_push_events`와 같게)

**Interfaces:**
- Consumes: Task 3 길, `cards.unread`
- Produces: `MESSAGES["card"] = ("카드가 왔어요", "눌러서 확인해 주세요", "ws.w3")`, `MESSAGES["card_thanks"] = ("보낸 카드에 고마워요가 왔어요", "눌러서 확인해 주세요", "ws.w3")`, `LABELS["cards"] = "새 카드"`(맨 끝), `pending`의 `n["cards"] = cards.unread(conn, uid)`

- [ ] **Step 1: 테스트 먼저** (`check_cards` 안, 금요일 10:00 KST로 고정):
  - `카드_알림`: A→B 보내기 → B에게만 한 통, 제목 "카드가 왔어요" · url `/?go=ws.w3` · 본문에 A 이름 · 글 없음
  - `고마워요_알림`: B thanks → A에게 "보낸 카드에 고마워요가 왔어요" 한 통 · 두 번째 thanks는 알림 없음
  - `근무시간_밖`: 토요일 10:00이면 보내기 · 고마워요 모두 알림 없음, 저장은 됨
  - `9시_묶음`: `push.pending(app, B)`에 `("cards", 1)`, 읽으면 빠짐 · `morning_text([("cards", 2)])["body"] == "새 카드 2건"`
- [ ] **Step 2: 실행해 실패 확인** — `python -m auth_check`.
- [ ] **Step 3: 구현** — notify는 응답 직전, commit 뒤. 알림 실패는 원래 응답에 영향 없음(지금 notify 규칙).
- [ ] **Step 4: 통과 확인** — `python -m auth_check` · `python -m push check` 통과(기존 `check_push_pending`의 합 검사도 그대로 — 카드가 없으면 0이라 빠진다).
- [ ] **Step 5: 커밋** — `git commit -m "카드 알림: 받으면 · 고마워요 · 9시 묶음 새 카드"`

### Task 5: 화면 바탕 — 그림 · 모양 · 자료 · 숫자 · 받은 카드 탭

**Files:**
- Create: `public/img/cards/{birthday,welcome,anniv,joy,condol,praise,thanks}-{1..4}.jpg` (스크래치패드 `wt-mock/public/card-art/*.jpg`를 그대로 복사 — 28장, 약 0.6MB)
- Modify: `public/index.html` — CSS(`.cm-*`, 시안 `css` 상수 그대로 + 아래 바꿈), 자료 · 함수(아래 Interfaces), `NAV` 워크스페이스 탭, `TODO`, `homePortalTasks`, `authBoot`

**Interfaces:**
- Consumes: Task 3 API
- Produces (Task 6 · 7이 씀):
  - `CARD_K = { cheer:{label:'축하', fg:'#B4232F', bg:'#FDECEC'}, praise:{label:'칭찬', fg:'#985A00', bg:'#FFF1D6'}, thanks:{label:'감사', fg:'#11734D', bg:'#E3F4EC'}, comfort:{label:'위로', fg:'#4D5566', bg:'#EEF0F4'} }`
  - `CARD_WHY = { birthday:'생일', welcome:'새 식구', anniv:'입사 기념', joy:'경사' }` (꼬리표 "축하 · 생일")
  - `cardSet(kind, reason) -> 'birthday'|'welcome'|'anniv'|'joy'|'condol'|'praise'|'thanks'`
  - `cardArt(kind, reason, n) -> html` (`<div class="cm-art"><img src="img/cards/{set}-{n}.jpg?v=1" alt="" loading="lazy"></div>`)
  - `cardFace({kind, reason, art, text, toName, fromName, date}) -> html` (시안 `face` 그대로, 날짜는 `M/D`)
  - `CARD_LIVE = { state:'', ready:false, unread:0, received:[], sent:[], opened:false }` · `async cardLoad()` (`GET /api/cards`, 끝나면 `renderSidebar(); renderContent()`)
  - 시연(서버 없음): `CARD_DEMO`(시안 `IN` · `OUT`과 같은 가상 카드 — `orgMembers` 사람) — `cardList()`가 `AUTH.live ? CARD_LIVE : CARD_DEMO`를 돌려준다
  - `TODO.w3 = ()=>cardList().unread`
  - `homePortalTasks`: 맨 끝에 `add(TODO.w3(), '새 카드 확인', 'ws', 'w3', '새 카드')`
  - `NAV` 워크스페이스 탭에 `{id:'w3', label:'받은 카드', render:cardContent}`
  - `cardContent()` — 시안 `cardBox` 그대로(받은 · 보낸 바꾸기, 요약 세 칸, 종류 걸러 보기, 카드 모음, "···" 숨기기 · "숨긴 카드 N장 보기" · 다시 보이기, 보낸 카드 줄). 안내는 `"카드는 받은 분과 보낸 분만 볼 수 있어요."`. 열쇠 없음(`live && !ready`)이면 맨 위 `kpiNote('서버 설정이 아직 끝나지 않아 카드를 보내고 받을 수 없어요.', 'info')`와 [카드 쓰기] 막음.
  - `cardSelfCheck()` (시작 — Task 6 · 7이 항목을 더함): `cardSet` 7가지 · `TODO.w3`가 안 읽은 수와 같음 · 홈 "새 카드 확인" 줄 · 숨긴 카드는 모음에서 빠지고 "숨긴 카드 1장 보기" · 실제 로그인인데 `ready:false`면 안내 한 줄 · [카드 쓰기] 막힘 · 시연 상태 되돌림
- 시안과 다르게 할 것: 시안의 `HR도 볼 수 없어요` 문구 없음, 받는 사람 이름 클릭(시연 명단 대신 `orgPeople()`), 카드 모음은 받은 날 순.

- [ ] **Step 1: `cardSelfCheck()` 먼저**(위 항목, `kpiLiveSelfCheck`처럼 상태를 저장 · 되돌림)
- [ ] **Step 2: 실패 확인** — devlive(`클로드 코드/devlive.py <worktree> <포트>`)로 HR 로그인 → 콘솔 `cardSelfCheck()` → 함수 없음.
- [ ] **Step 3: 구현** — `authBoot`의 `const kpi = kpiLiveLoad();`를 `Promise.all([kpiLiveLoad(), cardLoad()])`로(오늘 할 일 창이 카드 수까지 기다리게).
- [ ] **Step 4: 통과 확인** — `cardSelfCheck()` · `homeSelfCheck()` · `todoSelfCheck()` · `pushSelfCheck()` 통과, `python .ci/check-html.py` · `bash .ci/check-public.sh` 통과, 받은 카드 탭이 PC · 휴대폰 폭(390)에서 시안과 같은 모양(그림 잘림 없음). **그림 28장을 한 장씩 눈으로 보고 사람 얼굴 사진 · 이름 글자가 없음을 확인**(입사환영-1 사원증은 사람 모양 아이콘이어야 함).
- [ ] **Step 5: 커밋** — `git commit -m "받은 카드 탭: 그림 28장 · 카드 모음 · 숨기기 · 새 카드 숫자"`

### Task 6: 카드 보내기 — 창 · 보내는 곳 · 보내기 움직임

**Files:**
- Modify: `public/index.html` — `cardSend` 창, `celebShow` · `orgShow`에 단추, 답장, 보내기 움직임

**Interfaces:**
- Consumes: Task 5 전부, `GET /api/cards/options`, `POST /api/cards`
- Produces: `async cardSend({ to:{id,name,title,dept}|null, reason?:string })` — `to.id`는 **서버 직원 번호**(실제 로그인 조직도 사람은 `m.uid`, 시연은 `m.id`, 받은 카드의 `from.id`는 그대로). `to`가 없으면 이름 찾기 칸(`orgPeople()`에서 이름 · 부서, 본인 · 비활성 제외). 창을 열 때 options를 받아 종류 칩을 그린다(칩 이름: cheer는 `축하 · {CARD_WHY[reason]}`, 사유가 여럿이면 칩도 여럿). `reason`을 주면 그 칩이 처음 골라진다.
- 예시 문구(칩 3개, 누르면 글 칸에 들어감 — 그대로 씀):
  - birthday: `생일 축하해요! 오늘은 맛있는 거 많이 드세요.` · `생일 축하해요. 늘 고마워요.` · `행복한 생일 보내세요!`
  - welcome: `입사 환영해요! 궁금한 거 있으면 언제든 물어보세요.` · `함께하게 되어 반가워요.` · `첫 주 잘 보내세요. 응원할게요!`
  - anniv: `입사 기념일 축하해요. 늘 든든해요.` · `함께해 줘서 고마워요.` · `앞으로도 잘 부탁해요!`
  - joy: `진심으로 축하드려요!` · `좋은 소식 축하해요. 행복하세요.` · `결혼 축하드려요. 두 분 행복하세요.`
  - praise: `이번 일 정말 멋지게 해냈어요.` · `꼼꼼하게 챙겨 줘서 큰 도움이 됐어요.` · `늘 먼저 나서 줘서 든든해요.`
  - thanks: `바쁜데도 도와줘서 고마워요.` · `덕분에 일이 잘 끝났어요.` · `오늘도 수고 많았어요.`
  - comfort: `마음 깊이 위로를 전합니다.` · `힘든 시간 잘 이겨 내시길 바랍니다.` · `필요한 일 있으면 언제든 말씀해 주세요.`
- 보내는 곳: `celebShow` 사람 줄 `celeb-acts` 맨 앞 `[축하 카드]`(조사는 `[위로 카드]`, `cardSend({to, reason})` — 사유는 그 배너 kind에서), `orgShow` 연락처 단추 아래 `[칭찬 · 감사 카드 보내기]`(본인 · 비활성이면 없음), 받은 카드 탭 `[＋ 카드 쓰기]`, 받은 카드 `[답장 쓰기]`(`cardSend({to: 보낸 사람})`).
- 창 문구 · 모양은 시안 `draw()` 그대로(미리 보기는 PC만, 안내 `받는 분만 볼 수 있어요 · 같은 분께는 하루 한 장 · 보내면 휴대폰으로 알려 드려요`). `sentToday`면 [보내기]를 막고 `오늘은 이미 {이름} 님께 카드를 보냈어요`.
- 보내기: 글 규칙(Global Constraints)을 화면에서 먼저 보고, 단추를 막은 채 `POST` → 201이면 시안 `cardSent` 움직임 → 창 닫고 `kpiFlash('{이름} 님께 카드를 보냈어요.')` → `cardLoad()`. 실패면 움직임 없이 창 안 붉은 줄에 서버 문구, 단추 다시 켬.

- [ ] **Step 1: `cardSelfCheck()`에 항목 더하기** — 가짜 `fetch`(시안 · `payPinSelfCheck`의 `reply` 꼴)로: options가 조사면 칩 `위로` 하나 · 생일+입사기념이면 `축하 · 생일`·`축하 · 입사 기념`·`칭찬`·`감사` · 예시 칩이 사유별 문구 · 151자/줄 4개/빈칸이면 [보내기] 막힘(요청 안 감) · 보내는 동안 단추 막힘(두 번 눌러도 POST 한 번) · 403이면 창이 남고 `지금은 이 카드를 보낼 수 없어요.` · `sentToday`면 막힘 · 움직임 줄이기면 `.cm-fly` 안 생김 · `celebShow('condol')` 줄에 `[위로 카드]`만 · `orgShow`에 단추
- [ ] **Step 2: 실패 확인** — `cardSelfCheck()`
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `cardSelfCheck()` · `celebSelfCheck()` · `orgSelfCheck()` 통과, devlive에서 가상직원 → 가상팀장에게 실제로 보내 받은 쪽 목록에 보임
- [ ] **Step 5: 커밋** — `git commit -m "카드 보내기: 소식 · 조직도 · 카드함 · 답장에서, 봉투에 넣어 날아가기"`

### Task 7: 카드가 왔어요 — 도착 창 · 움직임 · 고마워요

**Files:**
- Modify: `public/index.html` — `cardArrive`, `openFx` · `burst` · `heartPop`(시안 그대로), `todayPopAuto`, `cardContent` 자동 열기, `cardOpen`

**Interfaces:**
- Consumes: Task 5 · 6, `POST /api/cards/<id>/read|thanks`
- Produces: `cardArrive(i=0, list, auto)` — list 없으면 안 읽은 받은 카드(오래된 것부터). 보이는 카드는 바로 `read` 요청(실패해도 창은 그대로). `auto`면 닫을 때(X · 마지막 카드 뒤) `homeTodayPop()`. 처음 여는 카드는 봉투째(`openFx(stage, c, true)`), 넘겨 본 카드로 돌아오면 가볍게.
  - `todayPopAuto`: `phoneSelf` · 알림 묻기 다음, `homeTodayPop()` 앞에 `if(cardList().unread) return cardArrive(0, null, true);`
  - `cardContent`: 그릴 때 `!CARD_LIVE.opened && unread`면 `opened = true`로 바꾸고 다음 틱에 `cardArrive(0)` — 알림(`?go=ws.w3`)으로 들어와도 같은 길
  - `cardOpen(id)` — 카드함에서 크게 보기(가볍게 떠오르기, 숨기기 링크)
  - `cardThanks(btn, card)` — 이미면 아무것도 안 함, 아니면 `heartPop` → `POST thanks` → `kpiFlash('{이름} 님께 고마워요를 보냈어요.')`
  - 움직임 길이 · 종류별 효과 개수는 시안 값 그대로(설계 4-6 표). 움직이는 중에도 X로 닫을 수 있고, 닫으면 남는 조각(`.cm-ep` · `.cm-p`)이 없다.

- [ ] **Step 1: `cardSelfCheck()`에 항목 더하기** — 안 읽은 2장이면 todayPopAuto가 도착 창(1/2), 닫으면 오늘 할 일 창 · 넘기면 read 요청이 카드마다 한 번 · 고마워요 두 번 눌러도 요청 한 번 · 움직임 줄이기면 `.cm-ep` · `.cm-p`가 남지 않고 카드가 바로 보임 · 조사 카드는 `.cm-p` 3개 이하 · 카드함을 처음 열면 도착 창 한 번(두 번째는 안 뜸)
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `cardSelfCheck()` · `homeSelfCheck()` · `pushSelfCheck()` · `tourSelfCheck()` 통과, devlive에서 PC · 휴대폰(390, 갤럭시 UA)으로 봉투 → 카드 → 효과가 끊김 없이 보이고 끝난 뒤 남는 조각 없음(`document.querySelectorAll('.cm-ep,.cm-p,.cm-fly,.cm-pop').length===0`)
- [ ] **Step 5: 커밋** — `git commit -m "카드가 왔어요: 봉투가 열리는 도착 창 · 종류별 효과 · 고마워요"`

### Task 8: 문서 · 전체 검사 · 화면 확인

**Files:**
- Modify: `CLAUDE.md` (워크스페이스 절 아래 "받은 카드 · 카드 보내기" 한 절 — 규칙 원본은 설계 문서라고 적고 코드가 지키는 약속만: 길 · 숫자 · 알림 · 잠금 · 움직임 · 그림 바꾸는 법(`?v=`)), `DEPLOY.md` (DB 판 21 확인 한 줄 — 서버 일 없음, `HR_PAY_KEY` 이미 있음), `README.md` (구현된 화면 목록에 한 줄), 설계 문서 머리에 "구현 완료 (PR #…)"는 PR 번호가 나온 뒤
- [ ] **Step 1: 전체 검사** — 저장소 규칙(`hr_portal_repo_ops`): `.ci/check-*.sh` 4개 · `check-html.py` · `hr_eval.selfcheck` · `check_schema` · `check_sqlite_features` · `auth_check` · `cards check` · `push check` · `paypin check` 모두 통과
- [ ] **Step 2: 화면 확인** — devlive로 가상직원 · 가상팀장 · HR 세 사람, PC 1280 · 휴대폰 390에서 보내기 · 도착 창 · 카드함 · 소식 · 조직도를 찍어 시안과 비교(그림은 `클로드 코드/뉴디자인/칭찬 카드 시안/구현/`에, 저장소에는 넣지 않음). 브라우저 콘솔 오류 없음.
- [ ] **Step 3: 커밋** — `git commit -m "문서: 축하 · 칭찬 카드"`
