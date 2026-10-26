-- 인사평가를 가상 자료에서 서버 저장으로 옮긴다 (2026-10-26 인사담당자 — 평가 3단계 ①):
-- 해마다 인사담당자가 회차(마감일 · 등급 기준)를 정하고 평가를 시작하면, 그해 평가 대상 한 사람마다 한 줄과 확정한 KPI 의 실행목표마다 한 줄이 만들어진다.
-- 흐름: 자기평가(SELF) → 평가자 점수(SCORE) → 등급 배정(GRADE) → 대표이사 확인(CEO) → 인사담당자 확정(HR) → 확정 끝(DONE) → 공개 → 직원의 이의 신청.
-- 서버 DB 는 019(급여 문의 받는 사람)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 20.
-- 번호 규칙: 번호는 merge 순서대로 — 다른 PR 이 먼저 020 을 써서 merge 되면 이 파일 번호를 다음 번호로 바꾼다 (check_schema.py 0번 검사가 막는다).
-- 001 의 evaluation_periods · department_quotas · evaluations 는 건드리지 않고 쓰지 않는다 — 8월에 설계한 것이라 지금 규칙(본부 묶음 · 대표이사 확인 · 공개 · 이의 신청)과 맞지 않고 운영 DB 에 자료도 없다.
--   (010 이 001 의 kpi_sheets · kpis · kpi_change_requests 를 쓰지 않은 것과 같다)
-- 여섯 표: ev_rounds(회차) · ev_targets(평가 대상) · ev_items(실행목표) · ev_files(증빙) · ev_pools(묶음 기록) · ev_appeals(이의 신청).
-- 직원 · 평가자 · 처리한 사람을 가리키는 칸은 모두 지울 수 없다 (RESTRICT) — 퇴직자는 지우지 않고 비활성으로 두므로, 누가 했는지 알 수 없게 되면 안 된다.
-- 감사로그: 서버 코드가 처리한 사실만 남긴다 — 회차 정하기 · 시작 · 근태 마침 · 제외 · 되돌림 · 자기평가 없이 · 평가자 바꾸기 · 자기평가 제출 · 평가자 제출 ·
--   본부장 마감 · 확인 · 돌려보냄 · 확정 · 되돌리기 · 공개 · 이의 신청 · 답변 · 이의 확인 · 이의 돌려보냄. 인사담당자가 대표이사 몫을 대신하면 on_behalf_of 에 대표이사가 남는다.
--   등급 · 점수 · 근태 숫자 · 글은 넣지 않는다 (공개 전 등급이 감사로그로 새지 않게). 임시 저장 · 고치기 하나하나도 남기지 않는다 (010 의 확정 전 초안과 같다).

-- 회차 — 해마다 한 줄. 그해 평가의 마감일 여섯과 등급 기준 점수, 그리고 시작 · 근태 마침 · 공개한 때.
-- 평가 시작일은 kpi_years.eval_start 한 곳에만 둔다 (그래서 그해 kpi_years 줄이 먼저 있어야 하고, 회차가 있는 해의 줄은 지울 수 없다).
-- 마감일: self_to 자기평가 · evid_due 증빙 올리기 · score_to 평가자 점수 · grade_to 등급 배정 · ceo_to 대표이사 확인 · final_to 인사담당자 확정 — 이 차례대로 늦어져야 한다 (같은 날은 된다). 증빙 기한은 평가자 점수 마감보다 늦으면 안 된다.
-- started_at · started_by: 평가를 시작해 대상 줄을 떠 둔 때. att_done_at: 근태(지각 · 연차) 입력을 마친 때. released_at · released_by: 직원에게 공개한 때 (시작하지 않고는 공개할 수 없다).
-- band_a · band_b · band_c: 등급 기준 점수 — 점수가 100 을 넘으면 S, band_a 를 넘으면 A, band_b 를 넘으면 B, band_c 를 넘으면 C 까지 받을 수 있고 그 아래는 D (기본 90 · 80 · 70).
--   A > B > C 이고 A 는 100 이하, C 는 1 이상이어야 한다.
CREATE TABLE ev_rounds (
    year         INTEGER PRIMARY KEY CHECK (year BETWEEN 2026 AND 2100) REFERENCES kpi_years(year) ON DELETE RESTRICT,
    self_to      TEXT    NOT NULL CHECK (date(self_to) IS self_to),
    evid_due     TEXT    NOT NULL CHECK (date(evid_due) IS evid_due),
    score_to     TEXT    NOT NULL CHECK (date(score_to) IS score_to),
    grade_to     TEXT    NOT NULL CHECK (date(grade_to) IS grade_to),
    ceo_to       TEXT    NOT NULL CHECK (date(ceo_to) IS ceo_to),
    final_to     TEXT    NOT NULL CHECK (date(final_to) IS final_to),
    band_a       INTEGER NOT NULL DEFAULT 90,
    band_b       INTEGER NOT NULL DEFAULT 80,
    band_c       INTEGER NOT NULL DEFAULT 70,
    started_at   TEXT    NULL,
    started_by   INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    att_done_at  TEXT    NULL,
    released_at  TEXT    NULL,
    released_by  INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_at   TEXT    NOT NULL,
    updated_by   INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    CHECK (self_to <= score_to AND score_to <= grade_to AND grade_to <= ceo_to AND ceo_to <= final_to),
    CHECK (evid_due <= score_to),
    CHECK (band_a <= 100 AND band_a > band_b AND band_b > band_c AND band_c >= 1),
    CHECK (released_at IS NULL OR started_at IS NOT NULL)
);

-- 평가 대상 — 한 사람 한 해 한 줄. 평가를 시작할 때 그 시점의 소속 · 평가자 · KPI 판을 떠 둔다 (나중에 조직이 바뀌어도 그해 평가는 그대로 보이게).
-- [떠 둔 칸]
--   pool: 같이 등급을 배정하는 묶음 — DIV:n(n번 본부의 팀원) · LEADERS(팀장) · DIRECT(대표이사 직속).
--   division_id · team_id: 보는 범위. 부서가 지워져도 남게 일부러 외래키를 걸지 않는다. div_name · team_name: 보고서 · 화면에 쓰는 이름("본부 직속"도 글자로 저장).
--   title: 직급. is_leader: 팀장이면 1. rater_id: 평가자 (NULL = 평가자를 못 찾음). assigner_id: 본부 팀원 묶음의 본부장 (NULL = 대표이사).
--   writes_kpi: 그해 KPI 를 쓰는 사람인가. kpi_version: 평가에 쓰는 확정본 판 (NULL = KPI 없이 등급만) — 확정본은 고칠 수 없어서(010) 평가 기준이 안 바뀐다.
-- [진행 칸]
--   stage: 지금 단계. out_at · out_reason · out_by: 평가에서 뺀 때 · 이유(1~100자) · 뺀 사람 (이유와 때는 함께 있거나 함께 없다).
--   no_self_at · no_self_by: 자기평가 없이 진행하기로 한 때 · 사람. self_at: 자기평가를 제출한 때.
--   late: 지각 횟수. leave_x2: 연차 초과 사용일 × 2 (반일 = 1) — 정수로 두려고 2 를 곱해 둔다. 초과 5일(10) 이상이면 S · A · B 를 받을 수 없다.
-- [점수 칸]
--   grade: 등급 배정 때 정한 등급 (SABCD). proposed: 평가자가 제출할 때 낸 등급 (SABCD). comment: 평가자 코멘트(직원에게 공개) · head_comment: 등급을 배정하는 사람(본부장 · 대표이사)의 코멘트 — 각각 500자까지.
--   bonus_x2 · bonus_reason: 가점 × 2 (0~20 = 10점까지, 0.5점 단위)와 사유 (가점이 있으면 사유가 있어야 한다). 사유는 100자까지.
--   sub_score_x10: 평가자가 제출할 때의 총점(가점 포함) × 10 (0~1100 = 110점까지). sub_marks: 그때의 실행목표별 등급 (JSON 객체, {"실행목표 번호": 등급}) —
--   나중에 다른 사람이 고친 것과 견주려고 떠 둔다.
--   scored_at · scored_by: 평가자가 제출한 때 · 사람. proxy_by: 마지막으로 대표이사 몫을 대신 넣은 인사담당자.
--   edit_seen: 평가자가 "확인"을 누른 고친 내용의 글 (평가자가 낸 것과 지금이 달라졌을 때 그 안내를 이미 봤는지 가린다).
-- 단계에 따라 있어야 하는 칸: GRADE 이후엔 평가자 제안(proposed), HR · DONE 에선 확정 등급(grade).
-- 한 번 만든 줄은 지울 수 없다 (아래 트리거) — 평가 기록 보존. 퇴직자는 비활성으로.
CREATE TABLE ev_targets (
    year           INTEGER NOT NULL REFERENCES ev_rounds(year) ON DELETE RESTRICT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    pool           TEXT    NOT NULL CHECK (pool IN ('LEADERS','DIRECT') OR pool GLOB 'DIV:[1-9]*'),
    division_id    INTEGER NULL,
    team_id        INTEGER NULL,
    div_name       TEXT    NOT NULL,
    team_name      TEXT    NOT NULL,
    title          TEXT    NULL,
    is_leader      INTEGER NOT NULL CHECK (is_leader IN (0,1)),
    rater_id       INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigner_id    INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    writes_kpi     INTEGER NOT NULL CHECK (writes_kpi IN (0,1)),
    kpi_version    INTEGER NULL,
    stage          TEXT    NOT NULL CHECK (stage IN ('SELF','SCORE','GRADE','CEO','HR','DONE')),
    out_at         TEXT    NULL,
    out_reason     TEXT    NULL CHECK (out_reason IS NULL OR length(out_reason) BETWEEN 1 AND 100),
    out_by         INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    no_self_at     TEXT    NULL,
    no_self_by     INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    self_at        TEXT    NULL,
    late           INTEGER NOT NULL DEFAULT 0 CHECK (late BETWEEN 0 AND 999),
    leave_x2       INTEGER NOT NULL DEFAULT 0 CHECK (leave_x2 BETWEEN 0 AND 730),
    grade          TEXT    NULL CHECK (grade IN ('S','A','B','C','D')),
    comment        TEXT    NOT NULL DEFAULT '' CHECK (length(comment) <= 500),
    head_comment   TEXT    NOT NULL DEFAULT '' CHECK (length(head_comment) <= 500),
    bonus_x2       INTEGER NOT NULL DEFAULT 0 CHECK (bonus_x2 BETWEEN 0 AND 20),
    bonus_reason   TEXT    NOT NULL DEFAULT '' CHECK (length(bonus_reason) <= 100),
    proposed       TEXT    NULL CHECK (proposed IN ('S','A','B','C','D')),
    sub_score_x10  INTEGER NULL CHECK (sub_score_x10 BETWEEN 0 AND 1100),
    sub_marks      TEXT    NULL CHECK (sub_marks IS NULL OR (json_valid(sub_marks) AND json_type(sub_marks) = 'object')),
    scored_at      TEXT    NULL,
    scored_by      INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    proxy_by       INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    edit_seen      TEXT    NULL,
    PRIMARY KEY (year, user_id),
    FOREIGN KEY (user_id, year, kpi_version) REFERENCES kpi_versions(user_id, year, version) ON DELETE RESTRICT,
    CHECK ((out_at IS NULL) = (out_reason IS NULL)),
    CHECK (bonus_x2 = 0 OR length(trim(bonus_reason)) > 0),
    CHECK (stage IN ('SELF','SCORE') OR proposed IS NOT NULL),
    CHECK (stage NOT IN ('HR','DONE') OR grade IS NOT NULL),
    CHECK (kpi_version IS NULL OR writes_kpi = 1)
);

-- 묶음별로 사람을 모아 보는 일이 잦다 (본부장 마감 · 대표이사 확인 · 인사담당자 확정).
CREATE INDEX ev_targets_pool ON ev_targets(year, pool);

CREATE TRIGGER trg_ev_targets_no_delete BEFORE DELETE ON ev_targets
BEGIN
    SELECT RAISE(ABORT, 'ev_targets 행(평가 대상)은 삭제할 수 없습니다. 평가 기록을 남겨야 합니다.');
END;

-- 실행목표 — 떠 둔 KPI(kpi_version)의 실행목표마다 한 줄. 평가를 시작할 때 빈 줄로 만들고, 직원이 실적(note)과 자기평가(self_grade)를, 평가자가 평가(grade)를 채운다.
-- rid: 그 KPI 확정본 안의 실행목표 번호 (kpi_mutes 의 rid 와 같다). note: 실적 글 500자까지. self_grade · grade: SABCD, 아직 안 골랐으면 비어 있다.
-- 한 번 만든 줄은 지울 수 없다 (평가 기록 보존).
CREATE TABLE ev_items (
    year         INTEGER NOT NULL,
    user_id      INTEGER NOT NULL,
    rid          INTEGER NOT NULL CHECK (rid >= 1),
    note         TEXT    NOT NULL DEFAULT '' CHECK (length(note) <= 500),
    self_grade   TEXT    NULL CHECK (self_grade IN ('S','A','B','C','D')),
    grade        TEXT    NULL CHECK (grade IN ('S','A','B','C','D')),
    PRIMARY KEY (year, user_id, rid),
    FOREIGN KEY (year, user_id) REFERENCES ev_targets(year, user_id) ON DELETE RESTRICT
);

CREATE TRIGGER trg_ev_items_no_delete BEFORE DELETE ON ev_items
BEGIN
    SELECT RAISE(ABORT, 'ev_items 행(실행목표별 평가)은 삭제할 수 없습니다. 평가 기록을 남겨야 합니다.');
END;

-- 증빙 — 실행목표에 붙인 파일의 이름 · 종류 · 크기 · 지문(sha256) · 올린 때. 파일 내용은 DB 에 넣지 않고 서버 폴더에 둔다.
-- 본인이 마감 전에 지울 수 있다 (그래서 지우기를 막지 않는다). 종류는 소문자 확장자 — jpeg 도 jpg 로 적는다. 크기는 1바이트 ~ 10MB.
-- id 는 AUTOINCREMENT — 지운 번호를 다시 쓰지 않는다. 서버 폴더의 파일 이름이 이 번호라, 지운 증빙의 번호를 들고 있던 화면이 같은 번호로 남의 새 파일을 받지 못하게.
CREATE TABLE ev_files (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    year     INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    rid      INTEGER NOT NULL,
    name     TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 120),
    kind     TEXT    NOT NULL CHECK (kind IN ('pdf','png','jpg','xlsx','xls','docx','doc','hwp','hwpx')),
    size     INTEGER NOT NULL CHECK (size BETWEEN 1 AND 10485760),
    sha256   TEXT    NOT NULL,
    at       TEXT    NOT NULL,
    FOREIGN KEY (year, user_id, rid) REFERENCES ev_items(year, user_id, rid) ON DELETE RESTRICT
);

-- 묶음 기록 — 묶음(pool)마다 한 줄. 묶음의 상태(기다림 · 확인 대기 · 확정 …)는 저장하지 않는다 — 묶음 사람들의 단계(ev_targets.stage)로 센다.
-- 여기엔 누가 언제 무엇을 했는지만 둔다:
--   closed_at · closed_by: 본부장이 마감한 때 · 사람. hr_seen: 마감 알림을 확인한 인사담당자 번호들 (JSON 목록).
--   ceo_at · ceo_by: 대표이사가 확인한 때 · 사람. back_at · back_by · back_reason: 돌려보낸 때 · 사람 · 이유(1~200자, 때와 이유는 함께 있거나 함께 없다).
--   undo_at · undo_by · undo_from: 확정을 되돌린 때 · 사람 · 되돌리기 전 단계 (HR 또는 CEO).
--   ceo_behalf · back_behalf: 인사담당자가 대표이사 몫을 대신 처리했으면 원래 처리해야 할 대표이사 (010 의 on_behalf_of 와 같다).
-- pool 의 모양은 ev_targets 와 같다.
CREATE TABLE ev_pools (
    year         INTEGER NOT NULL REFERENCES ev_rounds(year) ON DELETE RESTRICT,
    pool         TEXT    NOT NULL CHECK (pool IN ('LEADERS','DIRECT') OR pool GLOB 'DIV:[1-9]*'),
    closed_at    TEXT    NULL,
    closed_by    INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    hr_seen      TEXT    NOT NULL DEFAULT '[]' CHECK (json_valid(hr_seen) AND json_type(hr_seen) = 'array'),
    ceo_at       TEXT    NULL,
    ceo_by       INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    ceo_behalf   INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    back_at      TEXT    NULL,
    back_by      INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    back_behalf  INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    back_reason  TEXT    NULL CHECK (back_reason IS NULL OR length(back_reason) BETWEEN 1 AND 200),
    undo_at      TEXT    NULL,
    undo_by      INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    undo_from    TEXT    NULL CHECK (undo_from IN ('HR','CEO')),
    PRIMARY KEY (year, pool),
    CHECK ((back_at IS NULL) = (back_reason IS NULL))
);

-- 이의 신청 — 공개된 등급에 직원이 이의를 내면 한 줄. 한 사람 한 해에 한 번만 낼 수 있다. 쓰는 곳은 평가 3단계 ② —
--   번호(020)를 하나만 쓰려고 ① 에서 표를 함께 만든다.
-- reason: 이의 이유(1~500자). at: 낸 때. before: 이의 낸 등급. status: OPEN 답변 기다림 · CEO 대표이사 결정 기다림 · DONE 끝.
-- answer: 답변(500자까지). answered_at · answered_by: 답한 때 · 사람. after: 이의 뒤 등급. marks: 이의 뒤 실행목표별 등급 (JSON 객체).
-- decided_at · decided_by · decided_behalf: 결정한 때 · 사람 · 인사담당자가 대신했으면 원래 대표이사.
-- OPEN 이 아니면 답변과 이의 뒤 등급이 있어야 한다.
CREATE TABLE ev_appeals (
    year            INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    reason          TEXT    NOT NULL CHECK (length(reason) BETWEEN 1 AND 500),
    at              TEXT    NOT NULL,
    status          TEXT    NOT NULL CHECK (status IN ('OPEN','CEO','DONE')),
    before          TEXT    NOT NULL CHECK (before IN ('S','A','B','C','D')),
    after           TEXT    NULL CHECK (after IN ('S','A','B','C','D')),
    marks           TEXT    NULL CHECK (marks IS NULL OR (json_valid(marks) AND json_type(marks) = 'object')),
    answer          TEXT    NULL CHECK (answer IS NULL OR length(answer) <= 500),
    answered_at     TEXT    NULL,
    answered_by     INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    decided_at      TEXT    NULL,
    decided_by      INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    decided_behalf  INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT,
    PRIMARY KEY (year, user_id),
    FOREIGN KEY (year, user_id) REFERENCES ev_targets(year, user_id) ON DELETE RESTRICT,
    CHECK (status = 'OPEN' OR (answer IS NOT NULL AND after IS NOT NULL))
);
