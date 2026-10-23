"""급여·계약 비밀번호 (2026-10-22 인사담당자 — 급여명세서 · 원천징수영수증 · 연봉계약서 메뉴를 열 때 6자리 숫자 비밀번호).
설계: docs/superpowers/specs/2026-10-22-pay-pin-lock-design.md. 번호는 직원마다 하나, 해시만 pay_pins 에 둔다(원문은 DB · 로그 · 감사로그 어디에도 없다).

app.py 가 bp 를 붙인다. 세션 검사 · 같은 출처 검사는 auth._guard 가 모든 /api/* 에 하고, 로그인 확인 뒤 gate() 를 부른다.

  gate()                PREFIXES 길(급여명세서 · 원천징수영수증 · 연봉계약서, HR 길 포함 · 모든 method)은 풀린 창만 —
                        아니면 423 {"error": MSG_LOCKED, "lock": "set"|"ask"|"locked"}. 쓸 때마다 풀림을 6분(OPEN_FOR)으로 다시 늘린다
  GET  /api/pay/lock    {"state": "set"|"ask"|"open"|"locked", "left": 남은 횟수, "hr": [{"mailNo", "email"}]} — 열쇠가 없으면 {"state": "off"}
  POST /api/pay/lock    이 창 잠그기
  POST /api/pay/pin     {"pin", "again"} 처음 정하기 → 이 창이 풀림 (감사로그 PAY_PIN_SET). 이미 있으면 409
  POST /api/pay/unlock  {"pin"} 확인 → 이 창이 풀림. 연달아 5번(FAILS_MAX) 틀리면 잠김 — 이 사람의 모든 창, HR 초기화로만 푼다 (PAY_PIN_LOCKED)
  POST /api/pay/alive   화면이 쓰는 중이면 부른다 — 풀려 있을 때만 6분을 다시 센다 {"open": bool}
  PREFIXES              잠그는 길의 앞머리 — 이 목록은 이 한 곳에만 둔다
  problem()             쓸 수 없는 번호를 가린다 (6자리 숫자 · 두 번 같게 · 같은 숫자 6개 · 이어지는 숫자 · 생일)
  derive() · pin_hash() · pin_ok()    HR_PAY_KEY 에서 번호 전용 열쇠를 뽑고, 소금과 함께 해시한다
  python -m paypin check            위 규칙 · 해시 자체 검증 (CI)

HR_PAY_KEY 가 없으면 잠금도 없다(gate 통과 · 정하기 · 확인 503) — 그때는 명세서 등도 빈 목록이다.
감사로그에는 정함 · 잠김만 남긴다. 번호 · 틀린 숫자 · 한 번 한 번의 맞힘 · 틀림은 적지 않는다.

해시: pin_hash = HMAC-SHA256(k, salt + pin).hexdigest(), k = HMAC-SHA256(HR_PAY_KEY 32바이트, b"hr-pay-pin").digest().
열쇠가 DB 밖(서버 .env)에 있어서 DB 파일만 새어 나가도 6자리(100만 가지)를 맞혀 볼 수 없다. 비교는 시간차 없이(hmac.compare_digest).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta

from flask import Blueprint, Response, g, jsonify, request

import auth
import payslip
from hr_eval.domain.models import AuditEntry

bp = Blueprint("paypin", __name__)

PREFIXES = ("/api/payslips", "/api/withholding", "/api/contracts")  # 비밀번호 없이는 못 여는 길 — 모든 method
FAILS_MAX = 5  # 연달아 틀리면 잠김
OPEN_FOR = timedelta(seconds=360)  # 풀린 뒤 급여·계약을 안 쓰고 두면 다시 잠기는 시간

MSG_LOCKED = "급여·계약 메뉴는 비밀번호를 넣어야 볼 수 있어요."
MSG_NO_KEY = "서버에 급여 잠금 열쇠(HR_PAY_KEY)가 없어 비밀번호를 쓸 수 없습니다. 서버 설정을 먼저 마쳐 주세요."
MSG_WRONG = "비밀번호가 맞지 않아요."
MSG_SHAPE = "숫자 6자리로 정해 주세요."

PIN = re.compile(r"[0-9]{6}")  # [0-9]: \d 는 전각 숫자 등 다른 나라 숫자도 받는다
_RUN = "0123456789", "9876543210"  # 이어지는 숫자 (9 → 0 으로 넘어가는 것은 치지 않는다)


def problem(pin: object, again: object | None, birthday: str | None) -> str | None:
    """정하려는 번호가 안 되는 까닭(사용자에게 보일 문구), 괜찮으면 None. again 이 None 이면 두 번 비교는 건너뛴다(확인할 때). birthday 는 users.birthday 'MM-DD'."""
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        return MSG_SHAPE
    if again is not None and again != pin:
        return "두 번 입력한 비밀번호가 달라요."
    if len(set(pin)) == 1:
        return "같은 숫자 6개는 쓸 수 없어요."
    if any(pin in run for run in _RUN):
        return "이어지는 숫자는 쓸 수 없어요."
    if isinstance(birthday, str) and re.fullmatch(r"[0-9]{2}-[0-9]{2}", birthday) and birthday[:2] + birthday[3:] in pin:
        return "생일(월일)이 들어간 번호는 쓸 수 없어요."
    return None


def derive(pay_key: bytes) -> bytes:
    """HR_PAY_KEY(32바이트)에서 번호 전용 열쇠를 뽑는다 — 명세서 잠금(AES-GCM)에 쓰는 열쇠를 그대로 쓰지 않는다."""
    return hmac.new(pay_key, b"hr-pay-pin", hashlib.sha256).digest()


def pin_hash(k: bytes, salt: str, pin: str) -> str:
    return hmac.new(k, (salt + pin).encode(), hashlib.sha256).hexdigest()


def pin_ok(k: bytes, salt: str, pin: str, stored: str) -> bool:
    return hmac.compare_digest(pin_hash(k, salt, pin), stored)


# ---------------- 검문 · 길 — 시각은 auth._now() (검사가 시계를 바꿀 수 있게 모듈로 부른다) ----------------
def state_of(conn: sqlite3.Connection, uid: int, token_hash: str, now: datetime) -> str:
    """이 사람 · 이 창의 상태: set(아직 안 정함) · locked(5번 틀림 — 풀림보다 먼저 본다) · open(마지막 사용부터 6분 안) · ask(물어야 함)."""
    row = conn.execute("SELECT locked_at FROM pay_pins WHERE user_id = ?", (uid,)).fetchone()
    if row is None:
        return "set"
    if row[0] is not None:
        return "locked"
    fresh = conn.execute("SELECT 1 FROM sessions WHERE token_hash = ? AND pay_used_at > ?", (token_hash, auth._iso(now - OPEN_FOR))).fetchone()
    return "open" if fresh else "ask"


def _use(conn: sqlite3.Connection, now: datetime) -> str:
    """이 창의 상태. 풀려 있으면 6분을 지금부터 다시 센다. 그사이 잠김 · 초기화로 NULL 이 된 창은 되살리지 않는다(IS NOT NULL)."""
    st = state_of(conn, g.actor.user_id, g.session_hash, now)
    if st == "open":
        conn.execute("UPDATE sessions SET pay_used_at = ? WHERE token_hash = ? AND pay_used_at IS NOT NULL", (auth._iso(now), g.session_hash))
    return st


def _open_here(conn: sqlite3.Connection, now: datetime) -> None:
    conn.execute("UPDATE sessions SET pay_used_at = ? WHERE token_hash = ?", (auth._iso(now), g.session_hash))


def _locked(st: str) -> tuple[Response, int]:
    return jsonify(error=MSG_LOCKED, lock=st), 423


def gate() -> tuple[Response, int] | None:
    """auth._guard 가 로그인 확인 뒤 모든 /api/* 에 부른다. 열쇠가 없으면(잠금 꺼짐) · PREFIXES 밖이면 None, 풀린 창이면 6분을 늘리고 None."""
    if payslip.pay_key() is None or not request.path.startswith(PREFIXES):
        return None
    st = _use(auth._db(), auth._now())
    return None if st == "open" else _locked(st)


@bp.get("/api/pay/lock")
def lock_get() -> Response:
    """화면이 급여·계약 메뉴를 열 때 묻는다. hr = 잊었을 때 초기화를 부탁할 활성 HR 전원 (하이웍스 메일 받는 사람 채우기용)."""
    if payslip.pay_key() is None:
        return jsonify(state="off")
    conn, uid = auth._db(), g.actor.user_id
    row = conn.execute("SELECT fails FROM pay_pins WHERE user_id = ?", (uid,)).fetchone()
    hr = [{"mailNo": r[0] or "", "email": r[1]} for r in conn.execute(
        "SELECT u.hiworks_mail_no, u.email FROM users u JOIN user_roles r ON r.user_id = u.id"
        " WHERE r.role = 'HR_ADMIN' AND u.is_active = 1 ORDER BY u.id")]
    return jsonify(state=state_of(conn, uid, g.session_hash, auth._now()), left=FAILS_MAX - (row[0] if row else 0), hr=hr)


@bp.post("/api/pay/lock")
def lock_now() -> Response:
    """이 창 잠그기 (화면의 잠그기 단추 · 다른 메뉴로 갈 때)."""
    auth._db().execute("UPDATE sessions SET pay_used_at = NULL WHERE token_hash = ?", (g.session_hash,))
    return jsonify(ok=True)


@bp.post("/api/pay/pin")
def pin_set() -> Response:
    """{"pin", "again"} 처음 정하기 — 정하면 이 창이 바로 풀린다. 바꾸려면 HR 초기화 뒤 다시 정한다. 번호 원문은 해시에만 쓰고 버린다."""
    if (key := payslip.pay_key()) is None:
        return jsonify(error=MSG_NO_KEY), 503
    b = request.get_json(silent=True)
    b = b if isinstance(b, dict) else {}
    conn, now, uid = auth._db(), auth._now(), g.actor.user_id
    conn.execute("BEGIN IMMEDIATE")  # 두 창에서 동시에 정해도 한 줄만
    try:
        if conn.execute("SELECT 1 FROM pay_pins WHERE user_id = ?", (uid,)).fetchone():
            conn.execute("ROLLBACK")
            return jsonify(error="이미 비밀번호가 있어요."), 409
        bday = conn.execute("SELECT birthday FROM users WHERE id = ?", (uid,)).fetchone()[0]
        if (why := problem(b.get("pin"), b.get("again") or "", bday)) is not None:  # again 이 없으면 다른 것으로 친다
            conn.execute("ROLLBACK")
            return jsonify(error=why), 400
        salt = secrets.token_hex(16)
        conn.execute("INSERT INTO pay_pins (user_id, pin_hash, salt, set_at) VALUES (?, ?, ?, ?)",
                     (uid, pin_hash(derive(key), salt, b["pin"]), salt, auth._iso(now)))
        _open_here(conn, now)
        auth._audit(conn, AuditEntry.by(g.actor, action="PAY_PIN_SET", entity_type="user", entity_id=uid))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(ok=True)


@bp.post("/api/pay/unlock")
def unlock() -> Response:
    """{"pin"} 확인 — 맞으면 이 창이 풀리고 틀린 횟수는 0. 연달아 FAILS_MAX 번 틀리면 잠김: 이 사람의 모든 창을 잠그고, 맞는 번호도 받지 않는다.
    읽기부터 고치기까지 한 트랜잭션 — 동시에 여러 번 보내도 횟수가 새지 않는다. 정하기 규칙은 보지 않고 모양만 본다(횟수에 넣지 않음)."""
    if (key := payslip.pay_key()) is None:
        return jsonify(error=MSG_NO_KEY), 503
    b = request.get_json(silent=True)
    pin = b.get("pin") if isinstance(b, dict) else None
    if not isinstance(pin, str) or not PIN.fullmatch(pin):  # 글자가 아닌 값(258036 숫자)도 여기서 — pin_ok 는 글자만 받는다
        return jsonify(error=MSG_SHAPE), 400
    conn, now, uid = auth._db(), auth._now(), g.actor.user_id
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT pin_hash, salt, fails, locked_at FROM pay_pins WHERE user_id = ?", (uid,)).fetchone()
        if row is None or row[3] is not None:
            conn.execute("ROLLBACK")
            return _locked("set" if row is None else "locked")
        if pin_ok(derive(key), row[1], pin, row[0]):
            conn.execute("UPDATE pay_pins SET fails = 0 WHERE user_id = ?", (uid,))
            _open_here(conn, now)
            out = jsonify(ok=True)
        elif (fails := row[2] + 1) < FAILS_MAX:
            conn.execute("UPDATE pay_pins SET fails = ? WHERE user_id = ?", (fails, uid))
            out = jsonify(error=MSG_WRONG, left=FAILS_MAX - fails), 400
        else:
            conn.execute("UPDATE pay_pins SET fails = ?, locked_at = ? WHERE user_id = ?", (fails, auth._iso(now), uid))
            conn.execute("UPDATE sessions SET pay_used_at = NULL WHERE user_id = ?", (uid,))  # 이미 풀려 있던 다른 창도 잠근다
            auth._audit(conn, AuditEntry.by(g.actor, action="PAY_PIN_LOCKED", entity_type="user", entity_id=uid))
            out = _locked("locked")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return out


@bp.post("/api/pay/alive")
def alive() -> Response:
    """화면이 급여·계약 화면을 쓰는 중(누르기 · 넘기기)이면 부른다. 풀려 있을 때만 늘리고, 잠긴 창은 풀지 않는다."""
    return jsonify(open=_use(auth._db(), auth._now()) == "open")


def check() -> None:
    assert problem("111111", "111111", None) == "같은 숫자 6개는 쓸 수 없어요."
    for p in ("123456", "654321", "012345", "456789", "987654", "543210"):
        assert problem(p, p, None) == "이어지는 숫자는 쓸 수 없어요.", p
    assert problem("567890", "567890", None) is None  # 9 → 0 으로 넘어가는 것은 이어지는 숫자로 치지 않는다
    assert problem("903141", "903141", "03-14") == "생일(월일)이 들어간 번호는 쓸 수 없어요."
    assert problem("258036", "258037", None) == "두 번 입력한 비밀번호가 달라요."
    for bad in ("25803", "2580367", "25a036", "２５８０３６", 258036, None):
        assert problem(bad, bad, None) == "숫자 6자리로 정해 주세요.", bad
    assert problem("258036", "258036", "03-14") is None and problem("258036", None, None) is None
    assert problem("258036\n", None, None) == "숫자 6자리로 정해 주세요."  # 끝의 줄바꿈도 안 된다 (re.match 의 $ 함정)
    k1, k2 = derive(b"\x01" * 32), derive(b"\x02" * 32)
    h = pin_hash(k1, "ab" * 16, "258036")
    assert pin_ok(k1, "ab" * 16, "258036", h) and not pin_ok(k1, "ab" * 16, "258037", h)
    assert not pin_ok(k2, "ab" * 16, "258036", h)  # 열쇠가 다르면 맞혀 볼 수 없다
    assert pin_hash(k1, "cd" * 16, "258036") != h  # 사람마다 소금
    assert "258036" not in h


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["check"]:
        check()
        print("paypin 검사 통과")
    else:
        sys.exit("사용법: python -m paypin check")
