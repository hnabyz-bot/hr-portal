"""KPI 현황 규칙 (순수 함수) — 그 달 요약 · 확인 상태 · 부진 알람과 알람 끄기 · 화면과 같은 반올림 · 경영계획 글 검사.

서버(kpi.py)가 KPI 현황 · 월간 확인 · 확인 메모 · 알람 끄기 · 경영계획을 다룰 때 같은 규칙을 쓰도록 따로 떼어놨다. 입출력(DB · 시계)은 없다.
오늘(today)은 부르는 쪽이 한국 날짜로 넘긴다. 도메인은 auth 를 들여오지 않으므로 한국 시간대(KST)는 여기에 따로 둔다.
`prog` 는 kpi.progress_json 이 돌려주는 진행 JSON 그대로다 (year · items; 정기 줄은 months, 비정기 줄은 scheds).

- 부진 알람은 화면이 보여 주는 반올림한 숫자로 따진다: js_round(기대) − js_round(진행률) ≥ 기준.
  파이썬 round 는 56.5 → 56 (짝수쪽)이라 화면(Math.round → 57)과 달라지므로 쓰지 않는다.
- 알람을 끈 실행목표는 부진을 따질 때 뺀다. 끄기는 고른 달 말일까지(그해 안에서만) 이어진다.
- 월간 확인 상태: 확인 단계 없음(NOSTEP) · 적을 것 없음(NA) · 안 씀(NONE) · 확인함(DONE) · 확인 전(TODO).
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

#: 부진 알람 기준 기본값(퍼센트포인트). 기간 줄이 없는 해도 이 값이다.
GAP_DEFAULT = 20

#: 글자 수 한도 (서버 · 화면 · DB 같게): 확인 메모 · 답글 200, 알람 끄기 "직접 적기" 50,
#: 경영계획 목표 60 · 내용 300 · 목표는 20개까지.
MEMO_MAX = 200
MUTE_TEXT_MAX = 50
BIZ_GOAL_MAX = 60
BIZ_DETAIL_MAX = 300
BIZ_MAX = 20

#: 알람을 끄는 이유: 올해 내내 · 이번 달부터 고른 달까지 · 기다리는 중 · 직접 적기.
MUTE_WHY = ("year", "month", "wait", "etc")

KST = timezone(timedelta(hours=9))


def js_round(x: float) -> int:
    """화면의 Math.round 와 같은 반올림 (0.5 는 올림)."""
    return math.floor(x + 0.5)


def month_summary(prog: dict, month: int) -> dict:
    """그 달 한 사람의 기록 현황: {"reg": [쓴 수, 쓸 수], "irr": [결과 쓴 수, 일정 수], "records": 쓴 수 합, "noSched": 비정기가 있는데 그 달 일정이 없다}.

    정기는 그 달이 해당 없음(NA)이 아닌 실행목표가 쓸 수, 그중 DONE 이 쓴 수.
    비정기는 실제 기한(due)이 prog["year"]년 그 달인 일정이 일정 수, 그중 결과가 있는 것이 쓴 수."""
    year = prog["year"]
    reg, irr, has_irr = [0, 0], [0, 0], False
    for it in prog["items"]:
        if it["kind"] == "reg":
            state = it["months"][str(month)]["state"]
            if state != "NA":
                reg[1] += 1
                reg[0] += state == "DONE"
        else:
            has_irr = True
            for s in it["scheds"]:
                due = date.fromisoformat(s["due"])
                if (due.year, due.month) == (year, month):
                    irr[1] += 1
                    irr[0] += s["result"] is not None
    return {"reg": reg, "irr": irr, "records": reg[0] + irr[0], "noSched": has_irr and irr[1] == 0}


def check_state(step: bool, summary: dict | None, checked: bool) -> str:
    """월간 확인 상태. NOSTEP(확인 단계 없음) · NA(적을 것 없음) · NONE(안 씀) · DONE(확인함) · TODO(확인 전).
    summary 는 month_summary 결과, 확정본이 없으면 None."""
    if not step:
        return "NOSTEP"
    if summary is None or not (summary["records"] or summary["reg"][1] or summary["irr"][1]):
        return "NA"
    if summary["records"] == 0:
        return "NONE"
    return "DONE" if checked else "TODO"


def is_muted(year: int, until_month: int, today: date) -> bool:
    """알람 끄기가 아직 이어지나: 그해 안에서 고른 달 말일까지. 해가 바뀌면 다시 켜진다."""
    return today.year == year and today.month <= until_month


def mute_until(why: object, month: object, today: date) -> int:
    """알람 끄기에서 고른 이유 · 달 → 끄는 마지막 달. "year" 는 12월까지, 그 밖은 이번 달~12월 정수(참/거짓 아님)."""
    if why not in MUTE_WHY:
        raise ValueError("이유를 하나 골라 주세요.")
    if why == "year":
        return 12
    if isinstance(month, bool) or not isinstance(month, int) or not today.month <= month <= 12:
        raise ValueError("이번 달부터 12월 사이에서 골라 주세요.")
    return month


def alarm(items: Sequence[dict], values: Sequence[float], expects: Sequence[float], muted: Sequence[bool], gap: int) -> dict:
    """부진 알람. 끄지 않은 실행목표만 가중 평균(Σ가중치×값 / Σ가중치)해 진행률 · 기대를 구하고,
    화면과 같이 반올림한 숫자의 차이가 기준(gap) 이상이면 slow. 남는 것이 없으면 {"slow": False, "pct": None, "expected": None}."""
    live = [(it["weight"], v, e) for it, v, e, off in zip(items, values, expects, muted) if not off]
    total = sum(w for w, _, _ in live)
    if total <= 0:  # 남는 줄이 없거나 가중치가 모두 0 (검사가 막지만 죽지 않게)
        return {"slow": False, "pct": None, "expected": None}
    pct = sum(w * v for w, v, _ in live) / total
    expected = sum(w * e for w, _, e in live) / total
    return {"slow": js_round(expected) - js_round(pct) >= gap, "pct": pct, "expected": expected}


def same_kst_day(iso_utc: str, today: date) -> bool:
    """"YYYY-MM-DDTHH:MM:SSZ"(UTC)로 적힌 시각이 한국 날짜로 today 와 같은 날인가."""
    at = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return at.astimezone(KST).date() == today


def clean_biz_plan(goals: object) -> list[dict]:
    """경영계획 목록 → [{"goal", "detail"}]. 앞뒤 공백을 빼고 둘 다 빈 줄은 버린다. 잘못이면 쉬운 문구의 ValueError.
    제어문자 검사는 엔드포인트가 kpi._chars 로 한다."""
    if not isinstance(goals, list):
        raise ValueError("경영계획은 목표 목록으로 보내 주세요.")
    rows = []
    for g in goals:
        if not isinstance(g, dict) or not isinstance(g.get("goal"), str) or not isinstance(g.get("detail"), str):
            raise ValueError("경영계획은 줄마다 목표와 내용을 글자로 보내 주세요.")
        goal, detail = g["goal"].strip(), g["detail"].strip()
        if not goal and not detail:
            continue
        if not goal:
            raise ValueError("내용만 있는 줄이 있어요. 목표를 함께 적어 주세요.")
        if len(goal) > BIZ_GOAL_MAX:
            raise ValueError(f"목표는 {BIZ_GOAL_MAX}자까지 적을 수 있어요.")
        if len(detail) > BIZ_DETAIL_MAX:
            raise ValueError(f"내용은 {BIZ_DETAIL_MAX}자까지 적을 수 있어요.")
        rows.append({"goal": goal, "detail": detail})
    if len(rows) > BIZ_MAX:
        raise ValueError(f"목표는 {BIZ_MAX}개까지 적을 수 있어요.")
    return rows
