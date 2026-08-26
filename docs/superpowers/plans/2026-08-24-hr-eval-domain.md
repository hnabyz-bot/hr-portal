# 평가·성과 / 급여·계약 도메인 로직 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** hr-portal에 평가 쿼터·KPI·전자 연봉계약서의 PostgreSQL 스키마와 프레임워크 독립 도메인 로직을 추가한다. DB 연결·API·화면은 이번 범위가 아니다.

**Architecture:** `backend/hr_eval/` 아래 순수 도메인 계층(`domain/`)과 얇은 유스케이스 계층(`usecases.py` + `ports.py`)으로 나눈다. `domain/`은 DB도 Flask도 import하지 않아 DB 없이 전부 검증된다. 저장·감사로그가 필요한 유스케이스만 `ports.py`의 `Protocol`을 받는다. DDL은 `sql/001_init.sql` 한 파일이고, CI의 PostgreSQL 서비스 컨테이너에 실제로 적용해 검증한다.

**Tech Stack:** Python 3.12 (컨테이너 기준), 표준 라이브러리만 (`dataclasses`, `enum`, `decimal`, `hashlib`, `json`, `datetime`, `typing`). PostgreSQL 16. **새 pip 의존성 없음.**

**Spec:** [docs/superpowers/specs/2026-08-24-hr-evaluation-contract-design.md](../specs/2026-08-24-hr-evaluation-contract-design.md)

## Global Constraints

- **새 pip 의존성을 추가하지 않는다.** `backend/requirements.txt`는 건드리지 않는다. 표준 라이브러리만 쓴다.
- **테스트 프레임워크를 쓰지 않는다.** 검증은 `backend/hr_eval/selfcheck.py` 하나에 `assert`로 모은다. pytest·unittest 금지.
- **실행 방법은 `cd backend && python -m hr_eval.selfcheck` 하나뿐이다.** `sys.path` 조작 금지.
- **모든 모듈은 `from __future__ import annotations`로 시작한다.** 로컬 개발 환경은 Python 3.14, 배포 컨테이너는 3.12다. 3.13+ 전용 문법을 쓰지 않는다.
- **`domain/` 아래 어떤 파일도 `ports`, `usecases`, DB 드라이버, Flask를 import하지 않는다.** 의존 방향은 `usecases → domain` 단방향이다.
- **금액·점수·가중치는 전부 `Decimal`이다.** `float`를 쓰지 않는다. 비교도 `Decimal`끼리 한다.
- **반올림은 `Decimal.quantize(..., rounding=ROUND_HALF_UP)`만 쓴다.** 내장 `round()` 금지 — 은행가 반올림이라 `round(0.5) == 0`이다.
- **오류 메시지는 한국어다.** 사용자가 비개발자이고 화면에 그대로 노출될 수 있다.
- **실제 직원 개인정보·연봉 수치를 코드에 넣지 않는다.** hr-portal은 Public 저장소다. 예시 데이터는 `user_id=1`, `홍길동` 같은 가상 값만 쓴다.
- **브랜치 하나 = PR 하나.** 이 계획 전체가 `feature/hr-eval-domain` 브랜치 하나, PR 하나다. 각 Task마다 커밋한다.

## 파일 구조

| 파일 | 책임 |
|---|---|
| `backend/hr_eval/__init__.py` | 패키지 선언 (빈 파일) |
| `backend/hr_eval/domain/__init__.py` | 패키지 선언 (빈 파일) |
| `backend/hr_eval/domain/errors.py` | 예외 계층, `Issue`, `Severity` |
| `backend/hr_eval/domain/models.py` | 도메인 enum과 dataclass (Role, Grade, 계약서, KPI 시트 등) |
| `backend/hr_eval/domain/quota.py` | 본부 쿼터 산정 (순수 함수) |
| `backend/hr_eval/domain/grading.py` | 등급 배정 검증 (순수 함수) |
| `backend/hr_eval/domain/kpi.py` | KPI 집합 검증 (순수 함수) |
| `backend/hr_eval/domain/contract_rules.py` | 인상률 매핑·금액 계산·문서해시 (순수 함수) |
| `backend/hr_eval/ports.py` | 저장소·트랜잭션 `Protocol` (구현 없음) |
| `backend/hr_eval/usecases.py` | 부수효과가 있는 유스케이스 6종 |
| `backend/hr_eval/selfcheck.py` | 검증 러너 + 가짜 저장소 + 모든 검사 |
| `backend/hr_eval/sql/001_init.sql` | DDL 전체 |
| `.github/workflows/ci.yml` | 검증 job 2개 추가 (수정) |

**스펙과 다른 점 하나:** 스펙 §4의 파일 목록에는 `domain/kpi.py`가 없었다. 스펙 §5.3이 "수정 요청 경로도 같은 1~5번 검증을 재사용한다"고 했으므로, KPI 집합 검증을 순수 함수로 떼어내 `domain/kpi.py`에 둔다. 그래야 `submit_kpi_goal`과 `request_kpi_change`가 같은 함수를 부른다.

---

## Task 1: 패키지 뼈대 + 예외 계층 + 검증 러너

**Files:**
- Create: `backend/hr_eval/__init__.py`
- Create: `backend/hr_eval/domain/__init__.py`
- Create: `backend/hr_eval/domain/errors.py`
- Create: `backend/hr_eval/selfcheck.py`

**Interfaces:**
- Consumes: 없음 (첫 Task)
- Produces: `Severity`, `Issue(code, message, severity, target)`, `HrDomainError`, `ValidationError(issues)` + `.issues` + `.errors`, `PermissionDeniedError`, `StateConflictError`, `NotFoundError`. 검증 러너의 `@check` 데코레이터와 `main()`.

- [ ] **Step 1: 브랜치를 만든다**

```bash
git checkout main && git pull && git checkout -b feature/hr-eval-domain
```

- [ ] **Step 2: 실패하는 검사를 먼저 쓴다**

`backend/hr_eval/selfcheck.py`를 만든다. 러너와 첫 검사를 함께 넣는다.

```python
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
```

- [ ] **Step 3: 실패하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `ModuleNotFoundError: No module named 'hr_eval'` — 아직 패키지가 없다.

- [ ] **Step 4: 패키지와 예외 계층을 만든다**

`backend/hr_eval/__init__.py` — 빈 파일.
`backend/hr_eval/domain/__init__.py` — 빈 파일.

`backend/hr_eval/domain/errors.py`:

```python
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
```

- [ ] **Step 5: 통과하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `3/3 통과`

- [ ] **Step 6: 커밋한다**

```bash
git add backend/hr_eval && git commit -m "hr_eval: 패키지 뼈대와 예외 계층, 검증 러너 추가"
```

---

## Task 2: 본부 쿼터 산정

**Files:**
- Create: `backend/hr_eval/domain/quota.py`
- Modify: `backend/hr_eval/selfcheck.py` (검사 추가)

**Interfaces:**
- Consumes: 없음 (`domain/errors.py`도 쓰지 않는다 — 잘못된 인원수는 `ValueError`다)
- Produces: `SMALL_DIVISION_THRESHOLD = 4`, `QuotaMode`, `round_half_up(Decimal) -> int`, `GroupedQuota(headcount, upper, lower, cap_s, mode)`, `IndividualQuota(headcount, a, b, c, d, cap_s, mode)`, 둘 다 `.total() -> int`, 타입 별칭 `Quota`, `calculate_department_quota(headcount: int) -> Quota`

- [ ] **Step 1: 실패하는 검사를 먼저 쓴다**

`selfcheck.py`의 import 블록 아래에 추가한다.

```python
from hr_eval.domain.quota import (
    SMALL_DIVISION_THRESHOLD,
    GroupedQuota,
    IndividualQuota,
    QuotaMode,
    calculate_department_quota,
    round_half_up,
)
```

그리고 검사 함수를 추가한다.

```python
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
```

- [ ] **Step 2: 실패하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `ModuleNotFoundError: No module named 'hr_eval.domain.quota'`

- [ ] **Step 3: 구현한다**

`backend/hr_eval/domain/quota.py`:

```python
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
```

- [ ] **Step 4: 통과하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `9/9 통과`

- [ ] **Step 5: 커밋한다**

```bash
git add backend/hr_eval && git commit -m "hr_eval: 본부 쿼터 산정 (GROUPED/INDIVIDUAL 두 모드)"
```

---

## Task 3: 도메인 모델 (enum·dataclass)

**Files:**
- Create: `backend/hr_eval/domain/models.py`
- Modify: `backend/hr_eval/selfcheck.py` (검사 추가)

**Interfaces:**
- Consumes: 없음
- Produces: enum `Role`, `Grade`, `PeriodType`, `KpiSheetStatus`, `ContractStatus`, `PdfStatus`. dataclass `Actor(user_id, role)`, `EvaluationPeriod(id, type, is_kpi_window_open)`, `KpiSheet(id, period_id, user_id, status, submitted_at)`, `SalaryContract(...)` + `.as_document()`, `SignatureInput(consent_checked, signer_name, signature_image, ip, user_agent)`, `AuditEntry(action, entity_type, entity_id, actor_user_id, actor_role, before_data, after_data, reason, ip, user_agent)`

- [ ] **Step 1: 실패하는 검사를 먼저 쓴다**

`selfcheck.py`에 import와 검사를 추가한다.

```python
from hr_eval.domain.models import (
    Actor,
    ContractStatus,
    Grade,
    PdfStatus,
    Role,
    SalaryContract,
)
```

```python
@check
def enum_값이_DDL의_ENUM과_문자열로_일치한다():
    """DDL의 CREATE TYPE 값과 어긋나면 저장할 때 터진다."""
    assert [g.value for g in Grade] == ["S", "A", "B", "C", "D"]
    assert [r.value for r in Role] == [
        "EMPLOYEE",
        "TEAM_LEADER",
        "DIVISION_HEAD",
        "HR_ADMIN",
    ]
    assert [s.value for s in ContractStatus] == ["DRAFT", "SENT", "SIGNED", "CANCELLED"]
    assert [p.value for p in PdfStatus] == ["NONE", "PENDING", "GENERATED", "FAILED"]


@check
def 계약서_문서스냅샷은_서명값을_담지_않는다():
    """as_document()는 해시 대상이다. 서명 자체가 들어가면 해시가 자기참조가 된다."""
    c = _가짜계약서()
    doc = c.as_document()
    assert "signature_image" not in doc
    assert "document_hash" not in doc
    assert "signed_at" not in doc
    # 금액·등급·기간은 반드시 들어가야 위변조를 잡는다
    assert doc["grade"] == "A"
    assert doc["base_salary_after"] == "52000000"
    assert doc["contract_starts_on"] == "2027-01-01"


def _가짜계약서(**overrides) -> SalaryContract:
    """검사용 계약서. 전부 가상 값이다."""
    base = dict(
        id=1,
        period_id=1,
        user_id=10,
        evaluation_id=100,
        grade=Grade.A,
        base_salary_before=Decimal("50000000"),
        raise_pct=Decimal("4.00"),
        base_salary_after=Decimal("52000000"),
        contract_starts_on=date(2027, 1, 1),
        contract_ends_on=date(2027, 12, 31),
        status=ContractStatus.SENT,
    )
    base.update(overrides)
    return SalaryContract(**base)
```

`selfcheck.py` 상단 import에 `from datetime import date`를 추가한다.

- [ ] **Step 2: 실패하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `ModuleNotFoundError: No module named 'hr_eval.domain.models'`

- [ ] **Step 3: 구현한다**

`backend/hr_eval/domain/models.py`:

```python
"""hr_eval 도메인 타입.

DDL의 ENUM 값과 문자열이 정확히 일치해야 한다 (selfcheck가 지킨다).
어떤 DB 드라이버도 import하지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
```

- [ ] **Step 4: 통과하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `11/11 통과`

- [ ] **Step 5: 커밋한다**

```bash
git add backend/hr_eval && git commit -m "hr_eval: 도메인 enum·dataclass 추가 (DDL ENUM과 값 일치)"
```

---

## Task 4: 등급 배정 검증

**Files:**
- Create: `backend/hr_eval/domain/grading.py`
- Modify: `backend/hr_eval/selfcheck.py` (검사 추가)

**Interfaces:**
- Consumes: `quota.Quota`/`GroupedQuota`/`IndividualQuota`, `models.Grade`, `errors.Issue`/`Severity`/`ValidationError`
- Produces: `Assignment(user_id, total_score, grade)`, `GradeAssignmentResult(assignments, warnings, effective_upper, s_count)`, `validate_and_assign_evaluation_grades(*, quota, member_ids, assignments, is_annual=True) -> GradeAssignmentResult`

- [ ] **Step 1: 실패하는 검사를 먼저 쓴다**

`selfcheck.py`에 import와 검사를 추가한다.

```python
from hr_eval.domain.grading import Assignment, validate_and_assign_evaluation_grades
```

```python
def _배정(*쌍) -> list[Assignment]:
    """(user_id, 점수, 등급) 튜플들을 Assignment 목록으로 바꾼다."""
    return [Assignment(uid, Decimal(str(점수)), grade) for uid, 점수, grade in 쌍]


def _오류코드(fn) -> list[str]:
    """검증이 던진 ValidationError의 ERROR 코드 목록을 돌려준다."""
    try:
        fn()
    except ValidationError as err:
        return [i.code for i in err.errors]
    raise AssertionError("ValidationError가 나오지 않았다")


@check
def S등급은_100점_초과일_때만_가능하다():
    q = calculate_department_quota(10)
    멤버 = list(range(1, 11))

    def 백점_S():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정((1, "100.00", Grade.S), *[(i, "80", Grade.C) for i in range(2, 11)]),
        )

    assert "S_GRADE_SCORE_TOO_LOW" in _오류코드(백점_S)

    # 100.01점이면 통과한다 (S 1명 = Cap_S, 그래서 A는 0명이어야 한다)
    결과 = validate_and_assign_evaluation_grades(
        quota=q,
        member_ids=멤버,
        assignments=_배정(
            (1, "100.01", Grade.S),
            (2, "90", Grade.B),
            (3, "60", Grade.D),
            *[(i, "80", Grade.C) for i in range(4, 11)],
        ),
    )
    assert 결과.s_count == 1
    assert 결과.effective_upper == 0


@check
def S등급이_A쿼터를_차감한다():
    """스펙: Q_A' = Q_A - K. 10명 본부는 Q_A=1이므로 S 1명이면 A는 0명이다."""
    q = calculate_department_quota(10)
    멤버 = list(range(1, 11))

    def S와_A를_같이():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정(
                (1, "101", Grade.S),
                (2, "95", Grade.A),
                (3, "90", Grade.B),
                (4, "60", Grade.D),
                *[(i, "80", Grade.C) for i in range(5, 11)],
            ),
        )

    assert "QUOTA_EXCEEDED" in _오류코드(S와_A를_같이)


@check
def S등급_상한_초과는_막는다():
    q = calculate_department_quota(10)  # cap_s = 1
    멤버 = list(range(1, 11))

    def S_두명():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정(
                (1, "101", Grade.S),
                (2, "102", Grade.S),
                *[(i, "80", Grade.C) for i in range(3, 11)],
            ),
        )

    assert "S_GRADE_CAP_EXCEEDED" in _오류코드(S_두명)


@check
def 쿼터_초과는_ERROR_미달은_WARNING이다():
    q = calculate_department_quota(10)  # A1 B1 C7 D1
    멤버 = list(range(1, 11))

    def A_두명():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정(
                (1, "95", Grade.A),
                (2, "94", Grade.A),
                (3, "90", Grade.B),
                (4, "60", Grade.D),
                *[(i, "80", Grade.C) for i in range(5, 11)],
            ),
        )

    assert "QUOTA_EXCEEDED" in _오류코드(A_두명)

    # 전원 C: A·B·D가 전부 미달이지만 통과하고 경고 3건이 나온다
    결과 = validate_and_assign_evaluation_grades(
        quota=q, member_ids=멤버, assignments=_배정(*[(i, "80", Grade.C) for i in 멤버])
    )
    assert len(결과.warnings) == 3
    assert all(w.severity is Severity.WARNING for w in 결과.warnings)


@check
def 잔여흡수_등급은_초과로_잡히지_않는다():
    """C가 정원(7)보다 많아도 오류가 아니다. 그건 A·B·D 미달의 다른 얼굴일 뿐이다."""
    q = calculate_department_quota(10)
    멤버 = list(range(1, 11))
    결과 = validate_and_assign_evaluation_grades(
        quota=q, member_ids=멤버, assignments=_배정(*[(i, "80", Grade.C) for i in 멤버])
    )
    assert [w.code for w in 결과.warnings].count("QUOTA_UNDERFILLED") == 3


@check
def 그룹모드_4명본부는_상위를_합쳐_검사한다():
    q = calculate_department_quota(4)  # 상위 1 / 하위 3
    멤버 = [1, 2, 3, 4]

    # A1 B1 -> 상위 2명이라 초과
    def 상위_두명():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정((1, "95", Grade.A), (2, "94", Grade.B), (3, "80", Grade.C), (4, "60", Grade.D)),
        )

    assert "QUOTA_EXCEEDED" in _오류코드(상위_두명)

    # A1 C2 D1 -> 상위 1명. B가 0명이어도 오류가 아니다
    결과 = validate_and_assign_evaluation_grades(
        quota=q,
        member_ids=멤버,
        assignments=_배정((1, "95", Grade.A), (2, "80", Grade.C), (3, "78", Grade.C), (4, "60", Grade.D)),
    )
    assert 결과.warnings == ()

    # B1 C3 -> 상위 1명을 B로 줘도 된다
    결과 = validate_and_assign_evaluation_grades(
        quota=q,
        member_ids=멤버,
        assignments=_배정((1, "90", Grade.B), (2, "80", Grade.C), (3, "78", Grade.C), (4, "77", Grade.C)),
    )
    assert 결과.warnings == ()


@check
def 그룹모드_상위미달은_경고로_통과한다():
    q = calculate_department_quota(4)
    결과 = validate_and_assign_evaluation_grades(
        quota=q,
        member_ids=[1, 2, 3, 4],
        assignments=_배정(*[(i, "80", Grade.C) for i in (1, 2, 3, 4)]),
    )
    assert [w.code for w in 결과.warnings] == ["QUOTA_UNDERFILLED"]


@check
def 그룹모드_4명본부도_S가_가능하다():
    """Cap_S = 상위 정원 = 1. 대신 A·B는 0명이 된다."""
    q = calculate_department_quota(4)
    결과 = validate_and_assign_evaluation_grades(
        quota=q,
        member_ids=[1, 2, 3, 4],
        assignments=_배정((1, "105", Grade.S), (2, "80", Grade.C), (3, "78", Grade.C), (4, "60", Grade.D)),
    )
    assert 결과.s_count == 1
    assert 결과.effective_upper == 0
    assert 결과.warnings == ()


@check
def 그룹모드_1명본부는_상위등급을_줄_수_없다():
    q = calculate_department_quota(1)  # 상위 0 / 하위 1

    def 혼자_A():
        validate_and_assign_evaluation_grades(
            quota=q, member_ids=[1], assignments=_배정((1, "95", Grade.A))
        )

    assert "QUOTA_EXCEEDED" in _오류코드(혼자_A)

    결과 = validate_and_assign_evaluation_grades(
        quota=q, member_ids=[1], assignments=_배정((1, "80", Grade.C))
    )
    assert 결과.warnings == ()


@check
def 누락_중복_외부인원은_각각_오류다():
    q = calculate_department_quota(10)
    멤버 = list(range(1, 11))

    def 누락():
        validate_and_assign_evaluation_grades(
            quota=q, member_ids=멤버, assignments=_배정(*[(i, "80", Grade.C) for i in range(1, 10)])
        )

    assert "ASSIGNMENT_MISSING" in _오류코드(누락)

    def 중복():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정(*[(i, "80", Grade.C) for i in 멤버], (1, "80", Grade.C)),
        )

    assert "ASSIGNMENT_DUPLICATED" in _오류코드(중복)

    def 외부인원():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정(*[(i, "80", Grade.C) for i in 멤버], (99, "80", Grade.C)),
        )

    assert "MEMBER_NOT_IN_DIVISION" in _오류코드(외부인원)


@check
def 오류가_여러개면_전부_모아서_돌려준다():
    """첫 오류에서 멈추면 조직장이 한 번에 고칠 수 없다."""
    q = calculate_department_quota(10)
    멤버 = list(range(1, 11))

    def 한꺼번에():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정(
                (1, "100", Grade.S),   # 100점 이하 S
                (2, "95", Grade.A),
                (3, "94", Grade.A),    # A 초과
                (99, "80", Grade.C),   # 외부 인원
                *[(i, "80", Grade.C) for i in range(4, 11)],
            ),
        )

    코드 = _오류코드(한꺼번에)
    assert "S_GRADE_SCORE_TOO_LOW" in 코드
    assert "MEMBER_NOT_IN_DIVISION" in 코드
    assert len(코드) >= 3


@check
def 중간점검_기간에는_등급을_배정할_수_없다():
    q = calculate_department_quota(10)

    def 중간점검():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=list(range(1, 11)),
            assignments=_배정(*[(i, "80", Grade.C) for i in range(1, 11)]),
            is_annual=False,
        )

    assert "MIDTERM_GRADE_NOT_ALLOWED" in _오류코드(중간점검)
```

- [ ] **Step 2: 실패하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `ModuleNotFoundError: No module named 'hr_eval.domain.grading'`

- [ ] **Step 3: 구현한다**

`backend/hr_eval/domain/grading.py`:

```python
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
```

- [ ] **Step 4: 통과하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `23/23 통과`

- [ ] **Step 5: 커밋한다**

```bash
git add backend/hr_eval && git commit -m "hr_eval: 등급 배정 검증 (S 상한·쿼터 차감·두 모드)"
```

---

## Task 5: KPI 집합 검증

**Files:**
- Create: `backend/hr_eval/domain/kpi.py`
- Modify: `backend/hr_eval/selfcheck.py` (검사 추가)

**Interfaces:**
- Consumes: `errors.Issue`/`ValidationError`
- Produces: `MIN_KPI_COUNT = 3`, `TOTAL_WEIGHT = Decimal("100.00")`, `KpiInput(title, weight_pct, description=None, target=None)`, `validate_kpi_set(kpis: Sequence[KpiInput]) -> None` (문제가 있으면 `ValidationError`)

- [ ] **Step 1: 실패하는 검사를 먼저 쓴다**

`selfcheck.py`에 import와 검사를 추가한다.

```python
from hr_eval.domain.kpi import KpiInput, validate_kpi_set
```

```python
def _KPI(*가중치들) -> list[KpiInput]:
    return [
        KpiInput(title=f"목표{i + 1}", weight_pct=Decimal(str(w)))
        for i, w in enumerate(가중치들)
    ]


@check
def KPI는_최소_3개여야_한다():
    def 두개():
        validate_kpi_set(_KPI("50.00", "50.00"))

    assert "KPI_TOO_FEW" in _오류코드(두개)
    # 3개면 통과한다
    validate_kpi_set(_KPI("40.00", "30.00", "30.00"))


@check
def KPI_가중치_합은_정확히_100이어야_한다():
    def 구십구점구구():
        validate_kpi_set(_KPI("40.00", "30.00", "29.99"))

    assert "KPI_WEIGHT_SUM_INVALID" in _오류코드(구십구점구구)

    def 백점일():
        validate_kpi_set(_KPI("40.00", "30.00", "30.01"))

    assert "KPI_WEIGHT_SUM_INVALID" in _오류코드(백점일)

    # float였다면 0.1+0.2 문제로 새어나갔을 조합도 Decimal이라 정확히 맞는다
    validate_kpi_set(_KPI("33.33", "33.33", "33.34"))


@check
def KPI_가중치는_0초과_100이하여야_한다():
    def 영():
        validate_kpi_set(_KPI("0.00", "50.00", "50.00"))

    assert "KPI_WEIGHT_OUT_OF_RANGE" in _오류코드(영)

    def 음수():
        validate_kpi_set(_KPI("-10.00", "60.00", "50.00"))

    assert "KPI_WEIGHT_OUT_OF_RANGE" in _오류코드(음수)


@check
def KPI_제목이_비면_오류다():
    def 빈제목():
        validate_kpi_set(
            [
                KpiInput(title="   ", weight_pct=Decimal("40.00")),
                KpiInput(title="목표2", weight_pct=Decimal("30.00")),
                KpiInput(title="목표3", weight_pct=Decimal("30.00")),
            ]
        )

    코드 = _오류코드(빈제목)
    assert "KPI_TITLE_EMPTY" in 코드
```

- [ ] **Step 2: 실패하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `ModuleNotFoundError: No module named 'hr_eval.domain.kpi'`

- [ ] **Step 3: 구현한다**

`backend/hr_eval/domain/kpi.py`:

```python
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
                    f"{번호}번째 KPI 가중치는 0 초과 100 이하여야 합니다 "
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
```

- [ ] **Step 4: 통과하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `27/27 통과`

- [ ] **Step 5: 커밋한다**

```bash
git add backend/hr_eval && git commit -m "hr_eval: KPI 집합 검증 (최소 3개, 가중치 합 100)"
```

---

## Task 6: 계약 금액·문서해시 규칙

**Files:**
- Create: `backend/hr_eval/domain/contract_rules.py`
- Modify: `backend/hr_eval/selfcheck.py` (검사 추가)

**Interfaces:**
- Consumes: `models.Grade`, `errors.NotFoundError`
- Produces: `resolve_raise_pct(rates: Mapping[Grade, Decimal], grade: Grade) -> Decimal`, `calculate_new_salary(base_salary_before: Decimal, raise_pct: Decimal) -> Decimal`, `build_document_hash(payload: Mapping[str, str]) -> str`

**금액 반올림 결정:** 인상 후 연봉은 **원 단위 내림(`ROUND_DOWN`)** 한다. 올림하면 계약서 금액이 규정상 인상률을 넘어가고, 그건 규정 위반이 된다. 내림은 최대 1원 손해라 실무상 문제가 없다. (스펙 9장 재검토 항목)

- [ ] **Step 1: 실패하는 검사를 먼저 쓴다**

`selfcheck.py`에 import와 검사를 추가한다.

```python
from hr_eval.domain.contract_rules import (
    build_document_hash,
    calculate_new_salary,
    resolve_raise_pct,
)
from hr_eval.domain.errors import NotFoundError  # 이미 import돼 있으면 생략
```

```python
@check
def 등급으로_인상률을_찾는다():
    표 = {
        Grade.S: Decimal("8.00"),
        Grade.A: Decimal("5.00"),
        Grade.B: Decimal("3.00"),
        Grade.C: Decimal("2.00"),
        Grade.D: Decimal("0.00"),
    }
    assert resolve_raise_pct(표, Grade.A) == Decimal("5.00")

    try:
        resolve_raise_pct({Grade.A: Decimal("5.00")}, Grade.D)
    except NotFoundError:
        pass
    else:
        raise AssertionError("등록되지 않은 등급인데 NotFoundError가 나오지 않았다")


@check
def 인상후_연봉은_원단위_내림이다():
    # 50,000,000 * 1.05 = 52,500,000 (딱 떨어짐)
    assert calculate_new_salary(Decimal("50000000"), Decimal("5.00")) == Decimal("52500000")
    # 33,333,333 * 1.03 = 34,333,332.99 -> 34,333,332 (올리지 않는다)
    assert calculate_new_salary(Decimal("33333333"), Decimal("3.00")) == Decimal("34333332")
    # 0% 인상은 그대로
    assert calculate_new_salary(Decimal("40000000"), Decimal("0.00")) == Decimal("40000000")


@check
def 문서해시는_같은_내용에_같은_값_다른_내용에_다른_값이다():
    문서 = _가짜계약서().as_document()
    assert build_document_hash(문서) == build_document_hash(dict(문서))

    바뀐문서 = dict(문서)
    바뀐문서["base_salary_after"] = "60000000"
    assert build_document_hash(문서) != build_document_hash(바뀐문서)

    # 키 순서가 달라도 같은 해시여야 한다 (JSON 직렬화 순서에 흔들리면 안 된다)
    뒤집은문서 = dict(reversed(list(문서.items())))
    assert build_document_hash(문서) == build_document_hash(뒤집은문서)


@check
def 문서해시는_64자리_16진수다():
    h = build_document_hash(_가짜계약서().as_document())
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
```

`selfcheck.py` 상단에 `from hr_eval.domain.models import Grade`가 이미 있는지 확인하고, 없으면 추가한다.

- [ ] **Step 2: 실패하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `ModuleNotFoundError: No module named 'hr_eval.domain.contract_rules'`

- [ ] **Step 3: 구현한다**

`backend/hr_eval/domain/contract_rules.py`:

```python
"""연봉계약서 금액 계산과 문서해시.

인상률 수치 자체는 여기 없다. DB의 salary_raise_rates 테이블에만 있고
(hr-portal은 Public 저장소다) 이 모듈은 넘겨받은 표를 쓸 뿐이다.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_DOWN, Decimal
from typing import Mapping

from hr_eval.domain.errors import NotFoundError
from hr_eval.domain.models import Grade


def resolve_raise_pct(rates: Mapping[Grade, Decimal], grade: Grade) -> Decimal:
    try:
        return rates[grade]
    except KeyError:
        raise NotFoundError(
            f"이 평가기간에 {grade.value}등급 인상률이 등록되지 않았습니다"
        ) from None


def calculate_new_salary(base_salary_before: Decimal, raise_pct: Decimal) -> Decimal:
    """인상 후 연봉. 원 단위 내림.

    올림하면 계약서 금액이 규정상 인상률을 넘어간다. 내림은 최대 1원
    차이라 실무상 문제가 없고, 규정 위반 쪽이 훨씬 비싸다.
    """
    raw = base_salary_before * (Decimal(1) + raise_pct / Decimal(100))
    return raw.quantize(Decimal("1"), rounding=ROUND_DOWN)


def build_document_hash(payload: Mapping[str, str]) -> str:
    """계약 본문의 SHA-256.

    키를 정렬해 직렬화하므로 dict 순서가 달라도 같은 해시가 나온다.
    나중에 금액이나 등급이 바뀌면 해시가 어긋나 위변조를 잡을 수 있다.
    """
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: 통과하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `31/31 통과`

- [ ] **Step 5: 커밋한다**

```bash
git add backend/hr_eval && git commit -m "hr_eval: 계약 금액 계산과 문서해시 (원 단위 내림)"
```

---

## Task 7: 저장소 포트 + 유스케이스

**Files:**
- Create: `backend/hr_eval/ports.py`
- Create: `backend/hr_eval/usecases.py`
- Modify: `backend/hr_eval/selfcheck.py` (가짜 저장소 + 검사 추가)

**Interfaces:**
- Consumes: `domain/` 전체
- Produces:
  - `ports.KpiRepository`, `ports.PeriodRepository`, `ports.ContractRepository`, `ports.AuditLogRepository`, `ports.UnitOfWork`
  - `usecases.submit_kpi_goal(*, actor, period_id, user_id, kpis, uow) -> KpiSheet`
  - `usecases.finalize_salary_contract(*, actor, contract_id, signature, uow) -> SalaryContract`
  - `usecases.cancel_salary_contract(*, actor, contract_id, reason, uow) -> SalaryContract`
  - `usecases.resend_salary_contract(*, actor, contract_id, reason, uow) -> SalaryContract`

- [ ] **Step 1: 실패하는 검사를 먼저 쓴다**

`selfcheck.py`에 import, 가짜 저장소, 검사를 추가한다.

```python
from datetime import datetime, timezone

from hr_eval.domain.models import (
    Actor,
    EvaluationPeriod,
    KpiSheet,
    KpiSheetStatus,
    PdfStatus,
    PeriodType,
    Role,
    SignatureInput,
)
from hr_eval.usecases import (
    cancel_salary_contract,
    finalize_salary_contract,
    resend_salary_contract,
    submit_kpi_goal,
)
```

```python
class _가짜UoW:
    """DB 없이 유스케이스를 돌리기 위한 최소 구현.

    commit()이 불렸는지 기록해서, 오류 경로에서 저장이 일어나지 않는 걸 확인한다.
    """

    def __init__(self, *, period=None, sheet=None, contract=None):
        self.period = period or EvaluationPeriod(
            id=1, type=PeriodType.ANNUAL, is_kpi_window_open=True
        )
        self.sheet = sheet
        self.contract = contract
        self.saved_kpis = None
        self.inserted = []
        self.audit_entries = []
        self.committed = False
        self._next_id = 500

        uow = self

        class _Kpis:
            def get_sheet(self, period_id, user_id):
                return uow.sheet

            def create_sheet(self, period_id, user_id, created_by):
                uow._next_id += 1
                uow.sheet = KpiSheet(id=uow._next_id, period_id=period_id, user_id=user_id)
                return uow.sheet

            def replace_kpis(self, sheet_id, kpis):
                uow.saved_kpis = list(kpis)

            def save_sheet(self, sheet):
                uow.sheet = sheet

        class _Periods:
            def get(self, period_id):
                return uow.period

        class _Contracts:
            def get_for_update(self, contract_id):
                if uow.contract is None or uow.contract.id != contract_id:
                    raise NotFoundError(f"계약서를 찾을 수 없습니다 (id={contract_id})")
                return uow.contract

            def save(self, contract):
                uow.contract = contract

            def insert(self, contract):
                uow._next_id += 1
                saved = replace(contract, id=uow._next_id)
                uow.inserted.append(saved)
                return saved

        class _Audit:
            def append(self, entry):
                uow.audit_entries.append(entry)

        self.kpis = _Kpis()
        self.periods = _Periods()
        self.contracts = _Contracts()
        self.audit = _Audit()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def commit(self):
        self.committed = True

    @property
    def actions(self):
        return [e.action for e in self.audit_entries]


@check
def KPI제출은_본인이나_HR만_할_수_있다():
    uow = _가짜UoW()
    try:
        submit_kpi_goal(
            actor=Actor(user_id=99, role=Role.TEAM_LEADER),
            period_id=1,
            user_id=10,
            kpis=_KPI("40.00", "30.00", "30.00"),
            uow=uow,
        )
    except PermissionDeniedError:
        pass
    else:
        raise AssertionError("남의 KPI를 제출했는데 막히지 않았다")
    assert not uow.committed


@check
def KPI제출은_수정기간이_닫혀_있으면_막힌다():
    uow = _가짜UoW(
        period=EvaluationPeriod(id=1, type=PeriodType.ANNUAL, is_kpi_window_open=False)
    )
    try:
        submit_kpi_goal(
            actor=Actor(user_id=10, role=Role.EMPLOYEE),
            period_id=1,
            user_id=10,
            kpis=_KPI("40.00", "30.00", "30.00"),
            uow=uow,
        )
    except StateConflictError:
        pass
    else:
        raise AssertionError("수정 기간이 닫혔는데 제출됐다")
    assert not uow.committed


@check
def 확정된_KPI는_직접_수정할_수_없다():
    uow = _가짜UoW(
        sheet=KpiSheet(
            id=7, period_id=1, user_id=10, status=KpiSheetStatus.DIVISION_HEAD_APPROVED
        )
    )
    try:
        submit_kpi_goal(
            actor=Actor(user_id=10, role=Role.EMPLOYEE),
            period_id=1,
            user_id=10,
            kpis=_KPI("40.00", "30.00", "30.00"),
            uow=uow,
        )
    except StateConflictError as err:
        assert "수정 요청" in str(err)  # 무엇을 해야 하는지 알려줘야 한다
    else:
        raise AssertionError("확정된 KPI가 그대로 수정됐다")
    assert not uow.committed


@check
def KPI제출이_성공하면_DRAFT로_저장되고_감사로그가_남는다():
    uow = _가짜UoW()
    sheet = submit_kpi_goal(
        actor=Actor(user_id=10, role=Role.EMPLOYEE),
        period_id=1,
        user_id=10,
        kpis=_KPI("40.00", "30.00", "30.00"),
        uow=uow,
    )
    assert sheet.status is KpiSheetStatus.DRAFT
    assert sheet.submitted_at is not None
    assert len(uow.saved_kpis) == 3
    assert uow.actions == ["KPI_SUBMITTED"]
    assert uow.committed


@check
def 계약서는_본인만_서명할_수_있다():
    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10))
    try:
        finalize_salary_contract(
            actor=Actor(user_id=11, role=Role.HR_ADMIN),  # HR도 대리 서명 불가
            contract_id=1,
            signature=_서명(),
            uow=uow,
        )
    except PermissionDeniedError:
        pass
    else:
        raise AssertionError("본인이 아닌데 서명됐다")
    assert not uow.committed


@check
def 동의_미체크나_서명이미지_누락은_거부한다():
    for 잘못된서명 in (
        _서명(consent_checked=False),
        _서명(signer_name="   "),
        _서명(signature_image=None),
        _서명(ip=None),
    ):
        uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10))
        try:
            finalize_salary_contract(
                actor=Actor(user_id=10, role=Role.EMPLOYEE),
                contract_id=1,
                signature=잘못된서명,
                uow=uow,
            )
        except ValidationError:
            pass
        else:
            raise AssertionError(f"불완전한 서명이 통과됐다: {잘못된서명}")
        assert not uow.committed


@check
def 서명이_끝나면_잠기고_감사로그가_남는다():
    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10))
    signed = finalize_salary_contract(
        actor=Actor(user_id=10, role=Role.EMPLOYEE),
        contract_id=1,
        signature=_서명(),
        uow=uow,
    )
    assert signed.status is ContractStatus.SIGNED
    assert signed.is_locked is True
    assert signed.signer_user_id == 10
    assert signed.signer_ip == "192.0.2.10"
    assert signed.signed_at is not None
    assert len(signed.document_hash) == 64
    assert signed.pdf_status is PdfStatus.PENDING
    assert uow.actions == ["CONTRACT_SIGNED"]
    assert uow.audit_entries[0].ip == "192.0.2.10"
    assert uow.committed


@check
def 이미_서명된_계약서는_다시_서명할_수_없다():
    서명됨 = _가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True)
    uow = _가짜UoW(contract=서명됨)
    try:
        finalize_salary_contract(
            actor=Actor(user_id=10, role=Role.EMPLOYEE),
            contract_id=1,
            signature=_서명(),
            uow=uow,
        )
    except StateConflictError:
        pass
    else:
        raise AssertionError("서명된 계약서가 다시 서명됐다")
    assert not uow.committed


@check
def 계약_파기는_HR만_사유를_적어야_가능하다():
    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True))
    try:
        cancel_salary_contract(
            actor=Actor(user_id=10, role=Role.EMPLOYEE), contract_id=1, reason="그냥", uow=uow
        )
    except PermissionDeniedError:
        pass
    else:
        raise AssertionError("HR이 아닌데 파기됐다")

    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True))
    try:
        cancel_salary_contract(
            actor=Actor(user_id=1, role=Role.HR_ADMIN), contract_id=1, reason="   ", uow=uow
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("사유 없이 파기됐다")
    assert not uow.committed

    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True))
    파기됨 = cancel_salary_contract(
        actor=Actor(user_id=1, role=Role.HR_ADMIN),
        contract_id=1,
        reason="연봉 산정 오류로 재발송 예정",
        uow=uow,
    )
    assert 파기됨.status is ContractStatus.CANCELLED
    assert 파기됨.cancel_reason == "연봉 산정 오류로 재발송 예정"
    assert 파기됨.cancelled_by == 1
    assert uow.actions == ["CONTRACT_CANCELLED"]
    assert uow.committed


@check
def 재발송은_원본을_보존하고_새_버전을_만든다():
    원본 = _가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True)
    uow = _가짜UoW(contract=원본)
    새계약 = resend_salary_contract(
        actor=Actor(user_id=1, role=Role.HR_ADMIN),
        contract_id=1,
        reason="등급 정정에 따른 재발송",
        uow=uow,
    )
    # 원본은 파기 상태로 남는다
    assert uow.contract.status is ContractStatus.CANCELLED
    assert uow.contract.is_locked is True
    # 새 계약은 서명값이 전부 비워진 SENT 상태다
    assert 새계약.status is ContractStatus.SENT
    assert 새계약.version == 2
    assert 새계약.resent_from_id == 1
    assert 새계약.is_locked is False
    assert 새계약.signature_image is None
    assert 새계약.document_hash is None
    assert 새계약.signed_at is None
    assert 새계약.cancel_reason is None
    assert 새계약.pdf_status is PdfStatus.NONE
    assert uow.actions == ["CONTRACT_CANCELLED", "CONTRACT_RESENT"]
    assert uow.committed


def _서명(**overrides) -> SignatureInput:
    base = dict(
        consent_checked=True,
        signer_name="홍길동",
        signature_image=b"\x89PNG-가상서명",
        ip="192.0.2.10",
        user_agent="Mozilla/5.0 (검사용)",
    )
    base.update(overrides)
    return SignatureInput(**base)
```

`selfcheck.py` 상단 import에 `from dataclasses import replace`를 추가한다.

- [ ] **Step 2: 실패하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `ModuleNotFoundError: No module named 'hr_eval.usecases'`

- [ ] **Step 3: 포트를 만든다**

`backend/hr_eval/ports.py`:

```python
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
```

- [ ] **Step 4: 유스케이스를 만든다**

`backend/hr_eval/usecases.py`:

```python
"""부수효과가 있는 유스케이스.

각 함수가 트랜잭션 하나다. 검증에 실패하면 commit()에 도달하지 않으므로
부분 저장이 남지 않는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Sequence

from hr_eval.domain.contract_rules import build_document_hash
from hr_eval.domain.errors import (
    Issue,
    PermissionDeniedError,
    StateConflictError,
    ValidationError,
)
from hr_eval.domain.kpi import KpiInput, validate_kpi_set
from hr_eval.domain.models import (
    Actor,
    AuditEntry,
    ContractStatus,
    KpiSheet,
    KpiSheetStatus,
    PdfStatus,
    Role,
    SalaryContract,
    SignatureInput,
)
from hr_eval.ports import UnitOfWork


def _now() -> datetime:
    return datetime.now(timezone.utc)


def submit_kpi_goal(
    *,
    actor: Actor,
    period_id: int,
    user_id: int,
    kpis: Sequence[KpiInput],
    uow: UnitOfWork,
) -> KpiSheet:
    if actor.user_id != user_id and actor.role is not Role.HR_ADMIN:
        raise PermissionDeniedError("본인 또는 인사담당자만 KPI를 제출할 수 있습니다")

    with uow:
        period = uow.periods.get(period_id)
        if not period.is_kpi_window_open:
            raise StateConflictError(
                "KPI 수정 기간이 열려 있지 않습니다. 인사담당자에게 문의하십시오."
            )

        sheet = uow.kpis.get_sheet(period_id, user_id)
        if sheet is not None and sheet.status is KpiSheetStatus.DIVISION_HEAD_APPROVED:
            raise StateConflictError(
                "확정된 KPI는 직접 수정할 수 없습니다. 수정 요청을 등록하십시오."
            )

        validate_kpi_set(kpis)

        if sheet is None:
            sheet = uow.kpis.create_sheet(period_id, user_id, actor.user_id)

        uow.kpis.replace_kpis(sheet.id, kpis)
        sheet = replace(sheet, status=KpiSheetStatus.DRAFT, submitted_at=_now())
        uow.kpis.save_sheet(sheet)

        uow.audit.append(
            AuditEntry(
                action="KPI_SUBMITTED",
                entity_type="kpi_sheet",
                entity_id=sheet.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                after_data={"kpi_count": len(kpis)},
            )
        )
        uow.commit()

    return sheet


def finalize_salary_contract(
    *,
    actor: Actor,
    contract_id: int,
    signature: SignatureInput,
    uow: UnitOfWork,
) -> SalaryContract:
    """전자서명 완료 처리. 성공하면 계약서가 READ_ONLY로 잠긴다."""
    with uow:
        contract = uow.contracts.get_for_update(contract_id)

        if actor.user_id != contract.user_id:
            raise PermissionDeniedError(
                "연봉계약서는 본인만 서명할 수 있습니다 (대리 서명 불가)"
            )
        if contract.status is not ContractStatus.SENT:
            raise StateConflictError(
                f"서명할 수 없는 상태입니다 (현재 {contract.status.value})"
            )

        issues: list[Issue] = []
        if not signature.consent_checked:
            issues.append(Issue("CONSENT_REQUIRED", "계약 내용 동의에 체크해야 합니다"))
        if not signature.signer_name.strip():
            issues.append(Issue("SIGNER_NAME_REQUIRED", "서명자 성명을 입력해야 합니다"))
        if not signature.signature_image:
            issues.append(Issue("SIGNATURE_IMAGE_REQUIRED", "손글씨 서명이 필요합니다"))
        if not signature.ip:
            issues.append(Issue("SIGNER_IP_REQUIRED", "서명자 IP를 확인할 수 없습니다"))
        if issues:
            raise ValidationError(issues)

        signed = replace(
            contract,
            status=ContractStatus.SIGNED,
            is_locked=True,
            consent_checked=True,
            signer_user_id=actor.user_id,
            signer_name=signature.signer_name.strip(),
            signer_ip=signature.ip,
            signer_user_agent=signature.user_agent,
            signature_image=signature.signature_image,
            document_hash=build_document_hash(contract.as_document()),
            signed_at=_now(),
            pdf_status=PdfStatus.PENDING,
        )
        uow.contracts.save(signed)

        uow.audit.append(
            AuditEntry(
                action="CONTRACT_SIGNED",
                entity_type="salary_contract",
                entity_id=signed.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                before_data={"status": contract.status.value},
                after_data={
                    "status": signed.status.value,
                    "document_hash": signed.document_hash,
                },
                ip=signature.ip,
                user_agent=signature.user_agent,
            )
        )
        uow.commit()

    return signed


def _apply_cancel(
    contract: SalaryContract, actor: Actor, reason: str
) -> SalaryContract:
    return replace(
        contract,
        status=ContractStatus.CANCELLED,
        cancelled_at=_now(),
        cancelled_by=actor.user_id,
        cancel_reason=reason.strip(),
    )


def _guard_cancel(actor: Actor, reason: str) -> None:
    if actor.role is not Role.HR_ADMIN:
        raise PermissionDeniedError("계약 파기·재발송은 인사담당자만 할 수 있습니다")
    if not reason or not reason.strip():
        raise ValidationError([Issue("CANCEL_REASON_REQUIRED", "파기 사유를 입력해야 합니다")])


def cancel_salary_contract(
    *, actor: Actor, contract_id: int, reason: str, uow: UnitOfWork
) -> SalaryContract:
    _guard_cancel(actor, reason)

    with uow:
        contract = uow.contracts.get_for_update(contract_id)
        if contract.status is ContractStatus.CANCELLED:
            raise StateConflictError("이미 파기된 계약서입니다")

        cancelled = _apply_cancel(contract, actor, reason)
        uow.contracts.save(cancelled)
        uow.audit.append(
            AuditEntry(
                action="CONTRACT_CANCELLED",
                entity_type="salary_contract",
                entity_id=cancelled.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                before_data={"status": contract.status.value},
                after_data={"status": cancelled.status.value},
                reason=cancelled.cancel_reason,
            )
        )
        uow.commit()

    return cancelled


def resend_salary_contract(
    *, actor: Actor, contract_id: int, reason: str, uow: UnitOfWork
) -> SalaryContract:
    """파기와 새 계약서 발행을 한 트랜잭션으로 처리한다.

    원본은 서명값을 그대로 안은 채 CANCELLED로 남는다. 감사 추적이
    끊기지 않도록 덮어쓰지 않고 새 행을 만든다.
    """
    _guard_cancel(actor, reason)

    with uow:
        original = uow.contracts.get_for_update(contract_id)
        if original.status is ContractStatus.CANCELLED:
            raise StateConflictError("이미 파기된 계약서입니다")

        cancelled = _apply_cancel(original, actor, reason)
        uow.contracts.save(cancelled)
        uow.audit.append(
            AuditEntry(
                action="CONTRACT_CANCELLED",
                entity_type="salary_contract",
                entity_id=cancelled.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                before_data={"status": original.status.value},
                after_data={"status": cancelled.status.value},
                reason=cancelled.cancel_reason,
            )
        )

        fresh = replace(
            original,
            id=None,
            version=original.version + 1,
            resent_from_id=original.id,
            status=ContractStatus.SENT,
            is_locked=False,
            sent_at=_now(),
            consent_checked=False,
            signer_user_id=None,
            signer_name=None,
            signer_ip=None,
            signer_user_agent=None,
            signature_image=None,
            document_hash=None,
            signed_at=None,
            pdf_status=PdfStatus.NONE,
            pdf_path=None,
            pdf_generated_at=None,
            cancelled_at=None,
            cancelled_by=None,
            cancel_reason=None,
        )
        fresh = uow.contracts.insert(fresh)

        uow.audit.append(
            AuditEntry(
                action="CONTRACT_RESENT",
                entity_type="salary_contract",
                entity_id=fresh.id,
                actor_user_id=actor.user_id,
                actor_role=actor.role,
                after_data={"version": fresh.version, "resent_from_id": fresh.resent_from_id},
                reason=reason.strip(),
            )
        )
        uow.commit()

    return fresh
```

- [ ] **Step 5: 통과하는 걸 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `41/41 통과`

- [ ] **Step 6: 커밋한다**

```bash
git add backend/hr_eval && git commit -m "hr_eval: 저장소 포트와 유스케이스 4종 (KPI 제출, 서명·파기·재발송)"
```

---

## Task 8: PostgreSQL DDL + 제약조건 검사 스크립트

**Files:**
- Create: `backend/hr_eval/sql/001_init.sql`
- Create: `backend/hr_eval/sql/checks.sql`

**Interfaces:**
- Consumes: `domain/models.py`의 enum 값 (문자열이 정확히 일치해야 한다)
- Produces: 테이블 11개, ENUM 12종, 트리거 3종. 다음 PR(저장소 구현체)이 이 스키마를 쓴다.

**로컬에서는 검증할 수 없다.** 이 개발 환경에는 `psql`도 `docker`도 없다. DDL의 정답 판정은 Task 9에서 붙이는 CI의 PostgreSQL 서비스 컨테이너가 한다. 그래서 Task 8과 Task 9는 같은 PR에서 연달아 진행하고, **CI가 초록이 되기 전에는 DDL이 맞다고 말하지 않는다.**

- [ ] **Step 1: DDL을 스펙에서 그대로 옮긴다**

`backend/hr_eval/sql/001_init.sql`을 만들고, 스펙 문서의 SQL 블록을 **아래 순서대로** 이어 붙인다. 순서를 바꾸면 참조가 깨진다.

1. 스펙 §3.1 — `CREATE TYPE` 12개 (`quota_mode` 포함)
2. 스펙 §3.2 — `departments`, `users`, `ALTER TABLE departments ... fk_departments_leader`
3. 스펙 §3.3 — `evaluation_periods`, `department_quotas`
4. 스펙 §3.4 — `kpi_sheets`, `kpis`, `kpi_change_requests`
5. 스펙 §3.5 — `evaluations`
6. 스펙 §3.6 — `salary_raise_rates`, `salary_contracts`
7. 스펙 §3.7 — `audit_logs`
8. 스펙 §3.8 — 트리거 함수 3종과 트리거

파일 맨 위에 이 주석을 넣는다.

```sql
-- hr_eval 스키마 초기화
--
-- 설계 근거: docs/superpowers/specs/2026-08-24-hr-evaluation-contract-design.md
-- 적용:      psql -d <DB> -v ON_ERROR_STOP=1 -f 001_init.sql
--
-- 이 파일에는 실제 직원 정보도, 연봉 인상률 수치도 들어가지 않는다.
-- hr-portal은 Public 저장소이고, 그런 데이터는 서버 DB에만 존재한다.
```

- [ ] **Step 2: 스펙의 트리거 함수를 한 군데 고쳐서 옮긴다**

스펙 §3.8의 `enforce_contract_lock`은 `DECLARE old_frozen salary_contracts%ROWTYPE := OLD;`처럼 선언부에서 바로 대입한다. 이건 plpgsql에서 동작이 보장되지 않으므로 **본문에서 대입하는 형태로 바꿔 쓴다.** 나머지는 스펙 그대로다.

```sql
CREATE OR REPLACE FUNCTION enforce_contract_lock() RETURNS trigger
LANGUAGE plpgsql AS $fn$
DECLARE
    old_frozen salary_contracts%ROWTYPE;
    new_frozen salary_contracts%ROWTYPE;
BEGIN
    IF NOT OLD.is_locked THEN
        RETURN NEW;
    END IF;

    old_frozen := OLD;
    new_frozen := NEW;

    -- 잠금 후에도 바뀔 수 있는 칼럼만 비교에서 뺀다
    old_frozen.status           := NULL; new_frozen.status           := NULL;
    old_frozen.cancelled_at     := NULL; new_frozen.cancelled_at     := NULL;
    old_frozen.cancelled_by     := NULL; new_frozen.cancelled_by     := NULL;
    old_frozen.cancel_reason    := NULL; new_frozen.cancel_reason    := NULL;
    old_frozen.pdf_status       := NULL; new_frozen.pdf_status       := NULL;
    old_frozen.pdf_path         := NULL; new_frozen.pdf_path         := NULL;
    old_frozen.pdf_generated_at := NULL; new_frozen.pdf_generated_at := NULL;
    old_frozen.updated_at       := NULL; new_frozen.updated_at       := NULL;

    IF old_frozen IS DISTINCT FROM new_frozen THEN
        RAISE EXCEPTION 'READ_ONLY: 서명 완료된 계약서는 수정할 수 없습니다 (contract_id=%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.status NOT IN ('SIGNED', 'CANCELLED') THEN
        RAISE EXCEPTION 'READ_ONLY: 서명 완료된 계약서는 파기 외 상태로 바꿀 수 없습니다 (contract_id=%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$fn$;
```

- [ ] **Step 3: `updated_at` 트리거를 모든 대상 테이블에 붙인다**

스펙 §3.8은 `set_updated_at()` 함수만 보여주고 "모든 테이블에 붙인다"고 했다. 실제 트리거 선언을 파일 끝에 명시적으로 쓴다.

```sql
CREATE TRIGGER trg_departments_updated_at        BEFORE UPDATE ON departments        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_users_updated_at              BEFORE UPDATE ON users              FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_periods_updated_at            BEFORE UPDATE ON evaluation_periods FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_quotas_updated_at             BEFORE UPDATE ON department_quotas  FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_kpi_sheets_updated_at         BEFORE UPDATE ON kpi_sheets         FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_kpis_updated_at               BEFORE UPDATE ON kpis               FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_kcr_updated_at                BEFORE UPDATE ON kpi_change_requests FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_evaluations_updated_at        BEFORE UPDATE ON evaluations        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_raise_rates_updated_at        BEFORE UPDATE ON salary_raise_rates FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER trg_contracts_updated_at          BEFORE UPDATE ON salary_contracts   FOR EACH ROW EXECUTE FUNCTION set_updated_at();
```

`salary_contracts`는 `trg_contracts_updated_at`과 `trg_contract_lock`이 둘 다 BEFORE UPDATE다. PostgreSQL은 같은 시점 트리거를 **이름 알파벳 순**으로 실행하므로 `trg_contract_lock`이 먼저 돈다 — 잠금 검사가 `updated_at` 갱신보다 앞서야 하니 이 순서가 맞다. 트리거 이름을 바꾸지 말 것.

- [ ] **Step 4: 제약조건이 실제로 막는지 확인하는 스크립트를 쓴다**

`backend/hr_eval/sql/checks.sql`:

```sql
-- 001_init.sql 이 적용된 DB에서 제약조건이 실제로 동작하는지 확인한다.
-- 막혀야 할 것이 통과하면 RAISE EXCEPTION 으로 CI를 깨뜨린다.
-- 마지막에 ROLLBACK 하므로 데이터가 남지 않는다.

BEGIN;

INSERT INTO departments (code, name, level) VALUES ('DIV1', '가상본부', 'DIVISION');
INSERT INTO users (employee_no, name, email, role, department_id, hire_date)
VALUES ('T001', '가상직원', 't001@example.invalid', 'EMPLOYEE',
        (SELECT id FROM departments WHERE code = 'DIV1'), DATE '2020-01-01');
INSERT INTO evaluation_periods (year, type, name, starts_on, ends_on)
VALUES (2027, 'ANNUAL', '가상 정기평가', DATE '2027-01-01', DATE '2027-12-31');

-- (1) 100점 이하 S등급은 거부돼야 한다
DO $chk$
BEGIN
    INSERT INTO evaluations (period_id, user_id, division_id, kpi_score, grade)
    VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
            (SELECT id FROM users LIMIT 1),
            (SELECT id FROM departments LIMIT 1),
            100, 'S');
    RAISE EXCEPTION '[검사 실패] 100점짜리 S등급이 저장됐습니다';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE '[통과] 100점 이하 S등급 거부';
END
$chk$;

-- (2) 그룹 정원 합계가 인원수와 다르면 거부돼야 한다
DO $chk$
BEGIN
    INSERT INTO department_quotas (period_id, department_id, headcount, quota_mode,
                                   quota_upper, quota_lower, cap_s)
    VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
            (SELECT id FROM departments LIMIT 1),
            4, 'GROUPED', 1, 99, 1);
    RAISE EXCEPTION '[검사 실패] 정원 합계가 인원수와 다른데 저장됐습니다';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE '[통과] 그룹 정원 합계 불일치 거부';
END
$chk$;

-- (3) GROUPED 모드에 개별 정원 칼럼을 채우면 거부돼야 한다
DO $chk$
BEGIN
    INSERT INTO department_quotas (period_id, department_id, headcount, quota_mode,
                                   quota_upper, quota_lower, quota_a, cap_s)
    VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
            (SELECT id FROM departments LIMIT 1),
            4, 'GROUPED', 1, 3, 1, 1);
    RAISE EXCEPTION '[검사 실패] 모드에 맞지 않는 칼럼이 저장됐습니다';
EXCEPTION WHEN check_violation THEN
    RAISE NOTICE '[통과] 모드 불일치 칼럼 거부';
END
$chk$;

-- 서명 완료 계약서 하나를 만든다 (이후 검사의 대상)
INSERT INTO evaluations (period_id, user_id, division_id, kpi_score, bonus_score,
                         bonus_reason, grade, status)
VALUES ((SELECT id FROM evaluation_periods LIMIT 1),
        (SELECT id FROM users LIMIT 1),
        (SELECT id FROM departments LIMIT 1),
        95, 10, '가상 가점 사유', 'S', 'CONFIRMED');

INSERT INTO salary_contracts (
    period_id, user_id, evaluation_id, grade,
    base_salary_before, raise_pct, base_salary_after,
    contract_starts_on, contract_ends_on,
    status, is_locked, consent_checked,
    signer_user_id, signer_name, signer_ip, signature_image, document_hash, signed_at
) VALUES (
    (SELECT id FROM evaluation_periods LIMIT 1),
    (SELECT id FROM users LIMIT 1),
    (SELECT id FROM evaluations LIMIT 1),
    'S', 50000000, 8.00, 54000000,
    DATE '2027-01-01', DATE '2027-12-31',
    'SIGNED', TRUE, TRUE,
    (SELECT id FROM users LIMIT 1), '가상직원', '192.0.2.10'::inet,
    '\x00'::bytea, repeat('a', 64), now()
);

-- (4) 잠긴 계약서의 금액 수정은 거부돼야 한다
DO $chk$
BEGIN
    UPDATE salary_contracts SET base_salary_after = 99000000
    WHERE id = (SELECT id FROM salary_contracts LIMIT 1);
    RAISE EXCEPTION '[검사 실패] 잠긴 계약서의 금액이 수정됐습니다';
EXCEPTION WHEN restrict_violation THEN
    RAISE NOTICE '[통과] 잠긴 계약서 수정 거부';
END
$chk$;

-- (5) 잠긴 계약서의 파기는 허용돼야 한다
UPDATE salary_contracts
SET status = 'CANCELLED', cancelled_at = now(),
    cancelled_by = (SELECT id FROM users LIMIT 1),
    cancel_reason = '가상 파기 사유'
WHERE id = (SELECT id FROM salary_contracts LIMIT 1);

DO $chk$
BEGIN
    IF (SELECT status FROM salary_contracts LIMIT 1) <> 'CANCELLED' THEN
        RAISE EXCEPTION '[검사 실패] 잠긴 계약서를 파기하지 못했습니다';
    END IF;
    RAISE NOTICE '[통과] 잠긴 계약서 파기 허용';
END
$chk$;

-- (6) 계약서 삭제는 거부돼야 한다
DO $chk$
BEGIN
    DELETE FROM salary_contracts;
    RAISE EXCEPTION '[검사 실패] 계약서가 삭제됐습니다';
EXCEPTION WHEN restrict_violation THEN
    RAISE NOTICE '[통과] 계약서 삭제 거부';
END
$chk$;

-- (7) 감사로그 수정·삭제는 거부돼야 한다
INSERT INTO audit_logs (action, entity_type, entity_id)
VALUES ('TEST', 'salary_contract', 1);

DO $chk$
BEGIN
    UPDATE audit_logs SET action = 'TAMPERED';
    RAISE EXCEPTION '[검사 실패] 감사로그가 수정됐습니다';
EXCEPTION WHEN restrict_violation THEN
    RAISE NOTICE '[통과] 감사로그 수정 거부';
END
$chk$;

ROLLBACK;
```

- [ ] **Step 5: 커밋한다**

아직 검증하지 못한 상태다. 커밋 메시지에 그 사실을 적는다.

```bash
git add backend/hr_eval/sql && git commit -m "hr_eval: PostgreSQL DDL과 제약조건 검사 스크립트 추가 (CI 검증 대기)"
```

---

## Task 9: CI에 도메인·스키마 검사 붙이기

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/superpowers/plans/2026-08-24-hr-eval-domain.md` (해당 없음 — 이 단계는 CI만 손댄다)

**Interfaces:**
- Consumes: Task 1~8의 결과물 전부
- Produces: PR마다 자동으로 도는 검사 2개. `promote` job이 이 둘을 기다린다.

- [ ] **Step 1: 두 job을 추가한다**

`.github/workflows/ci.yml`의 `smoke` job 아래, `promote` job 위에 넣는다.

```yaml
  domain:
    name: 평가·계약 도메인 검사
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'

      - name: 도메인 로직 자체 검증
        working-directory: backend
        run: python -m hr_eval.selfcheck

  schema:
    name: DB 스키마 검사
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          # CI 전용 일회성 컨테이너다. 비밀번호를 아예 두지 않아
          # 시크릿 검사에도 걸리지 않는다.
          POSTGRES_HOST_AUTH_METHOD: trust
          POSTGRES_DB: hr_portal_ci
        options: >-
          --health-cmd pg_isready
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10
        ports:
          - 5432:5432
    steps:
      - uses: actions/checkout@v4

      - name: 스키마 적용
        run: |
          psql -h 127.0.0.1 -U postgres -d hr_portal_ci -v ON_ERROR_STOP=1 \
            -f backend/hr_eval/sql/001_init.sql

      - name: 제약조건 동작 확인
        run: |
          psql -h 127.0.0.1 -U postgres -d hr_portal_ci -v ON_ERROR_STOP=1 \
            -f backend/hr_eval/sql/checks.sql
```

- [ ] **Step 2: `promote` job이 새 검사를 기다리게 한다**

`.github/workflows/ci.yml`의 `promote` job에서 한 줄을 고친다.

```yaml
    needs: [validate, smoke, domain, schema]
```

(기존: `needs: [validate, smoke]`)

이걸 빼먹으면 스키마가 깨진 채로 `release` 브랜치가 갱신되고 2분 뒤 서버가 가져간다.

- [ ] **Step 3: 로컬에서 돌 수 있는 것만 먼저 확인한다**

```bash
cd backend && python -m hr_eval.selfcheck
```

Expected: `41/41 통과`

스키마 검사는 로컬에 `psql`이 없어 돌릴 수 없다. PR을 올린 뒤 CI 결과로 확인한다.

- [ ] **Step 4: 커밋하고 푸시한다**

```bash
git add .github/workflows/ci.yml
git commit -m "CI: 평가·계약 도메인 검사와 DB 스키마 검사 job 추가"
git push -u origin feature/hr-eval-domain
```

- [ ] **Step 5: PR을 만든다 — 사용자 승인 후에만**

`CLAUDE.md` 규칙상 PR 생성은 사용자 자격 증명을 재사용하므로 **매번 사용자에게 먼저 물어본다.** PR 본문에 반드시 넣을 것:

- 무엇을: 평가 쿼터·KPI·전자계약 스키마와 도메인 로직 (동작하는 화면은 없음)
- 왜: 규칙을 코드로 확정해두고, DB·로그인·화면은 다음 PR로
- 배포 시 주의사항: **이 PR은 서버에 아무 영향이 없다.** `docker-compose.yml`을 건드리지 않았고 PostgreSQL 컨테이너도 아직 없다. `001_init.sql`은 저장소에 파일로만 존재하며 어디에도 자동 적용되지 않는다.

- [ ] **Step 6: CI가 초록인지 확인한다**

`domain`과 `schema` job이 둘 다 통과해야 한다. `schema`가 실패하면 DDL 오류다 — psql 로그의 줄 번호를 보고 `001_init.sql`을 고친 뒤 다시 푸시한다. **CI가 초록이 되기 전에는 스키마가 맞다고 보고하지 않는다.**

---

## 완료 기준

- [ ] `cd backend && python -m hr_eval.selfcheck`가 41/41 통과
- [ ] CI의 `domain` job 통과
- [ ] CI의 `schema` job 통과 (DDL 적용 + 제약조건 7종 동작 확인)
- [ ] `backend/requirements.txt`에 변경 없음
- [ ] `docker-compose.yml`, `nginx.conf`, `public/index.html`에 변경 없음
- [ ] `grep -rn "domain" backend/hr_eval/domain/*.py`에서 `ports`·`usecases`·`psycopg`·`flask` import가 하나도 안 나옴

## 이 계획에 없는 것 (다음 PR)

스펙 8장 그대로다.

1. 로그인·세션·역할 미들웨어
2. `docker-compose.yml`에 PostgreSQL 추가 + 마이그레이션 실행 + `ports.py` 구현체
3. Flask API 엔드포인트
4. `public/index.html` 평가·성과 / 급여·계약 화면 (손글씨 서명 캔버스)
5. `reportlab` PDF 렌더링
6. KPI 수정 요청 유스케이스 (`request_kpi_change` / `approve_kpi_change`) — 검증 함수(`validate_kpi_set`)는 이번에 만들지만, 요청·승인 흐름은 전자결재 모듈과 겹쳐서 그쪽과 같이 설계한다
