"""본부 단위 등급 배정 검증.

부수효과가 없는 순수 함수다. 저장은 유스케이스 계층이 한다.
검증은 첫 오류에서 멈추지 않고 발견한 걸 전부 모아서 돌려준다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from hr_eval.domain.errors import Issue, Severity, ValidationError
from hr_eval.domain.models import Grade
from hr_eval.domain.quota import GroupedQuota, IndividualQuota, Quota

#: S등급 자격 기준. 이 점수를 "초과"해야 한다 (동점은 불가).
S_GRADE_MIN_SCORE = Decimal("100")

#: 총점 상한. KPI 100점 + 가점이 이 값을 넘을 수 없다.
#: DDL의 eval_total_max_chk 와 같은 값이어야 한다.
MAX_TOTAL_SCORE = Decimal("110")


@dataclass(frozen=True)
class Assignment:
    user_id: int
    total_score: Decimal
    grade: Grade


@dataclass(frozen=True)
class GradeAssignmentResult:
    assignments: tuple[Assignment, ...]
    warnings: tuple[Issue, ...]
    effective_upper: int  # S 차감 후 남은 상위 정원 (INDIVIDUAL이면 Q_A')
    s_count: int


def _check_band(issues: list[Issue], label: str, count: int, limit: int) -> None:
    """한 등급(또는 그룹)의 배정 인원을 정원과 대조한다."""
    if count > limit:
        issues.append(
            Issue(
                "QUOTA_EXCEEDED",
                f"{label} 정원을 초과했습니다 (배정 {count}명 / 정원 {limit}명)",
            )
        )
    elif count < limit:
        issues.append(
            Issue(
                "QUOTA_UNDERFILLED",
                f"{label} 정원에 미달했습니다 (배정 {count}명 / 정원 {limit}명)",
                severity=Severity.WARNING,
            )
        )


def validate_and_assign_evaluation_grades(
    *,
    quota: Quota,
    member_ids: Sequence[int],
    assignments: Sequence[Assignment],
    is_annual: bool = True,
) -> GradeAssignmentResult:
    """본부 단위 등급 배정을 검증한다.

    ERROR가 하나라도 있으면 ValidationError를 던진다 (경고도 함께 담는다).
    ERROR가 없으면 경고 목록과 함께 결과를 돌려준다.
    """
    issues: list[Issue] = []
    members = set(member_ids)
    counted = Counter(a.user_id for a in assignments)

    for uid in sorted(set(counted) - members):
        issues.append(
            Issue(
                "MEMBER_NOT_IN_DIVISION",
                f"본부 소속이 아닌 인원이 배정에 포함됐습니다 (사번키 {uid})",
                target=f"user:{uid}",
            )
        )
    for uid in sorted(members - set(counted)):
        issues.append(
            Issue(
                "ASSIGNMENT_MISSING",
                f"등급이 배정되지 않은 인원이 있습니다 (사번키 {uid})",
                target=f"user:{uid}",
            )
        )
    for uid in sorted(u for u, n in counted.items() if n > 1):
        issues.append(
            Issue(
                "ASSIGNMENT_DUPLICATED",
                f"같은 인원이 여러 번 배정됐습니다 (사번키 {uid})",
                target=f"user:{uid}",
            )
        )

    if not is_annual:
        issues.append(
            Issue(
                "MIDTERM_GRADE_NOT_ALLOWED",
                "중간점검 기간에는 등급을 배정할 수 없습니다",
            )
        )

    for a in assignments:
        if a.total_score > MAX_TOTAL_SCORE:
            issues.append(
                Issue(
                    "SCORE_ABOVE_MAX",
                    f"총점은 {MAX_TOTAL_SCORE}점을 넘을 수 없습니다 "
                    f"(사번키 {a.user_id}, 점수 {a.total_score})",
                    target=f"user:{a.user_id}",
                )
            )
        if a.grade is Grade.S and a.total_score <= S_GRADE_MIN_SCORE:
            issues.append(
                Issue(
                    "S_GRADE_SCORE_TOO_LOW",
                    f"S등급은 100점 초과일 때만 지정할 수 있습니다 "
                    f"(사번키 {a.user_id}, 점수 {a.total_score})",
                    target=f"user:{a.user_id}",
                )
            )

    by_grade = Counter(a.grade for a in assignments)
    s_count = by_grade[Grade.S]
    if s_count > quota.cap_s:
        issues.append(
            Issue(
                "S_GRADE_CAP_EXCEEDED",
                f"S등급 상한을 초과했습니다 (배정 {s_count}명 / 상한 {quota.cap_s}명)",
            )
        )

    if isinstance(quota, IndividualQuota):
        effective_upper = max(quota.a - s_count, 0)
        _check_band(issues, "A등급", by_grade[Grade.A], effective_upper)
        _check_band(issues, "B등급", by_grade[Grade.B], quota.b)
        _check_band(issues, "D등급", by_grade[Grade.D], quota.d)
        # C등급은 잔여 흡수라 검사하지 않는다.
    else:
        assert isinstance(quota, GroupedQuota)
        effective_upper = max(quota.upper - s_count, 0)
        _check_band(
            issues, "상위등급(A·B)", by_grade[Grade.A] + by_grade[Grade.B], effective_upper
        )
        # 하위(C·D)는 잔여 흡수라 검사하지 않는다.

    if any(i.severity is Severity.ERROR for i in issues):
        raise ValidationError(issues)

    return GradeAssignmentResult(
        assignments=tuple(assignments),
        warnings=tuple(i for i in issues if i.severity is Severity.WARNING),
        effective_upper=effective_upper,
        s_count=s_count,
    )
