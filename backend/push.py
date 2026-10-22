"""휴대폰 알림 바탕 (2026-10-22 인사담당자 — 휴대폰 알림 · 앱 아이콘 숫자, 설계 docs/superpowers/specs/2026-10-22-push-notifications-design.md).
표(017_push.sql) · 알림 문구 · 근무시간 · 키 · 보낼 대상 · 보내기 함수만 둔다. 주소(API) · 처리할 일 개수 · 아침 묶음 · 일이 생길 때 보내기는 뒤 작업에서 이 파일에 더한다.

  python -m push check    자체 검증 (CI) — 문구 · 근무시간 · 키 모양 · 진짜 pywebpush 로 가짜 푸시 서버에 보내 보기
  python -m push keygen   서버에서 한 번 — HR_PUSH_PUBLIC · HR_PUSH_PRIVATE 두 줄을 출력만 한다 (.env 에는 사람이 붙인다)

알림 문구에는 금액 · 다른 사람 이름 · 평가 등급이 없다 — 잠금 화면에 그대로 보이므로. 문구는 MESSAGES 한 곳에만 둔다.
받는 기기는 휴대폰 세션(sessions.kind='phone')뿐이다 — 로그아웃 · 휴대폰 해제 · 퇴사 경로마다 구독을 지우지 않아도 targets() 가 살아 있는 세션만 거른다.
보내기 함수 SEND 는 검사가 갈아 끼울 수 있게 모듈 이름으로 둔다 (push.SEND = 가짜). 로그에는 상태 코드만 — 이름 · 주소 · 문구는 남기지 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from sqlite3 import Connection

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush

import auth

logger = logging.getLogger(__name__)

SUBJECT = "https://hr.abyz-lab.work"  # VAPID subject — 사이트 주소 (개인 메일 주소를 넣지 않는다)
TTL_EVENT = 43200  # 일이 생겼을 때 보낸 알림: 휴대폰이 꺼져 있어도 12시간 안에 켜지면 전달
TTL_MORNING = 32400  # 9시 묶음: 18시까지 (9시간)
SEND_TIMEOUT = 10  # 푸시 서버 응답 기다리는 초 — 정하지 않으면 끝없이 기다린다

# 종류 → (제목 틀, 내용, 눌렀을 때 열 화면 go). 금액 · 이름 · 등급 자리를 만들지 않는다.
MESSAGES: dict[str, tuple[str, str, str]] = {
    "payslip": ("{month}월 급여명세서가 올라왔어요", "눌러서 확인해 주세요", "pay.p1"),
    "withholding": ("{year}년 원천징수영수증이 올라왔어요", "눌러서 확인해 주세요", "pay.p3"),
    "contract_sign": ("서명할 연봉계약서가 있어요", "눌러서 내용을 확인하고 서명해 주세요", "pay.p2"),
    "contract_ask": ("답변할 연봉계약서 문의가 있어요", "눌러서 확인해 주세요", "pay.p2"),
    "kpi_inbox": ("팀원 KPI 승인 요청이 왔어요", "눌러서 확인해 주세요", "perf.e1"),
    "kpi_back": ("KPI 계획서가 돌아왔어요", "사유를 확인하고 다시 작성해 주세요", "perf.e1"),
    "kpi_memo": ("KPI 확인 메모가 왔어요", "눌러서 확인해 주세요", "perf.e1"),
    "kpi_reply_to_author": ("확인 메모에 새 답글이 달렸어요", "눌러서 확인해 주세요", "perf.e4"),
    "kpi_reply_to_owner": ("확인 메모에 새 답글이 달렸어요", "눌러서 확인해 주세요", "perf.e1"),
}

# 처리할 일 종류 → 9시 묶음에 쓰는 이름 (화면 homePortalTasks 제목과 같은 말, 순서도 같게)
LABELS: dict[str, str] = {
    "p1": "새 급여명세서",
    "p2": "연봉계약서 서명",
    "p3": "새 원천징수영수증",
    "inbox": "처리할 KPI",
    "back": "반려된 KPI 다시 작성",
    "prev": "KPI 지난달 한 줄 쓰기",
    "memos": "확인 메모 확인",
    "check": "KPI 월간 확인",
    "replies": "확인 메모에 새 답글",
}


def in_work_hours(now: datetime) -> bool:
    """평일(월~금) 한국 시간 9:00 이상 18:00 미만. 공휴일은 빼지 않는다. now 는 시간대가 붙은 시각(아무 시간대)."""
    k = now.astimezone(auth.KST)
    return k.weekday() < 5 and 9 <= k.hour < 18


def message(kind: str, **fmt: object) -> dict:
    """알림 한 통 — {"title", "body", "url"}. url 은 눌렀을 때 열 화면(?go=)."""
    title, body, go = MESSAGES[kind]
    return {"title": title.format(**fmt), "body": body, "url": f"/?go={go}" if go else "/"}


def morning_text(items: list[tuple[str, int]]) -> dict:
    """9시 묶음 한 통. items = [(처리할 일 종류, 개수)] — 제목은 개수 합, 내용은 종류 이름(2건 이상이면 "이름 n건")."""
    items = [(k, n) for k, n in items if n > 0]
    names = [LABELS[k] + (f" {n}건" if n >= 2 else "") for k, n in items]
    return {"title": f"처리할 일이 {sum(n for _, n in items)}건 있어요", "body": " · ".join(names), "url": "/"}


def _b64u(s: str) -> bytes | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        return None
    try:
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except ValueError:
        return None


def keys() -> tuple[str, str] | None:
    """(공개키, 개인키) — .env 의 HR_PUSH_PUBLIC · HR_PUSH_PRIVATE (base64url). 요청마다 읽는다.
    공개키는 65바이트(비압축 점, 첫 바이트 0x04), 개인키는 32바이트가 아니면 None — 키가 없으면 보내기만 건너뛰고 다른 기능은 그대로."""
    pub, priv = (os.environ.get(k, "").strip() for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE"))
    pb, vb = _b64u(pub), _b64u(priv)
    if pb is None or vb is None or len(pb) != 65 or pb[0] != 4 or len(vb) != 32:
        return None
    return pub, priv


def _b64u_enc(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def keygen() -> tuple[str, str]:
    """새 VAPID 키 한 쌍 (공개, 개인) — P-256, base64url(패딩 없음). 파일(PEM)로 두지 않는다."""
    key = ec.generate_private_key(ec.SECP256R1())
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64u_enc(pub), _b64u_enc(key.private_numbers().private_value.to_bytes(32, "big"))


def targets(conn: Connection, uids: Iterable[int], now: datetime) -> list[tuple[int, str, dict]]:
    """uids 중 알림을 받을 기기 — [(user_id, token_hash, subscription_info)]. 휴대폰 세션이 살아 있는 구독만:
    해제 안 됨 · 마지막 사용 30일 안 · 만료 전 · 활성 직원 · 포털 사용 켜짐. PC 세션 구독은 받지 않는다."""
    ids = sorted({int(u) for u in uids})
    if not ids:
        return []
    now = now.astimezone(timezone.utc)
    rows = conn.execute(
        "SELECT s.user_id, p.token_hash, p.endpoint, p.p256dh, p.auth"
        " FROM push_subscriptions p JOIN sessions s ON s.token_hash = p.token_hash JOIN users u ON u.id = s.user_id"
        f" WHERE s.user_id IN ({','.join('?' * len(ids))}) AND p.user_id = s.user_id AND s.kind = 'phone'"
        " AND s.revoked_at IS NULL AND s.last_seen_at > ? AND s.expires_at > ? AND u.is_active = 1 AND u.portal_enabled = 1"
        " ORDER BY s.user_id, p.token_hash",
        (*ids, auth._iso(now - auth.PHONE_IDLE), auth._iso(now)),
    ).fetchall()
    return [(uid, th, {"endpoint": ep, "keys": {"p256dh": k, "auth": a}}) for uid, th, ep, k, a in rows]


def _webpush(info: dict, payload: dict, keys: tuple[str, str], ttl: int) -> int:
    """한 기기에 보내고 HTTP 상태 코드를 돌려준다 (성공 201). 푸시 서버 거절이면 그 코드, 연결 실패 · 깨진 구독이면 0 — 던지지 않는다."""
    try:
        return webpush(
            info, json.dumps(payload, ensure_ascii=False),
            vapid_private_key=keys[1], vapid_claims={"sub": SUBJECT},  # claims 는 pywebpush 가 고쳐 쓰므로 호출마다 새로
            ttl=ttl, timeout=SEND_TIMEOUT,
        ).status_code
    except WebPushException as exc:
        if exc.response is not None:
            return exc.response.status_code
        logger.warning("push send failed: %s", type(exc).__name__)
    except Exception as exc:  # 연결 실패 · 시간 초과 · 깨진 구독 값 — 한 기기의 실패가 나머지를 막지 않게
        logger.warning("push send failed: %s", type(exc).__name__)
    return 0


# (subscription_info, payload, (공개키, 개인키), ttl) → HTTP 상태 코드. 검사는 push.SEND 를 가짜로 바꿔 끼운다.
SEND: Callable[[dict, dict, tuple[str, str], int], int] = _webpush


def _check_send() -> None:
    """진짜 pywebpush 를 가짜 푸시 서버(내 컴퓨터)로 한 번씩 — 개인키 글자 모양이 통하는지 · TTL · VAPID 머리글 · 상태 코드 변환."""
    import socket
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    seen: list[dict] = []
    status = [201]

    class Fake(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append({k.lower(): v for k, v in self.headers.items()})
            self.send_response(status[0])
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    srv = HTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    pub, priv = keygen()
    device, _ = keygen()  # 받는 휴대폰의 키를 흉내 — 공개키 모양이면 된다
    sub = {"endpoint": f"http://127.0.0.1:{srv.server_port}/push/x", "keys": {"p256dh": device, "auth": _b64u_enc(os.urandom(16))}}
    assert SEND is _webpush
    logger.disabled = True  # 아래 0 이 나오는 경우는 일부러 만든 실패 — 경고 줄을 찍지 않는다
    try:
        for code in (201, 404, 410, 500):
            status[0] = code
            assert SEND(sub, message("kpi_back"), (pub, priv), TTL_EVENT) == code, code
        assert seen[0]["ttl"] == str(TTL_EVENT) and seen[0]["content-encoding"] == "aes128gcm"
        assert seen[0]["authorization"].startswith("vapid t=") and f"k={pub}" in seen[0]["authorization"]  # 개인키로 서명 · 공개키 짝
        srv.shutdown()
        with socket.socket() as s:  # 아무도 듣지 않는 포트
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
        assert SEND({"endpoint": f"http://127.0.0.1:{dead}/x", "keys": sub["keys"]}, message("kpi_back"), (pub, priv), TTL_EVENT) == 0  # 연결 실패
        assert SEND({"endpoint": "http://127.0.0.1:1/x", "keys": {"p256dh": "x", "auth": "x"}}, message("kpi_back"), (pub, priv), TTL_EVENT) == 0  # 깨진 구독
    finally:
        logger.disabled = False


def check() -> None:
    KST = timezone(timedelta(hours=9))
    assert in_work_hours(datetime(2026, 10, 23, 17, 59, tzinfo=KST))  # 금 17:59
    assert not in_work_hours(datetime(2026, 10, 23, 18, 0, tzinfo=KST))  # 금 18:00
    assert not in_work_hours(datetime(2026, 10, 24, 10, 0, tzinfo=KST))  # 토
    assert not in_work_hours(datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc))  # KST 월 08:30
    assert in_work_hours(datetime(2026, 10, 26, 0, 0, tzinfo=timezone.utc))  # KST 월 09:00
    assert not in_work_hours(datetime(2026, 10, 23, 8, 59, tzinfo=KST))  # 금 08:59
    assert not in_work_hours(datetime(2026, 10, 25, 12, 0, tzinfo=KST))  # 일
    assert in_work_hours(datetime(2026, 10, 23, 9, 0, tzinfo=KST).astimezone(timezone(timedelta(hours=-5))))  # 다른 시간대로 와도 같은 시각

    assert message("payslip", month=10) == {"title": "10월 급여명세서가 올라왔어요", "body": "눌러서 확인해 주세요", "url": "/?go=pay.p1"}
    assert message("withholding", year=2026)["title"] == "2026년 원천징수영수증이 올라왔어요"
    assert message("contract_sign")["url"] == "/?go=pay.p2" and message("kpi_reply_to_author")["url"] == "/?go=perf.e4"
    MESSAGES["_blank_go"] = ("제목", "내용", "")
    assert message("_blank_go")["url"] == "/"  # go 가 비면 홈
    del MESSAGES["_blank_go"]
    assert list(MESSAGES) == ["payslip", "withholding", "contract_sign", "contract_ask", "kpi_inbox", "kpi_back", "kpi_memo",
                              "kpi_reply_to_author", "kpi_reply_to_owner"]
    assert list(LABELS) == ["p1", "p2", "p3", "inbox", "back", "prev", "memos", "check", "replies"]
    for t, b, _ in MESSAGES.values():  # 금액 · 이름 자리 없음
        assert not re.search(r"\d[\d,]*\s*원|\{name\}|\{amount\}|등급", t + b)

    assert morning_text([("p1", 1), ("inbox", 2)]) == {"title": "처리할 일이 3건 있어요", "body": "새 급여명세서 · 처리할 KPI 2건", "url": "/"}
    assert morning_text([("check", 1)]) == {"title": "처리할 일이 1건 있어요", "body": "KPI 월간 확인", "url": "/"}
    assert morning_text([("p1", 0), ("inbox", 1)])["body"] == "처리할 KPI"  # 0건인 종류는 이름도 안 나옴

    for k in ("HR_PUSH_PUBLIC", "HR_PUSH_PRIVATE"):
        os.environ.pop(k, None)
    assert keys() is None  # 키 없음
    pub, priv = keygen()
    assert len(_b64u(pub) or b"") == 65 and (_b64u(pub) or b"\0")[0] == 4 and len(_b64u(priv) or b"") == 32 and "=" not in pub + priv
    os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=priv)
    assert keys() == (pub, priv)
    os.environ["HR_PUSH_PRIVATE"] = "short"
    assert keys() is None
    os.environ.update(HR_PUSH_PRIVATE=priv, HR_PUSH_PUBLIC=priv)  # 개인키(32바이트)를 공개키 자리에
    assert keys() is None
    os.environ.update(HR_PUSH_PUBLIC=pub, HR_PUSH_PRIVATE=pub)  # 공개키(65바이트)를 개인키 자리에
    assert keys() is None
    os.environ.update(HR_PUSH_PUBLIC=_b64u_enc(b"\x05" + _b64u(pub)[1:]), HR_PUSH_PRIVATE=priv)  # 첫 바이트가 0x04 가 아님
    assert keys() is None
    os.environ.update(HR_PUSH_PUBLIC=pub + "!", HR_PUSH_PRIVATE=priv)  # base64url 글자가 아님
    assert keys() is None

    _check_send()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.argv[1:] == ["check"]:
        check()
        print("push 검사 통과")
    elif sys.argv[1:] == ["keygen"]:
        public, private = keygen()
        print(f"HR_PUSH_PUBLIC={public}")
        print(f"HR_PUSH_PRIVATE={private}")
    else:
        sys.exit("사용법: python -m push check | keygen")
