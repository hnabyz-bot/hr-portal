"""하이웍스 SSO 로그인과 세션 검사 (로그인 설계 §3·§4·§5·§7, 4단계).

app.py 가 register(app) 로 붙인다.

  GET  /api/auth/hiworks?token=   하이웍스 "인사포털" 메뉴가 새 탭으로 보내는 곳 → 확인 → 세션 → "/" 로
  GET  /api/auth/message?m=       로그인이 안 됐을 때의 안내. 주소창에서 token 을 지우려고 여기로 보낸다
  POST /api/auth/logout           포털 세션만 끊는다 (하이웍스는 그대로)
  GET  /api/me                    지금 누구인가 (화면용)
  GET  /api/members               구성원 설정 명단 (HR) — 5단계
  POST /api/members               사람별 설정 여섯 칸 저장 (HR, 규칙은 domain/members.validate_members)
  POST /api/members/<id>/active   비활성화 · 다시 켜기 (HR)
  그 밖의 /api/* 전부             세션 검사. 세션 없이 열린 곳은 /api/health 와 위의 두 곳뿐 (§3)

서버 설정 (.env → docker-compose.yml):
  HR_ENV=production                    운영 모드 — docker-compose.yml 에 고정 (§7)
  HIWORKS_FIRST_HR_USER_NO             첫 HR 의 하이웍스 사용자 번호. 활성 HR 이 0명일 때만 쓴다 (§4)
  HIWORKS_SSO_LINK_BY_EMPLOYEE_NO=1    사번으로 첫 연결 (§4 대안) — 6단계 실연결에서 번호 체계가 다를 때만
  HIWORKS_LINK_API_BASE                PC 시험용 가짜 확인 서버. 운영 모드에 있으면 서버가 뜨지 않는다 (§7)

확인표(token)·세션 원본은 저장하지도 로그에 찍지도 않는다 — SHA-256 만 저장한다.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import secrets
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from dataclasses import replace

from flask import Blueprint, Flask, Response, abort, g, jsonify, redirect, request

from hr_eval.domain.errors import ValidationError
from hr_eval.domain.members import (
    MSG_DENIED,
    MSG_NOT_READY,
    Member,
    SsoOutcome,
    SsoToken,
    resolve_sso_user,
    validate_members,
)
from hr_eval.domain.models import Actor, AuditEntry, Role, position_of
from hr_eval.roster_sync import NoRedirect
from hr_eval.sql.db import connect

LINK_API_DEFAULT = "https://link-api.hiworks.com"  # 코드에 고정 (§5 "가짜 확인 서버")
COOKIE = "hr_session"
ENTRY_COOKIE = "hr_entry"  # 하이웍스 메뉴로 막 들어왔다는 1분짜리 표시 — 화면이 읽고 지운다 (§3 "주소로 연 창")
IDLE = timedelta(hours=4)  # 마지막 사용 후 (§10 확정)
MAX_AGE = timedelta(hours=12)  # 발급 후 최대 (§10)
TOKEN_KEEP = timedelta(seconds=60)  # 하이웍스 token 유효시간과 같게 (#65 ③)
RATE_PER_MIN = 30  # 접속자 주소마다 1분 (§5)
TOKEN_RE = re.compile(r"[0-9a-f]{64}")  # 하이웍스 token 모양 (2026-10-12 실측)
OPEN = {"/api/health", "/api/auth/hiworks", "/api/auth/message"}
MSG_LOGIN = "로그인이 필요합니다. 하이웍스에서 인사포털 메뉴로 들어와 주세요."
AGAIN = "하이웍스에서 인사포털 메뉴를 다시 눌러 주세요."
MESSAGES = {  # 안내 문구와 상태 코드
    "bad": (f"잘못된 접근입니다. {AGAIN}", 400),
    "used": (f"이미 사용한 접속입니다. {AGAIN}", 403),
    "check": ("하이웍스 확인에 실패했습니다. 메뉴를 다시 눌러주세요", 502),
    "denied": (MSG_DENIED, 403),  # 미등록·비활성 — 같은 문구
    "ready": (MSG_NOT_READY, 403),  # 시범 사용자 명단 밖
    "busy": ("요청이 많습니다. 잠시 후 다시 시도해 주세요.", 429),
}

_opener = urllib.request.build_opener(NoRedirect)  # 확인 서버가 다른 곳으로 넘기면 따라가지 않는다
bp = Blueprint("auth", __name__)


def refuse_unsafe_config(env: dict | os._Environ = os.environ) -> None:
    """운영 모드에 개발용 우회 주소가 있으면 서버를 띄우지 않는다 — 경고가 아니라 거부 (§7)."""
    if env.get("HR_ENV") == "production" and env.get("HIWORKS_LINK_API_BASE"):
        raise RuntimeError(
            "운영 모드(HR_ENV=production)에는 HIWORKS_LINK_API_BASE 를 둘 수 없습니다 — "
            "본인 확인이 가짜 서버로 넘어가 누구든 아무나로 로그인할 수 있게 됩니다 (로그인 설계 §7)"
        )


def link_api_base() -> str:
    return os.environ.get("HIWORKS_LINK_API_BASE") or LINK_API_DEFAULT


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _db() -> sqlite3.Connection:
    if "db" not in g:
        path = os.environ.get("HR_DB_PATH")
        if not path:
            abort(503)
        g.db = connect(path)
    return g.db


def _client_ip() -> str:
    """실제 접속자 주소. 운영은 Cloudflare → nginx 가 넘긴 CF-Connecting-IP (§5). 없으면(PC) 접속 주소."""
    return (request.headers.get("CF-Connecting-IP") or request.remote_addr or "")[:64]


def _same_origin() -> bool:
    """상태를 바꾸는 요청은 포털 화면에서 온 것만 받는다 (§5 CSRF)."""
    origin = request.headers.get("Origin")
    return bool(origin) and urlparse(origin).hostname == request.host.split(":")[0]


def _audit(conn: sqlite3.Connection, e: AuditEntry) -> None:
    conn.execute(
        "INSERT INTO audit_logs (actor_user_id, actor_position, actor_is_hr, on_behalf_of, action, entity_type,"
        " entity_id, before_data, after_data, reason, ip, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            e.actor_user_id, e.actor_position.value if e.actor_position else None, int(e.actor_is_hr), e.on_behalf_of,
            e.action, e.entity_type, e.entity_id,
            json.dumps(e.before_data, ensure_ascii=False) if e.before_data else None,
            json.dumps(e.after_data, ensure_ascii=False) if e.after_data else None,
            e.reason, e.ip, e.user_agent,
        ),
    )


def _members(conn: sqlite3.Connection) -> list[Member]:
    rows = conn.execute(
        "SELECT u.id, u.name, u.department_id, u.employee_no, u.hiworks_user_no, u.is_active, u.portal_enabled,"
        " u.writes_kpi, u.in_evaluation, u.has_contract, group_concat(r.role)"
        " FROM users u LEFT JOIN user_roles r ON r.user_id = u.id GROUP BY u.id"
    )
    out = []
    for r in rows:
        roles = [Role(x) for x in (r[10] or "").split(",") if x]
        out.append(
            Member(
                r[0], r[1], r[2], r[3], r[4], bool(r[5]), bool(r[6]), position_of(roles), Role.HR_ADMIN in roles,
                bool(r[7]), bool(r[8]), bool(r[9]),
            )
        )
    return out


def actor_of(conn: sqlite3.Connection, user_id: int) -> Actor:
    """세션 → Actor. 팀장·본부장이 "장"인 부서는 직책과 본인 소속으로 정한다 (팀장 = 자기 팀, 본부장 = 자기 본부)."""
    dept = conn.execute("SELECT department_id FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    roles = [Role(r[0]) for r in conn.execute("SELECT role FROM user_roles WHERE user_id = ?", (user_id,))]
    pos = position_of(roles)
    return Actor(
        user_id, pos, Role.HR_ADMIN in roles,
        led_team_id=dept if pos is Role.TEAM_LEADER else None,
        led_division_id=dept if pos is Role.DIVISION_HEAD else None,
    )


def ask_hiworks(token: str) -> SsoToken | None:
    """link-api 에 token 이 누구인지 묻는다. HTTPS 인증서 검증(기본), 5초, 리다이렉트 안 따라감. 실패하면 None."""
    req = urllib.request.Request(f"{link_api_base()}/v4/tokens/{token}", headers={"Accept": "application/json"})
    try:
        with _opener.open(req, timeout=5) as r:
            if r.status != 200:
                return None
            body = json.loads(r.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None  # 이유를 로그에 남기지 않는다 — 예외 문구에 주소(= token)가 들어 있을 수 있다
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        return None
    who = SsoToken.from_api(data)
    return who if who.user_no else None


def _rate_ok(conn: sqlite3.Connection, ip: str, now: datetime) -> bool:
    """접속자 주소마다 1분에 RATE_PER_MIN 번. 서버 프로그램 2개가 같은 숫자를 보도록 SQLite 에 센다 (§5)."""
    window = _iso(now)[:16]  # 1분 단위
    conn.execute("DELETE FROM sso_rate_hits WHERE window_start < ?", (window,))
    hits = conn.execute(
        "INSERT INTO sso_rate_hits (ip, window_start, hits) VALUES (?, ?, 1)"
        " ON CONFLICT (ip, window_start) DO UPDATE SET hits = hits + 1 RETURNING hits",
        (ip, window),
    ).fetchone()[0]
    return hits <= RATE_PER_MIN


def _go(code: str) -> Response:
    return redirect(f"/api/auth/message?m={code}", 302)


@bp.get("/api/auth/hiworks")
def hiworks_login() -> Response:
    conn, now, ip = _db(), _now(), _client_ip()
    if not _rate_ok(conn, ip, now):
        return _go("busy")
    token = request.args.get("token", "")
    if not TOKEN_RE.fullmatch(token):  # ① 모양
        return _go("bad")

    # ② 하이웍스에 묻기 전에 먼저 기록한다 — 기본 키라 같은 token 이 동시에 와도 하나만 들어간다 (§3 ②, §5)
    conn.execute("DELETE FROM sso_used_tokens WHERE used_at < ?", (_iso(now - TOKEN_KEEP),))
    try:
        conn.execute("INSERT INTO sso_used_tokens (token_hash, used_at) VALUES (?, ?)", (_sha(token), _iso(now)))
    except sqlite3.IntegrityError:
        return _go("used")

    who = ask_hiworks(token)  # ③
    if who is None:
        return _go("check")

    ua = (request.headers.get("User-Agent") or "")[:300]
    conn.execute("BEGIN IMMEDIATE")  # ④⑤ 사람 찾기 · 첫 HR · 세션을 한 트랜잭션에 (첫 HR 은 "0명 확인 → 지정"이 한 번에)
    try:
        res = resolve_sso_user(
            who, _members(conn),
            first_hr_user_no=os.environ.get("HIWORKS_FIRST_HR_USER_NO"),
            link_by_employee_no=os.environ.get("HIWORKS_SSO_LINK_BY_EMPLOYEE_NO") == "1",
        )
        if res.link:
            conn.execute("UPDATE users SET hiworks_user_no = ? WHERE id = ?", (who.user_no, res.user_id))
        if res.bootstrap_hr:  # 직책은 그대로 두고 HR 권한·포털 사용만 켠다. granted_by 비움 = 시스템
            conn.execute("UPDATE users SET portal_enabled = 1 WHERE id = ?", (res.user_id,))
            conn.execute("INSERT OR IGNORE INTO user_roles (user_id, role) VALUES (?, 'HR_ADMIN')", (res.user_id,))
        for e in res.audit:
            _audit(conn, e)
        if res.outcome is not SsoOutcome.OK:
            conn.execute("COMMIT")
            return _go("denied" if res.outcome is SsoOutcome.DENIED else "ready")

        # 로그인마다 새 세션 — 이 브라우저에 있던 세션 값은 이어 쓰지 않고 끊는다 (§5 세션 고정)
        old = request.cookies.get(COOKIE)
        if old:
            conn.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL", (_iso(now), _sha(old)))
        raw = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_sha(raw), res.user_id, _iso(now), _iso(now), _iso(now + MAX_AGE), ip, ua),
        )
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(now), res.user_id))
        _audit(conn, AuditEntry.by(actor_of(conn, res.user_id), action="LOGIN", entity_type="user",
                                   entity_id=res.user_id, ip=ip, user_agent=ua))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise

    resp = redirect("/", 302)  # ⑥ 주소창에서 token 을 지운다
    # max_age 없음 = 브라우저를 끄면 사라지는 쿠키. 유지 기간(4시간·12시간)은 서버가 센다 (§10)
    resp.set_cookie(COOKIE, raw, path="/", secure=True, httponly=True, samesite="Lax")
    # 화면이 "하이웍스 메뉴로 연 창"을 알아보는 신호 — 비밀 값이 아니라서 화면(JS)이 읽는다
    resp.set_cookie(ENTRY_COOKIE, "1", max_age=60, path="/", secure=True, samesite="Lax")
    return resp


@bp.get("/api/auth/message")
def login_message() -> Response:
    text, status = MESSAGES.get(request.args.get("m", ""), MESSAGES["bad"])
    page = (
        "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>인사포털</title></head>"
        "<body style='font-family:sans-serif;margin:0;padding:48px 24px;line-height:1.7;color:#222'>"
        f"<p style='font-size:17px;max-width:520px'>{html.escape(text)}</p></body></html>"
    )
    return Response(page, status=status, mimetype="text/html")


@bp.post("/api/auth/logout")
def logout() -> Response:
    _db().execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                  (_iso(_now()), g.session_hash))
    resp = jsonify(ok=True, message="포털에서 로그아웃했습니다. 공용 PC라면 하이웍스도 로그아웃해 주세요.")
    resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Lax")
    return resp


@bp.get("/api/me")
def me() -> Response:
    a: Actor = g.actor
    name, dept, writes_kpi = _db().execute(
        "SELECT u.name, d.name, u.writes_kpi FROM users u JOIN departments d ON d.id = u.department_id WHERE u.id = ?",
        (a.user_id,),
    ).fetchone()
    return jsonify(user_id=a.user_id, name=name, department=dept,
                   position=a.position.value if a.position else None, is_hr=a.is_hr, writes_kpi=bool(writes_kpi))


# ---------------- 구성원 설정 (HR) — 칸 이름은 화면(public/index.html 의 MS_FIELDS)과 같다 ----------------
LEAD = {Role.TEAM_LEADER: "TEAM", Role.DIVISION_HEAD: "DIVISION", Role.CEO: "CEO"}
FIELD = {"use": "portal_enabled", "hr": "is_hr", "kpi": "writes_kpi", "ev": "in_evaluation", "ct": "has_contract"}
AUDIT_KEYS = ("portal_enabled", "position", "is_hr", "writes_kpi", "in_evaluation", "has_contract", "is_active")


def _hr_only() -> tuple[Response, int] | None:
    return None if g.actor.is_hr else (jsonify(error="HR만 할 수 있습니다."), 403)


def _fix(m: Member) -> Member:
    """대표이사는 KPI·평가·계약서가 없고, 본부장은 평가·계약서가 없다 — 화면(msFix)과 같은 규칙 (§6)."""
    if m.position is Role.CEO:
        return replace(m, writes_kpi=False, in_evaluation=False, has_contract=False)
    if m.position is Role.DIVISION_HEAD:
        return replace(m, in_evaluation=False, has_contract=False)
    return m


def _audit_val(m: Member, k: str) -> object:
    v = getattr(m, k)
    return (v.value if v else None) if k == "position" else v


def _save_members(conn: sqlite3.Connection, before: list[Member], after: list[Member], action: str) -> int:
    """검사를 통과한 명단을 저장한다. 바뀐 사람마다 감사로그, 들어올 수 없게 된 사람은 세션을 끊는다."""
    validate_members(after, before=before)  # 직책 자리 · 본부장/대표이사 잠금 · 마지막 HR 보호 → ValidationError
    now, was, n = _iso(_now()), {m.id: m for m in before}, 0
    for m in after:
        b = was[m.id]
        if m == b:
            continue
        n += 1
        conn.execute(
            "UPDATE users SET portal_enabled = ?, writes_kpi = ?, in_evaluation = ?, has_contract = ?, is_active = ?,"
            " deactivated_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(deactivated_at, ?) END WHERE id = ?",
            (int(m.portal_enabled), int(m.writes_kpi), int(m.in_evaluation), int(m.has_contract), int(m.is_active),
             int(m.is_active), now, m.id),
        )
        for r in b.roles() - m.roles():
            conn.execute("DELETE FROM user_roles WHERE user_id = ? AND role = ?", (m.id, r.value))
        for r in m.roles() - b.roles():
            conn.execute("INSERT INTO user_roles (user_id, role, granted_by) VALUES (?, ?, ?)",
                         (m.id, r.value, g.actor.user_id))
        if not (m.portal_enabled and m.is_active):  # 들어올 수 없게 되면 지금 로그인도 끊는다 (§4)
            conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, m.id))
        diff = [k for k in AUDIT_KEYS if _audit_val(b, k) != _audit_val(m, k)]
        _audit(conn, AuditEntry.by(g.actor, action=action, entity_type="user", entity_id=m.id,
                                   before_data={k: _audit_val(b, k) for k in diff},
                                   after_data={k: _audit_val(m, k) for k in diff}))
    return n


@bp.get("/api/members")
def members_list() -> Response:
    if (denied := _hr_only()) is not None:
        return denied
    rows = _db().execute(
        "SELECT u.id, u.name, d.name, d.level, p.name, u.is_active, u.portal_enabled, u.writes_kpi, u.in_evaluation,"
        " u.has_contract, (SELECT group_concat(role) FROM user_roles r WHERE r.user_id = u.id),"
        " EXISTS (SELECT 1 FROM audit_logs a WHERE a.entity_type = 'user' AND a.entity_id = u.id"
        "         AND a.action IN ('MEMBER_UPDATED', 'MEMBER_ACTIVE'))"
        " FROM users u JOIN departments d ON d.id = u.department_id LEFT JOIN departments p ON p.id = d.parent_id"
        " ORDER BY u.name"
    )
    out = []
    for r in rows:
        roles = [Role(x) for x in (r[10] or "").split(",") if x]
        team = r[3] == "TEAM"
        out.append({
            "id": r[0], "name": r[1], "division": r[4] if team else r[2], "team": r[2] if team else "",
            "active": bool(r[5]), "use": bool(r[6]), "kpi": bool(r[7]), "ev": bool(r[8]), "ct": bool(r[9]),
            "lead": LEAD.get(position_of(roles), ""), "hr": Role.HR_ADMIN in roles,
            "isNew": not r[6] and not r[11],  # 명단 동기화로 들어와 HR 이 아직 손대지 않은 사람
            "me": r[0] == g.actor.user_id,
        })
    return jsonify(members=out)


@bp.post("/api/members")
def members_save() -> Response:
    """{"changes": {"<id>": {"use"|"hr"|"kpi"|"ev"|"ct": bool, "lead": ""|"TEAM"|"DIVISION"|"CEO"}}} — 한 트랜잭션."""
    if (denied := _hr_only()) is not None:
        return denied
    changes = (request.get_json(silent=True) or {}).get("changes")
    if not isinstance(changes, dict) or not changes:
        return jsonify(error="바꾼 내용이 없습니다."), 400
    pos_of = {v: k for k, v in LEAD.items()}
    conn = _db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        before = _members(conn)
        if not {str(m.id) for m in before} >= set(changes):
            raise ValueError("명단에 없는 사람")
        after = []
        for m in before:
            c = changes.get(str(m.id)) or {}
            if not isinstance(c, dict) or any(k not in (*FIELD, "lead") for k in c):
                raise ValueError("알 수 없는 칸")
            if any(not isinstance(v, bool) for k, v in c.items() if k in FIELD):
                raise ValueError("켜짐·꺼짐이 아님")
            upd = {FIELD[k]: v for k, v in c.items() if k in FIELD}
            if "lead" in c:
                if c["lead"] not in ("", *pos_of):
                    raise ValueError("알 수 없는 직책")
                upd["position"] = pos_of.get(c["lead"])
            after.append(_fix(replace(m, **upd)) if upd else m)
        n = _save_members(conn, before, after, "MEMBER_UPDATED")
        conn.execute("COMMIT")
    except ValidationError as err:
        conn.execute("ROLLBACK")
        return jsonify(error="저장하지 못했습니다.", issues=[i.message for i in err.errors]), 400
    except ValueError:
        conn.execute("ROLLBACK")
        return jsonify(error="바꾼 내용의 모양이 올바르지 않습니다."), 400
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(saved=n)


@bp.post("/api/members/<int:user_id>/active")
def members_active(user_id: int) -> Response:
    """{"active": false} 비활성화(그 사람의 로그인도 끊김) / {"active": true} 다시 켜기. 마지막 HR 은 막는다."""
    if (denied := _hr_only()) is not None:
        return denied
    on = (request.get_json(silent=True) or {}).get("active")
    if not isinstance(on, bool):
        return jsonify(error="active 는 true/false 입니다."), 400
    conn = _db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        before = _members(conn)
        if user_id not in {m.id for m in before}:
            conn.execute("ROLLBACK")
            return jsonify(error="명단에 없는 사람입니다."), 404
        after = [replace(m, is_active=on) if m.id == user_id else m for m in before]
        _save_members(conn, before, after, "MEMBER_ACTIVE")
        conn.execute("COMMIT")
    except ValidationError as err:
        conn.execute("ROLLBACK")
        return jsonify(error="바꾸지 못했습니다.", issues=[i.message for i in err.errors]), 400
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(ok=True)


def _session() -> Actor | None:
    """쿠키 → 세션. 폐기·만료(마지막 사용 후 4시간 또는 발급 후 12시간)·비활성·포털 사용 꺼짐이면 None."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    conn, now, h = _db(), _now(), _sha(raw)
    row = conn.execute(
        "SELECT s.user_id, s.last_seen_at, s.expires_at FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ? AND s.revoked_at IS NULL AND u.is_active = 1 AND u.portal_enabled = 1",
        (h,),
    ).fetchone()
    if row is None:
        return None
    user_id, seen, expires = row
    if _iso(now) >= expires or seen <= _iso(now - IDLE):
        return None
    if seen < _iso(now - timedelta(minutes=1)):  # 1분에 한 번만 적는다 (요청마다 쓰지 않게)
        conn.execute("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (_iso(now), h))
    g.session_hash = h
    return actor_of(conn, user_id)


def _guard() -> Response | None:
    path = request.path
    if not path.startswith("/api/") or path in OPEN:
        return None
    if request.method not in ("GET", "HEAD", "OPTIONS") and not _same_origin():
        return jsonify(error="허용되지 않은 요청입니다."), 403
    actor = _session()
    if actor is None:
        return jsonify(error=MSG_LOGIN), 401
    g.actor = actor
    return None


def _no_store(resp: Response) -> Response:
    if request.path.startswith("/api/auth/") or request.path == "/api/me":
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _close_db(_exc: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def register(app: Flask) -> None:
    app.register_blueprint(bp)
    app.before_request(_guard)
    app.after_request(_no_store)
    app.teardown_appcontext(_close_db)
