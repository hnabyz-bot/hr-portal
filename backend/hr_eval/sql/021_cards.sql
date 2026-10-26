-- 축하 · 칭찬 카드 (2026-10-26 인사담당자 — 설계 docs/superpowers/specs/2026-10-26-praise-cards-design.md 8장).
-- 서버 DB 는 020(인사평가)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 21.
-- 번호 규칙: 번호는 merge 순서대로 — 다른 PR 이 먼저 021 을 써서 merge 되면 이 파일 번호를 다음 번호로 바꾼다 (check_schema.py 0번 검사가 막는다).
-- 카드 한 장 = 한 줄. 종류(kind): cheer 축하 · praise 칭찬 · thanks 감사 · comfort 위로. 소식 이유(reason)는 축하일 때만 있다 — birthday 생일 · welcome 새 식구 · anniv 입사 기념 · joy 경사 (그 밖의 종류는 비어 있다).
-- 같은 사람에게 하루 한 장(UNIQUE — 종류와 상관없이) · 본인에게는 못 보낸다. sent_on 은 한국 날짜 YYYY-MM-DD — 모양이면서 실제 있는 날이어야 한다(date() 가 2월 30일을 3월 2일로 바꿔 돌려주므로 IS 로 되비교, 016 과 같다).
-- art(그림 번호 1~4)는 일부러 형을 적지 않는다(형 없음) — 013 · 016 의 year 와 같은 이유: INTEGER 로 적으면 글자 '1' 을 정수로 바꿔 받아 주고, 형이 없어야 글자 · 실수 · NULL 이 CHECK 에서 그대로 거부된다.
-- 글(body)은 서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다 (급여명세서 · 연봉계약서와 같은 열쇠). 잠근 글 = 12바이트 nonce + 내용 + 16바이트 태그 → 29바이트 이상 BLOB 만 받는다. 나머지 칸은 잠그지 않는다.
-- 보낸 뒤에는 내용 칸(보낸 · 받는 사람 · 종류 · 이유 · 그림 · 글 · 날짜 · 만든 때)을 고칠 수 없다(트리거 — 같은 값으로 다시 쓰는 UPDATE 도 막는다). 고치는 칸은 읽음(read_at) · 고마워요(thanked_at) · 숨김(hidden_at)뿐.
--   고마워요는 읽은 뒤에만(thanked_at 이 있으면 read_at 도 있다). 숨기기를 풀 때는 hidden_at 을 비운다. 감사로그는 남기지 않는다 — 누가 누구에게 보냈는지가 감사로그로 새지 않게(설계 10장 4번).
-- 직원을 지우지 못하게(RESTRICT) 한다 — 퇴직자는 지우지 않고 비활성으로 둔다. 카드를 지우는 길은 서버에 만들지 않는다(보관)지만, 표가 DELETE 를 막지는 않는다(검사 정리용).
CREATE TABLE cards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id    INTEGER NOT NULL REFERENCES users(id),
    recipient_id INTEGER NOT NULL REFERENCES users(id),
    kind         TEXT    NOT NULL CHECK (kind IN ('cheer', 'praise', 'thanks', 'comfort')),
    reason       TEXT    NULL CHECK (reason IS NULL OR reason IN ('birthday', 'welcome', 'anniv', 'joy')),
    art          NOT NULL CHECK (typeof(art) = 'integer' AND art BETWEEN 1 AND 4),
    body         BLOB    NOT NULL CHECK (typeof(body) = 'blob' AND length(body) > 28),   -- 잠근 글
    sent_on      TEXT    NOT NULL CHECK (sent_on GLOB '20[0-9][0-9]-[01][0-9]-[0-3][0-9]' AND date(sent_on) IS sent_on),   -- 보낸 날 (한국)
    created_at   TEXT    NOT NULL,
    read_at      TEXT    NULL,   -- 받은 사람이 처음 읽은 때
    thanked_at   TEXT    NULL,   -- 받은 사람이 [고마워요]를 누른 때
    hidden_at    TEXT    NULL,   -- 받은 사람이 숨긴 때 (비우면 다시 보인다)
    UNIQUE (sender_id, recipient_id, sent_on),
    CHECK (sender_id <> recipient_id),
    CHECK ((kind = 'cheer') = (reason IS NOT NULL)),
    CHECK (thanked_at IS NULL OR read_at IS NOT NULL)
);
CREATE INDEX idx_cards_recipient ON cards(recipient_id, created_at);
CREATE INDEX idx_cards_sender ON cards(sender_id, created_at);

CREATE TRIGGER trg_cards_content_readonly BEFORE UPDATE OF sender_id, recipient_id, kind, reason, art, body, sent_on, created_at ON cards
BEGIN
    SELECT RAISE(ABORT, 'READ_ONLY: 보낸 카드는 고칠 수 없어요.');
END;
