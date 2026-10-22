"""바로 위 관리자 — 실제 명단에서 정한다 (KPI 설계 §4 "사람과 관계").

순수 함수다. 하이웍스 조직(본부 → 팀, 2단계)과 구성원 설정의 직책 · 맡은 부서만 본다.
관계는 저장하지 않고 요청 때마다 계산한다 — 명단 동기화와 구성원 설정 저장이 곧바로 반영된다.

찾는 순서: 팀 → 그 본부 → 대표이사. 비활성인 사람은 관리자가 될 수 없고, 자기 자신은 돌려주지 않는다.
target_of 는 그 사람을 access.Target(누가 누구를 볼 수 있나 판단하는 모양)으로 바꾼다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from hr_eval.domain.access import Target
from hr_eval.domain.models import Role

# 장이 없는 맨 위 부서 — 조직도에서 칸을 빼는 곳 (화면 ORG_NO_COL 과 같은 값). 소속이면 관리자는 대표이사.
NO_COL: tuple[str, ...] = ("임원", "비상근")


@dataclass(frozen=True)
class Dept:
    id: int
    name: str
    parent_id: int | None


@dataclass(frozen=True)
class Person:
    id: int
    department_id: int
    position: Role | None = None
    led_department_id: int | None = None  # 맡은 부서. 없으면 본인 소속 부서
    is_active: bool = True


def is_direct_team(dept: Dept, depts: Sequence[Dept]) -> bool:
    """대표이사 직속 팀(지원팀 · 총무팀): 맨 위 부서이면서 아래 팀이 없고 NO_COL 이 아니다."""
    return dept.parent_id is None and dept.name not in NO_COL and not any(d.parent_id == dept.id for d in depts)


def led_dept(p: Person) -> int | None:
    """팀장 · 본부장이 실제로 이끄는 부서. 그 밖의 사람은 None."""
    if p.position in (Role.TEAM_LEADER, Role.DIVISION_HEAD):
        return p.led_department_id or p.department_id
    return None


def manager_of(user_id: int, people: Sequence[Person], depts: Sequence[Dept]) -> int | None:
    me = next((p for p in people if p.id == user_id), None)
    if me is None or me.position is Role.CEO:
        return None

    def find(role: Role, dept_id: int) -> int | None:
        return next(
            (p.id for p in people if p.is_active and p.id != user_id and p.position is role and led_dept(p) == dept_id),
            None,
        )

    by = {d.id: d for d in depts}
    d = by.get(led_dept(me) if me.position is Role.TEAM_LEADER else me.department_id)
    steps: list[tuple[Role, int]] = []  # 가까운 쪽부터. 못 찾으면 대표이사
    if d is None or me.position is Role.DIVISION_HEAD or (d.parent_id is None and d.name in NO_COL):
        pass
    elif me.position is Role.TEAM_LEADER:
        if d.parent_id is not None:
            steps = [(Role.DIVISION_HEAD, d.parent_id)]
    elif d.parent_id is not None:
        steps = [(Role.TEAM_LEADER, d.id), (Role.DIVISION_HEAD, d.parent_id)]
    elif is_direct_team(d, depts):
        steps = [(Role.TEAM_LEADER, d.id)]
    else:
        steps = [(Role.DIVISION_HEAD, d.id)]

    for role, dept_id in steps:
        found = find(role, dept_id)
        if found is not None:
            return found
    return next((p.id for p in people if p.is_active and p.id != user_id and p.position is Role.CEO), None)


def target_of(p: Person, depts: Sequence[Dept]) -> Target:
    """그 사람을 access.Target 으로. 대표이사 · 본부장은 부서를 찾지 않고, 그 밖은 기준 부서(팀장은 이끄는 곳, 나머지는 소속)로 정한다.
    기준 부서가 팀이면 (팀, 그 본부), 대표이사 직속 팀이면 (팀, 본부 없음, direct), 임원 · 비상근이면 팀도 본부도 없고,
    맨 위 본부면 (팀 없음, 그 본부). 부서를 못 찾으면 팀도 본부도 없다."""
    if p.position is Role.CEO:
        return Target(p.id, None, None, p.position)
    if p.position is Role.DIVISION_HEAD:
        return Target(p.id, None, led_dept(p), p.position)
    d = next((d for d in depts if d.id == (led_dept(p) or p.department_id)), None)
    if d is None or (d.parent_id is None and d.name in NO_COL):
        return Target(p.id, None, None, p.position)
    if d.parent_id is not None:
        return Target(p.id, d.id, d.parent_id, p.position)
    if is_direct_team(d, depts):
        return Target(p.id, d.id, None, p.position, direct=True)
    return Target(p.id, None, d.id, p.position)
