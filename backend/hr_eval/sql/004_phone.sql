-- 휴대폰 등록 (로그인 설계 §13.1, 2026-10-19).
-- 서버 DB 는 003 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).

-- 세션 종류: pc = 하이웍스 메뉴로 들어온 세션(마지막 사용 후 4시간 · 최대 12시간, 그 창에서만)
--            phone = PC 에서 띄운 QR 로 등록한 휴대폰(마지막 사용 후 30일, 쓸 때마다 연장). 한 사람에 한 대
ALTER TABLE sessions ADD COLUMN kind   TEXT NOT NULL DEFAULT 'pc' CHECK (kind IN ('pc', 'phone'));
ALTER TABLE sessions ADD COLUMN device TEXT NULL;   -- 휴대폰 이름(아이폰 · 갤럭시 · 안드로이드) — User-Agent 로 짐작

-- QR 에 담는 1회용 값. 원본은 저장하지 않는다(SHA-256 만). 3분, 한 번만. 한 사람에 하나(새로 띄우면 앞의 것은 지운다)
CREATE TABLE phone_codes (
    code_hash  TEXT    PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL,
    expires_at TEXT    NOT NULL,
    used_at    TEXT    NULL
);
