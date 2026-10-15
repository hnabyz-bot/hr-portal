"""로그인(4단계) 처음부터 끝까지 검사 — 가짜 하이웍스 확인 서버(HIWORKS_LINK_API_BASE)로.

    cd backend && python -m auth_check      (CI: 평가·계약 도메인 검사)

설계 9장 1·2·3·5·11·12번을 번호대로 확인하고, 사람 찾기(4)·첫 HR(13)은 실제 로그인으로 돌려 본다.
하이웍스는 부르지 않는다. 사람·번호·확인표는 전부 가짜다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
TMP = tempfile.TemporaryDirectory()
CALLS: dict[str, int] = {}
PEOPLE = {  # 가짜 하이웍스가 알려 줄 사람: token 번호 → 하이웍스 사용자 번호
    "hr": "HW-1", "lead": "HW-2", "emp": "HW-3", "off": "HW-4", "gone": "HW-5", "nobody": "HW-999",
}


class FakeLinkApi(BaseHTTPRequestHandler):
    """GET /v4/tokens/{token} → {"code": "SUC", "data": {"user-no", …}}. tokens 표에 없으면 404."""

    tokens: dict[str, str] = {}

    def log_message(self, *args) -> None:  # noqa: ANN002
        pass

    def do_GET(self) -> None:  # noqa: N802
        token = self.path.rsplit("/", 1)[-1]
        CALLS[token] = CALLS.get(token, 0) + 1
        if self.path.startswith("/leak"):
            CALLS["leak"] = CALLS.get("leak", 0) + 1
        who = self.tokens.get(token)
        if who == "redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/leak/{token}")
            self.end_headers()
            return
        if who is None:
            self.send_response(404)
            self.end_headers()
            return
        body = json.dumps({"code": "SUC", "data": {"user-no": who, "user-id": "id-" + who, "user-name": "가상", "employee-no": ""}})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body.encode())


SRV = ThreadingHTTPServer(("127.0.0.1", 0), FakeLinkApi)
threading.Thread(target=SRV.serve_forever, daemon=True).start()

# app 을 불러오기 전에 정한다 — app 이 불러올 때 DB 를 만들고 설정을 검사한다
os.environ.update(HR_DB_PATH=str(Path(TMP.name) / "auth.db"), HIWORKS_LINK_API_BASE=f"http://127.0.0.1:{SRV.server_port}")
os.environ.pop("HR_ENV", None)
for k in ("HIWORKS_FIRST_HR_USER_NO", "HIWORKS_SSO_LINK_BY_EMPLOYEE_NO"):
    os.environ.pop(k, None)

import auth  # noqa: E402
from app import app  # noqa: E402
from hr_eval.sql.db import connect  # noqa: E402

BASE = "https://localhost"  # 쿠키가 Secure 라 https 로 보낸다
ORIGIN = {"Origin": BASE}
passed = 0
_seq = [0]


def ok(cond: bool, label: str) -> None:
    global passed
    if not cond:
        raise AssertionError(label)
    passed += 1
    print(f"  [통과] {label}")


def q(sql: str, *a: object) -> list:
    c = connect(os.environ["HR_DB_PATH"])
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def x(sql: str, *a: object) -> None:
    c = connect(os.environ["HR_DB_PATH"])
    try:
        c.execute(sql, a)
    finally:
        c.close()


def new_token(who: str | None) -> str:
    """가짜 하이웍스에 사람을 붙인 새 token (64자리 a~f·숫자)."""
    _seq[0] += 1
    t = hashlib.sha256(f"t{_seq[0]}".encode()).hexdigest()
    if who is not None:
        FakeLinkApi.tokens[t] = PEOPLE.get(who, who)
    return t


def login(client, token: str, ip: str | None = None):
    _seq[0] += 1
    return client.get(f"/api/auth/hiworks?token={token}", base_url=BASE,
                      headers={"CF-Connecting-IP": ip or f"198.51.100.{_seq[0] % 250}"})


def message_of(resp) -> str:
    loc = resp.headers.get("Location", "")
    return loc.split("m=")[-1] if "m=" in loc else ""


def cookie(client) -> str | None:
    c = client.get_cookie(auth.COOKIE, domain="localhost")
    return c.value if c else None


def seed() -> None:
    c = connect(os.environ["HR_DB_PATH"])
    c.execute("INSERT INTO departments (id, code, name, level) VALUES (1, 'D1', '가상본부', 'DIVISION')")
    c.execute("INSERT INTO departments (id, code, name, level, parent_id) VALUES (2, 'T1', '가상팀', 'TEAM', 1)")
    for uid, no, dept, portal, active in (
        (1, "HW-1", 1, 0, 1),  # 인사담당자 — 처음에는 HR 이 아무도 없다
        (2, "HW-2", 2, 1, 1),  # 팀장
        (3, "HW-3", 2, 1, 1),  # 직원
        (4, "HW-4", 2, 0, 1),  # 시범 사용자 명단 밖
        (5, "HW-5", 2, 1, 0),  # 비활성
    ):
        c.execute(
            "INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, deactivated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, f"가상{uid}", f"u{uid}@example.invalid", dept, no, portal, active, None if active else "2027-01-01T00:00:00Z"),
        )
    c.execute("INSERT INTO user_roles (user_id, role) VALUES (2, 'TEAM_LEADER')")
    c.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    seed()
    cl = app.test_client()

    print("13·4. 첫 HR · 사람 찾기 (실제 로그인으로)")
    os.environ["HIWORKS_FIRST_HR_USER_NO"] = "HW-1"
    r = login(cl, new_token("hr"))
    ok(r.status_code == 302 and r.headers["Location"] == "/", "HR 이 0명일 때 설정의 하이웍스 번호와 같은 사람이 로그인")
    ok(q("SELECT role, granted_by FROM user_roles WHERE user_id = 1") == [("HR_ADMIN", None)]
       and q("SELECT portal_enabled FROM users WHERE id = 1") == [(1,)], "그 사람에게 HR 권한·포털 사용이 켜짐 (지정한 사람 = 시스템)")
    ok(q("SELECT COUNT(*) FROM audit_logs WHERE action = 'HR_BOOTSTRAP' AND entity_id = 1") == [(1,)], "감사로그 HR_BOOTSTRAP")
    os.environ["HIWORKS_FIRST_HR_USER_NO"] = "HW-4"  # HR 이 이미 있으면 설정값을 보지 않는다
    ok(message_of(login(app.test_client(), new_token("off"))) == "ready"
       and q("SELECT COUNT(*) FROM user_roles WHERE user_id = 4") == [(0,)], "HR 이 한 명이라도 있으면 설정값이 같아도 지정 안 됨")
    os.environ.pop("HIWORKS_FIRST_HR_USER_NO")
    없음, 비활성 = login(app.test_client(), new_token("nobody")), login(app.test_client(), new_token("gone"))
    ok(message_of(없음) == message_of(비활성) == "denied", "명단에 없는 사람과 비활성인 사람은 같은 안내")
    ok(app.test_client().get("/api/auth/message?m=denied").status_code == 403, "안내 화면은 403")

    print("1. token 취급")
    logs: list[str] = []

    class Grab(logging.Handler):
        def emit(self, rec: logging.LogRecord) -> None:
            logs.append(rec.getMessage())

    grab = Grab(level=logging.DEBUG)
    root = logging.getLogger()
    root.addHandler(grab)
    old_level = root.level
    root.setLevel(logging.DEBUG)
    emp = app.test_client()
    t = new_token("emp")
    r = login(emp, t)
    root.removeHandler(grab)
    root.setLevel(old_level)
    ok(r.status_code == 302 and r.headers["Location"] == "/", "302 로 token 없는 주소(/)로 보냄")
    ok(r.headers.get("Referrer-Policy") == "no-referrer" and r.headers.get("Cache-Control") == "no-store",
       "Referrer-Policy: no-referrer · Cache-Control: no-store")
    ok(not any(t in m for m in logs), "Flask 쪽 로그에 token 이 없음")
    ok(not q("SELECT 1 FROM sso_used_tokens WHERE token_hash = ?", t), "token 원본은 DB 에도 없음 (해시만)")
    conf = (HERE.parent / "nginx.conf").read_text(encoding="utf-8")
    fmt = conf.split("log_format hr_noquery", 1)[1].split(";", 1)[0]
    ok("location = /api/auth/hiworks" in conf and "access_log /var/log/nginx/access.log hr_noquery;" in conf
       and "$uri" in fmt and not any(v in fmt for v in ("$request ", "$request\"", "$request_uri", "$args", "$query_string")),
       "nginx: 로그인 받는 곳은 주소 뒤(?token=)를 빼고 기록")

    print("2. token 재사용 거부")
    before = CALLS.get(t, 0)
    ok(message_of(login(app.test_client(), t)) == "used" and CALLS.get(t, 0) == before,
       "같은 token 두 번째는 거부 — 하이웍스에 다시 묻지도 않음 (먼저 기록)")
    실패 = new_token(None)  # 하이웍스가 모르는 token
    ok(message_of(login(app.test_client(), 실패)) == "check" and message_of(login(app.test_client(), 실패)) == "used",
       "하이웍스 확인에 실패한 token 도 다시 쓸 수 없음")
    동시 = new_token("emp")
    results: list[str] = []

    def hit() -> None:
        resp = login(app.test_client(), 동시)
        results.append("ok" if resp.headers.get("Location") == "/" else message_of(resp))

    threads = [threading.Thread(target=hit) for _ in range(6)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    ok(results.count("ok") == 1 and results.count("used") == 5, f"같은 token 이 동시에 6번 와도 하나만 통과 ({results})")

    print("3. 확인 서버 고정")
    ok(auth.LINK_API_DEFAULT == "https://link-api.hiworks.com", "기본 확인 주소는 코드에 고정된 하이웍스 HTTPS")
    saved = os.environ.pop("HIWORKS_LINK_API_BASE")
    ok(auth.link_api_base() == "https://link-api.hiworks.com", "설정이 없으면 고정 주소만 씀")
    os.environ["HIWORKS_LINK_API_BASE"] = saved
    넘김 = new_token("redirect")
    ok(message_of(login(app.test_client(), 넘김)) == "check" and not CALLS.get("leak"), "확인 서버가 다른 곳으로 넘기면 따라가지 않음")
    for env, refused in (
        ({"HR_ENV": "production", "HIWORKS_LINK_API_BASE": "http://x"}, True),
        ({"HR_ENV": "production"}, False),
        ({"HIWORKS_LINK_API_BASE": "http://x"}, False),
    ):
        try:
            auth.refuse_unsafe_config(env)
            got = False
        except RuntimeError:
            got = True
        ok(got == refused, f"운영 모드에 우회 주소가 있을 때만 시작 거부: {env} → {'거부' if refused else '시작'}")
    compose = (HERE.parent / "docker-compose.yml").read_text(encoding="utf-8")
    ok("- HR_ENV=production" in compose and "HIWORKS_LINK_API_BASE" not in compose,
       "운영 모드는 docker-compose.yml 에 고정, 우회 주소는 넘기지 않음")

    print("5. 세션")
    set_cookie = r.headers.get("Set-Cookie", "")
    ok(all(f in set_cookie for f in ("Secure", "HttpOnly", "SameSite=Lax")), "쿠키 세 가지: Secure · HttpOnly · SameSite=Lax")
    raw = cookie(emp)
    ok(q("SELECT COUNT(*) FROM sessions WHERE token_hash = ?", hashlib.sha256(raw.encode()).hexdigest()) == [(1,)]
       and not q("SELECT 1 FROM sessions WHERE token_hash = ?", raw), "세션은 해시만 저장")
    me = emp.get("/api/me", base_url=BASE)
    ok(me.status_code == 200 and me.json["user_id"] == 3 and me.json["is_hr"] is False, "/api/me 로 누구인지 알려 줌")

    other = app.test_client()
    other.set_cookie(auth.COOKIE, "attacker-chosen", domain="localhost")
    login(other, new_token("emp"))
    ok(cookie(other) not in (None, "attacker-chosen"), "로그인 전 쿠키 값을 이어 쓰지 않고 새 세션")
    first = cookie(other)
    login(other, new_token("emp"))
    ok(cookie(other) != first and q("SELECT revoked_at IS NOT NULL FROM sessions WHERE token_hash = ?",
                                    hashlib.sha256(first.encode()).hexdigest()) == [(1,)], "로그인할 때마다 새 세션, 앞 세션은 끊김")

    ok(cl.get("/api/me", base_url=BASE).json["is_hr"] is True, "HR 으로 지정된 사람의 세션은 HR")
    x("UPDATE users SET portal_enabled = 0 WHERE id = 3")
    ok(emp.get("/api/me", base_url=BASE).status_code == 401, "포털 사용을 끄면 바로 401")
    x("UPDATE users SET portal_enabled = 1, is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = 3")
    ok(emp.get("/api/me", base_url=BASE).status_code == 401, "비활성화하면 바로 401")
    x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = 3")
    ok(emp.get("/api/me", base_url=BASE).status_code == 200, "(다시 켜면 다시 됨 — 이 검사용)")

    now = datetime.now(timezone.utc)
    iso = lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")  # noqa: E731
    h = hashlib.sha256(cookie(emp).encode()).hexdigest()
    x("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", iso(now - timedelta(hours=4, minutes=1)), h)
    ok(emp.get("/api/me", base_url=BASE).status_code == 401, "마지막 사용 후 4시간이 지나면 401")
    x("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?", iso(now), iso(now - timedelta(seconds=1)), h)
    ok(emp.get("/api/me", base_url=BASE).status_code == 401, "발급 후 12시간(상한)이 지나면 401")
    ok(round(q("SELECT (julianday(expires_at) - julianday(created_at)) * 24 FROM sessions WHERE token_hash = ?",
               hashlib.sha256(cookie(other).encode()).hexdigest())[0][0], 6) == 12, "상한은 발급 후 12시간")

    anon = app.test_client()
    ok(anon.get("/api/health", base_url=BASE).status_code == 200, "/api/health 는 세션 없이 열림")
    for path in ("/api/rules", "/api/rules/search?q=x", "/api/rules/faq", "/api/rules/hr", "/api/rules/hr/file", "/api/me"):
        ok(anon.get(path, base_url=BASE).status_code == 401, f"세션 없으면 401: GET {path}")
    for path in ("/api/calculate", "/api/search-places", "/api/export/excel", "/api/export/pdf",
                 "/api/export/application-form", "/api/voucher", "/api/export/voucher"):
        ok(anon.post(path, base_url=BASE, headers=ORIGIN, json={}).status_code == 401, f"세션 없으면 401: POST {path}")
    ok(other.get("/api/rules", base_url=BASE).status_code == 200, "세션이 있으면 규정 검색이 열림")
    ok(other.post("/api/calculate", base_url=BASE, headers=ORIGIN, json={}).status_code == 400,
       "세션이 있으면 경비 산출기까지 감 (빈 입력이라 400)")

    out = other.post("/api/auth/logout", base_url=BASE, headers=ORIGIN)
    ok(out.status_code == 200 and other.get("/api/me", base_url=BASE).status_code == 401, "로그아웃하면 그 세션은 끝")

    print("11. 요청 빈도 제한")
    real_now = auth._now
    fixed = real_now().replace(second=5)  # 1분 창 경계에 걸려 숫자가 나뉘지 않게 시각을 고정한다
    auth._now = lambda: fixed
    codes = [message_of(login(app.test_client(), "bad", ip="203.0.113.7")) for _ in range(31)]
    ok(codes[:30] == ["bad"] * 30 and codes[30] == "busy", "같은 접속자 주소는 1분에 30번까지, 31번째는 429")
    ok(message_of(login(app.test_client(), "bad", ip="203.0.113.8")) == "bad", "다른 접속자 주소는 막히지 않음 (여러 직원이 동시에)")
    ok(q("SELECT hits FROM sso_rate_hits WHERE ip = '203.0.113.7'") == [(31,)], "횟수는 SQLite 에 셈 (서버 프로그램 2개가 같은 숫자)")
    auth._now = real_now
    ok(app.test_client().get("/api/auth/message?m=busy").status_code == 429, "안내 화면은 429")
    ok(conf.count("proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;") == 2, "nginx 가 CF-Connecting-IP 를 api 로 넘김")

    print("12. 다른 사이트에서 몰래 보내는 요청 막기")
    s = app.test_client()
    login(s, new_token("lead"))
    ok(s.post("/api/auth/logout", base_url=BASE).status_code == 403, "Origin 이 없으면 거부")
    ok(s.post("/api/auth/logout", base_url=BASE, headers={"Origin": "https://evil.example"}).status_code == 403, "다른 사이트 Origin 은 거부")
    ok(s.post("/api/calculate", base_url=BASE, headers={"Origin": "https://evil.example"}, json={}).status_code == 403,
       "기존 POST(경비 산출기)도 같은 검사")
    ok(s.get("/api/auth/logout", base_url=BASE).status_code == 405, "로그아웃은 POST 전용")
    ok(s.post("/api/auth/logout", base_url=BASE, headers=ORIGIN).status_code == 200, "포털 화면에서 온 요청은 통과")

    print("5단계. 구성원 설정 저장 (HR)")
    hr = app.test_client()
    login(hr, new_token("hr"))
    worker = app.test_client()
    login(worker, new_token("emp"))
    ok(worker.get("/api/me", base_url=BASE).json.get("writes_kpi") is True, "/api/me 에 KPI 작성 여부 (현장직 구분)")
    ok(worker.get("/api/members", base_url=BASE).status_code == 403, "HR 이 아니면 명단을 못 봄")
    lst = hr.get("/api/members", base_url=BASE)
    rows = {m["id"]: m for m in lst.json["members"]}
    ok(lst.status_code == 200 and rows[1]["hr"] and rows[1]["me"] and rows[2]["lead"] == "TEAM"
       and rows[2]["division"] == "가상본부" and rows[2]["team"] == "가상팀", "HR 은 명단을 봄 (직책·HR 권한·본부·팀)")
    ok(rows[4]["isNew"] and not rows[3]["isNew"] and not rows[5]["active"], "새로 들어온 사람 · 비활성 표시")

    def save(client, changes, headers=ORIGIN):
        return client.post("/api/members", base_url=BASE, headers=headers, json={"changes": changes})

    ok(save(hr, {"4": {"use": True}}, headers={}).status_code == 403, "Origin 없는 저장은 거부")
    r = save(hr, {"3": {"lead": "TEAM"}})
    ok(r.status_code == 400 and any("팀장" in i for i in r.json["issues"]), "같은 팀에 팀장 둘 → 저장 안 됨, 이유를 알려 줌")
    r = save(hr, {"1": {"hr": False}})
    ok(r.status_code == 400 and any("HR" in i for i in r.json["issues"]), "마지막 HR 의 HR 권한을 끌 수 없음")
    for bad in ({"999": {"use": True}}, {"3": {"foo": True}}, {"3": {"use": "yes"}}, {"3": {"lead": "BOSS"}}):
        ok(save(hr, bad).status_code == 400, f"이상한 입력은 거부: {bad}")
    ok(q("SELECT COUNT(*) FROM audit_logs WHERE action = 'MEMBER_UPDATED'") == [(0,)], "거부된 저장은 아무것도 남기지 않음")

    r = save(hr, {"4": {"use": True}, "3": {"kpi": False}})
    ok(r.status_code == 200 and r.json["saved"] == 2
       and q("SELECT portal_enabled FROM users WHERE id = 4") == [(1,)] and q("SELECT writes_kpi FROM users WHERE id = 3") == [(0,)],
       "저장하면 DB 에 남음 (새로 고쳐도 그대로)")
    ok(q("SELECT before_data, after_data, actor_user_id FROM audit_logs WHERE action = 'MEMBER_UPDATED' AND entity_id = 4")
       == [('{"portal_enabled": false}', '{"portal_enabled": true}', 1)], "누가 무엇을 바꿨는지 감사로그")
    ok(save(hr, {"4": {"lead": "CEO"}}).status_code == 200
       and q("SELECT writes_kpi, in_evaluation, has_contract FROM users WHERE id = 4") == [(0, 0, 0)]
       and q("SELECT role, granted_by FROM user_roles WHERE user_id = 4") == [("CEO", 1)],
       "대표이사로 바꾸면 KPI·평가·계약서가 함께 꺼짐 (지정한 HR 이 남음)")
    ok(save(hr, {"4": {"lead": "", "kpi": True, "ev": True, "ct": True}}).status_code == 200
       and q("SELECT COUNT(*) FROM user_roles WHERE user_id = 4") == [(0,)], "직책을 없음으로 되돌림")
    ok(save(hr, {"2": {"hr": True}}).status_code == 200
       and sorted(r[0] for r in q("SELECT role FROM user_roles WHERE user_id = 2")) == ["HR_ADMIN", "TEAM_LEADER"],
       "팀장에게 HR 권한을 더하면 HR 팀장")
    ok(save(hr, {"3": {"use": False}}).status_code == 200 and worker.get("/api/me", base_url=BASE).status_code == 401,
       "포털 사용을 끄면 그 사람의 로그인이 바로 끊김")
    save(hr, {"3": {"use": True}})

    def active(uid, on):
        return hr.post(f"/api/members/{uid}/active", base_url=BASE, headers=ORIGIN, json={"active": on})

    ok(active(3, False).status_code == 200 and q("SELECT is_active, deactivated_at IS NOT NULL FROM users WHERE id = 3") == [(0, 1)],
       "비활성화 (날짜가 남음)")
    ok(active(3, True).status_code == 200 and q("SELECT is_active, deactivated_at FROM users WHERE id = 3") == [(1, None)],
       "다시 켜기")
    save(hr, {"2": {"hr": False}})
    r = active(1, False)
    ok(r.status_code == 400 and q("SELECT is_active FROM users WHERE id = 1") == [(1,)], "마지막 HR 은 비활성화할 수 없음")
    leader = app.test_client()
    login(leader, new_token("lead"))
    ok(active(999, False).status_code == 404, "명단에 없는 사람은 404")
    ok(leader.post("/api/members/3/active", base_url=BASE, headers=ORIGIN, json={"active": False}).status_code == 403
       and save(leader, {"3": {"use": False}}).status_code == 403, "로그인한 팀장이라도 HR 이 아니면 비활성화·저장 못 함")

    acts = {r[0] for r in q("SELECT action FROM audit_logs")}
    ok({"LOGIN", "HR_BOOTSTRAP"} <= acts, "감사로그 LOGIN 이 남음")
    ok(q("SELECT actor_position, actor_is_hr FROM audit_logs WHERE action = 'LOGIN' AND actor_user_id = 2 LIMIT 1")
       == [("TEAM_LEADER", 0)], "LOGIN 감사로그에 직책·HR 권한 두 칸")

    print(f"로그인 검사 {passed}건 전부 통과")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        SRV.shutdown()
