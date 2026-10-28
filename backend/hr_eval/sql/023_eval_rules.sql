-- 연말 평가의 등급 비율을 인사담당자가 해마다 정하고, 대표이사가 묶음 정원을 늘려 줄 수 있게 하고, 가점을 없앤다 (2026-10-27 인사담당자 결정 — 평가 규칙 손질 S1 · S2 · S3) — 판 23.
-- 서버 DB 는 022(카드 그림 번호 범위)까지 넣은 채로 있으므로 이 파일을 더한다 (hr_eval/sql/db.py).
-- 번호 규칙: 번호는 merge 순서대로 — 다른 PR 이 먼저 023 을 써서 merge 되면 이 파일 번호를 다음 번호로 바꾼다 (check_schema.py 0번 검사가 막는다).
-- 표를 다시 만들지 않는다 — 020 의 ev_rounds · ev_pools 에 칸만 더한다(ALTER TABLE ... ADD COLUMN). 이미 있는 줄은 아래 기본값을 받아 지금 규칙 그대로다.
--   한 칸만 보는 규칙(범위)은 칸에 CHECK 로, 여러 칸에 걸친 규칙(비율 합 · 늘렸으면 이유)은 트리거로 둔다 — ADD COLUMN 은 다른 칸을 보는 CHECK 를 달 수 없다.
--   트리거는 새 줄(BEFORE INSERT)과 고치기(BEFORE UPDATE OF 그 칸들) 둘 다 막는다. 서버가 회차를 INSERT ... ON CONFLICT (year) DO UPDATE 한 문장으로 저장하므로
--   그 문장의 새 줄 길(INSERT 트리거)과 덮어쓰는 길(UPDATE 트리거) 어느 쪽으로 가도 합 검사를 지난다.

-- [S1 등급 비율] 회차(ev_rounds) — 그해 묶음 인원에 곱하는 등급별 비율(%, 정수) 세 칸. 모든 묶음(본부 팀원 · 전사 팀장 · 직속부서 팀원)에 같은 비율이다.
--   ratio_sa: S·A 합계 정원 비율 · ratio_b: B 비율 · ratio_d: D 비율. C 는 나머지라 칸이 없다. 처음 값은 10 · 10 · 10 (지금 규칙 그대로).
--   칸마다 0~70, 세 칸 합은 70 이하 — 서버가 인원 × 비율을 반올림해도 정원 합이 묶음 인원을 넘지 않게 하는 한계다(5명 이상 묶음). 4명 이하 묶음은 비율을 쓰지 않는다(서버 규칙).
--   평가를 시작한 뒤에는 비율을 바꿀 수 없다는 규칙은 서버가 지킨다(시작 뒤에도 마감일 · 기준 점수는 저장되고 같은 비율은 다시 저장돼야 해서 표의 규칙으로 두지 않는다).
ALTER TABLE ev_rounds ADD COLUMN ratio_sa INTEGER NOT NULL DEFAULT 10 CHECK (ratio_sa BETWEEN 0 AND 70);
ALTER TABLE ev_rounds ADD COLUMN ratio_b  INTEGER NOT NULL DEFAULT 10 CHECK (ratio_b BETWEEN 0 AND 70);
ALTER TABLE ev_rounds ADD COLUMN ratio_d  INTEGER NOT NULL DEFAULT 10 CHECK (ratio_d BETWEEN 0 AND 70);

CREATE TRIGGER trg_ev_rounds_ratio_ins BEFORE INSERT ON ev_rounds
WHEN NEW.ratio_sa + NEW.ratio_b + NEW.ratio_d > 70
BEGIN
    SELECT RAISE(ABORT, 'RATIO_SUM: 등급 비율 세 칸 합은 70 이하여야 합니다.');
END;

CREATE TRIGGER trg_ev_rounds_ratio_upd BEFORE UPDATE OF ratio_sa, ratio_b, ratio_d ON ev_rounds
WHEN NEW.ratio_sa + NEW.ratio_b + NEW.ratio_d > 70
BEGIN
    SELECT RAISE(ABORT, 'RATIO_SUM: 등급 비율 세 칸 합은 70 이하여야 합니다.');
END;

-- [S2 대표이사 정원 늘려 주기] 묶음 기록(ev_pools) — 대표이사가 그 묶음의 그해 정원을 늘린 기록. 묶음마다 한 번에 하나(늘리면 앞의 것을 덮어쓴다).
--   extra_sa: S·A 정원을 늘린 인원 · extra_b: B 정원을 늘린 인원 (각각 0~5, 처음 0). 늘린 인원이 묶음 인원을 넘는지, 점수 기준 · 연차 규칙을 넘지 않는지는 서버가 본다.
--   extra_reason: 늘린 이유(1~200자) — 인원이 하나라도 있으면 있어야 한다(트리거). 둘 다 0 이면 비어 있어도 된다. 이유 글은 감사로그에 넣지 않는다(돌려보낸 이유 back_reason 과 같다).
--   extra_at · extra_by: 늘린 때 · 사람. extra_behalf: 인사담당자가 대표이사 몫을 대신했으면 원래 대표이사(ceo_behalf · back_behalf 와 같다). 사람 칸은 지울 수 없다(RESTRICT).
--   ALTER TABLE 로 외래키 칸을 더할 때는 기본값이 NULL 이어야 해서 사람 칸은 모두 NULL 로 시작한다.
ALTER TABLE ev_pools ADD COLUMN extra_sa     INTEGER NOT NULL DEFAULT 0 CHECK (extra_sa BETWEEN 0 AND 5);
ALTER TABLE ev_pools ADD COLUMN extra_b      INTEGER NOT NULL DEFAULT 0 CHECK (extra_b BETWEEN 0 AND 5);
ALTER TABLE ev_pools ADD COLUMN extra_reason TEXT    NULL CHECK (extra_reason IS NULL OR length(extra_reason) BETWEEN 1 AND 200);
ALTER TABLE ev_pools ADD COLUMN extra_at     TEXT    NULL;
ALTER TABLE ev_pools ADD COLUMN extra_by     INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT;
ALTER TABLE ev_pools ADD COLUMN extra_behalf INTEGER NULL REFERENCES users(id) ON DELETE RESTRICT;

CREATE TRIGGER trg_ev_pools_extra_ins BEFORE INSERT ON ev_pools
WHEN NEW.extra_sa + NEW.extra_b > 0 AND NEW.extra_reason IS NULL
BEGIN
    SELECT RAISE(ABORT, 'EXTRA_REASON: 정원을 늘렸으면 이유가 있어야 합니다.');
END;

-- 이유 칸만 지우는 고치기도 막아야 하므로 이유 칸도 보는 칸에 넣는다.
CREATE TRIGGER trg_ev_pools_extra_upd BEFORE UPDATE OF extra_sa, extra_b, extra_reason ON ev_pools
WHEN NEW.extra_sa + NEW.extra_b > 0 AND NEW.extra_reason IS NULL
BEGIN
    SELECT RAISE(ABORT, 'EXTRA_REASON: 정원을 늘렸으면 이유가 있어야 합니다.');
END;

-- [S3 가점 없앰] 평가에서 가점을 모두 없앤다(연봉 계산의 개인 포상 · 자격증 추가 인상률과 중복 혜택이라서).
--   ev_targets.bonus_x2 · bonus_reason 칸은 지우지 않는다 — SQLite 는 CHECK 가 걸린 칸을 지우려면 표를 새로 만들어야 하고 운영 평가 기록을 옮기는 위험을 질 이유가 없다.
--   판 23부터 쓰지 않는다: 서버는 이 두 칸을 읽지도 쓰지도 않고 총점은 가점 0 으로 센다 (그래서 값은 늘 0 · 빈 글이다).
