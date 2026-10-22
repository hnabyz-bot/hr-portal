"""하이웍스 SSO 로그인과 세션 검사 (로그인 설계 §3·§4·§5·§7, 4단계).

app.py 가 register(app) 로 붙인다.

  GET  /api/auth/hiworks?token=   하이웍스 "인사포털" 메뉴가 새 탭으로 보내는 곳 → 확인 → 세션 → "/" 로
  GET  /api/auth/message?m=       로그인이 안 됐을 때의 안내. 주소창에서 token 을 지우려고 여기로 보낸다
  POST /api/auth/logout           포털 세션만 끊는다 (하이웍스는 그대로)
  GET  /api/me                    지금 누구인가 (화면용)
  GET  /api/members               구성원 설정 명단 (HR) — 5단계
  POST /api/members               사람별 설정 여섯 칸 저장 (HR, 규칙은 domain/members.validate_members)
  POST /api/members/<id>/active   비활성화 · 다시 켜기 (HR)
  POST /api/members/<id>/phone/revoke  그 사람의 휴대폰 등록 해제 (HR)
  POST /api/members/<id>/org      조직도에서 빼기 · 다시 보이기 (HR) — DB 에서 지우지 않는다
  POST /api/members/contacts      HR 이 넣는 사람 정보 (HR) — 비상연락망(직급·내선·연락처·메일)과 입사일·생일. 화면이 이름으로 맞춘 뒤 보낸다
  POST /api/members/<id>/privacy  그 사람의 생일 알림 끄기·켜기 (HR) — 포털을 아직 못 쓰는 직원이 부탁할 때
  POST /api/members/<id>/employee-no  {"no": ""} 사원코드 지우기 (HR) — 급여명세서를 올릴 때 잘못 맞춘 직원에게서. 다음에 올릴 때 다시 붙는다
  POST /api/members/mail-nos      하이웍스 메일 받는 사람 채우기용 회사 번호 (HR) — 하이웍스 로그인 브라우저에서 가져온 짝
  GET  /api/org                   조직도 명단 (로그인한 누구나) — 필요한 칸만, 비공개 휴대폰은 본인·HR 에게만
  POST /api/me/privacy            내 휴대폰 공개 · 생일 알림 (본인)
  GET  /api/news                  홈 "이번 주 소식" (로그인한 누구나) — 지금 보일 줄만(생일은 오늘부터 7일). 생일·입사일 원본은 보내지 않는다
  POST /api/news/events           경사·조사 올리기 (HR)
  POST /api/news/events/<id>/remove  경사·조사 게시 내리기 (HR)
  GET  /api/workspace             내 워크스페이스(나의 업무 · 나의 일상) — 본인만. PC·휴대폰이 같은 내용
  POST /api/workspace/<kind>      저장 (tasks · routines). 판 번호가 다르면 409 + 서버 것 (다른 기기가 먼저 고침)
  GET  /api/payslips · POST /api/payslips/<ym>[/open|/close|/seen]  급여명세서 — 잠가 저장, 본인(공개된 달)·HR 만. payslip.py 맨 위 참고
  POST /api/phone/code            PC 에서 "휴대폰 등록" → QR 에 담을 1회용 값(3분) — §13.1
  POST /api/phone/register        휴대폰이 QR 주소(/#d=값)를 열면 화면이 보낸다 → 휴대폰 세션(30일)
  GET  /api/phone                 내 등록된 휴대폰 (PC 창이 "등록됐는지"를 볼 때도 쓴다)
  POST /api/phone/revoke          내 휴대폰 등록 해제
  그 밖의 /api/* 전부             세션 검사. 세션 없이 열린 곳은 /api/health · 로그인 받는 곳 · 안내 · 휴대폰 등록뿐 (§3)

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
from calendar import isleap
from datetime import date, datetime, timedelta, timezone
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
from hr_eval.domain.org import NO_COL, Dept, is_direct_team
from hr_eval.roster_sync import NoRedirect
from hr_eval.sql.db import connect

LINK_API_DEFAULT = "https://link-api.hiworks.com"  # 코드에 고정 (§5 "가짜 확인 서버")
COOKIE = "hr_session"
ENTRY_COOKIE = "hr_entry"  # 하이웍스 메뉴로 막 들어왔다는 1분짜리 표시 — 화면이 읽고 지운다 (§3 "주소로 연 창")
IDLE = timedelta(hours=4)  # 마지막 사용 후 (§10 확정)
MAX_AGE = timedelta(hours=12)  # 발급 후 최대 (§10)
PHONE_IDLE = timedelta(days=30)  # 휴대폰 세션: 마지막 사용 후, 쓸 때마다 연장 (§13.1)
CODE_KEEP = timedelta(minutes=3)  # QR 에 담는 1회용 값 (§13.1)
TOKEN_KEEP = timedelta(seconds=60)  # 하이웍스 token 유효시간과 같게 (#65 ③)
RATE_PER_MIN = 30  # 접속자 주소마다 1분 (§5)
TOKEN_RE = re.compile(r"[0-9a-f]{64}")  # 하이웍스 token 모양 (2026-10-12 실측)
MOBILE_RE = re.compile(r"iPhone|Android|Mobile")  # 휴대폰 브라우저 (§13.1 — 등록은 휴대폰에서만)
OPEN = {"/api/health", "/api/auth/hiworks", "/api/auth/message", "/api/phone/register"}
MSG_LOGIN = "로그인이 필요합니다. 하이웍스에서 인사포털 메뉴로 들어와 주세요."
AGAIN = "하이웍스에서 인사포털 메뉴를 다시 눌러 주세요."
MSG_QR = "QR을 다시 띄워 주세요. QR은 3분 동안 한 번만 쓸 수 있습니다."
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
        " u.writes_kpi, u.in_evaluation, u.has_contract, group_concat(r.role), u.led_department_id"
        " FROM users u LEFT JOIN user_roles r ON r.user_id = u.id GROUP BY u.id"
    )
    out = []
    for r in rows:
        roles = [Role(x) for x in (r[10] or "").split(",") if x]
        out.append(
            Member(
                r[0], r[1], r[2], r[3], r[4], bool(r[5]), bool(r[6]), position_of(roles), Role.HR_ADMIN in roles,
                bool(r[7]), bool(r[8]), bool(r[9]), r[11],
            )
        )
    return out


def _depts(conn: sqlite3.Connection) -> list[Dept]:
    return [Dept(*r) for r in conn.execute("SELECT id, name, parent_id FROM departments ORDER BY id")]


def _dept_kind(d: Dept, depts: list[Dept]) -> str:
    """고르는 목록용 종류 — 팀 · 장이 없는 부서('임원' 등) · 대표이사 직속 팀 · 본부."""
    if d.parent_id is not None:
        return "TEAM"
    if d.name in NO_COL:
        return "NOCOL"
    return "DIRECT" if is_direct_team(d, depts) else "DIVISION"


def actor_of(conn: sqlite3.Connection, user_id: int) -> Actor:
    """세션 → Actor. 팀장·본부장이 "장"인 부서는 직책과 맡은 부서로 정한다 (맡은 부서를 안 정했으면 본인 소속)."""
    dept = conn.execute("SELECT COALESCE(led_department_id, department_id) FROM users WHERE id = ?", (user_id,)).fetchone()[0]
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
    g.pop("phone_cookie", None)  # 끊는 응답에서 쿠키를 다시 늘리지 않는다
    conn = _db()
    conn.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL",
                 (_iso(_now()), g.session_hash))
    _forget_push(conn, [g.session_hash])
    resp = jsonify(ok=True, message="포털에서 로그아웃했습니다. 공용 PC라면 하이웍스도 로그아웃해 주세요.")
    resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="Lax")
    return resp


@bp.get("/api/me")
def me() -> Response:
    a: Actor = g.actor
    name, dept, writes_kpi, hire = _db().execute(
        "SELECT u.name, d.name, u.writes_kpi, u.hire_date FROM users u JOIN departments d ON d.id = u.department_id WHERE u.id = ?",
        (a.user_id,),
    ).fetchone()
    return jsonify(user_id=a.user_id, name=name, department=dept, kind=g.session_kind, hire_date=hire,
                   position=a.position.value if a.position else None, is_hr=a.is_hr, writes_kpi=bool(writes_kpi))


# ---------------- 휴대폰 등록 (§13.1) — PC 에서 QR 을 띄우고 휴대폰 카메라로 찍는다 ----------------
def _site() -> str:
    """QR 에 넣을 포털 주소. 운영은 Cloudflare 가 https 로 받는다(nginx·api 사이는 http). PC 시험은 localhost."""
    host = request.host
    return f"{'http' if host.split(':')[0] in ('localhost', '127.0.0.1') else 'https'}://{host}"


def _qr_rows(text: str) -> str:
    """QR 무늬 — 검은 칸 #, 흰 칸 . 를 줄마다 | 로 이어 화면(installQrSvg)이 그린다. reportlab 에 든 QR 부호기."""
    from reportlab.graphics.barcode import qrencoder as qr
    c = qr.QRCode(None, qr.QRErrorCorrectLevel.M)
    c.addData(text)
    c.make()
    n = c.getModuleCount()
    return "|".join("".join("#" if c.isDark(r, x) else "." for x in range(n)) for r in range(n))


def _device(ua: str) -> str:
    """등록 목록에 보일 휴대폰 이름. 짐작일 뿐이다(안드로이드 크롬은 기종을 숨긴다)."""
    if "iPhone" in ua or "iPad" in ua:
        return "아이폰"
    if "SM-" in ua or "Samsung" in ua:
        return "갤럭시"
    return "안드로이드" if "Android" in ua else "휴대폰"


def _phone_of(conn: sqlite3.Connection, user_id: int, now: datetime) -> dict | None:
    row = conn.execute(
        "SELECT device, created_at, last_seen_at FROM sessions WHERE user_id = ? AND kind = 'phone'"
        " AND revoked_at IS NULL AND last_seen_at > ? ORDER BY created_at DESC LIMIT 1",
        (user_id, _iso(now - PHONE_IDLE)),
    ).fetchone()
    return {"device": row[0], "created_at": row[1], "last_seen_at": row[2]} if row else None


def _forget_push(conn: sqlite3.Connection, token_hashes: list[str]) -> None:
    """끊은 세션의 알림 구독 줄을 지운다. push 가 auth 를 불러오므로 함수 안에서 불러온다 (순환 import)."""
    import push
    push.forget(conn, token_hashes)


def _revoke_phone(conn: sqlite3.Connection, user_id: int, now: datetime) -> int:
    _forget_push(conn, [r[0] for r in conn.execute(
        "SELECT token_hash FROM sessions WHERE user_id = ? AND kind = 'phone' AND revoked_at IS NULL", (user_id,))])
    return conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND kind = 'phone' AND revoked_at IS NULL",
                        (_iso(now), user_id)).rowcount


@bp.post("/api/phone/code")
def phone_code() -> Response:
    """QR 에 담을 1회용 값(3분). PC(하이웍스로 들어온 세션)에서만. 이 사람이 앞서 띄운 값은 지운다."""
    if g.session_kind != "pc":
        return jsonify(error="휴대폰 등록은 PC에서 합니다."), 403
    conn, now, raw = _db(), _now(), secrets.token_urlsafe(32)
    conn.execute("DELETE FROM phone_codes WHERE user_id = ? OR expires_at < ?", (g.actor.user_id, _iso(now)))
    conn.execute("INSERT INTO phone_codes (code_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                 (_sha(raw), g.actor.user_id, _iso(now), _iso(now + CODE_KEEP)))
    url = f"{_site()}/#d={raw}"  # 값은 # 뒤 — 서버로 가지 않아 nginx 기록·Referer 에 남지 않는다. 화면이 POST 로 보낸다
    return jsonify(qr=_qr_rows(url), seconds=int(CODE_KEEP.total_seconds()), since=_iso(now))


@bp.post("/api/phone/register")
def phone_register() -> Response:
    """{"code": 값} → 그 사람의 휴대폰 세션(30일). 한 사람에 한 대 — 앞서 등록한 휴대폰은 끊는다 (§13.1)."""
    conn, now, ip = _db(), _now(), _client_ip()
    if not _same_origin():  # 세션 검사 밖(OPEN)이라 여기서 따로 본다
        return jsonify(error="허용되지 않은 요청입니다."), 403
    if not _rate_ok(conn, ip, now):
        return jsonify(error=MESSAGES["busy"][0]), 429
    raw = str((request.get_json(silent=True) or {}).get("code", ""))[:100]
    ua = (request.headers.get("User-Agent") or "")[:300]
    if not MOBILE_RE.search(ua):  # QR 주소를 PC 에 옮겨 쳐서 30일 세션을 받는 일을 막는다. 값은 쓰지 않고 남긴다
        return jsonify(error="휴대폰 카메라로 찍어 주세요."), 400  # ponytail: 브라우저 이름은 꾸밀 수 있다 — 안내 수준, 공용 PC 대비는 PC 규칙
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(  # 쓰는 순간 표시한다 — 같은 값이 동시에 와도 하나만 통과
            "UPDATE phone_codes SET used_at = ? WHERE code_hash = ? AND used_at IS NULL AND expires_at > ? RETURNING user_id",
            (_iso(now), _sha(raw), _iso(now)),
        ).fetchone()
        uid = row[0] if row else None
        if uid is None or not conn.execute(
            "SELECT 1 FROM users WHERE id = ? AND is_active = 1 AND portal_enabled = 1", (uid,)
        ).fetchone():
            conn.execute("COMMIT")
            return jsonify(error=MSG_QR), 410
        _revoke_phone(conn, uid, now)
        old = request.cookies.get(COOKIE)  # 이 휴대폰에 남아 있던 세션도 이어 쓰지 않는다 (§5 세션 고정)
        if old:
            conn.execute("UPDATE sessions SET revoked_at = ? WHERE token_hash = ? AND revoked_at IS NULL", (_iso(now), _sha(old)))
            _forget_push(conn, [_sha(old)])
        sess = secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, ip, user_agent, kind, device)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'phone', ?)",
            (_sha(sess), uid, _iso(now), _iso(now), _iso(now + PHONE_IDLE), ip, ua, _device(ua)),
        )
        _audit(conn, AuditEntry.by(actor_of(conn, uid), action="PHONE_REGISTERED", entity_type="user",
                                   entity_id=uid, ip=ip, user_agent=ua))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    resp = jsonify(ok=True)
    resp.set_cookie(COOKIE, sess, max_age=int(PHONE_IDLE.total_seconds()), path="/", secure=True, httponly=True, samesite="Lax")
    return resp


@bp.get("/api/phone")
def phone_mine() -> Response:
    return jsonify(phone=_phone_of(_db(), g.actor.user_id, _now()), this=g.session_kind == "phone")


@bp.post("/api/phone/revoke")
def phone_revoke() -> Response:
    g.pop("phone_cookie", None)
    conn, now = _db(), _now()
    if _revoke_phone(conn, g.actor.user_id, now):
        _audit(conn, AuditEntry.by(g.actor, action="PHONE_REVOKED", entity_type="user", entity_id=g.actor.user_id))
    return jsonify(ok=True)


# ---------------- 구성원 설정 (HR) — 칸 이름은 화면(public/index.html 의 MS_FIELDS)과 같다 ----------------
LEAD = {Role.TEAM_LEADER: "TEAM", Role.DIVISION_HEAD: "DIVISION", Role.CEO: "CEO"}
FIELD = {"use": "portal_enabled", "hr": "is_hr", "kpi": "writes_kpi", "ev": "in_evaluation", "ct": "has_contract"}
AUDIT_KEYS = ("portal_enabled", "position", "is_hr", "writes_kpi", "in_evaluation", "has_contract", "is_active", "led_department_id")


def _hr_only() -> tuple[Response, int] | None:
    return None if g.actor.is_hr else (jsonify(error="HR만 할 수 있습니다."), 403)


def _fix(m: Member) -> Member:
    """대표이사는 KPI·평가·계약서가 없고, 본부장은 평가·계약서가 없다 — 화면(msFix)과 같은 규칙 (§6). 팀장·본부장이 아니면 맡은 부서도 없다."""
    if m.position not in (Role.TEAM_LEADER, Role.DIVISION_HEAD) and m.led_department_id is not None:
        m = replace(m, led_department_id=None)
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
    validate_members(after, before=before, depts=_depts(conn))  # 직책 자리 · 맡은 부서 · 본부장/대표이사 잠금 · 마지막 HR 보호 → ValidationError
    now, was, n = _iso(_now()), {m.id: m for m in before}, 0
    for m in after:
        b = was[m.id]
        if m == b:
            continue
        n += 1
        conn.execute(
            "UPDATE users SET portal_enabled = ?, writes_kpi = ?, in_evaluation = ?, has_contract = ?, is_active = ?,"
            " led_department_id = ?,"
            " deactivated_at = CASE WHEN ? = 1 THEN NULL ELSE COALESCE(deactivated_at, ?) END WHERE id = ?",
            (int(m.portal_enabled), int(m.writes_kpi), int(m.in_evaluation), int(m.has_contract), int(m.is_active),
             m.led_department_id, int(m.is_active), now, m.id),
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
    conn = _db()
    rows = conn.execute(
        "SELECT u.id, u.name, d.name, d.level, p.name, u.is_active, u.portal_enabled, u.writes_kpi, u.in_evaluation,"
        " u.has_contract, (SELECT group_concat(role) FROM user_roles r WHERE r.user_id = u.id),"
        " EXISTS (SELECT 1 FROM audit_logs a WHERE a.entity_type = 'user' AND a.entity_id = u.id"
        "         AND a.action IN ('MEMBER_UPDATED', 'MEMBER_ACTIVE')),"
        " (SELECT s.device || ' · 마지막 사용 ' || substr(s.last_seen_at, 6, 5) FROM sessions s WHERE s.user_id = u.id"
        "  AND s.kind = 'phone' AND s.revoked_at IS NULL AND s.last_seen_at > ? ORDER BY s.created_at DESC LIMIT 1),"
        " u.title, u.org_hidden, u.hire_date, u.birthday, u.birthday_public, u.led_department_id, u.department_id, u.employee_no"
        " FROM users u JOIN departments d ON d.id = u.department_id LEFT JOIN departments p ON p.id = d.parent_id"
        " ORDER BY u.name",
        (_iso(_now() - PHONE_IDLE),),
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
            "phone": r[12] or "",  # 등록된 휴대폰 (§13.1) — "갤럭시 · 마지막 사용 10-19"
            "title": r[13] or "", "orgHidden": bool(r[14]), "hireDate": r[15] or "",
            "birthday": r[16] or "", "birthdayPublic": bool(r[17]), "ledDept": r[18], "dept": r[19], "no": r[20] or "",  # no = 사원코드 (급여명세서를 올릴 때 기억됨)
        })
    depts = _depts(conn)
    open_ids = {r[0] for r in conn.execute("SELECT id FROM departments WHERE is_active = 1")}  # 닫힌 부서는 고르는 목록에 없다
    return jsonify(members=out, depts=[
        {"id": d.id, "name": d.name, "parent": d.parent_id, "kind": _dept_kind(d, depts)} for d in depts if d.id in open_ids])


@bp.post("/api/members")
def members_save() -> Response:
    """{"changes": {"<id>": {"use"|"hr"|"kpi"|"ev"|"ct": bool, "lead": ""|"TEAM"|"DIVISION"|"CEO", "ledDept": 부서 id|null}}} — 한 트랜잭션."""
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
            if not isinstance(c, dict) or any(k not in (*FIELD, "lead", "ledDept") for k in c):
                raise ValueError("알 수 없는 칸")
            if any(not isinstance(v, bool) for k, v in c.items() if k in FIELD):
                raise ValueError("켜짐·꺼짐이 아님")
            upd = {FIELD[k]: v for k, v in c.items() if k in FIELD}
            if "lead" in c:
                if c["lead"] not in ("", *pos_of):
                    raise ValueError("알 수 없는 직책")
                upd["position"] = pos_of.get(c["lead"])
            if "ledDept" in c:
                v = c["ledDept"]
                if v is not None and (type(v) is not int or v < 1):
                    raise ValueError("부서 번호가 아님")
                upd["led_department_id"] = None if v == m.department_id else v  # 소속과 같게 고르면 "소속 따라감"으로 저장
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


@bp.post("/api/members/<int:user_id>/phone/revoke")
def members_phone_revoke(user_id: int) -> Response:
    """그 사람의 휴대폰 등록 해제 (HR) — 휴대폰을 잃어버렸을 때 (§13.1)."""
    if (denied := _hr_only()) is not None:
        return denied
    conn = _db()
    if _revoke_phone(conn, user_id, _now()):
        _audit(conn, AuditEntry.by(g.actor, action="PHONE_REVOKED", entity_type="user", entity_id=user_id))
    return jsonify(ok=True)


# ---------------- 조직도 (2026-10-19) — 하이웍스 명단 + 비상연락망 값. 구조는 하이웍스 조직 그대로 ----------------
TEXT_MAX = {"title": 30, "ext": 20, "mobile": 30, "email": 120, "hire_date": 10, "birthday": 5}
PHONE_RE = re.compile(r"[0-9+()\- ]{2,30}")
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


@bp.get("/api/org")
def org_list() -> Response:
    """로그인한 누구나. 비활성·조직도에서 뺀 사람은 없다. 비공개 휴대폰은 본인과 HR 에게만 (비상연락망 관리)."""
    conn, a = _db(), g.actor
    divisions = [{"id": r[0], "name": r[1], "teams": []} for r in conn.execute(
        "SELECT id, name FROM departments WHERE level = 'DIVISION' ORDER BY id")]
    by_id = {d["id"]: d for d in divisions}
    for tid, name, parent in conn.execute("SELECT id, name, parent_id FROM departments WHERE level = 'TEAM' ORDER BY id"):
        if parent in by_id:
            by_id[parent]["teams"].append(name)
    out = []
    for r in conn.execute(
        "SELECT u.id, u.name, u.title, d.name, d.level, p.name, COALESCE(NULLIF(u.ext, ''), u.phone),"
        " COALESCE(NULLIF(u.hr_mobile, ''), u.mobile), COALESCE(NULLIF(u.hr_email, ''), u.email), u.mobile_public,"
        " u.hiworks_mail_no, (SELECT group_concat(role) FROM user_roles x WHERE x.user_id = u.id), u.birthday, u.birthday_public"
        " FROM users u JOIN departments d ON d.id = u.department_id LEFT JOIN departments p ON p.id = d.parent_id"
        " WHERE u.is_active = 1 AND u.org_hidden = 0 ORDER BY u.name"
    ):
        me, team = r[0] == a.user_id, r[4] == "TEAM"
        roles = [Role(x) for x in (r[11] or "").split(",") if x]
        show = bool(r[9]) or me or a.is_hr
        out.append({
            "id": r[0], "name": r[1], "title": r[2] or "", "division": r[5] if team else r[3], "team": r[3] if team else "",
            "lead": LEAD.get(position_of(roles), ""), "ext": r[6] or "", "email": r[8] or "",
            "mobile": (r[7] or "") if show else None, "mobilePublic": bool(r[9]),
            "mailNo": r[10] or "", "me": me,   # 하이웍스 메일 받는 사람 채우기 (회사 번호, 009)
        })
        if me:  # 생일은 본인 줄에만 — 다른 직원의 생일은 이번 주 소식(/api/news)으로만 보인다
            out[-1].update(birthday=r[12] or "", birthdayPublic=bool(r[13]))
    return jsonify(divisions=divisions, members=out, hr=a.is_hr)


def _privacy(user_id: int, allowed: tuple[str, ...]) -> Response:
    """{칸: true/false} — allowed 안의 칸만. 칸 이름은 allowed 에서 오므로 SQL 에 바로 넣어도 된다."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not body or any(k not in allowed or not isinstance(v, bool) for k, v in body.items()):
        return jsonify(error=f"{' · '.join(allowed)} 은 true/false 입니다."), 400
    conn = _db()
    if not conn.execute(f"UPDATE users SET {', '.join(f'{k} = ?' for k in body)} WHERE id = ?",
                        (*map(int, body.values()), user_id)).rowcount:
        return jsonify(error="명단에 없는 사람입니다."), 404
    _audit(conn, AuditEntry.by(g.actor, action="PRIVACY_UPDATED", entity_type="user", entity_id=user_id, after_data=body))
    return jsonify(ok=True)


@bp.post("/api/me/privacy")
def me_privacy() -> Response:
    """{"mobile_public"?: bool, "birthday_public"?: bool} — 조직도의 내 휴대폰 공개 · 홈 이번 주 소식의 생일 알림 (본인만)."""
    return _privacy(g.actor.user_id, ("mobile_public", "birthday_public"))


@bp.post("/api/members/<int:user_id>/privacy")
def members_privacy(user_id: int) -> Response:
    """{"birthday_public": bool} — 포털을 아직 못 쓰는 직원이 생일 알림을 꺼 달라고 하면 HR 이 대신 (2026-10-19 인사담당자)."""
    if (denied := _hr_only()) is not None:
        return denied
    return _privacy(user_id, ("birthday_public",))


@bp.post("/api/members/<int:user_id>/employee-no")
def members_employee_no(user_id: int) -> Response:
    """{"no": ""} 사원코드 지우기 (HR, 2026-10-21) — 급여명세서를 올릴 때 직원을 잘못 골라 남의 사원코드가 붙었을 때.
    지우기만 한다 — 다음에 올릴 때 맞는 직원에게 다시 붙는다(payslip._store). 감사로그에는 앞뒤 사원코드만."""
    if (denied := _hr_only()) is not None:
        return denied
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or body.get("no") != "":
        return jsonify(error='no 는 빈 글자("")만 됩니다 — 지우기.'), 400
    conn = _db()
    row = conn.execute("SELECT employee_no FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row:
        return jsonify(error="명단에 없는 사람입니다."), 404
    if row[0] is not None:  # 이미 비어 있으면 그대로 (감사로그도 없음)
        conn.execute("UPDATE users SET employee_no = NULL WHERE id = ?", (user_id,))
        _audit(conn, AuditEntry.by(g.actor, action="MEMBER_EMPLOYEE_NO", entity_type="user", entity_id=user_id,
                                   before_data={"no": row[0]}, after_data={"no": None}))
    return jsonify(ok=True)


@bp.post("/api/members/<int:user_id>/org")
def members_org(user_id: int) -> Response:
    """{"hidden": bool} 조직도에서 빼기 · 다시 보이기 (HR). 명단 동기화가 덮어쓰지 않는다."""
    if (denied := _hr_only()) is not None:
        return denied
    hidden = (request.get_json(silent=True) or {}).get("hidden")
    if not isinstance(hidden, bool):
        return jsonify(error="hidden 은 true/false 입니다."), 400
    conn = _db()
    if not conn.execute("UPDATE users SET org_hidden = ? WHERE id = ?", (int(hidden), user_id)).rowcount:
        return jsonify(error="명단에 없는 사람입니다."), 404
    _audit(conn, AuditEntry.by(g.actor, action="ORG_HIDDEN" if hidden else "ORG_SHOWN", entity_type="user", entity_id=user_id))
    return jsonify(ok=True)


@bp.post("/api/members/mail-nos")
def members_mail_nos() -> Response:
    """{"pairs": {"통합 사용자 번호": "회사 번호", …}} — 하이웍스 메일 받는 사람 채우기용 (HR, 2026-10-20).
    하이웍스 메일은 회사 번호로만 채운다. 공개 API 에는 없어 HR 이 하이웍스에 로그인한 브라우저에서 가져온다.
    명단에 없는 통합 번호는 건너뛰고 숫자만 돌려준다. 한 짝이라도 모양이 틀리면 아무것도 저장하지 않는다."""
    if (denied := _hr_only()) is not None:
        return denied
    pairs = (request.get_json(silent=True) or {}).get("pairs")
    num = lambda v: isinstance(v, str) and v.isascii() and v.isdigit() and v[0] != "0" and len(v) <= 12  # noqa: E731
    if not isinstance(pairs, dict) or not 0 < len(pairs) <= 3000 or not all(num(k) and num(v) for k, v in pairs.items()):
        return jsonify(error="번호 짝의 모양이 올바르지 않습니다."), 400
    conn = _db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        saved = sum(conn.execute("UPDATE users SET hiworks_mail_no = ? WHERE hiworks_user_no = ?", (v, k)).rowcount for k, v in pairs.items())
        _audit(conn, AuditEntry.by(g.actor, action="MEMBER_MAIL_NOS", entity_type="user", entity_id=None, after_data={"people": saved}))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    left = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1 AND hiworks_mail_no IS NULL").fetchone()[0]
    return jsonify(saved=saved, unknown=len(pairs) - saved, missing=left)


@bp.post("/api/members/contacts")
def members_contacts() -> Response:
    """{"rows": [{"id": n, "title"?, "ext"?, "mobile"?, "email"?, "hire_date"?: "YYYY-MM-DD", "birthday"?: "MM-DD"}]} — 비상연락망 파일 값·입사일·생일 (HR).
    빈 칸은 보내지 않고, 보낸 칸만 덮어쓴다.
    한 줄이라도 모양이 틀리면 아무것도 저장하지 않는다. 감사로그에는 값이 아니라 바뀐 칸 이름과 사람 수만 남긴다."""
    if (denied := _hr_only()) is not None:
        return denied
    rows = (request.get_json(silent=True) or {}).get("rows")
    if not isinstance(rows, list) or not rows or len(rows) > 2000:
        return jsonify(error="저장할 내용이 없습니다."), 400
    col = {"title": "title", "ext": "ext", "mobile": "hr_mobile", "email": "hr_email", "hire_date": "hire_date", "birthday": "birthday"}
    today = _now().date()
    conn = _db()
    known = {r[0] for r in conn.execute("SELECT id FROM users")}
    try:
        plan = []
        for row in rows:
            if not isinstance(row, dict) or row.get("id") not in known or any(k not in ("id", *col) for k in row):
                raise ValueError("사람·칸")
            vals = {}
            for k in col:
                if k in row:
                    v = row[k]
                    if not isinstance(v, str) or not v.strip() or len(v.strip()) > TEXT_MAX[k]:
                        raise ValueError(k)
                    v = v.strip()
                    if (k == "mobile" and not PHONE_RE.fullmatch(v)) or (k == "email" and not EMAIL_RE.fullmatch(v)):
                        raise ValueError(k)
                    if k == "hire_date":  # 실제 있는 날짜 · 1950년부터 · 앞날은 1년까지(입사 예정)
                        d = datetime.strptime(v, "%Y-%m-%d").date()  # 틀린 날짜는 ValueError
                        if v != d.isoformat() or d.year < 1950 or (d - today).days > 366:
                            raise ValueError(k)
                    if k == "birthday" and not (re.fullmatch(r"\d{2}-\d{2}", v) and datetime.strptime("2000-" + v, "%Y-%m-%d")):
                        raise ValueError(k)  # 월-일만 (연도 없이). 02-29 는 된다 — 2000년이 윤년
                    vals[col[k]] = v
            if vals:
                plan.append((row["id"], vals))
    except ValueError:
        return jsonify(error="파일 값의 모양이 올바르지 않습니다. 다시 골라 주세요."), 400
    conn.execute("BEGIN IMMEDIATE")
    try:
        for uid, vals in plan:
            conn.execute(f"UPDATE users SET {', '.join(f'{c} = ?' for c in vals)} WHERE id = ?", (*vals.values(), uid))
        fields = sorted({k for _, vals in plan for k in vals})
        _audit(conn, AuditEntry.by(g.actor, action="MEMBER_CONTACTS", entity_type="user", entity_id=None,
                                   after_data={"people": len(plan), "fields": fields}))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(saved=len(plan))


# ---------------- 홈 "이번 주 소식" (2026-10-19) — 새 식구 · 생일 · 입사 기념은 입사일·생일로, 경사·조사는 HR 이 올린다 ----------------
KST = timezone(timedelta(hours=9))  # 서버 컨테이너 시계는 UTC — "이번 주"는 한국 날짜로
CELEB_KINDS = ("joy", "condol")  # 경사 · 조사


def _today() -> date:
    return _now().astimezone(KST).date()


def news_items(people: list[tuple], today: date) -> list[dict]:
    """people = [(id, hire_date, birthday, birthday_public)] → 지금 보일 줄. 화면 celebList 와 같은 규칙:
    새 식구 = 입사 후 30일 · 생일 = 오늘부터 7일 안(2026-10-20 인사담당자 — 전에는 이번 주 월~일. 평년에는 02-29 를 02-28 에), 생일 알림을 끈 사람은 빼고 ·
    입사 N주년 = 이번 달, 1·3·5·10년 이후 5년마다. 순서는 새 식구 · 생일(날짜순) · 입사 기념."""
    week = [today + timedelta(days=i) for i in range(7)]
    md = lambda m, d: f"{m}월 {d}일"  # noqa: E731
    welcome, birthday, anniv = [], [], []
    for uid, hire, bday, public in people:
        h = date.fromisoformat(hire) if hire else None
        if h and 0 <= (today - h).days <= 30:
            welcome.append({"kind": "welcome", "id": uid, "note": f"{md(h.month, h.day)} 입사"})
        y = today.year - h.year if h else 0
        if h and h.month == today.month and (y in (1, 3, 5) or (y >= 10 and y % 5 == 0)):
            anniv.append({"kind": "anniversary", "id": uid, "note": f"입사 {y}주년 · {md(h.month, h.day)}"})
        if bday and public:
            day = next((d for d in week if d.strftime("%m-%d") == ("02-28" if bday == "02-29" and not isleap(d.year) else bday)), None)
            if day:
                birthday.append((day, {"kind": "birthday", "id": uid, "note": f"{md(int(bday[:2]), int(bday[3:]))} 생일"}))
    return welcome + [x for _, x in sorted(birthday, key=lambda b: b[0])] + anniv


@bp.get("/api/news")
def news() -> Response:
    """로그인한 누구나. 조직도에 보이는 사람만(비활성·조직도에서 뺀 사람은 없다). 생일·입사일 원본 대신 이번 주에 보일 줄만 보낸다."""
    conn, today = _db(), _today()
    shown = "u.is_active = 1 AND u.org_hidden = 0"
    people = conn.execute(f"SELECT u.id, u.hire_date, u.birthday, u.birthday_public FROM users u WHERE {shown}").fetchall()
    events = conn.execute(
        f"SELECT e.id, e.user_id, e.kind, e.label FROM celeb_events e JOIN users u ON u.id = e.user_id"
        f" WHERE {shown} AND e.removed_at IS NULL AND e.until_date >= ? ORDER BY e.id", (today.isoformat(),))
    return jsonify(items=news_items(people, today) + [{"kind": k, "id": uid, "note": label, "event": eid} for eid, uid, k, label in events])


@bp.post("/api/news/events")
def news_event_add() -> Response:
    """{"user_id": n, "kind": "joy"|"condol", "label": "결혼", "until": "YYYY-MM-DD"} — HR 이 본인(가족) 동의를 받고 올린다.
    게시 종료일이 지나면 저절로 안 보인다. 감사로그에는 내용(부친상 등)을 남기지 않는다."""
    if (denied := _hr_only()) is not None:
        return denied
    b = request.get_json(silent=True)
    b = b if isinstance(b, dict) else {}
    uid, kind, label, until, today = b.get("user_id"), b.get("kind"), b.get("label"), b.get("until"), _today()
    try:
        d = date.fromisoformat(until) if isinstance(until, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", until) else None
    except ValueError:
        d = None
    conn = _db()
    if (kind not in CELEB_KINDS or not isinstance(label, str) or not 0 < len(label.strip()) <= 30
            or d is None or not 0 <= (d - today).days <= 366 or type(uid) is not int
            or not conn.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1", (uid,)).fetchone()):
        return jsonify(error="종류 · 대상자 · 내용(30자까지) · 게시 종료일(오늘부터 1년 안)을 확인해 주세요."), 400
    eid = conn.execute(
        "INSERT INTO celeb_events (user_id, kind, label, until_date, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (uid, kind, label.strip(), until, g.actor.user_id, _iso(_now()))).lastrowid
    _audit(conn, AuditEntry.by(g.actor, action="NEWS_POSTED", entity_type="celeb_event", entity_id=eid,
                               after_data={"user_id": uid, "kind": kind, "until": until}))
    return jsonify(id=eid)


@bp.post("/api/news/events/<int:event_id>/remove")
def news_event_remove(event_id: int) -> Response:
    """게시 내리기 (HR). 지우지 않고 내린 때만 적는다."""
    if (denied := _hr_only()) is not None:
        return denied
    conn = _db()
    if not conn.execute("UPDATE celeb_events SET removed_at = ? WHERE id = ? AND removed_at IS NULL",
                        (_iso(_now()), event_id)).rowcount:
        return jsonify(error="없거나 이미 내린 소식입니다."), 404
    _audit(conn, AuditEntry.by(g.actor, action="NEWS_REMOVED", entity_type="celeb_event", entity_id=event_id))
    return jsonify(ok=True)


# ---------------- 워크스페이스 (2026-10-19) — 나의 업무 · 나의 일상을 사람별로. 전에는 기기마다 브라우저에만 있었다 ----------------
WS_KINDS = ("tasks", "routines")
WS_MAX_BYTES = 2_000_000  # 한 목록. 루틴 10개 × 1년 기록 ≈ 60KB, 업무 1,000건 ≈ 300KB (nginx 는 5MB)
WS_MAX_ITEMS = 5000


@bp.get("/api/workspace")
def workspace_get() -> Response:
    """{"tasks": {"data": [...] | null, "version": n}, "routines": {...}} — version 0 = 아직 서버에 없음."""
    rows = {k: (json.loads(d), v) for k, d, v in _db().execute(
        "SELECT kind, data, version FROM workspace_data WHERE user_id = ?", (g.actor.user_id,))}
    return jsonify({k: {"data": rows[k][0], "version": rows[k][1]} if k in rows else {"data": None, "version": 0} for k in WS_KINDS})


@bp.post("/api/workspace/<kind>")
def workspace_put(kind: str) -> Response:
    """{"data": [...], "version": 화면이 아는 판} → {"version": 새 판}. 판이 다르면 409 + 지금 서버 것 — 덮어쓰지 않는다.
    내용(업무 제목 · 메모 등)은 본인 것이라 감사로그에 남기지 않는다. 모양만 본다: 목록 · 항목은 객체."""
    if kind not in WS_KINDS:
        abort(404)
    raw = request.get_data(cache=True)
    b = request.get_json(silent=True)
    data, ver = (b.get("data"), b.get("version")) if isinstance(b, dict) else (None, None)
    if (not isinstance(data, list) or len(data) > WS_MAX_ITEMS or not all(isinstance(x, dict) for x in data)
            or type(ver) is not int or ver < 0 or len(raw) > WS_MAX_BYTES):
        return jsonify(error="저장할 내용의 모양이 올바르지 않습니다."), 400
    conn, uid = _db(), g.actor.user_id
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT data, version FROM workspace_data WHERE user_id = ? AND kind = ?", (uid, kind)).fetchone()
        now = row[1] if row else 0
        if now != ver:
            conn.execute("ROLLBACK")
            return jsonify(error="다른 기기에서 먼저 바꿨습니다.", data=json.loads(row[0]) if row else None, version=now), 409
        conn.execute(
            "INSERT INTO workspace_data (user_id, kind, data, version, updated_at) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (user_id, kind) DO UPDATE SET data = excluded.data, version = excluded.version, updated_at = excluded.updated_at",
            (uid, kind, json.dumps(data, ensure_ascii=False), now + 1, _iso(_now())))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(version=now + 1)


def _session() -> Actor | None:
    """쿠키 → 세션. 폐기·만료·비활성·포털 사용 꺼짐이면 None.
    PC: 마지막 사용 후 4시간 또는 발급 후 12시간. 휴대폰: 마지막 사용 후 30일(쓸 때마다 만료일·쿠키를 늘린다)."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    conn, now, h = _db(), _now(), _sha(raw)
    row = conn.execute(
        "SELECT s.user_id, s.last_seen_at, s.expires_at, s.kind FROM sessions s JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = ? AND s.revoked_at IS NULL AND u.is_active = 1 AND u.portal_enabled = 1",
        (h,),
    ).fetchone()
    if row is None:
        return None
    user_id, seen, expires, kind = row
    phone = kind == "phone"
    if _iso(now) >= expires or seen <= _iso(now - (PHONE_IDLE if phone else IDLE)):
        return None
    if seen < _iso(now - timedelta(minutes=1)):  # 1분에 한 번만 적는다 (요청마다 쓰지 않게)
        conn.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
                     (_iso(now), _iso(now + PHONE_IDLE) if phone else expires, h))
        if phone:
            g.phone_cookie = raw  # 응답에서 쿠키 유지 기간도 30일로 다시 늘린다 (_no_store)
    g.session_hash, g.session_kind = h, kind
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
    if request.path.startswith(("/api/auth/", "/api/phone", "/api/payslips")) or request.path == "/api/me":
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Cache-Control"] = "no-store"
    if g.get("phone_cookie") and resp.status_code < 400:
        resp.set_cookie(COOKIE, g.phone_cookie, max_age=int(PHONE_IDLE.total_seconds()), path="/",
                        secure=True, httponly=True, samesite="Lax")
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
