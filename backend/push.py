"""휴대폰 알림 바탕 (2026-10-22 인사담당자 — 휴대폰 알림 · 앱 아이콘 숫자, 설계 docs/superpowers/specs/2026-10-22-push-notifications-design.md).
표(017_push.sql) · 알림 문구 · 근무시간 · 키 · 보낼 대상 · 보내기 함수 · 구독 주소(API) · 처리할 일 개수 · 아침 9시 묶음 · 일이 생길 때 보내기(notify)를 둔다.

  python -m push check    자체 검증 (CI) — 문구 · 근무시간 · 키 모양 · 진짜 pywebpush 로 가짜 푸시 서버에 보내 보기
  python -m push keygen   서버에서 한 번 — HR_PUSH_PUBLIC · HR_PUSH_PRIVATE 두 줄을 출력만 한다 (.env 에는 사람이 붙인다)
  python -m push morning  평일 9시(KST) 묶음 · 업무 마감 · KPI 마감 — 서버 예약 작업이 부른다. 보낸 사람 수만 출력. 같은 날 두 번 돌지 않고, 근무시간(평일 9~18시) 밖에는 아무것도 안 한다

  GET  /api/push/key          공개키 {"key"} — 키가 없으면 404 (화면이 알림 켜기 안내를 하지 않는다)
  POST /api/push/subscribe    휴대폰 세션만. {"endpoint": https 푸시 서버 주소, "keys": {"p256dh", "auth"}} → 이 세션의 구독 1줄(덮어씀)
  POST /api/push/unsubscribe  이 세션의 구독 줄 삭제
  세션 검사 · 같은 출처 검사는 auth._guard 가 모든 /api/* 에 한다. app.py 가 bp 를 붙인다.

일이 생길 때 보내기: 급여명세서 · 원천징수영수증 공개, KPI 제출 · 수정 요청 · 일정 변경 요청 · 돌려보냄, 확인 메모 · 답글, 연봉계약서 발송 · 다시 보내기 · 문의가 그 일을 저장(commit)한 뒤
notify() 를 부른다 — 보내는 쪽(pending)이 DB 를 새로 잡으므로 commit 전에 부르면 잠김 오류가 난다. 키가 없거나 근무시간(평일 9~18시 KST) 밖이면 아무것도 안 한다 (다음 근무일 9시 묶음에 들어감).
보내기는 요청을 붙잡지 않게 데몬 스레드에서, 실패해도 원래 일은 그대로 (예외를 밖으로 내지 않고 로그에는 상태 코드 · 예외 종류만).
알림 문구에는 금액 · 다른 사람 이름 · 평가 등급이 없다 — 잠금 화면에 그대로 보이므로. 문구는 MESSAGES 한 곳에만 둔다.
받는 기기는 휴대폰 세션(sessions.kind='phone')뿐이다 — 로그아웃 · 휴대폰 해제 · 새 휴대폰 등록 때 forget() 으로 구독을 지우고(auth.py), 놓쳐도 targets() 가 살아 있는 세션만 거른다.
보내기 함수 SEND 는 검사가 갈아 끼울 수 있게 모듈 이름으로 둔다 (push.SEND = 가짜). 로그에는 상태 코드만 — 이름 · 주소 · 문구는 남기지 않는다.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import sys
import threading
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta, timezone
from sqlite3 import Connection
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Blueprint, Flask, Response, current_app, g, jsonify, request

import auth
from hr_eval.domain.models import Role
from hr_eval.domain.org import manager_of

logger = logging.getLogger(__name__)
bp = Blueprint("push", __name__)

SUBJECT = "https://hr.abyz-lab.work"  # VAPID subject — 사이트 주소 (개인 메일 주소를 넣지 않는다)
TTL_EVENT = 43200  # 일이 생겼을 때 보낸 알림: 휴대폰이 꺼져 있어도 12시간 안에 켜지면 전달
TTL_MORNING = 32400  # 9시 묶음: 18시까지 (9시간)
SEND_TIMEOUT = 10  # 푸시 서버 응답 기다리는 초 — 정하지 않으면 끝없이 기다린다
SYNC = False  # 검사가 True 로 두면 notify 가 스레드 없이 바로 보낸다

# 종류 → (제목 틀, 내용, 눌렀을 때 열 화면 go). 금액 · 이름 · 등급 자리를 만들지 않는다. KPI 알림의 내용은 "PC에서 …" — 평가·성과는 휴대폰에서 열리지 않아(PC_ONLY_NAV) 눌러도 "PC에서 이용해 주세요" 안내가 뜬다.
MESSAGES: dict[str, tuple[str, str, str]] = {
    "payslip": ("{month}월 급여명세서가 올라왔어요", "눌러서 확인해 주세요", "pay.p1"),
    "withholding": ("{year}년 원천징수영수증이 올라왔어요", "눌러서 확인해 주세요", "pay.p3"),
    "contract_sign": ("서명할 연봉계약서가 있어요", "눌러서 내용을 확인하고 서명해 주세요", "pay.p2"),
    "contract_ask": ("답변할 연봉계약서 문의가 있어요", "눌러서 확인해 주세요", "pay.p2"),
    "kpi_inbox": ("팀원 KPI 승인 요청이 왔어요", "PC에서 확인해 주세요", "perf.e1"),
    "kpi_back": ("KPI 계획서가 돌아왔어요", "PC에서 사유를 확인하고 다시 작성해 주세요", "perf.e1"),
    "kpi_memo": ("KPI 확인 메모가 왔어요", "PC에서 확인해 주세요", "perf.e1"),
    "kpi_reply_to_author": ("확인 메모에 새 답글이 달렸어요", "PC에서 확인해 주세요", "perf.e4"),
    "kpi_reply_to_owner": ("확인 메모에 새 답글이 달렸어요", "PC에서 확인해 주세요", "perf.e1"),
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


# ---- 9시 마감 알림 (2026-10-26 인사담당자 — "오늘 마감인 업무가 있습니다 · 오늘 KPI 업무 마감일입니다") ----
# 처리할 일 묶음과 따로 한 통씩: 업무 마감(나의 업무) · KPI 마감(비정기 일정 · 지난달 한 줄). 잠금 화면에 보이므로 개수만 — 업무 제목은 넣지 않는다.
WS_GO = "/?go=ws.w1"  # 누르면 나의 업무 (휴대폰에서도 열리고 처음 목록이 '오늘' — KPI- 업무도 여기 있다. 평가·성과는 휴대폰에서 안 열림)
_YMD = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def deadline_days(today: date) -> dict:
    """마감 알림이 보는 날 (today = 9시 묶음이 도는 평일의 한국 날짜). 주말에는 9시 묶음이 없어서 금요일이 주말 몫까지 미리 알린다:
    soon = 전날 미리 알릴 KPI 마감일 — 다음 평일까지 (월~목은 내일, 금요일은 토 · 일 · 월), weekend = 금요일에만 토 · 일 (나의 업무의 주말 마감),
    prev = 지난달 한 줄 마감(7일)을 알릴 날인가 — 7일, 7일이 토 · 일이면 그 앞 금요일(6일 · 5일)."""
    fri = today.weekday() == 4
    soon = [(today + timedelta(days=i)).isoformat() for i in range(1, 4 if fri else 2)]
    return {"soon": soon, "weekend": soon[:2] if fri else [], "prev": today.day == 7 or (fri and today.day in (5, 6))}


def task_counts(tasks: object, today: str, weekend: list[str]) -> tuple[int, int, int]:
    """나의 업무 목록(workspace_data tasks — 화면 wsTasks 모양)의 (오늘 마감, 주말 마감, 마감 지난) 개수. 끝낸 일(state '완료') · 마감 없는 일은 안 센다 (진행 중은 셈).
    모양이 틀린 줄은 건너뛴다 — 제목이 글자가 아니면 화면(wsNorm)에도 없고, 마감은 "YYYY-MM-DD" 만 읽는다."""
    t = w = late = 0
    for x in tasks if isinstance(tasks, list) else []:
        due = x.get("due") if isinstance(x, dict) and isinstance(x.get("title"), str) and x.get("state") != "완료" else None
        if isinstance(due, str) and _YMD.fullmatch(due):
            t, w, late = t + (due == today), w + (due in weekend), late + (due < today)
    return t, w, late


def tasks_text(n_today: int, n_weekend: int, n_late: int) -> dict | None:
    """업무 마감 알림 한 통 (셀 것이 없으면 None) — 제목은 가장 급한 것 하나(오늘 → 주말 → 지난 것), 내용은 나머지 개수와 안내."""
    rows = [r for r in ((n_today, "오늘 마감인 업무가", "오늘 마감 {}건"), (n_weekend, "주말 마감인 업무가", "주말 마감 {}건"),
                        (n_late, "마감 지난 업무가", "마감 지난 업무 {}건")) if r[0] > 0]
    if not rows:
        return None
    body = [part.format(n) for n, _, part in rows[1:]] + ["나의 업무에서 확인해 주세요"]
    return {"title": f"{rows[0][1]} {rows[0][0]}건 있어요", "body": " · ".join(body), "url": WS_GO}


def kpi_counts(prog: dict | None, today: str, soon: list[str]) -> tuple[int, list[str], int]:
    """진행 기록(kpi.progress_json — 지금 확정본)의 비정기 일정 중 결과를 안 적은 것: (오늘 마감 수, 전날 미리 알릴 일정의 기한들, 기한이 지난 수).
    기한은 일정 변경이 확인된 실제 기한(due) — 화면의 KPI- 업무 마감일과 같다. 확정본이 없으면(None) 0 · [] · 0."""
    dues = [s["due"] for it in (prog or {}).get("items", []) for s in it.get("scheds") or [] if s.get("result") is None]
    return sum(d == today for d in dues), [d for d in dues if d in soon], sum(d < today for d in dues)


def kpi_text(n_today: int, soon: list[str], n_late: int, n_prev: int, today: date) -> dict | None:
    """KPI 마감 알림 한 통 (셀 것이 없으면 None) — 제목은 가장 급한 것 하나(오늘 마감 → 지난달 한 줄 마감 → 곧 마감 → 지난 일정), 내용은 그 개수 · 나머지 개수 · 안내.
    결과 · 한 줄은 PC 에서만 쓴다. n_prev(지난달 한 줄 남은 수)는 알릴 날(deadline_days 의 prev)에만 넘긴다. 금요일의 곧 마감은 월요일 · 주말 · 주말·월요일."""
    mon = (today + timedelta(days=3)).isoformat()
    when = "내일" if today.weekday() != 4 else "월요일" if set(soon) == {mon} else "주말" if mon not in soon else "주말·월요일"
    until = "오늘까지" if today.day == 7 else "7일까지"
    rows = [r for r in ((n_today, "오늘 KPI 업무 마감일이에요", "오늘 마감 {}건"), (n_prev, f"{until} KPI 지난달 한 줄을 써 주세요", f"지난달 한 줄 {{}}건({until})"),
                        (len(soon), f"{when} KPI 업무 마감일이에요", f"{when} 마감 {{}}건"), (n_late, "마감 지난 KPI 일정이 있어요", "마감 지난 일정 {}건")) if r[0] > 0]
    if not rows:
        return None
    end = "PC에서 결과를 적어 주세요" if n_today or soon or n_late else "PC에서 써 주세요"
    body = [f"{rows[0][0]}건", *(part.format(n) for n, _, part in rows[1:]), end]
    return {"title": rows[0][1], "body": " · ".join(body), "url": WS_GO}


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


# 받을 수 있는 구독 = 휴대폰 세션이 살아 있는 구독 (구독 p · 세션 s · 직원 u). 값 둘: 마지막 사용 하한 · 지금 — targets() 와 morning() 의 정리가 같은 조건을 써서 어긋나지 않게 한 곳에 둔다.
_FROM = "push_subscriptions p JOIN sessions s ON s.token_hash = p.token_hash JOIN users u ON u.id = s.user_id"
_LIVE = ("p.user_id = s.user_id AND s.kind = 'phone' AND s.revoked_at IS NULL AND s.last_seen_at > ? AND s.expires_at > ?"
         " AND u.is_active = 1 AND u.portal_enabled = 1")


def _live_args(now: datetime) -> tuple[str, str]:
    now = now.astimezone(timezone.utc)
    return auth._iso(now - auth.PHONE_IDLE), auth._iso(now)


def targets(conn: Connection, uids: Iterable[int], now: datetime) -> list[tuple[int, str, dict]]:
    """uids 중 알림을 받을 기기 — [(user_id, token_hash, subscription_info)]. 휴대폰 세션이 살아 있는 구독만:
    해제 안 됨 · 마지막 사용 30일 안 · 만료 전 · 활성 직원 · 포털 사용 켜짐. PC 세션 구독은 받지 않는다."""
    ids = sorted({int(u) for u in uids})
    if not ids:
        return []
    rows = conn.execute(
        f"SELECT s.user_id, p.token_hash, p.endpoint, p.p256dh, p.auth FROM {_FROM}"
        f" WHERE s.user_id IN ({','.join('?' * len(ids))}) AND {_LIVE} ORDER BY s.user_id, p.token_hash",
        (*ids, *_live_args(now)),
    ).fetchall()
    return [(uid, th, {"endpoint": ep, "keys": {"p256dh": k, "auth": a}}) for uid, th, ep, k, a in rows]


def forget(conn: Connection, token_hashes: Iterable[str]) -> None:
    """세션이 끝난 구독 줄을 지운다 — 로그아웃 · 휴대폰 해제 · 새 휴대폰 등록 때 auth.py 가 부른다. 구독이 없는 세션 번호는 그냥 지나간다."""
    conn.executemany("DELETE FROM push_subscriptions WHERE token_hash = ?", [(h,) for h in token_hashes])


# 점으로 이어진 DNS 이름이고 끝 칸은 글자로 시작한다 — IP(v4 · 127.1 · 정수 표기 · v6)와 점 없는 이름(localhost · 사내 서버 이름)은 안 맞는다.
_HOST = re.compile(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]*")
_INNER = (".local", ".internal", ".lan", ".home", ".localhost")  # 사내망 · 내 컴퓨터를 가리키는 이름 끝


def _endpoint_ok(url: object) -> bool:
    """푸시 서버 주소 모양 — https, 1000자 안, 공개 DNS 이름. 서버가 나중에 이 주소로 보내므로 안쪽 망을 가리키는 모양을 거른다.
    ponytail: 이름이 안쪽 IP 로 풀리는 경우(DNS 재바인딩)는 모양만으로 못 막는다 — 보낼 때 풀린 IP 를 확인하려면 보내기 쪽에서."""
    if not isinstance(url, str) or len(url) > 1000 or not re.fullmatch(r"[\x21-\x7e]+", url):  # 공백 · 줄바꿈 · 한글 없음
        return False
    try:
        u = urlsplit(url)
        _ = u.port  # 숫자가 아니거나 65535 를 넘는 포트는 여기서 ValueError
    except ValueError:
        return False
    host = u.hostname or ""
    return u.scheme == "https" and "@" not in u.netloc and _HOST.fullmatch(host) is not None and not host.endswith(_INNER)


def _key_ok(v: object, limit: int) -> bool:
    """휴대폰이 보낸 암호화 키 글자 — base64url (뒤 = 는 있어도 됨), limit 자 이하."""
    return isinstance(v, str) and len(v) <= limit and _b64u(v.rstrip("=")) is not None


@bp.get("/api/push/key")
def push_key() -> Response:
    """공개키 — 휴대폰이 알림을 켤 때 푸시 서버에 알려 주는 값. 개인키는 내보내지 않는다."""
    k = keys()
    return (jsonify(key=k[0]), 200) if k else (jsonify(error="알림 키가 아직 없습니다."), 404)


@bp.post("/api/push/subscribe")
def subscribe() -> Response:
    """휴대폰 세션 하나에 구독 하나(다시 켜면 덮어씀). 같은 푸시 주소를 쓰던 다른 세션의 줄은 지운다 —
    쿠키만 지우고 같은 휴대폰에 다른 사람이 등록한 경우에도 앞 사람 알림이 그 휴대폰으로 가지 않게."""
    if g.session_kind != "phone":
        return jsonify(error="알림은 휴대폰에서만 받을 수 있습니다."), 403
    b = request.get_json(silent=True)
    b = b if isinstance(b, dict) else {}
    ks = b.get("keys")
    ks = ks if isinstance(ks, dict) else {}
    ep = b.get("endpoint")
    if not (_endpoint_ok(ep) and _key_ok(ks.get("p256dh"), 200) and _key_ok(ks.get("auth"), 100)):
        return jsonify(error="알림 정보의 모양이 올바르지 않습니다."), 400
    conn, h = auth._db(), g.session_hash
    conn.execute(
        "INSERT OR REPLACE INTO push_subscriptions (token_hash, user_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (h, g.actor.user_id, ep, ks["p256dh"], ks["auth"], auth._iso(auth._now())),
    )
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ? AND token_hash <> ?", (ep, h))
    return jsonify(ok=True)


@bp.post("/api/push/unsubscribe")
def unsubscribe() -> Response:
    forget(auth._db(), [g.session_hash])
    return jsonify(ok=True)


def _webpush(info: dict, payload: dict, keys: tuple[str, str], ttl: int) -> int:
    """한 기기에 보내고 HTTP 상태 코드를 돌려준다 (성공 201). 푸시 서버 거절이면 그 코드, 연결 실패 · 깨진 구독이면 0 — 던지지 않는다."""
    try:
        import requests
        from pywebpush import WebPushException, webpush  # 쓰는 자리에서 — 설치가 깨져도 앱이 뜨는 데는 영향 없게
    except Exception as exc:  # 서버 설치 문제 — 네트워크 실패("push send failed")와 로그에서 구별
        logger.warning("push library unavailable: %s", type(exc).__name__)
        return 0
    try:
        with requests.Session() as session:
            session.max_redirects = 0  # 푸시 서버가 다른 주소(안쪽 망 …)로 넘겨도 따라가지 않는다 — TooManyRedirects 로 실패(0), 구독은 그대로
            return webpush(
                info, json.dumps(payload, ensure_ascii=False),
                vapid_private_key=keys[1], vapid_claims={"sub": SUBJECT},  # claims 는 pywebpush 가 고쳐 쓰므로 호출마다 새로
                ttl=ttl, timeout=SEND_TIMEOUT, requests_session=session,
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


def _deliver_one(conn: Connection, found: list[tuple[str, dict]], payload: dict, ks: tuple[str, str], ttl: int, who: str) -> bool:
    """한 사람의 기기들(found = [(세션 번호, 구독 정보)])에 같은 알림을 보낸다 — 푸시 서버가 받아 준 기기가 하나라도 있으면 True.
    404 · 410 은 구독이 없어졌다는 뜻이라 그 줄을 지우고, 그 밖의 실패는 건너뛴다 (한 기기의 실패가 나머지를 막지 않게). 로그에는 상태 코드 · 예외 종류만 — who 는 morning · event."""
    reached = False
    for th, info in found:
        try:
            status = SEND(info, payload, ks, ttl)
        except Exception as exc:  # 진짜 SEND 는 던지지 않지만, 바꿔 낀 것이 던져도 나머지로 넘어간다
            logger.warning("%s push failed: %s", who, type(exc).__name__)
            continue
        if status in (404, 410):
            try:
                forget(conn, [th])
            except Exception as exc:  # 지우다 DB 가 실패해도 나머지 기기 · 사람에게는 간다 (다음 보내기에서 또 404 · 410 이면 그때 지운다) — 로그에는 예외 종류만
                logger.warning("%s push forget failed: %s", who, type(exc).__name__)
        elif not 200 <= status < 300:
            logger.warning("%s push rejected: status %s", who, status)
        reached = reached or 200 <= status < 300
    return reached


def pending(flask_app: Flask, uid: int) -> list[tuple[str, int]]:
    """그 사람이 처리할 일 [(종류, 개수)] — 0 인 종류는 빼고 LABELS 순서. 합은 그 사람 화면의 왼쪽 메뉴 숫자 합(homePortalTasks · TODO)과 같다:
    화면이 부르는 서버 함수를 그 사람으로 세워 그대로 쓴다 (kpi.inbox · plan_json · progress_json). 평가 · 이의 신청은 서버 기능이 없어 0,
    연봉계약서 문의는 알림만 보내고 세지 않는다 (설계 7장). 열쇠(HR_PAY_KEY)가 없으면 급여명세서 · 영수증 · 계약서는 0 — 화면의 목록도 비어 있다.
    새 앱 컨텍스트를 먼저 연다: 요청 안에서 불러도(inline 보내기) 그 요청의 g.actor · g.db 를 덮어쓰지 않고, 여기서 연 DB 연결은 컨텍스트가 닫힐 때 닫힌다."""
    import kpi  # 쓰는 자리에서 — kpi 가 이 파일을 불러와도 순환하지 않게
    import paypin

    n: dict[str, int] = {}
    with flask_app.app_context(), flask_app.test_request_context():
        conn = auth._db()
        g.actor = actor = auth.actor_of(conn, uid)
        year = kpi.plan_year()
        n.update(paypin.counts(conn, uid))  # 메뉴 숫자와 같은 셈 한 곳 (열쇠가 없으면 셋 다 0)
        box = kpi.inbox().get_json()
        items = box["items"]
        if actor.position is Role.CEO and conn.execute("SELECT 1 FROM ceo_delegation WHERE ceo_user_id = ?", (uid,)).fetchone():
            items = [i for i in items if i["proxy"]]  # 맡긴 동안 대표이사 몫은 HR 의 몫 — 대표이사가 HR 권한도 가졌을 때만 HR 몫이 남는다 (설계 7장)
        n["inbox"] = len(items)
        plan = kpi.plan_json(conn, uid, year)
        n["back"] = int(plan is not None and plan["noteKind"] == "RETURNED")
        with kpi._txn(conn):  # 진행 기록은 표 여럿을 읽는다 — 화면이 부르는 곳(progress_get)과 같이 한 트랜잭션 안에서
            prog = kpi.progress_json(conn, uid, year)
        n["prev"] = prog["todo"]["prev"] if prog else 0
        n["memos"] = box["review"]["memos"]
        if actor.is_hr or actor.position in (Role.TEAM_LEADER, Role.DIVISION_HEAD, Role.CEO):  # 월간 확인 · 새 답글은 이 사람들만 (화면 kvCanUse)
            n["check"], n["replies"] = box["review"]["check"], box["review"]["replies"]
    return [(k, n[k]) for k in LABELS if n.get(k)]


def deadlines(flask_app: Flask, uid: int, now: datetime) -> list[tuple[str, dict]]:
    """그 사람의 9시 마감 알림 [(태그, 알림)] — 업무 마감(morning-tasks) · KPI 마감(morning-kpi) 중 셀 것이 있는 것만, 보내는 순서로 (0~2통). 날짜는 now 의 한국 날짜.
    나의 업무는 서버에 저장된 목록(workspace_data tasks)만 센다 — 한 번도 저장하지 않았으면(화면은 예시를 보여 줌) 없다. 본인만 보는 자료라 개수만 쓰고 제목은 어디에도 남기지 않는다.
    KPI 는 지금 확정본의 진행 기록(kpi.progress_json — 화면의 KPI- 업무 · 지난달 한 줄과 같은 자료, 그해 것만 — 1월 1~7일에는 지난해 12월 한 줄 · 일정이 안 잡힌다,
    화면 · 처리할 일 묶음과 같은 한계). pending() 처럼 새 앱 컨텍스트에서 자기 DB 연결로 일한다."""
    import kpi  # 쓰는 자리에서 — kpi 가 이 파일을 불러와도 순환하지 않게

    k = now.astimezone(auth.KST).date()
    today, days, out = k.isoformat(), deadline_days(k), []
    with flask_app.app_context():
        conn = auth._db()
        row = conn.execute("SELECT data FROM workspace_data WHERE user_id = ? AND kind = 'tasks'", (uid,)).fetchone()
        if msg := tasks_text(*task_counts(json.loads(row[0]) if row else [], today, days["weekend"])):
            out.append(("morning-tasks", msg))
        try:  # KPI 를 못 세도(깨진 확정본 …) 업무 마감은 간다 — 로그에는 예외 종류만
            with kpi._txn(conn):  # 진행 기록은 표 여럿을 읽는다 — pending() 과 같이 한 트랜잭션 안에서
                prog = kpi.progress_json(conn, uid, kpi.plan_year())  # ponytail: pending() 도 같은 것을 한 번 더 읽는다 — 사람마다 두 번, 수십 명이면 문제없다
        except Exception as exc:
            logger.warning("morning kpi deadlines failed: %s", type(exc).__name__)
            return out
    n_today, soon, n_late = kpi_counts(prog, today, days["soon"])
    if msg := kpi_text(n_today, soon, n_late, prog["todo"]["prev"] if prog and days["prev"] else 0, k):
        out.append(("morning-kpi", msg))
    return out


def hr_admins(conn: Connection) -> list[int]:
    """활성 HR 전원 (user_roles.role = 'HR_ADMIN' 이면서 users.is_active = 1) — HR 에게 가는 알림의 받는 사람."""
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT r.user_id FROM user_roles r JOIN users u ON u.id = r.user_id WHERE r.role = 'HR_ADMIN' AND u.is_active = 1 ORDER BY r.user_id")]


def approvers(conn: Connection, owner: int) -> list[int]:
    """owner 의 KPI 를 처리할 사람 — kpi.inbox() 의 proxy() 와 같은 규칙: 바로 위 관리자가 있으면 그 사람, 없거나 그 관리자가 HR 에 맡긴 대표이사(ceo_delegation 에 줄이 있음)면
    활성 HR 전원(owner 본인 제외). 맡긴 대표이사는 받는 사람에서 빠진다 (그 대표이사가 HR 이면 HR 로서만 받는다)."""
    import kpi  # 쓰는 자리에서 — kpi 가 이 파일을 불러오므로 순환하지 않게
    people, depts = kpi.load_org(conn)
    boss = manager_of(owner, people, depts)
    if boss is not None and not kpi._ceos(conn, people).get(boss, False):
        return [boss]
    return [u for u in hr_admins(conn) if u != owner]


def deliver(flask_app: Flask, uids: list[int], msg: dict, ttl: int) -> int:
    """uids 의 휴대폰에 msg 를 보낸다 — 사람마다 처리할 일 합계를 badge(앱 아이콘 숫자)로 붙이고(못 세면 숫자 없이), 푸시 서버가 받아 준 사람 수를 돌려준다.
    404 · 410 은 그 구독을 지우고 그 밖의 실패는 건너뛰어 다음 기기 · 다음 사람으로 간다. 던지지 않는다 (스레드 안에서 부른다). 로그에는 상태 코드 · 예외 종류만.
    새 앱 컨텍스트에서 자기 DB 연결로 일한다 — 요청 안에서(SYNC) 불러도 그 요청의 g 를 건드리지 않는다."""
    sent = 0
    try:
        if (ks := keys()) is None:
            return 0
        with flask_app.app_context():
            conn = auth._db()
            devices: dict[int, list[tuple[str, dict]]] = {}
            for uid, th, info in targets(conn, uids, auth._now()):
                devices.setdefault(uid, []).append((th, info))
            for uid, found in devices.items():  # ponytail: pending() 은 사람마다 한 번 — 호출마다 BEGIN IMMEDIATE(kpi.inbox · progress_json)라 100명 안팎이면 문제없다. 큰 명세서 공개가 쓰기를 눈에 띄게 줄 세우면 개수를 한꺼번에 세는 쪽으로
                try:
                    payload = {**msg, "badge": sum(n for _, n in pending(flask_app, uid))}
                except Exception as exc:  # 숫자를 못 세도 알림 자체는 간다
                    logger.warning("event pending failed: %s", type(exc).__name__)
                    payload = msg
                sent += _deliver_one(conn, found, payload, ks, ttl, "event")
    except Exception as exc:
        logger.warning("event deliver failed: %s", type(exc).__name__)
    return sent


def notify(uids: Iterable[int] | Callable[[], Iterable[int]], kind: str, **fmt: object) -> None:
    """일이 생겼을 때 uids 의 휴대폰에 알림 한 통 (kind = MESSAGES 의 종류, fmt = 문구 빈칸). 요청 안에서 그 일을 저장(commit)한 뒤에 부른다 —
    보내는 쪽(pending)이 DB 를 새로 잡으므로 쓰기가 안 끝났으면 5초 기다리다 잠김 오류가 난다. 키가 없거나 근무시간(평일 9~18시 KST) 밖이면 아무것도 안 한다 (다음 근무일 9시 묶음에 들어감).
    uids 는 번호 목록이거나 번호를 만들어 주는 함수 — 함수는 보낼 때만 부르고, 거기서 DB 를 읽다 터져도 원래 일에는 영향이 없다. 같은 번호 · 0 · None 은 뺀다.
    요청을 붙잡지 않게 데몬 스레드에서 보낸다 (SYNC 면 바로). 무슨 일이 있어도 예외를 내지 않는다 — 알림이 원래 일을 되돌리거나 실패시키면 안 된다. 로그에는 예외 종류만."""
    try:
        if keys() is None or not in_work_hours(auth._now()):
            return
        ids = sorted({int(u) for u in (uids() if callable(uids) else uids) if u})
        if not ids:
            return
        args = (current_app._get_current_object(), ids, message(kind, **fmt), TTL_EVENT)
        if SYNC:
            deliver(*args)
        else:
            threading.Thread(target=deliver, args=args, daemon=True, name="push-deliver").start()
    except Exception as exc:
        logger.warning("push notify failed: %s", type(exc).__name__)


def morning(flask_app: Flask, now: datetime) -> int:
    """평일(한국 시간) 아침 묶음 — 휴대폰 구독이 있는 활성 직원마다 따로 한 통씩, 셀 것이 있는 것만: 처리할 일 묶음(1건 이상, 제목 = 개수 합, 내용 = 종류 이름) ·
    업무 마감 · KPI 마감(deadlines). 태그는 morning · morning-tasks · morning-kpi (서로 바꿔 치지 않게), 앱 아이콘 숫자는 세 통 모두 처리할 일 합.
    보낸 사람 수(푸시 서버가 한 통이라도 받아 준 사람)를 돌려준다. 근무시간(평일 9~18시) 밖 · 알림 키 없음이면 0 (기록도 쓰지 않는다 — 새벽 · 밤에 손으로 돌리거나 늦게 돌려도 울리지 않는다).
    오늘을 **보내기 전에 먼저 잡는다**(push_morning 에 INSERT OR IGNORE — 이미 줄이 있으면 0): 겹쳐 도는 두 번째 실행이 같은 사람에게 또 보내 휴대폰이 두 번 울리지 않게. 값은 끝에 보낸 사람 수로 고친다.
    그 대가로 보내는 도중에 멈추면(서버 재시작 등) 그날 묶음은 일부만 가고 다시 돌지 않는다 — 두 번 울리는 것보다 낫다고 본 선택.
    푸시 서버가 구독이 없어졌다(404 · 410)고 하면 그 줄을 지운다. 세션이 끝났거나 직원이 비활성인 구독 줄은 같은 일에서 함께 정리한다.
    한 사람의 개수 세기나 보내기가 실패해도 다음 사람으로 넘어간다 (처리할 일을 못 세면 마감 알림만 숫자 없이, 마감을 못 세면 묶음만). 앱 안에 예약 작업을 두지 않는다 — 서버 예약 작업이 python -m push morning 으로 부른다."""
    if not in_work_hours(now) or (ks := keys()) is None:
        return 0
    today, sent = now.astimezone(auth.KST).date().isoformat(), 0
    with flask_app.app_context():
        conn = auth._db()
        at = auth._iso(now.astimezone(timezone.utc))
        if not conn.execute("INSERT OR IGNORE INTO push_morning (day, people, ran_at) VALUES (?, 0, ?)", (today, at)).rowcount:  # 연결이 자동 저장이라 바로 보인다
            return 0  # 오늘은 이미 돌았거나 지금 다른 실행이 돌고 있다
        conn.execute(f"DELETE FROM push_subscriptions WHERE token_hash NOT IN (SELECT p.token_hash FROM {_FROM} WHERE {_LIVE})", _live_args(now))  # targets() 가 절대 고르지 않을 줄
        devices: dict[int, list[tuple[str, dict]]] = {}
        for uid, th, info in targets(conn, [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM push_subscriptions")], now):
            devices.setdefault(uid, []).append((th, info))
        for uid, found in devices.items():  # ponytail: 사람마다 차례로 센다 — 수십 명이면 몇 초, 느려지면 사람 여럿을 한 번에 세는 쪽으로
            try:
                items: list[tuple[str, int]] | None = pending(flask_app, uid)
            except Exception as exc:  # 한 사람의 고장이 나머지 아침 알림을 막지 않게 — 로그에는 예외 종류만. 마감 알림은 그래도 간다 (아이콘 숫자 없이)
                logger.warning("morning pending failed: %s", type(exc).__name__)
                items = None
            try:
                extra = deadlines(flask_app, uid, now)
            except Exception as exc:  # 마감을 못 세도 처리할 일 묶음은 간다
                logger.warning("morning deadlines failed: %s", type(exc).__name__)
                extra = []
            total = None if items is None else sum(n for _, n in items)
            msgs = ([("morning", morning_text(items))] if total else []) + extra  # 처리할 일 → 업무 마감 → KPI 마감 (휴대폰은 늦게 온 것을 위에 — KPI 가 맨 위)
            badge = {} if total is None else {"badge": total}  # 세 통 모두 같은 아이콘 숫자 (개인 업무는 세지 않는다)
            sent += any([_deliver_one(conn, found, {**m, **badge, "tag": tag}, ks, TTL_MORNING, "morning") for tag, m in msgs])  # 목록으로 — 한 통이 닿아도 나머지를 보낸다
        conn.execute("UPDATE push_morning SET people = ?, ran_at = ? WHERE day = ?", (sent, at, today))
    return sent


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
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/push/redirected")  # 3xx 일 때만 뜻이 있다
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
        for code in (301, 302, 307, 308):  # 푸시 서버가 다른 주소로 넘기려 해도 따라가지 않는다 — 실패(0), 요청은 한 번뿐 (구독이 안쪽 망으로 새로 넘기는 길을 막음)
            status[0], before = code, len(seen)
            assert SEND(sub, message("kpi_back"), (pub, priv), TTL_EVENT) == 0 and len(seen) == before + 1, code
        srv.shutdown()
        with socket.socket() as s:  # 아무도 듣지 않는 포트
            s.bind(("127.0.0.1", 0))
            dead = s.getsockname()[1]
        assert SEND({"endpoint": f"http://127.0.0.1:{dead}/x", "keys": sub["keys"]}, message("kpi_back"), (pub, priv), TTL_EVENT) == 0  # 연결 실패
        assert SEND({"endpoint": "http://127.0.0.1:1/x", "keys": {"p256dh": "x", "auth": "x"}}, message("kpi_back"), (pub, priv), TTL_EVENT) == 0  # 깨진 구독
        real, lines = sys.modules["pywebpush"], []
        handler = logging.Handler()
        handler.emit = lambda rec: lines.append(rec.getMessage())  # type: ignore[method-assign]
        logger.addHandler(handler)
        logger.disabled = False  # 이 한 건은 경고 문구를 확인한다
        sys.modules["pywebpush"] = None  # 설치가 깨진 흉내 — import 가 ImportError
        try:
            assert SEND(sub, message("kpi_back"), (pub, priv), TTL_EVENT) == 0  # 던지지 않고 0
            assert len(lines) == 1 and lines[0].startswith("push library unavailable: ")  # 연결 실패("push send failed")와 다른 문구
        finally:
            sys.modules["pywebpush"] = real
            logger.removeHandler(handler)
            logger.disabled = True
    finally:
        logger.disabled = False


def check() -> None:
    assert "pywebpush" not in sys.modules  # 앱이 뜰 때는 불러오지 않는다 — 보낼 때 처음 불러옴
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
    assert {k: MESSAGES[k][1] for k in MESSAGES if k.startswith("kpi_")} == {  # KPI 알림은 PC 에서 열라는 안내 — 평가·성과는 휴대폰에서 안 열린다
        "kpi_inbox": "PC에서 확인해 주세요", "kpi_back": "PC에서 사유를 확인하고 다시 작성해 주세요", "kpi_memo": "PC에서 확인해 주세요",
        "kpi_reply_to_author": "PC에서 확인해 주세요", "kpi_reply_to_owner": "PC에서 확인해 주세요"}
    assert message("kpi_back")["body"] == "PC에서 사유를 확인하고 다시 작성해 주세요" and message("kpi_inbox")["url"] == "/?go=perf.e1"  # 제목 · 열 화면은 그대로
    for t, b, _ in MESSAGES.values():  # 금액 · 이름 자리 없음
        assert not re.search(r"\d[\d,]*\s*원|\{name\}|\{amount\}|등급", t + b)

    assert morning_text([("p1", 1), ("inbox", 2)]) == {"title": "처리할 일이 3건 있어요", "body": "새 급여명세서 · 처리할 KPI 2건", "url": "/"}
    assert morning_text([("check", 1)]) == {"title": "처리할 일이 1건 있어요", "body": "KPI 월간 확인", "url": "/"}
    assert morning_text([("p1", 0), ("inbox", 1)])["body"] == "처리할 KPI"  # 0건인 종류는 이름도 안 나옴

    # 9시 마감 알림 — 볼 날: 월~목은 내일, 금요일은 토 · 일 · 월 / 지난달 한 줄은 7일, 7일이 토 · 일이면 그 앞 금요일
    D = date.fromisoformat
    assert deadline_days(D("2026-11-02")) == {"soon": ["2026-11-03"], "weekend": [], "prev": False}  # 월
    assert deadline_days(D("2026-11-06")) == {"soon": ["2026-11-07", "2026-11-08", "2026-11-09"], "weekend": ["2026-11-07", "2026-11-08"], "prev": True}  # 금, 7일이 토
    assert deadline_days(D("2026-12-07"))["prev"] and deadline_days(D("2027-02-05"))["prev"]  # 7일(월) · 7일이 일요일인 달의 금요일 5일
    assert not any(deadline_days(D(d))["prev"] for d in ("2026-11-05", "2026-12-04", "2026-12-08"))  # 목 5일 · 금 4일 · 8일
    T, WKND = "2026-11-06", ["2026-11-07", "2026-11-08"]
    tasks = [{"title": "a", "due": T}, {"title": "b", "due": T, "state": "진행 중"}, {"title": "c", "due": T, "state": "완료"},
             {"title": "d", "due": "2026-11-07"}, {"title": "e", "due": "2026-11-08", "state": "완료"}, {"title": "f", "due": "2026-10-30"},
             {"title": "g", "due": ""}, {"title": "h"}, {"title": 5, "due": T}, {"title": "i", "due": "내일"}, {"title": "j", "due": "2026-11-6"}, "k", None]
    assert task_counts(tasks, T, WKND) == (2, 1, 1)  # 끝낸 일 · 마감 없음 · 모양이 틀린 줄은 안 셈, 진행 중은 셈
    assert task_counts({"a": 1}, T, WKND) == (0, 0, 0) and task_counts(tasks, T, []) == (2, 0, 1)  # 목록이 아님 · 금요일이 아니면 주말 마감 없음
    assert tasks_text(2, 0, 1) == {"title": "오늘 마감인 업무가 2건 있어요", "body": "마감 지난 업무 1건 · 나의 업무에서 확인해 주세요", "url": "/?go=ws.w1"}  # 시안 화요일
    assert tasks_text(1, 1, 0)["body"] == "주말 마감 1건 · 나의 업무에서 확인해 주세요"  # 시안 금요일
    assert tasks_text(0, 1, 2) == {"title": "주말 마감인 업무가 1건 있어요", "body": "마감 지난 업무 2건 · 나의 업무에서 확인해 주세요", "url": "/?go=ws.w1"}
    assert tasks_text(0, 0, 3) == {"title": "마감 지난 업무가 3건 있어요", "body": "나의 업무에서 확인해 주세요", "url": "/?go=ws.w1"} and tasks_text(0, 0, 0) is None
    prog = {"items": [{"kind": "reg"}, {"kind": "irr", "scheds": [{"due": T, "result": None}, {"due": T, "result": {"text": "x"}}, {"due": "2026-11-09", "result": None},
                                                             {"due": "2026-10-01", "result": None}, {"due": "2026-12-01", "result": None}]}]}
    assert kpi_counts(prog, T, ["2026-11-07", "2026-11-08", "2026-11-09"]) == (1, ["2026-11-09"], 1) and kpi_counts(None, T, [T]) == (0, [], 0)  # 결과를 적은 일정은 안 셈
    TUE, FRI, D7 = D("2026-11-03"), D("2026-11-06"), D("2026-12-07")
    assert kpi_text(1, [], 0, 0, TUE) == {"title": "오늘 KPI 업무 마감일이에요", "body": "1건 · PC에서 결과를 적어 주세요", "url": "/?go=ws.w1"}  # 시안 화요일
    assert kpi_text(0, ["2026-11-04"], 0, 0, TUE)["title"] == "내일 KPI 업무 마감일이에요" and kpi_text(0, ["2026-11-09"], 0, 0, FRI)["title"] == "월요일 KPI 업무 마감일이에요"  # 시안 금요일
    assert kpi_text(0, ["2026-11-07"], 0, 0, FRI)["title"] == "주말 KPI 업무 마감일이에요" and kpi_text(0, ["2026-11-08", "2026-11-09"], 0, 0, FRI)["title"] == "주말·월요일 KPI 업무 마감일이에요"
    assert kpi_text(0, [], 0, 2, D7) == {"title": "오늘까지 KPI 지난달 한 줄을 써 주세요", "body": "2건 · PC에서 써 주세요", "url": "/?go=ws.w1"}  # 시안 12월 7일
    assert kpi_text(0, ["2026-11-09"], 0, 2, FRI) == {"title": "7일까지 KPI 지난달 한 줄을 써 주세요", "body": "2건 · 월요일 마감 1건 · PC에서 결과를 적어 주세요", "url": "/?go=ws.w1"}
    assert kpi_text(1, ["2026-11-04", "2026-11-04"], 3, 0, TUE)["body"] == "1건 · 내일 마감 2건 · 마감 지난 일정 3건 · PC에서 결과를 적어 주세요"
    assert kpi_text(1, [], 0, 2, D7)["body"] == "1건 · 지난달 한 줄 2건(오늘까지) · PC에서 결과를 적어 주세요"
    assert kpi_text(0, [], 1, 0, TUE) == {"title": "마감 지난 KPI 일정이 있어요", "body": "1건 · PC에서 결과를 적어 주세요", "url": "/?go=ws.w1"} and kpi_text(0, [], 0, 0, TUE) is None

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
    elif sys.argv[1:] == ["morning"]:
        from app import app  # 서버가 뜰 때와 같은 설정(DB 위치 …) — 보낸 사람 수만 출력한다

        print(morning(app, auth._now()))
    else:
        sys.exit("사용법: python -m push check | keygen | morning")
