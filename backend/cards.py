"""축하 · 칭찬 카드 (2026-10-26 인사담당자 — 직원끼리 짧은 카드를 주고받는다. 설계 docs/superpowers/specs/2026-10-26-praise-cards-design.md).
표는 021_cards.sql. 카드 글은 서버 .env 의 HR_PAY_KEY 로 잠가 둔다(급여명세서 · 연봉계약서와 같은 열쇠 — payslip.pay_key). 나머지 칸은 잠그지 않는다.

  KINDS · REASONS       카드 종류(cheer 축하 · praise 칭찬 · thanks 감사 · comfort 위로) · 축하 사유(birthday 생일 · welcome 새 식구 · anniv 입사 기념 · joy 경사)
  reasons_for()         그 사람이 지금 받을 수 있는 축하 사유와, 조사 게시 중인지 — 홈 "이번 주 소식"(auth.news_items · celeb_events)과 같은 규칙
  options()             그 사람에게 보낼 수 있는 카드 (조사 중이면 위로만)
  clean_text()          카드 글 검사 — 1~150자, 세 줄까지, 쓸 수 없는 글자 없음. 아니면 CardError(400)
  seal_text() · unseal_text()   글 잠그기 · 풀기. 잠근 글 = 12바이트 nonce + AES-GCM(내용, aad="card|보낸 사람|받는 사람|보낸 날") —
                        다른 날 · 다른 사람 자리로 옮겨 붙이면 풀리지 않는다(InvalidTag)
  unread()              안 읽은 받은 카드 수 (열쇠가 없으면 0)
  python -m cards check 위 규칙 자체 검증 (CI)

app.py 가 bp 를 붙인다. 세션 검사 · 같은 출처 검사는 auth._guard 가 모든 /api/* 에 한다. 오류는 CardError(문구, 상태)를 던지면 blueprint 의 errorhandler 가 {"error": 문구} 로 답한다(트랜잭션 안에서도 같다). 카드 길은 급여 비밀번호 앞머리(paypin.PREFIXES)가 아니다.
  GET  /api/cards              {"ready", "unread", "received", "sent"} — 최근이 위, 각 LIST_MAX 장. 열쇠가 없으면 ready False · 빈 목록 · 안 읽음 0.
                               받은 줄 {id, kind, reason, art, text, date, from: {id, name, title, dept}, read, thanked, hidden} · 보낸 줄 {…, to: {…}, read, thanked}
  GET  /api/cards/options?to=  {"options", "sentToday"} — 받을 수 없는 사람 404
  POST /api/cards              {"to", "kind", "reason", "art", "text"} → 201 {"id"} — 모양 400 · 받을 수 없음 404 · 지금 못 보내는 종류 403 · 오늘 이미 보냄 409 · 열쇠 없음 503
  POST /api/cards/<id>/read | thanks | hide {"hidden"}   받은 사람만 → 200 {"unread"} (남의 카드는 HR 도 404). 고치는 칸은 read_at · thanked_at · hidden_at 뿐
카드 일은 감사로그에 남기지 않는다(누가 누구에게 보냈는지가 새지 않게). 서버 로그에도 이름 · 글은 없다.
알림: 보내기를 저장(commit)한 뒤 받는 사람에게 push "card", 처음 [고마워요]를 누른 뒤(이미 눌렀으면 아무것도 안 바뀌어 안 보냄) 보낸 사람에게 push "card_thanks" — push.notify 규칙 그대로(평일 9~18시 · 휴대폰 등록한 사람),
알림이 실패해도 응답은 그대로. 알림에는 이름 · 글이 없다. 안 읽은 받은 카드는 push.pending 이 처리할 일 "cards" 로 센다.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import sys
from datetime import date
from sqlite3 import Connection

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from flask import Blueprint, Response, g, jsonify, request

import auth
import payslip
import push

bp = Blueprint("cards", __name__)
logger = logging.getLogger(__name__)

KINDS = ("cheer", "praise", "thanks", "comfort")
REASONS = ("birthday", "welcome", "anniv", "joy")
TEXT_MAX = 150  # 카드 글 글자 수
LINES_MAX = 3  # 카드 글 줄 수 (줄바꿈은 2번까지)
LIST_MAX = 500  # 상자에 한 번에 담는 받은 카드 · 보낸 카드 각각의 장 수 (최근 것부터)

_NEWS_REASON = {"birthday": "birthday", "welcome": "welcome", "anniversary": "anniv"}  # auth.news_items 의 kind → 축하 사유
# 카드 글 다듬기 — 화면 cardText 와 같은 판정이어야 한다(cardSelfCheck 에 cards.check 와 같은 표). 화면은 JS trim · \s 로 같은 목록을 쓴다
_NEWLINE = re.compile("\r\n?|[\u2028\u2029\x85]")  # 줄 나눔 글자는 모두 \n 으로 바꿔 센다
_SPACE = " \t\xa0\u1680\u2000-\u200a\u202f\u205f\u3000\ufeff"  # 줄 앞뒤에서 떼는 빈칸 (정규식 [] 안) — str.strip 의 목록(제어문자 · 줄 나눔 글자는 위에서 막거나 바꿈)에 BOM 을 더한 것 = JS trim
_EDGE = re.compile(f"^[{_SPACE}]+|[{_SPACE}]+$")
_UNSEEN = re.compile(f"[\n{_SPACE}\u200b-\u200f\u2060\u3164]")  # 보이지 않는 글자: 빈칸 · 줄바꿈 · 폭 없는 글자 · 한글 채움 문자 — 이것뿐이면 빈 글


class CardError(Exception):
    """사용자에게 보일 문구(msg)와 HTTP 상태(status)."""

    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(msg)
        self.msg, self.status = msg, status


def reasons_for(conn: Connection, uid: int, today: date) -> tuple[list[str], bool]:
    """(그 사람의 지금 축하 사유 — REASONS 순서, 조사 게시 중인가). 조직도에 보이는 사람(활성 · 조직도에서 안 뺌)만 — 아니면 ([], False)."""
    row = conn.execute("SELECT id, hire_date, birthday, birthday_public FROM users WHERE id = ? AND is_active = 1 AND org_hidden = 0", (uid,)).fetchone()
    if row is None:
        return [], False
    got = {_NEWS_REASON[n["kind"]] for n in auth.news_items([tuple(row)], today)}
    kinds = {k for (k,) in conn.execute("SELECT kind FROM celeb_events WHERE user_id = ? AND removed_at IS NULL AND until_date >= ?",
                                        (uid, today.isoformat()))}
    if "joy" in kinds:
        got.add("joy")
    return [r for r in REASONS if r in got], "condol" in kinds


def options(reasons: list[str], condol: bool) -> list[dict]:
    """보낼 수 있는 카드 목록. 조사 중이면 위로만, 아니면 사유마다 축하 + 칭찬 + 감사."""
    if condol:
        return [{"kind": "comfort", "reason": None}]
    return [{"kind": "cheer", "reason": r} for r in reasons] + [{"kind": "praise", "reason": None}, {"kind": "thanks", "reason": None}]


def clean_text(v: object) -> str:
    """카드 글: 글자만, 줄마다 앞뒤 빈칸(_SPACE)을 뗀 1~TEXT_MAX자, LINES_MAX줄까지 — 아니면 CardError(400). \\r\\n · 혼자 있는 \\r · U+2028 · U+2029 · U+0085 는 \\n 으로 바꿔 센다.
    보이는 글자가 없으면(빈칸 · 폭 없는 글자뿐 — _UNSEEN) 빈 글로 본다.
    글자 규칙은 kpi._chars 와 같다: UTF-8 로 쓸 수 있어야 하고(짝 없는 서로게이트 없음) 탭 · 줄바꿈 · CR 밖의 제어문자(DEL 포함)가 없어야 한다.
    특히 NUL 은 SQLite length() 가 거기서 멈춘다. 빈칸을 떼기 전의 글로 본다 — 제어문자가 가장자리에 있어도 똑같이 막는다."""
    if not isinstance(v, str):
        raise CardError("글을 적어 주세요.")
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise CardError("글에 쓸 수 없는 글자가 있어요.") from None
    if any(ord(ch) == 127 or (ord(ch) < 32 and ch not in "\t\n\r") for ch in v):
        raise CardError("글에 쓸 수 없는 글자가 있어요.")
    t = "\n".join(_EDGE.sub("", ln) for ln in _NEWLINE.sub("\n", v).split("\n")).strip("\n")
    if not _UNSEEN.sub("", t):
        raise CardError("글을 적어 주세요.")
    if len(t) > TEXT_MAX:
        raise CardError(f"글은 {TEXT_MAX}자까지 쓸 수 있어요.")
    if t.count("\n") >= LINES_MAX:
        raise CardError("글은 세 줄까지 쓸 수 있어요.")
    return t


def _aad(sender: int, recipient: int, sent_on: str) -> bytes:
    return f"card|{sender}|{recipient}|{sent_on}".encode()


def seal_text(key: bytes, sender: int, recipient: int, sent_on: str, text: str) -> bytes:
    """카드 글을 잠근다 (12바이트 nonce + 내용 + 태그). aad 에 보낸 사람 · 받는 사람 · 보낸 날을 묶어 하나라도 다르면 풀리지 않는다."""
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, text.encode(), _aad(sender, recipient, sent_on))


def unseal_text(key: bytes, sender: int, recipient: int, sent_on: str, blob: bytes) -> str:
    """seal_text 의 반대. 열쇠 · 사람 · 날이 다르거나 글이 바뀌었으면 InvalidTag."""
    return AESGCM(key).decrypt(blob[:12], blob[12:], _aad(sender, recipient, sent_on)).decode()


def unread(conn: Connection, uid: int) -> int:
    """이 사람이 아직 안 읽은 받은 카드 수. 잠금 열쇠가 없으면 카드를 못 열므로 0."""
    if payslip.pay_key() is None:
        return 0
    return conn.execute("SELECT COUNT(*) FROM cards WHERE recipient_id = ? AND read_at IS NULL", (uid,)).fetchone()[0]


# ---------------- 길 — 시각은 auth._now() · 오늘은 auth._today() (검사가 시계를 바꿀 수 있게 모듈로 부른다) ----------------
MSG_SHAPE = "보낸 내용의 모양이 올바르지 않아요."
MSG_WHO = "카드를 받을 수 없는 분이에요."
MSG_NOT_NOW = "지금은 이 카드를 보낼 수 없어요."
MSG_ONCE = "오늘은 이미 이 분께 카드를 보냈어요. 내일 다시 보내 주세요."
MSG_NO_KEY = "서버 설정이 아직 끝나지 않아 카드를 보낼 수 없어요."
MSG_GONE = "카드를 찾을 수 없어요."

_MARK = {  # 받은 사람이 누르는 것 — 고치는 칸은 read_at · thanked_at · hidden_at 뿐(내용 칸은 트리거가 막는다). 두 번째부터는 아무 줄도 고치지 않게 WHERE 로 거른다. ?1 = 지금, ?2 = 카드 번호
    "read": "UPDATE cards SET read_at = ?1 WHERE id = ?2 AND read_at IS NULL",
    "thanks": "UPDATE cards SET read_at = COALESCE(read_at, ?1), thanked_at = ?1 WHERE id = ?2 AND thanked_at IS NULL",
    "hide": "UPDATE cards SET read_at = COALESCE(read_at, ?1), hidden_at = COALESCE(hidden_at, ?1) WHERE id = ?2 AND (hidden_at IS NULL OR read_at IS NULL)",
    "show": "UPDATE cards SET hidden_at = NULL WHERE id = ?2 AND hidden_at IS NOT NULL",
}


@bp.errorhandler(CardError)
def _fail(e: CardError) -> tuple[Response, int]:
    return jsonify(error=e.msg), e.status


def _num(v: str) -> int | None:
    """주소에 든 번호 → 정수. 숫자가 아니거나 너무 길면 None."""
    return int(v) if v.isascii() and v.isdigit() and len(v) <= 9 else None


def _receivable(conn: Connection, me: int, to: int) -> bool:
    """카드를 받을 수 있는 사람인가 — 본인이 아니고, 활성이고, 조직도에서 안 뺀 사람. 포털 사용이 꺼진 사람도 받는다(켜진 뒤 로그인하면 와 있다)."""
    return to != me and conn.execute("SELECT 1 FROM users WHERE id = ? AND is_active = 1 AND org_hidden = 0", (to,)).fetchone() is not None


def _sent_today(conn: Connection, me: int, to: int, day: str) -> bool:
    return conn.execute("SELECT 1 FROM cards WHERE sender_id = ? AND recipient_id = ? AND sent_on = ?", (me, to, day)).fetchone() is not None


def _box(conn: Connection, key: bytes, me: int, received: bool) -> list[dict]:
    """내가 받은(received) 또는 보낸 카드 — 최근이 위, LIST_MAX 장까지. 상대 사람은 이름 · 직급 · 부서만."""
    mine, other = ("recipient_id", "sender_id") if received else ("sender_id", "recipient_id")
    out, skipped = [], 0
    for cid, kind, reason, art, blob, day, snd, rcv, read_at, thanked_at, hidden_at, uid, name, title, dept in conn.execute(
        "SELECT c.id, c.kind, c.reason, c.art, c.body, c.sent_on, c.sender_id, c.recipient_id, c.read_at, c.thanked_at, c.hidden_at, u.id, u.name, u.title, d.name"
        f" FROM cards c JOIN users u ON u.id = c.{other} LEFT JOIN departments d ON d.id = u.department_id WHERE c.{mine} = ? ORDER BY c.id DESC LIMIT ?",
        (me, LIST_MAX),
    ):
        try:
            text = unseal_text(key, snd, rcv, day, blob)
        except (InvalidTag, ValueError):  # HR_PAY_KEY 가 바뀐 경우뿐 — 500 으로 상자 전체를 막지 않고 그 줄만 뺀다
            skipped += 1
            continue
        row = {"id": cid, "kind": kind, "reason": reason, "art": art, "text": text, "date": day, "read": read_at is not None, "thanked": thanked_at is not None,
               "from" if received else "to": {"id": uid, "name": name, "title": title or "", "dept": dept or ""}}
        if received:
            row["hidden"] = hidden_at is not None
        out.append(row)
    if skipped:  # 요청마다 한 번, 장 수만 — 이름 · 글 · 번호는 적지 않는다
        logger.error("카드 글을 풀 수 없어 %s장을 목록에서 뺐음 — HR_PAY_KEY 가 바뀌었는지 확인", skipped)
    return out


@bp.get("/api/cards")
def box() -> Response:
    """내 카드 상자 {"ready", "unread", "received", "sent"}. 열쇠(HR_PAY_KEY)가 없으면 글을 열 수 없으므로 ready False · 빈 목록 · 안 읽음 0."""
    if (key := payslip.pay_key()) is None:
        return jsonify(ready=False, unread=0, received=[], sent=[])
    conn, me = auth._db(), g.actor.user_id
    return jsonify(ready=True, unread=unread(conn, me), received=_box(conn, key, me, True), sent=_box(conn, key, me, False))


@bp.get("/api/cards/options")
def options_of() -> Response:
    """?to=<직원 번호> — 그 사람에게 지금 보낼 수 있는 카드와, 오늘 이미 보냈는지. 받을 수 없는 사람(숫자가 아닌 값 포함)은 404."""
    conn, me, today = auth._db(), g.actor.user_id, auth._today()
    to = _num(request.args.get("to", ""))
    if to is None or not _receivable(conn, me, to):
        raise CardError(MSG_WHO, 404)
    return jsonify(options=options(*reasons_for(conn, to, today)), sentToday=_sent_today(conn, me, to, today.isoformat()))


def _store(conn: Connection, key: bytes, me: int, to: int, kind: str, reason: str | None, art: int, text: str, today: date) -> int:
    """BEGIN IMMEDIATE 안에서: 받을 수 있는 사람인가(404) → 지금 보낼 수 있는 종류인가(403) → 오늘 이미 보냈나(409) → 넣기. 새 카드 번호. 안 되면 CardError."""
    day = today.isoformat()
    if not _receivable(conn, me, to):
        raise CardError(MSG_WHO, 404)
    if {"kind": kind, "reason": reason} not in options(*reasons_for(conn, to, today)):
        raise CardError(MSG_NOT_NOW, 403)
    if _sent_today(conn, me, to, day):
        raise CardError(MSG_ONCE, 409)
    try:
        return conn.execute("INSERT INTO cards (sender_id, recipient_id, kind, reason, art, body, sent_on, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (me, to, kind, reason, art, seal_text(key, me, to, day, text), day, auth._iso(auth._now()))).lastrowid
    except sqlite3.IntegrityError as e:  # 위 검사를 빠져나간 같은 날 두 번째도 표의 UNIQUE 가 막는다
        if "UNIQUE" not in str(e):
            raise
        raise CardError(MSG_ONCE, 409) from None


@bp.post("/api/cards")
def send() -> Response:
    """{"to", "kind", "reason", "art", "text"} 카드 보내기 → 201 {"id"}. 글은 잠가서 넣는다. 열쇠가 없으면 503, 모양이 틀리면 400(글은 clean_text 의 문구)."""
    if (key := payslip.pay_key()) is None:
        raise CardError(MSG_NO_KEY, 503)
    b = request.get_json(silent=True)
    b = b if isinstance(b, dict) else {}
    to, kind, reason, art = b.get("to"), b.get("kind"), b.get("reason"), b.get("art")
    if not (type(to) is int and 0 < to < 2**31 and kind in KINDS and (reason is None or reason in REASONS) and (kind == "cheer") == (reason is not None)
            and type(art) is int and 1 <= art <= 4):  # type() is int: True · 1.0 · "1" 을 정수로 치지 않는다
        raise CardError(MSG_SHAPE)
    text = clean_text(b.get("text"))
    conn, today = auth._db(), auth._today()
    conn.execute("BEGIN IMMEDIATE")  # 같은 사람에게 동시에 보내도 한 장만 — 검사와 넣기가 한 덩어리 (CardError 도 여기서 되돌리고 errorhandler 로)
    try:
        cid = _store(conn, key, g.actor.user_id, to, kind, reason, art, text, today)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    resp = jsonify(id=cid), 201
    push.notify([to], "card")  # 저장(commit)이 끝났고 응답도 정해졌다 — 알림이 안 가도(근무시간 밖 · 휴대폰 없음 · 푸시 고장) 응답은 그대로
    return resp


def _own(conn: Connection, cid: str) -> int | None:
    """주소의 카드 번호 — 내가 받은 카드일 때만. 남의 카드 · 보낸 사람 · 없는 번호 · 숫자가 아닌 번호는 None (HR 도 같다)."""
    n = _num(cid)
    return n if n is not None and conn.execute("SELECT 1 FROM cards WHERE id = ? AND recipient_id = ?", (n, g.actor.user_id)).fetchone() else None


def _mark(conn: Connection, card: int, how: str) -> tuple[Response, bool]:
    """받은 사람의 읽음 · 고마워요 · 숨김을 한 트랜잭션으로 적고 (남은 안 읽음 수 응답, 이번에 줄이 실제로 바뀌었나)를 돌려준다. 받은 사람은 바뀌지 않으므로(트리거) 위의 확인 뒤에 해도 안전하다."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = conn.execute(_MARK[how], (auth._iso(auth._now()), card)).rowcount > 0
        left = unread(conn, g.actor.user_id)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return jsonify(unread=left), changed


@bp.post("/api/cards/<cid>/read")
def read(cid: str) -> Response:
    """받은 사람이 카드를 열었다. 이미 읽었으면 그대로 200. {"unread": 남은 안 읽음}."""
    conn = auth._db()
    if (card := _own(conn, cid)) is None:
        raise CardError(MSG_GONE, 404)
    return _mark(conn, card, "read")[0]


@bp.post("/api/cards/<cid>/thanks")
def thanks(cid: str) -> Response:
    """[고마워요] — 읽음도 함께 채운다. 두 번째부터는 200 이되 아무것도 바뀌지 않고 알림도 없다."""
    conn = auth._db()
    if (card := _own(conn, cid)) is None:
        raise CardError(MSG_GONE, 404)
    resp, changed = _mark(conn, card, "thanks")
    if changed:  # 처음 누른 한 번만 — 저장(commit)이 끝났고 응답도 정해졌다. 보낸 사람은 바뀌지 않으므로(트리거) 보낼 때 읽는다
        push.notify(lambda: [conn.execute("SELECT sender_id FROM cards WHERE id = ?", (card,)).fetchone()[0]], "card_thanks")
    return resp


@bp.post("/api/cards/<cid>/hide")
def hide(cid: str) -> Response:
    """{"hidden": bool} 숨기기 · 숨김 풀기. 숨길 때 읽음도 채운다(풀 때는 읽음 그대로)."""
    conn = auth._db()
    if (card := _own(conn, cid)) is None:
        raise CardError(MSG_GONE, 404)
    b = request.get_json(silent=True)
    hidden = b.get("hidden") if isinstance(b, dict) else None
    if not isinstance(hidden, bool):
        raise CardError(MSG_SHAPE)
    return _mark(conn, card, "hide" if hidden else "show")[0]


def check() -> None:
    assert options(["birthday", "anniv"], False) == [{"kind": "cheer", "reason": "birthday"}, {"kind": "cheer", "reason": "anniv"},
                                                     {"kind": "praise", "reason": None}, {"kind": "thanks", "reason": None}]
    assert options(["birthday"], True) == [{"kind": "comfort", "reason": None}]  # 조사면 위로만
    assert options([], False) == [{"kind": "praise", "reason": None}, {"kind": "thanks", "reason": None}]
    assert clean_text("  고마워요 \r\n덕분이에요 ") == "고마워요\n덕분이에요"
    assert clean_text("a\rb") == "a\nb"  # 혼자 있는 CR 도 줄바꿈
    assert clean_text("\n\n a \n\n b \n") == "a\n\nb"  # 줄마다 앞뒤 빈칸을 떼고, 맨 앞뒤 빈 줄도 뗀다
    for bad, msg in ((" \n ", "글을 적어 주세요."), (None, "글을 적어 주세요."), (150, "글을 적어 주세요."),
                     ("가" * 151, "글은 150자까지 쓸 수 있어요."), ("a\nb\nc\nd", "글은 세 줄까지 쓸 수 있어요."),
                     ("a\r\nb\r\nc\r\nd", "글은 세 줄까지 쓸 수 있어요."),
                     ("a\x00b", "글에 쓸 수 없는 글자가 있어요."), ("a\x7fb", "글에 쓸 수 없는 글자가 있어요."),
                     ("a\x1f", "글에 쓸 수 없는 글자가 있어요."),  # 가장자리 제어문자도 (str.strip 이 떼기 전에 본다)
                     ("a\ud800", "글에 쓸 수 없는 글자가 있어요.")):
        try:
            clean_text(bad)
        except CardError as e:
            assert e.msg == msg and e.status == 400, (bad, e.msg, e.status)
        else:
            raise AssertionError(f"통과하면 안 됨: {bad!r}")
    assert clean_text("가" * 150) and clean_text("a\nb\nc") == "a\nb\nc" and clean_text("a\tb") == "a\tb"

    def verdict(v: str) -> str:  # 다듬은 글, 안 되면 그 문구
        try:
            return clean_text(v)
        except CardError as e:
            return e.msg
    for v, want in (("가\u2028나", "가\n나"), ("\ufeff 가 \ufeff", "가"), ("\x85가\x85", "가"), ("가\u200b나", "가\u200b나"),
                    ("가\u2028나\u2029다\x85라", "글은 세 줄까지 쓸 수 있어요."), ("\u200b\u200c\u200d\u2060\ufeff", "글을 적어 주세요."),
                    (" \u200b \n \u3164 ", "글을 적어 주세요.")):  # 화면 cardSelfCheck 의 같은 표 — 두 쪽 판정이 같아야 한다
        assert verdict(v) == want, (v, verdict(v), want)
    k = bytes(range(32))
    b = seal_text(k, 1, 2, "2027-03-02", "고마워요")
    assert unseal_text(k, 1, 2, "2027-03-02", b) == "고마워요" and "고마워요".encode() not in b
    assert len(b) > 28 and seal_text(k, 1, 2, "2027-03-02", "고마워요") != b  # 매번 다른 nonce
    for args in ((1, 2, "2027-03-03"), (2, 1, "2027-03-02"), (1, 3, "2027-03-02")):  # 다른 날 · 다른 사람 자리에 옮겨 붙이면 안 풀림
        try:
            unseal_text(k, *args, b)
        except InvalidTag:
            pass
        else:
            raise AssertionError(f"풀리면 안 됨: {args}")
    try:
        unseal_text(k, 1, 2, "2027-03-02", b[:-1] + bytes([b[-1] ^ 1]))  # 글이 바뀌었으면 안 풀림
    except InvalidTag:
        pass
    else:
        raise AssertionError("바뀐 글이 풀리면 안 됨")
    try:
        unseal_text(bytes(32), 1, 2, "2027-03-02", b)  # 열쇠가 다르면 안 풀림
    except InvalidTag:
        pass
    else:
        raise AssertionError("다른 열쇠로 풀리면 안 됨")


if __name__ == "__main__":
    for out in (sys.stdout, sys.stderr):
        out.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["check"]:
        check()
        print("cards 검사 통과")
    else:
        sys.exit("사용법: python -m cards check")
