"""
Naver Map API 연동 모듈

- Geocoding API: 주소 -> 좌표 변환
- Directions 5 API: 자동차 경로 거리/소요시간 조회
- 지역 검색 API: 장소명 -> 도로명 주소
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

import app_config

logger = logging.getLogger(__name__)


class NaverMapAPIError(Exception):
    """Naver Map API 호출 중 발생한 오류."""


NAVER_ERROR_GUIDES: dict[str, str] = {
    "200": "인증 실패: Client ID / Client Secret이 올바르지 않습니다.",
    "210": "API 사용 권한 없음: Maps 구독 및 Application API 설정을 확인해 주세요.",
    "400": "API 호출 한도(Quota)를 초과했습니다. 잠시 후 다시 시도해 주세요.",
    "410": "API 요청 속도 제한에 걸렸습니다. 잠시 후 다시 시도해 주세요.",
    "420": "API 요청 속도 제한에 걸렸습니다. 잠시 후 다시 시도해 주세요.",
}


def _get_headers() -> dict[str, str]:
    if not app_config.NAVER_CLIENT_ID or not app_config.NAVER_CLIENT_SECRET:
        raise NaverMapAPIError("Naver API 키가 설정되지 않았습니다.")
    return {
        "X-NCP-APIGW-API-KEY-ID": app_config.NAVER_CLIENT_ID,
        "X-NCP-APIGW-API-KEY": app_config.NAVER_CLIENT_SECRET,
    }


def _parse_api_error(response: requests.Response, context: str) -> NaverMapAPIError:
    status = response.status_code
    error_code = ""
    message = ""

    try:
        payload = response.json()
        error = payload.get("error", {})
        error_code = str(error.get("errorCode", ""))
        message = str(error.get("message", ""))
    except Exception:
        message = response.text[:200]

    guide = NAVER_ERROR_GUIDES.get(error_code, "")
    parts = [f"{context} (HTTP {status})"]
    if message:
        parts.append(message)
    if guide:
        parts.append(f"-> {guide}")

    return NaverMapAPIError(" | ".join(parts))


def _request_naver_api(url: str, params: dict[str, Any], context: str, timeout: int = 15) -> dict[str, Any]:
    try:
        response = requests.get(url, headers=_get_headers(), params=params, timeout=timeout)
    except requests.RequestException as exc:
        raise NaverMapAPIError(f"{context}: 네트워크 연결 실패") from exc

    if not response.ok:
        raise _parse_api_error(response, context)

    return response.json()


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text).strip()


def _get_local_search_headers() -> dict[str, str]:
    client_id = app_config.NAVER_LOCAL_CLIENT_ID
    client_secret = app_config.NAVER_LOCAL_CLIENT_SECRET
    if not client_id or not client_secret:
        raise NaverMapAPIError("Naver 지역 검색 API 키가 설정되지 않았습니다.")
    return {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret,
    }


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


GOVERNMENT_SUFFIX_PATTERN = re.compile(r"(시청|구청|군청|도청|읍사무소|면사무소|주민센터|행정복지센터)$")
COMMERCIAL_BRANCH_PATTERN = re.compile(r"(점|본점|지점|직영점|프랜)$")
PUBLIC_CATEGORY_KEYWORDS = ("공공", "사회기관", "시청", "구청", "군청", "도청", "청사", "행정", "관공서")
FOOD_CATEGORY_KEYWORDS = ("한식", "중식", "일식", "양식", "카페", "음식", "요리", "치킨", "피자", "고기", "국밥", "분식", "술집", "주점")


def _score_place_relevance(query: str, name: str, category: str) -> float:
    q = _normalize_for_match(query)
    n = _normalize_for_match(name)
    cat = category.strip()

    if not q or not n:
        return 0.0

    score = 0.0
    if n == q:
        score += 120
    elif n.startswith(q):
        remainder = n[len(q):]
        if not remainder or remainder in ("본관", "별관", "본청"):
            score += 100
        elif COMMERCIAL_BRANCH_PATTERN.search(remainder):
            score += 15
        else:
            score += 70
    elif q in n:
        idx = n.find(q)
        remainder = n[idx + len(q):]
        score += 10 if COMMERCIAL_BRANCH_PATTERN.search(remainder) else 45
    else:
        score += 5

    if any(keyword in cat for keyword in PUBLIC_CATEGORY_KEYWORDS):
        score += 45
    if any(keyword in cat for keyword in FOOD_CATEGORY_KEYWORDS):
        score -= 40
    return score


def _build_supplemental_queries(query: str) -> list[str]:
    q = query.strip()
    queries = [q]
    normalized = _normalize_for_match(q)

    if GOVERNMENT_SUFFIX_PATTERN.search(normalized) and not any(c in q for c in ("경기", "서울", "특별")):
        queries.append(f"경기도 {q}")

    seen: set[str] = set()
    unique_queries: list[str] = []
    for item in queries:
        key = _normalize_for_match(item)
        if key not in seen:
            seen.add(key)
            unique_queries.append(item)
    return unique_queries


def _dedupe_places(places: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for place in places:
        key = place.get("road_address", "").strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(place)
    return deduped


def _rank_places(query: str, places: list[dict[str, str]]) -> list[dict[str, str]]:
    scored = [(_score_place_relevance(query, p["name"], p.get("category", "")), p) for p in places]
    scored.sort(key=lambda item: item[0], reverse=True)
    return [place for _, place in scored]


def _normalize_place_result(*, name: str, road_address: str = "", jibun_address: str = "",
                             category: str = "", telephone: str = "") -> dict[str, str] | None:
    road = road_address.strip()
    if not road:
        return None
    return {
        "name": name.strip() or road,
        "road_address": road,
        "jibun_address": jibun_address.strip(),
        "address": road,
        "category": category.strip(),
        "telephone": telephone.strip(),
    }


def _parse_local_search_items(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for item in items:
        normalized = _normalize_place_result(
            name=_strip_html(str(item.get("title", ""))),
            road_address=str(item.get("roadAddress", "")),
            jibun_address=str(item.get("address", "")),
            category=str(item.get("category", "")),
            telephone=str(item.get("telephone", "")),
        )
        if normalized:
            results.append(normalized)
    return results


def _search_places_via_local_api(query: str, display: int | None = None) -> list[dict[str, str]]:
    if display is None:
        display = app_config.NAVER_PLACE_SEARCH_DISPLAY

    fetch_count = min(max(display, 8), 10)
    merged_results: list[dict[str, str]] = []

    for search_query in _build_supplemental_queries(query):
        try:
            response = requests.get(
                app_config.NAVER_LOCAL_SEARCH_URL,
                headers=_get_local_search_headers(),
                params={"query": search_query.strip(), "display": fetch_count, "start": 1, "sort": "random"},
                timeout=15,
            )
        except requests.RequestException as exc:
            raise NaverMapAPIError("장소 검색 실패: 네트워크 연결 오류") from exc

        if response.status_code == 401:
            raise NaverMapAPIError("NCP_LOCAL_AUTH_MISMATCH")
        if not response.ok:
            raise NaverMapAPIError(f"장소 검색 API 오류 (HTTP {response.status_code})")

        merged_results.extend(_parse_local_search_items(response.json().get("items", [])))

    ranked = _rank_places(query, _dedupe_places(merged_results))
    return ranked[:display]


def _search_places_via_maps_geocoding(query: str) -> list[dict[str, str]]:
    data = _request_naver_api(
        app_config.NAVER_GEOCODE_URL,
        {"query": query.strip(), "count": 10},
        context="Geocoding(장소 검색)",
    )
    results: list[dict[str, str]] = []
    for item in data.get("addresses", []):
        normalized = _normalize_place_result(
            name=query.strip(),
            road_address=str(item.get("roadAddress", "")),
            jibun_address=str(item.get("jibunAddress", "")),
        )
        if normalized:
            results.append(normalized)
    return results


def search_places_by_keyword(query: str, display: int | None = None) -> list[dict[str, str]]:
    """장소명 키워드로 도로명 주소 후보 목록을 검색한다."""
    if not query or not query.strip():
        raise NaverMapAPIError("검색할 장소명을 입력해 주세요.")

    local_auth_ok = False
    try:
        results = _search_places_via_local_api(query, display=display)
        local_auth_ok = True
        if results:
            return results
    except NaverMapAPIError as exc:
        if str(exc) != "NCP_LOCAL_AUTH_MISMATCH":
            logger.warning("지역 검색 API 실패: %s", exc)

    results = _search_places_via_maps_geocoding(query)
    if results:
        return results

    if not local_auth_ok:
        raise NaverMapAPIError("Naver 지역 검색 API 인증 실패: 키를 확인해 주세요.")

    raise NaverMapAPIError(f"'{query.strip()}'에 대한 도로명 주소 검색 결과가 없습니다.")


def geocode_address(address: str) -> tuple[float, float]:
    """주소 문자열을 (경도, 위도)로 변환한다."""
    if not address or not address.strip():
        raise NaverMapAPIError("주소가 비어 있습니다.")

    data = _request_naver_api(
        app_config.NAVER_GEOCODE_URL,
        {"query": address.strip()},
        context="Geocoding(주소 검색)",
    )

    addresses = data.get("addresses", [])
    if not addresses:
        raise NaverMapAPIError(f"주소를 찾을 수 없습니다: {address}")

    first = addresses[0]
    return float(first["x"]), float(first["y"])


def get_driving_route(start_address: str, goal_address: str, option: str = "trafast") -> dict[str, Any]:
    """두 지점 간 자동차 경로 정보를 조회한다."""
    start_lon, start_lat = geocode_address(start_address)
    goal_lon, goal_lat = geocode_address(goal_address)

    data = _request_naver_api(
        app_config.NAVER_DIRECTIONS_URL,
        {"start": f"{start_lon},{start_lat}", "goal": f"{goal_lon},{goal_lat}", "option": option},
        context="Directions(경로 검색)",
        timeout=20,
    )

    routes = data.get("route", {}).get(option, [])
    if not routes:
        raise NaverMapAPIError(f"경로를 찾을 수 없습니다: {start_address} -> {goal_address}")

    summary = routes[0].get("summary", {})
    distance_m = int(summary.get("distance", 0))
    duration_ms = int(summary.get("duration", 0))

    return {
        "start": start_address,
        "goal": goal_address,
        "distance_m": distance_m,
        "duration_ms": duration_ms,
        "distance_km": round(distance_m / 1000, 2),
        "duration_min": round(duration_ms / 1000 / 60, 1),
    }


def calculate_route_segments(locations: list[str]) -> dict[str, Any]:
    """연속된 지점 목록에 대해 구간별 거리/시간을 계산한다."""
    if len(locations) < 2:
        raise NaverMapAPIError("경로 계산을 위해 최소 2개 이상의 지점이 필요합니다.")

    segments: list[dict[str, Any]] = []
    total_distance_km = 0.0
    total_duration_min = 0.0

    for idx in range(len(locations) - 1):
        segment = get_driving_route(locations[idx], locations[idx + 1])
        segment["segment_no"] = idx + 1
        segments.append(segment)
        total_distance_km += segment["distance_km"]
        total_duration_min += segment["duration_min"]

    one_way_distance_km = segments[0]["distance_km"] if segments else 0.0

    return {
        "segments": segments,
        "total_distance_km": round(total_distance_km, 2),
        "total_duration_min": round(total_duration_min, 1),
        "one_way_distance_km": round(one_way_distance_km, 2),
    }
