-- hr_eval 스키마 초기화 (SQLite)
--
-- 설계 근거: docs/superpowers/specs/2026-08-24-hr-evaluation-contract-design.md
--            docs/superpowers/specs/2026-08-24-sqlite-auth-design.md §5 (전환 규칙)
-- 적용:      python -m hr_eval.sql.check_schema   (CI 가 실행)
--            또는 sqlite3 <파일> < 001_init.sql
--
-- 이 파일에는 실제 직원 정보도, 연봉 인상률 수치도 들어가지 않는다.
-- hr-portal은 Public 저장소이고, 그런 데이터는 서버 DB에만 존재한다.
--
-- ------------------------------------------------------------
-- PostgreSQL -> SQLite 전환 규칙 (설계 문서 §5)
-- ------------------------------------------------------------
--   CREATE TYPE ... AS ENUM      -> TEXT + CHECK (col IN (...))
--   BIGINT GENERATED ... IDENTITY-> INTEGER PRIMARY KEY AUTOINCREMENT
--   TIMESTAMPTZ                  -> TEXT, ISO 8601 UTC ('2027-01-01T09:00:00Z')
--   DATE                         -> TEXT, 'YYYY-MM-DD'
--   BOOLEAN                      -> INTEGER 0/1 + CHECK (col IN (0,1))
--   NUMERIC(14,0) 연봉           -> INTEGER (원 단위, 정확)
--   NUMERIC(n,2)  점수/가중치/인상률 -> INTEGER 100배 저장, 칼럼명에 _x100
--   JSONB                        -> TEXT (JSON 문자열)
--   BYTEA                        -> BLOB
--   INET                         -> TEXT
--   plpgsql 트리거 함수          -> SQLite 트리거 + RAISE(ABORT, ...)
--
-- 100배 정수를 쓰는 이유: REAL 은 부동소수점이라 소수 2자리 가중치들의 합이
-- 정확히 100.00 이 되지 않는 경우가 있다 (예: 7.64+83.57+8.79 = 99.99999999999999).
-- 근거 재현: backend/hr_eval/sql/check_sqlite_features.py
--
-- ⚠ 외래키는 연결마다 꺼진 채로 시작하며, 트랜잭션 안에서 켜면 조용히 무시된다.
--    연결을 만드는 함수에서 어떤 쿼리보다도 먼저 PRAGMA foreign_keys = ON 을 걸 것.

PRAGMA foreign_keys = ON;

-- ============================================================
-- §3.2 조직·사용자
-- ============================================================
-- departments.leader_user_id 는 users 를 참조한다 (순환 참조).
-- SQLite 는 외래키 대상 테이블을 DML 시점에 확인하므로, 아직 만들어지지
-- 않은 users 를 여기서 참조해도 된다 (PostgreSQL 처럼 ALTER 로 나눌 필요 없음).
CREATE TABLE departments (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    code           TEXT    NOT NULL UNIQUE,
    name           TEXT    NOT NULL,
    level          TEXT    NOT NULL CHECK (level IN ('DIVISION','TEAM')),
    parent_id      INTEGER NULL REFERENCES departments(id) ON DELETE RESTRICT,
    leader_user_id INTEGER NULL REFERENCES users(id)       ON DELETE SET NULL,
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by     INTEGER NULL,
    CONSTRAINT departments_hierarchy_chk CHECK (
        (level = 'DIVISION' AND parent_id IS NULL) OR
        (level = 'TEAM'     AND parent_id IS NOT NULL)
    )
);
CREATE INDEX idx_departments_parent ON departments(parent_id);

CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_no   TEXT    NOT NULL UNIQUE,
    name          TEXT    NOT NULL,
    email         TEXT    NOT NULL UNIQUE,
    role          TEXT    NOT NULL DEFAULT 'EMPLOYEE'
                  CHECK (role IN ('EMPLOYEE','TEAM_LEADER','DIVISION_HEAD','HR_ADMIN')),
    department_id INTEGER NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
    hire_date     TEXT    NOT NULL,
    is_active     INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by    INTEGER NULL REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX idx_users_department ON users(department_id) WHERE is_active = 1;
CREATE INDEX idx_users_role       ON users(role);

-- ============================================================
-- §3.3 평가기간·쿼터
-- ============================================================
CREATE TABLE evaluation_periods (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    year                 INTEGER NOT NULL,
    type                 TEXT    NOT NULL CHECK (type IN ('ANNUAL','MIDTERM')),
    name                 TEXT    NOT NULL,
    starts_on            TEXT    NOT NULL,
    ends_on              TEXT    NOT NULL,
    status               TEXT    NOT NULL DEFAULT 'PREPARING'
                         CHECK (status IN ('PREPARING','KPI_OPEN','EVALUATING','CLOSED')),
    is_kpi_window_open   INTEGER NOT NULL DEFAULT 0 CHECK (is_kpi_window_open IN (0,1)),
    kpi_window_opened_at TEXT    NULL,
    kpi_window_closed_at TEXT    NULL,
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by           INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT periods_range_chk CHECK (ends_on >= starts_on),
    CONSTRAINT periods_unique    UNIQUE (year, type, name)
);

CREATE TABLE department_quotas (
    id                INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    period_id         INTEGER NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    department_id     INTEGER NOT NULL REFERENCES departments(id)        ON DELETE RESTRICT,
    headcount         INTEGER NOT NULL CHECK (headcount >= 0),
    quota_mode        TEXT    NOT NULL CHECK (quota_mode IN ('INDIVIDUAL','GROUPED')),
    -- INDIVIDUAL 모드 (N >= 5) 에서만 채운다
    quota_a           INTEGER NULL CHECK (quota_a >= 0),
    quota_b           INTEGER NULL CHECK (quota_b >= 0),
    quota_c           INTEGER NULL CHECK (quota_c >= 0),
    quota_d           INTEGER NULL CHECK (quota_d >= 0),
    -- GROUPED 모드 (N <= 4) 에서만 채운다
    quota_upper       INTEGER NULL CHECK (quota_upper >= 0),   -- A+B 합계 상한
    quota_lower       INTEGER NULL CHECK (quota_lower >= 0),   -- C+D 합계 (잔여 흡수)
    cap_s             INTEGER NOT NULL CHECK (cap_s >= 0),
    submission_status TEXT    NOT NULL DEFAULT 'NOT_SUBMITTED'
                      CHECK (submission_status IN ('NOT_SUBMITTED','SUBMITTED','CONFIRMED')),
    submitted_at      TEXT    NULL,
    submitted_by      INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    adjusted_by       INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    adjust_reason     TEXT    NULL,
    computed_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at        TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by        INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT quotas_unique     UNIQUE (period_id, department_id),
    CONSTRAINT quotas_adjust_chk CHECK (adjusted_by IS NULL OR adjust_reason IS NOT NULL),
    CONSTRAINT quotas_individual_chk CHECK (
        quota_mode <> 'INDIVIDUAL' OR (
            quota_a IS NOT NULL AND quota_b IS NOT NULL
            AND quota_c IS NOT NULL AND quota_d IS NOT NULL
            AND quota_upper IS NULL AND quota_lower IS NULL
            AND quota_a + quota_b + quota_c + quota_d = headcount
            AND cap_s <= quota_a
        )
    ),
    CONSTRAINT quotas_grouped_chk CHECK (
        quota_mode <> 'GROUPED' OR (
            quota_upper IS NOT NULL AND quota_lower IS NOT NULL
            AND quota_a IS NULL AND quota_b IS NULL
            AND quota_c IS NULL AND quota_d IS NULL
            AND quota_upper + quota_lower = headcount
            AND cap_s <= quota_upper
        )
    )
);

-- ============================================================
-- §3.4 KPI
-- ============================================================
CREATE TABLE kpi_sheets (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    period_id                 INTEGER NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    user_id                   INTEGER NOT NULL REFERENCES users(id)              ON DELETE RESTRICT,
    status                    TEXT    NOT NULL DEFAULT 'DRAFT'
                              CHECK (status IN ('DRAFT','TEAM_LEADER_APPROVED','DIVISION_HEAD_APPROVED')),
    submitted_at              TEXT    NULL,
    team_leader_approved_at   TEXT    NULL,
    team_leader_approved_by   INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    division_head_approved_at TEXT    NULL,
    division_head_approved_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    locked_at                 TEXT    NULL,
    created_at                TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at                TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by                INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT kpi_sheets_unique UNIQUE (period_id, user_id),
    CONSTRAINT kpi_sheets_tl_chk CHECK (
        status = 'DRAFT'
        OR (team_leader_approved_at IS NOT NULL AND team_leader_approved_by IS NOT NULL)
    ),
    CONSTRAINT kpi_sheets_dh_chk CHECK (
        status <> 'DIVISION_HEAD_APPROVED'
        OR (division_head_approved_at IS NOT NULL AND division_head_approved_by IS NOT NULL
            AND locked_at IS NOT NULL)
    )
);

CREATE TABLE kpis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id        INTEGER NOT NULL REFERENCES kpi_sheets(id) ON DELETE CASCADE,
    title           TEXT    NOT NULL,
    description     TEXT    NULL,
    target          TEXT    NULL,
    -- 가중치는 100배 정수. 0 초과 10000(=100.00%) 이하.
    weight_pct_x100 INTEGER NOT NULL CHECK (weight_pct_x100 > 0 AND weight_pct_x100 <= 10000),
    display_order   INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by      INTEGER NULL REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX idx_kpis_sheet ON kpis(sheet_id);

CREATE TABLE kpi_change_requests (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    sheet_id                  INTEGER NOT NULL REFERENCES kpi_sheets(id) ON DELETE CASCADE,
    requested_by              INTEGER NOT NULL REFERENCES users(id)      ON DELETE RESTRICT,
    reason                    TEXT    NOT NULL,
    proposed_kpis             TEXT    NOT NULL,   -- JSON 문자열
    status                    TEXT    NOT NULL DEFAULT 'PENDING'
                              CHECK (status IN ('PENDING','TEAM_LEADER_APPROVED','APPROVED','REJECTED','WITHDRAWN')),
    team_leader_approved_at   TEXT    NULL,
    team_leader_approved_by   INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    division_head_approved_at TEXT    NULL,
    division_head_approved_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    rejected_at               TEXT    NULL,
    rejected_by               INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    reject_reason             TEXT    NULL,
    applied_at                TEXT    NULL,
    created_at                TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at                TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    CONSTRAINT kcr_reject_chk CHECK (status <> 'REJECTED' OR reject_reason IS NOT NULL),
    CONSTRAINT kcr_apply_chk  CHECK (status <> 'APPROVED' OR applied_at IS NOT NULL),
    CONSTRAINT kcr_json_chk   CHECK (json_valid(proposed_kpis))
);
CREATE INDEX idx_kcr_sheet_status ON kpi_change_requests(sheet_id, status);
-- 한 시트에 미결 수정 요청은 1건만
CREATE UNIQUE INDEX uq_kcr_one_open ON kpi_change_requests(sheet_id)
    WHERE status IN ('PENDING','TEAM_LEADER_APPROVED');

-- ============================================================
-- §3.5 평가
-- ============================================================
-- 점수는 전부 100배 정수. 100.00점 = 10000, 110.00점 = 11000.
CREATE TABLE evaluations (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    period_id        INTEGER NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    user_id          INTEGER NOT NULL REFERENCES users(id)              ON DELETE RESTRICT,
    division_id      INTEGER NOT NULL REFERENCES departments(id)        ON DELETE RESTRICT,
    kpi_score_x100   INTEGER NOT NULL DEFAULT 0 CHECK (kpi_score_x100 BETWEEN 0 AND 10000),
    bonus_score_x100 INTEGER NOT NULL DEFAULT 0 CHECK (bonus_score_x100 >= 0),
    bonus_reason     TEXT    NULL,
    total_score_x100 INTEGER GENERATED ALWAYS AS (kpi_score_x100 + bonus_score_x100) STORED,
    grade            TEXT    NULL CHECK (grade IS NULL OR grade IN ('S','A','B','C','D')),
    evaluator_id     INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    status           TEXT    NOT NULL DEFAULT 'DRAFT'
                     CHECK (status IN ('DRAFT','SUBMITTED','CONFIRMED')),
    submitted_at     TEXT    NULL,
    confirmed_at     TEXT    NULL,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by       INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT evaluations_unique    UNIQUE (period_id, user_id),
    CONSTRAINT eval_total_max_chk    CHECK (kpi_score_x100 + bonus_score_x100 <= 11000),
    CONSTRAINT eval_s_grade_chk      CHECK (grade <> 'S' OR kpi_score_x100 + bonus_score_x100 > 10000),
    CONSTRAINT eval_bonus_reason_chk CHECK (bonus_score_x100 = 0 OR bonus_reason IS NOT NULL),
    CONSTRAINT eval_confirmed_chk    CHECK (status <> 'CONFIRMED' OR grade IS NOT NULL)
);
CREATE INDEX idx_evaluations_period_div ON evaluations(period_id, division_id);
CREATE INDEX idx_evaluations_user       ON evaluations(user_id);

-- ============================================================
-- §3.6 연봉계약
-- ============================================================
CREATE TABLE salary_raise_rates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    period_id      INTEGER NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    grade          TEXT    NOT NULL CHECK (grade IN ('S','A','B','C','D')),
    -- 인상률도 100배 정수. -100.00% = -10000, 100.00% = 10000.
    raise_pct_x100 INTEGER NOT NULL CHECK (raise_pct_x100 BETWEEN -10000 AND 10000),
    created_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by     INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT raise_rates_unique UNIQUE (period_id, grade)
);

CREATE TABLE salary_contracts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    period_id          INTEGER NOT NULL REFERENCES evaluation_periods(id) ON DELETE RESTRICT,
    user_id            INTEGER NOT NULL REFERENCES users(id)              ON DELETE RESTRICT,
    evaluation_id      INTEGER NOT NULL REFERENCES evaluations(id)        ON DELETE RESTRICT,
    grade              TEXT    NOT NULL CHECK (grade IN ('S','A','B','C','D')),
    -- 연봉은 원 단위 정수라 그대로 정확하다 (100배 하지 않는다)
    base_salary_before INTEGER NOT NULL CHECK (base_salary_before >= 0),
    raise_pct_x100     INTEGER NOT NULL,
    base_salary_after  INTEGER NOT NULL CHECK (base_salary_after >= 0),
    contract_starts_on TEXT    NOT NULL,
    contract_ends_on   TEXT    NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    resent_from_id     INTEGER NULL REFERENCES salary_contracts(id) ON DELETE SET NULL,
    status             TEXT    NOT NULL DEFAULT 'DRAFT'
                       CHECK (status IN ('DRAFT','SENT','SIGNED','CANCELLED')),
    is_locked          INTEGER NOT NULL DEFAULT 0 CHECK (is_locked IN (0,1)),
    sent_at            TEXT    NULL,
    -- 전자서명
    consent_checked    INTEGER NOT NULL DEFAULT 0 CHECK (consent_checked IN (0,1)),
    signer_user_id     INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    signer_name        TEXT    NULL,
    signer_ip          TEXT    NULL,
    signer_user_agent  TEXT    NULL,
    signature_image    BLOB    NULL,
    document_hash      TEXT    NULL,   -- 서명 시점 계약 본문 SHA-256
    signed_at          TEXT    NULL,
    -- PDF
    pdf_status         TEXT    NOT NULL DEFAULT 'NONE'
                       CHECK (pdf_status IN ('NONE','PENDING','GENERATED','FAILED')),
    pdf_path           TEXT    NULL,
    pdf_generated_at   TEXT    NULL,
    -- 파기
    cancelled_at       TEXT    NULL,
    cancelled_by       INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    cancel_reason      TEXT    NULL,
    created_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at         TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by         INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT contract_range_chk CHECK (contract_ends_on > contract_starts_on),
    CONSTRAINT contract_signed_chk CHECK (
        status <> 'SIGNED' OR (
            consent_checked = 1
            AND signer_user_id  IS NOT NULL
            AND signer_name     IS NOT NULL
            AND signer_ip       IS NOT NULL
            AND signature_image IS NOT NULL
            AND document_hash   IS NOT NULL
            AND signed_at       IS NOT NULL
            AND is_locked = 1
        )
    ),
    CONSTRAINT contract_cancel_chk CHECK (
        status <> 'CANCELLED' OR (
            cancelled_at IS NOT NULL AND cancelled_by IS NOT NULL AND cancel_reason IS NOT NULL
        )
    )
);
-- 한 사람 한 기간에 살아있는 계약은 1건
CREATE UNIQUE INDEX uq_contract_active ON salary_contracts(period_id, user_id)
    WHERE status <> 'CANCELLED';
CREATE INDEX idx_contracts_user   ON salary_contracts(user_id);
CREATE INDEX idx_contracts_status ON salary_contracts(period_id, status);

-- ============================================================
-- §3.7 감사 로그
-- ============================================================
CREATE TABLE audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    actor_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
    actor_role    TEXT    NULL
                  CHECK (actor_role IS NULL
                         OR actor_role IN ('EMPLOYEE','TEAM_LEADER','DIVISION_HEAD','HR_ADMIN')),
    action        TEXT    NOT NULL,   -- 'CONTRACT_SIGNED', 'CONTRACT_CANCELLED', ...
    entity_type   TEXT    NOT NULL,   -- 'salary_contract', 'kpi_sheet', ...
    entity_id     INTEGER NULL,
    before_data   TEXT    NULL,       -- JSON 문자열
    after_data    TEXT    NULL,       -- JSON 문자열
    reason        TEXT    NULL,
    ip            TEXT    NULL,
    user_agent    TEXT    NULL,
    CONSTRAINT audit_json_chk CHECK (
        (before_data IS NULL OR json_valid(before_data))
        AND (after_data IS NULL OR json_valid(after_data))
    )
);
CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id, occurred_at DESC);
CREATE INDEX idx_audit_actor  ON audit_logs(actor_user_id, occurred_at DESC);

-- ============================================================
-- §3.8 불변성 트리거
-- ============================================================
-- SQLite 에는 plpgsql 이 없다. 같은 보증을 RAISE(ABORT, ...) 로 만든다.
-- 재귀 트리거는 기본으로 꺼져 있으므로 updated_at 갱신이 자기 자신을
-- 다시 호출하지 않는다. 그래도 방어적으로 WHEN 조건을 둔다.

-- (1) updated_at 자동 갱신
CREATE TRIGGER trg_departments_updated_at AFTER UPDATE ON departments
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE departments SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_users_updated_at AFTER UPDATE ON users
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE users SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_periods_updated_at AFTER UPDATE ON evaluation_periods
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE evaluation_periods SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_quotas_updated_at AFTER UPDATE ON department_quotas
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE department_quotas SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_kpi_sheets_updated_at AFTER UPDATE ON kpi_sheets
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE kpi_sheets SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_kpis_updated_at AFTER UPDATE ON kpis
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE kpis SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_kcr_updated_at AFTER UPDATE ON kpi_change_requests
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE kpi_change_requests SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_evaluations_updated_at AFTER UPDATE ON evaluations
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE evaluations SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_raise_rates_updated_at AFTER UPDATE ON salary_raise_rates
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE salary_raise_rates SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

CREATE TRIGGER trg_contracts_updated_at AFTER UPDATE ON salary_contracts
WHEN NEW.updated_at = OLD.updated_at
BEGIN
    UPDATE salary_contracts SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ','now') WHERE id = NEW.id;
END;

-- (2) 서명 완료 계약서 READ_ONLY
--     잠금 후 바뀔 수 있는 칼럼: status(→CANCELLED), cancelled_*, pdf_*, updated_at
--     그 밖의 칼럼이 하나라도 바뀌면 거부한다.
--     NULL 비교가 필요하므로 `IS NOT` 를 쓴다 (`<>` 는 NULL 에서 무력화된다).
CREATE TRIGGER trg_contract_lock_frozen
BEFORE UPDATE ON salary_contracts
WHEN OLD.is_locked = 1 AND (
       NEW.period_id          IS NOT OLD.period_id
    OR NEW.user_id            IS NOT OLD.user_id
    OR NEW.evaluation_id      IS NOT OLD.evaluation_id
    OR NEW.grade              IS NOT OLD.grade
    OR NEW.base_salary_before IS NOT OLD.base_salary_before
    OR NEW.raise_pct_x100     IS NOT OLD.raise_pct_x100
    OR NEW.base_salary_after  IS NOT OLD.base_salary_after
    OR NEW.contract_starts_on IS NOT OLD.contract_starts_on
    OR NEW.contract_ends_on   IS NOT OLD.contract_ends_on
    OR NEW.version            IS NOT OLD.version
    OR NEW.resent_from_id     IS NOT OLD.resent_from_id
    OR NEW.is_locked          IS NOT OLD.is_locked
    OR NEW.sent_at            IS NOT OLD.sent_at
    OR NEW.consent_checked    IS NOT OLD.consent_checked
    OR NEW.signer_user_id     IS NOT OLD.signer_user_id
    OR NEW.signer_name        IS NOT OLD.signer_name
    OR NEW.signer_ip          IS NOT OLD.signer_ip
    OR NEW.signer_user_agent  IS NOT OLD.signer_user_agent
    OR NEW.signature_image    IS NOT OLD.signature_image
    OR NEW.document_hash      IS NOT OLD.document_hash
    OR NEW.signed_at          IS NOT OLD.signed_at
    OR NEW.created_at         IS NOT OLD.created_at
    OR NEW.created_by         IS NOT OLD.created_by
)
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 서명 완료된 계약서는 수정할 수 없습니다');
END;

--     상태는 파기(CANCELLED)로만 바꿀 수 있다.
CREATE TRIGGER trg_contract_lock_status
BEFORE UPDATE ON salary_contracts
WHEN OLD.is_locked = 1
 AND NEW.status <> 'CANCELLED'
 AND NEW.status <> OLD.status
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 서명 완료된 계약서는 파기 외 상태로 바꿀 수 없습니다');
END;

-- (3) 계약서·감사로그 삭제 금지 (감사 추적 보존)
CREATE TRIGGER trg_no_delete_contracts BEFORE DELETE ON salary_contracts
BEGIN
    SELECT RAISE(ABORT, 'salary_contracts 행은 삭제할 수 없습니다. 계약서는 파기(CANCELLED)로 처리하십시오.');
END;

CREATE TRIGGER trg_no_delete_audit BEFORE DELETE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs 행은 삭제할 수 없습니다.');
END;

CREATE TRIGGER trg_no_update_audit BEFORE UPDATE ON audit_logs
BEGIN
    SELECT RAISE(ABORT, 'audit_logs 행은 수정할 수 없습니다.');
END;
