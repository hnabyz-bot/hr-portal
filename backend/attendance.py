"""근태 현황 (2026-11-13 인사담당자 결정) — HR PC 의 데일리 근태 대시보드 자료를 팀장 · 본부장이 본다.

app.py 가 bp 를 붙인다. 설계 docs/superpowers/specs/2026-11-13-attendance-design.md.

  POST /api/attendance/upload   하루치 저장 (HR PC 예약작업 — 세션 없이, 머리 X-Attend-Key = 서버 HR_ATTEND_KEY)
  GET  /api/attendance/dates    자료가 있는 날짜들 (볼 수 있는 사람)
  GET  /api/attendance/day      그날 자료 중 보는 사람 범위의 팀만 (?date=, 없으면 가장 최근 날)

보는 범위: HR · 대표이사 = 전체 / 본부장 = 맡은 본부 / 팀장 = 맡은 팀이 속한 본부 전체(대표이사 직속 팀이면 그 팀) / 그 밖 403.
다른 범위의 팀은 화면에서 숨기는 것이 아니라 보내지 않는다.

근태 파일의 팀 이름은 하이웍스 부서 이름과 다르다(CsI제조팀 ↔ CsI팀 등). 그래서 이름표를 두지 않고 사람으로 맞춘다 —
근태 팀 구성원의 이름을 포털 구성원(활성)에서 찾아, 가장 많이 나온 소속의 단위를 그 팀의 단위로 본다(team_units).
단위 = 팀이면 그 위 본부, 맨 위 부서면 그 부서. 같은 이름이 여러 단위에 있으면 그 사람은 세지 않는다. 아무도 못 찾은 팀은 HR · 대표이사만.

열쇠(HR_ATTEND_KEY)가 서버에 없거나 32자보다 짧으면 올리기는 404 — 기능이 꺼진 것과 같다.
감사로그는 남기지 않는다(읽기 · 기계가 올리는 자료). 서버 로그에는 날짜 · 팀 수 · 사람 수만 — 이름은 남기지 않는다.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date

from flask import Blueprint, Response, abort, g, jsonify, request

import auth
from hr_eval.domain.models import Role

bp = Blueprint("attendance", __name__)
logger = logging.getLogger(__name__)

MAX_BODY = 2 * 1024 * 1024  # 하루치는 약 44KB
SUMMARY = ("총원", "실근무", "연차", "반차", "반반차", "미인식", "지각", "결근", "주52시간초과자")
OVERTIME = ("연장", "야간", "휴일")
TEXT = {"name": 40, "position": 20, "checkIn": 10, "checkOut": 10, "status": 30, "note": 300}  # 칸 → 글자 한도


class Bad(ValueError):
    """올린 자료의 모양이 틀렸다 — 400."""


def _text(v: object, n: int, what: str, need: bool = False) -> str:
    if v is None and not need:
        return ""
    if not isinstance(v, str) or len(v) > n or (need and not v.strip()) or any(ord(ch) < 32 and ch not in "\t\n" for ch in v):
        raise Bad(f"{what}: {n}자 이하 글자여야 합니다.")
    return v


def _int(v: object, hi: int, what: str) -> int:
    if v is None:
        return 0
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != int(v) or not 0 <= v <= hi:
        raise Bad(f"{what}: 0~{hi} 정수여야 합니다.")
    return int(v)


def clean(body: object) -> dict:
    """올린 하루치에서 아는 칸만 남긴다. 모양이 틀리면 Bad."""
    if not isinstance(body, dict):
        raise Bad("자료는 객체여야 합니다.")
    d = body.get("date")
    try:
        ok = isinstance(d, str) and date.fromisoformat(d).isoformat() == d and 2020 <= int(d[:4]) <= 2099
    except ValueError:
        ok = False
    if not ok:
        raise Bad("date: YYYY-MM-DD 실제 있는 날이어야 합니다.")
    teams = body.get("teams")
    if not isinstance(teams, list) or not 1 <= len(teams) <= 60:
        raise Bad("teams: 팀이 1~60개여야 합니다.")
    out = []
    for t in teams:
        if not isinstance(t, dict):
            raise Bad("teams: 팀은 객체여야 합니다.")
        name = _text(t.get("team"), 40, "team", need=True)
        summary = t.get("summary") if isinstance(t.get("summary"), dict) else {}
        members = t.get("members")
        if not isinstance(members, list) or len(members) > 1000:
            raise Bad(f"{name}: members 는 1000명까지의 목록이어야 합니다.")
        rows = []
        for m in members:
            if not isinstance(m, dict):
                raise Bad(f"{name}: 사람은 객체여야 합니다.")
            row = {k: _text(m.get(k), n, k, need=(k == "name")) for k, n in TEXT.items()}
            if not row["note"]:
                row.pop("note")
            row["lateMinutes"] = _int(m.get("lateMinutes"), 1440, "lateMinutes")
            ot = m.get("overtimeMinutes") if isinstance(m.get("overtimeMinutes"), dict) else {}
            row["overtimeMinutes"] = {k: _int(ot.get(k), 10000, f"overtimeMinutes.{k}") for k in OVERTIME}
            h = m.get("weeklyActualHours", 0)
            if isinstance(h, bool) or not isinstance(h, (int, float)) or not 0 <= h <= 168:
                raise Bad("weeklyActualHours: 0~168 숫자여야 합니다.")
            row["weeklyActualHours"] = round(float(h), 1)
            row["over52Flag"] = m.get("over52Flag") is True
            rows.append(row)
        out.append({"team": name, "summary": {k: _int(summary.get(k), 10000, f"summary.{k}") for k in SUMMARY}, "members": rows})
    return {"date": d, "teams": out}


def _key() -> str:
    k = os.environ.get("HR_ATTEND_KEY", "")
    return k if len(k) >= 32 else ""


@bp.post("/api/attendance/upload")
def upload() -> Response:
    key = _key()
    if not key:
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Attend-Key", "").encode(), key.encode()):
        return jsonify(error="열쇠가 맞지 않습니다."), 403
    if (request.content_length or 0) > MAX_BODY:
        return jsonify(error="자료가 너무 큽니다."), 413
    try:
        day = clean(request.get_json(silent=True))
    except Bad as e:
        return jsonify(error=str(e)), 400
    conn = auth._db()
    conn.execute(
        "INSERT INTO attendance_days (date, data, uploaded_at) VALUES (?, ?, ?)"
        " ON CONFLICT(date) DO UPDATE SET data = excluded.data, uploaded_at = excluded.uploaded_at",
        (day["date"], json.dumps(day, ensure_ascii=False), auth._iso(auth._now())),
    )
    logger.info("근태 자료 저장 %s — %d팀 %d명", day["date"], len(day["teams"]), sum(len(t["members"]) for t in day["teams"]))
    return jsonify(ok=True, date=day["date"])


def unit_of(dept: int | None, parent: dict[int, int | None]) -> int | None:
    """팀이면 그 위 본부, 맨 위 부서면 그 부서."""
    if dept is None:
        return None
    return parent.get(dept) or dept


def team_units(teams: list[dict], people: list[tuple[str, int]], parent: dict[int, int | None]) -> dict[str, int | None]:
    """근태 팀 → 단위. 구성원 이름으로 포털 소속을 찾아 가장 많이 나온 단위(같은 이름이 여러 단위면 그 사람은 안 셈)."""
    units: dict[str, set[int]] = defaultdict(set)
    for name, dept in people:
        units[name].add(unit_of(dept, parent))
    out: dict[str, int | None] = {}
    for t in teams:
        c = Counter(next(iter(units[m["name"]])) for m in t["members"] if len(units.get(m["name"], ())) == 1)
        out[t["team"]] = c.most_common(1)[0][0] if c else None
    return out


def viewer_unit(a, parent: dict[int, int | None]) -> int | str | None:
    """'all' = 전체 · 단위 번호 · None = 못 본다."""
    if a.is_hr or a.position is Role.CEO:
        return "all"
    if a.position is Role.DIVISION_HEAD:
        return unit_of(a.led_division_id, parent)
    if a.position is Role.TEAM_LEADER:
        return unit_of(a.led_team_id, parent)
    return None


def _org(conn: sqlite3.Connection) -> tuple[dict[int, int | None], dict[int, str]]:
    rows = conn.execute("SELECT id, parent_id, name FROM departments").fetchall()
    return {r[0]: r[1] for r in rows}, {r[0]: r[2] for r in rows}


def _scope(conn: sqlite3.Connection):
    parent, names = _org(conn)
    who = viewer_unit(g.actor, parent)
    if who is None:
        abort(403)
    return who, parent, names


@bp.get("/api/attendance/dates")
def dates() -> Response:
    conn = auth._db()
    _scope(conn)
    return jsonify(dates=[r[0] for r in conn.execute("SELECT date FROM attendance_days ORDER BY date DESC")])


@bp.get("/api/attendance/day")
def day() -> Response:
    conn = auth._db()
    who, parent, names = _scope(conn)
    d = request.args.get("date") or ""
    row = conn.execute(
        "SELECT date, data, uploaded_at FROM attendance_days WHERE date = ?" if d else
        "SELECT date, data, uploaded_at FROM attendance_days ORDER BY date DESC LIMIT 1", (d,) if d else ()
    ).fetchone()
    scope = "전체" if who == "all" else names.get(who, "")
    if not row:
        return jsonify(date=d or None, scope=scope, uploadedAt=None, teams=[])
    teams = json.loads(row[1])["teams"]
    if who != "all":
        people = conn.execute("SELECT name, department_id FROM users WHERE is_active = 1").fetchall()
        units = team_units(teams, people, parent)
        teams = [t for t in teams if units[t["team"]] == who]
    return jsonify(date=row[0], scope=scope, uploadedAt=row[2], teams=teams)


def check() -> None:
    """모양 고르기 · 단위 맞추기 · 보는 사람 단위."""
    from hr_eval.domain.models import Actor

    m = {"name": "가상하나", "position": "프로", "checkIn": "08:50", "checkOut": "18:10", "status": "정상", "lateMinutes": 0,
         "overtimeMinutes": {"연장": 10, "야간": 0, "휴일": 0}, "weeklyActualHours": 41.26, "over52Flag": False, "secret": "x"}
    day_ = clean({"date": "2026-11-12", "teams": [{"team": "가상팀", "summary": {"총원": 1, "기타": 9}, "members": [m]}], "extra": 1})
    t = day_["teams"][0]
    assert set(day_) == {"date", "teams"} and "기타" not in t["summary"] and t["summary"]["지각"] == 0, "모르는 칸은 버리고 빠진 수는 0"
    assert "secret" not in t["members"][0] and "note" not in t["members"][0], "모르는 사람 칸 · 빈 비고는 남기지 않는다"
    assert t["members"][0]["weeklyActualHours"] == 41.3, "시간은 소수 한 자리"
    assert clean({"date": "2026-11-12", "teams": [{"team": "가상팀", "members": [dict(m, note="[확인필요] 퇴근 미인식")]}]})["teams"][0]["members"][0]["note"]
    for bad in ({"date": "2026-02-30", "teams": [{"team": "a", "members": []}]}, {"date": "2026-11-12", "teams": []},
                {"date": "2026-11-12", "teams": [{"team": "", "members": []}]},
                {"date": "2026-11-12", "teams": [{"team": "a", "members": [dict(m, name="")]}]},
                {"date": "2026-11-12", "teams": [{"team": "a", "members": [dict(m, lateMinutes=-1)]}]},
                {"date": "2026-11-12", "teams": [{"team": "a", "members": [dict(m, weeklyActualHours=200)]}]},
                {"date": "2026-11-12", "teams": [{"team": "a", "members": [dict(m, status="a\x00")]}]}, [], None):
        try:
            clean(bad)
        except Bad:
            continue
        raise AssertionError(f"틀린 모양을 받았다: {bad}")

    parent = {1: None, 2: 1, 3: 1, 4: None, 5: None}  # 본부 1 > 팀 2 · 3 / 본부 4 / 직속 팀 5
    assert (unit_of(2, parent), unit_of(1, parent), unit_of(5, parent), unit_of(None, parent)) == (1, 1, 5, None)
    people = [("가", 2), ("나", 3), ("다", 4), ("라", 5), ("동명", 2), ("동명", 4)]
    teams = [{"team": "A", "members": [{"name": "가"}, {"name": "나"}, {"name": "다"}]},
             {"team": "B", "members": [{"name": "라"}]}, {"team": "C", "members": [{"name": "없음"}, {"name": "동명"}]}]
    assert team_units(teams, people, parent) == {"A": 1, "B": 5, "C": None}, "다수결 · 직속 팀 · 못 찾음/같은 이름은 None"

    a = lambda pos=None, hr=False, **kw: Actor(1, pos, hr, **kw)  # noqa: E731
    assert viewer_unit(a(hr=True), parent) == "all" and viewer_unit(a(Role.CEO), parent) == "all"
    assert viewer_unit(a(Role.DIVISION_HEAD, led_division_id=1), parent) == 1, "본부장 = 맡은 본부"
    assert viewer_unit(a(Role.TEAM_LEADER, led_team_id=3), parent) == 1, "팀장 = 맡은 팀의 본부 전체"
    assert viewer_unit(a(Role.TEAM_LEADER, led_team_id=5), parent) == 5, "직속 팀장 = 자기 팀"
    assert viewer_unit(a(), parent) is None, "직원은 못 본다"
    print("attendance 검사 통과")


if __name__ == "__main__":
    for out in (sys.stdout, sys.stderr):
        out.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["check"]:
        check()
    else:
        sys.exit("사용법: python -m attendance check")
