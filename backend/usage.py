"""처리 현황 (2026-10-29 인사담당자 결정) — 직원이 아직 하지 않은 일을 HR 이 보고 안내한다. HR 만 본다.

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

  GET  /api/usage   항목별 현황 + 아직 안 한 분 명단 (HR)

**접속 기록은 보지 않는다** (2026-10-29 인사담당자 — "감시한다고 생각하지 않을까"):
언제 들어왔는지(sessions) · 휴대폰 등록은 세지도 보내지도 않는다. 어느 메뉴를 눌렀는지는 기록이 아예 없다.
HR 이 공개 · 발송한 서류를 열었는지와 KPI 계획서를 냈는지 — **직원이 해야 하는 일**만 본다.
받은 카드는 해야 하는 일이 아니라 세지 않는다. 이 범위를 넓히려면 먼저 인사담당자에게 묻는다.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date

from flask import Blueprint, Response, abort, g, jsonify

import auth

bp = Blueprint("usage", __name__)

# 사람 한 줄을 세는 조건 — 포털 사용이 켜진 활성 구성원만 (구성원 설정과 같은 범위)
WHO = "u.is_active = 1 AND u.portal_enabled = 1"


def _one(conn: sqlite3.Connection, sql: str, *args) -> tuple:
    row = conn.execute(sql, args).fetchone()
    return tuple(row) if row else ()


def _ids(conn: sqlite3.Connection, sql: str, *args) -> list[int]:
    return [r[0] for r in conn.execute(sql, args)]


def _item(key: str, label: str, note: str, total: int, left: list[int]) -> dict:
    return {"key": key, "label": label, "note": note, "total": total, "done": total - len(left), "left": left}


def kpi_open(conn: sqlite3.Connection, today: date) -> bool:
    """그해 KPI 작성 기간이 열려 있나 (평가가 시작되면 닫힌다 — kpi.window_open 과 같은 규칙).
    사람별로만 열어 준 기간(kpi_windows)은 보지 않는다 — HR 이 그분을 위해 직접 연 것이라 이미 알고 있다."""
    r = _one(conn, "SELECT edit_from, edit_to, eval_start FROM kpi_years WHERE year = ?", today.year)
    d = today.isoformat()
    return bool(r and r[0] and r[0] <= d <= r[1] and (not r[2] or d < r[2]))


def items(conn: sqlite3.Connection, today: date) -> list[dict]:
    """항목마다 한 줄 — 대상 · 끝낸 수 · 아직 안 한 사람 번호. 지금 해당하는 일이 없는 항목은 빼고 보낸다."""
    out: list[dict] = []
    md = lambda s: s[5:10].replace("-", "/") if s else ""  # noqa: E731  '2026-10-25T…' → '10/25'

    pay = _one(conn, "SELECT ym, opened_at FROM payslip_months WHERE status = 'OPEN' ORDER BY ym DESC LIMIT 1")
    if pay:
        total = _one(conn, "SELECT COUNT(*) FROM payslips WHERE ym = ?", pay[0])[0]
        left = _ids(conn, "SELECT user_id FROM payslips WHERE ym = ? AND seen_at IS NULL", pay[0])
        out.append(_item("pay", f"{int(pay[0][5:7])}월 급여명세서 열람", f"공개 {md(pay[1])}", total, left))

    wh = _one(conn, "SELECT year, opened_at FROM withholding_years WHERE status = 'OPEN' ORDER BY year DESC LIMIT 1")
    if wh:
        total = _one(conn, "SELECT COUNT(*) FROM withholding WHERE year = ?", wh[0])[0]
        left = _ids(conn, "SELECT user_id FROM withholding WHERE year = ? AND seen_at IS NULL", wh[0])
        out.append(_item("wh", f"{wh[0]} 원천징수영수증 열람", f"공개 {md(wh[1])}", total, left))

    total = _one(conn, "SELECT COUNT(*) FROM contracts WHERE year = ? AND status IN ('SENT', 'SIGNED')", today.year)[0]
    if total:
        left = _ids(conn, "SELECT user_id FROM contracts WHERE year = ? AND status = 'SENT'", today.year)
        out.append(_item("ct", f"{today.year} 연봉계약서 서명", "발송한 것만", total, left))

    if kpi_open(conn, today):
        who = f"{WHO} AND u.writes_kpi = 1"
        total = _one(conn, f"SELECT COUNT(*) FROM users u WHERE {who}")[0]
        left = _ids(conn, f"SELECT u.id FROM users u WHERE {who} AND NOT EXISTS"
                          " (SELECT 1 FROM kpi_plans p WHERE p.user_id = u.id AND p.year = ? AND p.status <> 'DRAFT')",
                    today.year)
        out.append(_item("kpi", f"{today.year} KPI 계획서 제출", "작성 기간", total, left))

    return out


@bp.get("/api/usage")
def usage() -> Response:
    if not g.actor.is_hr:
        abort(403)
    conn = auth._db()
    today = auth._now().astimezone(auth.KST).date()
    rows = items(conn, today)

    left: dict[int, list[str]] = {}
    for it in rows:
        for uid in it["left"]:
            left.setdefault(uid, []).append(it["key"])
    people = []
    if left:
        q = ",".join("?" * len(left))
        for r in conn.execute(
            "SELECT u.id, u.name, u.title, d.name, d.level, p.name FROM users u"
            " JOIN departments d ON d.id = u.department_id LEFT JOIN departments p ON p.id = d.parent_id"
            f" WHERE u.id IN ({q})", tuple(left)
        ):
            team = r[4] == "TEAM"
            people.append({"id": r[0], "name": r[1], "title": r[2] or "",
                           "division": r[5] if team else r[3], "team": r[3] if team else "", "left": left[r[0]]})
        people.sort(key=lambda m: (-len(m["left"]), m["name"]))  # 남은 일이 많은 분부터, 같으면 가나다

    return jsonify(today=today.isoformat(), people=len(people),
                   items=[{k: v for k, v in it.items() if k != "left"} for it in rows], rows=people)


def check() -> None:
    """항목 줄 만들기 — 끝낸 수는 대상에서 안 한 사람을 뺀 것이고, 작성 기간은 평가 시작일에 닫힌다."""
    it = _item("pay", "10월 급여명세서 열람", "공개 10/25", 103, [7, 9])
    assert it["done"] == 101 and it["total"] == 103 and it["left"] == [7, 9]
    assert _item("ct", "2026 연봉계약서 서명", "", 3, [])["done"] == 3, "다 했으면 끝낸 수가 대상과 같다"

    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE kpi_years (year INTEGER PRIMARY KEY, edit_from TEXT, edit_to TEXT, eval_start TEXT)")
    assert not kpi_open(c, date(2026, 10, 29)), "그해 줄이 없으면 닫힘"
    c.execute("INSERT INTO kpi_years VALUES (2026, '2026-10-01', '2026-10-31', NULL)")
    assert kpi_open(c, date(2026, 10, 1)) and kpi_open(c, date(2026, 10, 31)), "양 끝 날짜를 포함한다"
    assert not kpi_open(c, date(2026, 9, 30)) and not kpi_open(c, date(2026, 11, 1)), "기간 밖은 닫힘"
    c.execute("UPDATE kpi_years SET eval_start = '2026-10-20'")
    assert kpi_open(c, date(2026, 10, 19)) and not kpi_open(c, date(2026, 10, 20)), "평가 시작일부터 닫힌다"
    c.execute("UPDATE kpi_years SET edit_from = NULL, edit_to = NULL")
    assert not kpi_open(c, date(2026, 10, 10)), "기간을 정하지 않았으면 닫힘"

    print("usage 검사 통과")


if __name__ == "__main__":
    for out in (sys.stdout, sys.stderr):
        out.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["check"]:
        check()
    else:
        sys.exit("사용법: python -m usage check")
