-- 근태 현황 (2026-11-13 인사담당자 결정) — 판 25.
-- HR PC 의 데일리 근태 대시보드 자료를 하루 한 줄로 둔다. data = {date, teams:[{team, summary, members:[…]}]}
-- (서버가 아는 칸만 남겨 저장 — backend/attendance.py clean). 같은 날을 다시 보내면 덮어쓴다(정정).
-- 하루 약 44KB · 1년 약 11MB. 보는 범위(본부 · 팀)는 읽을 때 서버가 정한다 — 이 표에는 사람 번호가 없다.
CREATE TABLE attendance_days (
    date         TEXT NOT NULL PRIMARY KEY CHECK (date(date) IS date),  -- 'YYYY-MM-DD' 실제 있는 날만
    data         TEXT NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    uploaded_at  TEXT NOT NULL
);
