"""누가 누구에게 무엇을 할 수 있나.

근거: 로그인 설계 §6 "평가에서 누가 무엇을 하나" · "HR에 맡기기", 8월 설계 §4·§8 (조회 범위),
KPI 설계 7장 (대표이사의 KPI 현황).

순수 함수다. 직책 · HR 권한 · 어느 부서의 장인가 · 대상(사람 또는 묶음)만 본다.
단계(자기평가 → 평가 → 배정 → 대표이사 확인 → HR 확정 → 공개)에 따른 잠금과
공개 전 열람 제한은 평가 유스케이스가 따로 본다 — 여기서는 "누가"만 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from hr_eval.domain.models import Actor, Role

TL, DH, CEO = Role.TEAM_LEADER, Role.DIVISION_HEAD, Role.CEO


class Act(str, Enum):
    KPI_APPROVE = "KPI_APPROVE"  # KPI 승인
    SCORE = "SCORE"  # 평가 — 점수 + 등급 의견
    ASSIGN = "ASSIGN"  # 등급 배정 · 마감 (본부장은 팀장이 낸 점수도 고친다)
    CONFIRM = "CONFIRM"  # 묶음 확인
    CONFIRM_EDIT = "CONFIRM_EDIT"  # 확인하면서 등급·점수 고치기 — 전사 팀장 묶음만
    SEND_BACK = "SEND_BACK"  # 고치지 않고 본부장에게 돌려보내기 — 본부 팀원 묶음만
    FINALIZE = "FINALIZE"  # 묶음 확정 · 되돌리기 · 결과 공개
    MANAGE = "MANAGE"  # 기간 · 기준 점수 · 근태 · 평가 제외 · 자기평가 없이 진행
    APPEAL_ANSWER = "APPEAL_ANSWER"  # 이의 신청 답변
    APPEAL_CONFIRM = "APPEAL_CONFIRM"  # 등급을 바꾸는 답변 확인
    VIEW_RESULT = "VIEW_RESULT"  # 평가 결과 조회
    VIEW_KPI = "VIEW_KPI"  # 남의 KPI 조회
    VIEW_CONTRACT = "VIEW_CONTRACT"  # 남의 연봉계약서 조회
    DELEGATE = "DELEGATE"  # "HR에 맡기기" 켜고 끄기


class Pool(str, Enum):
    """등급 비율을 함께 쓰는 묶음."""

    DIVISION = "DIVISION"  # 본부 팀원 (본부마다, 팀장·본부장 제외)
    LEADERS = "LEADERS"  # 전사 팀장
    DIRECT = "DIRECT"  # 대표이사 직속부서 팀원


@dataclass(frozen=True)
class Target:
    """대상이 되는 사람."""

    user_id: int
    team_id: int | None  # 소속 팀. 팀 없이 본부 바로 아래면 None
    division_id: int | None  # 소속 본부
    position: Role | None = None
    direct: bool = False  # 대표이사 직속부서 소속 (본부장이 없는 본부)

    @property
    def pool(self) -> Pool | None:
        """평가받는 묶음. 본부장·대표이사는 평가받지 않는다."""
        if self.position in (DH, CEO):
            return None
        if self.position is TL:
            return Pool.LEADERS
        return Pool.DIRECT if self.direct else Pool.DIVISION


@dataclass(frozen=True)
class PoolRef:
    """대상이 되는 묶음."""

    pool: Pool
    member_ids: frozenset[int]
    division_id: int | None = None  # 본부 팀원 묶음이면 그 본부


@dataclass(frozen=True)
class Delegation:
    """대표이사가 "HR에 맡기기"를 켜 둔 상태. 꺼져 있으면 None 을 넘긴다."""

    ceo_user_id: int


@dataclass(frozen=True)
class Permit:
    ok: bool
    on_behalf_of: int | None = None  # HR 이 대표이사 대신 하는 일이면 그 대표이사 — 감사로그에 적는다

    def __bool__(self) -> bool:
        return self.ok


HR_ONLY = {Act.FINALIZE, Act.MANAGE, Act.APPEAL_ANSWER}


def can(
    actor: Actor,
    act: Act,
    target: Target | PoolRef | None = None,
    *,
    delegation: Delegation | None = None,
) -> Permit:
    if act is Act.DELEGATE:
        return Permit(actor.position is CEO)  # HR 이 스스로 켤 수 없다
    if act in HR_ONLY:
        return Permit(actor.is_hr)
    if _own(actor, act, target):
        return Permit(True)
    if actor.position is CEO and _ceo(act, target):
        return Permit(True)
    # 대표이사가 맡겼을 때만, 본인 것은 빼고 HR 이 대신 한다
    if actor.is_hr and delegation is not None and _ceo(act, target) and actor.user_id not in _ids(target):
        return Permit(True, on_behalf_of=delegation.ceo_user_id)
    return Permit(False)


def _in_team(a: Actor, t: Target) -> bool:
    """a 가 팀장인 팀의 팀원."""
    return (
        a.position is TL
        and a.led_team_id is not None
        and t.team_id == a.led_team_id
        and t.position is None
        and t.user_id != a.user_id
    )


def _in_division(a: Actor, t: Target) -> bool:
    """a 가 본부장인 본부의 팀장·팀원."""
    return (
        a.position is DH
        and a.led_division_id is not None
        and t.division_id == a.led_division_id
        and t.position in (None, TL)
        and t.user_id != a.user_id
    )


def _own(a: Actor, act: Act, t: Target | PoolRef | None) -> bool:
    """팀장·본부장·HR 이 자기 권한으로 하는 것 (대표이사 몫은 _ceo)."""
    if isinstance(t, Target):
        mine = t.user_id == a.user_id
        if act is Act.KPI_APPROVE:
            return _in_team(a, t) or _in_division(a, t)
        if act is Act.SCORE:
            return _in_team(a, t) or (_in_division(a, t) and t.position is TL)
        if act in (Act.VIEW_RESULT, Act.VIEW_KPI):
            return mine or a.is_hr or _in_team(a, t) or _in_division(a, t)
        if act is Act.VIEW_CONTRACT:  # 본인과 HR 만. 팀장·본부장·대표이사는 못 본다 (인사담당자 2026-10-15)
            return mine or a.is_hr
    if isinstance(t, PoolRef) and act is Act.ASSIGN:
        return (
            t.pool is Pool.DIVISION
            and a.position is DH
            and a.led_division_id is not None
            and a.led_division_id == t.division_id
        )
    return False


def _ceo(act: Act, t: Target | PoolRef | None) -> bool:
    """대표이사의 일 (§6 표의 대표이사 칸). HR 에 맡기면 HR 이 대신 하는 범위이기도 하다."""
    if isinstance(t, Target):
        if act is Act.KPI_APPROVE:
            return t.position is DH or (t.position is TL and t.direct)
        if act is Act.SCORE:
            return t.position is TL and t.direct
        if act is Act.APPEAL_CONFIRM:
            return t.pool is not None
        if act is Act.VIEW_RESULT:
            return True
        if act is Act.VIEW_KPI:  # 본부장·팀장까지 (팀원은 인원·평균만 — KPI 설계 7장)
            return t.position in (DH, TL)
    if isinstance(t, PoolRef):
        if act is Act.ASSIGN:
            return t.pool is Pool.DIRECT
        if act is Act.CONFIRM:
            return True
        if act is Act.CONFIRM_EDIT:
            return t.pool is Pool.LEADERS
        if act is Act.SEND_BACK:
            return t.pool is Pool.DIVISION
    return False


def _ids(t: Target | PoolRef | None) -> frozenset[int]:
    if isinstance(t, Target):
        return frozenset({t.user_id})
    if isinstance(t, PoolRef):
        return t.member_ids
    return frozenset()
