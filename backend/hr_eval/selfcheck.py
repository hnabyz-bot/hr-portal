"""hr_eval 도메인 로직 자체 검증.

    cd backend && python -m hr_eval.selfcheck

테스트 프레임워크를 쓰지 않는다. 검사 하나가 실패해도 나머지는 계속 돌고,
마지막에 몇 개가 깨졌는지 알려준다.
"""

from __future__ import annotations

import copy
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
    DEFAULT_RATIOS,
    EXTRA_MAX,
    RATIO_SUM_MAX,
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
from hr_eval.domain.kpi_plan import ACTIONS, assign_ids, clean_plan, next_status, validate_plan, window_open, window_span
from hr_eval.domain.kpi_progress import (
    GRACE_DAY, LATE_DAYS, LINK_MAX, NOTE_MAX, WHY_MAX,
    effective_dues, item_expected, item_pct, month_open, month_state, overall, records_open, sched_state, task_title, valid_link,
)
from hr_eval.domain.kpi_review import (
    BIZ_DETAIL_MAX, BIZ_GOAL_MAX, BIZ_MAX, GAP_DEFAULT, KST, MEMO_MAX, MUTE_TEXT_MAX, MUTE_WHY,
    alarm, check_state, clean_biz_plan, is_muted, js_round, month_summary, mute_until, same_kst_day,
)
from hr_eval.domain.eval_rules import (
    APPEAL_DAYS, BANDS, BONUS_MAX_X2, FILE_MAX, FILES_PER_ITEM, GRADES, LEAVE_LIMIT_X2, ORDER, PCT, TEXT_MAX, TOTAL_MAX,
    Member as EvMember, above, after_score, assign_stage, at, band, cap, check_pool, edited, level, pool_key, pool_ref, pool_state, ready_stage, total,
)
from hr_eval.domain.access import Act, Delegation, Pool, PoolRef, Target, can
from hr_eval.domain.roster import (
    PortalDept,
    PortalUser,
    RosterError,
    parse_orgs,
    parse_users,
    plan_sync,
)
from hr_eval.domain.org import Dept, Person, is_direct_team, manager_of, target_of
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
def 비율을_바꾸면_정원이_바뀐다():
    q = calculate_department_quota(10, (20, 10, 0))
    assert (q.a, q.b, q.d, q.c, q.cap_s) == (2, 1, 0, 7, 2), q
    q = calculate_department_quota(20, (25, 15, 10))
    assert (q.a, q.b, q.d, q.c, q.cap_s) == (5, 3, 2, 10, 5), q


@check
def 비율_기본값은_지금_규칙과_같다():
    assert (DEFAULT_RATIOS, EXTRA_MAX, RATIO_SUM_MAX) == ((10, 10, 10), 5, 70)
    for n in range(0, 61):
        assert calculate_department_quota(n) == calculate_department_quota(n, (10, 10, 10), (0, 0)), n
    q = calculate_department_quota(10)
    assert (q.a, q.b, q.c, q.d, q.cap_s) == (1, 1, 7, 1, 1), q  # 지금 규칙: 10명 A1 B1 C7 D1


@check
def 비율_합_70이면_5명_이상_어디서나_정원_합이_인원을_넘지_않는다():
    for ratios in ((70, 0, 0), (35, 35, 0), (30, 30, 10), (25, 25, 20), (0, 0, 70)):
        for n in range(5, 201):
            q = calculate_department_quota(n, ratios)
            assert q.c >= 0 and q.total() == n, (ratios, n, q)


@check
def 늘린_정원은_SA와_B에_더해지고_S상한도_오른다():
    q = calculate_department_quota(10, extra=(1, 0))
    assert (q.a, q.cap_s, q.c) == (2, 2, 6), q
    q = calculate_department_quota(10, extra=(0, 2))
    assert (q.b, q.cap_s) == (3, 1), q
    q = calculate_department_quota(4, extra=(1, 0))  # 묶음(4명 이하)은 비율을 안 쓰고 위쪽 칸이 늘어난다
    assert (q.upper, q.cap_s, q.lower) == (2, 2, 2), q
    q = calculate_department_quota(1, extra=(1, 0))  # 1명 묶음도 늘려 주면 위쪽 1명
    assert (q.upper, q.lower) == (1, 0), q


@check
def 늘린_정원이_인원을_넘으면_ValueError():
    for n, extra in ((5, (5, 5)), (3, (2, 1))):
        try:
            calculate_department_quota(n, extra=extra)
        except ValueError as e:
            assert str(e) == "늘린 정원이 묶음 인원보다 많습니다.", e
        else:
            raise AssertionError(f"{n}명에 {extra}인데 ValueError가 나오지 않았다")


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
    assert can(_본부장, Act.ASSIGN, _묶음[Pool.DIVISION])
    assert not can(Actor(30, position=Role.DIVISION_HEAD, led_division_id=2), Act.ASSIGN, _묶음[Pool.DIVISION])
    assert not can(_본부장, Act.CONFIRM, _묶음[Pool.DIVISION])


@check
def 연봉계약서는_본인과_HR만_본다():
    """인사담당자 2026-10-15: HR 담당자·HR 팀장만. 팀장·본부장·대표이사는 못 본다."""
    for 못봄 in (_팀장, _본부장, _대표):
        assert not can(못봄, Act.VIEW_CONTRACT, _T[22]), 못봄
        assert not can(못봄, Act.VIEW_CONTRACT, _T[22], delegation=_맡김), 못봄
    assert can(_HR, Act.VIEW_CONTRACT, _T[22]) and can(_HR팀장, Act.VIEW_CONTRACT, _T[22])
    assert can(Actor(22), Act.VIEW_CONTRACT, _T[22])  # 본인 것은 본다 (서명해야 한다)
    assert not can(Actor(24), Act.VIEW_CONTRACT, _T[22])


@check
def 계약_현황은_HR과_자기_본부의_본부장만_본다():
    """금액 없이 이름·상태·서명한 날 (인사담당자 2026-10-15)."""
    현황 = Act.VIEW_CONTRACT_STATUS
    assert can(_본부장, 현황, _T[22]) and can(_본부장, 현황, _T[21])  # 자기 본부 팀원·팀장
    assert not can(_본부장, 현황, _T[32])  # 다른 본부
    assert not can(_팀장, 현황, _T[22]) and not can(_대표, 현황, _T[22])
    assert not can(_대표, 현황, _T[22], delegation=_맡김) and not can(Actor(24), 현황, _T[22])
    assert can(_HR, 현황, _T[22]) and can(_HR팀장, 현황, _T[32])
    assert not can(_본부장, Act.VIEW_CONTRACT, _T[22])  # 현황을 봐도 계약서(금액·본문)는 못 본다


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


def _맡은부서_명단(*사람: Member) -> list[Member]:
    """_가상조직() 의 부서(본부 10 · 팀 11·12 · 직속 팀 20 · 임원 30)에 놓는 명단. HR 한 명이 늘 있다."""
    return [Member(42, "가상HR", 20, portal_enabled=True, is_hr=True), *사람]


def _맡은본부장(id, 소속, 맡은=None):
    return Member(id, f"가상본부장{id}", 소속, portal_enabled=True, position=Role.DIVISION_HEAD,
                  in_evaluation=False, has_contract=False, led_department_id=맡은)


def _맡은팀장(id, 소속, 맡은=None):
    return Member(id, f"가상팀장{id}", 소속, portal_enabled=True, position=Role.TEAM_LEADER, led_department_id=맡은)


@check
def 맡은_부서가_본부가_아니면_본부장이_될_수_없다():
    _, 부서 = _가상조직()
    코드 = lambda 명단, **kw: _오류코드(lambda: validate_members(명단, depts=부서, **kw))  # noqa: E731
    # 하이웍스에서 '임원' 부서에 있어도 본부를 맡으면 본부장이다
    validate_members(_맡은부서_명단(_맡은본부장(2, 30, 맡은=10)), depts=부서)
    for 잘못 in (11, 20, 30):  # 팀 · 직속 팀 · 임원
        assert "LED_DEPT_INVALID" in 코드(_맡은부서_명단(_맡은본부장(2, 30, 맡은=잘못))), 잘못
    assert "LED_DEPT_INVALID" in 코드(_맡은부서_명단(_맡은본부장(2, 30)))  # 맡은 부서를 안 고르면 소속(임원)이 맡은 부서
    assert "LED_DEPT_INVALID" in 코드(_맡은부서_명단(_맡은본부장(2, 10, 맡은=999)))  # 없는 부서
    # 팀장은 팀이나 직속 팀
    validate_members(_맡은부서_명단(_맡은팀장(3, 30, 맡은=11), _맡은팀장(4, 30, 맡은=20)), depts=부서)
    for 잘못 in (10, 30):
        assert "LED_DEPT_INVALID" in 코드(_맡은부서_명단(_맡은팀장(3, 11, 맡은=잘못))), 잘못
    # 이미 저장된 사람은 바뀐 게 없으면 다시 묻지 않는다 (옛 자료 때문에 남의 저장이 막히면 안 된다)
    옛 = _맡은부서_명단(_맡은본부장(2, 30))
    validate_members(옛, before=옛, depts=부서)
    assert "LED_DEPT_INVALID" in 코드(_맡은부서_명단(_맡은본부장(2, 30, 맡은=11)), before=옛)
    # depts 를 안 주면 이 검사는 하지 않는다 (옛 호출)
    validate_members(_맡은부서_명단(_맡은본부장(2, 30, 맡은=11)))


@check
def 한_본부의_본부장은_맡은_부서_기준으로_한_명():
    _, 부서 = _가상조직()
    # 소속은 다르고 맡은 부서가 둘 다 10
    assert "POSITION_TAKEN" in _오류코드(
        lambda: validate_members(_맡은부서_명단(_맡은본부장(2, 30, 맡은=10), _맡은본부장(3, 10)), depts=부서))
    # 소속이 같아도 맡은 부서가 다르면 한 명씩이다
    validate_members(_맡은부서_명단(_맡은팀장(3, 20, 맡은=11), _맡은팀장(4, 20, 맡은=12)), depts=부서)
    # 다른 팀에서 맡은 부서를 옮겨 와 같은 팀에 둘이 되면 막는다
    assert "POSITION_TAKEN" in _오류코드(
        lambda: validate_members(_맡은부서_명단(_맡은팀장(3, 11), _맡은팀장(4, 12, 맡은=11)), depts=부서))
    # 비활성인 사람은 세지 않는다
    validate_members(_맡은부서_명단(_맡은본부장(2, 30, 맡은=10), replace(_맡은본부장(3, 10), is_active=False)), depts=부서)


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


# ------------------------------------------------------------------
# 직원 명단 동기화 비교 규칙 (로그인 설계 §11, 9장 9번). 가짜 하이웍스 응답이다.
# 실제 저장까지 도는 검사는 python -m hr_eval.roster_sync --check
# ------------------------------------------------------------------


def _hw사람(no, node, email=None, phone="", cell="", mail_id=None):
    return {"user_no": no, "mail_id": mail_id if mail_id is not None else no.lower(), "name": f"가상{no}",
            "email": f"{no.lower()}@example.invalid" if email is None else email,
            "phone": phone, "cell": cell, "node_id": node}


def _hw조직(root=(), **본부):
    """본부=(이름, [사람], {팀번호: (이름, [사람])}) → 하이웍스 조직도 모양."""
    return {"node_id": "C", "name": "가상회사", "entries": list(root), "nodes": [
        {"node_id": nid, "name": name, "entries": list(people),
         "nodes": [{"node_id": tid, "name": tname, "entries": list(tp), "nodes": []} for tid, (tname, tp) in teams.items()]}
        for nid, (name, people, teams) in 본부.items()]}


def _동기화(users_body, org_body, depts=(), users=(), **kw):
    nos = parse_users(users_body)
    nodes, people, at_root = parse_orgs(org_body)
    return plan_sync(nos, nodes, people, list(depts), list(users), at_root=at_root, **kw)


_기본조직 = _hw조직(
    root=[_hw사람("R1", "C")],
    B1=("가상본부", [_hw사람("H1", "B1")], {"T1": ("가상팀", [_hw사람("U1", "T1", phone="02-1"), _hw사람("U2", "T1")])}),
)
_기본명단 = [{"no": n, "user_id": n.lower(), "name": n} for n in ("R1", "H1", "U1", "U2", "N1")]  # N1 = 부서 없음


@check
def 명단동기화_처음에는_부서와_직원을_추가한다():
    plan = _동기화(_기본명단, _기본조직)
    assert [(n.node_id, n.depth, n.parent_id) for n in plan.dept_add] == [("B1", 1, None), ("T1", 2, "B1")]
    assert sorted(p.user_no for p in plan.user_add) == ["H1", "U1", "U2"]
    # 회사 바로 아래 · 부서 없음은 올리지 않고 숫자로만 알린다
    assert plan.notes["회사 바로 아래(올리지 않음)"] == 1 and plan.notes["부서 없음(올리지 않음)"] == 1
    # 새 사람에게 넘기는 것은 이름·메일·전화·소속뿐 — 포털 사용·직책 같은 사람별 설정 칸이 없다
    assert set(vars(plan.user_add[0])) == {"user_no", "name", "email", "phone", "mobile", "node_id", "mail_id"}
    assert "가상" not in str(plan.counts())  # 요약에는 이름이 없다


@check
def 명단동기화_조회가_실패하거나_비면_멈춘다():
    """9장 9: 아무도 비활성화되지 않는다 — 비교까지 가지 않는다."""
    for body in ({"code": "ERR", "message": "content-type 오류입니다."}, [], {"items": []}, None, [{"name": "번호 없음"}]):
        try:
            parse_users(body)
        except RosterError:
            pass
        else:
            raise AssertionError(f"이상한 사용자 목록인데 멈추지 않았다: {body!r}")
    for body in ({"code": "ERR"}, {"node_id": "C", "name": "x"}, _hw조직(), _hw조직(B1=("b", [], {}))):  # 모양 · 빈 조직 · 소속 0명
        try:
            parse_orgs(body)
        except RosterError:
            pass
        else:
            raise AssertionError(f"이상한 조직도인데 멈추지 않았다: {body!r}")
    # 성공 표시를 감싼 모양({"code": "SUC", "data": …})은 받는다
    assert parse_users({"code": "SUC", "data": _기본명단}) == frozenset({"R1", "H1", "U1", "U2", "N1"})


@check
def 명단동기화_3단계_조직이나_같은_조직_번호는_멈춘다():
    깊음 = _hw조직(B1=("b", [], {"T1": ("t", [_hw사람("U1", "T1")])}))
    깊음["nodes"][0]["nodes"][0]["nodes"] = [{"node_id": "X", "name": "x", "entries": [], "nodes": []}]
    겹침 = _hw조직(B1=("b", [], {"B1": ("t", [_hw사람("U1", "B1")])}))
    for body in (깊음, 겹침):
        try:
            parse_orgs(body)
        except RosterError:
            pass
        else:
            raise AssertionError("멈추지 않았다")


_포털부서 = [PortalDept(1, "B1", "가상본부", None), PortalDept(2, "T1", "가상팀", "B1")]


def _포털직원(**바꿈):
    base = {
        "H1": PortalUser(11, "H1", True, "B1", "h1@example.invalid"),
        "U1": PortalUser(12, "U1", True, "T1", "u1@example.invalid", "02-1"),
        "U2": PortalUser(13, "U2", True, "T1", "u2@example.invalid"),
        "X9": PortalUser(14, "X9", True, "T1", "x9@example.invalid"),  # 하이웍스에서 사라진 사람
        "M1": PortalUser(15, None, True, "T1", "m1@example.invalid"),  # 하이웍스와 연결 안 된 사람
    }
    for k, v in 바꿈.items():
        base[k] = replace(base[k], **v)
    return list(base.values())


@check
def 명단동기화_사라진_사람만_비활성이고_연결_안된_사람은_그대로다():
    plan = _동기화(_기본명단, _기본조직, _포털부서, _포털직원())
    assert [u.user_no for u in plan.user_deactivate] == ["X9"]
    assert plan.user_add == [] and plan.user_update == [] and plan.dept_add == []


@check
def 명단동기화_비활성인_사람은_다시_켜지도_고치지도_않는다():
    """9장 9: 다시 켜는 것은 HR 만."""
    조직 = _hw조직(B1=("가상본부", [_hw사람("H1", "B1")], {"T1": ("가상팀", [_hw사람("U1", "T1", phone="02-9"), _hw사람("U2", "T1")])}))
    plan = _동기화(_기본명단, 조직, _포털부서, _포털직원(U1=dict(is_active=False), X9=dict(is_active=False)))
    assert all(u.user_no != "U1" for u, _, _ in plan.user_update)  # 전화가 바뀌었어도 손대지 않는다
    assert all(p.user_no != "U1" for p in plan.user_add) and plan.user_deactivate == []


@check
def 명단동기화_소속과_연락처만_고친다():
    조직 = _hw조직(B1=("가상본부", [_hw사람("H1", "B1"), _hw사람("U2", "B1")],
                     {"T1": ("가상팀", [_hw사람("U1", "T1", phone="", cell="휴대-1", email="new@example.invalid")])}))
    plan = _동기화(_기본명단, 조직, _포털부서, _포털직원())
    바뀜 = {u.user_no: changed for u, _, changed in plan.user_update}
    assert 바뀜 == {"U1": ("메일", "전화", "휴대폰"), "U2": ("소속",)}, 바뀜
    # 메일이 비어 오면 지금 메일을 지우지 않는다
    조직2 = _hw조직(B1=("가상본부", [_hw사람("H1", "B1", email="")], {"T1": ("가상팀", [_hw사람("U1", "T1", phone="02-1"), _hw사람("U2", "T1")])}))
    assert _동기화(_기본명단, 조직2, _포털부서, _포털직원()).user_update == []


@check
def 명단동기화_메일이_없으면_하이웍스_아이디와_도메인으로_만든다():
    조직 = _hw조직(B1=("b", [_hw사람("H1", "B1", email="", mail_id="h1")], {}))
    assert _동기화(_기본명단, 조직, domain="example.invalid").user_add[0].email == "h1@example.invalid"
    plan = _동기화(_기본명단, 조직)  # 도메인도 없으면 올리지 않는다 (users.email 은 비울 수 없다)
    assert plan.user_add == [] and plan.notes["메일 없음(올리지 않음)"] == 1


@check
def 명단동기화_부서는_이름과_위치를_따르고_사라지면_닫는다():
    조직 = _hw조직(B1=("새이름본부", [_hw사람("H1", "B1"), _hw사람("U1", "B1"), _hw사람("U2", "B1")], {}))
    plan = _동기화(_기본명단, 조직, _포털부서, _포털직원())
    assert [(d.id, n.name) for d, n in plan.dept_update] == [(1, "새이름본부")]
    assert [d.id for d in plan.dept_close] == [2]  # 지우지 않고 닫는다
    # 닫힌 부서가 다시 나오면 연다
    닫힘 = [_포털부서[0], replace(_포털부서[1], is_active=False)]
    assert [d.id for d, _ in _동기화(_기본명단, _기본조직, 닫힘, _포털직원()).dept_update] == [2]


@check
def 명단동기화_한꺼번에_많이_사라지면_멈춘다():
    from hr_eval.domain.roster import MASS_DEACTIVATE_LIMIT

    많이 = [PortalUser(100 + i, f"Z{i}", True, "T1", f"z{i}@example.invalid") for i in range(MASS_DEACTIVATE_LIMIT + 1)]
    try:
        _동기화(_기본명단, _기본조직, _포털부서, 많이)
    except RosterError as err:
        assert "allow-mass" in str(err)
    else:
        raise AssertionError("한꺼번에 많이 사라졌는데 멈추지 않았다")
    assert len(_동기화(_기본명단, _기본조직, _포털부서, 많이, allow_mass=True).user_deactivate) == MASS_DEACTIVATE_LIMIT + 1
    # 상한까지는 그대로 진행한다
    assert len(_동기화(_기본명단, _기본조직, _포털부서, 많이[:-1]).user_deactivate) == MASS_DEACTIVATE_LIMIT


@check
def 명단동기화_여러_부서에_있으면_먼저_나온_부서다():
    조직 = _hw조직(B1=("b", [_hw사람("H1", "B1")], {"T1": ("t", [_hw사람("H1", "T1"), _hw사람("U1", "T1")])}))
    plan = _동기화(_기본명단, 조직)
    assert [p.node_id for p in plan.user_add if p.user_no == "H1"] == ["B1"]
    assert plan.notes["여러 부서(먼저 나온 부서로)"] == 1


def _가상조직():
    """본부 B(10: 팀 11·12) · 직속 팀 S(20) · 임원(30). 사람 1~9 는 설계 4장 표를 한 번씩 건드린다."""
    부서 = [
        Dept(10, "가상본부", None),
        Dept(11, "가상1팀", 10),
        Dept(12, "가상2팀", 10),
        Dept(20, "지원팀", None),
        Dept(30, "임원", None),
    ]
    TL, DH, CEO = Role.TEAM_LEADER, Role.DIVISION_HEAD, Role.CEO
    사람 = [
        Person(1, 30, CEO),
        Person(2, 30, DH, led_department_id=10),
        Person(3, 11, TL, led_department_id=11),
        Person(4, 11),
        Person(5, 12),
        Person(6, 10),
        Person(7, 20, TL, led_department_id=20),
        Person(8, 20),
        Person(9, 30),
    ]
    return 사람, 부서



@check
def 맡은_부서로_본부장을_찾는다():
    p, d = _가상조직()
    assert manager_of(3, p, d) == 2 and manager_of(6, p, d) == 2 and manager_of(5, p, d) == 2



@check
def 팀원의_관리자는_팀장이다():
    p, d = _가상조직()
    assert manager_of(4, p, d) == 3 and manager_of(8, p, d) == 7



@check
def 직속팀_팀장과_본부장과_임원의_관리자는_대표이사다():
    p, d = _가상조직()
    assert manager_of(7, p, d) == 1 and manager_of(2, p, d) == 1 and manager_of(9, p, d) == 1



@check
def 대표이사의_관리자는_없다():
    p, d = _가상조직()
    assert manager_of(1, p, d) is None



@check
def 대표이사가_없으면_관리자가_없다():
    p, d = _가상조직()
    p = [x for x in p if x.id != 1]
    assert manager_of(2, p, d) is None and manager_of(9, p, d) is None



@check
def 비활성_팀장은_건너뛰고_본부장이다():
    p, d = _가상조직()
    p = [replace(x, is_active=False) if x.id == 3 else x for x in p]
    assert manager_of(4, p, d) == 2



@check
def 직속팀_판별():
    _, d = _가상조직()
    by = {x.id: x for x in d}
    assert is_direct_team(by[20], d) and not is_direct_team(by[10], d) and not is_direct_team(by[30], d) and not is_direct_team(by[11], d)


def _행(이름, 가중치, 종류, **바꿈):
    """KPI 계획서의 실행목표 한 줄 (가상 글). 바꿈으로 칸을 덮어쓴다."""
    r = {
        "action": 이름, "formula": "가상 측정식", "weight": 가중치, "owner": "가상1",
        "S": "가상 s", "A": "가상 a", "B": "가상 b", "C": "가상 c", "D": "가상 d",
        "kind": 종류, "target": None, "unit": "", "scheds": [],
    }
    r.update(바꿈)
    return r


def _좋은계획():
    """정기 2 (묶음 1) · 비정기 1 (묶음 2), 가중치 40/30/30, S~D 모두 채움 — 2027년 계획서."""
    return [
        {"team": "가상 팀목표 1", "personal": "가상 개인목표 1", "items": [
            _행("가상 정기 업무 1", 40, "reg", target=12, unit="건"),
            _행("가상 정기 업무 2", 30, "reg"),
        ]},
        {"team": "가상 팀목표 2", "personal": "가상 개인목표 2", "items": [
            _행("가상 비정기 업무", 30, "irr", scheds=[
                {"due": "2027-03-31", "name": "가상 초안"},
                {"due": "2027-12-31", "name": "가상 마감"},
            ]),
        ]},
    ]


def _행들(goals):
    """계획서 전체의 실행목표 줄 (묶음 순서대로)."""
    return [it for g in goals for it in g["items"]]


def _행빼기(goals, k):
    """k번째(0부터) 실행목표 줄을 뺀 사본."""
    g = copy.deepcopy(goals)
    for 묶음 in g:
        if k < len(묶음["items"]):
            del 묶음["items"][k]
            return g
        k -= len(묶음["items"])
    raise IndexError(k)


def _가중치(goals, 값들):
    g = copy.deepcopy(goals)
    for it, w in zip(_행들(g), 값들):
        it["weight"] = w
    return g


def _바꿈(goals, k, **바꿈):
    """k번째(0부터) 실행목표 줄의 칸을 바꾼 사본."""
    g = copy.deepcopy(goals)
    _행들(g)[k].update(바꿈)
    return g


def _codes(goals, year=2027):
    return {i.code for i in validate_plan(goals, year)}


def _raises(exc, fn, *args):
    try:
        fn(*args)
    except exc:
        return
    raise AssertionError(f"{exc.__name__} 이(가) 나야 하는데 나지 않았다: {args}")


@check
def 좋은_계획서는_문제가_없다():
    assert validate_plan(_좋은계획(), 2027) == []


@check
def 실행목표는_세_개부터():
    assert "ITEMS_MIN" in _codes(_행빼기(_좋은계획(), 1))  # 묶음이 둘이어도 계획서 전체로 센다
    assert "ITEMS_MIN" in _codes([])
    assert "ITEMS_MIN" not in _codes(_좋은계획())


@check
def 가중치_소수_합은_정확히_100():
    assert validate_plan(_가중치(_좋은계획(), [33.33, 33.33, 33.34]), 2027) == []
    assert "WEIGHT_SUM" in _codes(_가중치(_좋은계획(), [33.33, 33.33, 33.33]))
    assert "WEIGHT_SUM" in _codes(_가중치(_좋은계획(), [40, 30, 31]))
    assert validate_plan(_가중치(_좋은계획(), [64.9, 2.9, 32.2]), 2027) == []  # float 로 더하면 100.00000000000001 이 되는 값도 십진수로 더한다


@check
def 가중치는_모두_0보다_커야_한다():
    assert "WEIGHT_POSITIVE" in _codes(_가중치(_좋은계획(), [70, 30, 0]))
    codes = _codes(_가중치(_좋은계획(), [110, 30, -40]))  # 합은 100 이어도 음수는 안 된다
    assert "WEIGHT_POSITIVE" in codes and "WEIGHT_SUM" not in codes


@check
def 달성기준은_모두_필요():
    assert "GRADE_REQUIRED" in _codes(_바꿈(_좋은계획(), 1, C=""))
    assert "GRADE_REQUIRED" in _codes(_바꿈(_좋은계획(), 0, S="  "))  # 공백만 있는 것도 빈 것


@check
def 빈_목표와_측정식은_TEXT_REQUIRED():
    좋은 = _좋은계획()
    for 칸 in ("action", "formula"):
        for 빈 in ("", "   "):
            assert _codes(_바꿈(좋은, 0, **{칸: 빈})) == {"TEXT_REQUIRED"}, (칸, 빈)
    assert validate_plan(_바꿈(좋은, 0, formula=" "), 2027)[0].target == "item:0.0"
    for 칸 in ("team", "personal"):
        g = _좋은계획()
        g[1][칸] = " "
        assert _codes(g) == {"TEXT_REQUIRED"}, 칸
    # 담당자 · 단위 · 일정 이름은 비워도 된다 (S~D 는 GRADE_REQUIRED 가 따로 본다)
    assert _codes(_바꿈(좋은, 0, owner="", unit="")) == set()
    assert _codes(_바꿈(좋은, 2, scheds=[{"due": "2027-05-05", "name": " "}])) == set()
    assert validate_plan(좋은, 2027) == []


@check
def 비정기는_그해_안의_일정이_하나_이상():
    좋은 = _좋은계획()
    코드 = lambda scheds: _codes(_바꿈(좋은, 2, scheds=scheds))
    assert "SCHED_MIN" in 코드([])
    for due in ("2028-01-03", "2026-12-31", "2027-02-30", "2027-3-1", "20270301", ""):  # 그해 밖 · 없는 날 · 모양이 다름
        assert "SCHED_DATE" in 코드([{"due": due, "name": ""}]), due
    assert 코드([{"due": "2027-01-01", "name": ""}, {"due": "2027-12-31", "name": ""}]) == set()  # 그해의 처음과 끝 · 이름은 비어도 된다
    문구 = lambda due: [i.message for i in validate_plan(_바꿈(좋은, 2, scheds=[{"due": "2027-05-05", "name": ""}, {"due": due, "name": ""}]), 2027)
                      if i.code == "SCHED_DATE"]
    assert 문구("") == ["실행목표 3번 2번째 일정: 예정 기한 날짜를 골라 주세요"], 문구("")  # 새 비정기 줄은 날짜가 비어 있다 — 파이썬 모양('') 대신
    for due in ("2028-01-03", "2027-02-30", "엉터리'날짜"):
        assert 문구(due) == ["실행목표 3번 2번째 일정: 예정 기한은 2027년 안의 날짜로 골라 주세요"], 문구(due)  # 적은 값을 되풀이하지 않는다
    일정 = lambda n: [{"due": "2027-05-05", "name": f"가상 {i}"} for i in range(n)]
    assert 코드(일정(24)) == set() and "SCHED_MAX" in 코드(일정(25))


@check
def 정기의_목표_숫자는_양수거나_비움():
    좋은 = _좋은계획()
    for 값 in (0, -3, -0.5):
        assert _codes(_바꿈(좋은, 0, target=값)) == {"TARGET"}, 값
    for 값 in ("12", True, False):  # 숫자가 아니면 모양이 틀린 것 (불리언은 숫자가 아니다)
        assert _codes(_바꿈(좋은, 0, target=값)) == {"SHAPE"}, 값
    assert _codes(_바꿈(좋은, 0, target=None)) == set() and _codes(_바꿈(좋은, 0, target=0.5)) == set()


@check
def 업무_종류는_정기_비정기_둘_중_하나():
    좋은 = _좋은계획()
    for 값 in ("", "regular", None, 1):
        assert "KIND" in _codes(_바꿈(좋은, 0, kind=값)), 값
    없음 = _좋은계획()
    del 없음[0]["items"][0]["kind"]
    assert "KIND" in _codes(없음)


@check
def 모양이_틀리면_SHAPE():
    좋은 = _좋은계획()
    for 틀림 in ({}, None, "x", [{"items": "x"}], [{"team": "t", "personal": "p"}], [{"team": 1, "personal": "p", "items": []}],
               ["x"], [{"team": "t", "personal": "p", "items": ["x"]}]):
        assert _codes(틀림) == {"SHAPE"}, 틀림
    for 칸, 값 in (("weight", "40"), ("weight", True), ("weight", None), ("weight", float("nan")), ("action", 5), ("S", None),
                  ("unit", 3), ("scheds", "x"), ("rid", 0), ("rid", "1"), ("rid", True), ("target", "5"), ("target", True)):
        assert _codes(_바꿈(좋은, 0, **{칸: 값})) == {"SHAPE"}, (칸, 값)
    # R1: 비정기는 목표 숫자 · 단위가 없고, 정기는 일정이 없다
    assert _codes(_바꿈(좋은, 2, target=5)) == {"SHAPE"} and _codes(_바꿈(좋은, 2, unit="건")) == {"SHAPE"}
    assert _codes(_바꿈(좋은, 0, scheds=[{"due": "2027-05-05", "name": ""}])) == {"SHAPE"}
    assert validate_plan(_바꿈(좋은, 2, target=5), 2027)[0].target == "item:1.0"  # 묶음 1 의 줄 0
    # 일정 한 칸의 모양
    for 일정 in ("x", {"due": "2027-05-05"}, {"due": 20270505, "name": ""}, {"due": "2027-05-05", "name": "", "sid": 0}):
        assert _codes(_바꿈(좋은, 2, scheds=[일정])) == {"SHAPE"}, 일정


@check
def 글자_수_한도를_넘으면_TEXT_LONG():
    좋은 = _좋은계획()
    한도 = {"action": 200, "formula": 200, "owner": 30, "unit": 10, "S": 100, "A": 100, "B": 100, "C": 100, "D": 100}
    for 칸, n in 한도.items():
        assert _codes(_바꿈(좋은, 0, **{칸: "가" * n})) == set(), 칸  # 한도 딱 맞는 건 된다
        assert _codes(_바꿈(좋은, 0, **{칸: "가" * (n + 1)})) == {"TEXT_LONG"}, 칸
    for 칸 in ("team", "personal"):
        for n, 기대 in ((200, set()), (201, {"TEXT_LONG"})):
            g = _좋은계획()
            g[0][칸] = "가" * n
            assert _codes(g) == 기대, (칸, n)
    일정 = lambda n: _codes(_바꿈(좋은, 2, scheds=[{"due": "2027-05-05", "name": "가" * n}]))
    assert 일정(40) == set() and 일정(41) == {"TEXT_LONG"}


@check
def 저장할_계획서는_아는_칸만_남긴다():
    좋은 = _좋은계획()
    assert clean_plan(좋은) == 좋은 and all("rid" not in it for it in _행들(clean_plan(좋은)))  # 번호가 없으면 칸도 없다
    지저분 = _좋은계획()
    지저분[0]["junk"] = "버릴 칸"
    지저분[0]["items"][0].update(extra={"a": 1}, rid=7)
    지저분[0]["items"][1].update(rid=None)
    지저분[1]["items"][0]["scheds"][0].update(sid=5, extra=2)
    기대 = _좋은계획()
    기대[0]["items"][0]["rid"] = 7
    기대[1]["items"][0]["scheds"][0]["sid"] = 5
    복사 = copy.deepcopy(지저분)
    assert clean_plan(지저분) == 기대 and 지저분 == 복사  # rid · sid 는 있을 때만 · 보낸 쪽은 건드리지 않는다
    # 정기 · 비정기가 아닌 줄은 모양 검사가 업무 종류 · 목표 숫자를 안 본다 — 저장할 때는 글자 / 숫자 / None 으로 고른다
    미정 = _좋은계획()
    미정[0]["items"][0].update(kind=["x"], target={"a": 1})
    assert validate_plan(미정, 2027)[0].code == "KIND" and all(i.code != "SHAPE" for i in validate_plan(미정, 2027))
    assert (_행들(clean_plan(미정))[0]["kind"], _행들(clean_plan(미정))[0]["target"]) == ("", None)
    assert _행들(clean_plan(_바꿈(좋은, 0, target=0.5)))[0]["target"] == 0.5


@check
def 새_행에_번호를_주고_종류를_바꾸면_새_번호():
    확정 = assign_ids(_좋은계획(), [])
    assert [it["rid"] for it in _행들(확정)] == [1, 2, 3]
    assert [s["sid"] for s in _행들(확정)[2]["scheds"]] == [1, 2]
    # 확정본에 있던 행은 문장을 고쳐도 번호를 지키고, 새 행 · 새 일정은 쓴 적 없는 번호
    고침 = copy.deepcopy(확정)
    _행들(고침)[1]["action"] = "가상 고친 문장"
    고침[0]["items"].append(_행("가상 새 업무", 0, "reg"))
    고침[1]["items"][0]["scheds"].append({"due": "2027-09-09", "name": "가상 새 일정"})
    결과 = assign_ids(고침, [확정])
    assert [it["rid"] for it in _행들(결과)] == [1, 2, 4, 3]
    assert [s["sid"] for s in _행들(결과)[3]["scheds"]] == [1, 2, 3]
    # 마지막 확정본에서 rid 1 은 정기(reg) — 지금 rid 1 이 비정기(irr)면 다른 행이다
    바뀜 = copy.deepcopy(확정)
    _행들(바뀜)[0].update(kind="irr", target=None, unit="", scheds=[{"due": "2027-04-04", "name": "가상 일정"}])
    결과 = assign_ids(바뀜, [확정])
    assert [it["rid"] for it in _행들(결과)] == [4, 2, 3]
    assert [s["sid"] for s in _행들(결과)[0]["scheds"]] == [3]
    # 마지막 확정본만 본다 — 그 전 판에서 종류가 달랐어도 지금 판과 같으면 이어진다
    옛 = copy.deepcopy(확정)
    _행들(옛)[0].update(kind="irr", target=None, unit="", scheds=[{"sid": 9, "due": "2027-02-02", "name": "가상 옛 일정"}])
    assert assign_ids(확정, [옛, 확정]) == 확정


@check
def 번호_부여는_원본을_안_건드리고_중복은_뒤에_새_번호():
    원본 = _좋은계획()
    복사 = copy.deepcopy(원본)
    확정 = assign_ids(원본, [])
    assert 원본 == 복사  # 입력은 그대로
    assert assign_ids(확정, []) == 확정 and assign_ids(확정, [확정]) == 확정  # 다시 해도 같다
    # 같은 번호가 둘이면 앞의 것이 지키고 뒤의 것이 새 번호 (일정 번호도 계획서 전체에서 하나뿐)
    중복 = copy.deepcopy(확정)
    _행들(중복)[1]["rid"] = 1
    _행들(중복)[2]["scheds"][1]["sid"] = 1
    결과 = assign_ids(중복, [])
    assert [it["rid"] for it in _행들(결과)] == [1, 2, 3] and [s["sid"] for s in _행들(결과)[2]["scheds"]] == [1, 2]
    결과 = assign_ids(중복, [확정])
    assert [it["rid"] for it in _행들(결과)] == [1, 4, 3] and [s["sid"] for s in _행들(결과)[2]["scheds"]] == [1, 3]
    # 지운 행의 번호는 다시 쓰지 않는다
    지움 = _행빼기(확정, 2)
    지움[0]["items"].append(_행("가상 새 업무", 30, "reg"))
    assert [it["rid"] for it in _행들(assign_ids(지움, [확정]))] == [1, 2, 4]
    # 앞에 있는 새 행이 뒤에 있는 기존 번호를 가로채지 않는다
    옛판 = assign_ids([{"team": "t", "personal": "p", "items": [_행("가상 옛 업무 1", 50, "reg"), _행("가상 옛 업무 2", 50, "reg")]}], [])
    앞에새행 = copy.deepcopy(옛판)
    앞에새행[0]["items"].insert(0, _행("가상 새 업무", 30, "reg"))
    assert [it["rid"] for it in _행들(assign_ids(앞에새행, [옛판]))] == [3, 1, 2]


@check
def 번호는_마지막_확정본에_있는_것만_지킨다():
    # 확정본이 없으면 클라이언트가 보낸 번호는 믿지 않는다 — 1 부터
    한줄 = [{"team": "t", "personal": "p", "items": [_행("가상 업무", 100, "reg", rid=99)]}]
    assert _행들(assign_ids(한줄, []))[0]["rid"] == 1
    보냄 = _좋은계획()
    _행들(보냄)[0]["rid"] = 99
    _행들(보냄)[2]["scheds"][0]["sid"] = 77
    결과 = _행들(assign_ids(보냄, []))
    assert [it["rid"] for it in 결과] == [1, 2, 3] and [s["sid"] for s in 결과[2]["scheds"]] == [1, 2]
    # 옛 판에만 있고 마지막 판에서 지운 rid 는 되살리지 못한다 — 쓴 적 없는 새 번호
    v1 = assign_ids(_좋은계획(), [])  # rid 1 2 3
    v2 = assign_ids(_행빼기(v1, 2), [v1])  # rid 3 을 지운 판
    되살림 = copy.deepcopy(v2)
    되살림[0]["items"].append(_행("가상 되살린 업무", 0, "reg", rid=3))
    assert [it["rid"] for it in _행들(assign_ids(되살림, [v1, v2]))] == [1, 2, 4]
    # 지운 행에 있던 일정의 sid 도 다시 쓰지 않는다 (v1 의 sid 1 2 는 v2 에서 사라졌다)
    새비정기 = copy.deepcopy(v2)
    새비정기[1]["items"].append(_행("가상 새 비정기 업무", 0, "irr", scheds=[{"due": "2027-09-09", "name": "가상 새 일정"}]))
    결과 = _행들(assign_ids(새비정기, [v1, v2]))
    assert 결과[2]["rid"] == 4 and [s["sid"] for s in 결과[2]["scheds"]] == [3]
    # 일정이 마지막 판과 다른 행 밑으로 옮겨졌으면 새 sid, 그대로 있는 일정은 sid 를 지킨다
    계획 = _좋은계획()
    계획[1]["items"].append(_행("가상 비정기 업무 2", 0, "irr", scheds=[{"due": "2027-08-08", "name": "가상 셋째"}]))
    v = assign_ids(계획, [])  # rid 1~4 · sid 1 2 (rid 3) · 3 (rid 4)
    옮김 = copy.deepcopy(v)
    옮김[1]["items"][1]["scheds"].append(옮김[1]["items"][0]["scheds"].pop(0))  # sid 1 이 rid 3 에서 rid 4 로
    결과 = _행들(assign_ids(옮김, [v]))
    assert [s["sid"] for s in 결과[2]["scheds"]] == [2] and [s["sid"] for s in 결과[3]["scheds"]] == [3, 4]


@check
def 상태_흐름():
    assert next_status("DRAFT", "submit") == "SUBMITTED" and next_status("SUBMITTED", "return") == "DRAFT"
    assert next_status("CONFIRMED", "request_change") == "CHANGE_REQUESTED" and next_status("CHANGE_REQUESTED", "reject") == "CONFIRMED"
    assert next_status("CHANGE_REQUESTED", "unlock") == "DRAFT"
    for bad in (("CONFIRMED", "confirm"), ("DRAFT", "unlock"), ("SUBMITTED", "save"), ("CHANGE_REQUESTED", "return")):
        _raises(StateConflictError, next_status, *bad)


@check
def 상태_흐름은_그림의_여덟_조합만_된다():
    된다 = {
        ("DRAFT", "save"): "DRAFT", ("DRAFT", "submit"): "SUBMITTED",
        ("SUBMITTED", "cancel"): "DRAFT", ("SUBMITTED", "return"): "DRAFT", ("SUBMITTED", "confirm"): "CONFIRMED",
        ("CONFIRMED", "request_change"): "CHANGE_REQUESTED",
        ("CHANGE_REQUESTED", "unlock"): "DRAFT", ("CHANGE_REQUESTED", "reject"): "CONFIRMED",
    }
    assert {a for _, a in 된다} == set(ACTIONS)
    for 상태 in ("DRAFT", "SUBMITTED", "CONFIRMED", "CHANGE_REQUESTED"):
        for 행동 in ACTIONS:
            if (상태, 행동) in 된다:
                assert next_status(상태, 행동) == 된다[(상태, 행동)], (상태, 행동)
            else:
                _raises(StateConflictError, next_status, 상태, 행동)
    _raises(StateConflictError, next_status, "NOPE", "save")
    _raises(StateConflictError, next_status, "DRAFT", "nope")


@check
def 작성_기간_판정():
    y = ("2027-01-04", "2027-01-22", "2027-12-01")
    assert window_open(date(2027, 1, 22), y, None) and not window_open(date(2027, 1, 23), y, None)
    assert window_open(date(2027, 3, 2), y, ("2027-03-02", "2027-03-10"))
    assert not window_open(date(2027, 12, 1), ("2027-11-01", "2027-12-31", "2027-12-01"), None)
    # 양 끝 날짜를 포함한다
    assert window_open(date(2027, 1, 4), y, None) and not window_open(date(2027, 1, 3), y, None)
    # 사람 기간이 우선 — 그해 기간 밖이어도 사람 기간 안이면 열리고, 그해 기간 안이어도 사람 기간 밖이면 닫힌다
    사람 = ("2027-03-02", "2027-03-10")
    assert window_open(date(2027, 3, 10), y, 사람) and not window_open(date(2027, 3, 11), y, 사람)
    assert not window_open(date(2027, 1, 10), y, 사람)
    # 평가 시작일부터는 사람 기간도 닫힌다
    assert window_open(date(2027, 11, 30), y, ("2027-11-20", "2027-12-31"))
    assert not window_open(date(2027, 12, 1), y, ("2027-11-20", "2027-12-31"))
    # 기간이 없으면 닫혀 있다 · 평가 시작일이 없으면 기간만 본다 · 그해 줄이 없어도 사람 기간은 쓴다
    assert not window_open(date(2027, 1, 10), None, None) and not window_open(date(2027, 1, 10), (None, None, None), None)
    assert not window_open(date(2027, 1, 10), ("2027-01-04", None, None), None)
    assert window_open(date(2027, 1, 10), ("2027-01-04", "2027-01-22", None), None)
    assert window_open(date(2027, 3, 5), None, 사람)
    # 어느 기간이 적용되는지는 한 곳에서 가린다: 사람 기간 > 그해 기간 > 없음
    assert window_span(y, 사람) == 사람 and window_span(y, None) == ("2027-01-04", "2027-01-22") and window_span(None, 사람) == 사람
    assert window_span(None, None) == (None, None) and window_span((None, None, None), None) == (None, None)


def _진행_정기(target=12, **kw):
    return {"kind": "reg", "target": target, "unit": "건", "weight": 40, "scheds": [], **kw}


def _진행_비정기(*dues, weight=30, sid0=1):
    return {"kind": "irr", "target": None, "unit": "", "weight": weight,
            "scheds": [{"sid": sid0 + i, "due": d, "name": f"가상 일정 {sid0 + i}"} for i, d in enumerate(dues)]}


@check
def 달_한_줄은_다음_달_7일까지():
    assert month_open(2027, 3, date(2027, 4, 7)) and not month_open(2027, 3, date(2027, 4, 8))
    assert month_open(2027, 12, date(2028, 1, 7)) and not month_open(2027, 3, date(2027, 2, 28))
    # 그 달 1일부터 열린다 · 12월은 해를 넘어 다음 해 1월 7일까지 · 윤년 2월
    assert month_open(2027, 3, date(2027, 3, 1)) and not month_open(2027, 12, date(2028, 1, 8))
    assert not month_open(2027, 12, date(2027, 11, 30)) and not month_open(2027, 11, date(2028, 1, 1))
    assert month_open(2028, 2, date(2028, 2, 29)) and month_open(2028, 2, date(2028, 3, 7)) and not month_open(2028, 2, date(2028, 3, 8))


@check
def 그해_기록은_다음_해_1월_7일까지():
    assert records_open(2027, date(2027, 1, 1)) and records_open(2027, date(2028, 1, 7))
    assert not records_open(2027, date(2028, 1, 8)) and not records_open(2027, date(2028, 6, 1))


@check
def 처음_확정_전_달과_입사_전_달은_해당_없음():
    assert month_state(2027, 1, date(2027, 3, 10), 2, None, False) == "NA"
    assert month_state(2027, 2, date(2027, 3, 10), 2, date(2027, 3, 2), False) == "NA"
    assert month_state(2027, 3, date(2027, 3, 10), 2, date(2027, 3, 2), False) == "OPEN"
    assert month_state(2027, 1, date(2027, 3, 10), 1, None, False) == "MISSED"
    assert month_state(2027, 2, date(2027, 3, 5), 1, None, True) == "DONE"
    # 입사한 달은 해당 있음: 그 달 말일이 입사일 이후이면 된다 (2월 말일 28일 · 윤년 29일이 경계)
    assert month_state(2027, 2, date(2027, 3, 3), 1, date(2027, 2, 28), False) == "OPEN"
    assert month_state(2027, 2, date(2027, 3, 3), 1, date(2027, 3, 1), False) == "NA"
    assert month_state(2028, 2, date(2028, 3, 3), 1, date(2028, 2, 29), False) == "OPEN"
    assert month_state(2028, 2, date(2028, 3, 3), 1, date(2028, 3, 1), False) == "NA"
    assert month_state(2027, 12, date(2028, 1, 3), 1, date(2027, 12, 31), False) == "OPEN"
    # 해당 없음이 먼저다: 첫 확정 전 달은 썼어도 해당 없음
    assert month_state(2027, 1, date(2027, 3, 10), 2, None, True) == "NA"
    assert month_state(2027, 2, date(2027, 3, 10), 2, date(2027, 3, 2), True) == "NA"


@check
def 달_상태는_열림_놓침_예정으로_나뉜다():
    상태 = lambda month, today, written=False: month_state(2027, month, today, 1, None, written)
    assert 상태(3, date(2027, 4, 7)) == "OPEN" and 상태(3, date(2027, 4, 8)) == "MISSED"
    assert 상태(3, date(2027, 3, 1)) == "OPEN" and 상태(3, date(2027, 2, 28)) == "FUTURE"
    assert 상태(5, date(2027, 3, 10)) == "FUTURE" and 상태(12, date(2027, 3, 10)) == "FUTURE"
    assert 상태(12, date(2028, 1, 7)) == "OPEN" and 상태(12, date(2028, 1, 8)) == "MISSED"
    assert 상태(3, date(2027, 4, 7), True) == "DONE" and 상태(3, date(2027, 4, 8), True) == "DONE"  # 쓴 달은 늘 DONE


@check
def 실적은_목표로_나누고_100에서_자른다():
    정기 = _진행_정기(12)
    assert abs(item_pct(정기, 10, None, ()) - 10 / 12 * 100) < 1e-9 and abs(item_pct(정기, 10, None, ()) - 83.3333333) < 1e-6
    assert item_pct(정기, 15, None, ()) == 100 and item_pct(정기, 12, None, ()) == 100
    assert item_pct(정기, None, None, ()) == 0 and item_pct(정기, 0, None, ()) == 0
    assert item_pct(정기, None, 70, ()) == 0  # 목표가 있으면 직접 입력한 진행률은 안 본다
    # 목표 없음: 직접 입력한 진행률 그대로 (없으면 0) · 목표 0 은 없는 것으로 (검사가 막지만 죽지 않는다)
    assert item_pct(_진행_정기(None), None, 70, ()) == 70 and item_pct(_진행_정기(None), 5, None, ()) == 0
    assert item_pct(_진행_정기(0), 5, 40, ()) == 40 and item_pct({"kind": "reg"}, None, 25, ()) == 25
    assert all(isinstance(item_pct(정기, a, None, ()), float) for a in (None, 0, 10, 15))


@check
def 비정기_진행률은_결과_쓴_일정_비율():
    네개 = _진행_비정기("2027-03-01", "2027-06-01", "2027-09-01", "2027-12-01")
    assert item_pct(네개, None, None, [1, 3]) == 50 and item_pct(네개, None, None, ()) == 0
    assert item_pct(네개, None, None, {1, 2, 3, 4}) == 100
    assert item_pct(네개, None, None, {1, 3, 99}) == 50  # 이 업무에 없는 일정 번호는 세지 않는다
    assert item_pct(네개, 5, 80, {2}) == 25  # 비정기는 실적 · 진행률 입력을 안 본다
    assert item_pct(_진행_비정기(), None, None, {1}) == 0  # 일정이 없어도 죽지 않는다


@check
def 비정기_기대는_기한_14일_뒤부터():
    it = {"kind": "irr", "scheds": [{"sid": 1, "due": "2027-09-24", "name": ""}]}
    assert item_expected(it, 2027, date(2027, 10, 8), {1: "2027-09-24"}) == 0
    assert item_expected(it, 2027, date(2027, 10, 9), {1: "2027-09-24"}) == 100
    # 일정 넷: 14일이 지난 것만 센다 · 바뀐 기한(dues)이 우선이고 dues 에 없으면 자기 기한
    넷 = _진행_비정기("2027-03-01", "2027-06-01", "2027-09-01", "2027-12-01")
    dues = {1: "2027-03-01", 2: "2027-06-01", 3: "2027-09-01", 4: "2027-12-01"}
    assert item_expected(넷, 2027, date(2027, 3, 15), dues) == 0 and item_expected(넷, 2027, date(2027, 3, 16), dues) == 25
    assert item_expected(넷, 2027, date(2027, 9, 10), dues) == 50 and item_expected(넷, 2027, date(2028, 3, 1), dues) == 100
    assert item_expected(넷, 2027, date(2027, 9, 10), {**dues, 2: "2027-12-17"}) == 25  # 2번 일정을 뒤로 미뤘다
    assert item_expected(넷, 2027, date(2027, 9, 10), {}) == 50 and item_expected(_진행_비정기(), 2027, date(2027, 9, 20), {}) == 0
    # 윤년: 2월 20일 기한 + 14일 = 3월 5일
    윤 = _진행_비정기("2028-02-20")
    assert item_expected(윤, 2028, date(2028, 3, 5), {}) == 0 and item_expected(윤, 2028, date(2028, 3, 6), {}) == 100


@check
def 정기_기대는_그해_날수_비율():
    assert round(item_expected({"kind": "reg"}, 2026, date(2026, 10, 12), {})) == 78
    정기 = {"kind": "reg"}
    # 화면 kvExpect 와 같은 식: 1월 1일은 1/365 → 0 · 12월 31일은 100 · 윤년(366일)의 7월 1일은 183/366 = 50
    assert round(item_expected(정기, 2026, date(2026, 1, 1), {})) == 0 and abs(item_expected(정기, 2026, date(2026, 1, 1), {}) - 100 / 365) < 1e-9
    assert item_expected(정기, 2026, date(2026, 12, 31), {}) == 100 and item_expected(정기, 2024, date(2024, 12, 31), {}) == 100
    assert item_expected(정기, 2024, date(2024, 7, 1), {}) == 50 and round(item_expected(정기, 2024, date(2024, 2, 29), {})) == 16
    # 그해 전이면 0 · 그해 뒤면 100
    assert item_expected(정기, 2027, date(2026, 12, 31), {}) == 0 and item_expected(정기, 2027, date(2028, 1, 1), {}) == 100


@check
def 일정_상태는_주황_빨강():
    상태 = lambda today, done=False, due="2027-09-24": sched_state(due, today, done)
    assert 상태(date(2027, 9, 23)) == "UPCOMING" and 상태(date(2027, 9, 24)) == "UPCOMING"
    assert 상태(date(2027, 9, 25)) == "LATE" and 상태(date(2027, 10, 8)) == "LATE"
    assert 상태(date(2027, 10, 9)) == "OVERDUE" and 상태(date(2028, 5, 1)) == "OVERDUE"
    assert 상태(date(2027, 9, 1), True) == "DONE" and 상태(date(2027, 10, 9), True) == "DONE"  # 결과 있으면 늦었어도 DONE
    # 해를 넘는 14일 · 윤년
    assert 상태(date(2028, 1, 8), due="2027-12-25") == "LATE" and 상태(date(2028, 1, 9), due="2027-12-25") == "OVERDUE"
    assert 상태(date(2028, 3, 5), due="2028-02-20") == "LATE" and 상태(date(2028, 3, 6), due="2028-02-20") == "OVERDUE"


@check
def 바뀐_기한이_원래_기한을_덮는다():
    내용 = [
        {"team": "가상 팀목표", "personal": "가상 개인목표", "items": [
            {**_진행_정기(), "rid": 1},
            {**_진행_비정기("2027-09-24", "2027-11-05"), "rid": 2},
        ]},
        {"team": "가상 팀목표 2", "personal": "가상 개인목표 2", "items": [
            {**_진행_비정기("2027-06-30", sid0=3), "rid": 3},
        ]},
    ]
    원래 = {1: "2027-09-24", 2: "2027-11-05", 3: "2027-06-30"}
    복사 = copy.deepcopy(내용)
    assert effective_dues(내용, []) == 원래
    assert effective_dues(내용, [(1, "2027-12-17")]) == {**원래, 1: "2027-12-17"}
    assert effective_dues(내용, [(1, "2027-10-01"), (3, "2027-07-15"), (1, "2027-12-17")]) == {1: "2027-12-17", 2: "2027-11-05", 3: "2027-07-15"}  # 나중 변경이 이긴다
    assert effective_dues(내용, [(99, "2027-12-17")]) == 원래  # 확정본에 없는 일정 번호는 무시
    assert effective_dues([], [(1, "2027-12-17")]) == {} and effective_dues(내용, ()) == 원래
    assert 내용 == 복사  # 확정본은 건드리지 않는다


@check
def 총합은_가중치와_값의_곱을_100으로_나눈다():
    items = [{"weight": 40}, {"weight": 30}, {"weight": 30}]
    assert overall(items, [100, 50, 0]) == 55 and overall(items, [100, 100, 100]) == 100 and overall(items, [0, 0, 0]) == 0
    assert abs(overall(items, [10 / 12 * 100, 0, 0]) - 400 / 12) < 1e-9 and overall([], []) == 0
    assert overall([{"weight": 33.5}, {"weight": 66.5}], [100, 100]) == 100


@check
def 빈_일정_이름은_실행목표로():
    assert task_title("  ", "분기별 웨비나 개최") == "KPI- 분기별 웨비나 개최"
    assert task_title("", " 분기별 웨비나 개최 ") == "KPI- 분기별 웨비나 개최"
    assert task_title(" 1차 웨비나 ", "분기별 웨비나 개최") == "KPI- 1차 웨비나"  # 일정 이름이 있으면 그것
    assert task_title("", "  ") == "KPI- "


@check
def 링크는_http_https만():
    for 참 in ("https://a.b/c", "http://a.b", "HTTPS://A.B/c", "Http://a.b", "https://a.b/가나다?q=1#x", "https://" + "a" * 292):
        assert valid_link(참), 참
    assert len("https://" + "a" * 292) == LINK_MAX
    for 거짓 in ("javascript:alert(1)", "http://a b", "http://a\tb", "https://a\nb", "https://a\x00b", "https://a\x7fb", "https://a　b",
              " https://a.b", "https://a.b ", "ftp://a.b", "//a.b", "a.b", "", "http://", "https://", "HTTPS://", "https:/a.b"):
        assert not valid_link(거짓), repr(거짓)
    assert not valid_link("https://" + "a" * 293) and len("https://" + "a" * 293) == 301  # 300자 딱 맞는 것은 되고 301자는 안 된다
    assert not valid_link(None) and not valid_link(5)  # 글자가 아니면 거짓 (죽지 않는다)
    assert (NOTE_MAX, WHY_MAX, LINK_MAX, GRACE_DAY, LATE_DAYS) == (200, 100, 300, 7, 14)


# ---- KPI 현황 규칙 (kpi_review.py) · 보는 대상 (org.target_of) ----


def _오류_문구(fn, *args) -> str:
    """fn(*args) 가 ValueError 를 내야 한다. 그 문구를 돌려준다."""
    try:
        fn(*args)
    except ValueError as e:
        return str(e)
    raise AssertionError(f"ValueError 가 나야 한다: {fn.__name__}{args}")


def _정기줄(rid: int, 삼월: str) -> dict:
    """진행 JSON(kpi.progress_json) 모양의 정기 실행목표: 3월만 정한 상태, 나머지 달은 FUTURE."""
    return {"rid": rid, "kind": "reg", "months": {str(m): {"state": 삼월 if m == 3 else "FUTURE", "text": None} for m in range(1, 13)}}


def _월요약_진행() -> dict:
    """정기 3개(3월 DONE · OPEN · NA) + 비정기 1개(일정 3/20 결과 · 3/28 없음 · 5/2 없음 · 다음 해 3/5 결과)."""
    일정 = [
        {"sid": 1, "due": "2027-03-20", "result": {"text": "끝냈어요", "link": None, "at": "2027-03-21T01:00:00Z"}},
        {"sid": 2, "due": "2027-03-28", "result": None},
        {"sid": 3, "due": "2027-05-02", "result": None},
        {"sid": 4, "due": "2028-03-05", "result": {"text": "내년", "link": None, "at": None}},  # 다른 해의 3월은 세지 않는다
    ]
    return {"year": 2027, "items": [_정기줄(1, "DONE"), _정기줄(2, "OPEN"), _정기줄(3, "NA"), {"rid": 4, "kind": "irr", "scheds": 일정}]}


@check
def 반올림은_화면과_같다():
    assert js_round(56.5) == 57 and js_round(57.49) == 57 and js_round(0.5) == 1
    assert js_round(0) == 0 and js_round(37.4) == 37 and js_round(99.5) == 100 and js_round(45.0) == 45
    assert isinstance(js_round(56.5), int)
    assert round(56.5) == 56  # 파이썬 round 는 짝수쪽으로 가서 화면과 달라진다 — 쓰지 않는 이유


@check
def KPI_현황_약속_숫자():
    assert (GAP_DEFAULT, MEMO_MAX, MUTE_TEXT_MAX, BIZ_GOAL_MAX, BIZ_DETAIL_MAX, BIZ_MAX) == (20, 200, 50, 60, 300, 20)
    assert MUTE_WHY == ("year", "month", "wait", "etc")
    assert KST.utcoffset(None).total_seconds() == 9 * 3600


@check
def 그_달_요약():
    prog = _월요약_진행()
    assert month_summary(prog, 3) == {"reg": [1, 2], "irr": [1, 2], "records": 2, "noSched": False}
    넷째달 = month_summary(prog, 4)
    assert 넷째달["noSched"] and 넷째달["irr"] == [0, 0] and 넷째달["reg"] == [0, 3] and 넷째달["records"] == 0  # 아직 안 온 달도 NA 가 아니면 센다
    다섯째달 = month_summary(prog, 5)
    assert 다섯째달["irr"] == [0, 1] and not 다섯째달["noSched"]
    정기만 = {"year": 2027, "items": [_정기줄(1, "DONE")]}
    assert month_summary(정기만, 3) == {"reg": [1, 1], "irr": [0, 0], "records": 1, "noSched": False}  # 비정기가 없으면 noSched 가 아니다
    assert month_summary({"year": 2027, "items": []}, 3) == {"reg": [0, 0], "irr": [0, 0], "records": 0, "noSched": False}
    assert month_summary({"year": 2028, "items": prog["items"]}, 3)["irr"] == [1, 1]  # 달은 prog["year"] 해의 것만 센다


@check
def 확인_상태는_순서대로():
    s = month_summary(_월요약_진행(), 3)
    assert check_state(False, s, True) == "NOSTEP" and check_state(True, None, False) == "NA"
    assert check_state(False, None, False) == "NOSTEP"  # 확인 단계가 없다는 것이 먼저
    assert check_state(True, {"reg": [0, 0], "irr": [0, 0], "records": 0, "noSched": True}, False) == "NA"
    assert check_state(True, {"reg": [0, 2], "irr": [0, 0], "records": 0, "noSched": False}, True) == "NONE"
    assert check_state(True, {"reg": [0, 0], "irr": [0, 1], "records": 0, "noSched": False}, False) == "NONE"
    assert check_state(True, s, True) == "DONE" and check_state(True, s, False) == "TODO"


@check
def 알람_끄기는_고른_달까지():
    assert is_muted(2027, 10, date(2027, 10, 31)) and not is_muted(2027, 10, date(2027, 11, 1)) and not is_muted(2027, 12, date(2028, 1, 2))
    assert is_muted(2027, 12, date(2027, 12, 31)) and not is_muted(2027, 10, date(2026, 10, 5))  # 다른 해에는 켜져 있다
    오늘 = date(2027, 4, 2)
    assert mute_until("year", None, 오늘) == 12 and mute_until("month", 4, 오늘) == 4
    assert mute_until("wait", 12, 오늘) == 12 and mute_until("etc", 7, 오늘) == 7 and mute_until("year", 3, 오늘) == 12  # year 는 달을 보지 않는다
    이유 = "이유를 하나 골라 주세요."
    for 틀린_이유 in ("later", "", None, 5, ["year"], "YEAR"):
        assert _오류_문구(mute_until, 틀린_이유, 5, 오늘) == 이유, 틀린_이유
    달 = "이번 달부터 12월 사이에서 골라 주세요."
    assert _오류_문구(mute_until, "month", 3, 오늘) == 달 and _오류_문구(mute_until, "etc", 13, 오늘) == 달
    assert _오류_문구(mute_until, "month", True, 오늘) == 달 and _오류_문구(mute_until, "month", True, date(2027, 1, 15)) == 달  # 참 = 1 이 1월에도 통과하면 안 된다
    for 틀린_달 in (None, 4.0, "4", 0, -1, 13, [4]):
        assert _오류_문구(mute_until, "wait", 틀린_달, 오늘) == 달, 틀린_달


@check
def 끈_항목은_빼고_따진다():
    items = [{"weight": 50}, {"weight": 50}]
    값, 기대 = [10, 80], [60, 60]
    둘다켜짐 = alarm(items, 값, 기대, [False, False], 20)
    assert 둘다켜짐 == {"slow": False, "pct": 45, "expected": 60}  # 차이 15 < 20
    첫째끔 = alarm(items, 값, 기대, [True, False], 20)
    assert 첫째끔 == {"slow": False, "pct": 80, "expected": 60}
    둘째끔 = alarm(items, 값, 기대, [False, True], 20)
    assert 둘째끔 == {"slow": True, "pct": 10, "expected": 60}  # 차이 50
    assert alarm(items, 값, 기대, [True, True], 20) == {"slow": False, "pct": None, "expected": None}
    assert alarm([], [], [], [], 20) == {"slow": False, "pct": None, "expected": None}
    # 가중치가 다르면 가중 평균: 30 · 70, 값 0 · 100 → 70, 기대 100 · 0 → 30
    assert alarm([{"weight": 30}, {"weight": 70}], [0, 100], [100, 0], [False, False], 20) == {"slow": False, "pct": 70, "expected": 30}
    assert alarm(items, 값, 기대, [False, True], 50)["slow"] and not alarm(items, 값, 기대, [False, True], 51)["slow"]  # 기준 딱 맞으면 부진 (≥)
    assert alarm([{"weight": 0}], [0], [100], [False], 20) == {"slow": False, "pct": None, "expected": None}  # 가중치 합이 0 이어도 죽지 않는다


@check
def 부진은_반올림한_숫자로():
    한줄 = [{"weight": 100}]
    결과 = alarm(한줄, [37.4], [56.5], [False], 20)
    assert 결과["slow"], 결과  # 화면은 37 · 57 (차이 20)
    assert 56.5 - 37.4 < 20 and round(56.5) - round(37.4) < 20  # 반올림 없이(19.1) · 파이썬 round(56)로는 부진이 아니게 되는 경계
    assert not alarm(한줄, [37.5], [56.5], [False], 20)["slow"]  # 38 · 57 → 19
    assert not alarm(한줄, [37.4], [56.4], [False], 20)["slow"]  # 37 · 56 → 19


@check
def 한국_날짜로_같은_날():
    assert same_kst_day("2027-03-10T15:30:00Z", date(2027, 3, 11)) and not same_kst_day("2027-03-10T14:59:00Z", date(2027, 3, 11))
    assert same_kst_day("2027-03-10T14:59:00Z", date(2027, 3, 10)) and same_kst_day("2027-03-11T14:59:59Z", date(2027, 3, 11))
    assert not same_kst_day("2027-03-11T15:00:00Z", date(2027, 3, 11)) and same_kst_day("2027-12-31T15:00:00Z", date(2028, 1, 1))  # 해가 바뀌는 곳


@check
def 경영계획_검사():
    assert clean_biz_plan([{"goal": " 매출 성장 ", "detail": ""}, {"goal": "", "detail": " "}]) == [{"goal": "매출 성장", "detail": ""}]
    assert clean_biz_plan([]) == [] and clean_biz_plan([{"goal": "  ", "detail": "\t"}]) == []
    assert clean_biz_plan([{"goal": "가", "detail": " 내용 ", "x": 1}]) == [{"goal": "가", "detail": "내용"}]  # 다른 칸은 버린다
    원본 = [{"goal": " 가 ", "detail": " 나 "}]
    clean_biz_plan(원본)
    assert 원본 == [{"goal": " 가 ", "detail": " 나 "}]  # 받은 것은 건드리지 않는다
    줄 = lambda i: {"goal": f"목표{i}", "detail": ""}
    assert len(clean_biz_plan([줄(i) for i in range(BIZ_MAX)])) == 20
    assert len(clean_biz_plan([줄(i) for i in range(BIZ_MAX)] + [{"goal": "", "detail": ""}] * 5)) == 20  # 빈 줄은 개수에 안 든다
    assert _오류_문구(clean_biz_plan, [줄(i) for i in range(BIZ_MAX + 1)])
    assert _오류_문구(clean_biz_plan, [{"goal": " ", "detail": "내용만"}])  # 목표 없이 내용
    assert clean_biz_plan([{"goal": "가" * 60, "detail": "나" * 300}]) == [{"goal": "가" * 60, "detail": "나" * 300}]
    assert clean_biz_plan([{"goal": " " + "가" * 60 + " ", "detail": " " + "나" * 300 + " "}])[0]["goal"] == "가" * 60  # 공백을 뺀 길이로 센다
    assert _오류_문구(clean_biz_plan, [{"goal": "가" * 61, "detail": ""}])
    assert _오류_문구(clean_biz_plan, [{"goal": "가", "detail": "나" * 301}])
    for 글자_아님 in (None, "목표", {"goal": "가", "detail": ""}, ({"goal": "가", "detail": ""},), 5):
        assert _오류_문구(clean_biz_plan, 글자_아님), 글자_아님  # 목록이 아니다
    for 틀린_줄 in (None, "가", 5, [], {"goal": "가"}, {"detail": "가"}, {"goal": 5, "detail": ""}, {"goal": "가", "detail": None}, {"goal": None, "detail": None}):
        assert _오류_문구(clean_biz_plan, [틀린_줄]), 틀린_줄  # 줄이 {goal, detail} 글자가 아니다


@check
def 보는_대상_모양():
    # 본부 1 > 팀 11 · 직속 팀 9 · 임원 8 — 팀장 21(팀 11) · 팀원 22(팀 11) · 본부 직속 23(본부 1) · 본부장 20 · 직속 팀원 42(팀 9) · 임원 소속 81
    D = [Dept(1, "가상본부", None), Dept(11, "가상1팀", 1), Dept(9, "가상지원팀", None), Dept(8, "임원", None)]
    P = {p.id: p for p in (
        Person(6, 8, Role.CEO),
        Person(20, 8, Role.DIVISION_HEAD, led_department_id=1),
        Person(21, 11, Role.TEAM_LEADER, led_department_id=11), Person(22, 11), Person(23, 1),
        Person(24, 1, Role.TEAM_LEADER, led_department_id=11),  # 소속은 본부, 이끄는 곳은 팀 — 이끄는 곳이 기준
        Person(41, 9, Role.TEAM_LEADER, led_department_id=9), Person(42, 9),
        Person(81, 8),
        Person(90, 777), Person(91, 11, Role.TEAM_LEADER, led_department_id=777),  # 명단에 없는 부서
        Person(92, 8, Role.DIVISION_HEAD),  # 맡은 부서가 없으면 소속 부서
    )}
    assert target_of(P[21], D) == Target(21, 11, 1, Role.TEAM_LEADER) and target_of(P[22], D) == Target(22, 11, 1)
    assert target_of(P[23], D) == Target(23, None, 1) and target_of(P[20], D) == Target(20, None, 1, Role.DIVISION_HEAD)
    assert target_of(P[42], D) == Target(42, 9, None, direct=True) and target_of(P[81], D) == Target(81, None, None)
    assert target_of(P[6], D) == Target(6, None, None, Role.CEO) and target_of(P[24], D) == Target(24, 11, 1, Role.TEAM_LEADER)
    assert target_of(P[41], D) == Target(41, 9, None, Role.TEAM_LEADER, direct=True)
    assert target_of(P[90], D) == Target(90, None, None) and target_of(P[91], D) == Target(91, None, None, Role.TEAM_LEADER)
    assert target_of(P[92], D) == Target(92, None, 8, Role.DIVISION_HEAD)  # 본부장은 맡은 부서(없으면 소속)를 본부로 본다
    assert target_of(P[22], D).pool is Pool.DIVISION and target_of(P[42], D).pool is Pool.DIRECT and target_of(P[21], D).pool is Pool.LEADERS
    assert can(Actor(21, Role.TEAM_LEADER, led_team_id=11), Act.VIEW_KPI, target_of(P[22], D))
    assert can(Actor(20, Role.DIVISION_HEAD, led_division_id=1), Act.VIEW_KPI, target_of(P[23], D))
    assert not can(Actor(6, Role.CEO), Act.VIEW_KPI, target_of(P[22], D)) and can(Actor(6, Role.CEO), Act.VIEW_KPI, target_of(P[21], D))
    assert can(Actor(6, Role.CEO), Act.VIEW_KPI, target_of(P[20], D)) and not can(Actor(6, Role.CEO), Act.VIEW_KPI, target_of(P[42], D))
    assert can(Actor(20, Role.DIVISION_HEAD, led_division_id=1), Act.VIEW_KPI, target_of(P[21], D))
    assert not can(Actor(20, Role.DIVISION_HEAD, led_division_id=1), Act.VIEW_KPI, target_of(P[42], D))  # 다른 본부(직속 팀)는 못 본다
    assert not can(Actor(21, Role.TEAM_LEADER, led_team_id=11), Act.VIEW_KPI, target_of(P[42], D))


# ---- 평가 규칙 (eval_rules.py) ----


@check
def 총점은_가중치와_가점():
    rows = [(40, "S"), (30, "A"), (30, "B")]
    assert total(rows, 0) == Decimal("101.0") and total(rows, 40) == Decimal("110") and total([(100, None)], 0) == 0
    assert total([(33.3, "A"), (33.3, "B"), (33.4, "C")], 1) == Decimal("90.5")  # 33.3 + 29.97 + 26.72 + 0.5 = 90.49 → 90.5 (둘째 자리에서 사사오입)


@check
def 점수_기준은_초과():
    b = BANDS
    assert [band(Decimal(x), b) for x in ("100.5", "100", "90.5", "90", "80.5", "80", "70.5", "70")] == ["S", "A", "A", "B", "B", "C", "C", "D"]
    assert above("A", "B") and not above("B", "B") and not above("C", "B")


@check
def 연차는_5일부터():
    assert cap(Decimal("105"), 10, BANDS) == "C" and cap(Decimal("105"), 9, BANDS) == "S" and cap(None, 10, BANDS) == "C" and cap(None, 0, BANDS) == "S"
    assert cap(Decimal("85"), 0, BANDS) == "B" and cap(Decimal("65"), 10, BANDS) == "D"  # 점수 기준과 연차 규칙 중 낮은 쪽


@check
def 정원_검사는_화면과_같다():  # evSelfCheck 의 R (101 · 99 · 96 · 92 · 88 · 80) — 6명이라 A·B·D 각 1명
    R = [EvMember(i, f"p{i}", Decimal(s), None, 0) for i, s in zip(range(1, 7), ("101", "99", "96", "92", "88", "80"))]
    g = lambda a, rows=R: [replace(m, grade=a.get(m.uid)) for m in rows]  # noqa: E731
    assert check_pool(g({1: "S", 3: "B", 6: "D", 2: "C", 4: "C", 5: "C"}), BANDS, True, need_note=False)[0] == []  # S 1명이면 A 0명으로 통과 (p2 가 p3 에게 밀린 줄은 코멘트를 안 따지면 경고라 오류가 아니다)
    assert len(check_pool(g({1: "S", 2: "A"}), BANDS, False)[0]) == 1  # S가 있는데 A까지 주면 초과
    assert any("100점" in e for e in check_pool(g({2: "S"}), BANDS, False)[0])  # 총점 100 이하 S 불가
    assert any("최대" in e for e in check_pool(g({1: "S", 2: "S"}), BANDS, False)[0])  # S 상한
    assert check_pool(g({2: "A"}), BANDS, False)[:2] == ([], [])  # 배정 중에는 누락 · 미달을 안 막음
    e, w, _ = check_pool(g({i: "C" for i in range(1, 7)}), BANDS, True); assert e == [] and len(w) == 3  # 미달은 경고만
    assert any("등급이 없" in e for e in check_pool(g({1: "A"}), BANDS, True)[0])  # 마감 · 확정은 누락을 막음
    assert len(check_pool(g({2: "A", 3: "B"}, R[:3]), BANDS, False)[0]) == 1  # N ≤ 4 는 A·B 합계
    assert any("연차" in e for e in check_pool([EvMember(1, "가", Decimal("95"), "A", 10)], BANDS, False)[0])
    kpi_없음 = [EvMember(1, "가", None, "S", 0), *R[1:]]  # 1명짜리 묶음은 S 정원이 0이라 6명 묶음에서 본다
    assert check_pool(kpi_없음, BANDS, False)[0] == []  # KPI 없는 사람은 점수 기준 없음
    assert any("100점" in e for e in check_pool([replace(kpi_없음[0], score=Decimal("50")), *R[1:]], BANDS, False)[0])  # 점수가 있으면 따진다


@check
def 정원_검사_문구와_순서는_화면_그대로():  # evValidate 의 문구 · 순서 — 이름 · 총점 · 정원 숫자가 든다
    R = [EvMember(i, f"p{i}", Decimal(s), None, 0) for i, s in zip(range(1, 7), ("101", "99", "96.5", "92", "88", "80"))]
    e, w, n = check_pool([replace(m, grade={1: "S", 2: "A", 6: "A"}.get(m.uid)) for m in R], BANDS, True)
    assert e == [
        "p3 님의 등급이 없습니다.", "p4 님의 등급이 없습니다.", "p5 님의 등급이 없습니다.",
        "p6 님은 총점 80점이라 A를 줄 수 없습니다. (A는 90점 초과)",
        "A는 0명까지입니다 (S 1명만큼 줄어듦). 현재 2명",
    ]
    assert w == ["B가 정원보다 1명 적습니다.", "D가 정원보다 1명 적습니다."] and n == {"S": 1, "A": 2, "B": 0, "C": 0, "D": 0}
    e = check_pool([EvMember(1, "가", Decimal("88.5"), "S", 0)], BANDS, False)[0]
    assert e[0] == "가 님은 총점 88.5점이라 S를 줄 수 없습니다. (100점 초과만)" and e[1] == "S는 최대 0명입니다. (현재 1명)"
    e = check_pool([EvMember(1, "가", Decimal("95"), "A", 10), EvMember(2, "나", Decimal("85"), "B", 12)], BANDS, False)[0]
    assert e == ["가 님은 연차 초과 5일 이상이라 S·A·B를 줄 수 없습니다.", "나 님은 연차 초과 5일 이상이라 S·A·B를 줄 수 없습니다.", "A·B 합계는 1명까지입니다. 현재 2명"]
    R5 = [EvMember(i, f"q{i}", Decimal("75"), "B" if i < 4 else "C", 0) for i in range(1, 6)]  # 75점은 C 기준(70 초과)까지 → B는 못 준다
    e, w, _ = check_pool(R5, BANDS, True)
    assert e[:3] == [f"q{i} 님은 총점 75점이라 B를 줄 수 없습니다. (B는 80점 초과)" for i in (1, 2, 3)] and e[3] == "B는 1명까지입니다. 현재 3명"
    assert w == ["A가 정원보다 1명 적습니다.", "D가 정원보다 1명 적습니다."]


@check
def 점수_역전은_코멘트가_없으면_오류_있으면_경고():
    R = [EvMember(1, "가", Decimal("95"), "C", 0), EvMember(2, "나", Decimal("91"), "A", 0)]
    R += [EvMember(i, f"p{i}", Decimal(str(90 - i)), "C", 0) for i in range(3, 7)]  # 6명 묶음 — 기본 비율이면 A·B·D 각 1명
    줄 = "가 님(총점 95점)이 총점이 더 낮은 나 님(91점)보다 등급이 낮습니다."
    e, w, _ = check_pool(R, BANDS, False)
    assert e == [줄 + " 코멘트에 이유를 적어 주세요."] and w == [], (e, w)
    e, w, _ = check_pool([replace(R[0], note=True), *R[1:]], BANDS, False)
    assert e == [] and w == [줄], (e, w)  # 코멘트가 있으면 경고만
    e, w, _ = check_pool(R, BANDS, False, need_note=False)
    assert e == [] and w == [줄], (e, w)  # 코멘트를 요구하지 않는 곳(배정 중)은 경고만
    # 정원 줄 뒤에 붙고, full 이어도 역전 줄이 마지막이다
    R2 = [R[0], R[1], replace(R[1], uid=7, name="다", score=Decimal("90.5")), *R[3:]]  # A 2명 → 정원 초과 + 가 님이 둘에게 밀림
    e, w, _ = check_pool(R2, BANDS, False)
    assert e[0] == "A는 1명까지입니다. 현재 2명" and len(e) == 2 and e[1].startswith("가 님(총점 95점)이 총점이 더 낮은 나 님(91점)"), e
    e, w, _ = check_pool(R2, BANDS, True)
    assert e[-1].endswith("코멘트에 이유를 적어 주세요.") and w[:2] == ["B가 정원보다 1명 적습니다.", "D가 정원보다 1명 적습니다."], (e, w)


@check
def 점수_역전_줄은_가장_높은_등급을_받은_사람을_밀린_사람_차례로_부른다():
    줄 = lambda ms: check_pool(ms, BANDS, False, need_note=False)[1]  # noqa: E731  (정원 경고는 full 일 때만이라 역전 줄만 남는다)
    M = lambda i, s, g: EvMember(i, f"p{i}", Decimal(s), g, 0)  # noqa: E731
    # 97 C · 94 C 는 91 B · 90.5 A 에게 모두 밀림 — 부르는 사람은 더 높은 등급(A) · 91 B 도 90.5 A 에게 밀림
    assert 줄([M(1, "97", "C"), M(2, "94", "C"), M(3, "91", "B"), M(4, "90.5", "A"), M(5, "85", "C"), M(6, "80", "C")]) == [
        "p1 님(총점 97점)이 총점이 더 낮은 p4 님(90.5점)보다 등급이 낮습니다.",
        "p2 님(총점 94점)이 총점이 더 낮은 p4 님(90.5점)보다 등급이 낮습니다.",
        "p3 님(총점 91점)이 총점이 더 낮은 p4 님(90.5점)보다 등급이 낮습니다.",
    ]
    # 등급이 같으면 점수가 높은 사람, 점수까지 같으면 명단 차례가 앞선 사람
    assert 줄([M(1, "97", "C"), M(2, "91", "B"), M(3, "92", "B")]) == ["p1 님(총점 97점)이 총점이 더 낮은 p3 님(92점)보다 등급이 낮습니다."]
    assert 줄([M(1, "97", "C"), M(2, "92", "B"), M(3, "92", "B")]) == ["p1 님(총점 97점)이 총점이 더 낮은 p2 님(92점)보다 등급이 낮습니다."]


@check
def 동점_점수없음_연차초과는_역전이_아니다():
    줄 = lambda ms: check_pool(ms, BANDS, False)[:2]  # noqa: E731
    assert 줄([EvMember(1, "가", Decimal("92"), "C", 0), EvMember(2, "나", Decimal("92"), "A", 0)]) == ([], [])  # 동점
    assert 줄([EvMember(1, "가", None, "C", 0), EvMember(2, "나", Decimal("91"), "A", 0)]) == ([], [])  # 점수 없음(KPI 없음)은 따지지 않는다
    assert 줄([EvMember(1, "가", Decimal("91"), "C", 0), EvMember(2, "나", None, "A", 0)]) == ([], [])  # 올려 받은 쪽이 점수 없음이어도 마찬가지
    assert 줄([EvMember(1, "가", Decimal("95"), "C", 10), EvMember(2, "나", Decimal("91"), "B", 0)]) == ([], [])  # 연차 초과라 그 등급을 받을 수 없었다


@check
def 정원_검사가_비율과_늘린_정원을_쓴다():
    R = [EvMember(i, f"p{i}", Decimal("95") - i, "A" if i < 3 else "C", 0) for i in range(1, 11)]  # 10명 — A 2명
    assert check_pool(R, BANDS, False, ratios=(20, 10, 10))[:2] == ([], [])  # S·A 20% → A 2명까지
    assert check_pool(R, BANDS, False)[0] == ["A는 1명까지입니다. 현재 2명"]  # 기본 비율은 1명
    assert check_pool(R, BANDS, False, extra=(1, 0))[:2] == ([], [])  # 늘려 주면 기본 비율도 통과


@check
def 묶음_상태():
    assert pool_state([], "DIRECT", False) == "WAIT"
    assert pool_state(["GRADE", "GRADE"], "DIRECT", False) == "CEO" and pool_state(["GRADE", "GRADE"], "DIV:3", False) == "WAIT"
    assert pool_state(["CEO", "CEO"], "DIV:3", True) == "FIX" and pool_state(["HR", "HR"], "LEADERS", False) == "HR" and pool_state(["DONE"], "LEADERS", False) == "DONE"


@check
def 묶음_열쇠():  # 팀장 · 직속 팀 · '임원'(본부 없음) · 본부 팀원 · 본부 바로 아래
    assert pool_key(Target(1, 11, 1, Role.TEAM_LEADER)) == "LEADERS" and pool_key(Target(2, 9, None, direct=True)) == "DIRECT"
    assert pool_key(Target(3, None, None)) == "DIRECT" and pool_key(Target(4, 11, 1)) == "DIV:1" and pool_key(Target(5, None, 1)) == "DIV:1"


@check
def 묶음_참조와_단계():
    ids = frozenset({4, 5})
    assert pool_ref("DIV:12", ids) == PoolRef(Pool.DIVISION, ids, division_id=12) and pool_ref("LEADERS", ids) == PoolRef(Pool.LEADERS, ids)
    assert pool_ref("DIRECT", ids) == PoolRef(Pool.DIRECT, ids) and pool_ref("DIRECT", ids).division_id is None
    assert [after_score(k) for k in ("LEADERS", "DIRECT", "DIV:1")] == ["CEO", "GRADE", "GRADE"] and assign_stage("LEADERS") == "CEO" and assign_stage("DIV:1") == "GRADE"
    assert [ready_stage(k) for k in ("LEADERS", "DIRECT", "DIV:1")] == ["CEO", "GRADE", "CEO"]
    assert at("SCORE", "CEO") < 0 and at("CEO", "CEO") == 0 and at("HR", "CEO") > 0 and at("DONE", "GRADE") > 0  # 대표이사 확인 다음이 HR 확정
    assert (ORDER, GRADES, BANDS, LEAVE_LIMIT_X2, BONUS_MAX_X2, TOTAL_MAX) == (("SELF", "SCORE", "GRADE", "CEO", "HR", "DONE"), ("S", "A", "B", "C", "D"), (90, 80, 70), 10, 20, Decimal("110"))
    assert (PCT, APPEAL_DAYS, FILE_MAX, FILES_PER_ITEM) == ({"S": 110, "A": 100, "B": 90, "C": 80, "D": 70}, 7, 10 * 1024 * 1024, 5)
    assert TEXT_MAX == {"note": 500, "comment": 500, "head": 500, "bonusReason": 100, "out": 100, "back": 200, "appeal": 500, "answer": 500}


@check
def 보는_수준():  # 팀장 21(팀 11) · 팀원 22 · 본부장 20(본부 1) · 대표 6 · HR 7 · 다른 팀장 31(팀 12)
    T = Target(22, 11, 1)
    assert level(Actor(7, None, True), T, rater=21, assigner=20, stage="GRADE", released=False) == "full"
    assert level(Actor(20, Role.DIVISION_HEAD, led_division_id=1), T, rater=21, assigner=20, stage="GRADE", released=False) == "full"
    assert level(Actor(21, Role.TEAM_LEADER, led_team_id=11), T, rater=21, assigner=20, stage="SCORE", released=False) == "full"
    assert level(Actor(21, Role.TEAM_LEADER, led_team_id=11), T, rater=21, assigner=20, stage="GRADE", released=False) == "mine"
    assert level(Actor(22, None), T, rater=21, assigner=20, stage="DONE", released=False) == "self"
    assert level(Actor(22, None), T, rater=21, assigner=20, stage="DONE", released=True) == "full"
    assert level(Actor(31, Role.TEAM_LEADER, led_team_id=12), T, rater=21, assigner=20, stage="DONE", released=True) == "none"
    # 팀장을 평가한 본부장: 전사 팀장 묶음이라 배정자가 아니다 → 공개 전 "mine"
    L = Target(21, 11, 1, Role.TEAM_LEADER)
    assert level(Actor(20, Role.DIVISION_HEAD, led_division_id=1), L, rater=20, assigner=6, stage="HR", released=False) == "mine"
    # 대표이사는 늘 full · 평가자가 아닌 같은 팀 팀장은 공개된 뒤에만 본다
    assert level(Actor(6, Role.CEO), T, rater=21, assigner=20, stage="SCORE", released=False) == "full"
    assert level(Actor(21, Role.TEAM_LEADER, led_team_id=11), T, rater=None, assigner=None, stage="DONE", released=False) == "none"
    assert level(Actor(21, Role.TEAM_LEADER, led_team_id=11), T, rater=None, assigner=None, stage="DONE", released=True) == "full"


@check
def 고친_내용():
    assert edited("B", 880, "C", Decimal("90.0"), past=True) == "총점 88.0 → 90.0 · 등급 B → C"
    assert edited("B", 880, "B", Decimal("88.0"), past=True) == "" and edited("B", 880, "C", Decimal("88.0"), past=False) == ""
    assert edited("B", 880, "B", Decimal("90.5"), past=True) == "총점 88.0 → 90.5" and edited("B", None, "A", None, past=True) == "등급 B → A"
    assert edited(None, 880, "C", Decimal("90.0"), past=True) == ""  # 평가자가 낸 것이 없으면 비교할 게 없다
    assert edited("B", None, None, None, past=True) == "등급 B → 없음"  # 지금 등급이 없으면 'None' 이 아니라 '없음'


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
