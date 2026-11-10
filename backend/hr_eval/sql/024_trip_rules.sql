-- 경비 산출기의 산출 기준(연비 · 전기차 충전 단가 · 활동비)을 HR 이 화면에서 바꾼다 (2026-11-10 인사담당자 결정) — 판 24.
-- 적용 시작일마다 한 줄. 출장일자가 그날 이후인 계산은 그 줄의 기준을 쓴다(가장 늦은 시작일 ≤ 출장일자).
-- 줄이 하나도 없으면 backend/app_config.py 의 값(처음 기준)을 쓴다. 숫자 모양은 backend/trip_rules.py clean 이 본다.
-- 잘못 넣은 줄은 HR 이 지울 수 있다 — 누가 언제 무엇을 바꾸고 지웠는지는 감사로그 TRIP_RULES_SET · TRIP_RULES_REMOVED.
CREATE TABLE trip_rules (
    start_date  TEXT    NOT NULL PRIMARY KEY CHECK (date(start_date) IS start_date),  -- 'YYYY-MM-DD' 실제 있는 날만 (2027-02-30 은 date() 가 바꿔 버려 거부)
    data        TEXT    NOT NULL CHECK (json_valid(data) AND json_type(data) = 'object'),
    updated_at  TEXT    NOT NULL,
    updated_by  INTEGER NOT NULL REFERENCES users(id)
);
