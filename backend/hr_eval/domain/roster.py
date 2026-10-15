"""직원 명단 동기화 — 하이웍스 명단·조직도와 포털을 비교해 무엇을 바꿀지 정한다 (로그인 설계 §11).

순수 함수다. 하이웍스도 DB 도 부르지 않는다. 가져오기·저장은 hr_eval/roster_sync.py.

하이웍스 응답 모양 (Postman 문서 · 2026-10-03 실측):
  GET /hrm/v2/users          → [{"no", "user_id", "name"}, …]          전체 목록, 나눠 주지 않는다
  GET /hrm/v2/organizations  → {"node_id", "name", "entries": [구성원], "nodes": [하위 조직 …]}
                               맨 위가 회사, 그 아래 본부, 그 아래 팀 (2단계)
  구성원: user_no, mail_id, name, email, phone, cell, node_id
  성공 응답에는 code 가 없다. 실패는 HTTP 202 에 {"code": "ERR", "message": …}.

규칙:
  - 있는 사람 = 사용자 목록(/users)에 있는 사람. 소속은 조직도에서 읽는다
  - 새 사람은 올리되 포털 사용은 꺼 둔다(테이블 기본값) — 사람별 설정은 건드리지 않는다
  - 고치는 것은 소속 · 메일 · 전화 · 휴대폰뿐. 비활성인 사람은 손대지 않는다(다시 켜는 것은 HR 만)
  - 사용자 목록에서 사라진 사람은 비활성 (세션 폐기는 저장하는 쪽)
  - 부서가 없는 사람(조직도에 없거나 회사 바로 아래)은 새로 올리지 않는다. 이미 있는 사람은 그대로 둔다
  - 응답이 이상하면(실패 코드 · 빈 목록 · 3단계 이상 · 한꺼번에 많은 비활성) RosterError — 아무것도 바꾸지 않는다
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Sequence

# 하루에 이보다 많은 사람이 사라지면 하이웍스 쪽 문제로 보고 멈춘다.
# ponytail: 고정 상한 — 실제로 많이 퇴사한 날은 --allow-mass 로 한 번 허락한다
MASS_DEACTIVATE_LIMIT = 10


class RosterError(Exception):
    """가져온 명단을 믿을 수 없다. 아무것도 바꾸지 않고 기록만 남긴다."""


@dataclass(frozen=True)
class HwNode:
    node_id: str
    name: str
    parent_id: str | None  # None = 본부 (회사 바로 아래)
    depth: int  # 1 = 본부, 2 = 팀


@dataclass(frozen=True)
class HwPerson:
    user_no: str
    name: str
    email: str
    phone: str
    mobile: str
    node_id: str
    mail_id: str = ""


@dataclass(frozen=True)
class PortalDept:
    id: int
    node_id: str | None  # departments.hiworks_node_id
    name: str
    parent_node_id: str | None
    is_active: bool = True


@dataclass(frozen=True)
class PortalUser:
    id: int
    user_no: str | None  # users.hiworks_user_no
    is_active: bool
    node_id: str | None  # 지금 부서의 hiworks_node_id
    email: str
    phone: str | None = None
    mobile: str | None = None


@dataclass
class SyncPlan:
    dept_add: list[HwNode] = field(default_factory=list)
    dept_update: list[tuple[PortalDept, HwNode]] = field(default_factory=list)
    dept_close: list[PortalDept] = field(default_factory=list)
    user_add: list[HwPerson] = field(default_factory=list)
    user_update: list[tuple[PortalUser, HwPerson, tuple[str, ...]]] = field(default_factory=list)
    user_deactivate: list[PortalUser] = field(default_factory=list)
    notes: Counter = field(default_factory=Counter)  # 올리지 않은 사람 등 — 숫자만

    def counts(self) -> dict[str, int]:
        """화면·로그에 내보내는 요약. 이름·번호·연락처는 넣지 않는다."""
        return {
            "부서 추가": len(self.dept_add),
            "부서 변경": len(self.dept_update),
            "부서 닫기": len(self.dept_close),
            "직원 추가": len(self.user_add),
            "직원 정보 변경": len(self.user_update),
            "직원 비활성": len(self.user_deactivate),
            **dict(self.notes),
        }


def _unwrap(body: object) -> object:
    """성공 응답에는 code 가 없다. code 가 있으면 SUC 이고 data 가 있을 때만 받는다."""
    if isinstance(body, dict) and "code" in body:
        if body.get("code") != "SUC" or "data" not in body:
            raise RosterError(f"하이웍스가 실패 코드를 돌려주었습니다 ({body.get('code')})")
        return body["data"]
    return body


def _s(v: object) -> str:
    return "" if v is None else str(v).strip()


def parse_users(body: object) -> frozenset[str]:
    """사용자 목록 → 하이웍스 사용자 번호들."""
    body = _unwrap(body)
    if not isinstance(body, list):
        raise RosterError("사용자 목록의 모양이 다릅니다")
    nos = frozenset(n for n in (_s(u.get("no")) for u in body if isinstance(u, dict)) if n)
    if not nos:
        raise RosterError("사용자 목록이 비었습니다")
    return nos


def parse_orgs(body: object) -> tuple[list[HwNode], list[HwPerson], frozenset[str]]:
    """조직도 → 부서들(본부·팀), 소속 직원들, 회사 바로 아래에 있는 사람 번호들."""
    root = _unwrap(body)
    if not isinstance(root, dict) or not isinstance(root.get("nodes"), list):
        raise RosterError("조직도의 모양이 다릅니다")
    nodes: list[HwNode] = []
    people: list[HwPerson] = []
    at_root = frozenset(n for n in (_s(e.get("user_no")) for e in root.get("entries") or [] if isinstance(e, dict)) if n)

    def walk(n: object, parent: str | None, depth: int) -> None:
        if depth > 2:
            raise RosterError("조직이 3단계 이상입니다 — 포털은 본부 → 팀 2단계만 받습니다")
        if not isinstance(n, dict):
            raise RosterError("조직도의 모양이 다릅니다")
        nid, name = _s(n.get("node_id")), _s(n.get("name"))
        if not nid or not name:
            raise RosterError("조직 번호나 이름이 비어 있습니다")
        nodes.append(HwNode(nid, name, parent, depth))
        for e in n.get("entries") or []:
            people.append(
                HwPerson(
                    _s(e.get("user_no")), _s(e.get("name")), _s(e.get("email")),
                    _s(e.get("phone")), _s(e.get("cell")), nid, _s(e.get("mail_id")),
                )
            )
        for child in n.get("nodes") or []:
            walk(child, nid, depth + 1)

    for top in root["nodes"]:
        walk(top, None, 1)
    if len({n.node_id for n in nodes}) != len(nodes):
        raise RosterError("같은 조직 번호가 두 번 나옵니다")
    if not people:
        raise RosterError("조직도에 소속 직원이 없습니다")
    return nodes, people, at_root


def plan_sync(
    user_nos: frozenset[str],
    nodes: Sequence[HwNode],
    people: Sequence[HwPerson],
    depts: Sequence[PortalDept],
    users: Sequence[PortalUser],
    *,
    domain: str = "",
    allow_mass: bool = False,
    at_root: frozenset[str] = frozenset(),
) -> SyncPlan:
    plan = SyncPlan()

    # 부서: 하이웍스 조직 번호로 짝짓는다. 이름·위치가 바뀌면 고치고, 사라지면 닫는다(지우지 않는다)
    have = {d.node_id: d for d in depts if d.node_id}
    for n in nodes:
        d = have.get(n.node_id)
        if d is None:
            plan.dept_add.append(n)
        elif (d.name, d.parent_node_id, d.is_active) != (n.name, n.parent_id, True):
            plan.dept_update.append((d, n))
    live = {n.node_id for n in nodes}
    plan.dept_close = [d for d in depts if d.node_id and d.is_active and d.node_id not in live]

    # 직원: 한 사람이 여러 부서에 있으면 조직도에서 먼저 나온 부서
    placed: dict[str, HwPerson] = {}
    for p in people:
        if not p.user_no:
            plan.notes["사용자 번호 없음(올리지 않음)"] += 1
        elif p.user_no in placed:
            plan.notes["여러 부서(먼저 나온 부서로)"] += 1
        else:
            placed[p.user_no] = p

    mine = {u.user_no: u for u in users if u.user_no}
    for no, p in placed.items():
        email = p.email or (f"{p.mail_id}@{domain}" if p.mail_id and domain else "")
        u = mine.get(no)
        if u is None:
            if not email:
                plan.notes["메일 없음(올리지 않음)"] += 1
                continue
            plan.user_add.append(replace(p, email=email))
        elif u.is_active:  # 비활성인 사람은 손대지 않는다
            new = replace(p, email=email or u.email)
            changed = tuple(
                k
                for k, a, b in (
                    ("소속", new.node_id, u.node_id),
                    ("메일", new.email, u.email),
                    ("전화", new.phone or None, u.phone),
                    ("휴대폰", new.mobile or None, u.mobile),
                )
                if a != b
            )
            if changed:
                plan.user_update.append((u, new, changed))

    no_dept = user_nos - placed.keys()  # 회사 바로 아래(at_root)도 부서가 없는 것이다 — 따로 센다
    for key, group in (
        ("회사 바로 아래(올리지 않음)", (no_dept & at_root) - mine.keys()),
        ("부서 없음(올리지 않음)", no_dept - at_root - mine.keys()),
        ("부서 없음(그대로 둠)", no_dept & mine.keys()),
    ):
        if group:
            plan.notes[key] += len(group)
    present = user_nos | placed.keys()
    plan.user_deactivate = [u for u in users if u.user_no and u.is_active and u.user_no not in present]

    if len(plan.user_deactivate) > MASS_DEACTIVATE_LIMIT and not allow_mass:
        raise RosterError(
            f"한 번에 {len(plan.user_deactivate)}명이 사라졌습니다 (상한 {MASS_DEACTIVATE_LIMIT}명). "
            "하이웍스 쪽 문제일 수 있어 멈춥니다. 실제로 퇴사한 것이면 --allow-mass 로 다시 실행하세요"
        )
    return plan
