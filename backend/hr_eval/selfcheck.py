"""hr_eval 도메인 로직 자체 검증.

    cd backend && python -m hr_eval.selfcheck

테스트 프레임워크를 쓰지 않는다. 검사 하나가 실패해도 나머지는 계속 돌고,
마지막에 몇 개가 깨졌는지 알려준다.
"""

from __future__ import annotations

import pathlib
import sys
import traceback
from dataclasses import replace
from datetime import date, datetime
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
from hr_eval.domain.models import (
    Actor,
    AuditEntry,
    ContractStatus,
    EvaluationPeriod,
    Grade,
    KpiSheet,
    KpiSheetStatus,
    PdfStatus,
    PeriodType,
    Role,
    SalaryContract,
    SignatureInput,
)
from hr_eval.domain.quota import (
    SMALL_DIVISION_THRESHOLD,
    GroupedQuota,
    IndividualQuota,
    QuotaMode,
    calculate_department_quota,
    round_half_up,
)
from hr_eval.domain.contract_rules import (
    build_document_hash,
    calculate_new_salary,
    resolve_raise_pct,
)
from hr_eval.domain.grading import Assignment, validate_and_assign_evaluation_grades
from hr_eval.domain.kpi import KpiInput, validate_kpi_set
from hr_eval.domain.access import Act, Delegation, Pool, PoolRef, Target, can
from hr_eval.domain.members import (
    MSG_DENIED,
    Member,
    SsoOutcome,
    SsoToken,
    resolve_sso_user,
    validate_members,
)
from hr_eval.usecases import (
    cancel_salary_contract,
    finalize_salary_contract,
    resend_salary_contract,
    submit_kpi_goal,
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


@check
def enum_값이_DDL의_ENUM과_문자열로_일치한다():
    """DDL의 CREATE TYPE 값과 어긋나면 저장할 때 터진다."""
    assert [g.value for g in Grade] == ["S", "A", "B", "C", "D"]
    assert [r.value for r in Role] == [
        "TEAM_LEADER",
        "DIVISION_HEAD",
        "CEO",
        "HR_ADMIN",
    ]
    assert [s.value for s in ContractStatus] == ["DRAFT", "SENT", "SIGNED", "CANCELLED"]
    assert [p.value for p in PdfStatus] == ["NONE", "PENDING", "GENERATED", "FAILED"]
    assert [k.value for k in KpiSheetStatus] == [
        "DRAFT",
        "TEAM_LEADER_APPROVED",
        "DIVISION_HEAD_APPROVED",
    ]
    assert [p.value for p in PeriodType] == ["ANNUAL", "MIDTERM"]
    assert [q.value for q in QuotaMode] == ["INDIVIDUAL", "GROUPED"]


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


@check
def 등급으로_인상률을_찾는다():
    # 인상률 수치는 salary_raise_rates 테이블에만 있어야 한다 (hr-portal은 Public
    # 저장소다). 아래 표는 전부 가상 값이라 실제 인상률과 겹치지 않는다.
    표 = {
        Grade.S: Decimal("11.11"),
        Grade.A: Decimal("7.77"),
        Grade.B: Decimal("5.55"),
        Grade.C: Decimal("3.33"),
        Grade.D: Decimal("1.11"),
    }
    assert resolve_raise_pct(표, Grade.A) == Decimal("7.77")

    try:
        resolve_raise_pct({Grade.A: Decimal("7.77")}, Grade.D)
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
            actor=Actor(user_id=99, position=Role.TEAM_LEADER),
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
            actor=Actor(user_id=10),
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
            actor=Actor(user_id=10),
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
        actor=Actor(user_id=10),
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
def KPI재제출은_이전_팀장승인_흔적을_지운다():
    """재제출인데 team_leader_approved_at이 남으면 kpi_sheets_tl_chk를
    DRAFT 상태에서만 우회로 통과시켜온 것과 같은 구멍이 도메인 계층에도 생긴다."""
    uow = _가짜UoW(
        sheet=KpiSheet(
            id=7,
            period_id=1,
            user_id=10,
            status=KpiSheetStatus.TEAM_LEADER_APPROVED,
            team_leader_approved_at=datetime(2027, 1, 10, 9, 0, 0),
            team_leader_approved_by=20,
        )
    )
    sheet = submit_kpi_goal(
        actor=Actor(user_id=10),
        period_id=1,
        user_id=10,
        kpis=_KPI("40.00", "30.00", "30.00"),
        uow=uow,
    )
    assert sheet.status is KpiSheetStatus.DRAFT
    assert sheet.team_leader_approved_at is None
    assert sheet.team_leader_approved_by is None


@check
def 계약서는_본인만_서명할_수_있다():
    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10))
    try:
        finalize_salary_contract(
            actor=Actor(user_id=11, is_hr=True),  # HR도 대리 서명 불가
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
                actor=Actor(user_id=10),
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
        actor=Actor(user_id=10),
        contract_id=1,
        signature=_서명(),
        uow=uow,
    )
    assert signed.status is ContractStatus.SIGNED
    assert signed.is_locked is True
    assert signed.signer_user_id == 10
    assert signed.signer_ip == "192.0.2.1"
    assert signed.signed_at is not None
    assert len(signed.document_hash) == 64
    assert signed.pdf_status is PdfStatus.PENDING
    assert uow.actions == ["CONTRACT_SIGNED"]
    assert uow.audit_entries[0].ip == "192.0.2.1"
    assert uow.committed


@check
def 이미_서명된_계약서는_다시_서명할_수_없다():
    서명됨 = _가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True)
    uow = _가짜UoW(contract=서명됨)
    try:
        finalize_salary_contract(
            actor=Actor(user_id=10),
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
            actor=Actor(user_id=10), contract_id=1, reason="그냥", uow=uow
        )
    except PermissionDeniedError:
        pass
    else:
        raise AssertionError("HR이 아닌데 파기됐다")

    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True))
    try:
        cancel_salary_contract(
            actor=Actor(user_id=1, is_hr=True), contract_id=1, reason="   ", uow=uow
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("사유 없이 파기됐다")
    assert not uow.committed

    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True))
    파기됨 = cancel_salary_contract(
        actor=Actor(user_id=1, is_hr=True),
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
        actor=Actor(user_id=1, is_hr=True),
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
        signature_image="\x89PNG-가상서명".encode("utf-8"),
        ip="192.0.2.1",
        user_agent="Mozilla/5.0 (검사용)",
    )
    base.update(overrides)
    return SignatureInput(**base)


@check
def 총점은_110점을_넘을_수_없다():
    """DDL의 eval_total_max_chk 와 같은 규칙을 도메인에서도 막는다."""
    q = calculate_department_quota(10)
    멤버 = list(range(1, 11))

    def 백십일점():
        validate_and_assign_evaluation_grades(
            quota=q,
            member_ids=멤버,
            assignments=_배정(
                (1, "111.00", Grade.S),
                *[(i, "80", Grade.C) for i in range(2, 11)],
            ),
        )

    assert "SCORE_ABOVE_MAX" in _오류코드(백십일점)

    # 정확히 110점은 통과한다 (상한은 이하이므로 경계 포함)
    결과 = validate_and_assign_evaluation_grades(
        quota=q,
        member_ids=멤버,
        assignments=_배정(
            (1, "110.00", Grade.S),
            (2, "90", Grade.B),
            (3, "60", Grade.D),
            *[(i, "80", Grade.C) for i in range(4, 11)],
        ),
    )
    assert 결과.s_count == 1


@check
def 상한_상수가_DDL과_같은_값이다():
    from hr_eval.domain.grading import MAX_TOTAL_SCORE

    assert MAX_TOTAL_SCORE == Decimal("110")
    ddl = (
        pathlib.Path(__file__).with_name("sql") / "001_init.sql"
    ).read_text(encoding="utf-8")
    # SQLite 스키마는 점수를 100배 정수로 저장한다 (110.00점 = 11000).
    # 도메인 상수와 DDL 이 같은 값을 말하는지 확인한다.
    expected = int(MAX_TOTAL_SCORE * 100)
    assert f"CHECK (kpi_score_x100 + bonus_score_x100 <= {expected})" in ddl

    from hr_eval.domain.grading import S_GRADE_MIN_SCORE

    expected_s = int(S_GRADE_MIN_SCORE * 100)
    assert (
        f"CHECK (grade <> 'S' OR kpi_score_x100 + bonus_score_x100 > {expected_s})" in ddl
    )


# ------------------------------------------------------------------
# 로그인 설계 9장 4·8·13·14번 (사람 연결 · 사람별 설정 · 첫 HR · 대표이사와 HR에 맡기기)
# 사람·번호는 전부 가상 값이다.
# ------------------------------------------------------------------


def _명단(바꿈: dict | None = None) -> list[Member]:
    """가상 구성원. 바꿈 = {id: {칸: 값}}. HR 은 42 한 명, 44 는 포털 사용 꺼짐, 24 는 하이웍스 미연결."""
    base = [
        Member(1, "가상대표", 900, "E001", "HW-1", portal_enabled=True, position=Role.CEO,
               writes_kpi=False, in_evaluation=False, has_contract=False),
        Member(20, "가상본부장", 1, "E020", "HW-20", portal_enabled=True, position=Role.DIVISION_HEAD,
               in_evaluation=False, has_contract=False),
        Member(21, "가상팀장", 11, "E021", "HW-21", portal_enabled=True, position=Role.TEAM_LEADER),
        Member(22, "가상팀원", 11, "E022", "HW-22", portal_enabled=True),
        Member(24, "가상미연결", 12, "E024", None, portal_enabled=True),
        Member(32, "가상퇴사", 13, "E032", "HW-32", is_active=False, portal_enabled=True),
        Member(41, "가상HR팀장", 91, "E041", "HW-41", portal_enabled=True, position=Role.TEAM_LEADER),
        Member(42, "가상HR", 91, "E042", "HW-42", portal_enabled=True, is_hr=True),
        Member(44, "가상준비중", 92, "E044", "HW-44"),
    ]
    바꿈 = 바꿈 or {}
    return [replace(m, **바꿈.get(m.id, {})) for m in base]


@check
def 사람연결_하이웍스_번호로_찾는다():
    r = resolve_sso_user(SsoToken("HW-22"), _명단())
    assert (r.outcome, r.user_id, r.link, r.bootstrap_hr, r.audit) == (SsoOutcome.OK, 22, False, False, ())


@check
def 사람연결_미등록과_비활성은_같은_결과_같은_문구다():
    """등록 여부를 구분해 알려 주지 않는다 (9장 4)."""
    없음 = resolve_sso_user(SsoToken("HW-999"), _명단())
    퇴사 = resolve_sso_user(SsoToken("HW-32"), _명단())
    assert 없음.outcome is SsoOutcome.DENIED
    assert (없음.outcome, 없음.user_id, 없음.message) == (퇴사.outcome, 퇴사.user_id, 퇴사.message)
    assert 퇴사.message == MSG_DENIED and 퇴사.audit == ()


@check
def 사람연결_사번_대안은_켜야만_쓴다():
    토큰 = SsoToken("HW-NEW", "id24", "가상미연결", "E024")
    assert resolve_sso_user(토큰, _명단()).outcome is SsoOutcome.DENIED

    r = resolve_sso_user(토큰, _명단(), link_by_employee_no=True)
    assert (r.outcome, r.user_id, r.link) == (SsoOutcome.OK, 24, True)
    assert [e.action for e in r.audit] == ["SSO_LINK"]
    assert r.audit[0].entity_id == 24 and r.audit[0].after_data["hiworks_user_no"] == "HW-NEW"


@check
def 사람연결_사번_대안은_이름까지_같고_아직_연결_안된_사람만():
    켬 = dict(link_by_employee_no=True)
    # 이름이 다르면 연결하지 않는다
    assert resolve_sso_user(SsoToken("HW-NEW", "", "다른이름", "E024"), _명단(), **켬).outcome is SsoOutcome.DENIED
    # 이미 연결된 사람(22)을 사번·이름만 같다고 가로챌 수 없다
    가로채기 = resolve_sso_user(SsoToken("HW-EVIL", "", "가상팀원", "E022"), _명단(), **켬)
    assert 가로채기.outcome is SsoOutcome.DENIED and not 가로채기.link
    # 하이웍스에 사번이 비어 있으면 연결하지 않는다
    assert resolve_sso_user(SsoToken("HW-NEW", "", "가상미연결", ""), _명단(), **켬).outcome is SsoOutcome.DENIED
    # 비활성인 사람은 연결도 하지 않는다
    퇴사 = _명단({32: dict(hiworks_user_no=None)})
    r = resolve_sso_user(SsoToken("HW-NEW", "", "가상퇴사", "E032"), 퇴사, **켬)
    assert r.outcome is SsoOutcome.DENIED and not r.link and r.audit == ()


@check
def 하이웍스_응답값을_글자로_맞춘다():
    t = SsoToken.from_api({"user-no": 12345, "user-id": " id1 ", "user-name": "가상", "employee-no": None})
    assert t == SsoToken("12345", "id1", "가상", "")


@check
def 직책은_하나_HR_권한은_따로다():
    """9장 8: 직책 두 줄은 거부, HR 권한은 직책과 함께."""
    a = Actor.from_roles(41, [Role.HR_ADMIN, Role.TEAM_LEADER], led_team_id=91)
    assert (a.position, a.is_hr, a.led_team_id) == (Role.TEAM_LEADER, True, 91)
    assert Actor.from_roles(22, []) == Actor(22)
    assert "POSITION_DUPLICATED" in _오류코드(lambda: Actor.from_roles(5, [Role.TEAM_LEADER, Role.DIVISION_HEAD]))
    try:
        Actor(5, position=Role.HR_ADMIN)
    except ValueError:
        pass
    else:
        raise AssertionError("HR 권한이 직책 칸에 들어갔다")
    # 저장 모양: user_roles 줄
    hrl = replace(_명단()[6], is_hr=True)
    assert hrl.roles() == {Role.TEAM_LEADER, Role.HR_ADMIN}
    assert _명단()[3].roles() == frozenset()


_T = {
    20: Target(20, None, 1, Role.DIVISION_HEAD),
    21: Target(21, 11, 1, Role.TEAM_LEADER),
    22: Target(22, 11, 1),
    23: Target(23, 12, 1, Role.TEAM_LEADER),
    24: Target(24, 12, 1),
    32: Target(32, 13, 2),
    41: Target(41, 91, 9, Role.TEAM_LEADER, direct=True),  # HR 팀장 = 직속부서 팀장
    42: Target(42, 91, 9, direct=True),  # HR 담당자
    43: Target(43, 92, 9, Role.TEAM_LEADER, direct=True),
    44: Target(44, 92, 9, direct=True),
}
_대표 = Actor(1, position=Role.CEO)
_본부장 = Actor(20, position=Role.DIVISION_HEAD, led_division_id=1)
_팀장 = Actor(21, position=Role.TEAM_LEADER, led_team_id=11)
_HR팀장 = Actor(41, position=Role.TEAM_LEADER, is_hr=True, led_team_id=91)
_HR = Actor(42, is_hr=True)
_묶음 = {
    Pool.DIVISION: PoolRef(Pool.DIVISION, frozenset({22, 24}), division_id=1),
    Pool.LEADERS: PoolRef(Pool.LEADERS, frozenset({21, 23, 41, 43})),
    Pool.DIRECT: PoolRef(Pool.DIRECT, frozenset({42, 44})),
}
_맡김 = Delegation(ceo_user_id=1)


@check
def 묶음은_직책과_소속으로_정해진다():
    assert _T[20].pool is None  # 본부장은 평가받지 않는다
    assert _T[21].pool is Pool.LEADERS and _T[41].pool is Pool.LEADERS
    assert _T[22].pool is Pool.DIVISION
    assert _T[42].pool is Pool.DIRECT


@check
def HR팀장은_팀장_권한과_HR_권한을_모두_쓴다():
    """9장 8."""
    assert can(_HR팀장, Act.SCORE, _T[42]) and can(_HR팀장, Act.KPI_APPROVE, _T[42])
    assert can(_HR팀장, Act.MANAGE) and can(_HR팀장, Act.FINALIZE)
    # HR 권한만으로는 평가하지 않는다 (맡겨졌을 때 대표이사 몫만)
    assert not can(_HR, Act.SCORE, _T[44])
    assert can(_HR, Act.MANAGE) and not can(_팀장, Act.MANAGE)


@check
def 팀장_본부장은_자기가_장인_부서까지다():
    assert can(_팀장, Act.SCORE, _T[22])
    assert not can(_팀장, Act.SCORE, _T[24])  # 다른 팀
    assert not can(_팀장, Act.SCORE, _T[21])  # 자기 자신
    assert can(_본부장, Act.SCORE, _T[21]) and not can(_본부장, Act.SCORE, _T[22])  # 본부장은 팀장을 평가
    assert can(_본부장, Act.KPI_APPROVE, _T[22]) and can(_본부장, Act.KPI_APPROVE, _T[21])
    assert not can(_본부장, Act.KPI_APPROVE, _T[32])  # 다른 본부
    assert not can(Actor(22), Act.KPI_APPROVE, _T[24])  # 직책 없음
    # 연봉계약서: 팀장은 팀원 것을 못 보고 본부장은 자기 본부를 본다 (8월 설계 §4)
    assert not can(_팀장, Act.VIEW_CONTRACT, _T[22])
    assert can(_본부장, Act.VIEW_CONTRACT, _T[22]) and can(Actor(22), Act.VIEW_CONTRACT, _T[22])
    assert can(_본부장, Act.ASSIGN, _묶음[Pool.DIVISION])
    assert not can(Actor(30, position=Role.DIVISION_HEAD, led_division_id=2), Act.ASSIGN, _묶음[Pool.DIVISION])
    assert not can(_본부장, Act.CONFIRM, _묶음[Pool.DIVISION])


@check
def 포털_사용이_꺼진_사람은_세션을_받지_못하고_준비중이다():
    r = resolve_sso_user(SsoToken("HW-44"), _명단())
    assert r.outcome is SsoOutcome.NOT_READY and r.user_id == 44 and "준비 중" in r.message


@check
def 구성원_설정_규칙을_모아서_알려준다():
    validate_members(_명단())  # 기본 명단은 통과
    두팀장 = _명단({22: dict(position=Role.TEAM_LEADER)})
    assert "POSITION_TAKEN" in _오류코드(lambda: validate_members(두팀장))
    # 비활성인 전 팀장은 세지 않는다
    validate_members(_명단({22: dict(position=Role.TEAM_LEADER), 21: dict(is_active=False, position=None)}))
    평가받는본부장 = _명단({20: dict(in_evaluation=True)})
    assert "NOT_EVALUATED_POSITION" in _오류코드(lambda: validate_members(평가받는본부장))
    assert "CEO_NO_KPI" in _오류코드(lambda: validate_members(_명단({1: dict(writes_kpi=True)})))
    # 평가가 시작된 사람의 KPI 작성은 못 바꾼다
    바꿈 = _명단({22: dict(writes_kpi=False)})
    assert "KPI_LOCKED" in _오류코드(lambda: validate_members(바꿈, before=_명단(), kpi_locked={22}))
    validate_members(바꿈, before=_명단(), kpi_locked={21})
    # 문제가 여럿이면 전부 모은다
    여럿 = _명단({22: dict(position=Role.TEAM_LEADER), 20: dict(has_contract=True)})
    assert len(_오류코드(lambda: validate_members(여럿))) >= 2


@check
def 감사로그에_직책과_HR_권한이_두_칸으로_남는다():
    e = AuditEntry.by(_HR팀장, action="X", entity_type="t", entity_id=1)
    assert (e.actor_user_id, e.actor_position, e.actor_is_hr, e.on_behalf_of) == (41, Role.TEAM_LEADER, True, None)
    uow = _가짜UoW(contract=_가짜계약서(id=1, user_id=10, status=ContractStatus.SIGNED, is_locked=True))
    cancel_salary_contract(actor=_HR, contract_id=1, reason="가상 사유", uow=uow)
    assert (uow.audit_entries[0].actor_position, uow.audit_entries[0].actor_is_hr) == (None, True)
    # 도메인 값과 DDL 이 같은 글자를 말하는지
    ddl = "".join(
        f.read_text(encoding="utf-8") for f in sorted((pathlib.Path(__file__).with_name("sql")).glob("0*.sql"))
    )
    roles = ",".join(f"'{r.value}'" for r in Role)
    positions = ",".join(f"'{r.value}'" for r in (Role.TEAM_LEADER, Role.DIVISION_HEAD, Role.CEO))
    assert f"CHECK (role IN ({roles}))" in ddl
    assert f"OR actor_position IN ({positions})" in ddl
    assert "actor_is_hr" in ddl and "on_behalf_of" in ddl and "actor_role" not in ddl


@check
def 첫HR은_HR이_0명일때_하이웍스_번호로만_지정된다():
    """9장 13."""
    무HR = _명단({42: dict(is_hr=False)})
    r = resolve_sso_user(SsoToken("HW-44"), 무HR, first_hr_user_no="HW-44")
    assert (r.outcome, r.user_id, r.bootstrap_hr) == (SsoOutcome.OK, 44, True)  # 포털 사용이 꺼져 있어도 켜고 들어온다
    assert [e.action for e in r.audit] == ["HR_BOOTSTRAP"] and r.audit[0].actor_user_id is None
    for 설정 in ("", None, "HW-22", "E044"):  # 비었거나 · 다른 번호 · 사번으로는 안 된다
        r = resolve_sso_user(SsoToken("HW-44", "id44", "가상준비중", "E044"), 무HR, first_hr_user_no=설정)
        assert r.outcome is SsoOutcome.NOT_READY and not r.bootstrap_hr, 설정


@check
def 첫HR_HR이_한명이라도_있으면_설정값을_보지_않는다():
    r = resolve_sso_user(SsoToken("HW-44"), _명단(), first_hr_user_no="HW-44")
    assert r.outcome is SsoOutcome.NOT_READY and not r.bootstrap_hr and r.audit == ()
    # 포털을 끈 HR · 비활성 HR 은 HR 로 세지 않는다
    for 바꿈 in ({42: dict(portal_enabled=False)}, {42: dict(is_active=False)}):
        assert resolve_sso_user(SsoToken("HW-44"), _명단(바꿈), first_hr_user_no="HW-44").bootstrap_hr


@check
def 마지막_HR은_빼거나_끄거나_비활성화할_수_없다():
    for 바꿈 in (dict(is_hr=False), dict(portal_enabled=False), dict(is_active=False)):
        assert "LAST_HR" in _오류코드(lambda: validate_members(_명단({42: 바꿈}))), 바꿈
    # 다른 HR 을 먼저 지정하면 뺄 수 있다
    validate_members(_명단({41: dict(is_hr=True), 42: dict(is_hr=False)}))


@check
def 대표이사의_일은_6장_표의_범위다():
    """9장 14."""
    c = _대표
    assert can(c, Act.KPI_APPROVE, _T[20]) and can(c, Act.KPI_APPROVE, _T[41])  # 본부장 · 직속부서 팀장
    assert not can(c, Act.KPI_APPROVE, _T[21]) and not can(c, Act.KPI_APPROVE, _T[22])
    assert can(c, Act.SCORE, _T[41]) and not can(c, Act.SCORE, _T[21]) and not can(c, Act.SCORE, _T[42])
    assert can(c, Act.ASSIGN, _묶음[Pool.DIRECT]) and not can(c, Act.ASSIGN, _묶음[Pool.DIVISION])
    assert all(can(c, Act.CONFIRM, p) for p in _묶음.values())
    assert can(c, Act.CONFIRM_EDIT, _묶음[Pool.LEADERS]) and not can(c, Act.CONFIRM_EDIT, _묶음[Pool.DIVISION])
    assert can(c, Act.SEND_BACK, _묶음[Pool.DIVISION]) and not can(c, Act.SEND_BACK, _묶음[Pool.LEADERS])
    assert not any(can(c, a) for a in (Act.FINALIZE, Act.MANAGE, Act.APPEAL_ANSWER))
    assert can(c, Act.APPEAL_CONFIRM, _T[22]) and not can(c, Act.APPEAL_CONFIRM, _T[20])
    assert can(c, Act.VIEW_KPI, _T[21]) and not can(c, Act.VIEW_KPI, _T[22])  # 팀원은 인원·평균만
    assert can(c, Act.VIEW_RESULT, _T[22])


@check
def 맡기지_않으면_HR은_대표이사의_일을_못_한다():
    for act, t in ((Act.KPI_APPROVE, _T[20]), (Act.SCORE, _T[43]), (Act.CONFIRM, _묶음[Pool.DIVISION]),
                   (Act.CONFIRM_EDIT, _묶음[Pool.LEADERS]), (Act.APPEAL_CONFIRM, _T[22])):
        assert not can(_HR, act, t), act
        assert can(_HR, act, t, delegation=_맡김), act


@check
def 맡김은_대표이사만_켠다():
    assert can(_대표, Act.DELEGATE)
    assert not can(_HR, Act.DELEGATE) and not can(_HR, Act.DELEGATE, delegation=_맡김)


@check
def 맡기면_HR이_대신하고_넣은_사람과_대표이사가_함께_남는다():
    허락 = can(_HR, Act.KPI_APPROVE, _T[20], delegation=_맡김)
    assert 허락 and 허락.on_behalf_of == 1
    e = AuditEntry.by(_HR, on_behalf_of=허락.on_behalf_of, action="KPI_APPROVED", entity_type="kpi_sheet", entity_id=7)
    assert (e.actor_user_id, e.actor_is_hr, e.on_behalf_of) == (42, True, 1)
    # 대표이사가 직접 하면 대신 표시가 없다
    assert can(_대표, Act.KPI_APPROVE, _T[20]).on_behalf_of is None


@check
def HR은_본인의_것을_대신할_수_없다():
    assert not can(_HR팀장, Act.SCORE, _T[41], delegation=_맡김)  # HR 팀장 본인의 평가
    assert not can(_HR팀장, Act.KPI_APPROVE, _T[41], delegation=_맡김)  # 본인 KPI 승인
    assert can(_HR, Act.SCORE, _T[41], delegation=_맡김)  # 다른 HR 은 된다
    assert not can(_HR, Act.ASSIGN, _묶음[Pool.DIRECT], delegation=_맡김)  # HR 담당자 본인의 등급
    assert can(_HR팀장, Act.ASSIGN, _묶음[Pool.DIRECT], delegation=_맡김)
    assert not can(_HR팀장, Act.CONFIRM, _묶음[Pool.LEADERS], delegation=_맡김)  # 본인이 든 묶음
    assert can(_HR, Act.CONFIRM, _묶음[Pool.LEADERS], delegation=_맡김)


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
