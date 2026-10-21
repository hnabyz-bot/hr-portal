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
        ok(r.status_code == 200 and r.json == {"ready": False, "months": []}, "달 목록 ready 거짓")
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
    check_news(hr)
    check_workspace(hr)
    check_game(hr)
    check_payslips(hr)

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
