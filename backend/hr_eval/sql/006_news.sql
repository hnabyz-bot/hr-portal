-- 홈 "이번 주 소식" (2026-10-19 인사담당자 결정).
-- 서버 DB 는 005 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).
-- 생일·생일 알림은 하이웍스 명단 동기화(roster_sync)가 건드리지 않는다 — HR 이 넣고, 알림은 본인(또는 본인 요청으로 HR)이 고른다.

ALTER TABLE users ADD COLUMN birthday TEXT NULL     -- 'MM-DD'. 연도는 저장하지 않는다 (생일 축하에는 월·일만 쓴다)
    CHECK (birthday IS NULL OR (birthday GLOB '[0-1][0-9]-[0-3][0-9]' AND date('2000-' || birthday) IS '2000-' || birthday));
ALTER TABLE users ADD COLUMN birthday_public INTEGER NOT NULL DEFAULT 1 CHECK (birthday_public IN (0,1));  -- 생일 알림. 처음엔 켜짐

-- 경사·조사 — HR 이 본인(또는 가족) 동의를 받고 올린다. 게시 종료일이 지나면 화면에서 내려간다. 내리기는 지우지 않고 표시만.
CREATE TABLE celeb_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    kind        TEXT    NOT NULL CHECK (kind IN ('joy','condol')),   -- 경사 · 조사
    label       TEXT    NOT NULL CHECK (length(trim(label)) BETWEEN 1 AND 30),   -- 결혼 · 부친상 …
    until_date  TEXT    NOT NULL CHECK (date(until_date) IS until_date),
    created_by  INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    created_at  TEXT    NOT NULL,
    removed_at  TEXT    NULL
);
CREATE INDEX idx_celeb_events_until ON celeb_events(until_date);
