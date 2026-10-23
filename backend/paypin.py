"""급여·계약 비밀번호 (2026-10-22 인사담당자 — 급여명세서 · 원천징수영수증 · 연봉계약서 메뉴를 열 때 6자리 숫자 비밀번호).
설계: docs/superpowers/specs/2026-10-22-pay-pin-lock-design.md. 번호는 직원마다 하나, 해시만 pay_pins 에 둔다(원문은 DB · 로그 · 감사로그 어디에도 없다).

이 파일이 하는 일 (지금은 규칙 · 해시까지 — 서버 검문 · 길은 뒤 작업에서 이 파일에 더한다):
  PREFIXES              잠그는 길의 앞머리 — 이 목록은 이 한 곳에만 둔다
  problem()             쓸 수 없는 번호를 가린다 (6자리 숫자 · 두 번 같게 · 같은 숫자 6개 · 이어지는 숫자 · 생일)
  derive() · pin_hash() · pin_ok()    HR_PAY_KEY 에서 번호 전용 열쇠를 뽑고, 소금과 함께 해시한다
  python -m paypin check            위 규칙 · 해시 자체 검증 (CI)

해시: pin_hash = HMAC-SHA256(k, salt + pin).hexdigest(), k = HMAC-SHA256(HR_PAY_KEY 32바이트, b"hr-pay-pin").digest().
열쇠가 DB 밖(서버 .env)에 있어서 DB 파일만 새어 나가도 6자리(100만 가지)를 맞혀 볼 수 없다. 비교는 시간차 없이(hmac.compare_digest).
"""

from __future__ import annotations

import hashlib
import hmac
import re
import sys
from datetime import timedelta

PREFIXES = ("/api/payslips", "/api/withholding", "/api/contracts")  # 비밀번호 없이는 못 여는 길 — 모든 method
FAILS_MAX = 5  # 연달아 틀리면 잠김
OPEN_FOR = timedelta(seconds=360)  # 풀린 뒤 급여·계약을 안 쓰고 두면 다시 잠기는 시간

MSG_LOCKED = "급여·계약 메뉴는 비밀번호를 넣어야 볼 수 있어요."
MSG_NO_KEY = "서버에 급여 잠금 열쇠(HR_PAY_KEY)가 없어 비밀번호를 쓸 수 없습니다. 서버 설정을 먼저 마쳐 주세요."
MSG_WRONG = "비밀번호가 맞지 않아요."

PIN = re.compile(r"[0-9]{6}")  # [0-9]: \d 는 전각 숫자 등 다른 나라 숫자도 받는다
_RUN = "0123456789", "9876543210"  # 이어지는 숫자 (9 → 0 으로 넘어가는 것은 치지 않는다)


def problem(pin: object, again: object | None, birthday: str | None) -> str | None:
    """정하려는 번호가 안 되는 까닭(사용자에게 보일 문구), 괜찮으면 None. again 이 None 이면 두 번 비교는 건너뛴다(확인할 때). birthday 는 users.birthday 'MM-DD'."""
    if not isinstance(pin, str) or not PIN.fullmatch(pin):
        return "숫자 6자리로 정해 주세요."
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
