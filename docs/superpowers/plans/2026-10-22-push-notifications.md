# 휴대폰 알림 · 앱 아이콘 숫자 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 처리할 일이 생기면 등록된 휴대폰으로 웹 푸시를 보내고 앱 아이콘에 숫자를 붙이며, 실제 로그인의 평가 진행 탭 · 평가 숫자 · KPI 기간 안내를 고친다(설계 §7.1, 2026-12-01 전 운영 반영).

**Architecture:** 서버에 `backend/push.py` 하나(키 · 구독 저장 · 보낼 대상 · 문구 · 처리할 일 개수 · 보내기 · 아침 9시 명령)를 두고, 일이 생기는 기존 함수가 commit 뒤 `push.notify(...)`만 부른다. 보내기는 뒤로 돌린 스레드에서 한다. 화면은 `public/sw.js`(알림 표시 · 누르면 이동 · 아이콘 숫자)와 `index.html`의 알림 켜기 칸 · `?go=` 이동 · 아이콘 숫자 맞추기를 더한다.

**Tech Stack:** Flask 3 · SQLite · `pywebpush`(새 의존성) · `cryptography`(이미 있음, 키 만들기) · Service Worker / Push API / Badging API · 기존 검사 하네스(`backend/auth_check.py`, 화면 `*SelfCheck()`).

**Spec:** `docs/superpowers/specs/2026-10-22-push-notifications-design.md` (승인 2026-10-22)

## Global Constraints

- 보내는 때: 근무시간 = **평일(월~금) 9:00 ≤ 한국 시간 < 18:00** (`auth.KST`, UTC+9 고정). 근무시간 밖의 일은 보내지 않는다 — 다음 근무일 9시 묶음에 저절로 들어간다. 공휴일은 빼지 않는다.
- 아침 묶음: 평일 9:00 KST, 남은 일 1건 이상인 사람에게 **한 통**, 같은 날 두 번 돌지 않는다. 앱 안에 예약 작업을 두지 않는다(gunicorn 작업자 2개) — 서버 systemd 타이머가 `docker compose exec -T api python -m push morning`.
- 알림 문구에 **금액 · 다른 사람 이름 · 평가 등급 없음**. 문구는 `push.MESSAGES` 한 곳에만 둔다.
- 받는 기기: 휴대폰 세션(`sessions.kind='phone'`)만, `revoked_at IS NULL`, 마지막 사용 30일 안(`auth.PHONE_IDLE`), `expires_at` 이후 아님, 활성 · 포털 사용 켜진 직원. PC로는 보내지 않는다.
- 대표이사가 KPI 승인을 맡긴 동안(`ceo_delegation`에 그 대표이사 줄): 승인 요청 알림은 활성 HR 전원에게만, 대표이사의 처리할 일 개수 · 아이콘 숫자 · 9시 묶음에서 처리함(inbox) 몫을 뺀다. 왼쪽 메뉴 숫자는 그대로.
- HR에게 가는 알림은 활성 HR 전원(`user_roles.role='HR_ADMIN'`, `users.is_active=1`).
- 연봉계약서 문의(`contract_ask`)는 **알림만** 보내고 처리할 일 개수(`pending` · 아이콘 숫자 · 9시 묶음)에는 넣지 않는다 — 계약서 구현(PR #178)이 이 수를 왼쪽 메뉴가 아니라 계약서 화면 안의 "직원 연봉계약 관리" 숫자로만 세기 때문(설계 §7의 "개수 = 왼쪽 메뉴 합계" 원칙을 따름, 내 판단 — PR에 적는다).
- 비밀값: `.env`의 `HR_PUSH_PUBLIC`(base64url, 65바이트 비압축 점) · `HR_PUSH_PRIVATE`(base64url, 32바이트). PEM 금지(`check-secrets.sh`). 저장소 · 로그 어디에도 값이 남지 않는다. 키가 없으면 보내기만 조용히 건너뛰고 다른 기능은 그대로.
- VAPID subject = `https://hr.abyz-lab.work` (개인 메일 주소 금지).
- 보내기는 요청을 붙잡지 않는다(HR의 급여명세서 공개가 바로 끝나야 한다). 실패는 원래 일을 되돌리지 않는다. 로그에는 개수와 상태 코드만(이름 · 주소 · 문구 없음).
- 새 표 번호: **017** (main 마지막 016).
- 저장소는 공개 — 실제 이름 · 전화 · 금액을 코드 · 커밋 · PR · 로그에 넣지 않는다. 검사 자료는 `auth_check.py`의 가상 사람(가상1~5).
- 화면 문구는 "~요"체, 한 단어만 다음 줄로 넘어가지 않게 끊는다. 새 화면을 파랑 한 색으로만 채우지 않는다(종 아이콘은 `--bg-danger` / `--coral-text`).
- 실제 로그인의 **평가 결과 탭(보고서 엑셀 · 이의 신청 포함)은 그대로** 둔다. 숨기는 것은 평가 진행 탭뿐.

## Review Focus

1. 근무시간 경계와 날짜: KST 금요일 17:59 → 보냄, 18:00 → 안 보냄, 토요일 → 안 보냄, UTC로 일요일 23:30(= KST 월요일 08:30) → 안 보냄, UTC 월요일 00:00(= KST 9:00) → 보냄. Task 1 테스트로 고정.
2. 보낼 대상에서 빠져야 할 사람: 해제된 휴대폰 세션 · 30일 지난 세션 · PC 세션 · 비활성 직원 · 포털 사용 꺼진 직원. 하나라도 받으면 다른 사람 휴대폰으로 갈 수 있다. Task 1 테스트.
3. 처리할 일 개수 ≠ 화면 숫자: 대표이사 위임 중, HR 대신 몫, 팀장/본부장만의 월간 확인 · 답글, 열쇠(`HR_PAY_KEY`) 없음일 때. Task 3 테스트가 경우마다 `pending()`을 `/api/...` 응답에서 화면이 세는 값과 비교한다.
4. 푸시 서버가 404/410을 주면 구독을 지우고, 500 · 연결 실패면 지우지 않고 다음 사람으로 넘어간다 — 한 사람의 실패가 나머지를 막지 않는다. Task 4 테스트.
5. 휴대폰에서 알림을 "허용 안 함"으로 한 뒤 · 아이폰 사파리(설치 안 함)에서 · 서버에 키가 없을 때 알림 켜기 칸이 엉뚱하게 뜨지 않는다. Task 5의 `pushSelfCheck()`.

---

### Task 1: 푸시 바탕 — 표 017 · 키 · 문구 · 근무시간 · 보낼 대상 · 보내기 함수

**Files:**
- Create: `backend/hr_eval/sql/017_push.sql`
- Create: `backend/push.py`
- Modify: `backend/hr_eval/sql/check_schema.py` (기대 표 목록 · 판 17 검사, 기존 판 검사 함수 모양을 따라 `check_push_v17` 추가 후 `main`에 연결)
- Modify: `backend/requirements.txt` (`pywebpush` — 설치 가능한 최신 2.x를 `pip index versions pywebpush`로 확인해 `==` 고정)
- Modify: `docker-compose.yml` (api 환경에 `HR_PUSH_PUBLIC=${HR_PUSH_PUBLIC:-}` · `HR_PUSH_PRIVATE=${HR_PUSH_PRIVATE:-}`), `.env.example` (두 줄 + "서버에서 `docker compose exec -T api python -m push keygen`으로 만들어 붙인다" 안내, 값은 비워 둔다)
- Modify: `.github/workflows/ci.yml` (평가·계약 도메인 검사 job, `python -m contract_doc` 다음에 `- name: 휴대폰 알림 문구 · 근무시간 · 키 자체 검증` / `run: python -m push check`)

**Interfaces:**
- Produces (모두 `backend/push.py`):
  - 표 `push_subscriptions(token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash), user_id INTEGER NOT NULL REFERENCES users(id), endpoint TEXT NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL, created_at TEXT NOT NULL)` + `INDEX ON push_subscriptions(user_id)`; 표 `push_morning(day TEXT PRIMARY KEY, people INTEGER NOT NULL, ran_at TEXT NOT NULL)`.
  - `SUBJECT = "https://hr.abyz-lab.work"`, `TTL_EVENT = 43200`, `TTL_MORNING = 32400`
  - `in_work_hours(now: datetime) -> bool` (aware datetime 아무 시간대)
  - `MESSAGES: dict[str, tuple[str, str, str]]` = kind → (제목 틀, 내용, go). 값은 아래 표 그대로.
  - `LABELS: dict[str, str]` = pending 종류 → 9시 묶음에 쓰는 이름(아래 표).
  - `message(kind: str, **fmt) -> dict` → `{"title", "body", "url"}`; url = `"/?go=" + go` (go가 빈 값이면 `"/"`).
  - `morning_text(items: list[tuple[str, int]]) -> dict` → 제목 `"처리할 일이 N건 있어요"`(N = 개수 합), 내용 = 종류 이름을 `" · "`로 이음, 2건 이상인 종류는 `"{이름} {n}건"`, url `"/"`.
  - `keys() -> tuple[str, str] | None` — 환경변수 둘을 읽어 base64url로 풀었을 때 65바이트(첫 바이트 0x04) · 32바이트가 아니면 None.
  - `keygen() -> tuple[str, str]` — `cryptography`로 P-256 키를 만들어 (공개, 개인) base64url(패딩 없음).
  - `targets(conn, uids: Iterable[int], now: datetime) -> list[tuple[int, str, dict]]` — (user_id, token_hash, subscription_info) — subscription_info = `{"endpoint", "keys": {"p256dh", "auth"}}`. Global Constraints의 받는 기기 조건으로 거른다.
  - `SEND: Callable[[dict, dict, tuple[str, str], int], int]` — (subscription_info, payload, keys, ttl) → HTTP 상태 코드. 기본값 `_webpush`(pywebpush `webpush(..., vapid_private_key=개인키 문자열, vapid_claims={"sub": SUBJECT}, ttl=ttl)`; `WebPushException`이면 응답 상태 코드, 연결 실패면 0). 검사는 이 이름을 바꿔 끼운다.
  - CLI: `python -m push check` (자체 검증 — 아래 Step 1의 assert들), `python -m push keygen` (두 줄 `HR_PUSH_PUBLIC=...` · `HR_PUSH_PRIVATE=...`을 출력만 한다). `morning`은 Task 3.

| kind | 제목 | 내용 | go |
|---|---|---|---|
| `payslip` | `{month}월 급여명세서가 올라왔어요` | 눌러서 확인해 주세요 | `pay.p1` |
| `withholding` | `{year}년 원천징수영수증이 올라왔어요` | 눌러서 확인해 주세요 | `pay.p3` |
| `contract_sign` | 서명할 연봉계약서가 있어요 | 눌러서 내용을 확인하고 서명해 주세요 | `pay.p2` |
| `contract_ask` | 답변할 연봉계약서 문의가 있어요 | 눌러서 확인해 주세요 | `pay.p2` |
| `kpi_inbox` | 팀원 KPI 승인 요청이 왔어요 | 눌러서 확인해 주세요 | `perf.e1` |
| `kpi_back` | KPI 계획서가 돌아왔어요 | 사유를 확인하고 다시 작성해 주세요 | `perf.e1` |
| `kpi_memo` | KPI 확인 메모가 왔어요 | 눌러서 확인해 주세요 | `perf.e1` |
| `kpi_reply_to_author` | 확인 메모에 새 답글이 달렸어요 | 눌러서 확인해 주세요 | `perf.e4` |
| `kpi_reply_to_owner` | 확인 메모에 새 답글이 달렸어요 | 눌러서 확인해 주세요 | `perf.e1` |

`LABELS` (화면 `homePortalTasks` 제목과 같은 말): `p1` 새 급여명세서 · `p2` 연봉계약서 서명 · `p3` 새 원천징수영수증 · `inbox` 처리할 KPI · `back` 반려된 KPI 다시 작성 · `prev` KPI 지난달 한 줄 쓰기 · `memos` 확인 메모 확인 · `check` KPI 월간 확인 · `replies` 확인 메모에 새 답글. 순서도 이대로.

- [ ] **Step 1: `python -m push check`가 할 자체 검증을 먼저 쓴다 (실패해야 함)**

```python
KST = timezone(timedelta(hours=9))
assert in_work_hours(datetime(2026, 10, 23, 17, 59, tzinfo=KST))          # 금 17:59
assert not in_work_hours(datetime(2026, 10, 23, 18, 0, tzinfo=KST))       # 금 18:00
assert not in_work_hours(datetime(2026, 10, 24, 10, 0, tzinfo=KST))       # 토
assert not in_work_hours(datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc))  # KST 월 08:30
assert in_work_hours(datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc))   # KST 월 09:00
assert message("payslip", month=10) == {"title": "10월 급여명세서가 올라왔어요", "body": "눌러서 확인해 주세요", "url": "/?go=pay.p1"}
assert morning_text([("p1", 1), ("inbox", 2)]) == {"title": "처리할 일이 3건 있어요", "body": "새 급여명세서 · 처리할 KPI 2건", "url": "/"}
for t, b, _ in MESSAGES.values():   # 금액 · 이름 자리 없음
    assert not re.search(r"\d[\d,]*\s*원|\{name\}|\{amount\}|등급", t + b)
pub, priv = keygen(); os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv); assert keys() == (pub, priv)
os.environ["HR_PUSH_PRIVATE"] = "short"; assert keys() is None
```

- [ ] **Step 2:** `cd backend && python -m push check` → 실패(모듈 없음) 확인.
- [ ] **Step 3:** 표 017, `push.py`의 위 Interfaces, check_schema 연결, requirements · compose · .env.example · ci.yml을 만든다. `targets()`는 `sessions` JOIN `users` 한 쿼리.
- [ ] **Step 4:** `python -m push check` 통과, `python -m hr_eval.sql.check_schema`(판 17 · 기대 표에 둘 추가) · `python -m hr_eval.sql.check_sqlite_features` 통과, `bash .ci/check-secrets.sh` 통과.
- [ ] **Step 5:** `targets()` 검사를 `auth_check.py`에 `check_push_targets(hr)`로 더한다(가상3에게 휴대폰 세션 · 구독을 직접 넣고: 살아 있음 → 1줄 / `revoked_at` 넣음 → 0 / `last_seen_at` 31일 전 → 0 / PC 세션 → 0 / `is_active=0` → 0 / `portal_enabled=0` → 0, 끝나면 되돌림). `main()`의 `check_contracts(hr)` 다음에 부른다. `python -m auth_check` 통과.
- [ ] **Step 6:** Commit `휴대폰 알림 바탕: 표 017 · 키 · 문구 · 근무시간 · 보낼 대상`

### Task 2: 구독 주소 — `/api/push/key` · `subscribe` · `unsubscribe` + 로그아웃·해제 때 지우기

**Files:**
- Modify: `backend/push.py` (`bp = Blueprint("push", __name__)` 와 세 주소)
- Modify: `backend/app.py` (`import push` · `app.register_blueprint(push.bp)` — 다른 blueprint 줄 모양 그대로 주석 한 줄)
- Modify: `backend/auth.py` — `logout`(현재 세션 `g.session_hash`), `phone_register`의 옛 휴대폰 해제 지점, `phone_revoke`, `members_phone_revoke`(그 사람의 휴대폰 세션)에서 `push.forget(conn, token_hashes)` 호출. 순환 import를 피하려면 함수 안에서 `import push`.
- Test: `backend/auth_check.py` `check_push_api(hr)`

**Interfaces:**
- Produces: `GET /api/push/key` → 키 있으면 `{"key": 공개키}` 200, 없으면 404. `POST /api/push/subscribe` 본문 `{"endpoint": https URL ≤ 1000자, "keys": {"p256dh": ≤ 200자, "auth": ≤ 100자}}` → 휴대폰 세션이 아니면 403, 형식이 틀리면 400, 맞으면 `INSERT OR REPLACE` 후 `{"ok": true}`. `POST /api/push/unsubscribe` → 이 세션 줄 삭제 `{"ok": true}`. 세 주소 모두 기존 `_guard`(로그인 · 같은 출처)를 그대로 받는다.
- Produces: `forget(conn, token_hashes: Iterable[str]) -> None`.

- [ ] **Step 1: 실패하는 검사** — `check_push_api(hr)`: 키 없음 → `GET /api/push/key` 404 / 키 넣고 → 200 · 공개키 같음 / PC 세션(hr)으로 subscribe → 403 / 휴대폰 세션 만들기는 `check_phone`이 쓰는 방식(코드 → register)으로 가상3 휴대폰 클라이언트 → subscribe 200 · 표에 1줄(user_id 3) / 같은 세션 다시 → 여전히 1줄(덮어씀) / endpoint가 `http://` → 400 / Origin 없이 POST → 403 / unsubscribe → 0줄 / 다시 subscribe 후 `/api/phone/revoke` → 0줄 / 다시 subscribe 후 HR의 `/api/members/3/phone/revoke` → 0줄 / 다시 subscribe 후 `/api/auth/logout` → 0줄.
- [ ] **Step 2:** `python -m auth_check` → 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `python -m auth_check` 통과(앞선 검사 전부 그대로 통과).
- [ ] **Step 5:** Commit `휴대폰 알림: 구독 주소 · 로그아웃·해제 때 구독 지우기`

### Task 3: 처리할 일 개수 `pending()` · 아침 9시 `python -m push morning`

**Files:**
- Modify: `backend/push.py`
- Test: `backend/auth_check.py` `check_push_pending(hr)`

**Interfaces:**
- Consumes: Task 1 `targets`, `SEND`, `keys`, `morning_text`, `LABELS`, `TTL_MORNING`.
- Produces:
  - `pending(flask_app, uid: int) -> list[tuple[str, int]]` — 0인 종류는 빼고 `LABELS` 순서. `flask_app.test_request_context()` 안에서 `g.actor = auth.actor_of(auth._db(), uid)`로 그 사람을 세워 화면과 같은 서버 함수를 쓴다:
    - `p1` = `SELECT COUNT(*) FROM payslips p JOIN payslip_months m ON m.ym = p.ym WHERE p.user_id = ? AND m.status = 'OPEN' AND p.seen_at IS NULL` (열쇠 `payslip.pay_key()`가 없으면 0 — 화면도 빈 목록)
    - `p3` = 같은 모양으로 `withholding` / `withholding_years` (열쇠 없으면 0)
    - `p2` = `SELECT COUNT(*) FROM contracts WHERE user_id = ? AND status = 'SENT'` (열쇠 없으면 0)
    - `inbox` · `memos` · `check` · `replies` = `kpi.inbox()`의 JSON(`items` 길이, `review`). `check` · `replies`는 직책이 팀장 · 본부장 · 대표이사이거나 HR일 때만(화면 `kvCanUse`). **대표이사이고 `ceo_delegation`에 본인 줄이 있으면 `inbox` = 0.**
    - `back` = `kpi.plan_json(conn, uid, kpi.plan_year())`의 `noteKind == "RETURNED"`면 1
    - `prev` = `kpi.progress_json(conn, uid, year)["todo"]["prev"]` (None이면 0)
  - `morning(flask_app, now: datetime) -> int` — 평일(KST)이 아니면 0. 키가 없으면 0(줄도 쓰지 않음). 아니면 휴대폰 구독이 있는 활성 직원마다 `pending()` → 1건 이상이면 `morning_text` + `"badge": 합계` + `"tag": "morning"`을 `SEND`(ttl `TTL_MORNING`) → 404/410이면 구독 삭제. (최종 검토 반영) 보내기 전에 `INSERT OR IGNORE`로 오늘 줄을 먼저 잡고(이미 있으면 0 — 겹쳐 도는 실행이 두 번 보내지 않게), 끝나면 `UPDATE`로 보낸 사람 수를 고친다. 보낸 사람 수를 돌려준다.
  - CLI `python -m push morning` — `from app import app` 후 `morning(app, auth._now())`, 보낸 사람 수만 출력.

- [ ] **Step 1: 실패하는 검사** `check_push_pending(hr)` — `push.SEND`를 호출을 적어 두는 가짜(상태 201)로 바꾸고, 가상 사람들로:
  - 공개된 달의 안 본 명세서 1 → `pending`에 `("p1", 1)`, `/api/payslips`가 주는 안 본 수와 같음
  - KPI 제출 1건(가상3 → 팀장 가상2) → 가상2 `("inbox", 1)`이 `/api/kpi/inbox`의 `items` 길이와 같음
  - 돌려보냄 → 가상3 `("back", 1)`
  - 대표이사 위임: 기존 검사들이 대표이사를 만드는 방식으로 대표이사 · 그 직속 1명을 두고 제출 → 위임 끔이면 대표이사 `inbox` 1, `ceo_delegation`에 줄 넣으면 0, 같은 때 HR `inbox`에는 대신 몫 1
  - `HR_PAY_KEY` 없음 → `p1` · `p2` · `p3` 0
  - `morning(app, 금 09:00 KST)` → 남은 일 있는 휴대폰 구독자에게만 1통씩, 제목 `처리할 일이 N건 있어요`, `badge == N`, `tag == "morning"` / 같은 날 다시 → 0 · 보낸 것 늘지 않음 / 토요일 → 0 / 가짜 SEND가 410 → 그 구독 삭제
  - 끝나면 넣은 자료를 되돌린다(뒤 검사가 영향받지 않게).
- [ ] **Step 2:** `python -m auth_check` 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `python -m auth_check` 통과.
- [ ] **Step 5:** Commit `휴대폰 알림: 처리할 일 개수 · 아침 9시 묶음`

### Task 4: 일이 생길 때 보내기 — `notify()`와 일곱 군데 연결

**Files:**
- Modify: `backend/push.py`
- Modify: `backend/payslip.py` `_toggle` · `backend/withholding.py` `_toggle` · `_store`(mode "some"이고 그 해가 이미 OPEN) · `backend/kpi.py` `submit` · `request_change` · `sched_request` · `send_back` · `memo_add` · `memo_edit` · `memo_reply` · `backend/contract.py` `send` · `cancel`(then == "resend") · `ask`
- Test: `backend/auth_check.py` `check_push_events(hr)`

**Interfaces:**
- Consumes: Task 1 · 3.
- Produces:
  - `SYNC: bool = False` — 검사에서 True로 두면 스레드 없이 바로 보낸다.
  - `notify(uids: Iterable[int], kind: str, **fmt) -> None` — 요청 안에서 **commit이 끝난 뒤** 부른다. 키가 없거나 `in_work_hours(auth._now())`가 아니면 아무것도 안 한다. 중복 · 0 · None 번호를 뺀다. `current_app._get_current_object()`를 잡아 데몬 스레드(`SYNC`면 바로)에서 `deliver(...)`. 절대 예외를 밖으로 내지 않는다(`logger.exception`에 개수만).
  - `deliver(flask_app, uids: list[int], msg: dict, ttl: int) -> int` — 사람마다 `pending()` 합계를 `badge`로 붙여 그 사람의 `targets()`에 `SEND`; 404/410은 `forget`; 그 밖의 실패는 건너뛰고 계속; 보낸 수를 돌려준다. 자기 DB 연결은 test_request_context의 `auth._db()`.
  - `approvers(conn, owner: int) -> list[int]` — `manager_of(owner, *load_org(conn))`; 없거나 그 관리자가 위임을 켠 대표이사면 활성 HR 전원(owner 본인 제외). `kpi.inbox()`의 `proxy()`와 같은 규칙(단, 위임 중인 대표이사는 빠진다).
  - `hr_admins(conn) -> list[int]`.

연결 지점과 받는 사람:

| 함수 | 언제 | 받는 사람 | kind |
|---|---|---|---|
| `payslip._toggle` | `to == "OPEN"`이고 상태가 실제로 바뀐 경우, COMMIT 뒤 | `SELECT user_id FROM payslips WHERE ym = ?` | `payslip`, `month=int(ym[5:7])` |
| `withholding._toggle` | 같은 조건 | `SELECT user_id FROM withholding WHERE year = ?` | `withholding`, `year=y` |
| `withholding._store` | mode "some"이고 그 해가 이미 OPEN, 저장 뒤 | 이번에 올린 사람들 | `withholding` |
| `kpi.submit` · `request_change` · `sched_request` | 저장 뒤 | `approvers(conn, 본인)` | `kpi_inbox` |
| `kpi.send_back` (RETURNED만, `reject`는 아님) | 저장 뒤 | 경로의 `uid` | `kpi_back` |
| `kpi.memo_add` · `memo_edit` | 저장 뒤 | 메모 대상(주인) | `kpi_memo` |
| `kpi.memo_reply` | 저장 뒤 | 주인이 쓰면 작성자(`kpi_reply_to_author`), 작성자가 쓰면 주인(`kpi_reply_to_owner`) | 왼쪽 |
| `contract.send` | `with` 블록 뒤 | 이번에 SENT로 바뀐 줄의 `user_id`들 | `contract_sign` |
| `contract.cancel` | `then == "resend"`이고 새 줄이 생긴 뒤 | 그 `uid` | `contract_sign` |
| `contract.ask` | 저장 뒤 | `hr_admins(conn)` | `contract_ask` |

- [ ] **Step 1: 실패하는 검사** `check_push_events(hr)` — `SYNC = True`, 가짜 `SEND`(호출 기록), `auth._now`를 근무시간(2026-10-23 10:00 KST)으로 고정(기존 검사들이 시각을 고정하는 방식을 따른다), 키 넣기, 받는 사람들에게 휴대폰 구독을 직접 넣는다. 확인:
  - 명세서 공개 → 그 달 명세서가 있는 구독자에게만 1통, 제목 `{달}월 급여명세서가 올라왔어요`, payload `badge` = 그 사람 `pending` 합계, 이미 OPEN인 달을 다시 공개 → 0통
  - 원천징수 공개 · "some" 올리기 → 같은 모양
  - KPI 제출 → 팀장에게 `kpi_inbox`; 관리자 없는 사람 → HR 전원; 위임 켠 대표이사 직속 → 대표이사 0통 · HR 1통
  - 돌려보냄 → 주인 `kpi_back`, `reject` → 0통
  - 메모 · 답글 → 표대로
  - 계약서 발송 · 파기 후 재발송 → `contract_sign`, 문의 → HR 전원 `contract_ask`
  - 시각을 금 18:00으로 → 0통; 키 없음 → 0통 · 원래 일은 성공
  - 가짜 SEND가 500 → 원래 응답 200 그대로 · 구독 남음, 410 → 구독 삭제
  - payload의 제목 · 내용에 가상 이름(`가상`)이 없음
- [ ] **Step 2:** `python -m auth_check` 실패 확인.
- [ ] **Step 3:** 구현.
- [ ] **Step 4:** `python -m auth_check` · `python -m hr_eval.selfcheck` 통과.
- [ ] **Step 5:** Commit `휴대폰 알림: 일이 생기면 보내기 (명세서 · 영수증 · KPI · 메모 · 계약서)`

### Task 5: 화면 — 서비스 워커 · 알림 켜기 칸 · `?go=` · 아이콘 숫자

**Files:**
- Create: `public/sw.js`
- Modify: `public/index.html`
- Modify: `nginx.conf` (`location = /sw.js { add_header Cache-Control "no-cache"; try_files $uri =404; }` — `location /` 앞)

**Interfaces:**
- Consumes: Task 2 주소, Task 1 payload 모양 `{title, body, url, badge, tag?}`.
- Produces (`index.html`):
  - `const PUSH = { state:'', key:'' }` — state: `''`(아직) · `'off'`(못 함 — 칸 없음) · `'ask'` · `'denied'` · `'ios'`(아이폰인데 홈 화면 앱 아님) · `'on'`
  - `pushState(env) -> string` — 순수 함수. env = `{ live, phone, sw, push, key, perm, ios, standalone }` → 위 state. 규칙: `!live || !phone || !key` → `'off'`; `ios && !standalone` → `'ios'`; `!sw || !push` → `'off'`; `perm==='granted'` → `'on'`; `perm==='denied'` → `'denied'`; 그 밖 `'ask'`.
  - `async pushInit()` — `authBoot`가 실제 로그인을 끝낸 뒤(`me.kind==='phone'`일 때만) 부른다: `/api/push/key`(404면 `'off'`), `navigator.serviceWorker.register('/sw.js')`, state 계산, `'on'`이면 조용히 `pushManager.subscribe({ userVisibleOnly:true, applicationServerKey })` → `POST /api/push/subscribe`, 떠 있는 알림 모두 `close()`, `pushBadgeSync()`.
  - `async pushEnable()` — 칸의 [켜기]: `Notification.requestPermission()` → granted면 구독 · 저장 · 토스트 "알림을 켰어요. 처리할 일이 생기면 알려 드릴게요" · 칸 사라짐, denied면 `'denied'`로 다시 그림.
  - `pushCard() -> string` — 홈(`homeContent`)에서 인사 배너 바로 다음, 휴대폰 폭에서만. 상태별 문구는 설계 §4.1 표 그대로: ask = **휴대폰 알림 켜기** / "처리할 일이 생기면<br>휴대폰으로 알려 드려요" / [켜기], denied = "휴대폰 설정에서 인사포털 알림을 켜 주세요" / [방법 보기](갤럭시: 설정 → 애플리케이션 → 인사포털 → 알림 / 아이폰: 설정 → 알림 → 인사포털, `orgModal`로), ios = "홈 화면에 추가하면 알림을 받을 수 있어요" / [추가 방법] → `installNotice()`. on · off = 빈 문자열. 모양: 흰 카드, 왼쪽 40px 원 안 종(`icon('bell')`, 배경 `--bg-danger`, 색 `--coral-text`), 오른쪽 `btn primary`.
  - `pushBadgeSync()` — `navigator.setAppBadge`가 있을 때만: n = `homePortalTasks()` 합계 − (대표이사이고 `KPI_LIVE.deleg`가 참이면 `KPI_LIVE.inbox.length`); n>0이면 `setAppBadge(n)`, 아니면 `clearAppBadge()`. `renderSidebar()` 끝에서 부른다(메뉴 숫자가 바뀔 때마다).
  - `?go=<nav>.<tab>`: `authBoot` 끝에서 읽어 `NAV`에 있는 메뉴 · 탭이면 `goTo(nav, tab)`, 주소에서 `go`를 지운다(`history.replaceState`). `go`가 있으면 `todayPopAuto()`를 건너뛴다(그 화면 위에 창이 덮이지 않게).
  - `pushSelfCheck()` — 콘솔 검증: `pushState` 경우 열 가지 이상(Review Focus 5 포함), `pushCard()` 상태별 문구 · on/off 빈 칸, `?go=` 파싱(모르는 값 → 홈), 대표이사 위임 때 아이콘 숫자에서 처리함 몫이 빠지는지.
- Produces (`sw.js`): `push` → `event.waitUntil(showNotification(title, { body, icon:'/android-chrome-192x192.png', badge:'/favicon-32x32.png', tag, data:{ url } }))` + `badge`가 숫자이고 `navigator.setAppBadge`가 있으면 맞추기(0이면 clear). `notificationclick` → 같은 출처의 열린 창이 있으면 `focus()` 후 `navigate(url)`, 없으면 `clients.openWindow(url)`. 캐시 · fetch 처리는 하지 않는다.

- [ ] **Step 1:** `pushSelfCheck()`를 먼저 쓴다 → 브라우저에서 실패 확인(함수 없음).
- [ ] **Step 2:** 구현(sw.js, index.html, nginx.conf).
- [ ] **Step 3:** 로컬 정적 서버(빈 포트 — `netstat -ano`로 확인, 8771은 다른 창이 씀)로 열어 `pushSelfCheck()` · `homeSelfCheck()` · `todoSelfCheck()` · `wcSelfCheck()` · `tourSelfCheck()` 통과, 휴대폰 폭(375)에서 `PUSH.state`를 ask · denied · ios로 바꿔 칸을 찍어 본다(가짜 `AUTH.live`).
- [ ] **Step 4:** `python .ci/check-html.py` · `bash .ci/check-public.sh` 통과.
- [ ] **Step 5:** Commit `휴대폰 알림: 서비스 워커 · 알림 켜기 칸 · 누르면 그 화면 · 아이콘 숫자`

### Task 6: 실제 로그인의 평가 진행 탭 · 평가 숫자 · KPI 기간 안내 (설계 §7.1 — 12월 1일 전)

**Files:**
- Modify: `public/index.html` — `TODO.e2` · `TODO.e3`, `evContent`, KPI 닫힘 띠(`kl-period closed`의 "작성 기간이 아니에요" 줄), 홈 KPI 카드(`homeKpi`의 "작성 기간이 아니에요"), 해당 `*SelfCheck`

**Interfaces:**
- Produces: `kpiClosedText(win, today) -> {head, sub}` — 순수 함수. win = 서버 `window`(`from, to, evalStart, open`):
  - `evalStart`가 있고 `today >= evalStart` → `{head:'평가가 시작되어 올해 KPI는 고칠 수 없어요', sub:''}`
  - `from`이 있고 `today < from` → `{head:'KPI 작성 기간이 열립니다', sub:'M월 D일 ~ M월 D일'}`
  - `from`이 있고 기간이 지남 → 지금 문구 `{head:'작성 기간이 아니에요', sub:'정한 기간 M/D ~ M/D'}`(지금 `md()` 모양 그대로)
  - `from` 없음 → `{head:'작성 기간이 아니에요', sub:'HR이 작성 기간을 열면 쓸 수 있어요'}`
  KPI 닫힘 띠와 홈 KPI 카드가 같은 함수를 쓴다.

- [ ] **Step 1: 실패하는 검사** — 기존 `kpiLiveSelfCheck`(또는 KPI 실제 로그인 검사가 있는 곳)에 더한다: 위 네 경우 문구; `AUTH.live`일 때 `TODO.e2()===0 && TODO.e3()===0`(오늘을 12-01 이후로 놓아도); `AUTH.live`일 때 `evContent()`가 "2026년 평가는 포털 밖에서 진행합니다"만 담고 시연 표(가상 이름)가 없음; `evResultContent()`는 실제 로그인에서도 지금과 같음(보고서 엑셀 단추 · 이의 신청 칸이 남음); 시연 모드(`AUTH.live=false`)의 평가 화면 · 숫자는 그대로.
- [ ] **Step 2:** 브라우저에서 실패 확인.
- [ ] **Step 3:** 구현. `evContent()` 첫 줄에서 `if(AUTH.live) return kpiNote('2026년 평가는 포털 밖에서 진행합니다.')`처럼(안내 모양은 기존 `kpiNote`). 연도는 `KPI_YEAR`.
- [ ] **Step 4:** 모든 화면 자체 검증 통과 · 휴대폰/PC 폭에서 평가 진행 탭 · KPI 띠를 찍어 확인.
- [ ] **Step 5:** Commit `실제 로그인: 평가 진행 탭은 안내만 · 평가 숫자 0 · KPI 작성 기간 안내`

### Task 7: 서버 안내 · 문서 · 마무리 검사

**Files:**
- Modify: `DEPLOY.md` — "휴대폰 알림" 절: ① 키 만들기 `docker compose exec -T api python -m push keygen` → 나온 두 줄을 서버 `.env`에 → `docker compose up -d` ② 평일 9시 타이머: `hr-portal-push-morning.service`(`ExecStart=/usr/bin/docker compose -f <저장소 경로>/docker-compose.yml exec -T api python -m push morning`) · `.timer`(`OnCalendar=Mon..Fri 09:00 Asia/Seoul`, `Persistent=false`) 파일 내용과 `systemctl enable --now` 명령 — 기존 `hr-portal-deploy.timer` 절의 모양 · 경로를 따른다 ③ 확인 `systemctl list-timers | grep push`.
- Modify: `CLAUDE.md` — "휴대폰 알림" 항목(규칙 요약 · 설계 경로 · 검사 함수 `pushSelfCheck()` · `python -m push check`), 환영 페이지/오늘 할 일 항목 옆에.
- Modify: `README.md` — 기능 표에 "휴대폰 알림" 한 줄.

- [ ] **Step 1:** 문서 작성.
- [ ] **Step 2:** 전체 검사: `export PYTHONIOENCODING=utf-8; for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py` 그리고 `cd backend && python -m hr_eval.selfcheck && python -m hr_eval.roster_sync --check && python -m auth_check && python -m services.rules_search && python -m contract_doc && python -m push check && python -m services.calculator && python -m hr_eval.sql.check_sqlite_features && python -m hr_eval.sql.check_schema` — 모두 통과.
- [ ] **Step 3:** Commit `휴대폰 알림: 서버 설정 안내 · 문서`
