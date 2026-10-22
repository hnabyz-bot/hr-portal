-- 휴대폰 알림 (2026-10-22 인사담당자 — 휴대폰 알림 · 앱 아이콘 숫자, 설계 docs/superpowers/specs/2026-10-22-push-notifications-design.md §8.1).
-- 서버 DB 는 016(연봉계약서)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 17.
-- 번호 규칙: 번호는 merge 순서대로 — 다른 PR 이 먼저 017 을 써서 merge 되면 이 파일 번호를 다음 번호로 바꾼다 (check_schema.py 0번 검사가 막는다).

-- 알림을 받을 휴대폰 — 휴대폰 세션(sessions.kind = 'phone') 하나에 구독 하나. 같은 세션이 다시 켜면 덮어쓴다.
-- 보낼 대상은 이 표가 아니라 세션이 정한다: 해제된 세션 · 30일 지난 세션 · 비활성 직원은 구독이 남아 있어도 보내지 않는다 (backend/push.py targets).
-- 세션 · 직원이 지워지면 구독도 함께 사라진다(지워지는 일이 거의 없지만, 구독이 세션 삭제를 막지 않게).
-- 푸시 서버가 구독이 없어졌다(404 · 410)고 알려 주면 그 줄을 지운다.
-- 글자 기본키는 SQLite 에서 NULL 도 받아 주므로(관례) NOT NULL 을 직접 적는다 — NULL 세션 번호는 외래키 검사도 건너뛴다.
CREATE TABLE push_subscriptions (
    token_hash TEXT    NOT NULL PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    endpoint   TEXT    NOT NULL,   -- 푸시 서버 주소(휴대폰마다 다른 긴 주소) — 로그 · 화면에 내지 않는다
    p256dh     TEXT    NOT NULL,   -- 휴대폰의 암호화용 공개키 (base64url)
    auth       TEXT    NOT NULL,   -- 휴대폰의 암호화용 비밀값 (base64url)
    created_at TEXT    NOT NULL
);
CREATE INDEX idx_push_subscriptions_user ON push_subscriptions(user_id);

-- 아침 9시 묶음을 돌린 날 — 같은 날 두 번 돌지 않는다 (날짜가 기본키라 두 번째 넣기는 거부된다).
CREATE TABLE push_morning (
    day    TEXT    NOT NULL PRIMARY KEY,   -- 한국 날짜 YYYY-MM-DD
    people INTEGER NOT NULL,      -- 보낸 사람 수
    ran_at TEXT    NOT NULL
);
