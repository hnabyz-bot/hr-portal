"""원천징수영수증 (2026-10-21 인사담당자 — 원천징수영수증 실제화). 사람별 요약 숫자(sum)와 영수증 PDF(pdf)를 급여명세서와 같은
서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다 — 열쇠 읽기는 payslip.pay_key. 서버는 NAS 를 읽지 않는다(HR 화면이 zip 을 풀어 보낸다).

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

  GET  /api/withholding                 내 영수증 요약 — 공개(OPEN)된 해만, 최근 해가 위. 열쇠가 없으면 빈 목록
  GET  /api/withholding?user=<id>       그 직원의 요약 (HR) — 공개 전(DRAFT)도. 남의 것을 보면 감사로그 WITHHOLDING_VIEW
  GET  /api/withholding/<year>/pdf      내 영수증 PDF (공개된 해만). ?user=<id> 는 HR — 남의 것이면 감사로그 WITHHOLDING_PDF_VIEW
  GET  /api/withholding/years           올린 해 목록 + 서버에 열쇠가 있는지(ready) (HR)
  POST /api/withholding/<year>          {"mode", "reset", "total", "people": [{"userId", "sum", "pdf"(base64)}]} 한 묶음 올리기 (HR)
  POST /api/withholding/<year>/open     공개 — 직원 본인에게 보임 (HR). 해 전체 올리기가 끝나야(pending 0) 된다
  POST /api/withholding/<year>/close    공개 내리기 (HR)
  POST /api/withholding/<year>/seen     본인이 열어 봄 — 직원 메뉴의 "새 영수증" 숫자 (본인, 공개된 것만)

올리기는 nginx 본문 한도(5MB) 때문에 묶음으로 나눠 보낸다:
  mode "all", reset 참   해 전체 첫 묶음 — 공개 중이면 409. 그 해 줄을 모두 지우고 넣는다. pending = total − 이 묶음 사람 수
  mode "all", reset 거짓 이어지는 묶음 — 이미 있는 직원은 400, pending 보다 많으면 409. pending 을 줄인다
  mode "some"            사람만 바꾸기 · 더하기 — 공개 중이어도 받는다(바로 보임). 해 전체가 덜 올라간(pending>0) 해는 409
공개했던 해(opened_at 있음)에 다시 올린 줄은 revised(직원 화면 "수정됨"), 바뀐 줄의 '봤음'은 새로.

요약 = {gross, taxDecided, localDecided, taxPrepaid, localPrepaid, taxSettle, localSettle} — 원 정수 또는 null(못 읽은 칸).
잠근 글 = 12바이트 nonce + AES-GCM(내용, aad="wh|해|직원|sum" 또는 "…|pdf") — 다른 직원 · 다른 해 · 다른 칸으로 옮기면 풀리지 않는다.
감사로그 · 서버 로그에는 해 · 인원 수 · 직원 번호만 남기고 이름 · 금액 · 파일 이름은 남기지 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Blueprint, Response, g, jsonify, request

import auth
import push
from hr_eval.domain.models import AuditEntry
from payslip import pay_key

bp = Blueprint("withholding", __name__)
logger = logging.getLogger(__name__)

YEAR = re.compile(r"20[0-9][0-9]")  # [0-9]: \d 는 다른 나라 숫자도 받는다. year 칸은 형이 없어 꼭 int 로 묶는다 (013)
BODY_MAX = 4_700_000  # 한 묶음 본문 (nginx 는 5MB)
PDF_MAX = 3_000_000  # 한 사람 PDF (base64 를 푼 바이트)
PEOPLE_MAX = 200  # 한 묶음
TOTAL_MAX = 10_000  # 해 전체 사람 수 (pending 이 SQLite 정수 범위를 넘지 않게)
WON_MAX = 100_000_000_000
SUM_KEYS = ("gross", "taxDecided", "localDecided", "taxPrepaid", "localPrepaid", "taxSettle", "localSettle")
PERSON_KEYS = {"userId", "sum", "pdf"}

MSG_NO_KEY = "서버에 잠금 열쇠(HR_PAY_KEY)가 없어 올릴 수 없습니다. 서버 설정을 먼저 마쳐 주세요."
MSG_OPEN = "공개 중인 해입니다. 공개를 내린 뒤 다시 올리거나, 사람만 바꾸기로 올려 주세요."
MSG_PENDING = "올리다 멈춘 해예요. 다시 올려 주세요"
MSG_CHUNK = "해 전체 올리기를 처음부터 다시 해 주세요 (남은 사람 수와 맞지 않습니다)"
MSG_SHAPE = "영수증 내용의 모양이 올바르지 않습니다"


@bp.after_request
def _no_store(resp: Response) -> Response:
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


def _seal(key: bytes, aad: str, data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, data, aad.encode())


def _unseal(key: bytes, aad: str, blob: bytes) -> bytes:
    """열쇠 · 자리(aad)가 다르거나 글이 바뀌었으면 InvalidTag."""
    return AESGCM(key).decrypt(blob[:12], blob[12:], aad.encode())


def _bad(why: str) -> tuple[Response, int]:
    return jsonify(error=f"{MSG_SHAPE}: {why}"), 400


def _won(v: object) -> bool:
    return v is None or (type(v) is int and abs(v) <= WON_MAX)  # 정수 또는 null (참/거짓 · 실수 · 글자 안 됨)


def _person(p: object, i: int) -> tuple[int, dict, bytes] | str:
    """올린 한 사람 → (직원 번호, 요약, PDF 바이트), 틀리면 이유."""
    who = f"{i + 1}번째 사람"
    if not isinstance(p, dict) or set(p) != PERSON_KEYS:
        return f"{who} 칸이 맞지 않습니다"
    uid, s, pdf = p["userId"], p["sum"], p["pdf"]
    if type(uid) is not int or uid < 1:
        return f"{who} 직원 번호가 올바르지 않습니다"
    if not isinstance(s, dict) or set(s) != set(SUM_KEYS) or not all(_won(v) for v in s.values()):
        return f"직원 번호 {uid} 요약 칸이 맞지 않거나 금액이 정수가 아닙니다"
    try:
        raw = base64.b64decode(pdf, validate=True) if isinstance(pdf, str) else b""
    except ValueError:
        raw = b""
    if not raw.startswith(b"%PDF") or len(raw) > PDF_MAX:
        return f"직원 번호 {uid} PDF 가 아니거나 {PDF_MAX:,}바이트를 넘습니다"
    return uid, s, raw


def _year(y: str) -> int | None:
    return int(y) if YEAR.fullmatch(y) else None


def _target() -> tuple[int, bool] | tuple[Response, int]:
    """볼 직원 (번호, 남의 것인지). ?user= 는 HR 만 — 직원은 본인 번호여도 403, 없는 직원은 404."""
    me, arg = g.actor.user_id, request.args.get("user")
    if arg is None:
        return me, False
    if (denied := auth._hr_only()) is not None:
        return denied
    if not (arg.isascii() and arg.isdigit() and len(arg) <= 9) or not auth._db().execute("SELECT 1 FROM users WHERE id = ?", (int(arg),)).fetchone():
        return jsonify(error="직원을 찾을 수 없습니다."), 404
    return int(arg), int(arg) != me  # 본인 번호면 ?user= 가 없는 것과 같다


@bp.get("/api/withholding")
def list_get() -> Response:
    """요약 목록 (PDF 빼고). 풀리지 않는 줄은 건너뛴다(열쇠가 바뀌었거나 줄이 옮겨진 경우)."""
    t = _target()
    if isinstance(t[0], Response):
        return t
    uid, other = t
    conn, key, out = auth._db(), pay_key(), []
    rows = conn.execute(
        "SELECT w.year, y.status, w.revised, w.sum, w.seen_at FROM withholding w JOIN withholding_years y ON y.year = w.year"
        " WHERE w.user_id = ?" + ("" if other else " AND y.status = 'OPEN'") + " ORDER BY w.year DESC", (uid,)) if key else []
    for year, status, revised, blob, seen in rows:
        try:
            s = json.loads(_unseal(key, f"wh|{year}|{uid}|sum", blob))
        except (InvalidTag, ValueError):
            logger.error("원천징수영수증을 풀 수 없음: 해 %s 직원 번호 %s — HR_PAY_KEY 가 바뀌었는지 확인", year, uid)
            continue
        row = {"year": year, "revised": bool(revised), "seen": seen is not None, "sum": s}
        if other:
            row["status"] = status
        out.append(row)
    if other:
        auth._audit(conn, AuditEntry.by(g.actor, action="WITHHOLDING_VIEW", entity_type="user", entity_id=uid, after_data={"years": len(out)}))
    return jsonify(years=out)


@bp.get("/api/withholding/<year>/pdf")
def pdf_get(year: str) -> Response:
    t = _target()
    if isinstance(t[0], Response):
        return t
    uid, other = t
    y, key, conn = _year(year), pay_key(), auth._db()
    row = conn.execute(
        "SELECT w.pdf FROM withholding w JOIN withholding_years yr ON yr.year = w.year WHERE w.year = ? AND w.user_id = ?"
        + ("" if other else " AND yr.status = 'OPEN'"), (y, uid)).fetchone() if y and key else None
    try:
        pdf = _unseal(key, f"wh|{y}|{uid}|pdf", row[0]) if row else None
    except InvalidTag:
        logger.error("원천징수영수증을 풀 수 없음: 해 %s 직원 번호 %s — HR_PAY_KEY 가 바뀌었는지 확인", y, uid)
        pdf = None
    if pdf is None:
        return jsonify(error="볼 수 있는 영수증이 없습니다."), 404
    if other:
        auth._audit(conn, AuditEntry.by(g.actor, action="WITHHOLDING_PDF_VIEW", entity_type="user", entity_id=uid, after_data={"year": y}))
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="withholding_{y}.pdf"'})


@bp.get("/api/withholding/years")
def years_get() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    conn, users = auth._db(), {}
    rows = conn.execute(
        "SELECT y.year, y.status, COUNT(w.user_id), y.pending, COALESCE(SUM(w.revised), 0), y.uploaded_at, y.opened_at"
        " FROM withholding_years y LEFT JOIN withholding w ON w.year = y.year GROUP BY y.year ORDER BY y.year DESC").fetchall()
    for year, uid in conn.execute("SELECT year, user_id FROM withholding ORDER BY year, user_id"):
        users.setdefault(year, []).append(uid)  # 사람만 올리기 확인 창의 "바꿈 · 더함" — 직원 번호만 (이름 · 금액 없음)
    return jsonify(ready=pay_key() is not None, years=[
        {"year": r[0], "status": r[1], "people": r[2], "pending": r[3], "revised": r[4], "uploadedAt": r[5], "openedAt": r[6], "users": users.get(r[0], [])}
        for r in rows])


def _store(conn, year: int, mode: str, reset: bool, total: int, plan: list[tuple[int, dict, bytes]], key: bytes) -> tuple[int, str] | int:
    """한 트랜잭션 안에서 규칙 확인 → 해 줄 → 사람 줄 → 감사로그. 문제가 있으면 (상태, 문구), 없으면 남은 사람 수(pending)."""
    active = {r[0] for r in conn.execute("SELECT id FROM users WHERE is_active = 1")}
    if (gone := next((uid for uid, _, _ in plan if uid not in active), None)) is not None:
        return 400, f"{MSG_SHAPE}: 직원 번호 {gone}은(는) 포털에 없거나 비활성(퇴사) 직원입니다"
    row = conn.execute("SELECT status, pending, opened_at IS NOT NULL FROM withholding_years WHERE year = ?", (year,)).fetchone()
    status, pending, opened = row or ("DRAFT", 0, 0)
    n = len(plan)
    if mode == "all" and reset:
        if status == "OPEN":
            return 409, MSG_OPEN
        pending = total - n
        conn.execute("DELETE FROM withholding WHERE year = ?", (year,))  # 다시 올리면 '봤음'도 새로
    elif mode == "all":
        if row is None or pending < n:
            return 409, MSG_CHUNK
        have = {r[0] for r in conn.execute("SELECT user_id FROM withholding WHERE year = ?", (year,))}
        if (dup := next((uid for uid, _, _ in plan if uid in have), None)) is not None:
            return 400, f"{MSG_SHAPE}: 직원 번호 {dup}은(는) 이 해에 이미 올렸습니다"
        pending -= n
    elif pending > 0:
        return 409, MSG_PENDING
    now = auth._iso(auth._now())
    conn.execute(
        "INSERT INTO withholding_years (year, status, pending, uploaded_at, uploaded_by) VALUES (?, 'DRAFT', ?, ?, ?)"
        " ON CONFLICT (year) DO UPDATE SET pending = excluded.pending, uploaded_at = excluded.uploaded_at, uploaded_by = excluded.uploaded_by",
        (year, pending, now, g.actor.user_id))  # 상태는 그대로 — 해 전체는 공개 중이면 위에서 막혔고, 사람만은 공개 중에도 받는다
    new_rev = int(opened and mode == "all")  # 사람만에서 새로 더한 사람은 "새 영수증"(수정됨 아님)
    conn.executemany(
        "INSERT INTO withholding (year, user_id, sum, pdf, revised, uploaded_at) VALUES (?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (year, user_id) DO UPDATE SET sum = excluded.sum, pdf = excluded.pdf, revised = ?, uploaded_at = excluded.uploaded_at, seen_at = NULL",
        [(year, uid, _seal(key, f"wh|{year}|{uid}|sum", json.dumps(s).encode()), _seal(key, f"wh|{year}|{uid}|pdf", pdf), new_rev, now, int(opened))
         for uid, s, pdf in plan])
    auth._audit(conn, AuditEntry.by(g.actor, action="WITHHOLDING_UPLOAD", entity_type="withholding_year", entity_id=None,
                                    after_data={"year": year, "mode": mode, "people": n}))
    return pending


@bp.post("/api/withholding/<year>")
def upload(year: str) -> Response:
    """한 묶음 올리기. 모양이 틀린 사람이 하나라도 있으면 아무것도 저장하지 않는다. 이미 공개된 해에 사람만 더 올리면(some) COMMIT 뒤에 이번에 올린 사람의 휴대폰에 알림(push.notify)."""
    if (denied := auth._hr_only()) is not None:
        return denied
    if (y := _year(year)) is None:
        return _bad("귀속연도는 2000~2099 입니다")
    if (key := pay_key()) is None:
        return jsonify(error=MSG_NO_KEY), 503
    if len(request.get_data(cache=True)) > BODY_MAX:
        return _bad(f"한 번에 보낸 내용이 {BODY_MAX:,}바이트를 넘습니다")
    b = request.get_json(silent=True)
    b = b if isinstance(b, dict) else {}
    mode, reset, total, people = b.get("mode"), b.get("reset"), b.get("total"), b.get("people")
    if mode not in ("all", "some"):
        return _bad("올리는 방식(mode)은 all 또는 some 입니다")
    if not isinstance(people, list) or not 0 < len(people) <= PEOPLE_MAX:
        return _bad(f"한 묶음은 1~{PEOPLE_MAX}명입니다")
    if mode == "all" and type(reset) is not bool:
        return _bad("reset 은 참/거짓입니다")
    if mode == "all" and reset and not (type(total) is int and len(people) <= total <= TOTAL_MAX):
        return _bad("전체 사람 수(total)가 이 묶음보다 적거나 정수가 아닙니다")
    plan, ids = [], set()
    for i, p in enumerate(people):
        if isinstance(got := _person(p, i), str):
            return _bad(got)
        if got[0] in ids:
            return _bad(f"직원 번호 {got[0]}이(가) 두 번 들어 있습니다")
        ids.add(got[0])
        plan.append(got)
    conn = auth._db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        res = _store(conn, y, mode, bool(reset), total, plan, key)
        live = mode == "some" and not isinstance(res, tuple) and conn.execute("SELECT status FROM withholding_years WHERE year = ?", (y,)).fetchone()[0] == "OPEN"
        conn.execute("ROLLBACK" if isinstance(res, tuple) else "COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if live:  # 이미 공개된 해에 사람만 더 올렸다 — 저장이 끝난 뒤 이번에 올린 사람에게 알림
        push.notify([p[0] for p in plan], "withholding", year=y)
    return (jsonify(error=res[1]), res[0]) if isinstance(res, tuple) else jsonify(saved=len(plan), pending=res)


def _toggle(year: str, to: str, action: str) -> Response:
    """공개(OPEN) · 내리기(DRAFT). 이미 그 상태면 그대로 두고 감사로그도 더하지 않는다. 공개로 바뀌었으면 COMMIT 뒤에 그 해 영수증이 있는 사람의 휴대폰에 알림(push.notify)."""
    if (denied := auth._hr_only()) is not None:
        return denied
    y, conn = _year(year), auth._db()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT y.status, y.pending, COUNT(w.user_id) FROM withholding_years y LEFT JOIN withholding w ON w.year = y.year"
                           " WHERE y.year = ? GROUP BY y.year", (y,)).fetchone() if y else None
        err, opened = None, False
        if row and to == "OPEN" and (row[1] > 0 or row[2] == 0):
            err = MSG_PENDING if row[1] > 0 else "올린 영수증이 없는 해입니다."
        elif row and row[0] != to:
            opened = to == "OPEN"
            if to == "OPEN":
                conn.execute("UPDATE withholding_years SET status = 'OPEN', opened_at = ?, opened_by = ? WHERE year = ?",
                             (auth._iso(auth._now()), g.actor.user_id, y))
            else:
                conn.execute("UPDATE withholding_years SET status = 'DRAFT' WHERE year = ?", (y,))
            auth._audit(conn, AuditEntry.by(g.actor, action=action, entity_type="withholding_year", entity_id=None, after_data={"year": y, "people": row[2]}))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    if err:
        return jsonify(error=err), 409
    if opened:  # 저장(COMMIT)이 끝난 뒤 — 그 해 영수증이 있는 사람에게 알림
        push.notify(lambda: [r[0] for r in conn.execute("SELECT user_id FROM withholding WHERE year = ?", (y,))], "withholding", year=y)
    return jsonify(status=to) if row else (jsonify(error="올린 적 없는 해입니다."), 404)


@bp.post("/api/withholding/<year>/open")
def open_year(year: str) -> Response:
    return _toggle(year, "OPEN", "WITHHOLDING_OPEN")


@bp.post("/api/withholding/<year>/close")
def close_year(year: str) -> Response:
    return _toggle(year, "DRAFT", "WITHHOLDING_CLOSE")


@bp.post("/api/withholding/<year>/seen")
def seen(year: str) -> Response:
    """본인의 공개된 영수증만. 처음 연 때를 남기고 다시 눌러도 그대로."""
    y = _year(year)
    n = auth._db().execute(
        "UPDATE withholding SET seen_at = COALESCE(seen_at, ?) WHERE year = ? AND user_id = ?"
        " AND EXISTS (SELECT 1 FROM withholding_years yr WHERE yr.year = withholding.year AND yr.status = 'OPEN')",
        (auth._iso(auth._now()), y, g.actor.user_id)).rowcount if y else 0
    return jsonify(ok=True) if n else (jsonify(error="열 수 있는 영수증이 없습니다."), 404)
