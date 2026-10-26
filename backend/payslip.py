"""급여명세서 (2026-10-20 인사담당자 — 급여명세서 실제화). 사람별 명세서 내용(머리 칸·금액)만 서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다
(사원코드 users.employee_no 는 잠그지 않는다) — 서버는 NAS 를 읽지 않는다.

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

  GET  /api/payslips                내 명세서 — 공개(OPEN)된 달만, 최근 달이 위. 열쇠가 없으면 빈 목록
  GET  /api/payslips?user=<id>      그 직원의 명세서 (HR) — 공개 전(DRAFT)도 포함. 남의 것을 보면 감사로그 PAYSLIP_VIEW
  GET  /api/payslips/months         올린 달 목록 + 서버에 열쇠가 있는지(ready) + 급여 문의 받는 사람(askTo) (HR)
  POST /api/payslips/ask-to         {"userId": 직원 번호 | null} 급여 문의 받는 사람 1명 (HR, 2026-10-26). 활성 HR 만 · null 이면 지움(HR 전원이 받음)
  POST /api/payslips/<ym>           {"payDate", "slips": [{"userId", "code", …명세서}]} 한 달치 올리기 (HR). 공개 중인 달은 409
  POST /api/payslips/<ym>/open      공개 — 직원 본인에게 보임 (HR)
  POST /api/payslips/<ym>/close     공개 내리기 (HR). 다시 올리려면 내렸다가 올린다
  POST /api/payslips/<ym>/seen      본인이 열어 봄 — 직원 메뉴의 "새 명세서" 숫자 (본인, 공개된 것만)

명세서 한 장 = {"head": {code, name, dept, title, job}, "pay": [[항목, 원]], "ded": [[항목, 원]], "gross", "deduct", "net"}.
원은 정수(정산 환급은 음수), 순서는 급여대장 칸 순서. 잠근 글 = 12바이트 nonce + AES-GCM(내용, aad="달|직원") —
줄을 다른 직원·다른 달 자리로 옮겨 붙여도 풀리지 않는다. 감사로그에는 달 · 인원 수만 남기고 금액·이름은 남기지 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import date

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Blueprint, Response, g, jsonify, request

import auth
import push
from hr_eval.domain.models import AuditEntry

bp = Blueprint("payslip", __name__)
logger = logging.getLogger(__name__)

KEY_ENV = "HR_PAY_KEY"
YM = re.compile(r"20[0-9][0-9]-(0[1-9]|1[0-2])")  # [0-9]: \d 는 다른 나라 숫자(٢ 등)도 받아 DB 검사에서 500
DATE = re.compile(r"20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]")
CODE = re.compile(r"[0-9A-Za-z-]{1,20}")
SLIPS_MAX = 2000  # 한 번에
BODY_MAX = 2_000_000  # 본문 (nginx 는 5MB)
ITEMS_MAX = 40  # 지급·공제 각각
WON_MAX = 1_000_000_000
HEAD_KEYS = ("code", "name", "dept", "title", "job")
BODY_KEYS = ("head", "pay", "ded", "gross", "deduct", "net")
SLIP_KEYS = {"userId", "code", *BODY_KEYS}

MSG_NO_KEY = "서버에 명세서 잠금 열쇠(HR_PAY_KEY)가 없어 올릴 수 없습니다. 서버 설정을 먼저 마쳐 주세요."
MSG_OPEN = "공개 중인 달입니다. 공개를 내린 뒤 다시 올려 주세요."
MSG_SHAPE = "명세서 내용의 모양이 올바르지 않습니다"


def pay_key() -> bytes | None:
    """HR_PAY_KEY(base64 → 32바이트). 요청마다 읽는다 — 서버 설정을 바꾸고 다시 띄우면 바로 쓴다. 없거나 모양이 틀리면 None."""
    try:
        k = base64.b64decode(os.environ.get(KEY_ENV, "").strip(), validate=True)
    except ValueError:
        return None
    return k if len(k) == 32 else None


def seal(key: bytes, ym: str, user_id: int, slip: dict) -> bytes:
    """명세서 한 장을 잠근다. aad 에 달 · 직원을 묶어 두 값이 다르면 풀리지 않는다."""
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, json.dumps(slip, ensure_ascii=False).encode(), f"{ym}|{user_id}".encode())


def unseal(key: bytes, ym: str, user_id: int, blob: bytes) -> dict:
    """seal 의 반대. 열쇠·달·직원이 다르거나 글이 바뀌었으면 InvalidTag."""
    return json.loads(AESGCM(key).decrypt(blob[:12], blob[12:], f"{ym}|{user_id}".encode()))


def _won(v: object) -> bool:
    return type(v) is int and abs(v) <= WON_MAX  # 정수만 (참/거짓·실수·글자 안 됨)


def _items_error(items: object, label: str) -> str | None:
    if not isinstance(items, list) or len(items) > ITEMS_MAX:
        return f"{label} 항목이 목록이 아니거나 {ITEMS_MAX}개를 넘습니다"
    for it in items:
        if not (isinstance(it, list) and len(it) == 2 and isinstance(it[0], str) and 1 <= len(it[0]) <= 30 and _won(it[1])):
            return f"{label} 항목 모양이 틀립니다 (이름 1~30자 · 금액 정수)"
    return None


def slip_error(s: object) -> str | None:
    """올린 명세서 한 줄({"userId", "code", …명세서})의 모양·합계 검사. 문제가 없으면 None, 있으면 이유."""
    if not isinstance(s, dict) or set(s) != SLIP_KEYS:
        return "칸이 맞지 않습니다"
    if type(s["userId"]) is not int or s["userId"] < 1:
        return "직원 번호가 올바르지 않습니다"
    if not isinstance(s["code"], str) or not CODE.fullmatch(s["code"]):
        return "사원코드 모양이 올바르지 않습니다"
    head = s["head"]
    if not isinstance(head, dict) or set(head) != set(HEAD_KEYS) or not all(isinstance(v, str) and len(v) <= 40 for v in head.values()):
        return "위쪽 칸(사원코드·사원명·부서·직급·직종)이 올바르지 않습니다"
    if (why := _items_error(s["pay"], "지급") or _items_error(s["ded"], "공제")) is not None:
        return why
    if not all(_won(s[k]) for k in ("gross", "deduct", "net")):
        return "지급액계·공제액계·차인지급액이 정수가 아닙니다"
    if sum(v for _, v in s["pay"]) != s["gross"]:
        return "지급 항목의 합이 지급액계와 다릅니다"
    if sum(v for _, v in s["ded"]) != s["deduct"]:
        return "공제 항목의 합이 공제액계와 다릅니다"
    if s["gross"] - s["deduct"] != s["net"]:
        return "지급액계 − 공제액계가 차인지급액과 다릅니다"
    return None


def _real_date(v: object) -> bool:
    if not (isinstance(v, str) and DATE.fullmatch(v)):
        return False
    try:
        return date.fromisoformat(v).isoformat() == v  # 2026-02-30 같은 없는 날은 ValueError
    except ValueError:
        return False


def _shape(name: str, why: str) -> str:
    return f"{MSG_SHAPE}: {name} {why}"


def _bad(name: str, why: str) -> tuple[Response, int]:
    return jsonify(error=_shape(name, why)), 400


def _who(s: object, i: int) -> str:
    h = s.get("head") if isinstance(s, dict) else None
    n = h.get("name") if isinstance(h, dict) else None
    return n[:40] if isinstance(n, str) and n else f"{i + 1}번째 명세서"


def _body() -> dict:
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def _slips_of(conn, uid: int, key: bytes, *, drafts: bool) -> list[dict]:
    """그 직원의 명세서를 풀어 최근 달부터. drafts 가 거짓이면 공개된 달만. 풀리지 않는 줄은 건너뛴다(열쇠가 바뀐 경우 등)."""
    out = []
    for ym, pay_date, status, revised, data, seen in conn.execute(
        "SELECT m.ym, m.pay_date, m.status, m.revised, p.data, p.seen_at FROM payslips p JOIN payslip_months m ON m.ym = p.ym"
        " WHERE p.user_id = ?" + ("" if drafts else " AND m.status = 'OPEN'") + " ORDER BY m.ym DESC",
        (uid,),
    ):
        try:
            slip = unseal(key, ym, uid, data)
        except (InvalidTag, ValueError):
            logger.error("명세서를 풀 수 없음: 달 %s 직원 번호 %s — HR_PAY_KEY 가 바뀌었는지 확인", ym, uid)
            continue
        row = {"ym": ym, "payDate": pay_date, "revised": bool(revised), "seen": seen is not None, **slip}
        if drafts:
            row["status"] = status
        out.append(row)
    return out


@bp.get("/api/payslips")
def slips_get() -> Response:
    """본인 것(공개된 달만). ?user=<id> 는 HR — 남의 것이면 공개 전 달까지 보이고 감사로그 PAYSLIP_VIEW."""
    conn, me, arg = auth._db(), g.actor.user_id, request.args.get("user")
    uid = me
    if arg is not None:
        if (denied := auth._hr_only()) is not None:
            return denied
        if not (arg.isascii() and arg.isdigit() and len(arg) <= 9) or not conn.execute("SELECT 1 FROM users WHERE id = ?", (int(arg),)).fetchone():
            return jsonify(error="직원을 찾을 수 없습니다."), 404
        uid = int(arg)
    other = uid != me  # 본인 번호면 ?user= 가 없는 것과 같다
    key = pay_key()
    slips = _slips_of(conn, uid, key, drafts=other) if key else []
    if other:
        auth._audit(conn, AuditEntry.by(g.actor, action="PAYSLIP_VIEW", entity_type="user", entity_id=uid, after_data={"months": len(slips)}))
    return jsonify(slips=slips)


@bp.get("/api/payslips/months")
def months_get() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    rows = auth._db().execute(
        "SELECT ym, pay_date, status, people, revised, uploaded_at, opened_at FROM payslip_months ORDER BY ym DESC")
    return jsonify(ready=pay_key() is not None, askTo=ask_to(auth._db()), months=[
        {"ym": r[0], "payDate": r[1], "status": r[2], "people": r[3], "revised": bool(r[4]), "uploadedAt": r[5], "openedAt": r[6]} for r in rows])


def ask_to(conn) -> int | None:
    """급여 문의 받는 사람(pay_ask_to) — 지금 활성 HR 일 때만 그 직원 번호. 아니면(안 정함 · 비활성 · HR 해제) None = 활성 HR 전원이 받는다 (paypin.lock_get)."""
    row = conn.execute(
        "SELECT a.user_id FROM pay_ask_to a JOIN users u ON u.id = a.user_id"
        " WHERE u.is_active = 1 AND EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id AND r.role = 'HR_ADMIN')").fetchone()
    return row[0] if row else None


@bp.post("/api/payslips/ask-to")
def ask_to_set() -> Response:
    """급여 문의 받는 사람 1명을 정한다 (HR). 고를 수 있는 사람은 활성 HR 만, null 이면 지운다. 바뀌었을 때만 감사로그 PAY_ASK_TO_SET."""
    if (denied := auth._hr_only()) is not None:
        return denied
    b, conn = _body(), auth._db()
    uid = b.get("userId")
    if "userId" not in b or not (uid is None or (type(uid) is int and 0 < uid < 2**31)):  # type: bool 은 int 의 한 갈래라 isinstance 로는 True 가 1 로 들어온다
        return jsonify(error="받는 사람을 골라 주세요."), 400
    conn.execute("BEGIN IMMEDIATE")
    try:
        if uid is not None and not conn.execute(
                "SELECT 1 FROM users u WHERE u.id = ? AND u.is_active = 1"
                " AND EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id AND r.role = 'HR_ADMIN')", (uid,)).fetchone():
            conn.execute("ROLLBACK")
            return jsonify(error="HR 권한이 있는 직원만 고를 수 있어요."), 400
        row = conn.execute("SELECT user_id FROM pay_ask_to").fetchone()
        was = row[0] if row else None
        if was != uid:
            if uid is None:
                conn.execute("DELETE FROM pay_ask_to")
            else:
                conn.execute("INSERT INTO pay_ask_to (id, user_id, updated_at, updated_by) VALUES (1, ?, ?, ?)"
                             " ON CONFLICT(id) DO UPDATE SET user_id = excluded.user_id, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                             (uid, auth._iso(auth._now()), g.actor.user_id))
            auth._audit(conn, AuditEntry.by(g.actor, action="PAY_ASK_TO_SET", entity_type="user", entity_id=uid,
                                            before_data={"userId": was}, after_data={"userId": uid}))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(askTo=uid)


def remember_codes(conn, pairs: list[tuple[int, str]]) -> str | None:
    """(직원 번호, 사원코드) 쌍을 기억한다 — 코드가 비어 있던 직원에게 붙인다(users.employee_no). 다른 직원에게 붙은 코드면 안내 문구를 돌려준다(그때는 아무것도 붙이지 않음).
    직원 번호가 명단에 있는지는 부르는 쪽이 먼저 확인한다. 급여명세서 · 연봉계약서가 같은 규칙을 쓴다."""
    no = {r[0]: r[1] for r in conn.execute("SELECT id, employee_no FROM users")}
    holder = {c: uid for uid, c in no.items() if c}
    remember = []  # 사원코드가 비어 있던 직원에게 기억할 것
    for uid, code in pairs:
        if holder.get(code, uid) != uid:
            return f"사원코드 {code}이(가) 이미 다른 직원에게 붙어 있습니다. 전에 직원을 잘못 골랐다면 구성원 설정에서 그 직원의 사원코드를 지운 뒤 다시 올려 주세요."
        if code not in holder and no[uid] is None:
            holder[code], no[uid] = uid, code
            remember.append((code, uid))
    conn.executemany("UPDATE users SET employee_no = ? WHERE id = ?", remember)
    return None


def _store(conn, ym: str, pay_date: str, plan: list[tuple[int, str, dict]], key: bytes) -> tuple[int, str] | None:
    """한 트랜잭션 안에서: 공개 중이면 409 → 직원·사원코드 확인 → 그 달을 지우고 다시 넣기 → 달 줄 → 감사로그. 문제가 있으면 (상태, 문구)."""
    row = conn.execute("SELECT status FROM payslip_months WHERE ym = ?", (ym,)).fetchone()
    if row and row[0] == "OPEN":
        return 409, MSG_OPEN
    known = {r[0] for r in conn.execute("SELECT id FROM users")}
    for uid, _, slip in plan:
        if uid not in known:
            return 400, _shape(slip["head"]["name"], "포털에 없는 직원입니다")
    if (why := remember_codes(conn, [(uid, code) for uid, code, _ in plan])) is not None:
        return 400, why
    conn.execute(
        "INSERT INTO payslip_months (ym, pay_date, status, people, uploaded_at, uploaded_by) VALUES (?, ?, 'DRAFT', ?, ?, ?)"
        " ON CONFLICT (ym) DO UPDATE SET pay_date = excluded.pay_date, status = 'DRAFT', people = excluded.people,"
        " revised = opened_at IS NOT NULL, uploaded_at = excluded.uploaded_at, uploaded_by = excluded.uploaded_by",  # 공개했던 적이 있으면 수정됨
        (ym, pay_date, len(plan), auth._iso(auth._now()), g.actor.user_id))
    conn.execute("DELETE FROM payslips WHERE ym = ?", (ym,))  # 다시 올리면 '봤음'도 새로
    conn.executemany("INSERT INTO payslips (ym, user_id, data) VALUES (?, ?, ?)", [(ym, uid, seal(key, ym, uid, slip)) for uid, _, slip in plan])
    auth._audit(conn, AuditEntry.by(g.actor, action="PAYSLIP_UPLOAD", entity_type="payslip_month", entity_id=None, after_data={"ym": ym, "people": len(plan)}))
    return None


@bp.post("/api/payslips/<ym>")
def upload(ym: str) -> Response:
    """한 달치 올리기. 모양이 틀린 줄이 하나라도 있으면 아무것도 저장하지 않는다."""
    if (denied := auth._hr_only()) is not None:
        return denied
    if not YM.fullmatch(ym):
        return _bad("귀속 월", "모양이 YYYY-MM이 아닙니다")
    if (key := pay_key()) is None:
        return jsonify(error=MSG_NO_KEY), 503
    if len(request.get_data(cache=True)) > BODY_MAX:
        return _bad("올린 내용", "크기가 2MB를 넘습니다")
    b = _body()
    pay_date, slips = b.get("payDate"), b.get("slips")
    if not _real_date(pay_date):
        return _bad("지급일", "모양이 YYYY-MM-DD(실제 있는 날)가 아닙니다")
    if not isinstance(slips, list) or not 0 < len(slips) <= SLIPS_MAX:
        return _bad("명세서", f"장수는 1~{SLIPS_MAX}장이어야 합니다")
    plan, dup = [], set()
    for i, s in enumerate(slips):
        if (why := slip_error(s)) is not None:
            return _bad(_who(s, i), why)
        if s["userId"] in dup:
            return _bad(_who(s, i), "직원이 두 번 들어 있습니다")
        dup.add(s["userId"])
        plan.append((s["userId"], s["code"], {k: s[k] for k in BODY_KEYS}))
    conn = auth._db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        err = _store(conn, ym, pay_date, plan, key)
        conn.execute("ROLLBACK" if err else "COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return (jsonify(error=err[1]), err[0]) if err else jsonify(saved=len(plan))


def _toggle(ym: str, to: str, action: str) -> Response:
    """공개(OPEN) · 내리기(DRAFT). 이미 그 상태면 그대로 두고 감사로그도 더하지 않는다. 공개로 바뀌었으면 COMMIT 뒤에 그 달 명세서가 있는 사람의 휴대폰에 알림(push.notify)."""
    if (denied := auth._hr_only()) is not None:
        return denied
    conn = auth._db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT status, people FROM payslip_months WHERE ym = ?", (ym,)).fetchone()
        changed = bool(row) and row[0] != to
        if changed:
            if to == "OPEN":
                conn.execute("UPDATE payslip_months SET status = 'OPEN', opened_at = ?, opened_by = ? WHERE ym = ?",
                             (auth._iso(auth._now()), g.actor.user_id, ym))
            else:
                conn.execute("UPDATE payslip_months SET status = 'DRAFT' WHERE ym = ?", (ym,))
            auth._audit(conn, AuditEntry.by(g.actor, action=action, entity_type="payslip_month", entity_id=None, after_data={"ym": ym, "people": row[1]}))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if changed and to == "OPEN":  # 저장(COMMIT)이 끝난 뒤 — 그 달 명세서가 있는 사람에게 알림
        push.notify(lambda: [r[0] for r in conn.execute("SELECT user_id FROM payslips WHERE ym = ?", (ym,))], "payslip", month=int(ym[5:7]))
    return jsonify(status=to) if row else (jsonify(error="올린 적 없는 달입니다."), 404)


@bp.post("/api/payslips/<ym>/open")
def open_month(ym: str) -> Response:
    return _toggle(ym, "OPEN", "PAYSLIP_OPEN")


@bp.post("/api/payslips/<ym>/close")
def close_month(ym: str) -> Response:
    return _toggle(ym, "DRAFT", "PAYSLIP_CLOSE")


@bp.post("/api/payslips/<ym>/seen")
def slip_seen(ym: str) -> Response:
    """본인의 공개된 명세서만. 처음 연 때를 남기고 다시 눌러도 그대로."""
    n = auth._db().execute(
        "UPDATE payslips SET seen_at = COALESCE(seen_at, ?) WHERE ym = ? AND user_id = ?"
        " AND EXISTS (SELECT 1 FROM payslip_months m WHERE m.ym = payslips.ym AND m.status = 'OPEN')",
        (auth._iso(auth._now()), ym, g.actor.user_id)).rowcount
    return jsonify(ok=True) if n else (jsonify(error="열 수 있는 명세서가 없습니다."), 404)
