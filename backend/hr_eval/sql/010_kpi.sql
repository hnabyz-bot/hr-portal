-- 평가·성과 메뉴의 KPI 계획서를 가상 자료에서 서버 저장으로 옮긴다 (2026-10-20 인사담당자 결정):
-- 직원이 올해 KPI 계획서를 작성 → 제출 → 바로 위 관리자가 확정(또는 돌려보내기·거절) → 확정 뒤 수정 요청 → 관리자가 풀어 주면 다시 작성.
-- 작성 기간은 해마다 인사담당자가 정하고, 한 사람만 따로 열어 줄 수도 있다. 확정한 계획서는 판 번호를 붙여 쌓고 고치지 않는다.
-- 서버 DB 는 009 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).
-- 001 의 kpi_sheets · kpis · kpi_change_requests 는 건드리지 않고 쓰지 않는다 (운영에 자료가 없다).

-- 맡은 부서. NULL = 본인 소속 부서 그대로.
-- 하이웍스에서 본부장이 '임원' 부서에 들어가도 맡은 본부를 따로 둔다 — 명단 동기화는 소속 부서만 바꾸고 이 칸은 건드리지 않는다.
-- 부서가 없어지면(삭제) 다시 본인 소속으로 돌아간다.
ALTER TABLE users ADD COLUMN led_department_id INTEGER NULL REFERENCES departments(id) ON DELETE SET NULL;

-- 해마다 KPI 작성 기간 — 인사담당자가 정한다. 기간을 아직 안 정했으면 edit_from · edit_to 둘 다 NULL.
-- eval_start: 그해 평가를 시작하는 날 (NULL = 미정).
CREATE TABLE kpi_years (
    year        INTEGER PRIMARY KEY CHECK (year BETWEEN 2026 AND 2100),
    edit_from   TEXT    NULL,
    edit_to     TEXT    NULL,
    eval_start  TEXT    NULL,
    updated_at  TEXT    NOT NULL,
    updated_by  INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    CHECK ((edit_from IS NULL) = (edit_to IS NULL)),
    CHECK (edit_from IS NULL OR (date(edit_from) IS edit_from AND date(edit_to) IS edit_to AND edit_to >= edit_from))
);

-- 한 사람만 따로 연 작성 기간 — 기간이 지났거나 아직 안 열렸을 때 인사담당자가 그 사람에게만 열어 준다.
CREATE TABLE kpi_windows (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    year        INTEGER NOT NULL,
    edit_from   TEXT    NOT NULL,
    edit_to     TEXT    NOT NULL,
    set_by      INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    set_at      TEXT    NOT NULL,
    PRIMARY KEY (user_id, year)
);

-- 계획서 — 한 사람 한 해에 한 줄. draft 는 지금 작성 중인 목록(JSON 목록), 확정되면 확정본과 같은 내용이 된다.
-- status: DRAFT 작성 중 · SUBMITTED 제출함 · CONFIRMED 확정 · CHANGE_REQUESTED 확정 뒤 수정 요청.
-- confirmed_version: 마지막으로 확정한 판 (kpi_versions.version).
-- note · note_kind: 관리자가 돌려보내거나(RETURNED) 거절한(REJECTED) 사유 — 둘은 함께 있거나 함께 없다.
-- change_reason: 확정 뒤 수정을 요청한 사유.
-- 직원을 지우지 않는다 (비활성으로 처리) — 그래서 RESTRICT.
CREATE TABLE kpi_plans (
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    year               INTEGER NOT NULL,
    status             TEXT    NOT NULL CHECK (status IN ('DRAFT','SUBMITTED','CONFIRMED','CHANGE_REQUESTED')),
    draft              TEXT    NOT NULL CHECK (json_valid(draft) AND json_type(draft) = 'array'),
    confirmed_version  INTEGER NULL CHECK (confirmed_version >= 1),
    note               TEXT    NULL,
    note_kind          TEXT    NULL CHECK (note_kind IN ('RETURNED','REJECTED')),
    change_reason      TEXT    NULL,
    submitted_at       TEXT    NULL,
    updated_at         TEXT    NOT NULL,
    PRIMARY KEY (user_id, year),
    CHECK (status NOT IN ('CONFIRMED','CHANGE_REQUESTED') OR confirmed_version IS NOT NULL),
    CHECK (status <> 'CHANGE_REQUESTED' OR change_reason IS NOT NULL),
    CHECK ((note IS NULL) = (note_kind IS NULL))
);

-- 확정본 — 확정할 때마다 판 번호(1, 2, …)를 붙여 쌓는다. 고치거나 지울 수 없다 (감사 추적 보존).
-- confirmed_by: 확정한 관리자. on_behalf_of: 인사담당자가 대신 확정했으면 원래 확정해야 할 관리자.
CREATE TABLE kpi_versions (
    user_id       INTEGER NOT NULL,
    year          INTEGER NOT NULL,
    version       INTEGER NOT NULL CHECK (version >= 1),
    content       TEXT    NOT NULL CHECK (json_valid(content) AND json_type(content) = 'array'),
    confirmed_at  TEXT    NOT NULL,
    confirmed_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    on_behalf_of  INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    PRIMARY KEY (user_id, year, version),
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT
);

CREATE TRIGGER trg_kpi_versions_frozen BEFORE UPDATE ON kpi_versions
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 확정본은 고칠 수 없습니다. 바꾸려면 수정 요청 뒤 새 판으로 확정하십시오.');
END;

CREATE TRIGGER trg_kpi_versions_no_delete BEFORE DELETE ON kpi_versions
BEGIN
    SELECT RAISE(ABORT, 'kpi_versions 행(확정본)은 삭제할 수 없습니다.');
END;

-- 대표이사의 "HR에 맡기기" — 줄이 있으면 켜진 것. 대표이사만 켜고 끈다 (HR 이 스스로 켤 수 없다).
-- 켜져 있는 동안 인사담당자가 대표이사 몫의 확정을 대신할 수 있다 (대신하면 on_behalf_of 에 대표이사가 남는다).
CREATE TABLE ceo_delegation (
    ceo_user_id  INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    since        TEXT    NOT NULL
);
