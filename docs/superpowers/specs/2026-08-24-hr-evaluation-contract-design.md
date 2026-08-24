# 평가·성과 / 급여·계약 백엔드 설계

- 작성일: 2026-08-24
- 대상 모듈: 평가·성과(KPI, 상대평가 쿼터), 급여·계약(전자 연봉계약서)
- 상태: 설계 승인됨 (구현 착수 전)

## 1. 이번 작업의 범위

**만드는 것**

1. PostgreSQL 스키마 (`backend/hr_eval/sql/001_init.sql`)
2. 프레임워크 독립 도메인 로직 (`backend/hr_eval/`)
   - `calculate_department_quota` — 본부 쿼터 산정
   - `validate_and_assign_evaluation_grades` — 등급 배정 검증
   - `submit_kpi_goal` — KPI 제출 검증
   - `finalize_salary_contract` / `cancel_salary_contract` / `resend_salary_contract`
3. 자체 검증 스크립트 (`backend/hr_eval/selfcheck.py`)

**만들지 않는 것 (별도 PR)**

- 로그인·세션·권한 미들웨어 — 모듈 순서 2번. 도메인 로직은 "이미 인증된 사용자"를 인자로 받는다.
- PostgreSQL 컨테이너 기동, 마이그레이션 실행, `docker-compose.yml` 수정
- Flask API 엔드포인트, `public/index.html` 화면
- PDF 실제 렌더링(`reportlab` 호출) — 스키마에 상태 칼럼만 준비한다
- 계약서 발송 메일/알림

### 왜 이 범위인가

현재 저장소에는 DB가 없다. `docker-compose.yml`은 nginx + Flask 두 컨테이너뿐이고, Flask 백엔드는 출장경비 계산·규정 검색용 무상태 API다. 저장되는 데이터가 0건이고 `users` 테이블도 세션도 없다.

`CLAUDE.md`의 합의된 모듈 순서에서 로그인·역할은 2번, 평가·성과 / 급여·계약은 5번(가장 민감해서 마지막)이다. 이번 PRD는 5번이므로, 지금은 **스키마와 규칙을 코드로 확정해두고** 실제 배선은 로그인 모듈 이후로 미룬다.

### 보안·컴플라이언스 전제

- `hr-portal`은 **Public 저장소**다. DDL과 로직 코드만 커밋한다.
- **연봉 인상률 수치, 직원 실데이터, 평가 결과는 커밋 대상이 아니다.** `.env`·`regulations/`와 같은 원칙으로 서버 DB에만 존재한다.
- 서명 IP·서명 이미지·평가 등급은 개인정보다. 현 설계는 무기한 보관이며, 보관기간 규정이 정해지면 파기 배치를 별도로 추가한다.

## 2. 확정된 업무 규칙

| 항목 | 규칙 |
|---|---|
| 조직 | 본부(DIVISION) > 팀(TEAM) 2단계. 쿼터는 본부 인원 기준 |
| 역할 | EMPLOYEE / TEAM_LEADER / DIVISION_HEAD / HR_ADMIN (1인 1역할) |
| 쿼터 산정 (N ≥ 5) | **개별 정원**: `Q_A = Q_B = Q_D = round_half_up(N × 10%)`, `Q_C = N − (Q_A + Q_B + Q_D)` |
| 쿼터 산정 (N ≤ 4) | **그룹 정원**: 상위(A·B 합계) = `1 if N ≥ 2 else 0`, 하위(C·D 합계) = 나머지 |
| S등급 자격 | 총점 > 100점 (초과, 100점 동점은 불가) |
| S등급 상한 | `Cap_S = Q_A` (N ≤ 4는 `Cap_S = 상위 정원`) |
| S등급 차감 | S가 K명이면 `Q_A' = Q_A − K` (N ≤ 4는 `상위' = 상위 − K`) |
| 쿼터 불일치 | **초과 = ERROR(제출 차단)**, 미달 = WARNING(통과) |
| 평가 주기 | 연 1회 ANNUAL(등급 확정) + MIDTERM 중간점검(등급 없음, 기록만) |
| KPI 개수 | 1인 최소 3개 |
| KPI 가중치 | 합계 정확히 100.00 |
| KPI 승인 | DRAFT → TEAM_LEADER_APPROVED → DIVISION_HEAD_APPROVED(확정·잠금) |
| KPI 수정 | HR이 연 `is_kpi_window_open` 기간에만 **수정안 요청** 가능. 팀장·본부장 승인 시 반영. 승인 전까지 기존 KPI가 유효 |
| 점수 | KPI 0~100점 + 가점(0점 이상). **총점 상한 110점**. 가점 입력 시 사유 필수 |
| 서명 | 동의 체크 + 성명 입력 + 손글씨 서명 이미지. 서명자·IP·UA·일시·문서해시 기록 |
| 서명 후 | 즉시 READ_ONLY 잠금. HR_ADMIN만 사유 입력 후 파기 가능 |
| 재발송 | 원본은 CANCELLED로 보존하고 **새 버전 행을 발행**한다 (덮어쓰지 않음) |

### 두 가지 쿼터 모드

본부 인원에 따라 정원을 매기는 방식이 다르다. **경계는 5명이다.**

**GROUPED (N ≤ 4) — 그룹 정원**

등급별로 쪼개지 않고 상위(A·B)와 하위(C·D)를 묶어서 정원을 준다. 4명짜리 본부에서 A 1명 + B 1명을 따로 강제하면 절반이 상위등급이 되어 상대평가가 의미를 잃기 때문이다.

| N | 상위 (A+B) | 하위 (C+D) | Cap_S |
|---:|---:|---:|---:|
| 0 | 0 | 0 | 0 |
| 1 | 0 | 1 | 0 |
| 2 | 1 | 1 | 1 |
| 3 | 1 | 2 | 1 |
| 4 | 1 | 3 | 1 |

- 상위 1명을 **A로 줄지 B로 줄지는 조직장이 고른다.** 하위도 C·D 중 자유롭게 나눈다.
- 1명짜리 본부는 상위 정원이 0이라 C 또는 D만 가능하다.
- `Cap_S = 상위 정원`이므로 **4명 본부에서도 100점 초과자가 있으면 S 1명이 가능하다.** 대신 그 1명이 상위 정원을 다 쓰므로 A·B는 0명이 된다.

**INDIVIDUAL (N ≥ 5) — 등급별 개별 정원**

| N | Q_A | Q_B | Q_C | Q_D | Cap_S |
|---:|---:|---:|---:|---:|---:|
| 5 | 1 | 1 | 2 | 1 | 1 |
| 7 | 1 | 1 | 4 | 1 | 1 |
| 10 | 1 | 1 | 7 | 1 | 1 |
| 15 | 2 | 2 | 9 | 2 | 2 |
| 20 | 2 | 2 | 14 | 2 | 2 |
| 25 | 3 | 3 | 16 | 3 | 3 |

- **5명 본부는 C가 40%까지 내려간다.** (15명 60%, 25명 64%)
- 10의 배수일 때만 10/10/70/10이 정확히 맞는다.

**두 모드 공통**: 정원 합계는 항상 본부 인원 N과 같고, 하위 그룹(GROUPED) 또는 C등급(INDIVIDUAL)이 **잔여 흡수** 역할이라 상한 검사를 하지 않는다.

### 반올림 함정

`round_half_up`은 반드시 `decimal.Decimal` + `ROUND_HALF_UP`으로 구현한다. 파이썬 내장 `round()`는 은행가 반올림(banker's rounding)이라 `round(0.5) == 0`이다. N=5인 본부에서 `N × 0.1 = 0.5`가 되므로, 내장 `round()`를 쓰면 A가 0명이 되어 규칙이 조용히 어긋난다.

### 가정 (반대 지시가 없으면 이대로 간다)

- 본부장도 평가 대상이며 본부 인원 N에 포함된다. 본부장의 KPI·평가 승인자는 HR_ADMIN이다.
- 일반 직원·팀장은 팀 소속, 본부장은 본부 직속 소속이다.
- 본부 인원 N = 해당 본부 직속 소속 + 하위 팀 소속 인원 중 재직자(`is_active`).
- 연봉 인상률 테이블은 평가기간별로 DB에 두고 HR_ADMIN이 관리한다.

## 3. 데이터 모델

### 3.1 ENUM

```sql
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
```

### 3.2 조직·사용자

```sql
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
```

본부 인원 N 조회 (2단계라 재귀 불필요):

```sql
SELECT count(*) FROM users u
JOIN departments d ON d.id = u.department_id
WHERE u.is_active AND (d.id = $1 OR d.parent_id = $1);
```

### 3.3 평가기간·쿼터

```sql
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
```

두 CHECK가 핵심이다. HR_ADMIN이 쿼터를 조율하더라도 **정원 합계 = 본부 인원**이 어느 모드에서든 DB 차원에서 유지되고, 모드에 맞지 않는 칼럼을 채우면 저장이 거부된다.

### 3.4 KPI

```sql
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
```

`proposed_kpis`는 수정안 전체 스냅샷이다. 승인 전까지 원본 `kpis` 행은 건드리지 않으므로, 평가 기준이 승인 전에 비는 구간이 생기지 않는다.

가중치 합 100은 여러 행에 걸친 조건이라 `CHECK`으로 표현할 수 없다. 도메인 로직에서 검증한다.

### 3.5 평가

```sql
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
```

`division_id`는 평가 시점의 본부 스냅샷이다. 나중에 조직개편이 있어도 그 해 쿼터 계산 근거가 흔들리지 않는다.

`eval_s_grade_chk`가 **"100점 이하 S등급 지정 불가"를 DB에서 직접 막는다.** 앱 코드에 버그가 있어도 뚫리지 않는다.

MIDTERM 기간은 `grade`가 NULL이다. "ANNUAL이면 등급 필수"는 다른 테이블을 참조해야 해서 `CHECK`으로 못 쓰고 도메인 로직에서 본다.

### 3.6 연봉계약

```sql
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
```

`contract_signed_chk`가 **"서명 완료 = 서명자·IP·일시·해시·잠금이 전부 채워진 상태"를 DB에서 보증한다.** 반쪽짜리 서명 행이 생길 수 없다.

`uq_contract_active` 부분 유니크 인덱스 덕분에, 파기하지 않고 재발송하는 실수가 DB에서 막힌다.

### 3.7 감사 로그

```sql
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
```

`actor_role`을 별도로 남긴다. 나중에 그 사람의 역할이 바뀌어도 "그때 무슨 권한으로 했는지"가 보존된다.

### 3.8 불변성 트리거

잠금은 앱 코드가 아니라 **DB가 강제한다.**

```sql
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
CREATE OR REPLACE FUNCTION enforce_contract_lock() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    old_frozen salary_contracts%ROWTYPE := OLD;
    new_frozen salary_contracts%ROWTYPE := NEW;
BEGIN
    IF NOT OLD.is_locked THEN
        RETURN NEW;
    END IF;

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

    IF NEW.status NOT IN ('SIGNED','CANCELLED') THEN
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
```

## 4. 코드 구조

```
backend/hr_eval/
├── sql/001_init.sql        # 3장 DDL 전체
├── domain/
│   ├── models.py           # dataclass + Enum (DB·프레임워크 무관)
│   ├── errors.py           # 예외 계층, Issue
│   ├── quota.py            # calculate_department_quota  (순수 함수)
│   ├── grading.py          # validate_and_assign_evaluation_grades (순수 함수)
│   └── contract_rules.py   # 인상률 매핑·금액 계산·문서해시 (순수 함수)
├── ports.py                # Repository / UnitOfWork Protocol (구현 없음)
├── usecases.py             # submit_kpi_goal, finalize_salary_contract, cancel, resend
└── selfcheck.py            # assert 기반 자체 검증
```

**`domain/`은 DB도 Flask도 import하지 않는다.** 입력은 dataclass, 출력은 결과 객체 또는 예외다. 그래서 DB 없이 전부 테스트된다.

`usecases.py`만 `ports.py`의 `Protocol`을 받아 저장·감사로그를 남긴다. 나중에 Flask에 붙이든 FastAPI로 가든 이 계층은 그대로 재사용한다.

새 의존성은 추가하지 않는다. 표준 라이브러리(`dataclasses`, `enum`, `decimal`, `hashlib`, `datetime`)만 쓴다.

### 예외 계층

```python
class HrDomainError(Exception): ...
class ValidationError(HrDomainError):   # 규칙 위반. issues 목록을 들고 있다 → HTTP 400
class PermissionDeniedError(HrDomainError):  # 역할 부족 → 403
class StateConflictError(HrDomainError):     # 잠김·이미 서명됨·창 닫힘 → 409
class NotFoundError(HrDomainError):          # → 404
```

`Issue = dataclass(code, message, severity, target)`. `severity`는 `ERROR` / `WARNING`.

**WARNING은 예외로 던지지 않고 결과 객체에 담아 돌려준다.** 쿼터 미달은 통과시키되 화면에 경고를 띄워야 하기 때문이다.

**검증은 첫 오류에서 멈추지 않고 전부 모아 한 번에 반환한다.** 조직장이 20명치 등급을 고칠 때 오류를 하나씩 만나면 못 쓴다.

## 5. 도메인 로직

### 5.1 `calculate_department_quota(headcount) -> Quota`

```python
SMALL_DIVISION_THRESHOLD = 4     # 이하면 GROUPED 모드

def calculate_department_quota(n):
    if n < 0:
        raise ValueError(...)

    if n <= SMALL_DIVISION_THRESHOLD:
        upper = 1 if n >= 2 else 0
        return GroupedQuota(upper=upper, lower=n - upper, cap_s=upper)

    q = round_half_up(n * 0.1)                 # Decimal ROUND_HALF_UP
    return IndividualQuota(a=q, b=q, d=q, c=n - 3 * q, cap_s=q)
```

- 반환 타입이 두 가지다. 공통 상위 타입 `Quota`가 `mode`, `cap_s`, `total()`을 제공한다.
- `IndividualQuota.c`는 `3 * round_half_up(N/10) <= N`이 모든 `N >= 5`에서 성립하므로 음수가 되지 않는다.
- 두 모드 모두 `total() == N`이다. selfcheck에서 전 구간 검증한다.

### 5.2 `validate_and_assign_evaluation_grades(...) -> GradeAssignmentResult`

입력: 평가기간, 본부, 쿼터(`Quota` 또는 DB 조율값), 본부 소속 인원 명단, 조직장이 배정한 `{user_id: (total_score, grade)}`.

검사 순서 (전부 수행하고 issues를 모아 반환):

**공통 검사** (모드 무관, 전부 수행하고 issues를 모아 반환):

| # | 검사 | severity |
|---|---|---|
| 1 | 명단에 없는 인원이 배정에 포함 | ERROR |
| 2 | 배정 누락 인원 존재 | ERROR |
| 3 | 같은 인원 중복 배정 | ERROR |
| 4 | `grade = S`인데 `total_score <= 100` | ERROR |
| 5 | `K = count(S) > Cap_S` | ERROR |
| 6 | 중간점검(MIDTERM) 기간에 등급 배정 시도 | ERROR |

**INDIVIDUAL 모드 (N ≥ 5) 추가 검사**

| # | 검사 | severity |
|---|---|---|
| 7 | `count(A) > Q_A' (= Q_A − K)` | ERROR |
| 8 | `count(B) > Q_B` / `count(D) > Q_D` | ERROR |
| 9 | `count(A) < Q_A'` / `count(B) < Q_B` / `count(D) < Q_D` | WARNING |

**GROUPED 모드 (N ≤ 4) 추가 검사**

| # | 검사 | severity |
|---|---|---|
| 7 | `count(A) + count(B) > 상위' (= 상위 − K)` | ERROR |
| 8 | `count(A) + count(B) < 상위'` | WARNING |

- **잔여 흡수 그룹은 상한 검사를 하지 않는다.** INDIVIDUAL의 C, GROUPED의 하위(C·D)가 그것이다. 상위가 미달이면 잔여가 늘어나는데, 그건 이미 WARNING으로 잡혔으므로 같은 사실을 ERROR로 두 번 잡으면 제출이 부당하게 막힌다.
- ERROR가 하나라도 있으면 `ValidationError(issues)`를 던진다. 저장은 일어나지 않는다.
- ERROR가 없으면 `GradeAssignmentResult(assignments, warnings, effective_upper, s_count=K)`를 반환한다. `effective_upper`는 INDIVIDUAL에서는 `Q_A'`, GROUPED에서는 `상위'`다.

`Cap_S`가 상위 정원과 같으므로, 두 모드 모두 **S를 상한까지 쓰면 A(및 GROUPED의 B)는 0명이 된다.** 의도된 동작이다.

### 5.3 `submit_kpi_goal(actor, sheet, kpis, uow) -> KpiSheet`

1. `actor.id == sheet.user_id` 또는 `actor.role == HR_ADMIN` (아니면 `PermissionDeniedError`)
2. 평가기간 `is_kpi_window_open = TRUE` (아니면 `StateConflictError`)
3. KPI 개수 >= 3 (아니면 `ValidationError`)
4. `sum(weight_pct) == Decimal("100.00")` — **`Decimal` 비교**, float 쓰지 않는다
5. 각 `weight_pct`는 0 초과 100 이하, 제목 공백 불가
6. 시트가 이미 `DIVISION_HEAD_APPROVED`(잠금)이면 → 직접 수정 불가. `StateConflictError`와 함께 "수정 요청(`kpi_change_requests`)을 쓰라"고 안내한다
7. 통과 시 한 트랜잭션에서: `kpis` 교체 → `kpi_sheets.status = DRAFT`, `submitted_at = now()` → `audit_logs` 기록

수정 요청 경로(`request_kpi_change` / `approve_kpi_change`)도 같은 1~5번 검증을 재사용한다. `approve_kpi_change`가 본부장 승인까지 받으면 그때 `proposed_kpis`를 `kpis`에 반영하고 `applied_at`을 찍는다.

### 5.4 `finalize_salary_contract(actor, contract_id, signature, uow) -> SalaryContract`

`signature = (consent_checked, signer_name, signature_image, ip, user_agent)`

한 트랜잭션 안에서:

1. `SELECT ... FOR UPDATE`로 계약서 잠금 획득 (동시 서명 방지)
2. `actor.id == contract.user_id` (본인만 서명. HR_ADMIN도 대리 서명 불가) → 아니면 `PermissionDeniedError`
3. `contract.status == 'SENT'` → 아니면 `StateConflictError` (이미 서명/파기됨)
4. `consent_checked`가 참, `signer_name`이 비지 않음, `signature_image`가 존재 → 아니면 `ValidationError`
5. `document_hash = sha256(정규화된 계약 본문)` 계산
6. `status='SIGNED'`, `is_locked=TRUE`, 서명 5종(`signer_user_id/name/ip/user_agent/signature_image`), `signed_at=now()`, `pdf_status='PENDING'` 기록
7. `audit_logs`에 `CONTRACT_SIGNED` + before/after + IP/UA 기록
8. 커밋

PDF 생성은 커밋 후 별도 단계다. 실패해도 서명은 유효하고 `pdf_status`만 `FAILED`가 된다. **PDF 생성 실패가 서명을 되돌리지 않는다.**

### 5.5 `cancel_salary_contract(actor, contract_id, reason, uow)`

- `actor.role == HR_ADMIN`만 가능 → 아니면 `PermissionDeniedError`
- `reason`이 비면 `ValidationError`
- `status='CANCELLED'`, `cancelled_at/by`, `cancel_reason` 기록 (트리거가 나머지 칼럼 변경을 차단한다)
- `audit_logs`에 `CONTRACT_CANCELLED` + 사유 기록

### 5.6 `resend_salary_contract(actor, contract_id, reason, uow) -> SalaryContract`

- 파기와 새 계약서 발행을 **한 트랜잭션**으로 처리한다
- `cancel_salary_contract` 수행 → `version = 이전 + 1`, `resent_from_id = 이전 id`, `status='SENT'`인 **새 행**을 만든다
- 서명된 원본은 그대로 남는다. `uq_contract_active`가 활성 계약 중복을 막는다
- `audit_logs`에 `CONTRACT_RESENT` 기록

## 6. 트랜잭션·동시성

- **유스케이스 1개 = 트랜잭션 1개.** 부분 저장 상태를 만들지 않는다.
- 본부 단위 평가 제출은 `department_quotas` 행을 `SELECT ... FOR UPDATE`로 잠가 직렬화한다. 두 조직장이 동시에 제출해 쿼터를 함께 초과하는 상황을 막는다.
- 계약서 서명·파기는 계약서 행 `FOR UPDATE`로 잠근다.
- 격리 수준은 PostgreSQL 기본값 `READ COMMITTED` + 명시적 행 잠금으로 간다. `SERIALIZABLE`은 쓰지 않는다.

## 7. 검증 (`selfcheck.py`)

테스트 프레임워크는 새로 깔지 않는다. `assert` 기반 단일 스크립트다.

```bash
python backend/hr_eval/selfcheck.py
```

포함할 케이스:

- 쿼터: N = 0~4 → GROUPED, N = 5, 7, 10, 15, 20, 25 → INDIVIDUAL. 2장 두 표와 일치
- 쿼터: **N = 0부터 200까지 전 구간에서 `quota.total() == N`** (모드 경계 포함)
- 모드 경계: N=4는 GROUPED, N=5는 INDIVIDUAL
- `round_half_up(0.5) == 1` (내장 `round()`였다면 0)
- S: 100점 정확히 → S 불가 / 100.01점 → 가능
- S가 Cap_S 초과 → ERROR
- S가 K명일 때 상위 정원이 `− K` 되는지 (두 모드 각각)
- INDIVIDUAL: A 초과 → ERROR, A 미달 → WARNING이면서 결과는 반환
- GROUPED: 4명 본부에 A1 B1 → `count(A)+count(B)=2 > 1` ERROR
- GROUPED: 4명 본부에 A1 C2 D1 → 통과, B는 0명이어도 오류 아님
- GROUPED: 4명 본부에 C4 → 상위 미달 WARNING, 통과
- GROUPED: 4명 본부에 S1 C3 → 통과 (Cap_S=1)
- GROUPED: 1명 본부에 A1 → 상위 정원 0이므로 ERROR
- 잔여 흡수 그룹은 초과로 잡히지 않는다 (INDIVIDUAL의 C, GROUPED의 C·D)
- 배정 누락·중복·타본부 인원 → 각각 ERROR
- 오류가 여러 개일 때 issues에 전부 담기는지 (첫 오류에서 멈추지 않음)
- KPI: 2개 → ERROR, 가중치 99.99 → ERROR, 100.00 → 통과
- KPI: window 닫힘 → `StateConflictError`
- KPI: 잠긴 시트 직접 수정 → `StateConflictError`
- 계약: 동의 미체크 서명 → `ValidationError`
- 계약: 본인 아닌 사람 서명 → `PermissionDeniedError`
- 계약: 이미 서명된 계약 재서명 → `StateConflictError`
- 계약: HR_ADMIN 아닌 사람 파기 → `PermissionDeniedError`, 사유 없는 파기 → `ValidationError`

CI(`.github/workflows/ci.yml`)에 이 스크립트 실행 단계를 한 줄 추가한다. DB가 필요 없으므로 CI에서 그대로 돈다.

## 8. 다음 단계 (별도 PR)

1. 로그인·세션·역할 미들웨어 (모듈 순서 2번)
2. `docker-compose.yml`에 PostgreSQL 추가 + 마이그레이션 실행 + 저장소 구현체 작성
3. Flask API 엔드포인트
4. `public/index.html` 평가·성과 / 급여·계약 화면 (손글씨 서명 캔버스 포함)
5. `reportlab` PDF 렌더링

## 9. 재검토 항목

- GROUPED/INDIVIDUAL 모드 경계를 4명에 둘지 (현재 `SMALL_DIVISION_THRESHOLD = 4`). 5명 본부가 A1 B1 C2 D1로 상위 40%가 되는 게 걸리면 경계를 올리는 걸 검토
- GROUPED 모드에서 하위(C·D) 배분을 조직장 자율로 둘지, D 최소 1명을 강제할지 (현재 자율)
- 서명 IP·서명 이미지 보관기간 및 파기 절차
- 가중치 합 100을 DB 트리거로도 강제할지 (현재는 도메인 로직만)
