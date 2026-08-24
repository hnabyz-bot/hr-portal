"""연봉계약서 금액 계산과 문서해시.

인상률 수치 자체는 여기 없다. DB의 salary_raise_rates 테이블에만 있고
(hr-portal은 Public 저장소다) 이 모듈은 넘겨받은 표를 쓸 뿐이다.
"""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_DOWN, Decimal
from typing import Mapping

from hr_eval.domain.errors import NotFoundError
from hr_eval.domain.models import Grade


def resolve_raise_pct(rates: Mapping[Grade, Decimal], grade: Grade) -> Decimal:
    try:
        return rates[grade]
    except KeyError:
        raise NotFoundError(
            f"이 평가기간에 {grade.value}등급 인상률이 등록되지 않았습니다"
        ) from None


def calculate_new_salary(base_salary_before: Decimal, raise_pct: Decimal) -> Decimal:
    """인상 후 연봉. 원 단위 내림.

    올림하면 계약서 금액이 규정상 인상률을 넘어간다. 내림은 최대 1원
    차이라 실무상 문제가 없고, 규정 위반 쪽이 훨씬 비싸다.
    """
    raw = base_salary_before * (Decimal(1) + raise_pct / Decimal(100))
    return raw.quantize(Decimal("1"), rounding=ROUND_DOWN)


def build_document_hash(payload: Mapping[str, str]) -> str:
    """계약 본문의 SHA-256.

    키를 정렬해 직렬화하므로 dict 순서가 달라도 같은 해시가 나온다.
    나중에 금액이나 등급이 바뀌면 해시가 어긋나 위변조를 잡을 수 있다.
    """
    canonical = json.dumps(
        dict(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
