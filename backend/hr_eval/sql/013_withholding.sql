-- 원천징수영수증을 서버에 해마다 · 사람마다 잠가서 (2026-10-21 인사담당자 — 원천징수영수증 실제화).
-- 서버 DB 는 012(KPI 2단계 ①)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 13.
-- (처음엔 012_withholding.sql 이었는데 KPI 2단계 ① 012_kpi_progress.sql 이 먼저 merge 되어 013 으로 바꿨다.)
-- HR 이 포털에서 해마다 올린 영수증 묶음(연말정산 PDF)을 사람별로 나눠, 요약 숫자(sum)와 PDF(pdf) 를 서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다.
-- 올린 해는 DRAFT(HR 만 봄), HR 이 "공개"를 눌러야 OPEN(직원 본인에게 보임). 공개한 해를 다시 올리려면 내렸다가 올린다 → revised.
-- pending = 아직 올리지 못한 사람 수. 공개(OPEN)는 모두 올린 뒤(pending 0)에만 가능하다.

-- year 는 일부러 형을 적지 않는다(형 없음). INTEGER PRIMARY KEY 로 적으면 SQLite 가 NULL 을 "다음 번호"로 몰래 채우고(2025 다음 2026),
-- INT 로 적으면 글자 '2026' 을 정수로 바꿔 받아 준다 — 형이 없어야 NULL 도 글자도 CHECK 에서 그대로 거부된다.
CREATE TABLE withholding_years (
    year        NOT NULL PRIMARY KEY CHECK (typeof(year) = 'integer' AND year BETWEEN 2000 AND 2099),   -- 귀속 연도
    status      TEXT    NOT NULL CHECK (status IN ('DRAFT', 'OPEN')),
    pending     INTEGER NOT NULL CHECK (typeof(pending) = 'integer' AND pending >= 0),   -- 아직 올리지 못한 사람 수
    uploaded_at TEXT    NOT NULL,
    uploaded_by INTEGER NOT NULL REFERENCES users(id),
    opened_at   TEXT    NULL,   -- 공개한 때 (내렸던 해에는 남아 있어도 된다)
    opened_by   INTEGER NULL REFERENCES users(id),
    CONSTRAINT withholding_open_chk CHECK (status <> 'OPEN' OR (opened_at IS NOT NULL AND pending = 0))
);

-- 사람마다 한 줄. sum · pdf = 잠근 글(12바이트 nonce + 내용 + 16바이트 태그) — 서버가 풀기 전에는 금액을 알 수 없다.
-- 직원을 지우지 못하게(RESTRICT) 한다 — 퇴직자는 지우지 않고 비활성으로 둔다.
CREATE TABLE withholding (
    year        INTEGER NOT NULL REFERENCES withholding_years(year) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    sum         BLOB    NOT NULL CHECK (typeof(sum) = 'blob' AND length(sum) > 28),   -- 요약 숫자
    pdf         BLOB    NOT NULL CHECK (typeof(pdf) = 'blob' AND length(pdf) > 28),   -- 영수증 PDF
    revised     INTEGER NOT NULL DEFAULT 0 CHECK (revised IN (0, 1)),   -- 공개했던 해를 다시 올려 다시 공개함 (직원 화면에 "수정됨")
    uploaded_at TEXT    NOT NULL,
    seen_at     TEXT    NULL,   -- 본인이 연 때 (공개된 뒤에만 채워진다). 직원 메뉴의 "새 영수증" 숫자
    PRIMARY KEY (year, user_id)
);
CREATE INDEX idx_withholding_user ON withholding(user_id);
