"""KPI 계획서 규칙 (순수 함수) — 모양 · 가중치 · 정기/비정기 · 고유 번호 · 상태 흐름 · 작성 기간.

서버(kpi.py)가 임시 저장 · 제출 · 확정 때 같은 규칙을 쓰도록 따로 떼어놨다.
계획서(goals)는 JSON 그대로의 모양이다:

    [{"team": str, "personal": str, "items": [
        {"rid": int?, "action": str, "formula": str, "weight": 숫자, "owner": str,
         "S": str, "A": str, "B": str, "C": str, "D": str,
         "kind": "reg"(정기) | "irr"(비정기), "target": 숫자 | None, "unit": str,
         "scheds": [{"sid": int?, "due": "YYYY-MM-DD", "name": str}]}]}]

정기(reg)는 scheds 가 빈 목록, 비정기(irr)는 target 이 None · unit 이 "" 이다.
rid · sid(행 · 일정의 고유 번호)는 새 행 · 새 일정에는 없고, 확정할 때 assign_ids 가 붙인다.
"""

from __future__ import annotations

import copy
import re
from datetime import date
from decimal import Decimal
from typing import Sequence

from hr_eval.domain.errors import Issue, StateConflictError

#: 실행목표 최소 개수 (계획서 전체에서).
MIN_ITEMS = 3

#: 비정기 한 행의 일정 개수 범위.
MAX_SCHEDS = 24

GRADES = ("S", "A", "B", "C", "D")

#: 글자 수 한도 (서버 · 화면 같게). 일정 이름은 SCHED_NAME_MAX.
TEXT_MAX = {"team": 200, "personal": 200, "action": 200, "formula": 200, "owner": 30, "unit": 10, **{g: 100 for g in GRADES}}
SCHED_NAME_MAX = 40

_LABEL = {
    "team": "팀목표", "personal": "개인목표", "action": "실행목표", "formula": "측정식", "owner": "담당자", "unit": "단위",
    **{g: f"{g} 달성 기준" for g in GRADES},
}
_ITEM_TEXTS = ("action", "formula", "owner", "unit", *GRADES)


def _num(x: object) -> Decimal | None:
    """숫자(불리언 아님 · 유한)면 Decimal, 아니면 None. float 은 str 을 거쳐 0.1+0.2 같은 군더더기가 없다."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    d = Decimal(str(x))
    return d if d.is_finite() else None


def _is_id(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 1


def _shape(goals: object) -> list[Issue]:
    """JSON 모양이 틀린 곳 (R1: 비정기에 목표 숫자 · 단위, 정기에 일정이 있으면 이것도). 있으면 다른 규칙은 볼 수 없다."""
    bad: list[Issue] = []

    def no(msg: str, target: str | None = None) -> None:
        bad.append(Issue("SHAPE", msg, target=target))

    if not isinstance(goals, list):
        no("계획서는 목표 묶음의 목록이어야 합니다")
        return bad
    for gi, g in enumerate(goals):
        if not (isinstance(g, dict) and isinstance(g.get("items"), list) and all(isinstance(g.get(k), str) for k in ("team", "personal"))):
            no(f"{gi + 1}번째 목표 묶음의 모양이 틀립니다", f"goal:{gi}")
            continue
        for ii, it in enumerate(g["items"]):
            where = f"{gi + 1}번째 묶음의 {ii + 1}번째 실행목표"
            if not isinstance(it, dict):
                no(f"{where}의 모양이 틀립니다", f"item:{gi}.{ii}")
                continue
            wrong = [k for k in _ITEM_TEXTS if not isinstance(it.get(k), str)]
            if _num(it.get("weight")) is None:
                wrong.append("weight")
            if it.get("rid") is not None and not _is_id(it["rid"]):
                wrong.append("rid")
            scheds = it.get("scheds")
            if not isinstance(scheds, list):
                wrong.append("scheds")
            kind = it.get("kind")
            if kind == "irr":
                if it.get("target") is not None:
                    wrong.append("target")
                if it.get("unit") != "" and "unit" not in wrong:
                    wrong.append("unit")
            elif kind == "reg" and scheds != [] and "scheds" not in wrong:
                wrong.append("scheds")
            if wrong:
                no(f"{where}에 모양이 틀린 칸이 있습니다 ({', '.join(wrong)})", f"item:{gi}.{ii}")
            for si, s in enumerate(scheds if isinstance(scheds, list) else []):
                if not (isinstance(s, dict) and isinstance(s.get("due"), str) and isinstance(s.get("name"), str)
                        and (s.get("sid") is None or _is_id(s["sid"]))):
                    no(f"{where}의 {si + 1}번째 일정 모양이 틀립니다", f"sched:{gi}.{ii}.{si}")
    return bad


def _in_year(due: str, year: int) -> bool:
    """YYYY-MM-DD 로 쓴 실제 날짜이고 그해 안인가."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", due):
        return False
    try:
        return date.fromisoformat(due).year == year
    except ValueError:
        return False


def validate_plan(goals: object, year: int) -> list[Issue]:
    """계획서 검사. 문제를 전부 모아 돌려준다 (빈 목록 = 문제 없음).

    모양(SHAPE)이 틀리면 그것만 돌려준다 — 값을 믿고 볼 수 없다.
    """
    bad = _shape(goals)
    if bad:
        return bad

    issues: list[Issue] = []

    def too_long(text: str, key: str, limit: int, where: str, target: str, label: str | None = None) -> None:
        if len(text) > limit:
            issues.append(Issue("TEXT_LONG", f"{where} {label or _LABEL[key]}: {limit}자까지 쓸 수 있습니다 (지금 {len(text)}자)", target=target))

    total = Decimal(0)
    n = 0   # 계획서 전체에서 몇 번째 실행목표인가 (메시지용)
    for gi, g in enumerate(goals):
        for key in ("team", "personal"):
            too_long(g[key], key, TEXT_MAX[key], f"{gi + 1}번째 묶음", f"goal:{gi}")
        for ii, it in enumerate(g["items"]):
            n += 1
            where, t = f"실행목표 {n}번", f"item:{gi}.{ii}"
            for key in _ITEM_TEXTS:
                too_long(it[key], key, TEXT_MAX[key], where, t)
            if any(not it[gr].strip() for gr in GRADES):
                issues.append(Issue("GRADE_REQUIRED", f"{where}: S~D 달성 기준을 모두 써 주세요", target=t))

            w = _num(it["weight"])
            total += w
            if w <= 0:
                issues.append(Issue("WEIGHT_POSITIVE", f"{where}: 가중치는 0보다 커야 합니다", target=t))

            kind = it.get("kind")
            if kind == "reg":
                x = it.get("target")
                if x is not None and not ((d := _num(x)) is not None and d > 0):
                    issues.append(Issue("TARGET", f"{where}: 목표 숫자는 0보다 큰 숫자이거나 비워야 합니다", target=t))
            elif kind == "irr":
                scheds = it["scheds"]
                if not scheds:
                    issues.append(Issue("SCHED_MIN", f"{where}: 예정 기한을 하나 이상 써 주세요", target=t))
                elif len(scheds) > MAX_SCHEDS:
                    issues.append(Issue("SCHED_MAX", f"{where}: 예정 기한은 {MAX_SCHEDS}개까지 쓸 수 있습니다 (지금 {len(scheds)}개)", target=t))
                for si, s in enumerate(scheds):
                    st = f"sched:{gi}.{ii}.{si}"
                    if not _in_year(s["due"], year):
                        issues.append(Issue("SCHED_DATE", f"{where}: 예정 기한은 {year}년 안의 날짜여야 합니다 ({s['due']!r})", target=st))
                    too_long(s["name"], "name", SCHED_NAME_MAX, f"{where} {si + 1}번째 일정", st, "이름")
            else:
                issues.append(Issue("KIND", f"{where}: 업무 종류를 정기적 또는 비정기적으로 골라 주세요", target=t))

    if n < MIN_ITEMS:
        issues.append(Issue("ITEMS_MIN", f"실행목표는 최소 {MIN_ITEMS}개 필요합니다 (지금 {n}개)"))
    if total != 100:
        issues.append(Issue("WEIGHT_SUM", f"가중치 합계가 100이어야 합니다 (지금 {total.normalize():f})"))
    return issues


def _renumber(things: list[dict], key: str, used: set[int], clash=lambda thing: False) -> None:
    """things 의 key 번호를 정리한다. 이미 있는 번호는 지키되, 중복(뒤의 것)이나 clash 인 것은 새 번호를 준다.

    새 번호 = used(이 계획서에서 쓴 적 있는 번호)와 지킨 번호에 없는 가장 작은 수부터.
    지킨 번호를 다 정한 뒤에 새 번호를 준다 — 앞의 새 행이 뒤에 있는 기존 번호를 가로채지 않게.
    """
    kept: set[int] = set()
    fresh = []
    for thing in things:
        n = thing.get(key)
        if _is_id(n) and n not in kept and not clash(thing):
            kept.add(n)
        else:
            fresh.append(thing)
    taken = used | kept
    n = 0
    for thing in fresh:
        n += 1
        while n in taken:
            n += 1
        thing[key] = n


def assign_ids(goals: list[dict], history: Sequence[list[dict]]) -> list[dict]:
    """확정할 때 새 행(rid) · 새 일정(sid)에 번호를 붙인 새 계획서를 돌려준다 (goals 는 건드리지 않는다).

    history = 이 계획서의 지난 확정본들 (오래된 것부터). 번호는 계획서 전체에서 하나뿐이고,
    한 번 쓴 번호는 지운 행의 것이라도 다시 쓰지 않는다 (옛 진행 기록이 새 행에 이어지지 않게).
    같은 rid 가 마지막 확정본에서 업무 종류가 달랐다면 다른 행으로 보고 새 rid 를 준다.
    """
    out = copy.deepcopy(goals)
    rows = [it for g in out for it in g["items"]]
    old = [it for version in history for g in version for it in g["items"]]
    last = {it["rid"]: it["kind"] for g in (history[-1] if history else []) for it in g["items"]}
    _renumber(
        rows, "rid", {it["rid"] for it in old},
        clash=lambda it: it.get("rid") in last and last[it["rid"]] != it.get("kind"),
    )
    _renumber([s for it in rows for s in it["scheds"]], "sid", {s["sid"] for it in old for s in it["scheds"]})
    return out


ACTIONS = ("save", "submit", "cancel", "confirm", "return", "request_change", "unlock", "reject")

#: (지금 상태, 행동) → 다음 상태. 설계 5.3 그림 그대로.
_FLOW = {
    ("DRAFT", "save"): "DRAFT",
    ("DRAFT", "submit"): "SUBMITTED",
    ("SUBMITTED", "cancel"): "DRAFT",
    ("SUBMITTED", "return"): "DRAFT",
    ("SUBMITTED", "confirm"): "CONFIRMED",
    ("CONFIRMED", "request_change"): "CHANGE_REQUESTED",
    ("CHANGE_REQUESTED", "unlock"): "DRAFT",
    ("CHANGE_REQUESTED", "reject"): "CONFIRMED",
}


def next_status(status: str, action: str) -> str:
    """상태에서 행동을 하면 가는 다음 상태. 안 되는 조합은 StateConflictError."""
    try:
        return _FLOW[(status, action)]
    except KeyError:
        raise StateConflictError(f"지금 상태({status})에서는 {action}을(를) 할 수 없습니다") from None


def window_open(
    today: date,
    year_row: tuple[str | None, str | None, str | None] | None,
    person_row: tuple[str, str] | None,
) -> bool:
    """오늘 KPI 를 쓸 수 있나 (날짜는 ISO 문자열, 양 끝 날짜 포함).

    year_row = 그해 (edit_from, edit_to, eval_start), person_row = 그 사람만의 (edit_from, edit_to).
    사람 기간이 있으면 그것이 우선한다. 평가 시작일부터는 어느 기간이든 닫힌다.
    """
    if year_row and year_row[2] and today >= date.fromisoformat(year_row[2]):
        return False
    start, end = person_row or (year_row[:2] if year_row else (None, None))
    return bool(start and end) and date.fromisoformat(start) <= today <= date.fromisoformat(end)
