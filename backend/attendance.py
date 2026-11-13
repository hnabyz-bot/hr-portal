"""근태 현황 (2026-11-13 인사담당자 결정) — HR PC 의 데일리 근태 대시보드 자료를 팀장 · 본부장이 본다.

app.py 가 bp 를 붙인다. 설계 docs/superpowers/specs/2026-11-13-attendance-design.md.

  POST /api/attendance/upload   하루치 저장 (HR PC 예약작업 — 세션 없이, 머리 X-Attend-Key = 서버 HR_ATTEND_KEY)
  GET  /api/attendance/dates    자료가 있는 날짜들 (볼 수 있는 사람)
  GET  /api/attendance/day      그날 자료 중 보는 사람 범위의 팀만 (?date=, 없으면 가장 최근 날)
  --- 2단계 (서류 확인 · 직원 본인 · 집계) ---
  GET  /api/attendance/checks   서류 확인 목록 (볼 수 있는 사람 — 범위 안만)
  POST /api/attendance/checks   {date, team, name, done} 제출 확인 · 취소 (책임자 · 그 본부 본부장 · HR)
  GET  /api/attendance/todo     누구나 — 내 서류(mine) + 내가 책임자인 확인 전(open) · 기한 지남(overdue) 수
  POST /api/attendance/mine     {date, team} 본인 "제출했어요"
  POST /api/attendance/found    늦게 낸 서류 자동 확인 (HR PC — 세션 없이, 같은 열쇠)
  GET  /api/attendance/stats    ?period=YYYY | YYYY-MM 월 · 연 집계 (볼 수 있는 사람 — 범위 안만)
  GET  /api/attendance/me       ?period=YYYY-MM 내 근태 (누구나 — 내 것만: 이름과 소속 단위가 둘 다 맞는 줄)

서류 확인 (2단계): 챙길 사람은 그날 자료에서 고르고(kind_of — 미인식 · 지각 · 결근 / 비고 [확인필요] · 문서 없음) 체크 기록만 저장한다(026).
책임자 = 바로 위 관리자(KPI 와 같은 manager_of), 이름을 못 맞춘 사람은 그 본부의 본부장(본부도 모르면 HR) 몫. 기한 = 근태 날짜 + 7일. CHECK_FROM 전 · 60일보다 오래된 날은 목록 · 알림에 안 나온다.

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
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta

from flask import Blueprint, Response, abort, g, jsonify, request

import auth
from hr_eval.domain.models import Role
from hr_eval.domain.org import led_dept, manager_of

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

# ---------------- 2단계: 서류 확인 · 직원 본인 · 집계 (2026-11-13 인사담당자 결정) ----------------
CHECK_FROM = "2026-11-16"  # 이 날 근태부터 서류 확인 · 직원 알림에 센다 (기능을 연 다음 월요일 — 그 전 미인식이 한꺼번에 뜨지 않게)
WINDOW_DAYS = 60  # ponytail: 오늘에서 60일 안의 날만 목록 · 알림에 — 날마다 그날 자료를 다시 읽어 고르므로 범위를 묶는다 (집계는 따로 다 읽는다)
DUE_DAYS = 7  # 근태 날짜 + 7일(달력)부터 기한 지남 (11/12 미인식 → 11/19 부터)
DONE_KEEP = 30  # 확인 끝난 줄은 30일 동안 "확인 완료"에
DOCS = {"miss": "미인식 소명서", "late": "지각 소명서", "leave": "휴가신청서"}
CHECK_NOTE = re.compile(r"\[확인필요\]|문서 없음|문서 미확인")  # Cowork 가 하이웍스에서 서류를 못 찾았다고 적은 비고
DONE_NOTE = re.compile(r"소명서[^,;()]*제출|결재 완료")  # 그날 이미 서류가 있다 — 줄 없이도 확인된 것
NO_TIME = ("", "-", "—")


def kind_of(m: dict) -> str | None:
    """챙길 일 — miss(미인식 소명서) · late(지각 소명서) · leave(휴가신청서) · None."""
    st, note = m.get("status", ""), m.get("note", "")
    if st == "미인식" or (CHECK_NOTE.search(note) and "미인식" in note):
        return "miss"
    if st == "지각":
        return "late"
    if st == "결근" or CHECK_NOTE.search(note):
        return "leave"
    return None


def label_of(m: dict, kind: str) -> str:
    note = m.get("note", "")
    if kind == "miss":
        if "출근 미인식" in note or (m.get("checkIn") or "") in NO_TIME:
            return "출근 미인식"
        return "퇴근 미인식" if "퇴근 미인식" in note or (m.get("checkOut") or "") in NO_TIME else "미인식"
    if kind == "late":
        return f"지각 {m['lateMinutes']}분" if m.get("lateMinutes") else "지각"
    st = m.get("status") or "근태"
    return "결근" if st == "결근" else f"{st} · 하이웍스 문서 없음"


class Org:
    """한 요청 동안 쓰는 조직 — 부서 · 활성 구성원 · (이름, 단위) → 사람 번호 · 바로 위 관리자."""

    def __init__(self, conn: sqlite3.Connection):
        import kpi  # 쓰는 자리에서 — kpi 가 push 를 거쳐 이 파일을 부를 수 있다

        self.parent, self.names = _org(conn)
        rows = conn.execute("SELECT id, name, department_id FROM users WHERE is_active = 1").fetchall()
        self.people = [(r[1], r[2]) for r in rows]
        self.uid: dict[tuple[str, int | None], int | None] = {}
        for uid, name, dept in rows:  # 같은 단위에 같은 이름이 둘이면 누구인지 모른다 — None
            k = (name, unit_of(dept, self.parent))
            self.uid[k] = None if k in self.uid else uid
        self.persons, self.depts = kpi.load_org(conn)
        self._mgr: dict[int, int | None] = {}

    def manager(self, uid: int) -> int | None:
        if uid not in self._mgr:
            self._mgr[uid] = manager_of(uid, self.persons, self.depts)
        return self._mgr[uid]

    def head_of(self, unit: int | None) -> int | None:
        """그 단위(본부)의 활성 본부장."""
        return next((p.id for p in self.persons if p.is_active and p.position is Role.DIVISION_HEAD and unit_of(led_dept(p), self.parent) == unit), None)


def collect(conn: sqlite3.Connection, org: Org, today: date, lo: str | None = None, hi: str | None = None) -> list[dict]:
    """챙길 일 전부 (날짜 차례). lo · hi 를 안 주면 CHECK_FROM · 오늘 - 60일 중 늦은 날부터 끝까지."""
    lo = lo or max(CHECK_FROM, (today - timedelta(days=WINDOW_DAYS)).isoformat())
    hi = hi or "9999-12-31"
    checks = {r[:3]: r[3:] for r in conn.execute(
        "SELECT date, team, name, self_at, done_at, done_by, done_how FROM attendance_checks WHERE date BETWEEN ? AND ?", (lo, hi))}
    out = []
    for d, data in conn.execute("SELECT date, data FROM attendance_days WHERE date BETWEEN ? AND ? ORDER BY date", (lo, hi)):
        teams = json.loads(data)["teams"]
        units = team_units(teams, org.people, org.parent)
        for t in teams:
            unit = units[t["team"]]
            for m in t["members"]:
                kind = kind_of(m)
                if not kind:
                    continue
                c = checks.get((d, t["team"], m["name"])) or (None, None, None, None)
                how = c[3] or ("NOTE" if DONE_NOTE.search(m.get("note", "")) else None)
                uid = org.uid.get((m["name"], unit)) if unit is not None else None
                resp = (org.manager(uid) if uid else None) or (None if uid else org.head_of(unit))
                out.append({"date": d, "team": t["team"], "name": m["name"], "position": m.get("position", ""), "kind": kind,
                            "label": label_of(m, kind), "unit": unit, "uid": uid, "resp": resp,
                            "selfAt": c[0], "doneAt": c[1], "doneBy": c[2], "how": how})
    return out


def overdue(it: dict, today: date) -> bool:
    return not it["how"] and date.fromisoformat(it["date"]) + timedelta(days=DUE_DAYS) <= today


def is_mine(a, it: dict) -> bool:
    """내가 챙길 사람 — 책임자이거나, 책임자를 못 찾은 사람이면 HR."""
    return it["resp"] == a.user_id or (it["resp"] is None and a.is_hr)


def can_check(a, it: dict, org: Org) -> bool:
    return a.is_hr or it["resp"] == a.user_id or (a.position is Role.DIVISION_HEAD and unit_of(a.led_division_id, org.parent) == it["unit"])


def todo_counts(conn: sqlite3.Connection, a, today: date) -> dict:
    """{mine: 내 서류(확인 전), open · overdue: 내가 챙길 사람 중 확인 전 · 기한 지남} — 메뉴 숫자 · 홈 · 9시 묶음(push.pending)이 같은 셈."""
    org = Org(conn)
    its = collect(conn, org, today)
    mine = [it for it in its if it["uid"] == a.user_id and not it["how"]]
    see = viewer_unit(a, org.parent) is not None
    lead = [it for it in its if see and is_mine(a, it) and not it["how"]]
    return {"mine": mine, "open": len(lead), "overdue": sum(overdue(it, today) for it in lead)}


def week52(conn: sqlite3.Connection, a, today: date) -> tuple[int, int] | None:
    """팀장 · 본부장의 9시 주 52시간 알림 — 가장 최근 자료가 이번 주(월~오늘)일 때 범위 안의 (주의 45시간 이상 · 초과 52시간 넘음) 수. 아니면 None."""
    if a.position not in (Role.TEAM_LEADER, Role.DIVISION_HEAD):
        return None
    row = conn.execute("SELECT date, data FROM attendance_days ORDER BY date DESC LIMIT 1").fetchone()
    if not row or date.fromisoformat(row[0]) < today - timedelta(days=today.weekday()):
        return None
    org = Org(conn)
    who = viewer_unit(a, org.parent)
    teams = json.loads(row[1])["teams"]
    units = team_units(teams, org.people, org.parent)
    ms = [m for t in teams if units[t["team"]] == who for m in t["members"]]
    over = sum(1 for m in ms if m["over52Flag"] or m["weeklyActualHours"] > 52)
    warn = sum(1 for m in ms if 45 <= m["weeklyActualHours"] and not (m["over52Flag"] or m["weeklyActualHours"] > 52))
    return warn, over


def _contacts(conn: sqlite3.Connection, uids: set[int], hr: bool) -> dict[int, dict]:
    """조직도와 같은 연락처 (비공개 휴대폰은 HR 에게만)."""
    if not uids:
        return {}
    q = ",".join("?" * len(uids))
    return {r[0]: {"email": r[1] or "", "mailNo": r[2] or "", "mobile": (r[3] or "") if (r[4] or hr) else ""} for r in conn.execute(
        "SELECT id, COALESCE(NULLIF(hr_email, ''), email), hiworks_mail_no, COALESCE(NULLIF(hr_mobile, ''), mobile), mobile_public"
        f" FROM users WHERE id IN ({q})", tuple(uids))}


def _today() -> date:
    return auth._now().astimezone(auth.KST).date()


@bp.get("/api/attendance/checks")
def checks_get() -> Response:
    conn, a, today = auth._db(), g.actor, _today()
    org = Org(conn)
    who = viewer_unit(a, org.parent)
    if who is None:
        abort(403)
    keep = (today - timedelta(days=DONE_KEEP)).isoformat()
    its = [it for it in collect(conn, org, today) if (who == "all" or it["unit"] == who)
           and (not it["how"] or ((it["doneAt"] or it["date"])[:10] >= keep))]
    contact = _contacts(conn, {it["uid"] for it in its if it["uid"] and not it["how"]}, a.is_hr)
    names = dict(conn.execute(f"SELECT id, name FROM users WHERE id IN ({','.join('?' * len(ids))})", tuple(ids)).fetchall()) if (
        ids := {it["doneBy"] for it in its if it["doneBy"]}) else {}
    out = [{"date": it["date"], "team": it["team"], "name": it["name"], "position": it["position"], "kind": it["kind"], "doc": DOCS[it["kind"]],
            "label": it["label"], "state": "done" if it["how"] else "self" if it["selfAt"] else "open", "how": it["how"],
            "doneAt": it["doneAt"], "doneByName": names.get(it["doneBy"], ""), "selfAt": it["selfAt"],
            "mine": is_mine(a, it), "can": can_check(a, it, org), "overdue": overdue(it, today),
            "days": (today - date.fromisoformat(it["date"])).days, "contact": contact.get(it["uid"]) if not it["how"] else None} for it in its]
    return jsonify(today=today.isoformat(), checkFrom=CHECK_FROM, due=DUE_DAYS, items=out)


def _find(its: list[dict], body: object, keys: tuple[str, ...]) -> dict | None:
    if not isinstance(body, dict) or not all(isinstance(body.get(k), str) for k in keys):
        return None
    return next((it for it in its if all(it[k] == body[k] for k in keys)), None)


@bp.post("/api/attendance/checks")
def checks_post() -> Response:
    conn, a, today, body = auth._db(), g.actor, _today(), request.get_json(silent=True)
    org = Org(conn)
    if viewer_unit(a, org.parent) is None:
        abort(403)
    if not isinstance(body, dict) or not isinstance(body.get("done"), bool):
        return jsonify(error="done 은 true/false 입니다."), 400
    it = _find(collect(conn, org, today), body, ("date", "team", "name"))
    if it is None:
        return jsonify(error="챙길 일이 없는 사람입니다."), 404
    if not can_check(a, it, org):
        return jsonify(error="이 사람의 서류는 확인할 수 없습니다."), 403
    if body["done"]:
        if not it["how"]:
            conn.execute("INSERT INTO attendance_checks (date, team, name, done_at, done_by, done_how) VALUES (?, ?, ?, ?, ?, 'MANUAL')"
                         " ON CONFLICT(date, team, name) DO UPDATE SET done_at = excluded.done_at, done_by = excluded.done_by, done_how = 'MANUAL'",
                         (it["date"], it["team"], it["name"], auth._iso(auth._now()), a.user_id))
    elif it["how"] == "NOTE":
        return jsonify(error="하이웍스 결재로 확인된 것은 취소할 수 없습니다."), 409
    elif it["how"]:
        conn.execute("UPDATE attendance_checks SET done_at = NULL, done_by = NULL, done_how = NULL WHERE date = ? AND team = ? AND name = ?",
                     (it["date"], it["team"], it["name"]))
    return jsonify(ok=True)


@bp.get("/api/attendance/todo")
def todo_get() -> Response:
    conn, a = auth._db(), g.actor
    t = todo_counts(conn, a, _today())
    mine = [{"date": it["date"], "team": it["team"], "kind": it["kind"], "doc": DOCS[it["kind"]], "label": it["label"], "self": bool(it["selfAt"])}
            for it in t["mine"]]
    return jsonify(mine=mine, open=t["open"], overdue=t["overdue"])


@bp.post("/api/attendance/mine")
def mine_post() -> Response:
    conn, a, body = auth._db(), g.actor, request.get_json(silent=True)
    it = _find([it for it in collect(conn, Org(conn), _today()) if it["uid"] == a.user_id], body, ("date", "team"))
    if it is None:
        return jsonify(error="내 근태 서류가 아닙니다."), 404
    if not it["selfAt"] and not it["how"]:
        conn.execute("INSERT INTO attendance_checks (date, team, name, self_at) VALUES (?, ?, ?, ?)"
                     " ON CONFLICT(date, team, name) DO UPDATE SET self_at = excluded.self_at",
                     (it["date"], it["team"], it["name"], auth._iso(auth._now())))
    return jsonify(ok=True)


@bp.post("/api/attendance/found")
def found_post() -> Response:
    """Cowork 가 하이웍스에서 다시 찾은 늦은 서류 — {found:[{date, team, name}]} 중 아직 확인 전인 것만 자동 확인."""
    key = _key()
    if not key:
        abort(404)
    if not hmac.compare_digest(request.headers.get("X-Attend-Key", "").encode(), key.encode()):
        return jsonify(error="열쇠가 맞지 않습니다."), 403
    body = request.get_json(silent=True)
    found = body.get("found") if isinstance(body, dict) else None
    if not isinstance(found, list) or len(found) > 2000 or not all(isinstance(f, dict) for f in found):
        return jsonify(error="found 는 2000개까지의 목록이어야 합니다."), 400
    conn = auth._db()
    its = collect(conn, Org(conn), _today())
    hit = {(it["date"], it["team"], it["name"]): it for f in found if (it := _find(its, f, ("date", "team", "name"))) and not it["how"]}  # 같은 줄이 두 번 와도 한 번
    now = auth._iso(auth._now())
    for it in hit.values():
        conn.execute("INSERT INTO attendance_checks (date, team, name, done_at, done_how) VALUES (?, ?, ?, ?, 'AUTO')"
                     " ON CONFLICT(date, team, name) DO UPDATE SET done_at = excluded.done_at, done_by = NULL, done_how = 'AUTO'",
                     (it["date"], it["team"], it["name"], now))
    logger.info("근태 늦은 서류 자동 확인 %d건 (받은 %d줄)", len(hit), len(found))
    return jsonify(ok=True, marked=len(hit))


def stats_of(days: list[tuple[str, list[dict]]], its: list[dict]) -> dict:
    """월 · 연 집계 — days = [(날짜, 범위 안 팀들)] · its = 그 기간의 챙길 일. 사람은 (팀, 이름)."""
    ppl: dict[tuple[str, str], dict] = {}
    weeks: dict[tuple[str, str], dict[tuple[int, int], float]] = defaultdict(dict)
    teams: dict[str, dict] = {}
    for d, ts in days:
        wk = date.fromisoformat(d).isocalendar()[:2]
        for t in ts:
            tt = teams.setdefault(t["team"], {"team": t["team"], "late": 0, "miss": 0})
            for m in t["members"]:
                k = (t["team"], m["name"])
                p = ppl.setdefault(k, {"name": m["name"], "team": t["team"], "position": m.get("position", ""), "late": 0, "lateMin": 0,
                                       "miss": 0, "open": 0, "days": []})
                h = m["weeklyActualHours"] if not m["over52Flag"] else max(m["weeklyActualHours"], 52.1)
                weeks[k][wk] = max(weeks[k].get(wk, 0), h)
                if m["status"] == "지각":
                    p["late"] += 1
                    p["lateMin"] += m["lateMinutes"]
                    tt["late"] += 1
                    p["days"].append({"date": d, "kind": "late", "min": m["lateMinutes"]})
                elif m["status"] == "미인식":
                    p["miss"] += 1
                    tt["miss"] += 1
                    p["days"].append({"date": d, "kind": "miss", "min": 0})
    for it in its:
        if not it["how"] and (it["team"], it["name"]) in ppl:
            ppl[(it["team"], it["name"])]["open"] += 1
    for k, p in ppl.items():
        p["over52"] = sum(1 for h in weeks[k].values() if h > 52)
        p["maxWeek"] = round(max(weeks[k].values(), default=0), 1)
    rows = [p for p in ppl.values() if p["late"] or p["miss"]]  # 지각 · 미인식이 둘 다 없는 사람은 빼고 (2026-11-13 인사담당자)
    rows.sort(key=lambda p: (-p["late"], -p["miss"], -p["lateMin"], p["name"]))  # 지각 횟수가 많은 사람이 맨 위 — 같으면 미인식 → 지각 분
    return {"people": rows, "teams": sorted(teams.values(), key=lambda t: t["team"])}


@bp.get("/api/attendance/stats")
def stats_get() -> Response:
    conn, a, today = auth._db(), g.actor, _today()
    org = Org(conn)
    who = viewer_unit(a, org.parent)
    if who is None:
        abort(403)
    period = request.args.get("period") or today.isoformat()[:7]
    if not re.fullmatch(r"20[0-9]{2}(-(0[1-9]|1[0-2]))?", period):
        return jsonify(error="period 는 YYYY 또는 YYYY-MM 입니다."), 400
    lo, hi = (f"{period}-01", f"{period}-31") if len(period) == 7 else (f"{period}-01-01", f"{period}-12-31")
    days = []
    for d, data in conn.execute("SELECT date, data FROM attendance_days WHERE date BETWEEN ? AND ? ORDER BY date", (lo, hi)):
        ts = json.loads(data)["teams"]
        if who != "all":
            units = team_units(ts, org.people, org.parent)
            ts = [t for t in ts if units[t["team"]] == who]
        days.append((d, ts))
    its = collect(conn, org, today, max(lo, CHECK_FROM), hi)
    return jsonify(period=period, days=len(days), scope="전체" if who == "all" else org.names.get(who, ""), **stats_of(days, its))


LEAVE_DAYS = {"반차": 0.5, "반반차": 0.25}  # 그 밖의 휴가는 하루
NORMAL = {"정상", "연장근무", "야간근무", "야간교대", "연장/야간근무", "외근", "해외출장", "근무변경", "단축근무", "입사일", "회식", "워크샵"}


def my_rows(days: list[tuple[str, list[dict]]], org: Org, uid: int) -> list[dict]:
    """그 사람의 날짜별 줄 — (이름, 단위)가 그 사람 번호로 맞는 근태 줄만. 최근 날부터."""
    out = []
    for d, teams in days:
        units = team_units(teams, org.people, org.parent)
        for t in teams:
            unit = units[t["team"]]
            for m in t["members"]:
                if unit is not None and org.uid.get((m["name"], unit)) == uid:
                    out.append({"date": d, "team": t["team"], "checkIn": m.get("checkIn", ""), "checkOut": m.get("checkOut", ""),
                                "status": m.get("status", ""), "lateMinutes": m.get("lateMinutes", 0)})
    return sorted(out, key=lambda r: r["date"], reverse=True)


def my_sum(rows: list[dict]) -> dict:
    """요약 네 칸 — 지각(횟수 · 분) · 미인식 · 결근 · 휴가(반차 0.5 · 반반차 0.25 · 그 밖 하루) · 확인 전 서류."""
    late = [r for r in rows if r["status"] == "지각"]
    leave = sum(LEAVE_DAYS.get(r["status"], 1) for r in rows if r["status"] not in NORMAL and r["status"] not in ("지각", "미인식", "결근", ""))
    return {"late": len(late), "lateMin": sum(r["lateMinutes"] for r in late), "miss": sum(r["status"] in ("미인식", "결근") for r in rows),
            "leave": leave, "open": sum(1 for r in rows if r.get("doc") and r["doc"]["state"] != "done")}


@bp.get("/api/attendance/me")
def me_get() -> Response:
    conn, a, today = auth._db(), g.actor, _today()
    period = request.args.get("period") or today.isoformat()[:7]
    if not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", period):
        return jsonify(error="period 는 YYYY-MM 입니다."), 400
    org = Org(conn)
    days = [(d, json.loads(data)["teams"]) for d, data in conn.execute(
        "SELECT date, data FROM attendance_days WHERE date BETWEEN ? AND ? ORDER BY date", (f"{period}-01", f"{period}-31"))]
    rows = my_rows(days, org, a.user_id)
    docs = {(it["date"], it["team"]): it for it in collect(conn, org, today, max(f"{period}-01", CHECK_FROM), f"{period}-31") if it["uid"] == a.user_id}
    for r in rows:
        it = docs.get((r["date"], r["team"]))
        r["doc"] = {"doc": DOCS[it["kind"]], "state": "done" if it["how"] else "self" if it["selfAt"] else "open"} if it else None
    months = sorted({d[:7] for (d,) in conn.execute("SELECT date FROM attendance_days")}, reverse=True)
    return jsonify(period=period, months=months, rows=rows, sum=my_sum(rows))


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

    k = lambda **kw: kind_of({"status": "정상", "note": "", **kw})  # noqa: E731
    assert (k(status="미인식"), k(status="지각"), k(status="결근"), k(status="병가", note="병가 (사용자 확인, 하이웍스 신청 문서 미확인)"),
            k(note="[확인필요] 퇴근 미인식 — 하이웍스 문서 없음"), k(status="연차", note="연차 / 개인사유"), k()) == ("miss", "late", "leave", "leave", "miss", None, None), "챙길 일 고르기"
    assert DONE_NOTE.search("출근 미인식 — 미인식 소명서 제출 (11/12, 결재 완료)") and not DONE_NOTE.search("[확인필요] 퇴근 미인식 — 하이웍스 문서 없음"), "그날 서류가 있으면 자동 확인"
    lb = lambda kind, **kw: label_of({"status": "정상", "note": "", "checkIn": "08:50", "checkOut": "18:00", "lateMinutes": 0, **kw}, kind)  # noqa: E731
    assert (lb("miss", checkOut="-"), lb("miss", checkIn=""), lb("late", lateMinutes=12), lb("leave", status="병가"), lb("leave", status="결근")) == (
        "퇴근 미인식", "출근 미인식", "지각 12분", "병가 · 하이웍스 문서 없음", "결근"), "줄 글자"
    it = {"how": None, "date": "2026-11-12"}
    assert not overdue(it, date(2026, 11, 18)) and overdue(it, date(2026, 11, 19)) and not overdue({**it, "how": "MANUAL"}, date(2026, 12, 1)), "기한 = 날짜 + 7일, 확인되면 끝"
    mm = lambda name, st="정상", lm=0, h=40.0, f=False: {"name": name, "status": st, "lateMinutes": lm, "weeklyActualHours": h, "over52Flag": f}  # noqa: E731
    days = [("2026-11-09", [{"team": "A", "members": [mm("가", "지각", 5, 20), mm("나", "미인식", h=53), mm("라", "지각", 30)]}]),
            ("2026-11-10", [{"team": "A", "members": [mm("가", "미인식", h=30), mm("나", h=12), mm("라")]}]),
            ("2026-11-16", [{"team": "A", "members": [mm("가", "지각", 7, 9), mm("나", h=8), mm("다", h=60), mm("라")]}])]
    st = stats_of(days, [{"team": "A", "name": "가", "how": None}, {"team": "A", "name": "가", "how": "MANUAL"}])
    g_ = {p["name"]: p for p in st["people"]}
    assert set(g_) == {"가", "나", "라"} and (g_["가"]["late"], g_["가"]["lateMin"], g_["가"]["miss"], g_["가"]["open"]) == (2, 12, 1, 1), "지각 횟수 · 분 · 미인식 · 서류 미확인"
    assert "다" not in g_, "지각 · 미인식이 없는 사람은 빠진다 (52시간이 넘어도)"
    assert (g_["나"]["over52"], g_["나"]["maxWeek"], g_["가"]["over52"]) == (1, 53, 0) and st["teams"] == [{"team": "A", "late": 3, "miss": 2}], "52시간 넘은 주 · 가장 긴 주 · 팀 합"
    assert [p["name"] for p in st["people"]] == ["가", "라", "나"] and len(g_["가"]["days"]) == 3, "지각 많은 순 → 미인식 → 지각 분 · 날짜별"
    ms = my_sum([{"status": "지각", "lateMinutes": 8}, {"status": "미인식", "lateMinutes": 0, "doc": {"state": "open"}}, {"status": "결근", "lateMinutes": 0},
                 {"status": "반차", "lateMinutes": 0}, {"status": "연차", "lateMinutes": 0}, {"status": "정상", "lateMinutes": 0, "doc": {"state": "done"}}])
    assert ms == {"late": 1, "lateMin": 8, "miss": 2, "leave": 1.5, "open": 1}, f"내 근태 요약 {ms}"
    print("attendance 검사 통과")


if __name__ == "__main__":
    for out in (sys.stdout, sys.stderr):
        out.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["check"]:
        check()
    else:
        sys.exit("사용법: python -m attendance check")
