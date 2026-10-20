"""KPI 계획서 ↔ 인사포털 (2026-10-20 인사담당자 결정 — 직원이 쓰고 제출, 바로 위 관리자가 확정).

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

  GET  /api/kpi/me                  내 올해 계획서 · 작성 기간 · 바로 위 관리자 — 본인만
  POST /api/kpi/me/draft            {"draft": [...]} 임시 저장 (DRAFT 일 때만 · 계획서가 없으면 만든다). 모양 · 글자 한도만 본다
  POST /api/kpi/me/submit           제출 (DRAFT → SUBMITTED). 계획서 규칙을 전부 통과해야 한다
  POST /api/kpi/me/cancel           제출 취소 (SUBMITTED → DRAFT)
  POST /api/kpi/me/request-change   {"reason": "..."} 확정 뒤 수정 요청 (CONFIRMED → CHANGE_REQUESTED)
  GET  /api/kpi/admin/year          ?year= 그해 작성 기간 (HR)
  POST /api/kpi/admin/year          {"year", "editFrom", "editTo", "evalStart"} 작성 기간 정하기 · 닫기 (HR)

  GET  /api/kpi/plans/<uid>         그 사람 올해 계획서 + owner — 주인 · 바로 위 관리자 · HR 만
  POST /api/kpi/plans/<uid>/confirm 확정 (SUBMITTED → CONFIRMED, 새 판)
  POST /api/kpi/plans/<uid>/return  {"note"} 돌려보내기 (SUBMITTED → DRAFT)
  POST /api/kpi/plans/<uid>/unlock  수정 요청 풀어 주기 (CHANGE_REQUESTED → DRAFT, 초안 = 마지막 확정본)
  POST /api/kpi/plans/<uid>/reject  {"note"} 수정 요청 거절 (CHANGE_REQUESTED → CONFIRMED)
  GET  /api/kpi/inbox               내가 처리할 것 {"items": […], "team": {상태: 수}} — HR 은 관리자가 없는 것 · 맡겨진 것도 proxy 로
  GET  /api/kpi/admin               ?year= 사람마다 상태 · 바로 위 관리자 · 사람별 기간 (HR)
  POST /api/kpi/admin/window        {"userId", "year", "editFrom", "editTo"} | {"userId", "year", "clear": true} 한 사람만의 작성 기간 (HR)
  GET/POST /api/kpi/delegation      {"on": bool} 대표이사 몫을 HR 에 맡기기 — 대표이사 본인만

본인 쓰기는 서버가 늘 같은 순서로 다시 본다: KPI 작성 대상 403 → 작성 기간 403 → 상태 409 → 내용 400.
관리자 처리는: 처리 권한 403 → 계획서 주인의 작성 기간 403 → 상태 409 → 사유 400. 권한은 요청마다 명단에서 새로 계산한다 (decider).
감사로그에는 행동 이름 · 대상 · 해(· 판)만 남긴다 — 계획서 · 사유 문장은 넣지 않는다. 임시 저장은 남기지 않는다. HR 이 대신 하면 on_behalf_of.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Iterator, NamedTuple

from flask import Blueprint, Response, g, jsonify, request

import auth
from hr_eval.domain.errors import StateConflictError
from hr_eval.domain.kpi_plan import (
    PLAN_MAX_BYTES, REASON_MAX, assign_ids, clean_plan, next_status, validate_plan, window_open, window_span,
)
from hr_eval.domain.models import Actor, AuditEntry, Role
from hr_eval.domain.org import Dept, Person, manager_of

bp = Blueprint("kpi", __name__)

YEARS = range(2026, 2101)  # kpi_years 의 CHECK 와 같게
STATES = ("NONE", "DRAFT", "SUBMITTED", "CONFIRMED", "CHANGE_REQUESTED")  # NONE = 계획서 줄이 없음
UID = "<int(max=2147483647):uid>"  # 경로의 사람 번호 — SQLite 정수보다 큰 수는 묶다가 서버 오류가 나므로 길 단계에서 404


def plan_year() -> int:
    """지금 쓰는 계획서의 해 — 한국 날짜의 올해."""
    return auth._today().year


def load_org(conn: sqlite3.Connection) -> tuple[list[Person], list[Dept]]:
    """바로 위 관리자를 찾는 데 쓰는 명단 (활성 · 비활성 모두 — Person.is_active 로 갈린다)."""
    people = [Person(m.id, m.department_id, m.position, m.led_department_id, m.is_active) for m in auth._members(conn)]
    return people, auth._depts(conn)


def window_rows(conn: sqlite3.Connection, user_id: int, year: int) -> tuple[tuple | None, tuple | None]:
    """(그해 (edit_from, edit_to, eval_start) | None, 그 사람만의 (edit_from, edit_to) | None) — domain 의 window_open 에 그대로 넘긴다."""
    return (
        conn.execute("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", (year,)).fetchone(),
        conn.execute("SELECT edit_from, edit_to FROM kpi_windows WHERE user_id = ? AND year = ?", (user_id, year)).fetchone(),
    )


def _window_json(year_row: tuple | None, person_row: tuple | None) -> dict:
    """화면에 보이는 작성 기간 — from · to 는 지금 적용되는 기간 (사람 기간 우선), open 도 같은 규칙으로 가린다."""
    start, end = window_span(year_row, person_row)
    return {"from": start, "to": end, "evalStart": year_row[2] if year_row else None, "open": window_open(auth._today(), year_row, person_row)}


def plan_json(conn: sqlite3.Connection, user_id: int, year: int) -> dict | None:
    """그 사람 그해 계획서를 화면 모양으로. 없으면 None. confirmed = 가장 최근 확정본 (없으면 None)."""
    row = conn.execute("SELECT status, draft, note, note_kind, change_reason FROM kpi_plans WHERE user_id = ? AND year = ?",
                       (user_id, year)).fetchone()
    if row is None:
        return None
    v = conn.execute(
        "SELECT v.version, v.content, v.confirmed_at, u.name FROM kpi_versions v JOIN users u ON u.id = v.confirmed_by"
        " WHERE v.user_id = ? AND v.year = ? ORDER BY v.version DESC LIMIT 1", (user_id, year)).fetchone()
    return {
        "status": row[0], "draft": json.loads(row[1]), "note": row[2], "noteKind": row[3], "changeReason": row[4],
        "confirmed": {"version": v[0], "content": json.loads(v[1]), "at": v[2], "by": v[3]} if v else None,
    }


class _Refuse(Exception):
    """요청을 막는다 — 응답 몸통은 {"error", …extra}, 상태 코드는 code. 아래 핸들러가 JSON 으로 바꿔 돌려준다."""

    def __init__(self, error: str, code: int, **extra: object) -> None:
        super().__init__(error)
        self.body, self.code = {"error": error, **extra}, code


@bp.errorhandler(_Refuse)
def _refused(e: _Refuse) -> tuple[Response, int]:
    return jsonify(e.body), e.code


@contextmanager
def _txn(conn: sqlite3.Connection) -> Iterator[None]:
    """한 트랜잭션 — 확인하고 쓰는 사이에 다른 요청이 끼어들지 못한다 (빠르게 두 번 눌러도 한 번만 통과)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


class _Mine(NamedTuple):
    conn: sqlite3.Connection
    uid: int           # 계획서 주인
    year: int
    draft: str | None  # 저장된 초안(JSON 글자). 계획서가 없으면 None
    next: str          # 이 행동을 하면 가는 상태
    now: str
    behalf: int | None = None  # HR 이 대신 처리하면 원래 관리자 (감사로그 on_behalf_of)


def _flow(status: str, action: str) -> str:
    """지금 상태에서 행동을 하면 가는 상태. 안 되면 409 (지금 상태를 알려 준다)."""
    try:
        return next_status(status, action)
    except StateConflictError:
        raise _Refuse("지금 상태에서는 할 수 없습니다.", 409, status=status) from None


@contextmanager
def _own(action: str) -> Iterator[_Mine]:
    """내 계획서를 바꾸는 요청의 틀. 본문 앞에서 KPI 작성 대상 403 → 작성 기간 403 → 상태 409 를 순서대로 본다 (내용 400 은 본문이).

    action 은 서버가 정하는 말("save" · "submit" · "cancel" · "request_change") — 요청이 보낸 글자를 상태 흐름에 넘기지 않는다.
    """
    conn, uid, year = auth._db(), g.actor.user_id, plan_year()
    with _txn(conn):
        if not conn.execute("SELECT writes_kpi FROM users WHERE id = ?", (uid,)).fetchone()[0]:
            raise _Refuse("KPI 작성 대상이 아닙니다.", 403)
        if not window_open(auth._today(), *window_rows(conn, uid, year)):
            raise _Refuse("KPI 작성 기간이 아닙니다.", 403)
        row = conn.execute("SELECT status, draft FROM kpi_plans WHERE user_id = ? AND year = ?", (uid, year)).fetchone()
        status = row[0] if row else "NONE"
        nxt = _flow("DRAFT" if action == "save" and row is None else status, action)  # 첫 임시 저장이 계획서를 만든다
        yield _Mine(conn, uid, year, row[1] if row else None, nxt, auth._iso(auth._now()))


def _log(m: _Mine, action: str, **after: int) -> None:
    """감사로그: 행동 · 계획서 주인 · 해(· 판)만. HR 이 대신 했으면 on_behalf_of = 원래 관리자."""
    auth._audit(m.conn, AuditEntry.by(g.actor, on_behalf_of=m.behalf, action=action, entity_type="kpi_plan", entity_id=m.uid,
                                      after_data={"year": m.year, **after}))


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def _text(v: object, label: str) -> str:
    """사유 글 (돌려보내기 · 거절 · 수정 요청): 글자만, 앞뒤 공백을 뺀 1~REASON_MAX자, UTF-8 로 쓸 수 있어야 한다 — 아니면 400.
    label 은 문구용 ("…이유")."""
    t = v.strip() if isinstance(v, str) else ""
    try:
        t.encode("utf-8")
    except UnicodeEncodeError:  # 짝 없는 서로게이트 — SQLite 에 묶다가 서버 오류가 나기 전에
        raise _Refuse(f"{label}에 쓸 수 없는 글자가 있습니다.", 400) from None
    if not 0 < len(t) <= REASON_MAX:
        raise _Refuse(f"{label}를 {REASON_MAX}자 안으로 써 주세요.", 400)
    return t


@bp.get("/api/kpi/me")
def me() -> Response:
    """{"year", "writesKpi", "window": {"from", "to", "evalStart", "open"}, "plan": {…} | null, "manager": {"id", "name"} | null}.
    window 의 from · to 는 지금 적용되는 기간 — 그 사람만의 기간이 있으면 그것. KPI 작성이 꺼진 사람도 읽기는 된다 (writesKpi false)."""
    conn, uid, year = auth._db(), g.actor.user_id, plan_year()
    people, depts = load_org(conn)
    boss = manager_of(uid, people, depts)
    name = conn.execute("SELECT name FROM users WHERE id = ?", (boss,)).fetchone()[0] if boss is not None else None
    return jsonify(
        year=year,
        writesKpi=bool(conn.execute("SELECT writes_kpi FROM users WHERE id = ?", (uid,)).fetchone()[0]),
        window=_window_json(*window_rows(conn, uid, year)),
        plan=plan_json(conn, uid, year),
        manager={"id": boss, "name": name} if boss is not None else None,
    )


def _clean_draft(draft: object, year: int) -> list[dict]:
    """임시 저장할 내용: 200KB 이하 → 모양 · 글자 한도 → 아는 칸만. 나머지 규칙(가중치 · 개수 …)은 제출 때 본다."""
    try:
        too_big = len(json.dumps(draft, ensure_ascii=False).encode("utf-8")) > PLAN_MAX_BYTES
    except ValueError:  # UTF-8 로 쓸 수 없는 글자(짝 없는 서로게이트)
        raise _Refuse("계획서에 쓸 수 없는 글자가 있습니다.", 400) from None
    if too_big:
        raise _Refuse(f"계획서가 너무 큽니다 ({PLAN_MAX_BYTES // 1024}KB까지).", 400)
    issues = [i.message for i in validate_plan(draft, year) if i.code in ("SHAPE", "TEXT_LONG")]
    if issues:
        raise _Refuse("저장하지 못했습니다. 아래를 고쳐 주세요.", 400, issues=issues)
    return clean_plan(draft)


@bp.post("/api/kpi/me/draft")
def draft_save() -> Response:
    """{"draft": [...]} → {"ok": true}. 모양 · 글자 한도를 본 뒤 clean_plan 으로 아는 칸만 남긴다 (모르는 칸은 버리고, 업무 종류가 정해지지 않은
    줄의 kind · target 은 바로잡는다). rid · sid 는 있으면 보낸 대로 두고 — 번호는 확정할 때 서버가 다시 매긴다. 감사로그는 남기지 않는다."""
    draft = _body().get("draft")
    with _own("save") as m:
        m.conn.execute(
            "INSERT INTO kpi_plans (user_id, year, status, draft, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id, year) DO UPDATE SET draft = excluded.draft, updated_at = excluded.updated_at",
            (m.uid, m.year, m.next, json.dumps(_clean_draft(draft, m.year), ensure_ascii=False), m.now))
    return jsonify(ok=True)


@bp.post("/api/kpi/me/submit")
def submit() -> Response:
    """초안 전체가 계획서 규칙을 통과해야 한다. 돌려보낸 사유가 있었으면 다시 낸 것으로 보고 지운다."""
    with _own("submit") as m:
        issues = validate_plan(json.loads(m.draft), m.year)
        if issues:
            raise _Refuse("제출하지 못했습니다. 아래를 고쳐 주세요.", 400, issues=[i.message for i in issues])
        m.conn.execute("UPDATE kpi_plans SET status = ?, submitted_at = ?, note = NULL, note_kind = NULL, updated_at = ?"
                       " WHERE user_id = ? AND year = ?", (m.next, m.now, m.now, m.uid, m.year))
        _log(m, "KPI_SUBMITTED")
    return jsonify(ok=True)


@bp.post("/api/kpi/me/cancel")
def cancel() -> Response:
    with _own("cancel") as m:
        m.conn.execute("UPDATE kpi_plans SET status = ?, updated_at = ? WHERE user_id = ? AND year = ?", (m.next, m.now, m.uid, m.year))
        _log(m, "KPI_CANCELLED")
    return jsonify(ok=True)


@bp.post("/api/kpi/me/request-change")
def request_change() -> Response:
    """{"reason": "…"} 1~200자(앞뒤 공백은 뺀다). 남아 있던 돌려보낸 · 거절 사유는 지운다."""
    reason = _body().get("reason")
    with _own("request_change") as m:
        reason = _text(reason, "수정을 요청하는 이유")
        m.conn.execute("UPDATE kpi_plans SET status = ?, change_reason = ?, note = NULL, note_kind = NULL, updated_at = ?"
                       " WHERE user_id = ? AND year = ?", (m.next, reason, m.now, m.uid, m.year))
        _log(m, "KPI_CHANGE_REQUESTED")
    return jsonify(ok=True)


# ---------------- 작성 기간 (HR) ----------------
def _year_json(conn: sqlite3.Connection, year: int) -> dict:
    row = conn.execute("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", (year,)).fetchone() or (None, None, None)
    return {"year": year, "editFrom": row[0], "editTo": row[1], "evalStart": row[2]}


def _iso_date(v: object) -> str | None:
    """빈 값(null · "") → None, YYYY-MM-DD 로 쓴 실제 날짜 → 그대로. 그 밖은 ValueError."""
    if v is None or v == "":
        return None
    if not (isinstance(v, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", v)):
        raise ValueError(v)
    date.fromisoformat(v)  # 없는 날(2월 30일 …)은 여기서 ValueError
    return v


def _out_of_year(year: int, *days: str | None) -> tuple[Response, int] | None:
    """기간 날짜가 그해 밖이면 400 — 그 해의 줄은 그해(한국 날짜)에만 읽히므로 다른 해의 날짜는 영영 쓰이지 않는다."""
    if all(d is None or d[:4] == str(year) for d in days):
        return None
    return jsonify(error=f"{year}년 기간에는 {year}년 날짜만 넣을 수 있어요. {year + 1}년 기간은 {year + 1}년 1월 1일부터 정할 수 있어요."), 400


def _year_arg() -> int:
    """?year= (없으면 올해). 2026~2100 의 네 자리 해가 아니면 400."""
    raw = request.args.get("year")
    year = plan_year() if raw is None else int(raw) if re.fullmatch(r"[0-9]{4}", raw) else 0
    if year not in YEARS:
        raise _Refuse("해가 올바르지 않습니다.", 400)
    return year


@bp.get("/api/kpi/admin/year")
def year_get() -> Response:
    """?year= (없으면 올해) → {"year", "editFrom", "editTo", "evalStart"} — 정하지 않았으면 null."""
    if (denied := auth._hr_only()) is not None:
        return denied
    return jsonify(_year_json(auth._db(), _year_arg()))


@bp.post("/api/kpi/admin/year")
def year_set() -> Response:
    """{"year", "editFrom", "editTo", "evalStart"} — 날짜는 YYYY-MM-DD, 셋 다 그해 안. editFrom · editTo 는 둘 다 쓰거나 둘 다 비운다(= 기간 닫기).
    evalStart(평가 시작일)는 비워도 된다. 그 해의 줄을 통째로 바꾼다."""
    if (denied := auth._hr_only()) is not None:
        return denied
    b = _body()
    year = b.get("year")
    try:
        start, end, ev = (_iso_date(b.get(k)) for k in ("editFrom", "editTo", "evalStart"))
        valid = type(year) is int and year in YEARS and (start is None) == (end is None) and not (start and end < start)
    except ValueError:
        valid = False
    if not valid:
        return jsonify(error="해와 날짜(YYYY-MM-DD)를 확인해 주세요. 시작일과 종료일은 함께 쓰고, 종료일은 시작일 이후여야 합니다."), 400
    if (denied := _out_of_year(year, start, end, ev)) is not None:
        return denied
    conn = auth._db()
    with _txn(conn):
        before = _year_json(conn, year)
        conn.execute(
            "INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (year) DO UPDATE SET edit_from = excluded.edit_from, edit_to = excluded.edit_to,"
            " eval_start = excluded.eval_start, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
            (year, start, end, ev, auth._iso(auth._now()), g.actor.user_id))
        after = _year_json(conn, year)
        auth._audit(conn, AuditEntry.by(g.actor, action="KPI_YEAR_SET", entity_type="kpi_year", entity_id=year,
                                        before_data={k: v for k, v in before.items() if k != "year"},
                                        after_data={k: v for k, v in after.items() if k != "year"}))
    return jsonify(ok=True, **after)


# ---------------- 관리자 처리 ----------------
def _ceos(conn: sqlite3.Connection, people: list[Person]) -> dict[int, bool]:
    """대표이사별로 HR 에 맡겼는지 (ceo_delegation 에 줄이 있으면 켜짐)."""
    on = {r[0] for r in conn.execute("SELECT ceo_user_id FROM ceo_delegation")}
    return {p.id: p.id in on for p in people if p.position is Role.CEO}


def decider(conn: sqlite3.Connection, actor: Actor, owner_id: int) -> tuple[bool, int | None]:
    """(처리할 수 있나, 대신하는 것이면 원래 관리자). 요청마다 명단에서 새로 계산한다 — 저장해 두지 않아 팀을 옮기면 바로 바뀐다.

    바로 위 관리자 본인은 (True, None) — HR 권한이 있어도 관리자로 처리한다. HR 은 본인 계획서가 아니면 대신할 수 있다:
    관리자가 없으면 (True, None), 대표이사 몫은 대표이사가 맡김을 켰을 때만 (True, 대표이사), 그 밖에는 (True, 그 관리자).
    """
    people, depts = load_org(conn)
    return _decide(actor, owner_id, manager_of(owner_id, people, depts), _ceos(conn, people))


def _decide(actor: Actor, owner_id: int, boss: int | None, ceos: dict[int, bool]) -> tuple[bool, int | None]:
    """decider 의 판단 — 명단을 이미 읽은 HR 목록(canAct)도 같은 규칙을 쓴다."""
    if boss == actor.user_id:
        return True, None
    if actor.is_hr and actor.user_id != owner_id and ceos.get(boss, True):
        return True, boss
    return False, None


@contextmanager
def _manage(uid: int, action: str) -> Iterator[_Mine]:
    """남의 계획서를 처리하는 요청의 틀. 본문 앞에서 처리 권한 403 → 계획서 주인의 작성 기간 403 → 상태 409 를 순서대로 본다 (사유 400 은 본문이).

    action 은 서버가 정하는 말("confirm" · "return" · "unlock" · "reject") — 요청이 보낸 글자를 상태 흐름에 넘기지 않는다.
    계획서가 없으면 상태는 "NONE".
    """
    conn, year = auth._db(), plan_year()
    with _txn(conn):
        can, behalf = decider(conn, g.actor, uid)
        if not can:
            raise _Refuse("이 KPI를 처리할 권한이 없습니다.", 403)
        if not window_open(auth._today(), *window_rows(conn, uid, year)):
            raise _Refuse("KPI 작성 기간이 아닙니다.", 403)
        row = conn.execute("SELECT status, draft FROM kpi_plans WHERE user_id = ? AND year = ?", (uid, year)).fetchone()
        nxt = _flow(row[0] if row else "NONE", action)
        yield _Mine(conn, uid, year, row[1], nxt, auth._iso(auth._now()), behalf)


@bp.post(f"/api/kpi/plans/{UID}/confirm")
def confirm(uid: int) -> Response:
    """초안에 번호(rid · sid)를 붙여 새 판으로 쌓는다. 초안도 확정본과 같아지고(R3) 돌려보낸 · 수정 요청 사유는 비운다."""
    with _manage(uid, "confirm") as m:
        old = m.conn.execute("SELECT version, content FROM kpi_versions WHERE user_id = ? AND year = ? ORDER BY version", (uid, m.year)).fetchall()
        version = old[-1][0] + 1 if old else 1
        content = json.dumps(assign_ids(json.loads(m.draft), [json.loads(c) for _, c in old]), ensure_ascii=False)
        m.conn.execute("INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by, on_behalf_of) VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (uid, m.year, version, content, m.now, g.actor.user_id, m.behalf))
        m.conn.execute("UPDATE kpi_plans SET status = ?, draft = ?, confirmed_version = ?, note = NULL, note_kind = NULL, change_reason = NULL,"
                       " updated_at = ? WHERE user_id = ? AND year = ?", (m.next, content, version, m.now, uid, m.year))
        _log(m, "KPI_CONFIRMED", version=version)
    return jsonify(ok=True)


def _note_back(uid: int, action: str, kind: str, label: str) -> Response:
    """돌려보내기 · 거절: 이유(1~200자)를 남기고 상태를 옮긴다. 이유는 본인 화면에만 가고 감사로그에는 안 간다."""
    note = _body().get("note")
    with _manage(uid, action) as m:
        note = _text(note, label)
        m.conn.execute("UPDATE kpi_plans SET status = ?, note = ?, note_kind = ?, change_reason = NULL, updated_at = ? WHERE user_id = ? AND year = ?",
                       (m.next, note, kind, m.now, uid, m.year))
        _log(m, f"KPI_{kind}")
    return jsonify(ok=True)


@bp.post(f"/api/kpi/plans/{UID}/return")
def send_back(uid: int) -> Response:
    """{"note": "…"} 제출한 것을 돌려보낸다 (SUBMITTED → DRAFT)."""
    return _note_back(uid, "return", "RETURNED", "돌려보내는 이유")


@bp.post(f"/api/kpi/plans/{UID}/reject")
def reject(uid: int) -> Response:
    """{"note": "…"} 수정 요청을 거절한다 (CHANGE_REQUESTED → CONFIRMED, 확정본은 그대로)."""
    return _note_back(uid, "reject", "REJECTED", "거절하는 이유")


@bp.post(f"/api/kpi/plans/{UID}/unlock")
def unlock(uid: int) -> Response:
    """수정 요청을 풀어 준다 (CHANGE_REQUESTED → DRAFT). 초안 = 마지막 확정본 내용, 확정본은 그대로 유효하다."""
    with _manage(uid, "unlock") as m:
        last = m.conn.execute("SELECT content FROM kpi_versions WHERE user_id = ? AND year = ? ORDER BY version DESC LIMIT 1", (uid, m.year)).fetchone()[0]
        m.conn.execute("UPDATE kpi_plans SET status = ?, draft = ?, note = NULL, note_kind = NULL, change_reason = NULL, updated_at = ?"
                       " WHERE user_id = ? AND year = ?", (m.next, last, m.now, uid, m.year))
        _log(m, "KPI_UNLOCKED")
    return jsonify(ok=True)


@bp.get(f"/api/kpi/plans/{UID}")
def plan_get(uid: int) -> Response:
    """주인 · 바로 위 관리자 · HR 만. GET /api/kpi/me 의 plan 과 같은 모양 + {"owner": {"id", "name", "dept"}}. 계획서가 없으면 404."""
    conn = auth._db()
    if not (g.actor.is_hr or uid == g.actor.user_id or manager_of(uid, *load_org(conn)) == g.actor.user_id):
        return jsonify(error="이 KPI를 볼 권한이 없습니다."), 403
    plan = plan_json(conn, uid, plan_year())
    if plan is None:
        return jsonify(error="아직 작성한 KPI가 없습니다."), 404
    owner = conn.execute("SELECT u.id, u.name, d.name FROM users u JOIN departments d ON d.id = u.department_id WHERE u.id = ?", (uid,)).fetchone()
    return jsonify(**plan, owner={"id": owner[0], "name": owner[1], "dept": owner[2]})


@bp.get("/api/kpi/inbox")
def inbox() -> Response:
    """{"items": [내가 처리할 계획서], "team": {상태: 수}}. items = 내가 바로 위 관리자인 제출 · 수정 요청 (오래된 것부터).
    HR 이면 관리자가 없거나 대표이사가 맡긴 것도 proxy: true 로 (HR 본인 것은 빼고). 주인이 활성이고 KPI 작성이 켜진 것만 —
    team · HR 목록과 같은 기준. team = 내가 바로 위 관리자인 KPI 작성자들의 상태 수 (HR 대신 몫은 안 센다)."""
    conn, year, mine = auth._db(), plan_year(), g.actor.user_id
    people, depts = load_org(conn)
    ceos = _ceos(conn, people)
    items = []
    for uid, name, dept, title, status, submitted, updated, reason, draft in conn.execute(
            "SELECT p.user_id, u.name, d.name, COALESCE(u.title, ''), p.status, p.submitted_at, p.updated_at, p.change_reason, p.draft"
            " FROM kpi_plans p JOIN users u ON u.id = p.user_id JOIN departments d ON d.id = u.department_id"
            " WHERE p.year = ? AND p.status IN ('SUBMITTED', 'CHANGE_REQUESTED') AND u.is_active = 1 AND u.writes_kpi = 1", (year,)):
        boss = manager_of(uid, people, depts)
        if boss != mine and not (g.actor.is_hr and uid != mine and (boss is None or ceos.get(boss, False))):
            continue
        rows = [it for goal in json.loads(draft) for it in goal["items"]]
        items.append({"userId": uid, "name": name, "dept": dept, "title": title, "status": status,
                      "at": submitted if status == "SUBMITTED" else updated, "reason": reason if status == "CHANGE_REQUESTED" else None,
                      "items": len(rows), "reg": sum(it["kind"] == "reg" for it in rows), "irr": sum(it["kind"] == "irr" for it in rows),
                      "scheds": sum(len(it["scheds"]) for it in rows), "proxy": boss != mine})
    items.sort(key=lambda i: (i["at"], i["userId"]))
    team = dict.fromkeys(STATES, 0)
    for uid, status in conn.execute("SELECT u.id, p.status FROM users u LEFT JOIN kpi_plans p ON p.user_id = u.id AND p.year = ?"
                                    " WHERE u.is_active = 1 AND u.writes_kpi = 1", (year,)):
        if manager_of(uid, people, depts) == mine:
            team[status or "NONE"] += 1
    return jsonify(items=items, team=team)


# ---------------- 사람별 작성 기간 · 전체 보기 (HR) ----------------
@bp.get("/api/kpi/admin")
def admin_list() -> Response:
    """?year= (없으면 올해) → {"year", "window": {"from", "to", "evalStart", "open"}, "people": […]} — KPI 작성이 켜진 활성 직원 전원.
    window.open = 그해 기간이 오늘(한국 날짜) 열려 있나 (평가 시작일부터는 닫힘).
    사람마다 status ("NONE" = 계획서 없음) · confirmedVersion · manager {"id", "name"} | null · window (그 사람만의 기간이 있을 때만, 없으면 null) ·
    canAct (이 HR 이 확정 · 돌려보내기 · 해제 · 거절할 수 있나 — 처리 요청의 decider 와 같은 판단. 본인 것 · 맡기지 않은 대표이사 몫은 false)."""
    if (denied := auth._hr_only()) is not None:
        return denied
    year = _year_arg()
    conn = auth._db()
    year_row = conn.execute("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", (year,)).fetchone()
    own = {r[0]: r[1:] for r in conn.execute("SELECT user_id, edit_from, edit_to FROM kpi_windows WHERE year = ?", (year,))}
    people, depts = load_org(conn)
    ceos = _ceos(conn, people)
    names = dict(conn.execute("SELECT id, name FROM users"))
    out = []
    for uid, name, dept, status, version in conn.execute(
            "SELECT u.id, u.name, d.name, p.status, p.confirmed_version FROM users u JOIN departments d ON d.id = u.department_id"
            " LEFT JOIN kpi_plans p ON p.user_id = u.id AND p.year = ? WHERE u.is_active = 1 AND u.writes_kpi = 1"
            " ORDER BY u.department_id, u.name, u.id", (year,)):
        boss = manager_of(uid, people, depts)
        out.append({"userId": uid, "name": name, "dept": dept, "manager": {"id": boss, "name": names[boss]} if boss is not None else None,
                    "status": status or "NONE", "confirmedVersion": version,
                    "window": _window_json(year_row, own[uid]) if uid in own else None, "canAct": _decide(g.actor, uid, boss, ceos)[0]})
    ys = year_row or (None, None, None)
    return jsonify(year=year, window={"from": ys[0], "to": ys[1], "evalStart": ys[2], "open": window_open(auth._today(), year_row, None)}, people=out)


@bp.post("/api/kpi/admin/window")
def window_set() -> Response:
    """{"userId", "year", "editFrom", "editTo"} 그 사람만의 작성 기간 (둘 다 그해 안의 YYYY-MM-DD, 종료일 >= 시작일) ·
    {"userId", "year", "clear": true} 지우기. userId 는 있는 활성 직원. 감사로그 KPI_WINDOW_SET — 대상 번호 · 해 · 날짜 (지우면 날짜가 둘 다 null)."""
    if (denied := auth._hr_only()) is not None:
        return denied
    b = _body()
    uid, year, clear = b.get("userId"), b.get("year"), b.get("clear") is True
    try:
        start, end = (_iso_date(b.get(k)) for k in ("editFrom", "editTo"))
        valid = (type(uid) is int and 0 < uid <= 2147483647 and type(year) is int and year in YEARS
                 and (clear or bool(start and end and end >= start)))
    except ValueError:
        valid = False
    conn = auth._db()
    if not valid or conn.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1", (uid,)).fetchone() is None:
        return jsonify(error="사람과 해, 날짜(YYYY-MM-DD)를 확인해 주세요. 종료일은 시작일 이후여야 합니다."), 400
    if not clear and (denied := _out_of_year(year, start, end)) is not None:
        return denied
    with _txn(conn):
        if clear:
            conn.execute("DELETE FROM kpi_windows WHERE user_id = ? AND year = ?", (uid, year))
        else:
            conn.execute(
                "INSERT INTO kpi_windows (user_id, year, edit_from, edit_to, set_by, set_at) VALUES (?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (user_id, year) DO UPDATE SET edit_from = excluded.edit_from, edit_to = excluded.edit_to,"
                " set_by = excluded.set_by, set_at = excluded.set_at",
                (uid, year, start, end, g.actor.user_id, auth._iso(auth._now())))
        auth._audit(conn, AuditEntry.by(g.actor, action="KPI_WINDOW_SET", entity_type="kpi_window", entity_id=uid,
                                        after_data={"year": year, "editFrom": None if clear else start, "editTo": None if clear else end}))
    return jsonify(ok=True)


# ---------------- 대표이사 몫 맡기기 ----------------
def _ceo_only() -> tuple[Response, int] | None:
    return None if g.actor.position is Role.CEO else (jsonify(error="대표이사만 할 수 있습니다."), 403)


@bp.get("/api/kpi/delegation")
def delegation_get() -> Response:
    if (denied := _ceo_only()) is not None:
        return denied
    return jsonify(on=auth._db().execute("SELECT 1 FROM ceo_delegation WHERE ceo_user_id = ?", (g.actor.user_id,)).fetchone() is not None)


@bp.post("/api/kpi/delegation")
def delegation_set() -> Response:
    """{"on": true | false} — 켜면 HR 이 대표이사 몫의 확정을 대신할 수 있다. 이미 그 상태면 아무것도 바꾸지 않고 감사로그도 안 남긴다."""
    if (denied := _ceo_only()) is not None:
        return denied
    on = _body().get("on")
    if not isinstance(on, bool):
        return jsonify(error="맡기려면 true, 거두려면 false 를 보내 주세요."), 400
    conn, me_id = auth._db(), g.actor.user_id
    with _txn(conn):
        if on:
            changed = conn.execute("INSERT OR IGNORE INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", (me_id, auth._iso(auth._now()))).rowcount
        else:
            changed = conn.execute("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", (me_id,)).rowcount
        if changed:
            auth._audit(conn, AuditEntry.by(g.actor, action="CEO_DELEGATION_ON" if on else "CEO_DELEGATION_OFF", entity_type="ceo_delegation", entity_id=me_id))
    return jsonify(ok=True, on=on)
