"""사람 찾기 · 첫 HR 지정 · 구성원 설정 규칙 (로그인 설계 §4·§6).

순수 함수다. DB 도 Flask 도 하이웍스도 부르지 않는다 — 호출하는 쪽이 users·user_roles 를
읽어 Member 로 넘기고, 돌려받은 결과(연결·HR 지정·감사로그)를 한 트랜잭션에 저장한다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Collection, Mapping, Sequence

from hr_eval.domain.errors import Issue, ValidationError
from hr_eval.domain.models import POSITIONS, AuditEntry, Role
from hr_eval.domain.org import NO_COL, Dept, is_direct_team

# 미등록과 비활성은 같은 문구다 — 등록 여부를 구분해 알려 주지 않는다 (§3 ④)
MSG_DENIED = "인사포털에 등록되지 않은 사용자입니다. 인사팀에 문의하세요"
MSG_NOT_READY = "인사포털은 준비 중입니다. 열리면 안내드리겠습니다"


@dataclass(frozen=True)
class Member:
    """users 한 줄 + user_roles. 구성원 설정 화면의 한 사람이기도 하다."""

    id: int
    name: str
    department_id: int
    employee_no: str | None = None
    hiworks_user_no: str | None = None
    is_active: bool = True
    portal_enabled: bool = False
    position: Role | None = None
    is_hr: bool = False
    writes_kpi: bool = True
    in_evaluation: bool = True
    has_contract: bool = True
    led_department_id: int | None = None  # 맡은 부서 (팀장 · 본부장). 없으면 본인 소속 부서

    def roles(self) -> frozenset[Role]:
        """user_roles 에 저장할 줄들."""
        return frozenset(r for r in (self.position, Role.HR_ADMIN if self.is_hr else None) if r)


def active_hr_count(members: Sequence[Member]) -> int:
    """들어올 수 있는 HR 수 — HR 권한 + 포털 사용 + 활성."""
    return sum(m.is_hr and m.portal_enabled and m.is_active for m in members)


@dataclass(frozen=True)
class SsoToken:
    """하이웍스 link-api 가 알려 준 사람. 값은 글자로 맞춘다 (번호가 숫자로 올 수도 있다)."""

    user_no: str
    user_id: str = ""
    user_name: str = ""
    employee_no: str = ""

    @classmethod
    def from_api(cls, data: Mapping) -> SsoToken:
        def s(key: str) -> str:
            v = data.get(key)
            return "" if v is None else str(v).strip()

        return cls(s("user-no"), s("user-id"), s("user-name"), s("employee-no"))


class SsoOutcome(str, Enum):
    OK = "OK"  # 세션을 발급한다
    DENIED = "DENIED"  # 미등록 · 비활성 (403, 같은 문구)
    NOT_READY = "NOT_READY"  # 시범 사용자 명단 밖 — 포털 사용 꺼짐 (403 "준비 중")


@dataclass(frozen=True)
class SsoResult:
    outcome: SsoOutcome
    user_id: int | None = None
    message: str = ""
    link: bool = False  # hiworks_user_no 를 이 사람에게 저장한다 (사번 대안)
    bootstrap_hr: bool = False  # HR 권한 + 포털 사용을 켠다 (첫 HR)
    audit: tuple[AuditEntry, ...] = field(default=())


def resolve_sso_user(
    token: SsoToken,
    members: Sequence[Member],
    *,
    first_hr_user_no: str | None = None,
    link_by_employee_no: bool = False,
) -> SsoResult:
    """하이웍스가 알려 준 사람을 포털 사람으로 바꾼다 (§3 ④, §4).

    link_by_employee_no: 조직 조회 번호와 SSO 번호가 다를 때만 켜는 대안 (§4, 첫 실연결에서 정한다).
    first_hr_user_no: 서버 설정의 첫 HR 하이웍스 사용자 번호. 활성 HR 이 0명일 때만 쓴다.
    """
    no = token.user_no
    user = next((m for m in members if no and m.hiworks_user_no == no), None)

    link = False
    if user is None and link_by_employee_no and token.employee_no:
        # 이미 다른 하이웍스 계정과 연결된 사람은 사번만 같다고 가로채지 못한다. 이름도 같아야 한다
        user = next(
            (
                m
                for m in members
                if m.hiworks_user_no is None
                and m.employee_no == token.employee_no
                and m.name.strip() == token.user_name
            ),
            None,
        )
        link = user is not None

    if user is None or not user.is_active:
        return SsoResult(SsoOutcome.DENIED, message=MSG_DENIED)

    audit: list[AuditEntry] = []
    if link:
        audit.append(
            AuditEntry(
                action="SSO_LINK",
                entity_type="user",
                entity_id=user.id,
                actor_user_id=None,
                after_data={"hiworks_user_no": no, "hiworks_user_id": token.user_id},
            )
        )

    # 하이웍스 사용자 번호로만 맞춘다 — 아이디·사번은 하이웍스 관리 권한으로 바꿀 수 있다
    setting = (first_hr_user_no or "").strip()
    bootstrap = bool(setting) and bool(no) and setting == no and active_hr_count(members) == 0
    if bootstrap:
        audit.append(
            AuditEntry(
                action="HR_BOOTSTRAP",
                entity_type="user",
                entity_id=user.id,
                actor_user_id=None,
                after_data={"is_hr": True, "portal_enabled": True},
            )
        )

    if not (user.portal_enabled or bootstrap):
        return SsoResult(SsoOutcome.NOT_READY, user.id, MSG_NOT_READY, link=link, audit=tuple(audit))
    return SsoResult(SsoOutcome.OK, user.id, link=link, bootstrap_hr=bootstrap, audit=tuple(audit))


def validate_members(
    after: Sequence[Member],
    *,
    before: Sequence[Member] = (),
    kpi_locked: Collection[int] = (),
    depts: Sequence[Dept] = (),
) -> None:
    """구성원 설정 저장 전 검사 (§6 규칙, §4 마지막 HR 보호). 문제를 전부 모아 ValidationError.

    after: 저장하려는 전체 명단. before: 지금 저장된 명단 (KPI 작성 잠금 비교용).
    kpi_locked: 평가가 시작돼 KPI 작성을 바꿀 수 없는 사람 id.
    depts: 부서 목록. 주면 팀장 · 본부장의 맡은 부서가 알맞은 종류인지도 본다 (바뀐 사람만 — 옛 자료가 남의 저장을 막지 않게).
    명단 동기화의 비활성 표시는 이 검사를 거치지 않는다 — HR 이 0명이 되면 첫 HR 규칙이 다시 작동한다.
    """
    issues: list[Issue] = []
    active = [m for m in after if m.is_active]
    was = {m.id: m for m in before}

    for m in after:
        t = f"user:{m.id}"
        if m.position is not None and m.position not in POSITIONS:
            issues.append(Issue("POSITION_INVALID", f"{m.name}: 직책이 아닙니다", target=t))
        if m.position in (Role.DIVISION_HEAD, Role.CEO) and (m.in_evaluation or m.has_contract):
            issues.append(
                Issue("NOT_EVALUATED_POSITION", f"{m.name}: 본부장·대표이사는 평가 대상·연봉계약서가 아닙니다", target=t)
            )
        if m.position is Role.CEO and m.writes_kpi:
            issues.append(Issue("CEO_NO_KPI", f"{m.name}: 대표이사는 KPI를 작성하지 않습니다", target=t))

    # 팀장의 맡은 부서는 팀(또는 직속 팀), 본부장의 맡은 부서는 본부. 맡은 부서를 안 고르면 본인 소속이 맡은 부서다
    by = {d.id: d for d in depts}
    for m in after:
        if m.position not in (Role.TEAM_LEADER, Role.DIVISION_HEAD) or not depts:
            continue
        led = m.led_department_id or m.department_id
        b = was.get(m.id)
        if b and b.position is m.position and (b.led_department_id or b.department_id) == led:
            continue
        d = by.get(led)
        if m.position is Role.TEAM_LEADER:
            good = d is not None and (d.parent_id is not None or is_direct_team(d, depts))
        else:
            good = d is not None and d.parent_id is None and d.name not in NO_COL and not is_direct_team(d, depts)
        if not good:
            what = "팀(또는 직속 팀)" if m.position is Role.TEAM_LEADER else "본부"
            issues.append(Issue("LED_DEPT_INVALID", f"{m.name}: 맡은 부서가 {what}이어야 합니다", target=f"user:{m.id}"))

    # 팀장은 팀에 한 명, 본부장은 본부에 한 명, 대표이사는 한 명 (비활성인 사람은 세지 않는다). 부서는 맡은 부서 기준
    per_dept = Counter(
        (m.position, m.led_department_id or m.department_id)
        for m in active
        if m.position in (Role.TEAM_LEADER, Role.DIVISION_HEAD)
    )
    for (pos, dept), n in per_dept.items():
        if n > 1:
            what = "팀장은 팀에" if pos is Role.TEAM_LEADER else "본부장은 본부에"
            issues.append(Issue("POSITION_TAKEN", f"{what} 한 명입니다 (부서 {dept}에 {n}명)", target=f"dept:{dept}"))
    if sum(m.position is Role.CEO for m in active) > 1:
        issues.append(Issue("POSITION_TAKEN", "대표이사는 한 명입니다"))

    if active_hr_count(after) == 0:
        issues.append(
            Issue("LAST_HR", "HR 권한이 있고 포털을 쓰는 사람이 한 명은 있어야 합니다. 다른 HR을 먼저 지정하세요")
        )

    for m in after:
        if m.id in kpi_locked and m.id in was and was[m.id].writes_kpi != m.writes_kpi:
            issues.append(Issue("KPI_LOCKED", f"{m.name}: 평가가 시작돼 KPI 작성을 바꿀 수 없습니다", target=f"user:{m.id}"))

    if issues:
        raise ValidationError(issues)
