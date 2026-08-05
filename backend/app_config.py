"""
출장경비 자동 산출 API - 설정 상수 모듈

API URL, 연비, 일비 규정 등을 한곳에서 관리한다.
API 키는 환경변수로만 읽는다 (docker-compose.yml의 environment 참고).
"""

import os

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID", "").strip()
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET", "").strip()
NAVER_GEOCODE_URL = "https://maps.apigw.ntruss.com/map-geocode/v2/geocode"
NAVER_DIRECTIONS_URL = "https://maps.apigw.ntruss.com/map-direction/v1/driving"

NAVER_LOCAL_CLIENT_ID = os.environ.get("NAVER_LOCAL_CLIENT_ID", "").strip()
NAVER_LOCAL_CLIENT_SECRET = os.environ.get("NAVER_LOCAL_CLIENT_SECRET", "").strip()
NAVER_LOCAL_SEARCH_URL = "https://openapi.naver.com/v1/search/local.json"
NAVER_PLACE_SEARCH_DISPLAY = 8

OPINET_API_KEY = os.environ.get("OPINET_API_KEY", "").strip()
GYEONGGI_SIDO_CODE = "02"
GASOLINE_PRODUCT_CODE = "B027"
DIESEL_PRODUCT_CODE = "D047"
LPG_PRODUCT_CODE = "K015"
OPINET_DATE_AREA_AVG_URL = "https://www.opinet.co.kr/api/dateAreaAvgRecentPrice.do"

FUEL_TYPE_OPTIONS = {
    "gasoline": {
        "label": "휘발유",
        "price_label": "전월 경기도 평균 휘발유",
        "product_code": GASOLINE_PRODUCT_CODE,
        "default_price": 1700,
    },
    "diesel": {
        "label": "경유",
        "price_label": "전월 경기도 평균 경유",
        "product_code": DIESEL_PRODUCT_CODE,
        "default_price": 1550,
    },
    "lpg": {
        "label": "LPG",
        "price_label": "전월 경기도 평균 LPG",
        "product_code": LPG_PRODUCT_CODE,
        "default_price": 1000,
    },
    "electric": {
        "label": "전기차",
        "price_label": "전기차 충전 단가",
        "product_code": "",
        "default_price": 347.2,
        "fixed_price": 347.2,
        "use_opinet": False,
    },
}

FUEL_EFFICIENCY_UNDER_1800 = 9
FUEL_EFFICIENCY_OVER_1800 = 8
FUEL_EFFICIENCY_LPG = 6
FUEL_EFFICIENCY_ELECTRIC = 4

DAILY_ALLOWANCE_AMOUNT = 20_000
ONE_WAY_DISTANCE_THRESHOLD_KM = 100
MIN_DESTINATIONS_FOR_ALLOWANCE = 3


def get_fuel_type_label(fuel_type: str) -> str:
    fuel_config = FUEL_TYPE_OPTIONS.get(fuel_type)
    return str(fuel_config["label"]) if fuel_config else fuel_type


def get_fuel_price_label(fuel_type: str) -> str:
    fuel_config = FUEL_TYPE_OPTIONS.get(fuel_type)
    return str(fuel_config["price_label"]) if fuel_config else "전월 경기도 평균 유가"


def get_fuel_price_unit(fuel_type: str) -> str:
    return "kWh" if fuel_type == "electric" else "L"


def uses_opinet_price(fuel_type: str) -> bool:
    fuel_config = FUEL_TYPE_OPTIONS.get(fuel_type)
    if not fuel_config:
        return True
    return bool(fuel_config.get("use_opinet", True))
