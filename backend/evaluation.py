"""인사평가(연말 평가) ↔ 인사포털 (2026-10-26 인사담당자 — 평가 3단계 ①: 가상 자료에서 서버 저장으로).

app.py 가 bp 를 붙인다. 세션 검사 · 같은 출처 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.
규칙(총점 · 점수 기준 · 연차 · 정원 · 묶음 상태)은 hr_eval/domain/eval_rules.py, 표는 hr_eval/sql/020_eval.sql 에 있다.

  POST /api/eval/admin/round              {"year", "evalStart", "selfTo", "evidDue", "scoreTo", "gradeTo", "ceoTo", "finalTo", "bands": {"A", "B", "C"}} 그해 회차 (HR)
  POST /api/eval/admin/start              {"year"} 평가 시작 — 대상 · 묶음 · 평가자 · 배정자 · 확정 KPI 판을 떠 둔다 → {"ok", "count", "noKpi", "noRater"} (HR)
  POST /api/eval/admin/att-done           {"year"} 근태 입력을 마쳤다 (HR · 이미 마쳤으면 그대로 200)
  POST /api/eval/admin/attendance         {"year", "rows": [{"userId", "late"?, "leaveOver"?}]} 지각 · 연차 초과 → {"ok", "done", "locked", "missing"} (HR)
  POST /api/eval/admin/<uid>/exclude      {"year", "reason"} 이번 평가에서 뺀다 (HR)
  POST /api/eval/admin/<uid>/include      {"year"} 제외를 되돌린다 (HR)
  POST /api/eval/admin/<uid>/skip-self    {"year"} 자기평가 없이 평가자에게 넘긴다 (HR) — 평가자에게 알림 (누른 HR 은 빼고)
  POST /api/eval/admin/<uid>/rater        {"year", "raterId"} 평가자를 바꾼다 (HR)
  POST /api/eval/admin/release            {"year"} 결과 공개 — 제외 안 된 대상 전원에게 알림(eval_open) (HR · 되돌릴 수 없다)
  GET  /api/eval/state?year=              보는 사람에 맞춘 그해 평가 (year 가 없으면 cur_year) → {"year", "offline", "round", "deleg", "ceoId", "me", "people", "pools", "todo"}
  GET  /api/eval/files/<fid>              증빙 내려받기 (본인 · HR — 자기평가를 낸 뒤에는 평가자 · 배정자 · 그 본부의 본부장 · 대표이사도)
  POST /api/eval/me/self                  {"year", "entries": [{"rid", "note"?, "selfGrade"?}]} 자기평가 임시 저장 — 보낸 줄 · 칸만 바꾼다 → {"ok", "me"} (본인 — 사람 번호는 세션에서만)
  POST /api/eval/me/submit                {"year"} 자기평가 제출 — 단계 SCORE · 평가자에게 알림 (대표이사가 평가자이고 맡겼으면 제출한 사람 뺀 HR 전원)
  POST /api/eval/files                    multipart year · rid · file — 증빙 올리기 → {"ok", "id"} (nginx 는 이 길만 본문 11MB)
  POST /api/eval/files/<fid>/delete       증빙 지우기 (올린 본인 · 올릴 수 있는 때만) → {"ok"}
  POST /api/eval/people/<uid>/edit        {"year", "marks"?: {"<rid>": 등급 | null}, "grade"?, "comment"?, "bonus"?, "bonusReason"?, "headComment"?} 평가자 · 배정자가 고친다
                                          — 보낸 칸만 바꾼다 → {"ok", "row": _row_json}
  POST /api/eval/people/<uid>/submit      {"year"} 평가자가 점수를 낸다 — 낸 그대로 떠 두고 다음 차례에게 알림
  POST /api/eval/people/<uid>/seen        {"year"} 평가자가 '윗사람이 고침' 안내를 확인 → {"ok", "editSeen"}
  POST /api/eval/pools/close              {"year", "pool"} 본부 팀원 묶음 마감 (배정자) — 대표이사(맡겼으면 빼고) · HR 전원에게 알림
  POST /api/eval/pools/seen               {"year", "pool"} 마감 알림을 확인 (HR)
  POST /api/eval/pools/confirm            {"year", "pools": [열쇠…]} 대표이사 확인 — 묶음째로 HR 확정 대기로 → {"ok", "done"} (대표이사 · 맡김이면 HR) — HR 전원에게 알림
  POST /api/eval/pools/back               {"year", "pool", "reason"} 본부 팀원 묶음을 배정자에게 돌려보낸다 (대표이사 · 맡김이면 HR) — 배정자에게 알림
  POST /api/eval/pools/finalize           {"year", "pools": [열쇠…]} HR 확정 → {"ok", "done"} (HR)
  POST /api/eval/pools/undo               {"year", "pool", "from"} 한 단계 되돌린다 — from = 화면이 본 묶음 상태(DONE | HR): 확정 → 확정 대기 / 확정 대기 → 대표이사 확인 전 (HR)
  GET  /api/eval/results?year=            평가 결과 (year 가 없으면 cur_year — 그해가 시작 전이면 공개한 해 중 최근)
                                          → {"years": 공개한 해(최근부터), "year", "offline", "released", "appealDue", "mine", "team", "appeals"}
  POST /api/eval/me/appeal                {"year", "reason"} 이의 신청 (본인 · 공개일 + 7일 한국 23:59 까지 한 번) — HR 전원에게 알림(eval_appeal, 신청한 사람은 빼고)
  POST /api/eval/appeals/<uid>/answer     {"year", "grade", "answer", "marks"?: {"<rid>": 등급}} HR 답변 — 등급 그대로면 끝(DONE · 신청한 사람에게 eval_answer),
                                          바꾸면 대표이사 확인 대기(CEO — 대표이사, 맡겼으면 신청한 사람 뺀 HR 에게 eval_appeal · 누른 사람은 빼고)
  POST /api/eval/appeals/<uid>/confirm    {"year"} 등급 변경 확인 — 고친 실행목표 등급 · 등급 반영 · 끝 (대표이사 · 맡김이면 HR) — 신청한 사람에게 eval_answer
  POST /api/eval/appeals/<uid>/back       {"year"} 등급 변경을 HR 로 돌려보낸다 (OPEN · 답변 글은 남김) — HR 전원(누른 사람 · 신청한 사람 빼고)에게 eval_appeal

보는 수준 (공개 전 등급은 어디에도 새지 않는다 — 사람 줄을 응답에 넣는 길은 모두 _row_json 하나를 지난다. 수준은 eval_rules.level):
  full 전부 (HR · 대표이사 · 배정자 · 점수를 내기 전의 평가자 · 공개 뒤) / mine 평가자가 낸 뒤 · 공개 전 — 자기가 낸 등급 · 항목 등급 · 총점만, 고쳤으면 edited 참(내용 글자 없음) /
  self 본인 · 공개 전 — 자기평가(실적 · 자기 등급 · 증빙)만, 평가 칸은 열쇠째 뺀다 / none 줄이 없다.
  자기평가를 내기 전(SELF)의 초안은 본인 · HR 만 본다 — 그 밖 사람(평가자 · 배정자 · 대표이사 · 본부장)의 줄에서는 ev[] 의 note · selfGrade · files 를 뺀다 (증빙 내려받기도 403).
  people = HR · 대표이사는 전원, 그 밖은 자기가 평가자이거나 배정자인 사람 (본인 줄은 me) · pools = HR · 대표이사는 전부, 그 밖은 자기가 배정자인 묶음만.
  배정자(R6) = 본부 팀원 묶음은 떠 둔 본부장이 활성이면 그 사람, 아니면 대표이사 · 그 밖 묶음은 대표이사. 판단 칸(can)은 _can — 쓰기 길도 같은 함수로 막는다.
  맡김(R11)이면 HR 이 대표이사 몫을 대신하되 자기 줄과 자기가 든 묶음의 사람은 대신 못 한다 — 점수 · 배정 · 묶음 마감 · 확인 · 돌려보내기 모두 (can.behalf — 화면의 "대리 입력" 표시).
  todo = 처리할 일 숫자 {me, admin, e3} (_todo — 화면은 실제 로그인이면 이 숫자를 그대로, 휴대폰 숫자도 eval_todo 로 같은 셈: e2 = me + admin · e3).
  상태: 해(2026~2100 정수) 400 · FIRST_YEAR 전 해는 offline 참에 빈 명단.
  결과(results — 한 트랜잭션): mine = 본인 줄 / team = HR 은 공개 전에도 전원(제외된 사람 포함), 그 밖은 공개 뒤에만 access.can VIEW_RESULT 범위(팀장 = 자기 팀원 ·
  본부장 = 자기 본부 · 대표이사 = 전원)의 제외 안 된 사람 — 평가자 · 배정자라서가 아니라 조직 위치로 / appeals = 처리할 이의 신청(_appeals_for — todo.e3 와 같은 셈).
  줄의 appeal(_appeal_json) = 본인 · HR · 대표이사에게 {"reason", "at", "status", "before", "answer", "after", "marks"} — 본인에게는 끝나기(DONE) 전 answer · after · marks 가 null
  (대표이사 확인을 기다리는 변경 · 돌려보내져 HR 이 고칠 답변 — 화면 evAppealBox 의 full), 그 밖 사람에게는 {"status"}만. 해 모양 400 은 상태와 같다.
  증빙: 없는 번호 404 → 볼 권한(can.evidence) 아님 403 → 폴더에 파일이 없음 404. 내려받기는 attachment · nosniff · no-store, 감사로그는 남기지 않는다.

거절 순서 (서버가 늘 같은 순서로 다시 본다. 확인하고 쓰는 일은 한 트랜잭션 — 두 번 눌러도 한 번만 통과):
회차: HR 403 → 해(2026~2100 정수) 400 → 포털 평가 전 해(FIRST_YEAR 전) 403 → 결과를 공개한 해 409 → 날짜 모양 · 범위 · 순서(R7) 400 → 기준 점수 400 → 시작한 뒤 평가 시작일을 바꿈 409.
  평가 시작일은 kpi_years.eval_start 한 곳에 둔다 — 그해 줄이 없으면 작성 기간 없이 만든다. /api/kpi/admin/year 도 시작한 해의 평가 시작일은 못 바꾼다(409).
시작: HR 403 → 해 400 → FIRST_YEAR 403 → 올해(plan_year)가 아님 403 → 회차 없음 409 → 지금 평가 시작일로 본 날짜 순서(R7)가 어긋남 409 → 오늘(한국) < 평가 시작일 403 →
  이미 시작 409 → 대상 0명 409. (회차를 저장한 뒤 /api/kpi/admin/year 로 평가 시작일을 옮겼을 수 있어서 순서를 다시 본다)
  대상(R3) = 활성 · 구성원 설정 "평가 대상" 켜짐 · 본부장 · 대표이사 아님 (포털 사용이 꺼진 사람도 든다). 사람마다 떠 두는 것 (R1 · R4 · R5):
  묶음 = 팀장 LEADERS · 대표이사 직속 팀 · '임원' · 본부 없음 DIRECT · 그 밖 DIV:<본부> / 평가자 = 바로 위 관리자(org.manager_of — 본인이면 NULL) /
  배정자 = DIV 묶음은 그 본부를 맡은 활성 본부장(없으면 NULL = 대표이사), 그 밖 NULL / KPI 판 = KPI 를 쓰면 그해 마지막 확정 판(없으면 NULL — 등급만) /
  단계 = KPI 판이 있으면 SELF, 없으면 SCORE / 이름 칸 = 본부 이름(없으면 "대표이사 직속부서") · 팀 이름(없으면 "본부 직속"). 실행목표마다 ev_items 빈 줄, 묶음마다 ev_pools 줄.
  저장한 뒤 KPI 가 있는 대상에게 알림(eval_turn).
근태 마침 · 근태: HR 403 → 해 400 → 시작 안 함 409 → 공개함 409 → (근태) 줄 모양 400 (하나라도 틀리면 아무것도 저장 안 함).
  대상이 아닌 번호는 missing, 요청한 HR 본인 · 제외됐거나 배정 단계를 지난 사람(at(stage, assign_stage(pool)) > 0)은 locked, 나머지는 저장해 done.
  본인 줄은 다른 HR 이 넣는다 (자기 연차 초과를 5일 밑으로 낮춰 S·A·B 제한을 피하지 못하게 — HR 이 명단 전체를 붙여 넣으므로 400 이 아니라 locked).
  연차 초과가 5일 이상이 되면 이미 준 S·A·B 등급을 지운다 (화면 evSetAttendance).
명단 한 사람 (HR 403 → 본인 403 → 해 400 → 그해 평가 대상 404 → 아래). 본인 평가는 다른 HR 이 처리한다:
  제외: 이미 제외 409 → 평가자가 점수를 냄(SCORE 지남) 409 → 사유 1~100자 400 · 되돌림: 제외가 아님 409 → 그 묶음이 WAIT · FIX 가 아님 409 ·
  자기평가 없이: 제외됨 409 → SELF 가 아님 409 → KPI 없음 409 (저장한 뒤 떠 둔 평가자에게 알림 — 받는 사람은 자기평가 제출과 같고 누른 HR 은 빼고) ·
  평가자 바꾸기: SELF · SCORE 가 아님 409 → 본인이 아니고 활성이며 포털을 쓰는(로그인해 점수를 낼 수 있는) 직원이 아님 400.
자기평가 저장 · 제출 (본인): 해 400 → 그해 평가 대상 아님 404 → 쓸 수 없음 403 (can.self 거짓 — 자기평가 마감일이 지났으면 그 날짜를 알리는 문구, 그 밖은 "지금은 쓸 수 없습니다") →
  저장은 모양 400 (목록 · 없는 실행목표 · 같은 줄 두 번 · 실적 500자 · 쓸 수 없는 글자 · 등급 SABCD 또는 null, 실적은 앞뒤 공백을 떼고 센다 · 뗀 글을 저장, 하나라도 틀리면 아무것도 저장 안 함) /
  제출은 실행목표마다 실적(공백 뺀 1자 이상)과 자기 등급이 다 있어야 400. 저장은 감사로그가 없고 제출은 EVAL_SELF_SUBMITTED (해만 — 글 없음). 알림은 저장(COMMIT)한 뒤.
증빙 올리기: 몸통이 10MB + 64KB 를 넘음 400 (읽기 전에) → 해 400 → 평가 대상 아님 404 → can.upload 거짓 403 → 떠 둔 실행목표 아님 400 → 그 실행목표에 이미 5개 400 →
  빈 파일 · 10MB 넘음 400 → 형식(_kind — 확장자 + 첫 바이트) 400. 이름은 경로를 떼고 제어 · 서식(Cf) 문자와 앞뒤 공백을 빼고 120자로 줄이며 (줄일 때 확장자는 쓴 그대로 남기고 이름 쪽을 자른다. 비면 "증빙.<종류>"), 파일은 DB 옆 eval_files/<ev_files.id> —
  줄을 넣어 번호를 받고 임시 이름으로 써서 바꿔 단다 (쓰다 실패하면 줄도 되돌린다 — 같은 트랜잭션). 번호는 지워도 다시 쓰지 않는다(AUTOINCREMENT) — 옛 번호로 남의 새 파일을 받지 못하게.
  줄과 파일은 늘 함께 지운다.
증빙 지우기: 없는 번호 404 → 올린 본인 아님 403 → can.upload 거짓 403 → 줄과 파일을 한 트랜잭션에서 지움 (파일이 이미 없어도 200). 올리기 · 지우기는 감사로그가 없다.
평가자 · 배정자 (사람 한 명): 해 400 → 그해 평가 대상 404 → 아래. 판단은 모두 잠근 뒤 다시 읽은 _ctx 의 _can (화면의 can 과 같은 함수):
  고치기: can.score 도 can.assign 도 아님 403 → 칸 권한 403 (평가자 코멘트 can.score — 낸 뒤에는 아무도 못 고친다(평가자의 숨긴 줄이 낸 코멘트를 그대로 보인다) ·
    윗사람 코멘트 can.head · 가점 · 사유 can.bonus) → 모양 400 (KPI 없는 사람의 항목 등급 · 가점 · 없는 실행목표 · 등급 아님 · 배정 단계에서 항목 등급 비움(null — 점수 단계 초안만 된다) ·
    가점 0~10 · 0.5 단위 · 가점이 있는데 사유 없음 ·
    글자 한도 — 앞뒤 공백을 떼고 세고 뗀 글을 저장 · _chars) → 줄 수 있는 가장 높은 등급(cap)보다 높음 400 (문구는 화면 evSubmitScore). 하나라도 틀리면 아무것도 저장 안 함.
    자동 등급(화면 evFit — KPI 있는 사람만): 점수 단계에서 항목 등급이 다 찼고 이번 요청이 점수(항목 등급 · 가점)를 바꿨으면 등급 = cap, 그 밖에는 지금 등급이 cap 보다 높아졌을 때만
    cap 으로 내린다. grade 를 보내면 그 값이 이긴다. 대표이사 대신(can.behalf)이면 proxy_by = 나. 감사로그 없음(임시 단계).
  내기: can.score 아님 403 → KPI 있으면 항목 등급이 다 · 평가자 코멘트 1자 이상 400 (KPI 없으면 코멘트 선택) → 등급 없음 400 → cap 보다 높음 400.
    떠 두기: proposed = 등급 · sub_score_x10(KPI 있을 때) · sub_marks · scored_at/by · 단계 = after_score(묶음). EVAL_SCORED (대신이면 on_behalf_of = 대표이사).
    알림(저장한 뒤): 배정자(팀장 묶음은 대표이사) — 그가 대표이사이고 맡겼으면 평가받는 사람 뺀 HR 전원. 낸 사람 본인에게는 보내지 않는다.
  확인(seen): 평가자 본인 아님 403 → edit_seen = 지금 고친 내용 글자 (응답은 editSeen 참/거짓만 — 글자는 공개 전 평가자에게 가지 않는다).
묶음 마감: 해 400 → 본부 팀원 묶음(DIV:) 아님 400 → 묶음 없음 404 → 권한(_pool_auth.close — 공개 여부는 빼고) 403 → 제외 안 된 사람이 없음 409 → 공개 뒤 409 → 제외 안 된 사람 중 배정 단계(GRADE) 전인 사람 409 →
  이미 넘어감 409 → 전원 검사(check_pool full — 등급 누락 · 정원 · 점수 기준 · 연차) 오류 409 {"error": 첫 오류, "errors"}. 모두 CEO · closed_at/by · 돌려보낸 기록(back_*) 비움 · hr_seen [] ·
  EVAL_POOL_CLOSED (HR 이 대신이면 on_behalf_of). 알림: 대표이사(맡겼으면 빼고) + HR 전원 — 마감한 사람 본인은 빼고.
마감 확인: HR 403 → 해 400 → 묶음 없음 404 → hr_seen 에 나를 더함 (이미 있으면 그대로).
묶음 확인 · 돌려보내기 · 확정 · 되돌리기 (마감과 같은 모양 — _pools_ok): 해 400 → 열쇠 모양 400 (확인 · 확정은 글자 열쇠 하나 이상의 목록 · 같은 열쇠는 한 번) →
  (되돌리기) 본 상태 from 이 HR · DONE 이 아님 400 → 없는 묶음 404 → 권한(_pool_auth — 상태 · 공개는 빼고) 403 → 공개 뒤 409 →
  묶음 상태(POOL_NEED — 확인 CEO · 돌려보내기 CEO · FIX · 확정 HR / 되돌리기는 지금 상태가 from 과 같아야)가 아님 409
  (이미 넘어갔으면 "이미 …했습니다", 아직이면 그 까닭 · 등급을 맞춰야 하면(FIX) errors 도). 문구에 어느 묶음인지. 목록은 하나라도 걸리면 아무것도 안 바뀐다 — 두 창의 두 번째는 409.
  권한: 확인 · 돌려보내기(본부 팀원 묶음만) = 활성 대표이사, 또는 맡김이고 그 묶음에 들지 않은(제외된 줄 포함) HR / 확정 · 되돌리기 = HR (HR 본인 일 — 자기가 든 묶음도 된다).
  확인: 모두 HR · ceo_at/by · ceo_behalf(HR 이 대신이면 대표이사) · EVAL_POOL_CONFIRMED 묶음마다. 알림: HR 전원 — 누른 사람은 빼고.
  돌려보내기: → 이유 1~200자 400 (_chars · 앞뒤 공백을 떼고 셈). 모두 GRADE · back_at/by/behalf/reason · 마감 기록(closed_at/by) 비움 (다시 마감하면 back_* 가 지워진다) ·
    EVAL_POOL_RETURNED (이유 없음). 알림: 배정자(_turn_to — 대표이사이고 맡겼으면 HR 전원) — 누른 사람은 빼고.
  돌려보내기는 FIX(마감 뒤 기준 점수가 바뀌어 등급이 어긋남)에서도 된다 — 대표이사는 본부 팀원 등급을 고치지 못하니 그것이 빠져나갈 길이다.
  확정: 모두 DONE · EVAL_POOL_FINALIZED 묶음마다 · 알림 없음. 되돌리기: DONE → HR (undo_from HR) / HR → ready_stage(직속 GRADE · 그 밖 CEO) · ceo_* 비움 (undo_from CEO) ·
    undo_at/by/from · EVAL_POOL_UNDONE {from} · 알림 없음. 본 상태(from)와 지금이 다르면 409 ("이미 되돌렸습니다" · "그사이 확정됐습니다") — 두 창이 눌러도 한 단계만.
결과 공개: HR 403 → 해 400 → FIRST_YEAR 403 → 시작 안 함 409 → 이미 공개 409 → 제외 안 된 사람이 없음 409 (공개는 되돌릴 수 없다) → 제외 안 된 사람 중 DONE 이 아닌 사람 409 →
  released_at/by · EVAL_RELEASED {count = 제외 안 된 대상 수}.
  알림(eval_open): 제외 안 된 대상 전원 — 공개한 HR 도 대상이면 받는다 (남의 일을 한 사람이 아니라 자기 결과를 받는 사람). 공개 뒤에는 level 이 full 이라 _row_json 이 전부 보인다.
이의 신청 (본인 — 사람 번호는 세션에서만): 해 400 → 그해 평가 대상 아님 404 → 공개 전 403 → 제외됨 403 → 오늘(한국) > 공개일(한국) + APPEAL_DAYS 403 → 이미 신청 409 →
  사유 1~500자 400 (_chars · 앞뒤 공백을 떼고 셈 · 뗀 글 저장). OPEN · before = 지금 등급 · EVAL_APPEALED.
HR 답변: HR 403 → 본인 403 → 해 400 → 이의 신청 없음 404 → OPEN 아님 409 → 답변 1~500자 400 → 등급 SABCD 400 → marks 모양 400 (KPI 없는 사람은 null · 빠짐만,
  떠 둔 실행목표 · SABCD) → 고친 실행목표 등급으로 낸 총점 · 연차로 cap 보다 높음 400 (_within — 정원은 따지지 않는다: 공개된 다른 사람 등급을 건드리지 않고 대표이사 확인을 거친다).
  등급 그대로 = 실행목표 등급 반영 · DONE · 결정한 사람 = 답한 HR / 바뀜 = CEO · after · marks(지금과 다른 실행목표만) — 점수 · 등급은 확인할 때. EVAL_APPEAL_ANSWERED {year, changed}.
대표이사 확인 · 돌려보내기: 해 400 → 대상 아님 404 → 권한(_appeal_can — 활성 대표이사, 또는 맡김이고 자기 이의가 아닌 HR) 403 → 이의 신청 없음 404 → CEO 아님 409 →
  (확인) cap 다시 400 → 실행목표 등급 · 등급 = after · DONE · decided_at/by/behalf · EVAL_APPEAL_CONFIRMED / (돌려보내기) OPEN · after · marks 비움 · EVAL_APPEAL_RETURNED.
  HR 이 대신이면 on_behalf_of = 대표이사. 두 창의 두 번째는 409 (이미 처리했습니다 · HR 답변을 기다리는 이의 신청입니다).
감사로그(R10): EVAL_ROUND_SET · EVAL_STARTED · EVAL_ATT_DONE · EVAL_ATTENDANCE(저장한 사람이 있을 때만) · EVAL_EXCLUDED · EVAL_INCLUDED · EVAL_SKIP_SELF · EVAL_RATER_SET · EVAL_SELF_SUBMITTED ·
  EVAL_SCORED · EVAL_POOL_CLOSED · EVAL_POOL_CONFIRMED · EVAL_POOL_RETURNED · EVAL_POOL_FINALIZED · EVAL_POOL_UNDONE · EVAL_RELEASED · EVAL_APPEALED · EVAL_APPEAL_ANSWERED ·
  EVAL_APPEAL_CONFIRMED · EVAL_APPEAL_RETURNED — after_data 는 해 · 사람 수 · 평가자 번호 · 묶음 열쇠 · 되돌린 단계(from) · 등급을 바꾸는 답변인가(changed)뿐.
  사유 · 근태 숫자 · 등급 · 점수 · 글은 넣지 않는다 (공개 전 등급이 감사로그로 새지 않게). 이의 신청 넷의 대상은 신청한 사람(ev_appeal).
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator, NamedTuple
from urllib.parse import quote

from flask import Blueprint, Response, g, jsonify, request

import auth
import kpi
import push
from hr_eval.domain.access import Act, Delegation, Target, can
from hr_eval.domain.eval_rules import (APPEAL_DAYS, BANDS, BONUS_MAX_X2, FILE_MAX, FILES_PER_ITEM, GRADES, LEAVE_LIMIT_X2, TEXT_MAX, Member, _band_text, above,
                                       after_score, assign_stage, at, cap, check_pool, edited, level, pool_key, pool_state, ready_stage, total)
from hr_eval.domain.models import Actor, AuditEntry, Role
from hr_eval.domain.org import led_dept, manager_of, target_of
from hr_eval.domain.quota import QuotaMode, calculate_department_quota
from kpi import UID, YEARS, _Refuse, _body, _chars, _txn

bp = Blueprint("evaluation", __name__)
bp.register_error_handler(_Refuse, kpi._refused)

FIRST_YEAR = int(os.environ.get("HR_EVAL_FIRST_YEAR", "2027"))  # ponytail: 로컬 확인용 환경 변수 — 운영에는 넣지 않는다
DATES = ("evalStart", "selfTo", "evidDue", "scoreTo", "gradeTo", "ceoTo", "finalTo")  # 회차 날짜 — 처음 것은 kpi_years.eval_start, 나머지는 ev_rounds 칸 차례
NO_DIV, NO_TEAM = "대표이사 직속부서", "본부 직속"  # 떠 두는 이름 칸 — 본부가 없는 사람(직속 팀 · 임원) · 팀이 없는 사람
ROWS_MAX = 2000  # 근태 한 번에 보내는 줄


# ---------------- 읽기 도구 (평가 읽기 · 쓰기 길이 함께 쓴다) ----------------
def cur_year(conn: sqlite3.Connection) -> int:
    """화면에 보일 해 (R13): 올해 회차가 시작돼 있으면 올해. 아니면 지난해 회차가 시작돼 있고 아직 안 끝났으면 지난해 —
    공개 전이거나, 이의 신청 기간(공개일 + 7일, 한국 날짜) 안이거나, 처리 중인 이의 신청(OPEN · CEO)이 있으면. 그 밖은 올해."""
    year = kpi.plan_year()
    if conn.execute("SELECT 1 FROM ev_rounds WHERE year = ? AND started_at IS NOT NULL", (year,)).fetchone():
        return year
    last = conn.execute("SELECT released_at FROM ev_rounds WHERE year = ? AND started_at IS NOT NULL", (year - 1,)).fetchone()
    if last and (last[0] is None or auth._today() <= _day(last[0]) + timedelta(days=APPEAL_DAYS)
                 or conn.execute("SELECT 1 FROM ev_appeals WHERE year = ? AND status IN ('OPEN', 'CEO')", (year - 1,)).fetchone()):
        return year - 1
    return year


def _day(iso: str) -> date:
    """저장한 UTC 시각 글자(auth._iso 모양) → 한국 날짜."""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(auth.KST).date()


def _round(conn: sqlite3.Connection, year: int) -> dict | None:
    """그해 회차 → {"year", "evalStart", "selfTo", …, "finalTo", "bands": {"A", "B", "C"}, "started", "attDone", "released"} (때는 ISO 글자 또는 None). 없으면 None."""
    r = conn.execute(
        "SELECT y.eval_start, r.self_to, r.evid_due, r.score_to, r.grade_to, r.ceo_to, r.final_to, r.band_a, r.band_b, r.band_c,"
        " r.started_at, r.att_done_at, r.released_at FROM ev_rounds r JOIN kpi_years y ON y.year = r.year WHERE r.year = ?", (year,)).fetchone()
    if r is None:
        return None
    return {"year": year, **dict(zip(DATES, r[:7])), "bands": dict(zip("ABC", r[7:10])), "started": r[10], "attDone": r[11], "released": r[12]}


def _ceo(conn: sqlite3.Connection) -> int | None:
    """활성 대표이사 번호 (여럿이면 번호가 작은 사람 — org.manager_of 가 고르는 사람과 같다)."""
    r = conn.execute("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 ORDER BY u.id LIMIT 1").fetchone()
    return r[0] if r else None


def _deleg(conn: sqlite3.Connection, ceo: int | None) -> bool:
    """대표이사가 자기 몫을 HR 에 맡겼나 (KPI 와 같은 스위치 하나 — R11)."""
    return ceo is not None and conn.execute("SELECT 1 FROM ceo_delegation WHERE ceo_user_id = ?", (ceo,)).fetchone() is not None


class _Ctx(NamedTuple):
    """한 요청에서 한 번 읽어 두는 그해 평가 — 읽기 길과 쓰기 길이 함께 쓴다 (쓰기 길은 _txn 안에서 다시 읽는다)."""

    conn: sqlite3.Connection
    year: int
    round: dict | None  # _round
    ceo: int | None  # 활성 대표이사 (_ceo)
    deleg: bool  # 대표이사가 HR 에 맡겼나 (_deleg)
    rows: dict[int, sqlite3.Row]  # 사람 → ev_targets 줄 (평가 대상)
    items: dict[int, list[dict]]  # 사람 → 떠 둔 KPI 판의 실행목표 (확정본 차례, 줄마다 team · personal 을 붙임). KPI 없으면 빈 목록
    entries: dict[tuple[int, int], sqlite3.Row]  # (사람, 실행목표 번호) → ev_items 줄
    files: dict[tuple[int, int], list[sqlite3.Row]]  # (사람, 실행목표 번호) → 증빙 (올린 차례)
    pools: dict[str, sqlite3.Row]  # 묶음 열쇠 → ev_pools 줄
    names: dict[int, tuple[str, str]]  # 사람 → (이름, 지금 직급) — 비활성 포함
    active: set[int]  # 활성인 사람
    login: set[int]  # 로그인할 수 있는 사람 (활성 · 포털 사용)
    appeals: dict[int, sqlite3.Row]  # 사람 → ev_appeals 줄 (이의 신청)


def _ctx(conn: sqlite3.Connection, year: int) -> _Ctx:
    def rows(sql: str) -> list[sqlite3.Row]:
        cur = conn.cursor()
        cur.row_factory = sqlite3.Row  # 이 커서만 — 같은 연결(g.db)을 쓰는 다른 코드는 그대로 튜플을 받는다
        return cur.execute(sql, (year,)).fetchall()

    targets = {r["user_id"]: r for r in rows("SELECT * FROM ev_targets WHERE year = ?")}
    items: dict[int, list[dict]] = {uid: [] for uid in targets}
    for uid, content in conn.execute("SELECT t.user_id, v.content FROM ev_targets t JOIN kpi_versions v ON v.user_id = t.user_id AND v.year = t.year"
                                     " AND v.version = t.kpi_version WHERE t.year = ?", (year,)):
        items[uid] = [{**it, "team": goal["team"], "personal": goal["personal"]} for goal in json.loads(content) for it in goal["items"]]
    files: dict[tuple[int, int], list[sqlite3.Row]] = {}
    for f in rows("SELECT id, user_id, rid, name, kind, size FROM ev_files WHERE year = ? ORDER BY id"):
        files.setdefault((f["user_id"], f["rid"]), []).append(f)
    users = conn.execute("SELECT id, name, title, is_active, portal_enabled FROM users").fetchall()
    ceo = _ceo(conn)
    return _Ctx(conn, year, _round(conn, year), ceo, _deleg(conn, ceo), targets, items,
                {(e["user_id"], e["rid"]): e for e in rows("SELECT * FROM ev_items WHERE year = ?")}, files,
                {p["pool"]: p for p in rows("SELECT * FROM ev_pools WHERE year = ?")},
                {u[0]: (u[1], u[2] or "") for u in users}, {u[0] for u in users if u[3]}, {u[0] for u in users if u[3] and u[4]},
                {a["user_id"]: a for a in rows("SELECT * FROM ev_appeals WHERE year = ?")})


def _is_ceo(ctx: _Ctx, uid: int | None) -> bool:
    return uid is not None and uid == ctx.ceo


def _assigner(ctx: _Ctx, row: sqlite3.Row) -> int | None:
    """등급을 배정하는 사람 (R6): 본부 팀원 묶음은 떠 둔 본부장이 활성이면 그 사람, 아니면 대표이사. 그 밖 묶음은 대표이사."""
    a = row["assigner_id"]
    return a if row["pool"].startswith("DIV:") and a in ctx.active else ctx.ceo


def _pool_who(ctx: _Ctx, key: str) -> int | None:
    """묶음의 배정자 — 한 묶음 사람들은 같은 본부장을 떠 뒀다."""
    return next((_assigner(ctx, r) for r in ctx.rows.values() if r["pool"] == key), ctx.ceo)


def _behalf_ok(ctx: _Ctx, me: Actor, key: str) -> bool:
    """대표이사 몫을 이 HR 이 대신할 수 있나 (R11): 맡겼고 · HR 이고 · 그 묶음(key)에 자기가 들지 않았다 — 자기 줄도, 같은 묶음 사람의 점수 · 등급도 대신 못 한다
    (같은 정원을 나눠 쓰는 동료의 등급이 자기 등급에 닿는다). 그해 떠 둔 줄로 본다 (제외된 줄도)."""
    mine = ctx.rows.get(me.user_id)
    return me.is_hr and ctx.deleg and (mine is None or mine["pool"] != key)


def _bands_of(ctx: _Ctx) -> tuple[int, int, int]:
    return tuple(ctx.round["bands"][k] for k in "ABC")


def _total(ctx: _Ctx, row: sqlite3.Row) -> Decimal | None:
    """지금 총점 = 떠 둔 KPI 판의 가중치 × 지금 실행목표 등급 + 가점. KPI 없이 등급만 받는 사람은 None."""
    if row["kpi_version"] is None:
        return None
    uid = row["user_id"]
    return total([(it["weight"], ctx.entries[(uid, it["rid"])]["grade"]) for it in ctx.items[uid]], row["bonus_x2"])


def _edited(ctx: _Ctx, row: sqlite3.Row) -> str:
    """평가자가 낸 것과 지금이 달라진 내용 (eval_rules.edited — 배정 단계를 지난 뒤에만). 평가자가 대표이사이거나 배정자 본인이면(팀장 없는 팀 팀원의 본부장 등)
    고친 사람도 자기라 따지지 않는다 (화면 evEdited — 자기가 고친 것을 '윗사람이 고침'으로 알리지 않게)."""
    rater = row["rater_id"]
    if _is_ceo(ctx, rater) or (rater is not None and rater == _assigner(ctx, row)):
        return ""
    return edited(row["proposed"], row["sub_score_x10"], row["grade"], _total(ctx, row), past=at(row["stage"], assign_stage(row["pool"])) > 0)


def _order(ctx: _Ctx, row: sqlite3.Row) -> tuple:
    """명단 차례: 본부 → 팀 → 팀장 먼저 → 이름."""
    return row["div_name"], row["team_name"], -row["is_leader"], ctx.names[row["user_id"]][0], row["user_id"]


def _pool(ctx: _Ctx, key: str) -> tuple[str, list[str], list[str], dict[str, int], list[sqlite3.Row]]:
    """묶음 → (상태, 오류, 경고, 등급별 인원, 제외 안 된 사람 줄). 화면 evGradeTable · evPoolCard · evPoolState 와 같은 셈:
    배정 단계에 온 사람의 등급만 세고, 다 오기 전에는 빠진 등급 · 미달을 따지지 않는다(full 거짓).
    모두 대표이사 확인 단계(ready_stage)에 왔으면 배정 단계도 지났으니 full 검사다 — 그 오류로 FIX · CEO 를 가른다."""
    ref = assign_stage(key)
    rows = sorted((r for r in ctx.rows.values() if r["pool"] == key and r["out_at"] is None), key=lambda r: _order(ctx, r))
    members = [Member(r["user_id"], ctx.names[r["user_id"]][0], _total(ctx, r), r["grade"] if at(r["stage"], ref) >= 0 else None, r["leave_x2"]) for r in rows]
    errors, warnings, counts = check_pool(members, _bands_of(ctx), all(at(r["stage"], ref) >= 0 for r in rows))
    return pool_state([r["stage"] for r in rows], key, bool(errors)), errors, warnings, counts, rows


# ---------------- 요청 모양 ----------------
def _year(v: object) -> int:
    if type(v) is not int or v not in YEARS:
        raise _Refuse("해가 올바르지 않습니다.", 400)
    return v


def _portal_year(v: object) -> int:
    """해 400 → 포털에서 평가하지 않는 해(FIRST_YEAR 전) 403 (R2)."""
    year = _year(v)
    if year < FIRST_YEAR:
        raise _Refuse(f"{year}년 평가는 포털 밖에서 진행합니다.", 403)
    return year


def _asked(raw: str) -> int:
    """?year= 글자 → 해 (ASCII 숫자 네 자리 · 2026~2100 — 아니면 400). 상태 · 결과가 함께 쓴다."""
    return _year(int(raw) if raw.isascii() and raw.isdigit() and len(raw) == 4 else None)


def _dates(year: int, b: dict) -> dict[str, str]:
    """회차 날짜 (R7) — 모두 YYYY-MM-DD. 평가 시작일은 그해 안, 나머지는 평가 시작일부터 다음 해 2월 말일까지(3월 연봉계약 전).
    자기평가 ≤ 평가자 평가 ≤ 등급 배정 ≤ 대표이사 확인 ≤ HR 확정, 증빙 ≤ 평가자 평가 (같은 날은 된다). 아니면 400."""
    try:
        d = {k: kpi._iso_date(b.get(k)) for k in DATES}
    except ValueError:
        d = {}
    if not d or None in d.values():
        raise _Refuse("단계별 마감일을 모두 날짜(YYYY-MM-DD)로 넣어 주세요.", 400)
    if d["evalStart"][:4] != str(year):
        raise _Refuse(f"평가 시작일은 {year}년 안의 날짜로 정해 주세요.", 400)
    feb = date(year + 1, 3, 1) - timedelta(days=1)
    if not all(d["evalStart"] <= d[k] <= feb.isoformat() for k in DATES[1:]):
        raise _Refuse(f"마감일은 평가 시작일부터 {year + 1}년 2월 {feb.day}일 사이로 정해 주세요.", 400)
    steps = [d[k] for k in ("selfTo", "scoreTo", "gradeTo", "ceoTo", "finalTo")]
    if steps != sorted(steps):
        raise _Refuse("마감일은 자기평가 → 평가자 평가 → 등급 배정 → 대표이사 확인 → HR 확정 차례로 정해 주세요.", 400)
    if d["evidDue"] > d["scoreTo"]:
        raise _Refuse("증빙 마감일은 평가자 평가 마감일보다 늦을 수 없습니다.", 400)
    return d


def _bands(v: object) -> tuple[int, int, int]:
    """등급 기준 점수 {"A", "B", "C"} — 1~100 정수(참/거짓 · 소수 아님), A > B > C. 아니면 400."""
    abc = tuple(v.get(k) for k in "ABC") if isinstance(v, dict) else ()
    if not (len(abc) == 3 and all(type(n) is int for n in abc) and 100 >= abc[0] > abc[1] > abc[2] >= 1):
        raise _Refuse("등급 기준 점수는 1~100 사이 정수로, A > B > C 가 되게 넣어 주세요.", 400)
    return abc


def _att_rows(v: object) -> list[tuple[int, int | None, int | None]]:
    """근태 줄 → [(번호, 지각, 연차 초과 × 2)] (보내지 않은 칸은 None). 지각 0~999 정수 · 연차 초과 0~365 이고 0.5 단위 (참/거짓 아님).
    하나라도 모양이 틀리면 400 — 아무것도 저장하지 않는다."""
    if not isinstance(v, list) or len(v) > ROWS_MAX:
        raise _Refuse(f"근태는 {ROWS_MAX}줄까지 목록으로 보내 주세요.", 400)
    out = []
    for r in v:
        uid, late, over = (r.get("userId"), r.get("late"), r.get("leaveOver")) if isinstance(r, dict) else (None, None, None)
        if not (type(uid) is int and 0 < uid <= 2147483647  # SQLite 정수보다 큰 수는 묶다가 서버 오류가 나므로 먼저
                and ("late" not in r or (type(late) is int and 0 <= late <= 999))
                and ("leaveOver" not in r or (type(over) in (int, float) and 0 <= over <= 365 and over * 2 == int(over * 2)))):
            raise _Refuse("근태 숫자를 확인해 주세요. 지각은 0~999회 정수, 연차 초과는 0~365일(0.5일 단위)입니다.", 400)
        out.append((uid, late if "late" in r else None, int(over * 2) if "leaveOver" in r else None))
    return out


def _open(conn: sqlite3.Connection, year: int) -> None:
    """시작했고 아직 공개 전인 해만 — 아니면 409."""
    r = conn.execute("SELECT started_at, released_at FROM ev_rounds WHERE year = ?", (year,)).fetchone()
    if r is None or r[0] is None:
        raise _Refuse("평가를 아직 시작하지 않았습니다.", 409)
    if r[1] is not None:
        raise _Refuse("결과를 공개한 평가는 바꿀 수 없습니다.", 409)


def _log(conn: sqlite3.Connection, action: str, entity_type: str, entity_id: int, behalf: int | None = None, **after: object) -> None:
    """감사로그: 행동 · 대상 · 해(· 사람 수 · 평가자 번호 · 묶음 열쇠)만. HR 이 대표이사 대신 했으면 behalf = 그 대표이사 (on_behalf_of)."""
    auth._audit(conn, AuditEntry.by(g.actor, on_behalf_of=behalf, action=action, entity_type=entity_type, entity_id=entity_id, after_data=after))


# ---------------- 회차 · 시작 ----------------
@bp.post("/api/eval/admin/round")
def round_set() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    b = _body()
    year = _portal_year(b.get("year"))
    conn = auth._db()
    with _txn(conn):
        old = _round(conn, year)
        if old and old["released"]:
            raise _Refuse("결과를 공개한 평가는 바꿀 수 없습니다.", 409)
        d, bands = _dates(year, b), _bands(b.get("bands"))
        if old and old["started"] and d["evalStart"] != old["evalStart"]:
            raise _Refuse("평가가 시작되어 평가 시작일을 바꿀 수 없습니다.", 409)
        now, me = auth._iso(auth._now()), g.actor.user_id
        conn.execute("INSERT INTO kpi_years (year, eval_start, updated_at, updated_by) VALUES (?, ?, ?, ?) ON CONFLICT (year) DO UPDATE SET"
                     " eval_start = excluded.eval_start, updated_at = excluded.updated_at, updated_by = excluded.updated_by", (year, d["evalStart"], now, me))
        conn.execute(
            "INSERT INTO ev_rounds (year, self_to, evid_due, score_to, grade_to, ceo_to, final_to, band_a, band_b, band_c, updated_at, updated_by)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (year) DO UPDATE SET self_to = excluded.self_to, evid_due = excluded.evid_due,"
            " score_to = excluded.score_to, grade_to = excluded.grade_to, ceo_to = excluded.ceo_to, final_to = excluded.final_to, band_a = excluded.band_a,"
            " band_b = excluded.band_b, band_c = excluded.band_c, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (year, *(d[k] for k in DATES[1:]), *bands, now, me))
        _log(conn, "EVAL_ROUND_SET", "ev_round", year, year=year)
    return jsonify(ok=True)


def _snapshot(conn: sqlite3.Connection, year: int) -> list[dict]:
    """평가 대상마다 떠 둘 한 줄 (모듈 머리말의 '시작' 규칙). rids = 떠 둔 KPI 판의 실행목표 번호들."""
    people, depts = kpi.load_org(conn)
    person = {p.id: p for p in people}
    names = {d.id: d.name for d in depts}
    titles = dict(conn.execute("SELECT id, title FROM users"))
    out = []
    for m in auth._members(conn):
        if not (m.is_active and m.in_evaluation) or m.position in (Role.DIVISION_HEAD, Role.CEO):
            continue
        t = target_of(person[m.id], depts)
        key, rater = pool_key(t), manager_of(m.id, people, depts)
        assigner = next((p.id for p in people if p.is_active and p.position is Role.DIVISION_HEAD and led_dept(p) == t.division_id),
                        None) if key.startswith("DIV:") else None
        version, content = (conn.execute("SELECT version, content FROM kpi_versions WHERE user_id = ? AND year = ? ORDER BY version DESC LIMIT 1",
                                         (m.id, year)).fetchone() or (None, None)) if m.writes_kpi else (None, None)
        out.append({"user_id": m.id, "pool": key, "division_id": t.division_id, "team_id": t.team_id,
                    "div_name": names.get(t.division_id, NO_DIV), "team_name": names.get(t.team_id, NO_TEAM), "title": titles[m.id],
                    "is_leader": int(m.position is Role.TEAM_LEADER), "rater_id": None if rater == m.id else rater, "assigner_id": assigner,
                    "writes_kpi": int(m.writes_kpi), "kpi_version": version, "stage": "SELF" if version else "SCORE",
                    "rids": [it["rid"] for it in kpi._rows(json.loads(content))] if content else []})
    return out


@bp.post("/api/eval/admin/start")
def start() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    year = _portal_year(_body().get("year"))
    if year != kpi.plan_year():
        raise _Refuse("올해 평가만 시작할 수 있습니다.", 403)
    conn = auth._db()
    with _txn(conn):
        rnd = _round(conn, year)
        if rnd is None or rnd["evalStart"] is None:  # 평가 시작일은 /api/kpi/admin/year 로도 지울 수 있다 (시작 전에는)
            raise _Refuse("단계별 마감일을 먼저 정해 주세요.", 409)
        try:
            _dates(year, rnd)  # 회차 저장 뒤 /api/kpi/admin/year 로 평가 시작일을 옮겼을 수 있다 — 지금 평가 시작일과 저장된 마감일로 순서(R7)를 다시 본다
        except _Refuse:
            raise _Refuse("평가 시작일이 마감일보다 늦어요. 마감일을 다시 저장해 주세요.", 409) from None
        ev = date.fromisoformat(rnd["evalStart"])
        if auth._today() < ev:
            raise _Refuse(f"평가 시작일({ev.month}/{ev.day})부터 시작할 수 있습니다.", 403)
        if rnd["started"]:
            raise _Refuse("이미 시작했습니다.", 409)
        rows = _snapshot(conn, year)
        if not rows:
            raise _Refuse("평가 대상이 없습니다.", 409)
        cols = ("user_id", "pool", "division_id", "team_id", "div_name", "team_name", "title", "is_leader", "rater_id", "assigner_id", "writes_kpi", "kpi_version", "stage")
        conn.executemany(f"INSERT INTO ev_targets (year, {', '.join(cols)}) VALUES (?{', ?' * len(cols)})", [(year, *(r[k] for k in cols)) for r in rows])
        conn.executemany("INSERT INTO ev_items (year, user_id, rid) VALUES (?, ?, ?)", [(year, r["user_id"], rid) for r in rows for rid in r["rids"]])
        conn.executemany("INSERT INTO ev_pools (year, pool) VALUES (?, ?)", [(year, k) for k in sorted({r["pool"] for r in rows})])
        conn.execute("UPDATE ev_rounds SET started_at = ?, started_by = ? WHERE year = ?", (auth._iso(auth._now()), g.actor.user_id, year))
        _log(conn, "EVAL_STARTED", "ev_round", year, year=year, count=len(rows))
    push.notify([r["user_id"] for r in rows if r["kpi_version"]], "eval_turn")  # 저장(COMMIT)한 뒤 — 자기평가를 쓸 사람에게
    return jsonify(ok=True, count=len(rows), noKpi=sum(r["writes_kpi"] and r["kpi_version"] is None for r in rows),
                   noRater=sum(r["rater_id"] is None for r in rows))


# ---------------- 근태 ----------------
@bp.post("/api/eval/admin/att-done")
def att_done() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    year = _year(_body().get("year"))
    conn = auth._db()
    with _txn(conn):
        _open(conn, year)
        if conn.execute("UPDATE ev_rounds SET att_done_at = ? WHERE year = ? AND att_done_at IS NULL", (auth._iso(auth._now()), year)).rowcount:
            _log(conn, "EVAL_ATT_DONE", "ev_round", year, year=year)
    return jsonify(ok=True)


@bp.post("/api/eval/admin/attendance")
def attendance() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    b = _body()
    year = _year(b.get("year"))
    conn = auth._db()
    with _txn(conn):
        _open(conn, year)
        done: dict[int, None] = {}  # 같은 사람이 여러 줄이면 한 번만 (뒤 줄이 이긴다)
        locked: dict[int, None] = {}
        missing: dict[int, None] = {}
        for uid, late, x2 in _att_rows(b.get("rows")):
            t = conn.execute("SELECT stage, pool, out_at, grade, late, leave_x2 FROM ev_targets WHERE year = ? AND user_id = ?", (year, uid)).fetchone()
            if t is None:
                missing[uid] = None
            elif uid == g.actor.user_id or t[2] is not None or at(t[0], assign_stage(t[1])) > 0:  # 본인 줄(다른 HR 이) · 제외됐거나 등급이 다음 단계로 넘어갔다(고치면 이미 준 등급이 틀어진다)
                locked[uid] = None
            else:
                late, x2 = t[4] if late is None else late, t[5] if x2 is None else x2
                grade = None if t[3] and above(t[3], cap(None, x2, BANDS)) else t[3]  # 연차 초과 5일 이상이면 이미 준 S·A·B 를 지운다
                conn.execute("UPDATE ev_targets SET late = ?, leave_x2 = ?, grade = ? WHERE year = ? AND user_id = ?", (late, x2, grade, year, uid))
                done[uid] = None
        if done:
            _log(conn, "EVAL_ATTENDANCE", "ev_round", year, year=year, count=len(done))
    return jsonify(ok=True, done=list(done), locked=list(locked), missing=list(missing))


# ---------------- 명단 한 사람 ----------------
@contextmanager
def _person(uid: int) -> Iterator[tuple[sqlite3.Connection, int, tuple]]:
    """HR 이 평가 명단의 한 사람을 고치는 요청의 틀 (HR 403 은 길에서 먼저): 본인 403 → 해 400 → 그해 평가 대상 404. 한 트랜잭션.
    돌려주는 줄 = (단계, 제외한 때, 묶음, KPI 판, 평가자)."""
    if uid == g.actor.user_id:  # 본인 평가는 다른 HR 이 — 자기 평가자 · 제외 · 자기평가 단계를 스스로 바꾸지 못하게
        raise _Refuse("본인 것은 다른 HR이 처리해요.", 403)
    year = _year(_body().get("year"))
    conn = auth._db()
    with _txn(conn):
        row = conn.execute("SELECT stage, out_at, pool, kpi_version, rater_id FROM ev_targets WHERE year = ? AND user_id = ?", (year, uid)).fetchone()
        if row is None:
            raise _Refuse("평가 대상이 아닙니다.", 404)
        yield conn, year, row


@bp.post(f"/api/eval/admin/{UID}/exclude")
def exclude(uid: int) -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    with _person(uid) as (conn, year, (stage, out_at, *_)):
        if out_at is not None:
            raise _Refuse("이미 평가에서 제외했습니다.", 409)
        if at(stage, "SCORE") > 0:
            raise _Refuse("평가자가 점수를 낸 뒤에는 제외할 수 없습니다.", 409)
        v = _body().get("reason")
        why = _chars(v.strip() if isinstance(v, str) else "", "제외 사유")
        if not 0 < len(why) <= TEXT_MAX["out"]:
            raise _Refuse(f"제외 사유를 1~{TEXT_MAX['out']}자로 써 주세요.", 400)
        conn.execute("UPDATE ev_targets SET out_at = ?, out_reason = ?, out_by = ? WHERE year = ? AND user_id = ?",
                     (auth._iso(auth._now()), why, g.actor.user_id, year, uid))
        _log(conn, "EVAL_EXCLUDED", "ev_target", uid, year=year)
    return jsonify(ok=True)


@bp.post(f"/api/eval/admin/{UID}/include")
def include(uid: int) -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    with _person(uid) as (conn, year, (_, out_at, pool, *_)):
        if out_at is None:
            raise _Refuse("평가에서 제외한 사람이 아닙니다.", 409)
        if _pool(_ctx(conn, year), pool)[0] not in ("WAIT", "FIX"):  # 이 사람을 빼고 센 묶음 상태 (화면 evInclude)
            raise _Refuse("이 묶음은 이미 다음 단계로 넘어가 되돌릴 수 없습니다. 먼저 묶음을 되돌려 주세요.", 409)
        conn.execute("UPDATE ev_targets SET out_at = NULL, out_reason = NULL, out_by = NULL WHERE year = ? AND user_id = ?", (year, uid))
        _log(conn, "EVAL_INCLUDED", "ev_target", uid, year=year)
    return jsonify(ok=True)


@bp.post(f"/api/eval/admin/{UID}/skip-self")
def skip_self(uid: int) -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    with _person(uid) as (conn, year, (stage, out_at, _, version, rater)):
        if out_at is not None:
            raise _Refuse("평가에서 제외된 사람입니다.", 409)
        if stage != "SELF":
            raise _Refuse("자기평가 단계가 아닙니다.", 409)
        if version is None:
            raise _Refuse("확정된 KPI가 없습니다.", 409)
        conn.execute("UPDATE ev_targets SET stage = 'SCORE', no_self_at = ?, no_self_by = ? WHERE year = ? AND user_id = ?",
                     (auth._iso(auth._now()), g.actor.user_id, year, uid))
        _log(conn, "EVAL_SKIP_SELF", "ev_target", uid, year=year)
        to = _turn_to(_ctx(conn, year), uid, rater)  # 자기평가 제출과 같다 — 대표이사가 평가자이고 맡겼으면 그 사람 뺀 HR 전원
    push.notify([u for u in to if u != g.actor.user_id], "eval_turn")  # 저장한 뒤 (평가자가 없으면 아무도) — 누른 HR 본인에게는 보내지 않는다
    return jsonify(ok=True)


@bp.post(f"/api/eval/admin/{UID}/rater")
def rater_set(uid: int) -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    with _person(uid) as (conn, year, (stage, *_)):
        if stage not in ("SELF", "SCORE"):
            raise _Refuse("평가자가 이미 점수를 냈습니다.", 409)
        rid = _body().get("raterId")
        if not (type(rid) is int and 0 < rid <= 2147483647 and rid != uid
                and conn.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1 AND portal_enabled = 1", (rid,)).fetchone()):  # 로그인할 수 없는 사람은 점수를 못 낸다
            raise _Refuse("평가자는 본인이 아닌, 포털을 쓰는 재직 중인 직원으로 골라 주세요.", 400)
        conn.execute("UPDATE ev_targets SET rater_id = ? WHERE year = ? AND user_id = ?", (rid, year, uid))
        _log(conn, "EVAL_RATER_SET", "ev_target", uid, year=year, raterId=rid)
    return jsonify(ok=True)


# ---------------- 보는 사람에 맞춘 판단 · 줄 (쓰기 길도 이것으로 막고, 응답 줄도 이것으로 깎는다) ----------------
POOL_NAMES = {"LEADERS": "전사 팀장", "DIRECT": "직속부서 팀원"}  # DIV:n 은 "<본부 이름> 팀원"
ITEM_KEYS = ("rid", "team", "personal", "action", "formula", "weight", "S", "A", "B", "C", "D")  # 응답의 실행목표 칸
SELF_KEEP = ("pid", "name", "title", "team", "div", "poolKey", "poolName", "kpi", "stage", "out", "noSelf", "late", "leaveOver", "items", "ev", "can",
             "hidden", "appeal")  # 본인 줄(공개 전)에 남기는 칸 — 나머지(평가 칸 · 나중에 더할 칸)는 열쇠째 빠진다
SELF_EV_KEEP = ("rid", "note", "selfGrade", "files")  # 본인 줄의 ev[] 에 남기는 칸 (평가자 등급 grade 는 빠진다)


def _pool_rank(key: str) -> tuple:
    """묶음 차례: 본부 팀원(본부 번호 차례) → 전사 팀장 → 직속부서 팀원."""
    return (0, int(key[4:])) if key.startswith("DIV:") else (1, key == "DIRECT")


def _who(ctx: _Ctx, uid: int | None) -> str:
    """"이름 직급" (없으면 빈 글자)."""
    return " ".join(filter(None, ctx.names[uid])) if uid is not None else ""


def _pool_name(key: str, div_name: str) -> str:
    return f"{div_name} 팀원" if key.startswith("DIV:") else POOL_NAMES[key]


def _num(d: Decimal | None) -> float | None:
    return None if d is None else float(d)


def _target(row: sqlite3.Row) -> Target:
    """떠 둔 줄 → access 의 대상 (팀 · 본부 · 팀장인가 · 직속 묶음인가 — 보는 수준 · 결과 범위 · 이의 확인 권한이 쓴다)."""
    return Target(row["user_id"], row["team_id"], row["division_id"], Role.TEAM_LEADER if row["is_leader"] else None, direct=row["pool"] == "DIRECT")


def _appeal_json(ctx: _Ctx, me: Actor, uid: int) -> dict | None:
    """줄의 이의 신청 (없으면 None): 본인 · HR · 대표이사 = 전부 — 본인에게는 끝나기(DONE) 전 답변 · 바뀔 등급 · 고친 실행목표 등급을 null 로
    (대표이사 확인을 기다리는 변경 · 돌려보내져 HR 이 고칠 답변 — 화면 evAppealBox 의 full) / 그 밖(팀장 · 본부장 …) = 상태만."""
    a = ctx.appeals.get(uid)
    if a is None:
        return None
    if not (uid == me.user_id or me.is_hr or me.position is Role.CEO):
        return {"status": a["status"]}
    cut = uid == me.user_id and a["status"] != "DONE"
    return {"reason": a["reason"], "at": a["at"], "status": a["status"], "before": a["before"], "answer": None if cut else a["answer"],
            "after": None if cut else a["after"], "marks": None if cut or a["marks"] is None else json.loads(a["marks"])}


def _can(ctx: _Ctx, me: Actor, row: sqlite3.Row) -> dict[str, bool]:
    """화면이 쓰는 판단 — 서버의 쓰기 길도 이 함수로 막는다 (같은 판단을 두 벌 두지 않는다). 대상 줄은 평가를 시작해야 생긴다.
    self 자기평가(본인 · SELF · 오늘 ≤ 자기평가 마감) · upload 증빙(본인 · 평가 시작일 ≤ 오늘 ≤ 증빙 마감 · SELF/SCORE) — 둘 다 제외 안 됨 · KPI 있음 /
    score 점수(SCORE · 평가자가 나, 또는 평가자가 대표이사이고 맡은 HR) / assign 등급 배정(공개 전 · 배정 단계 · 배정자가 나, 또는 배정자가 대표이사이고 맡은 HR) /
    bonus 가점(팀장 묶음은 평가자도, 그 밖은 배정자만) · head 윗사람 코멘트(배정자 — 팀장 묶음 빼고) /
    evidence 증빙 보기(본인 · HR — 자기평가를 낸 뒤(SELF 가 아님)에는 평가자 · 배정자 · 그 본부의 본부장 · 대표이사도) /
    behalf = score · assign 이 '대표이사 대신'으로 참이 됐나 (화면의 대리 입력 표시)."""
    rnd, uid, key, stage = ctx.round, row["user_id"], row["pool"], row["stage"]
    today, mine, on = auth._today().isoformat(), uid == me.user_id, row["out_at"] is None and not rnd["released"]
    ok, rater, who = _behalf_ok(ctx, me, key), row["rater_id"], _assigner(ctx, row)
    score = on and stage == "SCORE"
    s_own = score and rater == me.user_id
    s_behalf = score and not s_own and _is_ceo(ctx, rater) and ok
    assign = on and stage == assign_stage(key)
    a_own = assign and who == me.user_id
    a_behalf = assign and not a_own and _is_ceo(ctx, who) and ok
    self_on = mine and on and row["kpi_version"] is not None
    return {"self": self_on and stage == "SELF" and today <= rnd["selfTo"],
            "upload": self_on and stage in ("SELF", "SCORE") and rnd["evalStart"] <= today <= rnd["evidDue"],
            "score": s_own or s_behalf,
            "assign": a_own or a_behalf,
            "bonus": ((s_own or s_behalf) and key == "LEADERS") or a_own or a_behalf,
            "head": (a_own or a_behalf) and key != "LEADERS",
            "evidence": mine or me.is_hr or (stage != "SELF" and (me.position is Role.CEO or me.user_id in (rater, who)
                                                                 or (me.led_division_id is not None and me.led_division_id == row["division_id"]))),
            "behalf": s_behalf or a_behalf}


def _row_json(ctx: _Ctx, me: Actor, row: sqlite3.Row) -> dict | None:
    """보는 사람에 맞춰 깎은 한 사람 줄 (칸 이름은 화면 evPeople) — 사람 줄을 응답에 넣는 길은 모두 이 함수를 지난다. 못 보면(level none) None.
    full = 전부 (edited 는 고친 내용 글자) / mine = 평가자가 낸 뒤 · 공개 전: 자기가 낸 등급 · 항목 등급 · 총점만 — 지금 등급 · 항목 등급 · 가점 · 윗사람 코멘트 ·
    대리 입력 · 고친 내용 글자는 어디에도 없고 고쳤으면 edited 참 / self = 본인 · 공개 전: 자기평가(실적 · 자기 등급 · 증빙)만 — 평가 칸은 null 이 아니라 열쇠째 뺀다.
    자기평가를 내기 전(SELF)에는 본인 · HR 이 아니면 ev[] 에서 실적 · 자기 등급 · 증빙을 열쇠째 뺀다 (초안 — 번호 · 평가자 등급 칸만).
    assignerIsCeo = 등급을 배정하는 사람이 대표이사인가 (화면의 '본부장' · '대표이사' 글자 — 등급 · 점수와 상관없다). appeal = _appeal_json (공개 뒤에만 생긴다)."""
    uid, key = row["user_id"], row["pool"]
    t = _target(row)
    who = _assigner(ctx, row)
    lv = level(me, t, rater=row["rater_id"], assigner=who, stage=row["stage"], released=bool(ctx.round["released"]))
    if lv == "none":
        return None
    acts, score, ed, rater, bands = _can(ctx, me, row), _total(ctx, row), _edited(ctx, row), row["rater_id"], _bands_of(ctx)
    draft = row["stage"] == "SELF" and uid != me.user_id and not me.is_hr  # 내기 전 자기평가 — 본인 · HR 만 본다
    sub = None if row["sub_score_x10"] is None else Decimal(row["sub_score_x10"]) / 10
    marks = json.loads(row["sub_marks"]) if row["sub_marks"] else None
    ev = []
    for it in ctx.items[uid]:
        e = ctx.entries[(uid, it["rid"])]
        ev.append({"rid": it["rid"], "grade": e["grade"]} if draft else
                  {"rid": it["rid"], "note": e["note"], "selfGrade": e["self_grade"], "grade": e["grade"],
                   "files": [{"id": f["id"], "name": f["name"], "size": f["size"]} for f in ctx.files.get((uid, it["rid"]), [])] if acts["evidence"] else []})
    out = {"pid": uid, "name": ctx.names[uid][0], "title": row["title"] or "", "team": row["team_name"], "div": row["div_name"],
           "poolKey": key, "poolName": _pool_name(key, row["div_name"]), "kpi": row["kpi_version"] is not None, "stage": row["stage"],
           "out": {"at": row["out_at"], "why": row["out_reason"]} if row["out_at"] else None,
           "noSelf": _day(row["no_self_at"]).isoformat() if row["no_self_at"] else "", "late": row["late"], "leaveOver": row["leave_x2"] / 2,
           "items": [{k: it.get(k) for k in ITEM_KEYS} for it in ctx.items[uid]], "ev": ev,
           "grade": row["grade"], "proposed": row["proposed"], "subScore": _num(sub), "subMarks": marks, "comment": row["comment"],
           "headComment": row["head_comment"], "bonus": row["bonus_x2"] / 2, "bonusReason": row["bonus_reason"],
           "total": _num(score), "cap": cap(score, row["leave_x2"], bands), "proxy": _who(ctx, row["proxy_by"]),
           "edited": ed, "editSeen": bool(ed) and row["edit_seen"] == ed,
           "rater": None if rater is None else {"id": rater, "name": ctx.names[rater][0], "title": ctx.names[rater][1], "canLogin": rater in ctx.login},
           "raterIsCeo": _is_ceo(ctx, rater), "assignerIsCeo": _is_ceo(ctx, who), "can": acts, "hidden": lv == "mine", "appeal": _appeal_json(ctx, me, uid)}
    if lv == "mine":
        out.update(grade=row["proposed"], total=_num(sub), cap=cap(sub, row["leave_x2"], bands), bonus=0, bonusReason="", headComment="", proxy="",
                   edited=True if ed else "")
        for e in ev:
            e["grade"] = (marks or {}).get(str(e["rid"]))
    elif lv == "self":
        out = {k: out[k] for k in SELF_KEEP}
        out["ev"] = [{k: e[k] for k in SELF_EV_KEEP} for e in ev]
    return out


def _turn_to(ctx: _Ctx, uid: int, who: int | None) -> list[int | None]:
    """uid 의 다음 차례(who — 평가자 · 배정자)가 됐다는 알림의 받는 사람: who 가 대표이사이고 맡겼으면 uid 뺀 HR 전원, 아니면 who (없으면 아무도 — push.notify 가 None 을 뺀다).
    자기평가 제출 · 자기평가 없이 진행 · 평가자 제출이 함께 쓴다."""
    return [h for h in push.hr_admins(ctx.conn) if h != uid] if _is_ceo(ctx, who) and ctx.deleg else [who]


def _pool_auth(ctx: _Ctx, me: Actor, key: str) -> dict[str, bool]:
    """묶음 단추를 누를 사람인가 — 권한만 (공개 · 묶음 상태는 _pool_can 이 더한다. 쓰기 길의 403 은 이것으로).
    close 마감 = 본부 팀원 묶음 · 배정자가 나, 또는 배정자가 대표이사이고 그 묶음에 들지 않은 맡은 HR /
    confirm 확인 · back 돌려보내기(본부 팀원 묶음만) = 활성 대표이사, 또는 그 묶음에 들지 않은 맡은 HR (_behalf_ok — 제외된 줄도 든 것으로 본다) /
    finalize 확정 · undo 되돌리기 = HR (HR 본인 일이라 자기가 든 묶음도 된다)."""
    who, div = _pool_who(ctx, key), key.startswith("DIV:")
    ceo = _is_ceo(ctx, me.user_id) or _behalf_ok(ctx, me, key)
    return {"close": div and (who == me.user_id or (_is_ceo(ctx, who) and _behalf_ok(ctx, me, key))),
            "confirm": ceo, "back": div and ceo, "finalize": me.is_hr, "undo": me.is_hr}


POOL_NEED = {"confirm": ("CEO",), "back": ("CEO", "FIX"), "finalize": ("HR",), "undo": ("HR", "DONE")}  # 단추마다 있어야 할 묶음 상태 (마감은 쓰기 길이 단계 · 정원을 따로 본다)
# 돌려보내기는 FIX 에서도 — 마감 뒤 기준 점수가 바뀌어 등급이 어긋나면 대표이사는 본부 팀원 등급을 못 고치니 돌려보내는 것이 빠져나갈 길이다.


def _pool_can(ctx: _Ctx, me: Actor, key: str) -> dict[str, bool]:
    """묶음 단추 (화면 can) = 권한(_pool_auth) · 공개 전 · 그 단추의 묶음 상태(POOL_NEED). 마감은 상태를 보지 않는다 — 단계 · 정원은 state · errors 로 화면이 막고 쓰기 길이 409 로 다시 본다."""
    state, on = _pool(ctx, key)[0], not ctx.round["released"]
    return {k: v and on and (k == "close" or state in POOL_NEED[k]) for k, v in _pool_auth(ctx, me, key).items()}


def _pool_label(ctx: _Ctx, key: str) -> str:
    """묶음 이름 ("<본부 이름> 팀원" · "전사 팀장" · "직속부서 팀원") — 묶음 줄이 있으면 떠 둔 사람도 있다 (제외된 줄 포함)."""
    return _pool_name(key, next(r["div_name"] for r in ctx.rows.values() if r["pool"] == key))


def _pool_json(ctx: _Ctx, me: Actor, key: str) -> dict:
    """묶음 한 줄 — 배정자 · 대표이사 · HR 에게만 보낸다 (오류 문구에 이름 · 총점 · 등급이 든다)."""
    state, errors, warnings, counts, rows = _pool(ctx, key)
    p, q = ctx.pools[key], calculate_department_quota(len(rows))

    def act(when: str, by: str) -> dict | None:
        return {"at": p[when], "by": _who(ctx, p[by])} if p[when] else None

    return {"key": key, "name": _pool_label(ctx, key), "size": len(rows), "state": state,
            "counts": counts, "errors": errors, "warnings": warnings,
            "quota": (f"S 최대 {q.cap_s}명 · A {q.a}명 (S만큼 줄어듦) · B {q.b}명 · D {q.d}명 · C 나머지" if q.mode is QuotaMode.INDIVIDUAL
                      else f"S 최대 {q.cap_s}명 · A·B 합계 {q.upper}명 (S만큼 줄어듦) · C·D 나머지"),  # 화면 evGradeTable 의 정원 글자
            "log": {"closed": act("closed_at", "closed_by"), "ceo": act("ceo_at", "ceo_by"),
                    "back": p["back_at"] and {**act("back_at", "back_by"), "reason": p["back_reason"]},
                    "undo": p["undo_at"] and {**act("undo_at", "undo_by"), "from": p["undo_from"]}},
            "hrSeen": me.is_hr and me.user_id in json.loads(p["hr_seen"]), "can": _pool_can(ctx, me, key)}


def _appeal_can(ctx: _Ctx, me: Actor, row: sqlite3.Row) -> bool:
    """이의 신청의 등급 변경을 확인 · 돌려보낼 사람인가 (access.can APPEAL_CONFIRM): 활성 대표이사, 또는 맡김이고 그 이의가 자기 것이 아닌 HR."""
    return (_is_ceo(ctx, me.user_id) or (me.is_hr and ctx.deleg)) and bool(
        can(me, Act.APPEAL_CONFIRM, _target(row), delegation=Delegation(ctx.ceo) if ctx.deleg else None))


def _appeals_for(ctx: _Ctx, me: Actor) -> list[sqlite3.Row]:
    """보는 사람이 처리할 이의 신청 (신청한 사람의 ev_targets 줄, 명단 차례) — 처리할 일 e3 와 결과의 appeals 가 함께 쓴다:
    HR = 남의 OPEN(답변할 것) · 대표이사(맡겼으면 HR) = 확인할 수 있는 CEO(등급 변경 확인 — _appeal_can)."""
    rows = [ctx.rows[u] for u, a in ctx.appeals.items()
            if (a["status"] == "OPEN" and me.is_hr and u != me.user_id) or (a["status"] == "CEO" and _appeal_can(ctx, me, ctx.rows[u]))]
    return sorted(rows, key=lambda r: _order(ctx, r))


def _todo(ctx: _Ctx, me: Actor) -> dict[str, int]:
    """처리할 일 숫자 {me, admin, e3} — 한 곳: 화면은 실제 로그인이면 이 숫자를 그대로 보이고(EV_LIVE.todo), 휴대폰 숫자(push.pending 의 e2 = me + admin · e3)도
    eval_todo 로 같은 셈. e3 = 처리할 이의 신청 수(_appeals_for) — 공개 뒤에도 센다. me · admin 은 시작 전 · 공개 뒤 0. 제외된 사람은 세지 않는다. 묶음은 한 번만 센다 — 상태 CEO · FIX 면 '확인할 묶음': 대표이사 me, 맡김이면 그 묶음에 들지 않은 HR admin /
    그 밖에 배정 단계(assign_stage) 사람이 있으면 '배정할 묶음': 배정자(_pool_who — 본부장, 본부장 없는 본부 · 직속 · 전사 팀장은 대표이사) me,
    배정자가 대표이사이고 맡김이면 그 묶음에 들지 않은 HR admin.
    me = 낼 자기평가 + 점수 낼 사람(대신 아님) + 고친 것을 아직 확인 안 한 사람 + 묶음(위) /
    admin(HR 만) = 묶음(위) + 확정할 묶음(HR) + 확인 안 한 마감 알림 + 근태 안 마침 1 + 대표이사가 평가자인 점수 단계 사람 중 내가 대신할 수 있는 사람(맡김)."""
    rnd, e3 = ctx.round, len(_appeals_for(ctx, me))
    if not rnd or not rnd["started"] or rnd["released"]:
        return {"me": 0, "admin": 0, "e3": e3}
    live = [r for r in ctx.rows.values() if r["out_at"] is None]
    state = {k: _pool(ctx, k)[0] for k in ctx.pools}
    mine = ctx.rows.get(me.user_id)
    todo_me = (int(mine is not None and _can(ctx, me, mine)["self"])
               + sum(r["stage"] == "SCORE" and r["rater_id"] == me.user_id for r in live)
               + sum(r["rater_id"] == me.user_id and bool(ed := _edited(ctx, r)) and r["edit_seen"] != ed for r in live))
    admin = 0
    for k, s in state.items():
        if s in ("CEO", "FIX"):
            who = ctx.ceo
        elif any(r["pool"] == k and r["stage"] == assign_stage(k) for r in live):
            who = _pool_who(ctx, k)
        else:
            continue
        todo_me += who == me.user_id
        admin += _is_ceo(ctx, who) and _behalf_ok(ctx, me, k)  # _behalf_ok = 맡김 · HR · 그 묶음에 들지 않음
    if me.is_hr:
        admin += (sum(s == "HR" for s in state.values())
                  + sum(p["closed_at"] is not None and me.user_id not in json.loads(p["hr_seen"]) and state[k] == "CEO" for k, p in ctx.pools.items())
                  + (0 if rnd["attDone"] else 1)
                  + sum(r["stage"] == "SCORE" and _is_ceo(ctx, r["rater_id"]) and _behalf_ok(ctx, me, r["pool"]) for r in live))
    return {"me": todo_me, "admin": admin, "e3": e3}


def eval_todo(conn: sqlite3.Connection, actor: Actor) -> dict[str, int]:
    """지금 보일 해(cur_year)의 처리할 일 숫자 {"me", "admin", "e3"} — 휴대폰 숫자(push.pending 의 e2 = me + admin · e3)가 쓴다.
    포털 평가 전 해 · 회차 없음은 모두 0, 시작 전 · 공개 뒤는 me · admin 0 (e3 는 공개 뒤에도 센다)."""
    return _todo(_ctx(conn, cur_year(conn)), actor)


# ---------------- 읽기 ----------------
@bp.get("/api/eval/state")
def state() -> Response:
    conn, me, raw = auth._db(), g.actor, request.args.get("year")
    year = cur_year(conn) if raw is None else _asked(raw)
    if year < FIRST_YEAR:  # 포털 밖에서 평가하는 해 — 화면은 안내만 (R2)
        ceo = _ceo(conn)
        return jsonify(year=year, offline=True, round=None, deleg=_deleg(conn, ceo), ceoId=ceo, me=None, people=[], pools=[], todo={"me": 0, "admin": 0, "e3": 0})
    everyone = me.is_hr or me.position is Role.CEO
    with _txn(conn):  # 한 번에 읽는다 — 읽는 사이에 단계가 바뀌어 보는 수준과 칸이 어긋나지 않게
        ctx = _ctx(conn, year)
        mine = ctx.rows.get(me.user_id)
        people = [j for r in sorted(ctx.rows.values(), key=lambda r: _order(ctx, r))
                  if r["user_id"] != me.user_id and (everyone or me.user_id in (r["rater_id"], _assigner(ctx, r))) and (j := _row_json(ctx, me, r)) is not None]
        body = {"year": year, "offline": False, "round": ctx.round, "deleg": ctx.deleg, "ceoId": ctx.ceo,
                "me": None if mine is None else _row_json(ctx, me, mine), "people": people,
                "pools": [_pool_json(ctx, me, k) for k in sorted(ctx.pools, key=_pool_rank)
                          if everyone or _pool_who(ctx, k) == me.user_id],
                "todo": _todo(ctx, me)}
    return jsonify(body)


@bp.get("/api/eval/results")
def results() -> Response:
    """평가 결과 (머리말의 '결과') — 기본 해는 보일 해(cur_year)가 시작돼 있으면 그해(공개 전이어도 — 본인 '아직 공개 전' · HR 공개 화면), 아니면 공개한 해 중 최근."""
    conn, me, raw = auth._db(), g.actor, request.args.get("year")
    asked = None if raw is None else _asked(raw)
    with _txn(conn):  # 한 번에 읽는다 (상태와 같다)
        years = [y for (y,) in conn.execute("SELECT year FROM ev_rounds WHERE released_at IS NOT NULL ORDER BY year DESC")]
        year = asked
        if year is None:
            year = cur_year(conn)
            if years and not conn.execute("SELECT 1 FROM ev_rounds WHERE year = ? AND started_at IS NOT NULL", (year,)).fetchone():
                year = years[0]
        body = {"years": years, "year": year, "offline": year < FIRST_YEAR, "released": None, "appealDue": None, "mine": None, "team": [], "appeals": []}
        ctx = None if year < FIRST_YEAR else _ctx(conn, year)
        if ctx and ctx.round and ctx.round["started"]:
            rel, mine = ctx.round["released"], ctx.rows.get(me.user_id)
            seen = [r for r in sorted(ctx.rows.values(), key=lambda r: _order(ctx, r)) if r["user_id"] != me.user_id
                    and (me.is_hr or (rel and r["out_at"] is None and can(me, Act.VIEW_RESULT, _target(r))))]  # 조직 위치로 — 평가자 · 배정자라서가 아니라
            body.update(released=rel, appealDue=(_day(rel) + timedelta(days=APPEAL_DAYS)).isoformat() if rel else None,
                        mine=None if mine is None else _row_json(ctx, me, mine),
                        team=[j for r in seen if (j := _row_json(ctx, me, r)) is not None],
                        appeals=[j for r in _appeals_for(ctx, me) if (j := _row_json(ctx, me, r)) is not None])
    return jsonify(body)


MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "xls": "application/vnd.ms-excel", "doc": "application/msword",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "hwp": "application/octet-stream", "hwpx": "application/octet-stream"}  # 증빙 종류(ev_files.kind) → 내려받을 때의 Content-Type


def _files_dir() -> Path:
    """증빙 파일을 두는 곳 — 공개 폴더 밖, DB 옆 (없으면 만든다). 파일 이름은 ev_files.id 하나 (사람이 준 이름을 경로에 쓰지 않는다)."""
    d = Path(os.environ["HR_DB_PATH"]).parent / "eval_files"
    d.mkdir(exist_ok=True)
    return d


@bp.get("/api/eval/files/<int(max=2147483647):fid>")
def file_get(fid: int) -> Response:
    conn = auth._db()
    with _txn(conn):
        f = conn.execute("SELECT year, user_id, name, kind FROM ev_files WHERE id = ?", (fid,)).fetchone()
        if f is None:
            raise _Refuse("증빙을 찾을 수 없습니다.", 404)
        ctx = _ctx(conn, f[0])
        if not _can(ctx, g.actor, ctx.rows[f[1]])["evidence"]:
            raise _Refuse("이 증빙을 볼 권한이 없습니다.", 403)
    path = _files_dir() / str(fid)
    if not path.is_file():
        raise _Refuse("증빙 파일이 서버에 없습니다.", 404)
    resp = Response(path.read_bytes(), mimetype=MIME[f[3]])
    resp.headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(f[2], safe='')}"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------- 자기평가 (본인) ----------------
def _mine(conn: sqlite3.Connection, year: int) -> tuple[_Ctx, sqlite3.Row]:
    """본인의 그해 평가 줄 — 평가 대상이 아니면 404. 사람 번호는 늘 세션(g.actor)에서 쓴다: 요청이 보낸 번호로 남의 것을 쓸 길이 없다."""
    ctx = _ctx(conn, year)
    row = ctx.rows.get(g.actor.user_id)
    if row is None:
        raise _Refuse("이번 평가 대상이 아닙니다.", 404)
    return ctx, row


def _self_open(ctx: _Ctx, row: sqlite3.Row) -> None:
    """자기평가를 쓸 수 있나 — _can 의 self 가 거짓이면 403. 마감일이 지나서면(아직 SELF 단계인 사람) 그 날짜를 알려 준다."""
    if _can(ctx, g.actor, row)["self"]:
        return
    due = ctx.round["selfTo"]
    late = row["stage"] == "SELF" and row["out_at"] is None and not ctx.round["released"] and auth._today().isoformat() > due
    raise _Refuse(f"자기평가 마감일({due})이 지났습니다. HR에 문의해 주세요." if late else "지금은 자기평가를 쓸 수 없습니다.", 403)


def _words(v: object, label: str, limit: int, josa: str = "는") -> str:
    """사람이 쓴 글 — 앞뒤 공백을 뗀 글이 limit 자 안이어야 하고(_chars 규칙도), 뗀 글을 저장한다 (kpi._text · 제외 사유와 같게). 아니면 400."""
    if not isinstance(v, str):
        raise _Refuse(f"{label}{josa} 글로 써 주세요.", 400)
    t = _chars(v.strip(), label)
    if len(t) > limit:
        raise _Refuse(f"{label}{josa} {limit}자 안으로 써 주세요.", 400)
    return t


def _note(v: object) -> str:
    """실적 글 (자기평가)."""
    return _words(v, "실적", TEXT_MAX["note"], "은")


def _grade(v: object, what: str = "달성 등급") -> str | None:
    if v is not None and v not in GRADES:
        raise _Refuse(f"{what}은 S · A · B · C · D 중에서 골라 주세요.", 400)
    return v


@bp.post("/api/eval/me/self")
def self_save() -> Response:
    b = _body()
    year = _year(b.get("year"))
    conn = auth._db()
    with _txn(conn):
        ctx, row = _mine(conn, year)
        _self_open(ctx, row)
        uid, ents, puts = row["user_id"], b.get("entries"), {}
        if not isinstance(ents, list):
            raise _Refuse("실행목표별 실적을 목록으로 보내 주세요.", 400)
        for e in ents:  # 모두 확인한 뒤에 쓴다 — 하나라도 틀리면 아무것도 저장하지 않는다. 보낸 줄의 보낸 칸만 바뀐다.
            rid = e.get("rid") if isinstance(e, dict) else None
            cur = ctx.entries.get((uid, rid)) if type(rid) is int else None
            if cur is None:
                raise _Refuse("실행목표를 찾을 수 없습니다.", 400)
            if rid in puts:
                raise _Refuse("실행목표는 한 번씩만 보내 주세요.", 400)
            puts[rid] = (_note(e["note"]) if "note" in e else cur["note"], _grade(e["selfGrade"]) if "selfGrade" in e else cur["self_grade"], year, uid, rid)
        conn.executemany("UPDATE ev_items SET note = ?, self_grade = ? WHERE year = ? AND user_id = ? AND rid = ?", puts.values())
        ctx = _ctx(conn, year)
        me = _row_json(ctx, g.actor, ctx.rows[uid])
    return jsonify(ok=True, me=me)


@bp.post("/api/eval/me/submit")
def self_submit() -> Response:
    year = _year(_body().get("year"))
    conn = auth._db()
    with _txn(conn):
        ctx, row = _mine(conn, year)
        _self_open(ctx, row)
        uid, rater = row["user_id"], row["rater_id"]
        if not all(e["note"].strip() and e["self_grade"] for e in (ctx.entries[(uid, it["rid"])] for it in ctx.items[uid])):
            raise _Refuse("모든 KPI에 실적과 달성 등급을 넣어 주세요.", 400)
        conn.execute("UPDATE ev_targets SET stage = 'SCORE', self_at = ? WHERE year = ? AND user_id = ?", (auth._iso(auth._now()), year, uid))
        _log(conn, "EVAL_SELF_SUBMITTED", "ev_target", uid, year=year)
        to = _turn_to(ctx, uid, rater)  # 맡겼으면 HR 전원 (제출한 사람이 HR 이면 본인은 빼고)
    push.notify(to, "eval_turn")  # 저장(COMMIT)한 뒤 — 평가자가 없으면 아무도
    return jsonify(ok=True)


# ---------------- 증빙 올리기 · 지우기 (본인) ----------------
OLE, ZIP = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", b"PK\x03\x04"
HEADS = {"pdf": b"%PDF-", "png": b"\x89PNG\r\n\x1a\n", "jpg": b"\xff\xd8\xff", "xlsx": ZIP, "docx": ZIP, "hwpx": ZIP, "xls": OLE, "doc": OLE, "hwp": OLE}  # 종류 → 파일 첫 바이트
FORM_SLACK = 64 * 1024  # 올리는 몸통에서 파일 밖의 몫 (다른 칸 · 경계) — 파일 한도에 이만큼을 더한 것보다 크면 읽기 전에 거른다
TOO_BIG = f"증빙 파일은 {FILE_MAX // 1048576}MB까지 올릴 수 있습니다."


def _kind(head: bytes, name: str) -> str | None:
    """증빙 종류 — 이름의 확장자(소문자, jpeg 는 jpg)가 허용 목록에 있고 파일 첫 바이트가 그 종류와 맞아야 한다. 아니면 None."""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    kind = "jpg" if ext == "jpeg" else ext
    return kind if kind in HEADS and head.startswith(HEADS[kind]) else None


def _clean_name(raw: str) -> str:
    """올린 이름 → 보여 줄 이름(120자 줄이기 전): 경로(/ · \\ 앞)를 떼고 제어 · 서식 문자(방향 바꾸기 등)를 빼고 앞뒤 공백을 뗀다. 저장 경로에는 쓰지 않는다 (파일 이름은 번호 하나)."""
    t = raw.replace("\\", "/").rsplit("/", 1)[-1]
    return "".join(ch for ch in t if unicodedata.category(ch) not in ("Cc", "Cf")).strip()


def _short(name: str) -> str:
    """이름이 120자를 넘으면 확장자(쓴 그대로)는 남기고 이름 쪽을 잘라 120자로 맞춘다."""
    if len(name) <= 120:
        return name
    stem, _, ext = name.rpartition(".")
    return f"{stem[:120 - len(ext) - 1]}.{ext}"


def _digits(v: str | None) -> int | None:
    """폼 칸의 번호 → 정수 (ASCII 숫자 열 자리까지). 아니면 None."""
    return int(v) if v and v.isascii() and v.isdigit() and len(v) <= 10 else None


@bp.post("/api/eval/files")
def file_put() -> Response:
    if (request.content_length or 0) > FILE_MAX + FORM_SLACK:  # 읽기 전에 — 큰 몸통을 임시 파일로 받지 않게
        raise _Refuse(TOO_BIG, 400)
    year, rid = _digits(request.form.get("year")), _digits(request.form.get("rid"))
    conn = auth._db()
    with _txn(conn):
        ctx, row = _mine(conn, _year(year))
        uid = row["user_id"]
        if not _can(ctx, g.actor, row)["upload"]:
            raise _Refuse("증빙 마감일이 지났거나 올릴 수 없는 단계입니다.", 403)
        if (uid, rid) not in ctx.entries:
            raise _Refuse("증빙을 붙일 실행목표를 찾을 수 없습니다.", 400)
        if len(ctx.files.get((uid, rid), ())) >= FILES_PER_ITEM:
            raise _Refuse(f"실행목표 하나에 증빙은 {FILES_PER_ITEM}개까지 올릴 수 있습니다.", 400)
        f = request.files.get("file")
        data = f.stream.read(FILE_MAX + 1) if f else b""
        if not data:
            raise _Refuse("올릴 파일이 비어 있습니다.", 400)
        if len(data) > FILE_MAX:
            raise _Refuse(TOO_BIG, 400)
        name = _clean_name(f.filename or "")
        kind = _kind(data[:8], name)
        if kind is None:
            raise _Refuse("PDF · 그림(PNG·JPG) · 엑셀 · 워드 · 한글 파일만 올릴 수 있습니다.", 400)
        fid = conn.execute("INSERT INTO ev_files (year, user_id, rid, name, kind, size, sha256, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                           (year, uid, rid, _short(name) or f"증빙.{kind}", kind, len(data), hashlib.sha256(data).hexdigest(), auth._iso(auth._now()))).lastrowid
        d = _files_dir()
        tmp = d / f"{fid}.part"
        try:
            tmp.write_bytes(data)
            os.replace(tmp, d / str(fid))  # 같은 번호의 옛 파일이 있어도 덮어쓴다 (번호는 다시 쓰지 않지만, 파일을 쓴 뒤 되돌려진 줄의 번호는 다시 받는다)
        except BaseException:  # 줄은 _txn 이 되돌린다 — 임시 파일만 치운다
            tmp.unlink(missing_ok=True)
            raise
    return jsonify(ok=True, id=fid)


@bp.post("/api/eval/files/<int(max=2147483647):fid>/delete")
def file_delete(fid: int) -> Response:
    conn = auth._db()
    with _txn(conn):
        f = conn.execute("SELECT year, user_id FROM ev_files WHERE id = ?", (fid,)).fetchone()
        if f is None:
            raise _Refuse("증빙을 찾을 수 없습니다.", 404)
        if f[1] != g.actor.user_id:
            raise _Refuse("내가 올린 증빙만 지울 수 있습니다.", 403)
        ctx, row = _mine(conn, f[0])
        if not _can(ctx, g.actor, row)["upload"]:
            raise _Refuse("증빙 마감일이 지났거나 지울 수 없는 단계입니다.", 403)
        conn.execute("DELETE FROM ev_files WHERE id = ?", (fid,))
        (_files_dir() / str(fid)).unlink(missing_ok=True)  # 같은 트랜잭션 안에서 — 밖에서 지우면 그 사이에 같은 번호로 올린 새 파일을 지울 수 있다
    return jsonify(ok=True)


# ---------------- 평가자 점수 · 배정자 고치기 (사람 한 명) ----------------
@contextmanager
def _rated(uid: int) -> Iterator[tuple[_Ctx, sqlite3.Row, dict[str, bool]]]:
    """평가자 · 배정자가 한 사람의 평가를 쓰는 요청의 틀: 해 400 → 그해 평가 대상 404. 한 트랜잭션 — 잠근 뒤에 읽은 _ctx 와 그것으로 낸 _can 을 준다
    (화면이 먼저 읽은 상태를 믿지 않는다 — 두 번 눌러도 한 번만 통과)."""
    year = _year(_body().get("year"))
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        row = ctx.rows.get(uid)
        if row is None:
            raise _Refuse("평가 대상이 아닙니다.", 404)
        yield ctx, row, _can(ctx, g.actor, row)


def _within(ctx: _Ctx, row: sqlite3.Row, grade: str | None, score: Decimal | None) -> None:
    """등급이 이 사람에게 줄 수 있는 가장 높은 등급(cap)보다 높으면 400 — 문구는 화면 evSubmitScore 그대로 (연차 먼저, 그 밖은 점수 기준)."""
    bands = _bands_of(ctx)
    if grade is None or not above(grade, cap(score, row["leave_x2"], bands)):
        return
    if row["leave_x2"] >= LEAVE_LIMIT_X2 and grade in "SAB":
        raise _Refuse(f"연차 초과 {LEAVE_LIMIT_X2 // 2}일 이상이라 S·A·B를 줄 수 없습니다.", 400)
    raise _Refuse(f"총점 {score:.1f}점으로는 {grade}를 줄 수 없습니다. ({_band_text(grade, bands)}) 점수를 다시 봐 주세요.", 400)


BONUS_NO = "가점은 등급을 배정하는 분만 줄 수 있습니다."


@bp.post(f"/api/eval/people/{UID}/edit")
def person_edit(uid: int) -> Response:
    b = _body()
    with _rated(uid) as (ctx, row, can):
        if not (can["score"] or can["assign"]):
            raise _Refuse("지금 이 분의 평가를 고칠 수 없습니다.", 403)
        rules = (("comment", can["score"], "평가자 코멘트는 점수를 낼 때만 쓸 수 있습니다."),  # 낸 뒤에는 아무도 못 고친다 — 평가자의 숨긴 줄이 낸 코멘트를 그대로 보인다
                 ("headComment", can["head"], "윗사람 코멘트는 등급을 배정하는 분만 쓸 수 있습니다."), ("bonus", can["bonus"], BONUS_NO), ("bonusReason", can["bonus"], BONUS_NO))
        if why := next((w for k, allowed, w in rules if k in b and not allowed), None):
            raise _Refuse(why, 403)
        kpi_on = row["kpi_version"] is not None
        old = {it["rid"]: ctx.entries[(uid, it["rid"])]["grade"] for it in ctx.items[uid]}
        marks, bonus = dict(old), row["bonus_x2"]
        if "marks" in b:  # 모두 확인한 뒤에 쓴다 — 하나라도 틀리면 아무것도 저장하지 않는다
            if not kpi_on:
                raise _Refuse("KPI가 없는 분은 등급만 매깁니다.", 400)
            if not isinstance(b["marks"], dict):
                raise _Refuse("실행목표별 등급을 실행목표 번호마다 보내 주세요.", 400)
            for k, v in b["marks"].items():
                rid = int(k) if k.isascii() and k.isdigit() and len(k) <= 10 else None
                if rid not in marks:
                    raise _Refuse("실행목표를 찾을 수 없습니다.", 400)
                if v is None and row["stage"] != "SCORE":  # 비우는 것은 평가자의 점수 단계 초안만 — 배정 단계에서 비우면 낸 점수가 빈 채로 넘어간다
                    raise _Refuse("달성 등급을 골라 주세요.", 400)
                marks[rid] = _grade(v)
        if "bonus" in b:
            v = b["bonus"]
            if not kpi_on:
                raise _Refuse("KPI가 없는 분에게는 가점을 줄 수 없습니다.", 400)
            if not (type(v) in (int, float) and 0 <= v * 2 <= BONUS_MAX_X2 and v * 2 == int(v * 2)):  # 참/거짓 · 글자 · 무한 · NaN 아님 (범위를 먼저 봐서 int(inf) 를 안 부른다)
                raise _Refuse("가점은 0~10점, 0.5점 단위로 넣어 주세요.", 400)
            bonus = int(v * 2)
        reason = _words(b["bonusReason"], "가점 사유", TEXT_MAX["bonusReason"]) if "bonusReason" in b else row["bonus_reason"]
        if bonus and not reason:
            raise _Refuse("가점 사유를 입력해 주세요.", 400)
        comment = _words(b["comment"], "평가자 코멘트", TEXT_MAX["comment"]) if "comment" in b else row["comment"]
        head = _words(b["headComment"], "윗사람 코멘트", TEXT_MAX["head"]) if "headComment" in b else row["head_comment"]
        score = total([(it["weight"], marks[it["rid"]]) for it in ctx.items[uid]], bonus) if kpi_on else None
        grade = row["grade"]
        if "grade" in b:  # 보낸 등급이 이긴다 — 검사는 아래 _within
            grade = _grade(b["grade"], "등급")
        elif kpi_on:  # 화면 evFit: 점수 단계에서 채점을 마치면 점수에 맞는 가장 높은 등급이 저절로 나오고, 그 밖에는 기준보다 높아진 등급만 내린다 (현장직은 점수가 없어 맞추지 않는다)
            top = cap(score, row["leave_x2"], _bands_of(ctx))
            if row["stage"] == "SCORE" and all(marks.values()) and (marks != old or bonus != row["bonus_x2"]):
                grade = top
            elif grade and above(grade, top):
                grade = top
        _within(ctx, row, grade, score)
        conn, year, me = ctx.conn, ctx.year, g.actor.user_id
        conn.executemany("UPDATE ev_items SET grade = ? WHERE year = ? AND user_id = ? AND rid = ?", [(v, year, uid, rid) for rid, v in marks.items() if v != old[rid]])
        conn.execute("UPDATE ev_targets SET grade = ?, comment = ?, head_comment = ?, bonus_x2 = ?, bonus_reason = ?, proxy_by = ? WHERE year = ? AND user_id = ?",
                     (grade, comment, head, bonus, reason, me if can["behalf"] else row["proxy_by"], year, uid))  # 고치기는 감사로그가 없다 (임시 단계)
        ctx = _ctx(conn, year)
        out = _row_json(ctx, g.actor, ctx.rows[uid])
    return jsonify(ok=True, row=out)


@bp.post(f"/api/eval/people/{UID}/submit")
def person_submit(uid: int) -> Response:
    with _rated(uid) as (ctx, row, can):
        if not can["score"]:
            raise _Refuse("지금 이 분의 점수를 낼 수 없습니다.", 403)
        marks = {str(it["rid"]): ctx.entries[(uid, it["rid"])]["grade"] for it in ctx.items[uid]}
        if row["kpi_version"] is not None and not all(marks.values()):
            raise _Refuse("모든 KPI에 달성 등급을 골라 주세요.", 400)
        if row["kpi_version"] is not None and not row["comment"].strip():  # KPI 없는 사람(현장직)은 코멘트가 선택
            raise _Refuse("평가자 코멘트를 입력해 주세요. 직원에게 공개됩니다.", 400)
        who = _assigner(ctx, row)
        if row["grade"] is None:
            raise _Refuse("등급을 골라 주세요. 대표이사가 보고 확인합니다." if row["pool"] == "LEADERS"
                          else f"등급 의견을 골라 주세요. {'대표이사가' if _is_ceo(ctx, who) else '본부장이'} 보고 정합니다.", 400)
        score = _total(ctx, row)
        _within(ctx, row, row["grade"], score)
        me = g.actor.user_id
        ctx.conn.execute(  # 평가자가 낸 그대로 떠 둔다 — 공개 전 평가자에게는 이것만 보인다 (_row_json 의 mine)
            "UPDATE ev_targets SET stage = ?, proposed = grade, sub_score_x10 = ?, sub_marks = ?, scored_at = ?, scored_by = ?, proxy_by = ? WHERE year = ? AND user_id = ?",
            (after_score(row["pool"]), None if score is None else int(score * 10), json.dumps(marks), auth._iso(auth._now()), me,
             me if can["behalf"] else row["proxy_by"], ctx.year, uid))
        _log(ctx.conn, "EVAL_SCORED", "ev_target", uid, behalf=ctx.ceo if can["behalf"] else None, year=ctx.year)
        to = _turn_to(ctx, uid, who)  # 다음 차례 = 배정자(팀장 묶음은 대표이사), 맡겼으면 평가받는 사람 뺀 HR 전원
    push.notify([u for u in to if u != me], "eval_turn")  # 저장(COMMIT)한 뒤 — 낸 사람 본인에게는 보내지 않는다
    return jsonify(ok=True)


@bp.post(f"/api/eval/people/{UID}/seen")
def person_seen(uid: int) -> Response:
    with _rated(uid) as (ctx, row, _):
        if row["rater_id"] != g.actor.user_id:
            raise _Refuse("평가자 본인만 확인할 수 있습니다.", 403)
        ed = _edited(ctx, row)
        ctx.conn.execute("UPDATE ev_targets SET edit_seen = ? WHERE year = ? AND user_id = ?", (ed or None, ctx.year, uid))
    return jsonify(ok=True, editSeen=bool(ed))  # 고친 내용 글자는 보내지 않는다 — 공개 전 평가자에게는 '고쳤다'는 사실만


# ---------------- 묶음 (본부장 마감 · HR 마감 확인) ----------------
@bp.post("/api/eval/pools/close")
def pool_close() -> Response:
    b = _body()
    year, key = _year(b.get("year")), b.get("pool")
    if not (isinstance(key, str) and key.startswith("DIV:")):
        raise _Refuse("본부 팀원 묶음만 마감할 수 있습니다.", 400)
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        if key not in ctx.pools:
            raise _Refuse("묶음을 찾을 수 없습니다.", 404)
        if not _pool_auth(ctx, g.actor, key)["close"]:  # 권한만 본다 — 공개는 아래에서 409 로 따로 알린다
            raise _Refuse("이 묶음을 마감할 권한이 없습니다.", 403)
        _, errors, _, _, rows = _pool(ctx, key)
        if not rows:  # 모두 제외됐다 — 마감할 등급이 없다
            raise _Refuse("평가할 분이 없는 묶음입니다.", 409)
        if ctx.round["released"]:
            raise _Refuse("결과를 공개한 평가는 바꿀 수 없습니다.", 409)
        if any(at(r["stage"], "GRADE") < 0 for r in rows):
            raise _Refuse("아직 이전 단계에 있는 분이 있습니다.", 409)
        if any(r["stage"] != "GRADE" for r in rows):
            raise _Refuse("이미 마감했습니다.", 409)
        if errors:  # 모두 배정 단계라 _pool 이 전원 검사(full)를 했다 — 등급 누락 · 정원 · 점수 기준 · 연차
            raise _Refuse(errors[0], 409, errors=errors)
        me = g.actor.user_id
        conn.execute("UPDATE ev_targets SET stage = 'CEO' WHERE year = ? AND pool = ? AND out_at IS NULL", (year, key))
        conn.execute("UPDATE ev_pools SET closed_at = ?, closed_by = ?, hr_seen = '[]', back_at = NULL, back_by = NULL, back_behalf = NULL, back_reason = NULL"
                     " WHERE year = ? AND pool = ?", (auth._iso(auth._now()), me, year, key))
        _log(conn, "EVAL_POOL_CLOSED", "ev_pool", year, behalf=None if _pool_who(ctx, key) == me else ctx.ceo, year=year, pool=key)
        to = push.hr_admins(conn) + ([] if ctx.deleg else [ctx.ceo])  # 대표이사(맡겼으면 HR) + HR 전원
    push.notify([u for u in to if u != me], "eval_turn")  # 저장(COMMIT)한 뒤 — 마감한 사람 본인에게는 보내지 않는다
    return jsonify(ok=True)


@bp.post("/api/eval/pools/seen")
def pool_seen() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    b = _body()
    year, key = _year(b.get("year")), b.get("pool")
    conn, me = auth._db(), g.actor.user_id
    with _txn(conn):
        r = conn.execute("SELECT hr_seen FROM ev_pools WHERE year = ? AND pool = ?", (year, key if isinstance(key, str) else "")).fetchone()
        if r is None:
            raise _Refuse("묶음을 찾을 수 없습니다.", 404)
        if me not in (seen := json.loads(r[0])):
            conn.execute("UPDATE ev_pools SET hr_seen = ? WHERE year = ? AND pool = ?", (json.dumps([*seen, me]), year, key))
    return jsonify(ok=True)


# ---------------- 묶음 (대표이사 확인 · 돌려보내기 · HR 확정 · 되돌리기) ----------------
POOL_VERB = {"confirm": "확인할", "back": "돌려보낼", "finalize": "확정할", "undo": "되돌릴"}


def _keys(v: object) -> list[str]:
    """묶음 열쇠 목록 — 글자 열쇠 하나 이상 (같은 열쇠는 한 번, 보낸 차례). 아니면 400. 한 묶음 길은 [열쇠] 로 부른다."""
    if not (isinstance(v, list) and v and all(isinstance(k, str) for k in v)):
        raise _Refuse("묶음을 골라 주세요.", 400)
    return list(dict.fromkeys(v))


def _not_now(ctx: _Ctx, key: str, act: str, state: str) -> str:
    """상태가 맞지 않을 때의 까닭 — 이미 넘어갔으면 "이미 …", 아직이면 무엇을 기다리는지."""
    if act == "finalize":
        return "이미 확정했습니다." if state == "DONE" else "아직 대표이사 확인 전입니다."
    if act == "undo":  # 화면이 본 상태(from)와 다르다 — 그사이 누가 확정했거나, 이미 되돌렸다 (두 창의 두 번째)
        if state == "DONE":
            return "그사이 확정됐습니다."
        return "이미 되돌렸습니다." if state == "HR" or ctx.pools[key]["undo_at"] else "되돌릴 수 있는 단계가 아닙니다."
    if state in ("HR", "DONE"):
        return "이미 확인했습니다."
    if state == "FIX":  # 확인만 — 돌려보내기는 FIX 에서도 된다
        return "등급을 다시 맞춰야 합니다."
    if act == "back" and ctx.pools[key]["back_at"]:  # 돌려보낸 뒤 아직 다시 마감하지 않았다 (마감하면 back_* 를 지운다)
        return "이미 돌려보냈습니다."
    return "아직 확인할 차례가 아닙니다." if act == "confirm" else "아직 마감 전입니다."


def _pools_ok(ctx: _Ctx, keys: list[str], act: str, need: tuple[str, ...] | None = None) -> dict[str, str]:
    """묶음 단추 길의 거절 (묶음 마감과 같은 모양 — 모듈 머리말): 없는 묶음 404 → 권한(_pool_auth) 403 → 공개 뒤 409 → 상태(need — 없으면 POOL_NEED) 409.
    목록은 하나라도 걸리면 거절한다 — 쓰기 전에 모두 보므로 아무것도 안 바뀐다. 돌려주는 것 = 열쇠 → 지금 상태."""
    if any(k not in ctx.pools for k in keys):
        raise _Refuse("묶음을 찾을 수 없습니다.", 404)
    if bad := next((k for k in keys if not _pool_auth(ctx, g.actor, k)[act]), None):
        raise _Refuse(f"{_pool_label(ctx, bad)} 묶음을 {POOL_VERB[act]} 권한이 없습니다.", 403)
    if ctx.round["released"]:
        raise _Refuse("결과를 공개한 평가는 바꿀 수 없습니다.", 409)
    states = {}
    for k in keys:
        state, errors, *_ = _pool(ctx, k)
        if state not in (need or POOL_NEED[act]):
            raise _Refuse(f"{_pool_label(ctx, k)} 묶음은 {_not_now(ctx, k, act, state)}", 409, **({"errors": errors} if state == "FIX" else {}))
        states[k] = state
    return states


def _move(conn: sqlite3.Connection, year: int, key: str, stage: str) -> None:
    """묶음의 제외 안 된 사람 모두를 그 단계로."""
    conn.execute("UPDATE ev_targets SET stage = ? WHERE year = ? AND pool = ? AND out_at IS NULL", (stage, year, key))


@bp.post("/api/eval/pools/confirm")
def pool_confirm() -> Response:
    b = _body()
    year, keys = _year(b.get("year")), _keys(b.get("pools"))
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        _pools_ok(ctx, keys, "confirm")
        me, now = g.actor.user_id, auth._iso(auth._now())
        behalf = None if _is_ceo(ctx, me) else ctx.ceo  # HR 이 대신이면 대표이사 (on_behalf_of)
        for k in keys:
            _move(conn, year, k, "HR")
            conn.execute("UPDATE ev_pools SET ceo_at = ?, ceo_by = ?, ceo_behalf = ? WHERE year = ? AND pool = ?", (now, me, behalf, year, k))
            _log(conn, "EVAL_POOL_CONFIRMED", "ev_pool", year, behalf=behalf, year=year, pool=k)
        to = push.hr_admins(conn)
    push.notify([u for u in to if u != me], "eval_turn")  # 저장(COMMIT)한 뒤 — 확정할 HR 전원에게 (묶음이 여럿이어도 한 사람에 한 통), 누른 사람은 빼고
    return jsonify(ok=True, done=keys)


@bp.post("/api/eval/pools/back")
def pool_back() -> Response:
    b = _body()
    year, (key,) = _year(b.get("year")), _keys([b.get("pool")])
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        _pools_ok(ctx, [key], "back")
        v = b.get("reason")
        why = _chars(v.strip() if isinstance(v, str) else "", "돌려보내는 이유")
        if not 0 < len(why) <= TEXT_MAX["back"]:
            raise _Refuse(f"돌려보내는 이유를 1~{TEXT_MAX['back']}자로 써 주세요.", 400)
        me = g.actor.user_id
        behalf = None if _is_ceo(ctx, me) else ctx.ceo
        _move(conn, year, key, "GRADE")
        conn.execute("UPDATE ev_pools SET back_at = ?, back_by = ?, back_behalf = ?, back_reason = ?, closed_at = NULL, closed_by = NULL WHERE year = ? AND pool = ?",
                     (auth._iso(auth._now()), me, behalf, why, year, key))
        _log(conn, "EVAL_POOL_RETURNED", "ev_pool", year, behalf=behalf, year=year, pool=key)  # 이유는 남기지 않는다 (글)
        to = _turn_to(ctx, me, _pool_who(ctx, key))  # 다시 배정할 사람 — 대표이사이고 맡겼으면 HR 전원
    push.notify([u for u in to if u != me], "eval_turn")  # 저장(COMMIT)한 뒤 — 누른 사람 본인에게는 보내지 않는다
    return jsonify(ok=True)


@bp.post("/api/eval/pools/finalize")
def pool_finalize() -> Response:
    b = _body()
    year, keys = _year(b.get("year")), _keys(b.get("pools"))
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        _pools_ok(ctx, keys, "finalize")
        for k in keys:
            _move(conn, year, k, "DONE")
            _log(conn, "EVAL_POOL_FINALIZED", "ev_pool", year, year=year, pool=k)
    return jsonify(ok=True, done=keys)


@bp.post("/api/eval/pools/undo")
def pool_undo() -> Response:
    b = _body()
    year, (key,) = _year(b.get("year")), _keys([b.get("pool")])
    seen = b.get("from")  # 화면이 본 묶음 상태 — 지금과 다르면 409 (두 창에서 눌러도 한 단계만 내려간다)
    if seen not in POOL_NEED["undo"]:
        raise _Refuse("되돌릴 단계(from: HR · DONE)를 보내 주세요.", 400)
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        done = _pools_ok(ctx, [key], "undo", (seen,))[key] == "DONE"
        _move(conn, year, key, "HR" if done else ready_stage(key))  # HR 확정을 되돌림 / 대표이사 확인을 되돌림 (직속은 GRADE — 대표이사가 정하면서 확인한다)
        clear = "" if done else ", ceo_at = NULL, ceo_by = NULL, ceo_behalf = NULL"
        src = "HR" if done else "CEO"
        conn.execute(f"UPDATE ev_pools SET undo_at = ?, undo_by = ?, undo_from = ?{clear} WHERE year = ? AND pool = ?",
                     (auth._iso(auth._now()), g.actor.user_id, src, year, key))
        _log(conn, "EVAL_POOL_UNDONE", "ev_pool", year, year=year, pool=key, **{"from": src})
    return jsonify(ok=True)


# ---------------- 결과 공개 ----------------
@bp.post("/api/eval/admin/release")
def release() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    year = _portal_year(_body().get("year"))
    conn = auth._db()
    with _txn(conn):
        _open(conn, year)  # 시작 안 함 409 → 이미 공개 409
        rows = conn.execute("SELECT user_id, stage FROM ev_targets WHERE year = ? AND out_at IS NULL", (year,)).fetchall()
        if not rows:  # 모두 제외됐다 — 공개는 되돌릴 수 없으니 빈 결과를 공개하지 않는다
            raise _Refuse("평가할 분이 없습니다.", 409)
        if any(s != "DONE" for _, s in rows):
            raise _Refuse("아직 확정되지 않은 분이 있습니다.", 409)
        conn.execute("UPDATE ev_rounds SET released_at = ?, released_by = ? WHERE year = ?", (auth._iso(auth._now()), g.actor.user_id, year))
        _log(conn, "EVAL_RELEASED", "ev_round", year, year=year, count=len(rows))
    push.notify([u for u, _ in rows], "eval_open")  # 저장(COMMIT)한 뒤 — 제외 안 된 대상 전원 (공개한 HR 도 대상이면 자기 결과를 받는다)
    return jsonify(ok=True)


# ---------------- 이의 신청 (본인) · HR 답변 · 대표이사 확인 · 돌려보내기 ----------------
@bp.post("/api/eval/me/appeal")
def appeal() -> Response:
    b = _body()
    year = _year(b.get("year"))
    conn = auth._db()
    with _txn(conn):
        ctx, row = _mine(conn, year)  # 사람 번호는 세션에서만 — 남의 것을 신청할 길이 없다
        uid, rel = row["user_id"], ctx.round["released"]
        if not rel:
            raise _Refuse("아직 결과가 공개되지 않았습니다.", 403)
        if row["out_at"] is not None:
            raise _Refuse("평가에서 제외되어 이의 신청할 결과가 없습니다.", 403)
        if auth._today() > _day(rel) + timedelta(days=APPEAL_DAYS):  # 공개일(한국) + 7일 그날 23:59:59 까지
            raise _Refuse("이의 신청 기간이 끝났습니다.", 403)
        if uid in ctx.appeals:
            raise _Refuse("이미 이의 신청을 했습니다.", 409)
        v = b.get("reason")
        why = _chars(v.strip() if isinstance(v, str) else "", "이의 신청 사유")
        if not 0 < len(why) <= TEXT_MAX["appeal"]:
            raise _Refuse(f"이의 신청 사유를 1~{TEXT_MAX['appeal']}자로 써 주세요.", 400)
        conn.execute("INSERT INTO ev_appeals (year, user_id, reason, at, status, before) VALUES (?, ?, ?, ?, 'OPEN', ?)",
                     (year, uid, why, auth._iso(auth._now()), row["grade"]))
        _log(conn, "EVAL_APPEALED", "ev_appeal", uid, year=year)  # 사유는 남기지 않는다 (글)
        to = push.hr_admins(conn)
    push.notify([u for u in to if u != uid], "eval_appeal")  # 저장(COMMIT)한 뒤 — HR 전원 (신청한 HR 본인은 빼고)
    return jsonify(ok=True)


def _over(ctx: _Ctx, row: sqlite3.Row, v: object) -> dict[int, str]:
    """HR 답변의 실행목표 등급 {"<번호>": 등급} → {번호: 등급}. KPI 없는 사람은 null · 빠짐만, 떠 둔 실행목표 · SABCD (비우기 없음). 아니면 400."""
    if v is None:
        return {}
    if row["kpi_version"] is None:
        raise _Refuse("KPI가 없는 분은 등급만 매깁니다.", 400)
    if not isinstance(v, dict):
        raise _Refuse("실행목표별 등급을 실행목표 번호마다 보내 주세요.", 400)
    rids, out = {it["rid"] for it in ctx.items[row["user_id"]]}, {}
    for k, grade in v.items():
        if (rid := _digits(k)) not in rids:
            raise _Refuse("실행목표를 찾을 수 없습니다.", 400)
        if grade not in GRADES:
            raise _Refuse("달성 등급은 S · A · B · C · D 중에서 골라 주세요.", 400)
        out[rid] = grade
    return out


def _marked(ctx: _Ctx, row: sqlite3.Row, over: dict[int, str]) -> tuple[dict[int, str], Decimal | None]:
    """over 중 지금 실행목표 등급과 다른 것 · 그것을 덮어 낸 총점 (KPI 없는 사람은 None — 연차 규칙만)."""
    uid = row["user_id"]
    cur = {it["rid"]: ctx.entries[(uid, it["rid"])]["grade"] for it in ctx.items[uid]}
    diff = {r: v for r, v in over.items() if v != cur[r]}
    if row["kpi_version"] is None:
        return diff, None
    return diff, total([(it["weight"], diff.get(it["rid"], cur[it["rid"]])) for it in ctx.items[uid]], row["bonus_x2"])


def _put_marks(ctx: _Ctx, uid: int, marks: dict[int, str]) -> None:
    ctx.conn.executemany("UPDATE ev_items SET grade = ? WHERE year = ? AND user_id = ? AND rid = ?", [(v, ctx.year, uid, r) for r, v in marks.items()])


@bp.post(f"/api/eval/appeals/{UID}/answer")
def appeal_answer(uid: int) -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    if uid == g.actor.user_id:  # 자기 이의는 다른 HR 이 (명단 한 사람 · _person 과 같다)
        raise _Refuse("본인 것은 다른 HR이 처리해요.", 403)
    b = _body()
    year = _year(b.get("year"))
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        a, row = ctx.appeals.get(uid), ctx.rows.get(uid)
        if a is None:
            raise _Refuse("이의 신청을 찾을 수 없습니다.", 404)
        if a["status"] != "OPEN":
            raise _Refuse("이미 답변했습니다.", 409)
        v = b.get("answer")
        answer = _chars(v.strip() if isinstance(v, str) else "", "답변")
        if not 0 < len(answer) <= TEXT_MAX["answer"]:
            raise _Refuse(f"답변을 1~{TEXT_MAX['answer']}자로 써 주세요.", 400)
        grade = b.get("grade")
        if grade not in GRADES:
            raise _Refuse("등급은 S · A · B · C · D 중에서 골라 주세요.", 400)
        marks, score = _marked(ctx, row, _over(ctx, row, b.get("marks")))
        _within(ctx, row, grade, score)  # 점수 기준 · 연차만 — 정원은 따지지 않는다
        now, me, changed = auth._iso(auth._now()), g.actor.user_id, grade != a["before"]
        if changed:  # 등급을 바꾸는 답변은 대표이사 확인을 기다린다 — 실행목표 등급 · 등급은 확인할 때 반영
            conn.execute("UPDATE ev_appeals SET status = 'CEO', answer = ?, after = ?, marks = ?, answered_at = ?, answered_by = ? WHERE year = ? AND user_id = ?",
                         (answer, grade, json.dumps({str(r): m for r, m in marks.items()}) if marks else None, now, me, year, uid))
            to, kind = _turn_to(ctx, uid, ctx.ceo), "eval_appeal"  # 대표이사 — 맡겼으면 신청한 사람 뺀 HR 전원
        else:  # 유지는 바로 끝 — 고친 실행목표 등급만 반영
            _put_marks(ctx, uid, marks)
            conn.execute("UPDATE ev_appeals SET status = 'DONE', answer = ?, after = ?, marks = NULL, answered_at = ?, answered_by = ?, decided_at = ?, decided_by = ?,"
                         " decided_behalf = NULL WHERE year = ? AND user_id = ?", (answer, grade, now, me, now, me, year, uid))
            to, kind = [uid], "eval_answer"
        _log(conn, "EVAL_APPEAL_ANSWERED", "ev_appeal", uid, year=year, changed=changed)
    push.notify([u for u in to if u != me], kind)  # 저장(COMMIT)한 뒤 — 답한 사람 본인에게는 보내지 않는다
    return jsonify(ok=True)


@contextmanager
def _decide(uid: int) -> Iterator[tuple[_Ctx, sqlite3.Row, sqlite3.Row, int | None]]:
    """대표이사가 이의 신청의 등급 변경을 확인 · 돌려보내는 틀: 해 400 → 그해 평가 대상 404 → 권한(_appeal_can) 403 → 이의 신청 없음 404 → CEO 단계 아님 409.
    한 트랜잭션 — 돌려주는 것 = (ctx, 대상 줄, 이의 줄, on_behalf_of — HR 이 대신이면 대표이사)."""
    year = _year(_body().get("year"))
    conn = auth._db()
    with _txn(conn):
        ctx = _ctx(conn, year)
        row = ctx.rows.get(uid)
        if row is None:
            raise _Refuse("평가 대상이 아닙니다.", 404)
        if not _appeal_can(ctx, g.actor, row):
            raise _Refuse("이 이의 신청을 확인할 권한이 없습니다.", 403)
        a = ctx.appeals.get(uid)
        if a is None:
            raise _Refuse("이의 신청을 찾을 수 없습니다.", 404)
        if a["status"] != "CEO":  # 두 창의 두 번째 — 이미 확인했거나(DONE) 돌려보냈다(OPEN)
            raise _Refuse("이미 처리했습니다." if a["status"] == "DONE" else "HR 답변을 기다리는 이의 신청입니다.", 409)
        yield ctx, row, a, None if _is_ceo(ctx, g.actor.user_id) else ctx.ceo


@bp.post(f"/api/eval/appeals/{UID}/confirm")
def appeal_confirm(uid: int) -> Response:
    with _decide(uid) as (ctx, row, a, behalf):
        marks, score = _marked(ctx, row, {int(r): m for r, m in json.loads(a["marks"] or "{}").items()})
        _within(ctx, row, a["after"], score)  # 답할 때 본 것을 다시 (점수 기준 · 연차)
        _put_marks(ctx, uid, marks)
        conn = ctx.conn
        conn.execute("UPDATE ev_targets SET grade = ? WHERE year = ? AND user_id = ?", (a["after"], ctx.year, uid))
        conn.execute("UPDATE ev_appeals SET status = 'DONE', decided_at = ?, decided_by = ?, decided_behalf = ? WHERE year = ? AND user_id = ?",
                     (auth._iso(auth._now()), g.actor.user_id, behalf, ctx.year, uid))
        _log(conn, "EVAL_APPEAL_CONFIRMED", "ev_appeal", uid, behalf=behalf, year=ctx.year)
    push.notify([uid], "eval_answer")  # 저장(COMMIT)한 뒤 — 신청한 사람 (확인한 사람일 수 없다: 대표이사는 대상이 아니고 HR 은 자기 것을 못 한다)
    return jsonify(ok=True)


@bp.post(f"/api/eval/appeals/{UID}/back")
def appeal_back(uid: int) -> Response:
    with _decide(uid) as (ctx, row, a, behalf):
        ctx.conn.execute("UPDATE ev_appeals SET status = 'OPEN', after = NULL, marks = NULL WHERE year = ? AND user_id = ?", (ctx.year, uid))  # 답변 글은 HR 이 고치게 남긴다
        _log(ctx.conn, "EVAL_APPEAL_RETURNED", "ev_appeal", uid, behalf=behalf, year=ctx.year)
        to = push.hr_admins(ctx.conn)
    push.notify([u for u in to if u not in (g.actor.user_id, uid)], "eval_appeal")  # 저장(COMMIT)한 뒤 — 다시 답할 HR 전원 (누른 사람 · 신청한 사람 빼고)
    return jsonify(ok=True)
