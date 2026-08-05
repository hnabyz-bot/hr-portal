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
    일비 지급액을 계산한다.

    회사 규정: 편도 100km 이상 또는 출장지 3곳 이상 -> 20,000원, 그 외 0원.
    """
    meets_distance = one_way_distance_km >= app_config.ONE_WAY_DISTANCE_THRESHOLD_KM
    meets_destinations = destination_count >= app_config.MIN_DESTINATIONS_FOR_ALLOWANCE
    if meets_distance or meets_destinations:
        return app_config.DAILY_ALLOWANCE_AMOUNT
    return 0


def get_allowance_reason(one_way_distance_km: float, destination_count: int) -> str:
    """일비 지급/미지급 사유를 설명 문자열로 반환한다."""
    reasons: list[str] = []
    if one_way_distance_km >= app_config.ONE_WAY_DISTANCE_THRESHOLD_KM:
        reasons.append(f"편도 거리 {one_way_distance_km:.1f}km (기준 {app_config.ONE_WAY_DISTANCE_THRESHOLD_KM}km 이상)")
    if destination_count >= app_config.MIN_DESTINATIONS_FOR_ALLOWANCE:
        reasons.append(f"출장지 {destination_count}곳 (기준 {app_config.MIN_DESTINATIONS_FOR_ALLOWANCE}곳 이상)")

    if reasons:
        return "일비 지급: " + ", ".join(reasons)
    return f"일비 미지급: 편도 {one_way_distance_km:.1f}km, 출장지 {destination_count}곳 (기준 미충족)"


def build_manual_route_data(total_distance_km: float, one_way_distance_km: float | None = None) -> dict[str, Any]:
    """직접 입력한 이동거리(km)로 경로 데이터 dict를 생성한다."""
    total = round(total_distance_km, 2)
    return {
        "segments": [],
        "total_distance_km": total,
        "total_duration_min": 0.0,
        "one_way_distance_km": round(one_way_distance_km, 2) if one_way_distance_km is not None else total,
    }


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
