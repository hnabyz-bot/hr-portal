-- 급여명세서 「HR에 문의」 받는 사람 (2026-10-26 인사담당자 — 직원이 명세서에서 [HR에 문의]를 누르면 하이웍스 메일 쓰기 창의 받는 사람으로 들어갈 HR 1명).
-- 서버 DB 는 018(급여 비밀번호)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py) — 판 19.
-- 번호 규칙: 번호는 merge 순서대로 — 다른 PR 이 먼저 019 를 써서 merge 되면 이 파일 번호를 다음 번호로 바꾼다 (check_schema.py 0번 검사가 막는다).
-- 한 줄짜리 표(id = 1). 줄이 없으면 활성 HR 전원이 받는다 (backend/payslip.py ask_to). 문의 글은 저장하지 않는다 — 메일로 오간다.
-- 고른 사람이 비활성이 되거나 HR 권한이 빠지면 아래 트리거가 줄을 지운다 — 나중에 다시 켜져도 HR 이 모르는 사이에 받는 사람으로 돌아오지 않는다.
--   (구성원 설정 저장 · 명단 동기화 · 서버 명령 어느 길로 바뀌어도 같다. 그 변경 자체는 각자의 감사로그 MEMBER_UPDATED 등에 남는다)
-- 누가 언제 바꿨는지는 감사로그 PAY_ASK_TO_SET. 직원을 지우지 못하게(RESTRICT) 한다 — 퇴직자는 지우지 않고 비활성으로 둔다.
CREATE TABLE pay_ask_to (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    user_id     INTEGER NOT NULL REFERENCES users(id),
    updated_at  TEXT    NOT NULL,
    updated_by  INTEGER NOT NULL REFERENCES users(id)
);
CREATE TRIGGER trg_pay_ask_to_inactive AFTER UPDATE OF is_active ON users
WHEN NEW.is_active = 0
BEGIN
    DELETE FROM pay_ask_to WHERE user_id = NEW.id;
END;
CREATE TRIGGER trg_pay_ask_to_not_hr AFTER DELETE ON user_roles
WHEN OLD.role = 'HR_ADMIN'
BEGIN
    DELETE FROM pay_ask_to WHERE user_id = OLD.user_id;
END;
