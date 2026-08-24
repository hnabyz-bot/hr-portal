-- hr_eval 스키마 초기화
--
-- 설계 근거: docs/superpowers/specs/2026-08-24-hr-evaluation-contract-design.md
-- 적용:      psql -d <DB> -v ON_ERROR_STOP=1 -f 001_init.sql
--
-- 이 파일에는 실제 직원 정보도, 연봉 인상률 수치도 들어가지 않는다.
-- hr-portal은 Public 저장소이고, 그런 데이터는 서버 DB에만 존재한다.

-- ============================================================
-- §3.1 ENUM
-- ============================================================
CREATE TYPE user_role             AS ENUM ('EMPLOYEE','TEAM_LEADER','DIVISION_HEAD','HR_ADMIN');
CREATE TYPE department_level      AS ENUM ('DIVISION','TEAM');
CREATE TYPE period_type           AS ENUM ('ANNUAL','MIDTERM');
CREATE TYPE period_status         AS ENUM ('PREPARING','KPI_OPEN','EVALUATING','CLOSED');
CREATE TYPE kpi_sheet_status      AS ENUM ('DRAFT','TEAM_LEADER_APPROVED','DIVISION_HEAD_APPROVED');
CREATE TYPE change_request_status AS ENUM ('PENDING','TEAM_LEADER_APPROVED','APPROVED','REJECTED','WITHDRAWN');
CREATE TYPE quota_mode            AS ENUM ('INDIVIDUAL','GROUPED');
CREATE TYPE grade                 AS ENUM ('S','A','B','C','D');
CREATE TYPE evaluation_status     AS ENUM ('DRAFT','SUBMITTED','CONFIRMED');
CREATE TYPE submission_status     AS ENUM ('NOT_SUBMITTED','SUBMITTED','CONFIRMED');
CREATE TYPE contract_status       AS ENUM ('DRAFT','SENT','SIGNED','CANCELLED');
CREATE TYPE pdf_status            AS ENUM ('NONE','PENDING','GENERATED','FAILED');

-- ============================================================
-- §3.2 조직·사용자
-- ============================================================
CREATE TABLE departments (
    id             BIGINT           GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code           TEXT             NOT NULL UNIQUE,
    name           TEXT             NOT NULL,
    level          department_level NOT NULL,
    parent_id      BIGINT           NULL REFERENCES departments(id) ON DELETE RESTRICT,
    leader_user_id BIGINT           NULL,   -- FK는 users 생성 후 ALTER (순환 참조)
    is_active      BOOLEAN          NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ      NOT NULL DEFAULT now(),
    created_by     BIGINT           NULL,
    CONSTRAINT departments_hierarchy_chk CHECK (
        (level = 'DIVISION' AND parent_id IS NULL) OR
        (level = 'TEAM'     AND parent_id IS NOT NULL)
    )
);
CREATE INDEX idx_departments_parent ON departments(parent_id);

CREATE TABLE users (
    id            BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_no   TEXT        NOT NULL UNIQUE,
    name          TEXT        NOT NULL,
    email         TEXT        NOT NULL UNIQUE,
    role          user_role   NOT NULL DEFAULT 'EMPLOYEE',
    department_id BIGINT      NOT NULL REFERENCES departments(id) ON DELETE RESTRICT,
    hire_date     DATE        NOT NULL,
    is_active     BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    BIGINT      NULL REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX idx_users_department ON users(department_id) WHERE is_active;
CREATE INDEX idx_users_role       ON users(role);

ALTER TABLE departments
    ADD CONSTRAINT fk_departments_leader
    FOREIGN KEY (leader_user_id) REFERENCES users(id) ON DELETE SET NULL;

-- ============================================================
-- §3.3 평가기간·쿼터
-- ============================================================
CREATE TABLE evaluation_periods (
    id                   BIGINT        GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    year                 SMALLINT      NOT NULL,
    type                 period_type   NOT NULL,
    name                 TEXT          NOT NULL,
    starts_on            DATE          NOT NULL,
    ends_on              DATE          NOT NULL,
    status               period_status NOT NULL DEFAULT 'PREPARING',
    is_kpi_window_open   BOOLEAN       NOT NULL DEFAULT FALSE,
    kpi_window_opened_at TIMESTAMPTZ   NULL,
    kpi_window_closed_at TIMESTAMPTZ   NULL,
    created_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ   NOT NULL DEFAULT now(),
    created_by           BIGINT        NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT periods_range_chk CHECK (ends_on >= starts_on),
    CONSTRAINT periods_unique    UNIQUE (year, type, name)
);

CREATE TABLE department_quotas (
    id                BIGINT            GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id         BIGINT            NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    department_id     BIGINT            NOT NULL REFERENCES departments(id)        ON DELETE RESTRICT,
    headcount         INTEGER           NOT NULL CHECK (headcount >= 0),
    quota_mode        quota_mode        NOT NULL,
    -- INDIVIDUAL 모드 (N >= 5) 에서만 채운다
    quota_a           INTEGER           NULL CHECK (quota_a >= 0),
    quota_b           INTEGER           NULL CHECK (quota_b >= 0),
    quota_c           INTEGER           NULL CHECK (quota_c >= 0),
    quota_d           INTEGER           NULL CHECK (quota_d >= 0),
    -- GROUPED 모드 (N <= 4) 에서만 채운다
    quota_upper       INTEGER           NULL CHECK (quota_upper >= 0),   -- A+B 합계 상한
    quota_lower       INTEGER           NULL CHECK (quota_lower >= 0),   -- C+D 합계 (잔여 흡수)
    cap_s             INTEGER           NOT NULL CHECK (cap_s >= 0),
    submission_status submission_status NOT NULL DEFAULT 'NOT_SUBMITTED',
    submitted_at      TIMESTAMPTZ       NULL,
    submitted_by      BIGINT            NULL REFERENCES users(id) ON DELETE SET NULL,
    adjusted_by       BIGINT            NULL REFERENCES users(id) ON DELETE SET NULL,
    adjust_reason     TEXT              NULL,
    computed_at       TIMESTAMPTZ       NOT NULL DEFAULT now(),
    created_at        TIMESTAMPTZ       NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ       NOT NULL DEFAULT now(),
    created_by        BIGINT            NULL REFERENCES users(id) ON DELETE SET NULL,
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
    id                        BIGINT           GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id                 BIGINT           NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    user_id                   BIGINT           NOT NULL REFERENCES users(id)              ON DELETE RESTRICT,
    status                    kpi_sheet_status NOT NULL DEFAULT 'DRAFT',
    submitted_at              TIMESTAMPTZ      NULL,
    team_leader_approved_at   TIMESTAMPTZ      NULL,
    team_leader_approved_by   BIGINT           NULL REFERENCES users(id) ON DELETE SET NULL,
    division_head_approved_at TIMESTAMPTZ      NULL,
    division_head_approved_by BIGINT           NULL REFERENCES users(id) ON DELETE SET NULL,
    locked_at                 TIMESTAMPTZ      NULL,
    created_at                TIMESTAMPTZ      NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ      NOT NULL DEFAULT now(),
    created_by                BIGINT           NULL REFERENCES users(id) ON DELETE SET NULL,
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
    id            BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_id      BIGINT       NOT NULL REFERENCES kpi_sheets(id) ON DELETE CASCADE,
    title         TEXT         NOT NULL,
    description   TEXT         NULL,
    target        TEXT         NULL,
    weight_pct    NUMERIC(5,2) NOT NULL CHECK (weight_pct > 0 AND weight_pct <= 100),
    display_order SMALLINT     NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by    BIGINT       NULL REFERENCES users(id) ON DELETE SET NULL
);
CREATE INDEX idx_kpis_sheet ON kpis(sheet_id);

CREATE TABLE kpi_change_requests (
    id                        BIGINT                GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sheet_id                  BIGINT                NOT NULL REFERENCES kpi_sheets(id) ON DELETE CASCADE,
    requested_by              BIGINT                NOT NULL REFERENCES users(id)      ON DELETE RESTRICT,
    reason                    TEXT                  NOT NULL,
    proposed_kpis             JSONB                 NOT NULL,
    status                    change_request_status NOT NULL DEFAULT 'PENDING',
    team_leader_approved_at   TIMESTAMPTZ           NULL,
    team_leader_approved_by   BIGINT                NULL REFERENCES users(id) ON DELETE SET NULL,
    division_head_approved_at TIMESTAMPTZ           NULL,
    division_head_approved_by BIGINT                NULL REFERENCES users(id) ON DELETE SET NULL,
    rejected_at               TIMESTAMPTZ           NULL,
    rejected_by               BIGINT                NULL REFERENCES users(id) ON DELETE SET NULL,
    reject_reason             TEXT                  NULL,
    applied_at                TIMESTAMPTZ           NULL,
    created_at                TIMESTAMPTZ           NOT NULL DEFAULT now(),
    updated_at                TIMESTAMPTZ           NOT NULL DEFAULT now(),
    CONSTRAINT kcr_reject_chk CHECK (status <> 'REJECTED' OR reject_reason IS NOT NULL),
    CONSTRAINT kcr_apply_chk  CHECK (status <> 'APPROVED' OR applied_at IS NOT NULL)
);
CREATE INDEX idx_kcr_sheet_status ON kpi_change_requests(sheet_id, status);
-- 한 시트에 미결 수정 요청은 1건만
CREATE UNIQUE INDEX uq_kcr_one_open ON kpi_change_requests(sheet_id)
    WHERE status IN ('PENDING','TEAM_LEADER_APPROVED');

-- ============================================================
-- §3.5 평가
-- ============================================================
CREATE TABLE evaluations (
    id           BIGINT            GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id    BIGINT            NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    user_id      BIGINT            NOT NULL REFERENCES users(id)              ON DELETE RESTRICT,
    division_id  BIGINT            NOT NULL REFERENCES departments(id)        ON DELETE RESTRICT,
    kpi_score    NUMERIC(6,2)      NOT NULL DEFAULT 0 CHECK (kpi_score BETWEEN 0 AND 100),
    bonus_score  NUMERIC(6,2)      NOT NULL DEFAULT 0 CHECK (bonus_score >= 0),
    bonus_reason TEXT              NULL,
    total_score  NUMERIC(6,2)      GENERATED ALWAYS AS (kpi_score + bonus_score) STORED,
    grade        grade             NULL,
    evaluator_id BIGINT            NULL REFERENCES users(id) ON DELETE SET NULL,
    status       evaluation_status NOT NULL DEFAULT 'DRAFT',
    submitted_at TIMESTAMPTZ       NULL,
    confirmed_at TIMESTAMPTZ       NULL,
    created_at   TIMESTAMPTZ       NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ       NOT NULL DEFAULT now(),
    created_by   BIGINT            NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT evaluations_unique    UNIQUE (period_id, user_id),
    CONSTRAINT eval_total_max_chk    CHECK (kpi_score + bonus_score <= 110),
    CONSTRAINT eval_s_grade_chk      CHECK (grade <> 'S' OR kpi_score + bonus_score > 100),
    CONSTRAINT eval_bonus_reason_chk CHECK (bonus_score = 0 OR bonus_reason IS NOT NULL),
    CONSTRAINT eval_confirmed_chk    CHECK (status <> 'CONFIRMED' OR grade IS NOT NULL)
);
CREATE INDEX idx_evaluations_period_div ON evaluations(period_id, division_id);
CREATE INDEX idx_evaluations_user       ON evaluations(user_id);

-- ============================================================
-- §3.6 연봉계약
-- ============================================================
CREATE TABLE salary_raise_rates (
    id         BIGINT       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id  BIGINT       NOT NULL REFERENCES evaluation_periods(id) ON DELETE CASCADE,
    grade      grade        NOT NULL,
    raise_pct  NUMERIC(5,2) NOT NULL CHECK (raise_pct BETWEEN -100 AND 100),
    created_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by BIGINT       NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT raise_rates_unique UNIQUE (period_id, grade)
);

CREATE TABLE salary_contracts (
    id                 BIGINT          GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_id          BIGINT          NOT NULL REFERENCES evaluation_periods(id) ON DELETE RESTRICT,
    user_id            BIGINT          NOT NULL REFERENCES users(id)              ON DELETE RESTRICT,
    evaluation_id      BIGINT          NOT NULL REFERENCES evaluations(id)        ON DELETE RESTRICT,
    grade              grade           NOT NULL,
    base_salary_before NUMERIC(14,0)   NOT NULL CHECK (base_salary_before >= 0),
    raise_pct          NUMERIC(5,2)    NOT NULL,
    base_salary_after  NUMERIC(14,0)   NOT NULL CHECK (base_salary_after >= 0),
    contract_starts_on DATE            NOT NULL,
    contract_ends_on   DATE            NOT NULL,
    version            INTEGER         NOT NULL DEFAULT 1 CHECK (version >= 1),
    resent_from_id     BIGINT          NULL REFERENCES salary_contracts(id) ON DELETE SET NULL,
    status             contract_status NOT NULL DEFAULT 'DRAFT',
    is_locked          BOOLEAN         NOT NULL DEFAULT FALSE,
    sent_at            TIMESTAMPTZ     NULL,
    -- 전자서명
    consent_checked    BOOLEAN         NOT NULL DEFAULT FALSE,
    signer_user_id     BIGINT          NULL REFERENCES users(id) ON DELETE RESTRICT,
    signer_name        TEXT            NULL,
    signer_ip          INET            NULL,
    signer_user_agent  TEXT            NULL,
    signature_image    BYTEA           NULL,
    document_hash      TEXT            NULL,   -- 서명 시점 계약 본문 SHA-256
    signed_at          TIMESTAMPTZ     NULL,
    -- PDF
    pdf_status         pdf_status      NOT NULL DEFAULT 'NONE',
    pdf_path           TEXT            NULL,
    pdf_generated_at   TIMESTAMPTZ     NULL,
    -- 파기
    cancelled_at       TIMESTAMPTZ     NULL,
    cancelled_by       BIGINT          NULL REFERENCES users(id) ON DELETE RESTRICT,
    cancel_reason      TEXT            NULL,
    created_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ     NOT NULL DEFAULT now(),
    created_by         BIGINT          NULL REFERENCES users(id) ON DELETE SET NULL,
    CONSTRAINT contract_range_chk CHECK (contract_ends_on > contract_starts_on),
    CONSTRAINT contract_signed_chk CHECK (
        status <> 'SIGNED' OR (
            consent_checked
            AND signer_user_id  IS NOT NULL
            AND signer_name     IS NOT NULL
            AND signer_ip       IS NOT NULL
            AND signature_image IS NOT NULL
            AND document_hash   IS NOT NULL
            AND signed_at       IS NOT NULL
            AND is_locked
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
    id            BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id BIGINT      NULL REFERENCES users(id) ON DELETE SET NULL,
    actor_role    user_role   NULL,
    action        TEXT        NOT NULL,   -- 'CONTRACT_SIGNED', 'CONTRACT_CANCELLED', ...
    entity_type   TEXT        NOT NULL,   -- 'salary_contract', 'kpi_sheet', ...
    entity_id     BIGINT      NULL,
    before_data   JSONB       NULL,
    after_data    JSONB       NULL,
    reason        TEXT        NULL,
    ip            INET        NULL,
    user_agent    TEXT        NULL
);
CREATE INDEX idx_audit_entity ON audit_logs(entity_type, entity_id, occurred_at DESC);
CREATE INDEX idx_audit_actor  ON audit_logs(actor_user_id, occurred_at DESC);

-- ============================================================
-- §3.8 불변성 트리거
-- ============================================================
-- (1) updated_at 자동 갱신 — updated_at을 가진 모든 테이블에 BEFORE UPDATE로 붙인다
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$fn$;

-- (2) 서명 완료 계약서 READ_ONLY
--     잠금 후 바뀔 수 있는 칼럼: status(→CANCELLED), cancelled_*, pdf_*, updated_at
-- (brief Step 2 버전: DECLARE에서 OLD를 바로 대입하지 않고 본문에서 대입한다)
CREATE OR REPLACE FUNCTION enforce_contract_lock() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    old_frozen salary_contracts%ROWTYPE;
    new_frozen salary_contracts%ROWTYPE;
BEGIN
    IF NOT OLD.is_locked THEN
        RETURN NEW;
    END IF;

    old_frozen := OLD;
    new_frozen := NEW;

    -- 잠금 후에도 바뀔 수 있는 칼럼만 비교에서 뺀다
    old_frozen.status           := NULL; new_frozen.status           := NULL;
    old_frozen.cancelled_at     := NULL; new_frozen.cancelled_at     := NULL;
    old_frozen.cancelled_by     := NULL; new_frozen.cancelled_by     := NULL;
    old_frozen.cancel_reason    := NULL; new_frozen.cancel_reason    := NULL;
    old_frozen.pdf_status       := NULL; new_frozen.pdf_status       := NULL;
    old_frozen.pdf_path         := NULL; new_frozen.pdf_path         := NULL;
    old_frozen.pdf_generated_at := NULL; new_frozen.pdf_generated_at := NULL;
    old_frozen.updated_at       := NULL; new_frozen.updated_at       := NULL;

    IF old_frozen IS DISTINCT FROM new_frozen THEN
        RAISE EXCEPTION 'READ_ONLY: 서명 완료된 계약서는 수정할 수 없습니다 (contract_id=%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NOT (NEW.status = 'CANCELLED' OR NEW.status = OLD.status) THEN
        RAISE EXCEPTION 'READ_ONLY: 서명 완료된 계약서는 파기 외 상태로 바꿀 수 없습니다 (contract_id=%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;

CREATE TRIGGER trg_contract_lock BEFORE UPDATE ON salary_contracts
    FOR EACH ROW EXECUTE FUNCTION enforce_contract_lock();

-- (3) 계약서·감사로그 삭제 금지 (감사 추적 보존)
CREATE OR REPLACE FUNCTION forbid_row_change() RETURNS trigger
LANGUAGE plpgsql AS $fn$
BEGIN
    RAISE EXCEPTION '% 행은 %할 수 없습니다. 계약서는 파기(CANCELLED)로 처리하십시오.',
        TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$fn$;

CREATE TRIGGER trg_no_delete_contracts BEFORE DELETE ON salary_contracts
    FOR EACH ROW EXECUTE FUNCTION forbid_row_change();
CREATE TRIGGER trg_no_delete_audit     BEFORE DELETE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION forbid_row_change();
CREATE TRIGGER trg_no_update_audit     BEFORE UPDATE ON audit_logs
    FOR EACH ROW EXECUTE FUNCTION forbid_row_change();

-- ============================================================
-- updated_at 트리거 (brief Step 3) — trg_contract_lock이 이름 알파벳 순으로
-- trg_contracts_updated_at보다 먼저 실행되어야 하므로 트리거 이름을 바꾸지 않는다
-- ============================================================
CREATE TRIGGER trg_departments_updated_at        BEFORE UPDATE ON departments        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_users_updated_at              BEFORE UPDATE ON users              FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_periods_updated_at            BEFORE UPDATE ON evaluation_periods FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_quotas_updated_at             BEFORE UPDATE ON department_quotas  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_kpi_sheets_updated_at         BEFORE UPDATE ON kpi_sheets         FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_kpis_updated_at               BEFORE UPDATE ON kpis               FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_kcr_updated_at                BEFORE UPDATE ON kpi_change_requests FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_evaluations_updated_at        BEFORE UPDATE ON evaluations        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_raise_rates_updated_at        BEFORE UPDATE ON salary_raise_rates FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_contracts_updated_at          BEFORE UPDATE ON salary_contracts   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
