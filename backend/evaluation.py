"""인사평가(연말 평가) ↔ 인사포털 (2026-10-26 인사담당자 — 평가 3단계 ①: 가상 자료에서 서버 저장으로).

app.py 가 bp 를 붙인다. 세션 검사 · 같은 출처 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.
규칙(총점 · 점수 기준 · 연차 · 정원 · 묶음 상태)은 hr_eval/domain/eval_rules.py, 표는 hr_eval/sql/020_eval.sql 에 있다.

  POST /api/eval/admin/round              {"year", "evalStart", "selfTo", "evidDue", "scoreTo", "gradeTo", "ceoTo", "finalTo", "bands": {"A", "B", "C"}} 그해 회차 (HR)
  POST /api/eval/admin/start              {"year"} 평가 시작 — 대상 · 묶음 · 평가자 · 배정자 · 확정 KPI 판을 떠 둔다 → {"ok", "count", "noKpi", "noRater"} (HR)
  POST /api/eval/admin/att-done           {"year"} 근태 입력을 마쳤다 (HR · 이미 마쳤으면 그대로 200)
  POST /api/eval/admin/attendance         {"year", "rows": [{"userId", "late"?, "leaveOver"?}]} 지각 · 연차 초과 → {"ok", "done", "locked", "missing"} (HR)
  POST /api/eval/admin/<uid>/exclude      {"year", "reason"} 이번 평가에서 뺀다 (HR)
  POST /api/eval/admin/<uid>/include      {"year"} 제외를 되돌린다 (HR)
  POST /api/eval/admin/<uid>/skip-self    {"year"} 자기평가 없이 평가자에게 넘긴다 (HR) — 평가자에게 알림
  POST /api/eval/admin/<uid>/rater        {"year", "raterId"} 평가자를 바꾼다 (HR)

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
  자기평가 없이: 제외됨 409 → SELF 가 아님 409 → KPI 없음 409 (저장한 뒤 떠 둔 평가자에게 알림) ·
  평가자 바꾸기: SELF · SCORE 가 아님 409 → 본인이 아니고 활성이며 포털을 쓰는(로그인해 점수를 낼 수 있는) 직원이 아님 400.
감사로그(R10): EVAL_ROUND_SET · EVAL_STARTED · EVAL_ATT_DONE · EVAL_ATTENDANCE(저장한 사람이 있을 때만) · EVAL_EXCLUDED · EVAL_INCLUDED · EVAL_SKIP_SELF · EVAL_RATER_SET —
  after_data 는 해 · 사람 수 · 평가자 번호뿐. 사유 · 근태 숫자 · 등급 · 점수는 넣지 않는다 (공개 전 등급이 감사로그로 새지 않게).
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterator

from flask import Blueprint, Response, g, jsonify

import auth
import kpi
import push
from hr_eval.domain.eval_rules import APPEAL_DAYS, BANDS, TEXT_MAX, Member, above, assign_stage, at, cap, check_pool, pool_key, pool_state, total
from hr_eval.domain.models import AuditEntry, Role
from hr_eval.domain.org import led_dept, manager_of, target_of
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
    if last and (last[0] is None
                 or auth._today() <= datetime.strptime(last[0], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(auth.KST).date() + timedelta(days=APPEAL_DAYS)
                 or conn.execute("SELECT 1 FROM ev_appeals WHERE year = ? AND status IN ('OPEN', 'CEO')", (year - 1,)).fetchone()):
        return year - 1
    return year


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


def _score(conn: sqlite3.Connection, year: int, uid: int, version: int | None, bonus_x2: int) -> Decimal | None:
    """지금 총점 = 떠 둔 KPI 판의 가중치 × 지금 실행목표 등급 + 가점. KPI 없이 등급만 받는 사람은 None."""
    if version is None:
        return None
    content = conn.execute("SELECT content FROM kpi_versions WHERE user_id = ? AND year = ? AND version = ?", (uid, year, version)).fetchone()[0]
    grades = dict(conn.execute("SELECT rid, grade FROM ev_items WHERE year = ? AND user_id = ?", (year, uid)))
    return total([(it["weight"], grades.get(it["rid"])) for it in kpi._rows(json.loads(content))], bonus_x2)


def _pool_state(conn: sqlite3.Connection, year: int, key: str) -> str:
    """묶음 상태 (eval_rules.pool_state — 제외된 사람은 뺀다). 모두 대표이사 확인 단계에 왔을 때만 정원 검사(full)로 FIX · CEO 를 가른다 (화면 evPoolState)."""
    rows = conn.execute(
        "SELECT t.user_id, u.name, t.stage, t.grade, t.leave_x2, t.kpi_version, t.bonus_x2 FROM ev_targets t JOIN users u ON u.id = t.user_id"
        " WHERE t.year = ? AND t.pool = ? AND t.out_at IS NULL", (year, key)).fetchall()
    stages = [r[2] for r in rows]
    if (state := pool_state(stages, key, False)) != "CEO":
        return state
    bands = tuple(conn.execute("SELECT band_a, band_b, band_c FROM ev_rounds WHERE year = ?", (year,)).fetchone())
    members = [Member(uid, name, _score(conn, year, uid, v, bonus), grade, leave) for uid, name, _, grade, leave, v, bonus in rows]
    return pool_state(stages, key, bool(check_pool(members, bands, True)[0]))


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


def _log(conn: sqlite3.Connection, action: str, entity_type: str, entity_id: int, **after: int) -> None:
    """감사로그: 행동 · 대상 · 해(· 사람 수 · 평가자 번호)만."""
    auth._audit(conn, AuditEntry.by(g.actor, action=action, entity_type=entity_type, entity_id=entity_id, after_data=after))


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
        if _pool_state(conn, year, pool) not in ("WAIT", "FIX"):  # 이 사람을 빼고 센 묶음 상태 (화면 evInclude)
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
    push.notify([rater], "eval_turn")  # 저장한 뒤 — 떠 둔 평가자에게 (평가자가 없으면 아무도)
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
