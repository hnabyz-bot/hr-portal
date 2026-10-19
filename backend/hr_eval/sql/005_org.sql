-- 조직도 실제 명단 (2026-10-19 인사담당자 결정).
-- 서버 DB 는 004 까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).
-- 아래 칸은 하이웍스 명단 동기화(roster_sync)가 건드리지 않는다 — HR 이 비상연락망 파일로 넣거나 본인·HR 이 고른다.

ALTER TABLE users ADD COLUMN title    TEXT NULL;   -- 직급(호칭). 비상연락망 "직책" 칸 — 프로·리더·팀장·본부장… 화면 표시만, 권한(user_roles)과 무관
ALTER TABLE users ADD COLUMN ext      TEXT NULL;   -- 내선 (비상연락망). 없으면 하이웍스 회사 전화(phone)를 보여 준다
ALTER TABLE users ADD COLUMN hr_mobile TEXT NULL;  -- 비상연락망 연락처 — 있으면 하이웍스 휴대폰(mobile)보다 먼저 보인다
ALTER TABLE users ADD COLUMN hr_email TEXT NULL;   -- 비상연락망 메일 — 있으면 하이웍스 메일보다 먼저 보인다(조직도 표시만. 명단 연결은 하이웍스 메일·번호)
ALTER TABLE users ADD COLUMN mobile_public INTEGER NOT NULL DEFAULT 1 CHECK (mobile_public IN (0,1));  -- 본인이 고름. 처음엔 공개
ALTER TABLE users ADD COLUMN org_hidden    INTEGER NOT NULL DEFAULT 0 CHECK (org_hidden IN (0,1));     -- HR 이 조직도에서 뺌 (DB 에서 지우지 않음)
