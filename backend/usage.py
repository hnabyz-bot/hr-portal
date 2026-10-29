"""사용 현황 (2026-10-29 인사담당자 — 포털을 못 쓰고 있는 사람을 찾아 돕기 위한 화면). HR 만 본다.

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

  GET  /api/usage   요약 네 칸 · 무엇을 끝냈나 · 사람 명단 (HR)

새로 쌓는 기록은 없다 — 이미 쌓이고 있는 것만 센다 (2026-10-29 인사담당자 "안 쌓음"):
  언제 들어왔나   sessions.last_seen_at (PC · 휴대폰 모두 쓸 때마다 갱신된다 — auth._session)
  무엇을 열었나   payslips.seen_at · withholding.seen_at · contracts.signed_at · cards.read_at (HR 이 공개 · 발송한 것만)
어느 메뉴를 눌렀는지는 기록하지 않는다. 그 기록은 아예 없고, 이 화면을 위해 만들지도 않는다.

로그인 기록은 KEEP_DAYS(12개월)만 둔다 — 이 화면을 열 때 그보다 오래된 sessions 줄을 지운다(_sweep).
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, Response, abort, g, jsonify

import auth

bp = Blueprint("usage", __name__)

KEEP_DAYS = 365  # 로그인 기록 보관 (이 화면의 규칙 3)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def month_start(day: date, back: int = 0) -> date:
    """그달 1일. back=1 이면 지난달 1일."""
    y, m = day.year, day.month - back
    while m < 1:
        y, m = y - 1, m + 12
    return date(y, m, 1)


def _utc(day: date) -> str:
    """한국 날짜 0시 = UTC 로 전날 15시. 기록은 UTC 라 비교 전에 옮긴다."""
    return _iso(datetime(day.year, day.month, day.day, tzinfo=auth.KST).astimezone(timezone.utc))


def _sweep(conn: sqlite3.Connection, now: datetime) -> int:
    """12개월보다 오래 안 쓴 세션 줄을 지운다. 그만큼 지났으면 이미 만료된 줄이다."""
    cur = conn.execute("DELETE FROM sessions WHERE last_seen_at < ?", (_iso(now - timedelta(days=KEEP_DAYS)),))
    conn.commit()
    return cur.rowcount


# 사람 한 줄을 세는 조건 — 포털 사용이 켜진 활성 구성원만 (구성원 설정과 같은 범위)
WHO = "u.is_active = 1 AND u.portal_enabled = 1"
USED = "EXISTS (SELECT 1 FROM sessions s WHERE s.user_id = u.id AND s.last_seen_at >= ? AND s.last_seen_at < ?)"
PHONE = ("SELECT s.{} FROM sessions s WHERE s.user_id = u.id AND s.kind = 'phone' AND s.revoked_at IS NULL"
         " AND s.last_seen_at > ? ORDER BY s.created_at DESC LIMIT 1")


def _one(conn: sqlite3.Connection, sql: str, *args) -> tuple:
    row = conn.execute(sql, args).fetchone()
    return tuple(row) if row else ()


def done_rows(conn: sqlite3.Connection, today: date) -> list[dict]:
    """무엇을 끝냈나 — HR 이 공개 · 발송한 것을 직원이 실제로 열었는지. 없는 항목은 빼고 보낸다."""
    out, m1 = [], _utc(month_start(today))

    pay = _one(conn, "SELECT ym, opened_at FROM payslip_months WHERE status = 'OPEN' ORDER BY ym DESC LIMIT 1")
    if pay:
        n = _one(conn, "SELECT COUNT(*), COUNT(seen_at) FROM payslips WHERE ym = ?", pay[0])
        out.append({"key": "pay", "label": f"{int(pay[0][5:7])}월 급여명세서 열람", "unit": "명",
                    "note": f"공개 {pay[1][5:10].replace('-', '/')}" if pay[1] else "", "total": n[0], "done": n[1]})

    wh = _one(conn, "SELECT year, opened_at FROM withholding_years WHERE status = 'OPEN' ORDER BY year DESC LIMIT 1")
    if wh:
        n = _one(conn, "SELECT COUNT(*), COUNT(seen_at) FROM withholding WHERE year = ?", wh[0])
        out.append({"key": "wh", "label": f"{wh[0]} 원천징수영수증 열람", "unit": "명",
                    "note": f"공개 {wh[1][5:10].replace('-', '/')}" if wh[1] else "", "total": n[0], "done": n[1]})

    ct = _one(conn, "SELECT COUNT(*), COUNT(signed_at) FROM contracts WHERE year = ? AND status IN ('SENT', 'SIGNED')",
              today.year)
    if ct and ct[0]:
        out.append({"key": "ct", "label": f"{today.year} 연봉계약서 서명", "unit": "명", "note": "발송한 것만",
                    "total": ct[0], "done": ct[1]})

    cd = _one(conn, "SELECT COUNT(*), COUNT(read_at) FROM cards WHERE sent_on >= ?", month_start(today).isoformat())
    if cd and cd[0]:
        out.append({"key": "cd", "label": "받은 카드 읽음", "unit": "장", "note": f"{today.month}월에 온 카드",
                    "total": cd[0], "done": cd[1]})

    return out


@bp.get("/api/usage")
def usage() -> Response:
    if not g.actor.is_hr:
        abort(403)
    conn, now = auth._db(), auth._now()
    _sweep(conn, now)
    today = now.astimezone(auth.KST).date()
    live = _iso(now - auth.PHONE_IDLE)  # 이보다 오래 안 쓴 휴대폰 세션은 끊긴 것으로 본다 (auth.members 와 같은 규칙)
    m0, m1, m2 = _utc(month_start(today, 1)), _utc(month_start(today)), _iso(now + timedelta(days=1))

    n = _one(conn,
             f"SELECT COUNT(*), SUM({USED}), SUM({USED}),"
             f" SUM(NOT EXISTS (SELECT 1 FROM sessions s WHERE s.user_id = u.id)),"
             f" SUM(EXISTS ({PHONE.format('user_id')})) FROM users u WHERE {WHO}",
             m1, m2, m0, m1, live)

    rows = conn.execute(
        "SELECT u.id, u.name, u.title, d.name, d.level, p.name,"
        " (SELECT MAX(s.last_seen_at) FROM sessions s WHERE s.user_id = u.id),"
        f" ({PHONE.format('device')}), ({PHONE.format('last_seen_at')})"
        " FROM users u JOIN departments d ON d.id = u.department_id LEFT JOIN departments p ON p.id = d.parent_id"
        f" WHERE {WHO} ORDER BY (SELECT MAX(s.last_seen_at) FROM sessions s WHERE s.user_id = u.id) IS NOT NULL,"
        " (SELECT MAX(s.last_seen_at) FROM sessions s WHERE s.user_id = u.id), u.name",
        (live, live),
    ).fetchall()

    pay = _one(conn, "SELECT ym FROM payslip_months WHERE status = 'OPEN' ORDER BY ym DESC LIMIT 1")
    seen = dict(conn.execute("SELECT user_id, seen_at IS NOT NULL FROM payslips WHERE ym = ?", (pay[0],))) if pay else {}

    out = []
    for r in rows:
        team = r[4] == "TEAM"
        out.append({"id": r[0], "name": r[1], "title": r[2] or "",
                    "division": r[5] if team else r[3], "team": r[3] if team else "",
                    "last": r[6] or "", "phone": r[7] or "", "phoneAt": r[8] or "",
                    "pay": "" if r[0] not in seen else "seen" if seen[r[0]] else "not"})

    return jsonify(today=today.isoformat(), payYm=pay[0] if pay else "", keepDays=KEEP_DAYS,
                   people=n[0], month=n[1] or 0, lastMonth=n[2] or 0, never=n[3] or 0, phones=n[4] or 0,
                   done=done_rows(conn, today), rows=out)


def check() -> None:
    """달 계산과 한국 날짜 옮기기 — 화면 숫자가 달을 넘길 때 어긋나지 않게."""
    assert month_start(date(2026, 10, 29)) == date(2026, 10, 1)
    assert month_start(date(2026, 1, 5), 1) == date(2025, 12, 1)
    assert month_start(date(2026, 3, 31), 1) == date(2026, 2, 1)
    assert month_start(date(2026, 1, 1), 12) == date(2025, 1, 1)

    # 한국 10월 1일 0시 = UTC 9월 30일 15시. 이 변환이 틀리면 달 첫날 접속이 지난달로 세어진다
    assert _utc(date(2026, 10, 1)) == "2026-09-30T15:00:00Z"
    assert _utc(date(2026, 1, 1)) == "2025-12-31T15:00:00Z"

    print("usage 검사 통과")


if __name__ == "__main__":
    for out in (sys.stdout, sys.stderr):
        out.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["check"]:
        check()
    else:
        sys.exit("사용법: python -m usage check")
