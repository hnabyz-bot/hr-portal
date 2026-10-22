-- X-ray 특공대 오픈시간을 HR 이 탕비실에서 바꾼다 (2026-10-22 인사담당자 결정).
-- 서버 DB 는 014(KPI 2단계 ②)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 15.
-- (처음엔 014_game_hours.sql 이었는데 KPI 2단계 ② 014_kpi_review.sql 이 먼저 merge 되어 015 로 바꿨다.)
-- 한 줄짜리 표: 평일 시간대 · 주말 · 하루 종일 여는 날 · 잠시 닫기를 JSON 하나로 (모양은 backend/game.py hours_clean 이 본다).
-- 줄이 없으면 game.py HOURS_DEFAULT (예전 오픈시간 + 공휴일) 를 쓴다. 누가 언제 바꿨는지는 감사로그 GAME_HOURS_SET.
CREATE TABLE game_hours (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    data        TEXT    NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    updated_at  TEXT    NOT NULL,
    updated_by  INTEGER NOT NULL REFERENCES users(id)
);
