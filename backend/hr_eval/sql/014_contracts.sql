-- 전자연봉계약서 실제화 — 계약서 · 계약서 설정 · 직인 그림 표 (2026-10-22 인사담당자 — 연봉계약서 실제화, 설계 5.4).
-- 서버 DB 는 013(원천징수영수증)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 14.
-- 번호 규칙: 표를 바꿀 때는 001 을 고치지 말고 다음 번호 파일을 더한다. 번호는 merge 순서대로 — 다른 PR 이 먼저 014 를 써서 merge 되면
--   이 파일 번호를 다음 번호로 바꾼다 (파일 번호가 빠지거나 겹치면 check_schema.py 0번 검사가 막는다).
-- 8월 설계의 salary_contracts(001)는 평가 번호 · 등급이 필수라 이 흐름(HR 이 금액을 올려 발송 → 직원 서명)에 맞지 않아 쓰지 않는다 — 운영에 자료가 없고 건드리지도 않는다.
--   (그래서 표 이름도 contracts 이다. 색인 이름은 001 의 idx_contracts_user 와 겹치지 않게 idx_contracts_live_user.)
-- 금액 · 이름 · 생년월일 · 서명 그림이 들어 있는 칸(body · company · sign · pdf · ask, 설정 data, 직인 png)은 서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다
--   (급여명세서 · 원천징수영수증과 같은 열쇠). 잠근 글 = 12바이트 nonce + 내용 + 16바이트 태그 → 29바이트 이상 BLOB 만 받는다.
--   문서 고유번호(doc_hash)는 잠그지 않는다 — 소문자 16진수 64자(SHA-256).
--
-- 상태 흐름 (트리거 trg_contract_live_status):  DRAFT 발송 전 → SENT 서명 대기 → SIGNED 서명 완료 → REPLACED 새 계약으로 바뀜
--   SENT · SIGNED 는 CANCELLED(파기)로도 간다. 그 밖의 짝(건너뛰기 · 되돌리기)은 거부. 상태가 그대로인 UPDATE 는 막지 않는다.
-- 지우기는 DRAFT 줄만(직원이 본 적이 없다). 발송한 뒤에는 내용 칸이, 서명한 뒤에는 서명 칸이, 끝난 줄(REPLACED · CANCELLED)에는 문의 칸이 안 바뀐다.
--   문의 닫기(ask_closed_*) · 파기 칸 · replaced_on 은 잠그지 않는다. 8월 salary_contracts 의 트리거와 같은 방식(IS NOT 으로 NULL 도 비교).
-- 한 회차 · 한 직원에 SIGNED 는 하나, DRAFT · SENT 도 하나(부분 유일 색인 둘). 새 계약(연중 변경)이 서명되는 처리 한 번 안에서는
--   먼저 옛 SIGNED 줄을 REPLACED 로 바꾸고 그다음 새 줄을 SIGNED 로 바꾼다 — 색인은 문장마다 검사하므로 순서가 거꾸로면 거부된다.

-- 직인 그림 — 번호마다 한 줄, 지우지도 바꾸지도 않는다(옛 계약서가 서명 때의 직인을 가리킨다).
CREATE TABLE contract_seals (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    png        BLOB    NOT NULL CHECK (typeof(png) = 'blob' AND length(png) > 28),   -- 잠근 직인 그림
    created_at TEXT    NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(id)
);

CREATE TRIGGER trg_contract_seals_no_delete BEFORE DELETE ON contract_seals
BEGIN
    SELECT RAISE(ABORT, '직인 그림은 지울 수 없어요. 옛 계약서가 그때 직인을 가리켜요. 새 직인은 새로 올려 주세요.');
END;

-- 바꾸는 것도 막는다 — 계약서의 seal_id 는 발송 뒤 고정이지만, 가리키는 그림이 바뀌면 고정한 보람이 없다 (001 의 trg_no_update_audit 와 같은 방식)
CREATE TRIGGER trg_contract_seals_no_update BEFORE UPDATE ON contract_seals
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 직인 그림은 바꿀 수 없어요. 새 직인은 새로 올려 주세요.');
END;

-- 계약서 설정 — 한 줄(id = 1). data = 잠근 {ceo 대표이사 이름, addr 소재지, kind 종류}. 발송할 때 계약서 줄(company · seal_id)로 복사해 고정한다.
CREATE TABLE contract_settings (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    data       BLOB    NOT NULL CHECK (typeof(data) = 'blob' AND length(data) > 28),
    seal_id    INTEGER NOT NULL REFERENCES contract_seals(id),
    updated_at TEXT    NOT NULL,
    updated_by INTEGER NOT NULL REFERENCES users(id)
);

-- 계약서 — 회차 · 직원 · 차수마다 한 줄. 직원을 지우지 못하게(RESTRICT) 한다 — 퇴직자는 지우지 않고 비활성으로 둔다.
-- year 는 일부러 형을 적지 않는다(형 없음) — 013 과 같은 이유: INT 로 적으면 글자 '2027' 을 정수로 바꿔 받아 주고,
--   INTEGER PRIMARY KEY 로 적으면 NULL 을 "다음 번호"로 몰래 채운다. 형이 없어야 NULL 도 글자도 CHECK 에서 그대로 거부된다.
-- 날짜 칸은 YYYY-MM-DD 모양이면서 실제 있는 날이어야 한다(date() 가 2월 30일을 3월 2일로 바꿔 돌려주므로 IS 로 되비교).
CREATE TABLE contracts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    year         NOT NULL CHECK (typeof(year) = 'integer' AND year BETWEEN 2000 AND 2099),   -- 회차
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    version      INTEGER NOT NULL CHECK (typeof(version) = 'integer' AND version >= 1),   -- 차수 (연중 변경 때 +1)
    prev_id      INTEGER NULL REFERENCES contracts(id),   -- 앞 차수
    status       TEXT    NOT NULL CHECK (status IN ('DRAFT', 'SENT', 'SIGNED', 'REPLACED', 'CANCELLED')),
    starts_on    TEXT    NOT NULL CHECK (starts_on GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]' AND date(starts_on) IS starts_on),   -- 계약 시작일
    body         BLOB    NOT NULL CHECK (typeof(body) = 'blob' AND length(body) > 28),   -- 잠근 내용 (사원코드 · 이름 · 소속 · 직급 · 생년월일 · 금액 · 계약 기간 · 조정 사유)
    created_at   TEXT    NOT NULL,   -- 만든(올린) 때와 사람
    created_by   INTEGER NOT NULL REFERENCES users(id),
    sent_at      TEXT    NULL,       -- 발송한 때와 사람
    sent_by      INTEGER NULL REFERENCES users(id),
    due_on       TEXT    NULL CHECK (due_on IS NULL OR (due_on GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]' AND date(due_on) IS due_on)),   -- 서명 마감일
    company      BLOB    NULL CHECK (company IS NULL OR (typeof(company) = 'blob' AND length(company) > 28)),   -- 발송 때 고정한 계약서 설정 (잠금)
    seal_id      INTEGER NULL REFERENCES contract_seals(id),   -- 발송 때 고정한 직인
    signed_at    TEXT    NULL,       -- 서명한 때
    sign         BLOB    NULL CHECK (sign IS NULL OR (typeof(sign) = 'blob' AND length(sign) > 28)),   -- 잠근 서명 (서명 그림 · 입력한 성명 · 동의 · IP · 브라우저 · 로그인 종류)
    doc_hash     TEXT    NULL CHECK (doc_hash IS NULL OR (typeof(doc_hash) = 'text' AND length(doc_hash) = 64 AND doc_hash NOT GLOB '*[^0-9a-f]*')),   -- 문서 고유번호 (SHA-256, 잠그지 않음)
    pdf          BLOB    NULL CHECK (pdf IS NULL OR (typeof(pdf) = 'blob' AND length(pdf) > 28)),   -- 잠근 서명본 PDF
    replaced_on  TEXT    NULL CHECK (replaced_on IS NULL OR (replaced_on GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]' AND date(replaced_on) IS replaced_on)),   -- 새 계약으로 바뀐 날(새 계약 시작일)
    cancelled_at TEXT    NULL,       -- 파기한 때와 사람 · 사유
    cancelled_by INTEGER NULL REFERENCES users(id),
    cancel_reason TEXT   NULL CHECK (cancel_reason IS NULL OR (typeof(cancel_reason) = 'text' AND length(cancel_reason) BETWEEN 1 AND 200)),   -- 글자 수
    ask          BLOB    NULL CHECK (ask IS NULL OR (typeof(ask) = 'blob' AND length(ask) > 28)),   -- 잠근 문의 글
    ask_at       TEXT    NULL,       -- 문의를 보낸 때
    ask_closed_at TEXT   NULL,       -- 문의를 확인한 때와 사람
    ask_closed_by INTEGER NULL REFERENCES users(id),
    -- 상태마다 채워져 있어야 하는 칸
    CONSTRAINT contracts_draft_chk CHECK (status <> 'DRAFT' OR (sent_at IS NULL AND signed_at IS NULL)),
    CONSTRAINT contracts_sent_chk CHECK (status <> 'SENT' OR (
        sent_at IS NOT NULL AND sent_by IS NOT NULL AND due_on IS NOT NULL AND company IS NOT NULL AND seal_id IS NOT NULL AND signed_at IS NULL)),
    CONSTRAINT contracts_signed_chk CHECK (status NOT IN ('SIGNED', 'REPLACED') OR (
        sent_at IS NOT NULL AND sent_by IS NOT NULL AND due_on IS NOT NULL AND company IS NOT NULL AND seal_id IS NOT NULL
        AND signed_at IS NOT NULL AND sign IS NOT NULL AND doc_hash IS NOT NULL AND pdf IS NOT NULL)),
    CONSTRAINT contracts_replaced_chk CHECK (status <> 'REPLACED' OR replaced_on IS NOT NULL),
    CONSTRAINT contracts_cancelled_chk CHECK (status <> 'CANCELLED' OR (cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL AND cancel_reason IS NOT NULL))
);
CREATE UNIQUE INDEX uq_contracts_signed ON contracts(year, user_id) WHERE status = 'SIGNED';
CREATE UNIQUE INDEX uq_contracts_open ON contracts(year, user_id) WHERE status IN ('DRAFT', 'SENT');
CREATE UNIQUE INDEX uq_contracts_version ON contracts(year, user_id, version);
CREATE INDEX idx_contracts_live_user ON contracts(user_id);

-- 지우기는 DRAFT 줄만
CREATE TRIGGER trg_contract_live_delete BEFORE DELETE ON contracts
WHEN OLD.status <> 'DRAFT'
BEGIN
    SELECT RAISE(ABORT, '발송한 계약서는 지울 수 없어요. 파기(CANCELLED)로 처리해 주세요.');
END;

-- 상태 바꾸기는 정해진 순서로만 (OR 로 짝을 하나씩 적는다)
CREATE TRIGGER trg_contract_live_status BEFORE UPDATE ON contracts
WHEN NEW.status <> OLD.status AND NOT (
       (OLD.status = 'DRAFT'  AND NEW.status = 'SENT')
    OR (OLD.status = 'SENT'   AND NEW.status = 'SIGNED')
    OR (OLD.status = 'SENT'   AND NEW.status = 'CANCELLED')
    OR (OLD.status = 'SIGNED' AND NEW.status = 'REPLACED')
    OR (OLD.status = 'SIGNED' AND NEW.status = 'CANCELLED')
)
BEGIN
    SELECT RAISE(ABORT, '계약서 상태는 정해진 순서로만 바꿀 수 있어요 (발송 전 → 서명 대기 → 서명 완료 → 새 계약으로 바뀜 · 서명 대기/서명 완료 → 파기).');
END;

-- 발송한 뒤에는 내용 칸이 안 바뀐다 (`IS NOT` — `<>` 는 NULL 에서 무력화된다)
CREATE TRIGGER trg_contract_live_frozen_sent BEFORE UPDATE ON contracts
WHEN OLD.sent_at IS NOT NULL AND (
       NEW.year       IS NOT OLD.year
    OR NEW.user_id    IS NOT OLD.user_id
    OR NEW.version    IS NOT OLD.version
    OR NEW.prev_id    IS NOT OLD.prev_id
    OR NEW.starts_on  IS NOT OLD.starts_on
    OR NEW.body       IS NOT OLD.body
    OR NEW.created_at IS NOT OLD.created_at
    OR NEW.created_by IS NOT OLD.created_by
    OR NEW.sent_at    IS NOT OLD.sent_at
    OR NEW.sent_by    IS NOT OLD.sent_by
    OR NEW.due_on     IS NOT OLD.due_on
    OR NEW.company    IS NOT OLD.company
    OR NEW.seal_id    IS NOT OLD.seal_id
)
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 발송한 계약서의 내용은 바꿀 수 없어요');
END;

-- 서명한 뒤에는 서명 칸도 안 바뀐다
CREATE TRIGGER trg_contract_live_frozen_signed BEFORE UPDATE ON contracts
WHEN OLD.signed_at IS NOT NULL AND (
       NEW.sign      IS NOT OLD.sign
    OR NEW.doc_hash  IS NOT OLD.doc_hash
    OR NEW.pdf       IS NOT OLD.pdf
    OR NEW.signed_at IS NOT OLD.signed_at
)
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 서명한 계약서의 서명 · 문서 고유번호 · 서명본은 바꿀 수 없어요');
END;

-- 끝난 줄(새 계약으로 바뀜 · 파기)은 문의 글도 안 바뀐다 (문의 닫기 칸은 그대로 바뀐다)
CREATE TRIGGER trg_contract_live_frozen_ask BEFORE UPDATE ON contracts
WHEN OLD.status IN ('REPLACED', 'CANCELLED') AND (
       NEW.ask    IS NOT OLD.ask
    OR NEW.ask_at IS NOT OLD.ask_at
)
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 끝난 계약서의 문의 글은 바꿀 수 없어요');
END;
