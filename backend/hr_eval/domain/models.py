"""hr_eval 도메인 타입.

DDL의 ENUM 값과 문자열이 정확히 일치해야 한다 (selfcheck가 지킨다).
어떤 DB 드라이버도 import하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Iterable

from hr_eval.domain.errors import Issue, ValidationError


class Role(str, Enum):
    """user_roles 한 줄. 직책 셋 + HR 권한. 줄이 없으면 직책 없는 직원이다 (로그인 설계 §6)."""

    TEAM_LEADER = "TEAM_LEADER"
    DIVISION_HEAD = "DIVISION_HEAD"
    CEO = "CEO"
    HR_ADMIN = "HR_ADMIN"


POSITIONS = (Role.TEAM_LEADER, Role.DIVISION_HEAD, Role.CEO)


def position_of(roles: Iterable[Role]) -> Role | None:
    """user_roles 줄들에서 직책 하나를 꺼낸다. 직책 겸직은 운영에서 막는다 (겸직 C)."""
    found = [r for r in roles if r in POSITIONS]
    if len(found) > 1:
        raise ValidationError(
            [Issue("POSITION_DUPLICATED", "직책은 한 사람에 하나입니다 (HR 권한은 따로)")]
        )
    return found[0] if found else None


class Grade(str, Enum):
    S = "S"
    A = "A"
    B = "B"
    C = "C"
    D = "D"


class PeriodType(str, Enum):
    ANNUAL = "ANNUAL"
    MIDTERM = "MIDTERM"


class KpiSheetStatus(str, Enum):
    DRAFT = "DRAFT"
    TEAM_LEADER_APPROVED = "TEAM_LEADER_APPROVED"
    DIVISION_HEAD_APPROVED = "DIVISION_HEAD_APPROVED"


class ContractStatus(str, Enum):
    DRAFT = "DRAFT"
    SENT = "SENT"
    SIGNED = "SIGNED"
    CANCELLED = "CANCELLED"


class PdfStatus(str, Enum):
    NONE = "NONE"
    PENDING = "PENDING"
    GENERATED = "GENERATED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class Actor:
    """이미 인증된 요청자. 인증 자체는 이 계층의 책임이 아니다.

    직책과 HR 권한을 따로 담는다 — HR 팀장은 팀장이면서 HR 이다 (로그인 설계 §6).
    led_* 는 "그 부서의 장인가"다. 소속만으로는 장인지 알 수 없다 (8월 설계 §8).
    """

    user_id: int
    position: Role | None = None  # TEAM_LEADER · DIVISION_HEAD · CEO. None = 직책 없음
    is_hr: bool = False
    led_team_id: int | None = None  # 팀장이면 그 팀
    led_division_id: int | None = None  # 본부장이면 그 본부

    def __post_init__(self) -> None:
        if self.position is not None and self.position not in POSITIONS:
            raise ValueError(f"직책이 아닙니다: {self.position}")

    @classmethod
    def from_roles(cls, user_id: int, roles: Iterable[Role], **scope: int | None) -> Actor:
        """user_roles 줄들로 만든다. 직책이 두 줄이면 ValidationError."""
        roles = list(roles)
        return cls(user_id, position_of(roles), Role.HR_ADMIN in roles, **scope)


@dataclass(frozen=True)
class EvaluationPeriod:
    id: int
    type: PeriodType
    is_kpi_window_open: bool


@dataclass(frozen=True)
class KpiSheet:
    id: int
    period_id: int
    user_id: int
    status: KpiSheetStatus = KpiSheetStatus.DRAFT
    submitted_at: datetime | None = None
    team_leader_approved_at: datetime | None = None
    team_leader_approved_by: int | None = None
    division_head_approved_at: datetime | None = None
    division_head_approved_by: int | None = None
    locked_at: datetime | None = None


@dataclass(frozen=True)
class SignatureInput:
    """화면에서 올라온 서명 입력. 검증 전 원본이다."""

    consent_checked: bool
    signer_name: str
    signature_image: bytes | None
    ip: str | None
    user_agent: str | None = None


@dataclass(frozen=True)
class SalaryContract:
    id: int | None
    period_id: int
    user_id: int
    evaluation_id: int
    grade: Grade
    base_salary_before: Decimal
    raise_pct: Decimal
    base_salary_after: Decimal
    contract_starts_on: date
    contract_ends_on: date
    status: ContractStatus = ContractStatus.DRAFT
    is_locked: bool = False
    version: int = 1
    resent_from_id: int | None = None
    sent_at: datetime | None = None
    consent_checked: bool = False
    signer_user_id: int | None = None
    signer_name: str | None = None
    signer_ip: str | None = None
    signer_user_agent: str | None = None
    signature_image: bytes | None = None
    document_hash: str | None = None
    signed_at: datetime | None = None
    pdf_status: PdfStatus = PdfStatus.NONE
    pdf_path: str | None = None
    pdf_generated_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancelled_by: int | None = None
    cancel_reason: str | None = None

    def as_document(self) -> dict[str, str]:
        """해시 대상이 되는 계약 본문.

        서명값(이미지·해시·서명시각)은 넣지 않는다. 해시가 자기 자신을
        포함하면 검증이 불가능해진다. 나중에 금액이나 등급이 바뀌면
        해시가 달라지므로 위변조를 잡을 수 있다.
        """
        return {
            "period_id": str(self.period_id),
            "user_id": str(self.user_id),
            "evaluation_id": str(self.evaluation_id),
            "grade": self.grade.value,
            "base_salary_before": str(self.base_salary_before),
            "raise_pct": str(self.raise_pct),
            "base_salary_after": str(self.base_salary_after),
            "contract_starts_on": self.contract_starts_on.isoformat(),
            "contract_ends_on": self.contract_ends_on.isoformat(),
            "version": str(self.version),
        }


@dataclass(frozen=True)
class AuditEntry:
    action: str
    entity_type: str
    entity_id: int | None
    actor_user_id: int | None  # None = 시스템 (명단 동기화 · 첫 HR 지정)
    actor_position: Role | None = None  # 그때의 직책
    actor_is_hr: bool = False  # 그때 HR 권한이 있었나
    on_behalf_of: int | None = None  # HR 이 대표이사 대신 한 일이면 그 대표이사. actor 는 실제로 넣은 HR
    before_data: dict | None = None
    after_data: dict | None = None
    reason: str | None = None
    ip: str | None = None
    user_agent: str | None = None

    @classmethod
    def by(cls, actor: Actor, *, on_behalf_of: int | None = None, **fields) -> AuditEntry:
        """요청자의 직책·HR 권한을 그때 값으로 적는다."""
        return cls(
            actor_user_id=actor.user_id,
            actor_position=actor.position,
            actor_is_hr=actor.is_hr,
            on_behalf_of=on_behalf_of,
            **fields,
        )
