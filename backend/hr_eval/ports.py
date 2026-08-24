"""저장소·트랜잭션 인터페이스.

구현은 여기 없다. 나중에 psycopg 구현체를 붙이든 검사용 가짜를 쓰든
유스케이스는 이 모양만 알면 된다.
"""

from __future__ import annotations

from typing import Protocol, Sequence

from hr_eval.domain.kpi import KpiInput
from hr_eval.domain.models import (
    AuditEntry,
    EvaluationPeriod,
    KpiSheet,
    SalaryContract,
)


class KpiRepository(Protocol):
    def get_sheet(self, period_id: int, user_id: int) -> KpiSheet | None: ...
    def create_sheet(self, period_id: int, user_id: int, created_by: int) -> KpiSheet: ...
    def replace_kpis(self, sheet_id: int, kpis: Sequence[KpiInput]) -> None: ...
    def save_sheet(self, sheet: KpiSheet) -> None: ...


class PeriodRepository(Protocol):
    def get(self, period_id: int) -> EvaluationPeriod: ...


class ContractRepository(Protocol):
    def get_for_update(self, contract_id: int) -> SalaryContract:
        """SELECT ... FOR UPDATE. 동시 서명·파기를 직렬화한다."""
        ...

    def save(self, contract: SalaryContract) -> None: ...
    def insert(self, contract: SalaryContract) -> SalaryContract: ...


class AuditLogRepository(Protocol):
    def append(self, entry: AuditEntry) -> None: ...


class UnitOfWork(Protocol):
    """유스케이스 하나 = 트랜잭션 하나.

    with 블록을 빠져나갈 때 commit()이 불리지 않았으면 롤백한다.
    """

    kpis: KpiRepository
    periods: PeriodRepository
    contracts: ContractRepository
    audit: AuditLogRepository

    def __enter__(self) -> UnitOfWork: ...
    def __exit__(self, *exc: object) -> bool | None: ...
    def commit(self) -> None: ...
