"""
출장경비 자동 산출 API

nginx가 /api/ 요청을 이 서비스로 리버스 프록시한다.
Naver Map / Opinet API 키는 환경변수로만 전달되며, 프런트엔드에는 절대 노출되지 않는다.
"""

from __future__ import annotations

import logging
from datetime import date
from urllib.parse import quote

from flask import Flask, Response, jsonify, request

import export_utils
from services import opinet_api
from services.calculator import (
    TripCalculationResult,
    build_manual_route_data,
    build_trip_result,
    get_allowance_reason,
    get_fuel_efficiency,
    get_vehicle_type_label,
)
from services.naver_api import NaverMapAPIError, calculate_route_segments, search_places_by_keyword
from app_config import get_fuel_price_label, get_fuel_price_unit, get_fuel_type_label

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

VEHICLE_TYPES = {"under_1800", "over_1800"}
FUEL_TYPES = {"gasoline", "diesel", "lpg", "electric"}
MAX_DESTINATIONS = 10


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def _handle_api_error(err: ApiError):
    return jsonify({"error": err.message}), err.status


@app.errorhandler(NaverMapAPIError)
def _handle_naver_error(err: NaverMapAPIError):
    return jsonify({"error": str(err)}), 502


@app.errorhandler(404)
def _handle_not_found(_err):
    return jsonify({"error": "not found"}), 404


def _require_str(payload: dict, field: str, max_len: int = 200) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ApiError(f"'{field}' 값이 필요합니다.")
    value = value.strip()
    if len(value) > max_len:
        raise ApiError(f"'{field}' 값이 너무 깁니다. (최대 {max_len}자)")
    return value


def _require_destinations(payload: dict) -> list[str]:
    destinations = payload.get("destinations")
    if not isinstance(destinations, list) or not destinations:
        raise ApiError("출장지를 1곳 이상 입력해 주세요.")
    if len(destinations) > MAX_DESTINATIONS:
        raise ApiError(f"출장지는 최대 {MAX_DESTINATIONS}곳까지 입력할 수 있습니다.")
    cleaned = []
    for item in destinations:
        if not isinstance(item, str) or not item.strip():
            raise ApiError("출장지 주소가 비어 있습니다.")
        cleaned.append(item.strip())
    return cleaned


def _require_choice(payload: dict, field: str, choices: set[str]) -> str:
    value = payload.get(field)
    if value not in choices:
        raise ApiError(f"'{field}' 값이 올바르지 않습니다. ({', '.join(sorted(choices))} 중 하나)")
    return value


def _parse_trip_date(payload: dict) -> date:
    raw = payload.get("trip_date")
    if not isinstance(raw, str):
        raise ApiError("출장일자 형식이 올바르지 않습니다. (YYYY-MM-DD)")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ApiError("출장일자 형식이 올바르지 않습니다. (YYYY-MM-DD)") from exc


def _result_to_json(result: TripCalculationResult) -> dict:
    return {
        "trip_date": result.trip_date.isoformat(),
        "departure": result.departure,
        "destinations": result.destinations,
        "vehicle_type": result.vehicle_type,
        "vehicle_type_label": get_vehicle_type_label(result.vehicle_type),
        "fuel_type": result.fuel_type,
        "fuel_type_label": get_fuel_type_label(result.fuel_type),
        "fuel_efficiency": result.fuel_efficiency,
        "fuel_price": result.fuel_price,
        "fuel_price_label": get_fuel_price_label(result.fuel_type),
        "fuel_price_unit": get_fuel_price_unit(result.fuel_type),
        "fuel_price_source": result.fuel_price_source,
        "fuel_price_is_fallback": result.fuel_price_is_fallback,
        "total_distance_km": result.total_distance_km,
        "total_duration_min": result.total_duration_min,
        "one_way_distance_km": result.one_way_distance_km,
        "fuel_used_liters": result.fuel_used_liters,
        "fuel_cost": result.fuel_cost,
        "daily_allowance": result.daily_allowance,
        "allowance_reason": get_allowance_reason(result.one_way_distance_km, result.destination_count),
        "total_payment": result.total_payment,
        "route_segments": result.route_segments,
    }


def _result_from_json(payload: dict) -> TripCalculationResult:
    """/api/calculate 응답으로 받은 JSON을 그대로 되돌려 export용 결과 객체로 되살린다."""
    try:
        return TripCalculationResult(
            trip_date=date.fromisoformat(payload["trip_date"]),
            departure=str(payload["departure"]),
            destinations=list(payload["destinations"]),
            vehicle_type=str(payload["vehicle_type"]),
            fuel_type=str(payload["fuel_type"]),
            fuel_efficiency=float(payload["fuel_efficiency"]),
            fuel_price=float(payload["fuel_price"]),
            fuel_price_source=str(payload["fuel_price_source"]),
            fuel_price_is_fallback=bool(payload["fuel_price_is_fallback"]),
            total_distance_km=float(payload["total_distance_km"]),
            total_duration_min=float(payload["total_duration_min"]),
            one_way_distance_km=float(payload["one_way_distance_km"]),
            fuel_used_liters=float(payload["fuel_used_liters"]),
            fuel_cost=int(payload["fuel_cost"]),
            daily_allowance=int(payload["daily_allowance"]),
            total_payment=int(payload["total_payment"]),
            route_segments=list(payload.get("route_segments", [])),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError("계산 결과 형식이 올바르지 않습니다. 다시 계산해 주세요.") from exc


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/search-places")
def search_places():
    payload = request.get_json(silent=True) or {}
    query = _require_str(payload, "query", max_len=100)
    results = search_places_by_keyword(query)
    return jsonify({"results": results})


@app.post("/api/calculate")
def calculate():
    """
    거리 직접 입력 모드에서는 출발지/출장지 주소가 필요 없다.
    (원본 앱과 동일하게 출발지="거리 직접 입력", 출장지=["출장지 1", ...]로 자동 채운다)
    """
    payload = request.get_json(silent=True) or {}

    trip_date = _parse_trip_date(payload)
    vehicle_type = _require_choice(payload, "vehicle_type", VEHICLE_TYPES)
    fuel_type = _require_choice(payload, "fuel_type", FUEL_TYPES)
    manual = payload.get("manual_distance")

    if manual:
        if not isinstance(manual, dict) or "total_km" not in manual:
            raise ApiError("거리 직접 입력 값이 올바르지 않습니다.")
        try:
            total_km = float(manual["total_km"])
            dest_count = int(manual.get("destination_count", 1))
        except (TypeError, ValueError) as exc:
            raise ApiError("총 이동거리/출장지 개수는 숫자로 입력해 주세요.") from exc
        if total_km <= 0:
            raise ApiError("총 이동거리를 입력해 주세요.")
        if dest_count < 1 or dest_count > MAX_DESTINATIONS:
            raise ApiError(f"출장지 개수는 1~{MAX_DESTINATIONS} 사이여야 합니다.")

        departure = "거리 직접 입력"
        destinations = [f"출장지 {i + 1}" for i in range(dest_count)]
        route_data = build_manual_route_data(total_km)
    else:
        departure = _require_str(payload, "departure")
        destinations = _require_destinations(payload)
        route_data = calculate_route_segments([departure, *destinations])

    fuel_price_info = opinet_api.get_gyeonggi_previous_month_avg_price(trip_date, fuel_type)
    result = build_trip_result(trip_date, departure, destinations, vehicle_type, fuel_type, route_data, fuel_price_info)

    return jsonify(_result_to_json(result))


def _send_file(data: bytes, filename: str, mimetype: str) -> Response:
    """한글 파일명은 HTTP 헤더에 그대로 못 넣으므로 RFC 5987 형식으로 인코딩한다."""
    response = Response(data, mimetype=mimetype)
    response.headers["Content-Disposition"] = f"attachment; filename=download; filename*=UTF-8''{quote(filename)}"
    return response


@app.post("/api/export/excel")
def export_excel():
    payload = request.get_json(silent=True) or {}
    result = _result_from_json(payload)
    data = export_utils.export_to_excel(result)
    filename = f"출장경비_{result.trip_date.isoformat()}.xlsx"
    return _send_file(data, filename, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/export/pdf")
def export_pdf():
    payload = request.get_json(silent=True) or {}
    result = _result_from_json(payload)
    data = export_utils.export_to_pdf(result)
    filename = f"출장경비_{result.trip_date.isoformat()}.pdf"
    return _send_file(data, filename, "application/pdf")


@app.post("/api/export/application-form")
def export_application_form():
    payload = request.get_json(silent=True) or {}
    result = _result_from_json(payload)
    applicant_name = payload.get("applicant_name") or ""
    if not isinstance(applicant_name, str):
        applicant_name = ""
    data = export_utils.export_application_form(result, applicant_name.strip()[:50])
    filename = f"출장신청서_{result.trip_date.isoformat()}.pdf"
    return _send_file(data, filename, "application/pdf")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
