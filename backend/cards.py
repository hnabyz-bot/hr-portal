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

화면 · API 는 이 파일에 이어서 더한다.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from sqlite3 import Connection

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

import auth
import payslip

KINDS = ("cheer", "praise", "thanks", "comfort")
REASONS = ("birthday", "welcome", "anniv", "joy")
TEXT_MAX = 150  # 카드 글 글자 수
LINES_MAX = 3  # 카드 글 줄 수 (줄바꿈은 2번까지)

_NEWS_REASON = {"birthday": "birthday", "welcome": "welcome", "anniversary": "anniv"}  # auth.news_items 의 kind → 축하 사유


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
    """카드 글: 글자만, 줄마다 앞뒤 빈칸을 뗀 1~TEXT_MAX자, LINES_MAX줄까지 — 아니면 CardError(400). \\r\\n · 혼자 있는 \\r 은 \\n 으로 바꿔 센다.
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
    t = "\n".join(ln.strip() for ln in v.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
    if not t:
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
