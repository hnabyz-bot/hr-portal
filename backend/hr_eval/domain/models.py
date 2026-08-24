"""hr_eval 도메인 타입.

DDL의 ENUM 값과 문자열이 정확히 일치해야 한다 (selfcheck가 지킨다).
어떤 DB 드라이버도 import하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class Role(str, Enum):
    EMPLOYEE = "EMPLOYEE"
    TEAM_LEADER = "TEAM_LEADER"
    DIVISION_HEAD = "DIVISION_HEAD"
    HR_ADMIN = "HR_ADMIN"


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
    """이미 인증된 요청자. 인증 자체는 이 계층의 책임이 아니다."""

    user_id: int
    role: Role


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
    actor_user_id: int | None
    actor_role: Role | None
    before_data: dict | None = None
    after_data: dict | None = None
    reason: str | None = None
    ip: str | None = None
    user_agent: str | None = None
