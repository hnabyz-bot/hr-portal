-- 카드 그림 번호 범위를 1~4 에서 1~20 으로 넓힌다 (2026-10-27 인사담당자 — 생일 새 그림 5 · 6, 입사 기념 연차 숫자 그림 5~9) — 판 22.
-- 그림이 더 늘어도 판을 또 올리지 않게 20 까지 열어 둔다. 종류마다 고를 수 있는 번호는 서버 cards.arts_for 가 정한다(표는 범위만 본다).
-- 이미 보낸 카드는 저장된 번호 그대로 둔다(옛 그림 생일 1 · 2, 입사 기념 3 · 4 도 그대로 보인다 — 파일을 지우지 않는다).
-- SQLite 는 CHECK 를 고칠 수 없어 표를 새로 만들어 옮긴다(SQLite 문서 ALTER TABLE 의 "다른 표 고치기" 순서): 새 표 → 줄 그대로(id 포함) → 옛 표 지움 → 새 표 이름을 cards 로 → 색인 · 트리거 다시.
--   cards 를 외래키로 가리키는 표가 없어 외래키를 끄지 않아도 된다(옛 표를 지울 때 걸리는 외래키가 없다). 파일 하나가 한 트랜잭션이라(db.py) 중간에 실패하면 옛 표 그대로다.
--   AUTOINCREMENT 순번(sqlite_sequence)은 옛 표의 값을 이어받는다 — 마지막 카드를 지운 뒤라도 그 번호를 다시 쓰지 않게. 옛 표를 지우면 그 순번 줄도 지워지고, 이름을 바꾸면 새 표의 순번 줄 이름도 cards 로 바뀐다.
-- 나머지(칸 12개와 순서 · 제약 · 색인 2개 · 내용 칸 고치기를 막는 트리거와 문구 · 직원 외래키 ON DELETE 없음)는 021 과 같다 — 칸 설명은 021_cards.sql.
CREATE TABLE cards_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id    INTEGER NOT NULL REFERENCES users(id),
    recipient_id INTEGER NOT NULL REFERENCES users(id),
    kind         TEXT    NOT NULL CHECK (kind IN ('cheer', 'praise', 'thanks', 'comfort')),
    reason       TEXT    NULL CHECK (reason IS NULL OR reason IN ('birthday', 'welcome', 'anniv', 'joy')),
    art          NOT NULL CHECK (typeof(art) = 'integer' AND art BETWEEN 1 AND 20),
    body         BLOB    NOT NULL CHECK (typeof(body) = 'blob' AND length(body) > 28),
    sent_on      TEXT    NOT NULL CHECK (sent_on GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]' AND date(sent_on) IS sent_on),
    created_at   TEXT    NOT NULL,
    read_at      TEXT    NULL,
    thanked_at   TEXT    NULL,
    hidden_at    TEXT    NULL,
    UNIQUE (sender_id, recipient_id, sent_on),
    CHECK (sender_id <> recipient_id),
    CHECK ((kind = 'cheer') = (reason IS NOT NULL)),
    CHECK (thanked_at IS NULL OR read_at IS NOT NULL)
);
INSERT INTO cards_new (id, sender_id, recipient_id, kind, reason, art, body, sent_on, created_at, read_at, thanked_at, hidden_at)
    SELECT id, sender_id, recipient_id, kind, reason, art, body, sent_on, created_at, read_at, thanked_at, hidden_at FROM cards;
DELETE FROM sqlite_sequence WHERE name = 'cards_new';
INSERT INTO sqlite_sequence (name, seq) SELECT 'cards_new', seq FROM sqlite_sequence WHERE name = 'cards';
DROP TABLE cards;
ALTER TABLE cards_new RENAME TO cards;
CREATE INDEX idx_cards_recipient ON cards(recipient_id, created_at);
CREATE INDEX idx_cards_sender ON cards(sender_id, created_at);

CREATE TRIGGER trg_cards_content_readonly BEFORE UPDATE OF sender_id, recipient_id, kind, reason, art, body, sent_on, created_at ON cards
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 보낸 카드는 고칠 수 없어요.');
END;
