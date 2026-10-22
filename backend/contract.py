"""연봉계약서 (2026-10-22 인사담당자 — 연봉계약서 실제화, 설계 5.4). HR 이 금액을 올려 발송하고 직원이 서명한다. 사람별 계약서 내용(금액·이름·생년월일)과
계약서 설정(대표이사 이름·소재지·사업의 종류)·직인 그림은 급여명세서와 같은 서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다 — 열쇠 읽기는 payslip.pay_key.

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.
이 파일은 HR 쪽 앞부분이다 (직원 서명 · 서명본 PDF · 파기 · 새 계약 · 문의 · 내려받기는 뒤 작업에서 더한다).

  GET  /api/contracts/settings        계약서 설정 {"ready", "settings": {ceo, addr, kind, sealId} | null} (HR)
  GET  /api/contracts/settings/seal   지금 직인 그림 PNG (HR)
  POST /api/contracts/settings        {"ceo", "addr", "kind", "seal": base64 PNG | null} 설정 저장 (HR). 처음엔 seal 이 꼭 있어야 하고, 그 뒤 null 은 직인 그대로
  GET  /api/contracts/admin           ?year= {"ready", "settings": 설정이 있나, "year", "rows": [계약서 한 줄씩]} (HR) — 그 회차 전부, 상태 상관없이
  POST /api/contracts/<year>          {"rows": [{userId, code, birth6, base, ability, pos, site, qual, start | null}]} 올리기 (HR) → {"saved"}.
                                      발송 전(DRAFT) 줄을 만들거나 바꾼다. 그 직원에게 이미 발송(SENT)·서명(SIGNED)한 줄이 있으면 409
  POST /api/contracts/<id>/adjust     {base, ability, pos, site, qual, birth6, start | null, reason} 발송 전 줄의 금액 조정 (HR)
  POST /api/contracts/<id>/remove     발송 전 줄 빼기 (HR) — 직원이 본 적이 없어 기록 없이 지운다
  POST /api/contracts/send            {"ids": [계약서 번호], "due": "YYYY-MM-DD"} 발송 (HR) → {"sent", "skipped": [{id, why}]}.
                                      그때의 계약서 설정(대표이사 이름 · 소재지 · 종류)과 직인을 그 줄에 고정한다

계약서 한 줄(admin 의 rows) = {id, year, userId, version, status, start, end, code, name, dept, title, birth6, base, ability, pos, site, qual, annual, reason,
sentAt, due, late, signedAt, replacedOn, cancelledAt, cancelReason, ask: {text, at, closedAt} | null, prevId}. late = 서명 대기인데 마감일이 지남.

잠근 글 = 12바이트 nonce + AES-GCM(내용, aad). 계약서 칸의 aad = "ct|해|직원|차수|칸" (칸 = body · company · sign · pdf · ask) — 차수는 발송 뒤에 바뀌지 않고
줄을 넣기 전에도 알 수 있어서, 다른 줄로 옮겨 붙이면 풀리지 않으면서 번호(id)를 몰라도 잠글 수 있다. body · company · sign · ask 는 JSON 글(ask 는 문의 글 한 줄을 JSON 글자로),
pdf 와 직인 그림은 바이트 그대로. 설정 data 의 aad 는 "ct|settings", 직인 그림은 "ct|seal|직인번호"(직인 줄은 바꾸지 않는다).
body = {code, name, dept, title, birth6, base, ability, pos, site, qual, reason} — 이름 · 소속(팀 이름, 팀이 없으면 본부) · 직급은 올릴 때의 명단 값을 고정하고 모두 글자다("" 가능).
company = {name, ceo, addr, kind} — 발송 때 그 줄에 고정.

감사로그에는 행동 · 해 · 인원 수 · 계약서/직원 번호 · 마감일 · 바뀐 설정 칸 이름만 남긴다 (이름 · 금액 · 생년월일 · 사유는 넣지 않는다).
서버 로그에도 풀리지 않는 줄의 계약서 번호만 남긴다.
"""

from __future__ import annotations

import base64
import functools
import json
import logging
from typing import NamedTuple

from cryptography.exceptions import InvalidTag
from flask import Blueprint, Response, g, jsonify, request

import auth
import contract_doc
import kpi
import payslip
from hr_eval.domain.models import AuditEntry
from withholding import _no_store, _seal, _unseal, _year

bp = Blueprint("contract", __name__)
bp.after_request(_no_store)
bp.errorhandler(kpi._Refuse)(kpi._refused)  # 거절은 KPI 와 같은 방식 (raise kpi._Refuse(문구, 상태) → {"error"} JSON)
logger = logging.getLogger(__name__)

ROWS_MAX = 2000  # 한 번에
BODY_MAX = 2_000_000  # 본문 (nginx 는 5MB)
ID_MAX = 2**31 - 1  # SQLite 정수에 들어가고 계약서 번호로 쓸 수 있는 크기
CID = f"<int(max={ID_MAX}):cid>"
SEAL_MAX = 500_000  # 직인 그림 (base64 를 푼 바이트)
PNG_HEAD = b"\x89PNG\r\n\x1a\n"
REASON_MAX = 200
CFG_TEXT = {"ceo": ("대표이사 이름", 20), "addr": ("소재지", 100), "kind": ("사업의 종류", 30)}
PAY = {"base": "기본급", "ability": "능력급", "pos": "직책수당", "site": "현장관리수당", "qual": "자격수당"}
ROW_KEYS = {"userId", "code", "birth6", "start", *PAY}
ADJUST_KEYS = {"birth6", "start", "reason", *PAY}
BODY_KEYS = ("code", "name", "dept", "title", "birth6", *PAY, "reason")
CFG_AAD = "ct|settings"
COLS = ("c.id, c.year, c.user_id, c.version, c.prev_id, c.status, c.starts_on, c.body, c.sent_at, c.due_on, c.signed_at,"
        " c.replaced_on, c.cancelled_at, c.cancel_reason, c.ask, c.ask_at, c.ask_closed_at")

MSG_NO_KEY = "서버에 계약서 잠금 열쇠(HR_PAY_KEY)가 없어 저장할 수 없습니다. 서버 설정을 먼저 마쳐 주세요."
MSG_NO_CFG = "계약서 설정에서 대표이사 이름과 직인을 먼저 넣어 주세요."
MSG_SHAPE = "계약서 내용의 모양이 올바르지 않습니다"
MSG_DRAFT_ONLY = "발송 전인 계약서만 할 수 있습니다."
LOG_UNREADABLE = "연봉계약서를 풀 수 없음: 계약서 %s — HR_PAY_KEY 가 바뀌었는지 확인"


def _bad(why: str) -> kpi._Refuse:
    return kpi._Refuse(f"{MSG_SHAPE}: {why}", 400)


def _hr_write(fn):
    """쓰는 길의 앞 검사 — HR 이 아니면 403, 서버에 열쇠가 없으면 503. 열쇠를 첫 인자로 넘긴다."""
    @functools.wraps(fn)
    def wrapper(*args, **kw):
        if (denied := auth._hr_only()) is not None:
            return denied
        if (key := payslip.pay_key()) is None:
            return jsonify(error=MSG_NO_KEY), 503
        return fn(key, *args, **kw)
    return wrapper


def _json() -> dict:
    if len(request.get_data(cache=True)) > BODY_MAX:
        raise _bad(f"보낸 내용이 {BODY_MAX:,}바이트를 넘습니다")
    b = request.get_json(silent=True)
    return b if isinstance(b, dict) else {}


def _aad(year: int, uid: int, version: int, field: str) -> str:
    return f"ct|{year}|{uid}|{version}|{field}"


def _lock(key: bytes, aad: str, obj: object) -> bytes:
    return _seal(key, aad, json.dumps(obj, ensure_ascii=False).encode())


def _unlock(key: bytes, aad: str, blob: bytes) -> object:
    """_lock 의 반대. 열쇠 · 자리(aad)가 다르거나 글이 바뀌었으면 InvalidTag."""
    return json.loads(_unseal(key, aad, blob))


def _log(conn, action: str, entity: str, entity_id: int | None, **data: object) -> None:
    auth._audit(conn, AuditEntry.by(g.actor, action=action, entity_type=entity, entity_id=entity_id, after_data=data))


class _User(NamedTuple):
    name: str
    title: str | None
    active: int
    contract: int  # 구성원 설정 "연봉계약서"
    portal: int  # 구성원 설정 "포털 사용" — 꺼져 있으면 로그인할 수 없어 서명할 수 없다
    dept: str  # 소속 부서 이름 (팀, 팀이 없으면 본부)
    head: int  # 본부장 · 대표이사 (연봉계약서 대상이 아니다)


def _users(conn) -> dict[int, _User]:
    return {r[0]: _User(*r[1:]) for r in conn.execute(
        "SELECT u.id, u.name, u.title, u.is_active, u.has_contract, u.portal_enabled, d.name,"
        " EXISTS (SELECT 1 FROM user_roles r WHERE r.user_id = u.id AND r.role IN ('DIVISION_HEAD', 'CEO'))"
        " FROM users u JOIN departments d ON d.id = u.department_id")}


def _blocked(u: _User) -> str | None:
    """계약서를 만들거나 보낼 수 없는 이유 — 비활성이거나 연봉계약서 대상이 아닌 사람(꺼짐 · 본부장 · 대표이사)."""
    if not u.active:
        return "비활성"
    return "연봉계약서 대상 아님" if not u.contract or u.head else None


def _text(v: object, label: str, limit: int) -> str:
    """사람이 쓴 글 한 칸: 글자만, 앞뒤 공백을 뺀 1~limit자, kpi._chars 규칙(제어문자 거부). 아니면 400."""
    if not isinstance(v, str):
        raise _bad(f"{label}: 글자로 보내 주세요")
    t = kpi._chars(v.strip(), label)
    if not 0 < len(t) <= limit:
        raise _bad(f"{label}: 1~{limit}자로 써 주세요")
    return t


def _png(v: object) -> bytes:
    """직인 그림(base64 PNG) → 바이트. PNG 머리가 아니거나 500KB 를 넘거나 그림으로 읽을 수 없으면 400 (서명 때 PDF 를 못 만드는 그림을 미리 막는다)."""
    try:
        raw = base64.b64decode(v, validate=True) if isinstance(v, str) else b""
        if not raw.startswith(PNG_HEAD) or len(raw) > SEAL_MAX:
            raise ValueError
        contract_doc._reader(raw, "직인")
    except (OSError, ValueError):
        raise _bad(f"직인 그림은 {SEAL_MAX // 1000}KB 이하의 PNG 그림이어야 합니다") from None
    return raw


def _pay_error(r: dict, year: int) -> str | None:
    """금액 · 생년월일 · 계약 시작일 모양 (올리기 한 줄과 조정이 같이 쓴다). 문제가 있으면 이유, 없으면 None."""
    for k, label in PAY.items():
        if type(r[k]) is not int or not 0 <= r[k] <= payslip.WON_MAX:
            return f"{label}이(가) 0~10억 원의 정수가 아닙니다"
    if r["base"] < 1:
        return "기본급이 0원입니다"
    if not contract_doc.birth6_ok(r["birth6"]):
        return "생년월일이 실제 있는 날의 YYMMDD 6자리가 아닙니다"
    if (s := r["start"]) is not None:
        lo, hi = contract_doc.round_period(year)
        if not (payslip._real_date(s) and lo <= s <= hi):
            return f"계약 시작일이 {lo} ~ {hi} 안의 실제 있는 날(YYYY-MM-DD)이 아닙니다"
    return None


def _row_error(r: object, year: int) -> str | None:
    """올리기 한 줄의 모양 검사. 문제가 있으면 이유."""
    if not isinstance(r, dict) or set(r) != ROW_KEYS:
        return "칸이 맞지 않습니다"
    if type(r["userId"]) is not int or r["userId"] < 1:
        return "직원 번호가 올바르지 않습니다"
    if not isinstance(r["code"], str) or not (r["code"] == "" or payslip.CODE.fullmatch(r["code"])):
        return "사원코드 모양이 올바르지 않습니다"
    return _pay_error(r, year)


def _settings(conn, key: bytes) -> tuple[dict, int] | None:
    """계약서 설정 ({ceo, addr, kind}, 직인 번호). 줄이 없거나 풀리지 않으면 None."""
    row = conn.execute("SELECT data, seal_id FROM contract_settings").fetchone()
    if row is None:
        return None
    try:
        return _unlock(key, CFG_AAD, row[0]), row[1]
    except (InvalidTag, ValueError):
        logger.error("연봉계약서 설정을 풀 수 없음 — HR_PAY_KEY 가 바뀌었는지 확인")
        return None


def _seal_png(conn, key: bytes, seal_id: int) -> bytes | None:
    """직인 그림 바이트. 줄이 없거나 풀리지 않으면 None."""
    row = conn.execute("SELECT png FROM contract_seals WHERE id = ?", (seal_id,)).fetchone()
    try:
        return _unseal(key, f"ct|seal|{seal_id}", row[0]) if row else None
    except InvalidTag:
        logger.error("연봉계약서 직인 그림을 풀 수 없음: 직인 %s — HR_PAY_KEY 가 바뀌었는지 확인", seal_id)
        return None


def _view(key: bytes, r: tuple, today: str) -> dict:
    """계약서 한 줄 → HR 에게 보이는 사전. 풀리지 않으면 InvalidTag · ValueError."""
    cid, year, uid, ver, prev, status, start, body, sent_at, due, signed_at, replaced_on, cancelled_at, cancel_reason, ask, ask_at, closed_at = r
    b = _unlock(key, _aad(year, uid, ver, "body"), body)
    return {
        "id": cid, "year": year, "userId": uid, "version": ver, "status": status, "start": start, "end": contract_doc.round_period(year)[1],
        **{k: b[k] for k in BODY_KEYS[:-1]},
        "annual": contract_doc.calc(b["base"], b["ability"], b["pos"], b["site"], b["qual"])["annual"], "reason": b["reason"],
        "sentAt": sent_at, "due": due, "late": status == "SENT" and today > due, "signedAt": signed_at, "replacedOn": replaced_on,
        "cancelledAt": cancelled_at, "cancelReason": cancel_reason,
        "ask": None if ask is None else {"text": _unlock(key, _aad(year, uid, ver, "ask"), ask), "at": ask_at, "closedAt": closed_at},
        "prevId": prev,
    }


def _rows(conn, key: bytes, where: str, args: tuple) -> list[dict]:
    """조건에 맞는 계약서를 직원 · 차수 순으로. 풀리지 않는 줄은 건너뛴다(열쇠가 바뀌었거나 줄이 옮겨진 경우)."""
    out, today = [], auth._today().isoformat()
    for r in conn.execute(f"SELECT {COLS} FROM contracts c WHERE {where} ORDER BY c.user_id, c.version", args):
        try:
            out.append(_view(key, r, today))
        except (InvalidTag, ValueError):
            logger.error(LOG_UNREADABLE, r[0])
    return out


# ---------------- 계약서 설정 · 직인 ----------------
@bp.get("/api/contracts/settings")
def settings_get() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    key = payslip.pay_key()
    cfg = _settings(auth._db(), key) if key else None
    return jsonify(ready=key is not None, settings={**cfg[0], "sealId": cfg[1]} if cfg else None)


@bp.get("/api/contracts/settings/seal")
def seal_get() -> Response:
    if (denied := auth._hr_only()) is not None:
        return denied
    conn, key = auth._db(), payslip.pay_key()
    row = conn.execute("SELECT seal_id FROM contract_settings").fetchone()
    png = _seal_png(conn, key, row[0]) if key and row else None
    return Response(png, mimetype="image/png") if png else (jsonify(error="직인 그림이 없습니다."), 404)


@bp.post("/api/contracts/settings")
@_hr_write
def settings_post(key: bytes) -> Response:
    """설정 저장. 바뀐 것이 없으면 그대로 두고 감사로그도 더하지 않는다. 직인을 보내면 새 직인 줄이 생긴다(옛 직인 줄은 옛 계약서가 가리키므로 남는다)."""
    b = _json()
    new = {k: _text(b.get(k), label, limit) for k, (label, limit) in CFG_TEXT.items()}
    png = None if b.get("seal") is None else _png(b["seal"])
    conn = auth._db()
    with kpi._txn(conn):
        old, now = _settings(conn, key), auth._iso(auth._now())
        if old is None and png is None:
            raise _bad("처음에는 직인 그림도 함께 넣어 주세요")
        seal_id = old[1] if old else None
        changed = [k for k in new if old is None or old[0].get(k) != new[k]]
        if png is not None:
            seal_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM contract_seals").fetchone()[0]  # 잠금 자리(aad)에 번호가 들어가므로 번호를 먼저 정한다
            conn.execute("INSERT INTO contract_seals (id, png, created_at, created_by) VALUES (?, ?, ?, ?)",
                         (seal_id, _seal(key, f"ct|seal|{seal_id}", png), now, g.actor.user_id))
            changed.append("seal")
        if changed:
            conn.execute(
                "INSERT INTO contract_settings (id, data, seal_id, updated_at, updated_by) VALUES (1, ?, ?, ?, ?)"
                " ON CONFLICT (id) DO UPDATE SET data = excluded.data, seal_id = excluded.seal_id, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (_lock(key, CFG_AAD, new), seal_id, now, g.actor.user_id))
            _log(conn, "CONTRACT_SETTINGS", "contract_settings", None, fields=changed)
    return jsonify(ready=True, settings={**new, "sealId": seal_id})


# ---------------- 현황 ----------------
@bp.get("/api/contracts/admin")
def admin() -> Response:
    """그 회차(기본 = 다가오는 회차)의 계약서 전부 + 서버에 열쇠가 있는지(ready) · 계약서 설정이 있는지(settings)."""
    if (denied := auth._hr_only()) is not None:
        return denied
    arg = request.args.get("year")
    year = contract_doc.upcoming_year(auth._today()) if arg is None else _year(arg)
    if year is None:
        return jsonify(error="회차(해)는 2000~2099 입니다."), 400
    conn, key = auth._db(), payslip.pay_key()
    return jsonify(ready=key is not None, settings=key is not None and _settings(conn, key) is not None, year=year,
                   rows=_rows(conn, key, "c.year = ?", (year,)) if key else [])


# ---------------- 올리기 · 조정 · 빼기 ----------------
def _store(conn, key: bytes, year: int, plan: list[dict]) -> None:
    """한 트랜잭션 안에서: 직원 · 이미 발송한 줄 · 사원코드 확인 → 발송 전 줄 만들기/바꾸기 → 감사로그. 문제가 있으면 kpi._Refuse."""
    users = _users(conn)
    for r in plan:
        why = "포털에 없는 직원" if (u := users.get(r["userId"])) is None else _blocked(u)
        if why:
            raise kpi._Refuse(f"직원 번호 {r['userId']}은(는) 계약서를 만들 수 없습니다 ({why})", 400)
    sent = {r[0] for r in conn.execute("SELECT user_id FROM contracts WHERE year = ? AND status IN ('SENT', 'SIGNED')", (year,))}
    if (taken := next((r["userId"] for r in plan if r["userId"] in sent), None)) is not None:
        raise kpi._Refuse(f"직원 번호 {taken}은(는) 이 회차에 이미 발송한 계약서가 있습니다. 서명 대기는 파기, 서명 완료는 새 계약으로 바꾸기를 써 주세요.", 409)
    if (why := payslip.remember_codes(conn, [(r["userId"], r["code"]) for r in plan if r["code"]])) is not None:
        raise kpi._Refuse(why, 400)
    drafts = {r[0]: (r[1], r[2]) for r in conn.execute("SELECT user_id, id, version FROM contracts WHERE year = ? AND status = 'DRAFT'", (year,))}
    latest = {r[0]: r[1] for r in conn.execute("SELECT user_id, MAX(version) FROM contracts WHERE year = ? GROUP BY user_id", (year,))}  # 파기한 줄까지 센다
    now, first = auth._iso(auth._now()), contract_doc.round_period(year)[0]
    for r in plan:
        uid, u = r["userId"], users[r["userId"]]
        body = {"code": r["code"], "name": u.name, "dept": u.dept, "title": u.title or "", "birth6": r["birth6"],
                **{k: r[k] for k in PAY}, "reason": ""}
        start = r["start"] or first
        if uid in drafts:
            cid, ver = drafts[uid]
            conn.execute("UPDATE contracts SET body = ?, starts_on = ? WHERE id = ?", (_lock(key, _aad(year, uid, ver, "body"), body), start, cid))
        else:
            ver = latest.get(uid, 0) + 1
            conn.execute("INSERT INTO contracts (year, user_id, version, status, starts_on, body, created_at, created_by) VALUES (?, ?, ?, 'DRAFT', ?, ?, ?, ?)",
                         (year, uid, ver, start, _lock(key, _aad(year, uid, ver, "body"), body), now, g.actor.user_id))
    _log(conn, "CONTRACT_UPLOAD", "contract_year", None, year=year, people=len(plan))


@bp.post("/api/contracts/<year>")
@_hr_write
def upload(key: bytes, year: str) -> Response:
    """올리기. 모양이 틀린 줄이 하나라도 있으면 아무것도 저장하지 않는다."""
    if (y := _year(year)) is None:
        raise _bad("회차(해)는 2000~2099 입니다")
    rows = _json().get("rows")
    if not isinstance(rows, list) or not 0 < len(rows) <= ROWS_MAX:
        raise _bad(f"줄 수는 1~{ROWS_MAX}줄이어야 합니다")
    seen = set()
    for i, r in enumerate(rows):
        if (why := _row_error(r, y)) is not None:
            raise _bad(f"{i + 1}번째 줄 {why}")
        if r["userId"] in seen:
            raise _bad(f"{i + 1}번째 줄 직원이 두 번 들어 있습니다")
        seen.add(r["userId"])
    conn = auth._db()
    with kpi._txn(conn):
        _store(conn, key, y, rows)
    return jsonify(saved=len(rows))


def _draft(conn, cid: int) -> tuple[int, int, int, bytes]:
    """발송 전(DRAFT) 줄의 (회차, 직원 번호, 차수, 잠근 내용). 없으면 404, 발송했으면 409."""
    row = conn.execute("SELECT year, user_id, version, body, status FROM contracts WHERE id = ?", (cid,)).fetchone()
    if row is None:
        raise kpi._Refuse("계약서를 찾을 수 없습니다.", 404)
    if row[4] != "DRAFT":
        raise kpi._Refuse(MSG_DRAFT_ONLY, 409)
    return row[:4]


@bp.post(f"/api/contracts/{CID}/adjust")
@_hr_write
def adjust(key: bytes, cid: int) -> Response:
    """발송 전 줄의 금액 · 생년월일 · 시작일 · 사유를 바꾼다 (이름 · 소속 · 직급 · 사원코드는 올릴 때 그대로)."""
    b = _json()
    conn = auth._db()
    with kpi._txn(conn):
        year, uid, ver, blob = _draft(conn, cid)
        if set(b) != ADJUST_KEYS:
            raise _bad("칸이 맞지 않습니다")
        reason = _text(b["reason"], "조정 사유", REASON_MAX)
        if (why := _pay_error(b, year)) is not None:
            raise _bad(why)
        try:
            body = _unlock(key, _aad(year, uid, ver, "body"), blob)
        except (InvalidTag, ValueError):
            logger.error(LOG_UNREADABLE, cid)
            raise kpi._Refuse("이 계약서를 열 수 없습니다. 서버의 잠금 열쇠(HR_PAY_KEY)가 바뀌었는지 확인해 주세요.", 409) from None
        body.update(birth6=b["birth6"], reason=reason, **{k: b[k] for k in PAY})
        conn.execute("UPDATE contracts SET body = ?, starts_on = ? WHERE id = ?",
                     (_lock(key, _aad(year, uid, ver, "body"), body), b["start"] or contract_doc.round_period(year)[0], cid))
        _log(conn, "CONTRACT_ADJUST", "contract", cid, year=year, userId=uid)
    return jsonify(ok=True)


@bp.post(f"/api/contracts/{CID}/remove")
@_hr_write
def remove(key: bytes, cid: int) -> Response:
    """발송 전 줄 빼기. 직원이 본 적이 없으니 기록 없이 지운다(다시 올리면 다시 생긴다)."""
    conn = auth._db()
    with kpi._txn(conn):
        year, uid, _, _ = _draft(conn, cid)
        conn.execute("DELETE FROM contracts WHERE id = ?", (cid,))
        _log(conn, "CONTRACT_REMOVED", "contract", cid, year=year, userId=uid)
    return jsonify(ok=True)


# ---------------- 발송 ----------------
def _cannot_send(row: tuple | None, users: dict[int, _User]) -> str | None:
    """보낼 수 없는 이유(발송 결과 skipped 의 why): 발송 전이 아님 · 비활성 · 연봉계약서 대상 아님 · 포털 사용 꺼짐."""
    if row is None or row[3] != "DRAFT":
        return "발송 전이 아님"
    u = users[row[1]]
    return _blocked(u) or (None if u.portal else "포털 사용 꺼짐")


@bp.post("/api/contracts/send")
@_hr_write
def send(key: bytes) -> Response:
    """고른 발송 전 줄을 서명 대기로. 보낼 수 없는 줄은 건너뛰고 이유를 알려 준다. 계약서 설정이 없으면 409."""
    b = _json()
    ids, due = b.get("ids"), b.get("due")
    if not (isinstance(ids, list) and 0 < len(ids) <= ROWS_MAX and all(type(i) is int and 0 < i <= ID_MAX for i in ids) and len(set(ids)) == len(ids)):
        raise _bad(f"보낼 계약서(ids)는 서로 다른 계약서 번호 1~{ROWS_MAX}개여야 합니다")
    if not payslip._real_date(due) or due < auth._today().isoformat():
        raise _bad("서명 마감일은 오늘 이후의 실제 있는 날(YYYY-MM-DD)이어야 합니다")
    conn = auth._db()
    with kpi._txn(conn):
        if (cfg := _settings(conn, key)) is None:
            raise kpi._Refuse(MSG_NO_CFG, 409)
        company, seal_id = {"name": contract_doc.COMPANY_NAME, **cfg[0]}, cfg[1]
        users, now, skipped, years = _users(conn), auth._iso(auth._now()), [], {}
        for cid in ids:
            row = conn.execute("SELECT year, user_id, version, status FROM contracts WHERE id = ?", (cid,)).fetchone()
            if (why := _cannot_send(row, users)) is not None:
                skipped.append({"id": cid, "why": why})
                continue
            year, uid, ver, _ = row
            conn.execute("UPDATE contracts SET status = 'SENT', sent_at = ?, sent_by = ?, due_on = ?, company = ?, seal_id = ? WHERE id = ?",
                         (now, g.actor.user_id, due, _lock(key, _aad(year, uid, ver, "company"), company), seal_id, cid))
            years[year] = years.get(year, 0) + 1
        for year, n in years.items():
            _log(conn, "CONTRACT_SEND", "contract_year", None, year=year, people=n, due=due)
    return jsonify(sent=sum(years.values()), skipped=skipped)
