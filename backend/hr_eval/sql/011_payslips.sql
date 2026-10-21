-- 급여명세서를 서버에 달마다 · 사람마다 잠가서 (2026-10-20 인사담당자 — 급여명세서 실제화).
-- 서버 DB 는 009(또는 010)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).
-- HR 이 포털에서 급여대장을 읽어 올리면 사람별 숫자만 서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다 — 서버는 NAS 를 읽지 않는다.
-- 올린 달은 DRAFT(HR 만 봄), HR 이 "공개"를 눌러야 OPEN(직원 본인에게 보임). 공개한 달을 다시 올리려면 내렸다가 올린다 → revised.

-- ym 은 NOT NULL 을 따로 적는다 — SQLite 는 INTEGER 가 아닌 PRIMARY KEY 에 NULL 을 넣게 둔다.
CREATE TABLE payslip_months (
    ym          TEXT    NOT NULL PRIMARY KEY CHECK (ym GLOB '20[0-9][0-9]-[01][0-9]' AND substr(ym, 6, 2) BETWEEN '01' AND '12'),   -- 귀속 월
    pay_date    TEXT    NOT NULL CHECK (pay_date GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]' AND date(pay_date) IS pay_date),   -- 지급일 (없는 날 거부)
    status      TEXT    NOT NULL CHECK (status IN ('DRAFT', 'OPEN')),
    people      INTEGER NOT NULL CHECK (typeof(people) = 'integer' AND people >= 1),
    revised     INTEGER NOT NULL DEFAULT 0 CHECK (revised IN (0, 1)),   -- 공개했던 달을 다시 올려 다시 공개함 (직원 화면에 "수정됨")
    uploaded_at TEXT    NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    opened_at   TEXT    NULL,   -- 공개한 때 (내렸던 달에는 남아 있어도 된다)
    opened_by   INTEGER NULL REFERENCES users(id),
    CONSTRAINT payslip_open_chk CHECK (status <> 'OPEN' OR opened_at IS NOT NULL)
);

-- 사람마다 한 줄. data = 잠근 글(12바이트 nonce + 내용 + 16바이트 태그) — 서버가 풀기 전에는 금액을 알 수 없다.
-- 직원을 지우지 못하게(RESTRICT) 한다 — 퇴직자는 지우지 않고 비활성으로 둔다.
CREATE TABLE payslips (
    ym      TEXT    NOT NULL REFERENCES payslip_months(ym) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    data    BLOB    NOT NULL CHECK (typeof(data) = 'blob' AND length(data) > 28),
    seen_at TEXT    NULL,   -- 본인이 연 때 (공개된 뒤에만 채워진다). 직원 메뉴의 "새 명세서" 숫자
    PRIMARY KEY (ym, user_id)
);
CREATE INDEX idx_payslips_user ON payslips(user_id);
