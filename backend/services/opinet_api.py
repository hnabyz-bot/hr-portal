"""
Opinet (한국석유공사) 유가 API 연동 모듈

출장일 기준 전월 경기도 주유소 일별 평균 유가의 월평균을 조회한다.
(휘발유 B027 / 경유 D047 / LPG K015)

동일 월·유종은 캐시된 값을 사용해 기준이 바뀌지 않는다.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import requests

import app_config

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
_CACHE_FILE = _CACHE_DIR / "opinet_monthly_prices.json"
_MEMORY_CACHE: dict[str, dict[str, Any]] = {}


def _get_fuel_config(fuel_type: str) -> dict[str, Any]:
    if fuel_type not in app_config.FUEL_TYPE_OPTIONS:
        raise ValueError(f"알 수 없는 유종: {fuel_type}")
    return app_config.FUEL_TYPE_OPTIONS[fuel_type]


def _get_previous_month_range(reference: date | None = None) -> tuple[date, date]:
    today = reference or date.today()
    first_of_current = today.replace(day=1)
    last_of_previous = first_of_current - timedelta(days=1)
    first_of_previous = last_of_previous.replace(day=1)
    return first_of_previous, last_of_previous


def _build_params(**extra: str) -> dict[str, str]:
    if not app_config.OPINET_API_KEY:
        raise ValueError("Opinet API 키가 설정되지 않았습니다.")
    params = {"out": "json", "code": app_config.OPINET_API_KEY}
    params.update(extra)
    return params


def _parse_oil_list(data: dict[str, Any]) -> list[dict[str, Any]]:
    oil_list = data.get("RESULT", {}).get("OIL", [])
    if isinstance(oil_list, dict):
        return [oil_list]
    if isinstance(oil_list, list):
        return oil_list
    return []


def _fetch_opinet(url: str, **params: str) -> dict[str, Any]:
    response = requests.get(url, params=_build_params(**params), timeout=15)
    response.raise_for_status()
    return response.json()


def _parse_trade_date(trade_dt: str) -> date:
    return date(int(trade_dt[:4]), int(trade_dt[4:6]), int(trade_dt[6:8]))


def _cache_key(month_start: date, product_code: str) -> str:
    return f"{month_start.strftime('%Y-%m')}-{product_code}"


def _load_disk_cache() -> None:
    if _MEMORY_CACHE:
        return
    if not _CACHE_FILE.exists():
        return
    try:
        data = json.loads(_CACHE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _MEMORY_CACHE.update(data)
    except Exception as exc:
        logger.warning("Opinet 월별 유가 캐시 로드 실패: %s", exc)


def _save_disk_cache() -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(_MEMORY_CACHE, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        logger.warning("Opinet 월별 유가 캐시 저장 실패: %s", exc)


def _fetch_gyeonggi_monthly_prices(product_code: str, month_start: date, month_end: date) -> dict[str, Any]:
    """
    dateAreaAvgRecentPrice API로 경기도 전월 일별 유가를 수집한다.
    Opinet 무료 API는 1회 조회 시 기준일 전후 7일치를 반환하므로,
    전월 모든 날짜를 포함하도록 월 중 여러 기준일을 조회한다.
    """
    cache_key = _cache_key(month_start, product_code)
    _load_disk_cache()
    if cache_key in _MEMORY_CACHE:
        return _MEMORY_CACHE[cache_key]

    month_day_count = (month_end - month_start).days
    query_offsets = list(range(0, month_day_count + 1, 3))
    if month_day_count not in query_offsets:
        query_offsets.append(month_day_count)

    records_by_date: dict[str, float] = {}
    for offset in query_offsets:
        sample_date = month_start + timedelta(days=offset)
        data = _fetch_opinet(
            app_config.OPINET_DATE_AREA_AVG_URL,
            prodcd=product_code,
            area=app_config.GYEONGGI_SIDO_CODE,
            date=sample_date.strftime("%Y%m%d"),
        )
        for record in _parse_oil_list(data):
            trade_dt = str(record.get("DATE", ""))
            if len(trade_dt) != 8:
                continue
            record_date = _parse_trade_date(trade_dt)
            if month_start <= record_date <= month_end:
                records_by_date[trade_dt] = float(record["PRICE"])

    expected_days = month_day_count + 1
    collected_days = len(records_by_date)
    if collected_days == 0:
        raise ValueError(f"{month_start.strftime('%Y년 %m월')} 경기도 {product_code} 일별 유가 데이터가 없습니다.")

    daily_prices = [records_by_date[key] for key in sorted(records_by_date.keys())]
    avg_price = sum(daily_prices) / len(daily_prices)

    result = {
        "price": round(avg_price, 2),
        "day_count": collected_days,
        "expected_days": expected_days,
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "product_code": product_code,
    }
    _MEMORY_CACHE[cache_key] = result
    _save_disk_cache()
    return result


def get_gyeonggi_previous_month_avg_price(reference_date: date | None = None, fuel_type: str = "gasoline") -> dict[str, Any]:
    """
    경기도 전월 평균 유가(휘발유/경유/LPG)를 Opinet에서 조회한다.
    전기차는 Opinet 조회 없이 고정 단가를 사용한다.
    """
    fuel_config = _get_fuel_config(fuel_type)
    fuel_label = str(fuel_config["label"])
    price_label = str(fuel_config["price_label"])

    if not app_config.uses_opinet_price(fuel_type):
        fixed_price = float(fuel_config.get("fixed_price", fuel_config["default_price"]))
        return {
            "price": fixed_price,
            "fuel_type": fuel_type,
            "fuel_label": fuel_label,
            "price_label": price_label,
            "source": f"고정 충전 단가 ({fixed_price:,.1f}원/kWh)",
            "is_fallback": False,
        }

    product_code = str(fuel_config["product_code"])
    default_price = float(fuel_config["default_price"])

    ref = reference_date or date.today()
    month_start, month_end = _get_previous_month_range(ref)
    month_label = f"{month_start.year}년 {month_start.month}월"

    try:
        monthly_data = _fetch_gyeonggi_monthly_prices(product_code, month_start, month_end)
        day_count = int(monthly_data["day_count"])

        return {
            "price": float(monthly_data["price"]),
            "fuel_type": fuel_type,
            "fuel_label": fuel_label,
            "price_label": price_label,
            "source": f"Opinet {month_label} 경기도 {fuel_label} 월평균 ({day_count}일 평균)",
            "is_fallback": False,
        }
    except Exception as exc:
        logger.warning("Opinet API 조회 실패 (%s), 기본값(%s원/L) 사용: %s", price_label, default_price, exc)
        return {
            "price": default_price,
            "fuel_type": fuel_type,
            "fuel_label": fuel_label,
            "price_label": price_label,
            "source": f"기본값 ({price_label} {default_price:,.0f}원/L) - Opinet API 연결 실패",
            "is_fallback": True,
        }
