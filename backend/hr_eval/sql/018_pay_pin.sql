-- 급여·계약 비밀번호 잠금 (2026-10-22 인사담당자 — 급여·계약 메뉴 비밀번호, 설계 docs/superpowers/specs/2026-10-22-pay-pin-lock-design.md).
-- 서버 DB 는 017(휴대폰 알림)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 18.
-- 번호 규칙: 번호는 merge 순서대로 — 다른 PR 이 먼저 018 을 써서 merge 되면 이 파일 번호를 다음 번호로 바꾼다 (check_schema.py 0번 검사가 막는다).

-- 직원별 급여·계약 비밀번호 (6자리 숫자) — 사람마다 한 줄. 원문은 어디에도 두지 않고 해시만 둔다 (backend/paypin.py: HMAC-SHA256, 열쇠 = HR_PAY_KEY 에서 뽑은 값).
-- fails 는 맞히기 전까지 연달아 틀린 횟수 (맞으면 0). 5가 되면 잠김 — locked_at 이 그 시각이고, 새 창으로는 되돌릴 수 없다.
-- 잠김을 푸는 길은 HR 의 초기화(줄을 지움)뿐이다. fails 와 locked_at 이 어긋난 줄(5번인데 시각 없음 · 5번 아닌데 시각 있음)은 표가 거부한다.
-- 직원이 지워지면 줄도 함께 사라진다. 해시 · 소금이 비면 아무 번호나 맞다고 나올 수 있으므로 NOT NULL 을 적는다.
-- WITHOUT ROWID: INTEGER PRIMARY KEY 는 SQLite 의 줄 번호 자리라 NULL 을 넣으면 "다음 번호"를 몰래 채운다 (그 번호가 다른 직원이면 그 사람 줄이 된다). 이 방식이면 NOT NULL 이 그대로 지켜진다.
CREATE TABLE pay_pins (
    user_id   INTEGER NOT NULL PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    pin_hash  TEXT    NOT NULL,   -- HMAC-SHA256(열쇠, 소금 + 번호) 16진수 64자
    salt      TEXT    NOT NULL,   -- 사람마다 다른 무작위 값 (16진수 32자)
    set_at    TEXT    NOT NULL,
    fails     INTEGER NOT NULL DEFAULT 0 CHECK (fails BETWEEN 0 AND 5),
    locked_at TEXT    NULL,
    CHECK ((locked_at IS NULL) = (fails < 5))
) WITHOUT ROWID;

-- 비밀번호로 풀린 세션의 마지막 급여·계약 사용 시각 — 풀린 뒤 6분 동안 쓰지 않으면 다시 묻는다. NULL 이면 잠긴 세션 (처음 · 잠금 · 초기화 · 틀려서 잠김).
ALTER TABLE sessions ADD COLUMN pay_used_at TEXT NULL;
