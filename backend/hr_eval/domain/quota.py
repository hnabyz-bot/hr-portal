"""본부별 상대평가 쿼터 산정.

본부 인원에 따라 두 가지 모드가 있다 (스펙 2장).
  - N <= 4  : GROUPED   — 상위(A·B 합계) / 하위(C·D 합계) 두 그룹으로만 정원을 준다
  - N >= 5  : INDIVIDUAL — 등급별로 개별 정원을 준다

두 모드 모두 정원 합계는 본부 인원과 정확히 같다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

#: 이 인원 이하면 그룹 정원(GROUPED) 모드다.
SMALL_DIVISION_THRESHOLD = 4

#: A·B·D 각각의 목표 비율. C는 나머지를 흡수한다.
GRADE_RATIO = Decimal("0.1")


class QuotaMode(str, Enum):
    INDIVIDUAL = "INDIVIDUAL"
    GROUPED = "GROUPED"


def round_half_up(value: Decimal) -> int:
    """사사오입. 내장 round()는 은행가 반올림이라 round(0.5)==0 이다."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class GroupedQuota:
    """N <= 4. 상위 1명을 A로 줄지 B로 줄지는 조직장이 고른다."""

    headcount: int
    upper: int  # A+B 합계 상한
    lower: int  # C+D 합계. 잔여 흡수라 상한 검사를 하지 않는다
    cap_s: int
    mode: QuotaMode = QuotaMode.GROUPED

    def total(self) -> int:
        return self.upper + self.lower


@dataclass(frozen=True)
class IndividualQuota:
    """N >= 5. 등급별 개별 정원."""

    headcount: int
    a: int
    b: int
    c: int  # 잔여 흡수라 상한 검사를 하지 않는다
    d: int
    cap_s: int
    mode: QuotaMode = QuotaMode.INDIVIDUAL

    def total(self) -> int:
        return self.a + self.b + self.c + self.d


Quota = GroupedQuota | IndividualQuota


def calculate_department_quota(headcount: int) -> Quota:
    if headcount < 0:
        raise ValueError(f"본부 인원은 음수일 수 없습니다: {headcount}")

    if headcount <= SMALL_DIVISION_THRESHOLD:
        # 1명짜리 본부에 상위 정원을 주면 전원이 상위등급이 된다.
        upper = 1 if headcount >= 2 else 0
        return GroupedQuota(
            headcount=headcount,
            upper=upper,
            lower=headcount - upper,
            cap_s=upper,
        )

    q = round_half_up(Decimal(headcount) * GRADE_RATIO)
    return IndividualQuota(
        headcount=headcount,
        a=q,
        b=q,
        c=headcount - 3 * q,
        d=q,
        cap_s=q,
    )
