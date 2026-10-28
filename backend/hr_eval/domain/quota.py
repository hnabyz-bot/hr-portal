"""본부별 상대평가 쿼터 산정.

본부 인원에 따라 두 가지 모드가 있다 (스펙 2장).
  - N <= 4  : GROUPED   — 상위(A·B 합계) / 하위(C·D 합계) 두 그룹으로만 정원을 준다
  - N >= 5  : INDIVIDUAL — 등급별로 개별 정원을 준다

두 모드 모두 정원 합계는 본부 인원과 정확히 같다.

등급 비율과 늘린 정원은 부르는 쪽이 넘긴다 (기본값은 10 · 10 · 10 % 와 늘림 없음 = 지금 규칙).
  - ratios = (S·A %, B %, D %) — 5명 이상 묶음의 정원은 인원 × 비율을 사사오입한 값, C 는 나머지.
    세 칸 합이 70 이하면 5명 이상에서 정원 합이 인원을 넘지 않는다 (RATIO_SUM_MAX).
  - extra = (S·A +명, B +명) — 대표이사가 묶음 정원을 늘려 준 만큼. 5명 이상은 a · b 에, 4명 이하는 위쪽(upper)에 더한다.
  - 4명 이하 묶음은 비율을 쓰지 않는다 (상위 1명 · 1명 묶음은 0명, 거기에 늘린 정원만 더한다).
  - 비율 · 늘린 정원의 범위는 부르는 쪽(서버 · DB)이 본다. 여기서는 정원 합이 인원을 넘을 때만 ValueError.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

#: 이 인원 이하면 그룹 정원(GROUPED) 모드다.
SMALL_DIVISION_THRESHOLD = 4

#: (S·A, B, D) 목표 비율(%). C는 나머지를 흡수한다.
DEFAULT_RATIOS = (10, 10, 10)

#: 늘린 정원 한 칸(S·A 또는 B)의 상한(명) · 비율 세 칸 합의 상한(%) — 범위 검사는 부르는 쪽이 이 값으로 한다.
EXTRA_MAX = 5
RATIO_SUM_MAX = 70


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


def calculate_department_quota(
    headcount: int,
    ratios: tuple[int, int, int] = DEFAULT_RATIOS,
    extra: tuple[int, int] = (0, 0),
) -> Quota:
    """ratios = (S·A %, B %, D %), extra = (S·A +명, B +명)."""
    if headcount < 0:
        raise ValueError(f"본부 인원은 음수일 수 없습니다: {headcount}")
    extra_sa, extra_b = extra

    if headcount <= SMALL_DIVISION_THRESHOLD:
        # 1명짜리 본부에 상위 정원을 주면 전원이 상위등급이 된다.
        base = 1 if headcount >= 2 else 0
        upper = base + extra_sa + extra_b
        if upper > headcount:
            raise ValueError("늘린 정원이 묶음 인원보다 많습니다.")
        return GroupedQuota(
            headcount=headcount,
            upper=upper,
            lower=headcount - upper,
            cap_s=base + extra_sa,
        )

    ra, rb, rd = ratios
    a = round_half_up(Decimal(headcount) * ra / 100) + extra_sa
    b = round_half_up(Decimal(headcount) * rb / 100) + extra_b
    d = round_half_up(Decimal(headcount) * rd / 100)
    c = headcount - a - b - d
    if c < 0:
        raise ValueError("늘린 정원이 묶음 인원보다 많습니다.")
    return IndividualQuota(headcount=headcount, a=a, b=b, c=c, d=d, cap_s=a)
