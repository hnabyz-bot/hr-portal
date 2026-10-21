-- KPI 2단계 ① (2026-10-21): 확정한 계획서에 따라 한 해 동안 진행을 적고, 일정이 바뀌면 바로 위 관리자에게 확인을 받는다.
-- 진행 기록은 그 사람 · 그해 확정본이 있을 때만 쓴다 (010 의 kpi_plans 가 부모). 실행목표는 rid, 일정은 sid 로 가리킨다
--   (rid · sid 는 한 계획서 안에서 다시 쓰지 않는다 — hr_eval/domain/kpi_plan.py 의 assign_ids).
-- 서버 DB 는 011(급여명세서)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 12.
-- 네 표 모두 계획서(kpi_plans)에 붙어 있다: 기록이 달린 계획서는 지울 수 없다 (RESTRICT).
-- 글자 수 한도(월 한 줄 · 결과 한 줄 · 일정 변경 사유 · 돌려보내기 이유 200, 늦어지는 이유 100, 링크 300)는 화면 · 서버와 같게 DB 에서도 막는다.
-- 이 표들의 내용 문장은 감사로그에 남기지 않는다. 일정 변경 요청의 처리만 감사로그에 남는다 (서버 코드가 한다).

-- 실행목표별 지금까지의 진행 — 실행목표 하나에 한 줄. 모두 비어 있어도 된다.
-- actual: 지금까지의 실적(0 이상). pct: 진행률을 직접 적을 때(0~100). why: 늦어지고 있을 때 적는 이유.
CREATE TABLE kpi_progress (
    user_id     INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    rid         INTEGER NOT NULL CHECK (rid >= 1),
    actual      REAL    NULL CHECK (actual IS NULL OR actual >= 0),
    pct         INTEGER NULL CHECK (pct BETWEEN 0 AND 100),
    why         TEXT    NULL CHECK (why IS NULL OR length(why) <= 100),
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, year, rid),
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT
);

-- 실행목표별 월 한 줄 — 실행목표 하나에 달마다 한 줄(1~12월). link: 근거 자료 주소(선택).
CREATE TABLE kpi_month_notes (
    user_id     INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    rid         INTEGER NOT NULL CHECK (rid >= 1),
    month       INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    text        TEXT    NOT NULL CHECK (length(text) BETWEEN 1 AND 200),
    link        TEXT    NULL CHECK (link IS NULL OR length(link) <= 300),
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, year, rid, month),
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT
);

-- 일정별 결과 한 줄 — 일정 하나에 한 줄. link: 근거 자료 주소(선택).
CREATE TABLE kpi_results (
    user_id     INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    sid         INTEGER NOT NULL CHECK (sid >= 1),
    text        TEXT    NOT NULL CHECK (length(text) BETWEEN 1 AND 200),
    link        TEXT    NULL CHECK (link IS NULL OR length(link) <= 300),
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, year, sid),
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT
);

-- 일정 날짜 변경 요청 — 직원이 요청하면 바로 위 관리자(또는 대신 처리하는 인사담당자)가 확인하거나 돌려보낸다. 요청마다 한 줄이 쌓인다.
-- status: PENDING 기다리는 중 · CONFIRMED 확인함 · RETURNED 돌려보냄(이유는 note 에 필수) · CANCELLED 요청한 사람이 취소했거나 수정을 풀면서 닫음.
-- 기다리는 중(PENDING)일 때만 decided_at 이 비어 있다 — 기다리는 상태를 벗어나는 모든 길(확인 · 돌려보내기 · 취소)이 decided_at 을 채운다.
-- decided_by: 처리한 사람. on_behalf_of: 인사담당자가 대신 처리했으면 원래 처리해야 할 관리자.
-- 한 일정에는 기다리는 요청이 하나뿐이다 (아래 부분 유니크 인덱스).
CREATE TABLE kpi_sched_changes (
    id            INTEGER PRIMARY KEY,
    user_id       INTEGER NOT NULL,
    year          INTEGER NOT NULL,
    sid           INTEGER NOT NULL,
    from_due      TEXT    NOT NULL,
    to_due        TEXT    NOT NULL,
    reason        TEXT    NOT NULL CHECK (length(reason) BETWEEN 1 AND 200),
    status        TEXT    NOT NULL CHECK (status IN ('PENDING','CONFIRMED','RETURNED','CANCELLED')),
    note          TEXT    NULL CHECK (note IS NULL OR length(note) <= 200),
    requested_at  TEXT    NOT NULL,
    decided_at    TEXT    NULL,
    decided_by    INTEGER NULL REFERENCES users(id),
    on_behalf_of  INTEGER NULL REFERENCES users(id),
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT,
    CHECK ((status = 'PENDING') = (decided_at IS NULL)),
    CHECK (status <> 'RETURNED' OR note IS NOT NULL),
    CHECK (date(from_due) IS from_due AND date(to_due) IS to_due)
);

CREATE UNIQUE INDEX kpi_sched_one_pending ON kpi_sched_changes(user_id, year, sid) WHERE status = 'PENDING';
