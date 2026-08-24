"""hr_eval 도메인 로직 자체 검증.

    cd backend && python -m hr_eval.selfcheck

테스트 프레임워크를 쓰지 않는다. 검사 하나가 실패해도 나머지는 계속 돌고,
마지막에 몇 개가 깨졌는지 알려준다.
"""

from __future__ import annotations

import sys
import traceback
from decimal import Decimal

# Windows 콘솔 기본 코드페이지(cp949)에서 한글·기호가 깨지는 걸 막는다.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from hr_eval.domain.errors import (
    HrDomainError,
    Issue,
    NotFoundError,
    PermissionDeniedError,
    Severity,
    StateConflictError,
    ValidationError,
)
from hr_eval.domain.quota import (
    SMALL_DIVISION_THRESHOLD,
    GroupedQuota,
    IndividualQuota,
    QuotaMode,
    calculate_department_quota,
    round_half_up,
)

CHECKS: list = []


def check(fn):
    """검사 함수를 러너에 등록한다."""
    CHECKS.append(fn)
    return fn


@check
def 예외_계층이_HrDomainError를_상속한다():
    for cls in (ValidationError, PermissionDeniedError, StateConflictError, NotFoundError):
        assert issubclass(cls, HrDomainError), cls


@check
def ValidationError가_issues와_errors를_구분한다():
    issues = [
        Issue("E1", "오류입니다"),
        Issue("W1", "경고입니다", severity=Severity.WARNING),
    ]
    err = ValidationError(issues)
    assert len(err.issues) == 2
    assert len(err.errors) == 1
    assert err.errors[0].code == "E1"
    # 메시지에 코드가 드러나야 로그만 보고도 원인을 안다
    assert "E1" in str(err)


@check
def Issue의_기본_severity는_ERROR다():
    assert Issue("X", "메시지").severity is Severity.ERROR


@check
def 소규모_본부는_그룹_정원을_받는다():
    """스펙 2장 GROUPED 표와 정확히 일치해야 한다."""
    표 = {0: (0, 0), 1: (0, 1), 2: (1, 1), 3: (1, 2), 4: (1, 3)}
    for n, (상위, 하위) in 표.items():
        q = calculate_department_quota(n)
        assert isinstance(q, GroupedQuota), (n, q)
        assert q.mode is QuotaMode.GROUPED
        assert (q.upper, q.lower) == (상위, 하위), (n, q)
        assert q.cap_s == 상위, (n, q)


@check
def 일반_본부는_등급별_개별_정원을_받는다():
    """스펙 2장 INDIVIDUAL 표와 정확히 일치해야 한다."""
    표 = {
        5: (1, 1, 2, 1),
        7: (1, 1, 4, 1),
        10: (1, 1, 7, 1),
        15: (2, 2, 9, 2),
        20: (2, 2, 14, 2),
        25: (3, 3, 16, 3),
    }
    for n, (a, b, c, d) in 표.items():
        q = calculate_department_quota(n)
        assert isinstance(q, IndividualQuota), (n, q)
        assert q.mode is QuotaMode.INDIVIDUAL
        assert (q.a, q.b, q.c, q.d) == (a, b, c, d), (n, q)
        assert q.cap_s == q.a, (n, q)


@check
def 정원_합계는_언제나_본부_인원과_같다():
    """DB의 quotas_individual_chk / quotas_grouped_chk 와 같은 불변식이다."""
    for n in range(0, 201):
        q = calculate_department_quota(n)
        assert q.total() == n, (n, q)
        assert q.headcount == n, (n, q)


@check
def 모드_경계는_4명과_5명_사이다():
    assert SMALL_DIVISION_THRESHOLD == 4
    assert calculate_department_quota(4).mode is QuotaMode.GROUPED
    assert calculate_department_quota(5).mode is QuotaMode.INDIVIDUAL


@check
def 반올림은_내장_round가_아니라_ROUND_HALF_UP이다():
    assert round_half_up(Decimal("0.5")) == 1
    assert round_half_up(Decimal("1.5")) == 2
    assert round_half_up(Decimal("0.4")) == 0
    # 내장 round()였다면 0과 2가 나와 규칙이 조용히 어긋난다
    assert round(0.5) == 0 and round(1.5) == 2
    # 5명 본부가 바로 그 함정에 걸리는 지점이다
    assert calculate_department_quota(5).a == 1


@check
def 음수_인원은_거부한다():
    try:
        calculate_department_quota(-1)
    except ValueError:
        pass
    else:
        raise AssertionError("음수 인원인데 ValueError가 나오지 않았다")


def main() -> int:
    failed = 0
    for fn in CHECKS:
        try:
            fn()
        except Exception:
            failed += 1
            print(f"[실패] {fn.__name__}")
            traceback.print_exc()
        else:
            print(f"[통과] {fn.__name__}")
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} 통과")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
