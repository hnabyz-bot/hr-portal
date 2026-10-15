-- 감사로그: HR 이 대표이사의 일을 대신 했을 때 그 대표이사 (로그인 설계 §6 "HR에 맡기기").
-- actor_user_id 는 실제로 입력한 HR 이다. 대신 한 일이 아니면 비어 있다.
-- 서버 DB 는 001 까지 넣은 채로 있으므로 001 을 고치지 않고 이 파일을 더한다 (hr_eval/sql/db.py).
ALTER TABLE audit_logs ADD COLUMN on_behalf_of INTEGER NULL REFERENCES users(id) ON DELETE SET NULL;
