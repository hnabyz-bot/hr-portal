"""KPI 진행 규칙 (순수 함수) — 달 마감 · 진행률 · 기대 진행률 · 일정 상태 · 바뀐 기한 · 업무 제목 · 링크 검사.

서버(kpi.py)가 진행 기록을 받고 진행 JSON 을 만들 때 같은 규칙을 쓰도록 따로 떼어놨다. 입출력(DB · 시계)은 없다.
날짜는 `datetime.date`, 일정 기한(due)은 "YYYY-MM-DD" 글자다. 오늘(today)은 부르는 쪽이 한국 날짜로 넘긴다.
업무 줄(item)은 kpi_plan.py 맨 위에 적힌 확정본 모양이다 (kind "reg"(정기) | "irr"(비정기), target, scheds=[{sid, due, name}], weight).

- 달 한 줄은 그 달 1일부터 다음 달 7일까지 쓴다 (12월은 다음 해 1월 7일). 그해 기록은 다음 해 1월 7일까지다.
- 일정은 기한이 지나도 14일까지는 주황(LATE), 그 뒤는 빨강(OVERDUE)이다. 기한이 14일 넘게 지난 일정만 "기대"에 들어간다.
- 정기의 기대 진행률은 화면의 kvExpect(public/index.html)와 같은 식이다: (1월 1일부터 오늘까지 날수) / (그해 날수) × 100.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Collection, Sequence

#: 글자 수 한도 (서버 · 화면 같게): 월 한 줄 · 결과 한 줄 200, 늦어지는 이유 100, 링크 300.
NOTE_MAX = 200
WHY_MAX = 100
LINK_MAX = 300

#: 달 / 그해 기록을 마감하는 다음 달(해) 날짜, 일정이 주황으로 머무는 기한 뒤 날수.
GRACE_DAY = 7
LATE_DAYS = 14


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _deadline(year: int, month: int) -> date:
    """그 달 한 줄의 마감일 (다음 달 7일, 12월은 다음 해 1월 7일)."""
    return date(*_next_month(year, month), GRACE_DAY)


def month_open(year: int, month: int, today: date) -> bool:
    return date(year, month, 1) <= today <= _deadline(year, month)


def records_open(year: int, today: date) -> bool:
    return today <= date(year + 1, 1, GRACE_DAY)


def month_state(year: int, month: int, today: date, first_month: int | None, hire: date | None, written: bool) -> str:
    """NA(해당 없음) · DONE(썼다) · OPEN(쓸 수 있다) · MISSED(마감이 지났다) · FUTURE(아직 안 왔다).

    first_month 는 그 사람의 첫 확정이 속한 달이다. 입사한 달은 해당 있음 (그 달 말일이 입사일 전이면 해당 없음)."""
    if first_month is None or month < first_month:
        return "NA"
    if hire is not None and date(*_next_month(year, month), 1) - timedelta(days=1) < hire:
        return "NA"
    if written:
        return "DONE"
    if month_open(year, month, today):
        return "OPEN"
    return "FUTURE" if today < date(year, month, 1) else "MISSED"


def effective_dues(content: list[dict], changes: Sequence[tuple[int, str]]) -> dict[int, str]:
    """확정본의 {sid: due} 에 확인된 변경 (sid, to_due) 를 오래된 것부터 덮어쓴다. 확정본에 없는 sid 의 변경은 무시한다."""
    dues = {s["sid"]: s["due"] for g in content for it in g.get("items") or [] for s in it.get("scheds") or []}
    for sid, to_due in changes:
        if sid in dues:
            dues[sid] = to_due
    return dues


def sched_state(due: str, today: date, done: bool) -> str:
    """DONE · UPCOMING(기한 안) · LATE(기한 뒤 14일까지, 주황) · OVERDUE(그 뒤, 빨강)."""
    if done:
        return "DONE"
    d = date.fromisoformat(due)
    if today <= d:
        return "UPCOMING"
    return "LATE" if today <= d + timedelta(days=LATE_DAYS) else "OVERDUE"


def item_pct(item: dict, actual: float | None, pct: int | None, done_sids: Collection[int]) -> float:
    """막대에 쓰는 0~100 값. 정기+목표는 실적/목표, 정기(목표 없음)는 직접 입력한 진행률, 비정기는 결과 쓴 일정 비율."""
    if item.get("kind") == "irr":
        scheds = item.get("scheds") or []
        return sum(s["sid"] in done_sids for s in scheds) / len(scheds) * 100 if scheds else 0.0
    target = item.get("target")
    if target is None or target <= 0:   # 검사가 막지만 죽지 않게
        return float(pct or 0)
    return min(100.0, actual / target * 100) if actual is not None else 0.0


def item_expected(item: dict, year: int, today: date, dues: dict[int, str]) -> float:
    """기대 진행률. 정기는 그해 날수 비율(화면 kvExpect 와 같은 식, 반올림하지 않는다), 비정기는 기한이 14일 넘게 지난 일정의 비율."""
    if item.get("kind") == "irr":
        scheds = item.get("scheds") or []
        late = sum(sched_state(dues.get(s["sid"], s["due"]), today, False) == "OVERDUE" for s in scheds)
        return late / len(scheds) * 100 if scheds else 0.0
    start = date(year, 1, 1)
    if today < start:
        return 0.0
    if today > date(year, 12, 31):
        return 100.0
    return ((today - start).days + 1) / (date(year + 1, 1, 1) - start).days * 100


def overall(items: Sequence[dict], values: Sequence[float]) -> float:
    """Σ(가중치 × 값) / 100. 가중치는 합이 100 인 숫자, 값은 0~100."""
    return float(sum(it["weight"] * v for it, v in zip(items, values)) / 100)


def task_title(sched_name: str, action: str) -> str:
    """나의 업무에 올리는 제목. 일정 이름이 비면 실행목표 문장을 쓴다."""
    return "KPI- " + (sched_name.strip() or action.strip())


def valid_link(s: str) -> bool:
    """http:// 또는 https:// 로 시작하고 그 뒤에 글자가 있으며, 빈칸 · 제어문자가 없고 300자 이하."""
    if not isinstance(s, str) or len(s) > LINK_MAX:
        return False
    head = s[:8].lower()
    prefix = "https://" if head.startswith("https://") else "http://" if head.startswith("http://") else None
    return prefix is not None and len(s) > len(prefix) and not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in s)
