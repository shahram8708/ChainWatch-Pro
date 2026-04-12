"""OpenWeatherMap-backed weather risk service for shipment routes."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

import requests
from flask import current_app

from app.extensions import get_redis_client
from app.models.shipment import Shipment
from app.services.disruption_engine import PORT_COORDINATES, PORT_NAMES

logger = logging.getLogger(__name__)

OPENWEATHER_CURRENT_URL = "https://api.openweathermap.org/data/2.5/weather"
OPENWEATHER_ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
CACHE_TTL_SECONDS = 3600


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _get_app(app_context):
    if app_context is not None:
        return app_context
    return current_app._get_current_object()


def _risk_from_weather_condition(condition: str, weather_id: int | None, wind_speed: float, visibility_m: int | None) -> float:
    condition_norm = (condition or "").strip().lower()
    risk = 10.0

    if weather_id is not None and weather_id in {900, 901, 902, 960, 961, 962}:
        risk = 90.0
    elif condition_norm in {"clear", "clouds", "mist", "haze"}:
        risk = 8.0
    elif condition_norm in {"drizzle", "fog", "smoke"}:
        risk = 22.0
    elif condition_norm in {"rain", "snow", "squall"}:
        risk = 45.0
    elif condition_norm in {"thunderstorm"}:
        risk = 70.0
    elif condition_norm:
        risk = 18.0

    if wind_speed > 30.0:
        risk += 40.0
    elif wind_speed > 20.0:
        risk += 20.0

    if visibility_m is not None and visibility_m < 1000:
        risk += 20.0
    elif visibility_m is not None and visibility_m < 3000:
        risk += 10.0

    return max(0.0, min(100.0, risk))


def _cache_key_for_port(port_code: str) -> str:
    date_key = datetime.utcnow().strftime("%Y%m%d")
    return f"weather:{port_code}:{date_key}"


def _fetch_weather_for_coordinates(lat: float, lng: float, api_key: str, timeout: int = 10) -> dict[str, Any]:
    session = requests.Session()

    current_resp = session.get(
        OPENWEATHER_CURRENT_URL,
        params={
            "lat": lat,
            "lon": lng,
            "appid": api_key,
        },
        timeout=timeout,
    )
    current_resp.raise_for_status()
    current_data = current_resp.json()

    onecall_data = {}
    try:
        onecall_resp = session.get(
            OPENWEATHER_ONECALL_URL,
            params={
                "lat": lat,
                "lon": lng,
                "exclude": "minutely,hourly,daily",
                "appid": api_key,
            },
            timeout=timeout,
        )
        if onecall_resp.ok:
            onecall_data = onecall_resp.json()
    except Exception:
        logger.debug("One Call request failed for lat=%s lng=%s", lat, lng, exc_info=True)

    weather_list = current_data.get("weather") or []
    weather_main = weather_list[0].get("main") if weather_list else "Unknown"
    weather_id = weather_list[0].get("id") if weather_list else None
    wind_speed = _safe_float((current_data.get("wind") or {}).get("speed"), 0.0)
    visibility = current_data.get("visibility")
    rain_data = current_data.get("rain") or {}
    snow_data = current_data.get("snow") or {}

    risk_score = _risk_from_weather_condition(weather_main, weather_id, wind_speed, visibility)

    alerts = onecall_data.get("alerts") or []
    if alerts:
        risk_score = max(risk_score, 85.0)

    if weather_id in {900, 901, 902, 960, 961, 962}:
        worst_label = "Extreme maritime weather"
    elif weather_main:
        worst_label = weather_main
    else:
        worst_label = "Unknown"

    return {
        "risk_score": float(round(risk_score, 2)),
        "condition": weather_main,
        "weather_id": weather_id,
        "wind_speed": wind_speed,
        "visibility": visibility,
        "rain": rain_data,
        "snow": snow_data,
        "alerts": alerts,
        "description": f"{worst_label} at ({lat:.2f}, {lng:.2f})",
        "lat": lat,
        "lng": lng,
    }


def _load_cached_port_weather(port_code: str) -> dict[str, Any] | None:
    redis_client = get_redis_client()
    if redis_client is None:
        return None

    cache_key = _cache_key_for_port(port_code)
    try:
        cached = redis_client.get(cache_key)
        if not cached:
            return None
        return json.loads(cached)
    except Exception:
        logger.debug("Failed to read weather cache key=%s", cache_key, exc_info=True)
        return None


def _store_cached_port_weather(port_code: str, payload: dict[str, Any]) -> None:
    redis_client = get_redis_client()
    if redis_client is None:
        return

    cache_key = _cache_key_for_port(port_code)
    try:
        redis_client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(payload))
    except Exception:
        logger.debug("Failed to write weather cache key=%s", cache_key, exc_info=True)


def get_route_weather_risk(origin_port_code, destination_port_code, current_lat, current_lng, app_context):
    """Return route-level weather risk based on origin/destination/current conditions."""

    app = _get_app(app_context)
    api_key = app.config.get("OPENWEATHER_API_KEY")
    if not api_key:
        logger.error("OPENWEATHER_API_KEY is not configured")
        return {
            "risk_score": 30.0,
            "description": "Weather data unavailable — using conservative estimate",
            "queried_points": 0,
            "raw_conditions": [],
        }

    points: list[dict[str, Any]] = []

    origin_code = (origin_port_code or "").upper().strip()
    destination_code = (destination_port_code or "").upper().strip()

    origin_coords = PORT_COORDINATES.get(origin_code)
    destination_coords = PORT_COORDINATES.get(destination_code)

    if origin_coords:
        points.append(
            {
                "kind": "origin",
                "port_code": origin_code,
                "lat": origin_coords[0],
                "lng": origin_coords[1],
            }
        )

    if destination_coords:
        points.append(
            {
                "kind": "destination",
                "port_code": destination_code,
                "lat": destination_coords[0],
                "lng": destination_coords[1],
            }
        )

    if current_lat is not None and current_lng is not None:
        points.append(
            {
                "kind": "current",
                "port_code": None,
                "lat": _safe_float(current_lat),
                "lng": _safe_float(current_lng),
            }
        )

    if not points:
        return {
            "risk_score": 30.0,
            "description": "Weather data unavailable — using conservative estimate",
            "queried_points": 0,
            "raw_conditions": [],
        }

    raw_conditions: list[dict[str, Any]] = []
    try:
        for point in points:
            if point["port_code"]:
                cached = _load_cached_port_weather(point["port_code"])
                if cached:
                    raw_conditions.append(cached)
                    continue

            weather_data = _fetch_weather_for_coordinates(
                point["lat"],
                point["lng"],
                api_key,
            )
            weather_data["point_kind"] = point["kind"]
            weather_data["port_code"] = point["port_code"]
            if point["port_code"]:
                _store_cached_port_weather(point["port_code"], weather_data)
            raw_conditions.append(weather_data)
    except requests.RequestException as exc:
        logger.error("OpenWeather request failed: %s", exc, exc_info=True)
        return {
            "risk_score": 30.0,
            "description": "Weather data unavailable — using conservative estimate",
            "queried_points": 0,
            "raw_conditions": [],
        }
    except Exception as exc:
        logger.error("Unexpected weather risk failure: %s", exc, exc_info=True)
        return {
            "risk_score": 30.0,
            "description": "Weather data unavailable — using conservative estimate",
            "queried_points": 0,
            "raw_conditions": [],
        }

    route_weather_risk = 0.0
    worst_description = "Clear conditions"
    for item in raw_conditions:
        score = _safe_float(item.get("risk_score"), 0.0)
        if score >= route_weather_risk:
            route_weather_risk = score
            port_code = item.get("port_code")
            if port_code and port_code in PORT_NAMES:
                port_name = PORT_NAMES[port_code][0]
                worst_description = f"{item.get('condition', 'Unknown')} near {port_name}"
            else:
                worst_description = item.get("description") or "Route weather volatility detected"

    return {
        "risk_score": float(round(route_weather_risk, 2)),
        "description": worst_description,
        "queried_points": len(raw_conditions),
        "raw_conditions": raw_conditions,
    }


def get_weather_alert_locations(organisation_id, db_session, app_context):
    """Return deduplicated weather risk points for all active shipments in an organisation."""

    app = _get_app(app_context)

    shipments = (
        db_session.query(Shipment)
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(["pending", "in_transit", "delayed", "at_customs"]),
        )
        .all()
    )

    unique_points: dict[str, dict[str, Any]] = {}

    for shipment in shipments:
        origin_code = (shipment.origin_port_code or "").upper().strip()
        dest_code = (shipment.destination_port_code or "").upper().strip()

        if origin_code in PORT_COORDINATES:
            unique_points.setdefault(
                f"port:{origin_code}",
                {
                    "kind": "port",
                    "port_code": origin_code,
                    "lat": PORT_COORDINATES[origin_code][0],
                    "lng": PORT_COORDINATES[origin_code][1],
                },
            )

        if dest_code in PORT_COORDINATES:
            unique_points.setdefault(
                f"port:{dest_code}",
                {
                    "kind": "port",
                    "port_code": dest_code,
                    "lat": PORT_COORDINATES[dest_code][0],
                    "lng": PORT_COORDINATES[dest_code][1],
                },
            )

        if shipment.current_latitude is not None and shipment.current_longitude is not None:
            lat = round(_safe_float(shipment.current_latitude), 3)
            lng = round(_safe_float(shipment.current_longitude), 3)
            unique_points.setdefault(
                f"gps:{lat}:{lng}",
                {
                    "kind": "gps",
                    "port_code": None,
                    "lat": float(lat),
                    "lng": float(lng),
                },
            )

    locations: list[dict[str, Any]] = []
    for point in unique_points.values():
        try:
            if point["port_code"]:
                risk = get_route_weather_risk(point["port_code"], point["port_code"], None, None, app)
            else:
                risk = get_route_weather_risk(None, None, point["lat"], point["lng"], app)

            locations.append(
                {
                    "kind": point["kind"],
                    "port_code": point["port_code"],
                    "latitude": point["lat"],
                    "longitude": point["lng"],
                    "risk_score": _safe_float(risk.get("risk_score"), 30.0),
                    "description": risk.get("description") or "Weather risk",
                    "queried_points": risk.get("queried_points", 0),
                }
            )
        except Exception:
            logger.exception(
                "Failed weather alert location refresh for org_id=%s point=%s",
                organisation_id,
                point,
            )

    redis_client = get_redis_client()
    if redis_client is not None:
        cache_key = f"org:{organisation_id}:weather_alert_locations"
        try:
            redis_client.setex(cache_key, CACHE_TTL_SECONDS, json.dumps(locations))
        except Exception:
            logger.debug("Failed to cache weather alert locations org_id=%s", organisation_id, exc_info=True)

    return locations
