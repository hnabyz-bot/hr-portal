-- X-ray 특공대 게임을 인사포털 로그인에 붙인다 (2026-10-20 인사담당자 결정):
-- 로그인해야 게임 · 실제 직원 랭킹(실명 + 팀) · 동료 선물 · 게임 기록(출동권·칭호·모자 …)을 사람별로 → PC 와 휴대폰이 같은 기록.
-- 서버 DB 는 007 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).

-- 게임 진행 — 게임 화면 저장소(xrsq_*)의 칸 이름 → 값(JSON 글자) 그대로. 본인만 읽고 쓴다.
-- version: 워크스페이스와 같다 — 화면이 아는 판과 다르면 다른 기기가 먼저 저장한 것이라 덮어쓰지 않는다.
CREATE TABLE game_saves (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    data        TEXT    NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    version     INTEGER NOT NULL CHECK (version >= 1),
    updated_at  TEXT    NOT NULL
);

-- 한 사람 한 주에 한 줄: 그 주 최고점. 한 주 = 한국 시간 월요일 0시부터.
-- 역대 최고 = 이 표의 가장 높은 점수, 명예의 전당 = 지난 주들의 1위 (같은 점수면 먼저 낸 사람)
CREATE TABLE game_scores (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    week        TEXT    NOT NULL CHECK (date(week) IS week AND strftime('%w', week) = '1'),   -- 그 주 월요일
    score       INTEGER NOT NULL CHECK (score BETWEEN 1 AND 100000000),
    at          TEXT    NOT NULL,
    PRIMARY KEY (user_id, week)
);
CREATE INDEX idx_game_scores_week ON game_scores(week, score DESC);

-- 동료 선물 = 출동권 1장. 보내기는 하루 3명 · 한 사람에게 하루 1장, 받기는 하루 5장, 7일 지나면 선물함에서 안 보인다.
CREATE TABLE game_gifts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    to_user     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    day         TEXT    NOT NULL CHECK (date(day) IS day),   -- 보낸 날 (한국 날짜)
    created_at  TEXT    NOT NULL,
    claimed_on  TEXT    NULL CHECK (claimed_on IS NULL OR date(claimed_on) IS claimed_on),   -- 받은 날 (한국 날짜)
    CHECK (to_user <> from_user),
    UNIQUE (from_user, to_user, day)
);
CREATE INDEX idx_game_gifts_to ON game_gifts(to_user, claimed_on);
