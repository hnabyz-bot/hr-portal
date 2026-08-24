"""hr_eval 도메인 예외와 검증 결과 항목."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    ERROR = "ERROR"      # 저장을 막는다
    WARNING = "WARNING"  # 저장은 되지만 화면에 알린다


@dataclass(frozen=True)
class Issue:
    """검증에서 발견한 항목 하나.

    target은 화면에서 어느 줄을 붉게 칠할지 정하는 데 쓴다.
    예: "user:17", "kpi:2"
    """

    code: str
    message: str
    severity: Severity = Severity.ERROR
    target: str | None = None


class HrDomainError(Exception):
    """hr_eval 도메인이 던지는 모든 예외의 상위 타입."""


class ValidationError(HrDomainError):
    """업무 규칙 위반. HTTP로 옮기면 400.

    첫 오류에서 멈추지 않고 발견한 항목을 전부 들고 있다.
    조직장이 20명치 등급을 한 번에 고칠 수 있어야 하기 때문이다.
    """

    def __init__(self, issues: list[Issue]) -> None:
        self.issues = list(issues)
        super().__init__("; ".join(f"[{i.code}] {i.message}" for i in self.issues))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity is Severity.WARNING]


class PermissionDeniedError(HrDomainError):
    """역할이 모자란다. HTTP로 옮기면 403."""


class StateConflictError(HrDomainError):
    """잠겼거나, 이미 처리됐거나, 기간이 닫혔다. HTTP로 옮기면 409."""


class NotFoundError(HrDomainError):
    """대상이 없다. HTTP로 옮기면 404."""
