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

#: 돌려보내기 · 거절 · 수정 요청 사유의 글자 수 한도, 계획서 JSON 한 벌의 크기 한도 (UTF-8 바이트).
REASON_MAX = 200
PLAN_MAX_BYTES = 200 * 1024

_LABEL = {
    "team": "팀목표", "personal": "개인목표", "action": "실행목표", "formula": "측정식", "owner": "담당자", "unit": "단위",
    **{g: f"{g} 달성 기준" for g in GRADES},
}
_ITEM_TEXTS = ("action", "formula", "owner", "unit", *GRADES)
_REQUIRED = ("team", "personal", "action", "formula")   # 공백만 있어도 빈 것 (담당자 · 단위 · 일정 이름은 비워도 된다)


def _num(x: object) -> Decimal | None:
    """숫자(불리언 아님 · 유한)면 Decimal, 아니면 None. float 은 str 을 거쳐 0.1+0.2 같은 군더더기가 없다."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    d = Decimal(str(x))
    return d if d.is_finite() else None


def _is_id(x: object) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 1


def _shape(goals: object) -> list[Issue]:
    """JSON 모양이 틀린 곳. 있으면 다른 규칙은 볼 수 없다.

    R1: 비정기에 목표 숫자 · 단위가 있거나 정기에 일정이 있으면 이것도. R12: 정기의 목표 숫자가 숫자(불리언 아님)도 null 도 아니면 이것도.
    """
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
            elif kind == "reg":
                if scheds != [] and "scheds" not in wrong:
                    wrong.append("scheds")
                if it.get("target") is not None and _num(it["target"]) is None:
                    wrong.append("target")
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

    def check_text(text: str, key: str, limit: int, where: str, target: str, label: str | None = None) -> None:
        label = label or _LABEL[key]
        if key in _REQUIRED and not text.strip():
            issues.append(Issue("TEXT_REQUIRED", f"{where} {label}: 비워 둘 수 없습니다", target=target))
        if len(text) > limit:
            issues.append(Issue("TEXT_LONG", f"{where} {label}: {limit}자까지 쓸 수 있습니다 (지금 {len(text)}자)", target=target))

    total = Decimal(0)
    n = 0   # 계획서 전체에서 몇 번째 실행목표인가 (메시지용)
    for gi, g in enumerate(goals):
        for key in ("team", "personal"):
            check_text(g[key], key, TEXT_MAX[key], f"{gi + 1}번째 묶음", f"goal:{gi}")
        for ii, it in enumerate(g["items"]):
            n += 1
            where, t = f"실행목표 {n}번", f"item:{gi}.{ii}"
            for key in _ITEM_TEXTS:
                check_text(it[key], key, TEXT_MAX[key], where, t)
            if any(not it[gr].strip() for gr in GRADES):
                issues.append(Issue("GRADE_REQUIRED", f"{where}: S~D 달성 기준을 모두 써 주세요", target=t))

            w = _num(it["weight"])
            total += w
            if w <= 0:
                issues.append(Issue("WEIGHT_POSITIVE", f"{where}: 가중치는 0보다 커야 합니다", target=t))

            kind = it.get("kind")
            if kind == "reg":
                x = it.get("target")   # 모양 검사를 지났으니 null 이거나 숫자
                if x is not None and _num(x) <= 0:
                    issues.append(Issue("TARGET", f"{where}: 목표 숫자는 0보다 큰 숫자이거나 비워야 합니다", target=t))
            elif kind == "irr":
                scheds = it["scheds"]
                if not scheds:
                    issues.append(Issue("SCHED_MIN", f"{where}: 예정 기한을 하나 이상 써 주세요", target=t))
                elif len(scheds) > MAX_SCHEDS:
                    issues.append(Issue("SCHED_MAX", f"{where}: 예정 기한은 {MAX_SCHEDS}개까지 쓸 수 있습니다 (지금 {len(scheds)}개)", target=t))
                for si, s in enumerate(scheds):
                    st = f"sched:{gi}.{ii}.{si}"
                    if not _in_year(s["due"], year):  # 적은 값은 되풀이하지 않는다 (새 줄은 날짜가 비어 있다)
                        why = "예정 기한 날짜를 골라 주세요" if not s["due"] else f"예정 기한은 {year}년 안의 날짜로 골라 주세요"
                        issues.append(Issue("SCHED_DATE", f"{where} {si + 1}번째 일정: {why}", target=st))
                    check_text(s["name"], "name", SCHED_NAME_MAX, f"{where} {si + 1}번째 일정", st, "이름")
            else:
                issues.append(Issue("KIND", f"{where}: 업무 종류를 정기적 또는 비정기적으로 골라 주세요", target=t))

    if n < MIN_ITEMS:
        issues.append(Issue("ITEMS_MIN", f"실행목표는 최소 {MIN_ITEMS}개 필요합니다 (지금 {n}개)"))
    if total != 100:
        issues.append(Issue("WEIGHT_SUM", f"가중치 합계가 100이어야 합니다 (지금 {total.normalize():f})"))
    return issues


def clean_plan(goals: list[dict]) -> list[dict]:
    """저장할 모습의 계획서 — 아는 칸만 남긴 새 목록 (모르는 칸은 버린다). validate_plan 이 SHAPE 를 내지 않은 계획서에만 쓴다.

    rid · sid 는 있을 때만 남긴다. 정기 · 비정기가 아닌 줄은 모양 검사가 업무 종류 · 목표 숫자를 보지 않으므로,
    업무 종류는 글자가 아니면 "" 로, 목표 숫자는 숫자가 아니면 None 으로 둔다 (제출 때 KIND 로 걸린다).
    """
    def ids(d: dict, key: str) -> dict:
        return {key: d[key]} if d.get(key) is not None else {}

    return [
        {"team": g["team"], "personal": g["personal"], "items": [
            {**ids(it, "rid"), "action": it["action"], "formula": it["formula"], "weight": it["weight"], "owner": it["owner"],
             **{k: it[k] for k in GRADES}, "kind": it["kind"] if isinstance(it.get("kind"), str) else "",
             "target": it["target"] if _num(it.get("target")) is not None else None, "unit": it["unit"],
             "scheds": [{**ids(s, "sid"), "due": s["due"], "name": s["name"]} for s in it["scheds"]]}
            for it in g["items"]]}
        for g in goals
    ]


def _renumber(things: list[dict], key: str, used: set[int], keep) -> None:
    """things 의 key 번호를 새로 매긴다. keep(thing) 이 참이고 앞에 같은 번호가 없는 것만 번호를 지키고, 나머지는 새 번호.

    새 번호 = used(이 계획서의 어느 확정본에서든 쓴 적 있는 번호)와 지킨 번호에 없는 가장 작은 양의 정수.
    지킨 번호를 다 정한 뒤에 새 번호를 준다 — 앞의 새 행이 뒤에 있는 지킬 번호를 가로채지 않게.
    """
    kept: set[int] = set()
    fresh = []
    for thing in things:
        if keep(thing) and thing[key] not in kept:
            kept.add(thing[key])
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
    """확정할 때 행(rid) · 일정(sid)의 번호를 매긴 새 계획서를 돌려준다 (goals 는 건드리지 않는다).

    history = 이 계획서의 지난 확정본들 (오래된 것부터). 화면이 보낸 번호는 믿지 않는다 (R13):
    - 행의 rid 는 마지막 확정본에 같은 rid 가 있고 업무 종류도 같을 때만 지킨다 (종류를 바꾼 행은 새 행).
    - 일정의 sid 는 마지막 확정본에서 같은 rid 의 행 밑에 있던 것만 지킨다 (다른 행으로 옮겼으면 새 일정).
    - 같은 번호가 앞에 또 있으면 뒤의 것은 새 번호. 나머지는 모두 새 번호 —
      어느 확정본에서든 쓴 적 없는 가장 작은 양의 정수 (지운 행의 번호도 다시 쓰지 않아 옛 진행 기록이 이어지지 않는다).
    확정본이 없으면 행은 1, 2, … 일정도 1, 2, … 로 매긴다. goals 는 validate_plan 을 지난 것으로 본다.
    """
    out = copy.deepcopy(goals)
    rows = [it for g in out for it in g["items"]]
    last_rows = [it for g in (history[-1] if history else []) for it in g["items"]]
    every_rows = [it for version in history for g in version for it in g["items"]]
    kind_of = {it["rid"]: it["kind"] for it in last_rows}                       # 마지막 확정본: rid -> 업무 종류
    rid_of = {s["sid"]: it["rid"] for it in last_rows for s in it["scheds"]}     # 마지막 확정본: sid -> 그 일정이 있던 rid
    _renumber(
        rows, "rid", {it["rid"] for it in every_rows},
        lambda it: _is_id(it.get("rid")) and it["rid"] in kind_of and kind_of[it["rid"]] == it.get("kind"),
    )
    parent = {id(s): it["rid"] for it in rows for s in it["scheds"]}            # 일정 -> 새로 정한 행 rid
    _renumber(
        [s for it in rows for s in it["scheds"]], "sid", {s["sid"] for it in every_rows for s in it["scheds"]},
        lambda s: _is_id(s.get("sid")) and rid_of.get(s["sid"]) == parent[id(s)],
    )
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


def window_span(
    year_row: tuple[str | None, str | None, str | None] | None,
    person_row: tuple[str, str] | None,
) -> tuple[str | None, str | None]:
    """지금 적용되는 (edit_from, edit_to) — 그 사람만의 기간이 있으면 그것이 우선한다. 없으면 그해 기간, 그것도 없으면 (None, None)."""
    return person_row or (year_row[:2] if year_row else (None, None))


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
    start, end = window_span(year_row, person_row)
    return bool(start and end) and date.fromisoformat(start) <= today <= date.fromisoformat(end)
