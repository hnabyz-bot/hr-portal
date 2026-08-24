"""KPI 집합 검증.

순수 함수다. 최초 제출(submit_kpi_goal)과 확정 후 수정 요청
(request_kpi_change)이 같은 규칙을 써야 하므로 따로 떼어놨다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from hr_eval.domain.errors import Issue, ValidationError

#: 1인당 최소 KPI 개수.
MIN_KPI_COUNT = 3

#: 가중치 합계는 정확히 이 값이어야 한다.
TOTAL_WEIGHT = Decimal("100.00")


@dataclass(frozen=True)
class KpiInput:
    title: str
    weight_pct: Decimal
    description: str | None = None
    target: str | None = None


def validate_kpi_set(kpis: Sequence[KpiInput]) -> None:
    """문제가 없으면 조용히 돌아오고, 있으면 전부 모아 ValidationError를 던진다."""
    issues: list[Issue] = []

    if len(kpis) < MIN_KPI_COUNT:
        issues.append(
            Issue(
                "KPI_TOO_FEW",
                f"KPI는 최소 {MIN_KPI_COUNT}개 등록해야 합니다 (현재 {len(kpis)}개)",
            )
        )

    for idx, k in enumerate(kpis):
        번호 = idx + 1
        if not k.title.strip():
            issues.append(
                Issue(
                    "KPI_TITLE_EMPTY",
                    f"{번호}번째 KPI의 제목이 비어 있습니다",
                    target=f"kpi:{idx}",
                )
            )
        if not (Decimal(0) < k.weight_pct <= TOTAL_WEIGHT):
            issues.append(
                Issue(
                    "KPI_WEIGHT_OUT_OF_RANGE",
                    f"{번호}번째 KPI 가중치는 0 초과 100 이하여야 합니다"
                    f"(현재 {k.weight_pct})",
                    target=f"kpi:{idx}",
                )
            )

    total = sum((k.weight_pct for k in kpis), Decimal(0))
    if total != TOTAL_WEIGHT:
        issues.append(
            Issue(
                "KPI_WEIGHT_SUM_INVALID",
                f"KPI 가중치 합계는 정확히 100이어야 합니다 (현재 {total})",
            )
        )

    if issues:
        raise ValidationError(issues)
