"""연봉계약서 (2026-10-22 인사담당자 — 연봉계약서 실제화, 설계 5.4). HR 이 금액을 올려 발송하고 직원이 서명한다. 사람별 계약서 내용(금액·이름·생년월일)과
계약서 설정(대표이사 이름·소재지·사업의 종류)·직인 그림은 급여명세서와 같은 서버 .env 의 HR_PAY_KEY 로 잠가(AES-GCM) 둔다 — 열쇠 읽기는 payslip.pay_key.

app.py 가 bp 를 붙인다. 세션 검사는 auth._guard 가 모든 /api/* 에 하므로 여기서는 g.actor 만 쓴다.

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
  POST /api/contracts/<id>/cancel     {reason, then: "resend" | "draft" | "none"} 서명 대기 · 서명 완료 줄을 파기 (HR) → {ok, id: 다음 차수 번호 | null}.
                                      resend = 같은 내용 · 마감일로 다음 차수를 바로 서명 대기로, draft = 다음 차수를 발송 전으로, none = 다음 차수 없음
  POST /api/contracts/<id>/replace    {base, ability, pos, site, qual, start, reason} 서명 완료 줄을 새 계약으로 바꾸기 (HR) → {ok, id: 다음 차수(발송 전) 번호}.
                                      start = 바뀌는 날(회차 안 · 지금 계약 시작일 뒤). 새 계약이 서명되는 순간 옛 줄이 "새 계약으로 바뀜"이 된다

  GET  /api/contracts/mine            {"rows": [...]} 본인의 서명 대기 · 서명 완료 · 새 계약으로 바뀜 · 파기(발송된 적 있는 것) 줄. 최근 회차 · 높은 차수부터
  GET  /api/contracts/<id>            한 줄 (본인 · HR). HR 가 남의 것을 보면 감사로그. 발송 전 줄은 HR 만 (지금 계약서 설정으로 미리 봄)
  GET  /api/contracts/<id>/seal       그 줄에 고정된 직인 PNG (본인 · HR). 발송 전 줄은 지금 설정의 직인
  POST /api/contracts/<id>/sign       {name, consent: true, image: base64 PNG} 서명 (본인) → {signedAt, hash}. 마감이 지나도 받는다
  GET  /api/contracts/<id>/pdf        서명할 때 만들어 잠가 둔 서명본 PDF 그대로 (본인 · HR). 서명한 줄만 (새 계약으로 바뀜 · 서명 뒤 파기 포함)
  POST /api/contracts/<id>/ask        {text} HR 에 문의 (본인, 서명 대기 줄만). 1~200자. 확인 전 문의가 있으면 409 — HR 이 확인한 뒤에는 다시 보낼 수 있다 → {ok}
  POST /api/contracts/<id>/ask/close  문의를 "확인함"으로 (HR). 글은 지우지 않는다. 문의가 없거나 이미 확인했으면 409 → {ok}

  GET  /api/contracts/division        ?year= 본부장 현황 (본부장만, 아니면 403) {"year", "rows": [{name, team, status, late, signedOn, due}]} — 자기 본부 사람의 살아 있는 줄
                                      (발송 전 · 서명 대기 · 서명 완료). 금액 · 생년월일 · 차수 · 문의는 보내지 않는다 (access.Act.VIEW_CONTRACT_STATUS)
  GET  /api/contracts/<year>/zip      그 회차 서명 완료 · 새 계약으로 바뀐 줄의 서명본 PDF 묶음 + 서명정보.csv (HR, 보관용 — zip 에는 암호가 없다). 파기한 줄은 빼고, 풀리지 않는 줄은 건너뛰고, 하나도 없으면 404

계약서 한 줄(admin 의 rows) = {id, year, userId, version, status, start, end, code, name, dept, title, birth6, base, ability, pos, site, qual, annual, reason,
sentAt, due, late, signedAt, replacedOn, cancelledAt, cancelReason, ask: {text, at, closedAt} | null, prevId}. late = 서명 대기인데 마감일이 지남.
본인 · 한 건 보기의 줄 = 위에서 reason · cancelReason · ask.text 를 빼고 blocks(본문 블록 — contract_doc.build_blocks, 서명한 줄은 서명한 날(한국 날짜)이 들어간 것) ·
hash(서명 때 저장한 문서 고유번호, 서명 전 null) · canSign(서명 대기이고 본인 것)을 더한 것. 서명한 줄(서명 완료 · 새 계약으로 바뀜 · 서명 뒤 파기)의 blocks 는 서명할 때 고유번호를 매기고 서명본 PDF 에 그린 그 본문을 서명 기록에 넣어 둔 것을 그대로 내준다 —
양식(contract_doc.TPL · 문장)이 나중에 바뀌어도 서명한 본문은 그대로이고 hash 와 맞는다. 서명 전 줄만 지금 양식으로 만든다.
서명한 줄에는 how(본인 확인 방법 글 — "휴대폰 등록" · "하이웍스 로그인", 서명본 PDF 의 전자서명 정보와 같은 _how) · signImage(서명 그림 base64 PNG, 200KB 이하)도 붙는다 — 서명 전 줄 · HR 현황(admin) 줄에는 없다.

잠근 글 = 12바이트 nonce + AES-GCM(내용, aad). 계약서 칸의 aad = "ct|해|직원|차수|칸" (칸 = body · company · sign · pdf · ask) — 차수는 발송 뒤에 바뀌지 않고
줄을 넣기 전에도 알 수 있어서, 다른 줄로 옮겨 붙이면 풀리지 않으면서 번호(id)를 몰라도 잠글 수 있다. body · company · sign · ask 는 JSON 글(ask 는 문의 글 한 줄을 JSON 글자로),
pdf 와 직인 그림은 바이트 그대로. 설정 data 의 aad 는 "ct|settings", 직인 그림은 "ct|seal|직인번호"(직인 줄은 바꾸지 않는다).
body = {code, name, dept, title, birth6, base, ability, pos, site, qual, reason} — 이름 · 소속(팀 이름, 팀이 없으면 본부) · 직급은 올릴 때의 명단 값을 고정하고 모두 글자다("" 가능).
company = {name, ceo, addr, kind} — 발송 때 그 줄에 고정. sign = {image, name, consent, ip, ua, kind, blocks, tpl, salt} — 서명 그림은 base64 글자, blocks = 서명할 때 해시를 매기고 PDF 에 그린 본문 블록 그대로, tpl = 그때의 양식 판 번호,
salt = 서명할 때 뽑은 무작위 16바이트(16진수 글자) — 고유번호는 tpl · salt · blocks 를 묶은 SHA-256 이라 다시 확인하려면 둘 다 필요하다 (salt 는 이 잠근 기록에만 있고 화면 · 목록에는 내주지 않는다 — 양식은 공개이고 본문에 금액이 있어서 소금이 없으면 DB 사본만으로 금액을 맞혀 볼 수 있다).
다음 차수(파기 후 재발송 · 새 계약)를 만들 때는 내용을 풀어 새 차수 자리(aad)로 다시 잠근다 — 그대로 옮긴 글은 풀리지 않는다.

감사로그에는 행동 · 해 · 인원 수 · 계약서/직원 번호 · 마감일 · 문서 고유번호 · 바뀐 설정 칸 이름만 남긴다 (이름 · 금액 · 생년월일 · 사유는 넣지 않는다).
서버 로그에도 풀리지 않는 줄의 계약서 번호만 남긴다 (문의 글도 어디에도 남기지 않는다).
"""

from __future__ import annotations

import base64
import csv
import functools
import io
import json
import logging
import os
import re
import zipfile
from datetime import datetime, timezone
from typing import NamedTuple

from cryptography.exceptions import InvalidTag
from flask import Blueprint, Response, g, jsonify, request
from PIL import Image  # reportlab 이 이미 끌고 오는 Pillow

import auth
import contract_doc
import kpi
import payslip
import push
from hr_eval.domain import access
from hr_eval.domain.models import AuditEntry, Role
from hr_eval.domain.org import target_of
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
SIGN_MAX = 200_000  # 서명 그림 (같음)
PNG_HEAD = b"\x89PNG\r\n\x1a\n"
REASON_MAX = 200
ASK_MAX = 200
ZIP_HEAD = ("이름", "사원코드", "차수", "서명 일시", "문서 고유번호")
RRN_LIKE = re.compile(r"[0-9]{6}[- ]?[0-9]{7}")  # 주민등록번호 모양 (앞 6자리 + 뒤 7자리 — 붙었거나 대시 · 공백이 낀 것). 사번 칸에 잘못 들어온 것은 users.employee_no 에 그대로 남으므로 받지 않는다
BAD_FILE_CHARS = re.compile(r'[\x00-\x1f\\/:*?"<>|]')  # Windows 파일 이름에 못 쓰는 글자
CFG_TEXT = {"ceo": ("대표이사 이름", 20), "addr": ("소재지", 100), "kind": ("사업의 종류", 30)}
PAY = {"base": "기본급", "ability": "능력급", "pos": "직책수당", "site": "현장관리수당", "qual": "자격수당"}
ROW_KEYS = {"userId", "code", "birth6", "start", *PAY}
ADJUST_KEYS = {"birth6", "start", "reason", *PAY}
REPLACE_KEYS = {"start", "reason", *PAY}
SIGN_KEYS = {"name", "consent", "image"}
THEN = ("resend", "draft", "none")
BODY_KEYS = ("code", "name", "dept", "title", "birth6", *PAY, "reason")
CFG_AAD = "ct|settings"
COLS = ("c.id, c.year, c.user_id, c.version, c.prev_id, c.status, c.starts_on, c.body, c.sent_at, c.due_on, c.signed_at,"
        " c.replaced_on, c.cancelled_at, c.cancel_reason, c.ask, c.ask_at, c.ask_closed_at")
DOC_COLS = COLS + ", c.sign, c.company, c.seal_id, c.doc_hash"  # 본문을 내줄 때 더 필요한 칸 (맨 뒤 넷 — 뒤 셋은 company · seal_id · doc_hash)
NO_COMPANY = {"ceo": "(계약서 설정 필요)", "addr": "", "kind": ""}  # 계약서 설정이 없을 때 발송 전 줄의 미리 보기

MSG_NO_KEY = "서버에 계약서 잠금 열쇠(HR_PAY_KEY)가 없어 저장할 수 없습니다. 서버 설정을 먼저 마쳐 주세요."
MSG_NO_CFG = "계약서 설정에서 대표이사 이름과 직인을 먼저 넣어 주세요."
MSG_SHAPE = "계약서 내용의 모양이 올바르지 않습니다"
MSG_DRAFT_ONLY = "발송 전인 계약서만 할 수 있습니다."
MSG_UNREADABLE = "이 계약서를 열 수 없습니다. 서버의 잠금 열쇠(HR_PAY_KEY)가 바뀌었는지 확인해 주세요."
MSG_SIGN_IMG = "서명 그림을 읽을 수 없어요"
MSG_NOT_FOUND = "계약서를 찾을 수 없습니다."
MSG_NOT_SENT = "서명할 수 있는 상태가 아닙니다. 이미 서명했거나 파기된 계약서예요."
LOG_UNREADABLE = "연봉계약서를 풀 수 없음: 계약서 %s — HR_PAY_KEY 가 바뀌었는지 확인"


def _bad(why: str) -> kpi._Refuse:
    return kpi._Refuse(f"{MSG_SHAPE}: {why}", 400)


def _keyed(fn):
    """열쇠를 첫 인자로 넘긴다 — 서버에 열쇠가 없으면 503."""
    @functools.wraps(fn)
    def wrapper(*args, **kw):
        if (key := payslip.pay_key()) is None:
            return jsonify(error=MSG_NO_KEY), 503
        return fn(key, *args, **kw)
    return wrapper


def _hr_write(fn):
    """쓰는 길 (과 서명본 묶음) 의 앞 검사 — HR 이 아니면 403, 서버에 열쇠가 없으면 503. 열쇠를 첫 인자로 넘긴다."""
    keyed = _keyed(fn)

    @functools.wraps(fn)
    def wrapper(*args, **kw):
        if (denied := auth._hr_only()) is not None:
            return denied
        return keyed(*args, **kw)
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


def _opened(cid: int, read, *args):
    """read(*args) (_unlock · _unseal · _doc …) 를 부르되 풀리지 않으면 로그(계약서 번호만) + 409 — 열쇠가 바뀌었거나 줄이 옮겨진 경우."""
    try:
        return read(*args)
    except (InvalidTag, ValueError):
        logger.error(LOG_UNREADABLE, cid)
        raise kpi._Refuse(MSG_UNREADABLE, 409) from None


def _kst(iso: str) -> datetime:
    """저장해 둔 시각 (auth._iso 꼴, UTC) → 한국 시각."""
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(auth.KST)


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


def _png(v: object, limit: int) -> bytes | None:
    """직인 · 서명 그림(base64 PNG) → 바이트. PNG 머리가 아니거나 limit 바이트를 넘거나 끝까지 읽히지 않으면 None (서명 때 PDF 를 못 만드는 그림을 미리 막는다 —
    보낸 계약서의 직인은 바꿀 수 없어서, 깨진 그림이 들어가면 그 직인으로 보낸 계약서는 서명할 수 없다)."""
    try:
        raw = base64.b64decode(v, validate=True) if isinstance(v, str) else b""
        if not raw.startswith(PNG_HEAD) or len(raw) > limit:
            return None
        contract_doc._reader(raw, "그림")  # 크기를 먼저 본다(너무 크면 풀기 전에 거부 — 메모리가 늘지 않게)
        Image.open(io.BytesIO(raw)).load()  # 머리만 멀쩡하고 그림 데이터가 깨진 파일은 풀어 봐야 드러난다
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
        return None
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
    if isinstance(r["code"], str) and RRN_LIKE.fullmatch(r["code"]):  # 값은 되돌려 쓰지 않는다 (오류 글 · 로그에 남지 않게)
        return "사원코드가 주민등록번호 모양입니다 — 사번 칸을 확인해 주세요"
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


def _view(key: bytes, r: tuple, today: str, hr: bool = True) -> dict:
    """계약서 한 줄 → 사전. hr 가 거짓이면 (본인이 볼 때) 조정 사유 · 파기 사유 · 문의 글을 빼고 문의는 보낸 때 · 확인한 때만 — 칸 뒤쪽의 덤(DOC_COLS 의 셋)은 쓰지 않는다.
    풀리지 않으면 InvalidTag · ValueError."""
    cid, year, uid, ver, prev, status, start, body, sent_at, due, signed_at, replaced_on, cancelled_at, cancel_reason, ask, ask_at, closed_at, *_ = r
    b = _unlock(key, _aad(year, uid, ver, "body"), body)
    out = {
        "id": cid, "year": year, "userId": uid, "version": ver, "status": status, "start": start, "end": contract_doc.round_period(year)[1],
        **{k: b[k] for k in BODY_KEYS[:-1]},
        "annual": contract_doc.calc(b["base"], b["ability"], b["pos"], b["site"], b["qual"])["annual"],
        "sentAt": sent_at, "due": due, "late": status == "SENT" and today > due, "signedAt": signed_at, "replacedOn": replaced_on,
        "cancelledAt": cancelled_at,
        "ask": None if ask is None else {"at": ask_at, "closedAt": closed_at},
        "prevId": prev,
    }
    if hr:
        out.update(reason=b["reason"], cancelReason=cancel_reason)
        if ask is not None:
            out["ask"]["text"] = _unlock(key, _aad(year, uid, ver, "ask"), ask)
    return out


def _blocks(v: dict, company: dict, signed_at: str | None) -> list[dict]:
    """_view 한 줄(v) 과 회사 칸 → 지금 양식으로 만든 본문 블록. signed_at 이 있으면 서명한 날(한국 날짜)이 들어간다 (서명하는 순간 쓴다 — 서명한 뒤에는 그때 만든 것을 서명 기록에서 꺼낸다)."""
    return contract_doc.build_blocks(company=company, worker=v, pay=v, start=v["start"], end=v["end"],
                                     signed_on=_kst(signed_at).date().isoformat() if signed_at else None)


def _how(kind: str | None) -> str:
    """서명한 창의 로그인 종류(세션 kind) → 전자서명 정보의 "본인 확인" 글. 서명본 PDF(sign)와 화면(_doc)이 같은 글을 쓰게 한 곳에 둔다."""
    return "휴대폰 등록" if kind == "phone" else "하이웍스 로그인"


def _doc(key: bytes, r: tuple, today: str, preview: dict | None = None) -> dict:
    """본인 · HR 이 계약서를 열 때의 한 줄 (r = DOC_COLS): _view(hr=False) + blocks + hash(서명 때 저장한 문서 고유번호, 서명 전 null) + canSign(서명 대기이고 본인 것).
    본문: 서명한 줄은 서명 기록에 넣어 둔 그때의 블록 그대로(양식이 바뀌어도 서명한 본문은 그대로), 서명 전 줄은 회사 칸(발송 때 고정한 것, 발송 전 줄은 preview = 지금 설정)으로 지금 양식에서 만든다.
    서명한 줄에는 서명 기록에서 how(본인 확인 방법 글 — 서명본 PDF 와 같은 _how) · signImage(서명 그림 base64 PNG)가 더 붙는다. 서명 전 줄에는 이 두 칸이 없다."""
    v = _view(key, r, today, hr=False)
    sign, company, _, doc_hash = r[-4:]
    aad = lambda field: _aad(v["year"], v["userId"], v["version"], field)  # noqa: E731
    out = {}
    if v["signedAt"] is not None:
        rec = _unlock(key, aad("sign"), sign)
        blocks, out = rec["blocks"], {"how": _how(rec.get("kind")), "signImage": rec["image"]}
    else:
        blocks = _blocks(v, preview if company is None else _unlock(key, aad("company"), company), None)
    return {**v, "blocks": blocks, "hash": doc_hash, "canSign": v["status"] == "SENT" and v["userId"] == g.actor.user_id, **out}


def _rows(conn, key: bytes, where: str, args: tuple, *, doc: bool = False, order: str = "c.user_id, c.version") -> list[dict]:
    """조건에 맞는 계약서를 (기본: 직원 · 차수 순으로) 사전으로. doc 이면 본인이 보는 줄(_doc), 아니면 HR 줄(_view).
    풀리지 않는 줄은 건너뛴다(열쇠가 바뀌었거나 줄이 옮겨진 경우)."""
    out, today = [], auth._today().isoformat()
    for r in conn.execute(f"SELECT {DOC_COLS if doc else COLS} FROM contracts c WHERE {where} ORDER BY {order}", args):
        try:
            out.append(_doc(key, r, today) if doc else _view(key, r, today))
        except (InvalidTag, ValueError):
            logger.error(LOG_UNREADABLE, r[0])
    return out


def _seen(conn, cid: int) -> tuple:
    """볼 수 있는 계약서 한 줄(DOC_COLS). 없으면 404, 남의 것(본인과 HR 만 본다 — access.can)이면 403, 아직 발송 전인 본인 것은 직원에게 없는 것이라 404."""
    r = conn.execute(f"SELECT {DOC_COLS} FROM contracts c WHERE c.id = ?", (cid,)).fetchone()
    if r is None:
        raise kpi._Refuse(MSG_NOT_FOUND, 404)
    if not access.can(g.actor, access.Act.VIEW_CONTRACT, access.Target(user_id=r[2], team_id=None, division_id=None)):
        raise kpi._Refuse("볼 수 없는 계약서입니다.", 403)
    if r[8] is None and not g.actor.is_hr:  # 8 = sent_at
        raise kpi._Refuse(MSG_NOT_FOUND, 404)
    return r


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
    png = None
    if b.get("seal") is not None and (png := _png(b["seal"], SEAL_MAX)) is None:
        raise _bad(f"직인 그림은 {SEAL_MAX // 1000}KB 이하의 PNG 그림이어야 합니다")
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
def _round(arg: str | None) -> int:
    """회차(해) 글자 → 해. 없으면(None) 다가오는 회차, 모양이 틀리면 400."""
    if (year := contract_doc.upcoming_year(auth._today()) if arg is None else _year(arg)) is None:
        raise kpi._Refuse("회차(해)는 2000~2099 입니다.", 400)
    return year


@bp.get("/api/contracts/admin")
def admin() -> Response:
    """그 회차(기본 = 다가오는 회차)의 계약서 전부 + 서버에 열쇠가 있는지(ready) · 계약서 설정이 있는지(settings)."""
    if (denied := auth._hr_only()) is not None:
        return denied
    year = _round(request.args.get("year"))
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
        body = _opened(cid, _unlock, key, _aad(year, uid, ver, "body"), blob)
        start = b["start"] or contract_doc.round_period(year)[0]
        signed = conn.execute("SELECT starts_on FROM contracts WHERE year = ? AND user_id = ? AND status = 'SIGNED'", (year, uid)).fetchone()
        if signed and start <= signed[0]:  # 새 계약(연중 변경)으로 만든 줄 — 바뀌는 날이 지금 서명된 계약의 시작일보다 앞서거나 같으면 "바뀜"이 말이 안 된다
            raise _bad("새 계약의 계약 시작일은 지금 서명된 계약의 시작일 뒤여야 합니다")
        body.update(birth6=b["birth6"], reason=reason, **{k: b[k] for k in PAY})
        conn.execute("UPDATE contracts SET body = ?, starts_on = ? WHERE id = ?", (_lock(key, _aad(year, uid, ver, "body"), body), start, cid))
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
def _unsendable(u: _User) -> str | None:
    """보낼 수 없는 이유: 비활성 · 연봉계약서 대상 아님 · 포털 사용 꺼짐 (로그인할 수 없으면 서명할 수 없다)."""
    return _blocked(u) or (None if u.portal else "포털 사용 꺼짐")


def _cannot_send(row: tuple | None, users: dict[int, _User]) -> str | None:
    """보낼 수 없는 이유(발송 결과 skipped 의 why): 발송 전이 아님 · _unsendable."""
    return "발송 전이 아님" if row is None or row[3] != "DRAFT" else _unsendable(users[row[1]])


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
        users, now, skipped, years, sent_to = _users(conn), auth._iso(auth._now()), [], {}, []
        for cid in ids:
            row = conn.execute("SELECT year, user_id, version, status FROM contracts WHERE id = ?", (cid,)).fetchone()
            if (why := _cannot_send(row, users)) is not None:
                skipped.append({"id": cid, "why": why})
                continue
            year, uid, ver, _ = row
            conn.execute("UPDATE contracts SET status = 'SENT', sent_at = ?, sent_by = ?, due_on = ?, company = ?, seal_id = ? WHERE id = ?",
                         (now, g.actor.user_id, due, _lock(key, _aad(year, uid, ver, "company"), company), seal_id, cid))
            years[year] = years.get(year, 0) + 1
            sent_to.append(uid)
        for year, n in years.items():
            _log(conn, "CONTRACT_SEND", "contract_year", None, year=year, people=n, due=due)
    push.notify(sent_to, "contract_sign")  # 저장(COMMIT)이 끝난 뒤 — 이번에 서명 대기가 된 사람에게 알림
    return jsonify(sent=sum(years.values()), skipped=skipped)


# ---------------- 직원: 내 계약서 · 한 건 · 직인 · 서명 · 서명본 ----------------
@bp.get("/api/contracts/mine")
def mine() -> Response:
    """본인의 계약서들 — 발송된 적 있는 줄만 (발송 전은 직원에게 없는 것), 최근 회차 · 높은 차수부터. 서버에 열쇠가 없으면 올린 계약서도 없으니 빈 목록."""
    key = payslip.pay_key()
    rows = _rows(auth._db(), key, "c.user_id = ? AND c.sent_at IS NOT NULL", (g.actor.user_id,), doc=True, order="c.year DESC, c.version DESC") if key else []
    return jsonify(rows=rows)


@bp.get(f"/api/contracts/{CID}")
@_keyed
def one(key: bytes, cid: int) -> Response:
    """계약서 한 줄 + 본문 (본인 · HR). HR 가 남의 것을 보면 감사로그. 발송 전 줄(HR 만)은 지금 계약서 설정으로 미리 보여 준다."""
    conn = auth._db()
    r = _seen(conn, cid)
    preview = None
    if r[-3] is None:  # r[-3] = 발송 때 고정한 회사 칸
        cfg = _settings(conn, key)
        preview = {"name": contract_doc.COMPANY_NAME, **(cfg[0] if cfg else NO_COMPANY)}
    doc = _opened(cid, _doc, key, r, auth._today().isoformat(), preview)
    if r[2] != g.actor.user_id:
        _log(conn, "CONTRACT_VIEW", "user", r[2], contract=cid)
    return jsonify(doc)


@bp.get(f"/api/contracts/{CID}/seal")
@_keyed
def seal_of(key: bytes, cid: int) -> Response:
    """그 줄에 고정된 직인 PNG (화면이 사업주 칸에 얹는다). 발송 전 줄은 지금 설정의 직인. 없으면 404."""
    conn = auth._db()
    sid = _seen(conn, cid)[-2]
    if sid is None:
        row = conn.execute("SELECT seal_id FROM contract_settings").fetchone()
        sid = row[0] if row else None
    png = _seal_png(conn, key, sid) if sid is not None else None
    return Response(png, mimetype="image/png") if png else (jsonify(error="직인 그림이 없습니다."), 404)


@bp.get(f"/api/contracts/{CID}/pdf")
@_keyed
def pdf_get(key: bytes, cid: int) -> Response:
    """서명할 때 만들어 잠가 둔 서명본 PDF 를 그대로 (양식 코드가 바뀌어도 서명본은 그대로). 서명한 줄만 — 새 계약으로 바뀐 줄 · 서명 뒤 파기한 줄도 포함."""
    conn = auth._db()
    _, year, uid, ver, *_ = _seen(conn, cid)
    row = conn.execute("SELECT pdf FROM contracts WHERE id = ? AND signed_at IS NOT NULL", (cid,)).fetchone()
    if row is None:
        raise kpi._Refuse("서명본이 없습니다.", 404)
    pdf = _opened(cid, _unseal, key, _aad(year, uid, ver, "pdf"), row[0])
    if uid != g.actor.user_id:
        _log(conn, "CONTRACT_PDF_VIEW", "user", uid, contract=cid, year=year)
    return Response(pdf, mimetype="application/pdf", headers={"Content-Disposition": f'inline; filename="contract_{year}.pdf"'})


@bp.post(f"/api/contracts/{CID}/sign")
@_keyed
def sign(key: bytes, cid: int) -> Response:
    """서명 (본인). 본인 것 확인 → 입력 검사 → 서명본 PDF 와 문서 고유번호 만들기 (DB 를 잡기 전에) → 한 트랜잭션에서 상태를 다시 보고 저장.
    새 계약(연중 변경)이면 같은 트랜잭션에서 옛 서명 완료 줄을 먼저 '새 계약으로 바뀜'으로 바꾼다 — 서명 완료가 둘인 순간이 없다. 마감이 지나도 받는다."""
    b = _json()
    conn, me = auth._db(), g.actor.user_id
    own = conn.execute("SELECT user_id, sent_at FROM contracts WHERE id = ?", (cid,)).fetchone()
    if own is None or own[1] is None:  # 발송 전 줄은 직원에게 없는 것
        raise kpi._Refuse(MSG_NOT_FOUND, 404)
    if own[0] != me:
        raise kpi._Refuse("본인의 계약서만 서명할 수 있습니다.", 403)
    if set(b) != SIGN_KEYS:
        raise _bad("칸이 맞지 않습니다")
    name = conn.execute("SELECT name FROM users WHERE id = ?", (me,)).fetchone()[0]
    if b["consent"] is not True:
        raise kpi._Refuse("계약 내용을 읽고 이해했으며 동의한다는 데 체크해 주세요.", 400)
    if b["name"] != name:  # 입력한 그대로 본인 이름과 같아야 한다 (앞뒤 공백도 틀린 것 — 화면이 잘라서 보낸다)
        raise kpi._Refuse("성명이 본인 이름과 같지 않아요.", 400)
    if (image := _png(b["image"], SIGN_MAX)) is None:
        raise kpi._Refuse(MSG_SIGN_IMG, 400)
    r = conn.execute(f"SELECT {DOC_COLS} FROM contracts c WHERE c.id = ?", (cid,)).fetchone()
    _, year, _, ver, _, status, *_ = r
    company_blob, seal_id, _ = r[-3:]  # r 의 맨 뒤 셋 (sign 은 아직 없다)
    if status != "SENT":  # 빠른 거절 — 아래 트랜잭션에서 한 번 더 본다
        raise kpi._Refuse(MSG_NOT_SENT, 409)
    v, company = _opened(cid, lambda: (_view(key, r, auth._today().isoformat(), hr=False), _unlock(key, _aad(year, me, ver, "company"), company_blob)))
    if v["name"] != name:  # 계약서에 적힌 이름은 올릴 때 고정한 것 — 지금 이름과 다르면 서명본의 근로자와 서명한 사람이 어긋난다 (HR 이 다시 만들어야 한다)
        raise kpi._Refuse("계약서에 적힌 이름과 지금 이름이 달라요. 인사담당자에게 문의해 주세요.", 409)
    if (seal := _seal_png(conn, key, seal_id)) is None:
        raise kpi._Refuse(MSG_UNREADABLE, 409)
    signed_at = auth._iso(auth._now())
    blocks = _blocks(v, company, signed_at)
    tpl, salt = contract_doc.TPL, os.urandom(16).hex()  # 소금은 서명 기록(잠금)에만 둔다 — 고유번호가 금액을 맞혀 보는 데 쓰이지 않게
    digest = contract_doc.doc_hash(blocks, salt, tpl)
    esign = {"signedAt": _kst(signed_at).strftime("%Y-%m-%d %H:%M"), "name": name, "hash": digest,
             "how": _how(g.session_kind)}
    try:
        pdf = contract_doc.render_pdf(blocks, seal_png=seal, sign_png=image, esign=esign)
    except Exception as e:  # 그림이 깨졌거나 너무 큼 — 서명본을 못 만들면 서명도 받지 않는다 (아직 저장한 것 없음)
        logger.error("연봉계약서 서명본을 만들 수 없음: 계약서 %s (%s)", cid, type(e).__name__)
        raise kpi._Refuse(MSG_SIGN_IMG, 400) from None
    record = {"image": base64.b64encode(image).decode(), "name": name, "consent": True, "ip": auth._client_ip(),
              "ua": request.headers.get("User-Agent", "")[:300], "kind": g.session_kind,
              "blocks": blocks, "tpl": tpl, "salt": salt}  # 해시를 매기고 PDF 에 그린 본문 그대로 — 나중에 양식이 바뀌어도 서명한 본문을 이걸로 내준다
    with kpi._txn(conn):
        if conn.execute("SELECT status FROM contracts WHERE id = ?", (cid,)).fetchone()[0] != "SENT":
            raise kpi._Refuse(MSG_NOT_SENT, 409)
        old = conn.execute("SELECT id FROM contracts WHERE year = ? AND user_id = ? AND status = 'SIGNED'", (year, me)).fetchone()
        if old is not None:  # 새 계약 — 먼저 옛 줄을 '바뀜'으로 (유일 색인이 문장마다 검사하므로 이 순서)
            conn.execute("UPDATE contracts SET status = 'REPLACED', replaced_on = ? WHERE id = ?", (v["start"], old[0]))
        conn.execute("UPDATE contracts SET status = 'SIGNED', signed_at = ?, sign = ?, doc_hash = ?, pdf = ? WHERE id = ?",
                     (signed_at, _lock(key, _aad(year, me, ver, "sign"), record), digest, _seal(key, _aad(year, me, ver, "pdf"), pdf), cid))
        _log(conn, "CONTRACT_SIGNED", "contract", cid, contract=cid, hash=digest)
        if old is not None:
            _log(conn, "CONTRACT_REPLACED", "contract", cid, old=old[0], new=cid)
    return jsonify(signedAt=signed_at, hash=digest)


# ---------------- HR: 파기 · 새 계약으로 바꾸기 ----------------
def _follow(conn, key: bytes, prev: int, year: int, uid: int, start: str, body: dict, sent: tuple | None = None) -> int:
    """다음 차수 줄을 넣고 그 번호를 돌려준다 (앞 차수 = prev). 잠금은 새 차수 자리(aad)로 다시 한다 — 풀어 둔 내용을 받는 까닭.
    sent = (마감일, 회사 칸 사전, 직인 번호) 이면 바로 서명 대기(SENT), 아니면 발송 전(DRAFT)."""
    ver = conn.execute("SELECT MAX(version) + 1 FROM contracts WHERE year = ? AND user_id = ?", (year, uid)).fetchone()[0]
    now, me = auth._iso(auth._now()), g.actor.user_id
    cols = {"year": year, "user_id": uid, "version": ver, "prev_id": prev, "status": "SENT" if sent else "DRAFT", "starts_on": start,
            "body": _lock(key, _aad(year, uid, ver, "body"), body), "created_at": now, "created_by": me}
    if sent:
        due, company, seal_id = sent
        cols.update(sent_at=now, sent_by=me, due_on=due, company=_lock(key, _aad(year, uid, ver, "company"), company), seal_id=seal_id)
    return conn.execute(f"INSERT INTO contracts ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", tuple(cols.values())).lastrowid


@bp.post(f"/api/contracts/{CID}/cancel")
@_hr_write
def cancel(key: bytes, cid: int) -> Response:
    """서명 대기 · 서명 완료 줄을 파기 (HR). 파기한 줄은 지우지 않고 남는다(서명한 줄이면 서명본도). then = resend (같은 내용 · 마감일로 다음 차수를 바로 서명 대기로) ·
    draft (다음 차수를 발송 전으로) · none (끝). 다음 차수를 만드는데 그 직원에게 이 회차에 진행 중인 다른 줄 (발송 전 · 서명 대기, 예: 새 계약) 이 있으면 409."""
    b = _json()
    if set(b) != {"reason", "then"} or b["then"] not in THEN:
        raise _bad("칸이 맞지 않습니다")
    reason, then = _text(b["reason"], "파기 사유", REASON_MAX), b["then"]
    conn = auth._db()
    with kpi._txn(conn):
        row = conn.execute("SELECT year, user_id, version, status, starts_on, body, due_on, company, seal_id FROM contracts WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise kpi._Refuse(MSG_NOT_FOUND, 404)
        year, uid, ver, status, start, body, due, company, seal_id = row
        if status not in ("SENT", "SIGNED"):
            raise kpi._Refuse("서명 대기나 서명 완료인 계약서만 파기할 수 있습니다.", 409)
        content = co = None
        if then != "none":
            if conn.execute("SELECT 1 FROM contracts WHERE year = ? AND user_id = ? AND status IN ('DRAFT', 'SENT') AND id <> ?", (year, uid, cid)).fetchone():
                raise kpi._Refuse("이 직원에게는 이 회차에 진행 중인 다른 계약서가 있어요. 그 계약서를 먼저 빼거나 파기해 주세요.", 409)
            if then == "resend" and (why := _unsendable(_users(conn)[uid])):
                raise kpi._Refuse(f"다시 보낼 수 없습니다 ({why})", 409)
            content = _opened(cid, _unlock, key, _aad(year, uid, ver, "body"), body)
            co = _opened(cid, _unlock, key, _aad(year, uid, ver, "company"), company) if then == "resend" else None
        conn.execute("UPDATE contracts SET status = 'CANCELLED', cancelled_at = ?, cancelled_by = ?, cancel_reason = ? WHERE id = ?",
                     (auth._iso(auth._now()), g.actor.user_id, reason, cid))
        new = None if then == "none" else _follow(conn, key, cid, year, uid, start, content, (due, co, seal_id) if then == "resend" else None)
        _log(conn, "CONTRACT_CANCELLED", "contract", cid, contract=cid, then=then)
    if then == "resend":  # 새 서명 대기 줄이 생긴 경우만 (draft 는 발송 전 · none 은 끝)
        push.notify([uid], "contract_sign")
    return jsonify(ok=True, id=new)


@bp.post(f"/api/contracts/{CID}/replace")
@_hr_write
def replace(key: bytes, cid: int) -> Response:
    """서명 완료 줄을 새 계약으로 바꾸기 (연중 변경, HR). 새 금액 · 바뀌는 날(회차 안 · 지금 계약 시작일 뒤) · 사유로 다음 차수를 발송 전으로 만든다 —
    이름 · 소속 · 직급 · 생년월일 · 사원코드는 이 줄 그대로. 발송하고 직원이 서명할 때 이 줄이 '새 계약으로 바뀜'이 된다(그 전까지는 이 줄이 유효)."""
    b = _json()
    conn = auth._db()
    with kpi._txn(conn):
        row = conn.execute("SELECT year, user_id, version, status, starts_on, body FROM contracts WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise kpi._Refuse(MSG_NOT_FOUND, 404)
        year, uid, ver, status, start, blob = row
        if status != "SIGNED":
            raise kpi._Refuse("서명 완료한 계약서만 새 계약으로 바꿀 수 있습니다.", 409)
        if set(b) != REPLACE_KEYS:
            raise _bad("칸이 맞지 않습니다")
        reason = _text(b["reason"], "변경 사유", REASON_MAX)
        body = _opened(cid, _unlock, key, _aad(year, uid, ver, "body"), blob)
        if (why := _pay_error({**b, "birth6": body["birth6"]}, year)) is not None:
            raise _bad(why)
        if b["start"] is None or b["start"] <= start:
            raise _bad("바뀌는 날은 지금 계약 시작일보다 뒤여야 합니다")
        if conn.execute("SELECT 1 FROM contracts WHERE year = ? AND user_id = ? AND status IN ('DRAFT', 'SENT')", (year, uid)).fetchone():
            raise kpi._Refuse("이 직원에게는 이 회차에 진행 중인 계약서가 이미 있어요. 그 계약서를 먼저 빼거나 파기해 주세요.", 409)
        new = _follow(conn, key, cid, year, uid, b["start"], {**body, **{k: b[k] for k in PAY}, "reason": reason})
        _log(conn, "CONTRACT_REPLACE", "contract", cid, contract=cid)
    return jsonify(ok=True, id=new)


# ---------------- 문의 · 본부장 현황 · 서명본 묶음 ----------------
@bp.post(f"/api/contracts/{CID}/ask")
@_keyed
def ask(key: bytes, cid: int) -> Response:
    """HR 에 문의 (본인, 서명 대기 줄만). 글은 잠가 저장하고 감사로그에는 계약서 번호만 남긴다. 확인 전 문의가 있으면 409 —
    HR 이 확인한 뒤에는 다시 보낼 수 있다(그때 확인 표시를 비운다). 문의 중에도 서명은 할 수 있다."""
    b = _json()
    conn, me = auth._db(), g.actor.user_id
    with kpi._txn(conn):
        row = conn.execute("SELECT user_id, year, version, status, sent_at, ask, ask_closed_at FROM contracts WHERE id = ?", (cid,)).fetchone()
        if row is None or row[4] is None:  # 발송 전 줄은 직원에게 없는 것
            raise kpi._Refuse(MSG_NOT_FOUND, 404)
        uid, year, ver, status, _, old, closed = row
        if uid != me:
            raise kpi._Refuse("본인의 계약서에만 문의할 수 있습니다.", 403)
        if set(b) != {"text"}:
            raise _bad("칸이 맞지 않습니다")
        text = _text(b["text"], "문의 글", ASK_MAX)
        if status != "SENT":
            raise kpi._Refuse("서명 대기 중인 계약서에만 문의할 수 있어요.", 409)
        if old is not None and closed is None:
            raise kpi._Refuse("확인 전인 문의가 있어요. HR이 확인하면 다시 문의할 수 있어요.", 409)
        conn.execute("UPDATE contracts SET ask = ?, ask_at = ?, ask_closed_at = NULL, ask_closed_by = NULL WHERE id = ?",
                     (_lock(key, _aad(year, me, ver, "ask"), text), auth._iso(auth._now()), cid))
        _log(conn, "CONTRACT_ASK", "contract", cid, contract=cid)
    push.notify(lambda: push.hr_admins(conn), "contract_ask")  # 알림만 — 처리할 일 개수에는 넣지 않는다 (계약서 화면 안의 숫자)
    return jsonify(ok=True)


@bp.post(f"/api/contracts/{CID}/ask/close")
def ask_close(cid: int) -> Response:
    """문의를 "확인함"으로 (HR). 글은 그대로 두고 확인한 때와 사람만 적는다 — 서명한 뒤나 파기한 줄의 문의도 확인할 수 있다. 문의가 없거나 이미 확인했으면 409."""
    if (denied := auth._hr_only()) is not None:
        return denied
    conn = auth._db()
    with kpi._txn(conn):
        row = conn.execute("SELECT ask, ask_closed_at FROM contracts WHERE id = ?", (cid,)).fetchone()
        if row is None:
            raise kpi._Refuse(MSG_NOT_FOUND, 404)
        if row[0] is None or row[1] is not None:
            raise kpi._Refuse("확인할 문의가 없어요.", 409)
        conn.execute("UPDATE contracts SET ask_closed_at = ?, ask_closed_by = ? WHERE id = ?", (auth._iso(auth._now()), g.actor.user_id, cid))
        _log(conn, "CONTRACT_ASK_CLOSED", "contract", cid, contract=cid)
    return jsonify(ok=True)


@bp.get("/api/contracts/division")
def division() -> Response:
    """본부장 현황: 자기 본부 사람의 살아 있는 계약서 (발송 전 · 서명 대기 · 서명 완료) 의 이름 · 팀 · 상태 · 마감 지남 · 서명한 날 · 마감일만.
    누가 볼 수 있는지는 줄마다 access.can (Act.VIEW_CONTRACT_STATUS) 이 정한다. 잠근 내용은 열지 않으므로 금액 · 생년월일 · 차수 · 문의는 응답에 넣을 수 없다."""
    if g.actor.position is not Role.DIVISION_HEAD:
        raise kpi._Refuse("본부장만 볼 수 있어요.", 403)
    year, today, rows, conn = _round(request.args.get("year")), auth._today().isoformat(), [], auth._db()
    people, depts = kpi.load_org(conn)  # 그 사람이 어느 팀 · 본부인지는 다른 곳 (KPI 현황 등) 과 같은 규칙 org.target_of 으로 — 팀장은 이끄는 팀, 대표이사 직속 팀 · 임원은 본부 없음
    by = {p.id: p for p in people}
    for status, due, signed_at, uid, name, team in conn.execute(
            "SELECT c.status, c.due_on, c.signed_at, u.id, u.name, d.name"
            " FROM contracts c JOIN users u ON u.id = c.user_id JOIN departments d ON d.id = u.department_id"
            " WHERE c.year = ? AND c.status IN ('DRAFT', 'SENT', 'SIGNED') ORDER BY d.name, u.name, c.version", (year,)):
        if access.can(g.actor, access.Act.VIEW_CONTRACT_STATUS, target_of(by[uid], depts)):
            rows.append({"name": name, "team": team, "status": status, "late": status == "SENT" and today > due,
                         "signedOn": _kst(signed_at).date().isoformat() if signed_at else None, "due": due})
    return jsonify(year=year, rows=rows)


@bp.get("/api/contracts/<year>/zip")
@_hr_write
def zip_get(key: bytes, year: str) -> Response:
    """그 회차 서명 완료 · 새 계약으로 바뀐 줄 (바뀌기 전까지 유효했던 계약) 의 서명본 PDF 를 zip 하나로 (HR, 보관용): YYYY_사원코드 (없으면 직원 번호)_이름.pdf
    (2차부터는 이름 뒤에 _2차) 들과 서명정보.csv (PDF 한 개에 한 줄). 파기한 줄은 넣지 않는다. zip 에는 암호가 없다
    (파이썬 표준 기능으로는 암호 zip 을 만들 수 없다 — 화면이 NAS 에 둘 때 다시 암호를 걸라고 안내한다). 풀리지 않는 줄은 건너뛰고 (서버 로그에 계약서 번호만), 하나도 없으면 404."""
    y, today = _round(year), auth._today().isoformat()
    conn, buf, sheet, taken = auth._db(), io.BytesIO(), [], set()  # 메모리에 만든다 — 한 회차 수백 명(수십 MB)까지, 더 커지면 임시 파일로
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in conn.execute(f"SELECT {COLS}, c.doc_hash, c.pdf FROM contracts c WHERE c.year = ? AND c.status IN ('SIGNED', 'REPLACED') ORDER BY c.user_id, c.version", (y,)):
            try:
                v = _view(key, r, today, hr=False)
                pdf = _unseal(key, _aad(y, v["userId"], v["version"], "pdf"), r[-1])
            except (InvalidTag, ValueError):
                logger.error(LOG_UNREADABLE, r[0])
                continue
            stem = BAD_FILE_CHARS.sub("_", f"{y}_{v['code'] or v['userId']}_{v['name']}" + (f"_{v['version']}차" if v["version"] > 1 else ""))
            member, n = f"{stem}.pdf", 1
            while member in taken:  # 이름이 겹치면 _2, _3 …
                n += 1
                member = f"{stem}_{n}.pdf"
            taken.add(member)
            z.writestr(member, pdf)
            sheet.append((v["name"], v["code"], v["version"], _kst(v["signedAt"]).strftime("%Y-%m-%d %H:%M"), r[-2]))
        if sheet:
            out = io.StringIO()
            w = csv.writer(out, lineterminator="\r\n")
            w.writerow(ZIP_HEAD)
            w.writerows(sheet)
            z.writestr("서명정보.csv", out.getvalue().encode("utf-8-sig"))  # BOM 이 있어야 엑셀이 한글을 바로 읽는다
    if not sheet:
        raise kpi._Refuse("내려받을 서명본이 없습니다.", 404)
    _log(conn, "CONTRACT_ZIP", "contract_year", None, year=y, people=len(sheet))
    return Response(buf.getvalue(), mimetype="application/zip", headers={"Content-Disposition": f'attachment; filename="contracts_{y}.zip"'})
