-- 워크스페이스(나의 업무 · 나의 일상)를 서버에 사람별로 (2026-10-19 인사담당자 — PC 와 휴대폰이 같은 내용).
-- 서버 DB 는 006 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).
-- 목록 하나를 통째로 JSON 으로 둔다 — 화면(wsTasks · rtData)이 쓰던 모양 그대로. 본인만 읽고 쓴다(HR 도 못 본다).
-- version: 저장할 때마다 1씩. 화면이 아는 판과 다르면 다른 기기가 먼저 고친 것이라 덮어쓰지 않는다.

CREATE TABLE workspace_data (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind        TEXT    NOT NULL CHECK (kind IN ('tasks','routines')),   -- 나의 업무 · 나의 일상
    data        TEXT    NOT NULL CHECK (json_valid(data) AND json_type(data) = 'array'),
    version     INTEGER NOT NULL CHECK (version >= 1),
    updated_at  TEXT    NOT NULL,
    PRIMARY KEY (user_id, kind)
);
