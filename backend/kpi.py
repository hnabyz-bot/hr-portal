"""KPI 계획서 ↔ 인사포털 (2026-10-20 인사담당자 결정 — 직원이 쓰고 제출, 바로 위 관리자가 확정).

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

  GET  /api/kpi/me                  내 올해 계획서 · 작성 기간 · 바로 위 관리자 — 본인만
  POST /api/kpi/me/draft            {"draft": [...]} 임시 저장 (DRAFT 일 때만 · 계획서가 없으면 만든다). 모양 · 글자 한도만 본다
  POST /api/kpi/me/submit           제출 (DRAFT → SUBMITTED). 계획서 규칙을 전부 통과해야 한다
  POST /api/kpi/me/cancel           제출 취소 (SUBMITTED → DRAFT)
  POST /api/kpi/me/request-change   {"reason": "..."} 확정 뒤 수정 요청 (CONFIRMED → CHANGE_REQUESTED)
  GET  /api/kpi/admin/year          ?year= 그해 작성 기간 (HR)
  POST /api/kpi/admin/year          {"year", "editFrom", "editTo", "evalStart"} 작성 기간 정하기 · 닫기 (HR)

본인 쓰기는 서버가 늘 같은 순서로 다시 본다: KPI 작성 대상 403 → 작성 기간 403 → 상태 409 → 내용 400.
감사로그에는 행동 이름 · 대상 · 해만 남긴다 — 계획서 · 사유 문장은 넣지 않는다. 임시 저장은 남기지 않는다.
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
from hr_eval.domain.kpi_plan import PLAN_MAX_BYTES, REASON_MAX, clean_plan, next_status, validate_plan, window_open
from hr_eval.domain.models import AuditEntry
from hr_eval.domain.org import Dept, Person, manager_of

bp = Blueprint("kpi", __name__)

YEARS = range(2026, 2101)  # kpi_years 의 CHECK 와 같게


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
    uid: int
    year: int
    draft: str | None  # 저장된 초안(JSON 글자). 계획서가 없으면 None
    next: str          # 이 행동을 하면 가는 상태
    now: str


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
        try:
            nxt = next_status("DRAFT" if action == "save" and row is None else status, action)  # 첫 임시 저장이 계획서를 만든다
        except StateConflictError:
            raise _Refuse("지금 상태에서는 할 수 없습니다.", 409, status=status) from None
        yield _Mine(conn, uid, year, row[1] if row else None, nxt, auth._iso(auth._now()))


def _log(m: _Mine, action: str) -> None:
    auth._audit(m.conn, AuditEntry.by(g.actor, action=action, entity_type="kpi_plan", entity_id=m.uid, after_data={"year": m.year}))


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


@bp.get("/api/kpi/me")
def me() -> Response:
    """{"year", "writesKpi", "window": {"from", "to", "evalStart", "open"}, "plan": {…} | null, "manager": {"id", "name"} | null}.
    window 의 from · to 는 지금 적용되는 기간 — 그 사람만의 기간이 있으면 그것. KPI 작성이 꺼진 사람도 읽기는 된다 (writesKpi false)."""
    conn, uid, year = auth._db(), g.actor.user_id, plan_year()
    year_row, person_row = window_rows(conn, uid, year)
    start, end = person_row or (year_row[:2] if year_row else (None, None))
    people, depts = load_org(conn)
    boss = manager_of(uid, people, depts)
    name = conn.execute("SELECT name FROM users WHERE id = ?", (boss,)).fetchone()[0] if boss is not None else None
    return jsonify(
        year=year,
        writesKpi=bool(conn.execute("SELECT writes_kpi FROM users WHERE id = ?", (uid,)).fetchone()[0]),
        window={"from": start, "to": end, "evalStart": year_row[2] if year_row else None,
                "open": window_open(auth._today(), year_row, person_row)},
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
    """{"draft": [...]} → {"ok": true}. 초안은 보낸 그대로(rid · sid 포함) 둔다 — 번호는 확정할 때 붙는다. 감사로그는 남기지 않는다."""
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
        reason = reason.strip() if isinstance(reason, str) else ""
        if not 0 < len(reason) <= REASON_MAX:
            raise _Refuse(f"수정을 요청하는 이유를 {REASON_MAX}자 안으로 써 주세요.", 400)
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


@bp.get("/api/kpi/admin/year")
def year_get() -> Response:
    """?year= (없으면 올해) → {"year", "editFrom", "editTo", "evalStart"} — 정하지 않았으면 null."""
    if (denied := auth._hr_only()) is not None:
        return denied
    raw = request.args.get("year")
    year = plan_year() if raw is None else int(raw) if re.fullmatch(r"[0-9]{4}", raw) else 0
    if year not in YEARS:
        return jsonify(error="해가 올바르지 않습니다."), 400
    return jsonify(_year_json(auth._db(), year))


@bp.post("/api/kpi/admin/year")
def year_set() -> Response:
    """{"year", "editFrom", "editTo", "evalStart"} — 날짜는 YYYY-MM-DD. editFrom · editTo 는 둘 다 쓰거나 둘 다 비운다(= 기간 닫기).
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
