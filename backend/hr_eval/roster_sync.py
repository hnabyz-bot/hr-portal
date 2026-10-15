"""직원 명단 동기화 실행 (로그인 설계 §11). 비교 규칙은 domain/roster.py.

    cd backend
    python -m hr_eval.roster_sync            # 미리 보기 — 무엇이 바뀔지 숫자만. DB 는 안 바꾼다
    python -m hr_eval.roster_sync --apply    # 반영 (서버에서 하루 1회)
    python -m hr_eval.roster_sync --check    # 가짜 하이웍스 응답으로 처음부터 끝까지 (CI, 하이웍스를 부르지 않는다)

필요한 값 (서버 .env → docker-compose.yml): HR_DB_PATH, HIWORKS_OFFICE_TOKEN, HIWORKS_DOMAIN.
오피스 토큰·직원 이름·번호·연락처는 화면·로그에 내보내지 않는다 — 숫자만.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from hr_eval.domain.roster import (
    PortalDept,
    PortalUser,
    RosterError,
    SyncPlan,
    parse_orgs,
    parse_users,
    plan_sync,
)
from hr_eval.sql.db import connect, init_db

API_BASE = "https://api.hiworks.com"  # 고정. 다른 곳으로 토큰을 보내지 않는다
Fetch = Callable[[str], object]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """다른 주소로 넘기면 따라가지 않는다 — 따라가면 오피스 토큰이 함께 간다."""

    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


def hiworks_fetch(token: str) -> Fetch:
    opener = urllib.request.build_opener(NoRedirect)

    def fetch(path: str) -> object:
        req = urllib.request.Request(
            API_BASE + path,
            headers={  # GET 이어도 Content-Type 이 없으면 202 + code ERR (설정 가이드 4장)
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with opener.open(req, timeout=20) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as exc:
            raise RosterError(f"하이웍스 응답 {exc.code} ({path})") from None
        except (urllib.error.URLError, OSError) as exc:
            raise RosterError(f"하이웍스에 연결하지 못했습니다 ({type(exc).__name__}, {path})") from None
        if status != 200:  # 202 는 실패다 (code ERR)
            raise RosterError(f"하이웍스 응답 {status} ({path})")
        try:
            return json.loads(raw)
        except ValueError:
            raise RosterError(f"하이웍스 응답이 JSON 이 아닙니다 ({path})") from None

    return fetch


def load_portal(conn: sqlite3.Connection) -> tuple[list[PortalDept], list[PortalUser]]:
    depts = [
        PortalDept(r[0], r[1], r[2], r[3], bool(r[4]))
        for r in conn.execute(
            "SELECT d.id, d.hiworks_node_id, d.name, p.hiworks_node_id, d.is_active"
            " FROM departments d LEFT JOIN departments p ON p.id = d.parent_id"
        )
    ]
    users = [
        PortalUser(r[0], r[1], bool(r[2]), r[3], r[4], r[5], r[6])
        for r in conn.execute(
            "SELECT u.id, u.hiworks_user_no, u.is_active, d.hiworks_node_id, u.email, u.phone, u.mobile"
            " FROM users u JOIN departments d ON d.id = u.department_id"
        )
    ]
    return depts, users


def _audit(conn: sqlite3.Connection, action: str, entity_type: str, entity_id: int | None, after: dict | None = None) -> None:
    """행위자 없음 = 시스템. 연락처 값은 남기지 않는다 (감사로그는 지울 수 없다)."""
    conn.execute(
        "INSERT INTO audit_logs (action, entity_type, entity_id, after_data) VALUES (?, ?, ?, ?)",
        (action, entity_type, entity_id, json.dumps(after, ensure_ascii=False) if after else None),
    )


def apply_plan(conn: sqlite3.Connection, plan: SyncPlan, now: str) -> None:
    """한 트랜잭션. 중간에 실패하면 아무것도 바뀌지 않는다."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        ids = dict(conn.execute("SELECT hiworks_node_id, id FROM departments WHERE hiworks_node_id IS NOT NULL"))
        for n in sorted(plan.dept_add, key=lambda n: n.depth):  # 본부를 먼저 만들어야 팀이 붙는다
            cur = conn.execute(
                "INSERT INTO departments (code, name, level, parent_id, hiworks_node_id) VALUES (?, ?, ?, ?, ?)",
                (f"HW-{n.node_id}", n.name, "TEAM" if n.parent_id else "DIVISION", ids.get(n.parent_id), n.node_id),
            )
            ids[n.node_id] = cur.lastrowid
            _audit(conn, "DEPT_SYNC_ADDED", "department", cur.lastrowid)
        for d, n in plan.dept_update:
            conn.execute(
                "UPDATE departments SET name = ?, level = ?, parent_id = ?, is_active = 1 WHERE id = ?",
                (n.name, "TEAM" if n.parent_id else "DIVISION", ids.get(n.parent_id), d.id),
            )
            _audit(conn, "DEPT_SYNC_UPDATED", "department", d.id)
        for d in plan.dept_close:
            conn.execute("UPDATE departments SET is_active = 0 WHERE id = ?", (d.id,))
            _audit(conn, "DEPT_SYNC_CLOSED", "department", d.id)

        # 새 사람은 포털 사용이 꺼진 채 들어간다 (users 기본값). 사람별 설정·user_roles 는 건드리지 않는다
        for p in plan.user_add:
            cur = conn.execute(
                "INSERT INTO users (name, email, department_id, hiworks_user_no, phone, mobile) VALUES (?, ?, ?, ?, ?, ?)",
                (p.name, p.email, ids[p.node_id], p.user_no, p.phone or None, p.mobile or None),
            )
            _audit(conn, "USER_SYNC_ADDED", "user", cur.lastrowid, {"department_id": ids[p.node_id]})
        for u, p, changed in plan.user_update:
            conn.execute(
                "UPDATE users SET department_id = ?, email = ?, phone = ?, mobile = ? WHERE id = ?",
                (ids[p.node_id], p.email, p.phone or None, p.mobile or None, u.id),
            )
            _audit(conn, "USER_SYNC_UPDATED", "user", u.id, {"changed": list(changed), "department_id": ids[p.node_id]})
        for u in plan.user_deactivate:  # 세션도 바로 끊는다 (로그인 설계 §4)
            conn.execute("UPDATE users SET is_active = 0, deactivated_at = ? WHERE id = ?", (now, u.id))
            conn.execute("UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL", (now, u.id))
            _audit(conn, "USER_SYNC_DEACTIVATED", "user", u.id)

        _audit(conn, "ROSTER_SYNC", "roster", None, plan.counts())
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(db_path: str, fetch: Fetch, *, apply: bool = False, domain: str = "", allow_mass: bool = False) -> dict[str, int]:
    """가져와 비교하고, apply 면 반영한다. 요약(숫자)을 돌려준다. 믿을 수 없는 응답이면 RosterError."""
    user_nos = parse_users(fetch("/hrm/v2/users"))
    nodes, people, at_root = parse_orgs(fetch("/hrm/v2/organizations"))
    init_db(db_path)
    conn = connect(db_path)
    try:
        depts, users = load_portal(conn)
        plan = plan_sync(user_nos, nodes, people, depts, users, domain=domain, allow_mass=allow_mass, at_root=at_root)
        if apply:
            apply_plan(conn, plan, _now())
        return plan.counts()
    finally:
        conn.close()


def _record_failure(db_path: str, reason: str) -> None:
    conn = connect(db_path)
    try:
        _audit(conn, "ROSTER_SYNC_FAILED", "roster", None, {"reason": reason})
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = set(sys.argv[1:] if argv is None else argv)
    if "--check" in args:
        return _check()
    db, token = os.environ.get("HR_DB_PATH", ""), os.environ.get("HIWORKS_OFFICE_TOKEN", "").strip()
    if not db or not token:
        print("HR_DB_PATH 와 HIWORKS_OFFICE_TOKEN 이 필요합니다 (서버 .env · docker-compose.yml)")
        return 2
    apply = "--apply" in args
    try:
        counts = run(
            db, hiworks_fetch(token), apply=apply,
            domain=os.environ.get("HIWORKS_DOMAIN", "").strip(), allow_mass="--allow-mass" in args,
        )
    except (RosterError, sqlite3.Error) as exc:
        print(f"[멈춤] {exc} — 아무것도 바꾸지 않았습니다")
        if apply:
            _record_failure(db, str(exc))
        return 1
    print("[반영 완료]" if apply else "[미리 보기 — DB 는 바꾸지 않았습니다. 반영하려면 --apply]")
    for k, v in counts.items():
        print(f"  {k}: {v}")
    return 0


# ----------------------------------------------------------------------
# --check : 가짜 하이웍스 응답으로 가져오기 → 비교 → 저장까지. 사람·번호·메일은 전부 가짜다.
# ----------------------------------------------------------------------


def _org(nodes: dict, root_entries: list[str] = ()) -> dict:
    """{node_id: (이름, 부모 node_id 또는 None, [user_no, …])} → 하이웍스 조직도 모양."""

    def person(no: str, nid: str) -> dict:
        i = no[1:]
        return {"user_no": no, "mail_id": f"u{i}", "name": f"가상{i}", "email": f"u{i}@example.invalid",
                "phone": f"02-000-{i}", "cell": "", "node_id": nid, "is_admin": "N"}

    def build(nid: str) -> dict:
        name, _, members = nodes[nid]
        return {"node_id": nid, "name": name, "entries": [person(no, nid) for no in members],
                "nodes": [build(c) for c, v in nodes.items() if v[1] == nid]}

    return {"node_id": "C0", "name": "가상회사", "entries": [person(no, "C0") for no in root_entries],
            "nodes": [build(c) for c, v in nodes.items() if v[1] is None]}


def _users(nos: list[str]) -> list[dict]:
    return [{"no": no, "user_id": f"u{no[1:]}", "name": f"가상{no[1:]}"} for no in nos]


def _check() -> int:
    passed = 0

    def ok(cond: bool, label: str) -> None:
        nonlocal passed
        if not cond:
            raise AssertionError(label)
        passed += 1
        print(f"  [통과] {label}")

    T = [f"T{i}" for i in range(1, 25)]
    tree = {
        "D1": ("가상본부1", None, ["T1"]),
        "D11": ("가상팀1", "D1", ["T2", "T3", "T4"]),
        "D12": ("가상팀2", "D1", ["T5", "T6"]),
        "D2": ("가상본부2", None, []),
        "D21": ("가상팀3", "D2", T[6:22]),  # T7~T22
    }
    bodies = {"/hrm/v2/users": _users(T), "/hrm/v2/organizations": _org(tree, ["T23"])}  # T24 는 부서 없음

    def fetch(path: str) -> object:
        return json.loads(json.dumps(bodies[path]))

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "roster.db")
        init_db(db)

        def q(sql: str, *a: object) -> list:
            c = connect(db)
            try:
                return c.execute(sql, a).fetchall()
            finally:
                c.close()

        print("1. 미리 보기")
        c = run(db, fetch)
        ok(c["부서 추가"] == 5 and c["직원 추가"] == 22, "부서 5 · 직원 22명이 추가될 것으로 나옴")
        ok(c.get("회사 바로 아래(올리지 않음)") == 1 and c.get("부서 없음(올리지 않음)") == 1, "회사 바로 아래·부서 없음은 올리지 않음")
        ok(q("SELECT COUNT(*) FROM users")[0][0] == 0, "미리 보기는 DB 를 바꾸지 않음")

        print("2. 처음 반영")
        run(db, fetch, apply=True, domain="example.invalid")
        ok(q("SELECT COUNT(*) FROM users")[0][0] == 22, "직원 22명 추가")
        ok(q("SELECT COUNT(*) FROM users WHERE portal_enabled = 1")[0][0] == 0, "새 사람은 포털 사용이 꺼져 있음")
        ok(q("SELECT level, (SELECT hiworks_node_id FROM departments p WHERE p.id = d.parent_id) FROM departments d"
             " WHERE hiworks_node_id = 'D11'") == [("TEAM", "D1")], "팀은 본부 아래에 붙음")
        again = run(db, fetch, apply=True, domain="example.invalid")
        ok(sum(v for k, v in again.items() if "추가" in k or "변경" in k or "비활성" in k or "닫기" in k) == 0,
           "같은 명단으로 다시 돌리면 바뀌는 것이 없음")

        # HR 이 포털에서 정한 것 — 동기화가 건드리면 안 된다
        uid = lambda no: q("SELECT id FROM users WHERE hiworks_user_no = ?", no)[0][0]  # noqa: E731
        conn = connect(db)
        conn.execute("UPDATE users SET portal_enabled = 1, writes_kpi = 0 WHERE id = ?", (uid("T2"),))
        conn.execute("INSERT INTO user_roles (user_id, role) VALUES (?, 'TEAM_LEADER')", (uid("T2"),))
        conn.execute("UPDATE users SET is_active = 0, deactivated_at = '2027-01-01T00:00:00Z' WHERE id = ?", (uid("T6"),))
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)"
            " VALUES ('s5', ?, '2027-01-01T00:00:00Z', '2027-01-01T00:00:00Z', '2027-01-01T12:00:00Z')", (uid("T5"),)
        )
        conn.close()

        print("3. 바뀐 명단 반영")
        tree2 = dict(tree)
        tree2["D11"] = ("가상팀1", "D1", ["T2", "T4"])
        tree2["D12"] = ("가상팀2", "D1", ["T3", "T6"])  # T3 이동, T5 퇴사, T6 은 HR 이 비활성
        tree2["D21"] = ("가상팀3-새이름", "D2", T[6:22])
        org2 = _org(tree2, ["T23"])
        for n in org2["nodes"][0]["nodes"]:  # T4 전화 바뀜, T6 전화 바뀜(비활성이라 반영하지 않아야 한다)
            for e in n["entries"]:
                if e["user_no"] in ("T4", "T6"):
                    e["phone"] = "02-999-0000"
        bodies.update({"/hrm/v2/users": _users([t for t in T if t != "T5"]), "/hrm/v2/organizations": org2})
        c = run(db, fetch, apply=True)
        ok(c["직원 정보 변경"] == 2 and c["직원 비활성"] == 1 and c["부서 변경"] == 1, "이동 1 · 전화 1 · 퇴사 1 · 부서 이름 1")
        ok(q("SELECT d.hiworks_node_id FROM users u JOIN departments d ON d.id = u.department_id WHERE u.id = ?", uid("T3")) == [("D12",)], "옮긴 사람의 소속이 바뀜")
        ok(q("SELECT is_active, deactivated_at IS NOT NULL FROM users WHERE id = ?", uid("T5")) == [(0, 1)], "사라진 사람은 비활성")
        ok(q("SELECT revoked_at IS NOT NULL FROM sessions WHERE token_hash = 's5'") == [(1,)], "비활성이 되면 세션도 끊김")
        ok(q("SELECT is_active, phone FROM users WHERE id = ?", uid("T6")) == [(0, "02-000-6")], "HR 이 비활성한 사람은 다시 켜지도 고치지도 않음")
        ok(q("SELECT portal_enabled, writes_kpi FROM users WHERE id = ?", uid("T2")) == [(1, 0)]
           and q("SELECT role FROM user_roles WHERE user_id = ?", uid("T2")) == [("TEAM_LEADER",)], "사람별 설정·직책은 그대로")
        ok(q("SELECT name FROM departments WHERE hiworks_node_id = 'D21'") == [("가상팀3-새이름",)], "부서 이름을 따라 고침")
        acts = {r[0] for r in q("SELECT action FROM audit_logs")}
        ok({"USER_SYNC_ADDED", "USER_SYNC_UPDATED", "USER_SYNC_DEACTIVATED", "ROSTER_SYNC"} <= acts, "감사로그에 남음")
        ok(not any("02-" in (r[0] or "") for r in q("SELECT after_data FROM audit_logs")), "감사로그에 연락처 값은 없음")

        print("4. 사라진 부서는 닫는다")
        tree3 = {k: v for k, v in tree2.items() if k != "D12"}
        tree3["D11"] = ("가상팀1", "D1", ["T2", "T3", "T4"])
        bodies["/hrm/v2/organizations"] = _org(tree3, ["T23"])
        c = run(db, fetch, apply=True)
        ok(c["부서 닫기"] == 1 and q("SELECT is_active FROM departments WHERE hiworks_node_id = 'D12'") == [(0,)], "부서를 지우지 않고 닫음")

        print("5. 믿을 수 없는 응답이면 아무것도 바꾸지 않는다")
        snapshot = lambda: (q("SELECT * FROM users"), q("SELECT * FROM departments"), q("SELECT * FROM sessions"))  # noqa: E731
        before = snapshot()
        bad = {
            "실패 코드 (202 + ERR)": {"code": "ERR", "message": "content-type 오류입니다."},
            "빈 사용자 목록": [],
            "모양이 다름": {"items": []},
        }
        for label, body in bad.items():
            bodies["/hrm/v2/users"] = body
            try:
                run(db, fetch, apply=True)
                ok(False, label)
            except RosterError:
                ok(snapshot() == before, f"{label} → 멈추고 그대로")
        bodies["/hrm/v2/users"] = _users([t for t in T if t != "T5"])
        bodies["/hrm/v2/organizations"] = {"node_id": "C0", "name": "가상회사", "entries": [], "nodes": [
            {"node_id": "X1", "name": "a", "entries": [], "nodes": [
                {"node_id": "X2", "name": "b", "entries": [], "nodes": [
                    {"node_id": "X3", "name": "c", "entries": [{"user_no": "T1"}], "nodes": []}]}]}]}
        try:
            run(db, fetch, apply=True)
            ok(False, "3단계 조직")
        except RosterError:
            ok(snapshot() == before, "3단계 조직 → 멈추고 그대로")

        print("6. 한꺼번에 많이 사라지면 멈춘다")
        bodies["/hrm/v2/organizations"] = _org({"D1": ("가상본부1", None, ["T1"])})
        bodies["/hrm/v2/users"] = _users(["T1"])  # 19명이 한꺼번에 사라짐
        try:
            run(db, fetch, apply=True)
            ok(False, "대량 비활성")
        except RosterError:
            ok(snapshot() == before, "11명 이상 비활성 → 멈추고 그대로")
        c = run(db, fetch, apply=False, allow_mass=True)
        ok(c["직원 비활성"] >= 11, "--allow-mass 를 주면 미리 보기에 그대로 나옴")

    print("7. 하이웍스 부르는 부분 — 이 PC 안의 가짜 서버로 (하이웍스는 부르지 않는다)")
    _check_fetch(ok)
    print(f"명단 동기화 검사 {passed}건 전부 통과")
    return 0


def _check_fetch(ok: Callable[[bool, str], None]) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: dict[str, dict] = {}

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:  # noqa: ANN002
            pass

        def do_GET(self) -> None:  # noqa: N802
            seen[self.path] = dict(self.headers)
            routes = {
                "/hrm/v2/users": (200, {}, b'[{"no": "T1", "user_id": "u1", "name": "x"}]'),
                "/bad": (202, {}, b'{"code": "ERR", "message": "content-type"}'),
                "/redir": (302, {"Location": f"http://127.0.0.1:{self.server.server_port}/leak"}, b""),
            }
            status, headers, body = routes.get(self.path, (200, {}, b"{}"))
            self.send_response(status)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

    global API_BASE
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    real, API_BASE = API_BASE, f"http://127.0.0.1:{srv.server_port}"
    try:
        fetch = hiworks_fetch("TEST-TOKEN")
        ok(parse_users(fetch("/hrm/v2/users")) == frozenset({"T1"}), "200 응답을 읽음")
        h = {k.lower(): v for k, v in seen["/hrm/v2/users"].items()}
        ok(h.get("content-type") == "application/json" and h.get("authorization") == "Bearer TEST-TOKEN",
           "Content-Type 과 오피스 토큰을 붙여 보냄")
        for path, label in (("/bad", "202 + code ERR 은 실패"), ("/redir", "다른 주소로 넘기면 따라가지 않음")):
            try:
                fetch(path)
                ok(False, label)
            except RosterError:
                ok(True, label)
        ok("/leak" not in seen, "넘겨받은 주소에 토큰이 가지 않음")
    finally:
        API_BASE = real
        srv.shutdown()


if __name__ == "__main__":
    sys.exit(main())
