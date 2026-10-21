# 원천징수영수증 실제화 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** HR이 NAS의 암호 zip(원천징수영수증 PDF)을 포털에서 골라 올리면 사람별 PDF·요약이 잠겨 저장되고, HR이 공개하면 직원이 본인 것만 요약 + 원본 PDF로 본다.

**Architecture:** 급여명세서 실제화(`backend/payslip.py`, 화면 `ps*`)와 같은 길. 브라우저가 zip을 풀고(ZipCrypto · WinZip AES), pdf.js로 쪽 글자를 읽어 사람을 나누고 숫자를 읽고 직원을 맞춘 뒤, pdf-lib로 사람별 PDF를 떼어 서버로 보낸다. 서버(`backend/withholding.py`)는 `HR_PAY_KEY`로 요약·PDF를 잠가 SQLite(`012_withholding.sql`)에 둔다.

**Tech Stack:** 단일 파일 화면 `public/index.html`(인라인 JS) · Flask blueprint · SQLite · `cryptography` AESGCM · pdf.js 3.11.174 · pdf-lib 1.17.1 (둘 다 `public/vendor/`에 넣어 우리 서버가 준다).

**Spec:** `docs/superpowers/specs/2026-10-21-withholding-receipt-live-design.md` — 모든 작업자가 먼저 읽는다. 규칙의 근거와 실측 줄 모양(3장)이 거기 있다.

## Global Constraints

- 압축 암호는 zip을 푸는 데만 쓰고 변수·localStorage·서버·로그 어디에도 남기지 않는다. 암호 입력칸은 `<form>`에 넣지 않는다.
- 주민등록번호는 HR PC 화면 안에서 생일 비교에만 쓰고 서버로 보내지 않는다(서버에는 잠근 PDF·요약 숫자만).
- 실제 직원 이름·금액·주민번호를 저장소·커밋·PR·이슈·감사로그·서버 로그에 넣지 않는다. 시험 자료는 `가상…` 이름.
- 감사로그 `after_data`에는 해 · 사람 수 · 모드만. 이름·금액·파일 이름 금지.
- 못 읽은 숫자는 `null` — 0이나 다른 계산으로 채우지 않는다.
- 비활성 직원에게는 올리지 않는다(서버도 400).
- pdf.js · pdf-lib는 판을 고정해 `public/vendor/pdfjs-3.11.174/`, `public/vendor/pdf-lib-1.17.1/`에서 준다(다른 회사 서버의 스크립트를 부르지 않는다). 직원 화면은 "원본 보기"를 누를 때만 pdf.js를 부른다.
- 화면 글: "~해요"체가 아닌 기존 급여명세서 화면의 "~합니다/~해 주세요" 말투에 맞춘다. 직원에게 보이는 금액은 `payShow`(눈 모양)로 가린다.
- HR 화면은 단추 둘: **내 원천징수영수증** / **영수증 올리기 · 공개** (`paySwitch`·`ctSwitch`와 같은 `.hr-seg` 모양).
- DB 파일 번호는 merge 순서대로(`check_file_numbers`). 이 계획은 `012_`을 쓴다 — 그사이 다른 PR이 012를 먼저 쓰면 다음 번호로 바꾼다.
- 시연 모드(서버 없음 · `AUTH.live` 거짓)는 지금 가상 화면을 그대로 두고, HR 올리기·공개는 브라우저 메모리(`wh.mem`)에서 돌려 볼 수 있게 한다.

## Review Focus

1. **한 사람 묶음에 다른 사람 쪽이 섞이는 경우** — PDF 앞머리에 주인 없는 쪽, 첫 쪽 판정이 두 번 걸리는 쪽. 섞이면 남의 영수증이 보인다 → 주인 없는 쪽은 올리기를 막아야 한다. (Task 5 `whSplit` 시험)
2. **모든 영수증에 찍히는 이름(대표자)과 같은 이름의 직원** — 대표이사 본인 영수증, 이번 zip에 사람이 한 명뿐일 때. (Task 5 `whMatch` 시험)
3. **틀린 압축 암호 · 7z/alz · 암호 없는 zip · zip 안에 PDF 0개** — 알기 쉬운 안내가 나오고 화면이 멈추지 않아야 한다. (Task 4 `whZipRead`, Task 6 흐름)
4. **올리다 중간에 끊긴 해 전체 올리기** — `pending>0`인 해는 공개도, 사람만 올리기도 막혀야 한다. (Task 2 서버 시험)
5. **공개 중인 해에 사람만 바꾸기** — 바뀐 사람만 "수정됨"·'봤음' 초기화, 나머지 직원의 '봤음'은 그대로. (Task 2 서버 시험)

---

### Task 1: DB 표 `012_withholding.sql`

**Files:**
- Create: `backend/hr_eval/sql/012_withholding.sql`
- Modify: `backend/hr_eval/sql/check_schema.py` (새 `check_withholding_tables` + `main`에서 부르기)

**Interfaces:**
- Produces: 표 `withholding_years(year, status, pending, uploaded_at, uploaded_by, opened_at, opened_by)`, `withholding(year, user_id, sum, pdf, revised, uploaded_at, seen_at)` — Task 2가 쓴다.

- [ ] **Step 1: 실패하는 검사 쓰기** — `check_schema.py`에 `check_withholding_tables(conn)`("24. 원천징수영수증 (012)"), `check_payslip_tables` 바로 뒤에서 부른다. 가상 직원 한 명을 만들고 `_report`로:
  - `PRAGMA user_version >= 12`
  - 저장됨: `INSERT INTO withholding_years (year, status, pending, uploaded_at, uploaded_by) VALUES (2025, 'DRAFT', 0, '2027-03-02T09:00:00Z', ?)`
  - 거부됨(IntegrityError): year 1999 · 2100 · NULL · '2025'(글자) / status 'X' / pending -1 · 1.5 / 같은 해 두 번 / `status='OPEN'`인데 `opened_at` NULL / `status='OPEN'`인데 `pending` 2
  - `withholding` 저장됨: `(2025, uid, X'00'*29, X'00'*29, 0, '2027-03-02T09:00:00Z')`; 거부됨: 없는 해 · 같은 (해, 직원) 두 번 · `sum`이 글자 · `sum` 28바이트 이하 · `pdf` 28바이트 이하 · `revised` 2
  - 해를 지우면 그 해 줄도 지워짐(CASCADE), 줄이 있는 직원 삭제는 거부(RESTRICT)
- [ ] **Step 2: 실행해 실패 확인** — `cd backend && python -m hr_eval.sql.check_schema` → 판 번호/표 없음으로 실패.
- [ ] **Step 3: `012_withholding.sql` 쓰기** — `011_payslips.sql`의 주석 방식·제약 모양을 그대로 따른다. year `INTEGER NOT NULL PRIMARY KEY CHECK (typeof(year)='integer' AND year BETWEEN 2000 AND 2099)`, status `IN ('DRAFT','OPEN')`, pending `typeof='integer' AND >=0`, `CHECK (status <> 'OPEN' OR (opened_at IS NOT NULL AND pending = 0))`. `withholding`: `year REFERENCES withholding_years(year) ON DELETE CASCADE`, `user_id REFERENCES users(id) ON DELETE RESTRICT`, `sum`/`pdf` `BLOB CHECK (typeof='blob' AND length > 28)`, `revised IN (0,1) DEFAULT 0`, `uploaded_at TEXT NOT NULL`, `seen_at NULL`, `PRIMARY KEY (year, user_id)`, `INDEX ON withholding(user_id)`.
- [ ] **Step 4: 실행해 통과 확인** — `python -m hr_eval.sql.check_schema` → 전부 통과(0번 번호 검사 포함), `python -m hr_eval.sql.check_sqlite_features` 통과.
- [ ] **Step 5: Commit** — `git add backend/hr_eval/sql/012_withholding.sql backend/hr_eval/sql/check_schema.py && git commit -m "원천징수영수증: DB 표 (012)"`

### Task 2: 서버 `backend/withholding.py`

**Files:**
- Create: `backend/withholding.py`
- Modify: `backend/app.py` (blueprint 붙이기 — payslip 바로 아래 한 줄), `backend/auth_check.py` (`check_withholding(hr)` + `main`에서 `check_payslips(hr)` 다음에 부르기)

**Interfaces:**
- Consumes: `payslip.pay_key() -> bytes | None`, `auth._db()`, `auth._hr_only()`, `auth._audit(conn, AuditEntry)`, `auth._iso(auth._now())`, `g.actor.user_id`.
- Produces (HTTP — Task 6·7이 쓴다):
  - `GET /api/withholding` → `{"years":[{"year":2025,"revised":bool,"seen":bool,"sum":SUM}]}` 본인 · 공개된 해만 · 최근 해부터. `?user=<id>`(HR) → 그 직원의 모든 해 + `"status"`, 남의 것이면 감사로그 `WITHHOLDING_VIEW` `{"years": n}`. 직원이 `?user=`를 붙이면 403(본인 번호여도). 열쇠 없으면 `years:[]`.
  - `GET /api/withholding/<year>/pdf` (`?user=` HR) → `application/pdf` 바이트, 본인은 공개된 해만(아니면 404), 남의 것이면 감사로그 `WITHHOLDING_PDF_VIEW` `{"year": y}`.
  - `GET /api/withholding/years` (HR) → `{"ready":bool,"years":[{"year","status","people","pending","revised","uploadedAt","openedAt"}]}` (`people` = 줄 수, `revised` = 수정된 줄 수).
  - `POST /api/withholding/<year>` (HR) 본문 `{"mode":"all"|"some","reset":bool,"total":int,"people":[{"userId":int,"sum":SUM,"pdf":base64}]}` → `{"saved":n,"pending":m}`.
  - `POST /api/withholding/<year>/open` · `/close` (HR) → `{"status":"OPEN"|"DRAFT"}`. `/seen` (본인) → `{"ok":true}`.
  - `SUM` = `{"gross","taxDecided","localDecided","taxPrepaid","localPrepaid","taxSettle","localSettle"}` 각각 정수(절댓값 ≤ 100,000,000,000) 또는 `null`. 칸이 더 있거나 빠지면 400.
  - 잠금: `AESGCM(key)`, nonce 12바이트, aad `f"wh|{year}|{user_id}|sum"` / `f"wh|{year}|{user_id}|pdf"`. 풀리지 않는 줄은 건너뛰고 `logger.error`(해 · 직원 번호만).
  - 응답 전부 `Cache-Control: no-store`.

- [ ] **Step 1: 실패하는 검사 쓰기** — `auth_check.py`에 `check_withholding(hr)`. `check_payslips`의 준비(열쇠를 `bytes(range(32))`로 만들어 환경에 넣고 끝나면 되돌리기, `get`/`post` 람다, `snap`)를 그대로 따른다. PDF는 `b"%PDF-1.4\n" + b"x"*100` 같은 가짜. 검사(`ok(...)`)는 아래를 모두:
  - 열쇠 없음: 올리기 503, `/years`의 `ready` 거짓, 직원 목록 `[]`
  - HR 아님: 올리기·`/years`·`?user=` 403
  - 모양 거부 400(아무것도 저장 안 됨 — `snap()` 전후 같음): `mode` 틀림 · year 1999 · `sum` 칸 하나 빠짐 · `sum` 값이 실수/글자/참거짓 · `pdf`가 `%PDF`로 시작 안 함 · base64 아님 · PDF 3,000,000바이트 초과 · 본문 4,700,000바이트 초과 · 같은 직원 두 번 · 없는 직원 · 비활성 직원
  - 해 전체: `reset:true,total:3`으로 2명 → `pending` 1, `/open` 409 · 사람만(`mode:"some"`) 409 → 나머지 1명(`reset:false`) → `pending` 0 → `/open` 200 → 공개 중 `mode:"all",reset:true` 409 · `reset:false` 묶음이 `pending`보다 많으면 409
  - 본인: 공개 전 해는 목록에 없음·PDF 404·`/seen` 404 → 공개 후 보임, `sum` 왕복 그대로(null 칸 포함), PDF 바이트 그대로, `/seen` 뒤 `seen` 참
  - 사람만: 공개 중인 해에 이미 있는 1명 바꾸기 → 그 사람 `revised` 참·`seen` 거짓, 다른 사람 `seen` 그대로, 새 사람 더하기 → `revised` 거짓
  - 공개했던 해를 내린 뒤 해 전체로 다시 → 모든 줄 `revised` 참
  - 잠금: DB의 `sum`·`pdf`에 금액 숫자 글자·`%PDF`가 보이지 않음, 다른 직원 자리로 줄을 옮기면 목록에서 빠짐(오류 아님)
  - `?user=`: 직원 403(본인 번호여도), HR이 남의 것 → `WITHHOLDING_VIEW`·`WITHHOLDING_PDF_VIEW` 감사로그, 본인 번호면 감사로그 없음
  - 감사로그 `WITHHOLDING_UPLOAD/OPEN/CLOSE`의 `after_data`에 이름·금액·파일 이름 없음(가짜 이름 `가상` 글자와 금액 숫자가 들어 있지 않음)
  - 응답 헤더 `Cache-Control: no-store`
- [ ] **Step 2: 실행해 실패 확인** — `cd backend && python -m auth_check` → `check_withholding`에서 404/실패.
- [ ] **Step 3: `withholding.py` 쓰기** — 맨 위 문서 주석에 길·규칙을 `payslip.py`처럼 적는다. 상수 `YEAR = re.compile(r"20[0-9][0-9]")`, `BODY_MAX = 4_700_000`, `PDF_MAX = 3_000_000`, `PEOPLE_MAX = 200`, `WON_MAX = 100_000_000_000`, `SUM_KEYS`. 쓰기는 `BEGIN IMMEDIATE … COMMIT/ROLLBACK`(payslip `upload`와 같은 모양). 규칙:
  - `mode:"all", reset:true`: 해가 OPEN이면 409. 해 줄을 DRAFT로 만들거나 바꾸고 `pending = total - len(people)`(`total >= len(people)`가 아니면 400), 그 해 줄 모두 지우고 넣는다. `opened_at`이 있던 해면 새 줄 `revised=1`.
  - `mode:"all", reset:false`: 해가 없거나 `pending < len(people)`면 409. 이미 있는 직원이면 400. 넣고 `pending -= len`.
  - `mode:"some"`: `pending > 0`이면 409. 해가 없으면 DRAFT로 만든다. 사람마다 있으면 바꾸기(`revised = 1 if 해에 opened_at이 있으면`, `seen_at = NULL`), 없으면 더하기(`revised=0`).
  - `/open`: 줄 0개면 409, `pending>0`이면 409("올리다 멈춘 해예요. 다시 올려 주세요").
  - 409 문구: 공개 중 해 전체 = "공개 중인 해입니다. 공개를 내린 뒤 다시 올리거나, 사람만 바꾸기로 올려 주세요."
- [ ] **Step 4: `app.py`에 `import withholding` · `app.register_blueprint(withholding.bp)` (주석 한 줄)**
- [ ] **Step 5: 실행해 통과 확인** — `python -m auth_check` 전부 통과, `python -m hr_eval.selfcheck` 통과.
- [ ] **Step 6: Commit** — `git add backend/withholding.py backend/app.py backend/auth_check.py && git commit -m "원천징수영수증: 서버 (잠가 저장 · 해 전체/사람만 · 공개)"`

### Task 3: pdf.js · pdf-lib 넣기와 불러오기

**Files:**
- Create: `public/vendor/pdfjs-3.11.174/pdf.min.js`, `public/vendor/pdfjs-3.11.174/pdf.worker.min.js`, `public/vendor/pdfjs-3.11.174/cmaps/*.bcmap`(이름에 `KSC`·`KSCms`·`KSCpc`·`UniKS`·`Adobe-Korea1`이 든 것만), `public/vendor/pdf-lib-1.17.1/pdf-lib.min.js`
- Modify: `.ci/check-public.sh` (`ALLOWED_EXT`에 `bcmap`), `public/index.html` (`whLib()`)

**Interfaces:**
- Produces: `async function whLib(need = ['pdfjs']) -> { pdfjs, PDFLib }` — `need`에 `'pdflib'`이 있으면 pdf-lib도. 한 번 불러온 것은 다시 부르지 않는다(약속을 기억). `pdfjs.GlobalWorkerOptions.workerSrc = 'vendor/pdfjs-3.11.174/pdf.worker.min.js'`. 문서를 열 때 쓸 옵션 상수 `WH_PDF_OPTS = { cMapUrl:'vendor/pdfjs-3.11.174/cmaps/', cMapPacked:true }`.

- [ ] **Step 1: 내려받기 허락 받기** — 사용자에게 파일 이름 · 출처(`https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/…`, `https://cdnjs.cloudflare.com/ajax/libs/pdf-lib/1.17.1/pdf-lib.min.js`, cmaps는 `https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/cmaps/…`) · 대략 크기(약 3MB)를 알리고 허락을 받는다.
- [ ] **Step 2: 내려받고 원본 확인** — 받은 각 파일의 SHA-256을 cdnjs가 밝힌 값(`https://api.cdnjs.com/libraries/pdf.js/3.11.174?fields=sri`, `…/pdf-lib/1.17.1?fields=sri`의 sha512 → 받은 파일의 sha512 base64와 같아야 함)과 맞춘다. 다르면 멈추고 알린다. 각 js 맨 위에 라이선스 주석(Apache-2.0 · MIT)이 있는지 본다.
- [ ] **Step 3: `check-public.sh`에 `bcmap` 더하고 실행** — `bash .ci/check-public.sh` 통과.
- [ ] **Step 4: `whLib` 쓰기** — `<script>`를 붙여 불러오고 `window.pdfjsLib`·`window.PDFLib`를 돌려준다. 실패하면 Error('LIB').
- [ ] **Step 5: 확인** — 미리보기(8765)에서 콘솔 `await whLib(['pdfjs','pdflib'])` → 둘 다 있음, 네트워크에 다른 회사 주소 없음.
- [ ] **Step 6: Commit** — `git add public/vendor .ci/check-public.sh public/index.html && git commit -m "원천징수영수증: pdf.js 3.11.174 · pdf-lib 1.17.1 (우리 서버에서)"`

### Task 4: zip 풀기 (브라우저)

**Files:**
- Create: `tools/wh_fixtures.py` (시험용 가짜 zip·PDF 만들기 — 저장소에 둔다, 실제 자료 없음)
- Modify: `public/index.html` (원천징수영수증 구역에 `wh*` 함수, `whSelfCheck` 앞부분)

**Interfaces:**
- Produces:
  - `whKind(u8: Uint8Array) -> 'zip'|'pdf'|'7z'|'alz'|'egg'|'rar'|'?'` (첫 바이트: `PK\x03\x04` · `%PDF` · `7z\xBC\xAF\x27\x1C` · `ALZ\x01` · `EGGA` · `Rar!`)
  - `whZipList(buf: ArrayBuffer) -> [{ name, encrypted:bool, aes:{strength:1|2|3, method:0|8}|null, method, crc, csize, usize, flags, modTime, at }]` (가운데 목록에서. `rzXlsx`의 목차 읽기와 같은 방식)
  - `async whZipRead(buf, entry, pw: string|null) -> Uint8Array` — ZipCrypto(12바이트 머리, 확인 바이트 = flags bit3이면 `modTime>>8`, 아니면 `crc>>>24`) · WinZip AES(PBKDF2-HMAC-SHA1 1000회 `crypto.subtle`, 비밀번호 확인 2바이트, HMAC-SHA1 앞 10바이트 확인, AES-CTR 작은 끝 카운터 1부터) → deflate(`DecompressionStream('deflate-raw')`) 또는 저장. 풀린 내용의 CRC32가 목록과 다르면 Error('PW'). 틀린 암호 Error('PW'), 모르는 압축 방식 Error('ZIP').
  - `whAesEcb(key: Uint8Array) -> (block16: Uint8Array) => Uint8Array` — 순수 JS AES 암호화(128·192·256). AES-CTR 열쇠흐름을 블록마다 `crypto.subtle`로 부르면 큰 파일에서 느려서 둔다(주석에 적는다).
- 가짜 자료: `tools/wh_fixtures.py zip-samples` → base64 두 줄을 출력(ZipCrypto · AES-256, 안에 `a.txt` = `"가상 시험 123"` UTF-8, 암호 `hr-portal-check`). 이것을 `index.html`의 `WH_ZIP_ZC`·`WH_ZIP_AES` 상수로 넣는다. ZipCrypto 암호화는 스크립트 안에서 직접 한다(외부 도구 없이).

- [ ] **Step 1: 가짜 zip 만들기** — `python tools/wh_fixtures.py zip-samples` → 두 zip을 반디집 `bz.exe t -p:hr-portal-check`(있으면)로 "All OK" 확인, base64를 상수로 넣는다.
- [ ] **Step 2: 실패하는 검사 쓰기** — `async function whSelfCheck()`의 zip 부분:
  - `whAesEcb(0x00..0x1f)` 로 `00112233445566778899aabbccddeeff` → `8ea2b7ca516745bfeafc49904b496089` (FIPS-197 AES-256), 128비트 키 `000102…0f` → `69c4e0d86a7b0430d8cdb78070b4c55a`
  - 두 샘플 모두 `whZipList` 한 줄, `encrypted` 참, AES 샘플 `aes.strength===3`
  - 두 샘플 모두 `whZipRead(…,'hr-portal-check')` → UTF-8 `"가상 시험 123"`
  - 틀린 암호 → 두 샘플 모두 Error('PW')
  - `whKind`: 샘플 → 'zip', `%PDF-` → 'pdf', 7z 머리 → '7z'
- [ ] **Step 3: 미리보기 콘솔에서 `await whSelfCheck()` → 실패 확인**
- [ ] **Step 4: 구현** (`whKind`, `whZipList`, `whZipRead`, `whAesEcb`, CRC32 표)
- [ ] **Step 5: `await whSelfCheck()` 통과**
- [ ] **Step 6: Commit** — `git add tools/wh_fixtures.py public/index.html && git commit -m "원천징수영수증: zip 풀기 (ZipCrypto · AES, 화면 안에서)"`

### Task 5: 쪽 글자 → 사람 · 숫자 · 직원 (순수 규칙)

**Files:**
- Modify: `public/index.html` (`wh*` 규칙 함수, `whSelfCheck` 둘째 부분)

**Interfaces:**
- Consumes: 없음(글자만 받는 순수 함수).
- Produces:
  - `whLines(items: [{str, transform:[a,b,c,d,x,y]}]) -> string[]` — 같은 높이(y 차이 2 이하)를 한 줄로, 왼쪽(x)부터, 조각 사이에 공백 하나. 위에서 아래 순서.
  - `whFlat(s) -> string` 공백 지우기.
  - `whSplit(files: [{ name, pages:[{ lines:string[] }] }]) -> { people:[{ file:i, from, to }], orphans:[{ file:i, pages:[n…] }] }` — 첫 쪽 = `whFlat(쪽 글자)`에 `원천징수영수증`과 `차감징수세액`이 함께. (스펙 4.4)
  - `whNums(p1: string[], p2: string[]|null) -> { sum: SUM, check:{ decided2:boolean|null } }` — 스펙 4.5: 결정세액 줄 처음 두 금액, 차감징수세액 줄 처음 두 금액(음수), 기납부 = 주(현)근무지 + 종(전)근무지(없으면 0)이고 `t10(결정−기납부)===차감`(소득세·지방 둘 다)일 때만, 아니면 기납부 두 칸 `null`. 총급여 = `whGross(공제줄첫금액, 금액줄첫금액)`. `decided2` = 둘째 쪽 `결정세액(` 줄 금액이 소득세 결정과 같은지(줄이 없으면 null). 금액 모양 `/-?\d{1,3}(?:,\d{3})+|-?\d+/g`, `( - - - )` 같은 빈 자리는 금액이 아니다.
  - `whDeduct(gross: number) -> number` 근로소득공제(소득세법 제47조, 원 단위 버림, 한도 2,000만) — 표는 이 함수 한 곳(`// 법이 바뀌면 여기`).
  - `whGross(a: number|null, b: number|null) -> number|null` — 스펙 4.5의 해석 ①·② 중 `whDeduct`로 맞는 것이 정확히 하나일 때 그 총급여.
  - `whRrnMd(flat: string) -> 'MM-DD'|null` — `/\d{6}-[1-8]\d{6}/g`가 한 가지 값만일 때 그 3~6번째 자리.
  - `whMatch(groups: [{ flat:string }], members: [{ id, name, active, birthday }]) -> [{ id:number|null, why:''|'NONE'|'SAME'|'TWICE'|'OUT', bday:'ok'|'diff'|'' }]` — 스펙 4.6(모든 첫 쪽에 나오는 이름 빼기는 묶음이 둘 이상일 때만, 빼고 아무도 없으면 뺀 이름이 하나일 때 그 사람). `bday`: 맞춘 직원의 `birthday`와 `whRrnMd`가 다르면 'diff'. 후보 이름이 다른 후보 이름 속에 들어 있으면(예: '가상일' ⊂ '가상일순') 짧은 쪽은 뺀다.

- [ ] **Step 1: 실패하는 검사 쓰기** — `whSelfCheck` 둘째 부분(글자 견본은 스펙 3장의 가린 줄 모양에 가상 숫자를 넣어 만든다):
  - `whLines`: y 100의 `['결정세액',x10]`,`['1,000',x50]`,`['100',x80]` + y 120의 `['차감징수세액',x10]` → `['차감징수세액', '결정세액 1,000 100']`(위→아래 = y 큰 것부터)
  - `whSplit`: 쪽 [첫, 다음, 다음, 첫, 다음] → 사람 2 (0~2, 3~4), 주인 없는 쪽 없음 / [다음, 첫] → orphans 쪽 0 / 파일 둘이면 사람이 파일을 넘지 않음
  - `whNums` 견본(결정 `2,153,860 215,386`, 주(현) `4,445,520 444,460`, 차감 `-2,291,660 -229,070`) → `taxSettle -2291660`, `localSettle -229070`, `taxPrepaid 4445520`, `localPrepaid 444460`; 주(현)을 `4,445,529`로 바꾸면 → 여전히 맞음(절사 범위 안) / `4,445,600`이면 → 기납부 둘 다 null, 나머지 그대로; 차감 줄이 없으면 `taxSettle` null
  - `whDeduct(64353030) === 12967651`, `whDeduct(5000000) === 3500000`, `whDeduct(300000000) === 18750000`, `whDeduct(400000000) === 20000000`(한도) — 정수 계산(`Math.floor((g-45e6)*5/100)` 식), 실수 오차 없게
  - `whGross(12967651, 51385379) === 64353030`(해석 ①), `whGross(64353030, 51385379) === 64353030`(해석 ②), `whGross(1, 2) === null`
  - `whRrnMd('…900315-1234567…') === '03-15'`, 두 값이면 null, 없으면 null
  - `whMatch`: 명단 [가상일(1), 가상이(2), 가상대표(3), 가상퇴사(4, active:false), 가상일(5)] — 첫 쪽들에 모두 '가상대표'가 있을 때 → 가상이 묶음 → 2; 가상대표만 있는 묶음 → 3; '가상일' → SAME; 아무 이름 없음 → NONE; 가상퇴사 → OUT; 가상이 두 묶음 → 둘 다 TWICE; 묶음이 하나뿐이면 대표 빼기 안 함(이름 둘 → SAME); 생일 `03-15`인데 주민번호 `0316` → bday 'diff'; 명단에 '가상일'과 '가상일순'이 있고 첫 쪽에 '가상일순'만 있으면 → 가상일순(다른 후보 이름 속에 든 짧은 이름은 후보에서 뺀다)
- [ ] **Step 2: `await whSelfCheck()` 실패 확인**
- [ ] **Step 3: 구현**
- [ ] **Step 4: `await whSelfCheck()` 통과**
- [ ] **Step 5: Commit** — `git commit -am "원천징수영수증: 사람 나누기 · 숫자 읽기 · 직원 맞추기 규칙"`

### Task 6: HR 올리기 · 확인 창 · 공개

**Files:**
- Modify: `public/index.html` — `rzPick`/`rzLoad`/`rzAskPw`에 `kind==='wh'` 길, 새 `wh` 상태와 `whSwitch`·`whAdmin`·`whLoad`·`whStage`·`whBlock`·`whConfirm`·`whUpload`·`whOpenAsk`·`whSetOpen`·`whPreview`, `whContent` 맨 앞에서 HR 관리 화면으로 가지 나누기. 모양은 `.ps-*` 재사용, 새 클래스가 필요하면 `.wh-*`.

**Interfaces:**
- Consumes: Task 3 `whLib`, Task 4 `whKind`/`whZipList`/`whZipRead`, Task 5 전부, `msLoad`/`MS_LIVE`, `orgModal`/`orgCloseModal`, `kpiFlash`, `kpiNote`, Task 2 HTTP.
- Produces:
  - `const wh = { view:'me', years:[], ready:true, state:'', pick:null, mem:{ years:{}, rows:{} }, prev:null }` — `view`: HR 화면 'me' | 'admin'. `mem.years[y] = { year, status, pending:0, people, revised, uploadedAt, openedAt }`, `mem.rows[y][키] = { sum, pdf:Uint8Array, revised, seen }`(시연 키 = 시연 역할 열쇠).
  - `wh.pick = { file, mode:'all'|'some', year, groups:[{ file, from, to, flat, name, sum, check, pdfBytes:null }], to:[id|null|'skip'], why:[…], bday:[…], orphans, skipped:{ other:n }, err:'', busy:false }`
  - `whBlock() -> string` (빈 글자 = 올릴 수 있다): 고른 파일 없음 · 주인 없는 쪽 · 귀속연도(2000~2099 정수) · `mode:'all'`인데 공개 중인 해 · `pending>0`인 해에 사람만 · 고르지 않은 줄 · 같은 직원 두 줄(TWICE를 그대로 둠) · 올릴 사람 0명 · 한 사람 PDF가 3,000,000바이트 초과.
  - `whSwitch() -> string` — `id="whSeg"`, 단추 '내 원천징수영수증' / '영수증 올리기 · 공개'.

- [ ] **Step 1: `rzLoad` 가지** — `kind==='wh'`이면 `rzLocked` 검사 전에 `return whLoad(file, pw)`; `rzPick('wh')`는 `accept='.zip'`; `rzAskPw`의 안내 글은 `kind==='wh'`이면 "반디집에서 건 <b>압축 암호</b>를 넣습니다. 암호는 이 파일을 여는 데만 쓰고 어디에도 저장하지 않습니다.", 제목 "암호가 걸린 압축 파일입니다". `whLoad`: `whKind`가 zip이 아니면 안내(7z·알집 = "반디집에서 <b>zip</b> 형식으로 다시 압축해 주세요", PDF = "PDF를 암호 zip으로 묶어 올려 주세요") / 목록에 암호 걸린 항목이 있고 `pw==null`이면 `rzAskPw('wh', file)` / Error('PW')면 `rzAskPw('wh', file, '암호가 맞지 않습니다. 다시 넣어 주세요.')` / PDF 0개면 안내 / 그 밖은 `whStage(file.name, pdfs, encrypted)`. 암호는 `whZipRead`에 넘긴 뒤 들고 있지 않는다.
- [ ] **Step 2: `whStage`** — `whLib(['pdfjs'])` → PDF마다 쪽 글자(`getTextContent` → `whLines`) → `whSplit` → 사람마다 `whNums`(첫 쪽, 다음 쪽) → `whMatch`(명단 = 실제 `MS_LIVE.list` 활성 + 비활성(OUT 판단용), 시연 = `psMembers()`와 같은 모양으로 시연 인물). 쪽 수가 많으면 "읽는 중… n/N쪽" 창. 귀속연도 기본 = 올해−1.
- [ ] **Step 3: `whConfirm`** — 스펙 4.7: 맨 위 모드(해 전체/사람만 — 큰 글씨) · 귀속연도 칸 · PDF·사람·맞춘·못 맞춘 수 · 환급 합계·추가납부 합계("위하고 합계와 같은지 확인해 주세요") · 숫자를 못 읽은 사람 수 · 생일이 다른 사람 수 · 공개 중인 해 + 사람만이면 "이 N명에게 바로 보이고 '수정됨'이 붙어요", 사람만이면 "바꿈 n · 더함 m". 맞춘 사람 줄(접힘) / 못 맞춘 사람: 첫 쪽 작은 그림(pdf.js `render` 폭 160px 캔버스) + 직원 고르기 + "이번에서 빼기". "읽은 모양 보기"(접힘): 사람마다 `결정세액 찾음/못 찾음 · 금액 n개 · 자릿수`, 기납부·차감·총급여도 — 이름·금액 없이(스펙 4.8). 단추·안내는 `whBlock()`만 본다. 날짜·숫자 칸은 `psSync`처럼 바뀌는 곳만 고친다.
- [ ] **Step 4: `whUpload`** — `whLib(['pdflib'])`로 사람마다 `PDFDocument.create()` + `copyPages(원본, from..to)` → 바이트. 실제: 사람을 본문 4,500,000바이트 이하 묶음으로 나눠 `POST /api/withholding/<year>`(첫 묶음 `reset: mode==='all'`, `total` = 올릴 사람 수), 하나라도 실패하면 창에 이유(창을 닫지 않음) + 해 전체였으면 "올리다 멈춤 — 다시 올려 주세요". 시연: `wh.mem`에 같은 규칙(해 전체 = 지우고 넣기·OPEN이면 거부 / 사람만 = 바꾸기·더하기, 공개된 적 있으면 revised). 끝나면 `kpiFlash('… N명 원천징수영수증을 올렸습니다. 아직 공개 전입니다.')`(사람만+공개 중이면 "바로 보입니다").
- [ ] **Step 5: `whAdmin` · `whOpenAsk` · `whSetOpen` · `whPreview`** — `psAdmin`과 같은 표: 귀속연도 · 인원 · 상태(공개됨·공개 전·"올리다 멈춤 n명 남음") · 수정 n명 · 단추(미리 보기 / 공개·공개 내리기 / 해 전체 다시 올리기). 맨 위 도구: `id="whUp"` "해 전체 올리기"(`wh.pick` 모드 all로 `rzPick('wh')`), "사람만 바꾸기 · 더하기", 시연이면 "시연: 예시 자료로 올려 보기"(가짜 zip 없이 `whStage`에 글자 견본 쪽을 넘기는 길 — 그림은 "시연" 상자). 열쇠 없음이면 급여명세서와 같은 경고.
- [ ] **Step 6: 확인** — 미리보기(8765, 시연)에서 HR 역할 → 원천징수영수증 → 영수증 올리기 · 공개 → `python tools/wh_fixtures.py receipt-zip <스크래치패드 폴더>`가 만든 가짜 zip(가상 3명, 첫 쪽들에 '가상대표', 쪽 4·5·3, 숫자 견본 모양, ZipCrypto와 AES 각각)을 골라 → 암호 → 확인 창: 3명 맞음, 환급/추가 합계가 스크립트가 출력한 값과 같음, 숫자 못 읽음 0 → 올리기 → 공개 → 사람만으로 1명 바꾸기. 틀린 암호·7z 가짜 파일·암호 없는 zip도 한 번씩. 화면을 찍어 본다.
- [ ] **Step 7: Commit** — `git commit -am "원천징수영수증: HR 올리기 · 확인 창 · 공개 (해 전체 / 사람만)"`

### Task 7: 직원 화면 · 메뉴 숫자 · 사용법

**Files:**
- Modify: `public/index.html` — `whContent`(실제 로그인 가지), `WH_LIVE`, `whLoadMine`, `whViewer`, `TODO.p3`, `homePortalTasks`, `TOURS`에 `wh` 사용법, `msSeeList` 문구 확인, `todoSelfCheck`/`homeSelfCheck` 영향 확인.

**Interfaces:**
- Consumes: Task 2 HTTP, Task 3 `whLib`, Task 6 `wh`·`whSwitch`·`whAdmin`.
- Produces: `const WH_LIVE = { state:'', who:null, years:[], mine:[] }`(`PAY_LIVE`와 같은 방식) · `async whLoad…`는 Task 6과 이름이 겹치지 않게 `whFetch(who)`(지금 화면) · `whFetchMine()`(메뉴 숫자) · `whViewer(year, who)` · `TODO.p3 = ()=>AUTH.live ? WH_LIVE.mine.filter(y=>!y.seen).length : 0`.

- [ ] **Step 1: 요약 화면** — 실제 로그인이면 서버 해 목록으로 지금 화면 모양을 그린다: 맨 위 = `taxSettle + localSettle`(둘 다 null이면 효과 없이 "요약을 만들지 못했습니다. 원본 PDF를 확인해 주세요."), 흐름 줄은 값이 있는 것만(① 총급여 ② 결정세액 = 소득세+지방, 둘 중 하나라도 null이면 그 줄 숨김 ③ 미리 낸 세금 ④ 환급/추가), 실효세율은 총급여·결정 소득세가 있을 때만. "공제받은 주요 항목" 대신 "자세한 공제 내역은 원본 PDF에서 확인해 주세요." 수정됨·새 영수증 표시는 급여명세서 `payBadges`와 같은 모양. 처음 연 해는 `/seen`(낙관적으로 숫자 먼저 줄임 — `paySeen`과 같은 방식).
- [ ] **Step 2: 원본 보기 · 내려받기** — "원본 PDF 보기" → `whViewer`: PDF를 받아(`fetch …/pdf`) `whLib(['pdfjs'])`로 쪽마다 캔버스(카드 폭, 기기 화소 비율 반영)를 화면 안에 차례로 그린다 + "닫기". "내려받기"는 Blob + `<a download="원천징수영수증_${year}귀속.pdf">`, 아이폰 홈 화면 앱(`navigator.standalone===true`)이면 단추 대신 "PC에서 내려받을 수 있어요".
- [ ] **Step 3: HR이 직원 고르기** — '내 원천징수영수증' 화면에 `payPickerLive`와 같은 고르기(직원을 고르면 `?user=`, 공개 전 해도, 상태 표시).
- [ ] **Step 4: 메뉴 숫자** — `TODO.p3`, `homePortalTasks`에 `add(TODO.p3(), '새 원천징수영수증 확인', 'pay', 'p3', …)`, 로그인 직후 `whFetchMine()`(급여명세서 `payLoadMine`을 부르는 자리 옆), 화면을 다시 볼 때(`payWake` 옆) 새로.
- [ ] **Step 5: 사용법** — `TOURS`에 `wh:{ label:'원천징수영수증 보는 법', tab:'p3', steps }` — `#whSeg`(HR) · `#whUp`(HR) · 맨 위 금액 · 원본 보기 단추. 안내할 칸이 없으면 단계가 빠지는 기존 규칙을 따른다.
- [ ] **Step 6: 확인** — 콘솔 `whSelfCheck()`·`homeSelfCheck()`·`todoSelfCheck()`·`tourSelfCheck()`·`paySelfCheck()`·`psSelfCheck()` 통과. `클로드 코드/devlive.py`(저장소 밖)로 실제 서버 + 가짜 하이웍스를 띄워 HR로 가짜 zip 올리기 → 공개 → 가상직원으로 들어가 요약·원본 보기·내려받기·메뉴 숫자 → 사람만 바꾸기 → 그 직원만 "수정됨". 휴대폰 폭(390px)에서 원본 보기 한 번. 화면을 찍는다.
- [ ] **Step 7: Commit** — `git commit -am "원천징수영수증: 직원 화면 (요약 · 원본 보기) · 새 영수증 숫자 · 사용법"`

### Task 8: 문서 · 실제 파일 확인 · PR

**Files:**
- Modify: `CLAUDE.md`("급여·계약 > 원천징수영수증" 항목 — 급여명세서 항목처럼: 흐름 · 지키는 것 · 서버 · DB 판 12 · 검사 · 실제 파일 확인 방법), `README.md`(원천징수영수증이 시연이라고 적힌 곳이 있으면), `.env.example`(바꿀 것 없음 확인)
- (저장소 밖) `클로드 코드/check_wh_zip.py` — 바꿀 필요 없음

- [ ] **Step 1: 문서** — CLAUDE.md에 새 항목. 실제 파일 확인은 "PC 미리보기 시연 모드에서 확인 창까지 + 읽은 모양 보기"(스펙 4.8)를 적는다.
- [ ] **Step 2: 로컬 검사 전부** — `export PYTHONIOENCODING=utf-8; for f in check-encoding.sh check-secrets.sh check-pii.sh check-public.sh; do bash .ci/$f; done; python .ci/check-html.py` + `cd backend && python -m hr_eval.selfcheck && python -m auth_check && python -m hr_eval.sql.check_schema && python -m hr_eval.sql.check_sqlite_features` 모두 통과.
- [ ] **Step 3: 전체 검토** — 브랜치 전체를 새 검토자에게(서브에이전트 방식의 마지막 검토).
- [ ] **Step 4: push · PR** — PR 본문(템플릿 항목): 배포 시 주의 = DB 판 12(merge 2분 뒤 서버가 적용), `HR_PAY_KEY` 그대로 사용, 되돌리기. CI 5종 통과까지.
- [ ] **Step 5: 인사담당자 실제 파일 확인 (merge 전 필수)** — 인사담당자에게 PC 미리보기(8765, 이 브랜치)에서 실제 zip을 골라 확인 창까지 가 보고 숫자만 알려 달라고 한다: 사람 수 · 맞춘/못 맞춘 수 · 숫자 못 읽은 사람 수 · 환급 합계·추가납부 합계가 위하고와 같은지. 문제가 있으면 "읽은 모양 보기"를 붙여 달라고 해 원인을 고친다(실제 이름·금액은 받지 않는다).
- [ ] **Step 6: "merge 해줘"를 기다린다** — 사용자가 말하기 전에는 merge하지 않는다.
