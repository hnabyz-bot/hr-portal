-- KPI 2단계 ② (2026-10-21): KPI 현황 탭 — 바로 위 관리자가 달마다 점검하고, 확인 메모를 남기고, 진행이 늦은 목표를 알람으로 본다.
-- 월간 확인 · 확인 메모 · 알람 끄기는 그 사람 · 그해 계획서가 있을 때만 쓴다 (010 의 kpi_plans 가 부모, 012 와 같다).
-- 서버 DB 는 013(원천징수영수증)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 14.
-- (처음엔 013 이었으나 원천징수영수증 #168 이 013 을 먼저 merge 해서 014 로 옮겼다.)
-- 세 표(kpi_month_checks · kpi_memos · kpi_mutes)는 계획서(kpi_plans)에 붙어 있다: 기록이 달린 계획서는 지울 수 없다 (RESTRICT).
-- 글쓴이(author_id · checked_by)도 지울 수 없다 (RESTRICT) — 누가 썼는지 알 수 없게 되면 안 된다.
-- 글자 수 한도(확인 메모 · 답글 200, 알람 끄기 '직접 적기' 50)는 화면 · 서버와 같게 DB 에서도 막는다. 경영계획의 목표 60 · 내용 300 · 20개까지는 서버가 지킨다.
-- 감사로그: 월간 확인 · 알람 기준 · 경영계획의 처리만 남는다 (서버 코드가 한다 — 경영계획은 목표 문장 없이 개수만).
--   확인 메모 · 답글 · 알람 끄기는 남기지 않는다 (내용 문장 · 본인 설정 — 012 의 진행 기록과 같은 이유).

-- 해마다 알람 기준 · 경영계획 — kpi_years(해마다 한 줄)에 칸을 더한다.
-- alarm_gap: 진행이 기대보다 이만큼(퍼센트포인트) 이상 뒤처지면 '늦음' 알람. 1~100, 기본 20. 인사담당자가 해마다 정한다.
--   (기본값이 있어서 이미 있던 해 줄도 20 이 된다. 기간 줄이 없는 해는 서버가 20 으로 본다.)
-- biz_plan: 그해 경영계획 — [{"goal": 목표, "detail": 내용}] 목록(JSON). 아직 안 넣었으면 비어 있다. 대표이사 · 본부장 · 인사담당자가 본다.
ALTER TABLE kpi_years ADD COLUMN alarm_gap INTEGER NOT NULL DEFAULT 20 CHECK (alarm_gap BETWEEN 1 AND 100);
ALTER TABLE kpi_years ADD COLUMN biz_plan TEXT NULL CHECK (biz_plan IS NULL OR (json_valid(biz_plan) AND json_type(biz_plan) = 'array'));

-- 월간 확인 — 바로 위 관리자가 "이 달 이 사람 점검 끝"을 누르면 한 줄. 한 사람 한 해 한 달에 한 줄.
-- checked_by: 확인한 사람(바로 위 관리자 본인). 확인한 달의 기록을 고치면 서버가 그 달 확인을 지운다.
CREATE TABLE kpi_month_checks (
    user_id     INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    checked_at  TEXT    NOT NULL,
    checked_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    PRIMARY KEY (user_id, year, month),
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT
);

-- 확인 메모 — 바로 위 관리자가 그 사람에게 남기는 짧은 글. 몇 개든 쌓인다.
-- author_id: 쓴 사람(본인에게는 못 쓴다). edited_at: 쓴 날 고쳤으면 그 때.
-- ack_at: 본인이 '확인했습니다'를 누른 때. seen_at: 쓴 사람이 본인 답글을 마지막으로 본 때(새 답글 표시에 쓴다).
CREATE TABLE kpi_memos (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    year        INTEGER NOT NULL,
    author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    text        TEXT    NOT NULL CHECK (length(text) BETWEEN 1 AND 200),
    created_at  TEXT    NOT NULL,
    edited_at   TEXT    NULL,
    ack_at      TEXT    NULL,
    seen_at     TEXT    NULL,
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT,
    CHECK (author_id <> user_id)
);

CREATE INDEX kpi_memos_owner ON kpi_memos(user_id, year);

-- 확인 메모의 답글 — 본인(owner)과 메모를 쓴 사람(writer)이 주고받는다. 메모를 지우면 답글도 함께 지워진다.
CREATE TABLE kpi_memo_replies (
    id          INTEGER PRIMARY KEY,
    memo_id     INTEGER NOT NULL REFERENCES kpi_memos(id) ON DELETE CASCADE,
    author_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    who         TEXT    NOT NULL CHECK (who IN ('owner','writer')),
    text        TEXT    NOT NULL CHECK (length(text) BETWEEN 1 AND 200),
    created_at  TEXT    NOT NULL
);

-- 메모 하나의 답글을 빨리 찾는다 (메모를 읽을 때 · 메모를 지우면서 답글을 함께 지울 때).
CREATE INDEX kpi_memo_replies_memo ON kpi_memo_replies(memo_id);

-- 알람 끄기 — 본인이 늦은 목표의 알람을 끄고 이유를 고른다. 실행목표 하나에 한 줄.
-- why: year 올해 안 · month 이번 달까지 · wait 기다리는 중 · etc 직접 적기 (화면 KV_MUTE 의 열쇠와 같다).
-- text: '직접 적기'(etc)일 때만 있다. until_month: 이 달 말일까지 끈다(1~12). '올해 안'은 12월까지.
CREATE TABLE kpi_mutes (
    user_id      INTEGER NOT NULL,
    year         INTEGER NOT NULL,
    rid          INTEGER NOT NULL CHECK (rid >= 1),
    why          TEXT    NOT NULL CHECK (why IN ('year','month','wait','etc')),
    text         TEXT    NULL CHECK (text IS NULL OR length(text) BETWEEN 1 AND 50),
    until_month  INTEGER NOT NULL CHECK (until_month BETWEEN 1 AND 12),
    set_at       TEXT    NOT NULL,
    PRIMARY KEY (user_id, year, rid),
    FOREIGN KEY (user_id, year) REFERENCES kpi_plans(user_id, year) ON DELETE RESTRICT,
    CHECK ((why = 'etc') = (text IS NOT NULL)),
    CHECK (why <> 'year' OR until_month = 12)
);
