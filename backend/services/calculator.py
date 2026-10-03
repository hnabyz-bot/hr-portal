"""
출장경비 계산 로직 모듈

거리, 연비, 유가 정보를 바탕으로 유류비·일비·최종 지급금액을 산출한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import app_config


@dataclass
class TripCalculationResult:
    """출장경비 계산 결과를 담는 데이터 클래스."""

    trip_date: date
    departure: str
    destinations: list[str]
    vehicle_type: str
    fuel_type: str
    fuel_efficiency: float
    fuel_price: float
    fuel_price_source: str
    fuel_price_is_fallback: bool

    total_distance_km: float = 0.0
    total_duration_min: float = 0.0
    one_way_distance_km: float = 0.0
    fuel_used_liters: float = 0.0
    fuel_cost: int = 0
    daily_allowance: int = 0
    total_payment: int = 0

    route_segments: list[dict[str, Any]] = field(default_factory=list)

    @property
    def destination_count(self) -> int:
        return len(self.destinations)

    @property
    def returns_to_departure(self) -> bool:
        return any(s.get("is_return") for s in self.route_segments)


def get_fuel_efficiency(vehicle_type: str, fuel_type: str = "gasoline") -> float:
    """차량 구분·유종에 따른 연비(km/L 또는 km/kWh)를 반환한다."""
    if fuel_type == "lpg":
        return float(app_config.FUEL_EFFICIENCY_LPG)
    if fuel_type == "electric":
        return float(app_config.FUEL_EFFICIENCY_ELECTRIC)
    if vehicle_type == "under_1800":
        return float(app_config.FUEL_EFFICIENCY_UNDER_1800)
    if vehicle_type == "over_1800":
        return float(app_config.FUEL_EFFICIENCY_OVER_1800)
    raise ValueError(f"알 수 없는 차량 구분: {vehicle_type}")


def get_vehicle_type_label(vehicle_type: str) -> str:
    labels = {"under_1800": "1800cc 미만", "over_1800": "1800cc 이상"}
    return labels.get(vehicle_type, vehicle_type)


def calculate_fuel_used(total_distance_km: float, fuel_efficiency: float) -> float:
    """공식: 총거리 / 연비 = 사용연료(L)"""
    if fuel_efficiency <= 0:
        return 0.0
    return round(total_distance_km / fuel_efficiency, 2)


def calculate_fuel_cost(fuel_used_liters: float, fuel_price: float) -> int:
    """공식: 사용연료 x 전월평균유가 = 유류비"""
    return int(round(fuel_used_liters * fuel_price))


def calculate_daily_allowance(one_way_distance_km: float, destination_count: int) -> int:
    """
    활동비(국내 출장) 지급액을 계산한다.

    회사 규정(출장여비규정 제13조): 편도 100km 초과 또는 출장지 3곳 이상 -> 20,000원, 그 외 0원.
    """
    meets_distance = one_way_distance_km > app_config.ONE_WAY_DISTANCE_THRESHOLD_KM
    meets_destinations = destination_count >= app_config.MIN_DESTINATIONS_FOR_ALLOWANCE
    if meets_distance or meets_destinations:
        return app_config.DAILY_ALLOWANCE_AMOUNT
    return 0


def get_allowance_reason(one_way_distance_km: float, destination_count: int) -> str:
    """활동비 지급/미지급 사유를 설명 문자열로 반환한다."""
    reasons: list[str] = []
    if one_way_distance_km > app_config.ONE_WAY_DISTANCE_THRESHOLD_KM:
        reasons.append(f"편도 거리 {one_way_distance_km:g}km (기준 {app_config.ONE_WAY_DISTANCE_THRESHOLD_KM}km 초과)")
    if destination_count >= app_config.MIN_DESTINATIONS_FOR_ALLOWANCE:
        reasons.append(f"출장지 {destination_count}곳 (기준 {app_config.MIN_DESTINATIONS_FOR_ALLOWANCE}곳 이상)")

    if reasons:
        return "활동비 지급: " + ", ".join(reasons)
    return f"활동비 미지급: 편도 {one_way_distance_km:g}km, 출장지 {destination_count}곳 (기준 미충족)"


def build_manual_route_data(total_distance_km: float) -> dict[str, Any]:
    """직접 입력한 총 이동거리(km)로 경로 데이터 dict를 생성한다. 총 이동거리는 왕복 합계이므로 편도는 그 절반으로 본다."""
    total = round(total_distance_km, 2)
    return {
        "segments": [],
        "total_distance_km": total,
        "total_duration_min": 0.0,
        "one_way_distance_km": total / 2,
    }


def split_return_trip(departure: str, destinations: list[str], return_trip: bool) -> tuple[list[str], bool]:
    """마지막 출장지가 출발지와 같으면 출장지가 아니라 복귀로 본다 (방문 곳 수에 세지 않는다)."""
    def key(address: str) -> str:
        return "".join(address.split())

    while len(destinations) > 1 and key(destinations[-1]) == key(departure):
        destinations, return_trip = destinations[:-1], True
    return destinations, return_trip


def apply_return_trip(route_data: dict[str, Any]) -> dict[str, Any]:
    """
    마지막 구간(마지막 출장지 -> 출발지)을 복귀로 표시하고, 편도 거리를 회사에서 가장 먼 출장지 기준으로 고친다.

    규정 제13조는 "본사를 기준으로 편도 100km 초과 지역". 첫 구간과 복귀 구간 중 긴 쪽을 편도로 본다.
    """
    # 중간 출장지는 따로 재지 않는다 — 3곳 이상이면 거리와 무관하게 지급되므로 2곳까지만 정확하면 된다.
    segments = route_data["segments"]
    segments[-1]["is_return"] = True
    route_data["one_way_distance_km"] = max(segments[0]["distance_km"], segments[-1]["distance_km"])
    return route_data


def build_trip_result(
    trip_date: date,
    departure: str,
    destinations: list[str],
    vehicle_type: str,
    fuel_type: str,
    route_data: dict[str, Any],
    fuel_price_info: dict[str, Any],
) -> TripCalculationResult:
    """경로·유가 정보를 종합하여 최종 계산 결과를 생성한다."""
    fuel_efficiency = get_fuel_efficiency(vehicle_type, fuel_type)
    total_distance_km = route_data["total_distance_km"]
    total_duration_min = route_data["total_duration_min"]
    one_way_distance_km = route_data["one_way_distance_km"]

    fuel_used = calculate_fuel_used(total_distance_km, fuel_efficiency)
    fuel_price = fuel_price_info["price"]
    fuel_cost = calculate_fuel_cost(fuel_used, fuel_price)
    daily_allowance = calculate_daily_allowance(one_way_distance_km, len(destinations))
    total_payment = fuel_cost + daily_allowance

    return TripCalculationResult(
        trip_date=trip_date,
        departure=departure,
        destinations=destinations,
        vehicle_type=vehicle_type,
        fuel_type=fuel_type,
        fuel_efficiency=fuel_efficiency,
        fuel_price=fuel_price,
        fuel_price_source=fuel_price_info["source"],
        fuel_price_is_fallback=fuel_price_info["is_fallback"],
        total_distance_km=total_distance_km,
        total_duration_min=total_duration_min,
        one_way_distance_km=one_way_distance_km,
        fuel_used_liters=fuel_used,
        fuel_cost=fuel_cost,
        daily_allowance=daily_allowance,
        total_payment=total_payment,
        route_segments=route_data.get("segments", []),
    )


if __name__ == "__main__":
    # 자체 검증: python -m services.calculator (backend 폴더에서)
    def _manual(total_km: float, count: int) -> int:
        return calculate_daily_allowance(build_manual_route_data(total_km)["one_way_distance_km"], count)

    # 직접 입력한 총 이동거리는 왕복 합계 -> 200km를 넘어야 활동비 (출장여비규정 제13조: 편도 100km 초과)
    assert _manual(100, 1) == 0
    assert _manual(199, 2) == 0
    assert _manual(200, 1) == 0
    assert _manual(200.1, 1) == app_config.DAILY_ALLOWANCE_AMOUNT
    assert _manual(50, 3) == app_config.DAILY_ALLOWANCE_AMOUNT
    # 주소 자동 계산: 편도 100km 초과 또는 3곳 이상
    assert calculate_daily_allowance(100, 1) == 0
    assert calculate_daily_allowance(100.1, 1) == app_config.DAILY_ALLOWANCE_AMOUNT
    assert calculate_daily_allowance(10, 3) == app_config.DAILY_ALLOWANCE_AMOUNT
    assert get_allowance_reason(100, 2).startswith("활동비 미지급")
    assert get_allowance_reason(100.1, 1).startswith("활동비 지급")
    # 복귀: 출발지를 마지막 출장지로 넣어도 방문 곳 수에 세지 않는다 (띄어쓰기 달라도 같은 주소)
    assert split_return_trip("용인시 처인성로41-16", ["수원", "부산", "용인시  처인성로 41-16"], False) == (["수원", "부산"], True)
    assert split_return_trip("용인", ["수원"], True) == (["수원"], True)
    assert split_return_trip("용인", ["수원", "부산"], False) == (["수원", "부산"], False)
    # 편도 = 회사에서 가장 먼 출장지: 용인 -> 수원(25km) -> 부산 -> 용인(복귀 380km)
    _route = apply_return_trip({"segments": [{"distance_km": 25.0}, {"distance_km": 370.0}, {"distance_km": 380.0}], "one_way_distance_km": 25.0})
    assert _route["one_way_distance_km"] == 380.0 and _route["segments"][-1]["is_return"] and "is_return" not in _route["segments"][0]
    assert calculate_daily_allowance(_route["one_way_distance_km"], 2) == app_config.DAILY_ALLOWANCE_AMOUNT
    print("경비 계산 자체 검증 통과")
