"""Disruption Risk Score (DRS) computation engine for shipment intelligence."""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import func

from app.extensions import get_redis_client
from app.models.carrier_performance import CarrierPerformance
from app.models.disruption_score import DisruptionScore
from app.models.shipment import Shipment

logger = logging.getLogger(__name__)


PORT_COORDINATES: dict[str, tuple[float, float]] = {
    "CNSHA": (31.2304, 121.4737),
    "CNNGB": (29.8683, 121.5440),
    "CNSZX": (22.5431, 114.0579),
    "CNSHK": (22.3193, 114.1694),
    "SGSIN": (1.2644, 103.8223),
    "MYPKG": (3.0019, 101.3913),
    "THLCH": (13.0827, 100.8830),
    "VNHPH": (20.8449, 106.6881),
    "IDJKT": (-6.2088, 106.8456),
    "PHPMN": (14.5995, 120.9842),
    "LKCMB": (6.9271, 79.8612),
    "KRPUS": (35.1028, 129.0403),
    "JPYOK": (35.4437, 139.6380),
    "JPTYO": (35.6167, 139.8000),
    "TWKHH": (22.6163, 120.3126),
    "INBOM": (18.9480, 72.8440),
    "INMAA": (13.0827, 80.2707),
    "INNSA": (18.9536, 72.9497),
    "INMUN": (22.8390, 69.7276),
    "AEDXB": (25.2708, 55.3022),
    "QADOH": (25.2854, 51.5310),
    "SADMM": (26.4282, 50.1033),
    "OMSOH": (24.3499, 56.7294),
    "NLRTM": (51.9500, 4.1400),
    "DEHAM": (53.5461, 9.9661),
    "GBFXT": (51.9630, 1.3510),
    "BEANR": (51.2637, 4.4003),
    "FRLEH": (49.4944, 0.1079),
    "ESBCN": (41.3520, 2.1589),
    "ITGOA": (44.4056, 8.9463),
    "TRIST": (41.0105, 28.9867),
    "USEWR": (40.6663, -74.0419),
    "USNYC": (40.7128, -74.0060),
    "USLAX": (33.7405, -118.2719),
    "USLGB": (33.7701, -118.1937),
    "USOAK": (37.8044, -122.2711),
    "USSEA": (47.6062, -122.3321),
    "USSAV": (32.0809, -81.0912),
    "USMIA": (25.7781, -80.1794),
    "USHOU": (29.7604, -95.3698),
    "CAVAN": (49.2827, -123.1207),
    "MXVER": (19.1738, -96.1342),
    "BRSSZ": (-23.9608, -46.3336),
    "BRRIO": (-22.9068, -43.1729),
    "CLVAP": (-33.0458, -71.6197),
    "ZADUR": (-29.8587, 31.0218),
    "EGALY": (31.2001, 29.9187),
    "AUSYD": (-33.8688, 151.2093),
    "AUMEL": (-37.8136, 144.9631),
    "NZAKL": (-36.8485, 174.7633),
}

PORT_NAMES: dict[str, tuple[str, str]] = {
    "CNSHA": ("Shanghai", "China"),
    "CNNGB": ("Ningbo", "China"),
    "CNSZX": ("Shenzhen", "China"),
    "CNSHK": ("Hong Kong", "China"),
    "SGSIN": ("Singapore", "Singapore"),
    "MYPKG": ("Port Klang", "Malaysia"),
    "THLCH": ("Laem Chabang", "Thailand"),
    "VNHPH": ("Hai Phong", "Vietnam"),
    "IDJKT": ("Jakarta", "Indonesia"),
    "PHPMN": ("Manila", "Philippines"),
    "LKCMB": ("Colombo", "Sri Lanka"),
    "KRPUS": ("Busan", "South Korea"),
    "JPYOK": ("Yokohama", "Japan"),
    "JPTYO": ("Tokyo", "Japan"),
    "TWKHH": ("Kaohsiung", "Taiwan"),
    "INBOM": ("Mumbai", "India"),
    "INMAA": ("Chennai", "India"),
    "INNSA": ("Nhava Sheva", "India"),
    "INMUN": ("Mundra", "India"),
    "AEDXB": ("Dubai", "UAE"),
    "QADOH": ("Doha", "Qatar"),
    "SADMM": ("Dammam", "Saudi Arabia"),
    "OMSOH": ("Sohar", "Oman"),
    "NLRTM": ("Rotterdam", "Netherlands"),
    "DEHAM": ("Hamburg", "Germany"),
    "GBFXT": ("Felixstowe", "United Kingdom"),
    "BEANR": ("Antwerp", "Belgium"),
    "FRLEH": ("Le Havre", "France"),
    "ESBCN": ("Barcelona", "Spain"),
    "ITGOA": ("Genoa", "Italy"),
    "TRIST": ("Istanbul", "Turkey"),
    "USNYC": ("New York", "United States"),
    "USEWR": ("Newark", "United States"),
    "USLAX": ("Los Angeles", "United States"),
    "USLGB": ("Long Beach", "United States"),
    "USOAK": ("Oakland", "United States"),
    "USSEA": ("Seattle", "United States"),
    "USSAV": ("Savannah", "United States"),
    "USMIA": ("Miami", "United States"),
    "USHOU": ("Houston", "United States"),
    "CAVAN": ("Vancouver", "Canada"),
    "MXVER": ("Veracruz", "Mexico"),
    "BRSSZ": ("Santos", "Brazil"),
    "BRRIO": ("Rio de Janeiro", "Brazil"),
    "CLVAP": ("Valparaiso", "Chile"),
    "ZADUR": ("Durban", "South Africa"),
    "EGALY": ("Alexandria", "Egypt"),
    "AUSYD": ("Sydney", "Australia"),
    "AUMEL": ("Melbourne", "Australia"),
    "NZAKL": ("Auckland", "New Zealand"),
}

PORT_CODE_TO_REGION: dict[str, str] = {
    "CNSHA": "East Asia",
    "CNNGB": "East Asia",
    "CNSZX": "East Asia",
    "CNSHK": "East Asia",
    "KRPUS": "East Asia",
    "JPYOK": "East Asia",
    "JPTYO": "East Asia",
    "TWKHH": "East Asia",
    "SGSIN": "Southeast Asia",
    "MYPKG": "Southeast Asia",
    "THLCH": "Southeast Asia",
    "VNHPH": "Southeast Asia",
    "IDJKT": "Southeast Asia",
    "PHPMN": "Southeast Asia",
    "LKCMB": "Southeast Asia",
    "INBOM": "South Asia",
    "INMAA": "South Asia",
    "INNSA": "South Asia",
    "INMUN": "South Asia",
    "AEDXB": "Middle East",
    "QADOH": "Middle East",
    "SADMM": "Middle East",
    "OMSOH": "Middle East",
    "NLRTM": "Europe North",
    "DEHAM": "Europe North",
    "GBFXT": "Europe North",
    "BEANR": "Europe North",
    "FRLEH": "Europe North",
    "ESBCN": "Europe South",
    "ITGOA": "Europe South",
    "TRIST": "Europe South",
    "USNYC": "North America East Coast",
    "USEWR": "North America East Coast",
    "USSAV": "North America East Coast",
    "USMIA": "North America East Coast",
    "USHOU": "North America East Coast",
    "USLAX": "North America West Coast",
    "USLGB": "North America West Coast",
    "USOAK": "North America West Coast",
    "USSEA": "North America West Coast",
    "CAVAN": "North America West Coast",
    "MXVER": "Latin America",
    "BRSSZ": "Latin America",
    "BRRIO": "Latin America",
    "CLVAP": "Latin America",
    "ZADUR": "Africa",
    "EGALY": "Africa",
    "AUSYD": "Australia East Coast",
    "AUMEL": "Australia East Coast",
    "NZAKL": "Australia East Coast",
}

DEFAULT_DISTANCE_KM = 6500.0
DRS_SUBSCORE_CACHE_TTL_SECONDS = 900


def _pair_key(origin: str, destination: str) -> tuple[str, str]:
    return tuple(sorted([origin.upper().strip(), destination.upper().strip()]))


PORT_PAIR_DISTANCE_KM: dict[tuple[str, str], float] = {
    _pair_key("CNSHA", "USLAX"): 10460,
    _pair_key("CNNGB", "USLAX"): 10280,
    _pair_key("CNSZX", "USLAX"): 11600,
    _pair_key("CNSHA", "USNYC"): 11850,
    _pair_key("CNSHA", "NLRTM"): 19350,
    _pair_key("CNSHA", "DEHAM"): 19600,
    _pair_key("CNSHA", "SGSIN"): 3800,
    _pair_key("CNNGB", "SGSIN"): 4050,
    _pair_key("SGSIN", "NLRTM"): 15400,
    _pair_key("SGSIN", "DEHAM"): 15700,
    _pair_key("SGSIN", "USLAX"): 14100,
    _pair_key("SGSIN", "USNYC"): 15700,
    _pair_key("INBOM", "NLRTM"): 8850,
    _pair_key("INMAA", "NLRTM"): 8200,
    _pair_key("INBOM", "AEDXB"): 1940,
    _pair_key("INMAA", "AEDXB"): 2950,
    _pair_key("INBOM", "USLAX"): 15100,
    _pair_key("INBOM", "USNYC"): 12600,
    _pair_key("AEDXB", "NLRTM"): 5150,
    _pair_key("AEDXB", "USNYC"): 11000,
    _pair_key("KRPUS", "USLAX"): 9500,
    _pair_key("JPYOK", "USLAX"): 8800,
    _pair_key("JPYOK", "USNYC"): 10800,
    _pair_key("KRPUS", "NLRTM"): 20500,
    _pair_key("MYPKG", "DEHAM"): 16000,
    _pair_key("THLCH", "USLAX"): 13100,
    _pair_key("VNHPH", "USLAX"): 12400,
    _pair_key("NLRTM", "USNYC"): 5900,
    _pair_key("DEHAM", "USNYC"): 6200,
    _pair_key("GBFXT", "USNYC"): 5600,
    _pair_key("NLRTM", "USLAX"): 14100,
    _pair_key("DEHAM", "USLAX"): 15000,
    _pair_key("USNYC", "BRSSZ"): 7700,
    _pair_key("USLAX", "BRSSZ"): 9900,
    _pair_key("SGSIN", "AUSYD"): 6300,
    _pair_key("SGSIN", "AUMEL"): 6000,
    _pair_key("SGSIN", "NZAKL"): 8400,
    _pair_key("CNSHA", "AUSYD"): 7800,
    _pair_key("CNSHA", "AUMEL"): 8100,
    _pair_key("AEDXB", "INNSA"): 2100,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _mode_to_performance_mode(mode: str | None) -> str:
    mode_map = {
        "ocean_fcl": "ocean",
        "ocean_lcl": "ocean",
        "air": "air",
        "road": "road",
        "rail": "rail",
        "multimodal": "multimodal",
    }
    return mode_map.get((mode or "").strip().lower(), "multimodal")


def _port_code_to_region(port_code: str | None) -> str:
    code = (port_code or "").strip().upper()
    if not code:
        return "Global"

    if code in PORT_CODE_TO_REGION:
        return PORT_CODE_TO_REGION[code]

    prefix = code[:2]
    prefix_map = {
        "CN": "East Asia",
        "JP": "East Asia",
        "KR": "East Asia",
        "TW": "East Asia",
        "SG": "Southeast Asia",
        "MY": "Southeast Asia",
        "TH": "Southeast Asia",
        "VN": "Southeast Asia",
        "ID": "Southeast Asia",
        "PH": "Southeast Asia",
        "LK": "Southeast Asia",
        "IN": "South Asia",
        "AE": "Middle East",
        "QA": "Middle East",
        "SA": "Middle East",
        "OM": "Middle East",
        "NL": "Europe North",
        "DE": "Europe North",
        "GB": "Europe North",
        "BE": "Europe North",
        "FR": "Europe North",
        "ES": "Europe South",
        "IT": "Europe South",
        "TR": "Europe South",
        "US": "North America",
        "CA": "North America",
        "MX": "Latin America",
        "BR": "Latin America",
        "CL": "Latin America",
        "ZA": "Africa",
        "EG": "Africa",
        "AU": "Australia East Coast",
        "NZ": "Australia East Coast",
    }
    return prefix_map.get(prefix, "Global")


def _haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius_km = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lmb = math.radians(lng2 - lng1)

    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lmb / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radius_km * c


def _estimate_route_distance_km(origin_port_code: str, destination_port_code: str) -> float:
    key = _pair_key(origin_port_code, destination_port_code)
    if key in PORT_PAIR_DISTANCE_KM:
        return float(PORT_PAIR_DISTANCE_KM[key])

    origin_coords = PORT_COORDINATES.get((origin_port_code or "").upper().strip())
    destination_coords = PORT_COORDINATES.get((destination_port_code or "").upper().strip())

    if origin_coords and destination_coords:
        return _haversine_km(
            origin_coords[0],
            origin_coords[1],
            destination_coords[0],
            destination_coords[1],
        )

    return DEFAULT_DISTANCE_KM


def _cache_drs_sub_score(shipment: Shipment, sub_score_name: str, value: float) -> None:
    redis_client = get_redis_client()
    if redis_client is None:
        return

    key = f"drs:shipment:{shipment.id}:{sub_score_name}"
    payload = {
        "value": round(float(value), 4),
        "computed_at": datetime.utcnow().isoformat(),
    }
    try:
        redis_client.setex(key, DRS_SUBSCORE_CACHE_TTL_SECONDS, json.dumps(payload))
    except Exception:
        logger.exception("Failed to cache DRS sub-score key=%s", key)


def classify_drs(drs_total: float) -> dict[str, Any]:
    """Return DRS level classification metadata for UI and alerts."""

    score = max(0.0, min(100.0, _safe_float(drs_total, 0.0)))

    if score >= 81:
        return {
            "level": "critical",
            "label": "Critical",
            "color": "#D32F2F",
            "requires_alert": True,
            "alert_severity": "critical",
        }

    if score >= 61:
        return {
            "level": "warning",
            "label": "Warning",
            "color": "#FF8C00",
            "requires_alert": True,
            "alert_severity": "warning",
        }

    if score >= 31:
        return {
            "level": "watch",
            "label": "Watch",
            "color": "#F59E0B",
            "requires_alert": True,
            "alert_severity": "watch",
        }

    return {
        "level": "green",
        "label": "On Track",
        "color": "#00A86B",
        "requires_alert": False,
        "alert_severity": None,
    }


def compute_tvs(shipment: Shipment) -> float:
    """Compute Transit Velocity Score (TVS) from expected vs actual progress."""

    if shipment.status == "delivered" or shipment.actual_arrival is not None:
        return 100.0

    origin_code = (shipment.origin_port_code or "").upper().strip()
    destination_code = (shipment.destination_port_code or "").upper().strip()

    total_route_km = _estimate_route_distance_km(origin_code, destination_code)

    departure_time = shipment.actual_departure or shipment.estimated_departure
    if departure_time is None:
        return 50.0

    if shipment.estimated_departure and shipment.estimated_departure > datetime.utcnow():
        return 50.0

    total_transit_seconds = (
        (shipment.estimated_arrival - shipment.estimated_departure).total_seconds()
        if shipment.estimated_arrival and shipment.estimated_departure
        else 0
    )
    total_transit_hours = max(total_transit_seconds / 3600.0, 0.0)
    if total_transit_hours <= 0:
        return 50.0

    hours_elapsed = max((datetime.utcnow() - departure_time).total_seconds() / 3600.0, 0.0)
    expected_km_by_now = total_route_km * (hours_elapsed / total_transit_hours)
    expected_km_by_now = max(0.0, min(total_route_km, expected_km_by_now))

    if expected_km_by_now <= 0:
        return 50.0

    if shipment.current_latitude is None or shipment.current_longitude is None:
        return 50.0

    origin_coords = PORT_COORDINATES.get(origin_code)
    if not origin_coords:
        return 50.0

    actual_km_covered = _haversine_km(
        origin_coords[0],
        origin_coords[1],
        _safe_float(shipment.current_latitude),
        _safe_float(shipment.current_longitude),
    )

    tvs_raw = actual_km_covered / expected_km_by_now
    tvs = min(100.0, max(0.0, tvs_raw * 100.0))
    return float(tvs)


def compute_mcs(shipment: Shipment) -> float:
    """Compute Milestone Compliance Score (MCS) based on schedule adherence."""

    now = datetime.utcnow()

    if not shipment.estimated_departure or not shipment.estimated_arrival:
        return 50.0

    total_transit_hours = max(
        1.0,
        (shipment.estimated_arrival - shipment.estimated_departure).total_seconds() / 3600.0,
    )

    milestones: list[dict[str, Any]] = [
        {
            "name": "departure",
            "planned": shipment.estimated_departure,
            "actual": shipment.actual_departure,
        }
    ]

    midpoint_planned = shipment.estimated_departure + (
        shipment.estimated_arrival - shipment.estimated_departure
    ) / 2
    midpoint_actual = None

    if shipment.current_latitude is not None and shipment.current_longitude is not None:
        route_km = _estimate_route_distance_km(shipment.origin_port_code, shipment.destination_port_code)
        origin_coords = PORT_COORDINATES.get((shipment.origin_port_code or "").upper().strip())
        if origin_coords and route_km > 0:
            current_km = _haversine_km(
                origin_coords[0],
                origin_coords[1],
                _safe_float(shipment.current_latitude),
                _safe_float(shipment.current_longitude),
            )
            progress_ratio = max(0.0, min(1.0, current_km / route_km))
            if progress_ratio >= 0.5:
                midpoint_actual = shipment.updated_at or now

    milestones.append(
        {
            "name": "midpoint",
            "planned": midpoint_planned,
            "actual": midpoint_actual,
        }
    )

    milestones.append(
        {
            "name": "arrival",
            "planned": shipment.estimated_arrival,
            "actual": shipment.actual_arrival,
        }
    )

    if shipment.status == "at_customs" and shipment.updated_at:
        customs_planned = shipment.updated_at + timedelta(hours=48)
        milestones.append(
            {
                "name": "customs",
                "planned": customs_planned,
                "actual": None,
            }
        )

    milestone_count = max(len(milestones), 1)
    allowed_delay_window = max(total_transit_hours / milestone_count, 1.0)

    contributions: list[float] = []
    for milestone in milestones:
        planned = milestone.get("planned")
        actual = milestone.get("actual")

        if not planned:
            contributions.append(100.0)
            continue

        if actual is not None:
            if actual <= planned:
                contributions.append(100.0)
                continue

            delay_hours = max((actual - planned).total_seconds() / 3600.0, 0.0)
            contribution = max(0.0, 100.0 - ((delay_hours / allowed_delay_window) * 100.0))
            contributions.append(contribution)
            continue

        if planned > now:
            contributions.append(100.0)
            continue

        hours_overdue = max((now - planned).total_seconds() / 3600.0, 0.0)
        contribution = max(0.0, 50.0 - (hours_overdue * 5.0))
        contributions.append(contribution)

    mcs = mean(contributions) if contributions else 50.0
    return float(max(0.0, min(100.0, mcs)))


def compute_ehs(shipment: Shipment, app_context) -> tuple[float, dict[str, Any]]:
    """Compute External Hazard Score (EHS) using weather, port, customs, and event signals."""

    from app.services.external_data import (
        news_monitor_service,
        port_data_service,
        weather_service,
    )

    weather_score = 50.0
    weather_description = "Weather feed unavailable"
    weather_raw = {}

    try:
        weather_raw = weather_service.get_route_weather_risk(
            shipment.origin_port_code,
            shipment.destination_port_code,
            _safe_float(shipment.current_latitude, None),
            _safe_float(shipment.current_longitude, None),
            app_context,
        )
        weather_risk = _safe_float(weather_raw.get("risk_score"), 50.0)
        if weather_risk <= 10:
            weather_score = 0.0
        elif weather_risk <= 50:
            weather_score = 30.0
        elif weather_risk <= 80:
            weather_score = 70.0
        else:
            weather_score = 100.0
        weather_description = weather_raw.get("description") or weather_description
    except Exception:
        logger.exception("Weather hazard computation failed for shipment_id=%s", shipment.id)
        weather_score = 50.0

    port_congestion_score = 50.0
    port_congestion_description = "Port congestion feed unavailable"
    try:
        port_congestion_score = _safe_float(
            port_data_service.get_port_congestion_score(
                shipment.destination_port_code,
                app_context,
                organisation_id=shipment.organisation_id,
            ),
            50.0,
        )
        port_congestion_description = (
            f"Destination port congestion score {round(port_congestion_score, 1)}"
        )
    except Exception:
        logger.exception("Port congestion fetch failed for shipment_id=%s", shipment.id)
        port_congestion_score = 50.0

    customs_score = 50.0
    customs_description = "Customs risk feed unavailable"
    try:
        customs_score = _safe_float(
            port_data_service.get_customs_risk_score(
                shipment.destination_port_code,
                shipment.mode,
                app_context,
            ),
            50.0,
        )
        customs_description = f"Customs risk score {round(customs_score, 1)}"
    except Exception:
        logger.exception("Customs risk fetch failed for shipment_id=%s", shipment.id)
        customs_score = 50.0

    event_score = 50.0
    event_description = "Event monitoring unavailable"
    event_raw: dict[str, Any] = {}
    try:
        event_raw = news_monitor_service.get_route_event_risk(
            shipment.origin_port_code,
            shipment.destination_port_code,
            app_context,
            organisation_id=shipment.organisation_id,
        )
        event_score = _safe_float(event_raw.get("event_score"), 50.0)
        event_description = event_raw.get("event_description") or event_description
    except Exception:
        logger.exception("News event risk fetch failed for shipment_id=%s", shipment.id)
        event_score = 50.0

    ehs_score = max(weather_score, port_congestion_score, customs_score, event_score)

    ehs_signals = {
        "weather_score": float(max(0.0, min(100.0, weather_score))),
        "weather_description": weather_description,
        "weather_raw": weather_raw,
        "port_congestion_score": float(max(0.0, min(100.0, port_congestion_score))),
        "port_congestion_description": port_congestion_description,
        "customs_score": float(max(0.0, min(100.0, customs_score))),
        "customs_description": customs_description,
        "event_score": float(max(0.0, min(100.0, event_score))),
        "event_description": event_description,
        "event_raw": event_raw,
        "computed_at": datetime.utcnow().isoformat(),
    }

    return float(max(0.0, min(100.0, ehs_score))), ehs_signals


def compute_crs(shipment: Shipment, db_session) -> float:
    """Compute Carrier Reliability Score (CRS) for the shipment trade lane."""

    if shipment.carrier_id is None:
        return 50.0

    origin_region = _port_code_to_region(shipment.origin_port_code)
    destination_region = _port_code_to_region(shipment.destination_port_code)
    perf_mode = _mode_to_performance_mode(shipment.mode)

    records = (
        db_session.query(CarrierPerformance)
        .filter(
            CarrierPerformance.carrier_id == shipment.carrier_id,
            CarrierPerformance.origin_region == origin_region,
            CarrierPerformance.destination_region == destination_region,
            CarrierPerformance.mode == perf_mode,
            CarrierPerformance.organisation_id.in_([shipment.organisation_id, None]),
        )
        .order_by(
            CarrierPerformance.period_year.desc(),
            CarrierPerformance.period_month.desc(),
            CarrierPerformance.organisation_id.is_(None).asc(),
        )
        .limit(3)
        .all()
    )

    if not records:
        return 50.0

    weights = [0.5, 0.3, 0.2]
    weighted_sum = 0.0
    total_weight = 0.0

    for idx, record in enumerate(records[:3]):
        weight = weights[idx]
        otd_ratio = _safe_float(record.otd_rate, 0.0)
        weighted_sum += (otd_ratio * 100.0) * weight
        total_weight += weight

    if total_weight <= 0:
        return 50.0

    crs = weighted_sum / total_weight
    return float(max(0.0, min(100.0, crs)))


def _is_ocean_mid_voyage(shipment: Shipment) -> bool:
    mode = (shipment.mode or "").lower()
    if mode not in {"ocean_fcl", "ocean_lcl", "multimodal"}:
        return False

    location_name = (shipment.current_location_name or "").lower()
    if any(token in location_name for token in ["at sea", "mid ocean", "open ocean", "sea lane"]):
        return True

    if shipment.current_latitude is not None and shipment.current_longitude is not None and not location_name:
        return True

    return False


def _expected_max_dwell_hours(shipment: Shipment) -> float:
    if _is_ocean_mid_voyage(shipment):
        return 0.0

    location_name = (shipment.current_location_name or "").strip().lower()
    location_code = (shipment.current_location_name or "").strip().upper()

    if "customs" in location_name or "clearance" in location_name:
        return 96.0

    if (
        "port" in location_name
        or "terminal" in location_name
        or "berth" in location_name
        or "wharf" in location_name
        or location_code in PORT_COORDINATES
    ):
        return 72.0

    if "warehouse" in location_name or "hub" in location_name or "distribution" in location_name:
        return 24.0

    return 48.0


def compute_dtas(shipment: Shipment) -> float:
    """Compute Dwell Time Anomaly Score (DTAS) for stationarity risk."""

    expected_max_dwell = _expected_max_dwell_hours(shipment)
    if expected_max_dwell <= 0:
        return 0.0

    now = datetime.utcnow()
    if not shipment.updated_at:
        return 0.0

    hours_since_update = max((now - shipment.updated_at).total_seconds() / 3600.0, 0.0)
    if hours_since_update <= 2.0:
        return 0.0

    stationary_hours = 0.0
    redis_client = get_redis_client()
    cache_key = f"drs:shipment:{shipment.id}:last_position"

    current_payload = {
        "lat": _safe_float(shipment.current_latitude, None),
        "lng": _safe_float(shipment.current_longitude, None),
        "updated_at": shipment.updated_at.isoformat() if shipment.updated_at else None,
    }

    unchanged = False
    if redis_client is not None:
        try:
            cached_raw = redis_client.get(cache_key)
            if cached_raw:
                cached = json.loads(cached_raw)
                cached_lat = _safe_float(cached.get("lat"), None)
                cached_lng = _safe_float(cached.get("lng"), None)
                unchanged = (
                    cached_lat is not None
                    and cached_lng is not None
                    and current_payload["lat"] is not None
                    and current_payload["lng"] is not None
                    and abs(cached_lat - current_payload["lat"]) < 0.00001
                    and abs(cached_lng - current_payload["lng"]) < 0.00001
                )
            redis_client.setex(cache_key, 24 * 3600, json.dumps(current_payload))
        except Exception:
            logger.exception("Failed to evaluate DTAS stationarity cache for shipment_id=%s", shipment.id)

    if unchanged:
        stationary_hours = hours_since_update

    dtas = min(100.0, (stationary_hours / expected_max_dwell) * 100.0)
    return float(max(0.0, dtas))


def compute_cps(shipment: Shipment, db_session) -> float:
    """Compute Cascade Propagation Score (CPS) from cargo criticality and SLA pressure."""

    cargo_component = 0.0

    max_cargo_value = (
        db_session.query(func.max(Shipment.cargo_value_inr))
        .filter(
            Shipment.organisation_id == shipment.organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
        )
        .scalar()
    )

    max_cargo_value_float = _safe_float(max_cargo_value, 0.0)
    this_cargo = _safe_float(shipment.cargo_value_inr, 0.0)

    if this_cargo > 0 and max_cargo_value_float > 0:
        cargo_component = min(40.0, (this_cargo / max_cargo_value_float) * 40.0)

    if shipment.estimated_arrival is None:
        sla_component = 30.0
    else:
        days_remaining = (shipment.estimated_arrival - datetime.utcnow()).days
        if days_remaining <= 0:
            sla_tightness = 1.0
        else:
            sla_tightness = max(0.0, 1.0 - (days_remaining / 3.0))
        sla_tightness = min(1.0, sla_tightness)
        sla_component = sla_tightness * 60.0

    cps = cargo_component + sla_component
    return float(max(0.0, min(100.0, cps)))


def compute_drs(shipment: Shipment, db_session, app_context) -> dict[str, Any]:
    """Compute all DRS sub-scores and persist a disruption score snapshot."""

    try:
        previous_score_row = (
            db_session.query(DisruptionScore)
            .filter(DisruptionScore.shipment_id == shipment.id)
            .order_by(DisruptionScore.computed_at.desc())
            .first()
        )
        previous_drs_total = _safe_float(getattr(previous_score_row, "drs_total", None), None)

        tvs = compute_tvs(shipment)
        _cache_drs_sub_score(shipment, "tvs", tvs)

        mcs = compute_mcs(shipment)
        _cache_drs_sub_score(shipment, "mcs", mcs)

        ehs, ehs_signals = compute_ehs(shipment, app_context)
        _cache_drs_sub_score(shipment, "ehs", ehs)

        crs = compute_crs(shipment, db_session)
        _cache_drs_sub_score(shipment, "crs", crs)

        dtas = compute_dtas(shipment)
        _cache_drs_sub_score(shipment, "dtas", dtas)

        cps = compute_cps(shipment, db_session)
        _cache_drs_sub_score(shipment, "cps", cps)

        tvs_inverted = 100.0 - tvs
        mcs_inverted = 100.0 - mcs
        crs_inverted = 100.0 - crs

        drs = (
            (tvs_inverted * 0.25)
            + (mcs_inverted * 0.25)
            + (ehs * 0.20)
            + (crs_inverted * 0.15)
            + (dtas * 0.10)
            + (cps * 0.05)
        )
        drs = max(0.0, min(100.0, drs))

        ehs_signals["crs_score"] = float(round(crs, 2))

        score_row = DisruptionScore(
            shipment_id=shipment.id,
            computed_at=datetime.utcnow(),
            drs_total=round(drs, 2),
            tvs=round(tvs, 2),
            mcs=round(mcs, 2),
            ehs=round(ehs, 2),
            crs=round(crs, 2),
            dtas=round(dtas, 2),
            cps=round(cps, 2),
            ehs_signals=ehs_signals,
        )

        db_session.add(score_row)
        db_session.commit()

        shipment.disruption_risk_score = round(drs, 2)
        db_session.commit()

        if (
            previous_drs_total is not None
            and abs(float(round(drs, 2)) - float(previous_drs_total)) > 10.0
        ):
            from app.services import ai_service

            ai_service.invalidate_ai_content(
                organisation_id=shipment.organisation_id,
                content_type="shipment_disruption_summary",
                content_key=f"shipment_{shipment.id}",
                db_session=db_session,
            )

        result = {
            "shipment_id": str(shipment.id),
            "tvs": float(round(tvs, 2)),
            "mcs": float(round(mcs, 2)),
            "ehs": float(round(ehs, 2)),
            "crs": float(round(crs, 2)),
            "dtas": float(round(dtas, 2)),
            "cps": float(round(cps, 2)),
            "drs_total": float(round(drs, 2)),
            "classification": classify_drs(drs),
            "ehs_signals": ehs_signals,
        }
        _cache_drs_sub_score(shipment, "drs_total", drs)
        return result

    except Exception:
        logger.error("DRS computation failed for shipment_id=%s", shipment.id, exc_info=True)
        db_session.rollback()
        fallback_signals = {
            "weather_score": 50.0,
            "weather_description": "Fallback: weather signal unavailable",
            "port_congestion_score": 50.0,
            "port_congestion_description": "Fallback: congestion signal unavailable",
            "customs_score": 50.0,
            "customs_description": "Fallback: customs signal unavailable",
            "event_score": 50.0,
            "event_description": "Fallback: route event signal unavailable",
            "crs_score": 50.0,
            "computed_at": datetime.utcnow().isoformat(),
        }
        return {
            "shipment_id": str(shipment.id),
            "tvs": 50.0,
            "mcs": 50.0,
            "ehs": 50.0,
            "crs": 50.0,
            "dtas": 50.0,
            "cps": 50.0,
            "drs_total": 50.0,
            "classification": classify_drs(50.0),
            "ehs_signals": fallback_signals,
        }


__all__ = [
    "PORT_COORDINATES",
    "PORT_NAMES",
    "PORT_CODE_TO_REGION",
    "classify_drs",
    "compute_drs",
    "compute_tvs",
    "compute_mcs",
    "compute_ehs",
    "compute_crs",
    "compute_dtas",
    "compute_cps",
    "_port_code_to_region",
    "_mode_to_performance_mode",
]
