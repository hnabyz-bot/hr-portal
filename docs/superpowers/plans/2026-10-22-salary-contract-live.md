# 전자연봉계약서 실제화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 급여·계약 > 전자연봉계약서를 실제 연봉계약으로 바꾼다. HR이 NAS 암호 엑셀로 계약 금액을 올리고 → 확인 창 → 발송 → 직원이 서명 → 서버가 서명본 PDF를 잠가 보관.

**Architecture:** 급여명세서(`payslip.py`·`ps*`)·원천징수영수증(`withholding.py`·`wh*`)과 같은 길. 엑셀과 암호는 HR 브라우저 안에서만 읽고, 서버에는 사람별 계약 내용만 `HR_PAY_KEY`로 잠가 둔다. 계약서 문장은 서버(`contract_doc.py`)가 "블록 목록"으로 만들어 화면은 그리기만 하고 서버는 같은 블록으로 고유번호(SHA-256)와 PDF를 만든다.

**Tech Stack:** Flask + SQLite(`backend/`), reportlab(이미 있음, 나눔글꼴), cryptography AESGCM(이미 있음), `public/index.html` 한 파일(바닐라 JS). 새 라이브러리 없음.

**Spec:** `docs/superpowers/specs/2026-10-22-salary-contract-live-design.md` (이 계획의 "설계 n.n"은 그 문서의 절 번호). 시안은 저장소 밖 `클로드 코드/뉴디자인/연봉계약서 실제 시안/`.

## Global Constraints

- 브랜치 `feature/contract-live`. 작업 폴더는 이 워크트리(다른 창이 `hr-portal` 폴더를 함께 쓴다 — 그 폴더에서 브랜치를 바꾸지 않는다).
- **Public 저장소**: 실제 이름·금액·생년월일·대표이사 실명·직인 그림·NAS 경로·서버 주소를 코드·커밋·이슈에 넣지 않는다. 검사용 사람은 `가상…`, 대표이사는 `○○○`. `010-` 꼴 리터럴 금지(`.ci/check-pii.sh`).
- 잠금 열쇠는 `payslip.pay_key()` 하나(`HR_PAY_KEY`). 새 열쇠·새 의존성 없음. 열쇠가 없으면 쓰기 503, 목록 `ready:false`.
- 감사로그 · 서버 로그에 금액 · 이름 · 생년월일 · 사유 · 문의 글을 넣지 않는다(번호 · 수 · 해 · 날짜만).
- `/api/contracts*` 응답은 `Cache-Control: no-store` + `Referrer-Policy: no-referrer`(withholding `_no_store`와 같게). POST는 Origin 필요(이미 `auth._guard`가 한다).
- DB 파일은 **`014_contracts.sql`**. merge 전에 main에 014가 생기면(열린 #170) 다음 번호로 바꾸고 `check_schema`의 판 숫자도 바꾼다.
- 날짜 판정은 한국 날짜 `auth._today()`(서버 시계는 UTC).
- 원 단위 반올림은 **0.5 올림**(JS `Math.round`와 같게 — 서버는 `math.floor(x + 0.5)`).
- 화면 글: "~해요"체, 짧게. 상태 칸 폭 고정 · 줄 높이 같게 · 단추 왼쪽 정렬(첫 단추 [보기]) — 시안 그림 1.
- 시연(`AUTH.live` 거짓) 직원 화면은 지금 `ctContracts`·`ctDocHtml` 그대로 둔다. 시연 메모리는 HR 올리기 · 확인 창 · 현황 · 발송까지(설계 7장 "맡긴 것" — 실제 서명 확인은 `devlive.py`로).
- 로컬 검사(커밋 전 매번, 워크트리 루트에서): `export PYTHONIOENCODING=utf-8; for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py` + `cd backend && python -m hr_eval.selfcheck && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features && python -m auth_check && python -m contract_doc`.
- 화면 검사는 `node "../../클로드 코드/cdp_check.mjs"`(저장소 밖, 경로는 사용자 PC `클로드 코드/cdp_check.mjs`)로 `http://localhost:8771/`(이 워크트리 `public/`을 `python -m http.server 8771`로) 콘솔 식을 돌린다.

## Review Focus

1. **엑셀 날짜 칸이 숫자로 온다** — 날짜 서식 칸은 `rzXlsx`가 일련번호(예: 32947)로 준다. 생년월일 · 계약 시작일 모두 일련번호를 날짜로 읽어야 한다. 5자리 숫자(앞 0이 떨어진 `031225`)는 일련번호로 읽히므로 구성원 설정 생일과 다르면 경고가 떠야 한다 → Task 8 테스트.
2. **"지금 기본급" · "지금 기본급에 든 연구보조비"를 기본급으로 읽기** — 연봉 인상 계산 저장 표를 그대로 올리면 "새 기본급"만 읽어야 한다. 칸 맞추기는 이름이 **똑같은 것만**(공백만 무시) → Task 8 테스트.
3. **같은 순간 두 번 누름 · 두 창** — 발송 · 서명 · 새 계약 서명을 두 번 보내면 하나만 되고 둘째는 409. `SIGNED`가 한 사람·한 회차에 둘이 되는 일 없음(부분 유일 색인 + 한 트랜잭션) → Task 6 테스트.
4. **열쇠가 바뀌어 안 풀리는 줄** — 목록 · PDF · 묶음 내려받기에서 그 줄만 건너뛰고(서버 로그에 계약서 번호만) 500이 나지 않는다 → Task 6 · 7 테스트.
5. **서명 그림이 비었거나 엉뚱한 파일** — PNG 머리가 아니거나 200KB 넘음 · 성명에 앞뒤 공백 · 다른 사람 이름 → 400, 아무것도 안 바뀜 → Task 6 테스트.

---

### Task 1: 계산식 · 계약서 표 · 조항 (화면 — 시연 양식과 연봉 인상 계산)

설계 5.1. 현장관리수당은 시급에 넣고 연봉에서 뺀다. 표는 "연봉 외 수당" = [현장관리수당/자격수당(합)] [직책수당]. 조항 세 곳.

**Files:**
- Modify: `public/index.html` — `ctCalc`(~5426), `ctDocHtml`(~5561 표·조항), `ctSelfCheck`(~5502), `rzSelfCheck`(~5912, 값이 바뀌면)

**Interfaces:**
- Produces: `ctCalc(c) -> {hourly, ot, monthly, annual, bonus}` (새 식). Task 2의 서버 `calc`가 같은 값을 낸다.

- [ ] **Step 1: 실패하는 검사 더하기** — `ctSelfCheck`에:
```js
const s2 = ctCalc({ base:2418900, ability:1211000, pos:300000, site:150000, qual:50000 });
ok(s2.hourly===20486 && s2.ot===307283 && s2.monthly===4437183 && s2.annual===49665095 && s2.bonus===2418900, '현장관리수당은 시급에 넣고 연봉에서 뺀다');
ok(ctCalc({ base:2491500, ability:1283700 }).annual===51224084, '수당 없는 사람은 예전과 같다');
const d = ctDocHtml(mk({ pid:1, site:150000, qual:50000, pos:300000, start:'2027-03-01', end:'2028-02-29' }));
ok(d.includes('현장관리수당/자격수당') && d.includes('200,000원') && !d.includes('<th>현장관리수당</th>'), '표: 현장관리수당/자격수당 한 칸(합)');
ok(d.includes('월급여 − 현장관리수당·자격수당, 직책수당') && d.includes('직책수당, 현장관리수당·자격수당은 사업주의 급여규정에 의거 지급한다') && d.includes('직책수당, 현장관리수당·자격수당은 연봉에 포함하지 않으며'), '조항 세 곳');
```
(기존 양식 샘플 검사 `49535908`은 그대로 둔다.)

- [ ] **Step 2: 실패 확인** — `node cdp_check.mjs http://localhost:8771/ "ctSelfCheck()"` → exception "현장관리수당은 시급에…"

- [ ] **Step 3: 구현** — `ctCalc`: `hourly = (base + ability + pos + site + bonus/12)/209`, `annual = (monthly − qual − pos − site)*12 + bonus`. `ctDocHtml`: 제1조 괄호 `((월급여 − 현장관리수당·자격수당, 직책수당) × 12 + 명절상여)`, 2)의 둘째 줄 `- 직책수당, 현장관리수당·자격수당은 사업주의 급여규정에 의거 지급한다.`, 4) `직책수당, 현장관리수당·자격수당은 연봉에 포함하지 않으며, 면직 시 해당월의 일수로 일할 계산한다.`, 표 머리 `<th>현장관리수당/자격수당</th><th>직책수당</th>`, 값 `ctWon((c.site||0)+(c.qual||0))`.

- [ ] **Step 4: 통과 확인** — `ctSelfCheck()` → "ctSelfCheck 통과", `rzSelfCheck()`·`homeSelfCheck()` 통과(바뀐 값 때문에 rz 검사가 깨지면 rz 검사의 기대값만 새 식으로 고친다 — 다른 규칙은 건드리지 않는다).

- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서: 현장관리수당은 시급에 넣고 연봉에서 뺌 · 표 한 칸(현장관리수당/자격수당) · 조항 세 곳"`

---

### Task 2: 서버 계약서 규칙 · 본문 블록 · 문서 고유번호

설계 3장 · 5.1. 화면 양식(Task 1 이후의 `ctDocHtml`) 문장을 그대로 블록으로 옮긴다 — **양식 문장의 원본은 이제 이 파일**.

**Files:**
- Create: `backend/contract_doc.py`
- Modify: `.github/workflows/ci.yml` (평가·계약 도메인 검사 job, `services.rules_search` 단계 뒤에 `python -m contract_doc` 한 단계 — 같은 `working-directory`)

**Interfaces:**
- Produces:
  - `TPL = "2027-1"`, `COMPANY_NAME = "㈜에이치앤아비즈"`
  - `calc(base:int, ability:int, pos:int=0, site:int=0, qual:int=0) -> dict` — 키 `hourly, ot, monthly, annual, bonus`(정수, 0.5 올림). Task 1과 같은 값.
  - `round_period(year:int) -> tuple[str, str]` — `("2027-03-01", "2028-02-29")`.
  - `upcoming_year(today:date) -> int` — 3월 이후면 다음 해, 1~2월이면 그해. `running_year(today) = upcoming_year(today) - 1`.
  - `birth6_ok(s:str) -> bool` — `^[0-9]{6}$`이고 19YY 또는 20YY 중 하나로 실제 있는 날(월 1~12, 그 달의 날).
  - `build_blocks(*, company:dict, worker:dict, pay:dict, start:str, end:str, signed_on:str|None) -> list[dict]` — company `{name, ceo, addr, kind}`, worker `{name, birth6, dept, title}`, pay `{base, ability, pos, site, qual}`. signed_on None이면 체결일 줄은 `"(서명한 날)"`.
  - 블록 모양(화면 Task 11 · PDF Task 4가 그린다): `{"t":"title","text"}` · `{"t":"kv","rows":[[라벨, 값, 라벨, 값] 또는 [라벨, 값]]}`(두 칸 줄 = 값이 가로 전체) · `{"t":"h","text"}` · `{"t":"p","runs":[[글, 굵게:bool], …]}` · `{"t":"table","head":머리 줄 수,"rows":[[칸 …]]}` 칸 = 글 또는 `{"c":글,"span":n}` · `{"t":"center","text"}` · `{"t":"signs","employer":"㈜에이치앤아비즈 대표이사 ○○○","worker":"가상하나"}`(사업주 · 근로자 서명 자리 — 그리는 쪽이 "(인)" 위에 직인, 근로자 옆에 서명 그림을 얹는다).
  - `doc_hash(blocks:list) -> str` — `sha256(json.dumps({"tpl":TPL,"blocks":blocks}, ensure_ascii=False, sort_keys=True, separators=(",",":")).encode()).hexdigest()`.
  - 근로자 kv 값 = `f"{name} (생년월일 {birth6})"`, 금액 글 = `f"{n:,}원"`, 계약 기간 = `"2027년 3월 1일부터 2028년 2월 29일"`(굵게), 체결일 = `"2027년 2월 10일"`.

- [ ] **Step 1: 실패하는 검사 쓰기** — `contract_doc.py` 맨 아래 `def selfcheck() -> None` + `if __name__ == "__main__": selfcheck(); print("contract_doc 검사 통과")`:
```python
assert calc(2418900, 1211000, 300000) == {"hourly": 19768, "ot": 296517, "monthly": 4226417, "annual": 49535908, "bonus": 2418900}
assert calc(2418900, 1211000, 300000, 150000, 50000) == {"hourly": 20486, "ot": 307283, "monthly": 4437183, "annual": 49665095, "bonus": 2418900}
assert round_period(2027) == ("2027-03-01", "2028-02-29") and round_period(2026) == ("2026-03-01", "2027-02-28")
assert upcoming_year(date(2026, 10, 22)) == 2027 and upcoming_year(date(2027, 1, 10)) == 2027 and upcoming_year(date(2027, 3, 1)) == 2028
assert birth6_ok("900315") and birth6_ok("040229") and not birth6_ok("900230") and not birth6_ok("90031") and not birth6_ok("901315")
b = build_blocks(company={"name": COMPANY_NAME, "ceo": "○○○", "addr": "가상 주소", "kind": "제조업"}, worker={"name": "가상하나", "birth6": "900315", "dept": "가상팀", "title": "프로"},
                 pay={"base": 2418900, "ability": 1211000, "pos": 300000, "site": 150000, "qual": 50000}, start="2027-03-01", end="2028-02-29", signed_on="2027-02-10")
text = json.dumps(b, ensure_ascii=False)
assert "가상하나 (생년월일 900315)" in text and "49,665,095원" in text and "200,000원" in text and "현장관리수당/자격수당" in text
assert "2027년 3월 1일부터 2028년 2월 29일" in text and "2027년 2월 10일" in text and b[-1] == {"t": "signs", "employer": "㈜에이치앤아비즈 대표이사 ○○○", "worker": "가상하나"}
assert doc_hash(b) == doc_hash(json.loads(text)) and len(doc_hash(b)) == 64
assert doc_hash(b) != doc_hash(build_blocks(company={"name": COMPANY_NAME, "ceo": "○○○", "addr": "가상 주소", "kind": "제조업"}, worker={"name": "가상하나", "birth6": "900315", "dept": "가상팀", "title": "프로"},
                 pay={"base": 2418900, "ability": 1211000, "pos": 300000, "site": 150000, "qual": 50001}, start="2027-03-01", end="2028-02-29", signed_on="2027-02-10"))
assert "(서명한 날)" in json.dumps(build_blocks(company={"name": COMPANY_NAME, "ceo": "○○○", "addr": "a", "kind": "b"}, worker={"name": "x", "birth6": "900315", "dept": "d", "title": "t"},
                 pay={"base": 1, "ability": 0, "pos": 0, "site": 0, "qual": 0}, start="2027-03-01", end="2028-02-29", signed_on=None), ensure_ascii=False)
```

- [ ] **Step 2: 실패 확인** — `cd backend && python -m contract_doc` → NameError(calc 없음)

- [ ] **Step 3: 구현** — 위 함수들. `build_blocks`의 문장은 Task 1 뒤의 `ctDocHtml` 문장을 한 글자도 바꾸지 않고 옮긴다(제목 `연 봉 계 약 서`, kv 4줄 = 사업주 · 사업의 종류 / 소재지 / 근로자 · 소속·직급, 제1조~제5조, 표 두 개, "위와 같이 연봉 계약을 체결합니다.", 체결일, signs). 사업주 칸 = `f"{name} · 대표이사 {ceo}"`, 소속·직급 = `f"{dept} · {title}"`(직급이 비면 소속만).

- [ ] **Step 4: 통과 확인** — `python -m contract_doc` → "contract_doc 검사 통과"

- [ ] **Step 5: 커밋** — `git add backend/contract_doc.py .github/workflows/ci.yml && git commit -m "연봉계약서: 서버 계약서 규칙 · 본문 블록 · 문서 고유번호"`

---

### Task 3: DB 표 (`014_contracts.sql`)

설계 5.4 저장. 

**Files:**
- Create: `backend/hr_eval/sql/014_contracts.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` — `check_contract_live_tables(conn)`(번호 "26. 연봉계약서 (014)"), `main()`에서 `check_withholding_tables` 뒤에 부름

**Interfaces:**
- Produces (표 · 칸 이름 — Task 5~7이 쓴다):
  - `contract_seals(id INTEGER PK AUTOINCREMENT, png BLOB 잠금 >28, created_at TEXT, created_by → users)` — 지울 수 없음(트리거).
  - `contract_settings(id INTEGER PK CHECK (id = 1), data BLOB 잠금 >28 {ceo, addr, kind}, seal_id → contract_seals NOT NULL, updated_at, updated_by → users)`.
  - `contracts(id PK AUTOINCREMENT, year 형 없음 CHECK integer 2000~2099, user_id → users RESTRICT, version INTEGER ≥1, prev_id → contracts NULL, status TEXT IN ('DRAFT','SENT','SIGNED','REPLACED','CANCELLED'), starts_on TEXT 실제 날짜, body BLOB 잠금 >28, created_at, created_by, sent_at, sent_by, due_on(날짜), company BLOB 잠금, seal_id → contract_seals, signed_at, sign BLOB 잠금, doc_hash TEXT(64자 소문자 16진), pdf BLOB 잠금, replaced_on(날짜), cancelled_at, cancelled_by, cancel_reason TEXT(1~200자), ask BLOB 잠금, ask_at, ask_closed_at, ask_closed_by)`.
  - 상태 CHECK: `DRAFT` = sent_at · signed_at 비어 있음 / `SENT` = sent_at · sent_by · due_on · company · seal_id 있음, signed_at 비어 있음 / `SIGNED`·`REPLACED` = 위 + signed_at · sign · doc_hash · pdf 있음 / `REPLACED` = replaced_on 있음 / `CANCELLED` = cancelled_at · cancelled_by · cancel_reason 있음.
  - 색인: `uq_contracts_signed (year, user_id) WHERE status='SIGNED'`, `uq_contracts_open (year, user_id) WHERE status IN ('DRAFT','SENT')`, `uq_contracts_version (year, user_id, version)`, `idx_contracts_user (user_id)`.
  - 트리거: 지우기는 `OLD.status='DRAFT'`만 · 상태 바꾸기는 DRAFT→SENT, SENT→SIGNED, SENT→CANCELLED, SIGNED→REPLACED, SIGNED→CANCELLED만 · `OLD.sent_at IS NOT NULL`이면 year · user_id · version · prev_id · starts_on · body · created_* · sent_* · due_on · company · seal_id가 안 바뀜 · `OLD.signed_at IS NOT NULL`이면 sign · doc_hash · pdf · signed_at도 안 바뀜 · `OLD.status IN ('REPLACED','CANCELLED')`면 ask · ask_at도 안 바뀜.
  - 파일 머리 주석: 무엇 · 왜 · 판 14 · 8월 `salary_contracts`(001)는 쓰지 않는다 · 번호 규칙.

- [ ] **Step 1: 실패하는 검사 쓰기** — `check_contract_live_tables`(withholding 검사와 같은 `_rejects`/`_report` 모양): 판 ≥ 14 / 설정 한 줄 저장, id=2 거부 / 직인 지우기 거부 / DRAFT 저장 → SENT(필수 칸 채움) → SIGNED → REPLACED 흐름 저장 / 같은 회차·직원 `SIGNED` 둘 거부 · `DRAFT`와 `SENT` 동시 거부 · 같은 차수 거부 / DRAFT→SIGNED 건너뛰기 거부 · CANCELLED→SENT 거부 / SENT에 due_on 없음 거부 / SIGNED에 pdf 없음 거부 / doc_hash 63자 · 대문자 거부 / cancel_reason 201자 거부 / SENT 줄 지우기 거부 · DRAFT 줄 지우기 됨 / 서명 뒤 body 바꾸기 거부 · ask_closed_at 바꾸기 됨 / year '2027'(글자) · NULL 거부 / starts_on '2027-02-30' 거부 / 직원 지우기 RESTRICT.

- [ ] **Step 2: 실패 확인** — `cd backend && python -m hr_eval.sql.check_schema` → "26." 검사들이 실패(표 없음)

- [ ] **Step 3: `014_contracts.sql` 쓰기** — 위 모양. year는 withholding처럼 형을 적지 않는다(013 주석의 이유). 날짜 칸은 `x GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]' AND date(x) IS x`.

- [ ] **Step 4: 통과 확인** — `python -m hr_eval.sql.check_schema` → "전부 통과", `check_file_numbers`(0번)도 통과. `python -m hr_eval.sql.check_sqlite_features` 통과.

- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서: DB 표 014 (계약서 · 설정 · 직인, 상태 흐름 · 잠금 트리거)"`

---

### Task 4: 서명본 PDF 만들기

설계 5.4 "문서 고유번호 · 서명본 PDF", 4.4 전자서명 정보.

**Files:**
- Modify: `backend/contract_doc.py`

**Interfaces:**
- Consumes: Task 2 블록. `export_utils._register_korean_font() -> str`(글꼴 이름).
- Produces: `render_pdf(blocks:list, *, seal_png:bytes|None, sign_png:bytes|None, esign:dict|None) -> bytes` — esign `{"signedAt":"2027-02-10 14:22", "name":"…", "how":"하이웍스 로그인"|"휴대폰 등록", "hash":"…64자"}`. A4 세로, 표는 칸 테두리, `signs` 블록 = 왼쪽 `사업주 : {employer} (인)` 위에 직인 그림(약 18mm, "(인)" 자리), 오른쪽 `근로자 : {이름}` 옆 서명 그림(높이 약 12mm). esign이 있으면 끝에 "전자서명 정보" 상자: `서명 {signedAt} · {name} · 본인 확인: {how}` / `문서 고유번호 {hash[:16]}…` / `이 계약서는 전자문서로 작성·교부합니다(근로기준법 제17조).`. 그림이 None이면 그 자리는 빈칸.

- [ ] **Step 1: 실패하는 검사 더하기** (`selfcheck`에):
```python
png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")  # 1x1 PNG
pdf = render_pdf(b, seal_png=png, sign_png=png, esign={"signedAt": "2027-02-10 14:22", "name": "가상하나", "how": "하이웍스 로그인", "hash": doc_hash(b)})
assert pdf.startswith(b"%PDF") and pdf.rstrip().endswith(b"%%EOF") and len(pdf) > 2000
assert render_pdf(b, seal_png=None, sign_png=None, esign=None).startswith(b"%PDF")
```
- [ ] **Step 2: 실패 확인** — `python -m contract_doc` → NameError(render_pdf)
- [ ] **Step 3: 구현** — reportlab platypus(`SimpleDocTemplate`, `Paragraph`, `Table`, `Image`), 글꼴은 `_register_korean_font()`. 그림은 `ImageReader(io.BytesIO(png))`.
- [ ] **Step 4: 통과 확인** — `python -m contract_doc` 통과. 이 PC에서 한 번 `python -c "import contract_doc as c, json; …"`로 PDF 파일을 스크래치패드에 써서 열어 보고(맑은 고딕으로 한글이 보이는지 · 표가 쪽 밖으로 안 나가는지) 화면 그림을 보고서에 남긴다.
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서: 서명본 PDF (직인 · 서명 그림 · 전자서명 정보)"`

---

### Task 5: 서버 ① — 설정 · 직인 · 올리기 · 현황 · 조정 · 빼기 · 발송

설계 4.1~4.3 · 4.8 · 5.3 · 5.4.

**Files:**
- Create: `backend/contract.py` (`bp = Blueprint("contract", __name__)`, 머리 docstring에 길 목록 — payslip.py 모양)
- Modify: `backend/app.py`(`import contract` · `app.register_blueprint(contract.bp)` + 한 줄 주석), `backend/payslip.py`(사원코드 기억 부분을 함수로 떼기), `backend/auth_check.py`(`check_contracts(hr)` 앞부분 + `main()`에서 `check_withholding` 뒤에 부름)

**Interfaces:**
- Consumes: `payslip.pay_key()`, `contract_doc.calc/round_period/birth6_ok/build_blocks`, `auth._db/_hr_only/_audit/_iso/_now/_today`, `withholding._seal/_unseal` 모양(같은 함수를 이 파일에 둔다 — aad 글자만 다르다).
- Produces:
  - `payslip.remember_codes(conn, pairs:list[tuple[int, str]]) -> str | None` — payslip `_store`의 사원코드 기억(빈 직원에게 붙이기 · 남의 코드면 그 400 문구). payslip `_store`가 이것을 쓰도록 바꾼다(동작 같음 — `check_payslips` 그대로 통과).
  - aad: 계약서 칸 `f"ct|{id}|{year}|{user_id}|{칸}"`(칸 = body · company · sign · pdf · ask), 설정 `"ct|settings"`, 직인 `f"ct|seal|{id}"`.
  - body JSON: `{code, name, dept, title, birth6, base, ability, pos, site, qual, reason}` — name · dept(팀 이름, 팀이 없으면 본부) · title(`users.title` 또는 "")은 **서버가 올릴 때 명단에서** 채운다.
  - 계약서 한 줄 JSON(HR): `{id, year, userId, version, status, start, end, code, name, dept, title, birth6, base, ability, pos, site, qual, annual, reason, sentAt, due, late, signedAt, replacedOn, cancelledAt, cancelReason, ask:{text, at, closedAt}|null, prevId}` — `late` = status SENT이고 오늘 > due.
  - 길:
    - `GET /api/contracts/settings` (HR) → `{ready, settings:{ceo, addr, kind, sealId}|null}` · `GET /api/contracts/settings/seal` (HR) → 지금 직인 PNG
    - `POST /api/contracts/settings` (HR) `{ceo, addr, kind, seal: base64 PNG | null}` — 처음엔 seal 필수, 이후 null = 그대로. ceo 1~20자 · addr 1~100자 · kind 1~30자 · PNG 머리 · 500KB 이하. 감사로그 `CONTRACT_SETTINGS {fields:[바뀐 칸 이름]}`
    - `GET /api/contracts/admin?year=Y` (HR) → `{ready, settings:bool, year, rows:[…그 회차 전부, 상태 상관없이]}`
    - `POST /api/contracts/<year>` (HR) `{rows:[{userId, code, birth6, base, ability, pos, site, qual, start|null}]}` → `{saved:n}` — 1~2000줄 · 본문 2MB · 직원 중복 400 · 직원이 없음/비활성/`has_contract` 꺼짐/본부장·대표이사 → 400(이름 대신 직원 번호로 이유) · 그 직원에게 SENT 또는 SIGNED가 있으면 409 · 금액 정수 0~10억 · base ≥ 1 · `birth6_ok` · start는 null 또는 회차 안 날짜 · code는 "" 또는 `payslip.CODE`. 이미 DRAFT가 있으면 body · starts_on을 바꾸고, 없으면 새 줄(version = 그 회차 그 직원의 최대 차수 + 1). `remember_codes`. 감사로그 `CONTRACT_UPLOAD {year, people}`.
    - `POST /api/contracts/<id>/adjust` (HR, DRAFT만) `{base, ability, pos, site, qual, birth6, start|null, reason}` — reason 1~200자(제어문자 거부, `kpi._chars` 규칙). 감사로그 `CONTRACT_ADJUST`(entity_id = 계약서 번호)
    - `POST /api/contracts/<id>/remove` (HR, DRAFT만) → 줄 삭제. 감사로그 `CONTRACT_REMOVED`
    - `POST /api/contracts/send` (HR) `{ids:[…], due:"YYYY-MM-DD"}` → `{sent:n, skipped:[{id, why}]}` — 설정 없으면 409 "계약서 설정에서 대표이사 이름과 직인을 먼저 넣어 주세요." · due는 오늘(한국) 이후 · 줄마다 DRAFT · 활성 · `has_contract` · **`portal_enabled`**가 아니면 skipped(why = "포털 사용 꺼짐" / "비활성" / "연봉계약서 대상 아님" / "발송 전이 아님"). 보낼 때 company(설정 data를 이 줄 aad로 다시 잠금) · seal_id · sent_at · sent_by · due_on. 감사로그 `CONTRACT_SEND {year, people, due}`(회차별 한 줄).
  - 모든 길: 열쇠 없으면 쓰기 503(`MSG_NO_KEY` — payslip 문구 모양), HR 아니면 403, 없는 id 404. 한 요청 = `BEGIN IMMEDIATE` 한 트랜잭션(payslip `upload` 모양). 풀리지 않는 줄은 목록에서 건너뛰고 `logger.error("연봉계약서를 풀 수 없음: 계약서 %s …", id)`.

- [ ] **Step 1: 실패하는 검사 쓰기** — `check_contracts(hr)`(payslip 검사처럼 열쇠를 여기서 만들고 끝에 되돌림, 사람 = seed의 3번 직원 · 10~12번은 이 검사에서 더함, 가짜 PNG는 위 1x1). 확인할 것: 로그인 없이 401 · 직원은 admin/settings/올리기/발송 403 · 열쇠 없음 503 · 설정 저장(처음 seal 없으면 400) · 직인 PNG 아님 400 · 올리기 saved · DB body에 "가상"·금액 글자 없음 · 같은 사람 다시 올리면 DRAFT가 바뀜(줄 수 그대로) · 직원 중복 400 · `has_contract` 꺼진 사람 400 · birth6 `"900230"` 400 · start가 회차 밖 400 · base 0 400 · 실수 금액 400 · 사원코드 기억(payslip과 같은 규칙, 남의 코드 400) · adjust(사유 없으면 400) · remove(DRAFT만, SENT는 409) · 설정 없이 발송 409 · `portal_enabled` 꺼진 사람 skipped "포털 사용 꺼짐" · due 어제 400 · 발송하면 SENT · 다시 올리면 409 · admin rows의 annual이 `contract_doc.calc`와 같음 · 감사로그에 이름·금액·생년월일 없음 · 응답 `Cache-Control: no-store`.
- [ ] **Step 2: 실패 확인** — `cd backend && python -m auth_check` → `check_contracts` 첫 검사에서 실패(404)
- [ ] **Step 3: 구현** — `contract.py` + `remember_codes` 떼기 + app.py 등록.
- [ ] **Step 4: 통과 확인** — `python -m auth_check` 전부 통과(급여명세서 · 원천징수영수증 검사 포함)
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서: 서버 ① 설정 · 직인 · 올리기 · 현황 · 조정 · 빼기 · 발송"`

---

### Task 6: 서버 ② — 직원 보기 · 서명 · 서명본 · 파기 · 새 계약

설계 3장 연중 변경 · 4.1 파기 · 4.4 · 5.4.

**Files:**
- Modify: `backend/contract.py`, `backend/auth_check.py`(`check_contracts` 이어서)

**Interfaces:**
- Consumes: Task 4 `render_pdf`, Task 2 `build_blocks`·`doc_hash`, `access.can(actor, Act.VIEW_CONTRACT, Target(...))`.
- Produces:
  - `GET /api/contracts/mine` (본인) → `{rows:[…]}` — 그 직원의 SENT · SIGNED · REPLACED · (발송된 적 있는) CANCELLED, 최근 회차 · 높은 차수부터. 한 줄 = HR 줄에서 `reason`·`cancelReason`·`ask.text`를 뺀 것 + `blocks`(SENT = 발송 때 고정한 company로 `signed_on=None`, 서명한 것 = 서명 때 블록을 다시 만든 것과 같아야 하므로 `signed_on=signed_at의 한국 날짜`) + `hash` + `canSign`(SENT) + `ask:{at, closedAt}|null`.
  - `GET /api/contracts/<id>` (본인 · HR — `access.can(VIEW_CONTRACT)`) → 한 줄 + blocks(DRAFT도 HR에게는 지금 설정으로 미리 보기, 설정이 없으면 company = `{"ceo":"(계약서 설정 필요)", …}`). HR이 남의 것 → 감사로그 `CONTRACT_VIEW`(entity_type "user", entity_id 직원 번호, after `{"contract": id}`). 본인 것의 DRAFT는 404.
  - `GET /api/contracts/<id>/seal` (본인 · HR) → 그 계약서에 고정된 직인 PNG(화면이 사업주 칸에 얹는다), DRAFT면 지금 설정의 직인.
  - `POST /api/contracts/<id>/sign` (본인) `{name, consent:true, image: base64 PNG}` → `{signedAt, hash}` — 본인 · SENT 아니면 403/409 · consent 참 · `name.strip()`이 아니라 **그대로** `users.name`과 같아야(앞뒤 공백 있으면 400) · PNG 머리 · 200KB 이하. 서명 날 = `auth._today()`, 블록 → `doc_hash` → `render_pdf(seal=고정 직인, sign=그림, esign={signedAt: 한국 시각 "YYYY-MM-DD HH:MM", name, how: g.session_kind=="phone" ? "휴대폰 등록" : "하이웍스 로그인", hash})` → sign(그림 · 이름 · consent · ip · ua · kind) · doc_hash · pdf · signed_at 저장 → `prev_id`가 그 회차의 SIGNED 줄이면 같은 트랜잭션에서 그 줄을 REPLACED(replaced_on = 이 계약 starts_on)로 바꾼 뒤 이 줄을 SIGNED로. 감사로그 `CONTRACT_SIGNED {contract, hash}`(+ 바뀌었으면 `CONTRACT_REPLACED {old, new}`). 마감이 지나도 받는다.
  - `GET /api/contracts/<id>/pdf` (본인 · HR) — SIGNED · REPLACED · 서명 뒤 CANCELLED만, 저장된 PDF 그대로, `Content-Disposition: inline; filename="contract_{year}.pdf"`. HR이 남의 것 → `CONTRACT_PDF_VIEW`.
  - `POST /api/contracts/<id>/cancel` (HR) `{reason, then:"resend"|"draft"|"none"}` — SENT · SIGNED만. 이 줄 CANCELLED → then이 resend면 다음 차수 SENT(같은 body · starts_on · due_on · company · seal_id, sent_at 지금) / draft면 다음 차수 DRAFT(같은 body · starts_on) / none이면 끝. 감사로그 `CONTRACT_CANCELLED {contract, then}`.
  - `POST /api/contracts/<id>/replace` (HR) `{base, ability, pos, site, qual, start, reason}` — SIGNED만 · start는 회차 안이고 이 계약 starts_on보다 뒤 · 그 회차에 그 직원 DRAFT/SENT가 이미 있으면 409 → 다음 차수 DRAFT(prev_id = 이 줄, birth6 · code · name · dept · title은 이 줄에서). 감사로그 `CONTRACT_REPLACE {contract}`.

- [ ] **Step 1: 실패하는 검사 더하기** — 직원이 mine에서 SENT를 받고 blocks에 "(서명한 날)" · 다른 직원 계약 GET 403 · 본부장 · 팀장도 남의 GET 403 · 서명: consent 거짓 400 · 이름 다르면 400 · `" 가상3"`(앞 공백) 400 · PNG 아님 400 · 200KB 넘음 400 → 아무것도 안 바뀜 · 정상 서명 → SIGNED, hash 64자, mine의 blocks에 체결일 · `doc_hash(blocks) == hash` · PDF `%PDF` · 다시 서명 409 · **두 번 동시 서명**(스레드 둘) → 하나만 200 · 마감 지난 계약(due를 DB에서 어제로) 서명 200 · cancel(then=resend) → 원래 CANCELLED + 새 차수 SENT · cancel(then=none) → 살아 있는 줄 없음 · cancel DRAFT 409 · replace → DRAFT(prev_id) → 발송 → 서명 → 옛 줄 REPLACED · replaced_on · SIGNED 하나뿐 · replace start가 옛 시작일 이전 400 · 열쇠를 바꿔 안 풀리는 줄이 mine · admin에서 빠지고 500 없음 · HR이 남의 PDF → 감사로그 `CONTRACT_PDF_VIEW`, 내용에 이름·금액 없음.
- [ ] **Step 2: 실패 확인** — `python -m auth_check` → mine 404에서 실패
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m auth_check` 전부 통과
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서: 서버 ② 직원 보기 · 서명(고유번호 · 서명본 PDF) · 파기 · 새 계약으로 바꾸기"`

---

### Task 7: 서버 ③ — 문의 · 본부장 현황 · 서명본 묶음

설계 4.5 · 4.7 · 4.9.

**Files:**
- Modify: `backend/contract.py`, `backend/auth_check.py`

**Interfaces:**
- Produces:
  - `POST /api/contracts/<id>/ask` (본인, SENT만) `{text}` — 1~200자(`kpi._chars` 규칙) · 확인 전 문의가 있으면 409 → ask(잠금) · ask_at · ask_closed_* 비움. 감사로그 `CONTRACT_ASK {contract}`.
  - `POST /api/contracts/<id>/ask/close` (HR) — 문의가 있고 확인 전이면 ask_closed_at · by. 감사로그 `CONTRACT_ASK_CLOSED`.
  - HR 줄의 `ask = {text, at, closedAt}`, 본인 줄은 `{at, closedAt}`.
  - `GET /api/contracts/division?year=Y` — 본부장(`position is DIVISION_HEAD`)만, 아니면 403. 그 회차 계약서 중 `access.can(actor, Act.VIEW_CONTRACT_STATUS, Target(user_id, team_id, division_id, position))`인 줄의 살아 있는 것(DRAFT · SENT · SIGNED)만 `{rows:[{name, team, status, late, signedOn, due}]}` — **금액 · 차수 · 문의 · 생년월일 없음**. Target: 직원 부서가 팀이면 team_id = 부서, division_id = 부모 / 본부 바로 아래면 team_id None, division_id = 부서. position = 직원의 직책.
  - `GET /api/contracts/<year>/zip` (HR) — 그 회차 SIGNED 줄의 PDF를 `zipfile`(ZIP_DEFLATED, 암호 없음)로: `f"{year}_{code or user_id}_{name}.pdf"` + `서명정보.csv`(utf-8-sig, 머리 `이름,사원코드,차수,서명 일시,문서 고유번호`). `Content-Disposition: attachment; filename="contracts_{year}.zip"`. 안 풀리는 줄은 건너뛴다. 0명이면 404. 감사로그 `CONTRACT_ZIP {year, people}`.

- [ ] **Step 1: 실패하는 검사 더하기** — 문의: 남의 것 403 · SIGNED에 409 · 201자 400 · 두 번째 409 · HR 확인 뒤 다시 문의 200 · admin에서 HR은 글을 보고 mine은 글이 없음 · DB ask에 글 그대로 없음 · 감사로그에 글 없음 / division: seed의 본부장(이 검사에서 user_roles에 DIVISION_HEAD 더함 → 끝에 되돌림)이 자기 본부 사람만 받고 응답 JSON에 `base`·금액 숫자·`birth6` 없음 · 팀장 403 · 직원 403 / zip: 직원 403 · HR → `zipfile`로 열어 파일 수 = SIGNED 수 + 1, csv 첫 줄 머리 · PDF `%PDF` · 감사로그 `CONTRACT_ZIP` · 한 줄의 pdf를 다른 열쇠로 잠근 바이트로 바꿔 두면 그 줄만 빠지고 200(Review Focus 4).
- [ ] **Step 2: 실패 확인** — `python -m auth_check` → ask 404에서 실패
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — `python -m auth_check` 전부 통과 + 로컬 검사 전부
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서: 서버 ③ HR에 문의 · 본부장 현황(금액 없이) · 서명본 묶음 내려받기"`

---

### Task 8: 화면 ① — 엑셀 읽기 · 확인 창 · 올리기

설계 4.2 · 5.2 · 5.3. 급여명세서 `psStage`/`psConfirm`/`psBlock`/`psUpload` 모양을 따른다(시안 그림 2).

**Files:**
- Modify: `public/index.html` — 전자연봉계약서 블록(`ctContent` 근처)에 새 함수, `rzLoad`에 `kind==='contract'` 한 줄, `rzPick`은 그대로(.xlsx)

**Interfaces:**
- Consumes: `rzXlsx`, `rzHead`, `rzNorm`, `rzNum`, `RZ_EXEC`, `psMatch(people, members)`(people 원소는 `{head:{code, name, dept}}`), `psMembers()` 대신 아래 `ctMembers()`, `msBirthMd`, `MS_LIVE`/`msLoad`, `ctCalc`, `orgModal`/`orgCloseModal`, `kpiNote`/`kpiFlash`.
- Produces:
  - `CT_FIELDS` — `[[키, 표시, [후보 이름…]]]`: code `['사원코드','사번','사원번호']` · name `['이름','사원명','성명']` · dept `['부서','부서명','소속']` · birth `['생년월일','주민번호','주민등록번호']` · base `['새기본급','기본급']` · ability `['새능력급','능력급']` · pos `['직책수당']` · site `['현장관리수당','업무수당']` · qual `['자격수당']` · start `['계약시작일']`. 반드시: name · birth · base · ability.
  - `ctGuess(head, fields) -> {키: 칸 번호 | -1}` — **이름이 똑같은 것만**(`rzNorm` 뒤 같음), 후보 순서대로(새기본급이 기본급보다 먼저).
  - `ctXlDate(v) -> 'YYYY-MM-DD' | ''` — 숫자 1~60000 = 엑셀 일련번호(1899-12-30 + n일), 글자 = `msDate`.
  - `ctBirth6(v) -> {b6:'YYMMDD'|'', fromRrn:bool}` — 글자 `\d{6}-?\d{7}` 또는 숫자 12~13자리(앞 0 채워 13자리) = 주민번호 → 앞 6자리만(뒷자리는 변수에 남기지 않는다), 숫자 100000~999999 · 글자 6자리 = YYMMDD, 숫자 1~60000 = 일련번호 날짜 → YYMMDD, `1990-03-15`류 글자 → YYMMDD. 날짜로 성립하지 않으면 `''`.
  - `ctRead(rows) -> {at, map, people:[{i, head:{code,name,dept}, b6, fromRrn, base, ability, pos, site, qual, start, bad:[이유…]}], skip, exec, missing:[필수 칸 키…]}` — 합계·빈 줄 skip(`rzReadPay`와 같은 판단), 부서 `RZ_EXEC` 줄은 exec. 금액: `rzNum`, 빈칸 = 0, 소수·음수·NaN·10억 초과 → bad("기본급 소수" 식, 칸 이름). base 0 → bad. b6 '' → bad("생년월일 없음/읽을 수 없음"). start 칸이 있고 값이 있으면 `ctXlDate`, 못 읽으면 bad.
  - 화면 상태 `ctUi = { pick:null, f:"all", q:"", due:"", sel:new Set() }` — `pick = {file, locked, year, r, auto, to, members, err, busy}`(`ps.pick`과 같은 뜻의 칸). (`ct`라는 이름은 다른 함수의 지역 변수로 쓰이고 있어 쓰지 않는다) · `ctMembers()` — 실제 `MS_LIVE.list`, 시연은 `CT_PEOPLE`을 `{id:pid, name, team, division, no:'', use:true, ct:true, active:true, lead, birthday:''}`로.
  - `ctBlock() -> ''|이유` — 순서: 파일 없음 → 필수 칸 못 찾음 → 회차 모양 → bad 사람(이름 3명 + "외 n명") → 회차 밖 계약 시작일 → 못 맞춘 줄 남음 → 올릴 사람 0명. (대상 아님 · 이미 발송 · 포털 꺼짐은 막지 않고 목록에 보임 — 대상 아님 · 이미 발송은 올리기에서 자동으로 빠진다.)
  - `ctConfirm()` — 시안 그림 2의 구성(설계 4.2): 파일 줄 · 회차 고르기(다가오는 회차 · 진행 중 회차 — `ctPeriod` 규칙) · 재직자 기간 · 세 칸 · 합계(올릴 사람만, `ctCalc().annual`) · 칩(찾은 칸만, 못 찾은 필수 칸은 고르기 칸) · "이번에 만들지 않는 사람"(대상 아님: `!m.ct || !m.active || lead 본부장/대표이사`, 이미 발송: 그 회차에 SENT/SIGNED가 있는 직원) · "알아 둘 것"(계약 시작일 · HR 본인 · **파일에 없는 계약 대상자** · 생일 다름(`msBirthMd(b6)`의 월-일 ≠ `m.birthday`, 둘 다 있을 때) · **포털 사용 꺼짐**(`!m.use`) · 주민번호 칸에서 앞 6자리만 읽음) · 못 맞춘 사람(`psPick`처럼 고르기 + "이번에서 빼기") · 막는 이유 · [다른 파일 고르기][올리기 (발송 전)]. 다시 그릴 때 스크롤 · 초점 유지(`psConfirm` 끝부분과 같게).
  - `ctUpload()` — 실제: `POST /api/contracts/{year}` `{rows:[{userId, code, birth6, base, ability, pos, site, qual, start}]}`(대상 아님 · 이미 발송 · 뺀 줄 제외), 실패하면 창 유지 + 이유. 시연: `CT_MEM.rows`에 같은 모양 줄(status DRAFT)을 넣는다. 끝나면 `kpiFlash('n명 계약서를 올렸어요. 아직 발송 전이에요.')`.
  - `CT_MEM = { rows:[], settings:{ ceo:'○○○', addr:CT_COMPANY.addr, kind:CT_COMPANY.kind }, seq:0 }` — 시연 HR 현황의 자료(줄 모양 = Task 5 "계약서 한 줄 JSON").

- [ ] **Step 1: 실패하는 검사 쓰기** — `ctSelfCheck`에(가상 줄로):
```js
const H = [['사번','이름','부서','직급','지금 기본급','새 기본급','지금 능력급','새 능력급','직책수당','현장관리수당','자격수당','지금 기본급에 든 연구보조비','생년월일','계약 시작일']];
const g = ctGuess(H[0].map(rzNorm), CT_FIELDS);
ok(g.base===5 && g.ability===7 && g.birth===12 && g.start===13 && g.code===0, '새 기본급 · 새 능력급만 — 지금 금액과 연구보조비 칸은 안 읽음');
ok(ctGuess(['이름','기본급','능력급','생년월일'], CT_FIELDS).base===1, '"새"가 없으면 기본급');
ok(ctGuess(['이름','지금기본급','생년월일'], CT_FIELDS).base===-1, '비슷한 이름은 맞추지 않음');
ok(ctBirth6('900315').b6==='900315' && ctBirth6(900315).b6==='900315' && ctBirth6('1990-03-15').b6==='900315' && ctBirth6(32947).b6==='900315', '생년월일 모양 넷 (32947 = 엑셀 일련번호 1990-03-15)');
const rr = ctBirth6('900315-1'+'234567'); ok(rr.b6==='900315' && rr.fromRrn && !JSON.stringify(rr).includes('234567'), '주민번호에서 앞 6자리만 남김');
ok(ctBirth6(312254123456).b6==='031225', '앞 0이 떨어진 12자리 주민번호');
ok(ctBirth6('900230').b6==='' && ctBirth6('').b6==='' && ctBirth6('abc').b6==='', '없는 날 · 빈칸');
ok(ctXlDate(46447)==='2027-03-01' && ctXlDate('2027-03-15')==='2027-03-15' && ctXlDate('x')==='', '계약 시작일: 일련번호 · 글자');
const R = [H[0], ['A1','가상하나','가상팀','프로',1,2418900,1,1211000,'','',0,0,'900315',''], ['A2','가상둘','가상팀','프로',1,'2,000,000.5',1,0,0,0,0,0,'',''], ['합계','','','','','','','','','','','','','']];
const rd = ctRead(R);
ok(rd.people.length===2 && rd.people[0].base===2418900 && rd.people[0].pos===0 && !rd.people[0].bad.length, '빈 수당 칸 = 0원');
ok(rd.people[1].bad.some(x=>x.includes('기본급')) && rd.people[1].bad.some(x=>x.includes('생년월일')) && rd.skip===1, '소수 금액 · 생년월일 없음은 bad · 합계 줄은 건너뜀');
```
그리고 시연 흐름 검사 `await ctFlowCheck()`(ps의 `psFlowCheck`처럼 — 끝나면 `CT_MEM`·`ct.pick`을 되돌린다): 위 R로 `ctStage('가상.xlsx', R, false)` → `ctBlock()`이 bad 사람 이유 → 그 줄 "이번에서 빼기" → 빈 문자열 → `ctUpload()` → `CT_MEM.rows.length===1 && status==='DRAFT' && birth6==='900315'`. 시연 멤버 하나의 `birthday`를 `'03-16'`으로 두고 다시 고르면 확인 창 글에 "생일이 달라요"가 있다(Review Focus 1).
- [ ] **Step 2: 실패 확인** — `cdp_check.mjs … "ctSelfCheck()"` → ReferenceError ctGuess
- [ ] **Step 3: 구현** — 위 함수들 + `ctStage(fileName, rows, locked)`(rzLoad가 부름: 실제면 `msLoad`·현황 먼저 받고 실패 시 안내) + `rzLoad`의 `if(kind==='contract'){ const c = await rzXlsx(buf); rz.lock = null; return await ctStage(file.name, c.rows, pw!=null); }` + 읽기 실패 안내 문구(`kind==='contract'`).
- [ ] **Step 4: 통과 확인** — `ctSelfCheck()` · `await ctFlowCheck()` · `rzSelfCheck()` · `psSelfCheck()` 통과
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서 화면 ①: 계약 금액 엑셀 읽기 · 확인 창 · 올리기(발송 전)"`

---

### Task 9: 화면 ② — HR 현황 · 조정 · 빼기 · 발송 · 메일

설계 4.1 · 4.3 · 4.6, 시안 그림 1 · 3.

**Files:**
- Modify: `public/index.html` — `ctAdmin()`이 참일 때 그리는 것을 `ctHrPanel()`(시연 등급·인상률 표)에서 새 `ctAdminView()`로 바꾼다. `ctHrPanel`·`ctSendSelected`·`ctEdit`·`ctSaveEdit`·`ctCancelForm`·`ctDoCancel`은 다른 곳에서 안 쓰면 지운다(`grep`으로 확인 — 평가의 `evSyncContracts`·`ctContracts`·`ctHold`가 쓰는 것은 남긴다).

**Interfaces:**
- Consumes: Task 8 `CT_MEM`·`ctMembers`·`rzPick('contract')`, 서버 Task 5 길, `hwMailUrl`, `orgPeople()`(실제 명단의 `mailNo`·`email` — uid로 찾음 `m.uid`), `ORG_LIVE` 로드 함수.
- Produces:
  - `CT_LIVE = { state:'', year:null, rows:[], ready:false, settings:false, mine:[], mineState:'', division:null }` (Task 11도 씀).
  - `ctYear` — 지금 보는 회차(처음 = 다가오는 회차). `ctRows()` = 실제 `CT_LIVE.rows` · 시연 `CT_MEM.rows`(그 회차).
  - `ctAdminLoad()` — `GET /api/contracts/admin?year=` → CT_LIVE.
  - `ctAdminView()` — 시안 그림 1: 회차 고르기 + [계약 금액 올리기](시연은 옆에 "시연: 예시 자료로 올려 보기" `ctDemo()` — Task 8의 R 모양 가상 파일로 `ctStage`) · 요약 네 칸(`hm-tile` slate/amber/coral/green) · 칩(전체 · 발송 전 · 서명 대기 · 기한 지남 · 서명 완료 · 파일에 없음 · 포털 사용 꺼짐 · 문의 있음 — 고른 칩은 `ctUi.f`) + 검색(`ctUi.q`, 목록만 다시 그림 `ctList()`) · 표(설계 4.1 칸, 상태 칩 폭 118px 고정 `.ct-pill`, 줄 높이 같게 — 배지는 아래 줄 글자) · 아래 [선택한 n명 발송] [서명 안 한 n명에게 메일 쓰기] [서명본 내려받기 (n명)] · 계약서 설정 카드(Task 10). 열쇠가 없으면(`ready` 거짓) 급여명세서와 같은 안내.
  - "파일에 없음" = `ctMembers()` 중 계약 대상(`ct && active && lead 아님 본부장/대표이사`)인데 그 회차 줄이 없는 사람 — 표 아래에 이름만 줄로.
  - `ctAdjust(id)` 창 — 금액 다섯 · 생년월일 · 계약 시작일 · 사유 → `POST …/adjust`(시연: CT_MEM). `ctRemove(id)` — 확인 한 번 → `POST …/remove`.
  - `ctSendAsk()` 창(시안 그림 3) → `ctSend()` — `POST /api/contracts/send {ids, due}`, skipped가 있으면 이름과 이유를 알림. 체크(기본 켜짐)면 `ctMail(ids)`.
  - `ctMail(userIds)` — 받는 사람 = `orgPeople()`에서 uid가 맞는 사람의 `mailNo`. `hwMailUrl`을 **쉼표로 이은 번호**로 연다(`HW_MAIL + '&user_nos=' + nos.join(',')` — Task 12에서 실제로 되는지 확인, 안 되면 그 Task에서 고친다). `mailNo`가 없는 사람의 `email`은 `;`로 이어 복사 + `wsNotice`("받는 사람 칸에 붙여넣기").
  - 시연 `CT_MEM`도 같은 길(발송 = status SENT · due · sentAt).

- [ ] **Step 1: 실패하는 검사 더하기** — `await ctFlowCheck()`에 이어서: 올린 줄 고르기(`ctUi.sel`) → `ctUi.due = 오늘+14` → `ctSend()` → SENT · due / 다시 그린 `ctAdminView()` 글에 "서명 대기" 칩 수 1 · 상태 칸 `.ct-pill` 모두 같은 폭 클래스 / 포털 꺼짐 사람(시연 멤버 하나를 `use:false`로)을 보내면 skipped 알림에 "포털 사용 꺼짐" / `ctMail([1,2])`가 연 주소(검사용으로 `window.open`을 잠깐 바꿔 받음)에 `user_nos=` · 번호 없는 사람은 복사 글에 메일 주소.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현** — 모양은 시안 `mock.css`(저장소 밖 `클로드 코드/뉴디자인/연봉계약서 실제 시안/시안.html` 안 `.mk-*`)를 `.ct-*` 이름으로 옮긴다.
- [ ] **Step 4: 통과 확인** — `ctSelfCheck()` · `await ctFlowCheck()` · `homeSelfCheck()` 통과, 1280px·1440px 스크린샷(cdp `--shot`)에서 표가 넘치지 않고 상태 칸 시작이 맞는지 본다.
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서 화면 ②: HR 현황 · 조정 · 빼기 · 발송 · 하이웍스 메일"`

---

### Task 10: 화면 ③ — 파기 · 새 계약 · 계약서 설정 · 서명본 내려받기 · 문의 확인

설계 4.1 · 4.5 · 4.7 · 4.8, 시안 그림 8 · 9.

**Files:**
- Modify: `public/index.html`

**Interfaces:**
- Consumes: Task 6 · 7 길, Task 9 `ctAdminView`.
- Produces:
  - `ctCancelAsk(id)` 창 — 사유(필수) + 셋 중 고르기(파기 후 재발송 · 파기 후 새로 쓰기 · 파기만, 서명 완료 줄은 "연봉이 바뀐 것은 [새 계약으로 바꾸기]" 안내) → `POST …/cancel`.
  - `ctReplaceAsk(id)` 창 — 지금 금액(읽기) · 새 금액 다섯 · 바뀌는 날 · 사유 → `POST …/replace`. 표의 그 사람 아래 줄에 "지금 계약 유효 · M/D~".
  - `ctSettingsAsk()` 창(그림 8) — 대표이사 이름 · 소재지 · 사업의 종류 · 직인 그림 고르기(PNG, 500KB 넘으면 창 안에서 막음, 고른 그림 미리 보기) → `POST /api/contracts/settings`. 카드의 직인 미리 보기는 `GET /api/contracts/settings/seal`을 Blob 주소로(로그아웃 때 `URL.revokeObjectURL`). 시연은 가짜 직인(시안의 SVG를 그림으로) · 이름 `○○○`.
  - `ctZipAsk()` 창(그림 9) → `GET /api/contracts/{year}/zip`을 받아 `연봉계약서_{year}_서명본.zip`으로 저장(`rzSave`처럼 저장 위치를 물음 — `showSaveFilePicker` 있으면). 시연은 "서명본은 서버가 만들어요" 안내만.
  - 문의: 표의 "문의 있음" 표시 · [보기] 창 위에 글 + [확인함] → `POST …/ask/close`. HR 관리 화면 숫자(`ctSwitch`의 둘째 단추 배지) = 확인 안 한 문의 수.
- [ ] **Step 1: 실패하는 검사 더하기** — 시연 흐름에서: 설정 창 저장 → `CT_MEM.settings.ceo` · 파기 셋(재발송 = 새 차수 SENT · 새로 쓰기 = 새 차수 DRAFT · 파기만 = 살아 있는 줄 없음) · 새 계약 → 새 차수 DRAFT(prevId) · 직인 500KB 넘는 파일(가짜 Blob)을 고르면 창 안 오류 · 문의가 있는 줄 → 칩 "문의 있음" 1 → 확인함 → 0.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — 검사 통과 + `devlive.py`(저장소 밖 `클로드 코드/devlive.py`, `HR_PAY_KEY` 켜고 `python devlive.py <워크트리> 8790`)로 HR 로그인 → 설정 · 가짜 암호 엑셀 올리기 · 발송 · 파기 · 새 계약 · zip 받기를 손으로 한 번(스크린샷을 보고서에).
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서 화면 ③: 파기 · 새 계약 · 계약서 설정(직인) · 서명본 내려받기 · 문의 확인"`

---

### Task 11: 화면 ④ — 직원 서명 · 서명본 · 문의 · 메뉴 숫자 · 본부장 현황

설계 4.4 · 4.5 · 4.9 · 4.10, 시안 그림 4~7.

**Files:**
- Modify: `public/index.html` — `ctContent()`(실제 로그인이면 새 길), `TODO.p2`, `homePortalTasks`(문구 "연봉계약서 서명 · M/D까지"), `authBoot`(내 계약서 받기), `authLock`(CT_LIVE 비우기 · 직인 Blob 주소 정리), `visibilitychange`(`ctWake` — `liveWake(CT_LIVE…)` 모양)

**Interfaces:**
- Consumes: Task 6 · 7 길, Task 2 블록 모양, `whDrawPdf(bytes, box)`·`whLib(['pdfjs'])`(아이폰 홈 화면 앱에서 PDF를 화면 안에 그리기), `ctInitSign`·`ctPadClear`(서명 칸 — 그대로 씀).
- Produces:
  - `ctBlocksHtml(blocks, {sealUrl, signUrl}) -> string` — 블록 → 지금 `.ct-doc` 모양 HTML(`ct-doc-title` · `ct-kv` · `h4` · `p`(굵게 run은 `<b>`) · `.ct-tbl table`(span → colspan, 머리 줄은 th) · `ct-center` · `ct-signs`(사업주 줄의 "(인)" 위에 직인 그림, 근로자 옆 서명 그림)). 글은 모두 `escapeHtml`.
  - `ctLoadMine()` → `GET /api/contracts/mine` → `CT_LIVE.mine`. `TODO.p2` = 실제면 `CT_LIVE.mine.filter(c=>c.canSign).length`, 시연은 지금 그대로.
  - 직원 화면(실제): 맨 위 서명할 계약서가 있으면 `kpiNote`("YYYY년 연봉계약서가 도착했어요. M월 D일(요일)까지 서명해 주세요. D-n" / 지났으면 붉게 "마감일이 지났어요. 서명해 주세요") → 계약서 카드(`ct-scroll gated` + `ctBlocksHtml` + 서명 칸 — 지금 `ctViewer`의 서명 칸 그대로) + [HR에 문의](`ctAskAsk(id)` 창, 200자, 보낸 뒤 "문의를 보냈어요…" / 확인되면 "HR이 확인했어요") → `ctSign(id)`: `POST …/sign {name, consent, image: pad.toDataURL('image/png')의 base64 부분}` → 성공하면 `ctLoadMine()` 다시 + 맨 위 "M.D HH:MM 서명했어요. 계약서가 잠겨 바꿀 수 없어요." + [서명본 PDF 내려받기][인쇄] + 전자서명 정보(시안 그림 5). 연중 변경으로 새 계약이 오면 아래 "지금 유효한 계약서"를 함께. "이전 계약서" 목록(바뀜 · 파기 · 지난 회차).
  - `ctPdf(id, print)` — `GET …/pdf` → 내려받기 이름 `연봉계약서_{year}.pdf`(실명 없음) / 인쇄는 원천징수영수증 `whDown(…, print)`의 숨긴 틀 방식을 그대로 쓰되 **워터마크 없이**(가능하면 `whDown`의 틀 · 정리 부분을 함수로 떼어 함께 쓴다 — `WH_PRINT_KEEP` 규칙 그대로). `navigator.standalone===true`면 내려받기 대신 화면 안에 그리기(`whDrawPdf`) + "PC에서 내려받을 수 있어요".
  - 본부장(실제, `AUTH.me.position==='DIVISION_HEAD'`): `GET /api/contracts/division?year=` → 시안 그림 7 목록(이름 · 팀 · 상태 칩 · 서명한 날, 머리에 수 · 마감일). 금액 칸 없음.
  - 시연은 지금 화면 그대로(`AUTH.live` 거짓).
  - HR 표의 [보기] — `GET /api/contracts/{id}` 블록을 `ctBlocksHtml`로 그린 큰 창(`org-modal .card:has(.ct-doc)`, 직인은 `…/seal`). Task 9까지는 [보기]가 시연 줄만 그린다(시연 `ctDocHtml`) — 이 Task에서 실제 줄을 잇는다.
- [ ] **Step 1: 실패하는 검사 더하기** — `ctSelfCheck`에 `ctBlocksHtml` 검사(가짜 블록: 굵게 run → `<b>`, span 2 → `colspan="2"`, `<script>` 글은 이스케이프, signs에 직인 그림 주소) · `await ctLiveCheck()`(실제 로그인 흉내 — `whSelfCheck`의 넷째 부분처럼 `fetch`를 잠깐 바꿔): mine에 SENT 하나 → `TODO.p2()===1` · 홈 오늘의 업무 같은 수(`homeSelfCheck`) · 서명 요청 본문 모양(`name`, `consent:true`, `image`가 PNG base64) · 서명 뒤 숫자 0 · `authLock('out')` 뒤 `CT_LIVE.mine.length===0`.
- [ ] **Step 2: 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: 통과 확인** — 검사 통과 + `devlive.py`로 가상직원 로그인 → 받은 계약서 끝까지 읽기 · 서명 · PDF 내려받기, 390px 폭(cdp `--width 390`) 스크린샷, 가상본부장 현황.
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서 화면 ④: 직원 서명 · 서명본 PDF · HR에 문의 · 메뉴 숫자 · 본부장 현황"`

---

### Task 12: 마무리 — 사용법 · 문서 · 하이웍스 확인 · 전체 검사

**Files:**
- Modify: `public/index.html`(`TOURS.ct` — 실제 로그인 단계: 화면 고르기(HR) · 계약 금액 올리기(HR) · 발송(HR) · 끝까지 읽기 · 서명하기 · HR에 문의 · 서명본 내려받기. 가리키는 곳에 id를 붙임), `CLAUDE.md`(급여·계약 > 전자연봉계약서 항목 — 원천징수영수증 항목과 같은 모양으로: 흐름 · 서버 · DB 판 14 · 하지 않기로 한 것 · 검사 · 남는 위험), `README.md`(설계 문서 링크 한 줄, 다른 설계 링크가 있는 곳)

- [ ] **Step 1: 하이웍스 여러 명 채우기 확인** — 사용자(인사담당자)에게 하이웍스 로그인을 부탁하고 앱 안 브라우저에서 `https://mails.office.hiworks.com/write?mode=normal&user_nos=<번호1>,<번호2>`(번호는 HR 화면에서 고른 두 사람)를 열어 받는 사람이 둘인지 본다. 안 되면 `ctMail`을 "번호 있는 첫 사람만 채우고 나머지는 주소 복사"로 바꾸고 검사를 고친다. 결과를 PR 본문에 적는다.
- [ ] **Step 2: `TOURS.ct` · CLAUDE.md · README** 고치기 → `tourSelfCheck()` 통과.
- [ ] **Step 3: 전체 검사** — Global Constraints의 로컬 검사 전부 + 화면 `ctSelfCheck()` · `await ctFlowCheck()` · `await ctLiveCheck()` · `rzSelfCheck()` · `psSelfCheck()` · `await whSelfCheck()` · `homeSelfCheck()` · `tourSelfCheck()`.
- [ ] **Step 4: 사용자 확인용 화면** — 시연 모드(8771)에서 HR 현황 · 확인 창(가짜 파일) · 직원 서명 화면을 찍고, devlive로 서명본 PDF 한 장을 찍어 사용자에게 보낸다(실제 확인은 사용자가 실제 암호 엑셀로 시연 모드 확인 창까지 — 숫자만 알려 받기, 원천징수영수증 4.8과 같은 방식).
- [ ] **Step 5: 커밋** — `git commit -m "연봉계약서: 사용법 · 문서 · 전체 검사"` → 사용자 "올려줘" 뒤 push · PR(본문: 배포 뒤 DB 판 14 확인 · `HR_PAY_KEY` 이미 있음 · HR이 계약서 설정(대표이사 · 직인)부터 넣기 · 실제 엑셀 확인 결과)
