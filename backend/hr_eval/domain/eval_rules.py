"""평가 규칙 (순수 함수) — 총점 · 점수 기준 · 연차 · 정원 검사 · 묶음 상태 · 보는 수준 · 고친 내용.

서버가 연말 평가(자기평가 → 점수 → 등급 배정 → 대표이사 확인 → HR 확정)를 다룰 때 화면(public/index.html 의
evScore · evBand · evCap · evValidate · evPoolState · evEdited)과 같은 규칙을 쓰도록 따로 떼어놨다. 입출력(DB · 시계)은 없다.

- 총점 = 가중치 × 등급 비율(S110 · A100 · B90 · C80 · D70) 합 + 가점, 소수 첫째 자리 사사오입, 상한 110.
- 점수 기준은 "초과"다: 100 초과 S · 90 초과 A · 80 초과 B · 70 초과 C · 그 밖 D. 90.5점은 A, 딱 90점은 B.
- 연차 초과가 5일(x2 단위 10) 이상이면 S·A·B 를 못 준다. 반일이 있어 연차와 가점은 2배 숫자(x2)로 다룬다.
- 점수가 없는 사람(KPI 없는 현장직)은 점수 기준을 따지지 않고 연차 규칙만 본다.
- 묶음 열쇠: 전사 팀장 "LEADERS" · 대표이사 직속부서 팀원 "DIRECT" · 본부 팀원 "DIV:본부번호".
- 정원 검사 문구에는 이름 · 총점 · 등급이 든다. 이 결과는 배정자 · 대표이사 · HR 에게만 보낸다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from hr_eval.domain.access import Act, Pool, PoolRef, Target, can
from hr_eval.domain.models import Actor, Role
from hr_eval.domain.quota import QuotaMode, calculate_department_quota

GRADES = ("S", "A", "B", "C", "D")  # 앞이 높은 등급
PCT = {"S": 110, "A": 100, "B": 90, "C": 80, "D": 70}  # 등급별 달성 비율(%)
BANDS = (90, 80, 70)  # A · B · C 를 받으려면 '넘어야' 하는 점수 (HR 이 바꿀 수 있다). S 는 100 초과
LEAVE_LIMIT_X2 = 10  # 연차 초과 5일(반일 단위 x2)
BONUS_MAX_X2 = 20  # 가점 상한 10점(x2)
TOTAL_MAX = Decimal("110")

#: 묶음마다 거치는 단계는 다르지만 앞뒤 순서는 같다.
ORDER = ("SELF", "SCORE", "GRADE", "CEO", "HR", "DONE")
APPEAL_DAYS = 7  # 결과 공개 뒤 이의 신청을 받는 날수

#: 글자 수 한도 (서버 · 화면 · DB 같게).
TEXT_MAX = {"note": 500, "comment": 500, "head": 500, "bonusReason": 100, "out": 100, "back": 200, "appeal": 500, "answer": 500}
FILE_MAX = 10 * 1024 * 1024  # 증빙 파일 하나의 크기
FILES_PER_ITEM = 5  # 실행목표 하나에 올릴 수 있는 증빙 수


def total(rows: Sequence[tuple[object, str | None]], bonus_x2: int) -> Decimal:
    """총점. rows 는 (가중치, 등급) 줄 — 등급이 없는 줄은 0점. bonus_x2 는 가점의 2배."""
    base = sum((Decimal(str(w)) * PCT[g] / 100 for w, g in rows if g), Decimal(0))
    return min((base + Decimal(bonus_x2) / 2).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP), TOTAL_MAX)


def band(score: Decimal, bands: tuple[int, int, int]) -> str:
    """그 총점으로 받을 수 있는 가장 높은 등급 (기준을 넘어야 한다)."""
    a, b, c = bands
    return "S" if score > 100 else "A" if score > a else "B" if score > b else "C" if score > c else "D"


def above(g: str, cap: str) -> bool:
    """g 가 cap 보다 높은 등급인가."""
    return GRADES.index(g) < GRADES.index(cap)


def cap(score: Decimal | None, leave_x2: int, bands: tuple[int, int, int]) -> str:
    """이 사람에게 줄 수 있는 가장 높은 등급 = 점수 기준과 연차 규칙 중 낮은 쪽. 점수가 없으면 연차 규칙만."""
    leave = "C" if leave_x2 >= LEAVE_LIMIT_X2 else "S"
    top = "S" if score is None else band(score, bands)
    return leave if above(top, leave) else top


def pool_key(t: Target) -> str:
    """등급 비율을 함께 쓰는 묶음의 열쇠."""
    if t.position is Role.TEAM_LEADER:
        return "LEADERS"
    if t.direct or t.division_id is None:
        return "DIRECT"
    return f"DIV:{t.division_id}"


def pool_ref(key: str, member_ids: frozenset[int]) -> PoolRef:
    """access.can 에 넘길 묶음."""
    if key == "LEADERS":
        return PoolRef(Pool.LEADERS, member_ids)
    if key == "DIRECT":
        return PoolRef(Pool.DIRECT, member_ids)
    return PoolRef(Pool.DIVISION, member_ids, division_id=int(key.partition(":")[2]))


def assign_stage(key: str) -> str:
    """등급을 정하는(고치는) 단계: 전사 팀장 = 대표이사 확인, 그 밖 = 본부장(직속부서는 대표이사) 배정."""
    return "CEO" if key == "LEADERS" else "GRADE"


def after_score(key: str) -> str:
    """평가자가 점수를 낸 다음 단계."""
    return assign_stage(key)


def ready_stage(key: str) -> str:
    """대표이사 확인을 기다리는 단계. 직속부서 팀원은 대표이사가 등급을 정하면서 바로 확인한다."""
    return "GRADE" if key == "DIRECT" else "CEO"


def at(stage: str, ref: str) -> int:
    """0 = 그 단계, 음수 = 아직 앞 단계, 양수 = 이미 지났다."""
    return ORDER.index(stage) - ORDER.index(ref)


@dataclass(frozen=True)
class Member:
    """정원 검사에 넣는 묶음의 한 사람. score 가 None 이면 KPI 없는 사람(점수 기준 없음)."""

    uid: int
    name: str
    score: Decimal | None
    grade: str | None
    leave_x2: int


def _num(x: Decimal) -> str:
    """화면의 숫자 글자처럼: 99.0 → 99, 88.5 → 88.5."""
    return format(x.normalize(), "f")


def _band_text(g: str, bands: tuple[int, int, int]) -> str:
    return "100점 초과만" if g == "S" else f"{g}는 {bands['ABC'.index(g)]}점 초과"


def check_pool(members: Sequence[Member], bands: tuple[int, int, int], full: bool) -> tuple[list[str], list[str], dict[str, int]]:
    """묶음의 등급 배정 검사 → (오류, 경고, 등급별 인원). 문구와 순서는 화면 evValidate 그대로.

    full 이 거짓이면(배정하는 중) 등급 누락 · 정원 미달은 따지지 않는다. 마감 · 확정은 full.
    정원은 묶음 인원(len(members))으로 정한다."""
    errors: list[str] = []
    warnings: list[str] = []
    cnt = dict.fromkeys(GRADES, 0)
    for m in members:
        g = m.grade
        if not g:
            if full:
                errors.append(f"{m.name} 님의 등급이 없습니다.")
            continue
        cnt[g] += 1
        if m.score is not None and above(g, band(m.score, bands)):
            errors.append(f"{m.name} 님은 총점 {_num(m.score)}점이라 {g}를 줄 수 없습니다. ({_band_text(g, bands)})")  # 점수 기준보다 높은 등급은 못 준다
        if m.leave_x2 >= LEAVE_LIMIT_X2 and g in "SAB":
            errors.append(f"{m.name} 님은 연차 초과 {LEAVE_LIMIT_X2 // 2}일 이상이라 S·A·B를 줄 수 없습니다.")
    quota = calculate_department_quota(len(members))
    k = cnt["S"]
    if k > quota.cap_s:
        errors.append(f"S는 최대 {quota.cap_s}명입니다. (현재 {k}명)")
    if quota.mode is QuotaMode.INDIVIDUAL:
        qa = quota.a - k  # S 가 K명이면 A 정원에서 K를 뺀다
        if cnt["A"] > qa:
            errors.append(f"A는 {qa}명까지입니다{f' (S {k}명만큼 줄어듦)' if k else ''}. 현재 {cnt['A']}명")
        if cnt["B"] > quota.b:
            errors.append(f"B는 {quota.b}명까지입니다. 현재 {cnt['B']}명")
        if cnt["D"] > quota.d:
            errors.append(f"D는 {quota.d}명까지입니다. 현재 {cnt['D']}명")
        if full:
            if cnt["A"] < qa:
                warnings.append(f"A가 정원보다 {qa - cnt['A']}명 적습니다.")
            if cnt["B"] < quota.b:
                warnings.append(f"B가 정원보다 {quota.b - cnt['B']}명 적습니다.")
            if cnt["D"] < quota.d:
                warnings.append(f"D가 정원보다 {quota.d - cnt['D']}명 적습니다.")
    else:
        up = quota.upper - k
        if cnt["A"] + cnt["B"] > up:
            errors.append(f"A·B 합계는 {up}명까지입니다. 현재 {cnt['A'] + cnt['B']}명")
        if full and cnt["A"] + cnt["B"] < up:
            warnings.append(f"A·B가 정원보다 {up - cnt['A'] - cnt['B']}명 적습니다.")
    return errors, warnings, cnt


def pool_state(stages: Sequence[str], key: str, has_errors: bool) -> str:
    """묶음이 어디까지 왔나 (평가에서 제외된 사람은 뺀 단계들로):
    DONE 확정 · HR 확정 대기 · WAIT 아직 진행 중(빈 묶음 포함) · FIX 등급을 정하거나 비율을 맞춰야 함 · CEO 대표이사 확인 대기."""
    if not stages:
        return "WAIT"
    if all(s == "DONE" for s in stages):
        return "DONE"
    if all(s == "HR" for s in stages):
        return "HR"
    if any(s != ready_stage(key) for s in stages):
        return "WAIT"
    return "FIX" if has_errors else "CEO"


def level(me: Actor, t: Target, *, rater: int | None, assigner: int | None, stage: str, released: bool) -> str:
    """이 사람의 평가를 얼마나 보나: full 전부 · mine 평가자(자기가 낸 것만) · self 본인(자기평가만) · none 못 본다.

    assigner 는 등급을 배정하는 사람 — 대표이사가 하는 묶음이면 None 이 아니라 대표이사 번호를 넘긴다."""
    if me.is_hr or me.position is Role.CEO:
        return "full"
    if assigner is not None and assigner == me.user_id:
        return "full"
    if rater is not None and rater == me.user_id:
        return "full" if released or at(stage, "SCORE") <= 0 else "mine"  # 점수를 내기 전이나 공개된 뒤에는 전부, 그 사이에는 자기가 낸 것만
    if t.user_id == me.user_id:
        return "full" if released else "self"
    if released and can(me, Act.VIEW_RESULT, t):
        return "full"
    return "none"


def edited(proposed: str | None, sub_score_x10: int | None, grade: str | None, score: Decimal | None, *, past: bool) -> str:
    """평가자가 낸 것과 지금이 다르면 "총점 88.0 → 90.0 · 등급 B → C" (지금 등급이 없으면 "등급 B → 없음"). 같거나 아직 배정 단계를 안 지났으면(past 거짓) 빈 글자.

    proposed 는 평가자가 낸 등급 의견, sub_score_x10 은 낸 점수의 10배(880 = 88.0)."""
    if not past or not proposed:
        return ""
    parts = []
    if sub_score_x10 is not None and score is not None and score * 10 != sub_score_x10:
        parts.append(f"총점 {Decimal(sub_score_x10) / 10:.1f} → {score:.1f}")
    if grade != proposed:
        parts.append(f"등급 {proposed} → {grade or '없음'}")  # 등급을 지웠으면(연차 초과 등) 'None' 이 아니라 '없음'
    return " · ".join(parts)
