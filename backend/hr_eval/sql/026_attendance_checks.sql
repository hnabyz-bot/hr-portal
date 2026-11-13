-- 근태 서류 확인 (2026-11-13 인사담당자 결정 — 근태 현황 2단계) — 판 26.
-- 챙길 사람(미인식 · 지각 · 서류 누락)은 그날 근태 자료(attendance_days)에서 서버가 고른다 — 이 표에는 체크 기록만 둔다.
-- 한 사람 · 하루 한 줄 (근태 파일의 팀 이름 · 이름 그대로). 그날 자료가 정정되어 챙길 일이 없어지면 이 줄은 쓰이지 않는다.
--   self_at  = 본인이 "제출했어요"를 누른 때 (9시 알림이 멈춘다 — 확인은 아니다)
--   done_*   = 제출 확인. MANUAL = 책임자 · 본부장 · HR 이 눌렀다(done_by) / AUTO = 늦게 낸 서류를 Cowork 가 하이웍스에서 찾았다(done_by 없음)
--   비고에 '소명서 제출' · '결재 완료'가 있는 날은 줄 없이도 확인된 것으로 본다(backend/attendance.py DONE_NOTE).
CREATE TABLE attendance_checks (
    date      TEXT    NOT NULL CHECK (date(date) IS date),
    team      TEXT    NOT NULL CHECK (length(team) BETWEEN 1 AND 40),
    name      TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 40),
    self_at   TEXT    NULL,
    done_at   TEXT    NULL,
    done_by   INTEGER NULL REFERENCES users(id),
    done_how  TEXT    NULL CHECK (done_how IN ('MANUAL', 'AUTO')),
    PRIMARY KEY (date, team, name),
    CHECK ((done_at IS NULL) = (done_how IS NULL)),
    CHECK ((done_how IS 'MANUAL') = (done_by IS NOT NULL))
);
