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
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
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


IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"
GALAXY = "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 SamsungBrowser/26.0 Mobile Safari/537.36"


def check_phone(hr, save) -> None:
    """휴대폰 등록 (설계 13.1): QR 1회용 값(3분) → 휴대폰 세션(마지막 사용 후 30일) · 한 사람 한 대 · 해제."""
    print("13.1 휴대폰 등록")
    seen: list[str] = []
    real = auth._qr_rows
    auth._qr_rows = lambda text: (seen.append(text), real(text))[1]  # QR 에 담긴 주소를 엿본다 (화면은 그림만 받는다)

    def iso(d: datetime) -> str:
        return d.strftime("%Y-%m-%dT%H:%M:%SZ")

    def sha(v: str) -> str:
        return hashlib.sha256(v.encode()).hexdigest()

    def new_code(client):
        r = client.post("/api/phone/code", base_url=BASE, headers=ORIGIN)
        return r, (seen[-1].split("#d=")[-1] if r.status_code == 200 else "")

    def reg(client, code, ua=IPHONE, headers=ORIGIN, ip=None):
        _seq[0] += 1
        h = {**headers, "User-Agent": ua, "CF-Connecting-IP": ip or f"192.0.2.{_seq[0] % 250}"}
        return client.post("/api/phone/register", base_url=BASE, headers=h, json={"code": code})

    def me(client):
        return client.get("/api/me", base_url=BASE)

    pc = app.test_client()
    login(pc, new_token("emp"))
    r, code = new_code(pc)
    rows = r.json.get("qr", "").split("|") if r.status_code == 200 else []
    ok(r.status_code == 200 and r.json["seconds"] == 180 and len(rows) >= 21 and all(len(x) == len(rows) for x in rows)
       and seen[-1] == f"http://localhost/#d={code}", "PC 에서 QR: 3분짜리 1회용 값, 주소는 # 뒤 (서버 기록에 안 남음)")
    ok(r.headers.get("Cache-Control") == "no-store"
       and q("SELECT COUNT(*) FROM phone_codes WHERE code_hash = ?", sha(code)) == [(1,)]
       and not q("SELECT 1 FROM phone_codes WHERE code_hash = ?", code), "1회용 값은 해시만 저장, 응답은 저장 안 함")

    phone = app.test_client()
    ok(reg(phone, code, headers={}).status_code == 403, "Origin 없는 등록은 거부")
    ok(reg(phone, code, ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/141.0 Safari/537.36").status_code == 400
       and q("SELECT used_at FROM phone_codes WHERE code_hash = ?", sha(code)) == [(None,)],
       "PC 브라우저로는 등록 안 됨 (값은 남아 휴대폰이 쓸 수 있음)")
    ok(reg(phone, "wrong").status_code == 410 and me(phone).status_code == 401, "틀린 값은 410, 로그인 안 됨")
    r = reg(phone, code)
    sc = r.headers.get("Set-Cookie", "")
    ok(r.status_code == 200 and "Max-Age=2592000" in sc and all(f in sc for f in ("Secure", "HttpOnly", "SameSite=Lax")),
       "QR 값으로 휴대폰 등록: 30일 쿠키 (Secure · HttpOnly · SameSite=Lax)")
    ok(me(phone).json.get("kind") == "phone" and me(pc).json.get("kind") == "pc", "/api/me 가 세션 종류를 알려 줌 (휴대폰 · PC)")
    ok(reg(app.test_client(), code).status_code == 410, "같은 QR 값은 한 번만")
    got = pc.get("/api/phone", base_url=BASE).json
    ok(got["phone"]["device"] == "아이폰" and got["this"] is False and phone.get("/api/phone", base_url=BASE).json["this"] is True,
       "등록된 휴대폰 이름(아이폰) · 지금 기기가 휴대폰인지")
    ok(new_code(phone)[0].status_code == 403, "휴대폰에서는 QR 을 띄울 수 없음 (PC 에서 등록)")

    h, now = sha(cookie(phone)), datetime.now(timezone.utc)
    x("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", iso(now - timedelta(hours=5)), h)
    ok(me(phone).status_code == 200, "휴대폰은 4시간 넘게 안 써도 그대로 (PC 규칙 아님)")
    x("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?",
      iso(now - timedelta(days=29)), iso(now + timedelta(days=1)), h)
    r = me(phone)
    left = q("SELECT (julianday(expires_at) - julianday('now')) FROM sessions WHERE token_hash = ?", h)[0][0]
    ok(r.status_code == 200 and 29.9 < left <= 30.0 and "Max-Age=2592000" in r.headers.get("Set-Cookie", ""),
       "쓸 때마다 30일로 연장 (만료일 · 쿠키 둘 다)")
    x("UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", iso(now - timedelta(days=31)), h)
    ok(me(phone).status_code == 401, "30일 동안 안 쓰면 끝")

    galaxy, other = app.test_client(), app.test_client()
    reg(galaxy, new_code(pc)[1], ua=GALAXY)
    ok(me(galaxy).json.get("kind") == "phone" and pc.get("/api/phone", base_url=BASE).json["phone"]["device"] == "갤럭시",
       "갤럭시 등록")
    reg(other, new_code(pc)[1])
    ok(me(galaxy).status_code == 401 and me(other).status_code == 200, "한 사람에 한 대 — 새로 등록하면 앞 휴대폰은 끊김")

    _, a = new_code(pc)
    _, b = new_code(pc)
    ok(reg(app.test_client(), a).status_code == 410, "QR 을 새로 띄우면 앞의 값은 못 씀")
    x("UPDATE phone_codes SET expires_at = '2000-01-01T00:00:00Z' WHERE code_hash = ?", sha(b))
    ok(reg(app.test_client(), b).status_code == 410, "3분이 지난 값은 못 씀")

    ok(any(m["id"] == 3 and m["phone"].startswith("아이폰 · 마지막 사용") for m in hr.get("/api/members", base_url=BASE).json["members"]),
       "구성원 설정 명단에 등록된 휴대폰")
    ok(pc.post("/api/members/3/phone/revoke", base_url=BASE, headers=ORIGIN).status_code == 403, "HR 이 아니면 남의 휴대폰을 못 끊음")
    ok(hr.post("/api/members/3/phone/revoke", base_url=BASE, headers=ORIGIN).status_code == 200 and me(other).status_code == 401,
       "HR 이 해제하면 그 휴대폰은 바로 끊김")

    mine = app.test_client()
    reg(mine, new_code(pc)[1])
    ok(pc.post("/api/phone/revoke", base_url=BASE, headers=ORIGIN).status_code == 200 and me(mine).status_code == 401
       and pc.get("/api/phone", base_url=BASE).json["phone"] is None and me(pc).status_code == 200,
       "본인이 해제 — 휴대폰만 끊기고 PC 는 그대로")

    # 휴대폰 브라우저로 하이웍스 메뉴를 눌러 들어온 창(kind pc): 받은 값으로 그 자리에서 이 휴대폰을 등록한다 (PC 없는 현장 근무자)
    selfp = app.test_client()
    login(selfp, new_token("emp"))
    was = cookie(selfp)
    r, code = new_code(selfp)
    ok(r.json.get("code") == code and reg(selfp, code).status_code == 200 and me(selfp).json.get("kind") == "phone"
       and q("SELECT revoked_at IS NOT NULL FROM sessions WHERE token_hash = ?", sha(was)) == [(1,)] and me(pc).status_code == 200,
       "휴대폰으로 하이웍스에서 들어온 창이 QR 없이 등록 — 그 창의 하이웍스 세션은 휴대폰 세션으로 바뀌고 PC 는 그대로")
    pc.post("/api/phone/revoke", base_url=BASE, headers=ORIGIN)  # 아래 검사는 등록된 휴대폰이 없는 상태에서 시작

    late = app.test_client()
    _, c = new_code(pc)
    save(hr, {"3": {"use": False}})
    ok(reg(late, c).status_code == 410, "포털 사용이 꺼진 사람의 값은 못 씀")
    save(hr, {"3": {"use": True}})

    real_now, fixed = auth._now, auth._now().replace(second=5)  # 1분 창 경계에 걸려 숫자가 나뉘지 않게 시각을 고정한다 (11절과 같음)
    auth._now = lambda: fixed
    codes = [reg(app.test_client(), "x", ip="203.0.113.99").status_code for _ in range(31)]
    auth._now = real_now
    ok(codes[:30] == [410] * 30 and codes[30] == 429, "휴대폰 등록도 같은 접속자 주소 1분 30번까지")
    acts = {r[0] for r in q("SELECT action FROM audit_logs")}
    ok({"PHONE_REGISTERED", "PHONE_REVOKED"} <= acts, "감사로그 PHONE_REGISTERED · PHONE_REVOKED")
    auth._qr_rows = real


def check_org(hr, save) -> None:
    """조직도 실제 명단 (2026-10-19): 누구나 보는 명단 · 비공개 휴대폰 · 조직도에서 빼기 · 비상연락망 값."""
    print("조직도")
    m1, m2, m3 = ("010-" + s for s in ("1111-0002", "2222-0002", "3333-0003"))  # 가상 번호 — 파일에 완성된 번호를 남기지 않는다 (CI 개인정보 검사)
    emp, lead = app.test_client(), app.test_client()
    login(emp, new_token("emp"))
    login(lead, new_token("lead"))

    def org(client):
        return client.get("/api/org", base_url=BASE)

    def person(client, uid):
        return next((m for m in org(client).json["members"] if m["id"] == uid), None)

    ok(app.test_client().get("/api/org", base_url=BASE).status_code == 401, "로그인 없이는 조직도 명단 401")
    r = org(emp)
    ok(r.status_code == 200 and {"divisions", "members"} <= set(r.json)
       and any(d["name"] == "가상본부" and "가상팀" in d["teams"] for d in r.json["divisions"]), "조직도 명단 (본부 · 팀은 하이웍스 부서 그대로)")
    p2 = person(emp, 2)
    ok(p2 and p2["division"] == "가상본부" and p2["team"] == "가상팀" and p2["lead"] == "TEAM", "사람마다 본부 · 팀 · 직책")
    ok(person(emp, 5) is None, "비활성인 사람은 조직도에 없음")
    ok(set(p2) == {"id", "name", "title", "division", "team", "lead", "ext", "email", "mobile", "mobilePublic", "mailNo", "me"},
       "조직도 명단은 필요한 칸만 (포털 사용 · HR 권한 · 사번 등은 없음)")

    x("UPDATE users SET mobile = ?, phone = '02-000-0002', email = 'u2@example.invalid' WHERE id = 2", m1)
    ok(person(emp, 2)["mobile"] == m1 and person(emp, 2)["ext"] == "02-000-0002", "하이웍스 휴대폰 · 회사 전화")
    x("UPDATE users SET hr_mobile = ?, ext = '1002', hr_email = 'hr2@example.invalid' WHERE id = 2", m2)
    p2 = person(emp, 2)
    ok(p2["mobile"] == m2 and p2["ext"] == "1002" and p2["email"] == "hr2@example.invalid",
       "비상연락망 값이 하이웍스 값보다 먼저 (휴대폰 · 내선 · 메일)")

    x("UPDATE users SET mobile_public = 0 WHERE id = 2")
    ok(person(emp, 2)["mobile"] is None and person(emp, 2)["mobilePublic"] is False, "비공개 휴대폰은 다른 직원에게 안 보임")
    ok(person(lead, 2)["mobile"] == m2 and person(lead, 2)["me"] is True, "본인에게는 보임")
    ok(person(hr, 2)["mobile"] == m2, "HR 에게는 보임 (비상연락망 관리)")
    ok(lead.post("/api/me/privacy", base_url=BASE, headers=ORIGIN, json={"mobile_public": "yes"}).status_code == 400
       and lead.post("/api/me/privacy", base_url=BASE, json={"mobile_public": True}).status_code == 403, "공개 설정: 모양 · Origin 검사")
    ok(lead.post("/api/me/privacy", base_url=BASE, headers=ORIGIN, json={"mobile_public": True}).status_code == 200
       and person(emp, 2)["mobile"] == m2, "본인이 다시 공개하면 보임")

    mail = lambda c, pairs: c.post("/api/members/mail-nos", base_url=BASE, headers=ORIGIN, json={"pairs": pairs})  # noqa: E731
    x("UPDATE users SET hiworks_user_no = '777' WHERE id = 4")
    r = mail(hr, {"777": "33094", "999999": "1"})
    ok(r.status_code == 200 and r.json["saved"] == 1 and r.json["unknown"] == 1 and person(emp, 4)["mailNo"] == "33094" and person(emp, 3)["mailNo"] == "",
       "메일 받는 사람 채우기용 회사 번호 (HR 이 넣은 짝 · 명단에 없는 번호는 건너뜀)")
    ok(mail(emp, {"777": "1"}).status_code == 403 and person(emp, 4)["mailNo"] == "33094", "회사 번호는 HR 만")
    for bad in ({}, {"777": "0123"}, {"777": 33094}, {"HW-4": "1"}, {"777": "1" * 13}, ["777"]):
        ok(mail(hr, bad).status_code == 400 and person(emp, 4)["mailNo"] == "33094", f"틀린 번호 짝은 저장 안 함 ({str(bad)[:16]})")
    ok(hr.post("/api/members/mail-nos", base_url=BASE, json={"pairs": {"777": "2"}}).status_code == 403, "포털 화면에서만 (Origin)")
    x("UPDATE users SET hiworks_user_no = 'HW-4' WHERE id = 4")
    ok(emp.post("/api/members/4/org", base_url=BASE, headers=ORIGIN, json={"hidden": True}).status_code == 403, "HR 이 아니면 조직도에서 못 뺌")
    ok(hr.post("/api/members/4/org", base_url=BASE, headers=ORIGIN, json={"hidden": True}).status_code == 200
       and person(emp, 4) is None and q("SELECT is_active, org_hidden FROM users WHERE id = 4") == [(1, 1)],
       "HR 이 조직도에서 빼면 명단에서 빠짐 (DB 에는 그대로)")
    ok(next(m for m in hr.get("/api/members", base_url=BASE).json["members"] if m["id"] == 4)["orgHidden"] is True,
       "구성원 설정에 '조직도에서 뺌' 표시")
    ok(hr.post("/api/members/4/org", base_url=BASE, headers=ORIGIN, json={"hidden": False}).status_code == 200
       and person(emp, 4) is not None, "다시 보이기")
    ok(hr.post("/api/members/999/org", base_url=BASE, headers=ORIGIN, json={"hidden": True}).status_code == 404, "없는 사람은 404")

    def contacts(client, rows, headers=ORIGIN):
        return client.post("/api/members/contacts", base_url=BASE, headers=headers, json={"rows": rows})

    good = [{"id": 3, "title": "프로", "ext": "1003", "mobile": m3, "email": "u3hr@example.invalid"}, {"id": 4, "title": "리더"}]
    ok(contacts(emp, good).status_code == 403 and contacts(hr, good, headers={}).status_code == 403, "비상연락망 저장은 HR · 포털 화면에서만")
    before = q("SELECT title, ext, hr_mobile, hr_email FROM users WHERE id = 3")
    for bad, why in (([{"id": 3, "email": "nope"}], "메일 모양"), ([{"id": 3, "mobile": "010-abc"}], "전화 모양"),
                     ([{"id": 999, "title": "프로"}], "없는 사람"), ([{"id": 3, "title": " "}], "빈 값"),
                     ([{"id": 3, "portal_enabled": True}], "다른 칸"), ([{"id": 3, "title": "가" * 31}], "너무 김"),
                     ([good[0], {"id": 3, "email": "bad"}], "한 줄이라도 틀리면")):
        ok(contacts(hr, bad).status_code == 400 and q("SELECT title, ext, hr_mobile, hr_email FROM users WHERE id = 3") == before,
           f"틀린 값은 아무것도 저장 안 함 ({why})")
    r = contacts(hr, good)
    ok(r.status_code == 200 and r.json["saved"] == 2
       and q("SELECT title, ext, hr_mobile, hr_email FROM users WHERE id = 3") == [("프로", "1003", m3, "u3hr@example.invalid")]
       and q("SELECT title, ext FROM users WHERE id = 4") == [("리더", None)], "비상연락망 값 저장 (보낸 칸만)")
    p3 = person(emp, 3)
    ok(p3["title"] == "프로" and p3["mobile"] == m3 and p3["email"] == "u3hr@example.invalid", "조직도에 바로 보임")
    audit = q("SELECT after_data FROM audit_logs WHERE action = 'MEMBER_CONTACTS' ORDER BY id DESC LIMIT 1")[0][0]
    ok('"people": 2' in audit and m3 not in audit and "u3hr" not in audit, "감사로그에는 사람 수와 칸 이름만 (값은 남기지 않음)")

    print("입사일")
    ok(contacts(hr, [{"id": 3, "hire_date": "2021-03-02"}]).status_code == 200
       and q("SELECT hire_date FROM users WHERE id = 3") == [("2021-03-02",)], "입사일 저장 (HR)")
    ok(emp.get("/api/me", base_url=BASE).json["hire_date"] == "2021-03-02", "/api/me 에 내 입사일 (홈 배너의 재직 기간)")
    ok(next(m for m in hr.get("/api/members", base_url=BASE).json["members"] if m["id"] == 3)["hireDate"] == "2021-03-02",
       "구성원 설정에 입사일")
    for bad in ("2021-13-01", "2021-3-2", "1900-01-01", "2999-01-01", "어제"):
        ok(contacts(hr, [{"id": 3, "hire_date": bad}]).status_code == 400 and q("SELECT hire_date FROM users WHERE id = 3") == [("2021-03-02",)],
           f"틀린 입사일은 저장 안 함 ({bad})")
    ok(contacts(emp, [{"id": 3, "hire_date": "2020-01-01"}]).status_code == 403, "입사일은 HR 만")
    ok("hire_date" not in person(emp, 3) and "hireDate" not in person(emp, 3), "조직도 명단에는 입사일이 없음")


def check_led_dept(hr) -> None:
    """맡은 부서 (2026-10-20): 하이웍스에서 본부장이 '임원' 부서에 있어도 맡은 본부를 HR 이 정한다."""
    print("맡은 부서")
    # 앞 검사가 바꿔 놓은 것에 기대지 않는다 — 필요한 부서를 더하고 3 · 4 번 사람의 직책을 비운다
    for dept in ((3, "D3", "가상본부2", "DIVISION", None), (6, "D6", "가상팀2", "TEAM", 3), (4, "D4", "가상지원팀", "DIVISION", None),
                 (5, "D5", "임원", "DIVISION", None)):
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (?, ?, ?, ?, ?)", *dept)
    orig = q("SELECT id, department_id, writes_kpi, in_evaluation, has_contract FROM users WHERE id IN (3, 4) ORDER BY id")
    x("DELETE FROM user_roles WHERE user_id IN (3, 4) AND role IN ('TEAM_LEADER', 'DIVISION_HEAD', 'CEO')")
    x("UPDATE users SET led_department_id = NULL, in_evaluation = 1, has_contract = 1 WHERE id IN (3, 4)")
    x("UPDATE users SET department_id = 2 WHERE id = 3")
    x("UPDATE users SET department_id = 5 WHERE id = 4")  # 4 는 '임원' 부서 소속
    lead = app.test_client()
    login(lead, new_token("lead"))

    def save(client, changes, headers=ORIGIN):
        return client.post("/api/members", base_url=BASE, headers=headers, json={"changes": changes})

    def listing():
        return hr.get("/api/members", base_url=BASE).json

    def row(uid):
        return next(m for m in listing()["members"] if m["id"] == uid)

    def led(uid):
        return q("SELECT led_department_id FROM users WHERE id = ?", uid)

    def actor(uid):
        c = connect(os.environ["HR_DB_PATH"])
        try:
            return auth.actor_of(c, uid)
        finally:
            c.close()

    kinds = {d["id"]: d for d in listing()["depts"]}
    ok(kinds[2] == {"id": 2, "name": "가상팀", "parent": 1, "kind": "TEAM"} and kinds[1]["kind"] == kinds[3]["kind"] == "DIVISION"
       and kinds[4]["kind"] == "DIRECT" and kinds[5]["kind"] == "NOCOL", "/api/members 에 부서 목록 (본부 · 팀 · 직속 팀 · 임원)")
    ok(row(4)["ledDept"] is None and row(4)["dept"] == 5, "처음엔 맡은 부서가 없음 (null) · 본인 소속 부서 번호가 같이 옴")

    r = save(hr, {"4": {"lead": "DIVISION", "ledDept": 1}})
    ok(r.status_code == 200 and led(4) == [(1,)] and row(4)["ledDept"] == 1 and row(4)["lead"] == "DIVISION",
       "본부장으로 지정하며 맡은 본부를 고름 → 저장되고 다시 읽어도 남음 (소속은 '임원' 그대로)")
    ok(q("SELECT department_id, in_evaluation, has_contract FROM users WHERE id = 4") == [(5, 0, 0)], "본부장은 평가 · 계약서가 꺼짐 (기존 규칙 그대로)")
    audit = q("SELECT before_data, after_data FROM audit_logs WHERE action = 'MEMBER_UPDATED' AND entity_id = 4 ORDER BY id DESC LIMIT 1")[0]
    ok('"led_department_id": null' in audit[0] and '"led_department_id": 1' in audit[1], "감사로그에 맡은 부서 id (바뀌기 전 · 후)")

    r = save(hr, {"4": {"ledDept": 2}})
    ok(r.status_code == 400 and any("맡은 부서" in i for i in r.json["issues"]) and led(4) == [(1,)], "본부장의 맡은 부서가 팀이면 400 · 안 바뀜")
    ok(all(save(hr, {"4": {"ledDept": d}}).status_code == 400 for d in (4, 5, 999)) and led(4) == [(1,)],
       "직속 팀 · 임원 · 없는 부서도 본부장은 400")
    for bad in ("1", True, 1.5, [1], -1, 0):
        ok(save(hr, {"4": {"ledDept": bad}}).status_code == 400 and led(4) == [(1,)], f"맡은 부서 모양이 틀리면 400 ({bad!r})")

    ok(save(hr, {"3": {"lead": "DIVISION"}}).status_code == 400 and led(3) == [(None,)],
       "소속(팀)이 본부가 아닌데 맡은 부서를 안 고르면 본부장이 될 수 없음")
    r = save(hr, {"3": {"lead": "DIVISION", "ledDept": 1}})
    ok(r.status_code == 400 and any("본부에 한 명" in i for i in r.json["issues"]) and led(3) == [(None,)],
       "한 본부에 본부장 둘 → 맡은 부서 기준으로 400")
    ok(save(hr, {"3": {"lead": "DIVISION", "ledDept": 3}}).status_code == 200 and led(3) == [(3,)], "소속(팀)과 다른 본부의 본부장은 됨")
    ok(save(hr, {"3": {"ledDept": 1}}).status_code == 400 and led(3) == [(3,)], "이미 본부장이 있는 본부로 바꿔 맡으면 400")
    ok(save(hr, {"3": {"lead": ""}}).status_code == 200 and led(3) == [(None,)], "정리: 직책을 없애면 맡은 부서도 비워짐")

    r = save(hr, {"4": {"lead": "TEAM", "ledDept": 2}})
    ok(r.status_code == 400 and any("팀에 한 명" in i for i in r.json["issues"]), "팀장이 이미 있는 팀(2번 사람의 팀)을 맡으면 400")
    ok(save(hr, {"4": {"lead": "TEAM", "ledDept": 6}}).status_code == 200 and led(4) == [(6,)] and row(4)["lead"] == "TEAM",
       "임원 부서 사람을 팀장으로 바꾸고 다른 팀을 맡김")
    ok(save(hr, {"4": {"ledDept": 4}}).status_code == 200 and led(4) == [(4,)], "팀장은 직속 팀도 맡을 수 있음")
    ok(save(hr, {"4": {"ledDept": 1}}).status_code == 400 and save(hr, {"4": {"ledDept": 5}}).status_code == 400 and led(4) == [(4,)],
       "팀장의 맡은 부서가 본부 · 임원이면 400 · 안 바뀜")

    ok(save(hr, {"4": {"lead": "DIVISION", "ledDept": 1}}).status_code == 200, "다시 본부장")
    ok(actor(4).led_division_id == 1 and actor(4).led_team_id is None, "actor_of: 본부장이 이끄는 본부는 맡은 부서 (소속이 '임원'이어도)")
    save(hr, {"4": {"lead": "TEAM", "ledDept": 4}})
    ok(actor(4).led_team_id == 4 and actor(4).led_division_id is None, "actor_of: 팀장이 이끄는 팀도 맡은 부서")
    ok(save(hr, {"4": {"lead": ""}}).status_code == 200 and led(4) == [(None,)] and row(4)["ledDept"] is None and row(4)["lead"] == "",
       "직책을 없애면 맡은 부서도 서버가 비움")
    ok(save(hr, {"4": {"ledDept": 6}}).status_code == 200 and led(4) == [(None,)], "직책이 없는 사람에게 맡은 부서를 보내도 비어 있음")
    ok(save(hr, {"4": {"lead": "TEAM", "ledDept": None}}).status_code == 400 and led(4) == [(None,)],
       "팀장으로 지정하며 맡은 부서를 비우면 소속('임원')이라 400")

    ok(save(hr, {"3": {"lead": "TEAM", "ledDept": 6}}).status_code == 200 and led(3) == [(6,)], "소속과 다른 팀의 팀장")
    ok(save(hr, {"3": {"ledDept": None}}).status_code == 400 and led(3) == [(6,)],
       "맡은 부서를 비우면 소속(2번 팀, 팀장이 있음)으로 돌아가므로 한 팀 한 명 규칙에 걸림")
    x("UPDATE users SET department_id = 6 WHERE id = 3")
    ok(save(hr, {"3": {"ledDept": 6}}).status_code == 200 and led(3) == [(None,)] and row(3)["ledDept"] is None,
       "맡은 부서를 소속과 같게 고르면 비움으로 저장 (나중에 소속이 옮겨져도 따라감)")
    ok(save(hr, {"3": {"lead": ""}}).status_code == 200, "정리")
    x("UPDATE users SET department_id = 2 WHERE id = 3")

    ok(save(lead, {"4": {"lead": "DIVISION", "ledDept": 1}}).status_code == 403 and led(4) == [(None,)], "HR 이 아니면 403")
    ok(lead.get("/api/members", base_url=BASE).status_code == 403, "HR 이 아니면 명단 · 부서 목록도 403")
    ok(save(hr, {"4": {"lead": "DIVISION", "ledDept": 1}}, headers={}).status_code == 403 and led(4) == [(None,)], "포털 화면에서만 (Origin)")

    # 옛 자료가 남의 저장을 막지 않는다: 소속이 '임원'인 본부장이 맡은 부서 없이 이미 있어도 다른 사람 저장은 된다
    x("INSERT INTO user_roles (user_id, role) VALUES (4, 'DIVISION_HEAD')")
    x("UPDATE users SET in_evaluation = 0, has_contract = 0 WHERE id = 4")
    ok(save(hr, {"3": {"ev": False}}).status_code == 200, "맡은 부서를 안 정한 옛 본부장이 있어도 다른 사람 저장은 됨")
    ok(save(hr, {"4": {"kpi": False}}).status_code == 200 and q("SELECT writes_kpi FROM users WHERE id = 4") == [(0,)],
       "그 옛 본부장 본인의 다른 칸 저장도 됨 (직책 · 맡은 부서를 안 바꿨으니 다시 묻지 않음)")

    # 뒷정리 — 뒤 검사가 알던 모습으로
    x("DELETE FROM user_roles WHERE user_id IN (3, 4) AND role IN ('TEAM_LEADER', 'DIVISION_HEAD')")
    x("UPDATE users SET led_department_id = NULL WHERE id IN (3, 4)")
    for uid, dept, kpi, ev, ct in orig:
        x("UPDATE users SET department_id = ?, writes_kpi = ?, in_evaluation = ?, has_contract = ? WHERE id = ?", dept, kpi, ev, ct, uid)


def check_news(hr) -> None:
    """홈 이번 주 소식 (2026-10-19): 생일(월-일) · 생일 알림 · 새 식구·입사 기념 · 경사·조사."""
    print("이번 주 소식 — 규칙 (날짜 고정)")
    d = auth.date
    t = d(2026, 10, 1)  # 목요일 → 생일은 10/1~10/7

    def kinds(people, today=t):
        return [(i["kind"], i["id"]) for i in auth.news_items(people, today)]

    ok(kinds([(1, "2026-09-01", None, 1), (2, "2026-08-31", None, 1), (3, "2026-10-01", None, 1), (4, "2026-10-02", None, 1)])
       == [("welcome", 1), ("welcome", 3)], "새 식구 = 입사 후 30일 (앞날 입사는 아직)")
    ok(kinds([(1, None, "10-01", 1), (2, None, "10-07", 1), (3, None, "09-30", 1), (4, None, "10-08", 1), (5, None, "10-03", 0)])
       == [("birthday", 1), ("birthday", 2)], "생일 = 오늘부터 7일 안(지난 날은 빠짐), 생일 알림을 끈 사람은 빠짐")
    ok(kinds([(1, None, "02-29", 1)], d(2027, 2, 28)) == [("birthday", 1)] and kinds([(1, None, "02-29", 1)], d(2028, 2, 27)) == [("birthday", 1)],
       "평년에는 2월 29일 생일을 2월 28일에 (윤년은 2월 29일)")
    ok(kinds([(1, None, "01-02", 1), (2, None, "12-31", 1)], d(2026, 12, 31)) == [("birthday", 2), ("birthday", 1)], "연말연초 7일 · 날짜순")
    ok(kinds([(1, "2021-10-20", None, 1), (2, "2022-10-01", None, 1), (3, "2021-09-20", None, 1), (4, "2016-10-05", None, 1), (5, "2025-10-30", None, 1)])
       == [("anniversary", 1), ("anniversary", 4), ("anniversary", 5)], "입사 N주년 = 이번 달, 1·3·5·10년 이후 5년마다")
    notes = [i["note"] for i in auth.news_items([(1, "2026-09-07", "03-05", 1), (2, "2021-10-20", None, 1)], d(2026, 10, 5))]
    ok(notes == ["9월 7일 입사", "입사 5주년 · 10월 20일"] and auth.news_items([(1, None, "10-05", 1)], d(2026, 10, 5))[0]["note"] == "10월 5일 생일",
       "보이는 글 (입사 · 생일 · 주년)")

    print("이번 주 소식 — 저장 · 화면")
    emp, lead = app.test_client(), app.test_client()
    login(emp, new_token("emp"))
    login(lead, new_token("lead"))
    today = auth._today()

    def news(client):
        return client.get("/api/news", base_url=BASE)

    def has(kind, uid, client=None):
        return any(i["kind"] == kind and i["id"] == uid for i in news(client or emp).json["items"])

    def contacts(rows):
        return hr.post("/api/members/contacts", base_url=BASE, headers=ORIGIN, json={"rows": rows})

    ok(app.test_client().get("/api/news", base_url=BASE).status_code == 401, "로그인 없이는 401")
    md = today.strftime("%m-%d")
    ok(contacts([{"id": 2, "birthday": md}, {"id": 4, "hire_date": (today - auth.timedelta(days=3)).isoformat()}]).status_code == 200
       and q("SELECT birthday FROM users WHERE id = 2") == [(md,)], "생일 저장 (HR · 월-일)")
    for bad in ("02-30", "13-01", "3-05", "1990-03-15", "0315"):
        ok(contacts([{"id": 2, "birthday": bad}]).status_code == 400 and q("SELECT birthday FROM users WHERE id = 2") == [(md,)],
           f"틀린 생일은 저장 안 함 ({bad})")
    ok(emp.post("/api/members/contacts", base_url=BASE, headers=ORIGIN, json={"rows": [{"id": 2, "birthday": "01-01"}]}).status_code == 403,
       "생일은 HR 만")
    ok(has("birthday", 2) and has("welcome", 4), "이번 주 소식에 생일 · 새 식구")
    items = news(emp).json["items"]
    ok(all(set(i) <= {"kind", "id", "note", "event"} for i in items) and md not in str(news(emp).json),
       "생일·입사일 원본은 보내지 않음 (보일 줄만)")
    x("UPDATE users SET birthday = ? WHERE id = 5", md)
    ok(not has("birthday", 5), "비활성인 사람은 안 나옴")
    hr.post("/api/members/2/org", base_url=BASE, headers=ORIGIN, json={"hidden": True})
    ok(not has("birthday", 2), "조직도에서 뺀 사람은 안 나옴")
    hr.post("/api/members/2/org", base_url=BASE, headers=ORIGIN, json={"hidden": False})

    me2 = next(m for m in lead.get("/api/org", base_url=BASE).json["members"] if m["id"] == 2)
    other = next(m for m in emp.get("/api/org", base_url=BASE).json["members"] if m["id"] == 2)
    ok(me2["birthday"] == md and me2["birthdayPublic"] is True and "birthday" not in other, "조직도: 생일은 본인 줄에만")
    m2 = next(m for m in hr.get("/api/members", base_url=BASE).json["members"] if m["id"] == 2)
    ok(m2["birthday"] == md and m2["birthdayPublic"] is True, "구성원 설정에 생일 · 생일 알림")

    def privacy(client, body, path="/api/me/privacy", headers=ORIGIN):
        return client.post(path, base_url=BASE, headers=headers, json=body)

    ok(privacy(lead, {"birthday_public": False}).status_code == 200 and not has("birthday", 2)
       and q("SELECT birthday_public, mobile_public FROM users WHERE id = 2") == [(0, 1)], "본인이 생일 알림을 끄면 빠짐 (휴대폰 공개는 그대로)")
    for bad in ({"birthday_public": "no"}, {"is_hr": True}, {}, [True]):
        ok(privacy(lead, bad).status_code == 400, f"공개 설정: 틀린 값 거부 ({bad})")
    ok(privacy(lead, {"birthday_public": True}).status_code == 200 and has("birthday", 2), "다시 켜면 나옴")
    ok(privacy(emp, {"birthday_public": False}, "/api/members/2/privacy").status_code == 403, "남의 생일 알림은 HR 만")
    ok(privacy(hr, {"mobile_public": False}, "/api/members/2/privacy").status_code == 400, "HR 은 생일 알림만 대신 끔 (휴대폰 공개는 본인)")
    ok(privacy(hr, {"birthday_public": False}, "/api/members/2/privacy", {}).status_code == 403, "포털 화면에서만 (Origin)")
    ok(privacy(hr, {"birthday_public": False}, "/api/members/2/privacy").status_code == 200 and not has("birthday", 2)
       and privacy(hr, {"birthday_public": False}, "/api/members/999/privacy").status_code == 404, "HR 이 대신 끔 · 없는 사람은 404")
    privacy(hr, {"birthday_public": True}, "/api/members/2/privacy")

    def post(client, body, headers=ORIGIN):
        return client.post("/api/news/events", base_url=BASE, headers=headers, json=body)

    good = {"user_id": 3, "kind": "joy", "label": " 결혼 ", "until": (today + auth.timedelta(days=7)).isoformat()}
    ok(post(emp, good).status_code == 403 and post(hr, good, {}).status_code == 403, "경사·조사 올리기는 HR · 포털 화면에서만")
    far, past = (today + auth.timedelta(days=400)).isoformat(), (today - auth.timedelta(days=1)).isoformat()
    for bad, why in (({**good, "kind": "party"}, "종류"), ({**good, "label": "  "}, "빈 내용"), ({**good, "label": "가" * 31}, "너무 김"),
                     ({**good, "until": past}, "지난 날"), ({**good, "until": far}, "1년 넘게"), ({**good, "until": "2026-02-30"}, "없는 날"),
                     ({**good, "user_id": 999}, "없는 사람"), ({**good, "user_id": "3"}, "번호 모양"), ({**good, "user_id": 5}, "비활성"), ([good], "모양")):
        ok(post(hr, bad).status_code == 400, f"경사·조사: 틀리면 거부 ({why})")
    n0 = q("SELECT COUNT(*) FROM celeb_events")[0][0]
    ok(n0 == 0, "틀린 요청은 하나도 저장 안 됨")
    r = post(hr, good)
    eid = r.json["id"]
    ok(r.status_code == 200 and {"kind": "joy", "id": 3, "note": "결혼", "event": eid} in news(lead).json["items"], "올리면 모두에게 보임 (앞뒤 빈칸은 지움)")
    audit = q("SELECT after_data FROM audit_logs WHERE action = 'NEWS_POSTED' ORDER BY id DESC LIMIT 1")[0][0]
    ok("결혼" not in audit and '"kind": "joy"' in audit, "감사로그에는 내용을 남기지 않음 (종류 · 사람 · 종료일만)")
    x("INSERT INTO celeb_events (user_id, kind, label, until_date, created_by, created_at) VALUES (3, 'condol', '지난소식', ?, 1, 'x')", past)
    x("INSERT INTO celeb_events (user_id, kind, label, until_date, created_by, created_at) VALUES (3, 'condol', '오늘까지', ?, 1, 'x')", today.isoformat())
    notes = [i["note"] for i in news(emp).json["items"] if i["kind"] == "condol"]
    ok(notes == ["오늘까지"], "게시 종료일이 지나면 안 보임 (종료일 당일까지)")
    rm = lambda c, i: c.post(f"/api/news/events/{i}/remove", base_url=BASE, headers=ORIGIN)  # noqa: E731
    ok(rm(emp, eid).status_code == 403, "게시 내리기는 HR 만")
    ok(rm(hr, eid).status_code == 200 and not has("joy", 3) and q("SELECT removed_at IS NOT NULL FROM celeb_events WHERE id = ?", eid) == [(1,)],
       "게시 내리기 (지우지 않고 표시)")
    ok(rm(hr, eid).status_code == 404 and rm(hr, 999).status_code == 404, "이미 내렸거나 없는 소식은 404")


def check_workspace(hr) -> None:
    """워크스페이스 서버 저장 (2026-10-19): 본인만 · 판 번호로 다른 기기와 겹침 막기."""
    print("워크스페이스")
    emp, emp_phone, lead = app.test_client(), app.test_client(), app.test_client()
    login(emp, new_token("emp"))
    login(emp_phone, new_token("emp"))  # 같은 사람의 두 번째 기기
    login(lead, new_token("lead"))

    def get(client):
        return client.get("/api/workspace", base_url=BASE)

    def put(client, kind, data, version, headers=ORIGIN):
        return client.post(f"/api/workspace/{kind}", base_url=BASE, headers=headers, json={"data": data, "version": version})

    ok(app.test_client().get("/api/workspace", base_url=BASE).status_code == 401, "로그인 없이는 401")
    ok(get(emp).json == {"tasks": {"data": None, "version": 0}, "routines": {"data": None, "version": 0}}, "처음엔 서버에 없음 (판 0)")
    tasks = [{"id": 1, "title": "주간 보고", "due": "2026-10-20", "subs": [{"t": "취합", "done": False}]}]
    r = put(emp, "tasks", tasks, 0)
    ok(r.status_code == 200 and r.json == {"version": 1}, "처음 저장 → 판 1")
    ok(get(emp_phone).json["tasks"] == {"data": tasks, "version": 1}, "다른 기기(휴대폰)에서 같은 내용")
    ok(get(lead).json["tasks"]["data"] is None, "다른 사람에게는 안 보임 (본인만)")
    ok(put(emp_phone, "tasks", tasks + [{"id": 2, "title": "휴대폰에서"}], 1).json == {"version": 2}, "휴대폰에서 고쳐 저장 → 판 2")
    r = put(emp, "tasks", [{"id": 1, "title": "PC 옛 판"}], 1)
    ok(r.status_code == 409 and r.json["version"] == 2 and r.json["data"][1]["title"] == "휴대폰에서"
       and get(emp).json["tasks"]["version"] == 2, "옛 판으로 저장하면 409 + 서버 것 (덮어쓰지 않음)")
    ok(put(emp, "routines", [{"id": 1, "title": "산책", "log": {"2026-10-19": 1}, "sch": [{"from": "2026-10-01", "rep": "daily"}]}], 0).status_code == 200
       and get(emp).json["routines"]["version"] == 1 and get(emp).json["tasks"]["version"] == 2, "나의 일상은 따로 (판도 따로)")
    for bad, why in ((("tasks", {"a": 1}, 2), "목록이 아님"), (("tasks", [1, 2], 2), "항목이 객체가 아님"), (("tasks", [], "2"), "판 번호 모양"),
                     (("tasks", [{"t": "가" * 2_100_000}], 2), "너무 큼")):
        ok(put(emp, *bad).status_code == 400 and get(emp).json["tasks"]["version"] == 2, f"틀린 모양은 저장 안 함 ({why})")
    ok(put(emp, "notes", [], 0).status_code == 404, "없는 종류는 404")
    ok(put(emp, "tasks", [], 2, headers={}).status_code == 403, "포털 화면에서만 (Origin)")
    ok(put(emp, "tasks", [], 2).json == {"version": 3} and get(emp_phone).json["tasks"]["data"] == [], "다 지운 목록도 저장")
    ok(not q("SELECT 1 FROM audit_logs WHERE after_data LIKE '%주간 보고%' OR after_data LIKE '%휴대폰에서%'"), "내용은 감사로그에 남기지 않음")


def check_game(hr) -> None:
    """X-ray 특공대 (2026-10-20): 게임 기록은 본인만 · 판 번호 · 이번 주 최고점 · 랭킹(나 빼고·비활성 없음) · 동료 선물 규칙."""
    import game
    print("게임")
    emp, emp_phone, lead = app.test_client(), app.test_client(), app.test_client()
    login(emp, new_token("emp"))
    login(emp_phone, new_token("emp"))  # 같은 사람의 휴대폰
    login(lead, new_token("lead"))
    get = lambda c, p: c.get(p, base_url=BASE)  # noqa: E731
    post = lambda c, p, body, headers=ORIGIN: c.post(p, base_url=BASE, headers=headers, json=body)  # noqa: E731

    ok(all(app.test_client().get(p, base_url=BASE).status_code == 401 for p in ("/api/game/save", "/api/game/rank", "/api/game/gifts")),
       "로그인 없이는 401 (게임 기록·랭킹·선물)")
    ok(get(emp, "/api/game/save").json == {"data": None, "version": 0}, "처음엔 서버에 기록 없음 (판 0)")
    save = {"best": "900", "tix": '{"n":3,"t":0}', "titles": '["combo"]'}
    ok(post(emp, "/api/game/save", {"data": save, "version": 0}).json == {"version": 1}, "기록 저장 → 판 1")
    ok(get(emp_phone, "/api/game/save").json == {"data": save, "version": 1}, "휴대폰에서 같은 기록 (출동권·칭호·최고점)")
    ok(get(lead, "/api/game/save").json["data"] is None, "다른 사람에게는 안 보임")
    r = post(emp, "/api/game/save", {"data": {"best": "1"}, "version": 0})
    ok(r.status_code == 409 and r.json["version"] == 1 and r.json["data"] == save, "옛 판으로 저장하면 409 + 서버 것")
    for bad, why in (([], "객체가 아님"), ({"tix": 3}, "값이 글자가 아님"), ({"x y": "1"}, "칸 이름"), ({"a": "가" * 210_000}, "너무 큼")):
        ok(post(emp, "/api/game/save", {"data": bad, "version": 1}).status_code == 400, f"틀린 기록은 저장 안 함 ({why})")
    ok(post(emp, "/api/game/save", {"data": save, "version": 1}, headers={}).status_code == 403, "게임 화면에서만 (Origin)")

    score = lambda c, s: post(c, "/api/game/score", {"score": s})  # noqa: E731
    week = game._week(auth._today())
    for s in (500, 300, 900):
        score(emp, s)
    ok(q("SELECT score FROM game_scores WHERE user_id = 3 AND week = ?", week) == [(900,)], "이번 주 최고점만 남음 (500 → 300 무시 → 900)")
    ok(all(score(emp, s).status_code == 400 for s in (0, -5, "9", 1.5, 100_000_001)), "점수 모양·범위")
    score(lead, 400)
    last = game._week(auth._today() - timedelta(days=7))
    x("INSERT INTO game_scores (user_id, week, score, at) VALUES (3, ?, 700, '2026-01-01T00:00:09Z'), (2, ?, 700, '2026-01-01T00:00:01Z'),"
      " (5, ?, 99999, '2026-01-01T00:00:00Z')", last, last, last)
    me = get(emp, "/api/game/rank").json
    ok([(p["name"], p["dept"], p["score"]) for p in me["week"]] == [("가상2", "가상팀", 400)], "이번 주: 다른 직원만 (나는 화면이 붙임)")
    ok(me["me"] == {"week": 900, "best": 900} and me["thisWeek"] == week, "내 이번 주 · 역대 최고")
    ok([(d["name"], d["score"], d["n"], d["me"]) for d in me["dept"]] == [("가상팀", 1300, 2, True)], "부서 대항전: 부서원 이번 주 점수 합 · 내 부서 표시")
    ok([(h["week"], h["name"], h["me"]) for h in me["hof"]] == [(last, "가상2", False)], "명예의 전당: 같은 점수면 먼저 낸 사람 · 비활성 직원 빼고")
    ok(me["last"] == {"week": last, "rank": 2}, "주간 보상용 지난번 내 순위 (비활성 직원은 세지 않음)")
    ok([p["name"] for p in get(lead, "/api/game/rank").json["all"]] == ["가상3"], "역대 최고: 비활성 직원 없음")

    for uid in (10, 11, 12):
        x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled) VALUES (?, ?, ?, 2, ?, 1)",
          uid, f"가상{uid}", f"u{uid}@example.invalid", f"HW-{uid}")
    x("UPDATE users SET portal_enabled = 1 WHERE id IN (1, 2)")
    x("UPDATE users SET portal_enabled = 0 WHERE id = 4")  # 앞 검사가 켜 두었을 수 있다
    send = lambda c, to: post(c, "/api/game/gifts", {"to": to})  # noqa: E731
    mates = [m["id"] for m in get(emp, "/api/game/gifts").json["mates"]]
    ok(3 not in mates and 4 not in mates and 5 not in mates and 2 in mates, "보낼 동료: 나·포털 못 쓰는 사람·비활성 빼고")
    ok(send(emp, 2).json == {"ok": True}, "선물 보내기")
    r = send(emp, 2)
    ok(r.status_code == 409 and r.json["msg"] == "오늘은 이미 보냈어요", "한 사람에게 하루 한 장")
    ok(all(send(emp, t).status_code == 400 for t in (3, 4, 5, 999, "2")), "나 · 포털 못 쓰는 사람 · 비활성 · 없는 사람에게는 못 보냄")
    ok(send(emp, 10).status_code == 200 and send(emp, 11).status_code == 200, "하루 3명까지")
    r = send(emp, 12)
    ok(r.status_code == 409 and r.json["msg"] == "오늘은 3명에게 다 보냈어요" and sorted(get(emp, "/api/game/gifts").json["sent"]) == [2, 10, 11],
       "넷째 사람은 거절 · 오늘 보낸 사람 목록")
    box = get(lead, "/api/game/gifts").json["inbox"]
    ok([(b["name"], b["dept"]) for b in box] == [("가상3", "가상팀")] and box[0]["at"] > 0, "받은 선물함 (보낸 사람 · 부서 · 시각)")
    claim = lambda c, ids: post(c, "/api/game/gifts/claim", {"ids": ids})  # noqa: E731
    ok(claim(lead, [box[0]["id"]]).json == {"ids": [box[0]["id"]]} and claim(lead, [box[0]["id"]]).json == {"ids": []}, "받기 (두 번은 안 됨)")
    today, old = auth._today(), (auth._today() - timedelta(days=7)).isoformat()
    for frm, day in ((1, today.isoformat()), (10, today.isoformat()), (11, today.isoformat()), (12, today.isoformat()),
                     (10, (today - timedelta(days=1)).isoformat()), (11, old)):
        x("INSERT INTO game_gifts (from_user, to_user, day, created_at) VALUES (?, 2, ?, '2026-01-01T00:00:00Z')", frm, day)
    box = [b["id"] for b in get(lead, "/api/game/gifts").json["inbox"]]
    ok(len(box) == 5, "7일 지난 선물은 선물함에 없음")
    ok(len(claim(lead, box).json["ids"]) == 4 and len(get(lead, "/api/game/gifts").json["inbox"]) == 1, "받기는 하루 5장까지 (남은 건 선물함에)")
    ok(claim(emp, box).json == {"ids": []}, "남의 선물은 못 받음")
    ok(all(claim(lead, b).status_code == 400 for b in ([], "1", ["1"], list(range(51)))), "받을 선물 모양")
    ok(post(lead, "/api/game/gifts", {"to": 3}, headers={}).status_code == 403, "선물도 게임 화면에서만 (Origin)")

    print("게임 오픈시간 (2026-10-22 — HR 이 탕비실에서 바꾼다)")
    real_now, t0 = auth._now, datetime(2026, 12, 26, 3, 0, tzinfo=timezone.utc)  # 한국 12/26(토) 12:00 — 12/25 성탄절은 지난 날
    auth._now = lambda: t0
    try:
        hr, emp, lead = app.test_client(), app.test_client(), app.test_client()  # 시계를 옮겼으니 그 시각에 새로 로그인한다 (세션 만료)
        for c, who in ((hr, "hr"), (emp, "emp"), (lead, "lead")):
            login(c, new_token(who))
        hours = lambda c: get(c, "/api/game/hours")  # noqa: E731
        put = lambda c, body, headers=ORIGIN: post(c, "/api/game/hours", body, headers)  # noqa: E731
        ok(app.test_client().get("/api/game/hours", base_url=BASE).status_code == 401, "오픈시간도 로그인해야")
        d = hours(emp).json
        ok([(s["name"], s["from"], s["to"]) for s in d["slots"]] == [("평일 점심", 720, 810), ("오후 휴식", 930, 960), ("퇴근 후", 1080, 540)]
           and d["weekend"] == "all" and d["closed"] is None, "처음엔 예전 오픈시간 (점심 · 오후 휴식 · 퇴근 후 18:00~09:00 · 주말 종일)")
        ok(d["days"][0] == {"date": "2027-01-01", "name": "신정"} and len(d["days"]) == 24, "공휴일이 미리 들어 있음 · 지난 날(12/25)은 빠짐")
        ok(put(emp, d).status_code == 403 and put(lead, d).status_code == 403, "HR 이 아니면 못 바꿈")
        ok(put(hr, d, headers={}).status_code == 403, "포털 화면에서만 (Origin)")
        now_ms = int(t0.timestamp() * 1000)
        since, until = now_ms - 60_000, now_ms + 3600_000
        want = {"slots": [{"name": "점심", "from": 720, "to": 780}, {"name": "밤", "from": 1200, "to": 0}], "weekend": "slots",
                "days": [{"date": "2027-01-01", "name": "신정"}, {"date": "2027-03-02", "name": "창립기념일"}],
                "closed": {"msg": "연말 결산 기간이에요", "since": since, "until": until}}
        r = put(hr, {**want, "slots": [{"name": " 점심 ", "from": 720, "to": 780}, want["slots"][1]],
                     "days": [want["days"][1], {"date": "2026-12-25", "name": "지난 날"}, want["days"][0]]})
        ok(r.status_code == 200 and r.json == want, "HR 이 저장 → 이름 앞뒤 빈칸 · 지난 날은 빼고 날짜순")
        ok(hours(emp).json == want and hours(lead).json == want, "직원 누구에게나 같은 오픈시간")
        log = q("SELECT actor_user_id, actor_is_hr, before_data, after_data FROM audit_logs WHERE action = 'GAME_HOURS_SET'")
        ok(len(log) == 1 and log[0][:2] == (1, 1) and json.loads(log[0][2])["weekend"] == "all" and json.loads(log[0][3]) == want,
           "감사로그 GAME_HOURS_SET (누가 · 전 · 후)")
        auth._now = lambda: t0 + timedelta(hours=2)
        ok(hours(emp).json["closed"] == want["closed"], "다시 열 시각이 지나도 닫았던 기록은 남음 (화면은 열림으로 봄 · 무제한권 계산용)")
        auth._now = lambda: t0
        ok(put(hr, {**want, "closed": {"msg": "", "since": since, "until": None}}).json["closed"] == {"msg": "", "since": since, "until": None},
           "다시 열 시각 없이 닫기 (한 줄은 비워도 됨)")
        ok(put(hr, {**want, "closed": {"msg": "", "since": since, "until": now_ms}}).json["closed"]["until"] == now_ms, "다시 열기 = 지금까지 닫았던 기록")
        ok(put(hr, {**want, "closed": {"msg": "", "since": now_ms + 300_000, "until": None}}).status_code == 200, "HR 화면 시계가 조금 빨라도 받음 (10분까지)")
        slot = lambda **k: {**want, "slots": [{"name": "점심", "from": 720, "to": 780, **k}]}  # noqa: E731
        day = lambda **k: {**want, "days": [{"date": "2027-03-02", "name": "창립기념일", **k}]}  # noqa: E731
        for bad, why in (({**want, "slots": "x"}, "시간대 목록이 아님"), ({**want, "slots": want["slots"] * 6}, "시간대 12개"),
                         (slot(**{"from": 725}), "10분 단위가 아님"), (slot(**{"from": 1440}), "24:00 시작"), (slot(to=720), "시작 = 끝"),
                         (slot(**{"from": "720"}), "시각이 글자"), (slot(**{"from": True}), "시각이 참·거짓"), (slot(name=" "), "이름 비어 있음"),
                         (slot(name="가" * 13), "이름 13자"), ({**want, "weekend": "none"}, "주말 값"), (day(date="2027-02-30"), "없는 날"),
                         (day(date="2027-3-2"), "날짜 모양"), (day(name=""), "날 이름 비어 있음"), ({**want, "days": want["days"] * 2}, "같은 날 두 번"),
                         ({**want, "closed": {"msg": "가" * 41, "since": since, "until": None}}, "한 줄 41자"),
                         ({**want, "closed": {"msg": "", "since": since, "until": now_ms + 366 * 86_400_000 + 60_000}}, "1년 넘게 닫기"),
                         ({**want, "closed": {"msg": "", "since": since, "until": str(until)}}, "다시 열 시각이 글자"),
                         ({**want, "closed": {"msg": "", "until": until}}, "닫은 때 없음"), ({**want, "closed": {"msg": "", "since": now_ms + 700_000, "until": None}}, "닫은 때가 10분 넘게 뒤"),
                         ({**want, "closed": {"msg": "", "since": since, "until": since - 1}}, "다시 열 시각이 닫은 때보다 앞"), ({**want, "closed": "x"}, "닫기 모양"),
                         ([], "객체가 아님")):
            ok(put(hr, bad).status_code == 400, f"틀린 오픈시간은 저장 안 함 ({why})")
    finally:
        auth._now = real_now


def check_payslips(hr) -> None:
    """급여명세서 (2026-10-20): 잠가 저장 · HR 올리기·공개·내리기 · 본인은 공개된 것만 · 열쇠가 없으면 503. 사람·금액은 전부 가짜."""
    import base64
    import copy
    import unittest

    print("급여명세서")
    emp, lead = app.test_client(), app.test_client()
    login(emp, new_token("emp"))
    login(lead, new_token("lead"))
    raw_key = bytes(range(32))
    key, had = base64.b64encode(raw_key).decode(), os.environ.pop("HR_PAY_KEY", None)  # 열쇠는 글자로 적지 않고 여기서 만든다
    get = lambda c, p="/api/payslips": c.get(p, base_url=BASE)  # noqa: E731
    post = lambda c, p, body=None, headers=ORIGIN: c.post(p, base_url=BASE, headers=headers, json=body)  # noqa: E731
    up = lambda c, ym, slips, pay_date="2026-06-20": post(c, f"/api/payslips/{ym}", {"payDate": pay_date, "slips": slips})  # noqa: E731
    months = lambda c=hr: get(c, "/api/payslips/months")  # noqa: E731
    snap = lambda: (q("SELECT * FROM payslip_months ORDER BY ym"), q("SELECT ym, user_id, data, seen_at FROM payslips ORDER BY ym, user_id"),  # noqa: E731
                    q("SELECT id, employee_no FROM users ORDER BY id"), q("SELECT COUNT(*) FROM audit_logs"))
    body_of = lambda s: {k: s[k] for k in ("head", "pay", "ded", "gross", "deduct", "net")}  # noqa: E731  # 올린 것에서 userId · code 를 뺀 모양
    shown = lambda r: {k: r[k] for k in ("head", "pay", "ded", "gross", "deduct", "net")}  # noqa: E731  # 응답 한 장에서 같은 칸만

    def slip(uid, code, pay=(("기본급", 2500000), ("식대", 200000)), ded=(("국민연금", 121500), ("소득세", 30000))):
        gross, deduct = sum(v for _, v in pay), sum(v for _, v in ded)
        return {"userId": uid, "code": code, "head": {"code": code, "name": f"가상{uid}", "dept": "가상팀", "title": "프로", "job": "연구"},
                "pay": [list(p) for p in pay], "ded": [list(d) for d in ded], "gross": gross, "deduct": deduct, "net": gross - deduct}

    one = slip(3, "A001")
    two = slip(10, "A002", pay=(("기본급", 3000000), ("연장수당", 123456)), ded=(("소득세", 50000), ("정산 환급", -20000)))  # 환급은 음수
    try:
        ok(app.test_client().get("/api/payslips", base_url=BASE).status_code == 401, "로그인 없이는 401")
        ok(get(emp, "/api/payslips/months").status_code == 403, "직원은 달 목록을 못 봄 (403)")
        ok(get(emp, "/api/payslips?user=3").status_code == 403 and get(emp, "/api/payslips?user=10").status_code == 403,
           "직원이 ?user= 를 붙이면 403 (본인 번호여도)")
        ok(up(emp, "2026-05", [one, two]).status_code == 403, "직원은 올리기 403 (열쇠 검사보다 먼저)")

        print("  - 서버에 열쇠가 없을 때")
        r = months()
        ok(r.status_code == 200 and r.json == {"ready": False, "askTo": None, "months": []}, "달 목록 ready 거짓 · 급여 문의 받는 사람 없음")
        r = up(hr, "2026-05", [one, two])
        ok(r.status_code == 503 and "HR_PAY_KEY" in r.json["error"] and not q("SELECT 1 FROM payslip_months"), "올리기 503 + 설정 안내 (아무것도 저장 안 됨)")
        ok(get(emp).status_code == 200 and get(emp).json == {"slips": []}, "직원 목록은 빈 목록 (오류 아님)")
        os.environ["HR_PAY_KEY"] = base64.b64encode(bytes(range(31))).decode()
        ok(months().json["ready"] is False and up(hr, "2026-05", [one, two]).status_code == 503, "32바이트가 아닌 열쇠는 없는 것과 같음")
        os.environ["HR_PAY_KEY"] = key

        print("  - 올리기 · 잠금")
        r = up(hr, "2026-05", [one, two])
        ok(r.status_code == 200 and r.json == {"saved": 2}, "HR 이 두 사람 올림 → saved 2")
        m = months().json
        ok(m["ready"] is True and len(m["months"]) == 1 and m["months"][0]["uploadedAt"]
           and {k: m["months"][0][k] for k in ("ym", "payDate", "status", "people", "revised", "openedAt")}
           == {"ym": "2026-05", "payDate": "2026-06-20", "status": "DRAFT", "people": 2, "revised": False, "openedAt": None},
           "달 목록: DRAFT · 2명 · 지급일 · 수정 아님")
        blobs = [b[0] for b in q("SELECT data FROM payslips WHERE ym = '2026-05' ORDER BY user_id")]
        ok(len(blobs) == 2 and all(len(b) > 28 and "가상".encode() not in b and b"2500000" not in b and b"3123456" not in b
                                   and "기본급".encode() not in b for b in blobs), "DB 에는 잠긴 글만 (이름·항목·금액이 그대로 없음)")
        import payslip
        from cryptography.exceptions import InvalidTag

        def opens(ym, uid):
            try:
                return payslip.unseal(raw_key, ym, uid, blobs[0]) == body_of(one)
            except InvalidTag:
                return False

        ok(opens("2026-05", 3) and not opens("2026-05", 10) and not opens("2026-06", 3), "열쇠로 풀리고, 다른 직원·다른 달 자리로 옮기면 안 풀림 (aad)")
        a, b = (payslip.seal(raw_key, "2026-05", 3, body_of(one)) for _ in range(2))
        ok(a[:12] != b[:12] and a[12:] != b[12:], "같은 내용을 두 번 잠가도 nonce(앞 12바이트)가 달라 잠긴 글이 다름")

        def skipped(path, ym, uid):  # 안 풀리는 줄: 빈 목록 + 서버 로그 한 줄(달 · 직원 번호만). 로그는 잡아 두어 검사 출력에 ERROR 가 섞이지 않게
            with unittest.TestCase().assertLogs("payslip", "ERROR") as cm:
                r = get(hr, path)
            return (r.json == {"slips": []} and [(rec.args, rec.levelname) for rec in cm.records] == [((ym, uid), "ERROR")]
                    and not any(w in cm.output[0] for w in ("가상", "기본급", "2500000", "A001")))

        x("UPDATE payslips SET data = ? WHERE ym = '2026-05' AND user_id = 10", blobs[0])  # 한 사람의 줄을 다른 사람 자리에 붙여 본다
        ok(skipped("/api/payslips?user=10", "2026-05", 10), "옮겨 붙인 줄은 보여 주지 않음 (건너뜀) · 서버 로그에는 달 · 직원 번호만")
        x("UPDATE payslips SET data = ? WHERE ym = '2026-05' AND user_id = 10", blobs[1])
        os.environ["HR_PAY_KEY"] = base64.b64encode(bytes(range(1, 33))).decode()
        ok(skipped("/api/payslips?user=3", "2026-05", 3), "열쇠가 바뀌어 안 풀리는 줄도 건너뜀 (오류 아님) · 서버 로그에는 달 · 직원 번호만")
        os.environ["HR_PAY_KEY"] = key
        q_views = lambda: q("SELECT actor_user_id, entity_type, entity_id, after_data FROM audit_logs WHERE action = 'PAYSLIP_VIEW' ORDER BY id")  # noqa: E731

        print("  - 공개 전")
        ok(get(emp).json == {"slips": []}, "직원 목록은 빈 목록 (올린 것은 HR 만 봄)")
        ok(post(emp, "/api/payslips/2026-05/seen").status_code == 404, "공개 전 '봤음'은 404")
        n_views = len(q_views())
        r = get(hr, "/api/payslips?user=3")
        ok(r.status_code == 200 and len(r.json["slips"]) == 1 and r.json["slips"][0]["status"] == "DRAFT"
           and r.json["slips"][0]["ym"] == "2026-05" and shown(r.json["slips"][0]) == body_of(one), "HR 은 ?user= 로 공개 전(DRAFT) 한 장을 봄")
        views = q_views()[n_views:]
        ok(len(views) == 1 and views[0][:3] == (1, "user", 3) and not any(w in str(views[0]) for w in ("2500000", "2548500", "가상")),
           "남의 것을 보면 감사로그 PAYSLIP_VIEW 한 줄 (금액·이름 없음)")

        print("  - 공개")
        r = post(hr, "/api/payslips/2026-05/open")
        ok(r.status_code == 200 and r.json == {"status": "OPEN"}
           and q("SELECT status, opened_by, opened_at IS NOT NULL FROM payslip_months WHERE ym = '2026-05'") == [("OPEN", 1, 1)], "공개 → OPEN (누가 언제)")
        mine = get(emp).json["slips"]
        ok(len(mine) == 1 and (mine[0]["ym"], mine[0]["payDate"], mine[0]["revised"], mine[0]["seen"]) == ("2026-05", "2026-06-20", False, False)
           and shown(mine[0]) == body_of(one) and "status" not in mine[0], "직원 목록에 한 장 · 내용이 올린 것과 같음 · 아직 안 봄")
        ok(get(lead).json == {"slips": []}, "명세서가 없는 직원은 빈 목록 (남의 것은 안 보임)")
        ok(post(emp, "/api/payslips/2026-05/seen").json == {"ok": True} and get(emp).json["slips"][0]["seen"] is True, "'봤음' → seen 참")
        ok(post(emp, "/api/payslips/2026-05/seen").json == {"ok": True}, "두 번 눌러도 됨")
        ok(post(lead, "/api/payslips/2026-05/seen").status_code == 404 and post(emp, "/api/payslips/2026-04/seen").status_code == 404
           and post(emp, "/api/payslips/abc/seen").status_code == 404, "본인 명세서가 아니거나 없는 달의 '봤음'은 404")
        ok(get(hr, "/api/payslips?user=3").json["slips"][0]["status"] == "OPEN", "HR 이 보는 남의 줄에는 status")
        os.environ.pop("HR_PAY_KEY")  # 공개 중인 달이 있는데 서버 설정에서 열쇠가 빠졌을 때
        r, m = get(emp), months().json
        ok(r.status_code == 200 and r.json == {"slips": []} and m["ready"] is False and m["months"][0]["status"] == "OPEN",
           "공개 중인 달이 있어도 열쇠가 없으면 직원 목록은 빈 목록(200) · HR 달 목록 ready 거짓")
        os.environ["HR_PAY_KEY"] = key

        print("  - 공개 중 다시 올리기 · 내리기")
        before = snap()
        r = up(hr, "2026-05", [one, two])
        ok(r.status_code == 409 and "공개를 내린 뒤" in r.json["error"] and snap() == before, "공개 중인 달은 다시 올릴 수 없음 (409, 아무것도 안 바뀜)")
        r = post(hr, "/api/payslips/2026-05/close")
        ok(r.status_code == 200 and r.json == {"status": "DRAFT"} and get(emp).json == {"slips": []}, "공개 내리기 → 직원 목록은 다시 빔")
        one_b = slip(3, "A001", pay=(("기본급", 2600000), ("식대", 200000)))
        ok(up(hr, "2026-05", [one_b, two]).json == {"saved": 2}
           and months().json["months"][0]["revised"] is True and months().json["months"][0]["status"] == "DRAFT", "내린 뒤 다시 올리면 수정됨(revised) 표시")
        ok(get(emp).json == {"slips": []}, "다시 올린 것도 공개 전에는 직원에게 안 보임")
        post(hr, "/api/payslips/2026-05/open")
        mine = get(emp).json["slips"][0]
        ok(mine["revised"] is True and mine["seen"] is False and shown(mine) == body_of(one_b), "다시 공개: 수정됨 참 · '봤음' 다시 거짓 · 새 내용")
        ok(post(hr, "/api/payslips/2099-01/open").status_code == 404 and post(hr, "/api/payslips/2099-01/close").status_code == 404, "없는 달 공개·내리기는 404")
        ok(post(emp, "/api/payslips/2026-05/close").status_code == 403 and post(emp, "/api/payslips/2026-05/open").status_code == 403, "직원은 공개·내리기 403")

        print("  - 사원코드")
        no = {mb["id"]: mb["no"] for mb in get(hr, "/api/members").json["members"]}
        ok(no[3] == "A001" and no[10] == "A002" and no[2] == "", "비어 있던 직원에게 사원코드가 기억됨 (/api/members 의 no)")
        ok(up(hr, "2026-07", [one, two], "2026-08-20").json == {"saved": 2} and q("SELECT employee_no FROM users WHERE id = 3") == [("A001",)],
           "이미 붙은 자기 코드로 올리는 건 그대로 됨")

        print("  - 모양이 틀리면 400 · 아무것도 안 바뀜")
        def mut(f):  # 첫 사람의 명세서만 f 로 틀리게 고친 두 장
            s = copy.deepcopy(one)
            f(s)
            return [s, two]

        def too_many(s):
            s.update(pay=[[f"항목{i}", 1] for i in range(41)], gross=41, net=41 - s["deduct"])

        def too_big(s):
            s.update(pay=[["기본급", 1000000001]], gross=1000000001, net=1000000001 - s["deduct"])

        cases = [
            ("지급 항목 합 ≠ 지급액계", "2026-07", "2026-08-20", mut(lambda s: s.update(gross=s["gross"] + 1, net=s["net"] + 1))),
            ("공제 항목 합 ≠ 공제액계", "2026-07", "2026-08-20", mut(lambda s: s.update(deduct=s["deduct"] + 1, net=s["net"] - 1))),
            ("지급액계 − 공제액계 ≠ 차인지급액", "2026-07", "2026-08-20", mut(lambda s: s.update(net=s["net"] + 1))),
            ("금액이 글자", "2026-07", "2026-08-20", mut(lambda s: s["pay"][0].__setitem__(1, "2500000"))),
            ("금액이 실수", "2026-07", "2026-08-20", mut(lambda s: s["pay"][0].__setitem__(1, 2500000.0))),
            ("금액이 참/거짓", "2026-07", "2026-08-20", mut(lambda s: s["ded"][0].__setitem__(1, True))),
            ("금액이 너무 큼", "2026-07", "2026-08-20", mut(too_big)),
            ("항목 41개", "2026-07", "2026-08-20", mut(too_many)),
            ("항목 이름 31자", "2026-07", "2026-08-20", mut(lambda s: s["pay"][0].__setitem__(0, "가" * 31))),
            ("같은 직원 두 번", "2026-07", "2026-08-20", [one, copy.deepcopy(one)]),
            ("없는 직원 번호", "2026-07", "2026-08-20", mut(lambda s: s.update(userId=999))),
            ("머리 칸 빠짐", "2026-07", "2026-08-20", mut(lambda s: s["head"].pop("job"))),
            ("머리 글자 41자", "2026-07", "2026-08-20", mut(lambda s: s["head"].update(dept="가" * 41))),
            ("사원코드 모양", "2026-07", "2026-08-20", mut(lambda s: s.update(code="A 1"))),
            ("사원코드 21자", "2026-07", "2026-08-20", mut(lambda s: s.update(code="A" * 21))),
            ("0장", "2026-07", "2026-08-20", []),
            ("2001장", "2026-07", "2026-08-20", [one] * 2001),
            ("귀속 월 2026-13", "2026-13", "2026-08-20", [one, two]),
            ("귀속 월 모양", "2026-5", "2026-08-20", [one, two]),
            ("귀속 월 다른 나라 숫자", "202٦-05", "2026-08-20", [one, two]),  # \d 였을 때는 DB 검사에서 500
            ("지급일 다른 나라 숫자", "2026-07", "202٦-08-20", [one, two]),
            ("지급일 2026-02-30", "2026-07", "2026-02-30", [one, two]),
            ("지급일 모양", "2026-07", "2026-8-20", [one, two]),
            ("지급일 없음", "2026-07", None, [one, two]),
            ("없는 달에 틀린 내용", "2026-09", "2026-10-20", mut(lambda s: s.update(net=0))),
        ]
        before = snap()
        for label, ym, pd, slips in cases:
            r = post(hr, f"/api/payslips/{ym}", {"payDate": pd, "slips": slips})
            ok(r.status_code == 400 and r.json["error"].startswith("명세서 내용의 모양이 올바르지 않습니다: ") and snap() == before, f"400 · 안 바뀜 ({label})")
        r = post(hr, "/api/payslips/2026-07", {"payDate": "2026-08-20", "slips": [one], "pad": "가" * 700000})
        ok(r.status_code == 400 and snap() == before, "400 · 안 바뀜 (본문 2MB 넘음)")
        r = post(hr, "/api/payslips/2026-07", {"payDate": "2026-08-20", "slips": mut(lambda s: s.update(net=s["net"] + 1))})
        ok("가상3" in r.json["error"], "문구에 어느 직원인지 이름이 들어감 (HR 에게만 돌려줌)")
        ok(post(hr, "/api/payslips/2026-07", [1, 2]).status_code == 400 and snap() == before, "400 · 안 바뀜 (객체가 아닌 본문)")

        print("  - 사원코드가 다른 직원에게 붙어 있을 때")
        for label, slips in (("남의 코드를 다른 사람으로", [slip(11, "A011"), one, slip(10, "A001")]),
                             ("같은 코드를 두 직원에게", [slip(11, "A011"), slip(12, "A011")])):
            r = post(hr, "/api/payslips/2026-07", {"payDate": "2026-08-20", "slips": slips})
            ok(r.status_code == 400 and ("A001" if "남의" in label else "A011") in r.json["error"] and "이미 다른 직원에게" in r.json["error"]
               and snap() == before, f"400 · 그 달 그대로 · 앞사람의 코드 기억도 취소 ({label})")
        r = post(hr, "/api/payslips/2026-08", {"payDate": "2026-09-20", "slips": [slip(10, "A001")]})
        ok(r.status_code == 400 and snap() == before and not q("SELECT 1 FROM payslip_months WHERE ym = '2026-08'"), "새 달도 만들어지지 않음")

        print("  - 그 밖")
        ok(up(hr, "2026-07", [one, two], "2026-08-20").json == {"saved": 2} and q("SELECT people, revised FROM payslip_months WHERE ym = '2026-07'") == [(2, 0)],
           "공개한 적 없는 달을 다시 올려도 수정됨 아님")
        before = snap()
        ok(post(hr, "/api/payslips/2026-07", {"payDate": "2026-08-20", "slips": [one, two]}, headers={}).status_code == 403 and snap() == before,
           "다른 곳에서 온 요청(Origin 없음)은 올리기 403")
        ok(post(hr, "/api/payslips/2026-07/open", headers={}).status_code == 403 and post(emp, "/api/payslips/2026-05/seen", headers={}).status_code == 403,
           "공개·'봤음'도 Origin 없으면 403")
        ok(all(get(hr, p).status_code == 404 for p in ("/api/payslips?user=abc", "/api/payslips?user=", "/api/payslips?user=999", "/api/payslips?user=-1",
                                                     "/api/payslips?user=" + "9" * 5000)), "?user= 가 숫자가 아니거나 없는 직원이면 404")
        n_views = len(q_views())
        ok(up(hr, "2026-06", [slip(1, "A100"), slip(3, "A001")], "2026-07-20").json == {"saved": 2}
           and get(hr, "/api/payslips?user=1").json == {"slips": []} == get(hr).json, "HR 본인의 공개 전 명세서는 ?user= 로도 안 보임 (OPEN 만)")
        post(hr, "/api/payslips/2026-06/open")
        mine, same = get(hr).json, get(hr, "/api/payslips?user=1").json
        ok(mine == same and len(mine["slips"]) == 1 and "status" not in mine["slips"][0] and len(q_views()) == n_views, "HR 본인 번호의 ?user= 는 ?user= 없는 것과 같음 (감사로그 없음)")
        ok(get(hr, "/api/payslips?user=2").json == {"slips": []} and len(q_views()) == n_views + 1, "명세서가 없는 직원을 봐도 감사로그")
        ok([mb["ym"] for mb in months().json["months"]] == ["2026-07", "2026-06", "2026-05"], "달 목록은 최근 달이 위")
        ok([s["ym"] for s in get(emp).json["slips"]] == ["2026-06", "2026-05"], "직원 목록도 최근 달이 위 (공개된 달만)")

        for p in ("/api/payslips", "/api/payslips/months", "/api/payslips?user=3"):
            ok(get(hr, p).headers.get("Cache-Control") == "no-store", f"Cache-Control: no-store ({p})")
        ok(get(emp).headers.get("Cache-Control") == "no-store" and get(emp, "/api/payslips/months").headers.get("Cache-Control") == "no-store"
           and post(emp, "/api/payslips/2026-05/seen").headers.get("Cache-Control") == "no-store", "직원 응답·거절 응답도 no-store")

        logs = q("SELECT action, entity_type, entity_id, after_data FROM audit_logs WHERE action LIKE 'PAYSLIP%' ORDER BY id")
        ok({"PAYSLIP_UPLOAD", "PAYSLIP_OPEN", "PAYSLIP_CLOSE", "PAYSLIP_VIEW"} <= {a[0] for a in logs}, "감사로그 PAYSLIP_UPLOAD · OPEN · CLOSE · VIEW")
        ok(all(set(json.loads(a[3])) == {"ym", "people"} for a in logs if a[0] != "PAYSLIP_VIEW"), "올리기·공개·내리기 감사로그에는 달과 인원 수만")
        ok(not any(w in str(logs) for w in ("가상", "2500000", "3123456", "기본급", "A001", "A002")), "감사로그에 금액·이름·사원코드 없음")

        print("  - 사원코드 지우기 (직원을 잘못 골라 남의 코드가 붙었을 때)")
        clear = lambda c, uid, body={"no": ""}: post(c, f"/api/members/{uid}/employee-no", body)  # noqa: E731
        code_of = lambda uid: q("SELECT employee_no FROM users WHERE id = ?", uid)  # noqa: E731
        audit = lambda: q("SELECT actor_user_id, entity_type, entity_id, before_data, after_data FROM audit_logs WHERE action = 'MEMBER_EMPLOYEE_NO'")  # noqa: E731
        r = up(hr, "2026-09", [slip(11, "A001")], "2026-10-20")
        ok(r.status_code == 400 and "구성원 설정에서 그 직원의 사원코드를 지운 뒤" in r.json["error"], "남의 코드로 올리면 400 + 고치는 길 안내")
        ok(clear(emp, 3).status_code == 403 and clear(lead, 3).status_code == 403 and code_of(3) == [("A001",)], "HR 이 아니면 사원코드 지우기 403")
        ok(post(hr, "/api/members/3/employee-no", {"no": ""}, headers={}).status_code == 403 and code_of(3) == [("A001",)], "Origin 없으면 403")
        ok(clear(hr, 999).status_code == 404, "명단에 없는 사람은 404")
        ok(all(clear(hr, 3, b).status_code == 400 for b in ({"no": "B001"}, {"no": None}, {}, [""])) and code_of(3) == [("A001",)] and not audit(),
           "지우기만 됨 — 다른 값·모양은 400 (안 바뀜)")
        ok(clear(hr, 3).json == {"ok": True} and code_of(3) == [(None,)]
           and {mb["id"]: mb["no"] for mb in get(hr, "/api/members").json["members"]}[3] == "", "HR 이 지우면 비고 명단의 no 도 빈 글자")
        ok(audit() == [(1, "user", 3, '{"no": "A001"}', '{"no": null}')], "감사로그 MEMBER_EMPLOYEE_NO — 앞뒤 사원코드만")
        ok(up(hr, "2026-09", [slip(11, "A001")], "2026-10-20").json == {"saved": 1} and code_of(11) == [("A001",)],
           "지운 뒤 맞는 직원으로 다시 올리면 그 직원에게 붙음")
        ok(clear(hr, 2).json == {"ok": True} and len(audit()) == 1, "이미 비어 있으면 그대로 (감사로그 더하지 않음)")
    finally:
        os.environ.pop("HR_PAY_KEY", None)
        if had is not None:
            os.environ["HR_PAY_KEY"] = had


def check_withholding(hr) -> None:
    """원천징수영수증 (2026-10-21): 잠가 저장 · 해 전체(묶음 · pending)/사람만 · 공개·내리기 · 본인은 공개된 해만 · 열쇠가 없으면 503.
    사람·금액·PDF 는 전부 가짜."""
    import base64
    import unittest

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    print("원천징수영수증")
    emp, lead, ten = app.test_client(), app.test_client(), app.test_client()
    login(emp, new_token("emp"))
    login(lead, new_token("lead"))
    login(ten, new_token("HW-10"))
    raw_key = bytes(range(32))
    key, had = base64.b64encode(raw_key).decode(), os.environ.pop("HR_PAY_KEY", None)  # 열쇠는 글자로 적지 않고 여기서 만든다
    get = lambda c, p="/api/withholding": c.get(p, base_url=BASE)  # noqa: E731
    post = lambda c, p, body=None, headers=ORIGIN: c.post(p, base_url=BASE, headers=headers, json=body)  # noqa: E731
    years = lambda: get(hr, "/api/withholding/years")  # noqa: E731
    mine = lambda c=emp: get(c).json["years"]  # noqa: E731
    pdf_of = lambda c, y, user="": get(c, f"/api/withholding/{y}/pdf" + (f"?user={user}" if user else ""))  # noqa: E731
    snap = lambda: (q("SELECT * FROM withholding_years ORDER BY year"), q("SELECT * FROM withholding ORDER BY year, user_id"),  # noqa: E731
                    q("SELECT COUNT(*) FROM audit_logs"))

    def sm(uid, settle=-123457):  # 요약 숫자 (가짜). 지방소득세 차감은 못 읽은 칸(null)
        return {"gross": 45678900 + uid, "taxDecided": 1234567, "localDecided": 123456, "taxPrepaid": 1358024,
                "localPrepaid": 135802, "taxSettle": settle, "localSettle": None}

    def fake_pdf(uid, n=100):
        return b"%PDF-1.4\n%fake " + str(uid).encode() + b"x" * n

    def person(uid, s=None, pdf=None):
        return {"userId": uid, "sum": s or sm(uid), "pdf": base64.b64encode(pdf or fake_pdf(uid)).decode()}

    def wb(people, mode="all", reset=True, total=None):
        return {"mode": mode, "reset": reset, "total": len(people) if total is None else total, "people": people}

    def up(c, year, people, **kw):
        return post(c, f"/api/withholding/{year}", wb(people, **kw))

    try:
        ok(app.test_client().get("/api/withholding", base_url=BASE).status_code == 401, "로그인 없이는 401")
        ok(get(emp, "/api/withholding/years").status_code == 403, "직원은 해 목록을 못 봄 (403)")
        ok(get(emp, "/api/withholding?user=3").status_code == 403 and get(emp, "/api/withholding?user=10").status_code == 403
           and pdf_of(emp, 2025, 3).status_code == 403, "직원이 ?user= 를 붙이면 403 (본인 번호여도)")
        ok(up(emp, 2025, [person(3)]).status_code == 403, "직원은 올리기 403 (열쇠 검사보다 먼저)")
        ok(post(emp, "/api/withholding/2025/open").status_code == 403 and post(emp, "/api/withholding/2025/close").status_code == 403,
           "직원은 공개·내리기 403")

        print("  - 서버에 열쇠가 없을 때")
        ok(years().json == {"ready": False, "years": []}, "해 목록 ready 거짓")
        r = up(hr, 2025, [person(3)])
        ok(r.status_code == 503 and "HR_PAY_KEY" in r.json["error"] and not q("SELECT 1 FROM withholding_years"), "올리기 503 + 설정 안내 (아무것도 저장 안 됨)")
        ok(get(emp).status_code == 200 and get(emp).json == {"years": []}, "직원 목록은 빈 목록 (오류 아님)")
        os.environ["HR_PAY_KEY"] = key

        print("  - 모양이 틀리면 400 · 아무것도 안 바뀜")

        def bad_sum(**ch):
            s = sm(3)
            s.update(ch)
            return wb([person(3, s)])

        no_tax = sm(3)
        del no_tax["taxSettle"]
        cases = [
            ("mode 틀림", 2025, wb([person(3)], mode="both")),
            ("귀속연도 1999", 1999, wb([person(3)])),
            ("귀속연도 모양", "2025x", wb([person(3)])),
            ("귀속연도 다른 나라 숫자", "202٥", wb([person(3)])),
            ("sum 칸 하나 빠짐", 2025, wb([person(3, no_tax)])),
            ("sum 칸 더 있음", 2025, bad_sum(extra=1)),
            ("sum 값이 실수", 2025, bad_sum(gross=45678903.0)),
            ("sum 값이 글자", 2025, bad_sum(gross="45678903")),
            ("sum 값이 참/거짓", 2025, bad_sum(taxSettle=True)),
            ("sum 값이 너무 큼", 2025, bad_sum(gross=100_000_000_001)),
            ("pdf 가 %PDF 로 시작 안 함", 2025, wb([person(3, pdf=b"<html>" + b"x" * 100)])),
            ("pdf 가 base64 아님", 2025, wb([{**person(3), "pdf": "%%%not base64%%%"}])),
            ("pdf 3,000,000바이트 넘음", 2025, wb([person(3, pdf=fake_pdf(3, 3_000_000))])),  # base64 로는 4MB — 본문 한도 안
            ("같은 직원 두 번", 2025, wb([person(3), person(3)])),
            ("없는 직원", 2025, wb([person(999)])),
            ("비활성 직원", 2025, wb([person(5)])),
            ("직원 번호가 글자", 2025, wb([{**person(3), "userId": "3"}])),
            ("사람 칸 더 있음", 2025, wb([{**person(3), "name": "가상3"}])),
            ("0명", 2025, wb([])),
            ("total 이 사람 수보다 적음", 2025, wb([person(3), person(10)], total=1)),
            ("total 이 정수 아님", 2025, wb([person(3)], total="1")),
            ("reset 이 참/거짓 아님", 2025, wb([person(3)], reset="yes")),
        ]
        before = snap()
        for label, year, b in cases:
            r = post(hr, f"/api/withholding/{year}", b)
            ok(r.status_code == 400 and r.json["error"].startswith("영수증 내용의 모양이 올바르지 않습니다") and snap() == before, f"400 · 안 바뀜 ({label})")
        r = up(hr, 2025, [person(3, pdf=fake_pdf(3, 1_800_000)), person(10, pdf=fake_pdf(10, 1_800_000))])
        ok(r.status_code == 400 and snap() == before, "400 · 안 바뀜 (본문 4,700,000바이트 넘음)")
        r = up(hr, 2025, [person(3)] * 201)
        ok(r.status_code == 400 and "200" in r.json["error"] and snap() == before, "400 · 안 바뀜 (한 묶음 201명)")
        ok(post(hr, "/api/withholding/2025", [1, 2]).status_code == 400 and snap() == before, "400 · 안 바뀜 (객체가 아닌 본문)")

        print("  - 해 전체 올리기 (묶음 · pending)")
        r = up(hr, 2025, [person(3), person(10)], total=3)
        ok(r.status_code == 200 and r.json == {"saved": 2, "pending": 1}, "첫 묶음 2명 (전체 3명) → pending 1")
        y = years().json
        ok(y["ready"] is True and len(y["years"]) == 1 and y["years"][0]["uploadedAt"]
           and {k: y["years"][0][k] for k in ("year", "status", "people", "pending", "revised", "openedAt")}
           == {"year": 2025, "status": "DRAFT", "people": 2, "pending": 1, "revised": 0, "openedAt": None}
           and y["years"][0]["users"] == [3, 10], "해 목록: DRAFT · 2명 · 남은 1명 · 수정 0 · 올라간 직원 번호")
        r = post(hr, "/api/withholding/2025/open")
        ok(r.status_code == 409 and "올리다 멈춘 해" in r.json["error"] and q("SELECT status FROM withholding_years") == [("DRAFT",)],
           "다 올리기 전(pending 1)에는 공개 409")
        before = snap()
        ok(up(hr, 2025, [person(11)], mode="some", reset=False).status_code == 409 and snap() == before, "해 전체 올리기가 끝나지 않은 해에 사람만 409")
        ok(up(hr, 2025, [person(11), person(12)], reset=False).status_code == 409 and snap() == before, "남은 사람보다 많은 묶음 409")
        ok(up(hr, 2024, [person(11)], reset=False).status_code == 409 and snap() == before, "시작하지 않은 해에 이어 올리기 409")
        ok(up(hr, 2025, [person(3)], reset=False).status_code == 400 and snap() == before, "이어 올리는 묶음에 이미 올린 직원 400")

        print("  - 공개 전 (본인)")
        ok(get(emp).json == {"years": []} and pdf_of(emp, 2025).status_code == 404 and post(emp, "/api/withholding/2025/seen").status_code == 404,
           "공개 전에는 목록에 없음 · PDF 404 · '봤음' 404")
        r = up(hr, 2025, [person(11)], reset=False)
        ok(r.status_code == 200 and r.json == {"saved": 1, "pending": 0} and years().json["years"][0]["people"] == 3
           and years().json["years"][0]["users"] == [3, 10, 11], "나머지 1명 → pending 0 · 3명 · 직원 번호 셋")

        print("  - 잠금")
        rows = q("SELECT user_id, sum, pdf FROM withholding WHERE year = 2025 ORDER BY user_id")
        ok(len(rows) == 3 and all(len(b) > 28 and b"%PDF" not in b and b"4567890" not in b and b"1234567" not in b and b"123457" not in b
                                  for _, s, p in rows for b in (s, p)), "DB 에는 잠긴 글만 (금액 숫자 · %PDF 가 그대로 없음)")
        aes = AESGCM(raw_key)

        def opens(blob, aad):
            try:
                return aes.decrypt(blob[:12], blob[12:], aad.encode())
            except InvalidTag:
                return None

        s3, p3 = rows[0][1], rows[0][2]
        ok(json.loads(opens(s3, "wh|2025|3|sum")) == sm(3) and opens(p3, "wh|2025|3|pdf") == fake_pdf(3)
           and opens(s3, "wh|2025|10|sum") is None and opens(s3, "wh|2024|3|sum") is None and opens(p3, "wh|2025|3|sum") is None,
           "열쇠로 풀리고, 다른 직원 · 다른 해 · 다른 종류 자리로 옮기면 안 풀림 (aad)")

        def skipped(path, year, uid, want):  # 안 풀리는 줄: 서버 로그 한 줄(해 · 직원 번호만). 로그는 잡아 두어 검사 출력에 ERROR 가 섞이지 않게
            with unittest.TestCase().assertLogs("withholding", "ERROR") as cm:
                r = get(hr, path)
            return (want(r) and [(rec.args, rec.levelname) for rec in cm.records] == [((year, uid), "ERROR")]
                    and not any(w in cm.output[0] for w in ("가상", "45678", "%PDF")))

        x("UPDATE withholding SET sum = ?, pdf = ? WHERE year = 2025 AND user_id = 10", s3, p3)  # 한 사람의 줄을 다른 사람 자리에 붙여 본다
        ok(skipped("/api/withholding?user=10", 2025, 10, lambda r: r.status_code == 200 and r.json == {"years": []}),
           "다른 직원 자리로 옮긴 줄은 목록에서 빠짐 (오류 아님) · 서버 로그에는 해 · 직원 번호만")
        ok(skipped("/api/withholding/2025/pdf?user=10", 2025, 10, lambda r: r.status_code == 404), "옮긴 줄의 PDF 는 404 · 서버 로그에는 해 · 직원 번호만")
        x("UPDATE withholding SET sum = ?, pdf = ? WHERE year = 2025 AND user_id = 10", rows[1][1], rows[1][2])

        print("  - 공개")
        r = post(hr, "/api/withholding/2025/open")
        ok(r.status_code == 200 and r.json == {"status": "OPEN"}
           and q("SELECT status, pending, opened_by, opened_at IS NOT NULL FROM withholding_years WHERE year = 2025") == [("OPEN", 0, 1, 1)],
           "모두 올린 뒤 공개 → OPEN (누가 언제)")
        ok(mine() == [{"year": 2025, "revised": False, "seen": False, "sum": sm(3)}], "직원 목록에 한 해 · 요약이 올린 그대로 (null 칸 포함) · 아직 안 봄")
        r = pdf_of(emp, 2025)
        ok(r.status_code == 200 and r.data == fake_pdf(3) and r.mimetype == "application/pdf"
           and r.headers.get("Content-Disposition") == 'inline; filename="withholding_2025.pdf"', "PDF 바이트가 올린 그대로 · 파일 이름에 실명 없음")
        ok(get(lead).json == {"years": []} and pdf_of(lead, 2025).status_code == 404 and post(lead, "/api/withholding/2025/seen").status_code == 404,
           "영수증이 없는 직원은 빈 목록 · PDF 404 · '봤음' 404 (남의 것은 안 보임)")
        ok(post(emp, "/api/withholding/2025/seen").json == {"ok": True} and mine()[0]["seen"] is True, "'봤음' → seen 참")
        ok(post(emp, "/api/withholding/2025/seen").json == {"ok": True} and post(ten, "/api/withholding/2025/seen").json == {"ok": True},
           "두 번 눌러도 됨 · 다른 직원도 봄")
        ok(post(emp, "/api/withholding/abc/seen").status_code == 404 and post(emp, "/api/withholding/2024/seen").status_code == 404, "없는 해의 '봤음'은 404")
        before = snap()
        r = up(hr, 2025, [person(3), person(10), person(11)])
        ok(r.status_code == 409 and r.json["error"] == "공개 중인 해입니다. 공개를 내린 뒤 다시 올리거나, 사람만 바꾸기로 올려 주세요." and snap() == before,
           "공개 중인 해에 해 전체 409 (아무것도 안 바뀜)")
        ok(up(hr, 2025, [person(12)], reset=False).status_code == 409 and snap() == before, "공개 중인 해에 이어 올리기 409")

        print("  - HR 이 직원 것 보기 (?user=)")
        views = lambda: q("SELECT action, actor_user_id, entity_type, entity_id, after_data FROM audit_logs"  # noqa: E731
                          " WHERE action IN ('WITHHOLDING_VIEW', 'WITHHOLDING_PDF_VIEW') ORDER BY id")
        n = len(views())
        r = get(hr, "/api/withholding?user=3")
        ok(r.status_code == 200 and r.json == {"years": [{"year": 2025, "status": "OPEN", "revised": False, "seen": True, "sum": sm(3)}]},
           "HR 은 ?user= 로 직원 것을 봄 (status 포함)")
        r = pdf_of(hr, 2025, 3)
        ok(r.status_code == 200 and r.data == fake_pdf(3), "HR 은 ?user= 로 직원 PDF 를 봄")
        ok(views()[n:] == [("WITHHOLDING_VIEW", 1, "user", 3, '{"years": 1}'), ("WITHHOLDING_PDF_VIEW", 1, "user", 3, '{"year": 2025}')],
           "남의 것을 보면 감사로그 WITHHOLDING_VIEW · WITHHOLDING_PDF_VIEW (해 · 수만)")
        n = len(views())
        ok(get(hr, "/api/withholding?user=1").json == {"years": []} == get(hr).json and pdf_of(hr, 2025, 1).status_code == 404 and len(views()) == n,
           "HR 본인 번호의 ?user= 는 ?user= 없는 것과 같음 (감사로그 없음)")
        ok(all(get(hr, p).status_code == 404 for p in ("/api/withholding?user=abc", "/api/withholding?user=", "/api/withholding?user=999",
                                                     "/api/withholding?user=-1", "/api/withholding/2025/pdf?user=999")),
           "?user= 가 숫자가 아니거나 없는 직원이면 404")
        ok(pdf_of(hr, "abc", 3).status_code == 404 and pdf_of(hr, 2099, 3).status_code == 404, "없는 해 PDF 는 404")

        print("  - 사람만 바꾸기 · 더하기")
        r = up(hr, 2025, [person(3, sm(3, settle=250000))], mode="some", reset=False)
        ok(r.status_code == 200 and r.json == {"saved": 1, "pending": 0}, "공개 중인 해에 사람만 바꾸기 → 받음")
        ok(mine() == [{"year": 2025, "revised": True, "seen": False, "sum": sm(3, settle=250000)}], "바뀐 사람: 바로 보임 · 수정됨 참 · '봤음' 다시 거짓 · 새 요약")
        ok(mine(ten) == [{"year": 2025, "revised": False, "seen": True, "sum": sm(10)}], "다른 사람: 수정됨 아님 · '봤음' 그대로")
        ok(up(hr, 2025, [person(12)], mode="some", reset=False).json == {"saved": 1, "pending": 0}
           and q("SELECT revised, seen_at FROM withholding WHERE year = 2025 AND user_id = 12") == [(0, None)]
           and (years().json["years"][0]["people"], years().json["years"][0]["revised"]) == (4, 1), "새 사람 더하기 → 수정됨 아님 (새 영수증) · 4명 · 수정 1명")
        r = up(hr, 2024, [person(3)], mode="some", reset=False)
        ok(r.json == {"saved": 1, "pending": 0} and q("SELECT status, pending, opened_at FROM withholding_years WHERE year = 2024") == [("DRAFT", 0, None)]
           and [y["year"] for y in mine()] == [2025], "없는 해에 사람만 → 그 해를 DRAFT 로 만듦 (직원에게는 아직 안 보임)")
        ok(up(hr, 2024, [person(3, sm(3, settle=1))], mode="some", reset=False).status_code == 200
           and q("SELECT revised FROM withholding WHERE year = 2024 AND user_id = 3") == [(0,)], "공개한 적 없는 해에서 바꾸면 수정됨 아님")
        ok([(y["year"], y["status"]) for y in get(hr, "/api/withholding?user=3").json["years"]] == [(2025, "OPEN"), (2024, "DRAFT")],
           "HR 은 공개 전 해도 봄 · 최근 해부터")
        ok(pdf_of(hr, 2024, 3).data == fake_pdf(3) and pdf_of(emp, 2024).status_code == 404, "공개 전 해 PDF: HR 은 봄, 본인은 404")
        ok([y["year"] for y in years().json["years"]] == [2025, 2024], "해 목록은 최근 해부터")

        print("  - 공개 내리기 → 해 전체 다시")
        r = post(hr, "/api/withholding/2025/close")
        ok(r.status_code == 200 and r.json == {"status": "DRAFT"} and get(emp).json == {"years": []} and pdf_of(emp, 2025).status_code == 404,
           "공개 내리기 → 직원에게 다시 안 보임")
        ok(up(hr, 2025, [person(3), person(10)], total=3).json == {"saved": 2, "pending": 1}
           and up(hr, 2025, [person(11)], reset=False).json == {"saved": 1, "pending": 0}, "내린 해를 해 전체로 다시 (두 묶음)")
        ok(q("SELECT user_id, revised, seen_at FROM withholding WHERE year = 2025 ORDER BY user_id") == [(3, 1, None), (10, 1, None), (11, 1, None)]
           and (years().json["years"][0]["people"], years().json["years"][0]["revised"]) == (3, 3),
           "공개했던 해를 해 전체로 다시 → 모든 줄 수정됨 · '봤음' 새로 · 빠진 사람(12)은 지워짐")
        post(hr, "/api/withholding/2025/open")
        ok((mine()[0]["revised"], mine()[0]["seen"]) == (True, False), "다시 공개: 수정됨 참 · '봤음' 거짓")

        print("  - 그 밖")
        ok(post(hr, "/api/withholding/2099/open").status_code == 404 and post(hr, "/api/withholding/2099/close").status_code == 404
           and post(hr, "/api/withholding/abc/open").status_code == 404, "없는 해 공개 · 내리기는 404")
        x("INSERT INTO withholding_years (year, status, pending, uploaded_at, uploaded_by) VALUES (2023, 'DRAFT', 0, '2026-01-01T00:00:00Z', 1)")
        ok(post(hr, "/api/withholding/2023/open").status_code == 409, "줄이 하나도 없는 해는 공개 409")
        x("DELETE FROM withholding_years WHERE year = 2023")
        before = snap()
        ok(post(hr, "/api/withholding/2026", wb([person(3)]), headers={}).status_code == 403 and snap() == before, "Origin 없으면 올리기 403")
        ok(post(hr, "/api/withholding/2025/close", headers={}).status_code == 403 and post(emp, "/api/withholding/2025/seen", headers={}).status_code == 403,
           "내리기 · '봤음'도 Origin 없으면 403")
        os.environ.pop("HR_PAY_KEY")
        ok(get(emp).json == {"years": []} and pdf_of(emp, 2025).status_code == 404 and years().json["ready"] is False,
           "공개 중이어도 열쇠가 빠지면 직원 목록 빈 목록 · PDF 404 · ready 거짓")
        os.environ["HR_PAY_KEY"] = key

        for label, r in (("목록", get(emp)), ("해 목록", years()), ("?user=", get(hr, "/api/withholding?user=3")), ("PDF", pdf_of(emp, 2025)),
                         ("올리기", up(hr, 2025, [person(3)], mode="some", reset=False)), ("올리기 409", up(hr, 2025, [person(3)])),
                         ("공개", post(hr, "/api/withholding/2025/open")), ("봤음", post(emp, "/api/withholding/2025/seen")),
                         ("직원 403", get(emp, "/api/withholding/years")), ("400", post(hr, "/api/withholding/1999", {}))):
            ok(r.headers.get("Cache-Control") == "no-store", f"Cache-Control: no-store ({label})")

        logs = q("SELECT action, after_data FROM audit_logs WHERE action LIKE 'WITHHOLDING%' ORDER BY id")
        ok({"WITHHOLDING_UPLOAD", "WITHHOLDING_OPEN", "WITHHOLDING_CLOSE", "WITHHOLDING_VIEW", "WITHHOLDING_PDF_VIEW"} <= {a for a, _ in logs},
           "감사로그 WITHHOLDING_UPLOAD · OPEN · CLOSE · VIEW · PDF_VIEW")
        ok(all(set(json.loads(d)) <= {"year", "years", "mode", "people"} for _, d in logs), "감사로그에는 해 · 수 · 방식만")
        datas = str([d for _, d in logs])
        ok(not any(w in datas for w in ("가상", "45678", "1234567", "123457", "250000", "PDF", "pdf")), "감사로그에 이름 · 금액 · 파일 이름 없음")
    finally:
        os.environ.pop("HR_PAY_KEY", None)
        if had is not None:
            os.environ["HR_PAY_KEY"] = had


def check_contracts(hr) -> None:
    """연봉계약서 서버 ① ② ③ (2026-10-22): ① 설정 · 직인 · 올리기 · 현황 · 조정 · 빼기 · 발송 — 잠가 저장 · HR 만 · 열쇠가 없으면 503 · 이미 발송한 사람은 409.
    ② 직원 보기 · 서명 (서명본 PDF · 고유번호) · 파기 · 새 계약으로 바꾸기 — 본인 · HR 만 · 서명한 줄은 못 바꿈 · 동시에 두 번 서명은 하나만.
    ③ HR에 문의 · 확인함 · 본부장 현황(금액 없이) · 서명본 zip — 문의는 본인 서명 대기 줄만 · 현황은 자기 본부 사람만 · zip 은 HR 만, 안 풀리는 줄은 건너뜀.
    사람 · 금액 · 대표이사 이름 · 직인 그림은 전부 가짜."""
    import base64
    import csv
    import io
    import unittest
    import zipfile
    from datetime import timedelta

    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from PIL import Image

    import contract_doc

    print("연봉계약서 ① ② ③ (설정 · 올리기 · 현황 · 조정 · 빼기 · 발송 · 직원 보기 · 서명 · 파기 · 새 계약 · 문의 · 본부장 현황 · 서명본 zip)")
    emp = app.test_client()
    login(emp, new_token("emp"))
    raw_key = bytes(range(32))
    key, had = base64.b64encode(raw_key).decode(), os.environ.pop("HR_PAY_KEY", None)  # 열쇠는 글자로 적지 않고 여기서 만든다
    real_now = auth._now
    real_render = contract_doc.render_pdf  # 서명 검사에서 잠깐 바꾸므로 원래 것을 쥐고 있다
    real_tpl, real_build = contract_doc.TPL, contract_doc.build_blocks  # 양식을 바꿔 보는 검사도 있다
    get = lambda c, p: c.get(p, base_url=BASE)  # noqa: E731
    post = lambda c, p, body=None, headers=ORIGIN: c.post(p, base_url=BASE, headers=headers, json=body)  # noqa: E731
    PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="  # 1x1 그림
    buf = io.BytesIO()
    Image.new("RGBA", (2, 2), (200, 0, 0, 255)).save(buf, "PNG")
    PNG2 = base64.b64encode(buf.getvalue()).decode()
    Image.frombytes("RGB", (40, 40), os.urandom(40 * 40 * 3)).save(buf := io.BytesIO(), "PNG")  # 머리는 멀쩡하지만 그림 데이터가 깨졌거나 잘린 직인 — 머리만 읽으면 지나가지만 PDF 를 만들 때 터진다
    noise = buf.getvalue()
    PNG_BROKEN = base64.b64encode(noise[:len(noise) // 2] + b"\xff" * 20 + noise[len(noise) // 2 + 20:]).decode()
    PNG_CUT = base64.b64encode(noise[:-60]).decode()
    Image.new("1", (9000, 9000)).save(buf := io.BytesIO(), "PNG")  # 작은 파일인데 픽셀이 8100만 — 풀기 전에 크기로 거부해야 한다
    PNG_HUGE = base64.b64encode(buf.getvalue()).decode()
    Y = contract_doc.upcoming_year(auth._today())
    first, last = contract_doc.round_period(Y)
    day = lambda n: (auth._today() + timedelta(days=n)).isoformat()  # noqa: E731
    A, B, C, D, E, F, G, H, I = range(131, 140)  # A 보통 · B 포털 꺼짐 · C 계약서 꺼짐 · D 본부장 · E 나중에 비활성 · F 빼기 · G 나중에 계약서 꺼짐 · H 처음부터 비활성 · I 다른 회차와 함께 발송
    for uid in (A, B, C, D, E, F, G, H, I):
        x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, has_contract, title, is_active, deactivated_at)"
          " VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?)",
          uid, f"가상계약{'ABCDEFGHI'[uid - A]}", f"ct{uid}@example.invalid", f"HW-{uid}", int(uid != B), int(uid != C), "프로" if uid == A else None,
          int(uid != H), "2027-01-01T00:00:00Z" if uid == H else None)
    x("INSERT INTO user_roles (user_id, role) VALUES (?, 'DIVISION_HEAD')", D)
    M, N, O, Q, R = range(143, 148)  # ③ M 서명본 PDF 만 안 풀림 · N 본부 바로 아래 · O 다른 본부의 팀 · Q 다른 본부 바로 아래 · R 같은 본부의 다른 본부장
    TLX, TLY = 151, 152  # ③ 팀장인데 이끄는 팀이 소속 팀과 다름 — X 는 다른 본부의 팀을, Y 는 대표이사 직속 팀을 이끈다 (소속은 둘 다 팀 2 · 본부 1)

    def row(uid, **kw):
        return {"userId": uid, "code": "", "birth6": "900315", "base": 2418900, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0, "start": None, **kw}

    up = lambda c, rows, year=Y: post(c, f"/api/contracts/{year}", {"rows": rows})  # noqa: E731
    admin = lambda year=Y, c=hr: get(c, f"/api/contracts/admin?year={year}")  # noqa: E731
    rows_of = lambda year=Y, c=hr: {r["userId"]: r for r in admin(year, c).json["rows"]}  # noqa: E731
    cfg = {"ceo": "○○○", "addr": "가상시 가상구 가상로 1", "kind": "가상 서비스업", "seal": PNG}
    sv = lambda **kw: post(hr, "/api/contracts/settings", {**cfg, **kw})  # noqa: E731
    cfg_of = lambda: get(hr, "/api/contracts/settings").json  # noqa: E731
    send = lambda ids, due=None, c=hr: post(c, "/api/contracts/send", {"ids": ids, "due": due or day(14)})  # noqa: E731
    adj = lambda cid, **kw: post(hr, f"/api/contracts/{cid}/adjust", {"base": 2600000, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0,  # noqa: E731
                                                                      "birth6": "900315", "start": None, "reason": "가상 사유", **kw})
    snap = lambda: (q("SELECT * FROM contracts ORDER BY id"), q("SELECT * FROM contract_settings"), q("SELECT * FROM contract_seals ORDER BY id"),  # noqa: E731
                    q("SELECT id, employee_no FROM users ORDER BY id"), q("SELECT COUNT(*) FROM audit_logs"))
    audit = lambda action: q("SELECT entity_type, entity_id, after_data FROM audit_logs WHERE action = ? ORDER BY id", action)  # noqa: E731
    code_of = lambda uid: q("SELECT employee_no FROM users WHERE id = ?", uid)[0][0]  # noqa: E731

    def opened(uid, field, ver=1, year=Y):  # 열쇠로 풀어 본 그 칸 (DB 의 잠근 글 → JSON)
        blob = q(f"SELECT {field} FROM contracts WHERE year = ? AND user_id = ? AND version = ?", year, uid, ver)[0][0]
        return json.loads(AESGCM(raw_key).decrypt(blob[:12], blob[12:], f"ct|{year}|{uid}|{ver}|{field}".encode()))

    def hash_ok(m):  # 서명 기록의 소금 · 양식 판으로 본문(blocks)의 고유번호를 다시 계산한 값 = 저장한 값 (m = mine/한 건 보기의 줄). 소금이 없는 기록이면 확인할 수 없는 것 → 거짓 (오류 아님)
        rec_ = opened(m["userId"], "sign", m["version"], m["year"])
        return "salt" in rec_ and contract_doc.doc_hash(m["blocks"], rec_["salt"], rec_["tpl"]) == m["hash"]

    try:
        print("  - 권한")
        anon = app.test_client()
        ok(anon.get("/api/contracts/admin", base_url=BASE).status_code == 401 and anon.post(f"/api/contracts/{Y}", base_url=BASE, headers=ORIGIN, json={}).status_code == 401,
           "로그인 없이는 401")
        denied = [get(emp, "/api/contracts/admin"), get(emp, "/api/contracts/settings"), get(emp, "/api/contracts/settings/seal"), post(emp, "/api/contracts/settings", cfg),
                  up(emp, [row(A)]), send([1], c=emp), post(emp, "/api/contracts/1/adjust", {}), post(emp, "/api/contracts/1/remove")]
        ok(all(r.status_code == 403 for r in denied), "직원은 현황 · 설정 · 직인 · 올리기 · 발송 · 조정 · 빼기 모두 403 (열쇠 검사보다 먼저)")
        ok(post(hr, f"/api/contracts/{Y}", {"rows": [row(A)]}, headers={}).status_code == 403, "Origin 없으면 403")

        print("  - 서버에 열쇠가 없을 때")
        ok(admin().json == {"ready": False, "settings": False, "year": Y, "rows": []}, "현황은 ready 거짓 · 줄 없음 (오류 아님)")
        ok(cfg_of() == {"ready": False, "settings": None}, "설정도 ready 거짓")
        before = snap()
        writes = [up(hr, [row(A)]), sv(), send([1]), post(hr, "/api/contracts/1/adjust", {}), post(hr, "/api/contracts/1/remove")]
        ok(all(r.status_code == 503 and "HR_PAY_KEY" in r.json["error"] for r in writes) and snap() == before, "쓰기는 모두 503 + 설정 안내 (아무것도 저장 안 됨)")
        os.environ["HR_PAY_KEY"] = base64.b64encode(bytes(range(31))).decode()
        ok(admin().json["ready"] is False and up(hr, [row(A)]).status_code == 503, "32바이트가 아닌 열쇠는 없는 것과 같음")
        os.environ["HR_PAY_KEY"] = key

        print("  - 설정 — 모양이 틀리면 400")
        ok(cfg_of() == {"ready": True, "settings": None} and admin().json["settings"] is False, "설정이 아직 없으면 settings null · 현황 settings 거짓")
        before = snap()
        r = sv(seal=None)
        ok(r.status_code == 400 and "직인" in r.json["error"], "처음에는 직인 없이 저장할 수 없음 (400)")
        bad_cfg = {"대표이사 빈칸": {"ceo": "   "}, "대표이사 21자": {"ceo": "가" * 21}, "대표이사 제어문자": {"ceo": "가\x00나"}, "대표이사 짝 없는 글자": {"ceo": "가\ud800"},
                   "대표이사 글자 아님": {"ceo": 5}, "소재지 101자": {"addr": "가" * 101}, "소재지 없음": {"addr": None}, "종류 31자": {"kind": "가" * 31},
                   "직인 PNG 아님": {"seal": base64.b64encode(b"hello").decode()}, "직인 base64 아님": {"seal": "%%%"}, "직인 글자 아님": {"seal": 7},
                   "직인 머리만 PNG": {"seal": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"junk" * 20).decode()},
                   "직인 그림 데이터가 깨짐": {"seal": PNG_BROKEN}, "직인 그림 끝이 잘림": {"seal": PNG_CUT}, "직인 그림 픽셀이 너무 큼": {"seal": PNG_HUGE},
                   "직인 500KB 넘음": {"seal": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 500_000).decode()}}
        wrong = [k for k, ch in bad_cfg.items() if sv(**ch).status_code != 400]
        ok(not wrong and snap() == before, f"이름 · 소재지 · 종류 글자 규칙, 직인은 PNG 머리 · 끝까지 읽히는 그림 · 500KB 이하 — 틀리면 400, 아무것도 안 바뀜 {wrong}")

        print("  - 올리기 — 모양이 틀리면 400 (아무것도 안 바뀜)")
        no_start = row(A)
        del no_start["start"]
        rows_bad = {
            "같은 직원 두 번": [row(A), row(A)], "연봉계약서 꺼진 직원": [row(C)], "본부장": [row(D)], "비활성 직원": [row(H)], "포털에 없는 직원": [row(99999)],
            "직원 번호 글자": [row("131")], "직원 번호 참/거짓": [row(True)], "직원 번호 0": [row(0)],
            "생년월일 없는 날": [row(A, birth6="900230")], "생년월일 5자리": [row(A, birth6="90315")], "생년월일 숫자": [row(A, birth6=900315)],
            "기본급 0원": [row(A, base=0)], "기본급 음수": [row(A, base=-1)], "기본급 실수": [row(A, base=2418900.5)], "기본급 실수(.0)": [row(A, base=2418900.0)],
            "기본급 글자": [row(A, base="2418900")], "기본급 참/거짓": [row(A, base=True)], "능력급 10억 초과": [row(A, ability=1_000_000_001)],
            "직책수당 음수": [row(A, pos=-1)], "자격수당 실수": [row(A, qual=0.5)],
            "시작일 회차 전": [row(A, start=f"{Y}-02-28")], "시작일 회차 뒤": [row(A, start=f"{Y + 1}-03-01")], "시작일 없는 날": [row(A, start=f"{Y}-02-30")],
            "시작일 모양": [row(A, start=f"{Y}-4-1")], "시작일 숫자": [row(A, start=20270401)],
            "사원코드 주민번호 모양(대시)": [row(A, code="900315" + "-" + "1234567")], "사원코드 주민번호 모양(붙임)": [row(A, code="900315" + "1234567")],
            "사원코드 주민번호 모양(공백)": [row(A, code="900315" + " " + "1234567")], "사원코드 한글": [row(A, code="가나")], "사원코드 21자": [row(A, code="A" * 21)], "칸이 더 있음": [{**row(A), "extra": 1}], "start 칸 빠짐": [no_start],
            "줄이 사전 아님": ["x"], "줄 없음": [], "2001줄": [row(A)] * 2001}
        wrong = [k for k, rs in rows_bad.items() if up(hr, rs).status_code != 400]
        wrong += [k for k, r in (("회차 1999", post(hr, "/api/contracts/1999", {"rows": [row(A)]})), ("회차 글자", post(hr, "/api/contracts/20x7", {"rows": [row(A)]})),
                                 ("rows 목록 아님", post(hr, f"/api/contracts/{Y}", {"rows": "x"})), ("본문 2MB 넘음", post(hr, f"/api/contracts/{Y}", {"rows": [row(A)], "pad": "x" * 2_100_000})))
                  if r.status_code != 400]
        ok(not wrong and snap() == before, f"직원 · 금액 · 생년월일 · 시작일 · 사원코드 · 칸 · 줄 수 · 회차 모양 — 틀리면 400, 아무것도 안 바뀜 {wrong}")
        r = up(hr, [row(A, code="900315" + "-" + "1234567")])
        ok(r.status_code == 400 and "주민등록번호" in r.json["error"] and "900315" not in r.json["error"] and snap() == before and code_of(A) is None,
           "사원코드 칸에 주민번호 모양(앞 6자리 + 뒤 7자리 — 대시 · 붙임 · 공백)이 들어오면 400 · 이유에 값은 없음 · users.employee_no 에도 안 들어감")
        ok(up(hr, [row(A, code="CT-A1"), row(B, code="CT-A1")]).status_code == 400 and snap() == before, "같은 요청 안에서 두 사람에게 같은 사원코드를 붙이면 400 (아무것도 안 바뀜)")

        print("  - 올리기 · 잠금 · 사원코드 기억")
        r = up(hr, [row(A, code="CT-A1"), row(B, birth6="000229", start=f"{Y}-04-15"), row(E), row(F), row(G, site=100000, qual=50000)])
        ok(r.status_code == 200 and r.json == {"saved": 5}, "5명 올림 → saved 5")
        ids = {uid: cid for cid, uid in q("SELECT id, user_id FROM contracts WHERE year = ?", Y)}
        st = {uid: q("SELECT status, version, starts_on, prev_id FROM contracts WHERE id = ?", cid)[0] for uid, cid in ids.items()}
        ok(set(ids) == {A, B, E, F, G} and all(v[:2] == ("DRAFT", 1) and v[3] is None for v in st.values()) and st[A][2] == first and st[B][2] == f"{Y}-04-15",
           "모두 발송 전 · 1차 · 시작일은 비우면 회차 시작일")
        blobs = [b[0] for b in q("SELECT body FROM contracts WHERE year = ?", Y)]
        ok(all(len(b) > 28 and all(w.encode() not in b for w in ("가상", "2418900", "1211000", "900315", "CT-A1", "프로")) for b in blobs),
           "DB 에는 잠긴 글만 (이름 · 금액 · 생년월일 · 사원코드가 그대로 없음)")
        want = {"code": "CT-A1", "name": "가상계약A", "dept": "가상팀", "title": "프로", "birth6": "900315", "base": 2418900, "ability": 1211000, "pos": 300000,
                "site": 0, "qual": 0, "reason": ""}
        ok(opened(A, "body") == want and opened(B, "body")["title"] == "" and opened(B, "body")["code"] == "", "열쇠로 풀면 명단에서 채운 이름 · 소속 · 직급(없으면 빈 글자)과 올린 값")
        blob = q("SELECT body FROM contracts WHERE id = ?", ids[A])[0][0]
        aad_wrong = [f"ct|{ids[A]}|{Y}|{A}|body", f"ct|{Y}|{B}|1|body", f"ct|{Y}|{A}|2|body", f"ct|{Y}|{A}|1|company", f"ct|{Y + 1}|{A}|1|body"]

        def fails(aad):
            try:
                AESGCM(raw_key).decrypt(blob[:12], blob[12:], aad.encode())
            except InvalidTag:
                return True
            return False

        ok(all(fails(a) for a in aad_wrong), "다른 직원 · 다른 해 · 다른 차수 · 다른 칸 자리로 옮기면 안 풀림 (aad)")
        ok(code_of(A) == "CT-A1" and code_of(B) is None, "비어 있던 직원에게 올린 사원코드를 기억")
        n_rows, id_a = q("SELECT COUNT(*) FROM contracts")[0][0], ids[A]
        r = up(hr, [row(A, code="CT-A9", base=2500000, start=f"{Y}-05-01")])
        ok(r.json == {"saved": 1} and q("SELECT COUNT(*) FROM contracts")[0][0] == n_rows and q("SELECT id, version, status FROM contracts WHERE year = ? AND user_id = ?", Y, A) == [(id_a, 1, "DRAFT")]
           and opened(A, "body")["base"] == 2500000 and opened(A, "body")["code"] == "CT-A9" and q("SELECT starts_on FROM contracts WHERE id = ?", ids[A]) == [(f"{Y}-05-01",)],
           "같은 사람을 다시 올리면 발송 전 줄이 바뀜 (줄 수 · 번호 · 차수 그대로)")
        ok(code_of(A) == "CT-A1", "이미 사원코드가 있는 직원은 다른 코드로 올려도 그대로")
        up(hr, [row(A, code="CT-A1")])
        r = up(hr, [row(B, code="CT-A1")])
        ok(r.status_code == 400 and "이미 다른 직원에게 붙어" in r.json["error"] and code_of(B) is None, "남의 사원코드는 400 (급여명세서와 같은 안내)")
        ok(up(hr, [row(B, code="CT-B1", birth6="000229", start=f"{Y}-04-15")]).status_code == 200 and code_of(B) == "CT-B1", "빈 직원에게는 붙음")

        print("  - 현황")
        rows = rows_of()
        names = ["id", "year", "userId", "version", "status", "start", "end", "code", "name", "dept", "title", "birth6", "base", "ability", "pos", "site", "qual",
                 "annual", "reason", "sentAt", "due", "late", "signedAt", "replacedOn", "cancelledAt", "cancelReason", "ask", "prevId"]
        ra = rows[A]
        ok(set(rows) == {A, B, E, F, G} and all(set(r) == set(names) for r in rows.values()), "현황 줄 모양: 이름 · 금액 · 상태 · 시각 · 문의 · 앞 차수")
        ok(ra["status"] == "DRAFT" and ra["code"] == "CT-A1" and ra["name"] == "가상계약A" and ra["dept"] == "가상팀" and ra["title"] == "프로" and ra["start"] == first and ra["end"] == last
           and ra["year"] == Y and ra["version"] == 1 and ra["late"] is False and ra["ask"] is None and ra["prevId"] is None and ra["sentAt"] is None and ra["due"] is None
           and ra["reason"] == "" and rows[B]["start"] == f"{Y}-04-15" and rows[B]["birth6"] == "000229" and rows[B]["title"] == "", "A · B 줄 내용")
        ok(ra["annual"] == contract_doc.calc(2418900, 1211000, 300000)["annual"] == 49535908
           and rows[G]["annual"] == contract_doc.calc(2418900, 1211000, 300000, 100000, 50000)["annual"] and rows[G]["annual"] != ra["annual"],
           "총 계약 연봉은 contract_doc.calc 와 같음 (양식 샘플 49,535,908 · 현장관리수당은 연봉에서 빠짐)")
        ok(admin(Y + 1).json["rows"] == [] and admin("abc").status_code == 400 and admin("1999").status_code == 400 and get(hr, "/api/contracts/admin").json["year"] == contract_doc.upcoming_year(auth._today()),
           "다른 회차는 비어 있고, 해 모양이 틀리면 400, 해를 안 보내면 다가오는 회차")

        print("  - 설정 없이 발송")
        before = snap()
        r = send([ids[A]])
        ok(r.status_code == 409 and r.json["error"] == "계약서 설정에서 대표이사 이름과 직인을 먼저 넣어 주세요." and snap() == before, "설정이 없으면 발송 409 (아무것도 안 바뀜)")

        print("  - 설정 저장 · 직인")
        r = sv()
        seal1 = q("SELECT seal_id FROM contract_settings")[0][0]
        want_cfg = {"ceo": "○○○", "addr": "가상시 가상구 가상로 1", "kind": "가상 서비스업", "sealId": seal1}
        ok(r.status_code == 200 and r.json == {"ready": True, "settings": want_cfg} and cfg_of() == {"ready": True, "settings": want_cfg} and admin().json["settings"] is True,
           "저장하면 읽을 수 있고 현황 settings 참")
        cfg_blob, seal_blob = q("SELECT data FROM contract_settings")[0][0], q("SELECT png FROM contract_seals WHERE id = ?", seal1)[0][0]
        ok(all(w.encode() not in cfg_blob for w in ("○○○", "가상시", "서비스업")) and len(cfg_blob) > 28 and len(seal_blob) > 28 and not seal_blob.startswith(b"\x89PNG"),
           "DB 에는 잠긴 글만 (대표이사 이름 · 소재지 · 직인 그림이 그대로 없음)")
        ok(json.loads(AESGCM(raw_key).decrypt(cfg_blob[:12], cfg_blob[12:], b"ct|settings")) == {k: cfg[k] for k in ("ceo", "addr", "kind")}
           and AESGCM(raw_key).decrypt(seal_blob[:12], seal_blob[12:], f"ct|seal|{seal1}".encode()) == base64.b64decode(PNG), "설정은 aad ct|settings · 직인은 ct|직인번호 로 풀림")
        r = get(hr, "/api/contracts/settings/seal")
        ok(r.status_code == 200 and r.mimetype == "image/png" and r.data == base64.b64decode(PNG), "지금 직인 그림을 돌려줌")
        ok(audit("CONTRACT_SETTINGS") == [("contract_settings", None, '{"fields": ["ceo", "addr", "kind", "seal"]}')], "감사로그 CONTRACT_SETTINGS — 바뀐 칸 이름만")

        print("  - 조정 (발송 전만)")
        before = snap()
        adj_bad = {"사유 빈칸": {"reason": "  "}, "사유 201자": {"reason": "가" * 201}, "사유 제어문자": {"reason": "가\x00"}, "사유 글자 아님": {"reason": 5}, "기본급 0": {"base": 0},
                   "기본급 실수": {"base": 2600000.5}, "생년월일": {"birth6": "900230"}, "시작일 회차 밖": {"start": f"{Y}-02-28"}, "자격수당 음수": {"qual": -5},
                   "칸이 더 있음": {"extra": 1}}
        wrong = [k for k, ch in adj_bad.items() if adj(ids[A], **ch).status_code != 400]
        no_reason = {"base": 2600000, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0, "birth6": "900315", "start": None}
        wrong += ["사유 칸 없음"] if post(hr, f"/api/contracts/{ids[A]}/adjust", no_reason).status_code != 400 else []
        ok(not wrong and snap() == before, f"사유 1~200자(제어문자 거부) · 금액 · 생년월일 · 시작일 모양 — 틀리면 400, 아무것도 안 바뀜 {wrong}")
        ok(adj(99999).status_code == 404 and adj(0).status_code == 404 and post(hr, "/api/contracts/99999999999/adjust", {}).status_code == 404 and snap() == before, "없는 계약서 번호는 404")
        r = adj(ids[A], base=2600000, start=f"{Y}-05-01", reason="  가상 조정  ")
        ra = rows_of()[A]
        ok(r.status_code == 200 and ra["base"] == 2600000 and ra["start"] == f"{Y}-05-01" and ra["reason"] == "가상 조정" and ra["annual"] == contract_doc.calc(2600000, 1211000, 300000)["annual"],
           "조정 → 금액 · 시작일 · 사유(앞뒤 공백은 뺌)가 바뀌고 총 계약 연봉도 다시 셈")
        ok(opened(A, "body")["name"] == "가상계약A" and opened(A, "body")["code"] == "CT-A1" and opened(A, "body")["title"] == "프로", "이름 · 소속 · 직급 · 사원코드는 올릴 때 그대로")
        ok(adj(ids[A], base=2418900, start=None, reason="가상 되돌림").status_code == 200 and rows_of()[A]["start"] == first and rows_of()[A]["base"] == 2418900, "시작일을 비우면 회차 시작일")
        ok([(a[0], a[1], sorted(json.loads(a[2]))) for a in audit("CONTRACT_ADJUST")] == [("contract", ids[A], ["userId", "year"])] * 2, "감사로그 CONTRACT_ADJUST (조정 두 번) — 계약서 번호 · 해 · 직원 번호만")

        print("  - 빼기 (발송 전만)")
        n_rows = q("SELECT COUNT(*) FROM contracts")[0][0]
        ok(post(hr, "/api/contracts/99999/remove").status_code == 404 and q("SELECT COUNT(*) FROM contracts")[0][0] == n_rows, "없는 계약서 번호는 404")
        r = post(hr, f"/api/contracts/{ids[F]}/remove")
        ok(r.status_code == 200 and q("SELECT COUNT(*) FROM contracts")[0][0] == n_rows - 1 and not q("SELECT 1 FROM contracts WHERE id = ?", ids[F])
           and audit("CONTRACT_REMOVED") == [("contract", ids[F], json.dumps({"year": Y, "userId": F}))], "빼기 → 줄이 지워짐 + 감사로그 CONTRACT_REMOVED")
        ok(post(hr, f"/api/contracts/{ids[F]}/remove").status_code == 404, "지운 줄을 또 빼면 404")

        print("  - 발송")
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", E)
        x("UPDATE users SET has_contract = 0 WHERE id = ?", G)
        bad_send = [{"ids": [], "due": day(14)}, {"ids": "1", "due": day(14)}, {"ids": [ids[A], ids[A]], "due": day(14)}, {"ids": [True], "due": day(14)}, {"ids": [0], "due": day(14)},
                    {"ids": [2**40], "due": day(14)}, {"ids": ["1"], "due": day(14)}, {"ids": list(range(1, 2002)), "due": day(14)}, {"due": day(14)},
                    {"ids": [ids[A]], "due": day(-1)}, {"ids": [ids[A]], "due": f"{Y}-02-30"}, {"ids": [ids[A]]}, {"ids": [ids[A]], "due": "내일"}, {"ids": [ids[A]], "due": 20270301}]
        before = snap()
        wrong = [i for i, b in enumerate(bad_send) if post(hr, "/api/contracts/send", b).status_code != 400]
        ok(not wrong and snap() == before, f"ids 모양 · 마감일(어제 · 없는 날 · 빈칸 · 숫자) 틀리면 400, 아무것도 안 바뀜 {wrong}")
        r = send([ids[B]], day(0))
        ok(r.status_code == 200 and r.json == {"sent": 0, "skipped": [{"id": ids[B], "why": "포털 사용 꺼짐"}]} and audit("CONTRACT_SEND") == [], "마감일이 오늘이면 통과 · 포털 사용이 꺼진 사람은 건너뜀 (보낸 사람이 없으면 감사로그 없음)")
        r = send([ids[A], ids[B], ids[E], ids[G], ids[F]])
        ok(r.status_code == 200 and r.json == {"sent": 1, "skipped": [{"id": ids[B], "why": "포털 사용 꺼짐"}, {"id": ids[E], "why": "비활성"},
                                                                         {"id": ids[G], "why": "연봉계약서 대상 아님"}, {"id": ids[F], "why": "발송 전이 아님"}]},
           "발송 → A 만 나가고, 나머지는 이유와 함께 건너뜀 (포털 꺼짐 · 비활성 · 계약서 대상 아님 · 지워진 줄)")
        a = q("SELECT status, sent_at, sent_by, due_on, seal_id, company FROM contracts WHERE id = ?", ids[A])[0]
        company = opened(A, "company")
        ok(a[:5] == ("SENT", a[1], 1, day(14), seal1) and a[1] and company == {"name": contract_doc.COMPANY_NAME, **{k: cfg[k] for k in ("ceo", "addr", "kind")}}
           and all(w.encode() not in a[5] for w in ("○○○", "가상시")), "A 는 서명 대기 · 보낸 때와 사람 · 마감일 · 그때의 설정(잠금)과 직인 번호가 고정됨")
        ok([q("SELECT status FROM contracts WHERE id = ?", ids[u])[0][0] for u in (B, E, G)] == ["DRAFT"] * 3, "건너뛴 사람은 그대로 발송 전")
        ok(audit("CONTRACT_SEND") == [("contract_year", None, json.dumps({"year": Y, "people": 1, "due": day(14)}))], "감사로그 CONTRACT_SEND — 해 · 인원 수 · 마감일 (회차별 한 줄)")
        ra = rows_of()[A]
        ok(ra["status"] == "SENT" and ra["due"] == day(14) and ra["sentAt"] and ra["late"] is False, "현황: 서명 대기 · 마감일 · 보낸 때")
        ok(send([ids[A]]).json == {"sent": 0, "skipped": [{"id": ids[A], "why": "발송 전이 아님"}]}, "다시 발송하면 건너뜀")

        print("  - 발송한 뒤에는 건드릴 수 없음")
        before = snap()
        r = up(hr, [row(A, base=3000000)])
        ok(r.status_code == 409 and "이미 발송" in r.json["error"], "발송한 사람을 다시 올리면 409")
        ok(up(hr, [row(B, base=3100000), row(A, base=3000000)]).status_code == 409 and snap() == before, "발송한 사람이 섞여 있으면 전부 409 (다른 사람도 안 바뀜)")
        ok(adj(ids[A]).status_code == 409 and post(hr, f"/api/contracts/{ids[A]}/remove").status_code == 409 and snap() == before, "조정 · 빼기도 409")

        print("  - 설정을 바꿔도 이미 보낸 계약서는 그대로 · 여러 회차를 함께 발송")
        n_audit = len(audit("CONTRACT_SETTINGS"))
        ok(sv(seal=None).status_code == 200 and len(audit("CONTRACT_SETTINGS")) == n_audit, "바뀐 것이 없으면 그대로 (감사로그 더하지 않음)")
        r = sv(ceo="△△△", seal=None)
        ok(r.status_code == 200 and cfg_of()["settings"] == {**want_cfg, "ceo": "△△△"} and q("SELECT COUNT(*) FROM contract_seals") == [(1,)]
           and audit("CONTRACT_SETTINGS")[-1][2] == '{"fields": ["ceo"]}', "직인을 안 보내면 직인은 그대로 (바뀐 칸 이름 ceo 만 감사로그)")
        r = sv(ceo="△△△", seal=PNG2)
        seal2 = q("SELECT seal_id FROM contract_settings")[0][0]
        ok(r.status_code == 200 and seal2 == seal1 + 1 and q("SELECT COUNT(*) FROM contract_seals") == [(2,)] and audit("CONTRACT_SETTINGS")[-1][2] == '{"fields": ["seal"]}'
           and get(hr, "/api/contracts/settings/seal").data == base64.b64decode(PNG2), "직인을 보내면 새 직인 줄이 생기고 지금 직인이 바뀜 (옛 줄은 남음)")
        ok(opened(A, "company")["ceo"] == "○○○" and q("SELECT seal_id FROM contracts WHERE id = ?", ids[A]) == [(seal1,)]
           and q("SELECT 1 FROM contract_seals WHERE id = ?", seal1) != [], "이미 보낸 A 의 대표이사 이름 · 직인은 그대로")
        ok(up(hr, [row(I)]).json == {"saved": 1} and up(hr, [row(A, code="CT-A1"), row(I)], Y + 1).json == {"saved": 2}, "A 는 다른 회차에는 새로 올릴 수 있음")
        i_y = q("SELECT id FROM contracts WHERE year = ? AND user_id = ?", Y, I)[0][0]
        a_y1, i_y1 = (q("SELECT id FROM contracts WHERE year = ? AND user_id = ?", Y + 1, u)[0][0] for u in (A, I))
        r = send([i_y, a_y1, i_y1], day(7))
        ok(r.json == {"sent": 3, "skipped": []} and [json.loads(a[2]) for a in audit("CONTRACT_SEND")[1:]] == [{"year": Y, "people": 1, "due": day(7)}, {"year": Y + 1, "people": 2, "due": day(7)}],
           "두 회차를 함께 보내면 회차별로 감사로그 한 줄씩")
        ok(opened(A, "company", year=Y + 1)["ceo"] == "△△△" and q("SELECT seal_id FROM contracts WHERE id = ?", a_y1) == [(seal2,)], "그다음 발송부터는 바뀐 설정과 직인이 들어감")

        print("  - 마감이 지나면 late")
        for days, a_late, i_late in ((6, False, False), (7, False, False), (8, False, True), (14, False, True), (15, True, True)):
            auth._now = lambda days=days: real_now() + timedelta(days=days)
            late_hr = app.test_client()  # 시계를 옮기면 세션이 끊기므로 그 시계로 다시 로그인
            login(late_hr, new_token("hr"))
            got = rows_of(Y, late_hr)
            ok(got[A]["late"] is a_late and got[I]["late"] is i_late, f"{days}일 뒤: 마감 14일 · 7일 — 마감일 당일까지는 late 거짓, 지나면 참")
        auth._now = real_now

        print("  - 헤더 · 감사로그")
        calls = [admin(), get(hr, "/api/contracts/settings"), get(hr, "/api/contracts/settings/seal"), up(hr, [row(A)]), send([ids[A]]), up(hr, []), get(emp, "/api/contracts/admin"),
                 anon.get("/api/contracts/admin", base_url=BASE)]
        ok([r.status_code for r in calls] == [200, 200, 200, 409, 200, 400, 403, 401]
           and all(r.headers.get("Cache-Control") == "no-store" and r.headers.get("Referrer-Policy") == "no-referrer" for r in calls),
           "성공 · 거절(400 · 401 · 403 · 409) 응답 모두 Cache-Control: no-store + Referrer-Policy: no-referrer")
        logs = q("SELECT action, entity_type, entity_id, after_data FROM audit_logs WHERE action LIKE 'CONTRACT%' ORDER BY id")
        ok({"CONTRACT_SETTINGS", "CONTRACT_UPLOAD", "CONTRACT_ADJUST", "CONTRACT_REMOVED", "CONTRACT_SEND"} <= {a[0] for a in logs}, "감사로그 SETTINGS · UPLOAD · ADJUST · REMOVED · SEND")
        ok(all(set(json.loads(a[3])) == {"year", "people"} for a in logs if a[0] == "CONTRACT_UPLOAD"), "올리기 감사로그에는 해와 인원 수만")
        ok(not any(w in str(logs) for w in ("가상", "2418900", "2600000", "900315", "000229", "CT-A", "CT-B", "○○○", "△△△", "프로")), "감사로그에 이름 · 금액 · 생년월일 · 사원코드 · 직급 · 사유 · 대표이사 이름 없음")

        print("  - 직원 보기 (내 계약서 · 한 건 · 직인)")

        def client_of(uid):  # 그 사람으로 로그인한 새 창
            c = app.test_client()
            login(c, new_token(f"HW-{uid}"))
            return c

        J, K, L = 140, 141, 142  # J 서명 뒤 파기 후 재발송 · K 서명 뒤 파기만 · L 안 풀리는 줄
        for uid in (J, K, L):
            x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, has_contract) VALUES (?, ?, ?, 2, ?, 1, 1)",
              uid, f"가상계약{'JKL'[uid - J]}", f"ct{uid}@example.invalid", f"HW-{uid}")
        nm = {A: "가상계약A", I: "가상계약I", J: "가상계약J", K: "가상계약K", L: "가상계약L"}
        ca, ci, cd, cl = client_of(A), client_of(I), client_of(D), client_of(2)  # 직원 A · 직원 I · 본부장 · 팀장
        outsiders = (emp, cl, cd)  # 남의 계약서를 볼 수 없는 사람들 (직원 · 팀장 · 본부장)
        mine = lambda c: get(c, "/api/contracts/mine")  # noqa: E731
        sg = lambda c, cid, uid, **kw: post(c, f"/api/contracts/{cid}/sign", {"name": nm[uid], "consent": True, "image": PNG, **kw})  # noqa: E731
        cn = lambda cid, then="resend", c=hr, **kw: post(c, f"/api/contracts/{cid}/cancel", {"reason": "가상 파기 사유", "then": then, **kw})  # noqa: E731
        rp = lambda cid, c=hr, **kw: post(c, f"/api/contracts/{cid}/replace", {"base": 2700000, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0,  # noqa: E731
                                                                                 "start": f"{Y}-09-01", "reason": "가상 승진", **kw})
        by_id = lambda c: {m["id"]: m for m in mine(c).json["rows"]}  # noqa: E731
        kdate = lambda d: f"{d.year}년 {d.month}월 {d.day}일"  # noqa: E731
        text_of = lambda blocks: json.dumps(blocks, ensure_ascii=False)  # noqa: E731

        def unsealed(uid, field, ver=1, year=Y):  # 열쇠로 푼 바이트 그대로 (서명본 PDF)
            blob = q(f"SELECT {field} FROM contracts WHERE year = ? AND user_id = ? AND version = ?", year, uid, ver)[0][0]
            return AESGCM(raw_key).decrypt(blob[:12], blob[12:], f"ct|{year}|{uid}|{ver}|{field}".encode())

        ia, ib = ids[A], ids[B]
        co_y = {"name": contract_doc.COMPANY_NAME, "ceo": "○○○", "addr": cfg["addr"], "kind": cfg["kind"]}
        worker_a = {"name": "가상계약A", "birth6": "900315", "dept": "가상팀", "title": "프로"}
        pay_a = {"base": 2418900, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0}
        want_blocks = lambda signed_on=None, start=first: contract_doc.build_blocks(company=co_y, worker=worker_a, pay=pay_a, start=start, end=last, signed_on=signed_on)  # noqa: E731
        r = mine(ca)
        mine_keys = (set(names) - {"reason", "cancelReason"}) | {"blocks", "hash", "canSign"}
        row_y1, row_y = r.json["rows"][0], r.json["rows"][1]
        ok(r.status_code == 200 and [(m["year"], m["version"], m["status"]) for m in r.json["rows"]] == [(Y + 1, 1, "SENT"), (Y, 1, "SENT")]
           and all(set(m) == mine_keys for m in r.json["rows"]), "mine: 서명 대기 둘 (최근 회차부터) — 사유 · 파기 사유는 빠지고 본문 · 고유번호 · 서명 가능 여부가 붙음")
        ok(row_y["blocks"] == want_blocks() and "(서명한 날)" in text_of(row_y["blocks"]) and row_y["hash"] is None and row_y["canSign"] is True and row_y["ask"] is None
           and "△△△" in text_of(row_y1["blocks"]) and "△△△" not in text_of(row_y["blocks"]),
           "서명 대기 본문 = 발송 때 고정한 회사 칸 (설정을 바꿔도 옛 것은 그대로) · 서명한 날은 아직 (서명한 날) · 고유번호 없음")
        nonce = os.urandom(12)  # 확인 전 문의 글 (잠금 = nonce + 암호문) — 문의 길을 거치지 않고 칸에 직접 넣어 둔다
        x("UPDATE contracts SET ask = ?, ask_at = ? WHERE id = ?",
          nonce + AESGCM(raw_key).encrypt(nonce, json.dumps("가상 문의 글", ensure_ascii=False).encode(), f"ct|{Y + 1}|{I}|1|ask".encode()), "2026-10-01T00:00:00Z", i_y1)
        ask_row = next(m for m in mine(ci).json["rows"] if m["id"] == i_y1)
        ok(ask_row["ask"] == {"at": "2026-10-01T00:00:00Z", "closedAt": None} and "가상 문의 글" not in str(mine(ci).json)
           and next(m for m in admin(Y + 1).json["rows"] if m["id"] == i_y1)["ask"]["text"] == "가상 문의 글", "문의가 있어도 직원에게는 보낸 때 · 확인한 때만 (글은 HR 현황에만)")
        ok(mine(emp).json == {"rows": []} and mine(hr).json == {"rows": []}, "계약서가 없는 사람의 mine 은 빈 목록")

        print("  - 한 건 보기 · 직인 · 남의 것")
        r = get(ca, f"/api/contracts/{ia}")
        ok(r.status_code == 200 and r.json == row_y and audit("CONTRACT_VIEW") == [], "한 건 보기: 본인 것은 mine 의 줄과 같고 감사로그 없음")
        ok(all(get(c, f"/api/contracts/{ia}{tail}").status_code == 403 for c in outsiders for tail in ("", "/seal", "/pdf"))
           and all(get(ca, f"/api/contracts/{p}").status_code == 404 for p in ("99999", "99999/seal", "99999/pdf", f"{ia}/pdf")), "직원 · 팀장 · 본부장도 남의 계약서 · 직인 · PDF 는 403 · 없는 번호 404 · 서명 전 PDF 404")
        r = get(hr, f"/api/contracts/{ia}")
        ok(r.status_code == 200 and r.json == {**row_y, "canSign": False} and audit("CONTRACT_VIEW") == [("user", A, json.dumps({"contract": ia}))],
           "HR 는 남의 것도 볼 수 있고 (서명 가능은 거짓) 감사로그 CONTRACT_VIEW — 직원 번호 + 계약서 번호")
        r = get(hr, f"/api/contracts/{ib}")
        ok(r.status_code == 200 and r.json["status"] == "DRAFT" and r.json["canSign"] is False and "△△△" in text_of(r.json["blocks"]) and "(서명한 날)" in text_of(r.json["blocks"]),
           "HR 는 발송 전 줄도 지금 설정으로 미리 봄")
        cfg_row = q("SELECT id, data, seal_id, updated_at, updated_by FROM contract_settings")[0]
        x("DELETE FROM contract_settings")
        r, rs = get(hr, f"/api/contracts/{ib}"), get(hr, f"/api/contracts/{ib}/seal")
        x("INSERT INTO contract_settings (id, data, seal_id, updated_at, updated_by) VALUES (?, ?, ?, ?, ?)", *cfg_row)
        ok(r.status_code == 200 and "(계약서 설정 필요)" in text_of(r.json["blocks"]) and rs.status_code == 404, "설정이 없으면 대표이사 칸이 '(계약서 설정 필요)' · 발송 전 줄의 직인은 404")
        seals = [get(ca, f"/api/contracts/{ia}/seal"), get(ca, f"/api/contracts/{a_y1}/seal"), get(hr, f"/api/contracts/{ib}/seal")]
        ok([(s.status_code, s.mimetype, s.data) for s in seals] == [(200, "image/png", base64.b64decode(PNG)), (200, "image/png", base64.b64decode(PNG2)), (200, "image/png", base64.b64decode(PNG2))],
           "직인: 서명 대기 줄은 발송 때 고정한 직인 (올해 줄은 옛 직인 · 내년 줄은 새 직인) · 발송 전은 지금 설정의 직인")

        print("  - 서명 — 거절은 400 · 401 · 403 · 404 (아무것도 안 바뀜)")
        Image.frombytes("RGB", (300, 300), os.urandom(300 * 300 * 3)).save(buf := io.BytesIO(), "PNG")  # 멀쩡한 그림인데 200KB 를 넘음 (크기 상한만 걸리게)
        PNG_BIG = base64.b64encode(buf.getvalue()).decode()
        Image.frombytes("RGB", (200, 200), os.urandom(200 * 200 * 3)).save(buf := io.BytesIO(), "PNG")  # 12만 바이트쯤 — 한도 안의 큰 그림은 받는다
        PNG_NOISE = base64.b64encode(buf.getvalue()).decode()
        assert len(base64.b64decode(PNG_BIG)) > 200_000 > len(base64.b64decode(PNG_NOISE)) > 100_000
        before = snap()
        bad_sign = {"동의 거짓": {"consent": False}, "동의 글자": {"consent": "true"}, "동의 숫자": {"consent": 1},
                    "이름 다름": {"name": "가상계약B"}, "이름 앞 공백": {"name": " 가상계약A"}, "이름 뒤 공백": {"name": "가상계약A "}, "이름 빈칸": {"name": ""}, "이름 글자 아님": {"name": 5},
                    "그림 PNG 아님": {"image": base64.b64encode(b"hello").decode()}, "그림 base64 아님": {"image": "%%%"}, "그림 글자 아님": {"image": 7}, "그림 빈칸": {"image": ""},
                    "그림 머리만 PNG": {"image": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"junk" * 20).decode()}, "그림 데이터가 깨짐": {"image": PNG_BROKEN},
                    "그림 200KB 넘음": {"image": PNG_BIG}, "그림 픽셀이 너무 큼": {"image": PNG_HUGE}, "칸이 더 있음": {"extra": 1}}
        full = {"name": nm[A], "consent": True, "image": PNG}
        wrong = [k for k, ch in bad_sign.items() if sg(ca, ia, A, **ch).status_code != 400]
        wrong += [f"{k} 칸 빠짐" for k in full if post(ca, f"/api/contracts/{ia}/sign", {m: v for m, v in full.items() if m != k}).status_code != 400]
        ok(not wrong and snap() == before, f"동의 · 성명(앞뒤 공백도 그대로 본인 이름과 같아야 함) · 서명 그림(PNG 머리 · 200KB 이하 · 끝까지 읽히는 그림) · 칸이 틀리면 400 {wrong}")
        ok([sg(c, ia, A).status_code for c in (emp, cl, cd, hr)] == [403] * 4 and sg(ca, 99999, A).status_code == 404 and sg(ca, ib, A).status_code == 404
           and anon.post(f"/api/contracts/{ia}/sign", base_url=BASE, headers=ORIGIN, json=full).status_code == 401 and post(ca, f"/api/contracts/{ia}/sign", full, headers={}).status_code == 403
           and snap() == before, "남의 계약서는 누구도 (HR 도) 서명할 수 없음 403 · 없는 번호 · 남의 발송 전 줄 404 · 로그인 없이 401 · Origin 없이 403")

        print("  - 서명 — 마감이 지나도 받고 · 한국 날짜로 체결일 · 고정한 직인 · 고유번호 = 저장한 서명본")
        spy = []
        contract_doc.render_pdf = lambda blocks, **kw: (spy.append((blocks, kw)), real_render(blocks, **kw))[1]  # 부른 값을 기록하고 그대로 만든다
        t = (real_now() + timedelta(days=20)).replace(hour=16, minute=30, second=0)  # 마감(14일)을 지난 날 · UTC 16:30 = 한국 다음 날 01:30
        kst = t + timedelta(hours=9)
        auth._now = lambda: t
        ca_late = client_of(A)  # 시계를 옮기면 세션이 끊기므로 그 시계로 다시 로그인
        r = sg(ca_late, ia, A)
        auth._now = real_now
        contract_doc.render_pdf = real_render
        h = r.json.get("hash", "")
        st = q("SELECT status, signed_at, doc_hash FROM contracts WHERE id = ?", ia)[0]
        ok(r.status_code == 200 and set(r.json) == {"signedAt", "hash"} and r.json["signedAt"] == auth._iso(t) and len(h) == 64 and set(h) <= set("0123456789abcdef")
           and st == ("SIGNED", auth._iso(t), h), "서명 → 서명 완료 · 서명한 때 · 문서 고유번호(SHA-256 64자) 저장 (마감 14일이 지난 뒤에도 받음)")
        rec = opened(A, "sign")
        ok(len(spy) == 1 and contract_doc.doc_hash(spy[0][0], rec["salt"], rec["tpl"]) == h and spy[0][1]["seal_png"] == base64.b64decode(PNG) and spy[0][1]["sign_png"] == base64.b64decode(PNG)
           and spy[0][1]["esign"] == {"signedAt": kst.strftime("%Y-%m-%d %H:%M"), "name": "가상계약A", "how": "하이웍스 로그인", "hash": h},
           "서명본에 넣은 것: 그 본문의 고유번호 · 발송 때 고정한 직인 (지금 설정의 새 직인이 아님) · 서명 그림 · 전자서명 정보 (한국 시각 · 이름 · 본인 확인 방법)")
        ok(set(rec) == {"image", "name", "consent", "ip", "ua", "kind", "blocks", "tpl", "salt"} and rec["name"] == "가상계약A" and rec["consent"] is True and rec["kind"] == "pc" and isinstance(rec["ip"], str)
           and base64.b64decode(rec["image"]) == base64.b64decode(PNG) and rec["blocks"] == spy[0][0] and rec["tpl"] == contract_doc.TPL,
           "서명 기록 (잠금): 그림 · 입력한 성명 · 동의 · IP · 브라우저 · 로그인 종류 · 해시를 매기고 PDF 에 그린 본문 블록 그대로 · 그때의 양식 판 번호 · 고유번호의 소금")
        unsalted = hashlib.sha256(json.dumps({"tpl": rec["tpl"], "blocks": rec["blocks"]}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        ok(len(rec["salt"]) == 32 and set(rec["salt"]) <= set("0123456789abcdef") and h != unsalted and h == contract_doc.doc_hash(rec["blocks"], rec["salt"], rec["tpl"])
           and h != contract_doc.doc_hash(rec["blocks"], "0" * 32, rec["tpl"]),
           "고유번호 = 소금(16바이트 무작위 16진수 32자) 을 섞은 해시 — 공개된 양식 · 본문만으로 계산한 번호(소금 없음)와 다르다 (금액을 맞혀 보는 데 못 쓴다)")
        mine_y = by_id(ca)[ia]
        ok(t.date() != kst.date() and mine_y["status"] == "SIGNED" and mine_y["signedAt"] == auth._iso(t) and mine_y["hash"] == h and mine_y["canSign"] is False
           and mine_y["blocks"] == want_blocks(kst.date().isoformat()) and kdate(kst) in text_of(mine_y["blocks"]) and "(서명한 날)" not in text_of(mine_y["blocks"])
           and hash_ok(mine_y), "mine: 서명한 줄의 본문 = 체결일 (서명한 때의 한국 날짜 — UTC 로는 전날) · 본문 + 서명 기록의 소금 · 양식 판으로 다시 계산한 고유번호 = 저장한 고유번호")
        shown = [json.dumps(mine_y, ensure_ascii=False), get(ca, f"/api/contracts/{ia}").text, get(hr, f"/api/contracts/{ia}").text, json.dumps(admin(Y).json, ensure_ascii=False),
                 str(audit("CONTRACT_SIGNED")), str(q("SELECT doc_hash FROM contracts WHERE id = ?", ia))]
        ok(not any(rec["salt"] in t_ for t_ in shown), "소금은 화면(mine · 한 건 보기 · HR 현황) · 감사로그 · 잠그지 않는 칸 어디에도 나오지 않는다 — 잠근 서명 기록에만 있다")
        ok(mine_y["how"] == "하이웍스 로그인" and base64.b64decode(mine_y["signImage"]) == base64.b64decode(PNG) and "how" not in row_y and "signImage" not in row_y
           and get(ca, f"/api/contracts/{ia}").json == mine_y and get(hr, f"/api/contracts/{ia}").json == {**mine_y, "canSign": False}
           and not any("how" in m or "signImage" in m for m in admin(Y).json["rows"]),
           "서명한 줄: mine · 한 건 보기(본인 · HR)에 본인 확인 방법(how — 서명본 PDF 와 같은 글) · 서명 그림(signImage) / 서명 대기 줄 · HR 현황(admin) 줄에는 없다")
        pdf, stored = unsealed(A, "pdf"), q("SELECT pdf FROM contracts WHERE id = ?", ia)[0][0]
        r = get(ca, f"/api/contracts/{ia}/pdf")
        ok(pdf.startswith(b"%PDF") and b"%PDF" not in stored and r.status_code == 200 and r.mimetype == "application/pdf" and r.data == pdf
           and r.headers["Content-Disposition"] == f'inline; filename="contract_{Y}.pdf"' and audit("CONTRACT_PDF_VIEW") == [],
           "서명본 PDF: 잠가 저장 · 본인에게는 저장된 그대로 내줌 (파일 이름에 이름 없음) · 본인이 보면 감사로그 없음")
        ok(audit("CONTRACT_SIGNED") == [("contract", ia, json.dumps({"contract": ia, "hash": h}))] and rows_of()[A]["status"] == "SIGNED" and rows_of()[A]["signedAt"] == auth._iso(t),
           "감사로그 CONTRACT_SIGNED — 계약서 번호 · 문서 고유번호 / HR 현황: 서명 완료")
        before = snap()
        ok(sg(ca, ia, A).status_code == 409 and snap() == before and mine(ca).json["rows"][1]["hash"] == h, "다시 서명하면 409 (서명한 것은 그대로)")
        ok(adj(ia).status_code == 409 and post(hr, f"/api/contracts/{ia}/remove").status_code == 409 and up(hr, [row(A)]).status_code == 409 and snap() == before, "서명 완료 줄은 조정 · 빼기 · 다시 올리기 모두 409")
        r = get(hr, f"/api/contracts/{ia}/pdf")
        logs_pdf = audit("CONTRACT_PDF_VIEW")
        ok(r.status_code == 200 and r.data == pdf and logs_pdf == [("user", A, json.dumps({"contract": ia, "year": Y}))] and not any(w in str(logs_pdf) for w in ("가상", "2418900", "900315")),
           "HR 가 남의 서명본을 보면 감사로그 CONTRACT_PDF_VIEW — 직원 번호 · 계약서 번호 · 해만 (이름 · 금액 없음)")
        ok(all(get(c, f"/api/contracts/{ia}/pdf").status_code == 403 for c in outsiders), "서명본 PDF 도 직원 · 팀장 · 본부장은 남의 것 403")

        print("  - 양식이 나중에 바뀌어도 서명한 본문은 그대로 (서명 전 줄만 지금 양식)")
        patched = [{"t": "title", "text": "바뀐 양식"}]
        contract_doc.TPL, contract_doc.build_blocks = "2099-9", lambda **kw: patched  # 양식 판 번호 · 문장을 바꾼 새 버전
        try:
            got_mine, got_one, got_hr = by_id(ca), get(ca, f"/api/contracts/{ia}").json, get(hr, f"/api/contracts/{ia}").json
        finally:
            contract_doc.TPL, contract_doc.build_blocks = real_tpl, real_build
        ok(got_mine[ia]["blocks"] == got_one["blocks"] == got_hr["blocks"] == rec["blocks"] == spy[0][0] != patched and got_mine[ia]["hash"] == got_one["hash"] == got_hr["hash"] == h
           and contract_doc.doc_hash(got_one["blocks"], rec["salt"], rec["tpl"]) == h and got_mine[a_y1]["blocks"] == patched,
           "양식 (판 번호 · 문장) 이 바뀐 뒤에도 mine · 한 건 보기 (본인 · HR) 는 서명한 줄에 서명할 때의 본문을 그대로 내줌 — 고유번호도 그대로 맞음 · 서명 전 줄만 새 양식")

        print("  - 두 번 동시에 서명 → 하나만")
        c1, c2, gate, codes = client_of(I), client_of(I), threading.Barrier(2), []

        def go(c):
            gate.wait()
            codes.append(sg(c, i_y, I).status_code)

        ths = [threading.Thread(target=go, args=(c,)) for c in (c1, c2)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        ok(sorted(codes) == [200, 409] and q("SELECT COUNT(*) FROM contracts WHERE year = ? AND user_id = ? AND status = 'SIGNED'", Y, I) == [(1,)]
           and q("SELECT status FROM contracts WHERE id = ?", i_y) == [("SIGNED",)] and len(audit("CONTRACT_SIGNED")) == 2, "같은 서명을 동시에 두 번 보내면 하나만 200 · 나머지 409 · 서명 완료 줄은 하나")

        x("UPDATE users SET name = '가상계약X' WHERE id = ?", I)  # 올린 뒤에 이름이 바뀐 사람
        before = snap()
        r = sg(ci, i_y1, I, name="가상계약X")
        x("UPDATE users SET name = '가상계약I' WHERE id = ?", I)
        ok(r.status_code == 409 and "이름" in r.json["error"] and snap() == before, "계약서에 적힌 이름 (올릴 때 고정) 과 지금 이름이 다르면 서명 못 함 409 (서명한 사람과 계약서의 근로자가 어긋나지 않게)")

        print("  - 새 계약으로 바꾸기 (연중 변경) → 발송 → 서명 → 옛 계약서가 '바뀜'")
        before = snap()
        ok(rp(ia, c=emp).status_code == 403 and rp(ia, c=ca).status_code == 403 and rp(99999).status_code == 404 and rp(a_y1).status_code == 409 and rp(ib).status_code == 409 and snap() == before,
           "HR 만 (직원은 본인 것도 403) · 없는 번호 404 · 서명 완료가 아니면 (서명 대기 · 발송 전) 409")
        rp_bad = {"기본급 0": {"base": 0}, "기본급 실수": {"base": 2700000.5}, "사유 빈칸": {"reason": "  "}, "사유 201자": {"reason": "가" * 201}, "사유 제어문자": {"reason": "가\x00"},
                  "바뀌는 날 없음": {"start": None}, "바뀌는 날 = 지금 계약 시작일": {"start": first}, "바뀌는 날이 회차 앞": {"start": f"{Y}-02-28"},
                  "바뀌는 날이 회차 뒤": {"start": f"{Y + 1}-03-01"}, "바뀌는 날 없는 날": {"start": f"{Y}-09-31"}, "바뀌는 날 모양": {"start": f"{Y}-9-1"}, "바뀌는 날 숫자": {"start": 20270901},
                  "칸이 더 있음": {"extra": 1}, "생년월일 칸": {"birth6": "900315"}}
        wrong = [k for k, ch in rp_bad.items() if rp(ia, **ch).status_code != 400]
        wrong += ["사유 칸 없음"] if post(hr, f"/api/contracts/{ia}/replace", {"base": 2700000, "ability": 1, "pos": 0, "site": 0, "qual": 0, "start": f"{Y}-09-01"}).status_code != 400 else []
        ok(not wrong and snap() == before, f"금액 · 사유 · 바뀌는 날 (회차 안이고 지금 계약 시작일 뒤) · 칸이 틀리면 400, 아무것도 안 바뀜 {wrong}")
        r = rp(ia)
        v2 = r.json.get("id")
        row2 = q("SELECT status, version, prev_id, starts_on, sent_at, created_by FROM contracts WHERE id = ?", v2)[0] if v2 else None
        ok(r.status_code == 200 and row2 == ("DRAFT", 2, ia, f"{Y}-09-01", None, 1) and q("SELECT status FROM contracts WHERE id = ?", ia) == [("SIGNED",)]
           and audit("CONTRACT_REPLACE") == [("contract", ia, json.dumps({"contract": ia}))], "새 계약 → 같은 회차의 2차 발송 전 줄 (앞 차수 = 옛 줄) · 옛 줄은 서명 완료 그대로 · 감사로그 CONTRACT_REPLACE")
        blob2 = q("SELECT body FROM contracts WHERE id = ?", v2)[0][0]
        try:
            AESGCM(raw_key).decrypt(blob2[:12], blob2[12:], f"ct|{Y}|{A}|1|body".encode())
            moved = True
        except InvalidTag:
            moved = False
        ok(opened(A, "body", ver=2) == {**opened(A, "body"), "base": 2700000, "reason": "가상 승진"} and not moved and blob2 != q("SELECT body FROM contracts WHERE id = ?", ia)[0][0],
           "새 줄의 내용: 이름 · 소속 · 직급 · 생년월일 · 사원코드는 옛 줄 그대로 + 새 금액 · 사유 / 새 차수 자리로 다시 잠겨 옛 자리로는 안 풀림")
        before = snap()
        ok(rp(ia).status_code == 409 and cn(ia, "resend").status_code == 409 and cn(ia, "draft").status_code == 409 and snap() == before,
           "이미 진행 중인 (발송 전) 줄이 있으면 또 바꾸기 409 · 파기 후 재발송 / 새로 쓰기도 409 (한 사람 · 한 회차에 진행 중 줄은 하나)")
        ok(v2 not in by_id(ca) and get(ca, f"/api/contracts/{v2}").status_code == 404 and get(ca, f"/api/contracts/{v2}/seal").status_code == 404 and sg(ca, v2, A).status_code == 404
           and get(hr, f"/api/contracts/{v2}").json["status"] == "DRAFT", "발송 전 새 계약은 직원에게 안 보임 (mine · 한 건 · 직인 · 서명 모두 없는 것처럼) · HR 는 미리 봄")
        ok(adj(v2, start=None).status_code == 400 and adj(v2, start=first).status_code == 400 and adj(v2, start=f"{Y}-09-01", base=2750000).status_code == 200,
           "새 계약 줄의 조정도 바뀌는 날은 지금 서명된 계약의 시작일 뒤여야 함 (비우면 회차 시작일 = 같은 날이라 400)")
        ok(send([v2], day(10)).json == {"sent": 1, "skipped": []}, "새 계약 발송")
        rows_a = by_id(ca)
        ok([(m["year"], m["version"], m["status"], m["canSign"]) for m in rows_a.values()] == [(Y + 1, 1, "SENT", True), (Y, 2, "SENT", True), (Y, 1, "SIGNED", False)]
           and f"{Y}년 9월 1일부터" in text_of(rows_a[v2]["blocks"]) and "(서명한 날)" in text_of(rows_a[v2]["blocks"]),
           "mine: 새 계약 (서명 대기) 이 위에 · 지금 유효한 계약서 (서명 완료) 가 함께 — 새 계약 본문은 바뀌는 날부터")
        before = snap()
        contract_doc.render_pdf = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("깨짐"))  # 서명본을 못 만들면 아무것도 저장하지 않는다
        with unittest.TestCase().assertLogs("contract", "ERROR") as cm:
            r = sg(ca, v2, A)
        contract_doc.render_pdf = real_render
        ok(r.status_code == 400 and r.json["error"] == "서명 그림을 읽을 수 없어요" and snap() == before and q("SELECT status FROM contracts WHERE id IN (?, ?) ORDER BY id", ia, v2) == [("SIGNED",), ("SENT",)]
           and not any(w in line for line in cm.output for w in ("가상", "2418900", "900315")), "서명본을 못 만들면 400 · 옛 줄도 새 줄도 그대로 (서명 · 바뀜 모두 안 됨) · 서버 로그에 이름 · 금액 없음")
        r = sg(ca, v2, A)
        old = q("SELECT status, replaced_on, signed_at, doc_hash, pdf FROM contracts WHERE id = ?", ia)[0]
        ok(r.status_code == 200 and old == ("REPLACED", f"{Y}-09-01", auth._iso(t), h, stored) and q("SELECT status FROM contracts WHERE id = ?", v2) == [("SIGNED",)]
           and q("SELECT COUNT(*) FROM contracts WHERE year = ? AND user_id = ? AND status = 'SIGNED'", Y, A) == [(1,)]
           and audit("CONTRACT_REPLACED") == [("contract", v2, json.dumps({"old": ia, "new": v2}))] and len(audit("CONTRACT_SIGNED")) == 3,
           "새 계약 서명 → 옛 줄이 '새 계약으로 바뀜' (바뀐 날 = 새 시작일 · 옛 서명 · 고유번호 · 서명본 그대로) · 서명 완료는 하나뿐 · 감사로그 CONTRACT_REPLACED (옛 번호 · 새 번호)")
        rows_a = by_id(ca)
        ok([(m["version"], m["status"]) for m in rows_a.values() if m["year"] == Y] == [(2, "SIGNED"), (1, "REPLACED")] and rows_a[ia]["replacedOn"] == f"{Y}-09-01"
           and all(hash_ok(rows_a[c]) for c in (ia, v2)) and opened(A, "sign")["salt"] != opened(A, "sign", 2)["salt"] and get(ca, f"/api/contracts/{ia}/pdf").data == pdf and get(hr, f"/api/contracts/{ia}/pdf").data == pdf,
           "바뀐 뒤에도 옛 줄의 본문은 서명 때와 같은 고유번호 · 옛 서명본 PDF 도 그대로 내줌")
        before = snap()
        ok(cn(ia).status_code == 409 and cn(ia, "none").status_code == 409 and snap() == before, "'새 계약으로 바뀜' 줄은 파기할 수 없음 (409)")

        print("  - 파기 (서명 대기) → 재발송 / 새로 쓰기")
        before = snap()
        bad_cancel = [{"reason": "가상", "then": "x"}, {"reason": "  ", "then": "none"}, {"reason": "가" * 201, "then": "none"}, {"then": "none"}, {"reason": "가상"},
                      {"reason": "가상", "then": "none", "extra": 1}, {"reason": 5, "then": "none"}, {"reason": "가\x00", "then": "none"}, {"reason": "가상", "then": ["none"]}]
        ok(all(post(hr, f"/api/contracts/{a_y1}/cancel", b).status_code == 400 for b in bad_cancel) and cn(a_y1, c=ca).status_code == 403 and cn(a_y1, c=emp).status_code == 403
           and cn(99999).status_code == 404 and cn(ib).status_code == 409 and snap() == before, "사유 (1~200자) · then (resend / draft / none) 틀리면 400 · HR 만 · 없는 번호 404 · 발송 전 줄은 409")
        old_row = q("SELECT body, company, due_on, starts_on, seal_id FROM contracts WHERE id = ?", a_y1)[0]
        r = cn(a_y1, "resend", reason="  가상 금액 오류  ")
        new = r.json.get("id")
        o = q("SELECT status, cancelled_by, cancel_reason, body, company, due_on, starts_on, seal_id FROM contracts WHERE id = ?", a_y1)[0]
        n = q("SELECT status, version, prev_id, starts_on, due_on, seal_id, sent_by, sent_at IS NOT NULL FROM contracts WHERE id = ?", new)[0] if new else None
        ok(r.status_code == 200 and o[:3] == ("CANCELLED", 1, "가상 금액 오류") and o[3:] == old_row and q("SELECT cancelled_at IS NOT NULL FROM contracts WHERE id = ?", a_y1) == [(1,)]
           and n == ("SENT", 2, a_y1, old_row[3], old_row[2], old_row[4], 1, 1), "파기 후 재발송 → 원래 줄은 파기 (사유 · 사람 · 때) · 새 차수는 서명 대기 (같은 시작일 · 마감일 · 직인, 앞 차수 = 원래 줄)")
        ok(opened(A, "body", ver=2, year=Y + 1) == opened(A, "body", year=Y + 1) and opened(A, "company", ver=2, year=Y + 1) == opened(A, "company", year=Y + 1)
           and q("SELECT body FROM contracts WHERE id = ?", new)[0][0] != old_row[0] and q("SELECT company FROM contracts WHERE id = ?", new)[0][0] != old_row[1],
           "새 차수의 내용 · 회사 칸은 같은 값으로 새 차수 자리에 다시 잠김")
        ok(audit("CONTRACT_CANCELLED") == [("contract", a_y1, json.dumps({"contract": a_y1, "then": "resend"}))] and "가상 금액 오류" not in str(q("SELECT * FROM audit_logs")),
           "감사로그 CONTRACT_CANCELLED — 계약서 번호 · then (사유는 안 남김)")
        rows_a = by_id(ca)
        ok([(m["version"], m["status"], m["canSign"], m["hash"]) for m in rows_a.values() if m["year"] == Y + 1] == [(2, "SENT", True, None), (1, "CANCELLED", False, None)]
           and all(set(m) == mine_keys | ({"how", "signImage"} if m["signedAt"] else set()) for m in rows_a.values()) and rows_a[a_y1]["cancelledAt"] and sg(ca, a_y1, A).status_code == 409
           and [(m["version"], m["status"], m["cancelReason"]) for m in admin(Y + 1).json["rows"] if m["userId"] == A] == [(1, "CANCELLED", "가상 금액 오류"), (2, "SENT", None)],
           "mine: 새 차수가 서명 대기로, 파기한 줄은 '이전 계약서' (사유는 안 보임) — 파기한 줄은 서명할 수 없음 (409) · HR 현황에는 파기 사유")
        r = cn(new, "draft")
        d3 = r.json.get("id")
        row3 = q("SELECT status, version, prev_id, starts_on, due_on, company, seal_id, sent_at FROM contracts WHERE id = ?", d3)[0] if d3 else None
        ok(r.status_code == 200 and row3 == ("DRAFT", 3, new, old_row[3], None, None, None, None) and q("SELECT status FROM contracts WHERE id = ?", new) == [("CANCELLED",)]
           and opened(A, "body", ver=3, year=Y + 1) == opened(A, "body", year=Y + 1) and d3 not in by_id(ca), "파기 후 새로 쓰기 → 다음 차수는 발송 전 (같은 내용 · 시작일) · 직원에게는 안 보임")
        before = snap()
        ok(cn(d3, "none").status_code == 409 and cn(a_y1, "none").status_code == 409 and snap() == before, "발송 전 줄 · 이미 파기한 줄의 파기는 409")

        print("  - 서명 뒤 파기 → 재발송 / 파기만")
        ok(up(hr, [row(J), row(K, start=f"{Y}-04-15")]).json == {"saved": 2}, "J · K 올림")
        jid, kid = (q("SELECT id FROM contracts WHERE year = ? AND user_id = ?", Y, u)[0][0] for u in (J, K))
        ok(send([jid, kid], day(10)).json == {"sent": 2, "skipped": []}, "J · K 발송")
        cj, ck = client_of(J), client_of(K)
        x("UPDATE sessions SET kind = 'phone' WHERE token_hash = ?", auth._sha(cookie(cj)))  # J 는 휴대폰 등록으로 들어온 창
        spy.clear()
        contract_doc.render_pdf = lambda blocks, **kw: (spy.append((blocks, kw)), real_render(blocks, **kw))[1]
        rj, rk = sg(cj, jid, J), sg(ck, kid, K, image=PNG_NOISE)
        contract_doc.render_pdf = real_render
        ok(rj.status_code == 200 and rk.status_code == 200 and [s[1]["esign"]["how"] for s in spy] == ["휴대폰 등록", "하이웍스 로그인"]
           and opened(J, "sign")["kind"] == "phone" and opened(K, "sign")["kind"] == "pc" and opened(K, "sign")["image"] == PNG_NOISE and spy[1][1]["sign_png"] == base64.b64decode(PNG_NOISE),
           "본인 확인 방법: 휴대폰 등록으로 들어온 창은 '휴대폰 등록' · 아니면 '하이웍스 로그인' / 한도 (200KB) 안의 큰 서명 그림은 받아 그대로 서명본에 넣음")
        ok(by_id(cj)[jid]["how"] == "휴대폰 등록" and by_id(ck)[kid]["how"] == "하이웍스 로그인" and by_id(ck)[kid]["signImage"] == PNG_NOISE,
           "화면에 주는 본인 확인 방법도 서명한 창의 종류 그대로 (휴대폰 등록 / 하이웍스 로그인) · 큰 서명 그림도 그대로")
        before = snap()
        ok(rp(kid, start=f"{Y}-04-15").status_code == 400 and rp(kid, start=f"{Y}-04-01").status_code == 400 and snap() == before,
           "바뀌는 날은 지금 계약 시작일 (4/15) 보다 뒤여야 함 — 같은 날 · 회차 안의 앞선 날 모두 400")
        x("UPDATE users SET portal_enabled = 0 WHERE id = ?", J)
        r = cn(jid, "resend")
        x("UPDATE users SET portal_enabled = 1 WHERE id = ?", J)
        ok(r.status_code == 409 and "포털 사용 꺼짐" in r.json["error"] and snap() == before, "로그인할 수 없는 사람에게는 재발송 못 함 (409 — 파기도 안 됨)")
        signed_before = q("SELECT sign, doc_hash, pdf, signed_at, body FROM contracts WHERE id = ?", jid)[0]
        r = cn(jid, "resend")
        j2 = r.json.get("id")
        ok(r.status_code == 200 and q("SELECT status FROM contracts WHERE id = ?", jid) == [("CANCELLED",)] and q("SELECT sign, doc_hash, pdf, signed_at, body FROM contracts WHERE id = ?", jid)[0] == signed_before
           and q("SELECT status, version, prev_id, signed_at FROM contracts WHERE id = ?", j2) == [("SENT", 2, jid, None)], "서명 완료 줄도 파기 후 재발송 — 서명 · 서명본은 그대로 남고 새 차수는 서명 대기")
        mj = by_id(cj)
        ok(mj[jid]["status"] == "CANCELLED" and mj[jid]["canSign"] is False and hash_ok(mj[jid]) and mj[j2]["canSign"] is True
           and get(cj, f"/api/contracts/{jid}/pdf").data == unsealed(J, "pdf"), "서명 뒤 파기한 줄: mine 에 남고 본문은 서명 때와 같은 고유번호 · 서명본 PDF 도 그대로 내줌")
        r = sg(cj, j2, J)
        ok(r.status_code == 200 and q("SELECT status FROM contracts WHERE user_id = ? ORDER BY version", J) == [("CANCELLED",), ("SIGNED",)] and len(audit("CONTRACT_REPLACED")) == 1,
           "재발송 줄 서명 → 서명 완료는 하나 (파기한 옛 줄은 그대로 파기 — '바뀜' 아님)")
        r = cn(kid, "none")
        live = q("SELECT COUNT(*) FROM contracts WHERE user_id = ? AND status IN ('DRAFT', 'SENT', 'SIGNED')", K)
        mk = by_id(ck)[kid]
        ok(r.status_code == 200 and r.json.get("id") is None and q("SELECT status FROM contracts WHERE id = ?", kid) == [("CANCELLED",)] and live == [(0,)]
           and mk["status"] == "CANCELLED" and hash_ok(mk) and get(ck, f"/api/contracts/{kid}/pdf").data == unsealed(K, "pdf") and cn(kid, "none").status_code == 409,
           "파기만 → 다음 차수 없음 · 살아 있는 줄 없음 · 파기한 서명본은 계속 볼 수 있음 · 또 파기하면 409")

        print("  - 새 계약을 파기 후 재발송한 줄을 서명해도 옛 줄이 '바뀜' (앞 차수가 파기한 줄이어도 서명 완료는 하나)")
        r = rp(j2)  # J 의 서명 완료 줄 (2차) 을 새 계약으로 → 발송 → 파기 후 재발송 → 서명
        j3 = r.json.get("id")
        ok(r.status_code == 200 and send([j3], day(10)).json == {"sent": 1, "skipped": []}, "J: 새 계약 (3차) 을 만들어 발송")
        r = cn(j3, "resend")
        j4 = r.json.get("id")
        r = sg(cj, j4, J)
        ok(r.status_code == 200 and q("SELECT status FROM contracts WHERE user_id = ? ORDER BY version", J) == [("CANCELLED",), ("REPLACED",), ("CANCELLED",), ("SIGNED",)]
           and q("SELECT prev_id, replaced_on FROM contracts WHERE id IN (?, ?) ORDER BY id", j2, j4) == [(jid, f"{Y}-09-01"), (j3, None)]
           and audit("CONTRACT_REPLACED")[-1] == ("contract", j4, json.dumps({"old": j2, "new": j4})),
           "새 계약 (3차) 을 파기 후 재발송한 4차 줄을 서명 → 지금 유효하던 2차 줄이 '바뀜' (4차의 앞 차수는 파기한 3차) · 서명 완료는 하나")

        print("  - 안 풀리는 줄 (다른 열쇠로 잠긴 줄) — 목록에서 빠지고 500 없음")
        junk = lambda: os.urandom(40)  # noqa: E731  (열쇠 · 자리가 달라 풀리지 않는 29바이트 이상 글)
        stamp = auth._iso(real_now())
        x("INSERT INTO contracts (year, user_id, version, status, starts_on, body, created_at, created_by, sent_at, sent_by, due_on, company, seal_id, signed_at, sign, doc_hash, pdf)"
          " VALUES (?, ?, 1, 'SIGNED', ?, ?, ?, 1, ?, 1, ?, ?, ?, ?, ?, ?, ?)", Y, L, first, junk(), stamp, stamp, day(10), junk(), seal1, stamp, junk(), "a" * 64, junk())
        x("INSERT INTO contracts (year, user_id, version, status, starts_on, body, created_at, created_by, sent_at, sent_by, due_on, company, seal_id)"
          " VALUES (?, ?, 2, 'SENT', ?, ?, ?, 1, ?, 1, ?, ?, ?)", Y, L, first, junk(), stamp, stamp, day(10), junk(), seal1)
        l1, l2 = (m[0] for m in q("SELECT id FROM contracts WHERE user_id = ? ORDER BY version", L))
        cL, before, n_view = client_of(L), snap(), len(audit("CONTRACT_VIEW"))
        with unittest.TestCase().assertLogs("contract", "ERROR") as cm:
            rm, ra = mine(cL), admin()
            gets = [get(cL, f"/api/contracts/{l1}"), get(cL, f"/api/contracts/{l1}/pdf"), get(hr, f"/api/contracts/{l1}"), get(hr, f"/api/contracts/{l1}/pdf"), sg(cL, l2, L), cn(l2, "resend")]
        logged = {rec.args[0] for rec in cm.records if rec.args}
        ok(rm.status_code == 200 and rm.json == {"rows": []} and ra.status_code == 200 and all(m["userId"] != L for m in ra.json["rows"]) and [g.status_code for g in gets] == [409] * 6
           and {l1, l2} <= logged and snap() == before and len(audit("CONTRACT_VIEW")) == n_view and not any(w in line for line in cm.output for w in ("가상", "2418900", "900315")),
           "풀리지 않는 줄: 목록 (mine · 현황) 에서 빠짐 · 보기 · 서명본 · 서명 · 재발송은 409 (아무것도 안 바뀜, 감사로그 없음) · 서버 로그에는 계약서 번호만")
        r = cn(l2, "none")
        ok(r.status_code == 200 and q("SELECT status FROM contracts WHERE id = ?", l2) == [("CANCELLED",)], "풀리지 않는 서명 대기 줄도 파기만은 할 수 있음 (내용을 열지 않는다)")

        print("  - 서명 · 파기 · 새 계약 응답 헤더 · 감사로그")
        calls = [mine(ca), get(ca, f"/api/contracts/{ia}"), get(ca, f"/api/contracts/{ia}/seal"), get(ca, f"/api/contracts/{ia}/pdf"), get(emp, f"/api/contracts/{ia}"), get(ca, "/api/contracts/99999"),
                 sg(ca, ia, A), cn(ia), rp(ia), anon.get("/api/contracts/mine", base_url=BASE)]
        ok([r.status_code for r in calls] == [200, 200, 200, 200, 403, 404, 409, 409, 409, 401]
           and all(r.headers.get("Cache-Control") == "no-store" and r.headers.get("Referrer-Policy") == "no-referrer" for r in calls),
           "직원 길 · 서명 · 파기 · 새 계약 — 성공 · 거절 응답 모두 Cache-Control: no-store + Referrer-Policy: no-referrer")
        logs = q("SELECT action, entity_type, entity_id, after_data FROM audit_logs WHERE action LIKE 'CONTRACT%' ORDER BY id")
        ok({"CONTRACT_SIGNED", "CONTRACT_REPLACE", "CONTRACT_REPLACED", "CONTRACT_CANCELLED", "CONTRACT_VIEW", "CONTRACT_PDF_VIEW"} <= {a[0] for a in logs}
           and not any(w in str(logs) for w in ("가상", "2418900", "2700000", "2750000", "900315", "승진", "파기 사유", "금액 오류", "○○○", "△△△", "프로")),
           "서명 · 파기 · 새 계약 · 보기 감사로그에 이름 · 금액 · 생년월일 · 사유 · 대표이사 이름 없음")

        print("  - 문의 (HR에 문의 → HR 가 확인함)")
        ok(send([d3], day(9)).json == {"sent": 1, "skipped": []}, "A 의 다음 해 3차 줄(발송 전)을 서명 대기로 보내 둠 — 문의 검사용")
        ask_text = ("가상 금액 확인 요청 " + "나" * 200)[:200]
        askp = lambda c, cid, text=ask_text: post(c, f"/api/contracts/{cid}/ask", {"text": text})  # noqa: E731
        askc = lambda cid, c=hr: post(c, f"/api/contracts/{cid}/ask/close")  # noqa: E731
        ask_db = lambda cid: q("SELECT ask, ask_at, ask_closed_at, ask_closed_by FROM contracts WHERE id = ?", cid)[0]  # noqa: E731
        hr_row = lambda cid, year: next(m for m in admin(year).json["rows"] if m["id"] == cid)  # noqa: E731
        ask_log = json.dumps({"contract": d3})
        before = snap()
        ok([askp(c, d3).status_code for c in (emp, cl, cd, hr)] == [403] * 4 and askp(anon, d3).status_code == 401
           and post(ca, f"/api/contracts/{d3}/ask", {"text": "가상"}, headers={}).status_code == 403
           and askp(ca, 99999).status_code == 404 and askp(ca, ib).status_code == 404 and snap() == before,
           "문의는 본인 줄만: 남의 계약서에는 누구도 (HR 도) 403 · 없는 번호와 남의 발송 전 줄 404 · 로그인 없이 401 · Origin 없이 403 (아무것도 안 바뀜)")
        ok([askp(ca, c).status_code for c in (v2, ia, a_y1)] == [409] * 3 and snap() == before,
           "서명 대기가 아닌 줄 (서명 완료 · 새 계약으로 바뀜 · 파기) 에는 문의할 수 없음 409")
        ask_bad = {"빈 글": {"text": ""}, "공백만": {"text": "   "}, "201자": {"text": "가" * 201}, "제어문자": {"text": "가\x00나"}, "짝 없는 글자": {"text": "가\ud800"},
                   "글자 아님": {"text": 5}, "칸이 더 있음": {"text": "가상", "extra": 1}, "칸 없음": {}, "목록": ["가상"]}
        wrong = [k for k, b in ask_bad.items() if post(ca, f"/api/contracts/{d3}/ask", b).status_code != 400]
        ok(not wrong and snap() == before, f"문의 글은 1~200자 (제어문자 · 짝 없는 글자 · 글자 아님 · 칸이 틀리면 400, 아무것도 안 바뀜) {wrong}")
        r = askp(ca, d3)
        got = ask_db(d3)
        ok(r.status_code == 200 and r.json == {"ok": True} and got[0] and len(got[0]) > 28 and ask_text[:12].encode() not in got[0] and got[1] and got[2] is None and got[3] is None
           and opened(A, "ask", ver=3, year=Y + 1) == ask_text, "200자 문의 → 잠가 저장 (DB 에 글이 그대로 없음 · 열쇠로 풀면 그 글) · 보낸 때 · 확인 표시는 비어 있음")
        ok(hr_row(d3, Y + 1)["ask"] == {"at": got[1], "closedAt": None, "text": ask_text} and by_id(ca)[d3]["ask"] == {"at": got[1], "closedAt": None}
           and ask_text not in str(mine(ca).json) and get(ca, f"/api/contracts/{d3}").json["ask"] == {"at": got[1], "closedAt": None}
           and audit("CONTRACT_ASK") == [("contract", d3, ask_log)],
           "HR 현황에는 글 · 보낸 때 · 확인한 때 / 직원 줄 (mine · 한 건) 에는 보낸 때 · 확인한 때만 (글 없음) · 감사로그 CONTRACT_ASK — 계약서 번호만")
        before = snap()
        ok(askp(ca, d3, "가상 둘째 문의").status_code == 409 and snap() == before, "확인 전에 또 문의하면 409 (첫 문의는 그대로)")
        ok([askc(d3, c).status_code for c in (emp, cl, cd, ca)] == [403] * 4 and askc(99999).status_code == 404 and askc(v2).status_code == 409 and askc(ib).status_code == 409
           and post(hr, f"/api/contracts/{d3}/ask/close", headers={}).status_code == 403 and anon.post(f"/api/contracts/{d3}/ask/close", base_url=BASE, headers=ORIGIN).status_code == 401
           and snap() == before, "확인함은 HR 만 (직원 · 팀장 · 본부장 · 문의한 본인도 403) · 없는 번호 404 · 문의가 없는 줄 409 (아무것도 안 바뀜)")
        r = askc(d3)
        done = ask_db(d3)
        ok(r.status_code == 200 and r.json == {"ok": True} and done[:2] == got[:2] and done[2] and done[3] == 1
           and hr_row(d3, Y + 1)["ask"] == {"at": got[1], "closedAt": done[2], "text": ask_text} and by_id(ca)[d3]["ask"] == {"at": got[1], "closedAt": done[2]}
           and audit("CONTRACT_ASK_CLOSED") == [("contract", d3, ask_log)],
           "확인함 → 확인한 때와 사람이 적힘 (글 · 보낸 때는 그대로) · 직원 줄에도 확인한 때 · 감사로그 CONTRACT_ASK_CLOSED — 계약서 번호만")
        before = snap()
        ok(askc(d3).status_code == 409 and snap() == before, "이미 확인한 문의는 또 확인할 수 없음 409")
        k1, k2, gate2, codes2 = client_of(A), client_of(A), threading.Barrier(2), []
        again = ["  가상 다시 문의 하나  ", "가상 다시 문의 둘  "]

        def go_ask(c, text):
            gate2.wait()
            codes2.append(askp(c, d3, text).status_code)

        ths = [threading.Thread(target=go_ask, args=a) for a in zip((k1, k2), again)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        again_ask = hr_row(d3, Y + 1)["ask"]
        ok(sorted(codes2) == [200, 409] and again_ask["text"] in [s.strip() for s in again] and again_ask["closedAt"] is None and ask_db(d3)[2:] == (None, None)
           and len(audit("CONTRACT_ASK")) == 2, "확인한 뒤에는 다시 문의할 수 있음 (확인 표시가 비워지고 새 글 · 앞뒤 공백은 뺌) · 동시에 두 번 보내면 하나만 200 · 나머지 409")
        r = sg(ca, d3, A)
        ok(r.status_code == 200 and hr_row(d3, Y + 1)["status"] == "SIGNED" and hr_row(d3, Y + 1)["ask"]["text"] == again_ask["text"] and askp(ca, d3).status_code == 409,
           "문의 중에도 서명할 수 있음 · 서명한 뒤에도 문의 글은 HR 현황에 남음 · 서명 완료 줄에는 새 문의 409")
        ok(askc(d3).status_code == 200 and ask_db(d3)[2] and len(audit("CONTRACT_ASK_CLOSED")) == 2, "서명 완료 줄의 문의도 HR 가 확인함으로 표시할 수 있음")
        ok(not any(w in str(q("SELECT action, entity_type, entity_id, after_data FROM audit_logs WHERE action LIKE 'CONTRACT%'")) for w in ("가상", "나나나", "다시 문의")),
           "감사로그에 문의 글 없음")

        print("  - 본부장 현황 (금액 없이)")

        def mkuser(uid, name, dept=2):
            x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, has_contract) VALUES (?, ?, ?, ?, ?, 1, 1)",
              uid, name, f"ct{uid}@example.invalid", dept, f"HW-{uid}")

        def raw_row(year, uid, status, ver=1, **kw):
            """열쇠 없이 바로 넣은 줄 — UPDATE 가 아니라 INSERT 라 트리거에 안 걸린다. 칸은 아무 글 (kw 로 넘긴 칸만 내용이 있다)."""
            cols = {"year": year, "user_id": uid, "version": ver, "status": status, "starts_on": f"{year}-03-01", "body": junk(), "created_at": stamp, "created_by": 1}
            if status != "DRAFT":
                cols.update(sent_at=stamp, sent_by=1, due_on=day(10), company=junk(), seal_id=seal1)
            if status in ("SIGNED", "REPLACED"):
                cols.update(signed_at=stamp, sign=junk(), doc_hash="a" * 64, pdf=junk())
            if status == "REPLACED":
                cols.update(replaced_on=f"{year}-06-01")
            if status == "CANCELLED":
                cols.update(cancelled_at=stamp, cancelled_by=1, cancel_reason="가상")
            cols.update(kw)
            x(f"INSERT INTO contracts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", *cols.values())

        x("INSERT INTO departments (code, name, level, parent_id) VALUES ('CT-D2', '가상본부둘', 'DIVISION', NULL)")
        div2 = q("SELECT id FROM departments WHERE code = 'CT-D2'")[0][0]
        x("INSERT INTO departments (code, name, level, parent_id) VALUES ('CT-T2', '가상둘팀', 'TEAM', ?)", div2)
        team2 = q("SELECT id FROM departments WHERE code = 'CT-T2'")[0][0]
        for uid, name, dept in ((M, "가상계약M", 2), (N, "가상계약N", 1), (O, "가상계약O", team2), (Q, "가상계약Q", div2), (R, "가상계약R", 2)):  # 본부 1 = 가상본부 (팀 2 = 가상팀)
            mkuser(uid, name, dept)
        x("INSERT INTO user_roles (user_id, role) VALUES (?, 'DIVISION_HEAD')", R)
        x("INSERT INTO departments (code, name, level, parent_id) VALUES ('CT-DIR', '가상직속팀', 'DIVISION', NULL)")  # 대표이사 직속 팀: 맨 위 부서 · 아래 팀 없음 · 임원 아님
        direct = q("SELECT id FROM departments WHERE code = 'CT-DIR'")[0][0]
        for uid, name, lead in ((TLX, "가상팀장X", team2), (TLY, "가상팀장Y", direct)):
            mkuser(uid, name)
            x("INSERT INTO user_roles (user_id, role) VALUES (?, 'TEAM_LEADER')", uid)
            x("UPDATE users SET led_department_id = ? WHERE id = ?", lead, uid)
        x("UPDATE users SET led_department_id = 1 WHERE id = ?", D)  # 본부장 D 가 맡은 본부 (소속은 팀 2 라서 맡은 부서를 정해야 본부장으로 보인다)
        YY = Y + 2  # 이 검사만 쓰는 회차 — 줄은 내용을 안 풀어도 되는 모양으로 바로 넣는다
        raw_row(YY, A, "SENT", due_on=day(-3))  # 마감이 3일 지남
        raw_row(YY, B, "DRAFT")
        raw_row(YY, I, "SENT", due_on=day(5))
        raw_row(YY, J, "SIGNED", signed_at="2027-03-01T16:30:00Z")  # 한국 시각으로는 다음 날 01:30
        raw_row(YY, K, "CANCELLED")
        raw_row(YY, L, "REPLACED", ver=1)
        raw_row(YY, L, "SIGNED", ver=2, signed_at="2027-03-01T10:00:00Z")
        for uid in (N, 2, O, Q, R, TLX, TLY):  # N 본부 바로 아래 · 2 팀장 · O Q 다른 본부 · R 같은 본부의 다른 본부장 · TLX TLY 이끄는 팀이 소속과 다른 팀장
            raw_row(YY, uid, "DRAFT")
        divp = lambda c, year=None: get(c, "/api/contracts/division" + (f"?year={year}" if year else ""))  # noqa: E731
        want_div = [{"name": n, "team": t, "status": s, "late": late, "signedOn": on, "due": due} for n, t, s, late, on, due in (
            ("가상계약A", "가상팀", "SENT", True, None, day(-3)), ("가상계약B", "가상팀", "DRAFT", False, None, None), ("가상계약I", "가상팀", "SENT", False, None, day(5)),
            ("가상계약J", "가상팀", "SIGNED", False, "2027-03-02", day(10)), ("가상계약L", "가상팀", "SIGNED", False, "2027-03-01", day(10)),
            ("가상계약N", "가상본부", "DRAFT", False, None, None), ("가상2", "가상팀", "DRAFT", False, None, None))]
        want_div.sort(key=lambda m: (m["team"], m["name"]))  # 팀 이름 · 사람 이름 순
        res = divp(cd, YY)
        ok(res.status_code == 200 and res.json == {"year": YY, "rows": want_div} and all(set(m) == {"name", "team", "status", "late", "signedOn", "due"} for m in res.json["rows"])
           and not any(w in res.get_data(as_text=True) for w in ("base", "ability", "birth6", "annual", "userId", "version", "ask", "reason", "code", "2418900", "900315")),
           "본부장: 자기 본부 사람 (팀원 · 팀장 · 본부 바로 아래) 의 살아 있는 줄만 — 파기 · 바뀜 · 다른 본부 · 같은 본부의 다른 본부장은 안 나옴 / 마감 지남 · 서명한 날 (한국 날짜) 이 맞고 금액 · 생년월일 · 차수 · 문의 없음")
        x("UPDATE users SET led_department_id = ? WHERE id = ?", div2, D)
        other = divp(cd, YY)
        x("UPDATE users SET led_department_id = 1 WHERE id = ?", D)
        ok(sorted(m["name"] for m in other.json["rows"]) == ["가상계약O", "가상계약Q", "가상팀장X"],
           "다른 본부를 맡은 본부장은 그 본부 사람만 보임 — 소속은 본부 1 이어도 다른 본부의 팀을 이끄는 팀장은 그 본부장에게만 보이고 (본부 1 의 본부장에게는 안 보임), 대표이사 직속 팀을 이끄는 팀장은 어느 본부장에게도 안 보임")
        ok([divp(c, YY).status_code for c in (emp, cl, hr, ca)] == [403] * 4 and divp(anon, YY).status_code == 401 and divp(cd, "abc").status_code == 400
           and divp(cd, "1999").status_code == 400 and divp(cd).json["year"] == Y,
           "본부장이 아니면 403 (직원 · 팀장 · HR 는 연봉계약 관리 화면을 씀) · 해 모양이 틀리면 400 · 해를 안 보내면 다가오는 회차")

        print("  - 서명본 묶음 (zip)")

        def box(k, uid, year, field, data):  # 열쇠 k 로 잠근 칸 (자리 = 그 줄의 1차 칸) — 서버가 잠그는 모양 그대로
            nonce = os.urandom(12)
            return nonce + AESGCM(k).encrypt(nonce, data, f"ct|{year}|{uid}|1|{field}".encode())

        def readable(year, uid, name, code, pdf, pdf_key=raw_key):
            """내용도 서명본도 풀리는 서명 완료 줄. pdf_key 가 다르면 서명본만 안 풀린다."""
            body = {"code": code, "name": name, "dept": "가상팀", "title": "", "birth6": "900315", "base": 2418900, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0, "reason": ""}
            raw_row(year, uid, "SIGNED", body=box(raw_key, uid, year, "body", json.dumps(body, ensure_ascii=False).encode()), pdf=box(pdf_key, uid, year, "pdf", pdf))

        readable(Y, M, "가상계약M", "", b"%PDF-m", pdf_key=bytes(range(1, 33)))  # 서명본만 다른 열쇠로 잠긴 줄
        m_id = q("SELECT id FROM contracts WHERE year = ? AND user_id = ?", Y, M)[0][0]
        zp = lambda year, c=hr: get(c, f"/api/contracts/{year}/zip")  # noqa: E731
        before = snap()
        ok([zp(Y, c).status_code for c in (emp, cl, cd, ca)] == [403] * 4 and zp(Y, anon).status_code == 401 and zp("abc").status_code == 400 and zp(1999).status_code == 400
           and zp(2098).status_code == 404 and snap() == before and audit("CONTRACT_ZIP") == [],
           "묶음은 HR 만 (직원 · 팀장 · 본부장 403) · 해 모양이 틀리면 400 · 서명한 줄이 없는 회차는 404 (아무것도 안 바뀜, 감사로그 없음)")
        with unittest.TestCase().assertLogs("contract", "ERROR") as cm:
            r = zp(Y)
        zf = zipfile.ZipFile(io.BytesIO(r.data))
        files = {i.filename: zf.read(i) for i in zf.infolist()}
        want_pdf = {f"{Y}_CT-A1_가상계약A.pdf": unsealed(A, "pdf", ver=1), f"{Y}_CT-A1_가상계약A_2차.pdf": unsealed(A, "pdf", ver=2), f"{Y}_{I}_가상계약I.pdf": unsealed(I, "pdf"),
                    f"{Y}_{J}_가상계약J_2차.pdf": unsealed(J, "pdf", ver=2), f"{Y}_{J}_가상계약J_4차.pdf": unsealed(J, "pdf", ver=4)}
        ok(r.status_code == 200 and r.mimetype == "application/zip" and r.headers["Content-Disposition"] == f'attachment; filename="contracts_{Y}.zip"'
           and set(files) == {*want_pdf, "서명정보.csv"} and all(files[n] == p and p.startswith(b"%PDF") for n, p in want_pdf.items()) and zf.testzip() is None and len({*want_pdf.values()}) == len(want_pdf)
           and all(i.compress_type == zipfile.ZIP_DEFLATED and not i.flag_bits & 1 for i in zf.infolist()),
           "zip: 서명 완료 · 새 계약으로 바뀐 줄 (A 1·2차 · I · J 2·4차) 의 서명본 PDF 다섯 개 (1차는 YYYY_사원코드 (없으면 직원 번호)_이름.pdf, 2차부터는 이름 뒤에 _N차 · 저장된 그대로)"
           " + 서명정보.csv · 파기한 줄 (J 1·3차 · K) 은 없음 · 암호 없는 deflate")
        kst_of = lambda s: (datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ") + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")  # noqa: E731
        want_csv = []
        for uid, ver, name, code in ((A, 1, "가상계약A", "CT-A1"), (A, 2, "가상계약A", "CT-A1"), (I, 1, "가상계약I", ""), (J, 2, "가상계약J", ""), (J, 4, "가상계약J", "")):
            signed_at, doc_hash = q("SELECT signed_at, doc_hash FROM contracts WHERE year = ? AND user_id = ? AND version = ?", Y, uid, ver)[0]
            want_csv.append([name, code, str(ver), kst_of(signed_at), doc_hash])
        parsed = list(csv.reader(io.StringIO(files["서명정보.csv"].decode("utf-8-sig"))))
        ok(files["서명정보.csv"].startswith(b"\xef\xbb\xbf") and parsed[0] == ["이름", "사원코드", "차수", "서명 일시", "문서 고유번호"] and sorted(parsed[1:]) == sorted(want_csv),
           "서명정보.csv: utf-8-sig · 머리 이름 / 사원코드 / 차수 / 서명 일시 (한국 시각, 분까지) / 문서 고유번호 · PDF 한 개에 한 줄 (다섯 줄, 차수 칸이 차수)")
        ok({rec.args[0] for rec in cm.records if rec.args} == {l1, m_id} and not any(w in line for line in cm.output for w in ("가상", "2418900", "900315", "CT-A")),
           "안 풀리는 줄 (L: 내용이 안 풀림 · M: 서명본만 다른 열쇠) 은 묶음에서 빠지고 200 · 서버 로그에는 계약서 번호만")
        ok(audit("CONTRACT_ZIP") == [("contract_year", None, json.dumps({"year": Y, "people": 5}))], "감사로그 CONTRACT_ZIP — 해 · 내려받은 PDF 수만")
        r = zp(Y + 1)
        z1 = zipfile.ZipFile(io.BytesIO(r.data))
        ok(r.status_code == 200 and sorted(z1.namelist()) == sorted([f"{Y + 1}_CT-A1_가상계약A_3차.pdf", "서명정보.csv"])
           and z1.read(f"{Y + 1}_CT-A1_가상계약A_3차.pdf") == unsealed(A, "pdf", ver=3, year=Y + 1), "다른 회차는 그 회차의 서명 줄만 (파기한 1·2차는 없고 3차만)")
        Y3, bad_name = Y + 3, "가상" + '/\\:*?"<>|' + "이름"
        for uid in (148, 149, 150):
            mkuser(uid, f"가상계약Z{uid}")
        readable(Y3, 148, bad_name, "", b"%PDF-1")
        readable(Y3, 149, "가상같음", "SAME", b"%PDF-2")
        readable(Y3, 150, "가상같음", "SAME", b"%PDF-3")
        r = zp(Y3)
        z3 = zipfile.ZipFile(io.BytesIO(r.data))
        want3 = {f"{Y3}_148_가상" + "_" * 9 + "이름.pdf": b"%PDF-1", f"{Y3}_SAME_가상같음.pdf": b"%PDF-2", f"{Y3}_SAME_가상같음_2.pdf": b"%PDF-3"}
        ok(set(z3.namelist()) == {*want3, "서명정보.csv"} and all(z3.read(n) == p for n, p in want3.items())
           and bad_name in [c[0] for c in csv.reader(io.StringIO(z3.read("서명정보.csv").decode("utf-8-sig")))],
           "파일 이름: Windows 에서 못 쓰는 글자 (\\ / : * ? \" < > |) 는 _ 로 바꾸고 같은 이름이 겹치면 _2 를 붙임 · 서명정보.csv 에는 원래 이름")
        ok(audit("CONTRACT_ZIP") == [("contract_year", None, json.dumps({"year": y, "people": n})) for y, n in ((Y, 5), (Y + 1, 1), (Y3, 3))]
           and not any(w in str(audit("CONTRACT_ZIP")) for w in ("가상", "CT-A", "SAME")), "감사로그 CONTRACT_ZIP 은 회차마다 한 줄 (해 · 인원 수만, 이름 · 사원코드 없음)")
        with unittest.TestCase().assertLogs("contract", "ERROR"):  # 묶음 (Y) 이 안 풀리는 줄을 로그로 남기므로 잡아 두어 검사 출력에 ERROR 가 섞이지 않게
            calls = [askp(ca, d3), askc(d3), askc(d3, ca), divp(cd, YY), divp(emp, YY), zp(Y), zp(Y, emp), zp(2098)]
        ok([r.status_code for r in calls] == [409, 409, 403, 200, 403, 200, 403, 404]
           and all(r.headers.get("Cache-Control") == "no-store" and r.headers.get("Referrer-Policy") == "no-referrer" for r in calls),
           "문의 · 확인함 · 본부장 현황 · 묶음 — 성공 · 거절 응답 모두 Cache-Control: no-store + Referrer-Policy: no-referrer")

        print("  - 열쇠가 바뀌어 안 풀리는 줄")
        os.environ["HR_PAY_KEY"] = base64.b64encode(bytes(range(1, 33))).decode()
        with unittest.TestCase().assertLogs("contract", "ERROR") as cm:  # 로그는 잡아 두어 검사 출력에 ERROR 가 섞이지 않게
            r = admin()
        ok(r.status_code == 200 and r.json["rows"] == [] and r.json["ready"] is True and r.json["settings"] is False
           and sorted(rec.args[0] for rec in cm.records if rec.args) == sorted(c[0] for c in q("SELECT id FROM contracts WHERE year = ?", Y))
           and not any(w in line for line in cm.output for w in ("가상", "2418900", "900315", "CT-A")), "목록은 그 줄들을 건너뜀 (오류 아님) · 서버 로그에는 계약서 번호만")
        before = snap()
        with unittest.TestCase().assertLogs("contract", "ERROR"):
            r = adj(ids[B])
        ok(r.status_code == 409 and snap() == before, "열 수 없는 발송 전 줄의 조정은 409 (아무것도 안 바뀜)")
        before = snap()
        with unittest.TestCase().assertLogs("contract", "ERROR") as cm:
            rm, r1, r2, r3, r4 = mine(ca), get(ca, f"/api/contracts/{v2}"), get(ca, f"/api/contracts/{v2}/pdf"), get(ca, f"/api/contracts/{v2}/seal"), sg(ci, i_y1, I)
        ok(rm.status_code == 200 and rm.json == {"rows": []} and (r1.status_code, r2.status_code, r4.status_code) == (409, 409, 409) and r3.status_code == 404 and snap() == before
           and not any(w in line for line in cm.output for w in ("가상", "2418900", "900315", "CT-A")), "직원 길도 같음: mine 은 빈 목록 · 보기 · 서명본 · 서명은 409 · 직인은 404 (아무것도 안 바뀜, 서버 로그에는 번호만)")
        with unittest.TestCase().assertLogs("contract", "ERROR") as cm:
            rz = zp(Y)
        ok(rz.status_code == 404 and {rec.args[0] for rec in cm.records if rec.args} == {c[0] for c in q("SELECT id FROM contracts WHERE year = ? AND status IN ('SIGNED', 'REPLACED')", Y)}
           and not any(w in line for line in cm.output for w in ("가상", "2418900", "900315", "CT-A")), "묶음: 열쇠가 바뀌어 하나도 안 풀리면 404 (500 아님) · 서버 로그에는 계약서 번호만")
        res = divp(cd, Y)
        ok(res.status_code == 200 and len(res.json["rows"]) > 0, "본부장 현황은 잠근 내용을 풀지 않으므로 열쇠가 바뀌어도 그대로")
    finally:
        x("UPDATE users SET led_department_id = NULL WHERE id IN (?, ?, ?)", D, TLX, TLY)  # 본부장 현황 검사에서 정한 맡은 본부 · 다른 본부장 · 팀장은 되돌린다
        x("DELETE FROM user_roles WHERE user_id IN (?, ?, ?)", R, TLX, TLY)
        os.environ.pop("HR_PAY_KEY", None)
        if had is not None:
            os.environ["HR_PAY_KEY"] = had
        auth._now = real_now
        contract_doc.render_pdf = real_render
        contract_doc.TPL, contract_doc.build_blocks = real_tpl, real_build


def check_eval_report(hr) -> None:
    """평가 결과 보고용 엑셀 (2026-10-22): HR 만 · 받은 값 검사 · 시트 둘(요약 · 명단) · 연봉 인상 계산이 읽는 칸 제목 · '=' 글자는 수식이 아님."""
    import io
    import zipfile
    from urllib.parse import quote

    from openpyxl import load_workbook
    print("평가 결과 보고서")
    lead = app.test_client()
    login(lead, new_token("lead"))
    post = lambda c, body, headers=ORIGIN: c.post("/api/eval/report", base_url=BASE, headers=headers, json=body)  # noqa: E731
    man = lambda name, div, grade, total=85.5, appeal="": {"div": div, "team": "가상팀", "no": "", "name": name, "title": "사원",  # noqa: E731
                                                           "pool": "가상본부 팀원", "total": total, "grade": grade, "stage": "확정", "appeal": appeal}
    body = {"year": 2026, "date": "2026-10-22", "released": None, "appeals": 1, "open": 1,
            "people": [man("가상일", "가상본부", "A"), man("=1+1", "가상본부", "", appeal="처리 중 (지금 C)"), man("가상삼", "가상지원", "C", None)],
            "pools": [{"name": "가상본부 팀원", "quota": "A·B 합계 1명", "ok": True}]}

    ok(app.test_client().post("/api/eval/report", base_url=BASE, headers=ORIGIN, json=body).status_code == 401, "로그인 없이는 401")
    ok(post(lead, body).status_code == 403, "HR 이 아니면 403 (팀장)")
    bad = [{**body, "year": "2026"}, {**body, "people": []}, {**body, "people": [man("가상", "가상본부", "X")]},
           {**body, "people": [man("가\n상", "가상본부", "A")]}, {**body, "people": [man("가상", "가상본부", "A", 900)]},
           {**body, "pools": [{"name": "묶음", "quota": "", "ok": "yes"}]}, [1, 2]]
    ok(all(post(hr, b).status_code == 400 for b in bad), "형식 · 등급 · 줄바꿈 글자 · 총점 범위 · 묶음 값이 틀리면 400")
    ok(post(hr, {**body, "people": [man("가상", "가상본부", "A")] * 3001}).status_code in (400, 413), "인원이 너무 많으면 거절")

    r = post(hr, body)
    ok(r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
       and r.headers["Content-Disposition"].endswith("filename*=UTF-8''" + quote("2026년 인사평가 결과.xlsx")), "HR → 엑셀 (파일 이름 한글)")
    wb = load_workbook(io.BytesIO(r.data))
    sm, ro = wb["요약"], wb["명단"]
    head = [c.value for c in ro[4]]
    ok(wb.sheetnames == ["요약", "명단"] and {"사번", "이름", "최종등급"} <= set(head), "시트 요약 · 명단, 명단 칸 제목은 연봉 인상 계산이 읽는 이름")
    names = [ro.cell(row=r_, column=head.index("이름") + 1) for r_ in (5, 6, 7)]
    ok(names[1].value == "=1+1" and names[1].data_type == "s", "'=' 로 시작하는 글자는 수식이 아니라 글자")
    ok([ro.cell(row=r_, column=head.index("최종등급") + 1).value for r_ in (5, 6, 7)] == ["A", None, "C"]
       and ro.cell(row=7, column=head.index("총점") + 1).value == "현장직", "이의 처리 중이면 등급 칸이 비고, 점수 없는 사람은 현장직")
    rows = {sm.cell(row=r_, column=1).value: [sm.cell(row=r_, column=c).value for c in range(2, 9)] for r_ in range(9, 13)}
    ok(rows["가상본부"] == [0, 1, 0, 0, 0, 1, 2] and rows["가상지원"] == [0, 0, 0, 1, 0, 0, 1] and rows["합계"] == [0, 1, 0, 1, 0, 1, 3],
       "본부별 분포: 받은 순서대로 · 처리 중 칸 · 합계")
    z = zipfile.ZipFile(io.BytesIO(r.data))
    charts = [n for n in z.namelist() if n.startswith("xl/charts/chart")]
    ok(len(charts) == 1 and "<f>'요약'!" in z.read(charts[0]).decode() and "Sheet" not in z.read(charts[0]).decode(), "요약에 그래프 하나 (요약 시트의 칸을 가리킴)")


def check_push_targets(hr) -> None:
    """휴대폰 알림 보낼 대상 (2026-10-22): 살아 있는 휴대폰 세션의 구독만 — 해제 · 30일 넘게 안 씀 · 만료 · PC 세션 · 비활성 직원 · 포털 사용 끔 · 남의 구독은 안 받는다.
    세션 · 구독은 가상3(직원)에게 직접 넣고, 끝나면 지우고 직원 칸도 되돌린다. 주소 · 키는 가상(example.invalid)."""
    import push
    print("휴대폰 알림 보낼 대상")
    now, tail, seq = auth._now(), "9" * 63, [0]
    before = q("SELECT is_active, deactivated_at, portal_enabled FROM users WHERE id = 3")[0]
    c = connect(os.environ["HR_DB_PATH"])

    def put(kind: str = "phone", seen: timedelta = timedelta(0), left: timedelta = timedelta(days=30), revoked: bool = False, sub_user: int = 3) -> str:
        seq[0] += 1
        h = f"{seq[0]}{tail}"  # 가상 세션 번호 (64자)
        c.execute("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, revoked_at, kind) VALUES (?, 3, ?, ?, ?, ?, ?)",
                  (h, auth._iso(now - timedelta(days=40)), auth._iso(now - seen), auth._iso(now + left), auth._iso(now) if revoked else None, kind))
        c.execute("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)",
                  (h, sub_user, f"https://push.example.invalid/{seq[0]}", auth._iso(now)))
        return h

    def got(uids=(3,), at=now) -> list[str]:
        return [t[1] for t in push.targets(c, uids, at)]

    def refused(why: str, **kw) -> None:  # 살아 있는 세션(위)과 한 가지만 다른 세션의 구독은 받지 않는다
        h = put(**kw)
        ok(got(uids=(2, 3)) == [], f"{why} → 0줄")
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (h,))

    try:
        live = put()
        ok(push.targets(c, [3], now) == [(3, live, {"endpoint": "https://push.example.invalid/1", "keys": {"p256dh": "k", "auth": "a"}})],
           "살아 있는 휴대폰 세션의 구독 → 1줄 (보낼 때 쓰는 모양)")
        ok(got(at=now.astimezone(auth.KST)) == [live], "시각을 한국 시간으로 넘겨도 같은 결과")
        ok(got(uids=(2,)) == [] and got(uids=()) == [] and got(uids=iter([3])) == [live], "받을 사람(uids) 안의 사람 것만 — 비어 있으면 0줄, 반복자도 됨")
        ok(got(at=now + timedelta(days=30, seconds=1)) == [] and got(at=now + timedelta(days=29)) == [live], "마지막 사용 30일이 지나면 빠지고 그 전엔 받음")
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (live,))

        refused("해제한 세션(revoked_at)", revoked=True)
        refused("마지막 사용 31일 전", seen=timedelta(days=31))
        refused("만료 시각이 지난 세션", left=timedelta(seconds=-1))
        refused("PC 세션", kind="pc")
        refused("구독의 직원이 세션 주인과 다름(남의 구독)", sub_user=2)
        edge = put(seen=timedelta(days=29, hours=23))
        ok(got() == [edge], "마지막 사용 29일 23시간 전은 아직 받음")

        c.execute("UPDATE users SET is_active = 0, deactivated_at = ? WHERE id = 3", (auth._iso(now),))
        ok(got() == [], "비활성 직원 → 0줄")
        c.execute("UPDATE users SET is_active = 1, deactivated_at = NULL, portal_enabled = 0 WHERE id = 3")
        ok(got() == [], "포털 사용을 끈 직원 → 0줄")
        c.execute("UPDATE users SET portal_enabled = 1 WHERE id = 3")
        second = put()
        ok(got() == sorted([edge, second]), "되돌리면 다시 받음 (한 사람이 구독 둘이면 둘 다)")
    finally:
        c.execute("DELETE FROM sessions WHERE token_hash LIKE ?", (f"_{tail}",))
        c.execute("UPDATE users SET is_active = ?, deactivated_at = ?, portal_enabled = ? WHERE id = 3", before)
        c.close()
    ok(q("SELECT COUNT(*) FROM push_subscriptions WHERE token_hash LIKE ?", f"_{tail}") == [(0,)]
       and q("SELECT is_active, deactivated_at, portal_enabled FROM users WHERE id = 3") == [before], "끝나면 세션 · 구독을 지우고(구독도 함께) 직원 칸을 되돌림")


def check_push_api(hr) -> None:
    """휴대폰 알림 구독 주소 (2026-10-22): 공개키 · 휴대폰 세션만 등록 · 주소·키 모양 검사 · 로그아웃·해제·새 등록 때 구독을 지움.
    키는 검사가 만든 가짜 — 끝나면 환경 값을 되돌리고, 이 검사가 만든 세션 · QR 값 · 구독 줄을 지운다. 주소는 가상(example.invalid) 또는 공개된 푸시 서버 모양."""
    import push
    print("휴대폰 알림 구독 주소")
    env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    had = {t: {r[0] for r in q(f"SELECT {col} FROM {t}")} for t, col in (("sessions", "token_hash"), ("phone_codes", "code_hash"))}  # 시작 때 있던 것
    seen: list[str] = []
    real_qr = auth._qr_rows
    auth._qr_rows = lambda text: (seen.append(text), real_qr(text))[1]  # QR 에 담긴 1회용 값을 엿본다 (check_phone 과 같음)
    ep = "https://fcm.googleapis.com/fcm/send/fake-device-1"
    device, secret = push.keygen()[0], push._b64u_enc(os.urandom(16))  # 휴대폰 키 모양의 가짜

    def rows() -> list:
        return q("SELECT user_id, endpoint FROM push_subscriptions ORDER BY endpoint")

    def phone_of(who: str):
        """who 의 PC 세션 → QR 값 → 휴대폰 세션. (PC, 휴대폰) 클라이언트를 돌려준다."""
        pc, phone = app.test_client(), app.test_client()
        login(pc, new_token(who))
        pc.post("/api/phone/code", base_url=BASE, headers=ORIGIN)
        _seq[0] += 1
        phone.post("/api/phone/register", base_url=BASE, json={"code": seen[-1].split("#d=")[-1]},
                   headers={**ORIGIN, "User-Agent": IPHONE, "CF-Connecting-IP": f"192.0.2.{_seq[0] % 250}"})
        return pc, phone

    def sub(client, endpoint=ep, p256dh=device, secret_=secret, headers=ORIGIN, body=None):
        return client.post("/api/push/subscribe", base_url=BASE, headers=headers,
                           json=body if body is not None else {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": secret_}})

    def armed(client, label: str) -> None:
        """해제 · 로그아웃 · 새 등록 검사 앞에서 — 지울 구독 1줄이 정말 있었는지 (없으면 '지워짐'이 그냥 통과해 버린다)."""
        ok(sub(client).status_code == 200 and rows() == [(3, ep)], f"(검사용) {label} 앞에 구독 1줄이 있음")

    try:
        for k in env:
            os.environ.pop(k, None)
        ok(hr.get("/api/push/key", base_url=BASE).status_code == 404, "알림 키가 없으면 /api/push/key 는 404 (나머지 기능은 그대로)")
        pub, priv = push.keygen()
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        r = hr.get("/api/push/key", base_url=BASE)
        ok(r.status_code == 200 and r.json == {"key": pub} and priv not in r.get_data(as_text=True), "키가 있으면 공개키만 돌려줌 (개인키는 나가지 않음)")
        anon = app.test_client()
        ok(anon.get("/api/push/key", base_url=BASE).status_code == 401 and sub(anon).status_code == 401
           and anon.post("/api/push/unsubscribe", base_url=BASE, headers=ORIGIN).status_code == 401, "로그인하지 않으면 세 주소 모두 401")

        ok(sub(hr).status_code == 403 and rows() == [], "PC 세션은 구독할 수 없음 → 403, 표는 비어 있음")
        pc, phone = phone_of("emp")
        ok(phone.get("/api/me", base_url=BASE).json.get("kind") == "phone", "(검사용) 가상3 휴대폰 세션")
        h = hashlib.sha256(cookie(phone).encode()).hexdigest()
        r = sub(phone)
        ok(r.status_code == 200 and r.json == {"ok": True} and rows() == [(3, ep)]
           and q("SELECT token_hash, p256dh, auth FROM push_subscriptions") == [(h, device, secret)], "휴대폰 세션 구독 → 200, 이 세션 · 이 사람으로 1줄")
        ep2 = "https://updates.push.services.mozilla.com/wpush/v2/fake-device-2"
        ok(sub(phone, endpoint=ep2).status_code == 200 and rows() == [(3, ep2)], "같은 세션이 다시 구독하면 덮어씀 → 여전히 1줄")
        ok(sub(phone, endpoint="https://push.example.invalid/unknown-service").status_code == 200, "처음 보는 푸시 서버 주소도 받음 (서버 이름 목록을 두지 않음)")

        def refused(why: str, **kw) -> None:
            before = rows()
            ok(sub(phone, **kw).status_code == 400 and rows() == before, f"{why} → 400, 표 그대로")

        refused("http:// 주소", endpoint="http://fcm.googleapis.com/fcm/send/x")
        refused("주소가 문자열이 아님", endpoint=123)
        refused("주소 없음", body={"keys": {"p256dh": device, "auth": secret}})
        refused("keys 없음", body={"endpoint": ep})
        refused("keys 가 객체가 아님", body={"endpoint": ep, "keys": "x"})
        refused("본문이 객체가 아님", body=["x"])
        refused("p256dh 없음", body={"endpoint": ep, "keys": {"auth": secret}})
        refused("auth 없음", body={"endpoint": ep, "keys": {"p256dh": device}})
        refused("p256dh 가 비어 있음", p256dh="")
        refused("p256dh 에 base64url 이 아닌 글자", p256dh="not base64!")
        refused("p256dh 가 문자열이 아님", p256dh=5)
        refused("공백이 든 주소", endpoint="https://fcm.googleapis.com/fcm/send/a b")
        refused("줄바꿈이 든 주소", endpoint="https://fcm.googleapis.com/fcm/send/a\r\nHost: x")
        refused("로그인 정보가 든 주소", endpoint="https://user:pw@fcm.googleapis.com/fcm/send/x")
        refused("IPv4 주소 그대로", endpoint="https://203.0.113.5/fcm/send/x")
        refused("IPv4 줄임 표기(127.1)", endpoint="https://127.1/x")
        refused("IPv4 정수 표기", endpoint="https://2130706433/x")
        refused("IPv6 주소 그대로", endpoint="https://[2001:db8::1]/x")
        refused("localhost", endpoint="https://localhost/x")
        refused("점 없는 이름", endpoint="https://intranet/x")
        refused("끝에 점이 붙은 localhost", endpoint="https://localhost./x")
        for suffix in ("local", "internal", "lan", "home", "localhost"):
            refused(f".{suffix} 이름", endpoint=f"https://printer.{suffix}/x")
        refused("숫자가 아닌 포트", endpoint="https://fcm.googleapis.com:abc/fcm/send/x")
        refused("65535 를 넘는 포트", endpoint="https://fcm.googleapis.com:99999/fcm/send/x")
        ok(sub(phone, endpoint="https://push.example.invalid:443/fcm/send/x").status_code == 200, "알맞은 포트(443)가 붙은 주소는 받음")
        long_ep = "https://fcm.googleapis.com/" + "a" * 973  # 정확히 1000자
        ok(len(long_ep) == 1000 and sub(phone, endpoint=long_ep).status_code == 200 and rows() == [(3, long_ep)], "주소 1000자는 받음")
        refused("주소 1001자", endpoint=long_ep + "a")
        ok(sub(phone, p256dh="A" * 200, secret_="B" * 100).status_code == 200 and rows() == [(3, ep)], "p256dh 200자 · auth 100자는 받음")
        ok(sub(phone, p256dh="A" * 201).status_code == 400 and sub(phone, secret_="B" * 101).status_code == 400, "p256dh 201자 · auth 101자는 거부")
        ok(rows() == [(3, ep)], "거부된 요청은 표를 바꾸지 않음")
        ok(sub(phone, headers={}).status_code == 403 and phone.post("/api/push/unsubscribe", base_url=BASE).status_code == 403
           and rows() == [(3, ep)], "Origin 없는 POST 는 구독도 해제도 403")

        # 같은 푸시 주소를 다른 세션이 쓰고 있었다면(쿠키만 지우고 같은 휴대폰에 다른 사람이 등록) 앞 사람 줄은 지운다
        x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, 2, ?, ?, ?, 'phone')",
          "8" * 64, auth._iso(auth._now()), auth._iso(auth._now()), auth._iso(auth._now() + timedelta(days=30)))
        x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, 2, ?, 'k', 'a', ?)",
          "8" * 64, ep, auth._iso(auth._now()))
        other_ep = "https://push.example.invalid/other-device"  # 다른 휴대폰의 구독 — 주소가 달라 건드리면 안 됨
        x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, 2, ?, ?, ?, 'phone')",
          "7" * 64, auth._iso(auth._now()), auth._iso(auth._now()), auth._iso(auth._now() + timedelta(days=30)))
        x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, 2, ?, 'k', 'a', ?)",
          "7" * 64, other_ep, auth._iso(auth._now()))
        ok(sorted(rows()) == sorted([(3, ep), (2, ep), (2, other_ep)]), "(검사용) 같은 주소를 쓰는 다른 사람의 앞 구독 · 주소가 다른 구독")
        ok(sub(phone).status_code == 200 and sorted(rows()) == sorted([(3, ep), (2, other_ep)]),
           "같은 푸시 주소로 구독하면 다른 세션의 앞 줄은 지움 (알림이 앞 사람에게 가지 않게) — 주소가 다른 줄은 그대로")
        x("DELETE FROM sessions WHERE token_hash IN (?, ?)", "8" * 64, "7" * 64)
        ok(rows() == [(3, ep)], "(검사용) 가짜 세션을 지우면 구독도 함께 지워짐")

        ok(phone.post("/api/push/unsubscribe", base_url=BASE, headers=ORIGIN).json == {"ok": True} and rows() == [], "구독 해제 → 이 세션 줄 삭제")
        ok(phone.post("/api/push/unsubscribe", base_url=BASE, headers=ORIGIN).status_code == 200, "구독이 없어도 해제는 200")
        ok(phone.get("/api/me", base_url=BASE).status_code == 200, "해제해도 휴대폰 로그인은 그대로")

        armed(phone, "본인 해제")
        ok(pc.post("/api/phone/revoke", base_url=BASE, headers=ORIGIN).status_code == 200 and rows() == [], "본인이 휴대폰 해제 → 구독도 지워짐")
        pc, phone = phone_of("emp")
        armed(phone, "HR 해제")
        ok(hr.post("/api/members/3/phone/revoke", base_url=BASE, headers=ORIGIN).status_code == 200 and rows() == [],
           "HR 이 그 사람 휴대폰 해제 → 구독도 지워짐")
        pc, phone = phone_of("emp")
        armed(phone, "로그아웃")
        ok(phone.post("/api/auth/logout", base_url=BASE, headers=ORIGIN).status_code == 200 and rows() == [],
           "휴대폰에서 로그아웃 → 구독도 지워짐")

        pc, phone = phone_of("emp")
        armed(phone, "새 휴대폰 등록")
        _, phone_b = phone_of("emp")
        ok(phone.get("/api/me", base_url=BASE).status_code == 401 and rows() == [], "같은 사람이 새 휴대폰을 등록하면 앞 휴대폰의 구독이 지워짐")
        armed(phone_b, "앞 사람 세션으로 다른 사람이 등록")
        pc2 = app.test_client()
        login(pc2, new_token("lead"))
        pc2.post("/api/phone/code", base_url=BASE, headers=ORIGIN)
        _seq[0] += 1
        r = phone_b.post("/api/phone/register", base_url=BASE, json={"code": seen[-1].split("#d=")[-1]},
                         headers={**ORIGIN, "User-Agent": IPHONE, "CF-Connecting-IP": f"192.0.2.{_seq[0] % 250}"})
        ok(r.status_code == 200 and rows() == [], "휴대폰에 남은 앞 사람의 세션으로 다른 사람이 등록 → 앞 세션의 구독도 지워짐")

        pc3, phone_c = phone_of("emp")
        armed(phone_c, "PC 로그아웃")
        ok(pc3.post("/api/auth/logout", base_url=BASE, headers=ORIGIN).status_code == 200 and rows() == [(3, ep)],
           "PC 에서 로그아웃해도 휴대폰 구독은 남음 (그 세션의 줄만 지움)")
        x("DELETE FROM push_subscriptions")

        # 구독을 지우는 쪽이 고장 나도(표가 없거나 잠김 …) 끊는 일은 그대로 끝난다 — HR 의 분실 휴대폰 해제가 알림 표에 기대지 않게
        @contextmanager
        def forget_broken():
            def boom(conn, hashes):
                raise RuntimeError("검사용 고장")

            real_forget, real_quiet = push.forget, auth.logger.disabled
            push.forget, auth.logger.disabled = boom, True  # 경고 줄은 찍지 않는다
            try:
                yield
            finally:
                push.forget, auth.logger.disabled = real_forget, real_quiet

        for why, act in (
            ("본인 휴대폰 해제", lambda pc_, ph_: pc_.post("/api/phone/revoke", base_url=BASE, headers=ORIGIN)),
            ("HR 의 휴대폰 해제", lambda pc_, ph_: hr.post("/api/members/3/phone/revoke", base_url=BASE, headers=ORIGIN)),
            ("휴대폰 로그아웃", lambda pc_, ph_: ph_.post("/api/auth/logout", base_url=BASE, headers=ORIGIN)),
        ):
            pc_, ph_ = phone_of("emp")
            armed(ph_, why)
            hh = hashlib.sha256(cookie(ph_).encode()).hexdigest()
            with forget_broken():
                r = act(pc_, ph_)
            ok(r.status_code == 200 and q("SELECT revoked_at IS NOT NULL FROM sessions WHERE token_hash = ?", hh) == [(1,)]
               and ph_.get("/api/me", base_url=BASE).status_code == 401, f"구독 지우기가 고장 나도 끝남 (세션이 끊김): {why}")
        _, ph_a = phone_of("emp")  # 새 휴대폰 등록: 앞 휴대폰을 끊는 길도 같음
        armed(ph_a, "새 휴대폰 등록")
        with forget_broken():
            _, ph_b = phone_of("emp")
        ok(ph_b.get("/api/me", base_url=BASE).json.get("kind") == "phone" and ph_a.get("/api/me", base_url=BASE).status_code == 401,
           "구독 지우기가 고장 나도 새 휴대폰 등록은 끝남 (앞 휴대폰이 끊김)")
        c = connect(os.environ["HR_DB_PATH"])
        try:
            ok(rows() == [(3, ep)] and push.targets(c, [3], auth._now()) == [], "못 지워 남은 줄은 보낼 대상이 아님 (끊긴 세션은 targets() 가 거름)")
        finally:
            c.close()
    finally:
        auth._qr_rows = real_qr
        for k, v in env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        c = connect(os.environ["HR_DB_PATH"])
        try:  # 이 검사가 만든 세션 · QR 값을 지운다 (구독 줄은 세션과 함께 사라짐) — 뒤 검사가 앞의 상태를 그대로 본다
            for t, col in (("sessions", "token_hash"), ("phone_codes", "code_hash")):
                for (v,) in c.execute(f"SELECT {col} FROM {t}").fetchall():
                    if v not in had[t]:
                        c.execute(f"DELETE FROM {t} WHERE {col} = ?", (v,))
            c.execute("DELETE FROM push_subscriptions")
        finally:
            c.close()
    ok(rows() == [] and all(os.environ.get(k) == v for k, v in env.items())
       and all({r[0] for r in q(f"SELECT {col} FROM {t}")} <= had[t] for t, col in (("sessions", "token_hash"), ("phone_codes", "code_hash"))),
       "끝나면 구독 표 · 키 환경 값을 되돌리고, 이 검사가 만든 세션 · QR 값은 하나도 남지 않음")


def check_push_pending(hr) -> None:
    """휴대폰 알림 처리할 일 개수 · 아침 9시 묶음 (2026-10-22): pending() 의 합이 화면(homePortalTasks · TODO)이 서버 응답으로 세는 값과 같다 —
    급여명세서 · 원천징수영수증 · 연봉계약서(열쇠가 없으면 0) · 처리함 · 돌려받은 계획서 · 지난달 한 줄 · 확인 메모 · 월간 확인 · 새 답글(팀장 · 본부장 · 대표이사 · HR 만) ·
    평가 진행(e2 — /api/eval/state 의 todo.me + admin, 평가하는 해가 아니면 0) · 이의 신청(e3 — todo.e3) ·
    대표이사 위임 중에는 대표이사 몫만 빼고 HR 몫은 센다. pending() 은 요청 안에서 불러도 그 요청의 g.actor 를 건드리지 않는다.
    morning() 은 평일 하루 한 번, 남은 일이 있는 휴대폰에만 묶음 한 통(마감 알림은 check_push_deadlines) — 410 이면 구독 삭제 · 500 이면 그대로 · 쓸 수 없는 구독 줄은 같은 일에서 정리.
    이 검사만의 가상 사람 · 부서(61xx). 감사로그 때문에 지울 수 없는 가상 사람은 비활성으로 두고, 그 밖의 자료(계획서 · 메모 · 명세서 · 영수증 · 세션 · 구독 …)와 환경 값은 되돌린다."""
    import base64
    import unittest

    import contract
    import contract_doc
    import kpi
    import push
    import withholding
    from flask import g

    print("휴대폰 알림 처리할 일 개수 · 아침 9시 묶음")
    TL, EMP, EMP2, CEO, DIR, IDLE, CT, GONE, OFF = range(6101, 6110)  # 팀장 · 직원 · 직원 · 대표이사 · 대표이사 직속 · 일 없음 · 계약서 · 비활성 · 포털 끔
    cast = (TL, EMP, EMP2, CEO, DIR, IDLE, CT, GONE, OFF)
    mine = ",".join(map(str, cast))
    year, day, utc, KST = kpi.plan_year(), year_day, timezone.utc, auth.KST
    env = {k: os.environ.get(k) for k in ("HR_PAY_KEY", "HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    raw_key = bytes(range(32))  # 앞 검사들이 쓴 열쇠와 같게 — 앞 검사가 남긴 명세서 · 영수증 · 계약서도 풀린다
    pay_key = base64.b64encode(raw_key).decode()
    real_send, real_pending, real_review, real_prog = push.SEND, push.pending, kpi._review, kpi.progress_json
    kyear = q("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", year)
    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1")]
    FRI_A = datetime(2026, 10, 23, 9, 0, tzinfo=KST)
    FRI_B, FRI_C, SAT, FRI_D = FRI_A + timedelta(days=7), FRI_A + timedelta(days=14), FRI_A + timedelta(days=1), FRI_A + timedelta(days=21)
    days = [d.date().isoformat() for d in (FRI_A, FRI_B, FRI_C, SAT, FRI_D)]
    now_iso = auth._iso(auth._now())
    calls: list[tuple[str, dict, int]] = []
    codes: dict[str, int] = {}

    def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 두고, 정해 둔 주소는 그 상태 코드를 돌려준다 (기본 201)
        calls.append((info["endpoint"], payload, ttl))
        return codes.get(info["endpoint"], 201)

    def client(uid):
        c = app.test_client()
        login(c, new_token(f"HW-{uid}"))
        return c

    def screen(c) -> list[tuple[str, int]]:
        """화면이 서버 응답으로 세는 처리할 일 (homePortalTasks · TODO · kvCanUse) — 0 은 빼고 push.LABELS 순서."""
        who = c.get("/api/me", base_url=BASE).json
        ib = c.get("/api/kpi/inbox", base_url=BASE).json
        km = c.get("/api/kpi/me", base_url=BASE).json
        pr = c.get(f"/api/kpi/progress/{who['user_id']}?year={km['year']}", base_url=BASE) if km["plan"] and km["plan"]["confirmed"] else None
        leads = who["position"] in ("TEAM_LEADER", "DIVISION_HEAD", "CEO") or who["is_hr"]
        todo = c.get("/api/eval/state", base_url=BASE).json["todo"]
        n = {"p1": sum(not s["seen"] for s in c.get("/api/payslips", base_url=BASE).json["slips"]),
             "p2": sum(r["canSign"] for r in c.get("/api/contracts/mine", base_url=BASE).json["rows"]),
             "p3": sum(not y["seen"] for y in c.get("/api/withholding", base_url=BASE).json["years"]),
             "inbox": len(ib["items"]), "back": int(km["plan"] is not None and km["plan"]["noteKind"] == "RETURNED"),
             "prev": pr.json["todo"]["prev"] if pr is not None and pr.status_code == 200 else 0,
             "memos": ib["review"]["memos"], "check": ib["review"]["check"] if leads else 0, "replies": ib["review"]["replies"] if leads else 0,
             "e2": todo["me"] + todo["admin"], "e3": todo["e3"],  # 평가 진행 (TODO.e2 = evTodo — 서버 todo 의 me + admin) · 이의 신청 (TODO.e3 — 서버 todo 의 e3)
             "cards": c.get("/api/cards", base_url=BASE).json["unread"]}  # 새 카드 (카드 상자의 안 읽음)
        return [(k, n[k]) for k in push.LABELS if n[k]]

    def same(c, uid: int, label: str) -> list[tuple[str, int]]:
        want, got = screen(c), push.pending(app, uid)
        ok(got == want, f"{label}: pending {got} = 화면 {want}")
        return got

    def plan(tag):  # 정기 2 · 비정기 1 (check_kpi_manager 와 같은 모양)
        item = lambda name, w, kind, **more: {  # noqa: E731
            "action": name, "formula": "가상 측정식", "weight": w, "owner": "가상", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
            "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}
        return [{"team": f"{tag} 팀목표", "personal": f"{tag} 개인목표", "items": [
            item(f"{tag} 정기 1", 40, "reg"), item(f"{tag} 정기 2", 30, "reg"),
            item(f"{tag} 비정기", 30, "irr", scheds=[{"due": f"{year}-03-31", "name": "가상 초안"}, {"due": f"{year}-12-31", "name": "가상 마감"}])]}]

    def submit(c, tag) -> bool:
        return (c.post("/api/kpi/me/draft", base_url=BASE, headers=ORIGIN, json={"draft": plan(tag)}).status_code == 200
                and c.post("/api/kpi/me/submit", base_url=BASE, headers=ORIGIN, json={}).status_code == 200)

    seq = [0]

    def put(uid, *, kind="phone", seen=timedelta(0), left=timedelta(days=30), revoked=False, sub_user=None, at=FRI_A) -> str:
        """가상 세션 + 구독 한 줄 (시각은 at 기준) — 구독 주소를 돌려준다."""
        seq[0] += 1
        t, h, ep = at.astimezone(utc), f"{seq[0]:02d}" + "5" * 62, f"https://push.example.invalid/pend-{seq[0]}"
        x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, revoked_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?)",
          h, uid, auth._iso(t - timedelta(days=40)), auth._iso(t - seen), auth._iso(t + left), auth._iso(t) if revoked else None, kind)
        x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, sub_user or uid, ep, auth._iso(t))
        return ep

    subs = lambda: sorted(r[0] for r in q("SELECT endpoint FROM push_subscriptions WHERE endpoint LIKE 'https://push.example.invalid/pend-%'"))  # noqa: E731
    ran = lambda: q("SELECT day, people FROM push_morning ORDER BY day")  # noqa: E731
    post = lambda c, p, body=None: c.post(p, base_url=BASE, headers=ORIGIN, json=body or {})  # noqa: E731

    try:
        ok(not q("SELECT 1 FROM push_morning") and not q("SELECT 1 FROM push_subscriptions"), "(검사용) 시작할 때 아침 묶음 기록 · 구독이 비어 있음")
        os.environ["HR_PAY_KEY"] = pay_key

        # ---- 앞 검사가 남긴 실제 모양의 사람들 — 화면과 같은 숫자 ----
        old = {uid: client(uid) for uid in (2, 41, 44, 45, 46, 47, 50, 61, 71, 91)}  # 3 은 앞 검사가 규칙 번호(rid) 없는 확정본을 직접 넣어 두어 진행 기록이 500, 131 · 139 는 풀리지 않게 둔 계약서가 있어 뺀다 (그런 줄은 화면이 건너뛴다 — 열쇠가 틀린 설정 오류일 때만 생기는 차이)
        old[1] = hr
        for uid, c in sorted(old.items()):
            same(c, uid, f"기존 가상 사람 {uid}")

        # ---- 이 검사만의 조직 · 사람 ----
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (6101, 'Q61', '가상Q본부', 'DIVISION', NULL)")
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (6102, 'Q62', '가상Q팀', 'TEAM', 6101)")
        for uid, dept, on, portal in ((TL, 6102, 1, 1), (EMP, 6102, 1, 1), (EMP2, 6102, 1, 1), (CEO, 6101, 1, 1), (DIR, 6101, 1, 1),
                                      (IDLE, 6102, 1, 1), (CT, 6102, 1, 1), (GONE, 6102, 0, 1), (OFF, 6102, 1, 0)):
            x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, deactivated_at, writes_kpi)"
              " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", uid, f"가상Q{uid}", f"q{uid}@example.invalid", dept, f"HW-{uid}", portal, on,
              None if on else "2027-01-01T00:00:00Z", int(uid != CEO))
        x("INSERT INTO user_roles (user_id, role) VALUES (?, 'TEAM_LEADER')", TL)
        x("INSERT INTO user_roles (user_id, role) VALUES (?, 'CEO')", CEO)
        for uid in other_ceos:  # 대표이사는 이 사람 하나만 — 본부장이 없는 곳의 관리자는 처음 만나는 대표이사
            x("UPDATE users SET is_active = 0, deactivated_at = ? WHERE id = ?", now_iso, uid)
        x("INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, 1)"
          " ON CONFLICT (year) DO UPDATE SET edit_from = excluded.edit_from, edit_to = excluded.edit_to, eval_start = NULL", year, day(-1), day(7), now_iso)
        c_tl, c_emp, c_emp2, c_ceo, c_dir, c_ct = (client(u) for u in (TL, EMP, EMP2, CEO, DIR, CT))
        ok(all(push.pending(app, u) == screen(c) == [] for u, c in ((TL, c_tl), (EMP, c_emp), (EMP2, c_emp2), (CEO, c_ceo), (DIR, c_dir), (CT, c_ct)))
           and push.pending(app, IDLE) == [], "처음에는 아무도 처리할 일이 없음 (pending 도 화면도 빈 목록)")

        # ---- KPI 처리함 · 돌려받음 ----
        ok(submit(c_emp, "가상E"), "(검사용) 직원이 KPI 계획서를 제출")
        ok(same(c_tl, TL, "제출 1건 → 팀장") == [("inbox", 1)], "제출한 계획서 1건은 바로 위 팀장의 inbox 1")
        ok(push.pending(app, EMP) == [], "제출한 본인에게는 일이 없음")
        ok(post(c_tl, f"/api/kpi/plans/{EMP}/return", {"note": "가상 사유"}).status_code == 200, "(검사용) 팀장이 돌려보냄")
        ok(same(c_emp, EMP, "돌려보냄 → 본인") == [("back", 1)] and same(c_tl, TL, "처리함이 비어 감") == [], "돌려보낸 계획서는 본인에게 back 1 · 팀장 inbox 는 0")
        ok(submit(c_emp2, "가상F"), "(검사용) 다른 직원이 제출")
        ok(same(c_tl, TL, "다시 제출 1건") == [("inbox", 1)], "팀장 inbox 1")

        # ---- 대표이사 위임 ----
        ok(submit(c_dir, "가상D") and same(c_ceo, CEO, "대표이사 직속 제출") == [("inbox", 1)], "대표이사 직속의 제출은 위임을 끈 동안 대표이사 inbox 1")
        hr0 = dict(same(hr, 1, "위임 끔 → HR")).get("inbox", 0)
        ok(post(c_ceo, "/api/kpi/delegation", {"on": True}).status_code == 200, "(검사용) 대표이사가 HR 에 맡김")
        ok(push.pending(app, CEO) == [] and screen(c_ceo) == [("inbox", 1)], "맡긴 동안 대표이사 inbox 는 0 (화면의 왼쪽 메뉴 숫자는 그대로 1 — 설계 7장)")
        ok(dict(same(hr, 1, "위임 중 → HR")).get("inbox", 0) == hr0 + 1, "같은 때 HR 의 inbox 에는 대신 몫 1이 더해짐")
        ok(same(c_tl, TL, "위임은 팀장과 무관") == [("inbox", 1)], "대표이사 위임은 다른 팀장의 inbox 를 바꾸지 않음")

        # ---- 확인 메모 · 새 답글 (팀장 · 본부장 · 대표이사 · HR 만 월간 확인 · 새 답글) ----
        x("INSERT INTO kpi_memos (user_id, year, author_id, text, created_at) VALUES (?, ?, ?, '가상 메모', ?)", EMP, year, TL, now_iso)
        memo = q("SELECT id FROM kpi_memos WHERE user_id = ?", EMP)[0][0]
        ok(same(c_emp, EMP, "메모 도착") == [("back", 1), ("memos", 1)], "내가 확인 안 한 메모는 memos 1 (LABELS 순서: back 다음)")
        x("INSERT INTO kpi_memo_replies (memo_id, author_id, who, text, created_at) VALUES (?, ?, 'owner', '가상 답글', ?)", memo, EMP, now_iso)
        ok(same(c_tl, TL, "새 답글") == [("inbox", 1), ("replies", 1)], "메모를 쓴 팀장에게 본인의 새 답글 replies 1")
        kpi._review = lambda *a, **k: {"check": 2, "replies": 3, "memos": 1}  # 월간 확인 · 새 답글은 직책이 있는 사람만 — 숫자를 정해 놓고 사람마다 본다
        try:
            got = {u: dict(push.pending(app, u)) for u in (EMP, TL, 1, CEO, IDLE)}
        finally:
            kpi._review = real_review
        ok(got[EMP].get("check") is None and got[EMP].get("replies") is None and got[EMP]["memos"] == 1
           and got[IDLE].get("check") is None and got[IDLE].get("replies") is None and got[IDLE]["memos"] == 1,
           "직책 없는 직원에게는 check · replies 가 없고 memos 만 (화면 kvCanUse)")
        ok(all(got[u]["check"] == 2 and got[u]["replies"] == 3 and got[u]["memos"] == 1 for u in (TL, 1, CEO)),
           "팀장 · HR(직책 없음) · 대표이사에게는 check · replies 도 셈")
        kpi.progress_json = lambda conn, uid, yr, *a, **k: {"todo": {"months": 3, "prev": 2, "results": 1}}  # 지난달 한 줄: 진행 기록의 todo.prev
        try:
            got = push.pending(app, EMP)
            kpi.progress_json = lambda *a, **k: None
            none = push.pending(app, EMP)
        finally:
            kpi.progress_json = real_prog
        ok(got == [("back", 1), ("prev", 2), ("memos", 1)] and none == [("back", 1), ("memos", 1)],
           "지난달 한 줄(prev)은 진행 기록의 todo.prev (back 과 memos 사이) — 진행 기록이 없으면 빠짐")

        # ---- 급여명세서 · 원천징수영수증 · 연봉계약서 (열쇠 없으면 0) ----
        slip = {"userId": EMP, "code": "Q6102", "head": {"code": "Q6102", "name": "가상Q", "dept": "가상Q팀", "title": "프로", "job": "연구"},
                "pay": [["기본급", 2500000]], "ded": [["소득세", 30000]], "gross": 2500000, "deduct": 30000, "net": 2470000}
        ok(post(hr, "/api/payslips/2093-01", {"payDate": "2093-01-25", "slips": [slip]}).status_code == 200, "(검사용) HR 이 명세서를 올림")
        ok(same(c_emp, EMP, "공개 전 명세서") == [("back", 1), ("memos", 1)], "공개 전(DRAFT) 달은 안 셈")
        ok(post(hr, "/api/payslips/2093-01/open").status_code == 200, "(검사용) 공개")
        ok(same(c_emp, EMP, "공개된 명세서") == [("p1", 1), ("back", 1), ("memos", 1)] and push.pending(app, EMP2) == [], "공개된 달의 안 본 명세서 1 → p1 1 (남의 것은 안 셈)")
        ok(post(c_emp, "/api/payslips/2093-01/seen").status_code == 200 and same(c_emp, EMP, "연 명세서") == [("back", 1), ("memos", 1)], "연 명세서는 빠짐")
        x("UPDATE payslips SET seen_at = NULL WHERE ym = '2093-01'")
        pdf = base64.b64encode(b"%PDF-1.4\n%fake").decode()
        person = {"userId": EMP, "sum": dict.fromkeys(withholding.SUM_KEYS, 0), "pdf": pdf}
        ok(post(hr, "/api/withholding/2093", {"mode": "all", "reset": True, "total": 1, "people": [person]}).status_code == 200
           and post(hr, "/api/withholding/2093/open").status_code == 200, "(검사용) HR 이 영수증을 올리고 공개")
        ok(same(c_emp, EMP, "공개된 영수증") == [("p1", 1), ("p3", 1), ("back", 1), ("memos", 1)], "공개된 해의 안 본 영수증 1 → p3 1")
        seal = (q("SELECT id FROM contract_seals ORDER BY id LIMIT 1") or [(None,)])[0][0]
        if seal is None:  # 앞 검사가 직인을 안 남겼을 때만 (직인 줄은 지울 수 없다)
            x("INSERT INTO contract_seals (png, created_at, created_by) VALUES (zeroblob(40), ?, 1)", now_iso)
            seal = q("SELECT id FROM contract_seals ORDER BY id DESC LIMIT 1")[0][0]
        cy, cbody = 2091, {"code": "Q-CT", "name": "가상Q", "dept": "가상Q팀", "title": "", "birth6": "900315", "base": 2418900, "ability": 1211000,
                           "pos": 300000, "site": 0, "qual": 0, "reason": ""}
        x("INSERT INTO contracts (year, user_id, version, status, starts_on, body, created_at, created_by, sent_at, sent_by, due_on, company, seal_id)"
          " VALUES (?, ?, 1, 'SENT', ?, ?, ?, 1, ?, 1, ?, ?, ?)", cy, CT, contract_doc.round_period(cy)[0], contract._lock(raw_key, contract._aad(cy, CT, 1, "body"), cbody),
          now_iso, now_iso, day(14), contract._lock(raw_key, contract._aad(cy, CT, 1, "company"), {"name": contract_doc.COMPANY_NAME, "ceo": "○○○", "addr": "가상시", "kind": "가상 서비스업"}), seal)
        ok(same(c_ct, CT, "서명 대기 계약서") == [("p2", 1)], "서명 대기(SENT)인 내 연봉계약서 1건 → p2 1 (화면의 canSign)")
        os.environ.pop("HR_PAY_KEY")
        ok(push.pending(app, EMP) == [("back", 1), ("memos", 1)] and push.pending(app, CT) == [] and screen(c_emp) == push.pending(app, EMP) and screen(c_ct) == [],
           "서버에 HR_PAY_KEY 가 없으면 p1 · p2 · p3 는 0 (화면의 목록도 비어 있음)")
        os.environ["HR_PAY_KEY"] = pay_key
        ok(push.pending(app, EMP) == [("p1", 1), ("p3", 1), ("back", 1), ("memos", 1)], "(검사용) 열쇠를 되돌리면 다시 셈")

        # ---- 요청 안에서 불러도 그 요청의 g.actor · 연결을 건드리지 않는다 (Task 4 의 inline 보내기) ----
        with app.test_request_context():
            outer = auth.actor_of(auth._db(), TL)
            g.actor, db = outer, auth._db()
            inner = push.pending(app, EMP)
            ok(g.actor is outer and auth._db() is db and db.execute("SELECT 1").fetchone() == (1,) and inner == [("p1", 1), ("p3", 1), ("back", 1), ("memos", 1)],
               "요청 안에서 pending() 을 불러도 그 요청의 g.actor · DB 연결은 그대로, 값은 맞음")

        # ---- 평가 진행 (e2): 평가할 사람이 둘인 팀장 — 화면 숫자(/api/eval/state 의 todo 합)와 같다 ----
        import evaluation
        EY, LEAD_E, EMP_E, real_now = evaluation.FIRST_YEAR, 102, 103, auth._now  # check_eval_admin 의 가상 조직: 팀장이 평가하는 팀원 · 현장직(KPI 없음 — 처음부터 점수 단계)
        x("UPDATE ev_targets SET stage = 'SCORE' WHERE year = ? AND user_id = ?", EY, EMP_E)  # 팀원이 자기평가를 냈다
        auth._now = lambda: datetime(EY, 12, 15, 3, tzinfo=utc)  # 그해 평가 중(자기평가 마감 12/10 뒤) — 보일 해가 평가하는 해
        try:
            got = same(client(LEAD_E), LEAD_E, "평가할 사람이 둘인 팀장")
        finally:
            auth._now = real_now
            x("UPDATE ev_targets SET stage = 'SELF' WHERE year = ? AND user_id = ?", EY, EMP_E)
        ok(("e2", 2) in got, "평가 진행(e2): 평가할 사람이 둘인 팀장의 pending 에 ('e2', 2) — 화면 숫자와 같다")

        # ---- 아침 9시 묶음 ----
        pub, priv = push.keygen()
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        push.SEND = fake_send
        live = {u: put(u) for u in (EMP, TL, EMP2, IDLE, CEO)}
        orphan = [put(IDLE, revoked=True), put(IDLE, left=timedelta(seconds=-1)), put(IDLE, seen=timedelta(days=31)), put(IDLE, kind="pc"),
                  put(IDLE, sub_user=EMP), put(GONE), put(OFF)]
        ok(len(subs()) == 12, "(검사용) 가상 구독 12줄: 살아 있는 5 + 쓸 수 없는 7 (해제 · 만료 · 31일 안 씀 · PC · 남의 구독 · 비활성 · 포털 끔)")
        os.environ.pop("HR_PUSH_PRIVATE")
        ok(push.morning(app, FRI_A) == 0 and not calls and not ran() and len(subs()) == 12, "알림 키가 없으면 0 — 보내지 않고 기록도 쓰지 않고 구독 줄도 그대로")
        os.environ.update(HR_PUSH_PRIVATE=priv)
        ok(push.morning(app, SAT) == 0 and not calls and not ran() and len(subs()) == 12, "토요일에는 0 — 보내지 않고 기록도 없음")
        ok(push.morning(app, FRI_A - timedelta(minutes=1)) == 0 and push.morning(app, FRI_A + timedelta(hours=9)) == 0 and not calls and not ran() and len(subs()) == 12,
           "평일이어도 근무시간(9시~18시) 밖이면 0 — 금요일 8:59 · 18:00 에 손으로 돌려도(늦은 실행) 보내지 않고 기록도 없음")
        ok(push.morning(app, FRI_A) == 2, "금요일 9시: 남은 일이 있는 두 사람(직원 · 팀장)에게 보냄 → 2")
        by_ep = {ep: (p, ttl) for ep, p, ttl in calls}
        ok(len(calls) == 2 and set(by_ep) == {live[EMP], live[TL]}, "남은 일 있는 휴대폰에만 한 통씩 (일 없는 직원 · 위임한 대표이사 · 제출만 한 직원에게는 안 감)")
        ok(by_ep[live[EMP]] == ({"title": "처리할 일이 4건 있어요", "body": "새 급여명세서 · 새 원천징수영수증 · 반려된 KPI 다시 작성 · 확인 메모 확인",
                                 "url": "/", "badge": 4, "tag": "morning"}, push.TTL_MORNING), "직원: 제목 = 개수 합 · 내용 = 종류 이름 · badge = 합 · tag morning · TTL 9시간")
        ok(by_ep[live[TL]] == ({"title": "처리할 일이 2건 있어요", "body": "처리할 KPI · 확인 메모에 새 답글", "url": "/", "badge": 2, "tag": "morning"}, push.TTL_MORNING),
           "팀장: 처리할 KPI 1 + 새 답글 1")
        ok(ran() == [("2026-10-23", 2)] and subs() == sorted(live.values()), "그날을 기록(보낸 사람 2)하고, 쓸 수 없는 구독 7줄은 같은 일에서 지움 · 살아 있는 5줄은 그대로")
        ok(push.morning(app, FRI_A) == 0 and push.morning(app, FRI_A + timedelta(hours=3)) == 0 and len(calls) == 2 and ran() == [("2026-10-23", 2)],
           "같은 날 다시 돌려도 0 — 보낸 것은 늘지 않음")

        # 한 사람의 개수 세기가 터져도 나머지는 간다 (팀장 것이 터짐)
        def flaky(flask_app, uid):
            if uid == TL:
                raise RuntimeError("검사용 고장")
            return real_pending(flask_app, uid)

        calls[:] = []
        push.pending = flaky
        try:
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:  # 경고 한 줄 — 예외 종류만 (이름 · 주소 · 문구 없음)
                n_b = push.morning(app, FRI_B)
        finally:
            push.pending = real_pending
        ok(n_b == 1 and [c[0] for c in calls] == [live[EMP]] and ran()[-1] == ("2026-10-30", 1) and subs() == sorted(live.values())
           and cm.output == ["WARNING:push:morning pending failed: RuntimeError"],
           "한 사람(팀장)의 개수 세기가 터져도 나머지(직원)에게는 감 · 터진 사람의 구독은 그대로 · 로그에는 예외 종류만")

        # 푸시 서버가 구독이 없어졌다(410)고 하면 지우고, 500 이면 그대로 둔다
        calls[:] = []
        codes.update({live[EMP]: 410, live[TL]: 500})
        with unittest.TestCase().assertLogs("push", "WARNING") as cm:  # 거절 로그에는 상태 코드만 (410 은 줄을 지울 뿐 경고가 아님)
            n_c = push.morning(app, FRI_C)
        ok(n_c == 0 and {c[0] for c in calls} == {live[EMP], live[TL]} and ran()[-1] == ("2026-11-06", 0) and cm.output == ["WARNING:push:morning push rejected: status 500"],
           "410 · 500 은 보낸 것으로 세지 않음 (그날은 기록) · 500 은 상태 코드만 경고로 남김")
        ok(subs() == sorted(set(live.values()) - {live[EMP]}), "410 을 돌려준 구독만 삭제 — 500 을 돌려준 구독은 그대로")
        ok(ran() == [("2026-10-23", 2), ("2026-10-30", 1), ("2026-11-06", 0)], "기록은 돌린 세 날만 — 보낸 사람 수와 함께 (실패한 날도 그날은 돌린 것)")

        # 날을 보내기 전에 먼저 잡는다 — 겹쳐 도는 두 번째 실행 · 앞 실행이 중간에 멈춘 뒤 다시 돎에도 한 사람에게 두 통이 가지 않는다
        d_day, calls[:] = FRI_D.date().isoformat(), []
        codes.clear()
        put(EMP)  # 앞에서 410 으로 지워진 직원의 기기를 다시 등록 (팀장 기기는 그대로)
        x("INSERT INTO push_morning (day, people, ran_at) VALUES (?, 0, ?)", d_day, now_iso)  # 다른 실행이 방금 오늘을 잡았다
        ok(push.morning(app, FRI_D) == 0 and not calls and ran()[-1] == (d_day, 0), "오늘 줄이 이미 있으면(다른 실행이 잡음) 0 — 보내지 않고 그 줄도 그대로")
        x("DELETE FROM push_morning WHERE day = ?", d_day)
        during = []

        def overlap_send(info, payload, ks, ttl):  # 보내는 도중에 같은 날 두 번째 실행이 들어온다 (예약 작업이 겹침)
            during.append((push.morning(app, FRI_D), ran()[-1]))
            return fake_send(info, payload, ks, ttl)

        push.SEND = overlap_send
        try:
            n_d = push.morning(app, FRI_D)
        finally:
            push.SEND = fake_send
        ok(n_d == 2 and len(calls) == 2 and during == [(0, (d_day, 0))] * 2 and ran()[-1] == (d_day, 2),
           "보내는 도중에는 오늘 줄이 이미 있어(보낸 사람 0) 두 번째 실행은 0 · 보낸 것은 늘지 않고, 첫 실행은 끝에 보낸 사람 수(2)로 고침")
        ok(push.morning(app, FRI_D) == 0 and len(calls) == 2 and ran()[-1] == (d_day, 2), "끝난 뒤 다시 돌려도 0")
    finally:
        push.SEND, push.pending, kpi._review, kpi.progress_json = real_send, real_pending, real_review, real_prog
        for k, v in env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        x(f"DELETE FROM push_morning WHERE day IN ({','.join('?' * len(days))})", *days)
        x(f"DELETE FROM sessions WHERE user_id IN ({mine})")  # 이 검사의 로그인 · 가상 세션 (구독 줄도 함께)
        x(f"DELETE FROM kpi_memos WHERE user_id IN ({mine})")  # 답글도 함께
        x(f"DELETE FROM kpi_plans WHERE user_id IN ({mine})")
        x(f"DELETE FROM ceo_delegation WHERE ceo_user_id IN ({mine})")
        x("DELETE FROM payslip_months WHERE ym = '2093-01'")  # 명세서도 함께
        x("DELETE FROM withholding_years WHERE year = 2093")  # 영수증도 함께
        x("UPDATE contracts SET status = 'CANCELLED', cancelled_at = ?, cancelled_by = 1, cancel_reason = '검사 정리' WHERE user_id = ? AND status = 'SENT'", now_iso, CT)  # 발송한 줄은 지울 수 없어 파기로
        if kyear:
            x("UPDATE kpi_years SET edit_from = ?, edit_to = ?, eval_start = ? WHERE year = ?", *kyear[0], year)
        else:
            x("DELETE FROM kpi_years WHERE year = ?", year)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)
        x(f"UPDATE users SET is_active = 0, deactivated_at = ?, portal_enabled = 0 WHERE id IN ({mine})", now_iso)  # 감사로그가 가리켜 지울 수 없는 가상 사람은 비활성으로
    ok(not q("SELECT 1 FROM push_morning") and not q("SELECT 1 FROM push_subscriptions") and not q(f"SELECT 1 FROM sessions WHERE user_id IN ({mine})")
       and not q(f"SELECT 1 FROM kpi_plans WHERE user_id IN ({mine})") and not q("SELECT 1 FROM kpi_memos") and not q("SELECT 1 FROM ceo_delegation")
       and not q("SELECT 1 FROM payslip_months WHERE ym = '2093-01'") and not q("SELECT 1 FROM withholding_years WHERE year = 2093")
       and q("SELECT COUNT(*) FROM contracts WHERE status = 'SENT' AND user_id = ?", CT) == [(0,)]
       and q("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", year) == kyear
       and all(q("SELECT is_active FROM users WHERE id = ?", uid) == [(1,)] for uid in other_ceos)
       and all(os.environ.get(k) == v for k, v in env.items()) and push.SEND is real_send,
       "끝나면 구독 · 기록 · 세션 · 계획서 · 메모 · 위임 · 명세서 · 영수증 · 계약서(파기)를 정리하고 기간 · 대표이사 · 환경 값 · 보내기 함수를 되돌림")


def check_push_events(hr) -> None:
    """휴대폰 알림 일이 생길 때 보내기 (2026-10-22): 급여명세서 · 원천징수영수증 공개(공개 중인 해에 사람만 더 올리기 포함) · KPI 제출 · 수정 요청 · 일정 변경 요청 · 돌려보냄 ·
    확인 메모(쓰기 · 고치기 · 답글) · 연봉계약서 발송 · 파기 뒤 다시 보내기 · 문의가 commit 뒤에 받을 사람의 휴대폰으로 한 통씩 간다 (제목 · 열 화면 · TTL · 앱 아이콘 숫자 = 그 사람 pending 합계).
    돌려보내는 일만 가고 거절(reject)은 안 가며, 이미 그 상태면 안 간다. 대표이사 직속 KPI 는 위임을 켜면 HR 전원 · 대표이사가 없으면 HR 전원.
    근무시간(평일 9~18시) 밖 · 알림 키가 없음이면 안 보내고 원래 일은 성공한다. 푸시 서버가 500 · 410 · 예외여도 원래 응답은 그대로(410 은 구독 삭제).
    시각은 금요일 10:00 KST 로 고정하고(auth._now) 보내기 함수는 가짜로 바꾼다(SYNC 로 바로 — 한 번은 스레드로). 가상 사람 · 부서(62xx) · 가상 구독 주소(example.invalid).
    감사로그 · 확정본 때문에 지울 수 없는 것(가상 사람 · 확정된 계획서)은 비활성으로 두고, 그 밖의 자료와 환경 값 · 시각 · 보내기 함수는 되돌린다."""
    import base64
    import unittest

    import contract
    import contract_doc
    import kpi
    import push
    import withholding

    print("휴대폰 알림 일이 생길 때 보내기")
    TL, EMP, EMP2, OTHER, CEO, DIR, HR2, CT1, CT2, CT3, HR3 = cast = tuple(range(6201, 6212))  # 팀장 · 직원 · 직원 · 구경꾼 · 대표이사 · 대표이사 직속 · HR · 계약서 셋 · 비활성 HR
    mine = ",".join(map(str, cast))
    utc, KST, year = timezone.utc, auth.KST, 2026
    T0 = datetime(2026, 10, 23, 10, 0, tzinfo=KST)  # 금요일 근무시간
    clock = [T0]
    ym, wy, cy = "2094-03", 2094, 2092  # 가상 명세서 달 · 영수증 해 · 계약서 회차 (다른 검사가 쓰지 않는 값)
    env = {k: os.environ.get(k) for k in ("HR_PAY_KEY", "HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    raw_key = bytes(range(32))  # 앞 검사들이 쓴 열쇠와 같게 — 앞 검사가 남긴 계약서 설정도 풀린다
    real_now, real_send, real_sync, real_pending, real_forget = auth._now, push.SEND, push.SYNC, push.pending, push.forget
    kyear = q("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", year)
    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1")]
    had = {r[0] for r in q("SELECT token_hash FROM sessions")}  # 시작 때 있던 세션 — 끝나면 이 검사가 만든 세션(로그인 · 가상)만 지운다
    PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="  # 1x1 그림 (가짜 직인)
    calls: list[tuple[str, dict, int]] = []
    seen_all: list[dict] = []  # 보낸 내용 전부 (이름이 새지 않았는지 마지막에 본다)
    owner: dict[str, int] = {}  # 구독 주소 → 받는 사람
    dev: dict[int, str] = {}  # 사람 → 그 사람의 (첫) 기기 주소
    codes: dict[str, int] = {}
    boom: set[str] = set()
    threads: list[str] = []  # 보낸 스레드 이름 — 평소 검사는 요청 스레드 그대로, 스레드 검사는 push-deliver
    seq = [0]

    def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 두고, 정해 둔 주소는 그 상태 코드를 돌려주거나(기본 201) 던진다
        calls.append((info["endpoint"], payload, ttl))
        threads.append(threading.current_thread().name)
        if info["endpoint"] in boom:
            raise RuntimeError("검사용 고장")
        return codes.get(info["endpoint"], 201)

    def take() -> list[tuple[int, dict, int]]:
        """지금까지 보낸 것 [(받은 사람, 내용, ttl)] — 사람 순으로 돌려주고 비운다."""
        rows = sorted(((owner[ep], p, ttl) for ep, p, ttl in calls), key=lambda r: r[0])
        seen_all.extend(p for _, p, _ in rows)
        calls.clear()
        return rows

    def got(kind: str, title: str, url: str, *uids: int, badge: int | None = None) -> bool:
        """방금 보낸 것이 정확히 uids 에게 한 통씩이고, 제목 · 내용 · 열 화면 · TTL 이 맞고 앱 아이콘 숫자(badge)가 그 사람의 처리할 일 합계(badge 를 주면 그 숫자)인가."""
        rows = take()
        return (sorted(u for u, _, _ in rows) == sorted(uids)
                and all(p["title"] == title and p["body"] == push.MESSAGES[kind][1] and p["url"] == url and set(p) == {"title", "body", "url", "badge"}
                        and p["badge"] == (sum(n for _, n in push.pending(app, u)) if badge is None else badge) and ttl == push.TTL_EVENT for u, p, ttl in rows))

    def put(uid: int) -> str:
        """가상 휴대폰 세션 + 구독 한 줄 (고정한 시각 기준) — 구독 주소를 돌려준다."""
        seq[0] += 1
        t, h, ep = T0.astimezone(utc), f"{seq[0]:02d}" + "4" * 62, f"https://push.example.invalid/ev-{uid}-{seq[0]}"
        x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')",
          h, uid, auth._iso(t), auth._iso(t), auth._iso(t + timedelta(days=30)))
        x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, auth._iso(t))
        owner[ep] = uid
        return ep

    subs = lambda uid: sorted(r[0] for r in q("SELECT endpoint FROM push_subscriptions WHERE user_id = ?", uid))  # noqa: E731

    def client(uid: int):  # 지금(고정한) 시각의 PC 로그인
        c = app.test_client()
        login(c, new_token("hr" if uid == 1 else f"HW-{uid}"))
        return c

    post = lambda c, p, body=None: c.post(p, base_url=BASE, headers=ORIGIN, json=body or {})  # noqa: E731
    state = lambda: q("SELECT status FROM payslip_months WHERE ym = ?", ym)

    def plan(tag):  # 정기 2 · 비정기 1 (check_push_pending 과 같은 모양)
        item = lambda name, w, kind, **more: {  # noqa: E731
            "action": name, "formula": "가상 측정식", "weight": w, "owner": "가상", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
            "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}
        return [{"team": f"{tag} 팀목표", "personal": f"{tag} 개인목표", "items": [
            item(f"{tag} 정기 1", 40, "reg"), item(f"{tag} 정기 2", 30, "reg"),
            item(f"{tag} 비정기", 30, "irr", scheds=[{"due": f"{year}-03-31", "name": "가상 초안"}, {"due": f"{year}-12-31", "name": "가상 마감"}])]}]

    def submit(c, tag) -> bool:
        return (post(c, "/api/kpi/me/draft", {"draft": plan(tag)}).status_code == 200 and post(c, "/api/kpi/me/submit").status_code == 200)

    try:
        pub, priv = push.keygen()
        os.environ.update(HR_PAY_KEY=base64.b64encode(raw_key).decode(), HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        cfg = hr.get("/api/contracts/settings", base_url=BASE).json  # HR 쪽 준비는 시각을 고정하기 전에 (hr 로그인은 고정한 시각에서는 만료) — 앞 검사가 남긴 계약서 설정이 없으면 만든다
        if cfg["settings"] is None:
            ok(post(hr, "/api/contracts/settings", {"ceo": "○○○", "addr": "가상시 가상구 가상로 1", "kind": "가상 서비스업", "seal": PNG}).status_code == 200, "(검사용) 계약서 설정을 만듦")
        auth._now = lambda: clock[0].astimezone(utc)
        push.SYNC, push.SEND = True, fake_send
        now_iso = auth._iso(auth._now())

        # ---- 이 검사만의 조직 · 사람 · 휴대폰 구독 ----
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (6201, 'Q71', '가상R본부', 'DIVISION', NULL)")
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (6202, 'Q72', '가상R팀', 'TEAM', 6201)")
        for uid, dept, kpi_on, has_ct, active in ((TL, 6202, 1, 0, 1), (EMP, 6202, 1, 0, 1), (EMP2, 6202, 1, 0, 1), (OTHER, 6202, 1, 0, 1), (CEO, 6201, 0, 0, 1),
                                                  (DIR, 6201, 1, 0, 1), (HR2, 6201, 0, 0, 1), (CT1, 6202, 0, 1, 1), (CT2, 6202, 0, 1, 1), (CT3, 6202, 0, 0, 1),
                                                  (HR3, 6201, 0, 0, 0)):
            x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, deactivated_at, writes_kpi, has_contract)"
              " VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)", uid, f"가상R{uid}", f"r{uid}@example.invalid", dept, f"HW-{uid}", active, None if active else "2027-01-01T00:00:00Z", kpi_on, has_ct)
        for uid, role in ((TL, "TEAM_LEADER"), (CEO, "CEO"), (HR2, "HR_ADMIN"), (HR3, "HR_ADMIN")):
            x("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", uid, role)
        for uid in other_ceos:  # 대표이사는 이 사람 하나만 — 본부장이 없는 곳의 관리자는 처음 만나는 대표이사
            x("UPDATE users SET is_active = 0, deactivated_at = ? WHERE id = ?", now_iso, uid)
        x("INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, 1)"
          " ON CONFLICT (year) DO UPDATE SET edit_from = excluded.edit_from, edit_to = excluded.edit_to, eval_start = NULL", year, f"{year}-01-01", f"{year}-12-31", now_iso)
        for uid in (1, *cast[:-1]):  # 활성인 모든 사람이 휴대폰 하나씩 — 받지 말아야 할 사람이 받으면 잡힌다
            dev[uid] = put(uid)
        c_hr, c_tl, c_emp, c_emp2, c_ceo, c_dir, c_ct1 = (client(u) for u in (1, TL, EMP, EMP2, CEO, DIR, CT1))
        ok(len(owner) == 11 and not calls and q("SELECT COUNT(*) FROM push_subscriptions") == [(11,)], "(검사용) 가상 구독 11줄 — HR 둘 · 직원들 · 구경꾼까지, 아직 아무것도 안 보냄")

        c = connect(os.environ["HR_DB_PATH"])
        try:
            active_hr = [r[0] for r in q("SELECT DISTINCT r.user_id FROM user_roles r JOIN users u ON u.id = r.user_id WHERE r.role = 'HR_ADMIN' AND u.is_active = 1 ORDER BY 1")]
            ok(push.hr_admins(c) == active_hr and {1, HR2} <= set(active_hr) and HR3 not in active_hr
               and q("SELECT 1 FROM user_roles WHERE user_id = ? AND role = 'HR_ADMIN'", HR3), "hr_admins: 활성 HR 전원 (HR 권한이 있어도 비활성이면 빠짐)")
            ok(push.approvers(c, EMP) == [TL] and push.approvers(c, TL) == [CEO] and push.approvers(c, CEO) == push.hr_admins(c),
               "approvers: 바로 위 관리자 · 팀장은 (본부장이 없으면) 대표이사 · 대표이사는 관리자가 없어 HR 전원")
        finally:
            c.close()

        # ---- 급여명세서 ----
        slip = lambda uid: {"userId": uid, "code": f"R{uid}", "head": {"code": f"R{uid}", "name": "가상R", "dept": "가상R팀", "title": "프로", "job": "연구"},  # noqa: E731
                            "pay": [["기본급", 2500000]], "ded": [["소득세", 30000]], "gross": 2500000, "deduct": 30000, "net": 2470000}
        ok(post(c_hr, f"/api/payslips/{ym}", {"payDate": "2094-03-25", "slips": [slip(EMP), slip(EMP2)]}).status_code == 200 and not take(), "명세서를 올려도(공개 전) 아무도 안 받음")
        ok(post(c_hr, f"/api/payslips/{ym}/open").json == {"status": "OPEN"}
           and got("payslip", "3월 급여명세서가 올라왔어요", "/?go=pay.p1", EMP, EMP2, badge=1), "명세서 공개 → 그 달 명세서가 있는 두 사람에게만 1통씩 (같은 팀 팀장 · 구경꾼 · HR 은 안 받음) · 아이콘 숫자 = 안 본 명세서 1")
        ok(post(c_hr, f"/api/payslips/{ym}/open").status_code == 200 and not take(), "이미 공개된 달을 다시 공개하면 0통")
        ok(post(c_hr, f"/api/payslips/{ym}/close").json == {"status": "DRAFT"} and not take(), "내리기는 알리지 않음")
        ok(post(c_hr, f"/api/payslips/{ym}/open").status_code == 200 and got("payslip", "3월 급여명세서가 올라왔어요", "/?go=pay.p1", EMP, EMP2), "내렸다가 다시 공개하면 다시 보냄 (상태가 실제로 바뀔 때마다)")
        ok(post(c_hr, "/api/payslips/2094-04/open").status_code == 404 and not take(), "올린 적 없는 달은 404 · 0통")
        with app.test_request_context():  # 받는 사람 모양: 같은 사람 둘 · 0 · None 은 빼고 한 번씩, 번호를 만드는 함수도 받는다
            push.notify([EMP, EMP, 0, None, EMP2], "payslip", month=3)
            ok(got("payslip", "3월 급여명세서가 올라왔어요", "/?go=pay.p1", EMP, EMP2), "notify: 중복 · 0 · None 번호는 빼고 한 사람에게 한 통")
            push.notify(lambda: [EMP], "payslip", month=3)
            ok(got("payslip", "3월 급여명세서가 올라왔어요", "/?go=pay.p1", EMP), "notify: 받는 사람은 번호 목록 대신 번호를 만드는 함수도 됨 (보낼 수 없는 때는 부르지 않음)")
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:
                push.notify([EMP], "no_such_kind")
            ok(not take() and cm.output == ["WARNING:push:push notify failed: KeyError"], "notify 는 모르는 종류여도 예외를 내지 않고 예외 종류만 로그에 남김")

        # ---- 원천징수영수증 ----
        pdf = base64.b64encode(b"%PDF-1.4\n%fake").decode()
        person = lambda uid: {"userId": uid, "sum": dict.fromkeys(withholding.SUM_KEYS, 0), "pdf": pdf}  # noqa: E731
        ok(post(c_hr, f"/api/withholding/{wy}", {"mode": "all", "reset": True, "total": 2, "people": [person(EMP), person(EMP2)]}).status_code == 200
           and post(c_hr, f"/api/withholding/{wy}", {"mode": "some", "people": [person(EMP)]}).status_code == 200 and not take(), "영수증을 올려도(공개 전) · 공개 전에 사람만 더 올려도 아무도 안 받음")
        ok(post(c_hr, f"/api/withholding/{wy}/open").json == {"status": "OPEN"}
           and got("withholding", "2094년 원천징수영수증이 올라왔어요", "/?go=pay.p3", EMP, EMP2, badge=2), "영수증 공개 → 그 해 영수증이 있는 두 사람에게 1통씩 · 아이콘 숫자 = 명세서 1 + 영수증 1")
        ok(post(c_hr, f"/api/withholding/{wy}/open").status_code == 200 and not take(), "이미 공개된 해를 다시 공개하면 0통")
        ok(post(c_hr, f"/api/withholding/{wy}", {"mode": "some", "people": [person(EMP)]}).json == {"saved": 1, "pending": 0}
           and got("withholding", "2094년 원천징수영수증이 올라왔어요", "/?go=pay.p3", EMP), "공개 중인 해에 사람만 더 올리면 이번에 올린 사람에게만 1통")
        ok(post(c_hr, f"/api/withholding/{wy}", {"mode": "some", "people": [{**person(EMP), "pdf": "not a pdf"}]}).status_code == 400 and not take(), "올리기가 거절되면(400) 0통")
        ok(post(c_hr, f"/api/withholding/{wy}/close").status_code == 200 and not take(), "내리기는 알리지 않음")

        # ---- KPI: 제출 · 돌려보냄 · 다시 제출 · 확정 · 일정 변경 요청 · 수정 요청 · 거절 ----
        ok(post(c_emp, "/api/kpi/me/draft", {"draft": plan("가상E")}).status_code == 200 and not take(), "KPI 임시 저장은 알리지 않음")
        ok(post(c_emp, "/api/kpi/me/submit").status_code == 200 and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", TL, badge=1),
           "KPI 제출 → 바로 위 관리자(팀장)에게만 1통 (대표이사 · HR 은 안 받음) · 아이콘 숫자 = 처리함 1")
        ok(post(c_tl, f"/api/kpi/plans/{EMP}/return", {"note": "가상 사유"}).status_code == 200
           and got("kpi_back", "KPI 계획서가 돌아왔어요", "/?go=perf.e1", EMP), "돌려보냄 → 계획서 주인에게 1통")
        ok(post(c_emp, "/api/kpi/me/submit").status_code == 200 and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", TL), "다시 제출 → 팀장에게 1통")
        ok(post(c_tl, f"/api/kpi/plans/{EMP}/confirm").status_code == 200 and not take(), "확정은 알리지 않음")
        content = c_emp.get("/api/kpi/me", base_url=BASE).json["plan"]["confirmed"]["content"]
        sid = next(s["sid"] for goal in content for it in goal["items"] for s in it["scheds"])
        ok(post(c_emp, "/api/kpi/me/sched-change", {"year": year, "sid": sid, "due": f"{year}-11-30", "reason": "가상 사유"}).status_code == 200
           and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", TL), "일정 변경 요청 → 팀장에게 1통")
        ok(post(c_emp, "/api/kpi/me/sched-change", {"year": year, "sid": sid, "due": f"{year}-11-29", "reason": "가상 사유"}).status_code == 409 and not take(), "거절된 일정 변경 요청(409)은 0통")
        ok(post(c_emp, "/api/kpi/me/request-change", {"reason": "가상 사유"}).status_code == 200
           and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", TL), "수정 요청 → 팀장에게 1통")
        ok(post(c_tl, f"/api/kpi/plans/{EMP}/reject", {"note": "가상 사유"}).status_code == 200 and not take(), "거절(reject)은 알리지 않음 — 돌려보냄(return)과 다르다")

        # ---- KPI 처리자: 관리자가 없으면 HR 전원 · 대표이사가 맡기면 HR 전원 · 맡김을 끄면 대표이사 ----
        x("UPDATE users SET is_active = 0, deactivated_at = ? WHERE id = ?", now_iso, CEO)  # 대표이사가 아무도 없는 동안
        ok(submit(c_dir, "가상D") and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", 1, HR2), "관리자가 없는 사람의 제출 → HR 전원(본인 제외)에게 1통씩 (팀장 · 구경꾼은 안 받음)")
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", CEO)
        ok(post(c_dir, "/api/kpi/me/cancel").status_code == 200 and not take(), "제출 취소는 알리지 않음")
        ok(submit(c_dir, "가상D") and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", CEO), "대표이사 직속의 제출 → 대표이사에게 1통 (HR 은 안 받음)")
        ok(post(c_dir, "/api/kpi/me/cancel").status_code == 200 and post(c_ceo, "/api/kpi/delegation", {"on": True}).status_code == 200 and not take(), "(검사용) 대표이사가 HR 에 맡김")
        ok(submit(c_dir, "가상D") and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", 1, HR2), "맡긴 동안 대표이사 직속의 제출 → HR 전원에게만 1통씩, 대표이사는 0통")
        ok(post(c_dir, "/api/kpi/me/cancel").status_code == 200 and post(c_ceo, "/api/kpi/delegation", {"on": False}).status_code == 200 and submit(c_dir, "가상D")
           and got("kpi_inbox", "팀원 KPI 승인 요청이 왔어요", "/?go=perf.e1", CEO), "맡김을 끄면 다시 대표이사에게")

        # ---- 확인 메모 · 답글 ----
        r = post(c_tl, f"/api/kpi/memos/{EMP}", {"year": year, "text": "가상 메모"})
        mid = r.json["id"]
        ok(r.status_code == 200 and got("kpi_memo", "KPI 확인 메모가 왔어요", "/?go=perf.e1", EMP), "메모 쓰기 → 메모 주인에게 1통")
        ok(post(c_tl, f"/api/kpi/memos/{EMP}", {"year": year, "text": ""}).status_code == 400 and not take(), "거절된 메모(400)는 0통")
        ok(post(c_tl, f"/api/kpi/memo/{mid}/edit", {"text": "가상 고친 메모"}).status_code == 200 and got("kpi_memo", "KPI 확인 메모가 왔어요", "/?go=perf.e1", EMP), "메모 고치기 → 메모 주인에게 1통")
        ok(post(c_emp, f"/api/kpi/memo/{mid}/reply", {"text": "가상 답글"}).status_code == 200
           and got("kpi_reply_to_author", "확인 메모에 새 답글이 달렸어요", "/?go=perf.e4", TL), "주인이 답글 → 메모를 쓴 사람에게 1통 (열 화면 perf.e4)")
        ok(post(c_tl, f"/api/kpi/memo/{mid}/reply", {"text": "가상 답글"}).status_code == 200
           and got("kpi_reply_to_owner", "확인 메모에 새 답글이 달렸어요", "/?go=perf.e1", EMP), "쓴 사람이 답글 → 메모 주인에게 1통 (열 화면 perf.e1)")
        ok(post(c_emp, f"/api/kpi/memo/{mid}/ack").status_code == 200 and post(c_emp2, f"/api/kpi/memo/{mid}/reply", {"text": "가상"}).status_code == 403 and not take(),
           "확인했습니다 · 권한 없는 답글(403)은 0통")

        # ---- 연봉계약서: 발송 · 문의 · 파기 ----
        ctrs = {}
        for uid in (CT1, CT2, CT3):
            body = {"code": f"R-{uid}", "name": f"가상R{uid}", "dept": "가상R팀", "title": "", "birth6": "900315", "base": 2418900, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0, "reason": ""}
            x("INSERT INTO contracts (year, user_id, version, status, starts_on, body, created_at, created_by) VALUES (?, ?, 1, 'DRAFT', ?, ?, ?, 1)",
              cy, uid, contract_doc.round_period(cy)[0], contract._lock(raw_key, contract._aad(cy, uid, 1, "body"), body), now_iso)
            ctrs[uid] = q("SELECT id FROM contracts WHERE year = ? AND user_id = ?", cy, uid)[0][0]
        r = post(c_hr, "/api/contracts/send", {"ids": [ctrs[CT1], ctrs[CT2], ctrs[CT3]], "due": "2026-11-06"})
        ok(r.status_code == 200 and r.json["sent"] == 2 and r.json["skipped"] == [{"id": ctrs[CT3], "why": "연봉계약서 대상 아님"}]
           and got("contract_sign", "서명할 연봉계약서가 있어요", "/?go=pay.p2", CT1, CT2), "계약서 발송 → 이번에 서명 대기가 된 두 사람에게만 1통씩 (건너뛴 사람은 0통)")
        ok(post(c_hr, "/api/contracts/send", {"ids": [ctrs[CT1]], "due": "2026-11-06"}).json["sent"] == 0 and not take(), "이미 보낸 계약서를 다시 발송해도 0통")
        ok(post(c_ct1, f"/api/contracts/{ctrs[CT1]}/ask", {"text": "가상 문의"}).status_code == 200
           and got("contract_ask", "답변할 연봉계약서 문의가 있어요", "/?go=pay.p2", 1, HR2), "문의 → HR 전원(비활성 HR 은 빼고)에게 1통씩, 문의한 직원 · 구경꾼은 0통")
        ok(post(c_ct1, f"/api/contracts/{ctrs[CT1]}/ask", {"text": "가상 문의"}).status_code == 409 and not take(), "거절된 문의(409)는 0통")
        ok(post(c_hr, f"/api/contracts/{ctrs[CT1]}/cancel", {"reason": "가상 사유", "then": "draft"}).status_code == 200 and not take(), "파기 → 발송 전으로 돌리면(draft) 0통")
        r = post(c_hr, f"/api/contracts/{ctrs[CT2]}/cancel", {"reason": "가상 사유", "then": "resend"})
        ok(r.status_code == 200 and got("contract_sign", "서명할 연봉계약서가 있어요", "/?go=pay.p2", CT2), "파기 뒤 같은 내용으로 다시 보내면(resend) 그 직원에게 1통")
        ok(post(c_hr, f"/api/contracts/{r.json['id']}/cancel", {"reason": "가상 사유", "then": "none"}).status_code == 200 and not take(), "파기하고 끝내면(none) 0통")

        # HR 이 자기 연봉계약서에 문의하면 본인 휴대폰은 울리지 않고 다른 HR 에게만 간다
        x("UPDATE users SET has_contract = 1 WHERE id = ?", HR2)
        x("INSERT INTO contracts (year, user_id, version, status, starts_on, body, created_at, created_by) VALUES (?, ?, 1, 'DRAFT', ?, ?, ?, 1)",
          cy, HR2, contract_doc.round_period(cy)[0], contract._lock(raw_key, contract._aad(cy, HR2, 1, "body"),
          {"code": f"R-{HR2}", "name": "가상R", "dept": "가상R본부", "title": "", "birth6": "900315", "base": 2418900, "ability": 1211000, "pos": 300000, "site": 0, "qual": 0, "reason": ""}), now_iso)
        hr2_cid = q("SELECT id FROM contracts WHERE year = ? AND user_id = ?", cy, HR2)[0][0]
        ok(post(c_hr, "/api/contracts/send", {"ids": [hr2_cid], "due": "2026-11-06"}).json["sent"] == 1
           and got("contract_sign", "서명할 연봉계약서가 있어요", "/?go=pay.p2", HR2), "(검사용) HR 본인의 계약서를 서명 대기로 — 그 HR 에게 서명 요청 1통")
        ok(post(client(HR2), f"/api/contracts/{hr2_cid}/ask", {"text": "가상 문의"}).status_code == 200
           and got("contract_ask", "답변할 연봉계약서 문의가 있어요", "/?go=pay.p2", 1), "HR 이 자기 계약서에 문의 → 다른 HR(1)에게만 1통, 문의한 HR 본인(HR2)은 0통")

        # ---- 푸시 서버가 실패해도 원래 일은 그대로: 500 · 410 · 예외 · 숫자를 못 셈 ----
        extra = put(EMP2)
        codes.update({dev[EMP]: 500, dev[EMP2]: 410})
        boom.add(extra)
        ok(post(c_hr, f"/api/payslips/{ym}/close").status_code == 200 and not take(), "(검사용) 내림")
        with unittest.TestCase().assertLogs("push", "WARNING") as cm:
            r = post(c_hr, f"/api/payslips/{ym}/open")
        out = "\n".join(cm.output)
        ok(r.status_code == 200 and r.json == {"status": "OPEN"} and state() == [("OPEN",)] and len(take()) == 3
           and sorted(cm.output) == ["WARNING:push:event push failed: RuntimeError", "WARNING:push:event push rejected: status 500"]
           and "example.invalid" not in out and "가상" not in out, "푸시 서버가 500 · 예외여도 공개는 200 으로 끝나고 세 기기 모두 시도 · 로그에는 상태 코드 · 예외 종류만")
        ok(subs(EMP) == [dev[EMP]] and subs(EMP2) == [extra], "500 · 예외는 구독 그대로 · 410 을 돌려준 구독만 삭제")
        dev[EMP2] = extra
        codes.clear()
        boom.clear()

        # 푸시 서버가 구독이 없어졌다(410)고 했는데 그 줄을 지우다 DB 가 실패해도(forget 이 던짐) 나머지 사람에게는 가고 원래 일은 200 — 로그에는 예외 종류만
        def broken_forget(conn, hashes):
            raise RuntimeError("검사용 고장")

        push.forget, codes[dev[EMP]] = broken_forget, 410  # EMP 가 먼저 보내지므로 그 뒤의 EMP2 가 가는지 본다
        try:
            ok(post(c_hr, f"/api/payslips/{ym}/close").status_code == 200 and not take(), "(검사용) 내림")
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:
                r = post(c_hr, f"/api/payslips/{ym}/open")
            rows = take()
            ok(r.status_code == 200 and r.json == {"status": "OPEN"} and state() == [("OPEN",)] and [u for u, _, _ in rows] == [EMP, EMP2]
               and cm.output == ["WARNING:push:event push forget failed: RuntimeError"] and subs(EMP) == [dev[EMP]] and "example.invalid" not in "".join(cm.output),
               "410 이어도 구독 줄 지우기가 실패하면 그 사람 것은 그대로 두고 다음 사람에게는 감 · 공개는 200 · 로그에는 예외 종류만")
        finally:
            push.forget = real_forget
            codes.clear()
        push.pending = lambda flask_app, uid: (_ for _ in ()).throw(RuntimeError("검사용 고장"))
        try:
            ok(post(c_hr, f"/api/payslips/{ym}/close").status_code == 200 and not take(), "(검사용) 내림")
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:
                r = post(c_hr, f"/api/payslips/{ym}/open")
            rows = take()
            ok(r.status_code == 200 and [u for u, _, _ in rows] == [EMP, EMP2] and all("badge" not in p and p["title"] == "3월 급여명세서가 올라왔어요" for _, p, _ in rows)
               and cm.output == ["WARNING:push:event pending failed: RuntimeError"] * 2, "처리할 일 개수를 못 세도 알림은 감 (아이콘 숫자 없이) · 로그에는 예외 종류만")
        finally:
            push.pending = real_pending

        # ---- 스레드로 보내기 (운영 모양): 요청은 바로 끝나고 알림은 뒤따라 간다 ----
        push.SYNC = False
        try:
            ok(post(c_hr, f"/api/payslips/{ym}/close").status_code == 200, "(검사용) 내림")
            r = post(c_hr, f"/api/payslips/{ym}/open")
            for th in [t for t in threading.enumerate() if t.name == "push-deliver"]:
                th.join(15)
            ok(r.status_code == 200 and set(threads[-2:]) == {"push-deliver"} and got("payslip", "3월 급여명세서가 올라왔어요", "/?go=pay.p1", EMP, EMP2),
               "SYNC 가 꺼지면 데몬 스레드(push-deliver)에서 보냄 — 요청은 200 으로 끝나고 두 사람에게 1통씩")
        finally:
            push.SYNC = True

        # ---- 안 보내는 때: 키 없음 · 근무시간 밖 (원래 일은 성공) ----
        os.environ.pop("HR_PUSH_PRIVATE")
        ok(post(c_hr, f"/api/payslips/{ym}/close").status_code == 200 and post(c_hr, f"/api/payslips/{ym}/open").json == {"status": "OPEN"} and state() == [("OPEN",)] and not take(),
           "알림 키가 없으면 아무것도 안 보내고 공개는 그대로 성공")
        os.environ.update(HR_PUSH_PRIVATE=priv)
        for when, why in ((T0.replace(hour=18), "금요일 18:00"), (T0.replace(day=24), "토요일 10:00"), (T0.replace(day=25, hour=12), "일요일 12:00")):
            clock[0] = when
            c = client(1)  # 시각이 바뀌었으니 새로 로그인 (PC 세션은 4시간 안 쓰면 끝남)
            ok(post(c, f"/api/payslips/{ym}/close").status_code == 200 and post(c, f"/api/payslips/{ym}/open").json == {"status": "OPEN"} and state() == [("OPEN",)] and not take(),
               f"{why} 에는 안 보내고 공개는 성공")
        clock[0] = T0.replace(hour=17, minute=59)
        c = client(1)
        ok(post(c, f"/api/payslips/{ym}/close").status_code == 200 and post(c, f"/api/payslips/{ym}/open").status_code == 200
           and got("payslip", "3월 급여명세서가 올라왔어요", "/?go=pay.p1", EMP, EMP2), "금요일 17:59 에는 보냄 (18:00 부터 안 보냄)")

        ok(len(seen_all) > 20 and threads.count("push-deliver") == 2 and all("가상" not in json.dumps(p, ensure_ascii=False) for p in seen_all),
           "보낸 알림(스무 통 넘게) 제목 · 내용 어디에도 사람 이름(가상…)이 없음 — 스레드로 보낸 것은 스레드 검사의 두 통뿐")
    finally:
        push.SYNC, push.SEND, push.pending, push.forget = real_sync, real_send, real_pending, real_forget
        auth._now = real_now
        for k, v in env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        for h in {r[0] for r in q("SELECT token_hash FROM sessions")} - had:  # 이 검사의 로그인 · 가상 세션 (구독 줄도 함께)
            x("DELETE FROM sessions WHERE token_hash = ?", h)
        x(f"DELETE FROM kpi_memos WHERE user_id IN ({mine})")  # 답글도 함께
        x(f"DELETE FROM kpi_sched_changes WHERE user_id IN ({mine})")
        x(f"DELETE FROM kpi_plans WHERE user_id IN ({mine}) AND NOT EXISTS (SELECT 1 FROM kpi_versions v WHERE v.user_id = kpi_plans.user_id AND v.year = kpi_plans.year)")  # 확정된 계획서는 지울 수 없다
        x(f"DELETE FROM ceo_delegation WHERE ceo_user_id IN ({mine})")
        x("DELETE FROM payslip_months WHERE ym = ?", ym)  # 명세서도 함께
        x("DELETE FROM withholding_years WHERE year = ?", wy)  # 영수증도 함께
        x(f"DELETE FROM contracts WHERE status = 'DRAFT' AND user_id IN ({mine})")
        x(f"UPDATE contracts SET status = 'CANCELLED', cancelled_at = ?, cancelled_by = 1, cancel_reason = '검사 정리' WHERE user_id IN ({mine}) AND status = 'SENT'", auth._iso(real_now()))  # 발송한 줄은 지울 수 없어 파기로
        if kyear:
            x("UPDATE kpi_years SET edit_from = ?, edit_to = ?, eval_start = ? WHERE year = ?", *kyear[0], year)
        else:
            x("DELETE FROM kpi_years WHERE year = ?", year)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)
        x(f"UPDATE users SET is_active = 0, deactivated_at = ?, portal_enabled = 0 WHERE id IN ({mine})", auth._iso(real_now()))  # 감사로그가 가리켜 지울 수 없는 가상 사람은 비활성으로
    ok(not q(f"SELECT 1 FROM push_subscriptions WHERE user_id IN ({mine})") and not q("SELECT 1 FROM push_subscriptions WHERE endpoint LIKE 'https://push.example.invalid/ev-%'")
       and not q(f"SELECT 1 FROM sessions WHERE user_id IN ({mine})") and not q(f"SELECT 1 FROM kpi_memos WHERE user_id IN ({mine})")
       and not q(f"SELECT 1 FROM kpi_sched_changes WHERE user_id IN ({mine})") and not q(f"SELECT 1 FROM ceo_delegation WHERE ceo_user_id IN ({mine})")
       and not q("SELECT 1 FROM payslip_months WHERE ym = ?", ym) and not q("SELECT 1 FROM withholding_years WHERE year = ?", wy)
       and not q(f"SELECT 1 FROM contracts WHERE status IN ('DRAFT', 'SENT') AND user_id IN ({mine})")
       and q("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", year) == kyear
       and all(q("SELECT is_active FROM users WHERE id = ?", uid) == [(1,)] for uid in other_ceos)
       and all(os.environ.get(k) == v for k, v in env.items()) and push.SEND is real_send and push.SYNC is real_sync and push.pending is real_pending and push.forget is real_forget and auth._now is real_now,
       "끝나면 구독 · 세션 · 메모 · 위임 · 명세서 · 영수증 · 계약서(파기)를 정리하고 기간 · 대표이사 · 환경 값 · 보내기 함수 · 시각을 되돌림")


def check_push_deadlines(hr) -> None:
    """휴대폰 9시 마감 알림 (2026-10-26): 처리할 일 묶음과 따로 업무 마감(서버에 저장된 나의 업무) · KPI 마감(실제로 확정한 계획서의 비정기 일정 · 지난달 한 줄)이 한 통씩.
    끝낸 업무 · 결과를 적은 일정은 빠지고, 금요일은 주말(· 월요일) 몫까지, 지난달 한 줄은 7일(토 · 일이면 그 앞 금요일)에. 누르면 나의 업무, 아이콘 숫자는 처리할 일 합.
    알림에는 개수만 — 업무 · 일정 제목(가상…)은 없다. 마감을 못 세도 묶음은, 묶음을 못 세도 마감 알림은 (숫자 없이) 간다.
    시각은 auth._now 를 고정해 옮기고(진행 기록의 '오늘'도 같이) 보내기 함수는 가짜. 가상 사람 · 부서(63xx) · 가상 구독 주소(example.invalid).
    확정본 · 감사로그 때문에 지울 수 없는 것(가상 사람 · 확정된 계획서)은 비활성으로 두고, 그 밖의 자료와 환경 값 · 시각 · 보내기 함수는 되돌린다."""
    import unittest

    import kpi
    import push

    print("휴대폰 9시 마감 알림")
    TL, EMP, EMP2, IDLE = cast = tuple(range(6301, 6305))  # 팀장 · KPI 쓰는 직원 · 나의 업무만 쓰는 직원 · 일 없음
    mine = ",".join(map(str, cast))
    utc, KST, year = timezone.utc, auth.KST, 2026
    clock = [datetime(2026, 10, 5, 10, 0, tzinfo=KST)]  # 10월 첫 주에 확정 — 10월부터 월 한 줄이 해당 있다
    MON, TUE, FRI, D7 = (datetime(2026, m, d, 9, 0, tzinfo=KST) for m, d in ((11, 2), (11, 3), (11, 6), (12, 7)))  # 11/6 금요일은 7일이 토요일
    days = [d.date().isoformat() for d in (MON, TUE, FRI, D7)]
    env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    real_now, real_send, real_pending, real_deadlines = auth._now, push.SEND, push.pending, push.deadlines
    kyear = q("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", year)
    had = {r[0] for r in q("SELECT token_hash FROM sessions")}  # 시작 때 있던 세션 — 끝나면 이 검사가 만든 세션(로그인 · 가상)만 지운다
    calls: list[tuple[str, dict, int]] = []
    owner: dict[str, int] = {}  # 구독 주소 → 받는 사람
    phones: list[str] = []  # 가상 휴대폰 세션 번호

    def fake_send(info, payload, ks, ttl):
        calls.append((info["endpoint"], payload, ttl))
        return 201

    def client(uid: int):  # 지금(고정한) 시각의 PC 로그인
        c = app.test_client()
        login(c, new_token(f"HW-{uid}"))
        return c

    post = lambda c, p, body=None: c.post(p, base_url=BASE, headers=ORIGIN, json=body or {})  # noqa: E731

    def run(when) -> tuple[int, dict[int, list[dict]]]:
        """그날 9시 묶음을 돌린다 (가상 휴대폰은 그 시각에 막 쓴 것으로) — (보낸 사람 수, {받은 사람: [보낸 순서대로 내용]}). 모든 통의 TTL 은 9시간."""
        clock[0] = when
        t = when.astimezone(utc)
        x(f"UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE token_hash IN ({','.join('?' * len(phones))})", auth._iso(t), auth._iso(t + timedelta(days=30)), *phones)
        calls.clear()
        n, got = push.morning(app, when), {}
        for ep, p, ttl in calls:
            got.setdefault(owner[ep], []).append(p if ttl == push.TTL_MORNING else {"ttl": ttl})
        return n, got

    def msg(tag: str, title: str, body: str, badge: int | None) -> dict:
        return {"title": title, "body": body, "url": "/" if tag == "morning" else "/?go=ws.w1", "tag": tag, **({} if badge is None else {"badge": badge})}

    seen: list[dict] = []
    try:
        auth._now = lambda: clock[0].astimezone(utc)
        push.SEND = fake_send
        now_iso = auth._iso(auth._now())
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (6301, 'Q81', '가상S본부', 'DIVISION', NULL)")
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (6302, 'Q82', '가상S팀', 'TEAM', 6301)")
        for uid, kpi_on in ((TL, 1), (EMP, 1), (EMP2, 0), (IDLE, 0)):
            x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, writes_kpi) VALUES (?, ?, ?, 6302, ?, 1, 1, ?)",
              uid, f"가상S{uid}", f"s{uid}@example.invalid", f"HW-{uid}", kpi_on)
        x("INSERT INTO user_roles (user_id, role) VALUES (?, 'TEAM_LEADER')", TL)
        x("INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at, updated_by) VALUES (?, ?, ?, NULL, ?, 1)"
          " ON CONFLICT (year) DO UPDATE SET edit_from = excluded.edit_from, edit_to = excluded.edit_to, eval_start = NULL", year, f"{year}-01-01", f"{year}-12-31", now_iso)
        for uid in (EMP, EMP2, IDLE):  # 휴대폰 하나씩 (팀장은 없음 — 월간 확인 묶음이 섞이지 않게)
            h, ep = f"{len(phones):02d}" + "6" * 62, f"https://push.example.invalid/dl-{uid}"
            x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')",
              h, uid, now_iso, now_iso, auth._iso(auth._now() + timedelta(days=30)))
            x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, now_iso)
            phones.append(h)
            owner[ep] = uid

        # ---- 10/5 직원이 KPI 를 쓰고 팀장이 확정 (정기 2 · 비정기 일정 넷) · 다른 직원은 나의 업무를 저장 ----
        item = lambda name, w, kind, **more: {  # noqa: E731
            "action": name, "formula": "가상 측정식", "weight": w, "owner": "가상", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
            "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}
        scheds = [{"due": "2026-10-30", "name": "가상 초안"}, {"due": "2026-11-03", "name": "가상 보고"}, {"due": "2026-11-07", "name": "가상 행사"},
                  {"due": "2026-11-09", "name": "가상 점검"}]  # 금 · 화 · 토 · 월
        draft = [{"team": "가상S 팀목표", "personal": "가상S 개인목표", "items": [item("가상S 정기 1", 40, "reg"), item("가상S 정기 2", 30, "reg"),
                                                                       item("가상S 비정기", 30, "irr", scheds=scheds)]}]
        c_emp, c_tl, c_emp2 = client(EMP), client(TL), client(EMP2)
        ok(post(c_emp, "/api/kpi/me/draft", {"draft": draft}).status_code == 200 and post(c_emp, "/api/kpi/me/submit").status_code == 200
           and post(c_tl, f"/api/kpi/plans/{EMP}/confirm").status_code == 200, "(검사용) 직원 KPI 를 10월 5일에 제출 · 팀장이 확정")
        content = c_emp.get("/api/kpi/me", base_url=BASE).json["plan"]["confirmed"]["content"]
        sid = {s["name"]: s["sid"] for g in content for it in g["items"] for s in it["scheds"]}
        rid = next(it["rid"] for g in content for it in g["items"] if it["kind"] == "reg")
        tasks = [{"id": 1, "title": "가상 보고 정리", "due": "2026-11-02"}, {"id": 2, "title": "가상 끝낸 일", "due": "2026-11-02", "state": "완료", "doneAt": "2026-11-02"},
                 {"id": 3, "title": "가상 진행 중", "due": "2026-10-30", "state": "진행 중"}, {"id": 4, "title": "가상 날짜 없음", "due": ""},
                 {"id": 5, "title": "가상 주말 행사", "due": "2026-11-07"}]
        ok(post(c_emp2, "/api/workspace/tasks", {"data": tasks, "version": 0}).json == {"version": 1}, "(검사용) 다른 직원이 나의 업무 다섯 줄을 저장")
        pub, priv = push.keygen()  # 알림 키는 준비가 끝난 뒤에 — 위의 제출이 승인 요청 알림을 보내지 않게
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        bundle = lambda n, body: msg("morning", f"처리할 일이 {n}건 있어요", body, n)  # noqa: E731

        # ---- 11/2 월: 내일 마감 · 지난 일정 / 오늘 마감 업무 · 지난 업무 ----
        n, got = run(MON)
        seen.extend(p for ps in got.values() for p in ps)
        ok(n == 2 and set(got) == {EMP, EMP2}, "월요일: 셀 것이 있는 두 사람에게만 (일 없는 직원은 안 받음)")
        ok(got[EMP] == [bundle(2, "KPI 지난달 한 줄 쓰기 2건"),
                        msg("morning-kpi", "내일 KPI 업무 마감일이에요", "1건 · 마감 지난 일정 1건 · PC에서 결과를 적어 주세요", 2)],
           "KPI 쓰는 직원: 처리할 일 묶음 다음에 KPI 마감 한 통 — 내일(11/3) 마감 1 · 결과 안 적은 지난 일정(10/30) 1, 아이콘 숫자는 묶음과 같은 2 · 나의 업무를 저장한 적 없으니 업무 마감은 없음")
        ok(got[EMP2] == [msg("morning-tasks", "오늘 마감인 업무가 1건 있어요", "마감 지난 업무 1건 · 나의 업무에서 확인해 주세요", 0)],
           "나의 업무만 쓰는 직원: 업무 마감 한 통 — 오늘 마감 1(끝낸 일 · 날짜 없음은 빠짐) · 진행 중인 지난 업무 1, 처리할 일이 없으니 묶음 없이 아이콘 숫자 0")
        ok(run(MON)[0] == 0 and not calls, "같은 날 다시 돌려도 0 — 마감 알림도 한 번만")
        ok(post(client(EMP), "/api/kpi/me/result", {"year": year, "sid": sid["가상 초안"], "text": "가상 결과"}).status_code == 200, "(검사용) 직원이 지난 일정의 결과를 적음")

        # ---- 11/3 화: 오늘 마감 ----
        n, got = run(TUE)
        seen.extend(p for ps in got.values() for p in ps)
        ok(got[EMP][1:] == [msg("morning-kpi", "오늘 KPI 업무 마감일이에요", "1건 · PC에서 결과를 적어 주세요", 2)], "화요일: 오늘(11/3) 마감 1 — 결과를 적은 지난 일정은 빠짐")
        ok(got[EMP2] == [msg("morning-tasks", "마감 지난 업무가 2건 있어요", "나의 업무에서 확인해 주세요", 0)], "어제 마감인 업무도 끝내지 않으면 지난 업무 (2)")
        tasks[0] = {**tasks[0], "state": "완료", "doneAt": "2026-11-03"}
        ok(post(client(EMP2), "/api/workspace/tasks", {"data": tasks, "version": 1}).json == {"version": 2}, "(검사용) 업무 하나를 끝냄 (휴대폰 · PC 어디서든 같은 서버 목록)")
        ok(post(client(EMP), "/api/kpi/me/month", {"year": year, "rid": rid, "month": 10, "text": "가상 10월 한 줄"}).status_code == 200, "(검사용) 직원이 정기 하나의 10월 한 줄을 씀")

        # ---- 11/6 금 (7일이 토요일): 주말 · 월요일 몫과 지난달 한 줄 마감을 미리 ----
        n, got = run(FRI)
        seen.extend(p for ps in got.values() for p in ps)
        ok(got[EMP] == [bundle(1, "KPI 지난달 한 줄 쓰기"),
                        msg("morning-kpi", "7일까지 KPI 지난달 한 줄을 써 주세요", "1건 · 주말·월요일 마감 2건 · 마감 지난 일정 1건 · PC에서 결과를 적어 주세요", 1)],
           "금요일: 7일(토) 마감인 지난달 한 줄 1이 제목 · 토요일(11/7) · 월요일(11/9) 마감 2 · 결과 안 적은 화요일 일정 1")
        ok(got[EMP2] == [msg("morning-tasks", "주말 마감인 업무가 1건 있어요", "마감 지난 업무 1건 · 나의 업무에서 확인해 주세요", 0)],
           "금요일: 토요일 마감 업무 1 · 끝낸 업무는 빠지고 지난 업무 1")

        # ---- 12/7 월: 오늘까지 지난달 한 줄 (KPI 쓰는 직원도 나의 업무 하나를 저장 — 세 통) ----
        clock[0] = D7
        ok(post(client(EMP), "/api/workspace/tasks", {"data": [{"id": 1, "title": "가상 연말 정리", "due": "2026-12-07"}], "version": 0}).json == {"version": 1},
           "(검사용) KPI 쓰는 직원이 오늘 마감 업무 하나를 저장")
        n, got = run(D7)
        seen.extend(p for ps in got.values() for p in ps)
        ok(got[EMP] == [bundle(2, "KPI 지난달 한 줄 쓰기 2건"), msg("morning-tasks", "오늘 마감인 업무가 1건 있어요", "나의 업무에서 확인해 주세요", 2),
                        msg("morning-kpi", "오늘까지 KPI 지난달 한 줄을 써 주세요", "2건 · 마감 지난 일정 3건 · PC에서 결과를 적어 주세요", 2)],
           "12월 7일: 묶음 → 업무 마감 → KPI 마감 세 통, 아이콘 숫자는 모두 처리할 일 합 2 · 11월 한 줄 2건이 오늘까지 · 결과 안 적은 지난 일정 3 (묶음에도 그대로 — 인사담당자 결정)")
        ok(got[EMP2] == [msg("morning-tasks", "마감 지난 업무가 2건 있어요", "나의 업무에서 확인해 주세요", 0)], "주말이 지난 업무도 지난 업무로")

        # ---- 한쪽을 못 세도 다른 쪽은 간다 ----
        def boom_deadlines(flask_app, uid, now):
            if uid == EMP:
                raise RuntimeError("검사용 고장")
            return real_deadlines(flask_app, uid, now)

        def boom_pending(flask_app, uid):
            if uid == EMP2:
                raise RuntimeError("검사용 고장")
            return real_pending(flask_app, uid)

        x("DELETE FROM push_morning WHERE day = ?", days[-1])
        push.deadlines, push.pending = boom_deadlines, boom_pending
        try:
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:
                n, got = run(D7)
        finally:
            push.deadlines, push.pending = real_deadlines, real_pending
        seen.extend(p for ps in got.values() for p in ps)
        ok(n == 2 and got[EMP] == [bundle(2, "KPI 지난달 한 줄 쓰기 2건")]
           and got[EMP2] == [msg("morning-tasks", "마감 지난 업무가 2건 있어요", "나의 업무에서 확인해 주세요", None)]
           and sorted(cm.output) == ["WARNING:push:morning deadlines failed: RuntimeError", "WARNING:push:morning pending failed: RuntimeError"],
           "마감을 못 센 사람에게도 묶음은 · 처리할 일을 못 센 사람에게도 마감 알림은 아이콘 숫자 없이 감 — 로그에는 예외 종류만")
        real_prog = kpi.progress_json
        x("DELETE FROM push_morning WHERE day = ?", days[-1])
        kpi.progress_json = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("검사용 고장"))  # 진행 기록이 터짐 — 처리할 일 묶음도 같이 못 셈
        try:
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:
                n, got = run(D7)
        finally:
            kpi.progress_json = real_prog
        seen.extend(p for ps in got.values() for p in ps)
        ok(got[EMP] == [msg("morning-tasks", "오늘 마감인 업무가 1건 있어요", "나의 업무에서 확인해 주세요", None)]
           and "WARNING:push:morning kpi deadlines failed: RuntimeError" in cm.output,
           "KPI 진행 기록이 터진 사람에게도 업무 마감은 감 (묶음 · KPI 마감 없이, 아이콘 숫자 없이) — 로그에는 예외 종류만")
        ok(len(seen) == 17 and all("가상" not in json.dumps(p, ensure_ascii=False) for p in seen),
           "보낸 알림(열일곱 통) 어디에도 업무 · 일정 · 사람 이름(가상…)이 없음 — 개수만")
    finally:
        auth._now, push.SEND, push.pending, push.deadlines = real_now, real_send, real_pending, real_deadlines
        for k, v in env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        x(f"DELETE FROM push_morning WHERE day IN ({','.join('?' * len(days))})", *days)
        for h in {r[0] for r in q("SELECT token_hash FROM sessions")} - had:  # 이 검사의 로그인 · 가상 세션 (구독 줄도 함께)
            x("DELETE FROM sessions WHERE token_hash = ?", h)
        x(f"DELETE FROM workspace_data WHERE user_id IN ({mine})")
        x(f"DELETE FROM kpi_results WHERE user_id IN ({mine})")
        x(f"DELETE FROM kpi_month_notes WHERE user_id IN ({mine})")
        x(f"DELETE FROM kpi_plans WHERE user_id IN ({mine}) AND NOT EXISTS (SELECT 1 FROM kpi_versions v WHERE v.user_id = kpi_plans.user_id AND v.year = kpi_plans.year)")  # 확정된 계획서는 지울 수 없다
        if kyear:
            x("UPDATE kpi_years SET edit_from = ?, edit_to = ?, eval_start = ? WHERE year = ?", *kyear[0], year)
        else:
            x("DELETE FROM kpi_years WHERE year = ?", year)
        x(f"UPDATE users SET is_active = 0, deactivated_at = ?, portal_enabled = 0 WHERE id IN ({mine})", auth._iso(real_now()))  # 감사로그가 가리켜 지울 수 없는 가상 사람은 비활성으로
    ok(not q(f"SELECT 1 FROM push_subscriptions WHERE user_id IN ({mine})") and not q(f"SELECT 1 FROM sessions WHERE user_id IN ({mine})")
       and not q(f"SELECT 1 FROM push_morning WHERE day IN ({','.join('?' * len(days))})", *days) and not q(f"SELECT 1 FROM workspace_data WHERE user_id IN ({mine})")
       and q("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", year) == kyear
       and all(os.environ.get(k) == v for k, v in env.items()) and push.SEND is real_send and push.pending is real_pending and push.deadlines is real_deadlines
       and auth._now is real_now, "끝나면 구독 · 세션 · 아침 기록 · 나의 업무를 정리하고 기간 · 환경 값 · 보내기 함수 · 시각을 되돌림")


@contextmanager
def pay_gate_off():
    """급여·계약 비밀번호 검문(paypin.gate)을 잠시 끈다 — 잠금 이전 동작을 보는 검사(명세서 · 영수증 · 계약서 · 알림)용. 끝나면 되돌린다."""
    import paypin
    real = paypin.gate
    paypin.gate = lambda: None
    try:
        yield
    finally:
        paypin.gate = real


def check_pay_ask(hr) -> None:
    """급여명세서 [HR에 문의] 받는 사람 (2026-10-26): HR 만 정함 · 활성 HR 만 고름 · null 이면 지움 · /api/pay/lock 의 askHr = 정한 1명(아니면 HR 전원) ·
    고른 사람이 HR 해제 · 비활성이 되면 줄이 지워져 HR 전원으로(다시 켜도 돌아오지 않음) · 바뀔 때만 감사로그. 가상971(HR)을 만들고 끝나면 지운다 — 열쇠는 검사가 만든 가짜."""
    import base64

    print("급여 문의 받는 사람")
    emp = app.test_client()
    login(emp, new_token("emp"))
    post = lambda c, body, headers=ORIGIN: c.post("/api/payslips/ask-to", base_url=BASE, headers=headers, json=body)  # noqa: E731
    lock = lambda: emp.get("/api/pay/lock", base_url=BASE).json  # noqa: E731
    ask_to = lambda: hr.get("/api/payslips/months", base_url=BASE).json["askTo"]  # noqa: E731
    audit = lambda: q("SELECT actor_user_id, entity_type, entity_id, before_data, after_data FROM audit_logs WHERE action = 'PAY_ASK_TO_SET' ORDER BY id")  # noqa: E731
    me971 = {"mailNo": "9711", "email": "u971@example.invalid"}
    had_key = os.environ.get("HR_PAY_KEY")
    x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, hiworks_mail_no, portal_enabled, is_active)"
      " VALUES (971, '가상971', 'u971@example.invalid', 2, 'HW-971', '9711', 1, 1)")
    x("INSERT INTO user_roles (user_id, role) VALUES (971, 'HR_ADMIN')")
    try:
        os.environ["HR_PAY_KEY"] = base64.b64encode(os.urandom(32)).decode()  # 열쇠는 글자로 적지 않고 여기서 만든다
        all_hr = lock()["hr"]
        ok(len(all_hr) >= 2 and me971 in all_hr and lock()["askHr"] == all_hr and ask_to() is None, "정하기 전: askHr = 활성 HR 전원 (hr 과 같음) · askTo null")
        ok(post(emp, {"userId": 971}).status_code == 403 and post(hr, {"userId": 971}, headers={}).status_code == 403
           and post(app.test_client(), {"userId": 971}).status_code == 401 and not q("SELECT 1 FROM pay_ask_to"), "직원 403 · Origin 없으면 403 · 로그인 없이 401 (줄 없음)")
        for body in ({}, {"userId": "971"}, {"userId": True}, {"userId": 971.0}, {"userId": 0}, {"userId": -1}, {"userId": 2**31}, [971]):
            ok(post(hr, body).status_code == 400, f"모양이 틀리면 400 ({body!r})")
        for uid, why in ((3, "HR 이 아닌 직원(가상3)"), (999, "없는 사람")):
            r = post(hr, {"userId": uid})
            ok(r.status_code == 400 and r.json == {"error": "HR 권한이 있는 직원만 고를 수 있어요."}, f"{why} → 400")
        ok(not q("SELECT 1 FROM pay_ask_to") and not audit(), "거절된 것은 줄 · 감사로그 없음")

        r = post(hr, {"userId": 971})
        ok(r.status_code == 200 and r.json == {"askTo": 971} and ask_to() == 971 and lock()["askHr"] == [me971] and lock()["hr"] == all_hr,
           "가상971 을 정함 → askTo 971 · askHr 는 가상971 한 명 · hr(비밀번호 초기화 요청)은 그대로 HR 전원")
        ok(audit() == [(1, "user", 971, '{"userId": null}', '{"userId": 971}')], "감사로그 PAY_ASK_TO_SET — 앞뒤 직원 번호만")
        ok(post(hr, {"userId": 971}).json == {"askTo": 971} and len(audit()) == 1, "같은 사람을 또 정하면 그대로 (감사로그 더하지 않음)")

        r = post(hr, {"userId": None})
        ok(r.json == {"askTo": None} and not q("SELECT 1 FROM pay_ask_to") and ask_to() is None and lock()["askHr"] == lock()["hr"]
           and audit()[-1] == (1, "user", 971, '{"userId": 971}', '{"userId": null}'), "null → 줄을 지움 · HR 전원 · 감사로그(빠진 사람 번호)")
        ok(post(hr, {"userId": None}).json == {"askTo": None} and len(audit()) == 2, "이미 비어 있으면 그대로 (감사로그 더하지 않음)")

        ok(post(hr, {"userId": 971}).json == {"askTo": 971}, "(검사용) 가상971 을 다시 정함")
        x("DELETE FROM user_roles WHERE user_id = 971 AND role = 'HR_ADMIN'")  # 구성원 설정 저장(auth._save_members)이 HR 권한을 뺄 때와 같은 명령 — 이 DB 에는 앞 검사들의 가상 인물이 있어 저장 API 는 명단 전체 검사에서 걸린다
        ok(not q("SELECT 1 FROM pay_ask_to") and ask_to() is None and lock()["askHr"] == lock()["hr"] and me971 not in lock()["hr"],
           "HR 권한을 빼면 받는 사람 줄이 지워짐 → HR 전원")
        x("INSERT INTO user_roles (user_id, role) VALUES (971, 'HR_ADMIN')")
        ok(ask_to() is None and lock()["askHr"] == lock()["hr"] and me971 in lock()["hr"], "다시 HR 이 돼도 받는 사람으로 저절로 돌아오지 않음")
        ok(post(hr, {"userId": 971}).json == {"askTo": 971}, "(검사용) 다시 정함")
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = 971")  # 비활성화(구성원 설정 · 명단 동기화)와 같은 칸 바꾸기
        ok(not q("SELECT 1 FROM pay_ask_to") and ask_to() is None and lock()["askHr"] == lock()["hr"] and me971 not in lock()["hr"],
           "비활성이 되면 받는 사람 줄이 지워짐 → 활성 HR 전원")
        ok(post(hr, {"userId": 971}).status_code == 400, "비활성 HR 은 고를 수 없음 → 400")
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = 971")
        ok(ask_to() is None and me971 in lock()["hr"], "다시 켜도 받는 사람으로 저절로 돌아오지 않음")
        ok(len(audit()) == 4, "감사로그 PAY_ASK_TO_SET 은 HR 이 정하거나 지운 만큼만 (트리거로 지워진 것은 구성원 변경 기록에)")
    finally:
        os.environ.pop("HR_PAY_KEY", None)
        if had_key is not None:
            os.environ["HR_PAY_KEY"] = had_key
        x("DELETE FROM pay_ask_to")
        x("DELETE FROM user_roles WHERE user_id = 971")
        x("DELETE FROM users WHERE id = 971")
    ok(not q("SELECT 1 FROM users WHERE id = 971") and not q("SELECT 1 FROM pay_ask_to") and os.environ.get("HR_PAY_KEY") == had_key,
       "끝나면 가상971 · 받는 사람 줄 · 열쇠를 되돌림")


def check_pay_pin(hr) -> None:
    """급여·계약 비밀번호 (2026-10-23): 세 앞머리는 풀린 창만 · 정하기 규칙 · 확인 · 5번 틀리면 잠김(이미 풀린 다른 창도) · 6분 · 잠그기 · 아직 있어요 · 열쇠 없음 ·
    잠긴 채로 보는 메뉴 숫자(/api/pay/counts) · HR 초기화(본인 · 비HR · 없는 사람 거절) · 구성원 설정의 상태 · 서버 명령(메일 주소로 초기화).
    앞부분의 열쇠는 검사가 만든 가짜 32바이트, 뒷부분은 앞 검사들과 같은 열쇠(계약서 설정이 풀려야 발송할 수 있다). 번호는 가상.
    끝나면 가상3의 비밀번호 줄 · 생일 · 연봉계약서 대상 칸 · 이 검사가 만든 세션 · 시계 · 환경 값을 되돌린다 (뒷부분이 올린 명세서 · 영수증 · 계약서는 마지막 검사라 남겨 둔다)."""
    import base64
    import subprocess

    import contract
    import contract_doc
    import kpi
    import paypin
    import push
    import withholding

    print("급여·계약 비밀번호")
    PIN, WRONG = "258036", "147036"
    had_key, real_now = os.environ.get("HR_PAY_KEY"), auth._now
    had_sessions = {r[0] for r in q("SELECT token_hash FROM sessions WHERE user_id = 3")}
    had_bday, had_ct = q("SELECT birthday, has_contract FROM users WHERE id = 3")[0]
    get = lambda c, p="/api/payslips": c.get(p, base_url=BASE)  # noqa: E731
    post = lambda c, p, body=None, headers=ORIGIN: c.post(p, base_url=BASE, headers=headers, json=body)  # noqa: E731
    lock = lambda c: get(c, "/api/pay/lock").json  # noqa: E731
    put_pin = lambda c, pin, again=None: post(c, "/api/pay/pin", {"pin": pin, "again": pin if again is None else again})  # noqa: E731
    unlock = lambda c, pin=PIN: post(c, "/api/pay/unlock", {"pin": pin})  # noqa: E731
    locked = lambda r, st: r.status_code == 423 and r.json == {"error": paypin.MSG_LOCKED, "lock": st}  # noqa: E731  # 칸은 정확히 둘
    fails = lambda: q("SELECT fails, locked_at IS NOT NULL FROM pay_pins WHERE user_id = 3")  # noqa: E731
    audit = lambda act: q("SELECT actor_user_id, entity_type, entity_id, before_data, after_data FROM audit_logs WHERE action = ?", act)  # noqa: E731
    no_pin_text = lambda: not any(p in str(q("SELECT * FROM audit_logs")) + str(q("SELECT * FROM pay_pins")) for p in (PIN, WRONG))  # noqa: E731

    def window():  # 가상3 으로 로그인한 새 PC 창
        c = app.test_client()
        login(c, new_token("emp"))
        return c

    try:
        os.environ["HR_PAY_KEY"] = base64.b64encode(os.urandom(32)).decode()  # 열쇠는 글자로 적지 않고 여기서 만든다
        x("UPDATE users SET birthday = '03-14' WHERE id = 3")
        a, b = window(), window()
        ok(get(a, "/api/me").json["user_id"] == get(b, "/api/me").json["user_id"] == 3 and cookie(a) != cookie(b), "(검사용) 가상3 의 PC 창 A · B (세션이 따로)")

        print("  - 정하기 전")
        st = lock(a)
        ok(set(st) == {"state", "left", "hr", "askHr"} and st["state"] == "set" and st["left"] == 5
           and all(set(h) == {"mailNo", "email"} for h in st["hr"]) and "u1@example.invalid" in {h["email"] for h in st["hr"]},
           "GET /api/pay/lock → set · 남은 5번 · 초기화를 부탁할 HR(가상1 메일)")
        paths = (("GET", "/api/payslips"), ("GET", "/api/withholding"), ("GET", "/api/withholding/2025/pdf"), ("GET", "/api/contracts/mine"),
                 ("GET", "/api/contracts/division"), ("POST", "/api/payslips/2026-10/seen"), ("POST", "/api/contracts/1/sign"))
        for m, p in paths:
            ok(locked(a.open(p, method=m, base_url=BASE, headers=ORIGIN), "set"), f"잠긴 A 로 {m} {p} → 423 set")
        ok(locked(get(hr, "/api/payslips/months"), "set"), "HR 길도 HR 본인 비밀번호로 잠김 → 423 set")
        ok(not any(r.status_code == 200 and "slips" in (r.get_json(silent=True) or {}) for r in (get(a, "/api/payslips/"), get(a, "/api//payslips"))),
           "주소를 비틀어도(끝 / · 겹친 /) 명세서가 든 200 은 없음")
        ok(locked(unlock(a), "set"), "정하기 전에 확인 → 423 set")

        print("  - 정하기")
        for pin, why in (("111111", "같은 숫자"), ("123456", "이어지는"), ("654321", "이어지는"), ("903141", "생일")):
            r = put_pin(a, pin)
            ok(r.status_code == 400 and r.json == {"error": paypin.problem(pin, pin, "03-14")} and why in r.json["error"], f"{pin} → 400 ({why})")
        r = put_pin(a, PIN, "258037")
        ok(r.status_code == 400 and r.json == {"error": "두 번 입력한 비밀번호가 달라요."}, "두 번이 다르면 400")
        ok(post(a, "/api/pay/pin", {"pin": PIN}).status_code == 400, "again 없이 보내면 400 (두 번 확인)")
        r, r2 = put_pin(a, "25a036"), post(a, "/api/pay/pin", {"pin": 258036, "again": 258036})
        ok(r.status_code == r2.status_code == 400 and r.json == r2.json == {"error": "숫자 6자리로 정해 주세요."}, "25a036 · 숫자(글자 아님) → 400 (500 아님)")
        ok(not q("SELECT 1 FROM pay_pins WHERE user_id = 3") and not audit("PAY_PIN_SET"), "거절된 정하기는 아무것도 남기지 않음")
        r = put_pin(a, PIN)
        ok(r.status_code == 200 and r.json == {"ok": True} and get(a).status_code == 200 and "slips" in get(a).json and lock(a)["state"] == "open",
           "258036 두 번 → 200, 이 창(A)은 바로 풀림 (명세서 200 · open)")
        r = put_pin(a, "147036")
        ok(r.status_code == 409 and r.json == {"error": "이미 비밀번호가 있어요."}, "다시 정하기 → 409")
        rows = q("SELECT pin_hash, salt, fails, locked_at FROM pay_pins WHERE user_id = 3")
        ok(len(rows) == 1 and len(rows[0][0]) == 64 and len(rows[0][1]) == 32 and rows[0][2:] == (0, None) and no_pin_text(),
           "pay_pins 에는 해시 · 소금만 (어느 칸에도 258036 없음)")
        ok(audit("PAY_PIN_SET") == [(3, "user", 3, None, None)], "감사로그 PAY_PIN_SET 1줄 (번호 없음)")

        print("  - 다른 창 · 틀림 · 잠김")
        ok(lock(b)["state"] == "ask" and locked(get(b), "ask"), "다른 창 B 는 여전히 → ask · 423 ask")
        r, r2 = unlock(b, "12345"), post(b, "/api/pay/unlock", {"pin": 258036})
        ok(r.status_code == r2.status_code == 400 and r.json == r2.json == {"error": "숫자 6자리로 정해 주세요."} and fails() == [(0, 0)],
           "확인에 6자리 숫자가 아니면(숫자로 보내도) 400 · 틀린 횟수에 넣지 않음")
        tries = [unlock(b, WRONG) for _ in range(4)]
        ok([r.status_code for r in tries] == [400] * 4 and [r.json for r in tries] == [{"error": paypin.MSG_WRONG, "left": n} for n in (4, 3, 2, 1)],
           "틀리면 400 + 남은 횟수 4 · 3 · 2 · 1")
        ok(locked(get(b), "ask") and lock(b)["left"] == 1 and get(a).status_code == 200, "틀리는 동안 B 는 잠긴 채 · A 는 그대로 풀림")
        ok(locked(unlock(b, WRONG), "locked") and fails() == [(5, 1)] and audit("PAY_PIN_LOCKED") == [(3, "user", 3, None, None)],
           "5번째 틀림 → 423 locked · 감사로그 PAY_PIN_LOCKED 1줄")
        ok(locked(unlock(b), "locked") and fails() == [(5, 1)], "잠긴 뒤에는 맞는 번호도 423 locked")
        c = window()
        ok(lock(c)["state"] == "locked" and lock(c)["left"] == 0, "새로 로그인한 창 C 도 locked (새 창으로 되돌릴 수 없음)")
        ok(locked(get(a), "locked"), "이미 풀려 있던 A 도 잠김 → 423 locked")
        ok(no_pin_text() and len(q("SELECT 1 FROM audit_logs WHERE action LIKE 'PAY_PIN%'")) == 2, "감사로그에는 정함 · 잠김 두 줄뿐 (번호 · 틀린 숫자 · 한 번 한 번은 없음)")

        print("  - 동시에 틀리기")
        x("DELETE FROM pay_pins WHERE user_id = 3")
        ok(put_pin(a, PIN).status_code == 200, "(검사용) 줄을 지우고 다시 정함")
        crowd, start, codes = [app.test_client() for _ in range(8)], threading.Barrier(8), []
        for cl in crowd:
            cl.set_cookie(auth.COOKIE, cookie(b))  # B 창 여덟 개

        def go(cl):
            start.wait()
            codes.append(unlock(cl, WRONG).status_code)

        ths = [threading.Thread(target=go, args=(cl,)) for cl in crowd]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        ok(fails() == [(5, 1)] and codes.count(400) == 4 and codes.count(423) == 4 and len(audit("PAY_PIN_LOCKED")) == 2,
           f"동시에 틀린 번호 8번 → 틀린 횟수 정확히 5 · 400 은 4개 · 나머지 423 ({sorted(codes)})")

        print("  - 6분 · 아직 있어요")
        x("DELETE FROM pay_pins WHERE user_id = 3")
        t0 = real_now()
        auth._now = lambda: t0
        ok(put_pin(a, PIN).status_code == 200 and post(a, "/api/pay/lock").json == {"ok": True} and locked(get(a), "ask"),
           "(검사용) 다시 정하고 A 잠그기 → 423 ask")
        r = unlock(b, "111111")
        ok(r.status_code == 400 and r.json == {"error": paypin.MSG_WRONG, "left": 4} and fails() == [(1, 0)],
           "확인은 정하기 규칙을 보지 않음: 같은 숫자 6개도 그냥 틀린 번호 → 400 MSG_WRONG · 남은 4번 (규칙 문구 아님)")
        ok(unlock(b, WRONG).json["left"] == 3 and unlock(a).json == {"ok": True} and fails() == [(0, 0)] and get(a).status_code == 200,
           "맞는 번호 → 200 · 틀린 횟수 0 으로 · A 풀림")
        auth._now = lambda: t0 + timedelta(seconds=359)
        ok(get(a).status_code == 200, "359초 뒤 → 200 (쓸 때마다 6분을 다시 셈)")
        auth._now = lambda: t0 + timedelta(seconds=359 + 359)
        ok(get(a).status_code == 200, "처음 푼 지 718초에도 200 (359초에 쓴 것이 6분을 늘렸을 때만 참)")
        auth._now = lambda: t0 + timedelta(seconds=359 + 359 + 361)
        ok(locked(get(a), "ask"), "마지막으로 쓴 뒤 361초 → 423 ask")
        t1 = t0 + timedelta(seconds=359 + 359 + 361)
        ok(unlock(a).status_code == 200, "(검사용) 다시 풀림")
        auth._now = lambda: t1 + timedelta(seconds=300)
        ok(post(a, "/api/pay/alive").json == {"open": True}, "300초 뒤 alive → open true")
        auth._now = lambda: t1 + timedelta(seconds=300 + 359)
        ok(get(a).status_code == 200, "alive 로 늘어나 풀린 뒤 659초에도 200")
        r = post(b, "/api/pay/alive")
        ok(r.status_code == 200 and r.json == {"open": False} and locked(get(b), "ask"), "잠긴 창 B 의 alive → open false · 그 뒤에도 423 ask")
        auth._now = real_now

        print("  - 잠그기 · 로그아웃 · 같은 출처 · 열쇠 없음")
        ok(post(a, "/api/pay/lock").status_code == 200 and locked(get(a), "ask"), "POST /api/pay/lock → 다음 요청 423 ask")
        ok(unlock(a).status_code == 200 and get(a).status_code == 200, "(검사용) 다시 풀림")
        ok(post(a, "/api/auth/logout").status_code == 200 and get(a).status_code == 401, "풀린 A 로그아웃 → 401")
        ok(post(b, "/api/pay/unlock", {"pin": WRONG}, headers={}).status_code == 403 and fails() == [(0, 0)],
           "Origin 없는 확인 → 403 (기존 같은 출처 검사, 횟수 그대로)")
        ok(get(b, "/api/pay/lock").headers.get("Cache-Control") == "no-store", "/api/pay/lock 응답 Cache-Control: no-store")
        os.environ.pop("HR_PAY_KEY")
        r = unlock(b)
        ok(lock(b) == {"state": "off"} and r.status_code == 503 and r.json == {"error": paypin.MSG_NO_KEY} and put_pin(b, PIN).status_code == 503,
           "열쇠가 없으면 off · 확인 · 정하기 503")
        r = get(b)
        ok(r.status_code == 200 and r.json == {"slips": []}, "열쇠가 없으면 잠금도 없음 → 명세서 200 빈 목록")

        print("  - 메뉴 숫자 길 · HR 초기화 · 구성원 설정 · 서버 명령")
        raw_key = bytes(range(32))  # 앞 검사들이 쓴 열쇠와 같게 — 그 검사들이 남긴 계약서 설정 · 직인이 풀린다
        os.environ["HR_PAY_KEY"] = base64.b64encode(raw_key).decode()
        x("DELETE FROM pay_pins WHERE user_id = 3")  # 앞 번호는 앞 열쇠로 해시했다
        x("UPDATE users SET has_contract = 1 WHERE id = 3")
        cy, ym, wy = 2096, "2096-02", 2096  # 가상 회차 · 달 · 해 (다른 검사가 쓰지 않는 값)
        due = (auth._today() + timedelta(days=30)).isoformat()
        PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="  # 1x1 그림 (가짜 직인)

        def tally() -> dict:
            c = connect(os.environ["HR_DB_PATH"])
            try:
                return paypin.counts(c, 3)
            finally:
                c.close()

        before, before_due = tally(), q("SELECT MIN(due_on) FROM contracts WHERE user_id = 3 AND status = 'SENT'")[0][0]
        with pay_gate_off():  # 자료는 HR 길로 — 비밀번호 검문 없이 (HR 은 번호가 없다)
            slip = {"userId": 3, "code": "PP003", "head": {"code": "PP003", "name": "가상3", "dept": "가상팀", "title": "프로", "job": "연구"},
                    "pay": [["기본급", 2500000]], "ded": [["소득세", 30000]], "gross": 2500000, "deduct": 30000, "net": 2470000}
            ok(post(hr, f"/api/payslips/{ym}", {"payDate": f"{ym}-25", "slips": [slip]}).status_code == 200 and post(hr, f"/api/payslips/{ym}/open").status_code == 200,
               "(검사용) HR 이 가상3 의 명세서 한 달을 올리고 공개")
            person = {"userId": 3, "sum": dict.fromkeys(withholding.SUM_KEYS, 0), "pdf": base64.b64encode(b"%PDF-1.4\n%fake").decode()}
            ok(post(hr, f"/api/withholding/{wy}", {"mode": "all", "reset": True, "total": 1, "people": [person]}).status_code == 200
               and post(hr, f"/api/withholding/{wy}/open").status_code == 200, "(검사용) HR 이 가상3 의 영수증 한 해를 올리고 공개")
            if get(hr, "/api/contracts/settings").json["settings"] is None:  # 앞 검사가 계약서 설정을 안 남겼을 때만
                ok(post(hr, "/api/contracts/settings", {"ceo": "○○○", "addr": "가상시 가상구 가상로 1", "kind": "가상 서비스업", "seal": PNG}).status_code == 200,
                   "(검사용) 계약서 설정을 만듦")
            body = {"code": "PP-CT", "name": "가상3", "dept": "가상팀", "title": "", "birth6": "900315", "base": 2418900, "ability": 1211000,
                    "pos": 300000, "site": 0, "qual": 0, "reason": ""}
            x("INSERT INTO contracts (year, user_id, version, status, starts_on, body, created_at, created_by) VALUES (?, 3, 1, 'DRAFT', ?, ?, ?, 1)",
              cy, contract_doc.round_period(cy)[0], contract._lock(raw_key, contract._aad(cy, 3, 1, "body"), body), auth._iso(auth._now()))
            r = post(hr, "/api/contracts/send", {"ids": [q("SELECT id FROM contracts WHERE year = ? AND user_id = 3", cy)[0][0]], "due": due})
            ok(r.status_code == 200 and r.json["sent"] == 1, "(검사용) HR 이 가상3 에게 계약서 한 장을 발송 (마감일을 하나 정해 둠)")
        mine3 = ("p1", "p2", "p3")
        want = {**{k: before[k] + 1 for k in mine3}, "due": min(d for d in (before_due, due) if d)}  # 더 이른 서명 대기가 앞 검사에서 남아 있으면 그 날
        ok(tally() == {k: want[k] for k in mine3}, "만든 자료만큼 paypin.counts 가 각각 +1")
        tell = lambda c: get(c, "/api/pay/counts")  # noqa: E731

        def pend() -> dict:
            """가상3 의 push.pending — 앞 검사가 규칙 번호 없는 확정본을 남겨 진행 기록이 500 이라 그 한 줄만 비우고 부른다 (여기서는 p1 · p2 · p3 만 본다)."""
            real_prog, kpi.progress_json = kpi.progress_json, lambda *a, **k: None
            try:
                return dict(push.pending(app, 3))
            finally:
                kpi.progress_json = real_prog

        reset = lambda c, uid: post(c, f"/api/members/{uid}/pay-pin/reset")  # noqa: E731
        member = lambda uid: next(m for m in get(hr, "/api/members").json["members"] if m["id"] == uid)  # noqa: E731
        lead = app.test_client()
        login(lead, new_token("lead"))
        d, e = window(), window()

        r = tell(d)
        ok(r.status_code == 200 and r.json == want and r.headers.get("Cache-Control") == "no-store" and app.test_client().get("/api/pay/counts", base_url=BASE).status_code == 401,
           "비밀번호를 정하기 전(set)에도 GET /api/pay/counts → 200 {p1 · p2 · p3 · due} 칸이 정확히 넷 (각각 +1 · 가장 이른 마감일) · no-store · 로그인 없이 401")
        ok(put_pin(d, PIN).status_code == 200 and unlock(e).status_code == 200 and tell(d).json == tell(e).json == want and get(d).status_code == get(e).status_code == 200,
           "(검사용) 가상3 이 번호를 정하고 두 창 D · E 를 모두 풀어 둠 — 풀린 창도 같은 숫자")
        ok(post(e, "/api/pay/lock").status_code == 200 and locked(get(e), "ask") and tell(e).status_code == 200 and tell(e).json == want,
           "잠긴 창(ask)으로도 /api/pay/counts 200 — 같은 숫자 (명세서 길은 423)")
        got = pend()
        ok({k: got.get(k) for k in mine3} == {k: want[k] for k in mine3}, "push.pending 의 p1 · p2 · p3 이 같은 수 (한 곳에서 셈)")

        print("  - HR 초기화 (거절 · 풀려 있던 창 · 잠긴 사람)")
        n_reset = lambda: len(audit("PAY_PIN_RESET"))  # noqa: E731
        was = (member(3)["payPin"], member(3)["payPinLockedAt"], member(2)["payPin"], member(1)["payPin"])
        ok(was == ("set", None, "none", "none"), "구성원 설정(HR 이 봄): 가상3 은 payPin set · 아직 잠긴 때 없음 / 번호가 없는 사람은 none")
        r = reset(hr, 1)
        ok(r.status_code == 403 and r.json == {"error": "본인 비밀번호는 다른 HR이 초기화해요."} and n_reset() == 0, "HR 본인(1) 초기화 → 403 (다른 HR 이 해 주는 일)")
        r = reset(lead, 3)
        ok(r.status_code == 403 and r.json == {"error": "HR만 할 수 있습니다."} and q("SELECT 1 FROM pay_pins WHERE user_id = 3") and n_reset() == 0,
           "HR 이 아닌 가상2(팀장)가 초기화 → 403 (번호는 그대로)")
        ok(reset(d, 3).status_code == 403 and post(hr, "/api/members/3/pay-pin/reset", headers={}).status_code == 403
           and app.test_client().post("/api/members/3/pay-pin/reset", base_url=BASE, headers=ORIGIN).status_code == 401
           and q("SELECT 1 FROM pay_pins WHERE user_id = 3") and n_reset() == 0, "본인(가상3)도 못 함 403 · Origin 없으면 403 · 로그인 없이 401 (번호는 그대로)")
        r = reset(hr, 999)
        ok(r.status_code == 404 and n_reset() == 0, "없는 사람(999) → 404 (감사로그 없음)")
        r = reset(hr, 3)
        ok(r.status_code == 200 and r.json == {"ok": True} and audit("PAY_PIN_RESET") == [(1, "user", 3, None, None)] and not q("SELECT 1 FROM pay_pins WHERE user_id = 3"),
           "HR 가 가상3 을 초기화 → 200 · 번호 줄이 사라짐 · 감사로그 PAY_PIN_RESET 1줄 (대상 가상3 · 번호 없음)")
        ok(lock(d)["state"] == lock(e)["state"] == "set" and not q("SELECT 1 FROM sessions WHERE user_id = 3 AND pay_used_at IS NOT NULL")
           and member(3)["payPin"] == "none", "가상3 의 모든 창이 set (풀려 있던 D 도) · 풀림 기록이 모두 비워짐 · 구성원 설정에는 none")
        ok(put_pin(e, PIN).status_code == 200 and get(e).status_code == 200 and locked(get(d), "ask"),
           "다시 정하면 정한 창 E 만 풀림 — 초기화 때 풀려 있던 D 는 되살아나지 않고 ask")
        ok(reset(hr, 3).status_code == 200 and not q("SELECT 1 FROM pay_pins WHERE user_id = 3") and reset(hr, 3).status_code == 200 and n_reset() == 3,
           "번호가 없는 사람을 또 초기화해도 200 (한 번 더 부르는 일 — 감사로그에는 부른 만큼 남음)")

        print("  - 잠긴 사람 · 구성원 설정 · 서버 명령")
        ok(put_pin(d, PIN).status_code == 200 and member(3)["payPin"] == "set" and member(3)["payPinLockedAt"] is None, "(검사용) 다시 정함 → 구성원 설정 payPin set")
        for _ in range(4):
            unlock(e, WRONG)
        ok(locked(unlock(e, WRONG), "locked") and member(3)["payPin"] == "locked", "5번 틀린 뒤 → 구성원 설정 payPin locked")
        at = q("SELECT locked_at FROM pay_pins WHERE user_id = 3")[0][0]
        ok(at and member(3)["payPinLockedAt"] == at and member(2)["payPinLockedAt"] is None, "payPinLockedAt = 잠긴 때(ISO) / 번호가 없는 사람은 None")
        ok(tell(e).status_code == 200 and tell(e).json == want and locked(get(d), "locked"), "잠긴(locked) 사람의 창으로도 메뉴 숫자는 200 — 명세서 길은 423 locked")
        r = reset(hr, 3)
        ok(r.status_code == 200 and member(3)["payPin"] == "none" and member(3)["payPinLockedAt"] is None and lock(d)["state"] == "set" and lock(d)["left"] == 5
           and n_reset() == 4, "HR 초기화 → 잠겼던 사람도 none · 다시 set (남은 5번)")
        ok(put_pin(d, PIN).status_code == 200 and get(d).status_code == 200, "(검사용) 초기화한 사람이 새 번호를 정함")

        conn = connect(os.environ["HR_DB_PATH"])
        try:
            ok(paypin.reset_by_email(conn, "U3@EXAMPLE.INVALID") == 3 and not q("SELECT 1 FROM pay_pins WHERE user_id = 3")
               and not q("SELECT 1 FROM sessions WHERE user_id = 3 AND pay_used_at IS NOT NULL") and lock(d)["state"] == "set",
               "reset_by_email(대문자 메일 주소) → 직원 번호 3 · 번호 줄과 풀림 기록이 사라짐")
            ok(audit("PAY_PIN_RESET")[-1] == (None, "user", 3, None, None) and n_reset() == 5
               and q("SELECT actor_position, actor_is_hr FROM audit_logs WHERE action = 'PAY_PIN_RESET' ORDER BY id DESC LIMIT 1") == [(None, 0)],
               "서버 명령의 감사로그 PAY_PIN_RESET 은 부른 사람(actor)이 없음")
            ok(paypin.reset_by_email(conn, "nobody@example.invalid") is None and paypin.reset_by_email(conn, "") is None and n_reset() == 5,
               "없는 메일 주소 → None (감사로그 없음)")
        finally:
            conn.close()

        cli = lambda *a, db=None: subprocess.run([sys.executable, "-m", "paypin", *a], cwd=HERE, capture_output=True, text=True, encoding="utf-8",  # noqa: E731
                                                 env={**os.environ, "PYTHONIOENCODING": "utf-8", **({"HR_DB_PATH": db} if db else {})})
        ok(put_pin(d, PIN).status_code == 200, "(검사용) 다시 정함")
        r = cli("reset", "U3@example.invalid")
        ok(r.returncode == 0 and r.stdout.strip() == "초기화했습니다 (직원 번호 3)" and not q("SELECT 1 FROM pay_pins WHERE user_id = 3") and n_reset() == 6
           and audit("PAY_PIN_RESET")[-1] == (None, "user", 3, None, None), f"python -m paypin reset <메일 주소> → 초기화했습니다 (직원 번호 3) · 종료 코드 0 ({r.stdout.strip()!r} {r.stderr[-200:]!r})")
        r = cli("reset", "nobody@example.invalid")
        ok(r.returncode == 1 and "그 메일 주소의 직원이 없습니다" in r.stdout + r.stderr and n_reset() == 6, "없는 메일 주소 → 그 메일 주소의 직원이 없습니다 · 종료 코드 1")
        ok(cli("reset").returncode != 0 and cli().returncode != 0 and n_reset() == 6, "메일 주소가 없거나 명령이 없으면 사용법 · 종료 코드 0 아님")
        with tempfile.TemporaryDirectory() as td:
            r = cli("reset", "u3@example.invalid", db=str(Path(td) / "nope.db"))
            ok(r.returncode == 1 and "DB 파일이 없습니다" in r.stdout + r.stderr and "Traceback" not in r.stderr and list(Path(td).iterdir()) == [],
               f"없는 DB 파일(HR_DB_PATH) → 깔끔한 안내 · 종료 코드 1 · 빈 DB 와 WAL 파일을 만들지 않음 ({(r.stdout + r.stderr).strip()[-120:]!r})")

        os.environ.pop("HR_PAY_KEY")
        got = pend()
        ok(tell(d).json == {"p1": 0, "p2": 0, "p3": 0, "due": None} and not any(k in got for k in mine3), "열쇠를 빼면 메뉴 숫자 셋 모두 0 · due None (push.pending 에도 없음)")
    finally:
        auth._now = real_now
        os.environ.pop("HR_PAY_KEY", None)
        if had_key is not None:
            os.environ["HR_PAY_KEY"] = had_key
        x("DELETE FROM pay_pins WHERE user_id = 3")
        x("UPDATE users SET birthday = ?, has_contract = ? WHERE id = 3", had_bday, had_ct)
        made = [h for (h,) in q("SELECT token_hash FROM sessions WHERE user_id = 3") if h not in had_sessions]
        x(f"DELETE FROM sessions WHERE token_hash IN ({','.join('?' * len(made))})", *made)
    ok(not q("SELECT 1 FROM pay_pins WHERE user_id = 3") and q("SELECT birthday, has_contract FROM users WHERE id = 3") == [(had_bday, had_ct)]
       and {r[0] for r in q("SELECT token_hash FROM sessions WHERE user_id = 3")} == had_sessions
       and os.environ.get("HR_PAY_KEY") == had_key and auth._now is real_now,
       "끝나면 가상3 의 비밀번호 줄 · 생일 · 연봉계약서 대상 칸 · 이 검사의 세션 · 열쇠 · 시계를 되돌림")


def year_day(n: int) -> str:
    """오늘 + n일을 올해(한국 날짜) 안으로 맞춘 날짜 — KPI 기간은 그해 날짜만 받으므로 1월 1일 · 12월 31일에도 검사가 그대로 돈다."""
    t = auth._today()
    return min(max(t + timedelta(days=n), date(t.year, 1, 1)), date(t.year, 12, 31)).isoformat()


def check_kpi_owner(hr) -> None:
    """KPI 계획서 본인 쓰기 (2026-10-20): HR 이 연 작성 기간 안에서만 · 임시 저장 → 제출 → 취소 · 확정 뒤 수정 요청 · 기간 끝은 한국 자정 ·
    서버가 순서대로 다시 본다 (KPI 작성 대상 403 → 작성 기간 403 → 상태 409 → 내용 400)."""
    import kpi
    print("KPI 계획서 (본인)")
    emp, lead, anon = app.test_client(), app.test_client(), app.test_client()
    login(emp, new_token("emp"))
    login(lead, new_token("lead"))
    was_kpi = q("SELECT writes_kpi FROM users WHERE id = 3")[0][0]
    x("UPDATE users SET writes_kpi = 1 WHERE id = 3")  # 앞 검사가 꺼 두었다
    today, year = auth._today(), kpi.plan_year()
    day = year_day
    later = day(60) if day(60) > day(0) else None  # 평가 시작일 — 오늘 뒤 그해 안 (12월 31일이면 그런 날이 없어 비움)
    me = lambda c: c.get("/api/kpi/me", base_url=BASE)  # noqa: E731
    do = lambda c, what, body=None, headers=ORIGIN: c.post(f"/api/kpi/me/{what}", base_url=BASE, headers=headers, json=body or {})  # noqa: E731
    draft = lambda c, plan: do(c, "draft", {"draft": plan})  # noqa: E731
    row = lambda y=year: q("SELECT status, note, note_kind, change_reason, submitted_at IS NOT NULL FROM kpi_plans WHERE user_id = 3 AND year = ?", y)  # noqa: E731

    def setyear(client, frm, to, start=None, y=year, headers=ORIGIN):
        return client.post("/api/kpi/admin/year", base_url=BASE, headers=headers,
                           json={"year": y, "editFrom": frm, "editTo": to, "evalStart": start})

    def conflict(r, st):
        return r.status_code == 409 and r.json == {"error": "지금 상태에서는 할 수 없습니다.", "status": st}

    def good(y=year):
        """정기 2 · 비정기 1, 가중치 40/30/30 (가상 글)."""
        item = lambda name, w, kind, **more: {  # noqa: E731
            "action": name, "formula": "가상 측정식", "weight": w, "owner": "가상3", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
            "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}
        return [
            {"team": "가상 팀목표 1", "personal": "가상 개인목표 1", "items": [item("가상 정기 업무 1", 40, "reg", target=12, unit="건"),
                                                                     item("가상 정기 업무 2", 30, "reg")]},
            {"team": "가상 팀목표 2", "personal": "가상 개인목표 2", "items": [item("가상 비정기 업무", 30, "irr", scheds=[
                {"due": f"{y}-03-31", "name": "가상 초안"}, {"due": f"{y}-12-31", "name": "가상 마감"}])]},
        ]

    plan = good()

    # 로그인 · 기간이 없을 때
    ok(all(anon.get(p, base_url=BASE).status_code == 401 for p in ("/api/kpi/me", "/api/kpi/admin/year"))
       and all(do(anon, w).status_code == 401 for w in ("draft", "submit", "cancel", "request-change"))
       and anon.post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN, json={}).status_code == 401, "로그인 없이는 401 (KPI)")
    r = me(emp)
    ok(r.status_code == 200 and r.json == {"year": year, "writesKpi": True, "plan": None, "manager": {"id": 2, "name": "가상2"},
                                           "window": {"from": None, "to": None, "evalStart": None, "open": False}},
       "처음엔 계획서도 작성 기간도 없음 · 바로 위 관리자는 같은 팀 팀장")
    ok(me(lead).json["manager"] is None, "가상 조직에는 팀장 위 본부장도 대표이사도 없음 → 관리자 null")
    r = draft(emp, plan)
    ok(r.status_code == 403 and r.json == {"error": "KPI 작성 기간이 아닙니다."} and not q("SELECT 1 FROM kpi_plans"),
       "작성 기간이 없으면 임시 저장 403 (아무것도 안 만들어짐)")
    ok(all(do(emp, w, {"reason": "가상 사유"}).status_code == 403 for w in ("submit", "cancel", "request-change")), "제출 · 취소 · 수정 요청도 기간 밖이면 403")

    # HR 이 작성 기간을 연다
    ok(setyear(lead, day(-1), day(7)).status_code == 403 and setyear(emp, None, None).status_code == 403
       and emp.get("/api/kpi/admin/year", base_url=BASE).status_code == 403 and not q("SELECT 1 FROM kpi_years"),
       "HR 이 아니면 작성 기간을 못 열고 못 봄 (403)")
    ok(setyear(hr, day(-1), day(7), headers={}).status_code == 403, "작성 기간도 포털 화면에서만 (Origin)")
    for bad in ({"year": year, "editFrom": "2026-13-01", "editTo": day(7)}, {"year": year, "editFrom": "abc", "editTo": "abc"},
                {"year": year, "editFrom": "2026-2-3", "editTo": day(7)}, {"year": year, "editFrom": 20261020, "editTo": day(7)},
                {"year": year, "editFrom": day(-1)}, {"year": year, "editTo": day(7)}, {"year": year, "editFrom": day(7), "editTo": day(-1)},
                {"year": year, "editFrom": day(-1), "editTo": day(7), "evalStart": "내일"},
                {"year": str(year), "editFrom": day(-1), "editTo": day(7)}, {"year": True, "editFrom": day(-1), "editTo": day(7)},
                {"year": 2025, "editFrom": day(-1), "editTo": day(7)}, {"year": 2101, "editFrom": day(-1), "editTo": day(7)},
                {"editFrom": day(-1), "editTo": day(7)}, []):
        r = hr.post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN, json=bad)
        ok(r.status_code == 400 and not q("SELECT 1 FROM kpi_years"), f"날짜 모양 · 기간 순서 · 해가 틀리면 400, 아무것도 안 남김 ({str(bad)[:60]})")
    out_of_year = f"{year}년 기간에는 {year}년 날짜만 넣을 수 있어요. {year + 1}년 기간은 {year + 1}년 1월 1일부터 정할 수 있어요."
    for bad in ({"editFrom": f"{year + 1}-01-05", "editTo": f"{year + 1}-01-23"}, {"editFrom": f"{year - 1}-12-31", "editTo": day(0)},
                {"editFrom": day(0), "editTo": f"{year + 1}-01-01"}, {"editFrom": day(0), "editTo": day(0), "evalStart": f"{year + 1}-01-01"},
                {"editFrom": "", "editTo": "", "evalStart": f"{year - 1}-12-31"}):
        r = hr.post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN, json={"year": year, **bad})
        ok(r.status_code == 400 and r.json == {"error": out_of_year} and not q("SELECT 1 FROM kpi_years"),
           f"그해 밖의 날짜는 400 — 그 해의 줄은 그해에만 읽히므로 ({str(bad)[:70]})")
    r = setyear(hr, day(-1), day(7), later)
    ok(r.status_code == 200 and q("SELECT edit_from, edit_to, eval_start, updated_by FROM kpi_years WHERE year = ?", year)
       == [(day(-1), day(7), later, 1)], "HR 이 오늘을 포함한 기간을 엶 (누가 했는지도 남음)")
    ok(hr.get("/api/kpi/admin/year", base_url=BASE).json == {"year": year, "editFrom": day(-1), "editTo": day(7), "evalStart": later}
       and hr.get("/api/kpi/admin/year?year=2090", base_url=BASE).json == {"year": 2090, "editFrom": None, "editTo": None, "evalStart": None}
       and all(hr.get(f"/api/kpi/admin/year?year={y}", base_url=BASE).status_code == 400 for y in ("abc", "2025", "")),
       "기간 읽기: 해를 안 고르면 올해 · 정하지 않은 해는 빈 값 · 틀린 해는 400")

    # 임시 저장
    ok(draft(emp, plan).json == {"ok": True} and do(emp, "draft", {"draft": plan}, headers={}).status_code == 403,
       "기간 안에서 임시 저장 200 (Origin 없으면 403)")
    p = me(emp).json
    ok(p["window"] == {"from": day(-1), "to": day(7), "evalStart": later, "open": True}
       and p["plan"] == {"status": "DRAFT", "draft": plan, "note": None, "noteKind": None, "changeReason": None, "confirmed": None},
       "읽으면 초안이 그대로, 상태 DRAFT, 기간은 열림")
    ok(me(lead).json["plan"] is None, "다른 사람의 계획서는 안 보임")
    dirty = good()
    dirty[0]["junk"] = "버릴 칸"
    dirty[0]["items"][0].update(rid=7, extra={"a": 1})
    dirty[1]["items"][0]["scheds"][0].update(sid=5, extra=1)
    kept = good()
    kept[0]["items"][0]["rid"] = 7
    kept[1]["items"][0]["scheds"][0]["sid"] = 5
    ok(draft(emp, dirty).status_code == 200 and me(emp).json["plan"]["draft"] == kept, "모르는 칸은 버리고 rid · sid 는 보낸 대로 둠")
    big = good()
    big[0]["junk"] = ""
    base = len(json.dumps(big, ensure_ascii=False).encode("utf-8"))
    big[0]["junk"] = "a" * (200 * 1024 - base)
    ok(draft(emp, big).status_code == 200 and me(emp).json["plan"]["draft"] == plan, "딱 200KB 는 저장됨 (모르는 칸은 버려짐)")
    big[0]["junk"] += "a"
    r = draft(emp, big)
    ok(r.status_code == 400 and "너무 큽니다" in r.json["error"] and me(emp).json["plan"]["draft"] == plan, "200KB 를 넘으면 400 · 저장된 것은 그대로")
    big[0]["junk"] = "가" * 70000
    ok(draft(emp, big).status_code == 400, "큰 계획서는 막음 (한글은 한 글자 3바이트로 셈)")
    ok(draft(emp, [{"team": "\ud800", "personal": "", "items": []}]).status_code == 400, "UTF-8 로 쓸 수 없는 글자는 400 (서버 오류가 아님)")
    short = good()
    short[0]["items"][0]["action"] = "가" * 201
    wtype = good()
    wtype[0]["items"][0]["weight"] = "40"
    irr_target = good()
    irr_target[1]["items"][0]["target"] = 5
    for why, bad in (("목록이 아님", {"x": 1}), ("글자가 아닌 칸", wtype), ("비정기에 목표 숫자", irr_target), ("글자 한도", short), ("draft 칸 없음", None)):
        r = draft(emp, bad) if bad is not None else do(emp, "draft", {"nope": 1})
        ok(r.status_code == 400 and r.json["issues"] and me(emp).json["plan"]["draft"] == plan, f"모양 · 글자 한도가 틀린 초안은 400, 저장된 것은 그대로 ({why})")

    # 제출
    wrong = good()
    wrong[0]["items"][0]["weight"] = 39  # 합 99
    ok(draft(emp, []).status_code == 200 and draft(emp, wrong).status_code == 200, "가중치 합이 틀려도 · 빈 목록이어도 임시 저장은 됨 (나머지는 제출 때 본다)")
    r = do(emp, "submit")
    ok(r.status_code == 400 and any("가중치" in m for m in r.json["issues"]) and row() == [("DRAFT", None, None, None, 0)],
       "틀린 계획서는 제출되지 않음 (400 + 가중치 문구) · 상태 그대로")
    draft(emp, plan)
    x("UPDATE kpi_plans SET note = '가상 돌려보냄 사유', note_kind = 'RETURNED' WHERE user_id = 3 AND year = ?", year)  # 관리자가 돌려보낸 뒤라고 치고
    p = me(emp).json["plan"]
    ok(p["note"] == "가상 돌려보냄 사유" and p["noteKind"] == "RETURNED", "돌려보낸 사유가 계획서와 함께 옴")
    ok(do(emp, "submit").json == {"ok": True} and row() == [("SUBMITTED", None, None, None, 1)],
       "좋은 계획서를 내면 SUBMITTED · 제출 시각 · 돌려보낸 사유는 지움")
    ok(conflict(draft(emp, plan), "SUBMITTED") and conflict(do(emp, "submit"), "SUBMITTED")
       and conflict(do(emp, "request-change", {"reason": "가상 사유"}), "SUBMITTED"), "제출한 뒤에는 임시 저장 · 다시 제출 · 수정 요청이 409 (지금 상태를 알려 줌)")
    ok(conflict(draft(emp, "엉터리"), "SUBMITTED"), "상태 409 가 내용 400 보다 먼저")
    x("UPDATE kpi_plans SET note = '가상 남은 사유', note_kind = 'REJECTED' WHERE user_id = 3 AND year = ?", year)
    ok(do(emp, "cancel").json == {"ok": True} and row() == [("DRAFT", "가상 남은 사유", "REJECTED", None, 1)], "제출 취소 → DRAFT (상태만 바뀜)")
    ok(conflict(do(emp, "cancel"), "DRAFT") and conflict(do(emp, "request-change", {"reason": "가상 사유"}), "DRAFT")
       and conflict(do(emp, "request-change", {"reason": ""}), "DRAFT"), "작성 중일 때 취소 · 수정 요청은 409 (사유가 틀려도 상태 먼저)")
    ok(conflict(do(lead, "submit"), "NONE") and conflict(do(lead, "cancel"), "NONE"), "계획서가 없으면 제출 · 취소는 409 (상태 NONE)")
    x("UPDATE kpi_plans SET note = NULL, note_kind = NULL WHERE user_id = 3 AND year = ?", year)

    # 기간 끝은 한국 자정 — 서버 시계(UTC) 14:59 가 한국 23:59, 15:10 은 다음 날 00:10
    d = today - timedelta(days=1) if today.timetuple().tm_yday > 1 else today  # 1월 1일이면 어제는 작년 계획서 — 자정이 지나도 같은 해이게 오늘로
    setyear(hr, max(d - timedelta(days=5), date(d.year, 1, 1)).isoformat(), d.isoformat(), y=d.year)
    real_now = auth._now
    try:
        auth._now = lambda: datetime(d.year, d.month, d.day, 14, 59, tzinfo=timezone.utc)
        late = app.test_client()  # 그 시각에 연 창 — 1월 1일에는 오늘 밤이 지금보다 뒤라, 미리 연 창은 그사이 오래 쉬어 로그인이 풀린다
        login(late, new_token("emp"))
        ok(draft(late, good(d.year)).status_code == 200 and do(late, "submit").status_code == 200 and row(d.year)[0][0] == "SUBMITTED",
           "종료일 한국 시각 23:59 까지는 제출됨")
        ok(do(late, "cancel").status_code == 200, "(제출을 취소해 다시 DRAFT)")
        auth._now = lambda: datetime(d.year, d.month, d.day, 15, 10, tzinfo=timezone.utc)
        r = do(late, "submit")
        ok(r.status_code == 403 and r.json == {"error": "KPI 작성 기간이 아닙니다."} and row(d.year)[0][0] == "DRAFT",
           "한국 자정이 지나면(UTC 15:10) 제출 403")
    finally:
        auth._now = real_now

    # 확정 뒤 수정 요청 (확정은 다음 작업이 한다 — 여기서는 확정된 줄을 직접 넣는다)
    setyear(hr, day(-1), day(7), later)
    draft(emp, plan)
    crowd = [app.test_client() for _ in range(5)]  # 같은 사람이 창을 여러 개 열어 놓고 동시에 제출을 누른다
    for c in crowd:
        login(c, new_token("emp"))
    submitted = lambda: q("SELECT COUNT(*) FROM audit_logs WHERE action = 'KPI_SUBMITTED' AND entity_id = 3")[0][0]  # noqa: E731
    before, codes = submitted(), []
    threads = [threading.Thread(target=lambda c=c: codes.append(do(c, "submit").status_code)) for c in crowd]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    ok(sorted(codes) == [200, 409, 409, 409, 409] and submitted() == before + 1, f"동시에 제출을 다섯 번 눌러도 한 번만 통과 ({codes})")
    x("INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by) VALUES (3, ?, 1, ?, '2026-10-20T01:02:03Z', 2)",
      year, json.dumps(plan, ensure_ascii=False))
    x("UPDATE kpi_plans SET status = 'CONFIRMED', confirmed_version = 1 WHERE user_id = 3 AND year = ?", year)
    p = me(emp).json["plan"]
    ok(p["status"] == "CONFIRMED" and p["confirmed"] == {"version": 1, "content": plan, "at": "2026-10-20T01:02:03Z", "by": "가상2"},
       "확정본: 판 · 내용 · 시각 · 확정한 사람의 이름")
    ok(conflict(draft(emp, plan), "CONFIRMED") and conflict(do(emp, "submit"), "CONFIRMED") and conflict(do(emp, "cancel"), "CONFIRMED"),
       "확정되면 임시 저장 · 제출 · 취소는 409")
    for bad in (None, "", "   ", 5, ["x"], "가" * 201, "\x00", "가\x1b"):
        r = do(emp, "request-change", {"reason": bad} if bad is not None else {})
        ok(r.status_code == 400 and row() == [("CONFIRMED", None, None, None, 1)], f"수정 요청 사유는 1~200자 글자 ({bad!r:.12})")
    ok(do(emp, "request-change", {"reason": "가" * 200}).status_code == 200, "사유 200자는 됨")
    x("UPDATE kpi_plans SET status = 'CONFIRMED', change_reason = NULL, note = '가상 남은 사유', note_kind = 'RETURNED' WHERE user_id = 3 AND year = ?", year)
    ok(do(emp, "request-change", {"reason": "  가상 사유 문장  "}).json == {"ok": True}
       and row() == [("CHANGE_REQUESTED", None, None, "가상 사유 문장", 1)], "확정 뒤 수정 요청 → CHANGE_REQUESTED · 사유(앞뒤 공백 뺌) · 남은 사유는 지움")
    ok(me(emp).json["plan"]["changeReason"] == "가상 사유 문장" and conflict(draft(emp, plan), "CHANGE_REQUESTED")
       and conflict(do(emp, "submit"), "CHANGE_REQUESTED") and conflict(do(emp, "cancel"), "CHANGE_REQUESTED"),
       "수정 요청 중에는 임시 저장 · 제출 · 취소가 409 (풀어 주는 건 관리자)")

    # 기간이 닫히면 수정 요청도 막힘 · 사람만의 기간 · 평가 시작일
    x("UPDATE kpi_plans SET status = 'CONFIRMED', change_reason = NULL WHERE user_id = 3 AND year = ?", year)
    r = setyear(hr, "", "", later)
    ok(r.status_code == 200 and q("SELECT edit_from, edit_to FROM kpi_years WHERE year = ?", year) == [(None, None)]
       and me(emp).json["window"] == {"from": None, "to": None, "evalStart": later, "open": False}, "기간 닫기 = 둘 다 비움")
    ok(all(do(emp, w, {"reason": "가상 사유", "draft": plan}).status_code == 403 for w in ("draft", "submit", "cancel", "request-change"))
       and row() == [("CONFIRMED", None, None, None, 1)], "기간 밖에서는 수정 요청까지 모두 403 · 기록은 그대로")
    ahead = lambda n: (today + timedelta(days=n)).isoformat()  # noqa: E731  — SQL 로 바로 넣는 날짜라 그해 안으로 맞추지 않는다 (맞추면 12월 30 · 31일에 오늘이 들어간다)
    x("INSERT INTO kpi_windows (user_id, year, edit_from, edit_to, set_by, set_at) VALUES (3, ?, ?, ?, 1, '2026-10-20T00:00:00Z')", year, ahead(3), ahead(9))
    ok(me(emp).json["window"] == {"from": ahead(3), "to": ahead(9), "evalStart": later, "open": False}, "그 사람만의 기간이 우선 (아직 안 열림)")
    x("UPDATE kpi_windows SET edit_from = ?, edit_to = ? WHERE user_id = 3", day(-2), day(0))
    ok(me(emp).json["window"]["open"] is True and do(emp, "request-change", {"reason": "가상 사유 2"}).status_code == 200,
       "한 사람만 연 기간 안이면 그해 기간이 닫혀 있어도 씀")
    x("DELETE FROM kpi_windows WHERE user_id = 3")
    setyear(hr, day(-1), day(7), day(0))
    ok(me(emp).json["window"]["open"] is False and do(emp, "cancel").status_code == 403, "평가 시작일부터는 작성 기간 안이어도 닫힘")

    # KPI 작성이 꺼진 사람 — 읽기는 되고 쓰기는 모두 403 (기간이 닫혀 있어도 이 이유가 먼저)
    x("UPDATE users SET writes_kpi = 0 WHERE id = 3")
    for opened in (False, True):
        setyear(hr, day(-1), day(7), later if opened else day(0))
        r = me(emp)
        ok(r.status_code == 200 and r.json["writesKpi"] is False, f"KPI 작성이 꺼진 사람도 읽기는 200, writesKpi false (기간 {'열림' if opened else '닫힘'})")
        ok(all((r := do(emp, w, {"reason": "가상 사유", "draft": plan})).status_code == 403 and r.json == {"error": "KPI 작성 대상이 아닙니다."}
               for w in ("draft", "submit", "cancel", "request-change")), f"쓰기는 모두 403 (기간 {'열림' if opened else '닫힘'})")
    x("UPDATE users SET writes_kpi = ? WHERE id = 3", was_kpi)

    # 감사로그 — 행동 이름 · 대상 · 해만
    acts = {r[0] for r in q("SELECT action FROM audit_logs WHERE action LIKE 'KPI_%'")}
    ok(acts == {"KPI_YEAR_SET", "KPI_SUBMITTED", "KPI_CANCELLED", "KPI_CHANGE_REQUESTED"}, "감사로그: 기간 · 제출 · 취소 · 수정 요청 (임시 저장은 남기지 않음)")
    ok(q("SELECT actor_user_id, entity_type, entity_id, after_data FROM audit_logs WHERE action = 'KPI_SUBMITTED' ORDER BY id LIMIT 1")
       == [(3, "kpi_plan", 3, json.dumps({"year": year}))], "제출 감사로그: 누가 · 누구 계획서 · 해")
    ok(q("SELECT actor_user_id, actor_is_hr, entity_id FROM audit_logs WHERE action = 'KPI_YEAR_SET' ORDER BY id LIMIT 1") == [(1, 1, year)],
       "작성 기간 감사로그: HR · 해")
    ok(q("SELECT COUNT(*) FROM audit_logs WHERE action LIKE 'KPI_%' AND (IFNULL(before_data, '') || IFNULL(after_data, '') || IFNULL(reason, '')) LIKE '%가상%'")
       == [(0,)], "계획서 · 사유 문장은 감사로그에 없음")


def check_kpi_manager(hr) -> None:
    """KPI 계획서 관리자 쓰기 (2026-10-20): 바로 위 관리자가 확정 · 돌려보내기 · 해제 · 거절 — 그 사람의 작성 기간 안에서만 ·
    HR 은 대신 할 수 있고 원래 관리자가 남음 · 대표이사 몫은 맡겼을 때만 · 처리함 · 사람별 기간 ·
    서버가 순서대로 다시 본다 (권한 403 → 작성 기간 403 → 상태 409 → 사유 400)."""
    import kpi
    print("KPI 계획서 (관리자)")
    # 이 검사만의 가상 조직 — 앞 검사가 바꿔 놓은 것에 기대지 않는다.
    # 본부(31) 밑 팀 A(32) · B(33), 본부장이 없는 본부(35) 밑 팀 C(34), 본부장 · 대표이사
    for dept in ((31, "K31", "가상K본부", "DIVISION", None), (32, "K32", "가상K팀A", "TEAM", 31), (33, "K33", "가상K팀B", "TEAM", 31),
                 (35, "K35", "가상K본부2", "DIVISION", None), (34, "K34", "가상K팀C", "TEAM", 35)):
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (?, ?, ?, ?, ?)", *dept)
    cast = {41: ("가상팀장A", 32, "TEAM_LEADER"), 42: ("가상팀원A1", 32, None), 43: ("가상팀원A2", 32, None), 48: ("가상팀원A3", 32, None),
            44: ("가상팀장B", 33, "TEAM_LEADER"), 45: ("가상팀원B1", 33, None), 49: ("가상팀원B2", 33, None),
            46: ("가상본부장", 31, "DIVISION_HEAD"), 47: ("가상대표", 31, "CEO"), 50: ("가상팀장C", 34, "TEAM_LEADER")}
    for uid, (name, dept, role) in cast.items():
        x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, writes_kpi) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
          uid, name, f"k{uid}@example.invalid", dept, f"HW-{uid}", int(role != "CEO"))  # 대표이사는 KPI 를 쓰지 않는다
        if role:
            x("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", uid, role)
    # 팀 A 에 있어도 우리 팀 상태 · HR 목록에 안 세는 사람: 퇴사자, KPI 를 안 쓰는 현장직
    x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, deactivated_at, writes_kpi)"
      " VALUES (51, '가상퇴사자', 'k51@example.invalid', 32, 'HW-51', 1, 0, '2027-01-01T00:00:00Z', 1)")
    x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, writes_kpi)"
      " VALUES (52, '가상현장직', 'k52@example.invalid', 32, 'HW-52', 1, 1, 0)")
    cl = {uid: app.test_client() for uid in cast}
    for uid, c in cl.items():
        login(c, new_token(f"HW-{uid}"))
    lead, a1, a2, a3, leadb, b1, b2, head, ceo, leadc = (cl[i] for i in (41, 42, 43, 48, 44, 45, 49, 46, 47, 50))
    anon = app.test_client()
    x("UPDATE users SET writes_kpi = 1, title = NULL WHERE id = 1")
    x("UPDATE users SET title = '프로' WHERE id = 42")
    today, year = auth._today(), kpi.plan_year()
    day = year_day
    me = lambda c: c.get("/api/kpi/me", base_url=BASE).json  # noqa: E731
    inbox = lambda c: c.get("/api/kpi/inbox", base_url=BASE).json  # noqa: E731
    who = lambda c: [(i["userId"], i["proxy"]) for i in inbox(c)["items"]]  # noqa: E731
    act = lambda c, uid, what, body=None, headers=ORIGIN: c.post(f"/api/kpi/plans/{uid}/{what}", base_url=BASE, headers=headers, json=body or {})  # noqa: E731
    own = lambda c, what, body=None: c.post(f"/api/kpi/me/{what}", base_url=BASE, headers=ORIGIN, json=body or {})  # noqa: E731
    setyear = lambda frm, to: hr.post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN, json={"year": year, "editFrom": frm, "editTo": to})  # noqa: E731
    setwin = lambda body, c=hr, headers=ORIGIN: c.post("/api/kpi/admin/window", base_url=BASE, headers=headers, json=body)  # noqa: E731
    delegate = lambda on, c=ceo, headers=ORIGIN: c.post("/api/kpi/delegation", base_url=BASE, headers=headers, json={"on": on})  # noqa: E731
    row = lambda uid: q("SELECT status, note, note_kind, change_reason, confirmed_version FROM kpi_plans WHERE user_id = ? AND year = ?", uid, year)  # noqa: E731
    versions = lambda uid: q("SELECT version, confirmed_by, on_behalf_of FROM kpi_versions WHERE user_id = ? AND year = ? ORDER BY version", uid, year)  # noqa: E731
    audit = lambda action, uid: q("SELECT actor_user_id, actor_is_hr, on_behalf_of, entity_type, after_data FROM audit_logs"  # noqa: E731
                                  " WHERE action = ? AND entity_id = ? ORDER BY id", action, uid)
    conflict = lambda r, st: r.status_code == 409 and r.json == {"error": "지금 상태에서는 할 수 없습니다.", "status": st}  # noqa: E731
    no_auth, no_window = {"error": "이 KPI를 처리할 권한이 없습니다."}, {"error": "KPI 작성 기간이 아닙니다."}
    zero = {"NONE": 0, "DRAFT": 0, "SUBMITTED": 0, "CONFIRMED": 0, "CHANGE_REQUESTED": 0}
    calm = {"check": 0, "replies": 0, "memos": 0}  # 처리할 일 숫자(review) — 이 검사에서는 진행 기록 · 메모가 없어 늘 0
    y = {"year": year}

    def mk(tag="가상"):
        """정기 2 · 비정기 1 (일정 2), 가중치 40/30/30."""
        item = lambda name, w, kind, **more: {  # noqa: E731
            "action": name, "formula": "가상 측정식", "weight": w, "owner": "가상", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
            "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}
        return [{"team": f"{tag} 팀목표", "personal": f"{tag} 개인목표", "items": [
            item(f"{tag} 정기 1", 40, "reg"), item(f"{tag} 정기 2", 30, "reg"),
            item(f"{tag} 비정기", 30, "irr", scheds=[{"due": f"{year}-03-31", "name": "가상 초안"}, {"due": f"{year}-12-31", "name": "가상 마감"}])]}]

    def send(c, plan=None):
        """초안을 저장하고 제출한다."""
        return own(c, "draft", {"draft": plan or mk()}).status_code == 200 and own(c, "submit").status_code == 200

    ok(all(anon.get(p, base_url=BASE).status_code == 401 for p in ("/api/kpi/inbox", "/api/kpi/admin", "/api/kpi/delegation", "/api/kpi/plans/42"))
       and all(anon.post(f"/api/kpi/{p}", base_url=BASE, headers=ORIGIN, json={}).status_code == 401
               for p in ("plans/42/confirm", "plans/42/return", "plans/42/unlock", "plans/42/reject", "admin/window", "delegation")),
       "로그인 없이는 401 (관리자 쪽 KPI)")

    # ---- 제출 → 처리함 ----
    ok(setyear(day(-1), day(7)).status_code == 200 and send(a1), "작성 기간을 열고 팀원이 제출")
    at = q("SELECT submitted_at FROM kpi_plans WHERE user_id = 42 AND year = ?", year)[0][0]
    item = {"kind": "PLAN", "userId": 42, "name": "가상팀원A1", "dept": "가상K팀A", "title": "프로", "status": "SUBMITTED", "at": at, "reason": None,
            "items": 3, "reg": 2, "irr": 1, "scheds": 2, "proxy": False}
    ok(inbox(lead) == {"items": [item], "team": {**zero, "NONE": 2, "SUBMITTED": 1}, "review": calm},
       "팀장 처리함에 1건 (직급 · 제출 시각 · 행 수) · 우리 팀 상태: 제출 1 · 아직 안 씀 2")
    x("UPDATE users SET writes_kpi = 0 WHERE id = 42")
    ok(inbox(lead)["items"] == [], "KPI 를 안 쓰게 된 사람의 제출은 처리함에서 빠짐")
    x("UPDATE users SET writes_kpi = 1, is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = 42")
    ok(inbox(lead)["items"] == [], "비활성이 된 사람의 제출도 빠짐")
    x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = 42")
    ok(inbox(lead)["items"] == [item], "(되돌리면 다시 처리함에 보임)")
    ok(inbox(a2) == {"items": [], "team": zero, "review": calm} and inbox(b1) == {"items": [], "team": zero, "review": calm}
       and inbox(leadb) == {"items": [], "team": {**zero, "NONE": 2}, "review": calm} and inbox(hr) == {"items": [], "team": zero, "review": calm},
       "다른 팀원 · 다른 팀장 · HR 처리함에는 없음 (HR 은 바로 위 관리자가 아니면 안 옴)")

    ok(all((r := act(c, 42, "confirm")).status_code == 403 and r.json == no_auth for c in (a2, a1, leadb, head, ceo, leadc))
       and all(act(a2, 42, w, {"note": "가상 사유"}).status_code == 403 for w in ("return", "unlock", "reject"))
       and act(lead, 42, "confirm", headers={}).status_code == 403
       and row(42) == [("SUBMITTED", None, None, None, None)] and not versions(42),
       "관리자가_아니면_처리_못_한다: 같은 팀 다른 팀원 · 본인 · 다른 팀장 · 본부장 · 대표이사 403 (Origin 없어도 403) · 아무것도 안 바뀜")

    # ---- 계획서 보기 ----
    mine, owner = me(a1)["plan"], {"id": 42, "name": "가상팀원A1", "dept": "가상K팀A"}
    ok(hr.get("/api/kpi/plans/42", base_url=BASE).json == {**mine, "owner": owner}
       and lead.get("/api/kpi/plans/42", base_url=BASE).json == {**mine, "owner": owner} and a1.get("/api/kpi/plans/42", base_url=BASE).status_code == 200,
       "HR · 관리자 · 주인은 계획서를 봄: 본인 화면의 plan 과 같은 모양 + owner")
    ok(all((r := c.get("/api/kpi/plans/42", base_url=BASE)).status_code == 403 and r.json == {"error": "이 KPI를 볼 권한이 없습니다."}
           for c in (a2, leadb, ceo)), "남의_계획서는_못_본다: 같은 팀 다른 팀원 · 다른 팀장 · 대표이사(팀원은 못 봄) 403")
    ok(head.get("/api/kpi/plans/42", base_url=BASE).json == {**mine, "owner": owner}, "본부장은 본부의 팀원 계획서를 봄 (2단계 ② 에서 넓어진 범위)")
    ok(lead.get("/api/kpi/plans/43", base_url=BASE).json == {"error": "아직 작성한 KPI가 없습니다."}
       and lead.get("/api/kpi/plans/43", base_url=BASE).status_code == 404 and a2.get("/api/kpi/plans/43", base_url=BASE).status_code == 404
       and hr.get("/api/kpi/plans/999", base_url=BASE).status_code == 404 and leadb.get("/api/kpi/plans/43", base_url=BASE).status_code == 403
       and hr.get("/api/kpi/plans/99999999999999999999", base_url=BASE).status_code == 404 and act(hr, 99999999999999999999, "confirm").status_code == 404,
       "계획서가 없으면 404 (볼 권한을 먼저 봄) · 너무 큰 번호도 서버 오류가 아님")

    # ---- 돌려보내기 ----
    for bad in (None, "", "   ", 5, ["x"], "가" * 201, "\ud800", "\x00", "\x00가"):
        r = act(lead, 42, "return", {"note": bad} if bad is not None else {})
        ok(r.status_code == 400 and r.json.get("error") and row(42)[0][0] == "SUBMITTED",
           f"돌려보내는 이유는 1~200자 글자 (UTF-8 로 쓸 수 있는 글자) — 아니면 400 ({bad!r:.12})")
    r = act(lead, 42, "return", {"note": "  가상 돌려보냄 사유  "})
    ok(r.status_code == 200 and r.json == {"ok": True} and row(42) == [("DRAFT", "가상 돌려보냄 사유", "RETURNED", None, None)],
       "돌려보내기 → DRAFT · 사유(앞뒤 공백 뺌) · RETURNED")
    p = me(a1)["plan"]
    ok(p["status"] == "DRAFT" and p["note"] == "가상 돌려보냄 사유" and p["noteKind"] == "RETURNED", "본인 화면에 돌려보낸 사유가 옴")
    ok(audit("KPI_RETURNED", 42) == [(41, 0, None, "kpi_plan", json.dumps(y))] and inbox(lead)["items"] == [],
       "감사로그 KPI_RETURNED (사유 문장 없음) · 처리함에서 사라짐")
    ok(conflict(act(lead, 42, "return", {"note": "가상 사유"}), "DRAFT") and conflict(act(lead, 42, "confirm"), "DRAFT")
       and conflict(act(lead, 42, "return", {}), "DRAFT"), "작성 중인 것은 409 (사유가 틀려도 상태 먼저)")
    ok(send(a1) and row(42) == [("SUBMITTED", None, None, None, None)], "고쳐서 다시 내면 사유가 지워짐")

    # ---- 확정 ----
    r = act(lead, 42, "confirm")
    ok(r.status_code == 200 and r.json == {"ok": True}, "팀장이 확정")
    p = me(a1)["plan"]
    rows_ = [it for g_ in p["confirmed"]["content"] for it in g_["items"]]
    ok(p["status"] == "CONFIRMED" and p["confirmed"]["version"] == 1 and p["confirmed"]["by"] == "가상팀장A"
       and sorted(it["rid"] for it in rows_) == [1, 2, 3] and sorted(s["sid"] for it in rows_ for s in it["scheds"]) == [1, 2],
       "확정 → 판 1 · 확정한 사람 이름 · 각 행에 rid · 각 일정에 sid")
    v1 = p["confirmed"]["content"]
    ok(p["draft"] == v1 and p["note"] is None and p["noteKind"] is None and row(42) == [("CONFIRMED", None, None, None, 1)],
       "초안도 확정본과 같아지고(rid · sid 포함) 사유는 비움")
    ok(versions(42) == [(1, 41, None)] and audit("KPI_CONFIRMED", 42) == [(41, 0, None, "kpi_plan", json.dumps({**y, "version": 1}))],
       "판 1: 누른 사람 = 팀장 · 대신한 사람 없음 · 감사로그 KPI_CONFIRMED (판 번호)")
    r = inbox(lead)
    ok(r["items"] == [] and r["team"] == {**zero, "NONE": 2, "CONFIRMED": 1}, "처리함에서 사라지고 우리 팀 상태는 확정 1")

    # ---- 이미 처리된 것은 409 ----
    ok(all(conflict(act(lead, 42, w, b), "CONFIRMED") for w, b in (("confirm", None), ("unlock", None), ("return", {"note": "가상 사유"}),
                                                                ("reject", {"note": "가상 사유"}), ("return", {}), ("reject", {})))
       and versions(42) == [(1, 41, None)], "이미_처리된_것은_409: 같은 확정 두 번째 · 돌려보내기 · 해제 · 거절 모두 409 (판은 그대로)")
    ok(conflict(act(lead, 43, "confirm"), "NONE") and not q("SELECT 1 FROM kpi_plans WHERE user_id = 43"), "계획서가 없으면 409 (상태 NONE) — 아무것도 안 만들어짐")

    # ---- 수정 요청 → 해제 → 새 판 · 거절 ----
    ok(own(a1, "request-change", {"reason": "\ud800"}).status_code == 400 and row(42)[0][0] == "CONFIRMED", "수정 요청 사유에 쓸 수 없는 글자(짝 없는 서로게이트)가 있으면 400 (서버 오류가 아님)")
    ok(own(a1, "request-change", {"reason": "가상 수정 사유"}).status_code == 200, "팀원이 수정 요청")
    at_cr = q("SELECT updated_at FROM kpi_plans WHERE user_id = 42 AND year = ?", year)[0][0]
    r = inbox(lead)
    ok(r["items"] == [{**item, "status": "CHANGE_REQUESTED", "at": at_cr, "reason": "가상 수정 사유"}] and r["team"]["CHANGE_REQUESTED"] == 1,
       "수정 요청도 처리함에 (사유 · 시각 · 행 수는 확정본 기준)")
    ok(conflict(act(lead, 42, "confirm"), "CHANGE_REQUESTED") and conflict(act(lead, 42, "return", {"note": "가상 사유"}), "CHANGE_REQUESTED"),
       "수정 요청 중에는 확정 · 돌려보내기가 409")
    x("UPDATE kpi_plans SET draft = '[]' WHERE user_id = 42 AND year = ?", year)  # 옛 자료처럼 초안이 확정본과 달라져 있어도
    ok(act(lead, 42, "unlock").json == {"ok": True}, "팀장이 수정을 풀어 줌")
    p = me(a1)["plan"]
    ok(p["status"] == "DRAFT" and p["draft"] == v1 and p["changeReason"] is None and p["note"] is None and p["noteKind"] is None
       and p["confirmed"]["version"] == 1 and p["confirmed"]["content"] == v1 and versions(42) == [(1, 41, None)]
       and row(42) == [("DRAFT", None, None, None, 1)] and audit("KPI_UNLOCKED", 42) == [(41, 0, None, "kpi_plan", json.dumps({**y, "cancelled": 0}))],
       "해제 → 작성 중 · 초안 = 확정본 내용 · 확정본은 판 1 그대로 · 감사로그 KPI_UNLOCKED")
    edited = json.loads(json.dumps(v1))
    edited[0]["items"][0]["action"] = "가상 정기 1 (고침)"
    edited[0]["items"].reverse()  # 순서를 바꿔도 번호는 행을 따라감
    ok(send(a1, edited) and act(lead, 42, "confirm").status_code == 200, "고쳐서 다시 제출 · 확정")
    p = me(a1)["plan"]
    rows_ = [it for g_ in p["confirmed"]["content"] for it in g_["items"]]
    ok(p["confirmed"]["version"] == 2 and p["confirmed"]["content"] == edited
       and {it["action"]: it["rid"] for it in rows_} == {"가상 정기 1 (고침)": 1, "가상 정기 2": 2, "가상 비정기": 3}
       and versions(42) == [(1, 41, None), (2, 41, None)] and json.loads(q("SELECT content FROM kpi_versions WHERE user_id = 42 AND version = 1")[0][0]) == v1,
       "새 판은 판 2 (행 순서를 바꿔도 번호는 행을 따라 이어짐) · 판 1 은 그대로")
    ok(own(a1, "request-change", {"reason": "가상 두 번째 수정 사유"}).status_code == 200, "다시 수정 요청")
    for bad in (None, "", "   ", 5, "가" * 201, "\ud800", "\x00", "\x00가"):
        r = act(lead, 42, "reject", {"note": bad} if bad is not None else {})
        ok(r.status_code == 400 and row(42)[0][0] == "CHANGE_REQUESTED", f"거절 이유도 1~200자 글자 — 아니면 400 ({bad!r:.12})")
    ok(act(lead, 42, "reject", {"note": "가상 거절 사유"}).json == {"ok": True}
       and row(42) == [("CONFIRMED", "가상 거절 사유", "REJECTED", None, 2)], "거절 → 확정 상태로 돌아감 · 사유 · REJECTED · 수정 요청 사유는 비움")
    p = me(a1)["plan"]
    ok(p["status"] == "CONFIRMED" and p["noteKind"] == "REJECTED" and p["note"] == "가상 거절 사유" and p["changeReason"] is None
       and p["confirmed"]["version"] == 2 and p["draft"] == p["confirmed"]["content"] and audit("KPI_REJECTED", 42) == [(41, 0, None, "kpi_plan", json.dumps(y))],
       "본인 화면: 거절 사유 · 확정본 판 2 그대로 · 감사로그 KPI_REJECTED")

    # ---- 작성 기간 (계획서 주인 기준) ----
    ok(send(a2) and who(lead) == [(43, False)], "(팀원 2 제출)")
    ok(setyear("", "").status_code == 200, "HR 이 작성 기간을 닫음")
    ok(act(lead, 43, "confirm").status_code == 403 and act(lead, 43, "confirm").json == no_window
       and act(hr, 43, "confirm").json == no_window and act(hr, 43, "confirm").status_code == 403
       and all(act(lead, 43, w, {"note": "가상 사유"}).json == no_window for w in ("return", "unlock", "reject"))
       and row(43) == [("SUBMITTED", None, None, None, None)] and not versions(43),
       "기간_밖에서는_관리자도_못_한다: 팀장도 HR 도 확정 · 돌려보내기 · 해제 · 거절 403 · 아무것도 안 바뀜")
    ok(act(a1, 43, "confirm").json == no_auth and act(lead, 42, "unlock").json == no_window and act(lead, 42, "unlock").status_code == 403,
       "권한 403 이 기간 403 보다 먼저 · 기간 403 이 상태 409 보다 먼저")

    # ---- 사람별 기간 (HR) ----
    good = {"userId": 43, "year": year, "editFrom": day(-1), "editTo": day(5)}
    ok(not q("SELECT 1 FROM kpi_windows") and setwin(good, lead).status_code == 403 and setwin(good, a2).status_code == 403
       and setwin(good, headers={}).status_code == 403 and setwin(good, anon).status_code == 401 and not q("SELECT 1 FROM kpi_windows"),
       "사람별 기간은 HR 만 · 포털 화면에서만")
    for bad in ({**good, "userId": 999}, {**good, "userId": 5}, {**good, "userId": "43"}, {**good, "userId": True}, {**good, "userId": 0},
                {**good, "userId": 10 ** 30}, {**good, "year": 2025}, {**good, "year": str(year)}, {**good, "editTo": None}, {**good, "editFrom": "내일"},
                {**good, "editFrom": ""}, {**good, "editFrom": day(5), "editTo": day(-1)}, {"userId": 43, "year": year}, {"userId": 43, "year": year, "clear": False}, []):
        ok(setwin(bad).status_code == 400 and not q("SELECT 1 FROM kpi_windows"), f"사람별 기간이 틀리면 400, 아무것도 안 남김 ({str(bad)[:70]})")
    out_of_year = f"{year}년 기간에는 {year}년 날짜만 넣을 수 있어요. {year + 1}년 기간은 {year + 1}년 1월 1일부터 정할 수 있어요."
    for bad in ({**good, "editTo": f"{year + 1}-01-23"}, {**good, "editFrom": f"{year + 1}-01-05", "editTo": f"{year + 1}-01-23"},
                {**good, "editFrom": f"{year - 1}-12-31"}):
        r = setwin(bad)
        ok(r.status_code == 400 and r.json == {"error": out_of_year} and not q("SELECT 1 FROM kpi_windows"),
           f"사람별 기간도 그해 밖의 날짜는 400 ({str(bad)[:70]})")
    ok(setwin(good).json == {"ok": True} and q("SELECT edit_from, edit_to, set_by FROM kpi_windows WHERE user_id = 43 AND year = ?", year) == [(day(-1), day(5), 1)],
       "HR 이 한 팀원에게만 오늘을 포함한 기간을 엶")
    ok(own(a2, "cancel").status_code == 200 and own(a2, "submit").status_code == 200 and row(43)[0][0] == "SUBMITTED",
       "사람별_기간이_우선이다: 전체 기간이 닫혀 있어도 그 팀원은 제출 200")
    ok(own(a1, "request-change", {"reason": "가상 사유"}).status_code == 403 and own(b1, "submit").status_code == 403 and own(a3, "draft", {"draft": mk()}).status_code == 403,
       "다른 팀원은 403 (전체 기간이 닫혀 있음)")
    ok(act(lead, 43, "confirm").status_code == 200 and versions(43) == [(1, 41, None)], "관리자 처리도 그 팀원의 기간으로 봄: 전체 기간이 닫혀 있어도 확정")
    ok(setwin({"userId": 43, "year": year, "clear": True}).json == {"ok": True} and not q("SELECT 1 FROM kpi_windows")
       and [r[3:] for r in audit("KPI_WINDOW_SET", 43)] == [("kpi_window", json.dumps({**y, "editFrom": day(-1), "editTo": day(5)})),
                                                            ("kpi_window", json.dumps({**y, "editFrom": None, "editTo": None}))]
       and audit("KPI_WINDOW_SET", 43)[0][:3] == (1, 1, None),
       "기간 지우기 (clear) · 감사로그 KPI_WINDOW_SET 은 대상 번호 · 해 · 날짜 (지우면 날짜가 둘 다 null 이라 켠 것과 구분됨)")
    ok(setyear(day(-1), day(7)).status_code == 200, "(작성 기간을 다시 엶)")

    # ---- 동시에 두 번 누르기 ----
    ok(own(a2, "request-change", {"reason": "가상 사유"}).status_code == 200 and act(lead, 43, "unlock").status_code == 200 and own(a2, "submit").status_code == 200,
       "(수정 요청 → 해제 → 다시 제출)")
    crowd = [app.test_client() for _ in range(5)]  # 같은 팀장이 창을 여러 개 열어 놓고 동시에 확정을 누른다
    for c in crowd:
        login(c, new_token("HW-41"))
    codes = []
    threads = [threading.Thread(target=lambda c=c: codes.append(act(c, 43, "confirm").status_code)) for c in crowd]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    ok(sorted(codes) == [200, 409, 409, 409, 409] and versions(43) == [(1, 41, None), (2, 41, None)] and len(audit("KPI_CONFIRMED", 43)) == 2,
       f"동시에 확정을 다섯 번 눌러도 한 번만 통과 — 판도 하나만 ({codes})")

    # ---- HR 이 대신 ----
    ok(send(b1) and who(hr) == [], "(팀장 B 의 팀원 제출) HR 은 바로 위 관리자가 아니라서 처리함에 안 옴")
    ok(act(hr, 45, "confirm").status_code == 200 and versions(45) == [(1, 1, 44)] and me(b1)["plan"]["confirmed"]["by"] == "가상1"
       and audit("KPI_CONFIRMED", 45) == [(1, 1, 44, "kpi_plan", json.dumps({**y, "version": 1}))],
       "HR이_대신_확정하면_원래_관리자가_남는다: 판 · 감사로그에 on_behalf_of = 팀장 · 확정한 사람 이름은 HR")
    ok(own(b1, "request-change", {"reason": "가상 사유"}).status_code == 200 and act(hr, 45, "reject", {"note": "가상 거절"}).status_code == 200
       and own(b1, "request-change", {"reason": "가상 사유"}).status_code == 200 and act(hr, 45, "unlock").status_code == 200
       and own(b1, "submit").status_code == 200 and act(hr, 45, "return", {"note": "가상 돌려보냄"}).status_code == 200
       and row(45) == [("DRAFT", "가상 돌려보냄", "RETURNED", None, 1)], "HR 이 거절 · 해제 · 돌려보내기도 대신 함")
    ok(all(audit(a, 45)[-1][:3] == (1, 1, 44) for a in ("KPI_REJECTED", "KPI_UNLOCKED", "KPI_RETURNED")), "그 감사로그에도 on_behalf_of = 팀장")

    # ---- 관리자 본인이 HR 이기도 하면 관리자로 ----
    x("INSERT INTO user_roles (user_id, role) VALUES (44, 'HR_ADMIN')")
    ok(send(b2) and act(leadb, 49, "confirm").status_code == 200 and versions(49) == [(1, 44, None)]
       and audit("KPI_CONFIRMED", 49) == [(44, 1, None, "kpi_plan", json.dumps({**y, "version": 1}))],
       "HR 권한이 있는 팀장이 자기 팀원을 확정하면 관리자로 처리 (on_behalf_of 없음)")
    x("DELETE FROM user_roles WHERE user_id = 44 AND role = 'HR_ADMIN'")

    # ---- 대표이사 몫 · 맡기기 ----
    ok(send(head) and me(head)["manager"] == {"id": 47, "name": "가상대표"} and me(hr)["manager"] == {"id": 47, "name": "가상대표"},
       "(본부장 제출) 본부장 · HR 의 바로 위 관리자는 대표이사")
    ok(act(hr, 46, "confirm").status_code == 403 and act(hr, 46, "confirm").json == no_auth and who(hr) == [] and row(46)[0][0] == "SUBMITTED",
       "대표이사_몫은_맡겨야_대신한다: 맡기기 전에는 HR 도 403 · HR 처리함에도 안 옴")
    ok(send(hr), "(HR 도 본인 계획서를 제출)")
    ok(set(who(ceo)) == {(46, False), (1, False)}, "대표이사 처리함에는 본부장 · HR 의 계획서")
    x("UPDATE kpi_plans SET submitted_at = '2000-01-01T00:00:00Z' WHERE user_id = 46 AND year = ?", year)
    ok(who(ceo) == [(46, False), (1, False)], "처리함은 오래된 제출부터")
    x("UPDATE kpi_plans SET submitted_at = '1999-01-01T00:00:00Z' WHERE user_id = 1 AND year = ?", year)
    ok(who(ceo) == [(1, False), (46, False)], "(제출 시각이 바뀌면 순서도 바뀜)")
    ok(act(hr, 1, "confirm").status_code == 403 and act(hr, 1, "confirm").json == no_auth, "HR은_본인_것을_대신_못_한다")

    ok(all(c.get("/api/kpi/delegation", base_url=BASE).status_code == 403 and delegate(True, c).status_code == 403 for c in (hr, head, lead, a1))
       and not q("SELECT 1 FROM ceo_delegation"), "대표이사가 아니면 맡길 수 없음 (HR 이 스스로 켤 수도 없음)")
    ok(ceo.get("/api/kpi/delegation", base_url=BASE).json == {"on": False}, "처음엔 꺼짐")
    ok(all(ceo.post("/api/kpi/delegation", base_url=BASE, headers=ORIGIN, json=b).status_code == 400 for b in ({}, {"on": "true"}, {"on": 1}, {"on": [True]}))
       and delegate(True, headers={}).status_code == 403 and not q("SELECT 1 FROM ceo_delegation"), "on 은 true / false 만 · Origin 없으면 403")
    ok(delegate(True).json == {"ok": True, "on": True} and ceo.get("/api/kpi/delegation", base_url=BASE).json == {"on": True}, "대표이사가 맡김을 켬")
    since = q("SELECT ceo_user_id, since FROM ceo_delegation")
    ok(len(since) == 1 and since[0][0] == 47 and len(since[0][1]) == 20 and since[0][1].endswith("Z"), "맡긴 시각이 남음")
    ok(delegate(True).status_code == 200 and q("SELECT ceo_user_id, since FROM ceo_delegation") == since
       and audit("CEO_DELEGATION_ON", 47) == [(47, 0, None, "ceo_delegation", None)], "켜기는 여러 번 해도 같음 · 감사로그 CEO_DELEGATION_ON 은 한 번")
    ok(who(hr) == [(46, True)] and set(who(ceo)) == {(46, False), (1, False)},
       "맡기면 HR 처리함에 '대신'으로 옴 (HR 본인 것은 빼고) · 대표이사 처리함은 그대로")
    x("UPDATE users SET writes_kpi = 0 WHERE id = 46")
    ok(who(hr) == [] and who(ceo) == [(1, False)], "KPI 를 안 쓰게 된 사람의 것은 대신 처리함에서도 대표이사 처리함에서도 빠짐")
    x("UPDATE users SET writes_kpi = 1 WHERE id = 46")
    ok(who(hr) == [(46, True)], "(되돌리면 다시 보임)")
    ok(act(hr, 1, "confirm").status_code == 403, "맡겨도 HR 은 본인 것을 못 함")
    ok(act(hr, 46, "confirm").status_code == 200 and versions(46) == [(1, 1, 47)]
       and audit("KPI_CONFIRMED", 46) == [(1, 1, 47, "kpi_plan", json.dumps({**y, "version": 1}))], "맡기면 HR 이 대신 확정: on_behalf_of = 대표이사")
    ok(act(ceo, 1, "confirm").status_code == 200 and versions(1) == [(1, 47, None)], "대표이사 본인은 맡겨도 직접 확정할 수 있음 (on_behalf_of 없음)")
    ok(own(head, "request-change", {"reason": "가상 사유"}).status_code == 200 and who(hr) == [(46, True)] and inbox(hr)["items"][0]["status"] == "CHANGE_REQUESTED",
       "(수정 요청도 대신 처리함에)")
    ok(delegate(False).json == {"ok": True, "on": False} and not q("SELECT 1 FROM ceo_delegation") and delegate(False).status_code == 200
       and audit("CEO_DELEGATION_OFF", 47) == [(47, 0, None, "ceo_delegation", None)], "끄기 · 감사로그 CEO_DELEGATION_OFF")
    ok(act(hr, 46, "unlock").json == no_auth and act(hr, 46, "unlock").status_code == 403 and who(hr) == [], "끄면 다시 HR 이 못 함 · 처리함에서도 사라짐")
    ok(act(ceo, 46, "unlock").status_code == 200 and audit("KPI_UNLOCKED", 46) == [(47, 0, None, "kpi_plan", json.dumps({**y, "cancelled": 0}))], "대표이사가 직접 풀어 줌")

    # ---- 바로 위 관리자가 없으면 HR ----
    ok(send(leadc) and me(leadc)["manager"] == {"id": 47, "name": "가상대표"}, "(본부장이 없는 본부의 팀장 제출 — 바로 위는 대표이사)")
    x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = 47")
    ok(me(leadc)["manager"] is None and who(hr) == [(50, True)], "대표이사가 없으면 바로 위 관리자가 없음 → HR 처리함에 '대신'")
    ok(act(hr, 50, "confirm").status_code == 200 and versions(50) == [(1, 1, None)] and audit("KPI_CONFIRMED", 50)[0][:3] == (1, 1, None),
       "관리자가 없는 것은 HR 이 확정 (on_behalf_of 없음)")
    x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = 47")

    # ---- 팀을 옮기면 ----
    ok(send(a3) and who(lead) == [(48, False)], "(팀원 3 제출)")
    x("UPDATE users SET department_id = 33 WHERE id = 48")
    r = inbox(lead)
    ok(act(lead, 48, "confirm").status_code == 403 and act(lead, 48, "confirm").json == no_auth and r["items"] == [] and r["team"] == {**zero, "CONFIRMED": 2},
       "팀을_옮기면_새_관리자가_처리한다: 옛 팀장은 403 · 처리함에서도 사라짐")
    r = inbox(leadb)
    ok([(i["userId"], i["dept"]) for i in r["items"]] == [(48, "가상K팀B")] and r["team"] == {**zero, "DRAFT": 1, "CONFIRMED": 1, "SUBMITTED": 1},
       "새 팀장 처리함에 1건")
    ok(act(leadb, 48, "confirm").status_code == 200 and versions(48) == [(1, 44, None)], "새 팀장이 확정")

    # ---- HR 한눈에 보기 ----
    r = hr.get("/api/kpi/admin", base_url=BASE)
    people = {p["userId"]: p for p in r.json["people"]}
    ok(r.status_code == 200 and r.json["year"] == year and r.json["window"] == {"from": day(-1), "to": day(7), "evalStart": None, "open": True},
       "HR 목록: 해 · 올해 작성 기간 · 열려 있는지 (서버가 정한다)")
    hr.post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN, json={"year": year, "editFrom": day(-1), "editTo": day(7), "evalStart": day(0)})
    ok(hr.get("/api/kpi/admin", base_url=BASE).json["window"] == {"from": day(-1), "to": day(7), "evalStart": day(0), "open": False},
       "평가 시작일부터는 HR 목록에도 닫혀 있다고 나옴")
    ok(setyear(day(-1), day(7)).status_code == 200, "(평가 시작일을 비우고 다시 엶)")
    ok(people[42] == {"userId": 42, "name": "가상팀원A1", "dept": "가상K팀A", "manager": {"id": 41, "name": "가상팀장A"},
                      "status": "CONFIRMED", "confirmedVersion": 2, "window": None, "canAct": True}
       and people[45]["status"] == "DRAFT" and people[45]["confirmedVersion"] == 1
       and people[41]["status"] == "NONE" and people[41]["confirmedVersion"] is None and people[41]["manager"] == {"id": 46, "name": "가상본부장"}
       and people[46]["manager"] == {"id": 47, "name": "가상대표"} and people[1]["status"] == "CONFIRMED",
       "사람마다 이름 · 부서 · 바로 위 관리자 · 상태 · 확정한 판")
    ok(people[41]["canAct"] is True and people[1]["canAct"] is False and people[46]["canAct"] is False and people[50]["canAct"] is False,
       "canAct: 보통 관리자 몫은 HR 이 대신 처리 · HR 본인 것 · 맡기지 않은 대표이사 몫은 못 함 (서버 decider 와 같게)")
    can = lambda uid: {p["userId"]: p["canAct"] for p in hr.get("/api/kpi/admin", base_url=BASE).json["people"]}[uid]  # noqa: E731
    delegate(True)
    ok(can(46) is True and can(1) is False, "대표이사가 맡기면 그 몫은 canAct · HR 본인 것은 그래도 못 함")
    delegate(False)
    x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = 47")
    ok(can(50) is True and can(46) is True, "관리자가 없으면 HR 이 처리 (canAct)")
    x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = 47")
    ok(can(46) is False, "(대표이사를 되돌리면 다시 못 함)")
    ok(not {47, 5, 51, 52} & set(people) and all(p["window"] is None for p in people.values()), "KPI 를 안 쓰는 사람(대표이사 · 현장직) · 비활성인 사람은 빠짐 · 사람별 기간 없으면 null")
    setwin({"userId": 49, "year": year, "editFrom": day(-1), "editTo": day(3)})
    ok({p["userId"]: p["window"] for p in hr.get("/api/kpi/admin", base_url=BASE).json["people"] if p["window"]}
       == {49: {"from": day(-1), "to": day(3), "evalStart": None, "open": True}}, "사람별 기간이 있으면 그 사람 줄에 (열려 있는지도)")
    setwin({"userId": 49, "year": year, "clear": True, "editFrom": day(-1), "editTo": day(3)})  # 지우면서 날짜를 같이 보내도 기록은 null
    ok(not q("SELECT 1 FROM kpi_windows WHERE user_id = 49") and [r[4] for r in audit("KPI_WINDOW_SET", 49)]
       == [json.dumps({**y, "editFrom": day(-1), "editTo": day(3)}), json.dumps({**y, "editFrom": None, "editTo": None})],
       "지우면서 날짜를 같이 보내도 감사로그의 날짜는 null")
    r = hr.get("/api/kpi/admin?year=2090", base_url=BASE)
    ok(r.json["window"] == {"from": None, "to": None, "evalStart": None, "open": False} and all(p["status"] == "NONE" and p["confirmedVersion"] is None for p in r.json["people"]),
       "?year= 로 다른 해를 봄")
    ok(all(hr.get(f"/api/kpi/admin?year={v}", base_url=BASE).status_code == 400 for v in ("abc", "2025", ""))
       and lead.get("/api/kpi/admin", base_url=BASE).status_code == 403 and ceo.get("/api/kpi/admin", base_url=BASE).status_code == 403,
       "틀린 해는 400 · HR 이 아니면 403")

    # ---- 감사로그 ----
    acts = {r[0] for r in q("SELECT action FROM audit_logs WHERE action LIKE 'KPI_%' OR action LIKE 'CEO_%'")}
    ok({"KPI_CONFIRMED", "KPI_RETURNED", "KPI_UNLOCKED", "KPI_REJECTED", "KPI_WINDOW_SET", "CEO_DELEGATION_ON", "CEO_DELEGATION_OFF"} <= acts,
       "감사로그 행동 이름: 확정 · 돌려보내기 · 해제 · 거절 · 사람별 기간 · 맡기기 켜기 / 끄기")
    ok(q("SELECT COUNT(*) FROM audit_logs WHERE (action LIKE 'KPI_%' OR action LIKE 'CEO_%')"
         " AND (IFNULL(before_data, '') || IFNULL(after_data, '') || IFNULL(reason, '')) LIKE '%가상%'") == [(0,)], "계획서 · 사유 문장은 감사로그에 없음")


def check_kpi_progress(hr) -> None:
    """KPI 진행 기록 (2026-10-21): 확정한 계획서에 실적 · 진행률 · 월 한 줄 · 결과 한 줄 · 늦어지는 이유를 적고, 읽으면 숫자가 계산돼 나온다 ·
    서버가 순서대로 다시 본다 (KPI 작성 대상 403 → 확정본 404 → 기록 기간 403 → 칸 400) · 날짜는 한국 자정 기준.
    오늘에 기대는 검사는 모두 시계(auth._now)를 정해 두고 돈다 — 어느 날 돌려도 같다 (R4). 시계가 앞뒤로 가므로 창은 시계를 바꿀 때마다 새로 연다."""
    import kpi
    print("KPI 진행 기록")
    # 이 검사만의 가상 조직: 본부(61) 밑 팀(62) · 다른 팀(63)
    for dept in ((61, "P61", "가상P본부", "DIVISION", None), (62, "P62", "가상P팀", "TEAM", 61), (63, "P63", "가상P팀2", "TEAM", 61)):
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (?, ?, ?, ?, ?)", *dept)
    EMP, LEAD, PEER, OTHER, HEAD, FIELD, HR = 62, 61, 63, 64, 65, 66, 1
    for uid, (name, dept, role, writes) in {LEAD: ("가상P팀장", 62, "TEAM_LEADER", 1), EMP: ("가상P직원", 62, None, 1), PEER: ("가상P동료", 62, None, 1),
                                            OTHER: ("가상P다른팀장", 63, "TEAM_LEADER", 1), HEAD: ("가상P본부장", 61, "DIVISION_HEAD", 1),
                                            FIELD: ("가상P현장직", 62, None, 0)}.items():
        x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, writes_kpi) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
          uid, name, f"p{uid}@example.invalid", dept, f"HW-{uid}", writes)
        if role:
            x("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", uid, role)

    year, real_now = kpi.plan_year(), auth._now  # 진짜 오늘의 올해 — 아래 날짜는 모두 이 해 안 (시계를 바꿔 가며 돈다)
    ymd = lambda m, d: f"{year}-{m:02d}-{d:02d}"  # noqa: E731
    near = lambda a, b: abs(a - b) < 1e-9  # noqa: E731
    jar: dict[int, object] = {}

    def at(y, m, d, hh=3, mm=0):
        """시계를 UTC 로 (y-m-d hh:mm) 에 맞춘다. 기본 03:00 = 한국 정오."""
        t = datetime(y, m, d, hh, mm, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    get = lambda uid=EMP, who=EMP, y=year: c(who).get(f"/api/kpi/progress/{uid}?year={y}", base_url=BASE)  # noqa: E731
    prog = lambda: get().json  # noqa: E731
    row = lambda p, rid: next(i for i in p["items"] if i["rid"] == rid)  # noqa: E731
    sched = lambda p, sid: next(s for i in p["items"] for s in i.get("scheds", []) if s["sid"] == sid)  # noqa: E731
    put = lambda what, body, who=EMP, headers=ORIGIN: c(who).post(f"/api/kpi/me/{what}", base_url=BASE, headers=headers, json={"year": year, **body})  # noqa: E731
    own = lambda who, what, body=None: c(who).post(f"/api/kpi/me/{what}", base_url=BASE, headers=ORIGIN, json=body or {})  # noqa: E731
    act = lambda who, what: c(who).post(f"/api/kpi/plans/{EMP}/{what}", base_url=BASE, headers=ORIGIN, json={})  # noqa: E731
    err = lambda r, code, msg: r.status_code == code and r.json == {"error": msg}  # noqa: E731
    base = lambda r: {k: r[k] for k in ("rid", "kind", "action", "weight", "target", "unit", "actual", "pct", "why", "at")}  # noqa: E731
    cells = lambda r: "".join({"NA": "-", "DONE": "d", "OPEN": "o", "MISSED": "x", "FUTURE": "f"}[r["months"][str(m)]["state"]] for m in range(1, 13))  # noqa: E731
    counts = lambda: [q(f"SELECT COUNT(*) FROM {t} WHERE user_id = ?", EMP)[0][0] for t in ("kpi_progress", "kpi_month_notes", "kpi_results")]  # noqa: E731
    no_kpi, no_plan, closed, not_now = "KPI 작성 대상이 아닙니다.", "확정된 KPI가 없습니다.", "기록 기간이 지났습니다.", "이 달은 적는 기간이 아닙니다."
    no_rid, no_sid = "지금 확정본에 없는 실행목표입니다.", "지금 확정본에 없는 일정입니다."
    link_ok = "https://example.invalid/근거"

    def item(name, w, kind, **more):
        return {"action": name, "formula": "가상 측정식", "weight": w, "owner": "가상P직원", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
                "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}

    plan = [{"team": "가상 팀목표", "personal": "가상 개인목표", "items": [
        item("가상 정기 업무 1", 40, "reg", target=12, unit="건"), item("가상 정기 업무 2", 30, "reg"),
        item("가상 비정기 업무", 30, "irr", scheds=[{"due": ymd(5, 20), "name": "가상 초안"}, {"due": ymd(6, 5), "name": "가상 검토"},
                                               {"due": ymd(9, 30), "name": "가상 마감"}])]}]
    saved = q("SELECT year, edit_from, edit_to, eval_start, updated_at, updated_by FROM kpi_years WHERE year = ?", year)
    try:
        r = hr.post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN, json={"year": year, "editFrom": ymd(1, 1), "editTo": ymd(12, 31), "evalStart": None})
        ok(r.status_code == 200, "(작성 기간을 그해 내내 열어 둠 — 시계를 바꿔 가며 계획서를 확정하려고)")

        # ---- 한국 3월 10일: 확정 전 ----
        at(year, 3, 10)
        bodies = {"progress": {"rid": 1, "actual": 1}, "month": {"rid": 1, "month": 3, "text": "가상"}, "result": {"sid": 1, "text": "가상"}}
        ok(all(err(put(w, b, FIELD), 403, no_kpi) for w, b in bodies.items()),
           "KPI_작성이_꺼진_사람은_못_쓴다: 세 곳 모두 403 (확정본이 있고 없고를 보기 전에)")
        ok(all(err(put(w, b), 404, no_plan) for w, b in bodies.items()) and all(err(get(EMP, who), 404, no_plan) for who in (EMP, LEAD, HR)),
           "확정_전에는_못_쓴다: 계획서가 아예 없으면 쓰기도 읽기도 404")
        ok(own(EMP, "draft", {"draft": plan}).status_code == 200 and own(EMP, "submit").status_code == 200
           and all(err(put(w, b), 404, no_plan) for w, b in bodies.items()) and err(get(), 404, no_plan) and counts() == [0, 0, 0],
           "제출만 하고 확정 전이어도 404 · 아무것도 안 쓰임")
        ok(act(LEAD, "confirm").status_code == 200 and q("SELECT version, confirmed_at FROM kpi_versions WHERE user_id = ?", EMP) == [(1, f"{year}-03-10T03:00:00Z")],
           "(팀장이 확정 — 판 1, 한국 3월 10일)")
        ok(all(put(w, {"rid": 99, "sid": 99, "month": 3, "actual": 1, "text": "가상"}, headers={}).status_code == 403 for w in bodies), "포털 화면에서만 (Origin 없으면 403)")

        # ---- 달이 열리고 닫히는 때는 한국 자정 ----
        at(year, 5, 31, 14, 59)  # 한국 5월 31일 23:59
        ok(err(put("month", {"rid": 1, "month": 6, "text": "가상 6월"}), 403, not_now),
           "다음_달은_못_쓴다: 한국 5월 31일 23:59 에는 6월 줄이 아직 403")
        at(year, 5, 31, 15, 0)  # 한국 6월 1일 00:00
        ok(put("month", {"rid": 1, "month": 6, "text": "가상 6월"}).status_code == 200 and put("month", {"rid": 1, "month": 6, "text": ""}).status_code == 200
           and counts() == [0, 0, 0], "한국 6월 1일 0시가 되면 6월 줄이 열림 (UTC 로는 아직 5월 31일) — 써 보고 지움")
        at(year, 6, 7, 14, 59)  # 한국 6월 7일 23:59
        ok(put("month", {"rid": 1, "month": 5, "text": "가상 5월 줄"}).json == {"ok": True},
           "월_한_줄_마감은_한국_자정: 6월 7일 UTC 14:59 (한국 23:59) 에는 5월 줄이 200")
        at(year, 6, 7, 15, 0)  # 한국 6월 8일 00:00
        ok(err(put("month", {"rid": 1, "month": 5, "text": "가상 고침"}), 403, not_now) and err(put("month", {"rid": 1, "month": 5, "text": ""}), 403, not_now)
           and q("SELECT text, updated_at FROM kpi_month_notes WHERE user_id = ?", EMP) == [("가상 5월 줄", f"{year}-06-07T14:59:00Z")],
           "UTC 15:00 (한국 6월 8일 0시) 에는 5월 줄을 고치지도 지우지도 못함 · 쓴 줄은 그대로")

        # ---- 한국 6월 15일: 읽기 ----
        at(year, 6, 15)
        audit_n = q("SELECT COUNT(*) FROM audit_logs WHERE action <> 'LOGIN'")
        p = prog()
        days = (date(year, 6, 15) - date(year, 1, 1)).days + 1
        exp_reg = days / (date(year + 1, 1, 1) - date(year, 1, 1)).days * 100
        r1, r2, r3 = row(p, 1), row(p, 2), row(p, 3)
        ok(p["year"] == year and p["version"] == 1 and p["open"] is True and p["schedOpen"] is True and [i["rid"] for i in p["items"]] == [1, 2, 3]
           and set(p) == {"year", "version", "open", "schedOpen", "items", "total", "totalExpected", "todo", "next", "checkStep", "checks", "gap", "alarm"},
           "읽기: 해 · 판 · 기록 기간 · 일정 변경 기간 · 행 순서 · 월간 확인 단계 · 확인한 달 · 알람 기준 · 부진 알람")
        ok(base(r1) == {"rid": 1, "kind": "reg", "action": "가상 정기 업무 1", "weight": 40, "target": 12, "unit": "건", "actual": None, "pct": None, "why": None, "at": None}
           and base(r2) == {"rid": 2, "kind": "reg", "action": "가상 정기 업무 2", "weight": 30, "target": None, "unit": "", "actual": None, "pct": None, "why": None, "at": None}
           and base(r3) == {"rid": 3, "kind": "irr", "action": "가상 비정기 업무", "weight": 30, "target": None, "unit": "", "actual": None, "pct": None, "why": None, "at": None}
           and r1["value"] == 0 and r2["value"] == 0 and r3["value"] == 0, "행마다 종류 · 실행목표 · 가중치 · 목표 · 단위 · 쓴 값 (아직 없음) · 진행률 0")
        ok("months" in r1 and "months" in r2 and "months" not in r3 and "scheds" not in r1 and "scheds" not in r2 and len(r3["scheds"]) == 3
           and set(r1["months"]) == {str(m) for m in range(1, 13)}, "월 칸은 정기에만 (1~12월) · 일정은 비정기에만")
        ok(cells(r1) == "--xxd" + "o" + "f" * 6 and cells(r2) == "--xxx" + "o" + "f" * 6
           and r1["months"]["5"] == {"state": "DONE", "text": "가상 5월 줄", "link": None, "at": f"{year}-06-07T14:59:00Z"}
           and r1["months"]["6"] == {"state": "OPEN", "text": None, "link": None, "at": None},
           "달 상태: 첫 확정(3월) 전은 해당 없음 · 지난 달은 못 쓴 것 · 이번 달은 쓸 수 있음 · 앞 달은 아직 · 쓴 달은 완료 (글 · 링크 · 시각)")
        s1, s2, s3 = (sched(p, i) for i in (1, 2, 3))
        ok([(s["sid"], s["name"], s["task"], s["due"], s["origDue"], s["state"], s["result"], s["pending"], s["returned"]) for s in (s1, s2, s3)]
           == [(1, "가상 초안", "KPI- 가상 초안", ymd(5, 20), ymd(5, 20), "OVERDUE", None, None, None),
               (2, "가상 검토", "KPI- 가상 검토", ymd(6, 5), ymd(6, 5), "LATE", None, None, None),
               (3, "가상 마감", "KPI- 가상 마감", ymd(9, 30), ymd(9, 30), "UPCOMING", None, None, None)],
           "일정마다 이름 · 나의 업무 제목 · 기한 · 확정본 기한 · 상태 (14일 안은 주황 · 그 뒤는 빨강)")
        ok(near(r1["expected"], exp_reg) and near(r2["expected"], exp_reg) and near(r3["expected"], 100 / 3) and near(p["total"], 0)
           and near(p["totalExpected"], (70 * exp_reg + 30 * 100 / 3) / 100), "기대 진행률: 정기는 그해 날수 비율 · 비정기는 14일 넘게 지난 일정 비율 · 전체는 가중 평균")
        ok(p["todo"] == {"months": 2, "prev": 0, "results": 2} and p["next"] == {"sid": 3, "due": ymd(9, 30), "name": "가상 마감"},
           "할 일: 이번 달 쓸 칸 2 · 지난달 0 (7일이 지남) · 결과 안 쓴 늦은 일정 2 · 다음 일정 = 가장 가까운 남은 것")
        ok(c(EMP).get(f"/api/kpi/progress/{EMP}", base_url=BASE).json == p and get(y="abc").status_code == 400 and get(y="2025").status_code == 400
           and err(get(y=2090), 404, no_plan), "?year= 가 없으면 올해 · 틀린 해는 400 · 계획서 없는 해는 404")

        # ---- 일정 변경 기간 · 기록은 작성 기간과 상관없다 ----
        yr = lambda sql, *a: x(f"UPDATE kpi_years SET {sql} WHERE year = ?", *a, year)  # noqa: E731
        yr("eval_start = ?", ymd(6, 15))
        ok(prog()["schedOpen"] is False and prog()["open"] is True, "평가 시작일이 오늘이면 일정 변경 기간은 닫힘 (schedOpen false) — 기록 기간은 그대로")
        yr("eval_start = ?", ymd(6, 16))
        ok(prog()["schedOpen"] is True, "평가 시작일이 내일이면 아직 열림")
        yr("eval_start = ?, edit_from = NULL, edit_to = NULL", ymd(6, 15))
        ok(put("progress", {"rid": 1, "actual": 3}).status_code == 200 and prog()["schedOpen"] is False,
           "작성 기간이 닫히고 평가가 시작돼도 진행 기록은 쓰임 (작성 기간 · 평가 시작일과 상관없다)")
        yr("eval_start = NULL, edit_from = ?, edit_to = ?", ymd(1, 1), ymd(12, 31))

        # ---- 실적 · 진행률 ----
        ok(put("progress", {"rid": 1, "actual": 6}).json == {"ok": True}, "실적을 씀")
        p = prog()
        ok(row(p, 1)["actual"] == 6 and near(row(p, 1)["value"], 50) and near(p["total"], 20) and row(p, 1)["at"] == f"{year}-06-15T03:00:00Z",
           "실적_쓰면_진행률이_바뀐다: 목표 12에 실적 6 → value 50 · 전체 20 · 시각이 남음")
        ok(put("progress", {"rid": 1, "actual": 15}).status_code == 200 and near(row(prog(), 1)["value"], 100) and near(prog()["total"], 40),
           "실적이 목표를 넘으면 막대는 100 에서 멈춤")
        ok(all(put("progress", {"rid": 1, "actual": v}).status_code == 200 and row(prog(), 1)["actual"] == v for v in (0, 2.5, 1e9)),
           "실적 0 · 소수 · 10억은 됨")
        ok(put("progress", {"rid": 1, "actual": 6}).status_code == 200, "(실적을 6 으로 되돌림)")
        ok(put("progress", {"rid": 2, "pct": 70}).json == {"ok": True} and near(row(prog(), 2)["value"], 70) and row(prog(), 2)["pct"] == 70
           and near(prog()["total"], 41), "목표_없는_정기는_진행률을_직접: pct 70 → value 70 · 전체 41")
        ok(all(put("progress", {"rid": 2, "pct": v}).status_code == 200 and row(prog(), 2)["pct"] == v for v in (0, 100)) and put("progress", {"rid": 2, "pct": 70}).status_code == 200,
           "진행률 0 · 100 은 됨")
        wrong = ({"rid": 2, "actual": 5}, {"rid": 1, "pct": 50}, {"rid": 3, "actual": 1}, {"rid": 3, "pct": 1}, {"rid": 2, "actual": None}, {"rid": 1, "pct": None})
        ok(all((r := put("progress", b)).status_code == 400 and r.json.get("error") for b in wrong) and row(prog(), 2)["pct"] == 70 and row(prog(), 1)["actual"] == 6,
           "목표 없는 정기에 실적 · 목표 있는 정기에 진행률 · 비정기에 둘 다 → 400 (값은 그대로)")
        bad_n = ({"rid": 1, "actual": -1}, {"rid": 1, "actual": True}, {"rid": 1, "actual": "6"}, {"rid": 1, "actual": 1e9 + 1000}, {"rid": 1, "actual": float("nan")},
                 {"rid": 1, "actual": float("inf")}, {"rid": 1, "actual": 10 ** 400}, {"rid": 1, "actual": [6]}, {"rid": 2, "pct": 70.5}, {"rid": 2, "pct": 70.0},
                 {"rid": 2, "pct": True}, {"rid": 2, "pct": 101}, {"rid": 2, "pct": -1}, {"rid": 2, "pct": "70"})
        ok(all(put("progress", b).status_code == 400 for b in bad_n) and row(prog(), 1)["actual"] == 6 and row(prog(), 2)["pct"] == 70,
           "숫자가 틀리면 400: 음수 · 불리언 · 글자 · 10억 초과 · NaN · 무한 · 소수 진행률 · 0~100 밖")
        ok(all(err(put("progress", b), 400, no_rid) for b in ({"rid": 99, "actual": 1}, {"rid": "1", "actual": 1}, {"rid": True, "actual": 1}, {"rid": 1.0, "actual": 1},
                                                               {"rid": None, "actual": 1}, {"actual": 1}, {"rid": 0, "actual": 1}))
           and put("progress", {"rid": 1}).status_code == 400 and put("progress", {"rid": 1, "extra": 5}).status_code == 400,
           "없는 실행목표 (번호 모양이 틀린 것 포함) · 바꿀 칸이 없음 → 400")
        ok(put("progress", {"rid": 1, "actual": None}).status_code == 200 and row(prog(), 1)["actual"] is None and near(row(prog(), 1)["value"], 0)
           and row(prog(), 1)["at"] is None and counts()[0] == 1 and put("progress", {"rid": 1, "actual": 6}).status_code == 200,
           "실적_비우기: actual null → 비움 (값 0 · 비어 남은 줄은 지움) — 화면에서 숫자 칸을 지울 수 있게 (다시 6 으로)")
        ok(put("progress", {"rid": 2, "pct": None}).status_code == 200 and row(prog(), 2)["pct"] is None and near(row(prog(), 2)["value"], 0)
           and put("progress", {"rid": 2, "pct": 70}).status_code == 200 and row(prog(), 2)["pct"] == 70,
           "진행률_비우기: pct null → 비움 (다시 70 으로)")

        # ---- 늦어지는 이유 ----
        ok(put("progress", {"rid": 3, "why": "  가상 이유  "}).status_code == 200 and row(prog(), 3)["why"] == "가상 이유" and row(prog(), 3)["at"] == f"{year}-06-15T03:00:00Z"
           and put("progress", {"rid": 1, "why": "가상 이유 1"}).status_code == 200 and row(prog(), 1)["why"] == "가상 이유 1" and row(prog(), 1)["actual"] == 6,
           "늦어지는_이유는_모든_행에: 비정기도 · 정기도 · 보낸 칸만 바뀜 (실적은 그대로) · 앞뒤 공백은 뺌")
        ok(put("progress", {"rid": 1, "why": "가" * 100}).status_code == 200 and row(prog(), 1)["why"] == "가" * 100 and err(put("progress", {"rid": 1, "why": "가" * 101}), 400, "늦어지는 이유: 100자 안으로 써 주세요.")
           and row(prog(), 1)["why"] == "가" * 100, "늦어지는_이유는_100자: 100자는 되고 101자는 400 (저장된 것은 그대로)")
        ok(all(put("progress", {"rid": 1, "why": v}).status_code == 400 for v in (5, None, ["가"], "\ud800", "\x00", "\x00abc", "가\x00", "가\x1b", "가\x7f")) and row(prog(), 1)["why"] == "가" * 100,
           "이유가 글자가 아니거나 쓸 수 없는 글자(짝 없는 서로게이트 · NUL · 제어문자)면 400")
        ok(put("progress", {"rid": 3, "why": "첫째 줄\n둘째 줄"}).status_code == 200 and row(prog(), 3)["why"] == "첫째 줄\n둘째 줄"
           and put("progress", {"rid": 3, "why": ""}).status_code == 200, "줄바꿈 · 탭은 됨 (화면의 글 상자에서 Enter 를 눌러도 막히지 않게)")
        ok(put("progress", {"rid": 1, "why": ""}).status_code == 200 and row(prog(), 1)["why"] is None and row(prog(), 1)["actual"] == 6
           and put("progress", {"rid": 3, "why": "   "}).status_code == 200 and row(prog(), 3)["why"] is None and row(prog(), 3)["at"] is None
           and q("SELECT COUNT(*) FROM kpi_progress WHERE user_id = ? AND rid = 3", EMP) == [(0,)],
           "빈 이유는 NULL (다른 칸이 없는 줄은 아예 지움)")

        # ---- 월 한 줄 ----
        ok(put("month", {"rid": 1, "month": 6, "text": "  가상 6월 한 줄  ", "link": link_ok}).json == {"ok": True}, "6월 줄을 씀")
        p = prog()
        ok(row(p, 1)["months"]["6"] == {"state": "DONE", "text": "가상 6월 한 줄", "link": link_ok, "at": f"{year}-06-15T03:00:00Z"}
           and row(p, 2)["months"]["6"]["state"] == "OPEN" and p["todo"]["months"] == 1, "월_한_줄_쓰고_지우기: 쓰면 DONE (글 앞뒤 공백은 뺌) · 할 일이 줄어듦")
        ok(put("month", {"rid": 1, "month": 6, "text": "가상 고침"}).status_code == 200
           and row(prog(), 1)["months"]["6"] == {"state": "DONE", "text": "가상 고침", "link": None, "at": f"{year}-06-15T03:00:00Z"} and counts()[1] == 2,
           "다시 쓰면 덮어씀 (링크를 안 보내면 링크도 지워짐) · 줄은 늘지 않음")
        ok(put("month", {"rid": 1, "month": 6, "text": ""}).status_code == 200
           and row(prog(), 1)["months"]["6"] == {"state": "OPEN", "text": None, "link": None, "at": None} and counts()[1] == 1 and prog()["todo"]["months"] == 2,
           "\"\"로 보내면 그 달 줄을 지움 → 다시 OPEN")
        ok(put("month", {"rid": 1, "month": 6, "text": "가상"}).status_code == 200 and put("month", {"rid": 1, "month": 6, "text": "   "}).status_code == 200
           and row(prog(), 1)["months"]["6"]["state"] == "OPEN" and put("month", {"rid": 2, "month": 6, "text": ""}).status_code == 200,
           "빈칸만 보내도 지움 · 없는 줄을 지워도 오류가 아님")
        ok(put("month", {"rid": 1, "month": 6, "text": "가" * 200}).status_code == 200 and err(put("month", {"rid": 1, "month": 6, "text": "가" * 201}), 400, "월 한 줄: 200자 안으로 써 주세요.")
           and row(prog(), 1)["months"]["6"]["text"] == "가" * 200, "월 한 줄은 200자까지 (201자는 400 · 저장된 것은 그대로)")
        ok(all(put("month", {"rid": 1, "month": 6, **b}).status_code == 400 for b in ({}, {"text": 5}, {"text": None}, {"text": ["가"]}, {"text": "\ud800"}, {"text": "가", "link": "\ud800"},
                                                                  {"text": "\x00"}, {"text": "\x00abc"}, {"text": "가\x00"}, {"text": "가\x1b"}, {"text": "가\x7f"}, {"text": "가", "link": "\x00"}))
           and row(prog(), 1)["months"]["6"]["text"] == "가" * 200, "글이 없거나 글자가 아니거나 쓸 수 없는 글자면 400")
        ok(put("month", {"rid": 1, "month": 6, "text": "가상 6월"}).status_code == 200, "(6월 줄을 짧게 고쳐 둠)")
        ok(all(err(put("month", {"rid": r_, "month": m_, "text": "가상"}), 403, not_now) for r_, m_ in ((1, 5), (2, 5), (1, 7), (2, 12), (1, 1), (2, 2), (1, 3), (1, 4)))
           and row(prog(), 1)["months"]["5"]["text"] == "가상 5월 줄", "지난_달은_못_쓴다 · 다음_달은_못_쓴다: 6월 15일에 5월 · 7월 · 12월은 403, 첫 확정 전(1~2월)도 403, 쓴 5월 줄은 그대로")
        ok(err(put("month", {"rid": 1, "month": 7, "text": "가" * 201}), 403, not_now) and err(put("month", {"rid": 1, "month": 5, "text": ""}), 403, not_now),
           "기간 403 이 글자 수 400 보다 먼저")
        ok(all(put("month", {"rid": r_, "month": m_, "text": "가상"}).status_code == 400 for r_, m_ in ((3, 6), (3, 7), (99, 6), (1, 0), (1, 13), (1, "6"), (1, True), (1, None), (1, 6.0), (1, -1)))
           and put("month", {"rid": 1, "text": "가상"}).status_code == 400, "비정기 · 없는 실행목표 · 달 모양이 틀리면 400 (기간 403 보다 먼저)")

        # ---- 링크 ----
        long_ok = "https://example.invalid/" + "a" * (300 - len("https://example.invalid/"))
        for what in ("month", "result"):
            tgt = {"rid": 1, "month": 6} if what == "month" else {"sid": 3}
            good = lambda link: put(what, {**tgt, "text": "가상 링크 줄", "link": link})  # noqa: E731
            stored = lambda: q("SELECT link FROM kpi_month_notes WHERE user_id = ? AND rid = 1 AND month = 6", EMP) if what == "month" \
                else q("SELECT link FROM kpi_results WHERE user_id = ? AND sid = 3", EMP)
            ok(good(link_ok).status_code == 200 and stored() == [(link_ok,)], f"링크는_http만 ({what}): https 주소는 저장")
            ok(all(good(v).status_code == 200 and stored() == [(None,)] for v in (None, "", "   ")) and put(what, {**tgt, "text": "가상 링크 줄"}).status_code == 200
               and stored() == [(None,)], f"링크가 없거나 빈칸이면 NULL ({what})")
            ok(good("  http://example.invalid/a  ").status_code == 200 and stored() == [("http://example.invalid/a",)] and good(long_ok).status_code == 200 and stored() == [(long_ok,)],
               f"앞뒤 빈칸은 빼고 저장 · 300자는 됨 ({what})")
            for bad in ("javascript:alert(1)", "ftp://example.invalid/x", "https://", "http://", "example.invalid/x", "https://exa mple.invalid", "https://exa\nmple.invalid",
                        "https://exa\x00mple.invalid", long_ok + "a", "//example.invalid", 5, ["https://example.invalid"], "https://\ud800"):
                ok(good(bad).status_code == 400 and stored() == [(long_ok,)], f"링크가 틀리면 400, 저장된 것은 그대로 ({what}: {str(bad)[:24]!r})")
        ok(put("month", {"rid": 1, "month": 6, "text": ""}).status_code == 200 and put("result", {"sid": 3, "text": ""}).status_code == 200 and counts() == [2, 1, 0],
           "(링크 검사에 쓴 줄을 지움)")

        # ---- 결과 한 줄 ----
        ok(put("result", {"sid": 1, "text": "  가상 초안을 냈다  ", "link": link_ok}).json == {"ok": True}, "1번 일정 결과를 씀")
        p = prog()
        ok(sched(p, 1)["state"] == "DONE" and sched(p, 1)["result"] == {"text": "가상 초안을 냈다", "link": link_ok, "at": f"{year}-06-15T03:00:00Z"}
           and p["todo"]["results"] == 1 and near(row(p, 3)["value"], 100 / 3) and near(p["total"], 41 + 10),
           "결과_쓰면_일정이_완료: 상태 DONE · 결과 글 · 할 일 2 → 1 · 비정기 진행률 1/3 · 전체에 반영")
        ok(put("result", {"sid": 2, "text": "가상 검토를 마쳤다"}).status_code == 200 and prog()["todo"]["results"] == 0 and sched(prog(), 2)["state"] == "DONE",
           "(2번 일정도 씀 → 할 일 0)")
        ok(put("result", {"sid": 3, "text": "가상 미리 씀"}).status_code == 200 and sched(prog(), 3)["state"] == "DONE" and prog()["next"] is None
           and near(row(prog(), 3)["value"], 100), "기한 전에도 쓸 수 있음 → 모두 쓰면 다음 일정은 없음 · 진행률 100")
        ok(put("result", {"sid": 3, "text": ""}).status_code == 200 and sched(prog(), 3)["state"] == "UPCOMING" and sched(prog(), 3)["result"] is None
           and prog()["next"] == {"sid": 3, "due": ymd(9, 30), "name": "가상 마감"} and counts() == [2, 1, 2], "\"\"로 지우면 다시 예정 (UPCOMING) · 다음 일정으로 돌아옴")
        ok(put("result", {"sid": 3, "text": "가" * 200}).status_code == 200 and err(put("result", {"sid": 3, "text": "가" * 201}), 400, "결과 한 줄: 200자 안으로 써 주세요.")
           and all(put("result", {"sid": 3, **b}).status_code == 400 for b in ({}, {"text": 5}, {"text": None}, {"text": "\ud800"}, {"text": "\x00"}, {"text": "\x00abc"}, {"text": "가\x00"}, {"text": "가\x1b"}))
           and put("result", {"sid": 3, "text": ""}).status_code == 200, "결과 한 줄은 200자까지 · 글이 아니면 400")
        ok(all(err(put("result", {"sid": v, "text": "가상"}), 400, no_sid) for v in (99, 0, "1", True, 1.0, None, [1])) and err(put("result", {"text": "가상"}), 400, no_sid)
           and counts() == [2, 1, 2], "없는 일정 (번호 모양이 틀린 것 포함) → 400")

        # ---- 본문이 이상해도 서버 오류가 아님 ----
        odd = lambda what, **kw: c(EMP).post(f"/api/kpi/me/{what}", base_url=BASE, headers=ORIGIN, **kw).status_code  # noqa: E731
        ok(all(odd(w, json=b) == 400 for w in bodies for b in ([], "글", 5, None, {}, {"year": None}, {"year": "2026"}, {"year": True}, {"year": 2025}, {"year": 2101}, {"year": year + 0.5}))
           and all(odd(w, data="not json", content_type="application/json") == 400 for w in bodies) and all(odd(w) == 400 for w in bodies),
           "본문이 JSON 이 아니거나 모양이 틀리거나 해가 이상해도 400 (서버 오류가 아님)")
        ok(all(err(put(w, {**b, "year": year + 1}), 404, no_plan) for w, b in bodies.items()), "다음 해 계획서는 아직 없음 → 404")
        ok(q("SELECT COUNT(*) FROM audit_logs WHERE action <> 'LOGIN'") == audit_n, "진행 기록은 감사로그에 남기지 않음 (읽기 · 쓰기 모두 — 로그인만 남음)")

        # ---- 남의 기록 ----
        mine = prog()
        ok(all(err(get(EMP, who), 403, "이 KPI를 볼 권한이 없습니다.") for who in (PEER, OTHER, FIELD)),
           "남의_기록은_못_본다: 같은 팀 다른 팀원 · 다른 팀장 · 현장직 403")
        ok(get(EMP, LEAD).status_code == 200 and get(EMP, LEAD).json == mine and get(EMP, HR).json == mine and get(EMP, HEAD).json == mine,
           "바로 위 관리자(팀장) · HR · 본부장(2단계 ② 에서 넓어진 범위)은 봄 (본인 화면과 같은 내용)")
        ok(err(get(PEER, PEER), 404, no_plan) and err(get(PEER, LEAD), 404, no_plan) and err(get(PEER, OTHER), 403, "이 KPI를 볼 권한이 없습니다."),
           "계획서가 없으면 404 (볼 권한은 먼저 봄)")
        anon = app.test_client()
        ok(anon.get(f"/api/kpi/progress/{EMP}?year={year}", base_url=BASE).status_code == 401
           and all(anon.post(f"/api/kpi/me/{w}", base_url=BASE, headers=ORIGIN, json={"year": year, **b}).status_code == 401 for w, b in bodies.items())
           and c(EMP).get("/api/kpi/progress/99999999999999999999", base_url=BASE).status_code == 404, "로그인 없이는 401 · 너무 큰 번호도 서버 오류가 아님")
        before = counts()
        ok(all(err(put(w, {**b, "rid": 1, "userId": EMP, "user_id": EMP}, who), 404, no_plan) for w, b in bodies.items() for who in (PEER, LEAD)) and counts() == before
           and row(prog(), 1)["actual"] == 6, "남의 기록은 못 쓴다: 누가 보냈는지는 로그인한 사람으로만 정함 (본문에 남의 번호를 넣어도 소용없음)")
        x("INSERT INTO kpi_plans (user_id, year, status, draft, confirmed_version, updated_at) VALUES (?, ?, 'CONFIRMED', '[]', 1, '2000-01-01T00:00:00Z')", PEER, year)
        x("INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by) VALUES (?, ?, 1, ?, ?, ?)", PEER, year,
          json.dumps([{"team": "가상", "personal": "가상", "items": [{**item("가상 동료 업무", 100, "reg"), "rid": 1}]}], ensure_ascii=False), f"{year - 1}-12-30T10:00:00Z", LEAD)
        ok(cells(row(get(PEER, HR).json, 1)) == "x" * 5 + "o" + "f" * 6, "첫 확정이 다른 해이면 1월부터 해당 있음")

        # ---- 입사일 ----
        x("UPDATE users SET hire_date = ? WHERE id = ?", ymd(5, 20), EMP)
        ok(cells(row(prog(), 1)) == "-" * 4 + "d" + "o" + "f" * 6, "입사한 달은 해당 있음 · 그 전 달은 해당 없음 (5월 20일 입사 → 4월까지 해당 없음)")
        x("UPDATE users SET hire_date = '' WHERE id = ?", EMP)
        ok(cells(row(prog(), 1)) == "--xxd" + "o" + "f" * 6, "입사일이 비어 있으면 모든 달이 해당 있음")
        x("UPDATE users SET hire_date = NULL WHERE id = ?", EMP)

        # ---- 판 2 (한국 7월 1일 0시 30분 = UTC 6월 30일 15:30): 일정 하나를 지우고 실행목표를 더함 ----
        at(year, 6, 30, 15, 30)
        ok(own(EMP, "request-change", {"reason": "가상 일정을 줄이고 업무를 더함"}).status_code == 200 and act(LEAD, "unlock").status_code == 200, "(수정 요청 → 팀장이 풀어 줌)")
        ok(put("progress", {"rid": 1, "actual": 7}).status_code == 200 and prog()["version"] == 1 and row(prog(), 1)["actual"] == 7 and put("progress", {"rid": 1, "actual": 6}).status_code == 200,
           "계획서가 수정 중이어도 지금 확정본(판 1)에는 기록이 쓰임")
        draft = c(EMP).get("/api/kpi/me", base_url=BASE).json["plan"]["draft"]
        its = draft[0]["items"]
        its[0]["weight"], its[1]["weight"] = 30, 20
        its[2]["scheds"] = [s for s in its[2]["scheds"] if s["sid"] != 2]
        its.append(item("가상 정기 업무 3", 20, "reg"))
        ok(own(EMP, "draft", {"draft": draft}).status_code == 200 and own(EMP, "submit").status_code == 200 and act(LEAD, "confirm").status_code == 200
           and q("SELECT version, confirmed_at FROM kpi_versions WHERE user_id = ? ORDER BY version", EMP) == [(1, f"{year}-03-10T03:00:00Z"), (2, f"{year}-06-30T15:30:00Z")],
           "(검토 일정을 지우고 정기 업무를 하나 더해 다시 확정 — 판 2)")
        p = prog()
        ok(p["version"] == 2 and [i["rid"] for i in p["items"]] == [1, 2, 3, 4] and [s["sid"] for s in row(p, 3)["scheds"]] == [1, 3] and row(p, 4)["action"] == "가상 정기 업무 3",
           "판 2: 새 실행목표는 새 번호 · 지운 일정은 안 보임")
        ok(cells(row(p, 1)) == "--xxd" + "oo" + "f" * 5 and cells(row(p, 4)) == "-" * 6 + "o" + "f" * 5 and row(p, 1)["actual"] == 6 and row(p, 2)["pct"] == 70,
           "첫 달은 번호마다: 옛 실행목표는 판 1 의 3월 · 새 실행목표는 판 2 가 확정된 한국 7월 (UTC 로는 6월이지만) · 옛 기록은 이어짐")
        ok(err(put("result", {"sid": 2, "text": "가상 지운 일정"}), 400, no_sid) and sched(p, 1)["state"] == "DONE"
           and q("SELECT text FROM kpi_results WHERE user_id = ? AND sid = 2", EMP) == [("가상 검토를 마쳤다",)],
           "지금_확정본에_없는_일정은_못_쓴다: 지운 일정에 쓰면 400 · 옛 결과는 DB 에 남음 (화면에는 안 보임)")
        ok(err(put("month", {"rid": 4, "month": 6, "text": "가상"}), 403, not_now), "새 실행목표는 확정되기 전 달에 못 씀 (6월 줄 마감이 아직 안 지나도)")

        # ---- 바뀐 기한 · 일정 변경 요청 읽기 (처리하는 길은 다음 작업 — 여기서는 표에 직접 넣는다) ----
        def change(sid, to, status, req, dec=None, note=None):
            x("INSERT INTO kpi_sched_changes (user_id, year, sid, from_due, to_due, reason, status, note, requested_at, decided_at, decided_by)"
              " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", EMP, year, sid, ymd(1, 1), to, f"가상 사유 {sid}", status, note, req, dec, LEAD if dec else None)
            return q("SELECT MAX(id) FROM kpi_sched_changes")[0][0]

        z = lambda d, t: f"{year}-{d}T{t}Z"  # noqa: E731
        change(1, ymd(7, 1), "CONFIRMED", z("06-19", "01:00:00"), z("06-20", "01:00:00"))   # 판 2 가 확정되기 전에 확인된 변경 — 새 판의 기한에 이미 들어 있음
        ok((sched(prog(), 1)["due"], sched(prog(), 1)["origDue"]) == (ymd(5, 20), ymd(5, 20)), "판 2 가 확정되기 전에 확인된 변경은 안 덮음 (새 판의 기한에 이미 들어 있음)")
        change(1, ymd(7, 20), "CONFIRMED", z("06-30", "15:40:00"), z("07-01", "03:00:00"))   # 번호는 앞이지만 확인은 뒤
        change(1, ymd(7, 10), "CONFIRMED", z("06-30", "15:45:00"), z("06-30", "16:00:00"))   # 번호는 뒤지만 확인은 앞
        change(2, ymd(7, 5), "CONFIRMED", z("06-30", "15:46:00"), z("06-30", "16:00:00"))    # 지운 일정의 변경 — 무시
        change(1, ymd(8, 1), "RETURNED", z("07-01", "01:00:00"), z("07-01", "02:00:00"), "가상 돌려보냄")
        change(3, ymd(7, 25), "CONFIRMED", z("06-30", "15:50:00"), z("06-30", "17:00:00"))
        change(3, ymd(9, 1), "RETURNED", z("06-30", "15:55:00"), z("06-30", "15:56:00"), "가상 이전 돌려보냄")
        pend = change(3, ymd(8, 15), "PENDING", z("06-30", "16:00:00"))
        p = prog()
        ok((sched(p, 1)["due"], sched(p, 1)["origDue"]) == (ymd(7, 20), ymd(5, 20)) and (sched(p, 3)["due"], sched(p, 3)["origDue"]) == (ymd(7, 25), ymd(9, 30))
           and [s["sid"] for s in row(p, 3)["scheds"]] == [1, 3], "실제 기한: 판 2 가 확정된 뒤 확인된 변경만 · 확인한 시각이 오래된 것부터 덮음 · 확정본 기한(origDue)은 그대로")
        ok(sched(p, 1)["returned"] == {"to": ymd(8, 1), "note": "가상 돌려보냄", "at": z("07-01", "02:00:00")} and sched(p, 1)["pending"] is None,
           "가장 최근 요청이 돌려받은 것이면 returned (바꾸려던 날 · 이유 · 시각)")
        ok(sched(p, 3)["pending"] == {"id": pend, "to": ymd(8, 15), "reason": "가상 사유 3", "at": z("06-30", "16:00:00")} and sched(p, 3)["returned"] is None,
           "그 뒤에 새 요청이 있으면 returned 는 null · 기다리는 요청은 pending")
        ok(p["next"] == {"sid": 3, "due": ymd(7, 25), "name": "가상 마감"} and near(row(p, 3)["expected"], 0) and sched(p, 3)["state"] == "UPCOMING",
           "다음 일정과 기대 진행률은 실제 기한으로 (바뀐 기한이 없으면 1번 일정이 14일 넘게 늦은 것으로 셈)")
        x("DELETE FROM kpi_sched_changes WHERE user_id = ?", EMP)
        p = prog()
        ok((sched(p, 1)["due"], sched(p, 3)["due"]) == (ymd(5, 20), ymd(9, 30)) and near(row(p, 3)["expected"], 50) and p["next"]["due"] == ymd(9, 30), "(변경을 지우면 확정본 기한으로 돌아감)")

        # ---- 한국 7월 3일: 이번 달 + (1~7일이면) 지난달 ----
        at(year, 7, 3)
        p = prog()
        ok(p["todo"] == {"months": 5, "prev": 2, "results": 0}, "할 일: 7월 3일 — 이번 달 3칸 + 지난달 2칸 (새 실행목표는 6월이 해당 없음)")
        x("UPDATE users SET hire_date = ? WHERE id = ?", ymd(7, 10), EMP)
        ok(cells(row(prog(), 1)) == "-" * 6 + "o" + "f" * 5 and prog()["todo"] == {"months": 3, "prev": 0, "results": 0}
           and err(put("month", {"rid": 1, "month": 6, "text": "가상"}), 403, not_now), "입사 전 달은 6월 줄이 아직 열려 있어도 해당 없음 (쓴 줄이 있어도) → 못 씀 · 할 일에서도 빠짐")
        x("UPDATE users SET hire_date = NULL WHERE id = ?", EMP)
        ok(put("month", {"rid": 1, "month": 6, "text": "가상 늦게 쓴 6월"}).status_code == 200 and prog()["todo"] == {"months": 4, "prev": 1, "results": 0},
           "지난달 줄은 7일까지 씀 → 지난달 할 일이 줄어듦")
        at(year, 7, 7)
        ok(prog()["todo"] == {"months": 4, "prev": 1, "results": 0}, "7일까지는 지난달도 셈")
        at(year, 7, 8)
        p = prog()
        ok(p["todo"] == {"months": 3, "prev": 0, "results": 0} and cells(row(p, 2)) == "--xxx" + "x" + "o" + "f" * 5,
           "8일부터는 지난달을 안 셈 (못 쓴 달이 됨)")

        # ---- 다음 해 1월: 그해 기록은 1월 7일까지 ----
        at(year + 1, 1, 3)
        p = prog()
        ok(p["open"] is True and p["todo"] == {"months": 3, "prev": 3, "results": 1} and row(p, 1)["months"]["12"]["state"] == "OPEN",
           "다음 해 1월 3일: 그해 12월 줄이 아직 열려 있음 · 할 일의 지난달은 12월 (이번 달 1월은 다른 해라 안 셈) · 9월 말 일정은 빨강")
        ok(put("month", {"rid": 1, "month": 12, "text": "가상 12월"}).status_code == 200 and put("progress", {"rid": 1, "actual": 8}).status_code == 200
           and put("result", {"sid": 3, "text": "가상 늦은 결과"}).status_code == 200 and prog()["todo"] == {"months": 2, "prev": 2, "results": 0},
           "그해 기록은 다음 해에도 쓰임")
        at(year + 1, 1, 7, 14, 59)  # 한국 1월 7일 23:59
        ok(put("month", {"rid": 2, "month": 12, "text": "가상 12월 2"}).status_code == 200 and put("progress", {"rid": 1, "actual": 9}).status_code == 200
           and put("result", {"sid": 3, "text": "가상 늦은 결과 2"}).status_code == 200 and prog()["open"] is True, "1월 7일 UTC 14:59 (한국 23:59) 까지는 씀")
        at(year + 1, 1, 7, 15, 0)  # 한국 1월 8일 00:00
        p = prog()
        ok(all(err(put(w, b), 403, closed) for w, b in bodies.items()) and err(put("progress", {"rid": 99, "actual": 1}), 403, closed)
           and err(put("month", {"rid": 3, "month": 99, "text": "가" * 999}), 403, closed) and row(p, 1)["actual"] == 9,
           "기록_기간이_지났다: 한국 1월 8일 0시부터는 세 곳 모두 403 (칸이 틀려도 이 이유가 먼저) · 쓴 값은 그대로")
        ok(p["open"] is False and p["schedOpen"] is True and p["todo"] == {"months": 0, "prev": 0, "results": 0} and row(p, 1)["months"]["12"]["state"] == "DONE"
           and cells(row(p, 2)) == "--" + "x" * 9 + "d", "닫힌 뒤에도 읽기는 됨 (open false) · 할 일 0 · 쓴 줄은 완료로 남음")
    finally:
        auth._now = real_now
        x("DELETE FROM kpi_years WHERE year = ?", year)
        for row_ in saved:
            x("INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)", *row_)
    ok(q("SELECT year, edit_from, edit_to, eval_start, updated_at, updated_by FROM kpi_years WHERE year = ?", year) == saved, "(작성 기간 설정을 원래대로 돌려 놓음)")


def check_kpi_sched(hr) -> None:
    """KPI 일정 변경 (2026-10-21): 확정한 뒤에도 비정기 일정 하나의 기한을 바꿔 달라고 요청하고, 바로 위 관리자(또는 대신하는 HR)가 확인하거나 돌려보낸다 ·
    확인되면 그 일정의 실제 기한이 바뀌고(확정본은 그대로) 수정을 풀면 초안에 이어진다 · 평가 시작일부터는 막힌다 · 작성 기간과는 상관없다.
    서버가 순서대로 다시 본다. 오늘에 기대는 검사는 모두 시계(auth._now)를 정해 두고 돈다 — 어느 날 돌려도 같다 (R4). 시계가 바뀌면 창은 새로 연다."""
    import kpi
    print("KPI 일정 변경")
    # 이 검사만의 가상 조직: 본부(81) 밑 팀 둘(82 · 83), 팀이 없는 본부(84)
    for dept in ((81, "S81", "가상S본부", "DIVISION", None), (82, "S82", "가상S팀", "TEAM", 81), (83, "S83", "가상S팀2", "TEAM", 81),
                 (84, "S84", "가상S본부2", "DIVISION", None)):
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (?, ?, ?, ?, ?)", *dept)
    HR, LEAD, EMP, PEER, OTHER, HEAD, FIELD, HR2, CEO, NEW = 1, 71, 72, 73, 74, 75, 76, 77, 78, 79
    cast = {LEAD: ("가상S팀장", 82, "TEAM_LEADER", 1), EMP: ("가상S직원", 82, None, 1), PEER: ("가상S동료", 82, None, 1),
            OTHER: ("가상S다른팀장", 83, "TEAM_LEADER", 1), HEAD: ("가상S본부장", 81, "DIVISION_HEAD", 1), FIELD: ("가상S현장직", 82, None, 0),
            HR2: ("가상S인사", 84, "HR_ADMIN", 1), CEO: ("가상S대표", 81, "CEO", 0), NEW: ("가상S신입", 82, None, 1)}
    for uid, (name, dept, role, writes) in cast.items():
        x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, writes_kpi) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
          uid, name, f"s{uid}@example.invalid", dept, f"HW-{uid}", writes)
        if role:
            x("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", uid, role)
    x("UPDATE users SET title = '프로' WHERE id = ?", EMP)

    year, real_now = kpi.plan_year(), auth._now  # 진짜 오늘의 올해 — 아래 날짜는 모두 이 해 안 (시계를 정해 놓고 돈다)
    ymd = lambda m, d: f"{year}-{m:02d}-{d:02d}"  # noqa: E731
    z = lambda m, d, hh=3, mm=0: f"{year}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def at(m, d, hh=3, mm=0, yr=None):
        """시계를 UTC 로 (올해 — yr 이 있으면 그 해 — m월 d일 hh:mm) 에 맞춘다. 기본 03:00 = 한국 정오."""
        t = datetime(yr or year, m, d, hh, mm, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    NO_KPI, NO_PLAN, NO_AUTH = "KPI 작성 대상이 아닙니다.", "확정된 KPI가 없습니다.", "이 요청을 처리할 권한이 없습니다."
    EDITING = "지금은 계획서를 고치는 중이라 일정 변경을 요청할 수 없습니다."
    STARTED, WAITING = "평가가 시작되어 일정을 바꿀 수 없습니다.", "이미 확인을 기다리는 요청이 있습니다."
    NO_SID, HAS_RESULT = "지금 확정본에 없는 일정입니다.", "결과를 이미 쓴 일정은 날짜를 바꿀 수 없습니다."
    BAD_DUE, SAME_DUE = f"{year}년 안의 날짜(YYYY-MM-DD)를 써 주세요.", "지금 기한과 같은 날짜예요. 다른 날짜를 써 주세요."
    BAD_REASON, BAD_NOTE = "일정을 바꾸려는 이유를 200자 안으로 써 주세요.", "돌려보내는 이유를 200자 안으로 써 주세요."
    BAD_REASON_CHARS, BAD_NOTE_CHARS = "일정을 바꾸려는 이유에 쓸 수 없는 글자가 있습니다.", "돌려보내는 이유에 쓸 수 없는 글자가 있습니다."
    NOT_YOURS, THIS_YEAR = "본인의 요청만 취소할 수 있습니다.", "올해 계획서의 일정만 바꿀 수 있습니다."
    err = lambda r, code, msg: r.status_code == code and r.json == {"error": msg}  # noqa: E731
    bad_text = lambda r, *msgs: r.status_code == 400 and r.json.get("error") in msgs  # noqa: E731  — 글이 틀리면 400 (길이 문구 또는 쓸 수 없는 글자 문구)
    conflict = lambda r, st: r.status_code == 409 and r.json == {"error": "지금 상태에서는 할 수 없습니다.", "status": st}  # noqa: E731

    ask = lambda who, sid, due, reason="가상 사유", **over: c(who).post(  # noqa: E731
        "/api/kpi/me/sched-change", base_url=BASE, headers=ORIGIN, json={"year": year, "sid": sid, "due": due, "reason": reason, **over})
    drop = lambda who, cid, headers=ORIGIN: c(who).post(f"/api/kpi/me/sched-change/{cid}/cancel", base_url=BASE, headers=headers, json={})  # noqa: E731
    act = lambda who, cid, what, body=None, headers=ORIGIN: c(who).post(  # noqa: E731
        f"/api/kpi/sched-changes/{cid}/{what}", base_url=BASE, headers=headers, json=body or {})
    own = lambda who, what, body=None: c(who).post(f"/api/kpi/me/{what}", base_url=BASE, headers=ORIGIN, json=body or {})  # noqa: E731
    inbox = lambda who: c(who).get("/api/kpi/inbox", base_url=BASE).json  # noqa: E731
    box = lambda who: [i for i in inbox(who)["items"] if i["kind"] == "SCHED" and i["userId"] in cast]  # noqa: E731
    ids = lambda who: [i["id"] for i in box(who)]  # noqa: E731
    who_ = lambda who: [(i["id"], i["proxy"]) for i in box(who)]  # noqa: E731
    prog = lambda uid=EMP, who=EMP: c(who).get(f"/api/kpi/progress/{uid}?year={year}", base_url=BASE).json  # noqa: E731
    sched = lambda p, sid: next(s for i in p["items"] for s in i.get("scheds", []) if s["sid"] == sid)  # noqa: E731
    row = lambda cid: q("SELECT status, note, decided_at, decided_by, on_behalf_of FROM kpi_sched_changes WHERE id = ?", cid)  # noqa: E731
    audit = lambda action, uid=EMP: q("SELECT actor_user_id, actor_is_hr, on_behalf_of, entity_type, after_data FROM audit_logs"  # noqa: E731
                                      " WHERE action = ? AND entity_id = ? ORDER BY id", action, uid)
    aj = lambda sid, frm, to: json.dumps({"year": year, "sid": sid, "from": frm, "to": to})  # noqa: E731
    n_rows = lambda: q("SELECT COUNT(*) FROM kpi_sched_changes WHERE user_id BETWEEN 71 AND 79")[0][0]  # noqa: E731
    version1 = lambda uid=EMP: q("SELECT content FROM kpi_versions WHERE user_id = ? AND version = 1", uid)  # noqa: E731

    def item(name, w, kind, **more):
        return {"action": name, "formula": "가상 측정식", "weight": w, "owner": "가상S직원", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
                "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}

    plan = [{"team": "가상 팀목표", "personal": "가상 개인목표", "items": [
        item("가상 정기 업무 1", 40, "reg", target=12, unit="건"), item("가상 정기 업무 2", 30, "reg"),
        item("가상 비정기 업무", 30, "irr", scheds=[{"due": ymd(9, 30), "name": "가상 초안"}, {"due": ymd(11, 20), "name": "가상 마감"}])]}]

    def confirmed(uid):
        """계획서를 바로 확정된 모습으로 넣는다 (비정기 일정 하나, 번호 1) — 확정 흐름을 거치지 않아도 되는 사람용."""
        content = json.dumps([{"team": "가상 팀목표", "personal": "가상 개인목표", "items": [
            {**item("가상 비정기 업무", 100, "irr", scheds=[{"sid": 1, "due": ymd(9, 30), "name": "가상 초안"}]), "rid": 1}]}], ensure_ascii=False)
        x("INSERT INTO kpi_plans (user_id, year, status, draft, confirmed_version, updated_at) VALUES (?, ?, 'CONFIRMED', ?, 1, '2000-01-01T00:00:00Z')", uid, year, content)
        x("INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by) VALUES (?, ?, 1, ?, ?, ?)", uid, year, content, f"{year}-03-10T03:00:00Z", LEAD)

    saved = q("SELECT year, edit_from, edit_to, eval_start, updated_at, updated_by FROM kpi_years WHERE year = ?", year)
    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    for uid in other_ceos:  # 이 가상 조직의 대표이사는 내가 만든 한 명뿐이게
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
    setyear = lambda ev: c(HR).post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN,  # noqa: E731  — HR 이 평가 시작일을 정한다 (작성 기간은 그해 내내 열어 둔 채)
                                    json={"year": year, "editFrom": ymd(1, 1), "editTo": ymd(12, 31), "evalStart": ev})
    anon = app.test_client()
    try:
        r = hr.post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN, json={"year": year, "editFrom": ymd(1, 1), "editTo": ymd(12, 31), "evalStart": None})
        ok(r.status_code == 200, "(작성 기간을 그해 내내 열어 둠 — 시계를 정해 놓고 계획서를 확정하려고)")
        at(6, 15)  # 한국 6월 15일 12시
        ok(all(anon.post(u, base_url=BASE, headers=ORIGIN, json={}).status_code == 401
               for u in ("/api/kpi/me/sched-change", "/api/kpi/me/sched-change/1/cancel", "/api/kpi/sched-changes/1/confirm", "/api/kpi/sched-changes/1/return")),
           "로그인 없이는 401 (일정 변경)")

        # ---- 확정 전 ----
        ok(err(ask(FIELD, 1, ymd(10, 1)), 403, NO_KPI), "KPI_작성이_꺼진_사람은_못_한다: 403 (확정본이 있고 없고를 보기 전에)")
        ok(err(ask(EMP, 1, ymd(10, 1)), 404, NO_PLAN) and n_rows() == 0, "확정_전에는_못_한다: 계획서가 아예 없으면 404")
        ok(own(EMP, "draft", {"draft": plan}).status_code == 200 and own(EMP, "submit").status_code == 200
           and err(ask(EMP, 1, ymd(10, 1)), 404, NO_PLAN) and n_rows() == 0, "제출만 하고 확정 전이어도 404 (아직 한 번도 확정되지 않음)")
        ok(c(LEAD).post(f"/api/kpi/plans/{EMP}/confirm", base_url=BASE, headers=ORIGIN, json={}).status_code == 200
           and q("SELECT version, confirmed_at FROM kpi_versions WHERE user_id = ?", EMP) == [(1, z(6, 15))], "(팀장이 확정 — 판 1)")
        v1, plan_row = version1(), q("SELECT status, draft, confirmed_version, updated_at FROM kpi_plans WHERE user_id = ? AND year = ?", EMP, year)

        # ---- 요청의 조건 — 서버가 순서대로 본다 ----
        ok(all(ask(EMP, 1, ymd(10, 1), year=y).status_code == 400 for y in (None, "2026", True, 2025, 2101, year + 0.5, 10 ** 30))
           and err(ask(EMP, 1, ymd(10, 1), year=year + 1), 400, THIS_YEAR) and err(ask(EMP, 1, ymd(10, 1), year=2100), 400, THIS_YEAR) and n_rows() == 0,
           "해는 2026~2100 정수 — 틀리면 400 · 올해가 아닌 해도 400 (올해 계획서의 일정만)")
        ok(all(err(ask(EMP, s, ymd(10, 1)), 400, NO_SID) for s in (99, 0, -1, "1", True, 1.0, None, [1], 10 ** 30)) and n_rows() == 0
           and err(c(EMP).post("/api/kpi/me/sched-change", base_url=BASE, headers=ORIGIN, json={"year": year, "due": ymd(10, 1), "reason": "가상"}), 400, NO_SID),
           "지금 확정본에 없는 일정 (번호 모양이 틀린 것 · 불리언 포함) → 400")
        bad_due = (None, "", "   ", 5, True, [], {}, f"{year}0930", f"{year}-9-30", f"{year}-13-01", f"{year}-02-30", f"{year}-09-31", f"{year}-09-30 ", f"{year}-09-30\n",
                   f" {year}-09-30", f"{year}-W40-3", "내일", f"{year}-09-30T00:00", "２０２６-０９-３０", f"{year + 1}-01-05", f"{year - 1}-12-31", f"{year + 1}-09-30")
        ok(all(err(ask(EMP, 1, d), 400, BAD_DUE) for d in bad_due) and n_rows() == 0,
           "그해_밖_기한은_못_한다: 기한은 그해 안의 실제 날짜 YYYY-MM-DD — 글자가 아니거나 모양이 틀리거나(20270924 · 2-30 · 줄바꿈) 그해 밖이면 400")
        ok(err(ask(EMP, 1, ymd(9, 30)), 400, SAME_DUE) and err(ask(EMP, 2, ymd(11, 20)), 400, SAME_DUE) and n_rows() == 0,
           "같은_기한으로는_요청_못_한다: 지금 실제 기한과 같으면 400")
        bad_reason = (None, "", "   ", 5, ["가"], "가" * 201, "\x00", "가\x00", "\ud800", "가\x1b")
        ok(all(bad_text(ask(EMP, 1, ymd(10, 15), r), BAD_REASON, BAD_REASON_CHARS) for r in bad_reason) and n_rows() == 0, "이유는 1~200자 글자 — 아니면 400, 아무것도 안 남김")
        ok(err(ask(EMP, 99, "내일", ""), 400, NO_SID) and err(ask(EMP, 1, "내일", ""), 400, BAD_DUE) and err(ask(EMP, 1, ymd(9, 30), ""), 400, SAME_DUE),
           "순서: 일정 → 기한 → 이유 (앞에서 걸리면 뒤는 보지 않음)")
        ok(c(EMP).post("/api/kpi/me/sched-change", base_url=BASE, headers={}, json={"year": year, "sid": 1, "due": ymd(10, 15), "reason": "가상"}).status_code == 403
           and n_rows() == 0, "포털 화면에서만 (Origin 없으면 403)")

        # ---- 요청 → 처리함 ----
        r = ask(EMP, 1, ymd(10, 15), "  가상 사유 문장  ")
        r1 = r.json["id"]
        ok(r.status_code == 200 and r.json == {"ok": True, "id": r1}
           and q("SELECT sid, from_due, to_due, reason, status, note, requested_at, decided_at, decided_by, on_behalf_of FROM kpi_sched_changes WHERE id = ?", r1)
           == [(1, ymd(9, 30), ymd(10, 15), "가상 사유 문장", "PENDING", None, z(6, 15), None, None, None)],
           "요청: PENDING · 옛 기한(지금 실제 기한) · 새 기한 · 이유(앞뒤 공백 뺌) · 시각 · 결정 칸은 비어 있음")
        ok(audit("KPI_SCHED_REQUESTED") == [(EMP, 0, None, "kpi_plan", aj(1, ymd(9, 30), ymd(10, 15)))],
           "감사로그 KPI_SCHED_REQUESTED: 누가 · 누구 계획서 · 해 · 일정 · 옛 기한 · 새 기한 (이유 문장은 없음)")
        p = prog()
        ok(sched(p, 1)["pending"] == {"id": r1, "to": ymd(10, 15), "reason": "가상 사유 문장", "at": z(6, 15)} and sched(p, 1)["due"] == ymd(9, 30)
           and sched(p, 2)["pending"] is None, "주인 화면: 기다리는 요청 (아직 기한은 그대로)")
        item1 = {"kind": "SCHED", "id": r1, "userId": EMP, "name": "가상S직원", "dept": "가상S팀", "title": "프로", "action": "가상 비정기 업무", "sched": "가상 초안",
                 "from": ymd(9, 30), "to": ymd(10, 15), "reason": "가상 사유 문장", "at": z(6, 15), "proxy": False}
        ok(inbox(LEAD)["items"] == [item1], "팀장 처리함에 SCHED 1건 (이름 · 직급 · 실행목표 · 일정 · 옛 → 새 기한 · 이유 · 시각)")
        ok(all(box(w) == [] for w in (PEER, OTHER, HEAD, EMP, HR)), "다른 팀원 · 다른 팀장 · 본부장 · 본인 · HR(바로 위 관리자가 아님) 처리함에는 없음")

        # ---- 일정마다 하나 · 취소 ----
        ok(err(ask(EMP, 1, ymd(10, 20)), 409, WAITING) and err(ask(EMP, 1, ymd(10, 20), ""), 409, WAITING) and err(ask(EMP, 1, ymd(9, 30), ""), 400, SAME_DUE)
           and n_rows() == 1, "요청은_일정마다_하나: 같은 일정에 기다리는 요청이 있으면 409 (이유 400 보다 먼저, 같은 기한 400 보다 나중)")
        r2 = ask(EMP, 2, ymd(12, 5)).json["id"]
        ok(sorted(ids(LEAD)) == [r1, r2], "다른 일정은 따로 요청됨")
        ok(all(err(drop(w, r2), 403, NOT_YOURS) for w in (PEER, LEAD, HEAD, HR)) and drop(EMP, r2, headers={}).status_code == 403 and row(r2)[0][0] == "PENDING",
           "남의_요청은_취소_못_한다: 같은 팀 · 관리자 · 본부장 · HR 모두 403 (Origin 없어도 403)")
        ok(drop(EMP, r2).json == {"ok": True} and row(r2) == [("CANCELLED", None, z(6, 15), EMP, None)] and ids(LEAD) == [r1] and sched(prog(), 2)["pending"] is None
           and audit("KPI_SCHED_CANCELLED") == [(EMP, 0, None, "kpi_plan", aj(2, ymd(11, 20), ymd(12, 5)))],
           "본인이_취소한다: CANCELLED · 결정한 시각과 사람 = 본인 · 처리함에서 사라짐 · 감사로그 KPI_SCHED_CANCELLED")
        ok(conflict(drop(EMP, r2), "CANCELLED") and err(drop(PEER, r2), 403, NOT_YOURS) and all(drop(EMP, v).status_code == 404 for v in (0, 99999, 10 ** 30)),
           "두 번 취소는 409 (지금 상태를 알려 줌) · 남의 것은 상태보다 권한이 먼저 · 없는 번호 · 너무 큰 번호는 404")

        # ---- 관리자가 아니면 못 한다 ----
        ok(all(err(act(w, r1, what, {"note": "가상"}), 403, NO_AUTH) for w in (PEER, OTHER, HEAD, EMP, FIELD) for what in ("confirm", "return"))
           and act(LEAD, r1, "confirm", headers={}).status_code == 403 and row(r1)[0][0] == "PENDING",
           "관리자가_아니면_못_한다: 같은 팀 다른 팀원 · 다른 팀장 · 본부장 · 본인(주인) · 현장직 403 (Origin 없어도 403) · 요청은 그대로")
        ok(all(act(LEAD, v, w).status_code == 404 for v in (0, 99999, 10 ** 30) for w in ("confirm", "return")) and act(PEER, 99999, "confirm").status_code == 404,
           "없는 요청 번호는 404 (너무 큰 번호도 서버 오류가 아님)")

        # ---- 돌려보내기는 이유 필수 ----
        ok(all(bad_text(act(LEAD, r1, "return", b), BAD_NOTE, BAD_NOTE_CHARS) for b in ({}, {"note": None}, {"note": ""}, {"note": "   "}, {"note": 5}, {"note": ["가"]},
                                                                           {"note": "가" * 201}, {"note": "\x00"}, {"note": "가\x1b"}, {"note": "\ud800"}))
           and row(r1)[0][0] == "PENDING" and audit("KPI_SCHED_RETURNED") == [],
           "돌려보내기는_이유_필수: 이유가 없거나 글자가 아니거나 200자를 넘거나 쓸 수 없는 글자가 있으면 400 · 요청은 그대로 기다림")
        ok(act(LEAD, r1, "return", {"note": "  가상 돌려보냄  "}).json == {"ok": True} and row(r1) == [("RETURNED", "가상 돌려보냄", z(6, 15), LEAD, None)]
           and box(LEAD) == [] and audit("KPI_SCHED_RETURNED") == [(LEAD, 0, None, "kpi_plan", aj(1, ymd(9, 30), ymd(10, 15)))],
           "돌려보내기: RETURNED · 이유(앞뒤 공백 뺌) · 결정한 시각과 사람 · 처리함에서 사라짐 · 감사로그 KPI_SCHED_RETURNED (이유 문장 없음)")
        s = sched(prog(), 1)
        ok(s["returned"] == {"to": ymd(10, 15), "note": "가상 돌려보냄", "at": z(6, 15)} and s["pending"] is None and s["due"] == ymd(9, 30),
           "주인 화면 returned.note 에 이유가 보임 · 기한은 그대로")
        ok(all(conflict(act(LEAD, r1, w, {"note": "가상"}), "RETURNED") for w in ("confirm", "return")) and conflict(drop(EMP, r1), "RETURNED")
           and conflict(act(LEAD, r1, "return", {"note": ""}), "RETURNED") and row(r1)[0][0] == "RETURNED", "이미 처리된 요청은 409 (이유가 틀려도 상태가 먼저)")

        # ---- 확인되면 기한이 바뀐다 ----
        r3 = ask(EMP, 1, ymd(10, 15), "가" * 200).json["id"]
        ok(q("SELECT length(reason) FROM kpi_sched_changes WHERE id = ?", r3) == [(200,)] and sched(prog(), 1)["returned"] is None and sched(prog(), 1)["pending"]["id"] == r3
           and r3 != r1, "돌려받은 뒤 다시 요청할 수 있음 (이유 200자까지) · 새 요청이 있으면 returned 는 null")
        ok([(i["id"], i["kind"]) for i in inbox(LEAD)["items"]] == [(r3, "SCHED")], "일정_변경_요청_확인되면_기한이_바뀐다: 확인 전 — 팀장 처리함에 SCHED 1건")
        ok(act(LEAD, r3, "confirm").json == {"ok": True} and row(r3) == [("CONFIRMED", None, z(6, 15), LEAD, None)], "팀장이 확인")
        p = prog()
        ok((sched(p, 1)["due"], sched(p, 1)["origDue"]) == (ymd(10, 15), ymd(9, 30)) and sched(p, 1)["pending"] is None and sched(p, 1)["returned"] is None
           and p["next"] == {"sid": 1, "due": ymd(10, 15), "name": "가상 초안"}, "progress_json 의 due 는 새 기한 · origDue 는 옛 기한 · 다음 일정도 새 기한으로")
        ok(inbox(LEAD)["items"] == [] and audit("KPI_SCHED_CONFIRMED") == [(LEAD, 0, None, "kpi_plan", aj(1, ymd(9, 30), ymd(10, 15)))],
           "처리함이 비고 감사로그 KPI_SCHED_CONFIRMED 가 남음 (옛 · 새 기한만)")
        ok(version1() == v1 and q("SELECT status, draft, confirmed_version, updated_at FROM kpi_plans WHERE user_id = ? AND year = ?", EMP, year) == plan_row
           and q("SELECT COUNT(*) FROM kpi_versions WHERE user_id = ?", EMP) == [(1,)], "확인은 확정본(판 1)도 계획서 줄도 고치지 않음 — 실제 기한은 변경 기록에서 읽음")

        # ---- HR 이 대신 ----
        at(6, 16)
        ok(err(ask(EMP, 1, ymd(10, 15)), 400, SAME_DUE), "확인된 새 기한이 지금 기한이다 — 같은 날짜는 400")
        r4 = ask(EMP, 1, ymd(9, 30)).json["id"]  # 원래 기한으로 되돌려 달라는 것도 지금 기한과 다르니 된다
        ok(box(HR) == [] and ids(LEAD) == [r4], "HR 은 바로 위 관리자가 아니라서 처리함에 안 옴")
        ok(act(HR, r4, "confirm").json == {"ok": True} and row(r4) == [("CONFIRMED", None, z(6, 16), HR, LEAD)]
           and audit("KPI_SCHED_CONFIRMED")[-1] == (HR, 1, LEAD, "kpi_plan", aj(1, ymd(10, 15), ymd(9, 30))),
           "HR이_대신_확인하면_원래_관리자가_남는다: 요청 줄 · 감사로그에 on_behalf_of = 팀장 (누른 사람은 HR)")
        ok((sched(prog(), 1)["due"], sched(prog(), 1)["origDue"]) == (ymd(9, 30), ymd(9, 30)), "확인한 시각이 늦은 변경이 먼저 덮음 — 원래 기한으로 돌아옴")
        r5 = ask(EMP, 1, ymd(10, 31)).json["id"]
        ok(act(HR, r5, "return", {"note": "가상 HR 돌려보냄"}).status_code == 200 and row(r5) == [("RETURNED", "가상 HR 돌려보냄", z(6, 16), HR, LEAD)]
           and audit("KPI_SCHED_RETURNED")[-1] == (HR, 1, LEAD, "kpi_plan", aj(1, ymd(9, 30), ymd(10, 31))), "HR 이 돌려보내기도 대신 함 — 원래 관리자가 남음")

        # ---- 결과를 쓴 일정은 못 한다 ----
        n0 = n_rows()
        ok(own(EMP, "result", {"year": year, "sid": 2, "text": "가상 결과"}).status_code == 200 and err(ask(EMP, 2, ymd(12, 5)), 400, HAS_RESULT)
           and err(ask(EMP, 2, "내일", ""), 400, HAS_RESULT) and n_rows() == n0, "결과_쓴_일정은_요청_못_한다: 400 (기한 · 이유 검사보다 먼저)")
        ok(own(EMP, "result", {"year": year, "sid": 2, "text": ""}).status_code == 200 and ask(EMP, 2, ymd(12, 5)).status_code == 200, "결과를 지우면 다시 요청할 수 있음")
        r6 = q("SELECT MAX(id) FROM kpi_sched_changes")[0][0]

        # ---- 평가 시작일부터 막힌다 ----
        ok(ids(LEAD) == [r6], "(2번 일정 요청이 기다리는 중)")
        ok(setyear(ymd(6, 16)).status_code == 200, "(HR 이 평가 시작일을 오늘로 둠)")
        ok(err(ask(EMP, 1, ymd(8, 1)), 403, STARTED) and err(ask(EMP, 99, "내일", ""), 403, STARTED) and prog()["schedOpen"] is False,
           "평가_시작일부터_막힌다: 요청 403 (일정 · 기한 검사보다 먼저) · 화면의 schedOpen 도 false")
        ok(ids(LEAD) == [] and ids(HR) == [], "평가가 시작되면 기다리는 요청이 처리함에서 빠짐")
        ok(all(err(act(LEAD, r6, w, {"note": "가상"}), 403, STARTED) for w in ("confirm", "return")) and err(act(HR, r6, "confirm"), 403, STARTED)
           and err(act(LEAD, r1, "confirm"), 403, STARTED) and err(act(PEER, r6, "confirm"), 403, NO_AUTH) and row(r6)[0][0] == "PENDING",
           "확인 · 돌려보내기도 403 (HR 도 · 이미 처리된 요청도 상태 409 보다 먼저) · 권한 403 이 평가 시작일 403 보다 먼저")
        ok(setyear(ymd(6, 17)).status_code == 200, "(HR 이 평가 시작일을 내일로 둠)")
        ok(prog()["schedOpen"] is True and ids(LEAD) == [r6] and act(LEAD, r6, "confirm").status_code == 200
           and (sched(prog(), 2)["due"], sched(prog(), 2)["origDue"]) == (ymd(12, 5), ymd(11, 20)), "평가 시작일이 내일이면 아직 됨")
        r7 = ask(EMP, 1, ymd(8, 1)).json["id"]
        ok(setyear(ymd(6, 16)).status_code == 200, "(평가 시작일이 다시 오늘)")
        ok(drop(EMP, r7).json == {"ok": True} and row(r7)[0][0] == "CANCELLED", "평가가 시작된 뒤에도 본인 취소는 됨 (기다리는 요청이 남지 않게)")
        ok(setyear(None).status_code == 200, "(HR 이 평가 시작일을 비움)")
        ok(ask(EMP, 1, ymd(8, 1)).status_code == 200 and drop(EMP, q("SELECT MAX(id) FROM kpi_sched_changes")[0][0]).status_code == 200,
           "평가 시작일이 없으면 늘 열려 있음")

        # ---- 처리함: 오래된 것부터 · PLAN 과 함께 · 보이는 사람만 ----
        confirmed(PEER)
        at(6, 16, 4)
        rp = ask(PEER, 1, ymd(10, 5)).json["id"]
        at(6, 16, 5)
        ok(own(NEW, "draft", {"draft": plan}).status_code == 200 and own(NEW, "submit").status_code == 200, "(신입이 계획서를 제출)")
        team1 = inbox(LEAD)["team"]
        at(6, 16, 6)
        r8 = ask(EMP, 1, ymd(8, 15)).json["id"]
        items = inbox(LEAD)["items"]
        ok([(i["kind"], i["userId"]) for i in items] == [("SCHED", PEER), ("PLAN", NEW), ("SCHED", EMP)] and [i["at"] for i in items] == sorted(i["at"] for i in items)
           and items[1]["status"] == "SUBMITTED", "처리함은 계획서 줄(kind PLAN)과 일정 변경 줄(kind SCHED)을 합쳐 오래된 것부터")
        ok(inbox(LEAD)["team"] == team1 and team1["CONFIRMED"] == 2 and team1["SUBMITTED"] == 1, "우리 팀 상태 수(team)는 일정 변경이 있어도 그대로 (확정 2 · 제출 1)")
        x("UPDATE users SET writes_kpi = 0 WHERE id = ?", EMP)
        ok(ids(LEAD) == [rp], "KPI 를 안 쓰게 된 사람의 요청은 처리함에서 빠짐")
        x("UPDATE users SET writes_kpi = 1, is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", EMP)
        ok(ids(LEAD) == [rp], "비활성이 된 사람의 요청도 빠짐")
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", EMP)
        ok(ids(LEAD) == [rp, r8], "(되돌리면 다시 보임)")
        ok(drop(EMP, r8).status_code == 200 and drop(PEER, rp).status_code == 200 and ids(LEAD) == [], "(요청들을 취소)")

        # ---- 대표이사 몫 · 맡기기 · HR 본인 것 ----
        at(6, 17)
        confirmed(HEAD)
        confirmed(HR2)
        rh = ask(HEAD, 1, ymd(10, 6)).json["id"]
        ok(who_(CEO) == [(rh, False)] and box(HR) == [] and err(act(HR, rh, "confirm"), 403, NO_AUTH),
           "대표이사_몫은_맡겨야_대신한다: 본부장의 요청은 대표이사 처리함에 · HR 은 맡기기 전에는 안 보이고 403")
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(6, 17))
        rm = ask(HR2, 1, ymd(10, 7)).json["id"]
        ok(who_(HR) == [(rh, True), (rm, True)] and who_(CEO) == [(rh, False), (rm, False)], "맡기면 HR 처리함에 '대신'으로 옴 · 대표이사 처리함은 그대로")
        ok(who_(HR2) == [(rh, True)] and all(err(act(HR2, rm, w, {"note": "가상"}), 403, NO_AUTH) for w in ("confirm", "return")) and row(rm)[0][0] == "PENDING",
           "HR은_본인_것을_대신_못_한다: 본인 요청은 처리함에 없고 확인 · 돌려보내기 모두 403")
        ok(act(HR, rh, "confirm").status_code == 200 and row(rh) == [("CONFIRMED", None, z(6, 17), HR, CEO)]
           and audit("KPI_SCHED_CONFIRMED", HEAD) == [(HR, 1, CEO, "kpi_plan", aj(1, ymd(9, 30), ymd(10, 6)))], "맡기면 HR 이 대신 확인: on_behalf_of = 대표이사")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        ok(box(HR) == [] and err(act(HR, rm, "confirm"), 403, NO_AUTH) and act(CEO, rm, "confirm").status_code == 200
           and row(rm) == [("CONFIRMED", None, z(6, 17), CEO, None)], "끄면 다시 HR 이 못 함 · 대표이사 본인은 직접 확인 (on_behalf_of 없음)")
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        rh2, rm2 = ask(HEAD, 1, ymd(10, 8)).json["id"], ask(HR2, 1, ymd(10, 9)).json["id"]
        ok(who_(HR) == [(rh2, True), (rm2, True)] and who_(HR2) == [(rh2, True)] and err(act(HR2, rm2, "confirm"), 403, NO_AUTH),
           "바로 위 관리자가 없으면 HR 이 처리 — 그래도 HR 본인 것은 다른 HR 만")
        ok(act(HR, rm2, "confirm").status_code == 200 and row(rm2) == [("CONFIRMED", None, z(6, 17), HR, None)]
           and audit("KPI_SCHED_CONFIRMED", HR2)[-1][:3] == (HR, 1, None), "관리자가 없는 것은 HR 이 확인 (on_behalf_of 없음)")
        ok(drop(HEAD, rh2).status_code == 200, "(요청을 취소)")
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", CEO)

        # ---- 해제하면 바뀐 기한이 초안에 들어간다 ----
        at(6, 18)
        r9 = ask(EMP, 1, ymd(10, 1)).json["id"]
        ok(act(LEAD, r9, "confirm").status_code == 200 and sched(prog(), 1)["due"] == ymd(10, 1), "(1번 일정 기한이 확인됨 — 확정본의 옛 기한은 9월 30일)")
        rx = ask(EMP, 1, ymd(10, 3)).json["id"]
        ok(act(LEAD, rx, "return", {"note": "가상 되돌림"}).status_code == 200
           and sched(prog(), 1)["returned"] == {"to": ymd(10, 3), "note": "가상 되돌림", "at": z(6, 18)}, "(1번 일정 요청을 돌려받음 — 판 1 이 지금 확정본이라 돌려받은 줄이 보임)")
        at(6, 19)
        ok(own(EMP, "request-change", {"reason": "가상 수정 사유"}).status_code == 200 and q("SELECT status FROM kpi_plans WHERE user_id = ?", EMP) == [("CHANGE_REQUESTED",)],
           "(수정 요청)")
        r10 = ask(EMP, 2, ymd(12, 20)).json["id"]
        rp2 = ask(PEER, 1, ymd(10, 5)).json["id"]
        ok(r10 in ids(LEAD) and sched(prog(), 2)["pending"]["id"] == r10, "수정 요청 중에도 일정 변경을 요청할 수 있음 (확정본은 그대로 유효)")
        at(6, 20)
        ok(c(LEAD).post(f"/api/kpi/plans/{EMP}/unlock", base_url=BASE, headers=ORIGIN, json={}).json == {"ok": True}, "팀장이 수정을 풀어 줌")
        draft = c(EMP).get("/api/kpi/me", base_url=BASE).json["plan"]["draft"]
        ok({s["sid"]: s["due"] for it in draft[0]["items"] for s in it["scheds"]} == {1: ymd(10, 1), 2: ymd(12, 5)}
           and [s["name"] for it in draft[0]["items"] for s in it["scheds"]] == ["가상 초안", "가상 마감"],
           "해제하면_바뀐_기한이_초안에_들어간다: 초안의 기한 = 확인된 새 기한 (확인된 변경이 없는 일정은 확정본 기한)")
        ok(row(r10) == [("CANCELLED", None, z(6, 20), LEAD, None)] and sched(prog(), 2)["pending"] is None and row(rp2)[0][0] == "PENDING",
           "그 전에 걸어 둔 다른 PENDING 은 CANCELLED (결정한 시각 · 사람 = 풀어 준 팀장) — 다른 사람의 요청은 그대로")
        ok(audit("KPI_UNLOCKED") == [(LEAD, 0, None, "kpi_plan", json.dumps({"year": year, "cancelled": 1}))], "감사로그 KPI_UNLOCKED 에 취소한 요청 수(cancelled)")
        ok(version1() == v1 and json.loads(v1[0][0])[0]["items"][2]["scheds"][0]["due"] == ymd(9, 30), "확정본(판 1)은 그대로 — 옛 기한")
        ok(sched(prog(), 1)["returned"] == {"to": ymd(10, 3), "note": "가상 되돌림", "at": z(6, 18)}, "고치는 중에도 아직 판 1 이 지금 확정본 — 돌려받은 줄이 보임")
        ok(drop(PEER, rp2).status_code == 200, "(동료의 요청을 취소)")
        n0 = n_rows()
        r = ask(EMP, 1, ymd(10, 2))
        ok(r.status_code == 409 and r.json == {"error": EDITING, "status": "DRAFT"} and n_rows() == n0, "수정 중(DRAFT)에는 일정 변경을 요청할 수 없음 — 409 + 상태")
        its = draft[0]["items"]
        its[2]["scheds"] = [s for s in its[2]["scheds"] if s["sid"] != 2]  # 2번 일정을 지움
        at(6, 21)
        ok(own(EMP, "draft", {"draft": draft}).status_code == 200 and own(EMP, "submit").status_code == 200, "(고쳐서 다시 제출)")
        r = ask(EMP, 1, ymd(10, 2))
        ok(r.status_code == 409 and r.json == {"error": EDITING, "status": "SUBMITTED"}, "제출한 뒤 확정 전(SUBMITTED)에도 409")
        at(6, 22)
        ok(c(LEAD).post(f"/api/kpi/plans/{EMP}/confirm", base_url=BASE, headers=ORIGIN, json={}).status_code == 200
           and q("SELECT version, confirmed_at FROM kpi_versions WHERE user_id = ? ORDER BY version", EMP) == [(1, z(6, 15)), (2, z(6, 22))], "(다시 확정 — 판 2)")
        v2 = json.loads(q("SELECT content FROM kpi_versions WHERE user_id = ? AND version = 2", EMP)[0][0])
        p = prog()
        ok(v2[0]["items"][2]["scheds"] == [{"sid": 1, "due": ymd(10, 1), "name": "가상 초안"}] and p["version"] == 2
           and (sched(p, 1)["due"], sched(p, 1)["origDue"]) == (ymd(10, 1), ymd(10, 1)), "다시 확정하면 판 2 의 기한 = 새 기한 · progress_json 의 origDue 도 새 기한")
        ok(sched(p, 1)["returned"] is None and row(rx) == [("RETURNED", "가상 되돌림", z(6, 18), LEAD, None)],
           "돌려받은_줄은_다시_확정되면_숨는다: 판 2 가 확정되면 그 전에 돌려보낸 줄은 안 보임 (요청 줄은 RETURNED 그대로)")
        ok(version1() == v1, "판 1 은 끝까지 그대로")
        ok(err(ask(EMP, 2, ymd(12, 20)), 400, NO_SID), "새 판에서 지운 일정은 요청할 수 없음")
        at(6, 23)
        r11 = ask(EMP, 1, ymd(10, 20)).json["id"]
        ok(q("SELECT from_due FROM kpi_sched_changes WHERE id = ?", r11) == [(ymd(10, 1),)] and act(LEAD, r11, "confirm").status_code == 200
           and (sched(prog(), 1)["due"], sched(prog(), 1)["origDue"]) == (ymd(10, 20), ymd(10, 1)),
           "새 판 뒤의 변경은 새 판 기한 위에 덮임 (그 전에 확인된 변경은 새 판에 이미 들어 있어 다시 덮지 않음)")

        # ---- HR 이 대신 해제하면 취소된 요청에도 원래 관리자가 남는다 ----
        at(6, 24)
        rq = ask(PEER, 1, ymd(10, 5)).json["id"]
        ok(own(PEER, "request-change", {"reason": "가상 수정 사유"}).status_code == 200
           and c(HR).post(f"/api/kpi/plans/{PEER}/unlock", base_url=BASE, headers=ORIGIN, json={}).json == {"ok": True}
           and row(rq) == [("CANCELLED", None, z(6, 24), HR, LEAD)]
           and audit("KPI_UNLOCKED", PEER) == [(HR, 1, LEAD, "kpi_plan", json.dumps({"year": year, "cancelled": 1}))],
           "HR이_대신_해제하면_취소된_요청에도_원래_관리자가_남는다: on_behalf_of = 팀장 (감사로그 KPI_UNLOCKED 와 같은 사람) · 결정한 사람은 HR")

        # ---- 동시에 여러 번 누르기 ----
        def crowd(uid):
            cl = [app.test_client() for _ in range(5)]  # 같은 사람이 창을 여러 개 열어 놓고 동시에 누른다
            for one in cl:
                login(one, new_token(f"HW-{uid}"))
            return cl

        def together(clients, fn):
            codes = []
            threads = [threading.Thread(target=lambda one=one: codes.append(fn(one).status_code)) for one in clients]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
            return sorted(codes)

        pending_n = lambda: q("SELECT COUNT(*) FROM kpi_sched_changes WHERE user_id = ? AND sid = 1 AND status = 'PENDING'", EMP)[0][0]
        codes = together(crowd(EMP), lambda one: one.post("/api/kpi/me/sched-change", base_url=BASE, headers=ORIGIN,
                                                          json={"year": year, "sid": 1, "due": ymd(10, 25), "reason": "가상 사유"}))
        ok(codes == [200, 409, 409, 409, 409] and pending_n() == 1, f"동시에 다섯 번 요청해도 한 번만 통과 — 나머지는 서버 오류가 아니라 409 ({codes})")
        rc = q("SELECT id FROM kpi_sched_changes WHERE user_id = ? AND status = 'PENDING'", EMP)[0][0]
        n_conf = len(audit("KPI_SCHED_CONFIRMED"))
        codes = together(crowd(LEAD), lambda one: one.post(f"/api/kpi/sched-changes/{rc}/confirm", base_url=BASE, headers=ORIGIN, json={}))
        ok(codes == [200, 409, 409, 409, 409] and len(audit("KPI_SCHED_CONFIRMED")) == n_conf + 1 and row(rc)[0][0] == "CONFIRMED",
           f"동시에 다섯 번 확인해도 한 번만 통과 — 감사로그도 한 번 ({codes})")

        # ---- 본문이 이상해도 서버 오류가 아님 ----
        odd = lambda **kw: c(EMP).post("/api/kpi/me/sched-change", base_url=BASE, headers=ORIGIN, **kw).status_code  # noqa: E731
        ok(all(odd(json=b) == 400 for b in ([], "글", 5, {}, {"year": year}, {"year": year, "sid": 1}, {"year": year, "sid": 1, "due": ymd(10, 28)}))
           and odd(data="not json", content_type="application/json") == 400 and odd() == 400, "본문이 JSON 이 아니거나 모양이 틀려도 400 (서버 오류가 아님)")
        rj = ask(EMP, 1, ymd(10, 28)).json["id"]
        post_ = lambda **kw: c(LEAD).post(f"/api/kpi/sched-changes/{rj}/return", base_url=BASE, headers=ORIGIN, **kw).status_code  # noqa: E731
        ok(all(post_(json=b) == 400 for b in ([], "글", 5, {"note": {"a": 1}})) and post_(data="not json", content_type="application/json") == 400 and post_() == 400
           and row(rj)[0][0] == "PENDING" and drop(EMP, rj).status_code == 200, "돌려보내기 본문이 이상해도 400 (서버 오류가 아님) · 요청은 그대로")

        # ---- 해가 바뀐 뒤: 지난해 계획서의 일정은 못 바꾼다 (처리함에는 올해 것만 오므로 기다리기만 하는 요청이 생기지 않게) ----
        at(1, 3, yr=year + 1)
        n0 = n_rows()
        ok(err(ask(EMP, 1, ymd(12, 20)), 400, THIS_YEAR) and err(ask(EMP, 1, ymd(12, 20), "", year=year), 400, THIS_YEAR) and n_rows() == n0,
           "지난해_일정은_못_바꾼다: 다음 해 1월에는 지난해 계획서의 일정 변경 요청이 400 (이유 · 일정 검사보다 먼저) · 아무것도 안 남김")
        at(6, 25)

        # ---- 감사로그 · 결정 기록 ----
        acts = {r[0] for r in q("SELECT action FROM audit_logs WHERE action LIKE 'KPI_SCHED_%'")}
        ok(acts == {"KPI_SCHED_REQUESTED", "KPI_SCHED_CANCELLED", "KPI_SCHED_CONFIRMED", "KPI_SCHED_RETURNED"}, "감사로그 행동 이름: 요청 · 취소 · 확인 · 돌려보내기")
        ok(all(set(json.loads(r[0])) == {"year", "sid", "from", "to"} for r in q("SELECT after_data FROM audit_logs WHERE action LIKE 'KPI_SCHED_%'")),
           "그 감사로그에는 해 · 일정 · 옛 기한 · 새 기한만")
        ok(q("SELECT COUNT(*) FROM audit_logs WHERE (action LIKE 'KPI_SCHED_%' OR action = 'KPI_UNLOCKED') AND (IFNULL(before_data, '') || IFNULL(after_data, '') || IFNULL(reason, '')) LIKE '%가상%'")
           == [(0,)], "이유 · 돌려보내는 이유 문장은 감사로그에 없음")
        ok(q("SELECT COUNT(*) FROM kpi_sched_changes WHERE user_id BETWEEN 71 AND 79 AND ((status = 'PENDING') <> (decided_at IS NULL) OR (status <> 'PENDING' AND decided_by IS NULL))")
           == [(0,)], "PENDING 을 벗어난 모든 요청(확인 · 돌려보내기 · 본인 취소 · 해제 때 취소)에 결정한 시각과 사람이 있음 (R1)")
    finally:
        auth._now = real_now
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)
        x("DELETE FROM kpi_years WHERE year = ?", year)
        for row_ in saved:
            x("INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?)", *row_)
    ok(q("SELECT year, edit_from, edit_to, eval_start, updated_at, updated_by FROM kpi_years WHERE year = ?", year) == saved, "(작성 기간 설정을 원래대로 돌려 놓음)")


def check_kpi_review(hr) -> None:
    """KPI 현황 (2026-10-21, 2단계 ②): 남의 KPI 보는 범위가 넓어짐 (본부장 = 본부의 팀장 · 팀원, 대표이사 = 본부장 · 팀장 + 자기에게 보고하는 분) ·
    바로 위 관리자가 달마다 '확인' (HR 대신 없음 · 대표이사에게 보고하는 분은 확인 단계가 없음) · 확인한 달의 기록을 고치거나 지우면 그 달 확인이 풀림 ·
    결과의 달은 바뀐 기한으로 센다. 서버가 순서대로 다시 본다 (바로 위 관리자 403 → 대표이사 403 → 해 · 달 400 → 아직 안 온 달 403 → 확정본 404 → 기록 없음 409).
    오늘에 기대는 검사는 모두 시계(auth._now)를 정해 두고 돈다 — 어느 날 돌려도 같다 (R4). 시계가 바뀌면 창은 새로 연다.
    확인 메모 · 확인했습니다 · 답글 · 새 답글 표시는 이 검사의 뒤쪽 절 (바로 위 관리자만 쓰고 · 쓴 사람이 쓴 날에만 고치고 · 본인만 확인하고 · 감사로그는 없음).
    KPI 현황 명단(/api/kpi/board)과 처리할 일 숫자(inbox 의 review)는 맨 뒤 절 (팀장은 팀원만 · 본부장은 본부 전체 · 대표이사는 팀원 줄 없이 팀 숫자만 · HR 은 전체 · 직원은 403).
    뒤 절(알람 · 명단 · 경영계획)은 같은 가상 조직과 아래 도구를 이어 쓴다."""
    import kpi
    print("KPI 현황 (보는 범위 · 월간 확인)")
    # 이 검사만의 가상 조직: 본부(71) 밑 팀 둘(72 · 73), 대표이사 직속 팀(74), 장이 없는 '임원'(75)
    for dept in ((71, "R71", "가상R본부", "DIVISION", None), (72, "R72", "가상R팀A", "TEAM", 71), (73, "R73", "가상R팀B", "TEAM", 71),
                 (74, "R74", "가상R직속팀", "DIVISION", None), (75, "R75", "임원", "DIVISION", None)):
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (?, ?, ?, ?, ?)", *dept)
    HR, HEAD, LEAD, EMP, EMP2, LEAD2, PEER2, DIRECT, EXEC, CEO, DMEMBER = 1, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100
    cast = {HEAD: ("가상R본부장", 71, "DIVISION_HEAD", 1), LEAD: ("가상R팀장", 72, "TEAM_LEADER", 1), EMP: ("가상R직원", 72, None, 1),
            EMP2: ("가상R동료", 72, None, 1), LEAD2: ("가상R다른팀장", 73, "TEAM_LEADER", 1), PEER2: ("가상R다른팀원", 73, None, 1),
            DIRECT: ("가상R직속팀장", 74, "TEAM_LEADER", 1), EXEC: ("가상R임원직원", 75, None, 1), CEO: ("가상R대표", 71, "CEO", 0),
            DMEMBER: ("가상R직속팀원", 74, None, 1)}  # 직속 팀의 보통 팀원 — 계획서가 없다 (403 이 404 보다 먼저인 것을 보려고)
    for uid, (name, dept, role, writes) in cast.items():
        x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, writes_kpi) VALUES (?, ?, ?, ?, ?, 1, 1, ?)",
          uid, name, f"r{uid}@example.invalid", dept, f"HW-{uid}", writes)
        if role:
            x("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", uid, role)

    year, real_now = kpi.plan_year(), auth._now  # 진짜 오늘의 올해 — 아래 날짜는 모두 이 해 안 (시계를 정해 놓고 돈다)
    ymd = lambda m, d: f"{year}-{m:02d}-{d:02d}"  # noqa: E731
    z = lambda m, d, hh=3, mm=0: f"{year}-{m:02d}-{d:02d}T{hh:02d}:{mm:02d}:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def at(m, d, hh=3, mm=0):
        """시계를 UTC 로 (올해 m월 d일 hh:mm) 에 맞춘다. 기본 03:00 = 한국 정오."""
        t = datetime(year, m, d, hh, mm, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    NO_VIEW, NOT_BOSS = {"error": "이 KPI를 볼 권한이 없습니다."}, {"error": "바로 위 관리자만 확인할 수 있습니다."}
    TO_CEO, FUTURE = {"error": "대표이사에게 보고하는 분은 월간 확인이 없습니다."}, {"error": "아직 오지 않은 달입니다."}
    NO_PLAN, NO_REC = {"error": "확정된 KPI가 없습니다."}, {"error": "확인할 기록이 없습니다."}
    hit = lambda r, code, body: r.status_code == code and r.json == body  # noqa: E731
    prog = lambda uid, who=None: c(uid if who is None else who).get(f"/api/kpi/progress/{uid}?year={year}", base_url=BASE)  # noqa: E731
    plan_of = lambda uid, who: c(who).get(f"/api/kpi/plans/{uid}", base_url=BASE)  # noqa: E731
    sees = lambda who, uid: prog(uid, who).status_code == 200 and plan_of(uid, who).status_code == 200  # noqa: E731
    closed = lambda who, uid: hit(prog(uid, who), 403, NO_VIEW) and hit(plan_of(uid, who), 403, NO_VIEW)  # noqa: E731
    check = lambda who, uid, month, **over: c(who).post(f"/api/kpi/plans/{uid}/check", base_url=BASE, headers=ORIGIN,  # noqa: E731
                                                       json={"year": year, "month": month, **over})
    put = lambda who, what, body: c(who).post(f"/api/kpi/me/{what}", base_url=BASE, headers=ORIGIN, json={"year": year, **body})  # noqa: E731
    line = lambda who, m, text="가상 한 줄": put(who, "month", {"rid": 1, "month": m, "text": text})  # noqa: E731
    result = lambda who, sid, text="가상 결과": put(who, "result", {"sid": sid, "text": text})  # noqa: E731
    checks = lambda uid: prog(uid).json["checks"]  # noqa: E731  — 본인 화면에 붙은 확인
    n_checks = lambda: q("SELECT COUNT(*) FROM kpi_month_checks WHERE user_id BETWEEN 91 AND 99")[0][0]  # noqa: E731
    audit = lambda uid: q("SELECT actor_user_id, actor_is_hr, on_behalf_of, entity_type, after_data FROM audit_logs"  # noqa: E731
                          " WHERE action = 'KPI_MONTH_CHECKED' AND entity_id = ? ORDER BY id", uid)
    aj = lambda m: json.dumps({"year": year, "month": m})  # noqa: E731
    NOT_WRITER, BAD_YEAR = {"error": "바로 위 관리자만 메모를 남길 수 있습니다."}, {"error": "해가 올바르지 않습니다."}
    WRONG_DAY, NOT_AUTHOR = {"error": "쓴 날에만 고치거나 지울 수 있습니다."}, {"error": "쓴 사람만 고치거나 지울 수 있습니다."}
    NOT_REPLIER = {"error": "단 사람만 지울 수 있습니다."}
    memos = lambda uid, who: c(who).get(f"/api/kpi/memos/{uid}", base_url=BASE)  # noqa: E731
    view = lambda uid, who: memos(uid, who).json  # noqa: E731
    one = lambda uid, who, mid: next(m for m in view(uid, who)["memos"] if m["id"] == mid)  # noqa: E731
    memo_post = lambda who, uid, text="가상 메모", **over: c(who).post(f"/api/kpi/memos/{uid}", base_url=BASE, headers=ORIGIN,  # noqa: E731
                                                                      json={"year": year, "text": text, **over})
    memo_do = lambda who, mid, what, **body: c(who).post(f"/api/kpi/memo/{mid}/{what}", base_url=BASE, headers=ORIGIN, json=body)  # noqa: E731
    rp_del = lambda who, rpid: c(who).post(f"/api/kpi/memo-reply/{rpid}/delete", base_url=BASE, headers=ORIGIN, json={})  # noqa: E731
    seen = lambda who, uid: c(who).post(f"/api/kpi/memos/{uid}/seen", base_url=BASE, headers=ORIGIN, json={"year": year})  # noqa: E731
    memo_row = lambda mid: q("SELECT text, edited_at, ack_at, seen_at FROM kpi_memos WHERE id = ?", mid)  # noqa: E731
    n_memos = lambda: q("SELECT COUNT(*) FROM kpi_memos")[0][0]  # noqa: E731
    n_replies = lambda mid: q("SELECT COUNT(*) FROM kpi_memo_replies WHERE memo_id = ?", mid)[0][0]  # noqa: E731
    sched_due = lambda uid, sid: next(s["due"] for i in prog(uid).json["items"] for s in i.get("scheds", []) if s["sid"] == sid)  # noqa: E731

    def item(name, w, kind, **more):
        return {"action": name, "formula": "가상 측정식", "weight": w, "owner": "가상R직원", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
                "D": "가상 d", "kind": kind, "target": None, "unit": "", "scheds": [], **more}

    def confirmed(uid, when=None):
        """계획서를 바로 확정된 모습으로 넣는다: 정기 2 (실행목표 1 · 2) + 비정기 1 (실행목표 3, 일정 1 = 3/20 · 일정 2 = 5/20). when(없으면 2월 10일)에 확정."""
        content = json.dumps([{"team": "가상 팀목표", "personal": "가상 개인목표", "items": [
            {**item("가상 정기 업무 1", 40, "reg", target=12, unit="건"), "rid": 1}, {**item("가상 정기 업무 2", 30, "reg"), "rid": 2},
            {**item("가상 비정기 업무", 30, "irr", scheds=[{"sid": 1, "due": ymd(3, 20), "name": "가상 초안"}, {"sid": 2, "due": ymd(5, 20), "name": "가상 마감"}]),
             "rid": 3}]}], ensure_ascii=False)
        x("INSERT INTO kpi_plans (user_id, year, status, draft, confirmed_version, updated_at) VALUES (?, ?, 'CONFIRMED', ?, 1, '2000-01-01T00:00:00Z')", uid, year, content)
        x("INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by) VALUES (?, ?, 1, ?, ?, ?)", uid, year, content, when or z(2, 10), LEAD)

    for uid in (HEAD, LEAD, EMP, EMP2, LEAD2, DIRECT, EXEC):  # 동료 팀원(PEER2) 만 계획서가 없다 — '확정본 없음 404' 를 보려고
        confirmed(uid)
    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    for uid in other_ceos:  # 이 가상 조직의 대표이사는 내가 만든 한 명뿐이게
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
    anon = app.test_client()
    ev_saved = q("SELECT eval_start FROM kpi_years WHERE year = ?", year)  # 일정 변경은 평가 시작일부터 막힌다 — 앞 검사가 정해 둔 날짜에 기대지 않는다
    x("UPDATE kpi_years SET eval_start = NULL WHERE year = ?", year)
    try:
        at(4, 5)  # 한국 4월 5일 12시
        ok(anon.post(f"/api/kpi/plans/{EMP}/check", base_url=BASE, headers=ORIGIN, json={"year": year, "month": 3}).status_code == 401, "로그인 없이는 401 (월간 확인)")

        # ---- 보는 범위 ----
        ok(all(sees(HEAD, u) for u in (EMP, EMP2, LEAD, LEAD2)) and prog(EMP, HEAD).json == prog(EMP).json,
           "보는_범위가_넓어졌다: 본부장은 본부의 팀원 · 팀장(다른 팀 포함)의 진행 기록 · 계획서를 봄 (본인 화면과 같은 내용)")
        ok(closed(HEAD, DIRECT) and closed(HEAD, EXEC), "본부장도 본부 밖(대표이사 직속 팀장 · '임원' 소속)은 못 봄")
        ok(all(sees(CEO, u) for u in (LEAD, HEAD, DIRECT, EXEC)), "대표이사는 팀장 · 본부장 · 직속 팀장 · '임원' 소속(자기에게 보고하는 분)을 봄")
        ok(all(closed(CEO, u) for u in (EMP, EMP2, PEER2)), "대표이사는 팀원 KPI 를 응답으로 받지 않음: 진행 기록 · 계획서 모두 403")
        ok(closed(CEO, DMEMBER) and hit(memos(DMEMBER, CEO), 403, NO_VIEW) and closed(HEAD, DMEMBER) and closed(EMP, DMEMBER),
           "대표이사는 직속 팀의 팀원 KPI 도 못 봄: 진행 기록 · 계획서 · 메모 모두 403 (그 사람은 계획서가 없는데도 404 가 아니라 403 — 보는 범위가 먼저)")
        ok(prog(DMEMBER, DIRECT).status_code == 404 and plan_of(DMEMBER, DIRECT).status_code == 404 and prog(DMEMBER, HR).status_code == 404
           and view(DMEMBER, DIRECT)["role"] == "writer" and view(DMEMBER, DIRECT)["writer"]["id"] == DIRECT and view(DMEMBER, DIRECT)["canWrite"] is False,
           "직속 팀의 팀원은 직속 팀장(바로 위 관리자)과 HR 이 봄 (계획서가 없어 404) · 메모는 직속 팀장이 쓰는 사람 (계획서가 없어 canWrite 거짓)")
        ok(closed(LEAD2, EMP) and closed(EMP2, EMP) and closed(LEAD, LEAD2) and closed(EMP, EMP2), "다른 팀장 · 동료 팀원 · 팀장끼리 · 팀원끼리는 못 봄 403")
        ok(all(sees(w, EMP) for w in (EMP, LEAD, HR)) and all(sees(w, LEAD) for w in (LEAD, HEAD, CEO, HR)), "이미 되던 것은 그대로: 본인 · 바로 위 관리자 · HR")

        # ---- 확인하면 본인 화면에 붙는다 ----
        p = prog(EMP).json
        ok(p["checkStep"] is True and p["checks"] == {} and n_checks() == 0, "(확인 전: 확인 단계가 있고 확인한 달은 없음)")
        ok(line(EMP, 3, "가상 3월 줄").status_code == 200, "(팀원이 3월 한 줄을 씀)")
        r = check(LEAD, EMP, 3)
        ok(hit(r, 200, {"ok": True}) and checks(EMP) == {"3": z(4, 5)} and prog(EMP, LEAD).json["checks"] == {"3": z(4, 5)},
           "팀장이_확인하면_본인_화면에_붙는다: 팀장이 3월 확인 → 본인 · 팀장 화면의 checks[\"3\"] 에 확인한 시각")
        ok(q("SELECT user_id, year, month, checked_at, checked_by FROM kpi_month_checks WHERE user_id = ?", EMP) == [(EMP, year, 3, z(4, 5), LEAD)],
           "확인 줄: 그 사람 · 해 · 달 · 시각 · 확인한 사람(팀장)")
        ok(audit(EMP) == [(LEAD, 0, None, "kpi_plan", aj(3))] and "가상" not in audit(EMP)[0][4], "감사로그 KPI_MONTH_CHECKED: 해 · 달만 (내용 문장 없음)")

        # ---- 두 번 눌러도 한 번 ----
        at(4, 6)
        ok(hit(check(LEAD, EMP, 3), 200, {"ok": True}) and n_checks() == 1 and len(audit(EMP)) == 1 and checks(EMP) == {"3": z(4, 5)},
           "두_번_눌러도_한_번: 같은 확인을 또 보내면 200 · 줄 하나 · 감사로그 하나 · 처음 시각 그대로")

        # ---- 고치면 풀린다 ----
        ok(line(EMP, 3, "가상 고친 3월 줄").status_code == 200 and checks(EMP) == {} and n_checks() == 0,
           "고치면_확인이_풀린다: 3월 한 줄을 고치면 3월 확인이 풀림")
        ok(check(LEAD, EMP, 3).status_code == 200 and set(checks(EMP)) == {"3"} and len(audit(EMP)) == 2, "(다시 확인 — 새로 확인한 것이라 감사로그가 한 줄 더)")
        ok(line(EMP, 3, "").status_code == 200 and checks(EMP) == {} and hit(check(LEAD, EMP, 3), 409, NO_REC),
           "한 줄을 지워도 풀림 — 기록이 없어지면 다시 확인할 수도 없음 (409)")
        ok(line(EMP, 3).status_code == 200 and check(LEAD, EMP, 3).status_code == 200 and set(checks(EMP)) == {"3"}, "(다시 써서 확인)")
        ok(line(EMP, 4, "가상 4월 줄").status_code == 200 and line(EMP2, 3).status_code == 200 and set(checks(EMP)) == {"3"},
           "다른 달 줄 · 다른 사람의 3월 줄을 써도 3월 확인은 그대로")
        ok(result(EMP, 1).status_code == 200 and checks(EMP) == {}, "3월 기한 일정(3/20)의 결과를 쓰면 3월 확인이 풀림")
        ok(check(LEAD, EMP, 3).status_code == 200 and result(EMP, 2).status_code == 200 and set(checks(EMP)) == {"3"},
           "5월 기한 일정(5/20)의 결과를 써도 3월 확인은 그대로")
        ok(result(EMP, 1, "").status_code == 200 and checks(EMP) == {} and check(LEAD, EMP, 3).status_code == 200 and set(checks(EMP)) == {"3"},
           "3월 기한 일정의 결과를 지워도 3월 확인이 풀림 (줄은 남아 있으니 다시 확인은 됨)")
        ok(put(EMP, "progress", {"rid": 1, "actual": 3}).status_code == 200 and put(EMP, "progress", {"rid": 2, "pct": 40}).status_code == 200
           and put(EMP, "progress", {"rid": 2, "why": "가상 이유"}).status_code == 200 and set(checks(EMP)) == {"3"},
           "실적 · 진행률 · 늦어지는 이유를 고쳐도 확인은 그대로")

        # ---- 결과의 달은 바뀐 기한으로 센다 ----
        at(5, 25)
        ok(line(EMP2, 5, "가상 5월 줄").status_code == 200, "바뀐_기한의_달로_센다: (동료가 5월 한 줄을 씀)")
        r = c(EMP2).post("/api/kpi/me/sched-change", base_url=BASE, headers=ORIGIN, json={"year": year, "sid": 2, "due": ymd(7, 10), "reason": "가상 사유"})
        ok(r.status_code == 200 and c(LEAD).post(f"/api/kpi/sched-changes/{r.json['id']}/confirm", base_url=BASE, headers=ORIGIN, json={}).status_code == 200
           and sched_due(EMP2, 2) == ymd(7, 10), "(5/20 일정을 7/10 으로 일정 변경 — 팀장이 확인)")
        at(7, 12)
        ok(line(EMP2, 7, "가상 7월 줄").status_code == 200 and check(LEAD, EMP2, 5).status_code == 200 and check(LEAD, EMP2, 7).status_code == 200
           and set(checks(EMP2)) == {"5", "7"}, "(5월 · 7월 둘 다 확인해 둠)")
        ok(result(EMP2, 2).status_code == 200 and set(checks(EMP2)) == {"5"},
           "그 일정 결과를 쓰면 바뀐 기한의 달(7월)만 풀림 — 처음 기한의 달(5월)은 그대로")

        # ---- HR 은 대신 못 한다 ----
        n0, a0 = n_checks(), len(audit(EMP))
        ok(hit(check(HR, EMP, 4), 403, NOT_BOSS) and hit(check(CEO, EMP, 4), 403, NOT_BOSS), "HR은_월간_확인을_대신_못_한다: HR · 대표이사(바로 위 관리자가 아님) 모두 403")
        ok(c(CEO).post("/api/kpi/delegation", base_url=BASE, headers=ORIGIN, json={"on": True}).json == {"ok": True, "on": True}
           and all(hit(check(HR, u, m), 403, NOT_BOSS) for u, m in ((EMP, 4), (EMP2, 7), (HEAD, 3))) and n_checks() == n0 and len(audit(EMP)) == a0,
           "대표이사가 HR 에 맡김을 켜도 HR 은 403 (같은 문구) · 아무것도 안 남음")
        c(CEO).post("/api/kpi/delegation", base_url=BASE, headers=ORIGIN, json={"on": False})

        # ---- 대표이사에게 보고하는 분은 확인이 없다 ----
        ok(all(hit(check(CEO, u, 3), 403, TO_CEO) for u in (HEAD, DIRECT, EXEC)) and n_checks() == n0,
           "대표이사에게_보고하는_분은_확인이_없다: 대표이사가 본부장 · 직속 팀장 · '임원' 소속을 확인하면 403")
        ok(all(prog(u, CEO).json["checkStep"] is False for u in (HEAD, DIRECT, EXEC)) and all(prog(u).json["checkStep"] is True for u in (EMP, EMP2, LEAD, LEAD2)),
           "checkStep: 대표이사에게 보고하는 분은 거짓 · 나머지는 참")
        ok(hit(check(HEAD, LEAD, 3), 409, NO_REC) and hit(check(HEAD, EMP, 4), 403, NOT_BOSS), "본부장은 팀장의 확인을 하고(기록이 없으면 409) 팀원의 확인은 못 함")

        # ---- 거절 순서 ----
        ok(hit(check(LEAD2, EMP, 3), 403, NOT_BOSS) and hit(check(LEAD2, EMP, 13), 403, NOT_BOSS) and hit(check(LEAD2, EMP, 8, year=year - 1), 403, NOT_BOSS),
           "확인_거절_순서: 다른 팀장은 달 · 해가 이상해도 먼저 403")
        ok(hit(check(CEO, HEAD, 13), 403, TO_CEO) and hit(check(CEO, HEAD, 3, year=year - 1), 403, TO_CEO) and hit(check(CEO, DIRECT, "3"), 403, TO_CEO),
           "확인_거절_순서: 대표이사가 보고하는 분을 확인하면 달 · 해가 이상해도 먼저 403 (대표이사에게 보고하는 분은 확인이 없음 → 해 · 달 400)")
        ok(all(check(LEAD, EMP, m).status_code == 400 for m in (13, 0, -1, "3", True, 3.0, None, [3], 10 ** 30))
           and all(check(LEAD, EMP, 3, year=y).status_code == 400 for y in (year - 1, year + 1, "%d" % year, None, True, year + 0.5, 0))
           and c(LEAD).post(f"/api/kpi/plans/{EMP}/check", base_url=BASE, headers=ORIGIN, json={"month": 3}).status_code == 400
           and c(LEAD).post(f"/api/kpi/plans/{EMP}/check", base_url=BASE, headers=ORIGIN, data="not json", content_type="application/json").status_code == 400,
           "달이 1~12 정수가 아니거나 해가 올해 정수가 아니거나 본문이 이상하면 400")
        ok(hit(check(LEAD, EMP, 8), 403, FUTURE) and hit(check(LEAD, EMP, 12), 403, FUTURE) and hit(check(LEAD2, PEER2, 8), 403, FUTURE),
           "오늘(한국 7월 12일)보다 뒤의 달은 403 — 확정본 404 보다 먼저")
        ok(hit(check(LEAD2, PEER2, 3), 404, NO_PLAN), "그해 확정본이 없으면 404")
        ok(hit(check(LEAD, EMP, 6), 409, NO_REC) and hit(check(LEAD, EMP, 2), 409, NO_REC), "기록이 없는 달은 409")
        ok(c(LEAD).post(f"/api/kpi/plans/{EMP}/check", base_url=BASE, headers={}, json={"year": year, "month": 4}).status_code == 403 and n_checks() == n0,
           "포털 화면에서만 (Origin 없으면 403) · 아무것도 안 남음")
        ok(hit(check(LEAD, EMP, 4), 200, {"ok": True}) and set(checks(EMP)) == {"3", "4"}, "(앞의 거절이 아무것도 바꾸지 않았다 — 4월은 이제 확인됨)")

        # ---- 팀을 옮기면 ----
        x("UPDATE users SET department_id = 73 WHERE id = ?", EMP2)  # 팀 B 로
        ok(check(LEAD2, EMP2, 7).status_code == 200, "팀을_옮기면_새_관리자가_확인한다: 새 팀장이 확인 (결과를 써서 풀려 있던 7월)")
        ok(hit(check(LEAD, EMP2, 7), 403, NOT_BOSS) and q("SELECT checked_by FROM kpi_month_checks WHERE user_id = ? AND month = 7", EMP2) == [(LEAD2,)]
           and audit(EMP2)[-1] == (LEAD2, 0, None, "kpi_plan", aj(7)), "옛 팀장은 403 · 확인한 사람 · 감사로그는 새 팀장")
        ok(closed(LEAD, EMP2) and sees(LEAD2, EMP2) and sees(HEAD, EMP2), "보는 범위도 따라 바뀜: 옛 팀장은 못 보고 새 팀장 · 본부장은 봄")

        # ---- 확인 메모 · 확인했습니다 · 답글 (2단계 ②, Task 4) ----
        x("UPDATE users SET title = '팀장' WHERE id = ?", LEAD)  # 이름 옆에 붙는 직급 — 직급이 없는 사람(직원)은 이름만
        n_audit = lambda: q("SELECT COUNT(*) FROM audit_logs WHERE action NOT IN ('LOGIN', 'CEO_DELEGATION_ON', 'CEO_DELEGATION_OFF')")[0][0]  # noqa: E731
        audit0 = n_audit()  # 메모 일은 감사로그를 남기지 않는다 — 끝에서 견준다 (로그인 · 이 절이 켜고 끄는 맡김은 뺀다)
        at(8, 3)  # 한국 8월 3일 12시
        ok(anon.get(f"/api/kpi/memos/{EMP}", base_url=BASE).status_code == 401
           and anon.post(f"/api/kpi/memos/{EMP}", base_url=BASE, headers=ORIGIN, json={"year": year, "text": "가상"}).status_code == 401
           and anon.post("/api/kpi/memo/1/ack", base_url=BASE, headers=ORIGIN, json={}).status_code == 401, "로그인 없이는 401 (확인 메모)")

        # 팀장만 쓴다
        r = memo_post(LEAD, EMP, "가상 첫 메모")
        m1 = r.json.get("id")
        ok(r.status_code == 200 and r.json == {"ok": True, "id": m1}
           and q("SELECT user_id, year, author_id, text, created_at, edited_at, ack_at, seen_at FROM kpi_memos") == [(EMP, year, LEAD, "가상 첫 메모", z(8, 3), None, None, None)],
           "팀장만_쓴다: 바로 위 관리자(팀장)가 메모를 남김 — 쓴 사람 · 쓴 시각, 고친 때 · 확인 · 본 때는 비어 있음")
        ok(all(hit(memo_post(w, EMP), 403, NOT_WRITER) for w in (HEAD, LEAD2, EMP, EMP2)) and n_memos() == 1,
           "팀장만_쓴다: 본부장(윗사람) · 다른 팀장 · 본인 · 동료 팀원은 403 — 아무것도 안 남음")
        ok(c(LEAD).post(f"/api/kpi/memos/{EMP}", base_url=BASE, headers={}, json={"year": year, "text": "가상"}).status_code == 403 and n_memos() == 1,
           "포털 화면에서만 (Origin 없으면 403) · 아무것도 안 남음")

        # 대표이사 · HR 은 못 쓴다
        ok(all(hit(memo_post(HR, u), 403, NOT_WRITER) for u in (EMP, HEAD, DIRECT)) and all(hit(memo_post(CEO, u), 403, NOT_WRITER) for u in (HEAD, DIRECT, EXEC))
           and n_memos() == 1, "대표이사_HR은_메모를_못_쓴다: HR 은 누구에게도, 대표이사는 자기에게 보고하는 분(본부장 · 직속 팀장 · '임원' 소속)에게도 403")
        ok(c(CEO).post("/api/kpi/delegation", base_url=BASE, headers=ORIGIN, json={"on": True}).json == {"ok": True, "on": True}
           and all(hit(memo_post(HR, u), 403, NOT_WRITER) for u in (EMP, HEAD, DIRECT, EXEC)) and n_memos() == 1,
           "대표이사가 HR 에 맡김을 켜도 HR 은 403 (같은 문구)")
        c(CEO).post("/api/kpi/delegation", base_url=BASE, headers=ORIGIN, json={"on": False})
        ok(all((j := view(HEAD, w))["writer"] is None and j["canWrite"] is False and j["memos"] == [] for w in (HEAD, CEO, HR))
           and view(HEAD, HEAD)["role"] == "owner" and view(HEAD, CEO)["role"] == "" and view(HEAD, HR)["role"] == "",
           "대표이사에게 보고하는 분(본부장)의 메모란: writer 없음 · canWrite 거짓 — 대표이사도 쓰는 사람이 아니라 role 은 빈 글자")
        ok(view(EXEC, CEO)["writer"] is None and view(DIRECT, CEO)["writer"] is None and view(EXEC, CEO)["role"] == "",
           "(직속 팀장 · '임원' 소속도 같다)")

        # 읽기 · 모양
        mj = view(EMP, LEAD)
        ok(set(mj) == {"role", "canWrite", "writer", "memos"} and mj["role"] == "writer" and mj["canWrite"] is True
           and mj["writer"] == {"id": LEAD, "name": "가상R팀장", "title": "팀장"}
           and mj["memos"] == [{"id": m1, "at": z(8, 3), "authorId": LEAD, "by": "가상R팀장 팀장", "text": "가상 첫 메모", "edited": False, "ack": None,
                                "fresh": 0, "canEdit": True, "replies": []}],
           "GET 모양: role · canWrite · writer{id name title} · memos[{id at authorId by text edited ack fresh canEdit replies}] — 쓴 사람은 canEdit 참 · by 는 이름 + 직급")
        ok(all((j := view(EMP, w))["role"] == "" and j["canWrite"] is False and j["writer"]["id"] == LEAD and len(j["memos"]) == 1
               and not any(m["canEdit"] for m in j["memos"]) for w in (HEAD, HR)),
           "윗사람_HR은_읽기만: 본부장 · HR 은 읽기 200 — role 빈 글자 · canWrite 거짓 · canEdit 모두 거짓")
        mj = view(EMP, EMP)
        ok(mj["role"] == "owner" and mj["canWrite"] is False and mj["writer"]["id"] == LEAD and not mj["memos"][0]["canEdit"] and mj["memos"][0]["fresh"] == 0,
           "본인 화면: role owner · canWrite 거짓 · canEdit 거짓 · fresh 0")
        ok(all(hit(memos(EMP, w), 403, NO_VIEW) for w in (CEO, LEAD2, EMP2)), "볼 수 없는 사람(대표이사 · 다른 팀장 · 동료 팀원)은 메모란도 403")
        ok(view(LEAD, CEO)["role"] == "" and view(LEAD, CEO)["writer"]["id"] == HEAD and view(LEAD, CEO)["canWrite"] is False,
           "대표이사는 팀장의 메모란을 읽기만 (쓰는 사람은 본부장)")
        mj = view(PEER2, LEAD2)
        ok(mj["role"] == "writer" and mj["canWrite"] is False and mj["memos"] == [], "확정본이 없는 사람: 바로 위 관리자라도 canWrite 거짓")
        ok(hit(memo_post(LEAD2, PEER2), 404, NO_PLAN), "(그 사람에게 쓰려 하면 404)")

        # 본인이 확인했습니다
        at(8, 4)
        ok(hit(memo_do(EMP, m1, "ack"), 200, {"ok": True}) and memo_row(m1) == [("가상 첫 메모", None, z(8, 4), None)] and one(EMP, LEAD, m1)["ack"] == z(8, 4),
           "본인이_확인했습니다: 본인이 누르면 ack 에 그 시각")
        at(8, 5)
        ok(hit(memo_do(EMP, m1, "ack"), 200, {"ok": True}) and memo_row(m1)[0][2] == z(8, 4) and one(EMP, EMP, m1)["ack"] == z(8, 4),
           "또 눌러도 200 · 처음 시각 그대로")
        ok(all(memo_do(w, m1, "ack").status_code == 403 for w in (LEAD, HEAD, HR, CEO, EMP2)) and memo_row(m1)[0][2] == z(8, 4),
           "팀장 · 본부장 · HR · 대표이사 · 남은 확인 403 (본인만) · 시각 그대로")
        ok(memo_do(EMP, 999999, "ack").status_code == 404 and memo_do(LEAD, 999999, "ack").status_code == 404, "없는 메모는 누가 눌러도 404 (권한 403 보다 먼저)")

        # 답글 규칙
        at(8, 6)
        m2 = memo_post(LEAD, EMP, "가상 둘째 메모").json["id"]
        at(8, 6, 4)
        r = memo_do(EMP, m2, "reply", text="가상 본인 답글")
        rp1 = r.json.get("id")
        ok(r.status_code == 200 and r.json == {"ok": True, "id": rp1} and memo_row(m2)[0][2] == z(8, 6, 4)
           and q("SELECT memo_id, author_id, who, text, created_at FROM kpi_memo_replies WHERE id = ?", rp1) == [(m2, EMP, "owner", "가상 본인 답글", z(8, 6, 4))],
           "답글_규칙: 본인 답글 → 확인한 것으로 침 (ack = 답글 시각) · who owner")
        at(8, 6, 5)
        ok(memo_do(EMP, m2, "reply", text="가상 본인 답글 2").status_code == 200 and memo_row(m2)[0][2] == z(8, 6, 4), "(본인 답글을 또 달아도 처음 확인 시각 그대로)")
        at(8, 6, 6)
        r = memo_do(LEAD, m2, "reply", text="가상 팀장 답글")
        rp3 = r.json.get("id")
        ok(r.status_code == 200 and memo_row(m2)[0][2] is None and q("SELECT who, author_id FROM kpi_memo_replies WHERE id = ?", rp3) == [("writer", LEAD)]
           and one(EMP, EMP, m2)["ack"] is None, "팀장 답글 → ack 비움 (본인이 다시 확인) · who writer")
        at(8, 6, 7)
        ok(memo_do(EMP, m2, "reply", text="가상 본인 답글 3").status_code == 200 and memo_row(m2)[0][2] == z(8, 6, 7), "(본인이 다시 답하면 다시 확인됨)")
        rs = one(EMP, EMP, m2)["replies"]
        ok([(p["who"], p["by"], p["text"]) for p in rs] == [("owner", "가상R직원", "가상 본인 답글"), ("owner", "가상R직원", "가상 본인 답글 2"),
                                                           ("writer", "가상R팀장 팀장", "가상 팀장 답글"), ("owner", "가상R직원", "가상 본인 답글 3")]
           and [p["at"] for p in rs] == [z(8, 6, 4), z(8, 6, 5), z(8, 6, 6), z(8, 6, 7)] and set(rs[0]) == {"id", "at", "who", "by", "text", "canDelete"},
           "답글은 오래된 것부터 — 모양 {id at who by text canDelete}")
        ok([p["canDelete"] for p in rs] == [True, True, False, True] and [p["canDelete"] for p in one(EMP, LEAD, m2)["replies"]] == [False, False, True, False]
           and not any(p["canDelete"] for p in one(EMP, HEAD, m2)["replies"]), "canDelete: 단 사람 본인이고 단 날만 (본부장은 모두 거짓)")
        ok(all(memo_do(w, m2, "reply", text="가상 끼어들기").status_code == 403 for w in (EMP2, HEAD, HR, CEO, LEAD2)) and memo_do(EMP2, m2, "reply", text="").status_code == 403
           and memo_do(EMP, 999999, "reply", text="").status_code == 404 and n_replies(m2) == 4,
           "다른 팀원 · 본부장 · HR · 대표이사 · 다른 팀장 답글 403 (글 400 보다 먼저) · 없는 메모 404")
        ok(all(memo_do(w, m2, "reply", text=t).status_code == 400 for w in (EMP, LEAD) for t in ("", "  \n ", "가" * 201, "가\x00나", "\ud800", None, 5, ["가"]))
           and n_replies(m2) == 4 and memo_row(m2)[0][2] == z(8, 6, 7), "답글 글: 빈 글 · 공백뿐 · 201자 · 제어문자 · 짝 없는 글자 · 글이 아닌 것은 400 — 아무것도 안 바뀜")
        ok(memo_do(EMP, m2, "reply", text="  " + "가" * 200 + " \n").status_code == 200 and q("SELECT length(text) FROM kpi_memo_replies ORDER BY id DESC LIMIT 1") == [(200,)],
           "200자(앞뒤 공백은 뺌)까지 됨")
        at(8, 6, 8)
        ok(memo_do(LEAD, m1, "reply", text="가상 팀장 답글").status_code == 200 and memo_row(m1)[0][2] is None, "(확인한 메모에 팀장이 답글을 달면 확인이 풀림)")
        ok(memo_do(EMP, m1, "reply", text="가상 본인 답글").status_code == 200 and memo_row(m1)[0][2] == z(8, 6, 8), "(본인 답글 = 확인)")

        # 메모 쓰기의 거절 순서
        at(8, 7)
        n = n_memos()
        ok(hit(memo_post(HEAD, EMP, year=year - 1), 403, NOT_WRITER) and hit(memo_post(LEAD2, PEER2, year=year - 1), 400, BAD_YEAR)
           and all(hit(memo_post(LEAD2, PEER2, year=y), 400, BAD_YEAR) for y in (year + 1, True, "%d" % year, None, 0)),
           "메모_거절_순서: 바로 위 관리자 403 → 해(올해 정수) 400")
        ok(hit(memo_post(LEAD2, PEER2), 404, NO_PLAN) and hit(memo_post(LEAD2, PEER2, text=""), 404, NO_PLAN), "→ 확정본 404 (글이 비어 있어도 404 가 먼저)")
        ok(all(memo_post(LEAD, EMP, t).status_code == 400 for t in ("", "   \n ", "가" * 201, "가\x00나", "\ud800", None, 5, ["가"], {"a": 1})) and n_memos() == n,
           "→ 글 400: 빈 글 · 공백뿐 · 201자 · 제어문자 · 짝 없는 글자 · 글이 아닌 것 — 아무것도 안 남음")
        r = memo_post(LEAD, EMP, "  " + "가" * 200 + "\n")
        ok(r.status_code == 200 and q("SELECT text FROM kpi_memos WHERE id = ?", r.json["id"]) == [("가" * 200,)] and hit(memo_do(LEAD, r.json["id"], "delete"), 200, {"ok": True})
           and n_memos() == n, "200자(앞뒤 공백은 뺌)까지 됨 (같은 날 지움)")

        # 쓴 날(한국 날짜)에만 고친다 — UTC 15:00 에 한국 날짜가 바뀐다
        at(3, 10, 15, 30)  # 한국 3월 11일 0시 30분
        m3 = memo_post(LEAD, EMP, "가상 3월 메모").json["id"]
        at(3, 11, 1, 0)  # 한국 3월 11일 10시
        rp_w = memo_do(LEAD, m3, "reply", text="가상 팀장 답글").json["id"]
        at(3, 11, 2, 0)
        rp_o = memo_do(EMP, m3, "reply", text="가상 본인 답글").json["id"]
        ok(memo_row(m3)[0][2] == z(3, 11, 2) and one(EMP, LEAD, m3)["canEdit"] is True, "(본인이 답글로 확인함)")
        at(3, 11, 14, 59)  # 한국 3월 11일 23시 59분
        ok(memo_do(LEAD, m3, "edit", text="").status_code == 400 and memo_row(m3) == [("가상 3월 메모", None, z(3, 11, 2), None)], "고치기 글이 비면 400 · 아무것도 안 바뀜")
        ok(hit(memo_do(LEAD, m3, "edit", text="  가상 고친 메모 \n"), 200, {"ok": True}) and memo_row(m3) == [("가상 고친 메모", z(3, 11, 14, 59), None, None)],
           "쓴_날에만_고친다: 한국 3/11 23:59 에 고치기 200 — 글 · 고친 때, 확인(ack)은 비워짐")
        j = one(EMP, EMP, m3)
        ok(j["edited"] is True and j["ack"] is None and j["text"] == "가상 고친 메모" and j["at"] == z(3, 10, 15, 30), "GET: edited 참 · ack 없음 · 쓴 시각은 그대로")
        ok(memo_do(LEAD, m3, "edit", text="가" * 201).status_code == 400 and memo_do(LEAD, m3, "edit", text="가" * 200).status_code == 200, "고치기도 1~200자")
        ok(all(hit(memo_do(w, m3, "edit", text="가상 남의 손"), 403, NOT_AUTHOR) and hit(memo_do(w, m3, "delete"), 403, NOT_AUTHOR) for w in (EMP, HEAD, HR, CEO, EMP2))
           and memo_do(LEAD, 999999, "edit", text="가상").status_code == 404 and memo_do(EMP, 999999, "delete").status_code == 404,
           "고치기 · 지우기: 없는 메모 404 → 쓴 사람이 아니면 403 (본인 · 본부장 · HR · 대표이사도)")
        ok(hit(rp_del(LEAD, rp_o), 403, NOT_REPLIER) and hit(rp_del(HEAD, rp_o), 403, NOT_REPLIER) and hit(rp_del(EMP, rp_w), 403, NOT_REPLIER) and rp_del(EMP, 999999).status_code == 404,
           "답글 지우기: 없는 답글 404 → 단 사람이 아니면 403")
        ok(hit(rp_del(EMP, rp_o), 200, {"ok": True}) and n_replies(m3) == 1, "한국 3/11 23:59 에 본인 답글 지우기 200")
        at(3, 11, 15, 0)  # 한국 3월 12일 0시
        ok(hit(memo_do(LEAD, m3, "edit", text="가상 늦은 손"), 403, WRONG_DAY) and hit(memo_do(LEAD, m3, "delete"), 403, WRONG_DAY) and hit(rp_del(LEAD, rp_w), 403, WRONG_DAY)
           and memo_row(m3)[0][0] == "가" * 200 and n_replies(m3) == 1, "쓴_날에만_고친다: 한국 3/12 0시부터는 고치기 · 지우기 · 답글 지우기 403 — 아무것도 안 바뀜")
        ok(hit(memo_do(LEAD, m3, "edit", text=""), 403, WRONG_DAY) and hit(memo_do(EMP, m3, "edit", text="가상"), 403, NOT_AUTHOR) and hit(rp_del(EMP, rp_w), 403, NOT_REPLIER),
           "거절 순서: 쓴 사람 아님 403 → 쓴 날 아님 403 → 글 400 (날이 지났으면 글이 비어도 403)")
        ok(one(EMP, LEAD, m3)["canEdit"] is False and not any(p["canDelete"] for p in one(EMP, LEAD, m3)["replies"]), "GET: 날이 지나면 canEdit · canDelete 거짓")

        # 새 답글은 열어 보면 사라진다
        at(9, 1)
        m4 = memo_post(LEAD, EMP, "가상 넷째 메모").json["id"]
        at(9, 1, 4)
        ok(memo_do(EMP, m4, "reply", text="가상 새 답글 1").status_code == 200, "(본인 답글 1)")
        at(9, 1, 5)
        ok(memo_do(EMP, m4, "reply", text="가상 새 답글 2").status_code == 200 and memo_do(LEAD, m4, "reply", text="가상 팀장 답글").status_code == 200,
           "(본인 답글 2 · 팀장 답글 1 — 팀장 자신의 답글은 새 답글이 아님)")
        ok(one(EMP, LEAD, m4)["fresh"] == 2 and one(EMP, EMP, m4)["fresh"] == 0 and one(EMP, HEAD, m4)["fresh"] == 0 and one(EMP, HR, m4)["fresh"] == 0,
           "새_답글은_열어_보면_사라진다: 쓴 사람(팀장)에게만 fresh 2 · 본인 · 본부장 · HR 화면은 0")
        at(9, 1, 6)
        n_lead = q("SELECT COUNT(*) FROM kpi_memos WHERE user_id = ? AND author_id = ?", EMP, LEAD)[0][0]
        ok(hit(seen(LEAD, EMP), 200, {"ok": True}) and all(m["fresh"] == 0 for m in view(EMP, LEAD)["memos"])
           and q("SELECT COUNT(*) FROM kpi_memos WHERE user_id = ? AND author_id = ? AND seen_at = ?", EMP, LEAD, z(9, 1, 6)) == [(n_lead,)],
           "열어 보았다고 보내면 내가 쓴 그 사람의 메모 모두 seen_at = 지금 → fresh 0")
        at(9, 1, 7)
        ok(memo_do(EMP, m4, "reply", text="가상 새 답글 3").status_code == 200 and one(EMP, LEAD, m4)["fresh"] == 1 and one(EMP, EMP, m4)["fresh"] == 0,
           "그 뒤 달린 본인 답글만 새로 셈 (fresh 1)")
        at(9, 1, 8)
        ok(hit(seen(HEAD, EMP), 200, {"ok": True}) and hit(seen(LEAD, EMP2), 200, {"ok": True}) and hit(seen(CEO, EMP), 200, {"ok": True}) and one(EMP, LEAD, m4)["fresh"] == 1
           and q("SELECT COUNT(*) FROM kpi_memos WHERE seen_at = ?", z(9, 1, 8)) == [(0,)],
           "내가 쓴 메모가 없으면 아무것도 안 하고 200 (남의 메모 본 때는 안 바뀜 · 못 보는 사람이어도 200)")
        ok(all(c(LEAD).post(f"/api/kpi/memos/{EMP}/seen", base_url=BASE, headers=ORIGIN, json={"year": y}).status_code == 400 for y in (None, True, "%d" % year, 2020, year + 0.5))
           and c(LEAD).post(f"/api/kpi/memos/{EMP}/seen", base_url=BASE, headers=ORIGIN, json={}).status_code == 400, "열어 보았다: 해가 이상하면 400")
        ok(hit(seen(LEAD, EMP), 200, {"ok": True}) and one(EMP, LEAD, m4)["fresh"] == 0, "(다시 열어 보면 사라짐)")
        seen_up = lambda up: c(LEAD).post(f"/api/kpi/memos/{EMP}/seen", base_url=BASE, headers=ORIGIN, json={"year": year, "upTo": up})  # noqa: E731
        seen_of = lambda: q("SELECT DISTINCT seen_at FROM kpi_memos WHERE user_id = ? AND author_id = ?", EMP, LEAD)  # noqa: E731
        at(9, 1, 9)
        ok(memo_do(EMP, m4, "reply", text="가상 본 답글").status_code == 200 and one(EMP, LEAD, m4)["fresh"] == 1, "(팀장이 메모를 열어 본인 답글 하나를 봄 — 9시)")
        at(9, 1, 10)
        ok(memo_do(EMP, m4, "reply", text="가상 사이 답글").status_code == 200, "(팀장 화면이 '보았다'를 보내기 전에 본인이 답글을 더 닮 — 10시)")
        at(9, 1, 11)
        ok(hit(seen_up(z(9, 1, 9)), 200, {"ok": True}) and seen_of() == [(z(9, 1, 9),)] and one(EMP, LEAD, m4)["fresh"] == 1,
           "본_때까지만_보았다: upTo(화면에 보인 가장 새 본인 답글 9시)를 보내면 seen_at = 9시 — 그 뒤(10시)에 달린 답글은 새 답글로 남음 (fresh 1)")
        ok(hit(seen_up(z(9, 1, 8)), 200, {"ok": True}) and seen_of() == [(z(9, 1, 9),)] and one(EMP, LEAD, m4)["fresh"] == 1,
           "seen_at 은 앞으로만: 더 앞의 upTo(8시)를 보내도 9시 그대로")
        ok(all(hit(seen_up(v), 400, {"error": "본 때가 올바르지 않습니다."}) for v in ("x", 1, True, [], "", z(9, 1, 9)[:-1], f"{year}-02-30T03:00:00Z", f"{year}-09-01T25:00:00Z", f"{year}-09-01 03:00:00Z"))
           and hit(c(LEAD).post(f"/api/kpi/memos/{EMP}/seen", base_url=BASE, headers=ORIGIN, json={"year": 1, "upTo": "x"}), 400, BAD_YEAR) and seen_of() == [(z(9, 1, 9),)],
           "upTo 가 그 모양의 실제 시각이 아니면 400 (해 400 이 먼저) · seen_at 그대로")
        ok(hit(seen_up(f"{year + 1}-01-01T00:00:00Z"), 200, {"ok": True}) and seen_of() == [(z(9, 1, 11),)] and one(EMP, LEAD, m4)["fresh"] == 0
           and hit(seen_up(None), 200, {"ok": True}), "upTo 가 지금보다 뒤면 지금(11시)까지 · null 은 안 보낸 것과 같다")

        # 메모를 지우면 답글도
        at(9, 2)
        m5 = memo_post(LEAD, EMP, "가상 지울 메모").json["id"]
        at(9, 2, 4)
        ok(memo_do(EMP, m5, "reply", text="가상 본인 답글").status_code == 200 and memo_do(LEAD, m5, "reply", text="가상 팀장 답글").status_code == 200 and n_replies(m5) == 2,
           "(메모에 답글 둘)")
        n_all = q("SELECT COUNT(*) FROM kpi_memo_replies")[0][0]
        ok(hit(memo_do(LEAD, m5, "delete"), 200, {"ok": True}) and memo_row(m5) == [] and n_replies(m5) == 0
           and q("SELECT COUNT(*) FROM kpi_memo_replies")[0][0] == n_all - 2, "메모를_지우면_답글도: 같은 날 지우면 두 표 모두 비고 다른 메모의 답글은 그대로")
        ok(memo_do(LEAD, m5, "delete").status_code == 404 and memo_do(EMP, m5, "ack").status_code == 404, "(이미 지운 메모는 404)")
        ids = [m["id"] for m in view(EMP, LEAD)["memos"]]
        ok(len(ids) >= 3 and ids == sorted(ids, reverse=True) and [m["id"] for m in view(EMP, EMP)["memos"]] == ids,
           "메모는_새것부터: GET 은 id 가 큰 것(새것)부터 — 여러 개일 때 (본인 화면도 같은 순서)")

        # 팀을 옮기면 — 쓰는 사람은 지금 바로 위 관리자, 옛 메모는 그대로
        at(9, 3)
        m6 = memo_post(LEAD2, EMP2, "가상 다른 팀 메모").json["id"]
        rp6 = memo_do(LEAD2, m6, "reply", text="가상 옛 팀장 답글").json["id"]  # 옛 팀장이 같은 날 단 답글 (아래에서 팀을 옮긴 동안 지워 본다)
        x("UPDATE users SET department_id = 72 WHERE id = ?", EMP2)  # 팀 A 로
        try:
            j = view(EMP2, LEAD)
            ok(j["role"] == "writer" and j["writer"]["id"] == LEAD and j["canWrite"] is True and [m["authorId"] for m in j["memos"]] == [LEAD2]
               and j["memos"][0]["canEdit"] is False and hit(memos(EMP2, LEAD2), 403, NO_VIEW) and hit(memo_post(LEAD2, EMP2), 403, NOT_WRITER)
               and memo_post(LEAD, EMP2, "가상 새 팀장 메모").status_code == 200,
               "팀을_옮기면_새_관리자가_쓴다: 새 팀장이 role writer · 옛 메모를 읽음(고치기 거짓) · 옛 팀장은 읽기도 쓰기도 403")
            ok(all(hit(memo_do(LEAD2, m6, w, **b), 403, NO_VIEW) for w, b in (("reply", {"text": "가상 끼어들기"}), ("edit", {"text": "가상 옛 팀장의 손"}), ("delete", {})))
               and hit(rp_del(LEAD2, rp6), 403, NO_VIEW) and memo_row(m6)[0][0] == "가상 다른 팀 메모" and n_replies(m6) == 1,
               "옛_관리자는_못_본_뒤_쓰지_못한다: 팀을 옮긴 사람의 메모에 옛 팀장은 쓴 날이어도 답글 · 고치기 · 지우기 · 답글 지우기 403 (GET 과 같은 문구) — 아무것도 안 바뀜")
            r = memo_do(EMP2, m6, "reply", text="가상 본인 답글")
            ok(r.status_code == 200 and hit(memo_do(EMP2, m6, "ack"), 200, {"ok": True}) and memo_row(m6)[0][2] is not None and n_replies(m6) == 2
               and hit(rp_del(EMP2, r.json["id"]), 200, {"ok": True}) and n_replies(m6) == 1, "본인은 팀을 옮긴 뒤에도 답글 · 확인 · 자기 답글 지우기가 됨")
        finally:
            x("UPDATE users SET department_id = 73 WHERE id = ?", EMP2)  # 원래대로 (팀 B)
        ok(hit(memo_do(LEAD2, m6, "edit", text="가상 고친 다른 팀 메모"), 200, {"ok": True}) and memo_row(m6)[0][0] == "가상 고친 다른 팀 메모"
           and hit(rp_del(LEAD2, rp6), 200, {"ok": True}) and n_replies(m6) == 0,
           "(다시 같은 팀이 되면 쓴 사람은 쓴 날에 자기 메모를 고치고 자기 답글을 지움 — 팀이 다른 동안에는 못 함)")

        # 감사로그는 남기지 않는다
        ok(n_audit() == audit0
           and q("SELECT COUNT(*) FROM audit_logs WHERE action LIKE '%MEMO%' OR entity_type LIKE '%memo%'")[0][0] == 0,
           "감사로그_없음: 메모 · 답글 · 확인 · 본 때 일을 아무리 해도 audit_logs 줄 수가 그대로")

        # ---- 알람 끄기 · 부진 · 알람 기준 · 경영계획 (2단계 ②, Task 5) ----
        mute = lambda who, rid, why="year", **over: c(who).post("/api/kpi/me/mute", base_url=BASE, headers=ORIGIN,  # noqa: E731
                                                               json={"year": year, "rid": rid, "why": why, **over})
        unmute = lambda who, uid, rid=1, **over: c(who).post(f"/api/kpi/plans/{uid}/unmute", base_url=BASE, headers=ORIGIN,  # noqa: E731
                                                            json={"year": year, "rid": rid, **over})
        gap_set = lambda who, gap, y=None: c(who).post("/api/kpi/admin/alarm", base_url=BASE, headers=ORIGIN,  # noqa: E731
                                                       json={"year": y or year, "gap": gap})
        biz_get = lambda who, y=None: c(who).get(f"/api/kpi/biz-plan?year={y or year}", base_url=BASE)  # noqa: E731
        biz_set = lambda who, goals, y=None: c(who).post("/api/kpi/admin/biz-plan", base_url=BASE, headers=ORIGIN,  # noqa: E731
                                                         json={"year": y or year, "goals": goals})
        goal = lambda g, d="": {"goal": g, "detail": d}  # noqa: E731
        row_of = lambda uid, rid: next(i for i in prog(uid).json["items"] if i["rid"] == rid)  # noqa: E731
        mute_of = lambda uid, rid: row_of(uid, rid)["mute"]  # noqa: E731
        alarm_of = lambda uid: prog(uid).json["alarm"]  # noqa: E731
        mutes = lambda uid: q("SELECT rid, why, text, until_month, set_at FROM kpi_mutes WHERE user_id = ? ORDER BY rid", uid)  # noqa: E731
        year_row = lambda y: q("SELECT edit_from, edit_to, eval_start, alarm_gap, biz_plan, updated_by, updated_at FROM kpi_years WHERE year = ?", y)  # noqa: E731
        biz_db = lambda y: next(iter(q("SELECT biz_plan FROM kpi_years WHERE year = ?", y)), (None,))[0]  # noqa: E731
        admin_gap = lambda y: c(HR).get(f"/api/kpi/admin?year={y}", base_url=BASE).json["alarmGap"]  # noqa: E731
        BAD_MONTH, BAD_WHY, NO_RID = {"error": "이번 달부터 12월 사이에서 골라 주세요."}, {"error": "이유를 하나 골라 주세요."}, {"error": "지금 확정본에 없는 실행목표입니다."}
        NOT_UNMUTER, NOT_HR = {"error": "본인과 바로 위 관리자만 다시 켤 수 있습니다."}, {"error": "HR만 할 수 있습니다."}
        BAD_GAP = {"error": "해(2026~2100)와 알람 기준(1~100 사이 정수)을 확인해 주세요."}
        NO_BIZ = {"error": "경영계획 목표는 대표이사 · 본부장 · HR만 봅니다."}
        S, Y2 = LEAD2, 2099  # S = 숫자를 따져 볼 사람 (바로 위 관리자는 본부장, 진행 기록이 아직 없음) · Y2 = 기간 줄이 없는 먼 해
        had_row = bool(year_row(year))
        at(10, 15)  # 한국 10월 15일 12시
        ndays = (date(year + 1, 1, 1) - date(year, 1, 1)).days
        exp_reg = ((date(year, 10, 15) - date(year, 1, 1)).days + 1) / ndays * 100  # 정기 업무의 기대 진행률 (78~79%) · 늦은 비정기 업무(일정 둘 다 14일 넘게 지남)는 100%
        ok(anon.post("/api/kpi/me/mute", base_url=BASE, headers=ORIGIN, json={"year": year, "rid": 1, "why": "year"}).status_code == 401
           and anon.post(f"/api/kpi/plans/{EMP}/unmute", base_url=BASE, headers=ORIGIN, json={"year": year, "rid": 1}).status_code == 401
           and anon.post("/api/kpi/admin/alarm", base_url=BASE, headers=ORIGIN, json={"year": year, "gap": 30}).status_code == 401
           and anon.get(f"/api/kpi/biz-plan?year={year}", base_url=BASE).status_code == 401
           and anon.post("/api/kpi/admin/biz-plan", base_url=BASE, headers=ORIGIN, json={"year": year, "goals": []}).status_code == 401, "로그인 없이는 401 (알람 · 경영계획)")
        p = prog(S).json
        ok(p["gap"] == 20 and all(i["mute"] is None for i in p["items"]) and set(p["alarm"]) == {"slow", "pct", "expected"}
           and p["alarm"]["slow"] is True and p["alarm"]["pct"] == 0 and abs(p["alarm"]["expected"] - (0.7 * exp_reg + 30)) < 1e-9,
           "진행 기록에 gap(기본 20) · alarm · 실행목표마다 mute(없으면 null): 아무것도 안 쓴 사람은 부진")

        # 알람 끄기는 본인이
        r = mute(S, 3)
        ok(hit(r, 200, {"ok": True}) and mutes(S) == [(3, "year", None, 12, z(10, 15))]
           and mute_of(S, 3) == {"why": "year", "text": None, "until": 12, "at": z(10, 15)} and mute_of(S, 1) is None and mute_of(S, 2) is None,
           "알람_끄기는_본인이: 본인이 끄면 그 실행목표에만 mute 가 붙음 (올해 안 = 12월까지) — 끈 이유 · 끈 시각")
        ok(row_of(S, 3)["mute"] is not None and row_of(S, 3)["mute"] == prog(S, HEAD).json["items"][2]["mute"] == prog(S, HR).json["items"][2]["mute"],
           "(볼 수 있는 윗사람 · HR 에게도 같은 mute 가 보임)")
        r = c(LEAD).post("/api/kpi/me/mute", base_url=BASE, headers=ORIGIN, json={"year": year, "rid": 1, "why": "year", "userId": S})
        ok(r.status_code == 200 and mutes(LEAD) == [(1, "year", None, 12, z(10, 15))] and mutes(S) == [(3, "year", None, 12, z(10, 15))] and mutes(EMP) == [],
           "남의_알람은_못_끈다: 팀장이 본문에 사람 번호를 실어 보내도 (본문의 번호는 보지 않음) 팀장 자기 계획서의 알람만 꺼짐")
        x("DELETE FROM kpi_mutes WHERE user_id = ?", LEAD)
        ok(hit(mute(PEER2, 1), 404, NO_PLAN) and mutes(PEER2) == [], "확정본이 없는 사람이 같은 본문을 보내면 404 (남의 알람을 끌 길이 없음)")

        # 끈 항목은 빼고 따진다
        ok(put(S, "progress", {"rid": 1, "actual": 12}).status_code == 200 and put(S, "progress", {"rid": 2, "pct": 50}).status_code == 200
           and (a := alarm_of(S))["slow"] is False and abs(a["pct"] - 5500 / 70) < 1e-9 and abs(a["expected"] - exp_reg) < 1e-9,
           "(실적 12건 · 진행률 50% 를 씀 — 늦은 비정기 업무(막대 0)를 끈 채로는 남은 둘만 따져 진행률 78.6 ≈ 기대 78.9 → 부진 아님)")
        ok(hit(unmute(S, S, 3), 200, {"ok": True}) and mutes(S) == [] and mute_of(S, 3) is None
           and (a := alarm_of(S))["slow"] is True and abs(a["pct"] - 55) < 1e-9 and abs(a["expected"] - (0.7 * exp_reg + 30)) < 1e-9,
           "끈_항목은_빼고_따진다: 그 항목을 켜면 진행률 55 ≪ 기대 85 → 부진 (alarm.slow 거짓 → 참)")
        ok(mute(S, 3).status_code == 200 and alarm_of(S)["slow"] is False, "(다시 끄면 거짓)")
        ok(prog(S).json["total"] == 55 and mute(S, 1).status_code == 200 and mute(S, 2).status_code == 200
           and alarm_of(S) == {"slow": False, "pct": None, "expected": None} and prog(S).json["total"] == 55,
           "다 끄면 pct · expected 가 null (부진 아님) — 종합 진행률(total)은 끈 항목을 포함한 그대로")

        # 알람 끄기 검사
        before = mutes(S)
        ok(all(hit(mute(S, 1, "month", month=m), 400, BAD_MONTH) for m in (9, 1, 13, 0, -1, "10", 10.0, True, None, [10]))
           and hit(mute(S, 1, "month"), 400, BAD_MONTH) and hit(mute(S, 1, "wait", month=9), 400, BAD_MONTH) and hit(mute(S, 2, "etc", text="가상"), 400, BAD_MONTH),
           "알람_끄기_검사: 지난달 · 13월 · 숫자가 아닌 달 · 달 없음은 400")
        ok(all(hit(mute(S, 1, w), 400, BAD_WHY) for w in (None, "", "foo", "YEAR", 1, True, ["year"], {"a": 1}))
           and hit(c(S).post("/api/kpi/me/mute", base_url=BASE, headers=ORIGIN, json={"year": year, "rid": 1}), 400, BAD_WHY), "이유 없음 · 모르는 이유는 400")
        ok(hit(mute(S, 2, "etc", month=12), 400, {"error": "직접 적은 이유를 1~50자로 써 주세요."})
           and hit(mute(S, 2, "etc", month=12, text="   \n "), 400, {"error": "직접 적은 이유를 1~50자로 써 주세요."})
           and hit(mute(S, 2, "etc", month=12, text="가" * 51), 400, {"error": "직접 적은 이유: 50자 안으로 써 주세요."})
           and all(mute(S, 2, "etc", month=12, text=t).status_code == 400 for t in (None, "가\x00나", "\ud800", 5, ["가"])),
           "직접 적기(etc): 글 없음 · 51자 · 제어문자 · 글이 아닌 것은 400")
        ok(hit(mute(S, 99), 400, NO_RID) and all(hit(mute(S, rid), 400, NO_RID) for rid in (0, -1, "1", True, 1.0, None, [1], 10 ** 30))
           and hit(c(S).post("/api/kpi/me/mute", base_url=BASE, headers=ORIGIN, json={"year": year, "why": "year"}), 400, NO_RID),
           "지금 확정본에 없는 rid (숫자가 아니거나 참/거짓 포함)는 400")
        ok(hit(mute(S, 99, "foo"), 400, NO_RID) and hit(mute(S, 2, "etc", month=99, text=""), 400, BAD_MONTH),
           "순서: 실행목표 400 → 이유 · 달 400 → 직접 쓴 글 400")
        ok(hit(mute(CEO, 1), 403, {"error": "KPI 작성 대상이 아닙니다."}) and hit(mute(CEO, 99, "foo", year=1), 403, {"error": "KPI 작성 대상이 아닙니다."}),
           "작성 대상이 아닌 사람(대표이사)은 403 — 해 · 실행목표 400 보다 먼저")
        ok(all(hit(mute(S, 1, year=y), 400, BAD_YEAR) for y in (1, True, "%d" % year, None, 2101)) and hit(mute(S, 1, year=year + 1), 404, NO_PLAN)
           and mutes(S) == before, "해가 이상하면 400 → 그해 확정본이 없으면 404 — 아무것도 안 바뀜")
        r = mute(S, 2, "etc", month=12, text="  " + "가" * 50 + " \n")
        ok(r.status_code == 200 and mutes(S) == [before[0], (2, "etc", "가" * 50, 12, z(10, 15)), before[2]]
           and mute_of(S, 2) == {"why": "etc", "text": "가" * 50, "until": 12, "at": z(10, 15)}, "50자 (앞뒤 공백은 뺌)까지 됨 — 이미 끈 줄은 바꿔 끔 (줄 하나)")

        # 부진은 반올림한 숫자로 (정기 업무 하나만 따지게 나머지 둘은 끈 채로)
        ok(hit(unmute(S, S, 1), 200, {"ok": True}) and put(S, "progress", {"rid": 1, "actual": 7.128}).status_code == 200
           and hit(gap_set(HR, 20), 200, {"ok": True}) and 78.5 <= (a := alarm_of(S))["expected"] < 79.5 and 59 <= a["pct"] < 59.5 and a["expected"] - a["pct"] < 20,
           "(준비: 기대 78.9 안팎 · 진행률 59.4 안팎 — 반올림 전 차이는 20 이 안 됨)")
        ok(alarm_of(S)["slow"] is True and prog(S).json["gap"] == 20, "부진은_반올림한_숫자로: 기준 20 — 반올림해 79 − 59 = 20 이라 부진 (반올림 전 차이 19.5 로는 부진이 아님)")
        ok(gap_set(HR, 21).status_code == 200 and alarm_of(S)["slow"] is False and prog(S).json["gap"] == 21, "기준 21 이면 부진 아님")
        ok(put(S, "progress", {"rid": 1, "actual": 7.2}).status_code == 200 and 59.9 < alarm_of(S)["pct"] < 60.1 and gap_set(HR, 20).status_code == 200
           and alarm_of(S)["slow"] is False and gap_set(HR, 19).status_code == 200 and alarm_of(S)["slow"] is True,
           "진행률 60 이면 차이 19: 기준 20 은 아님 · 기준 19 는 부진 (차이가 정확히 기준일 때 참)")

        # 고른 달 말일까지
        r = mute(S, 1, "month", month=10, text="가상 버려지는 글")
        ok(r.status_code == 200 and mutes(S)[0] == (1, "month", None, 10, z(10, 15)), "(이번 달=10월까지 끔 — 직접 적기가 아니면 보낸 글은 버림)")
        at(10, 31, 14, 59)  # 한국 10월 31일 23시 59분
        ok(mute_of(S, 1) == {"why": "month", "text": None, "until": 10, "at": z(10, 15)} and alarm_of(S) == {"slow": False, "pct": None, "expected": None},
           "고른_달_말일까지: 한국 10/31 23:59 에는 아직 꺼져 있음 (셋 다 꺼져 있어 알람 없음)")
        at(10, 31, 15, 0)  # 한국 11월 1일 0시
        ok(mute_of(S, 1) is None and mutes(S)[0] == (1, "month", None, 10, z(10, 15)) and mute_of(S, 3) is not None and alarm_of(S)["pct"] is not None,
           "한국 11/1 0시부터 다시 켜짐 (줄은 DB 에 남음) — 올해 안으로 끈 것은 그대로 · 켜진 항목만 따짐")

        # 본인과 팀장이 다시 켠다
        at(11, 2)
        ok(mute(EMP, 1).status_code == 200 and mute(EMP, 3, "month", month=11).status_code == 200 and [m[0] for m in mutes(EMP)] == [1, 3], "(팀원이 두 항목의 알람을 끔)")
        ok(all(hit(unmute(w, EMP), 403, NOT_UNMUTER) for w in (HR, HEAD, CEO, EMP2, LEAD2)) and [m[0] for m in mutes(EMP)] == [1, 3],
           "본인과_팀장이_다시_켠다: HR · 본부장(윗사람) · 대표이사 · 다른 사람은 403 — 줄 그대로")
        ok(c(CEO).post("/api/kpi/delegation", base_url=BASE, headers=ORIGIN, json={"on": True}).json == {"ok": True, "on": True}
           and hit(unmute(HR, EMP), 403, NOT_UNMUTER) and len(mutes(EMP)) == 2, "대표이사가 HR 에 맡김을 켜도 HR 은 403 (같은 문구)")
        c(CEO).post("/api/kpi/delegation", base_url=BASE, headers=ORIGIN, json={"on": False})
        ok(hit(unmute(HR, EMP, year=1), 403, NOT_UNMUTER) and hit(unmute(LEAD, EMP, year=1), 400, BAD_YEAR) and hit(unmute(LEAD, EMP, year=True), 400, BAD_YEAR)
           and all(hit(unmute(LEAD, EMP, rid), 400, {"error": "실행목표 번호가 올바르지 않습니다."}) for rid in (None, "1", True, 1.5, [1], 10 ** 30, 0, -1))
           and len(mutes(EMP)) == 2, "순서: 다시 켤 수 있는 사람이 아님 403 → 해 400 → 실행목표 번호 400")
        ok(hit(unmute(LEAD, EMP, 1), 200, {"ok": True}) and [m[0] for m in mutes(EMP)] == [3] and mute_of(EMP, 1) is None and mute_of(EMP, 3) is not None, "팀장(바로 위 관리자)이 다시 켬")
        ok(hit(unmute(EMP, EMP, 3), 200, {"ok": True}) and mutes(EMP) == [], "본인이 다시 켬")
        ok(hit(unmute(EMP, EMP, 3), 200, {"ok": True}) and hit(unmute(LEAD, EMP, 99), 200, {"ok": True}), "꺼 두지 않은 줄을 켜려 해도 200 (아무 일 없음)")
        ok(mute(EMP2, 1).status_code == 200 and hit(unmute(LEAD, EMP2), 403, NOT_UNMUTER) and hit(unmute(LEAD2, EMP2), 200, {"ok": True}) and mutes(EMP2) == [],
           "바로 위 관리자는 지금 기준: 팀을 옮긴 사람은 옛 팀장 403 · 새 팀장 200")
        ok(mute(HEAD, 1).status_code == 200 and mute(DIRECT, 1).status_code == 200 and all(hit(unmute(w, u), 403, NOT_UNMUTER) for w in (CEO, HR) for u in (HEAD, DIRECT))
           and len(mutes(HEAD)) == 1 and len(mutes(DIRECT)) == 1,
           "대표이사는_다시_켜지_못한다: 바로 위 관리자가 대표이사인 분(본부장 · 직속 팀장)의 알람은 대표이사도 403 (KPI 현황에서 보기만 — 확인 · 메모가 없는 것과 같다) · HR 403 — 줄 그대로")
        ok(hit(unmute(HEAD, HEAD), 200, {"ok": True}) and hit(unmute(DIRECT, DIRECT), 200, {"ok": True}) and mutes(HEAD) == [] and mutes(DIRECT) == [], "그분들은 본인이 다시 켬")

        # 알람 기준은 HR이
        ok(all(hit(gap_set(w, 30), 403, NOT_HR) for w in (LEAD, HEAD, CEO, EMP, LEAD2)) and hit(gap_set(LEAD, 0), 403, NOT_HR) and prog(S).json["gap"] == 19,
           "알람_기준은_HR이: 팀장 · 본부장 · 대표이사 · 팀원은 403 (값이 이상해도 403 먼저) — 기준 그대로")
        ok(all(hit(gap_set(HR, g), 400, BAD_GAP) for g in (0, 101, -1, "20", True, False, 20.5, 30.0, None, [20], {"a": 1}, 10 ** 30))
           and all(c(HR).post("/api/kpi/admin/alarm", base_url=BASE, headers=ORIGIN, json={"year": y, "gap": 30}).json == BAD_GAP
                   for y in (None, True, "%d" % year, 2025, 2101, year + 0.5, 0))
           and c(HR).post("/api/kpi/admin/alarm", base_url=BASE, headers=ORIGIN, json={"gap": 30}).status_code == 400 and prog(S).json["gap"] == 19,
           "기준이 1~100 정수가 아니거나 해가 2026~2100 정수가 아니면 400 (숫자 글자 · 참/거짓 · 소수 포함) — 기준 그대로")
        ok(hit(gap_set(HR, 1), 200, {"ok": True}) and hit(gap_set(HR, 100), 200, {"ok": True}) and hit(gap_set(HR, 30), 200, {"ok": True})
           and prog(S).json["gap"] == 30 and admin_gap(year) == 30 and prog(S, HEAD).json["gap"] == 30 and year_row(year)[0][3] == 30,
           "HR 이 정함: 1 · 100 도 됨 — 진행 기록의 gap · /api/kpi/admin 의 alarmGap 이 30")
        logs = q("SELECT actor_user_id, actor_is_hr, on_behalf_of, entity_type, entity_id, after_data FROM audit_logs WHERE action = 'KPI_ALARM_SET' ORDER BY id")
        ok([json.loads(l[5])["gap"] for l in logs] == [20, 21, 20, 19, 1, 100, 30] and all(l[:5] == (HR, 1, None, "kpi_year", year) for l in logs)
           and logs[-1][5] == json.dumps({"year": year, "gap": 30}), "감사로그 KPI_ALARM_SET: 바꾼 때마다 해 · 기준 (거절한 것은 안 남음)")
        ok(year_row(Y2) == [] and admin_gap(Y2) == 20, "(먼 해는 기간 줄이 없음 — 기준은 기본 20)")
        ok(hit(gap_set(HR, 45, Y2), 200, {"ok": True}) and year_row(Y2) == [(None, None, None, 45, None, HR, z(11, 2))] and admin_gap(Y2) == 45,
           "기간 줄이 없던 해에도 정해짐: 날짜 없이 줄을 만들고 (기준 · 바꾼 때 · 바꾼 사람) 기간은 비어 있음")
        ok(hit(biz_set(HR, [goal("가상 먼 해 목표")], Y2), 200, {"ok": True})
           and c(HR).post("/api/kpi/admin/year", base_url=BASE, headers=ORIGIN,
                          json={"year": Y2, "editFrom": f"{Y2}-03-01", "editTo": f"{Y2}-03-31", "evalStart": f"{Y2}-06-01"}).status_code == 200
           and year_row(Y2)[0][:4] == (f"{Y2}-03-01", f"{Y2}-03-31", f"{Y2}-06-01", 45) and json.loads(year_row(Y2)[0][4]) == [goal("가상 먼 해 목표")] and admin_gap(Y2) == 45,
           "그 뒤 POST /api/kpi/admin/year 로 기간을 바꿔도 알람 기준 45 · 경영계획이 남음")

        # 경영계획은 대표이사 · 본부장 · HR만
        ok(all(hit(biz_get(w), 403, NO_BIZ) for w in (LEAD, EMP, LEAD2, EMP2, DIRECT)) and c(LEAD).get("/api/kpi/biz-plan?year=abc", base_url=BASE).status_code == 403,
           "경영계획은_대표이사_본부장_HR만: 팀장 · 팀원은 GET 403 (해가 이상해도 403 먼저)")
        ok(all(hit(biz_get(w), 200, {"year": year, "goals": []}) for w in (CEO, HEAD, HR)) and c(HR).get("/api/kpi/biz-plan?year=abc", base_url=BASE).status_code == 400
           and c(HR).get("/api/kpi/biz-plan?year=2025", base_url=BASE).status_code == 400, "대표이사 · 본부장 · HR 은 200 (아직 없으면 빈 목록) · 해가 이상하면 400")
        ok(all(hit(biz_set(w, [goal("가상")]), 403, NOT_HR) for w in (LEAD, HEAD, CEO, EMP)) and biz_db(year) is None, "HR 아닌 사람의 저장은 403 — 아무것도 안 바뀜")
        ok(hit(biz_set(HR, [goal(f"가상 목표 {i}") for i in range(21)]), 400, {"error": "목표는 20개까지 적을 수 있어요."})
           and hit(biz_set(HR, [goal("", "가상 내용만")]), 400, {"error": "내용만 있는 줄이 있어요. 목표를 함께 적어 주세요."})
           and hit(biz_set(HR, [goal("가" * 61)]), 400, {"error": "목표는 60자까지 적을 수 있어요."})
           and hit(biz_set(HR, [goal("가", "나" * 301)]), 400, {"error": "내용은 300자까지 적을 수 있어요."}),
           "경영계획_검사: 21개 · 목표 없이 내용 · 목표 61자 · 내용 301자는 400 (쉬운 문구)")
        ok(all(biz_set(HR, [goal(g, d)]).status_code == 400 for g, d in (("가\x00나", ""), ("가", "나\x01다"), ("\ud800", ""), ("가", "\udfff"), ("가\x7f", "")))
           and all(biz_set(HR, v).status_code == 400 for v in (None, "가", {"goal": "가", "detail": ""}, [None], ["가"], [{"goal": "가"}], [{"goal": 5, "detail": ""}], [{"goal": "가", "detail": None}]))
           and c(HR).post("/api/kpi/admin/biz-plan", base_url=BASE, headers=ORIGIN, json={"goals": []}).status_code == 400
           and all(c(HR).post("/api/kpi/admin/biz-plan", base_url=BASE, headers=ORIGIN, json={"year": y, "goals": []}).status_code == 400 for y in (True, "%d" % year, 2025, 2101))
           and biz_db(year) is None, "제어문자 · 짝 없는 글자 · 목록이 아닌 것 · 줄 모양이 이상한 것 · 해가 이상한 것은 400 — 아무것도 안 바뀜")
        r = biz_set(HR, [goal("가" * 60, "나" * 300)] + [goal(f"가상 목표 {i}") for i in range(19)])
        ok(hit(r, 200, {"ok": True}) and len(json.loads(biz_db(year))) == 20 and len(biz_get(CEO).json["goals"]) == 20, "목표 20개 · 목표 60자 · 내용 300자까지 됨")
        r = biz_set(HR, [goal("  가상 매출 목표  ", " 가상 내용 \n"), goal("  ", ""), goal("", "\n"), goal("가상 둘째 목표")])
        want = [goal("가상 매출 목표", "가상 내용"), goal("가상 둘째 목표")]
        ok(hit(r, 200, {"ok": True}) and json.loads(biz_db(year)) == want and all(hit(biz_get(w), 200, {"year": year, "goals": want}) for w in (CEO, HEAD, HR)),
           "둘 다 빈 줄은 버리고 앞뒤 공백을 뺀 채 저장 — 대표이사 · 본부장 · HR 이 같은 목록을 읽음")
        ok(hit(biz_set(HR, []), 200, {"ok": True}) and biz_db(year) == "[]" and hit(biz_get(CEO), 200, {"year": year, "goals": []}), "빈 목록으로 비울 수 있음")
        logs = q("SELECT actor_user_id, actor_is_hr, entity_type, entity_id, after_data FROM audit_logs WHERE action = 'KPI_BIZ_PLAN_SET' ORDER BY id")
        ok([(l[0], l[1], l[2], l[3]) for l in logs] == [(HR, 1, "kpi_year", Y2), (HR, 1, "kpi_year", year), (HR, 1, "kpi_year", year), (HR, 1, "kpi_year", year)]
           and [json.loads(l[4]) for l in logs] == [{"year": Y2, "count": 1}, {"year": year, "count": 20}, {"year": year, "count": 2}, {"year": year, "count": 0}]
           and not any("가상" in l[4] for l in logs), "감사로그 KPI_BIZ_PLAN_SET: 해 · 개수만 (목표 문장 없음)")

        # 포털 화면에서만
        snap = lambda: (mutes(S), year_row(year), biz_db(year), q("SELECT COUNT(*) FROM audit_logs WHERE action IN ('KPI_ALARM_SET', 'KPI_BIZ_PLAN_SET')")[0][0])  # noqa: E731
        s0, plain = snap(), lambda who, path, body: c(who).post(path, base_url=BASE, headers={}, json=body)  # noqa: E731
        ok(plain(S, "/api/kpi/me/mute", {"year": year, "rid": 2, "why": "year"}).status_code == 403
           and plain(S, f"/api/kpi/plans/{S}/unmute", {"year": year, "rid": 3}).status_code == 403
           and plain(HR, "/api/kpi/admin/alarm", {"year": year, "gap": 55}).status_code == 403
           and plain(HR, "/api/kpi/admin/biz-plan", {"year": year, "goals": [goal("가상")]}).status_code == 403 and snap() == s0 and mutes(S)[2][0] == 3,
           "포털 화면에서만 (Origin 없으면 403): 알람 끄기 · 다시 켜기 · 알람 기준 · 경영계획 모두 — 아무것도 안 바뀜")

        # 올해가 아님 · 기록 기간
        def next_jan(d):
            """시계를 다음 해 1월 d일 (UTC 03:00 = 한국 정오)에 맞춘다."""
            t = datetime(year + 1, 1, d, 3, 0, tzinfo=timezone.utc)
            auth._now = lambda: t
            jar.clear()

        next_jan(5)  # 지난해 기록은 1월 7일까지 쓸 수 있지만 알람은 올해 것만 끈다
        ok(hit(mute(S, 1), 400, {"error": "올해 KPI만 알람을 끌 수 있습니다."}) and mute_of(S, 3) is None and mutes(S)[2][:2] == (3, "year"),
           "올해가 아니면 400 · 해가 바뀌면 올해 안으로 끈 알람도 다시 켜진 것으로 보임 (줄은 남음)")
        next_jan(8)
        ok(hit(mute(S, 1), 403, {"error": "기록 기간이 지났습니다."}), "기록 기간(다음 해 1월 7일)이 지나면 403 — 올해 아님 400 보다 먼저")

        # 이 절이 만든 것을 치운다 (뒤 절이 같은 가상 조직을 이어 쓴다)
        x("DELETE FROM kpi_mutes")
        x("DELETE FROM kpi_progress WHERE user_id = ?", S)
        x("DELETE FROM kpi_years WHERE year = ?", Y2)
        if had_row:
            x("UPDATE kpi_years SET alarm_gap = 20, biz_plan = NULL WHERE year = ?", year)
        else:
            x("DELETE FROM kpi_years WHERE year = ?", year)

        # ---- KPI 현황 명단 · 처리할 일 숫자 (2단계 ②, Task 6) ----
        board = lambda who, month=None: c(who).get("/api/kpi/board", base_url=BASE, query_string=None if month is None else {"month": month})  # noqa: E731
        bj = lambda who, month=None: board(who, month).json  # noqa: E731
        by_id = lambda j, uid: next((p for p in j["people"] if p["userId"] == uid), None)  # noqa: E731
        mine_ids = lambda j: [p["userId"] for p in j["people"] if 91 <= p["userId"] <= 100]  # noqa: E731
        review = lambda who: c(who).get("/api/kpi/inbox", base_url=BASE).json["review"]  # noqa: E731
        near = lambda a, b: a is not None and abs(a - b) < 1e-9  # noqa: E731
        NO_BOARD = {"error": "KPI 현황을 볼 수 있는 분이 아닙니다."}
        bad_month = lambda top: {"error": f"달은 1~{top} 사이 숫자로 보내 주세요."}  # noqa: E731
        wipe = lambda: [x(f"DELETE FROM {t} WHERE user_id BETWEEN 91 AND 100")  # noqa: E731
                        for t in ("kpi_memos", "kpi_month_checks", "kpi_month_notes", "kpi_results", "kpi_progress", "kpi_sched_changes", "kpi_mutes")]

        at(4, 5)  # 앞 절이 시계를 다음 해로 두고 갔다 — 올해 4월 5일 한국 정오로. 앞 절들이 남긴 기록은 치우고 필요한 상태를 새로 만든다
        wipe()
        x("UPDATE users SET department_id = 72 WHERE id = ?", PEER2)  # 팀 A 의 팀원은 둘 (직원 · 다른팀원) — 계획서는 직원만 있다
        exp4 = ((date(year, 4, 5) - date(year, 1, 1)).days + 1) / ndays * 100  # 4월 5일 정기 업무의 기대 진행률 · 비정기(일정 1 = 3/20 만 14일 넘게 지남)는 50
        ok(anon.get("/api/kpi/board", base_url=BASE).status_code == 401, "로그인 없이는 401 (KPI 현황)")

        # 직원은 못 본다 · 달 검사
        ok(all(hit(board(w), 403, NO_BOARD) for w in (EMP, EMP2, PEER2, DMEMBER)) and hit(board(EMP, 13), 403, NO_BOARD) and hit(board(EMP, "a"), 403, NO_BOARD),
           "직원은_못_본다: 팀원은 403 (달이 이상해도 403 먼저)")
        ok(all(hit(board(LEAD, m), 400, bad_month(4)) for m in (13, 0, 5, "a", "-1", "4.0", "", " 4", "4 ", "004", "٣", "1e1"))
           and all(board(LEAD, m).status_code == 200 and bj(LEAD, m)["month"] == int(m) for m in (1, 2, 3, 4, "04")) and bj(LEAD)["month"] == 4,
           "달_검사: 13 · 0 · 다음 달 · 글자 · 음수 · 소수 · 빈 값 · 공백 · 자리 넘침 · 다른 나라 숫자는 400 (1~오늘의 달 정수만) · 안 보내면 오늘의 달")
        ok(put(EMP, "progress", {"rid": 1, "actual": 3}).status_code == 200 and put(EMP, "progress", {"rid": 2, "pct": 40}).status_code == 200
           and put(EMP, "progress", {"rid": 2, "why": "가상 이유"}).status_code == 200, "(준비: 직원이 실적 3건 · 진행률 40% · 늦어지는 이유를 씀 — 종합 진행률 22)")

        # 팀장은 팀원만
        j = bj(LEAD)
        e = by_id(j, EMP)
        ok(set(j) == {"year", "month", "gap", "scope", "depts", "people", "teams", "todo"} and (j["year"], j["month"], j["gap"]) == (year, 4, 20)
           and [p["userId"] for p in j["people"]] == [PEER2, EMP] and all(p["direct"] is True and p["deptId"] == 72 and p["position"] is None for p in j["people"])
           and j["teams"] == [], "팀장은_팀원만: 팀장의 명단 = 그 팀 팀원 둘(이름순, direct 참) · 본인과 다른 팀 사람은 없음 · teams 는 빈 목록")
        ok({"id": 72, "name": "가상R팀A", "parentId": 71} in j["depts"] and {"id": 71, "name": "가상R본부", "parentId": None} in j["depts"]
           and all(set(d) == {"id", "name", "parentId"} for d in j["depts"]) and [d["id"] for d in j["depts"]] == sorted(d["id"] for d in j["depts"]),
           "depts: 부서 번호 순서로 {id name parentId}")
        ok(set(e) == {"userId", "name", "title", "deptId", "position", "status", "direct", "checkStep", "total", "totalExpected", "alarm", "items", "month", "memo"}
           and (e["name"], e["title"], e["deptId"], e["position"], e["status"], e["direct"], e["checkStep"]) == ("가상R직원", "", 72, None, "CONFIRMED", True, True)
           and e["total"] == 22.0 and near(e["totalExpected"], 0.7 * exp4 + 15) and e["alarm"]["slow"] is False and near(e["alarm"]["pct"], 22.0)
           and near(e["alarm"]["expected"], 0.7 * exp4 + 15) and e["memo"] == {"count": 0, "open": 0, "fresh": 0},
           "사람 한 줄의 모양: 이름 · 직급(없으면 빈 글자) · 맡은 부서 · 직책 · 계획서 상태 · direct · checkStep · 종합 진행률 · 기대 · 알람 · 메모 수")
        ok(len(e["items"]) == 3 and all(set(i) == {"rid", "action", "kind", "weight", "value", "expected", "why", "mute"} for i in e["items"])
           and [(i["rid"], i["action"], i["kind"], i["weight"], i["value"], i["why"], i["mute"]) for i in e["items"]]
           == [(1, "가상 정기 업무 1", "reg", 40, 25, None, None), (2, "가상 정기 업무 2", "reg", 30, 40, "가상 이유", None), (3, "가상 비정기 업무", "irr", 30, 0, None, None)]
           and near(e["items"][0]["expected"], exp4) and near(e["items"][2]["expected"], 50), "items: 실행목표마다 rid · 내용 · 종류 · 가중치 · 막대(value) · 기대 · 이유 · 끈 알람")
        pe = by_id(j, PEER2)
        ok((pe["status"], pe["total"], pe["totalExpected"], pe["alarm"], pe["items"], pe["month"], pe["checkStep"]) == ("NONE", None, None, None, [], None, True),
           "확정본이 없는 사람: status NONE · 숫자 · 알람 null · items [] · month null (그래도 확인 단계는 있음)")

        # 본부장은 본부 전체
        j = bj(HEAD)
        ok([p["userId"] for p in j["people"]] == [LEAD, PEER2, EMP, LEAD2, EMP2] and [p["direct"] for p in j["people"]] == [True, False, False, True, False]
           and [p["position"] for p in j["people"]] == ["TEAM_LEADER", None, None, "TEAM_LEADER", None] and [p["deptId"] for p in j["people"]] == [72, 72, 72, 73, 73]
           and by_id(j, LEAD)["title"] == "팀장" and by_id(j, LEAD)["checkStep"] is True and j["teams"] == [],
           "본부장은_본부_전체: 본부의 팀장 둘 + 팀원들 (부서 번호 → 팀장 → 이름 순서) · 팀장 줄만 direct · 본부 밖(직속 팀 · '임원')은 없음 · teams 빈 목록")
        ok(bj(LEAD)["scope"] == {"deptId": 72, "name": "가상R팀A"} and j["scope"] == {"deptId": 71, "name": "가상R본부"} and bj(CEO)["scope"] is None and bj(HR)["scope"] is None,
           "scope: 팀장 · 본부장은 맡은 부서 (명단 제목) · 대표이사 · 직책 없는 HR 은 null")
        x("UPDATE users SET department_id = 75, led_department_id = 71 WHERE id = ?", HEAD)  # 본부장의 소속이 '임원'이고 맡은 부서는 본부인 모습
        try:
            j = bj(HEAD)
            ok(j["scope"] == {"deptId": 71, "name": "가상R본부"} and [p["userId"] for p in j["people"]] == [LEAD, PEER2, EMP, LEAD2, EMP2],
               "scope 는 맡은 부서: 소속이 '임원'인 본부장도 명단 제목은 이끄는 본부 (명단은 그대로)")
        finally:
            x("UPDATE users SET department_id = 71, led_department_id = NULL WHERE id = ?", HEAD)

        # 대표이사는 팀원 줄을 못 받는다
        j = bj(CEO)
        txt = json.dumps(j, ensure_ascii=False)  # 읽을 수 있는 글자로 — 응답 원문은 한글이 \uXXXX 로 바뀌어 나가 이름 찾기가 소용없다
        gone, names = (EMP, EMP2, PEER2, DMEMBER), ("가상R직원", "가상R동료", "가상R다른팀원", "가상R직속팀원")

        def leaves(o, key=""):
            """응답 JSON 안의 모든 (바로 위 키 이름, 값) — 사전의 키 글자도 값으로 한 번 더 (자리에 상관없이)."""
            if isinstance(o, dict):
                for k, v in o.items():
                    yield "", k
                    yield from leaves(v, k)
            elif isinstance(o, list):
                for v in o:
                    yield from leaves(v, key)
            else:
                yield key, o

        flat = list(leaves(j))
        ok(mine_ids(j) == [HEAD, LEAD, LEAD2, DIRECT, EXEC]
           and [by_id(j, u)["position"] for u in (HEAD, LEAD, LEAD2, DIRECT, EXEC)] == ["DIVISION_HEAD", "TEAM_LEADER", "TEAM_LEADER", "TEAM_LEADER", None],
           "대표이사는_팀원_줄을_못_받는다: 명단에는 본부장 · 팀장 · 직속 팀장 · '임원' 소속만")
        ok(not any(type(v) is int and k.lower().endswith("id") and v in gone for k, v in flat)
           and not any(isinstance(v, str) and (v in map(str, gone) or any(nm in v for nm in names)) for _, v in flat)
           and not any(nm in txt for nm in names) and by_id(j, LEAD)["items"] != [],
           "대표이사_응답_어디에도_팀원이_없다: 팀원 번호(…Id 칸 어디에도 · 글자 · 사전의 키로도) · 이름이 응답 글자에 하나도 없음")
        tm = {t["deptId"]: t for t in j["teams"]}
        ok(all(set(t) == {"deptId", "count", "confirmed", "avg", "slow"} for t in j["teams"]) and [t["deptId"] for t in j["teams"]] == sorted(tm)
           and tm[72]["count"] == 2 and tm[72]["confirmed"] == 1 and near(tm[72]["avg"], 22.0) and tm[72]["slow"] == 0
           and (tm[73]["count"], tm[73]["confirmed"], tm[73]["slow"]) == (1, 1, 1) and near(tm[73]["avg"], 0.0)
           and tm[74] == {"deptId": 74, "count": 1, "confirmed": 0, "avg": None, "slow": 0} and not {71, 75} & set(tm),
           "teams: 팀마다 인원 · 확정 · 평균(확정본이 있는 사람만, 없으면 null) · 부진 수 — 72 = 2 · 1 · 22 · 0 / 73 = 1 · 1 · 0 · 1 / 74 = 1 · 0 · null · 0 (줄로 보이는 사람 몫은 없음)")
        # 번호가 어느 칸에 새도 잡히게: 팀원 번호를 응답의 모든 정수 · 글자 · 사전의 키에서 뒤진다. 가중치(최대 100) · 부서(7x) · 달 · 해와 겹치지 않는 번호만 쓴다:
        # 있는 팀원 중 93 · 94 · 96 (이 가상 조직의 대표이사 응답에 그 숫자는 따로 없다 — 겹치면 깨끗한 코드에서도 실패하니 번호를 바꾼다. 100 은 가중치와 겹쳐 뺀다)
        # + 팀마다 하나씩 잠깐 더한 눈에 띄는 번호(9301~, 계획서 없음). 위 검사의 …Id 칸만 보는 방식은 teams 안의 "members": [93, 94] 같은 새는 길을 못 잡는다.
        probes = {9301: ("가상R새는지A", 72), 9302: ("가상R새는지B", 73), 9303: ("가상R새는지C", 74)}
        watch = {*probes, EMP, EMP2, PEER2}
        try:
            for uid, (name, dept) in probes.items():
                x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, writes_kpi) VALUES (?, ?, ?, ?, ?, 1, 1, 1)",
                  uid, name, f"r{uid}@example.invalid", dept, f"HW-{uid}")
            j2 = bj(CEO)
            n_before, n_after = {t["deptId"]: t["count"] for t in j["teams"]}, {t["deptId"]: t["count"] for t in j2["teams"]}
            seen_ints = {v for _, v in leaves(j2) if type(v) is int}
            dec = json.dumps(j2, ensure_ascii=False)
            ok(all(n_after[d] == n_before.get(d, 0) + 1 for d in (72, 73, 74)) and not seen_ints & watch
               and not any(isinstance(v, str) and v in map(str, watch) for _, v in leaves(j2))
               and not any(nm in dec for nm in (*names, *(n for n, _ in probes.values()))),
               "새는지_모든_칸에서: 팀마다 하나씩 더한 팀원이 인원에는 세어지지만, 팀원(있는 93 · 94 · 96 과 더한 9301~)의 번호(어느 칸의 정수든 · 글자 · 사전의 키) · 이름은 응답 어디에도 없음")
        finally:
            x("DELETE FROM users WHERE id IN (9301, 9302, 9303)")
        ok(all(by_id(j, u)["direct"] and by_id(j, u)["checkStep"] is False and by_id(j, u)["month"]["check"] == "NOSTEP" for u in (HEAD, DIRECT, EXEC))
           and not by_id(j, LEAD)["direct"] and not by_id(j, LEAD2)["direct"] and by_id(j, LEAD)["month"]["check"] == "NONE" and j["todo"] == {"check": 0},
           "대표이사에게 보고하는 분(본부장 · 직속 팀장 · '임원' 소속)은 direct 참 · 확인 단계 없음 · month.check NOSTEP — 팀장은 대표이사 몫이 아님")

        # HR 은 전체
        j = bj(HR)
        want = {r[0] for r in q("SELECT id FROM users WHERE is_active = 1 AND writes_kpi = 1 AND id <> ?", HR)}
        rank = {"DIVISION_HEAD": 0, "TEAM_LEADER": 1}
        ok({p["userId"] for p in j["people"]} == want and len(j["people"]) == len(want) and HR not in want and CEO not in want
           and set(mine_ids(j)) == {HEAD, LEAD, EMP, EMP2, LEAD2, PEER2, DIRECT, EXEC, DMEMBER} and j["teams"] == []
           and j["people"] == sorted(j["people"], key=lambda p: (p["deptId"], rank.get(p["position"], 2), p["name"], p["userId"])),
           "HR은_전체: 활성 · KPI 작성이 켜진 사람 모두 (HR 본인 · 작성이 꺼진 대표이사 · 비활성은 없음) — 순서는 부서 번호 → 본부장 · 팀장 · 그 밖 → 이름 · teams 빈 목록")
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", DMEMBER)
        x("UPDATE users SET writes_kpi = 0 WHERE id = ?", EMP2)
        try:
            ok(DMEMBER not in {p["userId"] for p in bj(HR)["people"]} and EMP2 not in {p["userId"] for p in bj(HR)["people"]}
               and EMP2 not in {p["userId"] for p in bj(HEAD)["people"]} and not {73, 74} & {t["deptId"] for t in bj(CEO)["teams"]},
               "비활성 · KPI 작성을 끈 사람은 명단에도 팀 묶음에도 없다")
        finally:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", DMEMBER)
            x("UPDATE users SET writes_kpi = 1 WHERE id = ?", EMP2)

        # 계획서 상태는 계획서 줄에서, 숫자는 확정본에서
        x("UPDATE kpi_plans SET status = 'CHANGE_REQUESTED', change_reason = '가상 사유' WHERE user_id = ?", EMP2)
        try:
            e2 = by_id(bj(LEAD2), EMP2)
            ok(e2["status"] == "CHANGE_REQUESTED" and e2["month"] is not None and len(e2["items"]) == 3, "수정 요청 중인 사람: status 는 CHANGE_REQUESTED, 숫자 · 월 요약은 지금 확정본으로")
        finally:
            x("UPDATE kpi_plans SET status = 'CONFIRMED', change_reason = NULL WHERE user_id = ?", EMP2)

        # 이번 달 적을 것 · 확인 상태가 바뀐다
        j = bj(LEAD, 3)
        ok(j["month"] == 3 and by_id(j, EMP)["month"] == {"reg": [0, 2], "irr": [0, 1], "records": 0, "noSched": False, "check": "NONE", "checkedAt": None},
           "확인_상태가_바뀐다: 아무것도 안 쓴 달은 NONE")
        ok(line(EMP, 3, "가상 3월 줄").status_code == 200
           and by_id(bj(LEAD, 3), EMP)["month"] == {"reg": [1, 2], "irr": [0, 1], "records": 1, "noSched": False, "check": "TODO", "checkedAt": None},
           "이번_달_적을_것: 3월 — 정기 [1, 2] · 비정기 [0, 1] · noSched 거짓 · 한 줄 쓰기 → 확인 TODO")
        ok(by_id(bj(LEAD, 4), EMP)["month"] == {"reg": [0, 2], "irr": [0, 0], "records": 0, "noSched": True, "check": "NONE", "checkedAt": None}
           and by_id(bj(LEAD), EMP)["month"] == by_id(bj(LEAD, 4), EMP)["month"], "이번_달_적을_것: 4월 — 비정기가 있는데 그 달 일정이 없어 noSched 참 · 달을 안 보내면 오늘의 달(4월)")
        ok(check(LEAD, EMP, 3).status_code == 200
           and by_id(bj(LEAD, 3), EMP)["month"] == {"reg": [1, 2], "irr": [0, 1], "records": 1, "noSched": False, "check": "DONE", "checkedAt": z(4, 5)}
           and by_id(bj(LEAD, 4), EMP)["month"]["check"] == "NONE", "확인_상태가_바뀐다: 팀장이 확인 → 그 달만 DONE · 확인한 시각")
        ok(line(EMP, 3, "가상 고친 3월 줄").status_code == 200 and by_id(bj(LEAD, 3), EMP)["month"]["check"] == "TODO" and by_id(bj(LEAD, 3), EMP)["month"]["checkedAt"] is None,
           "확인_상태가_바뀐다: 확인한 달의 줄을 고치면 다시 TODO")
        ok(by_id(bj(HEAD, 3), LEAD)["month"]["check"] == "NONE" and by_id(bj(HEAD, 3), LEAD)["direct"] is True, "(본부장 명단의 팀장 줄도 같은 규칙: 쓴 것이 없으면 NONE)")

        # 알람 기준은 한 번만 읽어 모두에게
        ok(hit(gap_set(HR, 40), 200, {"ok": True}) and bj(HR)["gap"] == 40 and bj(LEAD2)["gap"] == 40 and by_id(bj(LEAD2), EMP2)["alarm"]["slow"] is False
           and {t["deptId"]: t["slow"] for t in bj(CEO)["teams"]}[73] == 0, "알람 기준 40: 명단의 gap · 사람마다의 부진 판정 · 대표이사의 부진 수가 모두 새 기준을 따름")
        ok(hit(gap_set(HR, 20), 200, {"ok": True}) and bj(HR)["gap"] == 20 and by_id(bj(LEAD2), EMP2)["alarm"]["slow"] is True
           and {t["deptId"]: t["slow"] for t in bj(CEO)["teams"]}[73] == 1, "알람 기준 20 으로 되돌리면 부진")

        # 처리할 일 숫자
        wipe()
        confirmed(PEER2, z(1, 5))  # 다른팀원도 계획서를 가진다 — 1월에 확정이라 정기 업무가 1월부터 해당 있음 (확정본은 지울 수 없어 이 검사 끝까지 남는다)
        x("INSERT INTO kpi_month_notes (user_id, year, rid, month, text, link, updated_at) VALUES (?, ?, 1, 1, '가상 1월 줄', NULL, ?)", PEER2, year, z(1, 6))
        at(1, 8)
        ok(review(LEAD) == {"check": 1, "replies": 0, "memos": 0} and review(LEAD2)["check"] == 0 and bj(LEAD)["todo"] == {"check": 1} and bj(LEAD)["month"] == 1
           and hit(board(LEAD, 2), 400, bad_month(1)), "처리할_일_숫자: 1월 8일 — 이번 달(1월) 기록을 안 확인한 사람 1 (지난달은 작년이라 세지 않음) · 명단의 todo 와 같은 수")
        at(2, 5)
        ok(review(LEAD)["check"] == 1, "2월 5일 — 지난달(1월) 기록도 셈")
        at(3, 5)
        ok(line(EMP, 2, "가상 2월 줄").status_code == 200 and line(EMP, 3, "가상 3월 줄").status_code == 200, "(준비: 3월 5일 — 직원이 2월 · 3월 줄을 씀)")
        at(3, 10)
        ok(review(LEAD) == {"check": 1, "replies": 0, "memos": 0} and bj(LEAD)["todo"] == {"check": 1},
           "처리할_일_숫자: 3월 10일 — 지난달(2월) · 이번 달(3월)에 확인 안 한 기록이 있는 직원 1 (직원이 두 달 다 써도 한 사람) · 다른팀원의 확인 안 한 1월 기록은 세지 않는다")
        ok(line(PEER2, 3, "가상 3월 줄").status_code == 200 and review(LEAD) == {"check": 2, "replies": 0, "memos": 0} and bj(LEAD)["todo"] == {"check": 2},
           "다른팀원도 3월 줄을 쓰면 팀원 둘이 확인 안 한 기록 → 팀장 check 2 (사람 수)")
        ok(review(LEAD2)["check"] == 0 and review(HEAD)["check"] == 0 and review(CEO) == {"check": 0, "replies": 0, "memos": 0} and review(HR) == {"check": 0, "replies": 0, "memos": 0}
           and review(EMP) == {"check": 0, "replies": 0, "memos": 0},
           "바로 위 관리자가 아닌 사람 · 대표이사 · HR · 본인은 0")
        ok(check(LEAD, EMP, 2).status_code == 200 and review(LEAD)["check"] == 2, "(직원의 2월을 확인해도 3월이 남아 있어 사람 수는 그대로)")
        ok(check(LEAD, EMP, 3).status_code == 200 and review(LEAD)["check"] == 1 and bj(LEAD)["todo"] == {"check": 1}, "(3월도 확인 → 1)")
        ok(check(LEAD, PEER2, 3).status_code == 200 and review(LEAD)["check"] == 0 and bj(LEAD)["todo"] == {"check": 0}, "(다른팀원도 확인 → 0)")

        at(3, 11)
        mA, mB = memo_post(LEAD, EMP, "가상 메모").json["id"], memo_post(LEAD, PEER2, "가상 메모 2").json["id"]
        ok(review(EMP) == {"check": 0, "replies": 0, "memos": 1} and review(PEER2)["memos"] == 1 and review(LEAD) == {"check": 0, "replies": 0, "memos": 0}
           and review(HEAD)["memos"] == 0, "처리할_일_숫자: 팀장이 메모를 쓰면 본인 inbox 의 memos(확인 안 한 내 메모) 1 · 쓴 팀장은 아직 0")
        at(3, 11, 4)
        ok(memo_do(EMP, mA, "reply", text="가상 본인 답글").status_code == 200 and memo_do(EMP, mA, "reply", text="가상 본인 답글 2").status_code == 200
           and review(LEAD)["replies"] == 1 and review(EMP)["memos"] == 0, "본인이 답글(둘)을 달면 팀장 replies 1 (답글 수가 아니라 사람 수) · 본인 답글은 확인한 것이라 memos 0")
        ok(memo_do(PEER2, mB, "reply", text="가상 본인 답글").status_code == 200 and review(LEAD)["replies"] == 2, "다른 사람의 답글도 더해 replies 2")
        at(3, 11, 5)
        ok(memo_do(LEAD, mA, "reply", text="가상 팀장 답글").status_code == 200 and review(EMP)["memos"] == 1 and review(LEAD)["replies"] == 2,
           "팀장이 답글을 달면 본인의 확인이 풀려 memos 1 (팀장 자신의 답글은 새 답글이 아님)")
        ok(by_id(bj(LEAD), EMP)["memo"] == {"count": 1, "open": 1, "fresh": 2} and by_id(bj(LEAD), PEER2)["memo"] == {"count": 1, "open": 0, "fresh": 1}
           and by_id(bj(HEAD), EMP)["memo"] == {"count": 1, "open": 1, "fresh": 0}, "명단의 memo: 올해 메모 수 · 본인이 확인 안 한 수 · 내가 쓴 메모의 못 본 본인 답글 수 (남이 쓴 메모의 답글은 0)")
        x("UPDATE users SET department_id = 73 WHERE id = ?", PEER2)  # 다른팀원이 다른 팀으로 — 팀장이 더는 볼 수 없다
        try:
            ok(review(LEAD)["replies"] == 1 and PEER2 not in {p["userId"] for p in bj(LEAD)["people"]}, "더 이상 볼 수 없는 사람의 새 답글은 세지 않음 (명단에 없는 사람의 숫자는 지울 길이 없다)")
        finally:
            x("UPDATE users SET department_id = 72 WHERE id = ?", PEER2)
        ok(review(LEAD)["replies"] == 2, "(다시 같은 팀이 되면 다시 셈)")
        ok(memo_do(EMP, mA, "ack").status_code == 200 and review(EMP)["memos"] == 0 and review(LEAD)["replies"] == 2, "본인이 확인했습니다 → 본인 memos 0 (팀장의 못 본 답글은 그대로)")
        at(3, 11, 6)
        ok(seen(LEAD, EMP).status_code == 200 and review(LEAD)["replies"] == 1 and seen(LEAD, PEER2).status_code == 200 and review(LEAD)["replies"] == 0
           and by_id(bj(LEAD), EMP)["memo"]["fresh"] == 0, "열어 보았다고 보내면 replies 가 줄어 0")
        ok(all(set(review(w)) == {"check", "replies", "memos"} for w in (LEAD, HEAD, CEO, HR, EMP)), "inbox 의 review 모양: check · replies · memos")

        # 알람 기준 · 경영계획은 서로의 칸과 기간 날짜를 건드리지 않는다 (이미 날짜가 있는 해에서)
        Y3 = 2098
        d3 = (f"{Y3}-03-01", f"{Y3}-03-31", f"{Y3}-06-01")
        x("INSERT INTO kpi_years (year, edit_from, edit_to, eval_start, updated_at) VALUES (?, ?, ?, ?, ?)", Y3, *d3, z(1, 1))
        ok(hit(gap_set(HR, 33, Y3), 200, {"ok": True}) and year_row(Y3)[0][:5] == (*d3, 33, None), "알람 기준을 넣어도 이미 있는 기간 날짜 · 경영계획 칸은 그대로")
        ok(hit(biz_set(HR, [goal("가상 목표", "가상 내용")], Y3), 200, {"ok": True}) and year_row(Y3)[0][:4] == (*d3, 33) and json.loads(year_row(Y3)[0][4]) == [goal("가상 목표", "가상 내용")],
           "경영계획을 넣어도 기간 날짜 · 알람 기준은 그대로")
        ok(hit(gap_set(HR, 44, Y3), 200, {"ok": True}) and year_row(Y3)[0][:4] == (*d3, 44) and json.loads(year_row(Y3)[0][4]) == [goal("가상 목표", "가상 내용")],
           "알람 기준을 다시 바꿔도 경영계획 · 기간 날짜는 그대로")
        x("DELETE FROM kpi_years WHERE year = ?", Y3)

        # 이 절이 만든 것을 치운다
        # (다른팀원 PEER2 의 1월 확정본은 kpi_versions 줄이라 지울 수 없어 이 검사 끝까지 남는다 — 뒤 검사는 이 사람을 쓰지 않는다)
        wipe()
        x("UPDATE users SET department_id = 73 WHERE id = ?", PEER2)
        if had_row:
            x("UPDATE kpi_years SET alarm_gap = 20, biz_plan = NULL WHERE year = ?", year)
        else:
            x("DELETE FROM kpi_years WHERE year = ?", year)
    finally:
        auth._now = real_now
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)
        for (ev,) in ev_saved:
            x("UPDATE kpi_years SET eval_start = ? WHERE year = ?", ev, year)


def check_eval_admin(hr) -> None:
    """인사평가 회차 · 시작 · HR 명단 일 (2026-10-26, 평가 3단계 ①): HR 이 회차(단계별 마감일 · 등급 기준 점수)를 정하고, 평가 시작일(한국 날짜)부터 [평가 시작]을 한 번 누르면
    그 순간의 평가 대상 · 묶음 · 평가자 · 배정자 · 확정 KPI 판을 ev_targets 에 떠 둔다 (그 뒤 조직 · 구성원 설정이 바뀌어도 그대로). 근태 입력(연차 초과 5일이면 S·A·B 지움 · 넘어간 사람은 잠김) ·
    제외 · 되돌림 · 자기평가 없이 진행(평가자에게 알림) · 평가자 바꾸기 · 시작한 해의 평가 시작일 잠금. 감사로그에는 해 · 사람 수 · 평가자 번호만 (사유 · 근태 숫자 · 등급 없음).
    시계는 FIRST_YEAR(포털에서 평가하는 첫 해) 안으로 정해 놓고 돈다 — 시작은 올해 평가만 되므로. 알림은 가짜 보내기 함수로 바로(SYNC) 본다 (휴대폰 알림 검사와 같은 방법).
    이 검사만의 가상 조직: 부서 91~97 · 사람 101~116 (81~84 는 일정 변경 검사가 이미 쓴다). 떠 둔 줄 · 회차는 지울 수 없어 남는다 — 뒤 검사(평가 읽기)가 이 조직과 시작한 회차를 이어 쓴다.
    다른 사람의 평가 대상 칸 · 다른 대표이사 · 시계 · 알림 설정은 되돌린다."""
    import evaluation
    import push
    print("인사평가 회차 · 시작 · HR 명단 일")
    # 본부(91) > 팀A(92, 팀장 있음) · 팀B(93, 팀장 없음) / 본부장 없는 본부(94) > 팀C(95) / 대표이사 직속 팀(96 — HR 팀장 · HR 담당자) / 장이 없는 '임원'(97)
    for dept in ((91, "E91", "가상E본부", "DIVISION", None), (92, "E92", "가상E팀A", "TEAM", 91), (93, "E93", "가상E팀B", "TEAM", 91),
                 (94, "E94", "가상E본부2", "DIVISION", None), (95, "E95", "가상E팀C", "TEAM", 94), (96, "E96", "가상E지원팀", "DIVISION", None),
                 (97, "E97", "임원", "DIVISION", None)):
        x("INSERT INTO departments (id, code, name, level, parent_id) VALUES (?, ?, ?, ?, ?)", *dept)
    HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO, OFF, GONE, NOCONF, HEAD2 = range(101, 117)
    cast = {HEAD: ("가상E본부장", 91, ["DIVISION_HEAD"], 1), LEAD: ("가상E팀장", 92, ["TEAM_LEADER"], 1), EMP: ("가상E팀원", 92, [], 1),
            FIELD: ("가상E현장직", 92, [], 0), EMPB: ("가상E팀원B", 93, [], 1), DIVD: ("가상E본부직속", 91, [], 1),
            LEADC: ("가상E팀장C", 95, ["TEAM_LEADER"], 1), EMPC: ("가상E팀원C", 95, [], 1), HRL: ("가상EHR팀장", 96, ["TEAM_LEADER", "HR_ADMIN"], 1),
            HR: ("가상EHR담당", 96, ["HR_ADMIN"], 1), EXEC: ("가상E임원직원", 97, [], 1), CEO: ("가상E대표", 97, ["CEO"], 0),
            OFF: ("가상E평가꺼짐", 92, [], 1), GONE: ("가상E퇴직", 92, [], 1), NOCONF: ("가상E확정없음", 93, [], 1), HEAD2: ("가상E새본부장", 91, [], 1)}
    for uid, (name, dept, roles, writes) in cast.items():  # 임원 소속(EXEC)은 포털 사용이 꺼져 있다 — 그래도 평가 대상 (R3)
        x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, deactivated_at, writes_kpi, in_evaluation, title)"
          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '가상직급')", uid, name, f"e{uid}@example.invalid", dept, f"HW-{uid}", int(uid != EXEC),
          int(uid != GONE), "2027-01-01T00:00:00Z" if uid == GONE else None, writes, int(uid not in (OFF, HEAD2)))
        for role in roles:
            x("INSERT INTO user_roles (user_id, role) VALUES (?, ?)", uid, role)

    Y, real_now, real_send, real_sync = evaluation.FIRST_YEAR, auth._now, push.SEND, push.SYNC
    ymd = lambda m, d, y=Y: f"{y}-{m:02d}-{d:02d}"  # noqa: E731
    z = lambda m, d, hh=3, y=Y: f"{y}-{m:02d}-{d:02d}T{hh:02d}:00:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def at(m, d, hh=3, mm=0, y=Y):
        """시계를 UTC 로 (y년 m월 d일 hh:mm) 에 맞춘다. 기본 03:00 = 한국 정오 (근무시간 — 알림이 간다)."""
        t = datetime(y, m, d, hh, mm, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    def item(name, w, rid):
        return {"action": name, "formula": "가상 측정식", "weight": w, "owner": "가상E", "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c",
                "D": "가상 d", "kind": "reg", "target": None, "unit": "", "scheds": [], "rid": rid}

    def confirmed(uid, *versions):
        """확정본을 바로 넣는다 — versions 는 판마다 실행목표 번호 (가중치 40 · 30 · 30)."""
        for v, rids in enumerate(versions, 1):
            content = json.dumps([{"team": "가상 팀목표", "personal": "가상 개인목표", "items": [item(f"가상 업무 {r}", w, r) for r, w in zip(rids, (40, 30, 30))]}],
                                 ensure_ascii=False)
            if v == 1:
                x("INSERT INTO kpi_plans (user_id, year, status, draft, confirmed_version, updated_at) VALUES (?, ?, 'CONFIRMED', ?, ?, ?)", uid, Y, content, len(versions), z(3, 10))
            x("INSERT INTO kpi_versions (user_id, year, version, content, confirmed_at, confirmed_by) VALUES (?, ?, ?, ?, ?, ?)", uid, Y, v, content, z(3, 10 + v), HEAD)

    for uid in (LEAD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC):
        confirmed(uid, (1, 2, 3))
    confirmed(EMP, (1, 2, 3), (1, 2, 4))  # 판이 둘 — 마지막 확정 판(2)의 실행목표를 떠 둔다
    x("INSERT INTO kpi_plans (user_id, year, status, draft, updated_at) VALUES (?, ?, 'DRAFT', '[]', ?)", NOCONF, Y, z(3, 10))  # KPI 를 쓰는데 확정본이 없다

    post = lambda who, path, body: c(who).post(path, base_url=BASE, headers=ORIGIN, json=body)  # noqa: E731
    hit = lambda r, code, body: r.status_code == code and r.json == body  # noqa: E731
    ROUND = {"year": Y, "evalStart": ymd(12, 1), "selfTo": ymd(12, 10), "evidDue": ymd(12, 10), "scoreTo": ymd(12, 20),
             "gradeTo": ymd(1, 10, Y + 1), "ceoTo": ymd(1, 20, Y + 1), "finalTo": ymd(2, 10, Y + 1), "bands": {"A": 90, "B": 80, "C": 70}}
    rnd = lambda who=None, **over: post(who or HR, "/api/eval/admin/round", {**ROUND, **over})  # noqa: E731
    adm = lambda what, who=None, **body: post(who or HR, f"/api/eval/admin/{what}", {"year": Y, **body})  # noqa: E731
    one = lambda uid, what, who=None, **body: post(who or HR, f"/api/eval/admin/{uid}/{what}", {"year": Y, **body})  # noqa: E731
    att = lambda rows: adm("attendance", rows=rows)  # noqa: E731
    COLS = "pool, rater_id, assigner_id, stage, kpi_version, writes_kpi, div_name, team_name, division_id, team_id, is_leader, title"
    tgt = lambda uid: next(iter(q(f"SELECT {COLS} FROM ev_targets WHERE year = ? AND user_id = ?", Y, uid)), None)  # noqa: E731
    lv = lambda uid: q("SELECT late, leave_x2, grade FROM ev_targets WHERE year = ? AND user_id = ?", Y, uid)[0]  # noqa: E731
    out_of = lambda uid: q("SELECT out_at, out_reason, out_by FROM ev_targets WHERE year = ? AND user_id = ?", Y, uid)[0]  # noqa: E731
    rater = lambda uid: q("SELECT rater_id FROM ev_targets WHERE year = ? AND user_id = ?", Y, uid)[0][0]  # noqa: E731
    audits = lambda action: [(e, json.loads(a)) for e, a in q("SELECT entity_id, after_data FROM audit_logs WHERE action = ? ORDER BY id", action)]  # noqa: E731
    kyear = lambda: q("SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", Y)  # noqa: E731
    ky = lambda **b: post(HR, "/api/kpi/admin/year", {"year": Y, "editFrom": None, "editTo": None, "evalStart": ymd(12, 1), **b})  # noqa: E731

    def stage(uid, s, grade=None, proposed="B"):
        """단계를 바로 넣는다 (등급 배정 이후 단계엔 평가자 제안이 있어야 한다 — 표의 CHECK)."""
        x("UPDATE ev_targets SET stage = ?, proposed = ?, grade = ? WHERE year = ? AND user_id = ?", s, None if s in ("SELF", "SCORE") else proposed, grade, Y, uid)

    sent: list[tuple[int, str, str]] = []
    owner: dict[str, int] = {}

    def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 둔다 (받은 사람 · 제목 · 열 화면)
        sent.append((owner[info["endpoint"]], payload["title"], payload["url"]))
        return 201

    def took():
        out = sorted(sent)
        sent.clear()
        return out

    TURN = ("인사평가에서 처리할 일이 있어요", "/?go=perf.e2")
    for uid in (EMP, FIELD, NOCONF, HEAD, LEAD):  # 휴대폰을 등록한 사람 — KPI 있는 대상 둘(EMP · LEAD) · KPI 없는 대상 둘 · 평가 대상이 아닌 본부장(평가자)
        h, ep = f"{uid:03d}" + "5" * 61, f"https://push.example.invalid/eval-{uid}"
        x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')", h, uid, z(11, 30), z(11, 30), z(12, 31))
        x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, z(11, 30))
        owner[ep] = uid

    others = [r[0] for r in q("SELECT id FROM users WHERE id NOT BETWEEN 101 AND 116 AND in_evaluation = 1")]  # 앞 검사의 사람들 — 이 검사 동안만 평가 대상에서 뺀다
    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    db = connect(os.environ["HR_DB_PATH"])  # cur_year · _round · _ceo 를 바로 부를 연결
    HR_ONLY, BAD_YEAR = {"error": "HR만 할 수 있습니다."}, {"error": "해가 올바르지 않습니다."}
    NOT_STARTED, RELEASED = {"error": "평가를 아직 시작하지 않았습니다."}, {"error": "결과를 공개한 평가는 바꿀 수 없습니다."}
    NOT_TARGET, LOCKED = {"error": "평가 대상이 아닙니다."}, {"error": "평가가 시작되어 평가 시작일을 바꿀 수 없습니다."}
    try:
        for uid in others:
            x("UPDATE users SET in_evaluation = 0 WHERE id = ?", uid)
        for uid in other_ceos:  # 이 가상 조직의 대표이사는 내가 만든 한 명뿐이게
            x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
        pub, priv = push.keygen()
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        push.SYNC, push.SEND = True, fake_send

        # ---- 아무것도 정하기 전 (한국 11월 1일 월요일 정오) ----
        at(11, 1)
        paths = ("round", "start", "att-done", "attendance", f"{EMP}/exclude", f"{EMP}/include", f"{EMP}/skip-self", f"{EMP}/rater")
        ok(all(app.test_client().post(f"/api/eval/admin/{p}", base_url=BASE, headers=ORIGIN, json={}).status_code == 401 for p in paths), "로그인 없이는 401 (평가 HR 일 여덟 길)")
        ok(all(hit(post(LEAD, f"/api/eval/admin/{p}", {"year": Y}), 403, HR_ONLY) for p in paths), "회차는_HR이_2027년부터: 팀장(HR 아님)은 여덟 길 모두 403")
        ok(all(hit(rnd(year=v), 400, BAD_YEAR) and hit(adm("start", year=v), 400, BAD_YEAR) and hit(adm("attendance", year=v, rows=[]), 400, BAD_YEAR)
               and hit(one(EMP, "exclude", year=v, reason="가상"), 400, BAD_YEAR) for v in (None, str(Y), float(Y), True, 2025, 2101)),
           "해가 정수(2026~2100)가 아니면 400 (글자 · 소수 · 참/거짓 · 범위 밖)")
        offline = {"error": f"{Y - 1}년 평가는 포털 밖에서 진행합니다."}
        ok(hit(rnd(year=Y - 1, evalStart=ymd(12, 1, Y - 1)), 403, offline) and hit(adm("start", year=Y - 1), 403, offline),
           f"회차는_HR이_2027년부터: {Y - 1}년(포털 평가 전 해)은 회차도 시작도 403 · 문구")
        ok(hit(adm("start", year=Y + 1), 403, {"error": "올해 평가만 시작할 수 있습니다."}), "올해가 아닌 해는 시작 403")
        ok(hit(adm("start"), 409, {"error": "단계별 마감일을 먼저 정해 주세요."}), "시작은_평가_시작일부터_한_번: 회차 없이 409")
        ok(hit(adm("att-done"), 409, NOT_STARTED) and hit(att([]), 409, NOT_STARTED) and hit(one(EMP, "exclude", reason="가상"), 404, NOT_TARGET),
           "시작 전에는 근태 · 근태 마침 409, 사람 일은 대상이 없어 404")
        ok(evaluation.cur_year(db) == Y and evaluation._round(db, Y) is None, "(회차가 없으면 보일 해는 올해, 회차 없음)")

        # ---- 회차 ----
        bad = {"날짜 빠짐": {"selfTo": None}, "없는 날": {"selfTo": ymd(11, 31)}, "날짜 모양": {"selfTo": f"{Y}-12-5"}, "날짜 숫자": {"scoreTo": 20271220},
               "평가 시작일이 그해 밖": {"evalStart": ymd(1, 5, Y + 1)}, "마감일이 평가 시작일 전": {"selfTo": ymd(11, 30)},
               "3월 연봉계약 뒤": {"finalTo": ymd(3, 1, Y + 1)}, "순서가 거꾸로": {"scoreTo": ymd(12, 5)}, "등급 배정이 대표이사 확인 뒤": {"gradeTo": ymd(1, 25, Y + 1)},
               "증빙이 평가자 평가 뒤": {"evidDue": ymd(12, 21)}, "증빙이 평가 시작일 전": {"evidDue": ymd(11, 30)},
               "기준 A = B": {"bands": {"A": 80, "B": 80, "C": 70}}, "기준 101": {"bands": {"A": 101, "B": 80, "C": 70}}, "기준 0": {"bands": {"A": 90, "B": 80, "C": 0}},
               "기준 소수": {"bands": {"A": 90.0, "B": 80, "C": 70}}, "기준 참/거짓": {"bands": {"A": 90, "B": True, "C": 0}}, "기준 빠짐": {"bands": {"A": 90, "B": 80}},
               "기준 목록": {"bands": [90, 80, 70]}, "기준 없음": {"bands": None}}
        ok(all(rnd(**v).status_code == 400 for v in bad.values()) and not q("SELECT 1 FROM ev_rounds WHERE year = ?", Y) and not q("SELECT 1 FROM kpi_years WHERE year = ?", Y),
           f"회차: 날짜 모양 · 범위 · 순서 · 기준 점수가 틀리면 400, 아무것도 안 남김 ({', '.join(bad)})")
        ok(hit(rnd(scoreTo=ymd(12, 5)), 400, {"error": "마감일은 자기평가 → 평가자 평가 → 등급 배정 → 대표이사 확인 → HR 확정 차례로 정해 주세요."})
           and hit(rnd(bands={"A": 80, "B": 80, "C": 70}), 400, {"error": "등급 기준 점수는 1~100 사이 정수로, A > B > C 가 되게 넣어 주세요."}),
           "회차는_HR이_2027년부터: 순서가 거꾸로 400 · 기준 점수 A ≤ B 400 (문구)")
        ok(hit(rnd(), 200, {"ok": True}) and q("SELECT edit_from, edit_to, eval_start, updated_by FROM kpi_years WHERE year = ?", Y) == [(None, None, ymd(12, 1), HR)]
           and q("SELECT self_to, evid_due, score_to, grade_to, ceo_to, final_to, band_a, band_b, band_c, started_at, updated_by FROM ev_rounds WHERE year = ?", Y)
           == [(ymd(12, 10), ymd(12, 10), ymd(12, 20), ymd(1, 10, Y + 1), ymd(1, 20, Y + 1), ymd(2, 10, Y + 1), 90, 80, 70, None, HR)]
           and audits("EVAL_ROUND_SET") == [(Y, {"year": Y})],
           "회차는_HR이_2027년부터: HR 200 → 작성 기간 없이 kpi_years 줄을 만들고 평가 시작일을 넣음 · 회차 줄 · 감사로그 EVAL_ROUND_SET (해만)")
        feb = date(Y + 1, 3, 1) - timedelta(days=1)
        ok(hit(rnd(finalTo=feb.isoformat()), 200, {"ok": True}) and q("SELECT final_to FROM ev_rounds WHERE year = ?", Y) == [(feb.isoformat(),)]
           and hit(rnd(), 200, {"ok": True}), "다음 해 2월 말일까지는 됨 (같은 줄을 고침)")
        ok(evaluation._round(db, Y) == {"year": Y, **{k: ROUND[k] for k in ("evalStart", "selfTo", "evidDue", "scoreTo", "gradeTo", "ceoTo", "finalTo")},
                                         "bands": {"A": 90, "B": 80, "C": 70}, "started": None, "attDone": None, "released": None}, "_round: 회차 줄 + 평가 시작일 모양")

        # ---- 시작 ----
        at(11, 30, 14, 59)  # 한국 11월 30일 23:59 — 평가 시작일 전날
        ok(hit(adm("start"), 403, {"error": "평가 시작일(12/1)부터 시작할 수 있습니다."}) and not q("SELECT 1 FROM ev_targets WHERE year = ?", Y),
           "시작은_평가_시작일부터_한_번: 시작일 전날(한국 23:59) 403 · 아무것도 안 남김")
        at(12, 1)  # 한국 12월 1일(수) 정오
        MOVED = {"error": "평가 시작일이 마감일보다 늦어요. 마감일을 다시 저장해 주세요."}
        a = ky(evalStart=ymd(12, 11)).status_code == 200 and hit(adm("start"), 409, MOVED)  # 자기평가 · 증빙 마감(12/10)보다 늦음 (오늘 < 평가 시작일 403 보다 먼저)
        b = hit(rnd(selfTo=ymd(12, 15)), 200, {"ok": True}) and ky(evalStart=ymd(12, 11)).status_code == 200 and hit(adm("start"), 409, MOVED)  # 증빙 마감(12/10)보다만 늦음
        ok(a and b and not q("SELECT 1 FROM ev_targets WHERE year = ?", Y) and q("SELECT started_at FROM ev_rounds WHERE year = ?", Y) == [(None,)],
           "회차 저장 뒤 /api/kpi/admin/year 로 평가 시작일을 마감일(자기평가 · 증빙)보다 늦게 옮기면 시작 409 · 아무것도 안 떠 둠")
        ok(hit(rnd(), 200, {"ok": True}) and kyear() == [(None, None, ymd(12, 1))], "(회차를 다시 저장하면 평가 시작일도 돌아옴)")
        x("UPDATE users SET in_evaluation = 0 WHERE id BETWEEN 101 AND 116")
        ok(hit(adm("start"), 409, {"error": "평가 대상이 없습니다."}) and q("SELECT started_at FROM ev_rounds WHERE year = ?", Y) == [(None,)], "평가 대상이 0명이면 409 · 시작 안 됨")
        x("UPDATE users SET in_evaluation = 1 WHERE id BETWEEN 101 AND 116 AND id NOT IN (?, ?)", OFF, HEAD2)
        # 평가자 · 배정자를 못 찾는 시작 — 회차는 한 번만 시작되므로 시작 직전의 DB 를 떠 놓은 사본에서 해 본다 (그날 본부장 · 대표이사가 비활성)
        main_db, copy_db = os.environ["HR_DB_PATH"], str(Path(TMP.name) / "eval_start_copy.db")
        src, dst = connect(main_db), connect(copy_db)
        src.backup(dst)
        src.close()
        dst.close()
        os.environ["HR_DB_PATH"] = copy_db  # 앱(auth._db) · q · x 가 모두 사본을 본다
        try:
            at(12, 1)
            x("UPDATE users SET is_active = 0, deactivated_at = ? WHERE id IN (?, ?)", z(11, 30), HEAD, CEO)
            r = adm("start")
            ok(hit(r, 200, {"ok": True, "count": 11, "noKpi": 1, "noRater": 7})
               and all(rater(u) is None for u in (EMPB, DIVD, NOCONF, LEAD, LEADC, HRL, EXEC)) and [rater(u) for u in (EMP, FIELD, EMPC, HR)] == [LEAD, LEAD, LEADC, HRL]
               and q("SELECT DISTINCT assigner_id FROM ev_targets WHERE year = ? AND pool = 'DIV:91'", Y) == [(None,)],
               f"(DB 사본) 본부장 · 대표이사가 비활성인 날 시작 → 위로 아무도 없는 7명은 평가자 NULL (응답 noRater 7) · 본부장이 비활성인 본부 묶음은 배정자 NULL(대표이사) ({r.json})")
        finally:
            os.environ["HR_DB_PATH"] = main_db
            jar.clear()
            took()
        ok(not q("SELECT 1 FROM ev_targets WHERE year = ?", Y) and q("SELECT is_active FROM users WHERE id IN (?, ?) ORDER BY id", HEAD, CEO) == [(1,), (1,)],
           "(진짜 DB 는 그대로 — 아직 시작 전 · 본부장 · 대표이사 활성)")
        r = adm("start")
        ok(hit(r, 200, {"ok": True, "count": 11, "noKpi": 1, "noRater": 0}) and q("SELECT started_at, started_by FROM ev_rounds WHERE year = ?", Y) == [(z(12, 1), HR)],
           f"시작은_평가_시작일부터_한_번: 당일 200 · 대상 11명 · 확정 KPI 없음 1 · 평가자 못 찾음 0 ({r.json})")
        ok(took() == [(LEAD, *TURN), (EMP, *TURN)], "시작 → KPI 가 있는 대상에게만 알림 (KPI 없는 사람 · 평가 대상이 아닌 사람은 0통)")
        ok(hit(adm("start"), 409, {"error": "이미 시작했습니다."}) and q("SELECT COUNT(*) FROM ev_targets WHERE year = ?", Y) == [(11,)] and not took(),
           "시작은_평가_시작일부터_한_번: 또 누르면 409 · 줄이 늘지 않음 · 알림 없음")
        ok(audits("EVAL_STARTED") == [(Y, {"year": Y, "count": 11})], "감사로그 EVAL_STARTED (해 · 사람 수)")
        D1, D2 = "가상E본부", "가상E본부2"
        want = {EMP: ("DIV:91", LEAD, HEAD, "SELF", 2, 1, D1, "가상E팀A", 91, 92, 0, "가상직급"),
                FIELD: ("DIV:91", LEAD, HEAD, "SCORE", None, 0, D1, "가상E팀A", 91, 92, 0, "가상직급"),
                EMPB: ("DIV:91", HEAD, HEAD, "SELF", 1, 1, D1, "가상E팀B", 91, 93, 0, "가상직급"),
                DIVD: ("DIV:91", HEAD, HEAD, "SELF", 1, 1, D1, "본부 직속", 91, None, 0, "가상직급"),
                NOCONF: ("DIV:91", HEAD, HEAD, "SCORE", None, 1, D1, "가상E팀B", 91, 93, 0, "가상직급"),
                EMPC: ("DIV:94", LEADC, None, "SELF", 1, 1, D2, "가상E팀C", 94, 95, 0, "가상직급"),
                LEAD: ("LEADERS", HEAD, None, "SELF", 1, 1, D1, "가상E팀A", 91, 92, 1, "가상직급"),
                LEADC: ("LEADERS", CEO, None, "SELF", 1, 1, D2, "가상E팀C", 94, 95, 1, "가상직급"),
                HRL: ("LEADERS", CEO, None, "SELF", 1, 1, "대표이사 직속부서", "가상E지원팀", None, 96, 1, "가상직급"),
                HR: ("DIRECT", HRL, None, "SELF", 1, 1, "대표이사 직속부서", "가상E지원팀", None, 96, 0, "가상직급"),
                EXEC: ("DIRECT", CEO, None, "SELF", 1, 1, "대표이사 직속부서", "본부 직속", None, None, 0, "가상직급")}
        bad_rows = [(uid, tgt(uid)) for uid, row in want.items() if tgt(uid) != row]
        ok(not bad_rows, f"떠_둔_묶음과_평가자: 묶음 · 평가자 · 배정자 · 단계 · KPI 판 · 이름 칸이 맞음 (팀원 → 팀장 · 팀장 없는 팀 → 본부장 · 본부 직속 → 본부장 · "
                         f"본부장 없는 본부 → 배정자 대표이사 · 팀장 → 본부장 또는 대표이사 · HR 담당 → HR 팀장 · 임원 소속 → 대표이사) {bad_rows}")
        ok(all(tgt(uid) is None for uid in (HEAD, CEO, OFF, GONE, HEAD2)), "떠_둔_묶음과_평가자: 본부장 · 대표이사 · 평가 대상 꺼짐 · 비활성은 없음 (포털 사용이 꺼진 임원 소속은 있음)")
        ok(tgt(NOCONF)[3:5] == ("SCORE", None) and tgt(FIELD)[3:5] == ("SCORE", None), "확정_KPI가_없으면_등급만: KPI 를 쓰는데 확정본 없음 → kpi_version NULL · SCORE (응답 noKpi 1) · KPI 없는 사람도 SCORE")
        ok(q("SELECT rid, note, self_grade, grade FROM ev_items WHERE year = ? AND user_id = ? ORDER BY rid", Y, EMP) == [(1, "", None, None), (2, "", None, None), (4, "", None, None)]
           and q("SELECT COUNT(*) FROM ev_items WHERE year = ?", Y) == [(27,)] and not q("SELECT 1 FROM ev_items WHERE year = ? AND user_id IN (?, ?)", Y, FIELD, NOCONF),
           "실행목표마다 빈 줄 — 마지막 확정 판(판 2: 1 · 2 · 4)의 것, KPI 없는 사람은 없음")
        ok(q("SELECT pool, closed_at, hr_seen FROM ev_pools WHERE year = ? ORDER BY pool", Y) == [("DIRECT", None, "[]"), ("DIV:91", None, "[]"), ("DIV:94", None, "[]"), ("LEADERS", None, "[]")],
           "묶음마다 ev_pools 한 줄")
        ok(evaluation.cur_year(db) == Y and evaluation._round(db, Y)["started"] == z(12, 1) and evaluation._ceo(db) == CEO and evaluation._deleg(db, CEO) is False,
           "(보일 해 = 시작한 올해 · _round 의 started · 활성 대표이사 · 맡기지 않음)")
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 1))
        ok(evaluation._deleg(db, CEO) is True and evaluation._deleg(db, None) is False, "(_deleg: 맡기면 참 · 대표이사가 없으면 거짓)")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)

        # ---- 시작 뒤 조직이 바뀌어도 · 자기평가 없이 진행 ----
        at(12, 2)
        frozen = lambda: q("SELECT user_id, pool, rater_id, assigner_id, division_id, team_id, div_name, team_name, is_leader FROM ev_targets WHERE year = ? ORDER BY user_id", Y)  # noqa: E731
        before = frozen()
        x("UPDATE users SET department_id = 93 WHERE id = ?", EMP)  # 팀A → 팀B (팀장 없는 팀)
        x("UPDATE user_roles SET user_id = ? WHERE user_id = ? AND role = 'DIVISION_HEAD'", HEAD2, HEAD)  # 본부장이 바뀜
        x("UPDATE users SET in_evaluation = 0 WHERE id = ?", FIELD)
        r = one(EMPB, "skip-self")
        ok(hit(r, 200, {"ok": True}) and q("SELECT stage, no_self_at, no_self_by FROM ev_targets WHERE year = ? AND user_id = ?", Y, EMPB) == [("SCORE", z(12, 2), HR)],
           "자기평가_없이_진행: SELF → SCORE · no_self_at · no_self_by")
        ok(took() == [(HEAD, *TURN)], "자기평가_없이_진행: 떠 둔 평가자(옛 본부장)에게 알림 하나 — 지금 본부장이 아니어도")
        ok(frozen() == before, "시작_뒤에는_조직이_바뀌어도_그대로: 팀을 옮기고 본부장을 바꾸고 평가 대상을 꺼도 묶음 · 평가자 · 배정자 · 이름 칸이 그대로")
        x("UPDATE users SET department_id = 92 WHERE id = ?", EMP)
        x("UPDATE user_roles SET user_id = ? WHERE user_id = ? AND role = 'DIVISION_HEAD'", HEAD, HEAD2)
        x("UPDATE users SET in_evaluation = 1 WHERE id = ?", FIELD)
        ok(hit(one(EMPB, "skip-self"), 409, {"error": "자기평가 단계가 아닙니다."}) and hit(one(FIELD, "skip-self"), 409, {"error": "자기평가 단계가 아닙니다."})
           and hit(one(OFF, "skip-self"), 404, NOT_TARGET) and not took(), "자기평가_없이_진행: 이미 넘어간 사람 · KPI 없는 사람(처음부터 SCORE) 409 · 대상 아님 404 · 알림 없음")
        ok(audits("EVAL_SKIP_SELF") == [(EMPB, {"year": Y})], "감사로그 EVAL_SKIP_SELF (해만)")

        # ---- 시작한 해의 평가 시작일 ----
        ok(hit(ky(evalStart=ymd(12, 2)), 409, LOCKED) and hit(ky(evalStart=None), 409, LOCKED) and kyear() == [(None, None, ymd(12, 1))],
           "시작한_해의_평가_시작일은_못_바꾼다: /api/kpi/admin/year 로 평가 시작일을 바꾸거나 지우면 409 · 그대로")
        ok(ky(editFrom=ymd(1, 1), editTo=ymd(3, 31)).status_code == 200 and kyear() == [(ymd(1, 1), ymd(3, 31), ymd(12, 1))]
           and ky().status_code == 200 and kyear() == [(None, None, ymd(12, 1))], "작성 기간만 바꾸는 것은 200")
        ok(hit(rnd(evalStart=ymd(12, 2)), 409, LOCKED) and hit(rnd(selfTo=ymd(12, 11)), 200, {"ok": True})
           and q("SELECT self_to FROM ev_rounds WHERE year = ?", Y) == [(ymd(12, 11),)] and hit(rnd(), 200, {"ok": True}),
           "시작한_해의_평가_시작일은_못_바꾼다: /api/eval/admin/round 의 평가 시작일 변경도 409 · 다른 마감일은 바꿀 수 있음")

        # ---- 근태 ----
        stage(EMP, "GRADE", "A")
        ok(hit(att([{"userId": EMP, "late": 2, "leaveOver": 4.5}]), 200, {"ok": True, "done": [EMP], "locked": [], "missing": []}) and lv(EMP) == (2, 9, "A"),
           "연차를_넣으면_SAB를_지운다: A 를 넣어 둔 사람에게 연차 초과 4.5일 → 등급 그대로 (×2 정수 9 로 저장)")
        ok(hit(att([{"userId": EMP, "leaveOver": 5}]), 200, {"ok": True, "done": [EMP], "locked": [], "missing": []}) and lv(EMP) == (2, 10, None),
           "연차를_넣으면_SAB를_지운다: 5일 → 등급 지움 (지각은 안 보내서 그대로)")
        stage(EMP, "GRADE", "C")
        ok(att([{"userId": EMP, "leaveOver": 6}]).status_code == 200 and lv(EMP) == (2, 12, "C"), "C 는 연차가 많아도 그대로")
        BAD_ATT = {"error": "근태 숫자를 확인해 주세요. 지각은 0~999회 정수, 연차 초과는 0~365일(0.5일 단위)입니다."}
        ok(hit(att([{"userId": EMP, "leaveOver": 0.3}]), 400, BAD_ATT) and hit(att([{"userId": EMP, "leaveOver": 366}]), 400, BAD_ATT) and lv(EMP) == (2, 12, "C"),
           "연차를_넣으면_SAB를_지운다: 0.3 · 366 → 400")
        wrong = ({"leaveOver": -0.5}, {"leaveOver": True}, {"leaveOver": "1"}, {"leaveOver": None}, {"late": 1000}, {"late": 1.5}, {"late": 2.0}, {"late": True},
                 {"late": -1}, {"userId": str(FIELD)}, {"userId": True}, {"userId": 0}, {"userId": 2 ** 31})
        ok(all(att([{"userId": FIELD, "late": 7}, {"userId": EMP, **w}]).status_code == 400 for w in wrong) and lv(FIELD) == (0, 0, None),
           "연차를_넣으면_SAB를_지운다: 모양이 하나라도 틀리면 400 · 아무것도 저장 안 함 (앞 줄도)")
        ok(all(att(v).status_code == 400 for v in ("x", {"userId": FIELD}, None, [5], [{"userId": FIELD}] * 2001)) and att([{"userId": FIELD}] * 2000).json["done"] == [FIELD],
           "줄은 목록 2000줄까지 (같은 사람은 한 번만 셈)")
        r = att([{"userId": 999999, "late": 1}, {"userId": OFF, "late": 1}, {"userId": FIELD, "late": 3, "leaveOver": 365}])
        ok(hit(r, 200, {"ok": True, "done": [FIELD], "locked": [], "missing": [999999, OFF]}) and lv(FIELD) == (3, 730, None),
           "연차를_넣으면_SAB를_지운다: 없는 번호 · 평가 대상이 아닌 사람은 missing · 365일까지는 됨")
        att([{"userId": FIELD, "late": 0, "leaveOver": 0}])
        ok(hit(adm("att-done"), 200, {"ok": True}) and q("SELECT att_done_at FROM ev_rounds WHERE year = ?", Y) == [(z(12, 2),)], "근태 마침 → att_done_at")
        at(12, 3)
        ok(hit(adm("att-done"), 200, {"ok": True}) and q("SELECT att_done_at FROM ev_rounds WHERE year = ?", Y) == [(z(12, 2),)] and len(audits("EVAL_ATT_DONE")) == 1,
           "근태 마침을 또 눌러도 200 · 처음 시각 그대로 · 감사로그 한 번")

        # ---- 제외와 되돌림 ----
        why = lambda uid, reason="가상 휴직": one(uid, "exclude", reason=reason)  # noqa: E731
        stage(EMP, "GRADE", "A")
        ok(hit(why(EMP), 409, {"error": "평가자가 점수를 낸 뒤에는 제외할 수 없습니다."}) and out_of(EMP) == (None, None, None), "제외와_되돌림: 점수를 낸 뒤(GRADE) 제외 409")
        TOO_LONG = {"error": "제외 사유를 1~100자로 써 주세요."}
        ok(all(hit(why(DIVD, v), 400, TOO_LONG) for v in ("", "   ", "가" * 101, None, 5)) and hit(why(DIVD, "가상\x00"), 400, {"error": "제외 사유에 쓸 수 없는 글자가 있습니다."})
           and out_of(DIVD) == (None, None, None), "제외와_되돌림: 사유 없이 · 101자 · 글자가 아님 · 쓸 수 없는 글자 400")
        ok(hit(why(999999), 404, NOT_TARGET) and hit(why(OFF), 404, NOT_TARGET) and hit(one(DIVD, "exclude", year=Y + 1, reason="가상"), 404, NOT_TARGET), "제외: 대상이 아니면 404 (다른 해 포함)")
        ok(hit(why(DIVD, " 가상 휴직 "), 200, {"ok": True}) and out_of(DIVD) == (z(12, 3), "가상 휴직", HR) and hit(why(DIVD), 409, {"error": "이미 평가에서 제외했습니다."}),
           "제외와_되돌림: 제외 200 (사유는 앞뒤 공백을 뺌) · 또 제외 409")
        ok(hit(one(DIVD, "skip-self"), 409, {"error": "평가에서 제외된 사람입니다."}), "제외된 사람은 자기평가 없이 진행 409")
        ok(hit(one(DIVD, "include"), 200, {"ok": True}) and out_of(DIVD) == (None, None, None) and hit(one(DIVD, "include"), 409, {"error": "평가에서 제외한 사람이 아닙니다."}),
           "제외와_되돌림: 되돌림 200 · 또 되돌림 409")
        why(DIVD)
        for uid in (EMP, FIELD, EMPB, NOCONF):  # 본부 묶음의 나머지 넷이 모두 대표이사 확인 단계 · 정원에 맞음 → 묶음 상태 CEO
            stage(uid, "CEO", "D", "D")
        ok(hit(one(DIVD, "include"), 409, {"error": "이 묶음은 이미 다음 단계로 넘어가 되돌릴 수 없습니다. 먼저 묶음을 되돌려 주세요."}) and out_of(DIVD)[0] == z(12, 3),
           "제외와_되돌림: 묶음이 CEO 로 넘어간 뒤 되돌림 409")
        stage(EMPB, "CEO", None, "D")  # 등급 하나가 비면 FIX — 아직 되돌릴 수 있다
        ok(hit(one(DIVD, "include"), 200, {"ok": True}), "묶음이 FIX(등급을 맞춰야 함)면 되돌림 200")
        why(DIVD)
        for uid in (FIELD, NOCONF, EMPB):
            stage(uid, "SCORE")
        stage(EMP, "GRADE", "A")

        # ---- 근태 잠금 ----
        stage(EMPB, "CEO", "B")
        stage(LEAD, "CEO", "B")
        stage(LEADC, "HR", "B")
        r = att([{"userId": u, "late": 1} for u in (EMP, EMPB, LEAD, LEADC, DIVD)])
        ok(hit(r, 200, {"ok": True, "done": [EMP, LEAD], "locked": [EMPB, LEADC, DIVD], "missing": []}) and [lv(u)[0] for u in (EMP, EMPB, LEAD, LEADC, DIVD)] == [1, 0, 1, 0, 0],
           "근태는_넘어가면_잠긴다: GRADE 인 본부 팀원 됨 · CEO 면 locked · CEO 인 팀장 됨(팀장의 배정 단계 = CEO) · HR 이면 locked · 제외된 사람 locked")
        stage(EMPB, "SCORE")
        stage(LEAD, "SELF")
        stage(LEADC, "SELF")

        # ---- 평가자 바꾸기 ----
        BAD_RATER = {"error": "평가자는 본인이 아닌, 포털을 쓰는 재직 중인 직원으로 골라 주세요."}
        ok(all(hit(one(FIELD, "rater", raterId=v), 400, BAD_RATER) for v in (GONE, FIELD, EXEC, 999999, str(HEAD), True, None, 2 ** 31)) and rater(FIELD) == LEAD,
           "평가자_바꾸기: 비활성 · 본인 · 포털 사용 꺼짐(로그인 못 함) · 없는 사람 · 번호가 아님 400")
        ok(hit(one(EMP, "rater", raterId=HEAD), 409, {"error": "평가자가 이미 점수를 냈습니다."}) and rater(EMP) == LEAD, "평가자_바꾸기: GRADE 409")
        ok(hit(one(FIELD, "rater", raterId=HEAD), 200, {"ok": True}) and rater(FIELD) == HEAD and audits("EVAL_RATER_SET")[-1] == (FIELD, {"year": Y, "raterId": HEAD}),
           "평가자_바꾸기: 200 → rater_id · 감사로그 EVAL_RATER_SET (해 · 평가자 번호)")
        one(FIELD, "rater", raterId=LEAD)

        # ---- 본인 것은 다른 HR 이 ----
        SELF_HR = {"error": "본인 것은 다른 HR이 처리해요."}
        ok(all(hit(one(HR, w, reason="가상", raterId=LEAD), 403, SELF_HR) for w in ("exclude", "include", "skip-self", "rater"))
           and hit(one(HR, "exclude", year=None), 403, SELF_HR) and tgt(HR) == want[HR] and out_of(HR) == (None, None, None),
           "본인_것은_다른_HR이: HR 담당자(평가 대상)가 자기 줄을 제외 · 되돌림 · 자기평가 없이 · 평가자 바꾸기 → 403 (해 400 보다 먼저) · 줄은 그대로")
        n_att = len(audits("EVAL_ATTENDANCE"))
        r = att([{"userId": HR, "leaveOver": 1}, {"userId": FIELD, "late": 4}])
        ok(hit(r, 200, {"ok": True, "done": [FIELD], "locked": [HR], "missing": []}) and lv(HR) == (0, 0, None) and lv(FIELD)[0] == 4 and len(audits("EVAL_ATTENDANCE")) == n_att + 1,
           "본인_것은_다른_HR이: 근태 묶음에 HR 본인 줄이 있으면 그 줄만 locked (값 그대로) · 다른 줄은 저장")
        ok(hit(att([{"userId": HR, "late": 1}, {"userId": 999999, "late": 1}]), 200, {"ok": True, "done": [], "locked": [HR], "missing": [999999]})
           and len(audits("EVAL_ATTENDANCE")) == n_att + 1, "저장한 사람이 없으면 EVAL_ATTENDANCE 감사로그를 남기지 않음")
        ok(hit(one(HR, "exclude", who=HRL, reason="가상 휴직"), 200, {"ok": True}) and hit(one(HR, "include", who=HRL), 200, {"ok": True}) and out_of(HR) == (None, None, None),
           "다른 HR(HR 팀장)은 HR 담당자의 줄을 처리할 수 있음")
        att([{"userId": FIELD, "late": 0}])

        # ---- 공개한 해 ----
        x("UPDATE ev_rounds SET released_at = ? WHERE year = ?", z(12, 3), Y)
        ok(hit(rnd(), 409, RELEASED) and hit(att([{"userId": FIELD, "late": 1}]), 409, RELEASED) and hit(adm("att-done"), 409, RELEASED) and lv(FIELD)[0] == 0,
           "결과를 공개한 해는 회차 · 근태 · 근태 마침 409")
        # 보일 해 (R13): 지난해 회차가 안 끝났으면 지난해 — 공개 전 · 공개일 + 7일(한국) 안 · 처리 중인 이의 신청
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)
        at(1, 20, y=Y + 1)
        ok(evaluation.cur_year(db) == Y, "보일 해: 다음 해 1월에도 지난해 회차가 공개 전이면 지난해")
        x("UPDATE ev_rounds SET released_at = ? WHERE year = ?", f"{Y + 1}-01-10T15:00:00Z", Y)  # 한국 1월 11일 0시에 공개
        at(1, 18, 14, 59, y=Y + 1)
        r18 = evaluation.cur_year(db)
        at(1, 18, 15, 0, y=Y + 1)
        r19 = evaluation.cur_year(db)
        x("INSERT INTO ev_appeals (year, user_id, reason, at, status, before) VALUES (?, ?, '가상 이의', ?, 'OPEN', 'B')", Y, EMP, f"{Y + 1}-01-12T03:00:00Z")
        r_open = evaluation.cur_year(db)
        x("UPDATE ev_appeals SET status = 'DONE', answer = '가상 답', after = 'B' WHERE year = ? AND user_id = ?", Y, EMP)
        r_done = evaluation.cur_year(db)
        ok((r18, r19, r_open, r_done) == (Y, Y + 1, Y, Y + 1), f"보일 해: 공개일 + 7일(한국 1/18 23:59)까지 지난해 · 그 뒤 올해 · 처리 중인 이의 신청이 있으면 지난해 ({r18}, {r19}, {r_open}, {r_done})")
        x("DELETE FROM ev_appeals WHERE year = ?", Y)
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)
        at(12, 3)

        # ---- 감사로그 ----
        logs = q("SELECT action, after_data, before_data, reason FROM audit_logs WHERE action GLOB 'EVAL_*'")
        ok({a for a, *_ in logs} == {"EVAL_ROUND_SET", "EVAL_STARTED", "EVAL_ATT_DONE", "EVAL_ATTENDANCE", "EVAL_EXCLUDED", "EVAL_INCLUDED", "EVAL_SKIP_SELF", "EVAL_RATER_SET"}
           and all(set(json.loads(a)) <= {"year", "count", "raterId"} and not {"reason", "late", "leaveOver", "grade"} & set(json.loads(a)) for _, a, _, _ in logs)
           and not any("가상 휴직" in f"{a}{b}{rs}" for _, a, b, rs in logs) and audits("EVAL_ATTENDANCE")[0] == (Y, {"year": Y, "count": 1}),
           "감사로그에_숫자와_글이_없다: EVAL_* 여덟 가지의 after_data 는 해 · 사람 수 · 평가자 번호뿐 (사유 · 지각 · 연차 · 등급 없음, 사유 글자도 어디에도 없음)")
    finally:
        db.close()
        auth._now = real_now
        push.SYNC, push.SEND = real_sync, real_send
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for uid in others:
            x("UPDATE users SET in_evaluation = 1 WHERE id = ?", uid)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)
        x("DELETE FROM push_subscriptions WHERE user_id BETWEEN 101 AND 116")
        x("DELETE FROM sessions WHERE user_id BETWEEN 101 AND 116 AND kind = 'phone'")
        x("DELETE FROM ev_appeals WHERE year = ?", Y)
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)


def check_eval_view(hr) -> None:
    """인사평가 읽기 (2026-10-26, 평가 3단계 ①): GET /api/eval/state 는 보는 사람에 맞춰 깎은 줄만 준다 — 공개 전에는 평가자에게 자기가 낸 것만,
    본인에게는 자기평가만 (등급 · 점수 · 코멘트 열쇠가 아예 없다). 본부장은 자기가 배정하는 묶음 + 자기가 평가한 사람, 대표이사 · HR 은 전원.
    증빙 내려받기(본인 · 평가자 · 배정자 · 그 본부의 본부장 · 대표이사 · HR) · 처리할 일 숫자 · 포털 평가 전 해(FIRST_YEAR 전)는 안내만.
    check_eval_admin 이 시작해 둔 회차와 가상 조직(부서 91~97 · 사람 101~116)을 이어 쓴다 — 떠 둔 줄은 지울 수 없어 단계 · 등급 칸을 바로 넣어 상황을 만든다.
    대표이사 활성 · 다른 대표이사 · 시계 · 근태 마침 · 공개 · 맡김 · 포털 사용 · 올린 증빙은 되돌린다 (떠 둔 줄의 단계 · 등급 칸은 남는다)."""
    import evaluation
    from urllib.parse import quote

    from hr_eval.domain.models import Actor, Role
    print("인사평가 읽기 · 보는 수준 · 증빙 내려받기 · 할 일 숫자")
    HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO = range(101, 113)
    NOCONF, HEAD2, HRA = 115, 116, 1  # HRA = 평가 대상이 아닌 HR (처음 검사의 인사담당자)
    TARGETS = sorted((LEAD, EMP, FIELD, EMPB, NOCONF, DIVD, EMPC, LEADC, HRL, HR, EXEC))
    Y, real_now = evaluation.FIRST_YEAR, auth._now
    z = lambda m, d, hh=3, mi=0: f"{Y}-{m:02d}-{d:02d}T{hh:02d}:{mi:02d}:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def at(m, d, hh=3, mm=0):
        """시계를 UTC 로 (Y년 m월 d일 hh:mm) 에 맞춘다. 기본 03:00 = 한국 정오."""
        t = datetime(Y, m, d, hh, mm, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    get = lambda who, path: c(who).get(path, base_url=BASE)  # noqa: E731
    st = lambda who, query="": get(who, f"/api/eval/state{query}").json  # noqa: E731
    row = lambda s, uid: next((p for p in s["people"] if p["pid"] == uid), None)  # noqa: E731
    pids = lambda s: [p["pid"] for p in s["people"]]  # noqa: E731
    hit = lambda r, code, body: r.status_code == code and r.json == body  # noqa: E731

    def put(uid, **cols):
        x(f"UPDATE ev_targets SET {', '.join(f'{k} = ?' for k in cols)} WHERE year = ? AND user_id = ?", *cols.values(), Y, uid)

    def marks(uid, *rows):  # (실행목표 번호, 실적, 자기 등급, 지금 등급)
        for rid, note, self_grade, grade in rows:
            x("UPDATE ev_items SET note = ?, self_grade = ?, grade = ? WHERE year = ? AND user_id = ? AND rid = ?", note, self_grade, grade, Y, uid, rid)

    def leaves(v, path=()):
        """응답 전체를 끝까지 훑은 (열쇠 길, 값) 목록 — dict 의 모든 값 · 목록 · 목록 안의 목록까지 (목록 자리는 열쇠 길에 넣지 않는다)."""
        if isinstance(v, dict):
            return [leaf for k, w in v.items() for leaf in leaves(w, (*path, k))]
        if isinstance(v, list):
            return [leaf for w in v for leaf in leaves(w, path)]
        return [(path, v)]

    def leaks(v, values=(), nums=(), texts=()):
        """values 가 어느 칸의 값으로든 (열쇠 이름과 상관없이 — subMarks 의 값 등) · nums 가 숫자로 · texts 가 글자 안에 있는 곳."""
        return [(p, w) for p, w in leaves(v) if (isinstance(w, str) and (w in values or any(t in w for t in texts))) or (type(w) in (int, float) and w in nums)]

    FULL = {"pid", "name", "title", "team", "div", "poolKey", "poolName", "kpi", "stage", "out", "noSelf", "late", "leaveOver", "items", "ev", "grade", "proposed",
            "subScore", "subMarks", "comment", "headComment", "bonus", "bonusReason", "total", "cap", "proxy", "edited", "editSeen", "rater", "raterIsCeo", "assignerIsCeo", "can",
            "hidden", "appeal"}
    SELF_GONE = {"grade", "proposed", "subScore", "subMarks", "comment", "headComment", "bonus", "bonusReason", "total", "cap", "proxy", "edited", "editSeen", "rater", "raterIsCeo",
                 "assignerIsCeo"}
    NO_CAN = dict.fromkeys(("self", "upload", "score", "assign", "bonus", "head", "evidence", "behalf"), False)
    NO_POOL_CAN = dict.fromkeys(("close", "confirm", "back", "finalize", "undo"), False)
    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    att_done = q("SELECT att_done_at FROM ev_rounds WHERE year = ?", Y)[0][0]
    fdir = Path(os.environ["HR_DB_PATH"]).parent / "eval_files"
    db = connect(os.environ["HR_DB_PATH"])
    fids: list[int] = []
    try:
        for uid in other_ceos:  # 이 가상 조직의 대표이사는 check_eval_admin 이 만든 한 명뿐이게 (그 검사가 끝에 꺼 둔 것을 다시 켠다)
            x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", CEO)
        ok(q("SELECT user_id, pool, rater_id FROM ev_targets WHERE year = ? ORDER BY user_id", Y)
           == [(LEAD, "LEADERS", HEAD), (EMP, "DIV:91", LEAD), (FIELD, "DIV:91", LEAD), (EMPB, "DIV:91", HEAD), (DIVD, "DIV:91", HEAD), (LEADC, "LEADERS", CEO),
               (EMPC, "DIV:94", LEADC), (HRL, "LEADERS", CEO), (HR, "DIRECT", HRL), (EXEC, "DIRECT", CEO), (NOCONF, "DIV:91", HEAD)],
           "(이어 쓰는 회차: 대상 11명 · 묶음 · 평가자가 check_eval_admin 이 떠 둔 그대로)")
        # 단계 · 등급 칸을 깨끗이 (KPI 없는 사람은 SCORE, 나머지 SELF) — 앞 검사가 남긴 것을 지운다
        x("UPDATE ev_targets SET stage = CASE WHEN kpi_version IS NULL THEN 'SCORE' ELSE 'SELF' END, proposed = NULL, grade = NULL, sub_score_x10 = NULL,"
          " sub_marks = NULL, scored_at = NULL, scored_by = NULL, comment = '', head_comment = '', bonus_x2 = 0, bonus_reason = '', proxy_by = NULL, edit_seen = NULL,"
          " late = 0, leave_x2 = 0, out_at = NULL, out_reason = NULL, out_by = NULL, no_self_at = NULL, no_self_by = NULL WHERE year = ?", Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        # 본부 팀원(EMP): 팀장이 A · B · B (40×100% + 30×90% + 30×90% = 94.0점) · 등급 의견 B 로 냈고, 본부장이 B · C · C + 가점 1.5 (85.5점) · C 로 고쳐 마감했다
        SUB = {"1": "A", "2": "B", "4": "B"}
        put(EMP, stage="CEO", proposed="B", grade="C", sub_score_x10=940, sub_marks=json.dumps(SUB), scored_at=z(12, 4), scored_by=LEAD,
            comment="가상 팀장 코멘트", head_comment="가상 본부장 코멘트", bonus_x2=3, bonus_reason="가상 가점 사유")
        marks(EMP, (1, "가상 실적 1", "A", "B"), (2, "가상 실적 2", "A", "C"), (4, "가상 실적 4", "B", "C"))
        # 팀장(LEAD): 본부장이 A · A · A (100.0점) · A 로 냈고, 대표이사가 확인하며 A · A · B (97.0점) · B 로 고치는 중이다
        put(LEAD, stage="CEO", proposed="A", grade="B", sub_score_x10=1000, sub_marks=json.dumps({"1": "A", "2": "A", "3": "A"}), scored_at=z(12, 4), scored_by=HEAD,
            comment="가상 본부장 평가")
        marks(LEAD, (1, "가상 팀장 실적", "A", "A"), (2, "가상 팀장 실적", "A", "A"), (3, "가상 팀장 실적", "A", "B"))
        put(EMPB, stage="SCORE", no_self_at=z(12, 2, 15, 30), no_self_by=HR)  # 한국 12/3 0:30 에 자기평가 없이 넘김
        put(DIVD, out_at=z(12, 3), out_reason="가상 휴직", out_by=HR)
        put(FIELD, late=3, leave_x2=9)
        put(EXEC, proxy_by=HR)
        at(12, 5)  # 한국 12월 5일 정오 — 자기평가 · 증빙 마감(12/10) 전

        ok(app.test_client().get("/api/eval/state", base_url=BASE).status_code == 401 and app.test_client().get("/api/eval/files/1", base_url=BASE).status_code == 401,
           "로그인 없이는 401 (상태 · 증빙)")

        # ---- 전원 (HR · 대표이사) ----
        item = lambda rid, w: {"rid": rid, "team": "가상 팀목표", "personal": "가상 개인목표", "action": f"가상 업무 {rid}", "formula": "가상 측정식", "weight": w,  # noqa: E731
                               "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c", "D": "가상 d"}
        ev = lambda *g: [{"rid": rid, "note": f"가상 실적 {rid}", "selfGrade": s, "grade": gr, "files": []} for rid, s, gr in zip((1, 2, 4), "AAB", g)]  # noqa: E731
        want_emp = {"pid": EMP, "name": "가상E팀원", "title": "가상직급", "team": "가상E팀A", "div": "가상E본부", "poolKey": "DIV:91", "poolName": "가상E본부 팀원",
                    "kpi": True, "stage": "CEO", "out": None, "noSelf": "", "late": 0, "leaveOver": 0, "items": [item(1, 40), item(2, 30), item(4, 30)], "ev": ev("B", "C", "C"),
                    "grade": "C", "proposed": "B", "subScore": 94.0, "subMarks": SUB, "comment": "가상 팀장 코멘트", "headComment": "가상 본부장 코멘트",
                    "bonus": 1.5, "bonusReason": "가상 가점 사유", "total": 85.5, "cap": "B", "proxy": "", "edited": "총점 94.0 → 85.5 · 등급 B → C", "editSeen": False,
                    "rater": {"id": LEAD, "name": "가상E팀장", "title": "가상직급", "canLogin": True}, "raterIsCeo": False, "assignerIsCeo": False,
                    "can": {**NO_CAN, "evidence": True},
                    "hidden": False, "appeal": None}
        hra, ceo = st(HRA), st(CEO)
        ok(hra["year"] == Y and hra["offline"] is False and hra["round"] == evaluation._round(db, Y) and hra["deleg"] is False and hra["ceoId"] == CEO and hra["me"] is None,
           "상태: 해 · 회차(_round) · 맡김 · 대표이사 번호 · 평가 대상이 아닌 사람의 me 는 null")
        ok(all(sorted(pids(s)) == TARGETS and all(set(p) == FULL and set(p["can"]) == set(NO_CAN) and p["hidden"] is False for p in s["people"]) and s["me"] is None
               for s in (hra, ceo)), "대표이사와_HR은_전부: 대표이사 · HR → 11명 모두 full (줄마다 칸이 다 있음 — 화면 evPeople 칸 이름)")
        ok(row(hra, EMP) == want_emp and row(ceo, EMP) == want_emp, "대표이사와_HR은_전부: 고친 등급 C · 지금 총점 85.5 · 가점 · 윗사람 코멘트 · 고친 내용 글자까지 다 보임")
        f, b, d, x_ = row(hra, FIELD), row(hra, EMPB), row(hra, DIVD), row(hra, EXEC)
        ok(f["kpi"] is False and f["items"] == [] and f["ev"] == [] and f["total"] is None and f["cap"] == "S" and (f["late"], f["leaveOver"]) == (3, 4.5),
           "KPI 없는 사람: kpi 거짓 · 실행목표 없음 · 총점 null · 줄 수 있는 등급은 연차 규칙만 (4.5일 → S) · 지각 · 연차 초과(소수)")
        ok(b["noSelf"] == f"{Y}-12-03" and d["out"] == {"at": z(12, 3), "why": "가상 휴직"} and x_["proxy"] == "가상EHR담당 가상직급" and x_["raterIsCeo"] is True
           and x_["rater"] == {"id": CEO, "name": "가상E대표", "title": "가상직급", "canLogin": True} and x_["div"] == "대표이사 직속부서" and x_["team"] == "본부 직속",
           "자기평가 없이 넘긴 날(한국 날짜) · 제외(때 · 사유) · 대리 입력 '이름 직급' · 평가자가 대표이사 · 이름 칸")
        ok([row(hra, u)["poolName"] for u in (LEAD, HR, EMPC)] == ["전사 팀장", "직속부서 팀원", "가상E본부2 팀원"], "묶음 이름: 전사 팀장 · 직속부서 팀원 · <본부> 팀원")
        ok([row(hra, u)["assignerIsCeo"] for u in (EMP, EMPC, LEAD, HR)] == [False, True, True, True] and row(st(LEADC), EMPC)["assignerIsCeo"] is True,
           "assignerIsCeo: 본부장이 있는 본부 팀원 거짓 · 본부장 없는 본부 팀원 · 팀장 · 직속은 참 (평가자에게도 — 화면의 '본부장' · '대표이사' 글자)")
        x("UPDATE users SET portal_enabled = 0 WHERE id = ?", LEADC)
        ok(row(st(HRA), EMPC)["rater"] == {"id": LEADC, "name": "가상E팀장C", "title": "가상직급", "canLogin": False}, "평가자가 포털을 못 쓰면 rater.canLogin 거짓 (HR 명단 경고)")
        x("UPDATE users SET portal_enabled = 1 WHERE id = ?", LEADC)
        P91 = {"key": "DIV:91", "name": "가상E본부 팀원", "size": 4, "state": "WAIT", "counts": {"S": 0, "A": 0, "B": 0, "C": 1, "D": 0}, "errors": [], "warnings": [],
               "quota": "S 최대 1명 · A·B 합계 1명 (S만큼 줄어듦) · C·D 나머지", "log": {"closed": None, "ceo": None, "back": None, "undo": None}, "hrSeen": False,
               "can": NO_POOL_CAN}
        ok([p["key"] for p in hra["pools"]] == ["DIV:91", "DIV:94", "LEADERS", "DIRECT"] and hra["pools"][0] == P91 and all(p["can"] == NO_POOL_CAN for p in hra["pools"])
           and [p["size"] for p in hra["pools"]] == [4, 1, 3, 2], "대표이사와_HR은_전부: pools 넷 (제외된 사람 빼고 셈 · 배정 단계에 온 사람의 등급만 셈 · 정원 글자) · 맡김 전 HR 은 마감 못 함")
        ok([p["can"]["close"] for p in ceo["pools"]] == [False, True, False, False], "대표이사: 본부장 없는 본부 묶음(DIV:94)만 마감할 수 있음 (본부장이 있는 본부는 본부장이)")
        ok(sorted(["DIRECT", "DIV:100", "LEADERS", "DIV:91"], key=evaluation._pool_rank) == ["DIV:91", "DIV:100", "LEADERS", "DIRECT"],
           "묶음 차례: 본부 팀원은 본부 번호 차례(91 → 100) → 전사 팀장 → 직속부서 팀원")

        # ---- 공개 전 평가자 (팀장) ----
        lead = st(LEAD)
        want_mine = {**want_emp, "ev": ev("A", "B", "B"), "grade": "B", "total": 94.0, "cap": "A", "bonus": 0, "bonusReason": "", "headComment": "", "proxy": "",
                     "edited": True, "hidden": True}
        ok(row(lead, EMP) == want_mine, "공개_전_평가자는_낸_것만: 팀장 → grade B · 항목 등급은 낸 그대로(A·B·B) · 총점 = 낸 94.0 · edited 참 · hidden 참 · 가점 0 · 윗사람 코멘트 ''")
        ok(pids(lead) == [EMP, FIELD] and lead["pools"] == [] and lead["me"]["pid"] == LEAD, "팀장: people 은 자기가 평가하는 두 사람 · pools 없음 · me 는 자기 줄")
        got = leaves(lead)
        ok(all(g in got for g in ((("people", "grade"), "B"), (("people", "ev", "selfGrade"), "B"), (("people", "subMarks", "4"), "B"), (("people", "subScore"), 94.0),
                                  (("me", "ev", "note"), "가상 팀장 실적"), (("people", "items", "weight"), 30), (("round", "bands", "C"), 70))),
           "(훑기가 me · people · ev · items · subMarks 의 값 · round 안까지 닿음)")
        NEW = (85.5, 855, 1.5)
        TEXTS = ("85.5", "855", "→", "가상 본부장 코멘트", "가상 가점 사유")
        bad = leaks(lead, ("C",), NEW, TEXTS)
        ok(not bad and "85.5" not in get(LEAD, "/api/eval/state").get_data(as_text=True),
           f"새는지_모든_칸에서: 팀장 응답 전체(어느 칸의 값이든 — subMarks 의 값까지)에 고친 등급 C · 고친 총점(85.5 · ×10 855 · 글자) · 가점 1.5 · 윗사람 코멘트 · "
           f"고친 내용 글자가 없음 {bad}")
        ok(leaks(hra, ("C",), NEW, TEXTS), "(같은 훑기가 HR 응답에서는 C · 85.5 를 찾아냄 — 훑기가 제대로 잡는다)")

        # ---- 본인 ----
        emp = st(EMP)
        me = emp["me"]
        ok(me == {**{k: v for k, v in want_emp.items() if k not in SELF_GONE}, "ev": [{k: v for k, v in e.items() if k != "grade"} for e in ev("B", "C", "C")]}
           and not SELF_GONE & set(me) and all("grade" not in e and {"note", "selfGrade"} <= set(e) for e in me["ev"]) and emp["people"] == [] and emp["pools"] == [],
           "본인은_자기평가만: 본인 me 줄에 grade · proposed · subScore · subMarks · comment · headComment · bonus · total · cap · edited · rater 열쇠가 없고 "
           "ev[] 에 grade 열쇠가 없음 · note · selfGrade · late · leaveOver 는 있음")
        bad = leaks(emp, ("C",), (94.0, 940, *NEW), ("94", "가상 팀장 코멘트", *TEXTS))
        ok(not bad, f"본인 응답 어디에도 고친 등급 C · 낸 총점 94 · 고친 총점 · 평가자 코멘트가 없음 {bad}")

        # ---- 본부장 ----
        head = st(HEAD)
        ok(pids(head) == [LEAD, EMP, FIELD, EMPB, NOCONF, DIVD] and head["me"] is None,
           "본부장은_자기_본부를_배정자로: 자기 본부 묶음 다섯(제외된 사람 포함) + 자기가 평가한 팀장 — 본부 → 팀 → 팀장 먼저 → 이름 차례 · 다른 본부 · 직속 · 다른 팀장 없음")
        ok(row(head, EMP) == want_emp, "본부장은_자기_본부를_배정자로: 본부 팀원은 full (고친 C · 85.5 가 보임)")
        lr = row(head, LEAD)
        ok(lr["hidden"] is True and lr["grade"] == "A" and [e["grade"] for e in lr["ev"]] == ["A", "A", "A"] and lr["total"] == 100.0 and lr["edited"] == ""
           and not leaks(lr, ("B",), (97.0, 970), ("97",)), "본부장은_자기_본부를_배정자로: 자기가 평가한 팀장은 mine (대표이사가 고치는 중인 B · 97점은 없음, 아직 확인 전이라 edited '')")
        bad = leaks(head, (), (97.0, 970), ("97",))
        ok(not bad, f"본부장 응답 전체에도 대표이사가 고치는 중인 팀장 총점(97.0 · ×10 970 · 글자)이 없음 {bad}")
        ok(head["pools"] == [{**P91, "can": {**NO_POOL_CAN, "close": True}}], "본부장은_자기_본부를_배정자로: pools 에 자기 본부 묶음만 · 마감 권한")

        # ---- 팀장 · 직원 ----
        leadc, empb = st(LEADC), st(EMPB)
        ok(pids(leadc) == [EMPC] and leadc["pools"] == [] and EMP not in pids(leadc), "팀장은_자기_평가_대상만: 다른 팀장 응답 people 에 그 팀원(EMP)이 없음 · 자기 팀원만")
        ok(empb["people"] == [] and empb["pools"] == [] and empb["me"]["pid"] == EMPB, "팀장은_자기_평가_대상만: 직원은 people 빈 목록 · pools 없음")

        # ---- 판단 칸 (can) ----
        ok(st(EMPC)["me"]["can"] == {**NO_CAN, "self": True, "upload": True, "evidence": True}, "판단 칸: 자기평가 단계 본인 → self · upload · evidence")
        ok(row(lead, FIELD)["can"] == {**NO_CAN, "score": True, "evidence": True}, "판단 칸: 점수 단계 팀원 → 평가자는 score (본부 팀원 가점은 배정자 몫)")
        ok(row(ceo, LEAD)["can"] == {**NO_CAN, "assign": True, "bonus": True, "evidence": True} and row(ceo, EXEC)["can"] == NO_CAN,
           "판단 칸: 대표이사 확인 단계 팀장 → 대표이사 assign · bonus (팀장 묶음은 윗사람 코멘트 없음) · 자기평가 단계 직속 직원은 아직 못 고침 · 증빙도 자기평가를 낸 뒤에")
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 5))
        put(LEADC, stage="SCORE")
        put(HR, stage="SCORE")
        hra, hr_me = st(HRA), st(HR)
        ok(hra["deleg"] is True and row(hra, LEADC)["can"] == {**NO_CAN, "score": True, "bonus": True, "evidence": True, "behalf": True}
           and row(hra, LEAD)["can"] == {**NO_CAN, "assign": True, "bonus": True, "evidence": True, "behalf": True},
           "판단 칸: 맡김 → HR 이 대표이사가 평가자인 팀장 채점 · 팀장 확인 단계 고치기를 대신 (behalf 참)")
        ok(row(st(HRL), HR)["can"] == {**NO_CAN, "score": True, "evidence": True} and hr_me["me"]["can"]["score"] is False,
           "판단 칸: HR 팀장이 HR 담당을 채점하는 것은 평가자 본인 일 (behalf 거짓) · HR 담당은 자기 줄을 못 고침")
        ok([p["can"]["close"] for p in hra["pools"]] == [False, True, False, False] and [p["can"]["close"] for p in hr_me["pools"]] == [False, True, False, False],
           "맡김 → HR 도 본부장 없는 본부 묶음을 마감할 수 있음 (그 묶음 사람이 아닐 때)")
        cx = evaluation._ctx(db, Y)
        ok(evaluation._pool_can(cx, Actor(EMPC, None, True), "DIV:94")["close"] is False and evaluation._pool_can(cx, Actor(HRA, None, True), "DIV:94")["close"] is True
           and evaluation._pool_can(cx, Actor(HRA, None, True), "LEADERS")["close"] is False, "_pool_can: 그 묶음에 든 HR 은 맡겨도 마감 못 함 · 본부 팀원 묶음만 마감")
        ok(st(HRA)["todo"] == {"me": 0, "admin": 2, "e3": 0} and st(CEO)["todo"] == {"me": 2, "admin": 0, "e3": 0},
           "할_일_숫자: 맡김 → HR admin 에 대표이사가 평가자인 점수 단계 사람 1 + 대표이사가 배정할 묶음 1 (전사 팀장 — 대표이사 확인 단계 팀장이 있음) · 대표이사 me 에도 2")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        put(LEADC, stage="SELF")
        put(HR, stage="SELF")

        # ---- 증빙 ----
        PDF, HWP = b"%PDF-1.4\n", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
        for rid, name, kind, data in ((1, "가상 증빙 1.pdf", "pdf", PDF), (4, "가상 한글.hwp", "hwp", HWP)):
            x("INSERT INTO ev_files (year, user_id, rid, name, kind, size, sha256, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", Y, EMP, rid, name, kind, len(data), "0" * 64, z(12, 5))
            fids.append(q("SELECT id FROM ev_files WHERE year = ? AND user_id = ? AND name = ?", Y, EMP, name)[0][0])
            fdir.mkdir(exist_ok=True)
            (fdir / str(fids[-1])).write_bytes(data)
        pdf, hwp = fids
        url = f"/api/eval/files/{pdf}"
        for u in (EMP, LEAD, HEAD, CEO, HRA, FIELD, EMPB, LEADC, EMPC):
            c(u)  # 로그인(감사로그 LOGIN)을 먼저 해 둔다
        n_logs = q("SELECT COUNT(*) FROM audit_logs")[0][0]
        rs = [get(u, url) for u in (EMP, LEAD, HEAD, CEO, HRA)]
        cd = "attachment; filename*=UTF-8''" + quote("가상 증빙 1.pdf", safe="")
        ok(all(r.status_code == 200 and r.data == PDF and r.headers["Content-Type"] == "application/pdf" and r.headers["Content-Disposition"] == cd
               and r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store" for r in rs),
           "증빙은_볼_사람만: 본인 · 평가자(팀장) · 본부장 · 대표이사 · HR 200 · 파일 그대로 · Content-Type · 한글 이름(filename*) · nosniff · no-store")
        r = get(EMP, f"/api/eval/files/{hwp}")
        ok(r.status_code == 200 and r.data == HWP and r.headers["Content-Type"] == "application/octet-stream", "한글(hwp) 증빙은 application/octet-stream")
        NO = {"error": "이 증빙을 볼 권한이 없습니다."}
        ok(all(hit(get(u, url), 403, NO) for u in (FIELD, EMPB, LEADC, EMPC)), "증빙은_볼_사람만: 같은 팀 동료 · 같은 본부 다른 팀 직원 · 다른 팀장 · 다른 본부 직원 403")
        ok(get(EMP, "/api/eval/files/999999").status_code == 404 and get(EMP, f"/api/eval/files/{2 ** 31}").status_code == 404, "증빙은_볼_사람만: 없는 번호 404")
        ok(q("SELECT COUNT(*) FROM audit_logs")[0][0] == n_logs, "증빙 내려받기는 감사로그를 남기지 않음")
        ok(row(st(LEAD), EMP)["ev"][0]["files"] == [{"id": pdf, "name": "가상 증빙 1.pdf", "size": 9}] and st(EMP)["me"]["ev"][2]["files"] == [{"id": hwp, "name": "가상 한글.hwp", "size": 8}]
           and row(st(HRA), EMP)["ev"][1]["files"] == [], "상태 응답의 ev[].files — 실행목표마다 (번호 · 이름 · 크기)")
        (fdir / str(pdf)).unlink()
        ok(hit(get(EMP, url), 404, {"error": "증빙 파일이 서버에 없습니다."}), "증빙은_볼_사람만: 폴더에서 파일이 사라졌으면 404")
        put(LEAD, rater_id=CEO)  # 팀장의 평가자를 잠깐 대표이사로 — 본부장은 이 팀장의 평가자도 배정자(전사 팀장 묶음 = 대표이사)도 아니다
        x("INSERT INTO user_roles (user_id, role) VALUES (?, 'DIVISION_HEAD')", HEAD2)
        x("UPDATE users SET led_department_id = 94 WHERE id = ?", HEAD2)  # 다른 본부(94)의 본부장
        x("INSERT INTO ev_files (year, user_id, rid, name, kind, size, sha256, at) VALUES (?, ?, 1, '가상 팀장 증빙.pdf', 'pdf', ?, ?, ?)", Y, LEAD, len(PDF), "0" * 64, z(12, 5))
        fids.append(q("SELECT id FROM ev_files WHERE year = ? AND user_id = ?", Y, LEAD)[0][0])
        (fdir / str(fids[-1])).write_bytes(PDF)
        lurl = f"/api/eval/files/{fids[-1]}"
        r = get(HEAD, lurl)
        ok(r.status_code == 200 and r.data == PDF and hit(get(HEAD2, lurl), 403, NO),
           "증빙은_볼_사람만: 평가자도 배정자도 아닌 그 본부의 본부장 200 · 다른 본부의 본부장 403")
        put(LEAD, rater_id=HEAD)
        x("DELETE FROM user_roles WHERE user_id = ? AND role = 'DIVISION_HEAD'", HEAD2)
        x("UPDATE users SET led_department_id = NULL WHERE id = ?", HEAD2)

        # ---- 자기평가를 내기 전 초안은 본인 · HR 만 (EMPC — 자기평가 단계 · 평가자 팀장C · 배정자 대표이사 · 본부 94) ----
        marks(EMPC, (1, "가상 초안 실적", "A", None))
        x("INSERT INTO ev_files (year, user_id, rid, name, kind, size, sha256, at) VALUES (?, ?, 1, '가상 초안 증빙.pdf', 'pdf', ?, ?, ?)", Y, EMPC, len(PDF), "0" * 64, z(12, 5))
        fids.append(q("SELECT id FROM ev_files WHERE year = ? AND user_id = ?", Y, EMPC)[0][0])
        (fdir / str(fids[-1])).write_bytes(PDF)
        durl = f"/api/eval/files/{fids[-1]}"
        x("INSERT INTO user_roles (user_id, role) VALUES (?, 'DIVISION_HEAD')", HEAD2)
        x("UPDATE users SET led_department_id = 94 WHERE id = ?", HEAD2)  # 그 본부(94)의 본부장
        hr_row, own, viewers = row(st(HRA), EMPC), st(EMPC)["me"], {who: st(who) for who in (LEADC, CEO)}
        ok(all((r_ := row(s, EMPC)) is not None and [set(e) for e in r_["ev"]] == [{"rid", "grade"}] * 3 and r_["items"] == hr_row["items"]
               and not leaks(s, (), (), ("가상 초안",)) for s in viewers.values()),
           "자기평가_전_초안은_본인과_HR만: SELF 인 사람을 평가자(팀장C) · 배정자(대표이사)가 보면 ev[] 는 번호 · 평가자 등급뿐 (실적 · 자기 등급 · 증빙 열쇠 없음) · "
           "KPI 실행목표는 그대로 · 응답 어디에도 초안 글자(실적 · 증빙 이름) 없음")
        ok(hr_row["ev"][0]["note"] == own["ev"][0]["note"] == "가상 초안 실적" and hr_row["ev"][0]["selfGrade"] == own["ev"][0]["selfGrade"] == "A"
           and hr_row["ev"][0]["files"][0]["name"] == own["ev"][0]["files"][0]["name"] == "가상 초안 증빙.pdf", "HR · 본인은 초안(실적 · 자기 등급 · 증빙)을 봄")
        ok(get(EMPC, durl).status_code == 200 and get(HRA, durl).status_code == 200 and all(hit(get(u, durl), 403, NO) for u in (LEADC, CEO, HEAD2)),
           "자기평가_전_초안은_본인과_HR만: 증빙 내려받기 — 본인 · HR 200, 평가자 · 대표이사 · 그 본부의 본부장 403")
        put(EMPC, stage="SCORE")
        r_ = row(st(LEADC), EMPC)
        ok(r_["ev"][0]["note"] == "가상 초안 실적" and r_["ev"][0]["files"][0]["name"] == "가상 초안 증빙.pdf" and all(get(u, durl).status_code == 200 for u in (LEADC, CEO, HEAD2)),
           "자기평가를 낸 뒤(SCORE)에는 평가자 · 대표이사 · 그 본부의 본부장도 실적 · 증빙을 봄")
        put(EMPC, stage="SELF")
        marks(EMPC, (1, "", None, None))
        x("DELETE FROM user_roles WHERE user_id = ? AND role = 'DIVISION_HEAD'", HEAD2)
        x("UPDATE users SET led_department_id = NULL WHERE id = ?", HEAD2)

        # ---- 할 일 숫자 ----
        todo = lambda who: st(who)["todo"]  # noqa: E731
        at(12, 10, 14, 59)  # 한국 12/10 23:59 — 자기평가 마감일 밤
        a = todo(EMPC), st(EMPC)["me"]["can"]["self"]
        at(12, 10, 15, 0)  # 한국 12/11 0시
        b = todo(EMPC), st(EMPC)["me"]["can"]["self"]
        ok(a == ({"me": 1, "admin": 0, "e3": 0}, True) and b == ({"me": 0, "admin": 0, "e3": 0}, False), f"할_일_숫자: 본인 자기평가 1 (마감일 한국 23:59 까지) → 다음 날 0 · can.self 도 거짓 {a} {b}")
        at(12, 5)
        ok(todo(LEAD) == {"me": 2, "admin": 0, "e3": 0}, "할_일_숫자: 팀장 — 점수 낼 사람 1(현장직) + 윗사람이 고쳤는데 아직 확인 안 한 사람 1")
        put(EMP, edit_seen="총점 94.0 → 85.5 · 등급 B → C")
        ok(todo(LEAD) == {"me": 1, "admin": 0, "e3": 0} and row(st(LEAD), EMP)["editSeen"] is True, "확인한 고친 내용은 안 셈 · editSeen 참")
        ok(todo(HEAD) == {"me": 2, "admin": 0, "e3": 0}, "할_일_숫자: 본부장 — 점수 낼 사람 2 (제외된 사람은 안 셈)")
        put(EMPB, stage="GRADE", proposed="C")
        put(NOCONF, stage="GRADE", proposed="C")
        ok(todo(HEAD) == {"me": 1, "admin": 0, "e3": 0}, "할_일_숫자: 본부장 — 둘이 배정 단계로 오면 배정할 묶음 1")
        put(EMPB, stage="CEO", proposed="B", grade="C", sub_score_x10=900, scored_by=HEAD)  # 팀장 없는 팀 팀원: 본부장이 평가자이자 배정자 — 낸 B · 90점을 스스로 C · 0점으로
        head = st(HEAD)
        ok(row(head, EMPB)["edited"] == "" and row(st(HRA), EMPB)["edited"] == "" and todo(HEAD) == {"me": 1, "admin": 0, "e3": 0}
           and row(head, EMP)["edited"] == "총점 94.0 → 85.5 · 등급 B → C",
           "평가자가 배정자 본인이면 자기가 고친 것은 edited '' · 할 일(고친 것 확인)에 안 셈 — 팀장이 평가한 사람은 그대로 고친 내용 글자")
        ok(todo(HRA) == {"me": 0, "admin": 0, "e3": 0}, "HR: 근태를 마쳤고 맡김 · 마감 알림이 없으면 0")
        x("UPDATE ev_rounds SET att_done_at = NULL WHERE year = ?", Y)
        ok(todo(HRA) == {"me": 0, "admin": 1, "e3": 0} and todo(HEAD)["admin"] == 0, "할_일_숫자: HR 근태 안 마침 1 (HR 이 아니면 admin 0)")
        x("UPDATE ev_rounds SET att_done_at = ? WHERE year = ?", att_done, Y)
        actor = Actor(LEAD, Role.TEAM_LEADER, False, led_team_id=92)
        ok(evaluation.eval_todo(db, actor) == todo(LEAD) == {"me": 1, "admin": 0, "e3": 0}, "eval_todo(conn, actor) 는 상태 응답의 todo 와 같은 셈 (휴대폰 숫자용)")
        x("UPDATE ev_rounds SET released_at = ? WHERE year = ?", z(12, 5), Y)
        ok(todo(LEAD) == todo(HRA) == evaluation.eval_todo(db, actor) == {"me": 0, "admin": 0, "e3": 0}, "할_일_숫자: 공개 뒤에는 0")
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)

        # ---- 해 ----
        off = st(LEAD, f"?year={Y - 1}")
        ok(off["year"] == Y - 1 and off["offline"] is True and off["round"] is None and off["me"] is None and off["people"] == [] and off["pools"] == []
           and off["todo"] == {"me": 0, "admin": 0, "e3": 0}, f"{Y - 1}년은_안내만: 포털 평가 전 해 → offline 참 · people 빈 목록")
        ok(st(LEAD)["year"] == Y and st(LEAD, f"?year={Y}")["year"] == Y, "해를 주지 않으면 보일 해(cur_year)")
        BAD = {"error": "해가 올바르지 않습니다."}
        ok(all(hit(get(LEAD, f"/api/eval/state?year={v}"), 400, BAD) for v in ("abc", "2025", "2101", "", "2027.0", "%EF%BC%92%EF%BC%90%EF%BC%92%EF%BC%97", "9" * 5000)),
           "해가 정수(2026~2100)가 아니면 400 (글자 · 범위 밖 · 빈 값 · 소수 · 전각 숫자 · 아주 긴 숫자)")
    finally:
        db.close()
        auth._now = real_now
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        x("UPDATE ev_rounds SET att_done_at = ?, released_at = NULL WHERE year = ?", att_done, Y)
        x("UPDATE users SET portal_enabled = 1 WHERE id = ?", LEADC)
        x("DELETE FROM user_roles WHERE user_id = ? AND role = 'DIVISION_HEAD'", HEAD2)
        x("UPDATE users SET led_department_id = NULL WHERE id = ?", HEAD2)
        x("UPDATE ev_targets SET rater_id = ? WHERE year = ? AND user_id = ?", HEAD, Y, LEAD)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)
        for fid in fids:
            x("DELETE FROM ev_files WHERE id = ?", fid)
            (fdir / str(fid)).unlink(missing_ok=True)


def check_eval_self(hr) -> None:
    """인사평가 자기평가 · 증빙 올리기 (2026-10-26, 평가 3단계 ①): 본인이 실행목표마다 실적(500자)과 자기 등급을 임시 저장하고(자기평가 마감일 한국 밤 12시까지 — 사람 번호는 세션에서만 써서
    남의 것을 쓸 길이 없다), 다 채우면 제출해 평가자(대표이사가 평가자이고 맡겼으면 본인 뺀 HR 전원)에게 넘긴다. 증빙은 실행목표마다 5개 · 10MB까지, 확장자와 첫 바이트가 맞는 형식만
    (이름의 경로는 떼고 저장 이름은 번호 하나), 증빙 마감일(한국 밤 12시) 전 · 평가자가 점수를 내기 전에만 올리고 지운다. nginx 는 증빙 올리기만 본문 11MB.
    check_eval_view 가 이어 둔 회차와 가상 조직(부서 91~97 · 사람 101~116)을 쓴다 — 시작에 단계 · 실적을 깨끗이 하고, 끝에 시계 · 알림 · 대표이사 · 맡김 · 마감일 · 올린 증빙을 되돌린다."""
    import io
    from unittest import mock

    import evaluation
    import push
    from hr_eval.domain.eval_rules import FILE_MAX
    print("인사평가 자기평가 · 증빙 올리기 · 지우기")
    HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO = range(101, 113)
    NOCONF, HRA = 115, 1  # HRA = 평가 대상이 아닌 HR (처음 검사의 인사담당자)
    Y, real_now, real_send, real_sync = evaluation.FIRST_YEAR, auth._now, push.SEND, push.SYNC
    ymd = lambda m, d, y=Y: f"{y}-{m:02d}-{d:02d}"  # noqa: E731
    z = lambda m, d, hh=3, mi=0: f"{Y}-{m:02d}-{d:02d}T{hh:02d}:{mi:02d}:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def at(m, d, hh=3, mm=0):
        """시계를 UTC 로 (Y년 m월 d일 hh:mm) 에 맞춘다. 기본 03:00 = 한국 정오 (12월 1일은 수요일 — 2일은 근무시간이라 알림이 간다)."""
        t = datetime(Y, m, d, hh, mm, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    get = lambda who, path: c(who).get(path, base_url=BASE)  # noqa: E731
    post = lambda who, path, body: c(who).post(path, base_url=BASE, headers=ORIGIN, json=body)  # noqa: E731
    hit = lambda r, code, body: r.status_code == code and r.json == body  # noqa: E731
    save = lambda who, entries, **over: post(who, "/api/eval/me/self", {"year": Y, "entries": entries, **over})  # noqa: E731
    sub = lambda who, **over: post(who, "/api/eval/me/submit", {"year": Y, **over})  # noqa: E731
    rm = lambda who, fid: post(who, f"/api/eval/files/{fid}/delete", {})  # noqa: E731
    st = lambda who: get(who, "/api/eval/state").json  # noqa: E731

    def up(who, rid, data, name, year=Y):
        """증빙 올리기 (multipart: year · rid · file)."""
        return c(who).post("/api/eval/files", base_url=BASE, headers=ORIGIN, data={"year": str(year), "rid": str(rid), "file": (io.BytesIO(data), name)})

    items = lambda uid: q("SELECT rid, note, self_grade FROM ev_items WHERE year = ? AND user_id = ? ORDER BY rid", Y, uid)  # noqa: E731
    stage_of = lambda uid: q("SELECT stage, self_at FROM ev_targets WHERE year = ? AND user_id = ?", Y, uid)[0]  # noqa: E731
    files_of = lambda uid: q("SELECT id, rid, name, kind, size, sha256, at FROM ev_files WHERE year = ? AND user_id = ? ORDER BY id", Y, uid)  # noqa: E731
    audits = lambda action: [(e, json.loads(a)) for e, a in q("SELECT entity_id, after_data FROM audit_logs WHERE action = ? ORDER BY id", action)]  # noqa: E731
    n_audit = lambda: q("SELECT COUNT(*) FROM audit_logs WHERE action GLOB 'EVAL_*'")[0][0]  # noqa: E731
    put = lambda uid, **cols: x(f"UPDATE ev_targets SET {', '.join(f'{k} = ?' for k in cols)} WHERE year = ? AND user_id = ?", *cols.values(), Y, uid)  # noqa: E731
    dbdir = Path(os.environ["HR_DB_PATH"]).parent
    fdir = dbdir / "eval_files"
    ls = lambda: sorted(p.name for p in fdir.iterdir()) if fdir.exists() else []  # noqa: E731

    sent: list[tuple[int, str, str]] = []
    owner: dict[str, int] = {}

    def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 둔다 (받은 사람 · 제목 · 열 화면)
        sent.append((owner[info["endpoint"]], payload["title"], payload["url"]))
        return 201

    def took():
        out = sorted(sent)
        sent.clear()
        return out

    TURN = ("인사평가에서 처리할 일이 있어요", "/?go=perf.e2")
    PDF, PNG, JPG = "%PDF-1.4\n가상".encode(), b"\x89PNG\r\n\x1a\n" + b"0" * 8, b"\xff\xd8\xff\xe0" + b"0" * 8
    ZIP, OLE = b"PK\x03\x04" + b"0" * 8, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 8
    BAD_YEAR, NOT_TARGET = {"error": "해가 올바르지 않습니다."}, {"error": "이번 평가 대상이 아닙니다."}
    CLOSED, FILL = {"error": "지금은 자기평가를 쓸 수 없습니다."}, {"error": "모든 KPI에 실적과 달성 등급을 넣어 주세요."}
    LATE = {"error": f"자기평가 마감일({ymd(12, 5)})이 지났습니다. HR에 문의해 주세요."}
    ENTRIES, RID, DUP = ({"error": "실행목표별 실적을 목록으로 보내 주세요."}, {"error": "실행목표를 찾을 수 없습니다."}, {"error": "실행목표는 한 번씩만 보내 주세요."})
    NOTE_LEN, NOTE_TYPE, NOTE_CHAR = ({"error": "실적은 500자 안으로 써 주세요."}, {"error": "실적은 글로 써 주세요."}, {"error": "실적에 쓸 수 없는 글자가 있습니다."})
    GRADE = {"error": "달성 등급은 S · A · B · C · D 중에서 골라 주세요."}
    NO_UP = {"error": "증빙 마감일이 지났거나 올릴 수 없는 단계입니다."}
    NO_ITEM, FIVE = {"error": "증빙을 붙일 실행목표를 찾을 수 없습니다."}, {"error": "실행목표 하나에 증빙은 5개까지 올릴 수 있습니다."}
    EMPTY, BIG = {"error": "올릴 파일이 비어 있습니다."}, {"error": "증빙 파일은 10MB까지 올릴 수 있습니다."}
    FMT = {"error": "PDF · 그림(PNG·JPG) · 엑셀 · 워드 · 한글 파일만 올릴 수 있습니다."}
    NO_DEL, NOT_MINE = {"error": "증빙 마감일이 지났거나 지울 수 없는 단계입니다."}, {"error": "내가 올린 증빙만 지울 수 있습니다."}
    NO_FILE = {"error": "증빙을 찾을 수 없습니다."}

    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    orig = q("SELECT self_to FROM ev_rounds WHERE year = ?", Y)[0][0]
    hashes: list[str] = []
    fids: list[int] = []
    RESET = ("UPDATE ev_targets SET stage = CASE WHEN kpi_version IS NULL THEN 'SCORE' ELSE 'SELF' END, proposed = NULL, grade = NULL, sub_score_x10 = NULL,"
             " sub_marks = NULL, scored_at = NULL, scored_by = NULL, comment = '', head_comment = '', bonus_x2 = 0, bonus_reason = '', proxy_by = NULL, edit_seen = NULL,"
             " late = 0, leave_x2 = 0, out_at = NULL, out_reason = NULL, out_by = NULL, no_self_at = NULL, no_self_by = NULL, self_at = NULL WHERE year = ?")
    try:
        for uid in other_ceos:  # 이 가상 조직의 대표이사는 check_eval_admin 이 만든 한 명뿐이게
            x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", CEO)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x("UPDATE ev_rounds SET self_to = ?, released_at = NULL WHERE year = ?", ymd(12, 5), Y)  # 자기평가 마감 12/5 · 증빙 마감 12/10 (평가 시작 12/1)
        ok(q("SELECT started_at, evid_due FROM ev_rounds WHERE year = ?", Y) == [(z(12, 1), ymd(12, 10))], "(이어 쓰는 회차: 시작함 · 증빙 마감 12/10 · 자기평가 마감을 12/5 로 둠)")
        pub, priv = push.keygen()
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        push.SYNC, push.SEND = True, fake_send
        for uid in (LEAD, HEAD, CEO, HRL, HR, HRA, LEADC):  # 휴대폰을 등록한 사람 — 평가자 · 대표이사 · HR
            h, ep = f"{uid:03d}" + "6" * 61, f"https://push.example.invalid/self-{uid}"
            x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')", h, uid, z(11, 30), z(11, 30), z(12, 31))
            x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, z(11, 30))
            owner[ep] = uid
            hashes.append(h)

        # ---- 로그인 · 해 · 대상 아님 · KPI 없음 ----
        at(12, 2)
        paths = ("/api/eval/me/self", "/api/eval/me/submit", "/api/eval/files", "/api/eval/files/1/delete")
        ok(all(app.test_client().post(p, base_url=BASE, headers=ORIGIN, json={}).status_code == 401 for p in paths), "로그인 없이는 401 (자기평가 저장 · 제출 · 증빙 올리기 · 지우기)")
        ok(all(hit(save(EMP, [], year=v), 400, BAD_YEAR) and hit(sub(EMP, year=v), 400, BAD_YEAR) for v in (None, str(Y), float(Y), True, 2025, 2101))
           and all(hit(up(EMP, 1, PDF, "a.pdf", year=v), 400, BAD_YEAR) for v in (None, float(Y), True, 2025, 2101, "", "abc", "２０２７", "9" * 5000)),
           "해가 정수(2026~2100)가 아니면 400 (글자 · 소수 · 참/거짓 · 범위 밖 · 전각 숫자 — 증빙은 폼 글자 칸)")
        ok(all(hit(save(EMP, [], year=v), 404, NOT_TARGET) and hit(sub(EMP, year=v), 404, NOT_TARGET) and hit(up(EMP, 1, PDF, "a.pdf", year=v), 404, NOT_TARGET) for v in (Y - 1, Y + 1)),
           "평가 대상이 아닌 해(포털 밖의 해 · 회차 없는 해)는 404")
        ok(all(hit(save(who, []), 404, NOT_TARGET) and hit(sub(who), 404, NOT_TARGET) and hit(up(who, 1, PDF, "a.pdf"), 404, NOT_TARGET) for who in (HRA, HEAD, CEO)),
           "평가 대상이 아닌 사람(HR · 본부장 · 대표이사)은 자기평가도 증빙도 404")
        n0, names0 = n_audit(), ls()
        ok(all(hit(save(who, [{"rid": 1, "note": "가상", "selfGrade": "A"}]), 403, CLOSED) and hit(sub(who), 403, CLOSED) and hit(up(who, 1, PDF, "a.pdf"), 403, NO_UP)
               for who in (FIELD, NOCONF)) and not q("SELECT 1 FROM ev_items WHERE user_id IN (?, ?)", FIELD, NOCONF),
           "KPI_없는_사람은_자기평가가_없다: 확정 KPI 가 없는 사람(현장직 · 확정 없음)은 저장 · 제출 403 · 증빙 403")
        ok(hit(save(FIELD, "x"), 403, CLOSED) and hit(save(HRA, "x"), 404, NOT_TARGET), "거절 순서: 모양이 틀려도 대상이 아니면 404 · 쓸 수 없으면 403 이 먼저 (모양 400 은 그 다음)")

        # ---- 자기평가 모양 ----
        bad = [("x", ENTRIES), (None, ENTRIES), ({"rid": 1}, ENTRIES), ([5], RID), ([{}], RID), ([{"rid": 99}], RID), ([{"rid": 4}], RID), ([{"rid": "1"}], RID), ([{"rid": True}], RID),
               ([{"rid": 1.0}], RID), ([{"rid": 1, "selfGrade": "E"}], GRADE), ([{"rid": 1, "selfGrade": "a"}], GRADE), ([{"rid": 1, "selfGrade": 5}], GRADE),
               ([{"rid": 1, "selfGrade": ""}], GRADE), ([{"rid": 1, "selfGrade": ["A"]}], GRADE), ([{"rid": 1, "note": "가" * 501}], NOTE_LEN),
               ([{"rid": 1, "note": "가상\x00"}], NOTE_CHAR), ([{"rid": 1, "note": "가상\x07"}], NOTE_CHAR), ([{"rid": 1, "note": 5}], NOTE_TYPE), ([{"rid": 1, "note": None}], NOTE_TYPE),
               ([{"rid": 1}, {"rid": 1}], DUP), ([{"rid": 1, "note": "가상 저장되면 안 되는 글", "selfGrade": "A"}, {"rid": 99}], RID)]
        ok(all(hit(save(DIVD, v), 400, m) for v, m in bad) and items(DIVD) == [(1, "", None), (2, "", None), (3, "", None)],
           "자기평가_모양: 목록이 아님 · 없는 실행목표(rid) · 숫자가 아닌 rid · 등급 아님('E' · 소문자 · 빈 글자) · 실적 501자 · NUL 등 쓸 수 없는 글자 · 글이 아님 · 같은 줄 두 번 → 400, "
           "뒤 줄이 틀리면 앞 줄도 저장 안 함")
        r = save(DIVD, [{"rid": 1, "note": "가상 실적 1", "selfGrade": "A"}, {"rid": 3, "note": "가" * 500, "selfGrade": "D"}], userId=EMP, pid=EMP, rater=HEAD)
        ok(r.status_code == 200 and r.json["ok"] is True and items(DIVD) == [(1, "가상 실적 1", "A"), (2, "", None), (3, "가" * 500, "D")]
           and items(EMP) == [(1, "", None), (2, "", None), (4, "", None)],
           "자기평가_모양: 보낸 줄만 바뀜(500자는 됨 · 안 보낸 2번은 그대로) · 본문에 다른 사람 번호를 넣어도 본인 줄만 바뀜 — 남의 것을 쓸 길이 없다")
        me = r.json["me"]
        ok(me == st(DIVD)["me"] and me["pid"] == DIVD and me["ev"][0]["note"] == "가상 실적 1" and me["ev"][0]["selfGrade"] == "A" and "grade" not in me["ev"][0]
           and "proposed" not in me and me["can"]["self"] is True, "저장 응답의 me 는 상태 읽기의 me 와 같은 줄(_row_json) — 평가 칸이 없는 본인 줄")
        ok(save(DIVD, [{"rid": 1, "note": "가상 고친 실적"}]).status_code == 200 and items(DIVD)[0] == (1, "가상 고친 실적", "A")
           and save(DIVD, [{"rid": 1, "selfGrade": None}]).status_code == 200 and items(DIVD)[0] == (1, "가상 고친 실적", None)
           and save(DIVD, []).status_code == 200 and items(DIVD)[2] == (3, "가" * 500, "D"),
           "자기평가_모양: 보낸 칸만 바뀜(실적만 · 등급만) · 등급을 null 로 비움 · 빈 목록 200")
        ok(save(DIVD, [{"rid": 2, "note": "  " + "가" * 500 + " \n\t "}]).status_code == 200 and items(DIVD)[1] == (2, "가" * 500, None)
           and hit(save(DIVD, [{"rid": 2, "note": " \n" + "가" * 501 + "  "}]), 400, NOTE_LEN) and items(DIVD)[1] == (2, "가" * 500, None)
           and save(DIVD, [{"rid": 2, "note": "  \n"}]).status_code == 200 and items(DIVD)[1] == (2, "", None),
           "자기평가_모양: 실적은 앞뒤 공백을 떼고 센다 — 500자 + 뒤쪽 줄바꿈 · 공백은 200 · 떼고 저장 / 떼고 501자는 400 / 공백만이면 빈 글로 저장")
        ok(n_audit() == n0 and not took(), "임시 저장은 감사로그도 알림도 없음")

        # ---- 제출은 다 채워야 ----
        ok(hit(sub(EMP), 400, FILL) and stage_of(EMP) == ("SELF", None), "제출은_다_채워야: 아무것도 안 썼으면 400")
        save(EMP, [{"rid": 1, "note": "가상 실적 1", "selfGrade": "A"}, {"rid": 2, "note": "가상 실적 2", "selfGrade": "B"}, {"rid": 4, "selfGrade": "C"}])
        ok(hit(sub(EMP), 400, FILL), "제출은_다_채워야: 실적 하나가 비면 400")
        ok(all(save(EMP, [{"rid": 4, "note": v}]).status_code == 200 and hit(sub(EMP), 400, FILL) for v in ("  \n\t ", "\u3000\u00a0")), "제출은_다_채워야: 공백만 써도 400 (줄바꿈 · 탭 · 전각 공백)")
        save(EMP, [{"rid": 4, "note": "가상 실적 4"}, {"rid": 2, "selfGrade": None}])
        ok(hit(sub(EMP), 400, FILL) and stage_of(EMP) == ("SELF", None) and not took(), "제출은_다_채워야: 등급 하나가 비면 400 · 단계 그대로 · 알림 없음")
        save(EMP, [{"rid": 2, "selfGrade": "B"}])
        r = sub(EMP)
        ok(hit(r, 200, {"ok": True}) and stage_of(EMP) == ("SCORE", z(12, 2)), "제출은_다_채워야: 다 채우면 200 → 단계 SCORE · self_at")
        ok(took() == [(LEAD, *TURN)], "제출 → 떠 둔 평가자(팀장)에게만 eval_turn")
        before = items(EMP)
        ok(hit(save(EMP, [{"rid": 1, "note": "제출 뒤에 고침"}]), 403, CLOSED) and hit(sub(EMP), 403, CLOSED) and items(EMP) == before and not took(),
           "제출은_다_채워야: 제출한 뒤 다시 저장 · 제출은 403 · 실적 그대로 · 알림 없음")
        logs = q("SELECT after_data, before_data, reason FROM audit_logs WHERE action = 'EVAL_SELF_SUBMITTED'")
        ok(audits("EVAL_SELF_SUBMITTED") == [(EMP, {"year": Y})] and not any("가상 실적" in "".join(map(str, row)) for row in logs) and n_audit() == n0 + 1,
           "제출은_다_채워야: 감사로그 EVAL_SELF_SUBMITTED 는 해만 — 실적 글자가 어디에도 없음 (저장은 안 남김)")

        # ---- 증빙: 제출 뒤(SCORE)에도 올림 ----
        r = up(EMP, 1, PDF, "가상 증빙.pdf")
        fid_emp = r.json["id"]
        fids.append(fid_emp)
        ok(r.status_code == 200 and r.json == {"ok": True, "id": fid_emp} and (fdir / str(fid_emp)).read_bytes() == PDF, "증빙은_마감일까지_점수_전까지: 자기평가를 낸 뒤(SCORE)에도 올릴 수 있음 → 200 · 파일은 번호 이름")

        # ---- 자기평가 마감일 밤 12시 ----
        at(12, 5, 14, 59)  # 한국 12/5 23:59 — 자기평가 마감일 밤
        full = [{"rid": r_, "note": f"가상 실적 {r_}", "selfGrade": "B"} for r_ in (1, 2, 3)]
        ok(save(EMPB, full).status_code == 200 and hit(sub(EMPB), 200, {"ok": True}) and stage_of(EMPB)[0] == "SCORE" and stage_of(EMPB)[1] == z(12, 5, 14, 59) and not took(),
           "자기평가는_마감일_밤까지: 마감일 한국 23:59(UTC 14:59)에도 저장 · 제출 200 (일요일 밤이라 알림은 안 감)")
        at(12, 5, 15, 0)  # 한국 12/6 0시
        ok(hit(save(LEAD, full), 403, LATE) and hit(sub(LEAD), 403, LATE) and items(LEAD) == [(1, "", None), (2, "", None), (3, "", None)] and stage_of(LEAD)[0] == "SELF",
           "자기평가는_마감일_밤까지: 다른 사람은 다음 날 0시(UTC 15:00)에 저장 · 제출 403 + 마감일 안내 문구 · 아무것도 안 바뀜")
        ok(hit(save(EMPB, full), 403, CLOSED) and not st(LEAD)["me"]["can"]["self"], "자기평가는_마감일_밤까지: 이미 낸 사람은 마감 뒤에도 '지금은 쓸 수 없습니다' (can.self 거짓)")
        r = up(LEAD, 1, PDF, "마감 뒤 증빙.pdf")
        fids.append(r.json.get("id"))
        ok(r.status_code == 200 and st(LEAD)["me"]["can"]["upload"] is True, "증빙 마감일은 따로: 자기평가 마감이 지나도 증빙 마감(12/10)까지는 올릴 수 있음")

        # ---- 증빙 형식 ----
        at(12, 2)
        base = ls()
        wrong = [(PNG, "가상.pdf"), (b"MZ\x90\x00" + b"0" * 8, "가상.exe"), (PDF, "가상"), (PDF, "가상.pdf.exe"), (ZIP, "가상.hwp"), (OLE, "가상.hwpx"), (OLE, "가상.xlsx"), (ZIP, "가상.xls"),
                 (b"%PDF", "가상.pdf"), (JPG, "가상.png"), (PNG, "가상.jpg"), (b"<script>", "가상.html")]
        ok(all(hit(up(EMPC, 1, d, n), 400, FMT) for d, n in wrong) and not files_of(EMPC) and ls() == base,
           "증빙_형식은_첫_바이트로: .pdf 인데 PNG 머리 · .exe · 확장자 없음 · 이중 확장자 · 확장자와 머리가 안 맞음(OLE ↔ ZIP) · 머리가 짧음 → 400 · 줄도 파일도 안 남음")
        ok(hit(up(EMPC, 1, b"", "가상.pdf"), 400, EMPTY) and hit(c(EMPC).post("/api/eval/files", base_url=BASE, headers=ORIGIN, data={"year": str(Y), "rid": "1"}), 400, EMPTY)
           and not files_of(EMPC), "증빙_형식은_첫_바이트로: 0바이트 · 파일을 안 보냄 → 400")
        for rid_, data, name in ((1, OLE, "가상 한글.hwp"), (1, PDF, "가상.PDF"), (1, PNG, "가상.png"), (1, JPG, "사진.JPEG"), (1, ZIP, "표.xlsx")):
            r = up(EMPC, rid_, data, name)
            fids.append(r.json["id"])
            ok(r.status_code == 200 and (fdir / str(r.json["id"])).read_bytes() == data, f"증빙_형식은_첫_바이트로: {name} 200 (.hwp 는 OLE 머리 · .PDF 는 소문자로 따짐 · .JPEG 는 jpg)")
        got = files_of(EMPC)
        ok([(g[1], g[2], g[3], g[4], g[5], g[6]) for g in got] == [(1, "가상 한글.hwp", "hwp", len(OLE), hashlib.sha256(OLE).hexdigest(), z(12, 2)),
                                                                 (1, "가상.PDF", "pdf", len(PDF), hashlib.sha256(PDF).hexdigest(), z(12, 2)),
                                                                 (1, "가상.png", "png", len(PNG), hashlib.sha256(PNG).hexdigest(), z(12, 2)),
                                                                 (1, "사진.JPEG", "jpg", len(JPG), hashlib.sha256(JPG).hexdigest(), z(12, 2)),
                                                                 (1, "표.xlsx", "xlsx", len(ZIP), hashlib.sha256(ZIP).hexdigest(), z(12, 2))],
           "증빙 줄: 이름 · 종류(jpeg 도 jpg) · 크기 · sha256 · 올린 때")
        ok(hit(up(EMPC, 1, ZIP, "여섯째.docx"), 400, FIVE) and len(files_of(EMPC)) == 5, "증빙_형식은_첫_바이트로: 한 실행목표에 6번째는 400")
        for rid_, data, name, kind in ((2, ZIP, "문서.docx", "docx"), (2, OLE, "문서.doc", "doc"), (2, ZIP, "한글.hwpx", "hwpx"), (2, OLE, "표.xls", "xls")):
            r = up(EMPC, rid_, data, name)
            fids.append(r.json["id"])
            ok(r.status_code == 200 and files_of(EMPC)[-1][2:4] == (name, kind), f"{name} 200 (다른 실행목표는 따로 다섯 개까지)")
        ok(all(hit(up(EMPC, v, PDF, "가상.pdf"), 400, NO_ITEM) for v in (99, 4, 0, -1, "abc", "", "1.5", "１")) and hit(c(EMPC).post(
            "/api/eval/files", base_url=BASE, headers=ORIGIN, data={"year": str(Y), "file": (io.BytesIO(PDF), "a.pdf")}), 400, NO_ITEM) and len(files_of(EMPC)) == 9,
           "증빙은 떠 둔 실행목표에만: 없는 번호 · 이 사람 KPI 에 없는 번호 · 숫자가 아님 · 빠짐 → 400")

        # ---- 크기 ----
        edge = b"%PDF-" + b"0" * (FILE_MAX - 5)
        r = up(EMPC, 3, edge, "열 메가.pdf")
        fids.append(r.json["id"])
        ok(r.status_code == 200 and files_of(EMPC)[-1][4] == FILE_MAX and (fdir / str(r.json["id"])).stat().st_size == FILE_MAX, "증빙_형식은_첫_바이트로: 딱 10MB 는 200")
        ok(hit(up(EMPC, 3, edge + b"0", "넘음.pdf"), 400, BIG) and hit(up(EMPC, 3, edge + b"0" * 70000, "많이 넘음.pdf"), 400, BIG)
           and hit(up(FIELD, 1, edge + b"0" * 70000, "올릴 수 없는 사람.pdf"), 400, BIG) and len(files_of(EMPC)) == 10 and rm(EMPC, r.json["id"]).status_code == 200,
           "증빙_형식은_첫_바이트로: 10MB + 1바이트 400 · 읽기 전에 몸통 크기로 거름(10MB + 70KB) 400 · 줄 안 남음")

        # ---- 이름 · 저장 위치 ----
        outside = lambda: sorted(p.name for p in dbdir.iterdir() if not p.name.startswith("auth.db"))  # noqa: E731  (DB 자신의 -wal · -shm 은 오가므로 뺀다)
        before_dir = outside()
        # 윈도 경로는 역슬래시를 두 번 써서 보낸다 — 요청 머리의 따옴표 안 역슬래시는 다음 글자를 그대로 쓰라는 표시라
        # Werkzeug 3.1.9 부터 한 번만 쓴 역슬래시는 서버에 오기 전에 사라진다 (C:\fakepath\y.pdf → C:fakepathy.pdf). 3.1.8 은 두 개 그대로 넘긴다 — 어느 쪽이든 y.pdf
        names = {"../../x.pdf": "x.pdf", "C:\\\\fakepath\\\\y.pdf": "y.pdf", "a\x07b.pdf": "ab.pdf", "가" * 200 + ".pdf": "가" * 116 + ".pdf", "a\u202efdp.pdf": "afdp.pdf"}
        for raw, want in names.items():
            r = up(EMPC, 3, PDF, raw)
            fids.append(r.json.get("id"))
            ok(r.status_code == 200 and files_of(EMPC)[-1][2] == want, f"증빙_형식은_첫_바이트로: 이름 {raw[:20]!r} → 저장 이름 {want[:20]!r} (경로를 떼고 · 제어문자를 빼고 · 120자로 줄이되 확장자는 남김)")
        ok(evaluation._clean_name("C:\\fakepath\\y.pdf") == "y.pdf" and evaluation._clean_name("\\\\nas\\공유\\z.pdf") == "z.pdf",
           "증빙_형식은_첫_바이트로: 이름 정리 — 서버에 온 윈도 경로(C:\\…\\y.pdf · \\\\nas\\…\\z.pdf)는 마지막 이름만")
        ok(outside() == before_dir and not (dbdir.parent / "x.pdf").exists() and not (fdir / "x.pdf").exists() and not (fdir / "y.pdf").exists()
           and all(n.isdigit() for n in ls()), "증빙_형식은_첫_바이트로: 폴더 밖에 아무것도 없고 폴더 안은 번호 이름뿐 (사람이 준 이름을 경로에 쓰지 않음)")
        for f in files_of(EMPC):  # 이름을 본 파일은 지운다 (실행목표 3번을 다시 비움)
            if f[1] == 3:
                rm(EMPC, f[0])
        # 쓰다가 실패하면 줄도 되돌린다
        n_rows, names_now = len(files_of(EMPC)), ls()
        app.logger.disabled = True  # 예상한 서버 오류의 자세한 로그를 찍지 않게
        try:
            with mock.patch.object(evaluation.os, "replace", side_effect=OSError("가상 쓰기 실패")):
                r = up(EMPC, 2, PDF, "실패.pdf")
        finally:
            app.logger.disabled = False
        ok(r.status_code == 500 and len(files_of(EMPC)) == n_rows and ls() == names_now, "증빙은_줄과_파일이_같이: 파일을 못 쓰면 줄도 되돌림 · 임시 파일도 안 남음")
        r = up(EMPC, 3, PDF, "다시.pdf")
        fids.append(r.json["id"])
        ok(r.status_code == 200 and (fdir / str(r.json["id"])).read_bytes() == PDF, "(실패 뒤에도 올리기는 됨)")
        (fdir / str(r.json["id"] + 1)).write_bytes(b"stale")  # 파일을 쓴 뒤 줄이 되돌려져 남은 옛 파일 (번호는 지워도 다시 쓰지 않지만 되돌려진 번호는 다시 받는다)
        stale = r.json["id"] + 1
        r = up(EMPC, 2, ZIP, "덮어쓰기.docx")
        fids.append(r.json["id"])
        ok(r.json["id"] == stale and (fdir / str(stale)).read_bytes() == ZIP, "증빙은_줄과_파일이_같이: 같은 번호의 옛 파일이 남아 있어도 새 파일로 덮어씀")
        top = r.json["id"]
        ok(rm(EMPC, top).status_code == 200 and (r := up(EMPC, 2, ZIP, "다시 올림.docx")).json["id"] == top + 1,
           "증빙 번호는 지워도 다시 쓰지 않는다: 맨 끝 증빙을 지우고 올리면 그다음 번호 (옛 번호를 든 화면이 남의 새 파일을 받지 못하게)")
        fids[fids.index(top)] = r.json["id"]
        r = up(EMPC, 3, JPG, "나" * 130 + ".JPEG")
        fids.append(r.json["id"])
        last = files_of(EMPC)[-1]
        ok(r.status_code == 200 and len(last[2]) == 120 and last[2] == "나" * 115 + ".JPEG" and last[3] == "jpg",
           "증빙_형식은_첫_바이트로: 긴 이름은 확장자를 쓴 그대로(.JPEG) 남기고 이름 쪽을 잘라 120자 · 종류는 jpg")

        # ---- 올릴 수 있는 때 ----
        put(EMPC, stage="GRADE", proposed="B")
        ok(hit(up(EMPC, 3, PDF, "점수 뒤.pdf"), 403, NO_UP), "증빙은_마감일까지_점수_전까지: 평가자가 점수를 낸 뒤(GRADE) 403")
        put(EMPC, stage="SELF", proposed=None)
        put(EMPC, out_at=z(12, 2), out_reason="가상 휴직", out_by=HRA)
        ok(hit(up(EMPC, 3, PDF, "제외.pdf"), 403, NO_UP), "평가에서 제외된 사람은 403")
        put(EMPC, out_at=None, out_reason=None, out_by=None)
        x("UPDATE ev_rounds SET released_at = ? WHERE year = ?", z(12, 2), Y)
        ok(hit(up(EMPC, 3, PDF, "공개 뒤.pdf"), 403, NO_UP), "결과를 공개한 뒤는 403")
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)
        at(11, 30, 14, 59)  # 한국 11/30 23:59 — 평가 시작일(12/1) 전날
        ok(hit(up(EMPC, 3, PDF, "시작 전.pdf"), 403, NO_UP), "증빙은_마감일까지_점수_전까지: 평가 시작일 전날 403")
        at(12, 10, 14, 59)  # 한국 12/10 23:59 — 증빙 마감일 밤
        r = up(EMPC, 3, PDF, "마감일 밤.pdf")
        fids.append(r.json.get("id"))
        ok(r.status_code == 200, "증빙은_마감일까지_점수_전까지: 증빙 마감일 한국 23:59(UTC 14:59)에도 200")
        at(12, 10, 15, 0)  # 한국 12/11 0시
        ok(hit(up(EMPC, 3, PDF, "마감 다음 날.pdf"), 403, NO_UP) and hit(up(EMPB, 1, PDF, "낸 사람.pdf"), 403, NO_UP) and st(EMPC)["me"]["can"]["upload"] is False,
           "증빙은_마감일까지_점수_전까지: 마감 다음 날 0시(UTC 15:00)는 403 (자기평가를 낸 사람도) · can.upload 거짓")
        ok(n_audit() == n0 + len(audits("EVAL_SELF_SUBMITTED")), "증빙 올리기도 감사로그를 남기지 않음 (감사로그는 제출만)")

        # ---- 지우기 ----
        at(12, 2)
        ok(all(hit(rm(who, fid_emp), 403, NOT_MINE) for who in (LEAD, HEAD, CEO, HRA, EMPC)) and files_of(EMP) != [] and (fdir / str(fid_emp)).is_file(),
           "증빙_지우기: 남의 것은 평가자 · 본부장 · 대표이사 · HR 도 403 · 그대로")
        ok(hit(rm(EMP, 999999), 404, NO_FILE) and c(EMP).post(f"/api/eval/files/{2 ** 31}/delete", base_url=BASE, headers=ORIGIN, json={}).status_code == 404, "증빙_지우기: 없는 번호 404")
        r = rm(EMP, fid_emp)
        ok(hit(r, 200, {"ok": True}) and files_of(EMP) == [] and not (fdir / str(fid_emp)).exists() and hit(rm(EMP, fid_emp), 404, NO_FILE),
           "증빙_지우기: 주인 200 (제출한 SCORE 단계에서도) → 줄도 파일도 사라짐 · 또 지우면 404")
        fids.remove(fid_emp)
        victim = files_of(EMPC)[-1][0]
        put(EMPC, stage="GRADE", proposed="B")
        ok(hit(rm(EMPC, victim), 403, NO_DEL) and (fdir / str(victim)).is_file() and any(f[0] == victim for f in files_of(EMPC)), "증빙_지우기: 평가자가 점수를 낸 뒤 403 · 줄도 파일도 그대로")
        put(EMPC, stage="SELF", proposed=None)
        at(12, 10, 15, 0)
        ok(hit(rm(EMPC, victim), 403, NO_DEL) and (fdir / str(victim)).is_file(), "증빙_지우기: 증빙 마감 다음 날 403")
        at(12, 2)
        (fdir / str(victim)).unlink()  # 파일이 이미 없어도
        ok(hit(rm(EMPC, victim), 200, {"ok": True}) and all(f[0] != victim for f in files_of(EMPC)), "증빙_지우기: 파일이 이미 없어도 줄은 지우고 200")
        fids.remove(victim)
        ok(n_audit() == n0 + len(audits("EVAL_SELF_SUBMITTED")), "(지우기도 감사로그 없음)")

        # ---- 알림 받는 사람 ----
        for uid in (LEADC, HRL, HR):
            x("UPDATE ev_items SET note = '가상 실적', self_grade = 'B' WHERE year = ? AND user_id = ?", Y, uid)
        took()
        ok(hit(sub(LEADC), 200, {"ok": True}) and took() == [(CEO, *TURN)], "제출 알림: 평가자가 대표이사(맡기지 않음) → 대표이사에게")
        ok(hit(sub(HR), 200, {"ok": True}) and took() == [(HRL, *TURN)], "제출 알림: 평가자가 HR 팀장 → 그 사람에게만")
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 2))
        ok(hit(sub(HRL), 200, {"ok": True}) and took() == [(HRA, *TURN), (HR, *TURN)],
           "제출 알림: 평가자가 대표이사이고 맡겼으면 HR 전원 — 제출한 HR 본인(HR 팀장)은 빼고, 대표이사에게는 안 감")
        put(LEADC, stage="SELF", self_at=None)
        ok(hit(sub(LEADC), 200, {"ok": True}) and took() == [(HRA, *TURN), (HRL, *TURN), (HR, *TURN)], "제출 알림: 맡겼으면 HR 아닌 사람의 제출은 HR 전원에게")
        skip = lambda uid: post(HRA, f"/api/eval/admin/{uid}/skip-self", {"year": Y})  # noqa: E731
        put(HRL, stage="SELF", self_at=None)
        ok(hit(skip(HRL), 200, {"ok": True}) and took() == [(HR, *TURN)],
           "자기평가 없이 진행 알림: 받는 사람은 제출과 같다 — 평가자가 대표이사이고 맡겼으면 HR 전원 (평가받는 HR 팀장 본인 · 누른 HR 은 빼고, 대표이사에게는 안 감)")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        ok(hit(skip(EXEC), 200, {"ok": True}) and took() == [(CEO, *TURN)], "자기평가 없이 진행 알림: 맡기지 않았으면 평가자(대표이사)에게만")
        ok(len(audits("EVAL_SELF_SUBMITTED")) == 6, "(자기평가 제출 감사로그: 제출마다 한 줄)")

        # ---- nginx ----
        conf = (HERE.parent / "nginx.conf").read_text(encoding="utf-8")
        head, _, rest = conf.partition("location = /api/eval/files")
        blk, api = rest.split("}", 1)[0], conf.split("location /api/ {", 1)[1].split("}", 1)[0]
        proxy = lambda b: {ln.split("#")[0].strip() for ln in b.splitlines() if ln.strip().startswith(("proxy_set_header", "proxy_read_timeout"))}  # noqa: E731
        ok(rest and "client_max_body_size 11m;" in blk and conf.count("client_max_body_size") == 2 and "client_max_body_size 5m;" in head.split("location", 1)[0]
           and "proxy_pass http://api:8000;" in blk and proxy(blk) == proxy(api) and len(proxy(blk)) == 4,
           "nginx_증빙만_11MB: location = /api/eval/files 만 본문 11m (전체 5m 는 그대로) · 나머지 proxy 설정은 /api/ 와 같음")
    finally:
        auth._now = real_now
        push.SYNC, push.SEND = real_sync, real_send
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        app.logger.disabled = False
        for fid in fids:
            if fid:
                x("DELETE FROM ev_files WHERE id = ?", fid)
                (fdir / str(fid)).unlink(missing_ok=True)
        x("DELETE FROM ev_files WHERE year = ?", Y)
        for p in (fdir.iterdir() if fdir.exists() else ()):
            p.unlink()
        for h in hashes:
            x("DELETE FROM push_subscriptions WHERE token_hash = ?", h)
            x("DELETE FROM sessions WHERE token_hash = ?", h)
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x("UPDATE ev_rounds SET self_to = ?, released_at = NULL WHERE year = ?", orig, Y)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)


def check_eval_score(hr) -> None:
    """인사평가 평가자 점수 · 등급 배정 · 본부장 마감 (2026-10-26, 평가 3단계 ①): 평가자는 점수 단계(SCORE)에서만 실행목표 등급 · 등급 의견 · 코멘트를 쓰고 낸다 —
    항목 등급이 다 차면 점수에 맞는 가장 높은 등급이 저절로 나오고(화면 evFit), 점수 기준 · 연차로 줄 수 없는 등급은 400. 내면 낸 그대로 떠 둔다(공개 전 평가자에게는 그것만 보인다).
    배정자(본부장 · 대표이사)는 배정 단계에서만 고치고, 본부장은 묶음 전원이 배정 단계에 와서 정원에 맞으면 한 번 마감한다. 맡김이면 HR 이 대표이사 몫을 대신하되 자기 줄과 자기가 든 묶음의 사람은 못 한다.
    고친 것은 평가자에게 '고쳤다'는 사실만 알린다. 알림은 저장한 뒤 다음 차례에게(글에 등급 · 이름 없음). 감사로그에는 해 · 묶음 열쇠뿐 (고치기 · 확인은 남기지 않는다).
    check_eval_self 가 이어 둔 회차와 가상 조직(부서 91~97 · 사람 101~116)을 쓴다 — 시작에 단계 · 등급을 깨끗이 하고 단계는 바로 넣어 상황을 만든다.
    끝에 시계 · 알림 · 대표이사 · 맡김 · 다른 본부장 · 묶음 기록 · 단계 · 등급을 되돌린다."""
    import evaluation
    import push
    print("인사평가 평가자 점수 · 등급 배정 · 본부장 마감 · 대신 입력")
    HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO, OFF = range(101, 114)
    NOCONF, HEAD2, HRA = 115, 116, 1  # HRA = 평가 대상이 아닌 HR (처음 검사의 인사담당자)
    Y, real_now, real_send, real_sync = evaluation.FIRST_YEAR, auth._now, push.SEND, push.SYNC
    z = lambda m, d, hh=3, mi=0: f"{Y}-{m:02d}-{d:02d}T{hh:02d}:{mi:02d}:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def at(m, d, hh=3, mm=0):
        """시계를 UTC 로 (Y년 m월 d일 hh:mm) 에 맞춘다. 기본 03:00 = 한국 정오 (12월 7일은 화요일 — 근무시간이라 알림이 간다)."""
        t = datetime(Y, m, d, hh, mm, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    get = lambda who, path: c(who).get(path, base_url=BASE)  # noqa: E731
    post = lambda who, path, body: c(who).post(path, base_url=BASE, headers=ORIGIN, json=body)  # noqa: E731
    hit = lambda r, code, body: r.status_code == code and r.json == body  # noqa: E731
    st = lambda who: get(who, "/api/eval/state").json  # noqa: E731
    row_of = lambda s, uid: next((p for p in s["people"] if p["pid"] == uid), None)  # noqa: E731
    pool_of = lambda who, k: next(p for p in st(who)["pools"] if p["key"] == k)  # noqa: E731
    edit = lambda who, uid, **body: post(who, f"/api/eval/people/{uid}/edit", {"year": Y, **body})  # noqa: E731
    submit = lambda who, uid, **over: post(who, f"/api/eval/people/{uid}/submit", {"year": Y, **over})  # noqa: E731
    seen = lambda who, uid, **over: post(who, f"/api/eval/people/{uid}/seen", {"year": Y, **over})  # noqa: E731
    close = lambda who, pool, **over: post(who, "/api/eval/pools/close", {"year": Y, "pool": pool, **over})  # noqa: E731
    pseen = lambda who, pool, **over: post(who, "/api/eval/pools/seen", {"year": Y, "pool": pool, **over})  # noqa: E731
    put = lambda uid, **cols: x(f"UPDATE ev_targets SET {', '.join(f'{k} = ?' for k in cols)} WHERE year = ? AND user_id = ?", *cols.values(), Y, uid)  # noqa: E731
    COLS = "stage, grade, proposed, sub_score_x10, sub_marks, comment, head_comment, bonus_x2, bonus_reason, scored_at, scored_by, proxy_by"
    tg = lambda uid: q(f"SELECT {COLS} FROM ev_targets WHERE year = ? AND user_id = ?", Y, uid)[0]  # noqa: E731
    ig = lambda uid: [r[0] for r in q("SELECT grade FROM ev_items WHERE year = ? AND user_id = ? ORDER BY rid", Y, uid)]  # noqa: E731
    pool_row = lambda k: q("SELECT closed_at, closed_by, hr_seen, back_at, back_by, back_behalf, back_reason FROM ev_pools WHERE year = ? AND pool = ?", Y, k)[0]  # noqa: E731
    audits = lambda action: [(e, json.loads(a)) for e, a in q("SELECT entity_id, after_data FROM audit_logs WHERE action = ? ORDER BY id", action)]  # noqa: E731

    def setg(uid, *grades):  # 실행목표 등급을 rid 차례로 바로 넣는다
        for (rid,), v in zip(q("SELECT rid FROM ev_items WHERE year = ? AND user_id = ? ORDER BY rid", Y, uid), grades):
            x("UPDATE ev_items SET grade = ? WHERE year = ? AND user_id = ? AND rid = ?", v, Y, uid, rid)

    sent: list[tuple[int, str, str]] = []
    texts: list[str] = []
    owner: dict[str, int] = {}

    def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 둔다 (받은 사람 · 제목 · 열 화면) — 글은 따로 모아 등급 · 이름이 없는지 본다
        sent.append((owner[info["endpoint"]], payload["title"], payload["url"]))
        texts.append(f"{payload['title']} {payload['body']}")
        return 201

    def took():
        out = sorted(sent)
        sent.clear()
        return out

    TURN = ("인사평가에서 처리할 일이 있어요", "/?go=perf.e2")
    OK = {"ok": True}
    BAD_YEAR, NOT_TARGET, HR_ONLY = {"error": "해가 올바르지 않습니다."}, {"error": "평가 대상이 아닙니다."}, {"error": "HR만 할 수 있습니다."}
    RELEASED = {"error": "결과를 공개한 평가는 바꿀 수 없습니다."}
    CANT, NO_SCORE = {"error": "지금 이 분의 평가를 고칠 수 없습니다."}, {"error": "지금 이 분의 점수를 낼 수 없습니다."}
    CMT_NO, HEAD_NO = {"error": "평가자 코멘트는 점수를 낼 때만 쓸 수 있습니다."}, {"error": "윗사람 코멘트는 등급을 배정하는 분만 쓸 수 있습니다."}
    BONUS_NO, BONUS_BAD = {"error": "가점은 등급을 배정하는 분만 줄 수 있습니다."}, {"error": "가점은 0~10점, 0.5점 단위로 넣어 주세요."}
    REASON, REASON_LEN = {"error": "가점 사유를 입력해 주세요."}, {"error": "가점 사유는 100자 안으로 써 주세요."}
    MARKS, RID = {"error": "실행목표별 등급을 실행목표 번호마다 보내 주세요."}, {"error": "실행목표를 찾을 수 없습니다."}
    ITEM_GRADE, GRADE_BAD = {"error": "달성 등급은 S · A · B · C · D 중에서 골라 주세요."}, {"error": "등급은 S · A · B · C · D 중에서 골라 주세요."}
    CMT_LEN, CMT_TYPE, CMT_CHAR = ({"error": "평가자 코멘트는 500자 안으로 써 주세요."}, {"error": "평가자 코멘트는 글로 써 주세요."},
                                   {"error": "평가자 코멘트에 쓸 수 없는 글자가 있습니다."})
    HEAD_LEN, NO_KPI_MARKS, NO_KPI_BONUS = ({"error": "윗사람 코멘트는 500자 안으로 써 주세요."}, {"error": "KPI가 없는 분은 등급만 매깁니다."},
                                            {"error": "KPI가 없는 분에게는 가점을 줄 수 없습니다."})
    FILL, NO_CMT = {"error": "모든 KPI에 달성 등급을 골라 주세요."}, {"error": "평가자 코멘트를 입력해 주세요. 직원에게 공개됩니다."}
    LEAVE = {"error": "연차 초과 5일 이상이라 S·A·B를 줄 수 없습니다."}
    PICK, EMPTY_POOL = {"error": "달성 등급을 골라 주세요."}, {"error": "평가할 분이 없는 묶음입니다."}
    over = lambda score, g_, band: {"error": f"총점 {score}점으로는 {g_}를 줄 수 없습니다. ({band}) 점수를 다시 봐 주세요."}  # noqa: E731
    DIV_ONLY, NO_POOL, NO_CLOSE = {"error": "본부 팀원 묶음만 마감할 수 있습니다."}, {"error": "묶음을 찾을 수 없습니다."}, {"error": "이 묶음을 마감할 권한이 없습니다."}
    EARLY, DONE_ALREADY, NOT_RATER = {"error": "아직 이전 단계에 있는 분이 있습니다."}, {"error": "이미 마감했습니다."}, {"error": "평가자 본인만 확인할 수 있습니다."}
    RESET = ("UPDATE ev_targets SET stage = CASE WHEN kpi_version IS NULL THEN 'SCORE' ELSE 'SELF' END, proposed = NULL, grade = NULL, sub_score_x10 = NULL,"
             " sub_marks = NULL, scored_at = NULL, scored_by = NULL, comment = '', head_comment = '', bonus_x2 = 0, bonus_reason = '', proxy_by = NULL, edit_seen = NULL,"
             " late = 0, leave_x2 = 0, out_at = NULL, out_reason = NULL, out_by = NULL, no_self_at = NULL, no_self_by = NULL, self_at = NULL WHERE year = ?")
    POOLS_RESET = "UPDATE ev_pools SET closed_at = NULL, closed_by = NULL, hr_seen = '[]', back_at = NULL, back_by = NULL, back_behalf = NULL, back_reason = NULL WHERE year = ?"

    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    hashes: list[str] = []
    try:
        for uid in other_ceos:  # 이 가상 조직의 대표이사는 check_eval_admin 이 만든 한 명뿐이게
            x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", CEO)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x(POOLS_RESET, Y)
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)
        ok(q("SELECT started_at, self_to, band_a, band_b, band_c FROM ev_rounds WHERE year = ?", Y) == [(z(12, 1), f"{Y}-12-10", 90, 80, 70)],
           "(이어 쓰는 회차: 시작함 · 자기평가 마감 12/10 · 기준 90 · 80 · 70)")
        pub, priv = push.keygen()
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        push.SYNC, push.SEND = True, fake_send
        for uid in (HEAD, LEAD, CEO, HRL, HR, HRA, LEADC):  # 휴대폰을 등록한 사람 — 평가자 · 배정자 · 대표이사 · HR
            h, ep = f"{uid:03d}" + "7" * 61, f"https://push.example.invalid/score-{uid}"
            x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')", h, uid, z(11, 30), z(11, 30), z(12, 31))
            x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, z(11, 30))
            owner[ep] = uid
            hashes.append(h)
        for uid in (EMP, LEAD, HR, EMPC):  # 자기평가를 낸 사람들 (KPI 없는 FIELD · NOCONF 는 처음부터 SCORE)
            put(uid, stage="SCORE")
        audit0 = q("SELECT COALESCE(MAX(id), 0) FROM audit_logs")[0][0]
        at(12, 7)

        # ---- 로그인 · 해 · 대상 ----
        paths = (f"/api/eval/people/{EMP}/edit", f"/api/eval/people/{EMP}/submit", f"/api/eval/people/{EMP}/seen", "/api/eval/pools/close", "/api/eval/pools/seen")
        ok(all(app.test_client().post(p, base_url=BASE, headers=ORIGIN, json={}).status_code == 401 for p in paths), "로그인 없이는 401 (고치기 · 내기 · 확인 · 마감 · 마감 확인)")
        ok(all(hit(edit(LEAD, EMP, year=v), 400, BAD_YEAR) and hit(submit(LEAD, EMP, year=v), 400, BAD_YEAR) and hit(seen(LEAD, EMP, year=v), 400, BAD_YEAR)
               and hit(close(HEAD, "DIV:91", year=v), 400, BAD_YEAR) and hit(pseen(HRA, "DIV:91", year=v), 400, BAD_YEAR) for v in (None, str(Y), float(Y), True, 2101)),
           "해가 정수(2026~2100)가 아니면 400 (다섯 길)")
        ok(all(hit(edit(LEAD, u, grade="B"), 404, NOT_TARGET) and hit(submit(LEAD, u), 404, NOT_TARGET) and hit(seen(LEAD, u), 404, NOT_TARGET) for u in (HEAD, CEO, OFF, 999999))
           and hit(edit(LEAD, EMP, year=Y + 1, grade="B"), 404, NOT_TARGET) and hit(edit(LEAD, EMP, year=Y - 1, grade="B"), 404, NOT_TARGET),
           "평가 대상이 아니면 404 (본부장 · 대표이사 · 대상 꺼짐 · 없는 번호 · 회차 없는 해 · 포털 밖의 해)")

        # ---- 점수와_자동_등급 (팀장 → 본부 팀원 EMP: 가중치 40 · 30 · 30, 실행목표 1 · 2 · 4) ----
        ok(all(hit(edit(who, EMP, grade="B"), 403, CANT) for who in (EMP, FIELD, HEAD, CEO, HRA, LEADC)) and tg(EMP)[1] is None,
           "점수 단계 팀원은 평가자(팀장)만 고친다 — 본인 · 동료 · 배정자(본부장) · 대표이사 · HR · 다른 팀장 403")
        r = edit(LEAD, EMP, marks={"1": "A"})
        ok(r.status_code == 200 and r.json["ok"] is True and r.json["row"]["grade"] is None and r.json["row"]["total"] == 40.0 and ig(EMP) == ["A", None, None],
           "점수와_자동_등급: 항목 등급 하나 → 200 · 아직 다 안 차서 등급 없음 (총점 40.0)")
        r = edit(LEAD, EMP, marks={"2": "B", "4": "B"})
        ok(r.status_code == 200 and (r.json["row"]["grade"], r.json["row"]["total"], r.json["row"]["cap"]) == ("A", 94.0, "A") and tg(EMP)[1] == "A",
           "점수와_자동_등급: 다 고르면 등급 = 그 점수로 줄 수 있는 가장 높은 등급 (94.0점 → A)")
        ok(r.json["row"] == row_of(st(LEAD), EMP), "응답 row 는 상태 읽기의 그 줄과 같다 (_row_json)")
        ok(edit(LEAD, EMP, grade="B").json["row"]["grade"] == "B" and edit(LEAD, EMP, marks={"1": "A"}).json["row"]["grade"] == "B" and tg(EMP)[1] == "B",
           "점수와_자동_등급: 낮은 등급으로 고치면 그대로 · 같은 항목 등급을 또 보내도(바뀐 것 없음) 그대로")
        r = edit(LEAD, EMP, marks={"1": "S"})
        ok((r.json["row"]["grade"], r.json["row"]["total"]) == ("A", 98.0), "점수와_자동_등급: 항목 등급을 바꾸면 다시 점수에 맞춤 (98.0점 → A)")
        ok(hit(edit(LEAD, EMP, grade="S"), 400, over("98.0", "S", "100점 초과만")) and tg(EMP)[1] == "A",
           "점수와_자동_등급: 줄 수 있는 것보다 높은 등급 400 (문구 — 화면 evSubmitScore) · 그대로")
        ok(hit(edit(LEAD, EMP, marks={"1": "A"}, grade="S"), 400, over("94.0", "S", "100점 초과만")) and ig(EMP) == ["S", "B", "B"] and tg(EMP)[1] == "A",
           "항목 등급과 등급을 함께 보내도 하나라도 틀리면 아무것도 저장 안 함")
        ok(hit(edit(LEAD, EMP, bonus=3), 403, BONUS_NO) and hit(edit(LEAD, EMP, bonusReason="가상"), 403, BONUS_NO) and hit(edit(LEAD, EMP, bonus="x", grade="E"), 403, BONUS_NO)
           and hit(edit(LEAD, EMP, headComment="가상"), 403, HEAD_NO) and tg(EMP)[6:9] == ("", 0, ""),
           "점수와_자동_등급: 본부 팀원의 가점 · 사유 · 윗사람 코멘트는 팀장(평가자)에게 칸 자체가 403 (모양 400 보다 먼저)")
        bad = [({"marks": "A"}, MARKS), ({"marks": ["A"]}, MARKS), ({"marks": None}, MARKS), ({"marks": {"3": "A"}}, RID), ({"marks": {"9": "A"}}, RID), ({"marks": {"x": "A"}}, RID),
               ({"marks": {"1.0": "A"}}, RID), ({"marks": {"１": "A"}}, RID), ({"marks": {"1": "E"}}, ITEM_GRADE), ({"marks": {"1": "a"}}, ITEM_GRADE), ({"marks": {"1": 5}}, ITEM_GRADE),
               ({"marks": {"1": "B", "2": ""}}, ITEM_GRADE), ({"grade": "E"}, GRADE_BAD), ({"grade": ""}, GRADE_BAD), ({"grade": ["A"]}, GRADE_BAD), ({"grade": 1}, GRADE_BAD),
               ({"comment": "가" * 501}, CMT_LEN), ({"comment": 5}, CMT_TYPE), ({"comment": None}, CMT_TYPE), ({"comment": "가상\x00"}, CMT_CHAR), ({"comment": "가상\x07"}, CMT_CHAR)]
        ok(all(hit(edit(LEAD, EMP, **b), 400, m) for b, m in bad) and ig(EMP) == ["S", "B", "B"] and tg(EMP)[1] == "A" and tg(EMP)[5] == "",
           "점수와_자동_등급: 모양 400 — 항목 등급이 객체가 아님 · 없는 실행목표(이 사람 KPI 에 없는 3 · 숫자가 아닌 열쇠 · 전각 숫자) · 등급 아님 · 코멘트 501자 · 글이 아님 · 쓸 수 없는 글자, "
           "앞 칸이 맞아도 아무것도 저장 안 함")
        ok(edit(LEAD, EMP, comment="  " + "가" * 500 + " \n").status_code == 200 and tg(EMP)[5] == "가" * 500, "평가자 코멘트는 앞뒤 공백을 떼고 500자까지 · 뗀 글을 저장")
        ok(hit(edit(LEAD, FIELD, marks={"1": "A"}), 400, NO_KPI_MARKS) and hit(edit(LEAD, FIELD, marks={}), 400, NO_KPI_MARKS), "KPI_없는_사람은_등급만: 현장직에게 항목 등급(빈 것도) 400")
        # 팀장 묶음(LEAD — 평가자 본부장)은 평가자도 가점을 준다
        r = edit(HEAD, LEAD, marks={"1": "B", "2": "B", "3": "B"})
        ok((r.json["row"]["grade"], r.json["row"]["total"]) == ("B", 90.0), "(본부장이 팀장 LEAD 를 B · B · B → 90.0점 · 저절로 B)")
        ok(hit(edit(HEAD, LEAD, bonus=3), 400, REASON) and hit(edit(HEAD, LEAD, bonus=3, bonusReason="   "), 400, REASON) and tg(LEAD)[7] == 0,
           "점수와_자동_등급: 가점 3 + 사유 없음(공백만 포함) 400 — 팀장 묶음은 평가자도 가점을 준다")
        ok(all(hit(edit(HEAD, LEAD, bonus=v, bonusReason="가상"), 400, BONUS_BAD) for v in (10.5, 0.3, -0.5, True, "3", None, [3], 1e999))
           and hit(edit(HEAD, LEAD, bonus=1, bonusReason="가" * 101), 400, REASON_LEN) and tg(LEAD)[7:9] == (0, ""),
           "가점은 0~10 · 0.5 단위 · 숫자만(참/거짓 · 글자 · 무한 아님) · 사유 100자")
        ok(hit(edit(HEAD, LEAD, headComment="가상"), 403, HEAD_NO), "팀장 묶음에는 윗사람 코멘트 칸이 없다 (평가자에게도 403)")

        # ---- 점수보다_높은_등급은_못_준다 ----
        r = edit(LEAD, EMP, marks={"1": "B", "2": "B", "4": "B"})
        ok((r.json["row"]["total"], r.json["row"]["grade"]) == (90.0, "B"), "(EMP 를 B · B · B → 딱 90.0점 · 저절로 B — 기준은 '초과')")
        ok(hit(edit(LEAD, EMP, grade="A"), 400, over("90.0", "A", "A는 90점 초과")) and edit(LEAD, EMP, grade="B").status_code == 200 and tg(EMP)[1] == "B",
           "점수보다_높은_등급은_못_준다: 총점 90.0 인 사람에게 A 400 · B 200")
        r = edit(HEAD, LEAD, bonus=0.5, bonusReason=" 가상 가점 사유 ")
        ok((r.json["row"]["total"], r.json["row"]["grade"]) == (90.5, "A") and tg(LEAD)[7:9] == (1, "가상 가점 사유"),
           "가점 0.5(×2 정수 1 · 사유는 앞뒤 공백을 뗌) → 90.5점 · 점수 단계라 등급도 점수에 맞춤 A (화면 evSaveBonus → evFit)")
        ok(edit(HEAD, LEAD, grade="B").status_code == 200 and hit(edit(HEAD, LEAD, grade="A"), 200, {"ok": True, "row": row_of(st(HEAD), LEAD)}) and tg(LEAD)[1] == "A",
           "점수보다_높은_등급은_못_준다: 90.5 인 사람에게 A 200")
        put(EMP, leave_x2=10)
        ok(hit(edit(LEAD, EMP, grade="B"), 400, LEAVE) and edit(LEAD, EMP, grade="C").json["row"]["grade"] == "C", "점수보다_높은_등급은_못_준다: 연차 5일인 사람에게 B 400 · C 200")
        ok(edit(LEAD, EMP, marks={"1": "S"}).json["row"]["grade"] == "C", "연차 초과 5일 이상이면 저절로 나오는 등급도 C (98.0점이어도)")
        put(FIELD, leave_x2=10)
        ok(hit(edit(LEAD, FIELD, grade="A"), 400, LEAVE) and edit(LEAD, FIELD, grade="C").status_code == 200, "KPI 없는 사람도 연차 규칙은 본다 (A 400 · C 200)")
        put(EMP, leave_x2=0)
        put(FIELD, leave_x2=0, grade=None)

        # ---- 제출하면_떠_둔다 ----
        r = edit(LEAD, EMP, marks={"1": "A", "4": None}, comment="")
        ok(r.json["row"]["grade"] == "D" and r.json["row"]["total"] == 67.0, "(항목 하나를 비우면 점수가 67.0 으로 떨어져 등급도 D 로 내려감)")
        ok(hit(submit(LEAD, EMP), 400, FILL) and tg(EMP)[0] == "SCORE", "제출하면_떠_둔다: 항목 등급이 하나라도 비면 400")
        r = edit(LEAD, EMP, marks={"4": "B"}, comment=" \n ")
        ok(r.json["row"]["grade"] == "A" and tg(EMP)[5] == "" and hit(submit(LEAD, EMP), 400, NO_CMT), "제출하면_떠_둔다: 코멘트 없이(공백만) 400 (문구)")
        edit(LEAD, EMP, comment=" 가상 팀장 코멘트 ", grade="B")
        took()
        ok(hit(submit(LEAD, EMP), 200, OK), "제출하면_떠_둔다: 다 채우면 200")
        t = tg(EMP)
        ok(t[:4] == ("GRADE", "B", "B", 940) and json.loads(t[4]) == {"1": "A", "2": "B", "4": "B"} and t[5:] == ("가상 팀장 코멘트", "", 0, "", z(12, 7), LEAD, None),
           "제출하면_떠_둔다: 그대로 떠 둠 — proposed = 등급 B · 총점 ×10 940 · 항목 등급 · scored_at/by · 본부 팀원은 GRADE · 대신 입력 없음")
        ok(took() == [(HEAD, *TURN)], "제출하면_떠_둔다: 배정자(본부장) 휴대폰에 eval_turn 한 통")
        ok(hit(edit(LEAD, EMP, comment="가상 고친 코멘트"), 403, CANT) and hit(edit(LEAD, EMP, marks={"1": "S"}), 403, CANT) and hit(submit(LEAD, EMP), 403, NO_SCORE)
           and tg(EMP)[5] == "가상 팀장 코멘트" and ig(EMP) == ["A", "B", "B"] and not took(),
           "제출하면_떠_둔다: 낸 뒤 다시 고치기 · 내기 403 — 코멘트 · 항목 등급 그대로 (숨긴 줄이 낸 코멘트를 그대로 보인다) · 알림 없음")
        mine = row_of(st(LEAD), EMP)
        ok(mine["hidden"] is True and (mine["grade"], mine["subScore"], mine["comment"]) == ("B", 94.0, "가상 팀장 코멘트") and mine["can"]["score"] is False,
           "(낸 뒤 팀장에게는 낸 것만 — hidden · 낸 등급 · 낸 총점 · 낸 코멘트)")
        edit(HEAD, LEAD, comment="가상 본부장 평가")
        ok(hit(submit(HEAD, LEAD), 200, OK) and tg(LEAD)[:4] == ("CEO", "A", "A", 905) and took() == [(CEO, *TURN)],
           "제출하면_떠_둔다: 팀장은 CEO 단계로 (가점 포함 90.5 → 905) · 대표이사에게 알림")
        edit(HRL, HR, marks={"1": "B", "2": "B", "3": "B"}, comment="가상 HR팀장 평가")
        ok(hit(submit(HRL, HR), 200, OK) and tg(HR)[:4] == ("GRADE", "B", "B", 900) and took() == [(CEO, *TURN)],
           "제출하면_떠_둔다: 직속(HR 담당 — 평가자 HR 팀장)은 GRADE · 배정자 대표이사에게 알림")
        ok(audits("EVAL_SCORED") == [(EMP, {"year": Y}), (LEAD, {"year": Y}), (HR, {"year": Y})]
           and q("SELECT DISTINCT on_behalf_of FROM audit_logs WHERE action = 'EVAL_SCORED'") == [(None,)],
           "제출하면_떠_둔다: 감사로그 EVAL_SCORED 는 해만 (등급 · 점수 없음) · 대신이 아니면 on_behalf_of 없음")

        # ---- KPI_없는_사람은_등급만 ----
        ok(hit(edit(HEAD, FIELD, grade="B"), 403, CANT), "배정자는_배정_단계에서만: 점수 단계(SCORE) 사람은 본부장도 403")
        ok(edit(LEAD, FIELD, grade="B").status_code == 200 and hit(submit(LEAD, FIELD), 200, OK) and tg(FIELD)[:5] == ("GRADE", "B", "B", None, "{}") and tg(FIELD)[5] == "",
           "KPI_없는_사람은_등급만: 코멘트 없이 제출 200 · 총점 없음 · 항목 등급 {}")
        ok(took() == [(HEAD, *TURN)], "(현장직을 내도 배정자에게 알림)")
        ok(hit(submit(HEAD, NOCONF), 400, {"error": "등급 의견을 골라 주세요. 본부장이 보고 정합니다."}), "등급 없이 내면 400 (본부 팀원 문구)")
        ok(edit(HEAD, NOCONF, grade="C").status_code == 200 and hit(submit(HEAD, NOCONF), 200, OK) and tg(NOCONF)[0] == "GRADE" and not took(),
           "평가자가 배정자 본인이면(팀장 없는 팀 · 본부 직속) 내도 자기에게 알림이 가지 않음")

        # ---- 배정자는_배정_단계에서만 (본부장 → GRADE 인 EMP: 낸 것 B · 94.0) ----
        r = edit(HEAD, EMP, marks={"1": "C", "2": "C", "4": "C"})
        ok(r.status_code == 200 and (r.json["row"]["total"], r.json["row"]["grade"]) == (80.0, "C"),
           "배정자는_배정_단계에서만: 본부장이 항목 등급을 고침 200 · 고친 점수(80.0)로는 B 를 못 줘 등급이 C 로 내려감")
        ok(hit(edit(HEAD, EMP, marks={"1": None}), 400, PICK) and hit(edit(HEAD, EMP, marks={"2": "B", "4": None}), 400, PICK) and ig(EMP) == ["C", "C", "C"],
           "배정 단계에서는 항목 등급을 비울(null) 수 없다 400 · 다른 칸도 그대로 (비우기는 평가자의 점수 단계 초안만)")
        r = edit(HEAD, EMP, bonus=1.5, bonusReason="가상 가점 사유", headComment=" 가상 본부장 코멘트 ")
        ok(r.status_code == 200 and (r.json["row"]["total"], r.json["row"]["grade"]) == (81.5, "C") and tg(EMP)[6:9] == ("가상 본부장 코멘트", 3, "가상 가점 사유"),
           "배정자는_배정_단계에서만: 가점(×2 정수 3) · 사유 · 윗사람 코멘트 200 · 배정 단계에서는 점수가 올라도 등급을 올리지 않음")
        ok(all(hit(edit(HEAD, EMP, headComment=v), 400, m) for v, m in (("가" * 501, HEAD_LEN), (["가상"], {"error": "윗사람 코멘트는 글로 써 주세요."})))
           and tg(EMP)[6] == "가상 본부장 코멘트", "윗사람 코멘트 501자 · 글이 아님 400")
        ok(hit(edit(HEAD, EMP, comment="가상"), 403, CMT_NO) and tg(EMP)[5] == "가상 팀장 코멘트", "평가자 코멘트는 점수 단계에서만 — 배정자도 403 · 낸 그대로")
        ok(row_of(st(LEAD), EMP)["edited"] == "", "배정 단계에서는 아직 '고쳤다'가 아니다 (edited '' — 마감한 뒤에 알린다)")
        ok(hit(edit(HEAD, FIELD, bonus=1, bonusReason="가상"), 400, NO_KPI_BONUS) and edit(HEAD, FIELD, grade="C").status_code == 200 and tg(FIELD)[1] == "C",
           "KPI 없는 사람에게 가점 400 · 등급은 고침")
        ok(hit(edit(CEO, EMP, grade="D"), 403, CANT) and hit(edit(HRA, EMP, grade="D"), 403, CANT) and tg(EMP)[1] == "C",
           "배정자는_배정_단계에서만: 대표이사 · HR 이 본부장이 있는 본부 팀원을 고침 403 (돌려보내기만 — ②)")
        x("INSERT INTO user_roles (user_id, role) VALUES (?, 'DIVISION_HEAD')", HEAD2)
        x("UPDATE users SET led_department_id = 94 WHERE id = ?", HEAD2)  # 다른 본부(94)의 본부장 — 그 본부 묶음은 본부장이 없던 때 떠 둬 배정자가 대표이사
        ok(hit(edit(HEAD2, EMP, grade="D"), 403, CANT) and hit(edit(HEAD2, EMPC, grade="D"), 403, CANT), "배정자는_배정_단계에서만: 다른 본부장 403 (떠 둔 배정자가 아님)")

        # ---- 대표이사는_팀장과_직속을 ----
        r = edit(CEO, LEAD, grade="B", bonus=1, bonusReason="가상 대표 가점")
        ok(r.status_code == 200 and (tg(LEAD)[1], tg(LEAD)[7], r.json["row"]["total"]) == ("B", 2, 91.0), "대표이사는_팀장과_직속을: 대표이사 확인(CEO) 단계 팀장의 등급 · 가점 200")
        ok(hit(edit(CEO, LEAD, marks={"2": None}), 400, PICK) and ig(LEAD) == ["B", "B", "B"], "대표이사 확인 단계(팀장의 배정 단계)도 항목 등급을 비울 수 없다 400")
        ok(hit(edit(CEO, LEAD, headComment="가상"), 403, HEAD_NO) and hit(edit(CEO, LEAD, comment="가상"), 403, CMT_NO) and tg(LEAD)[5] == "가상 본부장 평가",
           "팀장 묶음: 윗사람 코멘트 칸 없음 · 평가자 코멘트는 낸 그대로 403")
        r = edit(CEO, HR, grade="C", headComment="가상 대표 코멘트")
        ok(r.status_code == 200 and (tg(HR)[1], tg(HR)[6]) == ("C", "가상 대표 코멘트"), "대표이사는_팀장과_직속을: 직속 팀원(GRADE)의 등급 · 윗사람 코멘트 200")
        put(HRL, stage="SCORE")
        r = edit(CEO, HRL, marks={"1": "A", "2": "A", "3": "A"}, comment="가상 대표 평가", grade=None)
        ok(r.status_code == 200 and (r.json["row"]["grade"], r.json["row"]["total"]) == (None, 100.0), "등급을 함께 보내면 그 값이 이긴다 (null — 저절로 정하지 않음)")
        ok(hit(submit(CEO, HRL), 400, {"error": "등급을 골라 주세요. 대표이사가 보고 확인합니다."}), "등급 없이 내면 400 (팀장 문구)")
        ok(edit(CEO, HRL, grade="A").status_code == 200 and hit(submit(CEO, HRL), 200, OK) and tg(HRL)[:4] == ("CEO", "A", "A", 1000) and not took(),
           "대표이사는_팀장과_직속을: 대표이사가 평가자인 직속 팀장(SCORE) 채점 · 제출 200 → CEO 단계 · 다음 차례도 자기라 알림 없음")

        # ---- 대신_입력은_맡긴_동안_본인_것_빼고 (LEADC — 평가자 대표이사) ----
        put(LEADC, stage="SCORE")
        ok(hit(edit(HR, LEADC, marks={"1": "B"}), 403, CANT) and hit(edit(HRA, LEADC, marks={"1": "B"}), 403, CANT) and ig(LEADC) == [None, None, None],
           "대신_입력은_맡긴_동안_본인_것_빼고: 맡기기 전 HR 이 대표이사가 평가자인 팀장 채점 403")
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 7))
        r = edit(HR, LEADC, marks={"1": "B", "2": "B", "3": "B"}, comment="가상 대신 평가")
        ok(r.status_code == 200 and r.json["row"]["proxy"] == "가상EHR담당 가상직급" and r.json["row"]["can"]["behalf"] is True and tg(LEADC)[11] == HR and ig(LEADC) == ["B"] * 3,
           "대신_입력은_맡긴_동안_본인_것_빼고: 맡긴 뒤 200 · proxy '이름 직급'")
        ok(hit(edit(HR, HR, grade="D"), 403, CANT) and tg(HR)[1] == "C", "대신_입력은_맡긴_동안_본인_것_빼고: HR 담당자 본인 줄(직속 · 배정은 대표이사 몫)은 맡겨도 403")
        n_audit = len(audits("EVAL_SCORED"))
        ok(hit(submit(HR, LEADC), 200, OK) and tg(LEADC)[:3] == ("CEO", "B", "B") and tg(LEADC)[10:] == (HR, HR), "대신 제출 200 → scored_by · proxy_by = 대신 넣은 HR")
        ok(q("SELECT actor_user_id, on_behalf_of, after_data FROM audit_logs WHERE action = 'EVAL_SCORED' ORDER BY id")[n_audit:] == [(HR, CEO, json.dumps({"year": Y}))],
           "감사로그 EVAL_SCORED: 넣은 사람 HR · on_behalf_of = 대표이사")
        ok(took() == [(HRA, *TURN), (HRL, *TURN)], "대신 낸 다음 차례(대표이사 확인)는 맡겼으니 HR 전원 — 낸 HR 본인 · 대표이사는 빼고")
        ok(edit(HR, LEADC, grade="C").status_code == 200 and tg(LEADC)[1] == "C", "맡긴 동안은 대표이사 확인 단계 팀장의 등급도 HR 이 대신 고침")
        # 자기가 든 묶음의 사람도 대신 못 한다 — 같은 정원을 나눠 쓰는 동료의 등급이 자기 등급에 닿는다
        put(EXEC, stage="GRADE", proposed="D")
        ok(hit(edit(HR, EXEC, grade="D"), 403, CANT) and tg(EXEC)[1] is None and row_of(st(HR), EXEC)["can"]["assign"] is False,
           "대신_입력은_자기_묶음_빼고: HR 담당자(직속 묶음)는 같은 직속 묶음 사람(임원 소속 — 배정자 대표이사)의 등급을 맡겨도 403 · can.assign 거짓")
        ok(hit(edit(HRL, LEAD, grade="C"), 403, CANT) and tg(LEAD)[1] == "B" and row_of(st(HRL), LEAD)["can"]["assign"] is False,
           "대신_입력은_자기_묶음_빼고: HR 팀장(전사 팀장 묶음)은 다른 팀장의 대표이사 확인 단계 등급을 맡겨도 403 · can.assign 거짓")
        r = edit(HRL, EXEC, grade="D")
        ok(r.status_code == 200 and r.json["row"]["can"]["behalf"] is True and tg(EXEC)[1] == "D" and tg(EXEC)[11] == HRL
           and edit(HR, LEAD, grade="B").status_code == 200 and tg(LEAD)[11] == HR,
           "대신_입력은_자기_묶음_빼고: 그 묶음에 들지 않은 맡은 HR 은 200 — HR 팀장이 직속 묶음을 · HR 담당자가 전사 팀장 묶음을 (proxy)")
        put(EXEC, stage="SCORE", proposed=None)
        ok(hit(edit(HR, EXEC, marks={"1": "B"}), 403, CANT) and edit(HRL, EXEC, marks={"1": "B"}).status_code == 200 and ig(EXEC) == ["B", None, None],
           "대신_입력은_자기_묶음_빼고: 점수도 같다 — 대표이사가 평가자인 같은 묶음 사람 채점 403 · 다른 묶음 HR 200")
        put(EXEC, stage="SELF", grade=None, proxy_by=None)
        setg(EXEC, None, None, None)
        put(HR, stage="SCORE")
        r = edit(HRL, HR, marks={"1": "A"})
        ok(r.status_code == 200 and r.json["row"]["proxy"] == "" and r.json["row"]["can"]["behalf"] is False and tg(HR)[11] is None,
           "대신_입력은_맡긴_동안_본인_것_빼고: HR 팀장이 HR 담당자를 채점 → proxy '' (맡김 중이어도 평가자 본인 일)")
        ok(hit(submit(HRL, HR), 200, OK) and tg(HR)[0] == "GRADE" and tg(HR)[11] is None and took() == [(HRA, *TURN)],
           "맡긴 동안 직속 팀원을 내면 다음 차례(대표이사 몫)는 HR 전원 — 평가받는 HR 담당자 본인 · 낸 HR 팀장은 빼고")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        ok(hit(edit(HR, LEADC, grade="B"), 403, CANT) and tg(LEADC)[1] == "C", "대신_입력은_맡긴_동안_본인_것_빼고: 맡김을 끄면 다음 요청 403")

        # ---- 마감은_전원_배정과_정원 (DIV:91 — EMP · FIELD · EMPB · NOCONF · DIVD 다섯, 정원 S 1 · A 1 · B 1 · D 1) ----
        ok(all(hit(close(HEAD, v), 400, DIV_ONLY) for v in ("LEADERS", "DIRECT", None, 91, ["DIV:91"], "div:91")) and hit(close(HEAD, "DIV:999"), 404, NO_POOL),
           "마감은_전원_배정과_정원: 팀장 · 직속 묶음 열쇠 · 열쇠가 아님 400 · 없는 묶음 404")
        ok(all(hit(close(who, "DIV:91"), 403, NO_CLOSE) for who in (HEAD2, LEAD, CEO, HRA, EMP)) and hit(close(HEAD, "DIV:94"), 403, NO_CLOSE),
           "마감은_전원_배정과_정원: 다른 본부장 · 팀장 · 대표이사(본부장이 있는 본부) · HR · 직원 403 · 본부장도 남의 본부 403")
        ok(hit(close(HEAD, "DIV:91"), 409, EARLY) and pool_row("DIV:91")[0] is None and tg(EMP)[0] == "GRADE",
           "마감은_전원_배정과_정원: 배정 단계(GRADE)가 아닌 사람(자기평가 중 둘)이 있으면 409 · 그대로")
        for uid in (EMPB, DIVD):
            setg(uid, "B", "B", "B")  # 90.0점
            put(uid, stage="GRADE", proposed="C", grade="C")
        put(NOCONF, grade="B")  # B 가 둘 (FIELD 는 C 로 고쳤으니 다시 B)
        put(FIELD, grade="B")
        r = close(HEAD, "DIV:91")
        ok(r.status_code == 409 and r.json == {"error": "B는 1명까지입니다. 현재 2명", "errors": ["B는 1명까지입니다. 현재 2명"]} and pool_row("DIV:91")[0] is None,
           "마감은_전원_배정과_정원: 정원 초과 409 (오류 목록 · 첫 오류가 error) · 그대로")
        put(NOCONF, grade=None)
        r = close(HEAD, "DIV:91")
        ok(r.status_code == 409 and r.json["errors"] == ["가상E확정없음 님의 등급이 없습니다."], "등급이 빈 사람이 있어도 409 (마감은 전원 검사)")
        x("UPDATE ev_rounds SET released_at = ? WHERE year = ?", z(12, 7), Y)
        ok(hit(close(HEAD, "DIV:91"), 409, RELEASED) and hit(close(LEAD, "DIV:91"), 403, NO_CLOSE), "결과를 공개한 뒤는 409 (권한이 없는 사람은 403 이 먼저)")
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)
        ok(edit(HEAD, NOCONF, grade="C").status_code == 200, "(본부장이 정원에 맞춤 — B 1 · C 4)")
        x("UPDATE ev_pools SET back_at = ?, back_by = ?, back_behalf = ?, back_reason = '가상 돌려보낸 이유' WHERE year = ? AND pool = 'DIV:91'", z(12, 6), HRA, CEO, Y)
        took()
        n_closed = len(audits("EVAL_POOL_CLOSED"))
        ok(hit(close(HEAD, "DIV:91"), 200, OK) and [tg(u)[0] for u in (EMP, FIELD, EMPB, NOCONF, DIVD)] == ["CEO"] * 5
           and pool_row("DIV:91") == (z(12, 7), HEAD, "[]", None, None, None, None),
           "마감은_전원_배정과_정원: 미달(A · D 0명)은 경고뿐 → 마감 200 · 모두 CEO · closed_at/by · 돌려보낸 기록 비움 · hr_seen []")
        ok(took() == [(HRA, *TURN), (HRL, *TURN), (HR, *TURN), (CEO, *TURN)], "마감은_전원_배정과_정원: 대표이사 · HR 전원에게 알림 한 통씩")
        ok(audits("EVAL_POOL_CLOSED")[n_closed:] == [(Y, {"year": Y, "pool": "DIV:91"})] and q("SELECT on_behalf_of FROM audit_logs WHERE action = 'EVAL_POOL_CLOSED'") == [(None,)],
           "감사로그 EVAL_POOL_CLOSED (해 · 묶음 열쇠만)")
        ok(hit(close(HEAD, "DIV:91"), 409, DONE_ALREADY) and not took() and len(audits("EVAL_POOL_CLOSED")) == n_closed + 1, "또 마감 409 '이미 마감했습니다.' · 알림 · 감사로그 없음")
        p91 = pool_of(HEAD, "DIV:91")
        ok(p91["state"] == "CEO" and p91["log"]["closed"] == {"at": z(12, 7), "by": "가상E본부장 가상직급"} and p91["log"]["back"] is None, "(상태 읽기: 묶음 CEO · 마감 기록 · 돌려보낸 기록 없음)")

        # ---- 고친_것은_사실만_알린다 (본부장이 EMP 를 94.0 · B → 81.5 · C 로 고쳐 마감) ----
        lr = row_of(st(LEAD), EMP)
        ok(lr["edited"] is True and lr["editSeen"] is False and (lr["grade"], lr["total"]) == ("B", 94.0), "고친_것은_사실만_알린다: 마감 뒤 팀장 응답 edited 참 · editSeen 거짓 · 등급 · 총점은 낸 그대로")
        text = json.dumps(st(LEAD), ensure_ascii=False)  # 응답은 \uXXXX 로 오므로 풀어서 본다
        ok("→" not in text and "81.5" not in text and "가상 본부장 코멘트" not in text and "가상 가점 사유" not in text
           and all(t in json.dumps(st(HEAD), ensure_ascii=False) for t in ("→", "81.5", "가상 본부장 코멘트", "가상 가점 사유")),
           "고친_것은_사실만_알린다: 공개 전 팀장 응답 어디에도 고친 내용 글자(→ · 고친 총점 · 윗사람 코멘트 · 가점 사유)가 없음 (본부장 응답에는 있음 — 훑기가 잡는다)")
        ok(all(hit(seen(who, EMP), 403, NOT_RATER) for who in (HEAD, EMP, HRA, CEO)), "확인(seen)은 평가자 본인만 — 배정자 · 본인 · HR · 대표이사 403")
        todo0 = st(LEAD)["todo"]["me"]
        ok(hit(seen(LEAD, EMP), 200, {"ok": True, "editSeen": True}) and q("SELECT edit_seen FROM ev_targets WHERE year = ? AND user_id = ?", Y, EMP) == [("총점 94.0 → 81.5 · 등급 B → C",)]
           and row_of(st(LEAD), EMP)["editSeen"] is True and st(LEAD)["todo"]["me"] == todo0 - 1,
           "고친_것은_사실만_알린다: seen → 응답은 editSeen 참뿐 (고친 내용 글자는 서버가 들고 있음) · 할 일 하나 줄어듦")
        ok(hit(seen(LEAD, FIELD), 200, {"ok": True, "editSeen": False}), "고친 것이 없는 사람을 확인하면 editSeen 거짓")
        x("UPDATE ev_items SET grade = 'B' WHERE year = ? AND user_id = ? AND rid = 2", Y, EMP)  # 본부장이 또 고쳤다 (①에는 마감 뒤 고치는 길이 없어 DB 로 — 고친 내용이 바뀜)
        lr = row_of(st(LEAD), EMP)
        ok(lr["edited"] is True and lr["editSeen"] is False and st(LEAD)["todo"]["me"] == todo0, "고친_것은_사실만_알린다: 본부장이 또 고치면 다시 거짓 · 할 일 다시 셈")
        x("UPDATE ev_items SET grade = 'C' WHERE year = ? AND user_id = ? AND rid = 2", Y, EMP)

        # ---- HR_마감_알림_확인 ----
        a0, b0 = st(HRA)["todo"]["admin"], st(HRL)["todo"]["admin"]
        ok(pool_of(HRA, "DIV:91")["hrSeen"] is False and pool_of(HRL, "DIV:91")["hrSeen"] is False, "HR_마감_알림_확인: 마감 뒤 HR 둘 다 hrSeen 거짓")
        ok(hit(pseen(HEAD, "DIV:91"), 403, HR_ONLY) and all(hit(pseen(HRA, v), 404, NO_POOL) for v in ("DIV:999", None, 91, ["DIV:91"])), "마감 확인은 HR 만 403 · 없는 묶음 404")
        ok(hit(pseen(HRA, "DIV:91"), 200, OK) and hit(pseen(HRA, "DIV:91"), 200, OK) and pool_row("DIV:91")[2] == json.dumps([HRA]), "HR_마감_알림_확인: seen 200 · 또 눌러도 한 번만 들어감")
        ok(pool_of(HRA, "DIV:91")["hrSeen"] is True and st(HRA)["todo"]["admin"] == a0 - 1 and pool_of(HRL, "DIV:91")["hrSeen"] is False and st(HRL)["todo"]["admin"] == b0,
           "HR_마감_알림_확인: 그 HR 만 hrSeen 참 · todo.admin 1 줄어듦 · 다른 HR 은 그대로")

        # ---- 마감은_한_번 (같은 묶음을 GRADE 로 되돌려 두 요청이 동시에) ----
        x("UPDATE ev_targets SET stage = 'GRADE' WHERE year = ? AND pool = 'DIV:91' AND out_at IS NULL", Y)
        x("UPDATE ev_pools SET closed_at = NULL, closed_by = NULL WHERE year = ? AND pool = 'DIV:91'", Y)
        twins = [app.test_client() for _ in range(2)]
        for t in twins:
            login(t, new_token(f"HW-{HEAD}"))
        n_closed, codes, gate = len(audits("EVAL_POOL_CLOSED")), [], threading.Barrier(2)
        took()

        def race(cl):
            gate.wait()
            codes.append(cl.post("/api/eval/pools/close", base_url=BASE, headers=ORIGIN, json={"year": Y, "pool": "DIV:91"}).status_code)

        threads = [threading.Thread(target=race, args=(t,)) for t in twins]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        ok(sorted(codes) == [200, 409] and len(audits("EVAL_POOL_CLOSED")) == n_closed + 1 and took() == [(HRA, *TURN), (HRL, *TURN), (HR, *TURN), (CEO, *TURN)]
           and [tg(u)[0] for u in (EMP, FIELD, EMPB, NOCONF, DIVD)] == ["CEO"] * 5, f"마감은_한_번: 두 요청이 동시에 마감 → 하나 200 · 하나 409 · 감사로그 하나 · 알림은 한 사람에 한 통씩 ({codes})")

        # ---- 본부장_없는_본부는_대표이사가 (DIV:94 — EMPC, 평가자 팀장C, 배정자 대표이사) ----
        put(EMPC, out_at=z(12, 7), out_reason="가상 휴직", out_by=HRA)  # 한 사람뿐인 묶음에서 그 사람을 빼면 평가할 사람이 없다
        ok(hit(close(HEAD, "DIV:94"), 403, NO_CLOSE) and hit(close(CEO, "DIV:94"), 409, EMPTY_POOL) and pool_row("DIV:94")[0] is None and not took(),
           "평가할 사람이 없는 묶음(모두 제외)은 마감 409 · 기록 · 알림 없음 (권한이 없는 사람은 403 이 먼저)")
        x("UPDATE ev_rounds SET released_at = ? WHERE year = ?", z(12, 7), Y)
        ok(hit(close(CEO, "DIV:94"), 409, EMPTY_POOL), "(빈 묶음 409 는 공개 뒤 409 보다 먼저)")
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)
        put(EMPC, out_at=None, out_reason=None, out_by=None)
        r = edit(LEADC, EMPC, marks={"1": "B", "2": "B", "3": "B"}, comment="가상 팀장C 코멘트")
        ok(r.json["row"]["grade"] == "B" and hit(submit(LEADC, EMPC), 200, OK) and tg(EMPC)[0] == "GRADE" and took() == [(CEO, *TURN)],
           "본부장 없는 본부 팀원을 내면 배정자(대표이사)에게 알림")
        ok(hit(close(HEAD, "DIV:94"), 403, NO_CLOSE) and hit(close(HRA, "DIV:94"), 403, NO_CLOSE) and hit(close(HEAD2, "DIV:94"), 403, NO_CLOSE),
           "(그 본부의 지금 본부장 · 맡기기 전 HR 은 403 — 배정자는 떠 둔 대로 대표이사)")
        r = edit(CEO, EMPC, grade="C", headComment="가상 대표 코멘트")
        ok(r.status_code == 200 and tg(EMPC)[1] == "C", "본부장_없는_본부는_대표이사가: 대표이사가 GRADE 사람을 고침 200")
        ok(hit(close(CEO, "DIV:94"), 200, OK) and tg(EMPC)[0] == "CEO" and pool_row("DIV:94")[:2] == (z(12, 7), CEO) and took() == [(HRA, *TURN), (HRL, *TURN), (HR, *TURN)],
           "본부장_없는_본부는_대표이사가: 대표이사 마감 200 → HR 전원에게 알림 (마감한 대표이사 본인은 빼고)")
        put(EMPC, stage="GRADE")
        x("UPDATE ev_pools SET closed_at = NULL, closed_by = NULL WHERE year = ? AND pool = 'DIV:94'", Y)
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 7))
        r = edit(HR, EMPC, headComment="가상 대신 코멘트")
        ok(r.status_code == 200 and r.json["row"]["proxy"] == "가상EHR담당 가상직급" and tg(EMPC)[11] == HR, "맡김 켠 HR 이 본부장 없는 본부 팀원을 대신 고침 (proxy)")
        ok(hit(close(HR, "DIV:94"), 200, OK) and tg(EMPC)[0] == "CEO" and pool_row("DIV:94")[:2] == (z(12, 7), HR) and took() == [(HRA, *TURN), (HRL, *TURN)],
           "본부장_없는_본부는_대표이사가: 맡김 켠 HR 도 마감 200 (묶음에 본인이 없을 때) → 맡겼으니 HR 전원 (본인 빼고 · 대표이사 없음)")
        ok(q("SELECT actor_user_id, on_behalf_of FROM audit_logs WHERE action = 'EVAL_POOL_CLOSED' ORDER BY id")[-2:] == [(CEO, None), (HR, CEO)],
           "감사로그: 대표이사 마감은 on_behalf_of 없음 · HR 이 대신 마감하면 on_behalf_of = 대표이사")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)

        # ---- 감사로그 · 알림 글 ----
        logs = q("SELECT action, after_data, before_data, reason FROM audit_logs WHERE id > ? AND action GLOB 'EVAL_*'", audit0)
        ok({a for a, *_ in logs} == {"EVAL_SCORED", "EVAL_POOL_CLOSED"} and all(set(json.loads(a)) <= {"year", "pool"} and b is None and rs is None for _, a, b, rs in logs)
           and not any(t in a for _, a, _, _ in logs for t in ("가상", "코멘트", '"A"', '"B"', '"C"', '"D"', "81.5", "94.0", "940")),
           "감사로그에_숫자와_글이_없다: 이 검사가 남긴 EVAL_* 는 낸 것 · 마감뿐 (고치기 · 확인은 남기지 않음) · after_data 는 해 · 묶음 열쇠뿐 — 등급 · 점수 · 글 없음")
        ok(texts and set(texts) == {"인사평가에서 처리할 일이 있어요 PC에서 확인해 주세요"}, "알림 글은 모두 같은 한 줄 — 등급 · 이름 없음")
    finally:
        auth._now = real_now
        push.SYNC, push.SEND = real_sync, real_send
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for h in hashes:
            x("DELETE FROM push_subscriptions WHERE token_hash = ?", h)
            x("DELETE FROM sessions WHERE token_hash = ?", h)
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        x("DELETE FROM user_roles WHERE user_id = ? AND role = 'DIVISION_HEAD'", HEAD2)
        x("UPDATE users SET led_department_id = NULL WHERE id = ?", HEAD2)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x(POOLS_RESET, Y)
        x("UPDATE ev_rounds SET released_at = NULL WHERE year = ?", Y)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)

def check_eval_close(hr) -> None:
    """인사평가 대표이사 확인 · 돌려보내기 · HR 확정 · 되돌리기 · 결과 공개 (2026-10-27, 평가 3단계 ②): 대표이사(맡겼으면 그 묶음에 들지 않은 HR)가 묶음째로 확인해 HR 확정으로 넘기고,
    본부 팀원 묶음은 이유를 적어 배정자(본부장)에게 돌려보낸다. HR 이 확정하고(자기가 든 묶음도 — HR 본인 일), 공개 전에는 한 단계씩 되돌린다. 모두 확정되면 공개 — 제외 안 된 대상에게 알림 · 그 뒤 등급이 보인다.
    목록 요청은 하나라도 걸리면 아무것도 안 바뀌고, 두 번 누르면 두 번째는 409 (감사로그 · 알림 한 번). 감사로그에는 해 · 묶음 열쇠 · 되돌린 단계 · 사람 수뿐 (돌려보낸 이유 · 등급 없음).
    check_eval_score 가 되돌려 둔 회차와 가상 조직(부서 91~97 · 사람 101~116)을 쓴다 — 묶음을 대표이사 확인 직전으로 바로 넣고(마감 길은 check_eval_score 가 본다) 그 뒤는 실제 길로 간다.
    끝에 시계 · 알림 · 대표이사 · 맡김 · 근태 마침 · 묶음 기록 · 단계 · 등급 · 공개를 되돌린다."""
    import evaluation
    import push
    print("인사평가 대표이사 확인 · 돌려보내기 · HR 확정 · 되돌리기 · 결과 공개")
    HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO = range(101, 113)
    NOCONF, HRA = 115, 1  # HRA = 평가 대상이 아닌 HR (처음 검사의 인사담당자)
    D1, D2, LD, DR = "DIV:91", "DIV:94", "LEADERS", "DIRECT"  # 본부 팀원(본부장 있음) · 본부 팀원(본부장 없음 — 배정자 대표이사) · 전사 팀장 · 직속부서 팀원
    Y, real_now, real_send, real_sync = evaluation.FIRST_YEAR, auth._now, push.SEND, push.SYNC
    z = lambda m, d, hh=3: f"{Y}-{m:02d}-{d:02d}T{hh:02d}:00:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    get = lambda who, path: c(who).get(path, base_url=BASE)  # noqa: E731
    post = lambda who, path, body: c(who).post(path, base_url=BASE, headers=ORIGIN, json=body)  # noqa: E731
    hit = lambda r, code, body: r.status_code == code and r.json == body  # noqa: E731
    st = lambda who: get(who, "/api/eval/state").json  # noqa: E731
    row_of = lambda s, uid: next((p for p in s["people"] if p["pid"] == uid), None)  # noqa: E731
    pool_of = lambda who, k: next(p for p in st(who)["pools"] if p["key"] == k)  # noqa: E731
    cans = lambda who, what: {p["key"]: p["can"][what] for p in st(who)["pools"]}  # noqa: E731
    act = lambda who, what, **body: post(who, f"/api/eval/pools/{what}", {"year": Y, **body})  # noqa: E731
    release = lambda who, **over: post(who, "/api/eval/admin/release", {"year": Y, **over})  # noqa: E731
    put = lambda uid, **cols: x(f"UPDATE ev_targets SET {', '.join(f'{k} = ?' for k in cols)} WHERE year = ? AND user_id = ?", *cols.values(), Y, uid)  # noqa: E731
    stages = lambda k: [s for (s,) in q("SELECT stage FROM ev_targets WHERE year = ? AND pool = ? AND out_at IS NULL ORDER BY user_id", Y, k)]  # noqa: E731
    prow = lambda k: q("SELECT closed_at, closed_by, ceo_at, ceo_by, ceo_behalf, back_at, back_by, back_behalf, back_reason, undo_at, undo_by, undo_from"  # noqa: E731
                       " FROM ev_pools WHERE year = ? AND pool = ?", Y, k)[0]
    audits = lambda action: [(e, json.loads(a)) for e, a in q("SELECT entity_id, after_data FROM audit_logs WHERE action = ? ORDER BY id", action)]  # noqa: E731
    by = lambda action: q("SELECT actor_user_id, on_behalf_of FROM audit_logs WHERE action = ? ORDER BY id", action)  # noqa: E731

    def setg(uid, *grades):  # 실행목표 등급을 rid 차례로 바로 넣는다
        for (rid,), v in zip(q("SELECT rid FROM ev_items WHERE year = ? AND user_id = ? ORDER BY rid", Y, uid), grades):
            x("UPDATE ev_items SET grade = ? WHERE year = ? AND user_id = ? AND rid = ?", v, Y, uid, rid)

    def ready(k, stage):  # 묶음 사람(제외 안 된)을 그 단계로 — 실행목표 B · B · B (90.0점) · 낸 등급 = 등급 C (정원 · 점수 기준에 맞음)
        for (uid,) in q("SELECT user_id FROM ev_targets WHERE year = ? AND pool = ? AND out_at IS NULL", Y, k):
            setg(uid, "B", "B", "B")
            put(uid, stage=stage, proposed="C", grade="C")

    sent: list[tuple[int, str, str]] = []
    texts: list[str] = []
    owner: dict[str, int] = {}

    def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 둔다 (받은 사람 · 제목 · 열 화면) — 글은 따로 모아 등급 · 이름이 없는지 본다
        sent.append((owner[info["endpoint"]], payload["title"], payload["url"]))
        texts.append(f"{payload['title']} {payload['body']}")
        return 201

    def took():
        out = sorted(sent)
        sent.clear()
        return out

    TURN, OPEN = ("인사평가에서 처리할 일이 있어요", "/?go=perf.e2"), ("인사평가 결과가 공개됐어요", "/?go=perf.e3")
    OK = {"ok": True}
    BAD_YEAR, HR_ONLY, RELEASED = {"error": "해가 올바르지 않습니다."}, {"error": "HR만 할 수 있습니다."}, {"error": "결과를 공개한 평가는 바꿀 수 없습니다."}
    PICK, NO_POOL, NOT_DONE = {"error": "묶음을 골라 주세요."}, {"error": "묶음을 찾을 수 없습니다."}, {"error": "아직 확정되지 않은 분이 있습니다."}
    WHY, WHY_CHAR = {"error": "돌려보내는 이유를 1~200자로 써 주세요."}, {"error": "돌려보내는 이유에 쓸 수 없는 글자가 있습니다."}
    SEEN = {"error": "되돌릴 단계(from: HR · DONE)를 보내 주세요."}
    no = lambda name, verb: {"error": f"{name} 묶음을 {verb} 권한이 없습니다."}  # noqa: E731
    is_ = lambda name, why: {"error": f"{name} 묶음은 {why}"}  # noqa: E731
    N1, N2, NL, ND = "가상E본부 팀원", "가상E본부2 팀원", "전사 팀장", "직속부서 팀원"
    RESET = ("UPDATE ev_targets SET stage = CASE WHEN kpi_version IS NULL THEN 'SCORE' ELSE 'SELF' END, proposed = NULL, grade = NULL, sub_score_x10 = NULL,"
             " sub_marks = NULL, scored_at = NULL, scored_by = NULL, comment = '', head_comment = '', bonus_x2 = 0, bonus_reason = '', proxy_by = NULL, edit_seen = NULL,"
             " late = 0, leave_x2 = 0, out_at = NULL, out_reason = NULL, out_by = NULL, no_self_at = NULL, no_self_by = NULL, self_at = NULL WHERE year = ?")
    POOLS_RESET = ("UPDATE ev_pools SET closed_at = NULL, closed_by = NULL, hr_seen = '[]', ceo_at = NULL, ceo_by = NULL, ceo_behalf = NULL, back_at = NULL, back_by = NULL,"
                   " back_behalf = NULL, back_reason = NULL, undo_at = NULL, undo_by = NULL, undo_from = NULL WHERE year = ?")

    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    att0 = q("SELECT att_done_at FROM ev_rounds WHERE year = ?", Y)[0][0]
    env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    hashes: list[str] = []
    try:
        for uid in other_ceos:  # 이 가상 조직의 대표이사는 check_eval_admin 이 만든 한 명뿐이게
            x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", CEO)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x(POOLS_RESET, Y)
        x("UPDATE ev_rounds SET released_at = NULL, released_by = NULL, att_done_at = ? WHERE year = ?", z(12, 2), Y)
        pub, priv = push.keygen()
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        push.SYNC, push.SEND = True, fake_send
        for uid in (HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO, NOCONF, HRA):  # 휴대폰을 등록한 사람 — 대상 전원(포털을 안 쓰는 임원 소속 포함) · 본부장 · 대표이사 · HR
            h, ep = f"{uid:03d}" + "8" * 61, f"https://push.example.invalid/close-{uid}"
            x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')", h, uid, z(11, 30), z(11, 30), z(12, 31))
            x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, z(11, 30))
            owner[ep] = uid
            hashes.append(h)
        t = datetime(Y, 12, 7, 3, tzinfo=timezone.utc)  # 한국 12/7(화) 정오 — 근무시간이라 알림이 간다
        auth._now = lambda: t
        # 대표이사 확인 직전: 본부 팀원 둘 · 전사 팀장은 모두 CEO(마감함), 직속부서 팀원은 GRADE 인데 한 사람 등급이 빠져 FIX. 본부 직속은 제외됨
        put(DIVD, out_at=z(12, 1), out_reason="가상 휴직", out_by=HRA)
        for k, s in ((D1, "CEO"), (D2, "CEO"), (LD, "CEO"), (DR, "GRADE")):
            ready(k, s)
        put(EMP, proposed="B")  # 팀장이 낸 B 를 본부장이 C 로 고쳐 마감했다
        put(EXEC, grade=None)
        x("UPDATE ev_pools SET closed_at = ?, closed_by = ? WHERE year = ? AND pool = ?", z(12, 6), HEAD, Y, D1)
        x("UPDATE ev_pools SET closed_at = ?, closed_by = ? WHERE year = ? AND pool = ?", z(12, 6), CEO, Y, D2)
        audit0 = q("SELECT COALESCE(MAX(id), 0) FROM audit_logs")[0][0]
        ok([(p["key"], p["state"]) for p in st(CEO)["pools"]] == [(D1, "CEO"), (D2, "CEO"), (LD, "CEO"), (DR, "FIX")],
           "(묶음: 본부 팀원 둘 · 전사 팀장은 대표이사 확인 대기, 직속부서 팀원은 등급이 빠져 FIX)")

        # ---- 로그인 · 해 ----
        paths = ("/api/eval/pools/confirm", "/api/eval/pools/back", "/api/eval/pools/finalize", "/api/eval/pools/undo", "/api/eval/admin/release")
        ok(all(app.test_client().post(p, base_url=BASE, headers=ORIGIN, json={}).status_code == 401 for p in paths), "로그인 없이는 401 (확인 · 돌려보내기 · 확정 · 되돌리기 · 공개)")
        ok(all(hit(act(CEO, "confirm", year=v, pools=[D1]), 400, BAD_YEAR) and hit(act(CEO, "back", year=v, pool=D1, reason="가상"), 400, BAD_YEAR)
               and hit(act(HRA, "finalize", year=v, pools=[D1]), 400, BAD_YEAR) and hit(act(HRA, "undo", year=v, pool=D1, **{"from": "DONE"}), 400, BAD_YEAR)
               and hit(release(HRA, year=v), 400, BAD_YEAR) for v in (None, str(Y), float(Y), True, 2101)),
           "해가 정수(2026~2100)가 아니면 400 (다섯 길)")
        ok(hit(act(CEO, "confirm", year=Y + 1, pools=[D1]), 404, NO_POOL) and hit(act(HRA, "undo", year=Y + 1, pool=D1, **{"from": "DONE"}), 404, NO_POOL), "시작하지 않은 해에는 묶음이 없어 404")
        ok(hit(release(LEAD), 403, HR_ONLY) and hit(release(CEO), 403, HR_ONLY) and hit(release(HRA, year=Y - 1), 403, {"error": f"{Y - 1}년 평가는 포털 밖에서 진행합니다."})
           and hit(release(HRA, year=Y + 1), 409, {"error": "평가를 아직 시작하지 않았습니다."}),
           "공개: HR 만 403 · 포털 평가 전 해 403 · 시작하지 않은 해 409")
        ok(hit(release(HRA), 409, NOT_DONE) and q("SELECT released_at, released_by FROM ev_rounds WHERE year = ?", Y) == [(None, None)],
           "공개는_모두_확정된_뒤: 확정 안 된 사람이 있으면 409 '아직 확정되지 않은 분이 있습니다.' · 그대로")

        # ---- 돌려보내기는_본부_팀원만 ----
        ok(cans(CEO, "back") == {D1: True, D2: True, LD: False, DR: False} and cans(CEO, "confirm") == {D1: True, D2: True, LD: True, DR: False}
           and cans(HEAD, "back") == cans(HEAD, "confirm") == {D1: False} and not any(cans(HRA, "confirm").values()) and not any(cans(HRA, "back").values()),
           "can: 대표이사는 대기 묶음 확인 · 본부 팀원 묶음만 돌려보내기 — 본부장 · 맡기기 전 HR 은 거짓")
        ok(hit(act(CEO, "back", pool=LD, reason="가상"), 403, no(NL, "돌려보낼")) and hit(act(CEO, "back", pool=DR, reason="가상"), 403, no(ND, "돌려보낼"))
           and all(hit(act(who, "back", pool=D1, reason="가상"), 403, no(N1, "돌려보낼")) for who in (HEAD, HRA, LEAD, EMP)),
           "돌려보내기는_본부_팀원만: 팀장 · 직속부서 묶음 403 · 본부장 · 맡기기 전 HR · 팀장 · 직원 403 (어느 묶음인지 문구에)")
        ok(all(hit(act(CEO, "back", pool=v, reason="가상"), 400, PICK) for v in (None, 91, [D1])) and hit(act(CEO, "back", pool="DIV:999", reason="가상"), 404, NO_POOL),
           "돌려보내기: 묶음 열쇠가 글자가 아니면 400 · 없는 묶음 404")
        ok(all(hit(act(CEO, "back", pool=D1, **b), 400, m) for b, m in (({}, WHY), ({"reason": "  \n "}, WHY), ({"reason": "가" * 201}, WHY), ({"reason": 5}, WHY),
                                                                        ({"reason": "가상\x00"}, WHY_CHAR)))
           and stages(D1) == ["CEO"] * 4 and prow(D1)[5] is None,
           "돌려보내기는_본부_팀원만: 이유 없이 · 공백만 · 201자 · 글이 아님 · 쓸 수 없는 글자 400 — 그대로")
        took()
        ok(hit(act(CEO, "back", pool=D1, reason=" 가상 돌려보낸 이유 "), 200, OK) and stages(D1) == ["GRADE"] * 4 and stages(D2) == ["CEO"]
           and prow(D1)[:2] == (None, None) and prow(D1)[5:9] == (z(12, 7), CEO, None, "가상 돌려보낸 이유"),
           "돌려보내기는_본부_팀원만: 200 → 모두 GRADE · 마감 기록 비움 · back_at/by/reason (앞뒤 공백 뗌 · 대신 아님)")
        ok(took() == [(HEAD, *TURN)], "돌려보내기는_본부_팀원만: 본부장(배정자)에게 알림 한 통")
        ok(audits("EVAL_POOL_RETURNED") == [(Y, {"year": Y, "pool": D1})] and by("EVAL_POOL_RETURNED") == [(CEO, None)], "감사로그 EVAL_POOL_RETURNED (해 · 묶음 열쇠만 — 이유 없음)")
        ok(hit(act(CEO, "back", pool=D1, reason="가상"), 409, is_(N1, "이미 돌려보냈습니다.")) and hit(act(CEO, "confirm", pools=[D1]), 409, is_(N1, "아직 확인할 차례가 아닙니다."))
           and not took() and len(audits("EVAL_POOL_RETURNED")) == 1,
           "또 돌려보내면 409 '이미 돌려보냈습니다.' · 돌려보낸 묶음 확인 409 · 알림 · 감사로그 없음")
        hp = pool_of(HEAD, D1)
        ok(hp["state"] == "WAIT" and hp["log"]["back"] == {"at": z(12, 7), "by": "가상E대표 가상직급", "reason": "가상 돌려보낸 이유"} and hp["log"]["closed"] is None
           and st(HEAD)["todo"] == {"me": 1, "admin": 0, "e3": 0} and row_of(st(HEAD), EMP)["can"]["assign"] is True,
           "(본부장 화면: 돌려보낸 이유 · 다시 배정할 묶음 1 · 고칠 수 있음)")
        ok(hit(post(HEAD, "/api/eval/pools/close", {"year": Y, "pool": D1}), 200, OK) and stages(D1) == ["CEO"] * 4 and prow(D1)[:2] == (z(12, 7), HEAD)
           and prow(D1)[5:9] == (None,) * 4 and pool_of(HEAD, D1)["log"]["back"] is None,
           "돌려보내기는_본부_팀원만: 본부장이 다시 마감하면 돌려보낸 기록(back_*)이 지워짐")
        took()  # 마감 알림 — check_eval_score 가 본다
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 7))
        ok(hit(act(HRA, "back", pool=D2, reason="가상 대신 돌려보냄"), 200, OK) and stages(D2) == ["GRADE"] and prow(D2)[5:8] == (z(12, 7), HRA, CEO)
           and took() == [(HRL, *TURN), (HR, *TURN)] and by("EVAL_POOL_RETURNED")[-1] == (HRA, CEO),
           "맡김 → HR 이 본부장 없는 본부 묶음을 대신 돌려보냄 (back_behalf · on_behalf_of = 대표이사) → 배정자가 대표이사라 HR 전원 (누른 사람 빼고)")
        ok(hit(post(HRA, "/api/eval/pools/close", {"year": Y, "pool": D2}), 200, OK) and stages(D2) == ["CEO"], "(맡은 HR 이 다시 마감)")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        took()
        put(EMPB, grade="B")  # 90.0점 B — 지금 기준(B 는 80점 초과)에는 맞는다
        x("UPDATE ev_rounds SET band_a = 95, band_b = 90 WHERE year = ?", Y)  # 마감 뒤 HR 이 기준 점수를 올렸다 (회차 저장은 시작 뒤에도 기준을 바꿀 수 있다) → B 는 90점 초과라 어긋남
        p = pool_of(CEO, D1)
        ok(p["state"] == "FIX" and p["can"]["back"] is True and p["can"]["confirm"] is False and row_of(st(CEO), EMPB)["can"]["assign"] is False,
           "(마감한 본부 묶음이 FIX — 대표이사는 확인도 등급 고치기도 못 하고 돌려보내기만 된다)")
        ok(hit(act(CEO, "back", pool=D1, reason="가상 기준이 바뀌어 다시 배정"), 200, OK) and stages(D1) == ["GRADE"] * 4 and took() == [(HEAD, *TURN)],
           "돌려보내기: 본부 묶음은 FIX 에서도 200 → 모두 GRADE · 본부장에게 알림 (빠져나갈 길)")
        x("UPDATE ev_rounds SET band_a = 90, band_b = 80 WHERE year = ?", Y)
        put(EMPB, grade="C")
        ok(hit(post(HEAD, "/api/eval/pools/close", {"year": Y, "pool": D1}), 200, OK) and stages(D1) == ["CEO"] * 4, "(기준을 되돌리고 본부장이 다시 마감)")
        took()

        # ---- 확인은_대표이사가_묶음째로 (본부 팀원 둘 · 전사 팀장 = CEO 대기, 직속부서 = FIX) ----
        ok(st(CEO)["todo"] == {"me": 4, "admin": 0, "e3": 0} and st(HRA)["todo"] == {"me": 0, "admin": 2, "e3": 0} and st(HEAD)["todo"] == {"me": 0, "admin": 0, "e3": 0},
           "할_일_숫자: 대표이사 확인할 묶음 4 (CEO 셋 · FIX 하나) · HR 은 확인 안 한 마감 알림 2 · 본부장 0")
        ok(all(hit(act(CEO, "confirm", pools=v), 400, PICK) for v in (None, D1, [], [91], [[D1]], {"0": D1})) and hit(act(CEO, "confirm", pools=[D1, "DIV:999"]), 404, NO_POOL),
           "확인: 묶음이 목록이 아니거나 · 비었거나 · 글자가 아닌 열쇠 400 · 없는 묶음이 하나라도 있으면 404")
        ok(all(hit(act(who, "confirm", pools=[D1]), 403, no(N1, "확인할")) for who in (HEAD, HRA, LEAD)) and hit(act(HEAD, "confirm", pools=[D2, D1]), 403, no(N2, "확인할")),
           "확인은_대표이사가_묶음째로: 본부장 · 맡기기 전 HR · 팀장 403")
        r = act(CEO, "confirm", pools=[D1, DR])
        ok(r.status_code == 409 and r.json == {**is_(ND, "등급을 다시 맞춰야 합니다."), "errors": ["가상E임원직원 님의 등급이 없습니다."]} and stages(D1) == ["CEO"] * 4 and prow(D1)[2] is None,
           "확인은_대표이사가_묶음째로: 상태가 CEO 가 아닌 묶음(FIX)을 섞으면 409 (어느 묶음 · 까닭) — 아무것도 안 바뀜")
        ok(hit(act(CEO, "confirm", pools=[D1, D2, D1]), 200, {"ok": True, "done": [D1, D2]}) and stages(D1) == ["HR"] * 4 and stages(D2) == ["HR"] and stages(LD) == ["CEO"] * 3
           and prow(D1)[2:5] == prow(D2)[2:5] == (z(12, 7), CEO, None) and prow(LD)[2] is None and q("SELECT stage FROM ev_targets WHERE year = ? AND user_id = ?", Y, DIVD) == [("SELF",)],
           "확인은_대표이사가_묶음째로: 대표이사가 둘을 확인 → 둘 HR (제외된 사람은 그대로) · ceo_at/by · 하나(전사 팀장)는 그대로 · 같은 열쇠는 한 번")
        ok(audits("EVAL_POOL_CONFIRMED") == [(Y, {"year": Y, "pool": D1}), (Y, {"year": Y, "pool": D2})] and by("EVAL_POOL_CONFIRMED") == [(CEO, None)] * 2,
           "감사로그 EVAL_POOL_CONFIRMED 묶음마다 (해 · 묶음 열쇠만)")
        ok(took() == [(HRA, *TURN), (HRL, *TURN), (HR, *TURN)], "확인 뒤 HR 전원에게 알림 한 통씩 (묶음이 둘이어도)")
        ok(hit(act(CEO, "confirm", pools=[LD, D1]), 409, is_(N1, "이미 확인했습니다.")) and stages(LD) == ["CEO"] * 3 and not took() and len(audits("EVAL_POOL_CONFIRMED")) == 2,
           "확인은_대표이사가_묶음째로: 이미 확인한 묶음을 섞으면 409 '이미 확인했습니다.' — 전사 팀장도 그대로 · 알림 · 감사로그 없음")
        ok(hit(act(HRA, "finalize", pools=[D1, LD]), 409, is_(NL, "아직 대표이사 확인 전입니다.")) and stages(D1) == ["HR"] * 4,
           "확정: 대표이사 확인 전 묶음을 섞으면 409 — 아무것도 안 바뀜")
        ok(hit(act(HRA, "undo", pool=LD, **{"from": "HR"}), 409, is_(NL, "되돌릴 수 있는 단계가 아닙니다.")) and stages(LD) == ["CEO"] * 3,
           "되돌리기: 확인한 적 없는 묶음은 409 '되돌릴 수 있는 단계가 아닙니다.'")
        ok(cans(CEO, "confirm") == {D1: False, D2: False, LD: True, DR: False} and cans(HRA, "finalize") == cans(HRA, "undo") == {D1: True, D2: True, LD: False, DR: False}
           and not any(cans(CEO, "finalize").values()) and pool_of(CEO, D1)["log"]["ceo"] == {"at": z(12, 7), "by": "가상E대표 가상직급"},
           "can: 확인한 묶음은 HR 확정 · 되돌리기 (HR 만) · 확인 기록")
        ok(st(CEO)["todo"] == {"me": 2, "admin": 0, "e3": 0} and st(HRA)["todo"] == {"me": 0, "admin": 2, "e3": 0}, "할_일_숫자: 대표이사 남은 확인할 묶음 2 · HR 확정할 묶음 2 (확인한 묶음의 마감 알림은 안 셈)")

        # ---- HR은_자기_묶음을_확인_못_한다 (맡김 — HR 담당자는 직속부서 · HR 팀장은 전사 팀장 묶음에 든다) ----
        put(EXEC, grade="C")
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 7))
        ok(cans(HR, "confirm") == {D1: False, D2: False, LD: True, DR: False} and cans(HRL, "confirm") == {D1: False, D2: False, LD: False, DR: True}
           and cans(HRA, "confirm") == {D1: False, D2: False, LD: True, DR: True} and cans(HR, "back") == {D1: False, D2: False, LD: False, DR: False},
           "can.confirm: 맡김 → HR 은 자기가 들지 않은 대기 묶음만 (제외된 줄 포함)")
        ok(st(HR)["todo"]["admin"] == 3 and st(HRL)["todo"]["admin"] == 3 and st(HRA)["todo"]["admin"] == 4 and st(CEO)["todo"]["me"] == 2,
           "할_일_숫자: 맡김 → HR admin = 확정할 묶음 2 + 자기가 들지 않은 확인할 묶음 (HR 담당 1 · HR 팀장 1 · 대상 아닌 HR 2) · 대표이사 me 는 그대로 2")
        ok(hit(act(HR, "confirm", pools=[DR]), 403, no(ND, "확인할")) and hit(act(HR, "confirm", pools=[LD, DR]), 403, no(ND, "확인할"))
           and hit(act(HRL, "confirm", pools=[LD]), 403, no(NL, "확인할")) and stages(LD) == ["CEO"] * 3 and stages(DR) == ["GRADE"] * 2,
           "HR은_자기_묶음을_확인_못_한다: 맡겨도 HR 담당자는 직속부서 묶음(본인 있음) 403 · 섞어도 403 · HR 팀장은 전사 팀장 묶음 403 — 그대로")
        ok(hit(act(HR, "confirm", pools=[LD]), 200, {"ok": True, "done": [LD]}) and stages(LD) == ["HR"] * 3 and prow(LD)[2:5] == (z(12, 7), HR, CEO)
           and by("EVAL_POOL_CONFIRMED")[-1] == (HR, CEO), "HR은_자기_묶음을_확인_못_한다: HR 담당자가 전사 팀장 묶음 200 — ceo_behalf · on_behalf_of = 대표이사")
        ok(took() == [(HRA, *TURN), (HRL, *TURN)], "대신 확인해도 HR 전원(누른 사람 빼고)에게 알림")
        ok(hit(act(CEO, "confirm", pools=[DR]), 200, {"ok": True, "done": [DR]}) and stages(DR) == ["HR"] * 2 and prow(DR)[2:5] == (z(12, 7), CEO, None)
           and by("EVAL_POOL_CONFIRMED")[-1] == (CEO, None) and took() == [(HRA, *TURN), (HRL, *TURN), (HR, *TURN)],
           "맡긴 동안에도 대표이사가 직접 확인 200 (대신 아님)")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)

        # ---- 확정은_한_번 ----
        ok(all(hit(act(who, "finalize", pools=[D1]), 403, no(N1, "확정할")) for who in (CEO, HEAD, LEAD)) and all(hit(act(HRA, "finalize", pools=v), 400, PICK) for v in (None, [], [1]))
           and hit(act(HRA, "finalize", pools=["DIV:999"]), 404, NO_POOL), "확정: HR 이 아니면 403 · 목록 모양 400 · 없는 묶음 404")
        twins = [app.test_client() for _ in range(2)]
        for cl, uid in zip(twins, (HRA, HRL)):
            login(cl, new_token(f"HW-{uid}"))
        got, gate = [], threading.Barrier(2)

        def race(cl):
            gate.wait()
            r = cl.post("/api/eval/pools/finalize", base_url=BASE, headers=ORIGIN, json={"year": Y, "pools": [D1]})
            got.append((r.status_code, r.json))

        threads = [threading.Thread(target=race, args=(cl,)) for cl in twins]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        ok(sorted(got, key=lambda v: v[0]) == [(200, {"ok": True, "done": [D1]}), (409, is_(N1, "이미 확정했습니다."))] and stages(D1) == ["DONE"] * 4
           and audits("EVAL_POOL_FINALIZED") == [(Y, {"year": Y, "pool": D1})] and not took(),
           f"확정은_한_번: 두 HR 이 동시에 확정 → 하나 200 · 하나 409 '이미 확정했습니다.' · 감사로그 하나 · 알림 없음 ({[s for s, _ in got]})")
        ok(hit(act(HR, "finalize", pools=[DR, LD, D2]), 200, {"ok": True, "done": [DR, LD, D2]}) and stages(DR) == ["DONE"] * 2 and stages(LD) == ["DONE"] * 3 and stages(D2) == ["DONE"]
           and audits("EVAL_POOL_FINALIZED")[1:] == [(Y, {"year": Y, "pool": k}) for k in (DR, LD, D2)] and not took(),
           "HR 확정은 HR 본인 일 — 자기가 든 묶음(직속부서)도 200 · 감사로그 묶음마다 · 알림 없음")

        # ---- 되돌리기 (from = 화면이 본 묶음 상태) ----
        ok(all(hit(act(who, "undo", pool=DR, **{"from": "DONE"}), 403, no(ND, "되돌릴")) for who in (CEO, HEAD)) and hit(act(HRA, "undo", pool=None, **{"from": "DONE"}), 400, PICK)
           and hit(act(HRA, "undo", pool="DIV:999", **{"from": "DONE"}), 404, NO_POOL), "되돌리기: HR 이 아니면 403 · 열쇠 모양 400 · 없는 묶음 404")
        ok(all(hit(act(HRA, "undo", pool=DR, **b), 400, SEEN) for b in ({}, {"from": None}, {"from": "CEO"}, {"from": "done"}, {"from": ["DONE"]}))
           and hit(act(HRA, "undo", pool="DIV:999"), 400, SEEN) and stages(DR) == ["DONE"] * 2, "되돌리기: 본 상태(from)가 없거나 HR · DONE 이 아니면 400 (없는 묶음 404 보다 먼저) — 그대로")
        ok(hit(act(HR, "undo", pool=D1, **{"from": "HR"}), 409, is_(N1, "그사이 확정됐습니다.")) and stages(D1) == ["DONE"] * 4,
           "되돌리기: HR 확정 대기로 보고 눌렀는데 그사이 확정됐으면 409 — 그대로")
        undo = lambda who, k, seen: act(who, "undo", pool=k, **{"from": seen})  # noqa: E731
        ok(hit(undo(HRA, DR, "DONE"), 200, OK) and stages(DR) == ["HR"] * 2 and prow(DR)[2:5] == (z(12, 7), CEO, None) and prow(DR)[9:] == (z(12, 7), HRA, "HR"),
           "되돌리기: 확정(DONE) → HR 확정 대기 · 대표이사 확인 기록은 그대로 · undo_at/by/from HR")
        ok(hit(undo(HRA, DR, "DONE"), 409, is_(ND, "이미 되돌렸습니다.")) and hit(undo(HRL, DR, "DONE"), 409, is_(ND, "이미 되돌렸습니다."))
           and stages(DR) == ["HR"] * 2 and prow(DR)[2] == z(12, 7) and len(audits("EVAL_POOL_UNDONE")) == 1,
           "되돌리기: 같은 요청(본 상태 DONE)을 또 보내면 409 '이미 되돌렸습니다.' — 한 단계만 내려감 · 대표이사 확인 기록 그대로 · 감사로그 하나")
        ok(hit(release(HRA), 409, NOT_DONE), "공개는_모두_확정된_뒤: 하나라도 DONE 이 아니면 409")
        ok(hit(undo(HRA, DR, "HR"), 200, OK) and stages(DR) == ["GRADE"] * 2 and prow(DR)[2:5] == (None, None, None) and prow(DR)[11] == "CEO",
           "되돌리기: HR → (직속) GRADE — 대표이사 확인 기록 비움 · undo_from CEO")
        ok(hit(undo(HRA, DR, "HR"), 409, is_(ND, "이미 되돌렸습니다.")) and stages(DR) == ["GRADE"] * 2 and len(audits("EVAL_POOL_UNDONE")) == 2
           and pool_of(CEO, DR)["state"] == "CEO" and pool_of(HRA, DR)["log"]["undo"]["from"] == "CEO" and cans(CEO, "confirm")[DR] is True,
           "되돌리기: 또 보내면 409 (대표이사 확인 전으로 한 번만) · 직속부서 팀원은 다시 대표이사 확인 대기")
        ok(hit(undo(HR, D1, "DONE"), 200, OK) and stages(D1) == ["HR"] * 4 and hit(undo(HR, D1, "HR"), 200, OK) and stages(D1) == ["CEO"] * 4
           and prow(D1)[:2] == (z(12, 7), HEAD) and prow(D1)[2] is None and prow(D1)[11] == "CEO",
           "되돌리기: (본부) DONE → HR → CEO — 마감 기록은 그대로 · 다시 대표이사 확인 대기")
        ok(audits("EVAL_POOL_UNDONE") == [(Y, {"year": Y, "pool": k, "from": f}) for k, f in ((DR, "HR"), (DR, "CEO"), (D1, "HR"), (D1, "CEO"))] and not took(),
           "감사로그 EVAL_POOL_UNDONE {해 · 묶음 · 되돌린 단계} · 알림 없음")
        ok(hit(act(CEO, "confirm", pools=[D1, DR]), 200, {"ok": True, "done": [D1, DR]}) and hit(act(HRA, "finalize", pools=[D1, DR]), 200, {"ok": True, "done": [D1, DR]}),
           "(다시 확인 · 확정)")
        took()

        # ---- 공개하면_보인다 (공개 전) ----
        lr, me = row_of(st(LEAD), EMP), st(EMP)["me"]
        ok(lr["hidden"] is True and (lr["grade"], lr["edited"]) == ("B", True) and "grade" not in me, "(공개 전: 팀장에게는 낸 등급 B · 고쳤다는 사실만 · 본인 me 에는 등급 칸이 없음)")

        # ---- 공개는_모두_확정된_뒤 ----
        live = [u for (u,) in q("SELECT user_id FROM ev_targets WHERE year = ? AND out_at IS NULL", Y)]
        x("UPDATE ev_targets SET out_at = ?, out_reason = '가상 제외' WHERE year = ? AND out_at IS NULL", z(12, 7), Y)
        ok(hit(release(HRA), 409, {"error": "평가할 분이 없습니다."}) and q("SELECT released_at FROM ev_rounds WHERE year = ?", Y) == [(None,)] and not took(),
           "공개: 제외 안 된 분이 한 명도 없으면 409 '평가할 분이 없습니다.' (공개는 되돌릴 수 없다) — 그대로")
        x(f"UPDATE ev_targets SET out_at = NULL, out_reason = NULL WHERE year = ? AND user_id IN ({','.join('?' * len(live))})", Y, *live)
        ok(hit(release(HRA), 200, OK) and q("SELECT released_at, released_by FROM ev_rounds WHERE year = ?", Y) == [(z(12, 7), HRA)]
           and audits("EVAL_RELEASED") == [(Y, {"year": Y, "count": 10})] and by("EVAL_RELEASED") == [(HRA, None)],
           "공개는_모두_확정된_뒤: 200 → released_at/by · 감사로그 EVAL_RELEASED (해 · 사람 수 — 제외된 사람 빼고 10)")
        ok(took() == [(u, *OPEN) for u in (LEAD, EMP, FIELD, EMPB, LEADC, EMPC, HRL, HR, NOCONF)],
           "공개 → 대상 휴대폰에 eval_open 한 통씩 (제외된 사람 · 포털을 안 쓰는 사람 · 대상이 아닌 본부장 · 대표이사 · HR 빼고 — HR 담당자는 대상이라 받음)")
        ok(hit(release(HRA), 409, RELEASED) and hit(release(HRL), 409, RELEASED) and not took() and len(audits("EVAL_RELEASED")) == 1, "또 공개하면 409 · 알림 · 감사로그 없음")
        ok(hit(act(HRA, "undo", pool=D1, **{"from": "DONE"}), 409, RELEASED) and hit(act(CEO, "confirm", pools=[LD]), 409, RELEASED) and hit(act(HRA, "finalize", pools=[D1]), 409, RELEASED)
           and hit(act(CEO, "back", pool=D1, reason="가상"), 409, RELEASED) and hit(act(HEAD, "confirm", pools=[D1]), 403, no(N1, "확인할")),
           "공개 뒤: 되돌리기 · 확인 · 확정 · 돌려보내기 409 (권한이 없으면 403 이 먼저)")
        ok(not any(v for who in (HRA, CEO) for p in st(who)["pools"] for v in p["can"].values()), "공개 뒤 묶음 단추(can)는 모두 거짓")

        # ---- 공개하면_보인다 ----
        lr, me = row_of(st(LEAD), EMP), st(EMP)["me"]
        ok(lr["hidden"] is False and (lr["grade"], lr["proposed"], lr["edited"]) == ("C", "B", "등급 B → C") and me["grade"] == "C" and me["hidden"] is False,
           "공개하면_보인다: 팀장 응답의 그 사람 hidden 거짓 · 고친 등급 C 가 보임 · 본인 me 에 최종 등급 C")

        # ---- 감사로그 · 알림 글 ----
        logs = q("SELECT action, after_data, before_data, reason FROM audit_logs WHERE id > ? AND action GLOB 'EVAL_*'", audit0)
        ok({a for a, *_ in logs} == {"EVAL_POOL_RETURNED", "EVAL_POOL_CLOSED", "EVAL_POOL_CONFIRMED", "EVAL_POOL_FINALIZED", "EVAL_POOL_UNDONE", "EVAL_RELEASED"}
           and all(set(json.loads(a)) <= {"year", "pool", "from", "count"} and b is None and rs is None for _, a, b, rs in logs)
           and not any(t in a for _, a, _, _ in logs for t in ("가상", '"B"', '"C"')),
           "감사로그에_등급과_글이_없다: after_data 는 해 · 묶음 열쇠 · 되돌린 단계 · 사람 수뿐 (돌려보낸 이유 · 등급 없음)")
        ok(set(texts) == {"인사평가에서 처리할 일이 있어요 PC에서 확인해 주세요", "인사평가 결과가 공개됐어요 PC에서 확인해 주세요"}, "알림 글은 두 가지 한 줄뿐 — 등급 · 이름 없음")
    finally:
        auth._now = real_now
        push.SYNC, push.SEND = real_sync, real_send
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for h in hashes:
            x("DELETE FROM push_subscriptions WHERE token_hash = ?", h)
            x("DELETE FROM sessions WHERE token_hash = ?", h)
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x(POOLS_RESET, Y)
        x("UPDATE ev_rounds SET released_at = NULL, released_by = NULL, att_done_at = ? WHERE year = ?", att0, Y)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)

def check_eval_appeal(hr) -> None:
    """인사평가 결과 읽기 · 이의 신청 (2026-10-27, 평가 3단계 ②): GET /api/eval/results — 본인 줄 · 보는 범위(팀장 = 자기 팀원 · 본부장 = 자기 본부 · 대표이사 · HR = 전원,
    HR 은 공개 전에도) · 처리할 이의 신청 · 공개한 해. 이의 신청은 공개일 + 7일(한국 23:59)까지 한 번 — HR 답변(등급 그대로면 바로 끝, 바꾸면 고친 점수와 함께 대표이사 확인) →
    대표이사 확인 · 돌려보내기. 정원은 따지지 않고 점수 기준 · 연차만 본다. 맡겨도 HR 은 자기 이의도, 자기가 쓴 등급 변경 답변도 확인하지 못한다. 처리할 일 e3 = 휴대폰 숫자(push.pending 의 e3 — e2 와 따로).
    감사로그에는 해 · 바뀜(참/거짓)뿐 (사유 · 답변 · 등급 없음), 알림 글에 등급 · 이름이 없다.
    check_eval_close 가 되돌려 둔 회차와 가상 조직(부서 91~97 · 사람 101~116)을 쓴다 — 모두 확정(DONE)한 상태를 DB 에 바로 넣고 공개부터는 실제 길로 간다.
    지난_해의 둘째 공개한 해(Y2 = 2098 — 어떤 검사도 시작하지 않는 먼 해)는 회차 · 한 사람 줄을 DB 에 바로 넣는다 (떠 둔 줄은 지울 수 없어 남고, 회차의 시작 · 공개는 되돌린다).
    끝에 시계 · 알림 · 대표이사 · 맡김 · 이의 신청 · 근태 마침 · 단계 · 등급 · 공개를 되돌린다."""
    import evaluation
    import push
    print("인사평가 결과 읽기 · 이의 신청 · HR 답변 · 대표이사 확인")
    Y2 = 2098  # 지난_해의 둘째 공개한 해 — 떠 둔 줄이 남으므로 다른 검사가 시작할 일이 없는 먼 해 (YEARS · ev_rounds CHECK 안)
    HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO = range(101, 113)
    NOCONF, HRA = 115, 1  # HRA = 평가 대상이 아닌 HR (처음 검사의 인사담당자)
    Y, real_now, real_send, real_sync = evaluation.FIRST_YEAR, auth._now, push.SEND, push.SYNC
    z = lambda m, d, hh=3, y=Y: f"{y}-{m:02d}-{d:02d}T{hh:02d}:00:00Z"  # noqa: E731
    jar: dict[int, object] = {}

    def at(m, d, hh=3, mm=0, ss=0, y=Y):
        """시계를 UTC 로 (y년 m월 d일 hh:mm:ss) 에 맞춘다. 기본 03:00 = 한국 정오 (근무시간 — 알림이 간다). 로그인은 새로."""
        t = datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)
        auth._now = lambda: t
        jar.clear()

    def c(uid):
        if uid not in jar:
            jar[uid] = app.test_client()
            login(jar[uid], new_token(f"HW-{uid}"))
        return jar[uid]

    get = lambda who, path: c(who).get(path, base_url=BASE)  # noqa: E731
    post = lambda who, path, body: c(who).post(path, base_url=BASE, headers=ORIGIN, json=body)  # noqa: E731
    hit = lambda r, code, body: r.status_code == code and r.json == body  # noqa: E731
    res = lambda who, qs="": get(who, f"/api/eval/results{qs}").json  # noqa: E731
    st = lambda who: get(who, "/api/eval/state").json  # noqa: E731
    ask = lambda who, **b: post(who, "/api/eval/me/appeal", {"year": Y, **b})  # noqa: E731
    ans = lambda who, uid, **b: post(who, f"/api/eval/appeals/{uid}/answer", {"year": Y, **b})  # noqa: E731
    dec = lambda who, uid, what, **b: post(who, f"/api/eval/appeals/{uid}/{what}", {"year": Y, **b})  # noqa: E731
    row_of = lambda rows, uid: next((p for p in rows if p["pid"] == uid), None)  # noqa: E731
    pids = lambda rows: sorted(p["pid"] for p in rows)  # noqa: E731
    put = lambda uid, **cols: x(f"UPDATE ev_targets SET {', '.join(f'{k} = ?' for k in cols)} WHERE year = ? AND user_id = ?", *cols.values(), Y, uid)  # noqa: E731
    ap = lambda uid: next(iter(q("SELECT status, before, after, marks, answer, answered_by, decided_by, decided_behalf FROM ev_appeals WHERE year = ? AND user_id = ?",  # noqa: E731
                                 Y, uid)), None)
    marks_of = lambda uid: [g_ for (g_,) in q("SELECT grade FROM ev_items WHERE year = ? AND user_id = ? ORDER BY rid", Y, uid)]  # noqa: E731
    grade_of = lambda uid: q("SELECT grade FROM ev_targets WHERE year = ? AND user_id = ?", Y, uid)[0][0]  # noqa: E731
    audits = lambda action: [(e, json.loads(a)) for e, a in q("SELECT entity_id, after_data FROM audit_logs WHERE action = ? ORDER BY id", action)]  # noqa: E731
    by = lambda action: q("SELECT actor_user_id, on_behalf_of FROM audit_logs WHERE action = ? ORDER BY id", action)  # noqa: E731
    e3 = lambda who: st(who)["todo"]["e3"]  # noqa: E731

    def phone(uid):
        """휴대폰 숫자(push.pending)의 (평가 진행 e2, 이의 신청 e3)."""
        d = dict(push.pending(app, uid))
        return d.get("e2", 0), d.get("e3", 0)

    def setg(uid, *grades):  # 실행목표 등급을 rid 차례로 바로 넣는다
        for (rid,), v in zip(q("SELECT rid FROM ev_items WHERE year = ? AND user_id = ? ORDER BY rid", Y, uid), grades):
            x("UPDATE ev_items SET grade = ? WHERE year = ? AND user_id = ? AND rid = ?", v, Y, uid, rid)

    sent: list[tuple[int, str, str]] = []
    texts: list[str] = []
    owner: dict[str, int] = {}

    def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 둔다 (받은 사람 · 제목 · 열 화면) — 글은 따로 모아 등급 · 이름이 없는지 본다
        sent.append((owner[info["endpoint"]], payload["title"], payload["url"]))
        texts.append(f"{payload['title']} {payload['body']}")
        return 201

    def took():
        out = sorted(sent)
        sent.clear()
        return out

    APPEAL, ANSWER = ("처리할 이의 신청이 있어요", "/?go=perf.e3"), ("이의 신청에 답변이 왔어요", "/?go=perf.e3")
    OK = {"ok": True}
    BAD_YEAR, HR_ONLY, SELF_HR = {"error": "해가 올바르지 않습니다."}, {"error": "HR만 할 수 있습니다."}, {"error": "본인 것은 다른 HR이 처리해요."}
    NOT_YET, OUT, LATE, TWICE = ({"error": "아직 결과가 공개되지 않았습니다."}, {"error": "평가에서 제외되어 이의 신청할 결과가 없습니다."},
                                 {"error": "이의 신청 기간이 끝났습니다."}, {"error": "이미 이의 신청을 했습니다."})
    WHY, WHY_CHAR = {"error": "이의 신청 사유를 1~500자로 써 주세요."}, {"error": "이의 신청 사유에 쓸 수 없는 글자가 있습니다."}
    NO_APPEAL, NO_TARGET, ANSWERED = {"error": "이의 신청을 찾을 수 없습니다."}, {"error": "평가 대상이 아닙니다."}, {"error": "이미 답변했습니다."}
    ANS, ANS_CHAR, GRADE = {"error": "답변을 1~500자로 써 주세요."}, {"error": "답변에 쓸 수 없는 글자가 있습니다."}, {"error": "등급은 S · A · B · C · D 중에서 골라 주세요."}
    NO_KPI, SHAPE, NO_ITEM = {"error": "KPI가 없는 분은 등급만 매깁니다."}, {"error": "실행목표별 등급을 실행목표 번호마다 보내 주세요."}, {"error": "실행목표를 찾을 수 없습니다."}
    MARK, LEAVE = {"error": "달성 등급은 S · A · B · C · D 중에서 골라 주세요."}, {"error": "연차 초과 5일 이상이라 S·A·B를 줄 수 없습니다."}
    NO_RIGHT, DONE_, WAIT = {"error": "이 이의 신청을 확인할 권한이 없습니다."}, {"error": "이미 처리했습니다."}, {"error": "HR 답변을 기다리는 이의 신청입니다."}
    RESET = ("UPDATE ev_targets SET stage = CASE WHEN kpi_version IS NULL THEN 'SCORE' ELSE 'SELF' END, proposed = NULL, grade = NULL, sub_score_x10 = NULL,"
             " sub_marks = NULL, scored_at = NULL, scored_by = NULL, comment = '', head_comment = '', bonus_x2 = 0, bonus_reason = '', proxy_by = NULL, edit_seen = NULL,"
             " late = 0, leave_x2 = 0, out_at = NULL, out_reason = NULL, out_by = NULL, no_self_at = NULL, no_self_by = NULL, self_at = NULL WHERE year = ?")

    other_ceos = [r[0] for r in q("SELECT u.id FROM users u JOIN user_roles r ON r.user_id = u.id WHERE r.role = 'CEO' AND u.is_active = 1 AND u.id <> ?", CEO)]
    att0 = q("SELECT att_done_at FROM ev_rounds WHERE year = ?", Y)[0][0]
    env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}
    hashes: list[str] = []
    try:
        for uid in other_ceos:  # 이 가상 조직의 대표이사는 check_eval_admin 이 만든 한 명뿐이게
            x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", uid)
        x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", CEO)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x("UPDATE ev_rounds SET released_at = NULL, released_by = NULL, att_done_at = ? WHERE year = ?", z(12, 2), Y)
        pub, priv = push.keygen()
        os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
        push.SYNC, push.SEND = True, fake_send
        for uid in (HEAD, LEAD, EMP, FIELD, EMPB, DIVD, LEADC, EMPC, HRL, HR, EXEC, CEO, NOCONF, HRA):  # 휴대폰을 등록한 사람 — 대상 전원 · 본부장 · 대표이사 · HR
            h, ep = f"{uid:03d}" + "9" * 61, f"https://push.example.invalid/appeal-{uid}"
            x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')", h, uid, z(12, 10), z(12, 10), z(12, 31))
            x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, z(12, 10))
            owner[ep] = uid
            hashes.append(h)
        # 모두 확정: 본부 직속(DIVD)은 제외 · 실행목표 B · B · B (90.0점) · 등급 C. 본부 팀원 묶음(DIV:91 — 4명, A·B 합계 1명)은 팀원B 의 B 로 정원이 찼다 · 현장직은 연차 초과 5일
        put(DIVD, out_at=z(12, 1), out_reason="가상 휴직", out_by=HRA)
        live = [u for (u,) in q("SELECT user_id FROM ev_targets WHERE year = ? AND out_at IS NULL", Y)]
        for uid in live:
            setg(uid, "B", "B", "B")
            put(uid, stage="DONE", proposed="C", grade="C")
        put(EMPB, grade="B")
        put(FIELD, leave_x2=10)
        everyone = sorted(u for (u,) in q("SELECT user_id FROM ev_targets WHERE year = ?", Y))
        audit0 = q("SELECT COALESCE(MAX(id), 0) FROM audit_logs")[0][0]

        # ---- 공개 전 (한국 12/14 화요일 정오) ----
        at(12, 14)
        paths = ("/api/eval/me/appeal", f"/api/eval/appeals/{EMP}/answer", f"/api/eval/appeals/{EMP}/confirm", f"/api/eval/appeals/{EMP}/back")
        ok(all(app.test_client().post(p, base_url=BASE, headers=ORIGIN, json={}).status_code == 401 for p in paths)
           and app.test_client().get("/api/eval/results", base_url=BASE).status_code == 401, "로그인 없이는 401 (결과 읽기 · 이의 신청 · 답변 · 확인 · 돌려보내기)")
        ok(hit(ask(EMP, reason="가상 이의"), 403, NOT_YET) and hit(ask(DIVD, reason="가상 이의"), 403, NOT_YET) and q("SELECT COUNT(*) FROM ev_appeals WHERE year = ?", Y) == [(0,)],
           "이의_신청은_7일: 공개 전 403 '아직 결과가 공개되지 않았습니다.' (제외된 사람도 — 공개 전이 먼저)")
        r = res(LEAD)
        ok(r["year"] == Y and r["years"] == [] and r["offline"] is False and r["released"] is None and r["appealDue"] is None and r["team"] == [] and r["appeals"] == []
           and r["mine"]["pid"] == LEAD and "grade" not in r["mine"] and r["mine"]["appeal"] is None,
           "결과를_보는_범위: 공개 전 팀장 — team 빈 목록 · 본인 줄은 자기평가만(등급 칸 없음) · 기본 해 = 시작한 해(공개 전이어도)")
        r = res(HRA)
        ok(pids(r["team"]) == everyone and row_of(r["team"], EMP)["grade"] == "C" and r["mine"] is None,
           "결과를_보는_범위: HR 은 공개 전에도 전원 (제외된 사람 포함 · 등급이 보임) · 평가 대상이 아니면 mine null")

        # ---- 공개 (한국 12/15 수요일 정오 — 이의 신청은 12/22 23:59 까지) ----
        at(12, 15)
        ok(hit(post(HRA, "/api/eval/admin/release", {"year": Y}), 200, OK), "(HR 이 결과 공개)")
        took()
        at(12, 16)
        r = res(EMP)
        ok(r["years"] == [Y] and r["released"] == z(12, 15) and r["appealDue"] == f"{Y}-12-22" and r["mine"]["grade"] == "C" and r["mine"]["appeal"] is None,
           "공개 뒤: years · released · appealDue(공개일 + 7일, 한국 날짜) · 본인 등급")

        # ---- 이의_신청은_7일 (거절) ----
        ok(all(hit(ask(EMP, year=v, reason="가상"), 400, BAD_YEAR) for v in (None, str(Y), float(Y), True, 2101)), "이의 신청: 해가 정수(2026~2100)가 아니면 400")
        ok(hit(ask(HEAD, reason="가상"), 404, {"error": "이번 평가 대상이 아닙니다."}) and hit(ask(EMP, year=Y + 1, reason="가상"), 404, {"error": "이번 평가 대상이 아닙니다."}),
           "이의 신청: 그해 평가 대상이 아니면 404 (본부장 · 다른 해)")
        ok(hit(ask(DIVD, reason="가상"), 403, OUT), "이의 신청: 평가에서 제외된 사람 403")
        ok(all(hit(ask(EMP, **b), 400, m) for b, m in (({}, WHY), ({"reason": "  \n "}, WHY), ({"reason": "가" * 501}, WHY), ({"reason": 5}, WHY), ({"reason": "가상\x00"}, WHY_CHAR)))
           and ap(EMP) is None, "이의 신청: 사유 없이 · 공백만 · 501자 · 글이 아님 · 쓸 수 없는 글자 400 — 저장 안 됨")

        # ---- 이의 신청 ----
        ok(hit(ask(EMP, reason=" 가상 실적 누락 ", userId=NOCONF, year=Y), 200, OK) and ap(EMP)[:3] == ("OPEN", "C", None) and ap(NOCONF) is None
           and q("SELECT reason, at FROM ev_appeals WHERE year = ? AND user_id = ?", Y, EMP) == [("가상 실적 누락", z(12, 16))],
           "이의_신청은_7일: 200 → OPEN · before = 지금 등급 C · 사유 앞뒤 공백 뗌 · 낸 때 — 남의 번호(userId)를 보내도 내 것만 생긴다 (남의 것을 신청할 길이 없다)")
        ok(took() == [(HRA, *APPEAL), (HRL, *APPEAL), (HR, *APPEAL)], "이의 신청 → HR 전원에게 알림 한 통씩")
        ok(hit(ask(EMP, reason="가상 또"), 409, TWICE) and not took() and audits("EVAL_APPEALED") == [(EMP, {"year": Y})], "이의_신청은_7일: 두 번째 409 · 알림 · 감사로그 없음")
        ok(all(hit(ask(u, reason=f"가상 이의 {u}"), 200, OK) for u in (FIELD, LEAD, HR)), "(현장직 · 팀장 · HR 담당도 이의 신청)")
        ok(took() == sorted([(h, *APPEAL) for h in (HRA, HRL, HR)] * 2 + [(HRA, *APPEAL), (HRL, *APPEAL)]), "HR 이 신청하면 다른 HR 에게만 알림")
        ok(audits("EVAL_APPEALED") == [(u, {"year": Y}) for u in (EMP, FIELD, LEAD, HR)] and by("EVAL_APPEALED")[0] == (EMP, None),
           "감사로그 EVAL_APPEALED (해만 — 사유 없음 · 대상 = 신청한 사람)")
        ok((e3(HRA), e3(HRL), e3(HR), e3(CEO), e3(LEAD)) == (4, 4, 3, 0, 0), "처리할 일 e3: HR = 남의 OPEN (HR 담당은 자기 것 빼고 3) · 대표이사 · 팀장 0")
        ok(phone(HRA) == (0, 4) and phone(HR) == (0, 3) and phone(CEO) == (0, 0), "휴대폰 숫자(push.pending)의 e3 = 화면 e3 · e2 에는 이의 신청이 들지 않는다 (공개 뒤 e2 0)")
        ok(pids(res(HRA)["appeals"]) == sorted([EMP, FIELD, LEAD, HR]) and pids(res(HR)["appeals"]) == sorted([EMP, FIELD, LEAD]) and res(CEO)["appeals"] == []
           and res(LEAD)["appeals"] == [], "결과의 appeals: HR = 답변할 OPEN (자기 것 빼고) · 대표이사 · 팀장 빈 목록")

        # ---- 유지는_바로_끝 (현장직 — KPI 없음 · 연차 초과 5일) ----
        ok(hit(ans(LEAD, FIELD, grade="C", answer="가상"), 403, HR_ONLY) and hit(ans(CEO, FIELD, grade="C", answer="가상"), 403, HR_ONLY), "답변: HR 이 아니면 403")
        ok(hit(ans(HR, HR, grade="C", answer="가상"), 403, SELF_HR) and hit(ans(HR, HR, year=None), 403, SELF_HR), "답변: HR 은 자기 이의에 답하지 못한다 403 (해 400 보다 먼저)")
        ok(hit(ans(HRA, FIELD, year=None, grade="C", answer="가상"), 400, BAD_YEAR) and hit(ans(HRA, NOCONF, grade="C", answer="가상"), 404, NO_APPEAL)
           and hit(ans(HRA, 999999, grade="C", answer="가상"), 404, NO_APPEAL), "답변: 해 400 · 이의 신청이 없으면 404")
        ok(all(hit(ans(HRA, FIELD, grade="C", **b), 400, m) for b, m in (({}, ANS), ({"answer": " "}, ANS), ({"answer": "가" * 501}, ANS), ({"answer": 1}, ANS),
                                                                            ({"answer": "가상\x07"}, ANS_CHAR)))
           and all(hit(ans(HRA, FIELD, grade=v, answer="가상"), 400, GRADE) for v in (None, "X", "s", ["C"]))
           and all(hit(ans(HRA, FIELD, grade="C", answer="가상", marks=v), 400, NO_KPI) for v in ({}, {"1": "A"}, []))
           and hit(ans(HRA, FIELD, grade="B", answer="가상"), 400, LEAVE) and ap(FIELD)[0] == "OPEN",
           "답변: 답변 1~500자 · 쓸 수 없는 글자 · 등급 SABCD · KPI 없는 사람의 실행목표 등급 400 · 연차 초과 5일이면 B 400 — 그대로 OPEN")
        ok(hit(ans(HRA, FIELD, grade="C", answer=" 가상 검토 결과 유지 ", marks=None), 200, OK) and ap(FIELD) == ("DONE", "C", "C", None, "가상 검토 결과 유지", HRA, HRA, None)
           and grade_of(FIELD) == "C", "유지는_바로_끝: 등급 그대로 → DONE · after = C · 답변(앞뒤 공백 뗌) · 답한 사람 = 끝낸 사람")
        ok(took() == [(FIELD, *ANSWER)], "유지는_바로_끝: 신청한 사람에게 eval_answer")
        mine = res(FIELD)["mine"]
        ok(mine["appeal"] == {"reason": f"가상 이의 {FIELD}", "at": z(12, 16), "status": "DONE", "before": "C", "answer": "가상 검토 결과 유지", "after": "C", "marks": None},
           "유지는_바로_끝: 결과 appeal.status DONE · 본인에게 답변이 보임")
        ok(hit(ans(HRA, FIELD, grade="C", answer="가상"), 409, ANSWERED) and audits("EVAL_APPEAL_ANSWERED") == [(FIELD, {"year": Y, "changed": False})],
           "또 답하면 409 '이미 답변했습니다.' · 감사로그 EVAL_APPEAL_ANSWERED {해, changed 거짓}")
        ok(hit(ask(EMPB, reason="가상 이의 B"), 200, OK) and took() == [(HRA, *APPEAL), (HRL, *APPEAL), (HR, *APPEAL)], "(팀원B — 실행목표 B · B · B · 등급 B — 도 이의 신청)")
        ok(hit(ans(HRA, EMPB, grade="B", answer="가상 2번 실적 반영 · 등급 유지", marks={"2": "A", "3": "B"}), 200, OK)
           and ap(EMPB) == ("DONE", "B", "B", '{"2": "A"}', "가상 2번 실적 반영 · 등급 유지", HRA, HRA, None) and marks_of(EMPB) == ["B", "A", "B"] and grade_of(EMPB) == "B"
           and took() == [(EMPB, *ANSWER)],
           "유지는_바로_끝: 실행목표를 고쳐도 등급 그대로면 바로 DONE — 고친 실행목표 등급을 반영하고 이의 기록에도 marks(바뀐 것만)로 남김")
        em = res(EMPB)["mine"]
        ok(em["appeal"]["marks"] == {"2": "A"} and em["appeal"]["status"] == "DONE" and em["total"] == 93.0, "유지는_바로_끝: 본인에게는 끝난(DONE) 뒤 고친 실행목표가 보임 · 총점 93.0")

        # ---- 바꾸려면_점수와_대표이사 (팀장 — 실행목표 B · B · B = 90.0점 · 등급 C) ----
        ok(hit(ans(HRA, LEAD, grade="A", answer="가상 상향"), 400, {"error": "총점 90.0점으로는 A를 줄 수 없습니다. (A는 90점 초과) 점수를 다시 봐 주세요."}),
           "바꾸려면_점수와_대표이사: 점수를 안 고치고 올리면 400 (점수 기준 — 화면 evSubmitScore 문구)")
        ok(all(hit(ans(HRA, LEAD, grade="A", answer="가상 상향", marks=v), 400, m) for v, m in ((["A"], SHAPE), ("A", SHAPE), ({"9": "A"}, NO_ITEM), ({"x": "A"}, NO_ITEM),
                                                                                             ({"1": None}, MARK), ({"1": "X"}, MARK)))
           and ap(LEAD)[0] == "OPEN", "답변의 실행목표 등급: 번호마다가 아님 · 없는 실행목표 · 비우기 · 등급 아님 400")
        ok(hit(ans(HRA, LEAD, grade="A", answer="가상 상향", marks={"1": "A", "2": "B"}), 200, OK) and ap(LEAD) == ("CEO", "C", "A", '{"1": "A"}', "가상 상향", HRA, None, None)
           and grade_of(LEAD) == "C" and marks_of(LEAD) == ["B", "B", "B"],
           "바꾸려면_점수와_대표이사: 실행목표를 고쳐(94.0점) 올림 → CEO · after A · marks 는 바뀐 실행목표만 — 그때까지 등급 · 실행목표 등급 그대로")
        ok(took() == [(CEO, *APPEAL)], "등급을 바꾸는 답변 → 대표이사에게 eval_appeal")
        lm, sm = res(LEAD)["mine"], st(LEAD)["me"]
        cut = {"reason": f"가상 이의 {LEAD}", "at": z(12, 16), "status": "CEO", "before": "C", "answer": None, "after": None, "marks": None}
        ok(lm["grade"] == "C" and lm["appeal"] == cut and sm["appeal"] == cut,
           "바꾸려면_점수와_대표이사: 본인 응답(결과 · 상태 둘 다)에 after · answer · marks 없음 (대표이사 확인 전) · 등급 C 그대로")
        full = {**cut, "answer": "가상 상향", "after": "A", "marks": {"1": "A"}}
        ok(row_of(res(HRA)["team"], LEAD)["appeal"] == full and row_of(res(CEO)["appeals"], LEAD)["appeal"] == full and row_of(res(HEAD)["team"], LEAD)["appeal"] == {"status": "CEO"},
           "HR · 대표이사에게는 바뀔 등급 · 고친 실행목표 · 답변 / 본부장에게는 상태만")
        ok(e3(CEO) == 1 and phone(CEO) == (0, 1) and e3(HRA) == 2, "처리할 일 e3: 대표이사 = 확인할 등급 변경 1 · HR = 남은 OPEN 2")
        ok(hit(ans(HRA, LEAD, grade="A", answer="가상"), 409, ANSWERED), "대표이사 확인을 기다리는 이의에 또 답하면 409")
        ok(all(hit(dec(who, LEAD, w), 403, NO_RIGHT) for who in (HEAD, HRA, HR, LEAD) for w in ("confirm", "back")),
           "확인 · 돌려보내기: 대표이사가 아니면 403 (본부장 · 맡기기 전 HR · 본인)")
        ok(hit(dec(CEO, FIELD, "confirm"), 409, DONE_) and hit(dec(CEO, EMP, "confirm"), 409, WAIT) and hit(dec(CEO, NOCONF, "back"), 404, NO_APPEAL)
           and hit(dec(CEO, 999999, "confirm"), 404, NO_TARGET) and hit(dec(CEO, LEAD, "confirm", year=None), 400, BAD_YEAR),
           "확인 · 돌려보내기: 끝난 이의 409 · HR 답변 전 409 · 이의 없음 404 · 대상 아님 404 · 해 400")
        ok(hit(dec(CEO, LEAD, "back"), 200, OK) and ap(LEAD) == ("OPEN", "C", None, None, "가상 상향", HRA, None, None) and grade_of(LEAD) == "C",
           "바꾸려면_점수와_대표이사: 돌려보내기 → OPEN · after · marks 비움 (답변 글은 HR 이 고치게 남김)")
        ok(took() == [(HRA, *APPEAL), (HRL, *APPEAL), (HR, *APPEAL)] and audits("EVAL_APPEAL_RETURNED") == [(LEAD, {"year": Y})] and by("EVAL_APPEAL_RETURNED") == [(CEO, None)],
           "돌려보내면 HR 전원에게 eval_appeal · 감사로그 EVAL_APPEAL_RETURNED")
        ok(hit(dec(CEO, LEAD, "back"), 409, WAIT) and hit(dec(CEO, LEAD, "confirm"), 409, WAIT) and not took() and res(LEAD)["mine"]["appeal"] == {**cut, "status": "OPEN"},
           "또 돌려보내거나 확인하면 409 · 본인에게는 남긴 답변 글도 보이지 않음 (끝나기 전)")
        ok(hit(ans(HRA, LEAD, grade="A", answer="가상 다시 상향", marks={"1": "A"}), 200, OK) and ap(LEAD)[0] == "CEO" and took() == [(CEO, *APPEAL)], "(HR 이 다시 답변 → 대표이사 확인 대기)")
        ok(hit(dec(CEO, LEAD, "confirm"), 200, OK) and grade_of(LEAD) == "A" and marks_of(LEAD) == ["A", "B", "B"] and ap(LEAD) == ("DONE", "C", "A", '{"1": "A"}', "가상 다시 상향", HRA, CEO, None),
           "바꾸려면_점수와_대표이사: 대표이사 확인 → 등급 A · 실행목표 등급 반영 · DONE · 결정한 사람")
        ok(took() == [(LEAD, *ANSWER)] and audits("EVAL_APPEAL_CONFIRMED") == [(LEAD, {"year": Y})] and by("EVAL_APPEAL_CONFIRMED") == [(CEO, None)],
           "확인 → 신청한 사람에게 eval_answer · 감사로그 EVAL_APPEAL_CONFIRMED")
        lm = res(LEAD)["mine"]
        ok(lm["grade"] == "A" and lm["total"] == 94.0 and lm["appeal"] == {**full, "status": "DONE", "answer": "가상 다시 상향"} and hit(dec(CEO, LEAD, "confirm"), 409, DONE_),
           "확인 뒤 본인 결과: 등급 A · 총점 94.0 · 답변 · 바뀐 등급 · 고친 실행목표가 보임 · 또 확인하면 409")
        ok(audits("EVAL_APPEAL_ANSWERED") == [(FIELD, {"year": Y, "changed": False}), (EMPB, {"year": Y, "changed": False}), (LEAD, {"year": Y, "changed": True}),
                                               (LEAD, {"year": Y, "changed": True})],
           "감사로그 EVAL_APPEAL_ANSWERED {해, changed}")

        # ---- 정원은_따지지_않는다 · HR_본인_이의는_대신_확인_못_한다 ----
        ok(hit(ans(HRA, EMP, grade="B", answer="가상 정원과 상관없이 상향"), 200, OK) and ap(EMP)[:3] == ("CEO", "C", "B") and took() == [(CEO, *APPEAL)],
           "정원은_따지지_않는다: A·B 정원이 찬 묶음(팀원B 가 B)에서도 C → B 답변 200")
        x("INSERT INTO ceo_delegation (ceo_user_id, since) VALUES (?, ?)", CEO, z(12, 16))
        ok(hit(ans(HRL, HR, grade="B", answer="가상 HR 담당 상향"), 200, OK) and ap(HR)[:3] == ("CEO", "C", "B") and took() == [(HRA, *APPEAL)],
           "맡김 → 등급을 바꾸는 답변 알림은 HR 로 (신청한 HR 담당 · 답한 HR 팀장 빼고)")
        ok((e3(HR), e3(HRA), e3(HRL), e3(CEO)) == (1, 1, 1, 2) and phone(HR) == (0, 1) and phone(HRA) == (0, 1) and phone(HRL) == (0, 1) and phone(CEO) == (0, 2)
           and pids(res(HR)["appeals"]) == [EMP] and pids(res(HRA)["appeals"]) == [HR] and pids(res(HRL)["appeals"]) == [EMP] and pids(res(CEO)["appeals"]) == sorted([EMP, HR]),
           "처리할 일 e3: 맡김 → HR 은 확인할 등급 변경 — 자기 이의 · 자기가 쓴 답변은 빼고 (HR 담당 = 팀원 · 대상 아닌 HR = HR 담당 · HR 팀장 = 팀원) · 대표이사 둘 · 휴대폰 숫자 같음")
        ok(hit(dec(HR, HR, "confirm"), 403, NO_RIGHT) and hit(dec(HR, HR, "back"), 403, NO_RIGHT) and ap(HR)[0] == "CEO",
           "HR_본인_이의는_대신_확인_못_한다: 맡겨도 HR 담당자 본인이 자기 이의 확인 · 돌려보내기 403")
        ANSWERER = {"error": "답변한 분은 확인할 수 없어요. 다른 HR이나 대표이사가 확인해요."}
        ok(all(hit(dec(who, uid, w), 403, ANSWERER) for who, uid in ((HRA, EMP), (HRL, HR)) for w in ("confirm", "back")) and ap(EMP)[0] == ap(HR)[0] == "CEO" and not took(),
           "답변한_HR은_확인_못_한다: 맡겨도 등급을 바꾸는 답변을 쓴 HR 은 그 이의를 대신 확인 · 돌려보내기 403 — 그대로 · 알림 없음")
        ok(hit(dec(HR, EMP, "confirm"), 200, OK) and grade_of(EMP) == "B" and ap(EMP)[6:] == (HR, CEO) and by("EVAL_APPEAL_CONFIRMED")[-1] == (HR, CEO) and took() == [(EMP, *ANSWER)]
           and q("SELECT COUNT(*) FROM ev_targets WHERE year = ? AND pool = 'DIV:91' AND out_at IS NULL AND grade = 'B'", Y) == [(2,)],
           "정원은_따지지_않는다: 맡은 HR 이 남의 이의를 대신 확인 200 (decided_behalf · on_behalf_of = 대표이사) → 그 묶음 B 둘")
        ok(hit(dec(CEO, HR, "confirm"), 200, OK) and grade_of(HR) == "B" and ap(HR)[6:] == (CEO, None) and took() == [(HR, *ANSWER)],
           "HR_본인_이의는_대신_확인_못_한다: 대표이사는 200")
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)

        # ---- 결과를_보는_범위 (공개 뒤) ----
        live_set = sorted(live)
        r_lead, r_head, r_leadc, r_emp, r_ceo, r_hra = (res(u) for u in (LEAD, HEAD, LEADC, EMP, CEO, HRA))
        ok(pids(r_lead["team"]) == [EMP, FIELD] and pids(r_head["team"]) == sorted([LEAD, EMP, FIELD, EMPB, NOCONF]) and pids(r_leadc["team"]) == [EMPC]
           and r_emp["team"] == [] and pids(r_ceo["team"]) == live_set and pids(r_hra["team"]) == everyone,
           "결과를_보는_범위: 팀장 = 자기 팀원 · 본부장 = 자기 본부(팀장 포함 · 제외된 사람 빼고) · 다른 팀장에게 그 팀원 없음 · 직원 빈 목록 · 대표이사 = 제외 안 된 전원 · HR = 전원")
        ok(row_of(r_lead["team"], EMP)["appeal"] == {"status": "DONE"} and row_of(r_lead["team"], EMP)["grade"] == "B" and row_of(r_ceo["team"], EMP)["appeal"]["after"] == "B"
           and r_lead["appeals"] == [] and r_emp["mine"]["appeal"]["status"] == "DONE",
           "팀장에게 팀원의 이의는 상태만 · 대표이사에게는 내용까지")

        # ---- 이의_신청은_7일 (공개일 + 7일 한국 23:59) ----
        at(12, 22, 14, 59, 59)  # 한국 12/22 23:59:59
        ok(hit(ask(EMPC, reason="가상 마감 밤"), 200, OK) and ap(EMPC)[0] == "OPEN" and not took(), "이의_신청은_7일: 공개일 + 7일 한국 23:59 200 (근무시간 밖이라 알림은 다음 9시 묶음)")
        at(12, 22, 15)  # 한국 12/23 0시
        ok(hit(ask(LEADC, reason="가상"), 403, LATE) and hit(ask(EMPC, reason="가상"), 403, LATE) and ap(LEADC) is None, "이의_신청은_7일: 다음 날 0시 403 '이의 신청 기간이 끝났습니다.' (기간이 먼저)")
        at(12, 23)
        ok(e3(HRA) == 1 and phone(HRA) == (0, 1), "기간이 끝나도 남은 OPEN 은 처리할 일 e3 에 셈")

        # ---- 감사로그 · 알림 글 ----
        logs = q("SELECT action, after_data, before_data, reason FROM audit_logs WHERE id > ? AND action GLOB 'EVAL_APPEAL*'", audit0)
        ok({a for a, *_ in logs} == {"EVAL_APPEALED", "EVAL_APPEAL_ANSWERED", "EVAL_APPEAL_CONFIRMED", "EVAL_APPEAL_RETURNED"}
           and all(set(json.loads(a)) <= {"year", "changed"} and b is None and rs is None for _, a, b, rs in logs)
           and not any(t in a for _, a, _, _ in logs for t in ("가상", '"A"', '"B"', '"C"')),
           "감사로그에_등급과_글이_없다: 이의 신청 감사로그의 after_data 는 해 · changed 뿐 (사유 · 답변 · 등급 없음)")
        ok({"처리할 이의 신청이 있어요 PC에서 확인해 주세요", "이의 신청에 답변이 왔어요 PC에서 확인해 주세요"} <= set(texts)
           and set(texts) <= {"처리할 이의 신청이 있어요 PC에서 확인해 주세요", "이의 신청에 답변이 왔어요 PC에서 확인해 주세요", "인사평가 결과가 공개됐어요 PC에서 확인해 주세요"},
           "알림 글은 정해 둔 한 줄뿐 — 등급 · 이름 없음")

        # ---- 지난_해 (Y2 도 공개 — 회차 · 한 사람 줄을 바로 넣는다) ----
        y2 = Y2
        x("INSERT INTO kpi_years (year, eval_start, updated_at, updated_by) VALUES (?, ?, ?, 1) ON CONFLICT (year) DO NOTHING", y2, f"{y2}-12-01", z(12, 1, y=y2))
        x("INSERT INTO ev_rounds (year, self_to, evid_due, score_to, grade_to, ceo_to, final_to, started_at, started_by, released_at, released_by, updated_at, updated_by)"
          " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", y2, f"{y2}-12-05", f"{y2}-12-05", f"{y2}-12-06", f"{y2}-12-07", f"{y2}-12-08", f"{y2}-12-09",
          z(12, 1, y=y2), HRA, z(12, 11, y=y2), HRA, z(12, 1, y=y2), HRA)
        x("INSERT INTO ev_targets (year, user_id, pool, division_id, team_id, div_name, team_name, title, is_leader, rater_id, assigner_id, writes_kpi, stage, proposed, grade)"
          " VALUES (?, ?, 'DIV:91', 91, 92, '가상E본부', '가상E팀A', '가상직급', 0, ?, ?, 0, 'DONE', 'A', 'A')", y2, EMP, LEAD, HEAD)
        at(12, 12, y=y2)
        r, old = res(EMP), res(EMP, f"?year={Y}")
        ok(r["years"] == [y2, Y] and r["year"] == y2 and r["mine"]["grade"] == "A" and r["appealDue"] == f"{y2}-12-18" and old["year"] == Y and old["mine"]["grade"] == "B"
           and old["mine"]["appeal"]["status"] == "DONE" and old["years"] == [y2, Y],
           "지난_해: 공개한 두 해 → years 둘(최근부터) · 기본은 올해 · ?year= 지난해 결과(이의로 바뀐 등급 B)")
        ok(res(LEAD)["team"] and pids(res(LEAD)["team"]) == [EMP] and pids(res(LEAD, f"?year={Y}")["team"]) == [EMP, FIELD], "지난_해: 팀원 결과도 그해 떠 둔 줄로")
        ok(res(EMP, "?year=2026") == {"years": [y2, Y], "year": 2026, "offline": True, "released": None, "appealDue": None, "mine": None, "team": [], "appeals": []}
           and res(EMP, "?year=2040") == {"years": [y2, Y], "year": 2040, "offline": False, "released": None, "appealDue": None, "mine": None, "team": [], "appeals": []},
           "포털 평가 전 해는 offline 참 · 시작하지 않은 해는 빈 결과")
        ok(all(hit(get(EMP, f"/api/eval/results?year={v}"), 400, BAD_YEAR) for v in ("abc", "2025", "2101", "", "2027.0", "9" * 50)), "결과: 해가 정수(2026~2100)가 아니면 400")
        at(3, 6, y=Y2 + 1)
        r = res(EMP)
        ok(r["year"] == y2 and r["mine"]["grade"] == "A", "기본 해: 보일 해(cur_year)가 시작 전이면 공개한 해 중 최근")
    finally:
        auth._now = real_now
        push.SYNC, push.SEND = real_sync, real_send
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for h in hashes:
            x("DELETE FROM push_subscriptions WHERE token_hash = ?", h)
            x("DELETE FROM sessions WHERE token_hash = ?", h)
        x("DELETE FROM ceo_delegation WHERE ceo_user_id = ?", CEO)
        x("DELETE FROM ev_appeals WHERE year IN (?, ?)", Y, Y2)
        x(RESET, Y)
        x("UPDATE ev_items SET note = '', self_grade = NULL, grade = NULL WHERE year = ?", Y)
        x("UPDATE ev_rounds SET released_at = NULL, released_by = NULL, att_done_at = ? WHERE year = ?", att0, Y)
        x("UPDATE ev_rounds SET released_at = NULL, released_by = NULL, started_at = NULL, started_by = NULL WHERE year = ?", Y2)
        x("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", CEO)
        for uid in other_ceos:
            x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = ?", uid)


def check_cards(hr) -> None:
    """축하 · 칭찬 카드 (2026-10-26): 보내기(하루 한 장 · 동시에도) · 받는 사람에 따른 종류(생일 · 새 식구 · 입사 기념 · 경사 · 조사) · 받을 수 없는 사람 · 남의 카드 404(HR 도 같다) ·
    읽음 · 고마워요 · 숨기기 · 글 검사 · 열쇠 없음 · 알림(받으면 · 고마워요 · 9시 묶음 새 카드 · 근무시간 밖 · 푸시가 고장나도 응답 그대로) · 감사로그 없음. 글은 서버 열쇠(HR_PAY_KEY)로 잠겨 DB 에 그대로 없다. 급여 비밀번호 검문은 켠 채로 본다(카드 길은 그 앞머리가 아니다).
    가상 사람 6명(6311~6316, 부서는 seed 의 가상팀)을 만들고, 끝나면 카드 줄 · 경사 · 조사 · 이 검사의 세션을 지우고 가상 사람은 비활성으로 둔다(로그인 감사로그가 가리켜 지울 수 없다). 열쇠 · 로그 설정은 되돌린다."""
    import base64
    import unittest

    import cards
    import push

    print("축하 · 칭찬 카드")
    A, B, C, D, E, F = cast = tuple(range(6311, 6317))  # 보내는 사람 · 받는 사람 · 구경꾼 · 포털 사용이 꺼진 사람 · 비활성 · 조직도에서 뺀 사람
    mine = ",".join(map(str, cast))
    raw_key, had = bytes(range(32)), os.environ.pop("HR_PAY_KEY", None)  # 열쇠는 글자로 적지 않고 여기서 만든다
    key = base64.b64encode(raw_key).decode()
    today = auth._today()
    day, soon, ago = today.isoformat(), (today + timedelta(days=3)).isoformat(), (today - timedelta(days=1)).isoformat()
    had_sessions = {r[0] for r in q("SELECT token_hash FROM sessions")}
    logs: list[str] = []

    class Grab(logging.Handler):  # 검사 내내 조용히 모으기만 한다(레벨은 그대로) — 끝에 이름 · 글이 새지 않았는지 본다
        def emit(self, rec: logging.LogRecord) -> None:
            logs.append(rec.getMessage())

    grab, root = Grab(level=logging.DEBUG), logging.getLogger()

    M_SHAPE, M_WHO, M_NOW = "보낸 내용의 모양이 올바르지 않아요.", "카드를 받을 수 없는 분이에요.", "지금은 이 카드를 보낼 수 없어요."
    M_ONCE, M_KEY, M_GONE = "오늘은 이미 이 분께 카드를 보냈어요. 내일 다시 보내 주세요.", "서버 설정이 아직 끝나지 않아 카드를 보낼 수 없어요.", "카드를 찾을 수 없어요."
    is_err = lambda r, code, msg: r.status_code == code and r.json == {"error": msg}  # noqa: E731
    PRAISE, THANKS, COMFORT = ({"kind": k, "reason": None} for k in ("praise", "thanks", "comfort"))
    cheer = lambda reason: {"kind": "cheer", "reason": reason}  # noqa: E731
    get = lambda c, p="/api/cards": c.get(p, base_url=BASE)  # noqa: E731
    post = lambda c, p, body=None, headers=ORIGIN: c.post(p, base_url=BASE, headers=headers, json=body)  # noqa: E731
    form = lambda to=B, **kw: {"to": to, "kind": "praise", "reason": None, "art": 1, "text": "카드글 가상 칭찬이에요", **kw}  # noqa: E731
    send = lambda c, to=B, **kw: post(c, "/api/cards", form(to, **kw))  # noqa: E731
    opts = lambda c, to: get(c, f"/api/cards/options?to={to}")  # noqa: E731
    total = lambda: q(f"SELECT COUNT(*) FROM cards WHERE sender_id IN ({mine}) OR recipient_id IN ({mine})")[0][0]  # noqa: E731
    cnt = lambda s, r: q("SELECT COUNT(*) FROM cards WHERE sender_id = ? AND recipient_id = ?", s, r)[0][0]  # noqa: E731
    row = lambda cid: q("SELECT sender_id, recipient_id, kind, reason, art, body, sent_on, created_at, read_at, thanked_at, hidden_at FROM cards WHERE id = ?", cid)  # noqa: E731
    content = lambda cid: q("SELECT sender_id, recipient_id, kind, reason, art, body, sent_on, created_at FROM cards WHERE id = ?", cid)  # noqa: E731
    wipe = lambda: x(f"DELETE FROM cards WHERE sender_id IN ({mine}) OR recipient_id IN ({mine})")  # noqa: E731
    reset_b = lambda: (x("UPDATE users SET birthday = NULL, birthday_public = 1, hire_date = NULL WHERE id = ?", B), x("DELETE FROM celeb_events WHERE user_id = ?", B))  # noqa: E731

    def client(uid: int):
        c = app.test_client()
        login(c, new_token(f"HW-{uid}"))
        return c

    def event(kind: str, until: str) -> None:
        x("INSERT INTO celeb_events (user_id, kind, label, until_date, created_by, created_at) VALUES (?, ?, '가상', ?, 1, ?)", B, kind, until, auth._iso(auth._now()))

    try:
        os.environ["HR_PAY_KEY"] = key
        root.addHandler(grab)
        for uid, portal, active, hidden, title in ((A, 1, 1, 0, "프로"), (B, 1, 1, 0, None), (C, 1, 1, 0, None), (D, 0, 1, 0, None), (E, 1, 0, 0, None), (F, 1, 1, 1, None)):
            x("INSERT INTO users (id, name, email, department_id, hiworks_user_no, portal_enabled, is_active, deactivated_at, org_hidden, title)"
              " VALUES (?, ?, ?, 2, ?, ?, ?, ?, ?, ?)", uid, f"가상카드{'ABCDEF'[uid - A]}", f"card{uid}@example.invalid", f"HW-{uid}", portal, active,
              None if active else "2027-01-01T00:00:00Z", hidden, title)
        a, b, c = client(A), client(B), client(C)
        audit0 = q("SELECT COUNT(*) FROM audit_logs WHERE action <> 'LOGIN'")

        print("  - 칭찬_보내기_받기")
        ok(get(app.test_client()).status_code == 401 and opts(app.test_client(), B).status_code == 401 and post(app.test_client(), "/api/cards", form()).status_code == 401
           and post(a, "/api/cards", form(), headers={}).status_code == 403 and cnt(A, B) == 0, "로그인 없이는 401 · Origin 없는 보내기는 403 (카드 줄 없음)")
        text1 = "카드글 가상 칭찬이에요\n두 줄"
        r = send(a, art=2, text=text1)
        cid = r.json.get("id")
        ok(r.status_code == 201 and type(cid) is int, f"A → B 칭찬 보내기 201 ({r.status_code} {r.json})")
        info = {"id": cid, "kind": "praise", "reason": None, "art": 2, "text": text1, "date": day}
        who_a, who_b = {"id": A, "name": "가상카드A", "title": "프로", "dept": "가상팀"}, {"id": B, "name": "가상카드B", "title": "", "dept": "가상팀"}
        ok(get(b).json == {"ready": True, "unread": 1, "received": [{**info, "from": who_a, "read": False, "thanked": False, "hidden": False}], "sent": []},
           "B 의 목록: 받은 카드 1장 (글 · 보낸 사람 이름 · 직급 · 부서) · 안 읽음 1 · 보낸 카드 없음")
        ok(get(a).json == {"ready": True, "unread": 0, "received": [], "sent": [{**info, "to": who_b, "read": False, "thanked": False}]},
           "A 의 목록: 보낸 카드 1장 (받는 사람 title 이 없으면 빈 글) · 읽지 않음 · 받은 카드 없음")
        s = row(cid)[0]
        shown = [r, get(b), get(a), opts(a, B), post(b, "/api/cards/999999/read")]  # 보내기 · 상자 · options · 오류 응답 모두
        ok(all(resp.headers.get("Cache-Control") == "no-store" and resp.headers.get("Referrer-Policy") == "no-referrer" for resp in shown),
           "풀어서 내려 주는 글이라 카드 길의 성공 · 오류 응답 모두 Cache-Control: no-store + Referrer-Policy: no-referrer")
        ok(s[:5] == (A, B, "praise", None, 2) and s[6] == day and len(s[7]) == 20 and s[7].endswith("Z") and s[8:] == (None, None, None)
           and "칭찬이에요".encode() not in bytes(s[5]) and "두 줄".encode() not in bytes(s[5]) and cards.unseal_text(raw_key, A, B, day, s[5]) == text1,
           "DB 에는 글이 그대로 없고(잠금) 열쇠 · 보낸 사람 · 받는 사람 · 날로만 풀림 · 읽음 · 고마워요 · 숨김 칸은 비어 있음")

        print("  - 하루_한장_동시")
        r = send(a, kind="thanks", art=3, text="카드글 또 보내요")
        ok(is_err(r, 409, M_ONCE) and cnt(A, B) == 1, "같은 사람에게 같은 날 두 번째는 종류가 달라도 409 (문구)")
        ok(opts(a, B).json["sentToday"] is True and opts(a, C).json["sentToday"] is False and opts(b, A).json["sentToday"] is False,
           "options 의 sentToday: A → B 는 보냄 · A → C 는 아직 · B → A 는 방향이 달라 아직")
        ok(send(a, C).status_code == 201 and send(b, A).status_code == 201 and opts(a, C).json["sentToday"] is True,
           "다른 사람에게는 같은 날도 보낼 수 있고, 받은 사람이 거꾸로 보내는 것도 됨")
        wipe()
        crowd, start, codes = [app.test_client() for _ in range(6)], threading.Barrier(6), []
        for cl in crowd:
            cl.set_cookie(auth.COOKIE, cookie(a))  # A 창 여섯 개

        def go(cl):
            start.wait()
            codes.append(send(cl, text="카드글 동시에").status_code)

        ths = [threading.Thread(target=go, args=(cl,)) for cl in crowd]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        ok(sorted(codes) == [201] + [409] * 5 and cnt(A, B) == 1, f"같은 사람에게 동시에 6번 보내도 1장만 201 · 나머지 409 ({sorted(codes)})")
        real_sent, cards._sent_today = cards._sent_today, lambda *args: False  # 미리 보는 검사를 건너뛰어도 표의 UNIQUE 가 막고 409 로 답한다
        try:
            r = send(a, text="카드글 몰래")
        finally:
            cards._sent_today = real_sent
        ok(is_err(r, 409, M_ONCE) and cnt(A, B) == 1, "미리 보는 검사를 건너뛰어 INSERT 가 표의 UNIQUE 에 걸려도 409 (500 아님)")

        print("  - 생일_사유")
        wipe()
        x("UPDATE users SET birthday = ?, birthday_public = 1 WHERE id = ?", today.strftime("%m-%d"), B)
        ok(opts(a, B).json == {"options": [cheer("birthday"), PRAISE, THANKS], "sentToday": False}, "B 생일이 오늘이고 알림이 켜져 있으면 options 에 cheer/birthday")
        x("UPDATE users SET birthday_public = 0 WHERE id = ?", B)
        ok(opts(a, B).json["options"] == [PRAISE, THANKS], "생일 알림을 끈 사람은 cheer 없음")
        ok(is_err(send(a, kind="cheer", reason="birthday"), 403, M_NOW) and is_err(send(c, kind="cheer", reason="welcome"), 403, M_NOW)
           and cnt(A, B) == 0 and cnt(C, B) == 0, "알림을 끈 사람에게 cheer/birthday · 사유가 없는 cheer/welcome 은 403 (문구) — 줄 없음")
        x("UPDATE users SET birthday_public = 1 WHERE id = ?", B)
        r = send(a, kind="cheer", reason="birthday", art=4, text="카드글 생일 축하해요")
        ok(r.status_code == 201 and row(r.json["id"])[0][2:5] == ("cheer", "birthday", 4)
           and get(b).json["received"][0]["kind"] == "cheer" and get(b).json["received"][0]["reason"] == "birthday", "알림을 켜면 cheer/birthday 201 — 종류 · 사유 · 그림 번호가 그대로 남음")
        ok(is_err(send(c, kind="cheer", reason="anniv"), 403, M_NOW), "해당 없는 사유(입사 기념)는 그래도 403")

        print("  - 새식구_입사기념_경사")
        wipe()
        reset_b()
        x("UPDATE users SET hire_date = ? WHERE id = ?", (today - timedelta(days=10)).isoformat(), B)
        ok(opts(a, B).json["options"] == [cheer("welcome"), PRAISE, THANKS], "입사 10일 전 → cheer/welcome")
        x("UPDATE users SET hire_date = ? WHERE id = ?", date(today.year - 3, today.month, 1).isoformat(), B)
        ok(opts(a, B).json["options"] == [cheer("anniv"), PRAISE, THANKS], "3년 전 이번 달 입사 → cheer/anniv")
        x("UPDATE users SET hire_date = ? WHERE id = ?", date(today.year - 4, today.month, 1).isoformat(), B)
        ok(opts(a, B).json["options"] == [PRAISE, THANKS], "4년 전 입사는 기념이 아님")
        reset_b()
        event("joy", soon)
        ok(opts(a, B).json["options"] == [cheer("joy"), PRAISE, THANKS], "경사(celeb_events joy) 게시 중 → cheer/joy")
        x("UPDATE celeb_events SET removed_at = ? WHERE user_id = ?", auth._iso(auth._now()), B)
        event("joy", ago)
        ok(opts(a, B).json["options"] == [PRAISE, THANKS], "내린 경사 · 게시가 끝난 경사는 없음")
        x("UPDATE users SET birthday = ?, hire_date = ? WHERE id = ?", today.strftime("%m-%d"), (today - timedelta(days=10)).isoformat(), B)
        event("joy", day)
        ok(opts(a, B).json["options"] == [cheer("birthday"), cheer("welcome"), cheer("joy"), PRAISE, THANKS], "여럿이면 생일 · 새 식구 · 입사 기념 · 경사 순서로 모두 (게시 마지막 날까지)")

        print("  - 조사_게시_뒤_축하_거부")
        wipe()
        event("condol", soon)
        ok(opts(a, B).json == {"options": [COMFORT], "sentToday": False}, "조사 게시 중이면 생일 · 새 식구 · 경사가 있어도 options 는 comfort 하나")
        ok(is_err(send(a), 403, M_NOW) and is_err(send(a, kind="thanks"), 403, M_NOW) and is_err(send(a, kind="cheer", reason="birthday"), 403, M_NOW) and cnt(A, B) == 0,
           "조사 중에 praise · thanks · cheer 는 403 (줄 없음)")
        r = send(a, kind="comfort", art=1, text="카드글 가상 위로예요")
        ok(r.status_code == 201 and row(r.json["id"])[0][2:5] == ("comfort", None, 1) and get(b).json["received"][0]["kind"] == "comfort", "comfort 는 201")
        ok(is_err(send(a, kind="comfort"), 409, M_ONCE), "위로도 하루 한 장")
        x("UPDATE celeb_events SET removed_at = ? WHERE kind = 'condol' AND user_id = ?", auth._iso(auth._now()), B)
        ok(is_err(send(c, kind="comfort"), 403, M_NOW) and send(c).status_code == 201, "조사를 내리면 comfort 는 403 · 칭찬은 다시 201")
        reset_b()

        print("  - 받을_수_없는_사람")
        wipe()
        for who, why in ((A, "본인"), (E, "비활성"), (F, "조직도에서 뺀 사람"), (999999, "없는 번호"), ("abc", "숫자가 아닌 값"), ("-1", "음수"), ("", "비어 있음")):
            ok(is_err(opts(a, who), 404, M_WHO), f"options: {why} → 404 (문구)")
        ok(is_err(get(a, "/api/cards/options"), 404, M_WHO), "options: ?to= 가 아예 없어도 404")
        for who, why in ((A, "본인"), (E, "비활성"), (F, "조직도에서 뺀 사람"), (999999, "없는 번호")):
            ok(is_err(send(a, who), 404, M_WHO) and total() == 0, f"보내기: {why} → 404 (문구) — 줄 없음")
        dc = app.test_client()
        login(dc, new_token(f"HW-{D}"))
        ok(dc.get("/api/me", base_url=BASE).status_code == 401, "(전제) 포털 사용이 꺼진 D 는 로그인할 수 없음")
        ok(opts(a, D).status_code == 200 and opts(a, D).json["options"] == [PRAISE, THANKS], "포털 사용이 꺼진 사람도 받을 수 있음 — options 200")
        r = send(a, D, text="카드글 D 에게")
        ok(r.status_code == 201 and cnt(A, D) == 1, "포털 사용이 꺼진 사람에게 보내기 201")
        x("UPDATE users SET portal_enabled = 1 WHERE id = ?", D)
        d = client(D)
        got = get(d).json
        ok(got["unread"] == 1 and [(k["id"], k["text"], k["from"]["id"]) for k in got["received"]] == [(r.json["id"], "카드글 D 에게", A)],
           "포털 사용이 켜진 뒤 첫 로그인: 안 읽음 1 · 카드가 그대로 와 있음")

        print("  - 남의_카드_404")
        wipe()
        cid = send(a, text="카드글 B 에게만").json["id"]
        before = row(cid)
        for who, cl in (("구경꾼 C", c), ("HR", hr), ("보낸 사람 A", a)):
            res = [post(cl, f"/api/cards/{cid}/{path}", body) for path, body in (("read", None), ("thanks", None), ("hide", {"hidden": True}), ("hide", {"hidden": False}))]
            ok(all(is_err(r, 404, M_GONE) for r in res) and row(cid) == before, f"{who}: 남의 카드 read · thanks · hide 는 모두 404 (문구) — DB 그대로")
        ok(all(cid not in [k["id"] for k in get(cl).json["received"] + get(cl).json["sent"]] for cl in (c, hr)), "구경꾼 · HR 의 목록에는 남의 카드가 없음")
        ok(all(is_err(post(b, f"/api/cards/{bad}/{path}", {"hidden": 5}), 404, M_GONE) for bad in ("999999", "abc", "-1", "0", "1.5", "99999999999999999999") for path in ("read", "thanks", "hide")),
           "없는 번호 · 숫자가 아닌 번호 · 너무 큰 번호도 404 (모양이 틀린 본문보다 먼저)")

        print("  - 읽음_고마워요_숨기기")
        wipe()
        ids = [send(sender, text=f"카드글 {n}").json["id"] for n, sender in enumerate((a, c, hr))]
        c1, c2, c3 = ids
        fixed = [content(i) for i in ids]
        unread_of = lambda: get(b).json["unread"]  # noqa: E731
        received = lambda cid: next(k for k in get(b).json["received"] if k["id"] == cid)  # noqa: E731
        ok(unread_of() == 3 and [k["id"] for k in get(b).json["received"]] == ids[::-1], "받은 카드 3장 · 안 읽음 3 · 최근이 위")
        r = post(b, f"/api/cards/{c1}/read")
        ok(r.status_code == 200 and r.json == {"unread": 2} and unread_of() == 2 and get(a).json["sent"][0]["read"] is True and row(c1)[0][8] is not None, "읽음 → 200 {unread 2} · 보낸 사람 목록에 읽음")
        x("UPDATE cards SET read_at = '2000-01-01T00:00:00Z' WHERE id = ?", c1)
        r = post(b, f"/api/cards/{c1}/read")
        ok(r.status_code == 200 and r.json == {"unread": 2} and row(c1)[0][8] == "2000-01-01T00:00:00Z", "이미 읽은 카드의 읽음은 200 · 그대로 (읽은 때가 바뀌지 않음)")
        r = post(b, f"/api/cards/{c1}/thanks")
        ok(r.status_code == 200 and get(a).json["sent"][0]["thanked"] is True and received(c1)["thanked"] is True and row(c1)[0][9] is not None and row(c1)[0][8] == "2000-01-01T00:00:00Z",
           "고마워요 → 200 · A 의 보낸 목록에 thanked · 읽은 때는 그대로")
        x("UPDATE cards SET thanked_at = '2000-01-01T00:00:01Z' WHERE id = ?", c1)
        r = post(b, f"/api/cards/{c1}/thanks")
        ok(r.status_code == 200 and row(c1)[0][9] == "2000-01-01T00:00:01Z" and row(c1)[0][8] == "2000-01-01T00:00:00Z", "두 번째 고마워요도 200 · thanked_at 그대로")
        r = post(b, f"/api/cards/{c2}/thanks")
        ok(r.status_code == 200 and r.json == {"unread": 1} and row(c2)[0][8] is not None and row(c2)[0][9] is not None and received(c2)["read"] is True,
           "안 읽은 카드에 고마워요를 눌러도 읽음이 함께 채워짐 (안 읽음 1)")
        r = post(b, f"/api/cards/{c3}/hide", {"hidden": True})
        ok(r.status_code == 200 and r.json == {"unread": 0} and received(c3)["hidden"] is True and received(c3)["read"] is True and not received(c1)["hidden"]
           and get(hr).json["sent"][0]["read"] is True and row(c3)[0][10] is not None, "숨기기 true → 200 · hidden True · 안 읽은 카드면 읽음도 채워짐 (안 읽음 0) — 다른 카드는 그대로")
        x("UPDATE cards SET hidden_at = '2000-01-01T00:00:02Z', read_at = '2000-01-01T00:00:03Z' WHERE id = ?", c3)
        r = post(b, f"/api/cards/{c3}/hide", {"hidden": True})
        ok(r.status_code == 200 and row(c3)[0][10] == "2000-01-01T00:00:02Z" and row(c3)[0][8] == "2000-01-01T00:00:03Z", "이미 숨긴 카드를 또 숨겨도 200 · 그대로")
        r = post(b, f"/api/cards/{c3}/hide", {"hidden": False})
        ok(r.status_code == 200 and r.json == {"unread": 0} and received(c3)["hidden"] is False and row(c3)[0][10] is None and row(c3)[0][8] == "2000-01-01T00:00:03Z",
           "숨기기 false → hidden False · 읽음은 그대로 (안 읽음 0)")
        ok(post(b, f"/api/cards/{c3}/hide", {"hidden": False}).status_code == 200 and received(c3)["hidden"] is False, "숨기지 않은 카드의 숨기기 풀기도 200")
        ok(all(is_err(post(b, f"/api/cards/{c2}/hide", body), 400, M_SHAPE) for body in (None, {}, {"hidden": "true"}, {"hidden": 1}, {"hidden": None}, [True])),
           "숨기기 본문이 {hidden: true/false} 가 아니면 400")
        ok([content(i) for i in ids] == fixed, "읽음 · 고마워요 · 숨기기를 거치고도 내용 칸(사람 · 종류 · 그림 · 잠근 글 · 날짜)은 바이트까지 그대로")

        print("  - 글_검사")
        wipe()
        bad = (("가" * 151, "글은 150자까지 쓸 수 있어요."), ("가\n나\n다\n라", "글은 세 줄까지 쓸 수 있어요."), ("가\r\n나\r\n다\r\n라", "글은 세 줄까지 쓸 수 있어요."),
               ("   \n  ", "글을 적어 주세요."), ("", "글을 적어 주세요."), (123, "글을 적어 주세요."), (None, "글을 적어 주세요."), (["가"], "글을 적어 주세요."),
               ("가\x00나", "글에 쓸 수 없는 글자가 있어요."), ("가\x7f", "글에 쓸 수 없는 글자가 있어요."), ("가\ud800", "글에 쓸 수 없는 글자가 있어요."))
        ok(all(is_err(send(a, text=t), 400, m) for t, m in bad), "글 검사: 151자 · 줄바꿈 3번(\\r\\n 도) · 빈칸만 · 글이 아님 · 쓸 수 없는 글자(NUL · DEL · 서로게이트) → 400 (각 문구)")
        no_text = form()
        del no_text["text"]
        shapes = [(f"art={v!r}", form(art=v)) for v in (0, 5, "1", 1.5, True, None, [1])] + [("art 없음", {k: v for k, v in form().items() if k != "art"})]
        shapes += [(f"kind={v!r}", form(kind=v)) for v in ("foo", None, ["praise"], 5, "")]
        shapes += [("cheer 인데 사유 없음", form(kind="cheer")), ("praise 인데 사유 있음", form(reason="birthday")), ("없는 사유", form(kind="cheer", reason="bogus")), ("사유가 글이 아님", form(kind="cheer", reason=7))]
        shapes += [(f"to={v!r}", form(v)) for v in (str(B), True, 0, -1, 1.5, 2**40, None, [B])] + [("to 없음", {k: v for k, v in form().items() if k != "to"})]
        shapes += [("본문이 목록", [1]), ("본문이 글", "x")]
        ok(all(is_err(post(a, "/api/cards", body), 400, M_SHAPE) for _, body in shapes), f"모양 검사: art 0/5/\"1\"/실수/true/없음 · 종류 이상 · cheer 와 사유가 안 맞음 · to 이상 · 본문이 객체가 아님 → 모두 400 ({len(shapes)}가지)")
        ok(is_err(a.post("/api/cards", base_url=BASE, headers=ORIGIN, data="{", content_type="application/json"), 400, M_SHAPE) and is_err(post(a, "/api/cards", no_text), 400, "글을 적어 주세요."),
           "깨진 JSON 은 400 · text 가 없으면 글 문구 400")
        ok(total() == 0, "거부된 것은 한 줄도 남기지 않음")
        t150 = "카드글" + "가" * 67 + " \r\n " + "나" * 40 + "\r\n" + "다" * 38  # 줄바꿈을 \n 으로 바꿔 세면 정확히 150자 · 세 줄
        want = "카드글" + "가" * 67 + "\n" + "나" * 40 + "\n" + "다" * 38
        r = send(a, text=t150)
        ok(r.status_code == 201 and len(want) == 150 and get(b).json["received"][0]["text"] == want and get(a).json["sent"][0]["text"] == want,
           "150자 · 세 줄(\\r\\n 은 \\n 으로 · 줄 끝 빈칸은 뗌)은 201 — 받는 사람 · 보낸 사람 모두 같은 글")

        print("  - 열쇠_없음")
        empty = {"ready": False, "unread": 0, "received": [], "sent": []}
        ok(send(hr, text="카드글 둘째").status_code == 201, "(검사용) B 에게 카드를 한 장 더 — 열쇠가 바뀐 경우에 장 수를 세어 보려고")
        os.environ.pop("HR_PAY_KEY")
        ok(get(b).json == empty and get(a).json == empty, "열쇠를 빼면 GET 은 ready False · 빈 목록 · 안 읽음 0 (DB 에는 카드가 있음)")
        ok(is_err(send(c), 503, M_KEY) and cnt(C, B) == 0, "열쇠가 없으면 보내기 503 (문구) — 줄 없음")
        os.environ["HR_PAY_KEY"] = base64.b64encode(bytes(range(31))).decode()
        ok(get(b).json == empty and is_err(send(c), 503, M_KEY), "32바이트가 아닌 열쇠는 없는 것과 같음")
        os.environ["HR_PAY_KEY"] = base64.b64encode(os.urandom(32)).decode()  # 열쇠가 바뀐 경우
        with unittest.TestCase().assertLogs("cards", "ERROR") as cm:  # 로그는 잡아 두어 검사 출력에 ERROR 가 섞이지 않게
            rb, ra = get(b), get(a)
        ok(rb.status_code == 200 and rb.json["ready"] is True and rb.json["received"] == [] and ra.status_code == 200 and ra.json["sent"] == []
           and [rec.args for rec in cm.records] == [(2,), (1,)] and not any(w in line for line in cm.output for w in ("가상카드", "카드글", str(B), str(A))),
           "열쇠가 바뀌어 풀리지 않는 카드는 목록에서 빠지고 500 이 아님 — 서버 로그에는 상자마다 한 번, 빠진 장 수(2 · 1)만 (이름 · 글 · 번호 없음)")
        os.environ["HR_PAY_KEY"] = key
        ok(get(b).json["unread"] == 2 and [k["text"] for k in get(b).json["received"]] == ["카드글 둘째", want], "열쇠를 되돌리면 다시 보임")

        print("  - 카드_알림")
        wipe()
        T_FRI, T_SAT = datetime(2026, 10, 23, 10, 0, tzinfo=auth.KST), datetime(2026, 10, 24, 10, 0, tzinfo=auth.KST)  # 금 · 토 10:00 KST (근무시간 안 · 밖)
        clock, calls, owner, mode = [T_FRI], [], {}, ["ok"]
        real_now, real_send, real_sync = auth._now, push.SEND, push.SYNC
        push_env = {k: os.environ.get(k) for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE")}

        def fake_send(info, payload, ks, ttl):  # 보낸 것을 적어 두고, 고장 내기로 했으면 500 을 돌려주거나 던진다
            calls.append((owner[info["endpoint"]], payload, ttl))
            if mode[0] == "raise":
                raise RuntimeError("검사용 고장")
            return 500 if mode[0] == "500" else 201

        def take() -> list:
            """지금까지 보낸 것 [(받은 사람, 내용, ttl)] — 사람 순으로 돌려주고 비운다."""
            rows = sorted(calls, key=lambda r: r[0])
            calls.clear()
            return rows

        def pushed(uid: int, title: str, badge: int, url: str = "/?go=ws.w3") -> bool:
            """방금 보낸 것이 정확히 uid 에게 한 통이고, 제목 · 내용 · 열 화면 · TTL · 앱 아이콘 숫자가 맞고, 내용에 이름 · 카드 글이 없는가."""
            rows = take()
            return (len(rows) == 1 and rows[0][0] == uid and rows[0][1] == {"title": title, "body": "눌러서 확인해 주세요", "url": url, "badge": badge}
                    and rows[0][2] == push.TTL_EVENT and not any(w in json.dumps(rows[0][1], ensure_ascii=False) for w in ("가상카드", "카드글")))

        def phone(uid: int) -> None:  # 가상 휴대폰 세션 + 구독 한 줄 (고정한 시각 기준) — 세션을 지우면 구독도 함께 지워진다
            t, h, ep = clock[0].astimezone(timezone.utc), str(uid).ljust(64, "5"), f"https://push.example.invalid/card-{uid}"
            x("INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at, kind) VALUES (?, ?, ?, ?, ?, 'phone')",
              h, uid, auth._iso(t), auth._iso(t), auth._iso(t + timedelta(days=30)))
            x("INSERT INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, 'k', 'a', ?)", h, uid, ep, auth._iso(t))
            owner[ep] = uid

        try:
            pub, priv = push.keygen()
            os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
            auth._now = lambda: clock[0].astimezone(timezone.utc)
            push.SYNC, push.SEND = True, fake_send
            for uid in (A, B, C):
                phone(uid)
            ca, cb, cc = client(A), client(B), client(C)
            r = send(ca, text="카드글 알림 시험")
            first = r.json.get("id")
            ok(r.status_code == 201 and pushed(B, "카드가 왔어요", 1),
               "A → B 카드: B 의 휴대폰에만 한 통 (A · C 는 조용) — 제목 · 열 화면 · TTL · 아이콘 숫자 1 · 내용은 안내뿐 (이름 · 글 없음)")
            x("UPDATE users SET portal_enabled = 0 WHERE id = ?", D)
            phone(D)
            ok(send(ca, D, text="카드글 D 에게").status_code == 201 and not take(), "포털 사용이 꺼진 사람에게 보내도 201 · 휴대폰 줄이 있어도 알림 없음")

            print("  - 9시_묶음")
            ok(push.pending(app, B) == [("cards", 1)] and push.pending(app, A) == [] and push.pending(app, C) == [],
               f"처리할 일에 안 읽은 받은 카드가 새 카드로 셈 — 받은 B 만 1건, 보낸 A · 구경꾼 C 는 0 ({push.pending(app, B)})")
            ok(push.morning_text([("cards", 2)]) == {"title": "처리할 일이 2건 있어요", "body": "새 카드 2건", "url": "/"}
               and push.morning_text(push.pending(app, B))["body"] == "새 카드", "9시 묶음 문구: 2건이면 '새 카드 2건' · 1건이면 건수 없이 '새 카드'")
            r = post(cb, f"/api/cards/{first}/read")
            ok(r.status_code == 200 and push.pending(app, B) == [] and not take(), "카드를 읽으면 처리할 일에서 빠지고, 읽음은 알림을 보내지 않음")

            print("  - 고마워요_알림")
            ok(post(cb, f"/api/cards/{first}/thanks").status_code == 200 and pushed(A, "보낸 카드에 고마워요가 왔어요", 0, "/?go=ws.w3.out"),
               "B 의 고마워요 → 보낸 A 의 휴대폰에만 한 통 (B 는 조용) · 누르면 받은 카드 › 보낸 카드 · 내용은 안내뿐 (이름 · 글 없음)")
            ok(post(cb, f"/api/cards/{first}/thanks").status_code == 200 and not take(), "두 번째 고마워요는 아무것도 안 바꾸고 알림도 없음")

            print("  - 알림이_고장나도")
            mode[0] = "raise"
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:
                r = send(ca, C, text="카드글 고장")
            second = r.json.get("id")
            ok(r.status_code == 201 and cnt(A, C) == 1 and len(take()) == 1 and cm.output == ["WARNING:push:event push failed: RuntimeError"],
               "푸시 보내기가 던져도 보내기는 201 · 카드는 저장 · 로그에는 예외 종류만")
            mode[0] = "500"
            with unittest.TestCase().assertLogs("push", "WARNING") as cm:
                r = post(cc, f"/api/cards/{second}/thanks")
            ok(r.status_code == 200 and r.json == {"unread": 0} and row(second)[0][9] is not None and len(take()) == 1 and cm.output == ["WARNING:push:event push rejected: status 500"],
               "푸시 서버가 500 이어도 고마워요는 200 · 저장 · 로그에는 상태 코드만")
            mode[0] = "ok"

            print("  - 근무시간_밖")
            clock[0] = T_SAT
            ca, cb = client(A), client(B)
            r = send(ca, text="카드글 토요일")
            weekend = r.json.get("id")
            ok(r.status_code == 201 and cnt(A, B) == 2 and not take(), "토요일 10시에 보내기: 201 · 저장됨 · 알림 없음 (평일 9~18시만)")
            ok(post(cb, f"/api/cards/{weekend}/thanks").status_code == 200 and row(weekend)[0][9] is not None and not take(), "토요일 10시에 고마워요: 200 · 저장됨 · 알림 없음")
        finally:
            push.SYNC, push.SEND, auth._now = real_sync, real_send, real_now
            for k, v in push_env.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v
        ok(push.SEND is real_send and push.SYNC is real_sync and auth._now is real_now and all(os.environ.get(k) == v for k, v in push_env.items()),
           "알림 검사가 끝나면 보내기 함수 · 시각 · 알림 키를 되돌림")

        print("  - 감사로그_없음")
        ok(q("SELECT COUNT(*) FROM audit_logs WHERE action LIKE '%CARD%' OR entity_type LIKE '%card%'") == [(0,)] and q("SELECT COUNT(*) FROM audit_logs WHERE action <> 'LOGIN'") == audit0,
           "위 일들 뒤에 audit_logs 에 CARD 가 든 action 이 없고, 로그인 말고는 한 줄도 늘지 않음")
        ok(not any("가상카드" in m or "카드글" in m for m in logs), "서버 로그에 이름 · 카드 글이 없음")
    finally:
        root.removeHandler(grab)
        os.environ.pop("HR_PAY_KEY", None)
        if had is not None:
            os.environ["HR_PAY_KEY"] = had
        x(f"DELETE FROM cards WHERE sender_id IN ({mine}) OR recipient_id IN ({mine})")
        x(f"DELETE FROM celeb_events WHERE user_id IN ({mine})")
        made = [h for (h,) in q(f"SELECT token_hash FROM sessions WHERE user_id IN ({mine})") if h not in had_sessions]
        x(f"DELETE FROM sessions WHERE token_hash IN ({','.join('?' * len(made))})", *made)
        x(f"UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z', portal_enabled = 0, birthday = NULL, hire_date = NULL WHERE id IN ({mine})")
    ok(not q(f"SELECT 1 FROM cards WHERE sender_id IN ({mine}) OR recipient_id IN ({mine})") and not q(f"SELECT 1 FROM users WHERE id IN ({mine}) AND is_active = 1")
       and not q(f"SELECT 1 FROM push_subscriptions WHERE user_id IN ({mine})") and os.environ.get("HR_PAY_KEY") == had and grab not in root.handlers,
       "끝나면 카드 줄 · 경사 · 조사 · 세션(휴대폰 구독 포함)을 지우고 가상 사람은 비활성으로 두며 열쇠 · 로그 설정을 되돌림")


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
    sets = r.headers.getlist("Set-Cookie")
    set_cookie = next((c for c in sets if c.startswith(auth.COOKIE + "=")), "")
    entry = next((c for c in sets if c.startswith(auth.ENTRY_COOKIE + "=")), "")
    ok(all(f in set_cookie for f in ("Secure", "HttpOnly", "SameSite=Lax")), "쿠키 세 가지: Secure · HttpOnly · SameSite=Lax")
    ok("Max-Age" not in set_cookie and "Expires" not in set_cookie, "세션 쿠키는 브라우저를 끄면 사라짐 (유지 기간은 서버가 셈)")
    ok(entry.startswith(auth.ENTRY_COOKIE + "=1;") and "Max-Age=60" in entry and "Secure" in entry and "HttpOnly" not in entry,
       "하이웍스로 들어왔다는 표시: 1분짜리, 화면(JS)이 읽을 수 있음")
    ok(app.test_client().get("/api/me", base_url=BASE, headers={"Cookie": entry.split(";")[0]}).status_code == 401,
       "표시 쿠키만으로는 들어올 수 없음")
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
    ok(conf.count("proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;") == 3, "nginx 가 CF-Connecting-IP 를 api 로 넘김 (로그인 · 증빙 올리기 · 그 밖 /api/)")

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

    check_phone(hr, save)
    check_org(hr, save)
    check_led_dept(hr)
    check_news(hr)
    check_workspace(hr)
    check_game(hr)
    check_kpi_owner(hr)
    check_kpi_manager(hr)
    check_kpi_progress(hr)
    check_kpi_sched(hr)
    check_kpi_review(hr)
    check_eval_admin(hr)
    check_eval_view(hr)
    check_eval_self(hr)
    check_eval_score(hr)
    check_eval_close(hr)
    check_eval_appeal(hr)
    with pay_gate_off():  # 아래 검사들은 비밀번호 잠금 이전 동작을 본다 — 잠금은 check_pay_pin 이 켠 채로 본다
        check_payslips(hr)
        check_pay_ask(hr)
        check_withholding(hr)
        check_eval_report(hr)
        check_contracts(hr)
        check_push_targets(hr)
        check_push_api(hr)
        check_push_pending(hr)
        check_push_events(hr)
        check_push_deadlines(hr)
    check_pay_pin(hr)
    check_cards(hr)

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
