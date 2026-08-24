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
