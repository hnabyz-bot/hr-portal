"""경비 산출기의 산출 기준 — HR 이 화면에서 바꾼다 (2026-11-10 인사담당자 결정: 연비가 바뀔 때마다 코드를 고치지 않게).

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 HR 인지만 본다.

  GET  /api/trip/rules                  HR 만. 오늘 기준 · 저장한 줄 전부(늦은 시작일부터) · 처음 기준
  POST /api/trip/rules                  HR 만. {"start": "YYYY-MM-DD", "data": {칸: 숫자}} — 같은 시작일이면 고친다
  POST /api/trip/rules/<start>/remove   HR 만. 잘못 넣은 줄 지우기

기준은 적용 시작일마다 한 줄(024_trip_rules.sql). 출장일자가 그날 이후면 그 줄 — 가장 늦은 시작일 ≤ 출장일자(for_day).
줄이 없으면 app_config.py 의 값(DEFAULT, 처음 기준). 계산(/api/calculate)과 자주 묻는 질문(/api/rules/faq)이 for_day 를 쓴다.
감사로그 TRIP_RULES_SET · TRIP_RULES_REMOVED 에 전 · 후 숫자 (기준 숫자는 회사 규정이라 감추지 않는다).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from flask import Blueprint, Response, g, jsonify, request

import app_config
import auth
from hr_eval.domain.models import AuditEntry

bp = Blueprint("trip_rules", __name__)

# 칸 → (가장 큰 값, 소수 자리). 연비 · 충전 단가는 0 보다 커야 하고(나눗셈) 활동비는 0원도 된다(활동비를 없애는 규정).
FIELDS = {
    "under_1800": (50, 1),     # 1,800cc 미만 km/L
    "over_1800": (50, 1),      # 1,800cc 이상 km/L
    "lpg": (50, 1),            # LPG km/L
    "electric": (50, 1),       # 전기차 km/kWh
    "charge_price": (5000, 1),  # 전기차 충전 단가 원/kWh
    "allowance": (1_000_000, 0),  # 활동비 원
}
DEFAULT = {
    "under_1800": app_config.FUEL_EFFICIENCY_UNDER_1800,
    "over_1800": app_config.FUEL_EFFICIENCY_OVER_1800,
    "lpg": app_config.FUEL_EFFICIENCY_LPG,
    "electric": app_config.FUEL_EFFICIENCY_ELECTRIC,
    "charge_price": app_config.FUEL_TYPE_OPTIONS["electric"]["fixed_price"],
    "allowance": app_config.DAILY_ALLOWANCE_AMOUNT,
}
FIRST_DAY = date(2020, 1, 1)
AHEAD_DAYS = 2 * 366  # 시작일은 2년 뒤까지


def clean(b: object) -> dict | None:
    """화면이 보낸 기준 → 저장할 모양(정수로 떨어지면 정수). 칸이 빠졌거나 · 더 있거나 · 숫자 · 범위 · 소수 자리가 틀리면 None."""
    if not isinstance(b, dict) or set(b) != set(FIELDS):
        return None
    out = {}
    for k, (top, places) in FIELDS.items():
        v = b[k]
        if type(v) not in (int, float) or v != v or not (0 <= v <= top) or (v == 0 and k != "allowance") or round(v, places) != v:
            return None
        out[k] = int(v) if float(v).is_integer() else v
    return out


def _start(s: object, today: date) -> str | None:
    try:
        d = date.fromisoformat(s) if isinstance(s, str) and len(s) == 10 else None
    except ValueError:
        return None
    return s if d and FIRST_DAY <= d <= today + timedelta(days=AHEAD_DAYS) else None


def for_day(day: date, conn=None) -> dict:
    """그날 출장에 쓸 기준 — 가장 늦은 시작일 ≤ 그날. 없으면 처음 기준. 저장된 줄은 저장할 때 clean 을 통과했다."""
    row = (conn or auth._db()).execute(
        "SELECT data FROM trip_rules WHERE start_date <= ? ORDER BY start_date DESC LIMIT 1", (day.isoformat(),)).fetchone()
    return {**DEFAULT, **json.loads(row[0])} if row else dict(DEFAULT)


def _state(conn) -> dict:
    today = auth._today()
    rows = conn.execute(
        "SELECT r.start_date, r.data, r.updated_at, u.name FROM trip_rules r JOIN users u ON u.id = r.updated_by"
        " ORDER BY r.start_date DESC").fetchall()
    rules = [{"start": s, "data": {**DEFAULT, **json.loads(d)}, "at": at, "by": by} for s, d, at, by in rows]
    now = next((r["start"] for r in rules if r["start"] <= today.isoformat()), None)
    return {"today": today.isoformat(), "current": now, "rules": rules, "default": DEFAULT}


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


@bp.get("/api/trip/rules")
def rules_get() -> Response:
    """{today, current: 오늘 쓰는 줄의 시작일 | null(처음 기준), rules: [{start, data, at, by(바꾼 HR 이름)}], default}"""
    if (denied := auth._hr_only()) is not None:
        return denied
    return jsonify(_state(auth._db()))


@bp.post("/api/trip/rules")
def rules_put() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    b = _body()
    start, data = _start(b.get("start"), auth._today()), clean(b.get("data"))
    if start is None:
        return jsonify(error="적용 시작일이 올바르지 않습니다. (2020년부터 2년 뒤까지)"), 400
    if data is None:
        return jsonify(error="기준 숫자가 올바르지 않습니다. 연비 · 충전 단가는 0보다 크게(소수 첫째 자리까지), 활동비는 원 단위로 적어 주세요."), 400
    conn = auth._db()
    old = conn.execute("SELECT data FROM trip_rules WHERE start_date = ?", (start,)).fetchone()
    conn.execute(
        "INSERT INTO trip_rules (start_date, data, updated_at, updated_by) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (start_date) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (start, json.dumps(data), auth._iso(auth._now()), g.actor.user_id))
    auth._audit(conn, AuditEntry.by(g.actor, action="TRIP_RULES_SET", entity_type="trip_rules", entity_id=int(start.replace("-", "")),
                                    before_data={"start": start, **json.loads(old[0])} if old else None, after_data={"start": start, **data}))
    return jsonify(_state(conn))


@bp.post("/api/trip/rules/<start>/remove")
def rules_remove(start: str) -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    conn = auth._db()
    old = conn.execute("SELECT data FROM trip_rules WHERE start_date = ?", (start,)).fetchone()
    if old is None:
        return jsonify(error="없는 기준입니다."), 404
    conn.execute("DELETE FROM trip_rules WHERE start_date = ?", (start,))
    auth._audit(conn, AuditEntry.by(g.actor, action="TRIP_RULES_REMOVED", entity_type="trip_rules", entity_id=int(start.replace("-", "")),
                                    before_data={"start": start, **json.loads(old[0])}))
    return jsonify(_state(conn))


# 자주 묻는 질문 글(서버 faq.json)에 적는 자리 — {연비1800미만} → 7, {140÷연비1800미만} → 20 (예시 계산)
NAMES = {"연비1800미만": "under_1800", "연비1800이상": "over_1800", "연비LPG": "lpg", "연비전기차": "electric",
         "충전단가": "charge_price", "활동비": "allowance"}
_TOKEN = re.compile(r"\{(?:(\d+(?:\.\d+)?)÷)?(" + "|".join(NAMES) + r")\}")


def _num(x: float) -> str:
    """소수 첫째 자리까지, 천 단위 쉼표, .0 은 뗀다 — 17.5 · 20 · 347.2 · 20,000"""
    return f"{round(x, 1):,.1f}".removesuffix(".0")


def fill(text: str, rules: dict) -> str:
    def sub(m: re.Match) -> str:
        v = rules[NAMES[m[2]]]
        if not m[1]:
            return _num(v)
        return _num(float(m[1]) / v) if v else m[0]
    return _TOKEN.sub(sub, text)


def check() -> None:
    """python -m trip_rules check — 숫자 모양 · 자주 묻는 질문 자리 (DB 없이)."""
    good = {"under_1800": 8, "over_1800": 7.5, "lpg": 6, "electric": 4.2, "charge_price": 347.2, "allowance": 20000}
    assert clean(good) == good and clean({**good, "under_1800": 8.0})["under_1800"] == 8 and type(clean({**good, "under_1800": 8.0})["under_1800"]) is int
    assert clean({**good, "allowance": 0})["allowance"] == 0, "활동비 0원은 됨"
    for bad in ({**good, "under_1800": 0}, {**good, "lpg": -1}, {**good, "lpg": 51}, {**good, "electric": 4.25}, {**good, "allowance": 20000.5},
                {**good, "charge_price": "347"}, {**good, "lpg": True}, {**good, "lpg": None}, {**good, "lpg": float("nan")},
                {k: v for k, v in good.items() if k != "lpg"}, {**good, "extra": 1}, [], None, {**good, "allowance": 1_000_001}):
        assert clean(bad) is None, bad
    today = date(2026, 11, 10)
    assert _start("2026-12-01", today) == "2026-12-01" and _start("2020-01-01", today) == "2020-01-01"
    for bad in ("2019-12-31", "2026-02-30", "2026-1-01", "20261201", 20261201, None, "2028-11-12"):
        assert _start(bad, today) is None, bad
    assert set(DEFAULT) == set(FIELDS) and clean(dict(DEFAULT)) is not None, "처음 기준도 규칙을 지킨다"
    r = {**good, "under_1800": 7}
    assert fill("1L에 {연비1800미만}km · 이상 {연비1800이상}km\n140 ÷ {연비1800미만} = {140÷연비1800미만}L · {활동비}원 · {충전단가}원",
                r) == "1L에 7km · 이상 7.5km\n140 ÷ 7 = 20L · 20,000원 · 347.2원"
    assert fill("{140÷연비1800이상}", r) == "18.7" and fill("{100÷연비전기차}", r) == "23.8"
    assert fill("{모르는칸} {연비} {140÷활동비}", {**r, "allowance": 0}) == "{모르는칸} {연비} {140÷활동비}", "모르는 자리 · 0 으로 나누기는 그대로"
    print("trip_rules check 통과")


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["check"]:
        check()
    else:
        print("사용법: python -m trip_rules check")
