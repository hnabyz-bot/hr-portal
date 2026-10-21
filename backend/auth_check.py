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
    ok(inbox(lead) == {"items": [item], "team": {**zero, "NONE": 2, "SUBMITTED": 1}},
       "팀장 처리함에 1건 (직급 · 제출 시각 · 행 수) · 우리 팀 상태: 제출 1 · 아직 안 씀 2")
    x("UPDATE users SET writes_kpi = 0 WHERE id = 42")
    ok(inbox(lead)["items"] == [], "KPI 를 안 쓰게 된 사람의 제출은 처리함에서 빠짐")
    x("UPDATE users SET writes_kpi = 1, is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = 42")
    ok(inbox(lead)["items"] == [], "비활성이 된 사람의 제출도 빠짐")
    x("UPDATE users SET is_active = 1, deactivated_at = NULL WHERE id = 42")
    ok(inbox(lead)["items"] == [item], "(되돌리면 다시 처리함에 보임)")
    ok(inbox(a2) == {"items": [], "team": zero} and inbox(b1) == {"items": [], "team": zero}
       and inbox(leadb) == {"items": [], "team": {**zero, "NONE": 2}} and inbox(hr) == {"items": [], "team": zero},
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
           for c in (a2, leadb, head, ceo)), "남의_계획서는_못_본다: 같은 팀 다른 팀원 · 다른 팀장 · 본부장 · 대표이사 403")
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
           and set(p) == {"year", "version", "open", "schedOpen", "items", "total", "totalExpected", "todo", "next"}, "읽기: 해 · 판 · 기록 기간 · 일정 변경 기간 · 행 순서")
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
        ok(all(err(get(EMP, who), 403, "이 KPI를 볼 권한이 없습니다.") for who in (PEER, OTHER, HEAD, FIELD)),
           "남의_기록은_못_본다: 같은 팀 다른 팀원 · 다른 팀장 · 본부장 · 현장직 403")
        ok(get(EMP, LEAD).status_code == 200 and get(EMP, LEAD).json == mine and get(EMP, HR).json == mine, "바로 위 관리자(팀장) · HR 은 봄 (본인 화면과 같은 내용)")
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

    NO_KPI, NO_PLAN, NO_AUTH = "KPI 작성 대상이 아닙니다.", "확정된 KPI가 없습니다.", "이 요청을 처리할 권한이 없습니다."
    EDITING = "지금은 계획서를 고치는 중이라 일정 변경을 요청할 수 없습니다."
    STARTED, WAITING = "평가가 시작되어 일정을 바꿀 수 없습니다.", "이미 확인을 기다리는 요청이 있습니다."
    NO_SID, HAS_RESULT = "지금 확정본에 없는 일정입니다.", "결과를 이미 쓴 일정은 날짜를 바꿀 수 없습니다."
    BAD_DUE, SAME_DUE = f"{year}년 안의 날짜(YYYY-MM-DD)를 써 주세요.", "지금 기한과 같은 날짜예요. 다른 날짜를 써 주세요."
    BAD_REASON, BAD_NOTE = "일정을 바꾸려는 이유를 200자 안으로 써 주세요.", "돌려보내는 이유를 200자 안으로 써 주세요."
    BAD_REASON_CHARS, BAD_NOTE_CHARS = "일정을 바꾸려는 이유에 쓸 수 없는 글자가 있습니다.", "돌려보내는 이유에 쓸 수 없는 글자가 있습니다."
    NOT_YOURS = "본인의 요청만 취소할 수 있습니다."
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
           and err(ask(EMP, 1, ymd(10, 1), year=year + 1), 404, NO_PLAN) and n_rows() == 0, "해는 2026~2100 정수 — 틀리면 400 · 계획서가 없는 해는 404")
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
        ok(version1() == v1, "판 1 은 끝까지 그대로")
        ok(err(ask(EMP, 2, ymd(12, 20)), 400, NO_SID), "새 판에서 지운 일정은 요청할 수 없음")
        at(6, 23)
        r11 = ask(EMP, 1, ymd(10, 20)).json["id"]
        ok(q("SELECT from_due FROM kpi_sched_changes WHERE id = ?", r11) == [(ymd(10, 1),)] and act(LEAD, r11, "confirm").status_code == 200
           and (sched(prog(), 1)["due"], sched(prog(), 1)["origDue"]) == (ymd(10, 20), ymd(10, 1)),
           "새 판 뒤의 변경은 새 판 기한 위에 덮임 (그 전에 확인된 변경은 새 판에 이미 들어 있어 다시 덮지 않음)")

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
