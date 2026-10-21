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
  POST /api/kpi/plans/<uid>/unlock  수정 요청 풀어 주기 (CHANGE_REQUESTED → DRAFT, 초안 = 마지막 확정본 + 지금 실제 기한, 기다리던 일정 변경 요청은 취소)
  POST /api/kpi/plans/<uid>/reject  {"note"} 수정 요청 거절 (CHANGE_REQUESTED → CONFIRMED)
  GET  /api/kpi/inbox               내가 처리할 것 {"items": […], "team": {상태: 수}} — 계획서(kind PLAN) · 일정 변경(kind SCHED), 오래된 것부터. HR 은 관리자가 없는 것 · 맡겨진 것도 proxy 로
  GET  /api/kpi/admin               ?year= 사람마다 상태 · 바로 위 관리자 · 사람별 기간 (HR)
  POST /api/kpi/admin/window        {"userId", "year", "editFrom", "editTo"} | {"userId", "year", "clear": true} 한 사람만의 작성 기간 (HR)
  GET/POST /api/kpi/delegation      {"on": bool} 대표이사 몫을 HR 에 맡기기 — 대표이사 본인만

  GET  /api/kpi/progress/<uid>      ?year= 그 사람 그해 진행 기록 + 계산한 숫자 — 주인 · 바로 위 관리자 · HR 만. 그해 확정본이 없으면 404
  POST /api/kpi/me/progress         {"year", "rid", "actual"?, "pct"?, "why"?} 실적 · 진행률 · 늦어지는 이유 (보낸 칸만 바꾼다)
  POST /api/kpi/me/month            {"year", "rid", "month", "text", "link"} 정기 실행목표의 월 한 줄 (text "" 는 지움)
  POST /api/kpi/me/result           {"year", "sid", "text", "link"} 비정기 일정의 결과 한 줄 (text "" 는 지움)

  POST /api/kpi/me/sched-change     {"year", "sid", "due", "reason"} 비정기 일정 하나의 기한을 바꿔 달라는 요청 → {"ok", "id"} (확정본이 있고 평가 시작일 전일 때)
  POST /api/kpi/me/sched-change/<id>/cancel  본인의 기다리는 요청을 취소
  POST /api/kpi/sched-changes/<id>/confirm   바로 위 관리자(또는 대신하는 HR)가 확인 — 그 일정의 실제 기한이 바뀐다 (확정본은 그대로)
  POST /api/kpi/sched-changes/<id>/return    {"note"} 돌려보내기 (이유 필수)

본인 쓰기는 서버가 늘 같은 순서로 다시 본다: KPI 작성 대상 403 → 작성 기간 403 → 상태 409 → 내용 400.
관리자 처리는: 처리 권한 403 → 계획서 주인의 작성 기간 403 → 상태 409 → 사유 400. 권한은 요청마다 명단에서 새로 계산한다 (decider).
진행 기록 쓰기는: KPI 작성 대상 403 → 그해 확정본 404 → 기록 기간(다음 해 1월 7일까지) 403 → 실행목표 · 일정 · 종류 400 → (월 한 줄) 달 403 → 글자 · 링크 · 숫자 한도 400.
작성 기간 · 계획서 상태는 보지 않고 지금 확정본에 쓴다. 감사로그는 남기지 않는다 (내용 문장이라서).
일정 변경 요청은: KPI 작성 대상 403 → 해 400 → 확정본 404 → 계획서 상태 409 → 평가 시작일 403 → 일정 · 결과 · 기한 400 → 같은 일정에 기다리는 요청 409 → 이유 400.
확인 · 돌려보내기는: 요청 번호 404 → 처리 권한 403 → 평가 시작일 403 → 기다리는 중이 아니면 409 → (돌려보내기) 이유 400. 작성 기간과는 상관없다.
감사로그에는 행동 이름 · 대상 · 해(· 판 · 일정과 옛 · 새 기한)만 남긴다 — 계획서 · 사유 문장은 넣지 않는다. 임시 저장은 남기지 않는다. HR 이 대신 하면 on_behalf_of.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Iterator, NamedTuple

from flask import Blueprint, Response, g, jsonify, request

import auth
from hr_eval.domain.errors import StateConflictError
from hr_eval.domain.kpi_plan import (
    PLAN_MAX_BYTES, REASON_MAX, assign_ids, clean_plan, next_status, validate_plan, window_open, window_span,
)
from hr_eval.domain.kpi_progress import (
    LINK_MAX, NOTE_MAX, WHY_MAX, effective_dues, item_expected, item_pct, month_state, overall, records_open, sched_state, task_title, valid_link,
)
from hr_eval.domain.models import Actor, AuditEntry, Role
from hr_eval.domain.org import Dept, Person, manager_of

bp = Blueprint("kpi", __name__)

YEARS = range(2026, 2101)  # kpi_years 의 CHECK 와 같게
STATES = ("NONE", "DRAFT", "SUBMITTED", "CONFIRMED", "CHANGE_REQUESTED")  # NONE = 계획서 줄이 없음
UID = "<int(max=2147483647):uid>"  # 경로의 사람 번호 — SQLite 정수보다 큰 수는 묶다가 서버 오류가 나므로 길 단계에서 404
CID = "<int(max=2147483647):cid>"  # 경로의 일정 변경 요청 번호 — 같은 이유


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


def _chars(t: str, label: str) -> str:
    """사람이 쓴 글의 글자 규칙 (한 곳): UTF-8 로 쓸 수 있어야 하고(짝 없는 서로게이트 없음) 제어문자가 없어야 한다 — 아니면 400.
    줄바꿈 · 탭 · 캐리지리턴은 글 상자에서 Enter 로 들어오므로 둔다. 특히 NUL 은 SQLite length() 가 거기서 멈춰 CHECK 가 서버 오류로 터진다."""
    try:
        t.encode("utf-8")
    except UnicodeEncodeError:  # 짝 없는 서로게이트 — SQLite 에 묶다가 서버 오류가 나기 전에
        raise _Refuse(f"{label}에 쓸 수 없는 글자가 있습니다.", 400) from None
    if any(ord(ch) == 127 or (ord(ch) < 32 and ch not in "\t\n\r") for ch in t):
        raise _Refuse(f"{label}에 쓸 수 없는 글자가 있습니다.", 400)
    return t


def _text(v: object, label: str) -> str:
    """사유 글 (돌려보내기 · 거절 · 수정 요청): 글자만, 앞뒤 공백을 뺀 1~REASON_MAX자, _chars 규칙 — 아니면 400.
    label 은 문구용 ("…이유")."""
    t = _chars(v.strip() if isinstance(v, str) else "", label)
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
    """수정 요청을 풀어 준다 (CHANGE_REQUESTED → DRAFT). 초안 = 마지막 확정본 내용에 지금 실제 기한(확인된 일정 변경)을 넣은 것 — 다음 확정본이 이어받는다.
    확정본은 그대로 유효하다. 기다리던 일정 변경 요청은 모두 취소한다 (새 판에서 일정이 바뀔 수 있다) — 감사로그에 취소한 수(cancelled)."""
    with _manage(uid, "unlock") as m:
        dues, content = _dues(m.conn, uid, m.year), _versions(m.conn, uid, m.year)[-1][1]
        for it in _rows(content):
            for s in it.get("scheds") or []:
                s["due"] = dues.get(s["sid"], s["due"])
        closed = m.conn.execute("UPDATE kpi_sched_changes SET status = 'CANCELLED', decided_at = ?, decided_by = ?, on_behalf_of = ? WHERE user_id = ? AND year = ? AND status = 'PENDING'",
                                (m.now, g.actor.user_id, m.behalf, uid, m.year)).rowcount
        m.conn.execute("UPDATE kpi_plans SET status = ?, draft = ?, note = NULL, note_kind = NULL, change_reason = NULL, updated_at = ?"
                       " WHERE user_id = ? AND year = ?", (m.next, json.dumps(content, ensure_ascii=False), m.now, uid, m.year))
        _log(m, "KPI_UNLOCKED", cancelled=closed)
    return jsonify(ok=True)


def _can_view(conn: sqlite3.Connection, uid: int) -> bool:
    """이 사람의 KPI(계획서 · 진행 기록)를 볼 수 있나: HR · 본인 · 바로 위 관리자. 보는 범위를 넓힐 때는 여기 한 곳만 고친다."""
    return g.actor.is_hr or uid == g.actor.user_id or manager_of(uid, *load_org(conn)) == g.actor.user_id


@bp.get(f"/api/kpi/plans/{UID}")
def plan_get(uid: int) -> Response:
    """주인 · 바로 위 관리자 · HR 만. GET /api/kpi/me 의 plan 과 같은 모양 + {"owner": {"id", "name", "dept"}}. 계획서가 없으면 404."""
    conn = auth._db()
    if not _can_view(conn, uid):
        return jsonify(error="이 KPI를 볼 권한이 없습니다."), 403
    plan = plan_json(conn, uid, plan_year())
    if plan is None:
        return jsonify(error="아직 작성한 KPI가 없습니다."), 404
    owner = conn.execute("SELECT u.id, u.name, d.name FROM users u JOIN departments d ON d.id = u.department_id WHERE u.id = ?", (uid,)).fetchone()
    return jsonify(**plan, owner={"id": owner[0], "name": owner[1], "dept": owner[2]})


@bp.get("/api/kpi/inbox")
def inbox() -> Response:
    """{"items": [내가 처리할 것], "team": {상태: 수}}. items = 오래된 것부터 두 가지: kind "PLAN" = 내가 바로 위 관리자인 제출 · 수정 요청 계획서,
    kind "SCHED" = 기다리는 일정 변경 요청 (그해 평가 시작일이 지났으면 뺀다). 일정 변경 줄은 id · 실행목표(action) · 일정 이름(sched) · 옛 → 새 기한 · 이유가 있다.
    HR 이면 관리자가 없거나 대표이사가 맡긴 것도 proxy: true 로 (HR 본인 것은 빼고). 주인이 활성이고 KPI 작성이 켜진 것만 —
    team · HR 목록과 같은 기준. team = 내가 바로 위 관리자인 KPI 작성자들의 상태 수 (HR 대신 몫은 안 센다 · 일정 변경은 안 센다)."""
    conn, year, mine = auth._db(), plan_year(), g.actor.user_id
    people, depts = load_org(conn)
    ceos = _ceos(conn, people)

    def proxy(uid: int) -> bool | None:
        """이 사람의 것이 내 처리함에 오나: False = 내가 바로 위 관리자, True = HR 이 대신 처리할 것, None = 내 몫이 아님."""
        boss = manager_of(uid, people, depts)
        if boss == mine:
            return False
        return True if g.actor.is_hr and uid != mine and (boss is None or ceos.get(boss, False)) else None

    items = []
    for uid, name, dept, title, status, submitted, updated, reason, draft in conn.execute(
            "SELECT p.user_id, u.name, d.name, COALESCE(u.title, ''), p.status, p.submitted_at, p.updated_at, p.change_reason, p.draft"
            " FROM kpi_plans p JOIN users u ON u.id = p.user_id JOIN departments d ON d.id = u.department_id"
            " WHERE p.year = ? AND p.status IN ('SUBMITTED', 'CHANGE_REQUESTED') AND u.is_active = 1 AND u.writes_kpi = 1", (year,)):
        px = proxy(uid)
        if px is None:
            continue
        rows = [it for goal in json.loads(draft) for it in goal["items"]]
        items.append({"kind": "PLAN", "userId": uid, "name": name, "dept": dept, "title": title, "status": status,
                      "at": submitted if status == "SUBMITTED" else updated, "reason": reason if status == "CHANGE_REQUESTED" else None,
                      "items": len(rows), "reg": sum(it["kind"] == "reg" for it in rows), "irr": sum(it["kind"] == "irr" for it in rows),
                      "scheds": sum(len(it["scheds"]) for it in rows), "proxy": px})
    waiting = conn.execute(
        "SELECT c.id, c.user_id, u.name, d.name, COALESCE(u.title, ''), c.sid, c.from_due, c.to_due, c.reason, c.requested_at"
        " FROM kpi_sched_changes c JOIN users u ON u.id = c.user_id JOIN departments d ON d.id = u.department_id"
        " WHERE c.year = ? AND c.status = 'PENDING' AND u.is_active = 1 AND u.writes_kpi = 1", (year,)).fetchall() if _sched_open(conn, year) else []
    for cid, uid, name, dept, title, sid, frm, to, reason, at in waiting:
        px = proxy(uid)
        if px is None:
            continue
        last = conn.execute("SELECT content FROM kpi_versions WHERE user_id = ? AND year = ? ORDER BY version DESC LIMIT 1", (uid, year)).fetchone()
        names = {s["sid"]: (it["action"], s["name"]) for it in _rows(json.loads(last[0])) for s in it["scheds"]} if last else {}
        action, sched = names.get(sid, ("", ""))
        items.append({"kind": "SCHED", "id": cid, "userId": uid, "name": name, "dept": dept, "title": title, "action": action, "sched": sched,
                      "from": frm, "to": to, "reason": reason, "at": at, "proxy": px})
    items.sort(key=lambda i: (i["at"], i["userId"], i["kind"], i.get("id", 0)))
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


# ---------------- 진행 기록 (2단계 ①, 2026-10-21) ----------------
def _rows(content: list[dict]) -> list[dict]:
    """확정본(목표 묶음의 목록)의 실행목표 줄을 차례로."""
    return [it for goal in content for it in goal["items"]]


def _versions(conn: sqlite3.Connection, uid: int, year: int) -> list[tuple[int, list[dict], str]]:
    """(판, 내용, 확정 시각) — 오래된 판부터. 확정본이 없으면 빈 목록."""
    return [(v, json.loads(c), at) for v, c, at in conn.execute(
        "SELECT version, content, confirmed_at FROM kpi_versions WHERE user_id = ? AND year = ? ORDER BY version", (uid, year))]


def _first_months(year: int, versions: list[tuple[int, list[dict], str]]) -> dict[int, int]:
    """실행목표(rid)마다 그 줄이 처음 들어간 판이 확정된 한국 달 — 월 한 줄은 그 달부터 해당 있다. 다른 해에 확정했으면 1월부터."""
    first: dict[int, int] = {}
    for _, content, at in versions:
        t = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(auth.KST)  # 확정 시각은 UTC — 1일 새벽에 달이 어긋나지 않게
        for it in _rows(content):
            first.setdefault(it["rid"], t.month if t.year == year else 1)
    return first


def _hire(conn: sqlite3.Connection, uid: int) -> date | None:
    h = conn.execute("SELECT hire_date FROM users WHERE id = ?", (uid,)).fetchone()[0]
    try:
        return date.fromisoformat(h) if h else None
    except ValueError:  # 손으로 넣은 엉터리 입사일은 없는 것으로
        return None


def _dues(conn: sqlite3.Connection, uid: int, year: int) -> dict[int, str]:
    """일정마다 실제 기한 {sid: due}: 지금(마지막) 확정본의 기한에, 그 판이 확정된 뒤 확인된 일정 변경을 확인한 시각이 오래된 것부터 덮는다.
    (그 전에 확인된 변경은 새 판의 기한에 이미 들어 있다.) 확정본이 없으면 {}."""
    versions = _versions(conn, uid, year)
    if not versions:
        return {}
    changes = conn.execute(
        "SELECT sid, to_due FROM kpi_sched_changes WHERE user_id = ? AND year = ? AND status = 'CONFIRMED' AND decided_at >= ?"
        " ORDER BY decided_at, id", (uid, year, versions[-1][2])).fetchall()
    return effective_dues(versions[-1][1], changes)


def progress_json(conn: sqlite3.Connection, uid: int, year: int) -> dict | None:
    """그 사람 그해 진행 기록을 지금 확정본의 실행목표 줄 모양으로, 계산한 숫자까지 붙여. 확정본이 없으면 None.
    날짜 판정은 한국 날짜(auth._today). 호출하는 쪽이 한 트랜잭션 안에서 부른다 (표 여럿을 한 번에 읽으므로).
    items 마다 rid · kind · action · weight · target · unit · actual · pct · why · at · value(막대 0~100) · expected(기대 진행률) 와
    정기는 months {"1"…"12": {state, text, link, at}}, 비정기는 scheds [{sid, name, task, due, origDue, state, result, pending, returned}]
    (returned = 가장 늦게 요청한 것이 돌려보내진 경우, 지금 확정본이 확정된 뒤에 돌려보낸 것만 — 수정 → 다시 확정한 뒤에는 옛 줄을 안 보인다).
    total · totalExpected = 가중 평균, todo = 지금 할 일 수, next = 결과가 없고 실제 기한이 오늘 이후인 가장 가까운 일정."""
    versions = _versions(conn, uid, year)
    if not versions:
        return None
    today, (version, content, confirmed_at) = auth._today(), versions[-1]
    first, dues, hire = _first_months(year, versions), _dues(conn, uid, year), _hire(conn, uid)
    own = (uid, year)
    prog = {r[0]: r[1:] for r in conn.execute("SELECT rid, actual, pct, why, updated_at FROM kpi_progress WHERE user_id = ? AND year = ?", own)}
    notes = {(r[0], r[1]): r[2:] for r in conn.execute("SELECT rid, month, text, link, updated_at FROM kpi_month_notes WHERE user_id = ? AND year = ?", own)}
    results = {r[0]: r[1:] for r in conn.execute("SELECT sid, text, link, updated_at FROM kpi_results WHERE user_id = ? AND year = ?", own)}
    changes = conn.execute("SELECT id, sid, to_due, reason, status, note, requested_at, decided_at FROM kpi_sched_changes"
                           " WHERE user_id = ? AND year = ? ORDER BY requested_at, id", own).fetchall()
    waiting = {c[1]: c for c in changes if c[4] == "PENDING"}
    newest = {c[1]: c for c in changes}  # 요청한 시각이 가장 늦은 것이 마지막에 덮는다. 돌려받은 줄은 지금 확정본이 확정된 뒤에 돌려보낸 것만 (일정이 바뀔 수 있는 새 판 앞의 것은 숨는다)
    this_m = today.month if today.year == year else None  # 할 일: 이번 달, 그리고 1~7일이면 지난달 — 그해 달만
    last = today.replace(day=1) - timedelta(days=1)
    prev_m = last.month if today.day <= 7 and last.year == year else None

    rows, items, values, expects, nxt = _rows(content), [], [], [], None
    cur = prev = late = 0
    for it in rows:
        rid = it["rid"]
        actual, pct, why, at = prog.get(rid, (None, None, None, None))
        out = {"rid": rid, "kind": it["kind"], "action": it["action"], "weight": it["weight"], "target": it["target"], "unit": it["unit"],
               "actual": actual, "pct": pct, "why": why, "at": at}
        done: set[int] = set()
        if it["kind"] == "reg":
            out["months"] = {}
            for m in range(1, 13):
                text, link, mat = notes.get((rid, m), (None, None, None))
                out["months"][str(m)] = {"state": month_state(year, m, today, first[rid], hire, text is not None), "text": text, "link": link, "at": mat}
            cur += this_m is not None and out["months"][str(this_m)]["state"] == "OPEN"
            prev += prev_m is not None and out["months"][str(prev_m)]["state"] == "OPEN"
        else:
            out["scheds"] = []
            for s in it["scheds"]:
                sid, res, due = s["sid"], results.get(s["sid"]), dues[s["sid"]]
                state, c, lc = sched_state(due, today, res is not None), waiting.get(sid), newest.get(sid)
                if res is not None:
                    done.add(sid)
                late += state in ("LATE", "OVERDUE")
                if res is None and due >= today.isoformat() and (nxt is None or (due, sid) < (nxt["due"], nxt["sid"])):
                    nxt = {"sid": sid, "due": due, "name": s["name"]}
                out["scheds"].append({
                    "sid": sid, "name": s["name"], "task": task_title(s["name"], it["action"]), "due": due, "origDue": s["due"], "state": state,
                    "result": {"text": res[0], "link": res[1], "at": res[2]} if res else None,
                    "pending": {"id": c[0], "to": c[2], "reason": c[3], "at": c[6]} if c else None,
                    "returned": {"to": lc[2], "note": lc[5], "at": lc[7]} if lc and lc[4] == "RETURNED" and lc[7] >= confirmed_at else None})
        out["value"], out["expected"] = item_pct(it, actual, pct, done), item_expected(it, year, today, dues)
        values.append(out["value"])
        expects.append(out["expected"])
        items.append(out)
    return {"year": year, "version": version, "open": records_open(year, today), "schedOpen": _sched_open(conn, year),
            "items": items, "total": overall(rows, values), "totalExpected": overall(rows, expects),
            "todo": {"months": cur + prev, "prev": prev, "results": late}, "next": nxt}


@bp.get(f"/api/kpi/progress/{UID}")
def progress_get(uid: int) -> Response:
    """?year= (없으면 올해) → progress_json. 주인 · 바로 위 관리자 · HR 만 (plan_get 과 같은 범위). 그해 확정본이 없으면 404."""
    conn = auth._db()
    if not _can_view(conn, uid):
        return jsonify(error="이 KPI를 볼 권한이 없습니다."), 403
    year = _year_arg()
    with _txn(conn):  # 표 여럿을 읽는 동안 다른 요청이 끼어들지 않게
        out = progress_json(conn, uid, year)
    if out is None:
        return jsonify(error="확정된 KPI가 없습니다."), 404
    return jsonify(out)


class _Rec(NamedTuple):
    conn: sqlite3.Connection
    uid: int  # 쓰는 사람 = 로그인한 사람 (본문의 번호는 보지 않는다)
    year: int
    versions: list[tuple[int, list[dict], str]]
    now: str

    @property
    def items(self) -> list[dict]:
        """지금(마지막) 확정본의 실행목표 줄 — 기록은 여기 있는 rid · sid 에만 쓴다."""
        return _rows(self.versions[-1][1])


@contextmanager
def _record() -> Iterator[_Rec]:
    """내 진행 기록을 쓰는 요청의 틀. 본문 앞에서 KPI 작성 대상 403 → (해가 이상하면 400) → 그해 확정본 404 → 기록 기간 403 을 순서대로 본다.
    작성 기간 · 계획서 상태는 보지 않는다 — 수정 중이어도 기록은 늘 지금 확정본에 쓴다. 감사로그는 남기지 않는다 (내용 문장이라서)."""
    conn, uid, year = auth._db(), g.actor.user_id, _body().get("year")
    with _txn(conn):
        if not conn.execute("SELECT writes_kpi FROM users WHERE id = ?", (uid,)).fetchone()[0]:
            raise _Refuse("KPI 작성 대상이 아닙니다.", 403)
        if type(year) is not int or year not in YEARS:
            raise _Refuse("해가 올바르지 않습니다.", 400)
        versions = _versions(conn, uid, year)
        if not versions:
            raise _Refuse("확정된 KPI가 없습니다.", 404)
        if not records_open(year, auth._today()):
            raise _Refuse("기록 기간이 지났습니다.", 403)
        yield _Rec(conn, uid, year, versions, auth._iso(auth._now()))


def _item(m: _Rec, rid: object) -> dict:
    it = next((i for i in m.items if type(rid) is int and i["rid"] == rid), None)  # type 검사: True 가 1번으로 통하지 않게
    if it is None:
        raise _Refuse("지금 확정본에 없는 실행목표입니다.", 400)
    return it


def _line(v: object, label: str, limit: int) -> str:
    """진행 기록의 글 한 줄: 글자만, 앞뒤 공백을 뺀 limit자 이하 ("" 가능), _chars 규칙. 글자가 아니거나 쓸 수 없는 글자가 있거나 한도를 넘으면 400."""
    if not isinstance(v, str):
        raise _Refuse(f"{label}: 글자로 보내 주세요.", 400)
    t = _chars(v.strip(), label)
    if len(t) > limit:
        raise _Refuse(f"{label}: {limit}자 안으로 써 주세요.", 400)
    return t


def _link(v: object) -> str | None:
    """근거 링크: 없거나 빈칸이면 None, 아니면 앞뒤 빈칸을 뺀 글이 valid_link 를 지나야 한다 (아니면 400)."""
    if v is None:
        return None
    s = _line(v, "링크", LINK_MAX)
    if s and not valid_link(s):
        raise _Refuse("링크는 http:// 또는 https:// 로 시작하는 주소만, 빈칸 없이 써 주세요.", 400)
    return s or None


@bp.post("/api/kpi/me/progress")
def progress_set() -> Response:
    """{"year", "rid", "actual"?, "pct"?, "why"?} 보낸 칸만 바꾼다. actual(0~10억 숫자)은 목표 숫자가 있는 정기에만, pct(0~100 정수)는 목표 숫자가 없는
    정기에만, why(늦어지는 이유 100자)는 모든 행에 — 다른 데 보내면 400. actual · pct 는 null, why 는 "" 로 보내면 그 칸을 비운다."""
    b = _body()
    with _record() as m:
        it = _item(m, b.get("rid"))
        reg, has_target = it["kind"] == "reg", it["target"] is not None
        if "actual" in b and not (reg and has_target):
            raise _Refuse("실적은 목표 숫자가 있는 정기 업무에만 적을 수 있습니다.", 400)
        if "pct" in b and not (reg and not has_target):
            raise _Refuse("진행률은 목표 숫자가 없는 정기 업무에만 직접 적을 수 있습니다.", 400)
        sets: dict[str, object] = {}
        if "actual" in b:
            a = b["actual"]  # null 은 비우기 (화면의 숫자 칸을 지웠을 때)
            if a is not None and not (isinstance(a, (int, float)) and not isinstance(a, bool) and 0 <= a <= 1e9):  # NaN · 무한도 여기서 걸린다
                raise _Refuse("실적은 0 이상 10억 이하 숫자로 적어 주세요.", 400)
            sets["actual"] = a
        if "pct" in b:
            pct = b["pct"]
            if pct is not None and (type(pct) is not int or not 0 <= pct <= 100):
                raise _Refuse("진행률은 0~100 사이 정수로 적어 주세요.", 400)
            sets["pct"] = pct
        if "why" in b:
            sets["why"] = _line(b["why"], "늦어지는 이유", WHY_MAX) or None
        if not sets:
            raise _Refuse("바꿀 칸(actual · pct · why)을 보내 주세요.", 400)
        cols = list(sets)  # 위에서 정한 고정 이름뿐 — 요청의 글자가 SQL 에 들어가지 않는다
        m.conn.execute(
            f"INSERT INTO kpi_progress (user_id, year, rid, {', '.join(cols)}, updated_at) VALUES (?, ?, ?, {', '.join('?' * len(cols))}, ?)"
            f" ON CONFLICT (user_id, year, rid) DO UPDATE SET {', '.join(c + ' = excluded.' + c for c in cols)}, updated_at = excluded.updated_at",
            (m.uid, m.year, it["rid"], *sets.values(), m.now))
        m.conn.execute("DELETE FROM kpi_progress WHERE user_id = ? AND year = ? AND rid = ? AND actual IS NULL AND pct IS NULL AND why IS NULL",
                       (m.uid, m.year, it["rid"]))  # 비워서 아무것도 안 남은 줄은 두지 않는다
    return jsonify(ok=True)


@bp.post("/api/kpi/me/month")
def month_set() -> Response:
    """{"year", "rid", "month", "text", "link"} 정기 실행목표의 그 달 한 줄(200자) · 근거 링크(선택, 안 보내면 지워진다). text "" 는 그 달 줄을 지운다.
    쓸 수 있는 달: 그 달 1일부터 다음 달 7일까지, 그리고 이 실행목표가 해당 있는 달 (첫 확정 전 · 입사 전 달은 403)."""
    b = _body()
    with _record() as m:
        it = _item(m, b.get("rid"))
        if it["kind"] != "reg":
            raise _Refuse("월 한 줄은 정기 업무에만 쓸 수 있습니다.", 400)
        month = b.get("month")
        if type(month) is not int or not 1 <= month <= 12:
            raise _Refuse("달은 1~12 사이 숫자로 보내 주세요.", 400)
        if month_state(m.year, month, auth._today(), _first_months(m.year, m.versions)[it["rid"]], _hire(m.conn, m.uid), False) != "OPEN":
            raise _Refuse("이 달은 적는 기간이 아닙니다.", 403)
        text, link = _line(b.get("text"), "월 한 줄", NOTE_MAX), _link(b.get("link"))
        key = (m.uid, m.year, it["rid"], month)
        if text:
            m.conn.execute("INSERT OR REPLACE INTO kpi_month_notes (user_id, year, rid, month, text, link, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (*key, text, link, m.now))
        else:
            m.conn.execute("DELETE FROM kpi_month_notes WHERE user_id = ? AND year = ? AND rid = ? AND month = ?", key)
    return jsonify(ok=True)


@bp.post("/api/kpi/me/result")
def result_set() -> Response:
    """{"year", "sid", "text", "link"} 지금 확정본의 비정기 일정 하나의 결과 한 줄(200자) · 근거 링크(선택). text "" 는 지운다 (일정이 다시 예정)."""
    b = _body()
    with _record() as m:
        sid = b.get("sid")
        if not any(type(sid) is int and s["sid"] == sid for it in m.items for s in it["scheds"]):
            raise _Refuse("지금 확정본에 없는 일정입니다.", 400)
        text, link = _line(b.get("text"), "결과 한 줄", NOTE_MAX), _link(b.get("link"))
        if text:
            m.conn.execute("INSERT OR REPLACE INTO kpi_results (user_id, year, sid, text, link, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (m.uid, m.year, sid, text, link, m.now))
        else:
            m.conn.execute("DELETE FROM kpi_results WHERE user_id = ? AND year = ? AND sid = ?", (m.uid, m.year, sid))
    return jsonify(ok=True)


# ---------------- 일정 변경 요청 (2단계 ①, 2026-10-21) ----------------
def _sched_open(conn: sqlite3.Connection, year: int) -> bool:
    """일정 변경은 그해 평가 시작일부터 막힌다 (한국 날짜). 평가 시작일을 정하지 않았으면 늘 열려 있다."""
    ev = conn.execute("SELECT eval_start FROM kpi_years WHERE year = ?", (year,)).fetchone()
    return not (ev and ev[0]) or auth._today().isoformat() < ev[0]


STARTED = "평가가 시작되어 일정을 바꿀 수 없습니다."


class _Change(NamedTuple):
    id: int
    uid: int  # 일정의 주인
    year: int
    sid: int
    frm: str
    to: str
    status: str


def _change(conn: sqlite3.Connection, cid: int) -> _Change:
    row = conn.execute("SELECT id, user_id, year, sid, from_due, to_due, status FROM kpi_sched_changes WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise _Refuse("일정 변경 요청을 찾을 수 없습니다.", 404)
    return _Change(*row)


def _waiting(ch: _Change) -> None:
    """기다리는 중(PENDING)이 아니면 409 — 지금 상태를 알려 준다."""
    if ch.status != "PENDING":
        raise _Refuse("지금 상태에서는 할 수 없습니다.", 409, status=ch.status)


def _sched_log(conn: sqlite3.Connection, uid: int, year: int, sid: int, action: str, frm: str, to: str, behalf: int | None = None) -> None:
    """감사로그: 행동 · 일정 주인 · 해 · 일정 · 옛 기한 · 새 기한만 — 이유 · 돌려보내는 이유 문장은 넣지 않는다. HR 이 대신 했으면 on_behalf_of = 원래 관리자."""
    auth._audit(conn, AuditEntry.by(g.actor, on_behalf_of=behalf, action=action, entity_type="kpi_plan", entity_id=uid,
                                    after_data={"year": year, "sid": sid, "from": frm, "to": to}))


def _close(conn: sqlite3.Connection, ch: _Change, status: str, action: str, note: str | None = None, behalf: int | None = None) -> None:
    """기다리는 요청을 닫는다 (확인 · 돌려보내기 · 본인 취소). 닫는 모든 길이 결정한 시각과 사람을 채운다 (표의 CHECK: PENDING 일 때만 비어 있다)."""
    conn.execute("UPDATE kpi_sched_changes SET status = ?, note = ?, decided_at = ?, decided_by = ?, on_behalf_of = ? WHERE id = ?",
                 (status, note, auth._iso(auth._now()), g.actor.user_id, behalf, ch.id))
    _sched_log(conn, ch.uid, ch.year, ch.sid, action, ch.frm, ch.to, behalf)


@bp.post("/api/kpi/me/sched-change")
def sched_request() -> Response:
    """{"year", "sid", "due", "reason"} → {"ok": true, "id"}. 지금 확정본의 비정기 일정 하나의 기한을 바꿔 달라는 요청.
    서버가 보는 순서: KPI 작성 대상 403 → 해 400 (2026~2100 정수가 아니거나 올해가 아님) → 확정본 404 → 계획서 상태 409 (고치는 중) → 평가 시작일 403 → 일정이 확정본의 비정기 일정이 아님 400 →
    결과를 이미 씀 400 → 기한이 그해 안의 실제 날짜가 아니거나 지금 실제 기한과 같음 400 → 같은 일정에 기다리는 요청이 있음 409 → 이유(1~200자) 400.
    확인과 쓰기는 한 트랜잭션이라 빠르게 여러 번 눌러도 한 번만 통과한다."""
    b = _body()
    conn, uid, year, sid = auth._db(), g.actor.user_id, b.get("year"), b.get("sid")
    with _txn(conn):
        if not conn.execute("SELECT writes_kpi FROM users WHERE id = ?", (uid,)).fetchone()[0]:
            raise _Refuse("KPI 작성 대상이 아닙니다.", 403)
        if type(year) is not int or year not in YEARS:
            raise _Refuse("해가 올바르지 않습니다.", 400)
        if year != plan_year():  # 처리함에는 올해 요청만 온다 — 지난해 것을 받으면 아무도 못 보는 요청이 쌓인다
            raise _Refuse("올해 계획서의 일정만 바꿀 수 있습니다.", 400)
        versions, plan = _versions(conn, uid, year), conn.execute("SELECT status FROM kpi_plans WHERE user_id = ? AND year = ?", (uid, year)).fetchone()
        if not versions or plan is None:
            raise _Refuse("확정된 KPI가 없습니다.", 404)
        if plan[0] not in ("CONFIRMED", "CHANGE_REQUESTED"):
            raise _Refuse("지금은 계획서를 고치는 중이라 일정 변경을 요청할 수 없습니다.", 409, status=plan[0])
        if not _sched_open(conn, year):
            raise _Refuse(STARTED, 403)
        if not any(type(sid) is int and s["sid"] == sid for it in _rows(versions[-1][1]) if it["kind"] == "irr" for s in it["scheds"]):  # type 검사: True 가 1번으로 통하지 않게
            raise _Refuse("지금 확정본에 없는 일정입니다.", 400)
        if conn.execute("SELECT 1 FROM kpi_results WHERE user_id = ? AND year = ? AND sid = ?", (uid, year, sid)).fetchone():
            raise _Refuse("결과를 이미 쓴 일정은 날짜를 바꿀 수 없습니다.", 400)
        try:
            to = _iso_date(b.get("due"))  # YYYY-MM-DD 로 쓴 실제 날짜 (20270924 · 2-30 · 줄바꿈이 붙은 것은 ValueError)
        except ValueError:
            to = None
        if to is None or to[:4] != str(year):
            raise _Refuse(f"{year}년 안의 날짜(YYYY-MM-DD)를 써 주세요.", 400)
        frm = _dues(conn, uid, year)[sid]
        if to == frm:
            raise _Refuse("지금 기한과 같은 날짜예요. 다른 날짜를 써 주세요.", 400)
        if conn.execute("SELECT 1 FROM kpi_sched_changes WHERE user_id = ? AND year = ? AND sid = ? AND status = 'PENDING'", (uid, year, sid)).fetchone():
            raise _Refuse("이미 확인을 기다리는 요청이 있습니다.", 409)  # 부분 유니크 인덱스는 마지막 보루 — 여기서 먼저 막아 서버 오류가 아니라 409
        reason = _text(b.get("reason"), "일정을 바꾸려는 이유")
        cid = conn.execute("INSERT INTO kpi_sched_changes (user_id, year, sid, from_due, to_due, reason, status, requested_at) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                           (uid, year, sid, frm, to, reason, auth._iso(auth._now()))).lastrowid
        _sched_log(conn, uid, year, sid, "KPI_SCHED_REQUESTED", frm, to)
    return jsonify(ok=True, id=cid)


@bp.post(f"/api/kpi/me/sched-change/{CID}/cancel")
def sched_cancel(cid: int) -> Response:
    """본인이 기다리는 요청을 취소한다 (PENDING → CANCELLED). 요청 번호 404 → 본인 것이 아니면 403 → 기다리는 중이 아니면 409."""
    conn = auth._db()
    with _txn(conn):
        ch = _change(conn, cid)
        if ch.uid != g.actor.user_id:
            raise _Refuse("본인의 요청만 취소할 수 있습니다.", 403)
        _waiting(ch)
        _close(conn, ch, "CANCELLED", "KPI_SCHED_CANCELLED")
    return jsonify(ok=True)


def _settle(cid: int, status: str, action: str, label: str | None) -> Response:
    """확인 · 돌려보내기 (PENDING → CONFIRMED | RETURNED): 요청 번호 404 → 처리 권한 403 → 평가 시작일 403 → 기다리는 중이 아니면 409 → (돌려보내기) 이유 1~200자 400.
    권한은 계획서 확정 · 돌려보내기와 같은 decider (바로 위 관리자 · HR 대신 · 대표이사 맡김). 작성 기간과는 상관없다. 이유 · 처리한 사람은 요청 줄에 남고 감사로그에는 날짜만."""
    note = _body().get("note")
    conn = auth._db()
    with _txn(conn):
        ch = _change(conn, cid)
        can, behalf = decider(conn, g.actor, ch.uid)
        if not can:
            raise _Refuse("이 요청을 처리할 권한이 없습니다.", 403)
        if not _sched_open(conn, ch.year):
            raise _Refuse(STARTED, 403)
        _waiting(ch)
        _close(conn, ch, status, action, _text(note, label) if label else None, behalf)
    return jsonify(ok=True)


@bp.post(f"/api/kpi/sched-changes/{CID}/confirm")
def sched_confirm(cid: int) -> Response:
    """확인한다 — 그 일정의 실제 기한이 새 기한으로 바뀐다 (확정본 · 계획서 줄은 건드리지 않고 _dues 가 변경 기록에서 읽는다)."""
    return _settle(cid, "CONFIRMED", "KPI_SCHED_CONFIRMED", None)


@bp.post(f"/api/kpi/sched-changes/{CID}/return")
def sched_return(cid: int) -> Response:
    """{"note": "…"} 돌려보낸다 — 기한은 그대로, 이유는 주인 화면(progress 의 returned.note)에 보인다."""
    return _settle(cid, "RETURNED", "KPI_SCHED_RETURNED", "돌려보내는 이유")
