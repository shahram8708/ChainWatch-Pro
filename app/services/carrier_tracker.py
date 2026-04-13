"""Carrier tracking ingestion, analytics, and AI commentary services."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Any
from urllib.parse import quote

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy import and_, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.organisation import Organisation
from app.models.shipment import Shipment
from app.services import ai_service
from app.services.disruption_engine import PORT_COORDINATES, _mode_to_performance_mode, _port_code_to_region

logger = logging.getLogger(__name__)

SUPPORTED_API_TYPES = {"MANUAL", "REST", "SOAP", "EDI"}
ACTIVE_SHIPMENT_STATUSES = {"pending", "in_transit", "delayed", "at_customs"}
ANALYTICS_SHIPMENT_STATUSES = {"pending", "in_transit", "delayed", "at_customs", "delivered"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _build_carrier_performance_upsert_stmt(db_session, payload: dict[str, Any]):
    conflict_columns = [
        "carrier_id",
        "organisation_id",
        "origin_region",
        "destination_region",
        "mode",
        "period_year",
        "period_month",
    ]

    update_values = {
        "total_shipments": payload["total_shipments"],
        "on_time_count": payload["on_time_count"],
        "otd_rate": payload["otd_rate"],
        "avg_delay_hours": payload["avg_delay_hours"],
        "reliability_score": payload["reliability_score"],
    }

    bind = db_session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        statement = pg_insert(CarrierPerformance).values(**payload)
    else:
        statement = sqlite_insert(CarrierPerformance).values(**payload)

    return statement.on_conflict_do_update(
        index_elements=conflict_columns,
        set_=update_values,
    )


def _get_app(app_context):
    if app_context is not None:
        return app_context
    return current_app._get_current_object()


def _period_cutoff_value(months: int) -> int:
    months = max(int(months or 1), 1)
    now = datetime.utcnow()
    year = now.year
    month = now.month - (months - 1)

    while month <= 0:
        month += 12
        year -= 1

    return (year * 100) + month


def _shift_period_key(period_key: int, months_back: int) -> int:
    """Shift a YYYYMM period key backwards by `months_back` months."""

    year = int(period_key) // 100
    month = int(period_key) % 100
    shift = max(int(months_back or 0), 0)

    month -= shift
    while month <= 0:
        month += 12
        year -= 1

    return (year * 100) + month


def _parse_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(tz=None).replace(tzinfo=None)
        return parsed
    except ValueError:
        pass

    for fmt in [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    return None


def _great_circle_interpolate(start: tuple[float, float], end: tuple[float, float], fraction: float) -> tuple[float, float]:
    """Interpolate geodesic position between two lat/lng points."""

    import math

    lat1, lon1 = map(math.radians, start)
    lat2, lon2 = map(math.radians, end)

    delta = 2 * math.asin(
        math.sqrt(
            math.sin((lat2 - lat1) / 2) ** 2
            + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
        )
    )

    if abs(delta) < 1e-12:
        return start

    a = math.sin((1 - fraction) * delta) / math.sin(delta)
    b = math.sin(fraction * delta) / math.sin(delta)

    x = a * math.cos(lat1) * math.cos(lon1) + b * math.cos(lat2) * math.cos(lon2)
    y = a * math.cos(lat1) * math.sin(lon1) + b * math.cos(lat2) * math.sin(lon2)
    z = a * math.sin(lat1) + b * math.sin(lat2)

    lat = math.atan2(z, math.sqrt(x * x + y * y))
    lon = math.atan2(y, x)

    return (math.degrees(lat), math.degrees(lon))


def _simulate_shipment_position(shipment: Shipment, now: datetime) -> tuple[float | None, float | None, str]:
    origin_code = (shipment.origin_port_code or "").upper().strip()
    destination_code = (shipment.destination_port_code or "").upper().strip()

    origin_coords = PORT_COORDINATES.get(origin_code)
    destination_coords = PORT_COORDINATES.get(destination_code)
    if not origin_coords or not destination_coords:
        return None, None, "Position unavailable"

    if not shipment.estimated_departure or not shipment.estimated_arrival:
        return origin_coords[0], origin_coords[1], f"Near {origin_code}"

    start_time = shipment.actual_departure or shipment.estimated_departure
    total_seconds = max((shipment.estimated_arrival - shipment.estimated_departure).total_seconds(), 1.0)
    elapsed_seconds = max((now - start_time).total_seconds(), 0.0)

    fraction = max(0.0, min(1.0, elapsed_seconds / total_seconds))
    lat, lng = _great_circle_interpolate(origin_coords, destination_coords, fraction)

    if fraction <= 0.02:
        location_name = f"Departing {origin_code}"
    elif fraction >= 0.98:
        location_name = f"Approaching {destination_code}"
    else:
        location_name = f"In transit ({int(fraction * 100)}% route completion)"

    return lat, lng, location_name


def _get_org_profile(organisation: Organisation) -> dict[str, Any]:
    data = organisation.org_profile_data or {}
    if isinstance(data, dict):
        return data
    return {}


def _extract_carrier_credentials(organisation: Organisation, carrier: Carrier) -> dict[str, Any] | None:
    profile = _get_org_profile(organisation)

    candidate_maps = [
        profile.get("carrier_api_credentials"),
        profile.get("carrier_integrations"),
        (profile.get("integrations") or {}).get("carrier_credentials"),
        (profile.get("integrations") or {}).get("carriers"),
    ]

    lookup_keys = [
        str(carrier.id),
        (carrier.scac_code or "").upper(),
        (carrier.name or "").strip(),
        (carrier.name or "").strip().lower(),
    ]

    for candidate in candidate_maps:
        if not isinstance(candidate, dict):
            continue

        for key in lookup_keys:
            if not key:
                continue
            value = candidate.get(key)
            if isinstance(value, dict):
                return value

    return None


def _decrypt_credentials(encrypted_payload: dict[str, Any], encryption_key: str) -> dict[str, Any]:
    fernet = Fernet(encryption_key.encode("utf-8"))
    decrypted: dict[str, Any] = {}

    for key, value in encrypted_payload.items():
        if not isinstance(value, str):
            decrypted[key] = value
            continue

        try:
            decrypted[key] = fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            decrypted[key] = value

    return decrypted


def _extract_position_from_payload(payload: Any) -> tuple[float, float, str | None] | None:
    candidate_items: list[Any] = [payload]

    if isinstance(payload, dict):
        for key in ["data", "position", "location", "tracking", "result"]:
            value = payload.get(key)
            if isinstance(value, list) and value:
                candidate_items.extend(value)
            elif value is not None:
                candidate_items.append(value)
    elif isinstance(payload, list):
        candidate_items.extend(payload)

    for item in candidate_items:
        if isinstance(item, list) and item:
            item = item[0]
        if not isinstance(item, dict):
            continue

        lat = _safe_float(item.get("lat", item.get("latitude")), default=None)
        lng = _safe_float(item.get("lng", item.get("longitude")), default=None)
        if lat is None or lng is None:
            continue

        location_name = item.get("location_name") or item.get("location") or item.get("city")
        return float(lat), float(lng), (str(location_name).strip() if location_name else None)

    return None


def _rest_tracking_position(credentials: dict[str, Any], shipment: Shipment) -> tuple[float, float, str | None] | None:
    endpoint = (
        credentials.get("tracking_endpoint")
        or credentials.get("endpoint")
        or credentials.get("url")
        or credentials.get("base_url")
    )
    if not endpoint:
        return None

    shipment_ref = shipment.external_reference or str(shipment.id)
    endpoint = str(endpoint).strip()

    if "{" in endpoint and "}" in endpoint:
        request_url = endpoint.format(
            tracking_ref=quote(shipment_ref, safe=""),
            shipment_id=quote(str(shipment.id), safe=""),
            external_reference=quote(shipment_ref, safe=""),
        )
        params = None
    else:
        request_url = endpoint.rstrip("/")
        params = {
            "tracking_ref": shipment_ref,
            "shipment_id": str(shipment.id),
            "external_reference": shipment_ref,
        }

    headers = {
        "Accept": "application/json",
    }

    api_key = credentials.get("api_key") or credentials.get("token") or credentials.get("access_token")
    if api_key:
        auth_header = credentials.get("auth_header") or "Authorization"
        if auth_header.lower() == "authorization":
            headers[auth_header] = f"Bearer {api_key}"
        else:
            headers[auth_header] = str(api_key)

    if isinstance(credentials.get("headers"), dict):
        for key, value in credentials["headers"].items():
            if key and value is not None:
                headers[str(key)] = str(value)

    timeout = int(_safe_float(credentials.get("timeout_seconds"), 8))
    response = requests.get(request_url, params=params, headers=headers, timeout=max(timeout, 3))
    response.raise_for_status()

    payload = response.json()
    return _extract_position_from_payload(payload)


def poll_carrier_for_updates(carrier, organisation, db_session, app_context):
    """Poll one carrier and update in-progress shipment positions."""

    app = _get_app(app_context)
    now = datetime.utcnow()

    shipments = (
        db_session.query(Shipment)
        .filter(
            Shipment.organisation_id == organisation.id,
            Shipment.carrier_id == carrier.id,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(list(ACTIVE_SHIPMENT_STATUSES)),
        )
        .all()
    )

    summary = {
        "carrier_id": str(carrier.id),
        "carrier_name": carrier.name,
        "shipments_updated": 0,
        "positions_from_api": 0,
        "positions_simulated": 0,
        "errors": [],
    }

    tracking_type = ((carrier.tracking_api_type or "manual").strip().upper() or "MANUAL")
    if tracking_type not in SUPPORTED_API_TYPES:
        tracking_type = "MANUAL"

    use_simulation_fallback = True
    decrypted_credentials: dict[str, Any] | None = None

    if tracking_type in {"REST", "SOAP", "EDI"}:
        credentials = _extract_carrier_credentials(organisation, carrier)
        if credentials:
            try:
                encryption_key = app.config.get("ENCRYPTION_KEY") or ""
                decrypted = credentials
                if encryption_key:
                    decrypted = _decrypt_credentials(credentials, encryption_key)

                decrypted_credentials = decrypted
                logger.info(
                    "Carrier %s polling configured (%s) for org_id=%s with credential keys=%s",
                    carrier.name,
                    tracking_type,
                    organisation.id,
                    list(decrypted.keys()),
                )
            except Exception as exc:
                summary["errors"].append(f"Credential decryption failed: {exc}")
                logger.exception(
                    "Credential handling failed carrier_id=%s org_id=%s",
                    carrier.id,
                    organisation.id,
                )
        else:
            logger.info(
                "Carrier %s has no stored %s credentials for org_id=%s; using manual simulation fallback.",
                carrier.name,
                tracking_type,
                organisation.id,
            )

    for shipment in shipments:
        try:
            changed = False
            api_position = None

            if tracking_type == "REST" and decrypted_credentials:
                try:
                    api_position = _rest_tracking_position(decrypted_credentials, shipment)
                except Exception as exc:
                    logger.info(
                        "REST tracking fetch failed carrier_id=%s shipment_id=%s: %s",
                        carrier.id,
                        shipment.id,
                        exc,
                    )

            if shipment.status == "pending" and shipment.estimated_departure and shipment.estimated_departure <= now:
                shipment.status = "in_transit"
                shipment.actual_departure = shipment.actual_departure or now
                changed = True

            if api_position is not None:
                lat, lng, location_name = api_position
                shipment.current_latitude = round(lat, 6)
                shipment.current_longitude = round(lng, 6)
                shipment.current_location_name = location_name or shipment.current_location_name or "Carrier update"
                shipment.updated_at = now
                summary["positions_from_api"] += 1
                changed = True
            elif use_simulation_fallback:
                lat, lng, location_name = _simulate_shipment_position(shipment, now)
                if lat is not None and lng is not None:
                    shipment.current_latitude = round(lat, 6)
                    shipment.current_longitude = round(lng, 6)
                    shipment.current_location_name = location_name
                    shipment.updated_at = now
                    summary["positions_simulated"] += 1
                    changed = True

            if changed:
                summary["shipments_updated"] += 1
        except Exception as exc:
            summary["errors"].append(f"Shipment {shipment.id}: {exc}")
            logger.exception(
                "Carrier polling update failed carrier_id=%s shipment_id=%s",
                carrier.id,
                shipment.id,
            )

    return summary


def ingest_historical_performance_data(carrier_name, organisation_id, shipment_data_rows, db_session):
    """Ingest historical row data into monthly carrier performance benchmarks."""

    organisation_uuid = _coerce_uuid(organisation_id)
    summary = {
        "records_processed": 0,
        "groups_created": 0,
        "groups_updated": 0,
        "errors": [],
    }

    grouped: dict[tuple[Any, ...], dict[str, Any]] = defaultdict(
        lambda: {
            "total_shipments": 0,
            "on_time_count": 0,
            "delay_hours": [],
        }
    )

    carrier_cache: dict[str, Carrier] = {}

    for index, row in enumerate(shipment_data_rows or [], start=1):
        try:
            if not isinstance(row, dict):
                summary["errors"].append(f"Row {index}: not a dictionary payload")
                continue

            row_carrier_name = (row.get("carrier_name") or carrier_name or "").strip()
            if not row_carrier_name:
                summary["errors"].append(f"Row {index}: missing carrier name")
                continue

            cache_key = row_carrier_name.lower()
            carrier = carrier_cache.get(cache_key)
            if carrier is None:
                carrier = (
                    db_session.query(Carrier)
                    .filter(func.lower(Carrier.name) == row_carrier_name.lower())
                    .first()
                )
                if carrier is None:
                    mode_guess = _mode_to_performance_mode(row.get("mode"))
                    carrier = Carrier(
                        name=row_carrier_name,
                        mode=mode_guess,
                        tracking_api_type="manual",
                        is_global_carrier=False,
                    )
                    db_session.add(carrier)
                    db_session.flush()
                carrier_cache[cache_key] = carrier

            origin_code = (row.get("origin_port_code") or row.get("origin") or "").strip().upper()
            destination_code = (row.get("destination_port_code") or row.get("destination") or "").strip().upper()
            if not origin_code or not destination_code:
                summary["errors"].append(f"Row {index}: missing origin or destination")
                continue

            estimated_arrival = _parse_datetime(row.get("estimated_arrival"))
            actual_arrival = _parse_datetime(row.get("actual_arrival"))
            if estimated_arrival is None or actual_arrival is None:
                summary["errors"].append(
                    f"Row {index}: estimated_arrival and actual_arrival are required for performance ingestion"
                )
                continue

            mode = _mode_to_performance_mode(row.get("mode"))
            origin_region = _port_code_to_region(origin_code)
            destination_region = _port_code_to_region(destination_code)

            period_year = actual_arrival.year
            period_month = actual_arrival.month
            on_time = actual_arrival <= estimated_arrival
            delay_hours = 0.0
            if not on_time:
                delay_hours = max((actual_arrival - estimated_arrival).total_seconds() / 3600.0, 0.0)

            key = (
                carrier.id,
                organisation_uuid,
                origin_region,
                destination_region,
                mode,
                period_year,
                period_month,
            )
            grouped_entry = grouped[key]
            grouped_entry["total_shipments"] += 1
            grouped_entry["on_time_count"] += 1 if on_time else 0
            if delay_hours > 0:
                grouped_entry["delay_hours"].append(delay_hours)

            summary["records_processed"] += 1
        except Exception as exc:
            summary["errors"].append(f"Row {index}: {exc}")
            logger.exception("Historical ingestion row failed row=%s", index)

    for key, values in grouped.items():
        (
            carrier_id,
            org_id,
            origin_region,
            destination_region,
            mode,
            period_year,
            period_month,
        ) = key

        total_shipments = int(values["total_shipments"])
        on_time_count = int(values["on_time_count"])
        otd_rate = (on_time_count / total_shipments) if total_shipments > 0 else 0.0
        avg_delay_hours = mean(values["delay_hours"]) if values["delay_hours"] else 0.0

        delay_penalty = min(30.0, avg_delay_hours * 1.2)
        reliability_score = max(0.0, min(100.0, (otd_rate * 100.0) - delay_penalty))

        payload = {
            "carrier_id": carrier_id,
            "organisation_id": org_id,
            "origin_region": origin_region,
            "destination_region": destination_region,
            "mode": mode,
            "period_year": period_year,
            "period_month": period_month,
            "total_shipments": total_shipments,
            "on_time_count": on_time_count,
            "otd_rate": round(otd_rate, 4),
            "avg_delay_hours": round(avg_delay_hours, 1),
            "reliability_score": round(reliability_score, 2),
        }

        existing = (
            db_session.query(CarrierPerformance.id)
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                CarrierPerformance.organisation_id == org_id,
                CarrierPerformance.origin_region == origin_region,
                CarrierPerformance.destination_region == destination_region,
                CarrierPerformance.mode == mode,
                CarrierPerformance.period_year == period_year,
                CarrierPerformance.period_month == period_month,
            )
            .first()
        )

        stmt = _build_carrier_performance_upsert_stmt(db_session, payload)
        db_session.execute(stmt)

        if existing:
            summary["groups_updated"] += 1
        else:
            summary["groups_created"] += 1

    db_session.commit()
    return summary


def _load_performance_rows_for_carrier(carrier_id, organisation_id, db_session, months):
    cutoff = _period_cutoff_value(months)
    months = max(int(months or 1), 1)
    period_key_expr = (CarrierPerformance.period_year * 100) + CarrierPerformance.period_month

    def _rows_for_scope(org_filter, minimum_key, maximum_key=None):
        query = (
            db_session.query(CarrierPerformance)
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                org_filter,
                period_key_expr >= minimum_key,
            )
            .order_by(CarrierPerformance.period_year.asc(), CarrierPerformance.period_month.asc())
        )
        if maximum_key is not None:
            query = query.filter(period_key_expr <= maximum_key)
        return query.all()

    def _latest_period_key_for_scope(org_filter):
        return (
            db_session.query(func.max(period_key_expr))
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                org_filter,
            )
            .scalar()
        )

    org_scope = CarrierPerformance.organisation_id == organisation_id
    global_scope = CarrierPerformance.organisation_id.is_(None)

    org_rows = _rows_for_scope(org_scope, cutoff)
    if org_rows:
        return org_rows

    latest_org_period = _latest_period_key_for_scope(org_scope)
    if latest_org_period:
        historical_cutoff = _shift_period_key(int(latest_org_period), months - 1)
        org_rows = _rows_for_scope(org_scope, historical_cutoff, int(latest_org_period))
        if org_rows:
            return org_rows

    global_rows = _rows_for_scope(global_scope, cutoff)
    if global_rows:
        return global_rows

    latest_global_period = _latest_period_key_for_scope(global_scope)
    if latest_global_period:
        historical_cutoff = _shift_period_key(int(latest_global_period), months - 1)
        return _rows_for_scope(global_scope, historical_cutoff, int(latest_global_period))

    return []


def _analytics_window_start(months: int, reference_now: datetime | None = None) -> datetime:
    now = reference_now or datetime.utcnow()
    return now - timedelta(days=max(int(months or 1), 1) * 31)


def _shipment_reference_timestamp(shipment: Shipment) -> datetime | None:
    return shipment.actual_arrival or shipment.estimated_arrival or shipment.updated_at or shipment.created_at


def _shipment_outcome_metrics(shipment: Shipment, reference_now: datetime | None = None) -> tuple[bool, float]:
    now = reference_now or datetime.utcnow()
    estimated_arrival = shipment.estimated_arrival
    actual_arrival = shipment.actual_arrival
    status = (shipment.status or "").strip().lower()

    if estimated_arrival and actual_arrival:
        delay_hours = max((actual_arrival - estimated_arrival).total_seconds() / 3600.0, 0.0)
        return actual_arrival <= estimated_arrival, delay_hours

    if estimated_arrival:
        overdue_hours = (now - estimated_arrival).total_seconds() / 3600.0
        if status == "delayed":
            return False, max(overdue_hours, 2.0)
        if overdue_hours > 0:
            return False, overdue_hours
        return True, 0.0

    return status not in {"delayed", "cancelled"}, 0.0


def _load_shipments_for_carrier_analytics(carrier_id, organisation_id, db_session, months=3):
    org_uuid = _coerce_uuid(organisation_id)
    carrier_uuid = _coerce_uuid(carrier_id)
    if org_uuid is None or carrier_uuid is None:
        return []

    base_rows = (
        db_session.query(Shipment)
        .filter(
            Shipment.organisation_id == org_uuid,
            Shipment.carrier_id == carrier_uuid,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(list(ANALYTICS_SHIPMENT_STATUSES)),
        )
        .all()
    )

    if not base_rows:
        return []

    window_start = _analytics_window_start(months)
    filtered = [
        shipment
        for shipment in base_rows
        if (_shipment_reference_timestamp(shipment) or datetime.utcnow()) >= window_start
    ]

    if filtered:
        return filtered
    return base_rows


def _shipment_trend_points_for_carrier(carrier_id, organisation_id, db_session, months=12):
    rows = _load_shipments_for_carrier_analytics(carrier_id, organisation_id, db_session, months)
    if not rows:
        return []

    reference_now = datetime.utcnow()
    grouped: dict[tuple[int, int], dict[str, float]] = defaultdict(
        lambda: {
            "total_shipments": 0,
            "on_time_count": 0,
            "delayed_count": 0,
            "delay_hours_sum": 0.0,
        }
    )

    for shipment in rows:
        timestamp = _shipment_reference_timestamp(shipment) or reference_now
        key = (timestamp.year, timestamp.month)
        on_time, delay_hours = _shipment_outcome_metrics(shipment, reference_now)

        bucket = grouped[key]
        bucket["total_shipments"] += 1
        bucket["on_time_count"] += 1 if on_time else 0
        if delay_hours > 0:
            bucket["delayed_count"] += 1
            bucket["delay_hours_sum"] += delay_hours

    points: list[dict[str, Any]] = []
    for year, month in sorted(grouped.keys()):
        bucket = grouped[(year, month)]
        total_shipments = int(bucket["total_shipments"])
        delayed_count = int(bucket["delayed_count"])
        avg_delay = (bucket["delay_hours_sum"] / delayed_count) if delayed_count else 0.0
        otd_rate = ((bucket["on_time_count"] / total_shipments) * 100.0) if total_shipments else 0.0

        points.append(
            {
                "period": f"{year:04d}-{month:02d}",
                "otd_rate": round(otd_rate, 2),
                "total_shipments": total_shipments,
                "avg_delay_hours": round(avg_delay, 2),
            }
        )

    return points


def _shipment_lane_breakdown_for_carrier(carrier_id, organisation_id, db_session, months=3):
    rows = _load_shipments_for_carrier_analytics(carrier_id, organisation_id, db_session, months)
    if not rows:
        return []

    reference_now = datetime.utcnow()
    grouped: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "total_shipments": 0,
            "on_time_count": 0,
            "delayed_count": 0,
            "delay_hours_sum": 0.0,
            "reliability_sum": 0.0,
        }
    )

    for shipment in rows:
        lane = f"{_port_code_to_region(shipment.origin_port_code)} -> {_port_code_to_region(shipment.destination_port_code)}"
        on_time, delay_hours = _shipment_outcome_metrics(shipment, reference_now)
        reliability = max(0.0, min(100.0, 100.0 - _safe_float(shipment.disruption_risk_score, 0.0)))

        bucket = grouped[lane]
        bucket["total_shipments"] += 1
        bucket["on_time_count"] += 1 if on_time else 0
        if delay_hours > 0:
            bucket["delayed_count"] += 1
            bucket["delay_hours_sum"] += delay_hours
        bucket["reliability_sum"] += reliability

    lane_rows: list[dict[str, Any]] = []
    for lane, values in grouped.items():
        total_shipments = int(values["total_shipments"])
        if total_shipments <= 0:
            continue

        delayed_count = int(values["delayed_count"])
        avg_delay_hours = (values["delay_hours_sum"] / delayed_count) if delayed_count else 0.0
        otd_rate = (values["on_time_count"] / total_shipments) * 100.0
        crs_score = values["reliability_sum"] / total_shipments

        lane_rows.append(
            {
                "lane": lane,
                "otd_rate": round(otd_rate, 2),
                "total_shipments": total_shipments,
                "avg_delay_hours": round(avg_delay_hours, 2),
                "crs_score": round(crs_score, 2),
            }
        )

    lane_rows.sort(key=lambda item: item["total_shipments"], reverse=True)
    return lane_rows


def _compute_trend_direction_from_shipments(carrier_id, organisation_id, db_session, months=3):
    months = max(int(months or 1), 1)
    now = datetime.utcnow()
    current_start = _analytics_window_start(months, now)
    prior_start = _analytics_window_start(months * 2, now)

    rows = _load_shipments_for_carrier_analytics(carrier_id, organisation_id, db_session, months * 2)
    if not rows:
        return "neutral"

    current_total = 0
    current_on_time = 0
    prior_total = 0
    prior_on_time = 0

    for shipment in rows:
        timestamp = _shipment_reference_timestamp(shipment)
        if timestamp is None:
            continue

        on_time, _ = _shipment_outcome_metrics(shipment, now)
        if timestamp >= current_start:
            current_total += 1
            current_on_time += 1 if on_time else 0
        elif prior_start <= timestamp < current_start:
            prior_total += 1
            prior_on_time += 1 if on_time else 0

    if current_total <= 0 or prior_total <= 0:
        return "neutral"

    current_otd = (current_on_time / current_total) * 100.0
    prior_otd = (prior_on_time / prior_total) * 100.0
    if current_otd > prior_otd + 0.5:
        return "up"
    if current_otd < prior_otd - 0.5:
        return "down"
    return "neutral"


def _shipment_summary_for_carrier(carrier: Carrier, organisation_id, db_session, months=3):
    rows = _load_shipments_for_carrier_analytics(carrier.id, organisation_id, db_session, months)
    if not rows:
        return None

    reference_now = datetime.utcnow()
    shipments_count = len(rows)
    on_time_count = 0
    delayed_count = 0
    delay_hours_sum = 0.0
    reliability_sum = 0.0

    for shipment in rows:
        on_time, delay_hours = _shipment_outcome_metrics(shipment, reference_now)
        on_time_count += 1 if on_time else 0
        if delay_hours > 0:
            delayed_count += 1
            delay_hours_sum += delay_hours
        reliability_sum += max(0.0, min(100.0, 100.0 - _safe_float(shipment.disruption_risk_score, 0.0)))

    otd_rate = (on_time_count / shipments_count) * 100.0 if shipments_count else 0.0
    avg_delay_hours = (delay_hours_sum / delayed_count) if delayed_count else 0.0
    crs_score = (reliability_sum / shipments_count) if shipments_count else 0.0
    trend = _compute_trend_direction_from_shipments(carrier.id, organisation_id, db_session, months)

    return {
        "carrier_id": str(carrier.id),
        "carrier_name": carrier.name,
        "mode": carrier.mode,
        "otd_rate": round(otd_rate, 2),
        "avg_delay_hours": round(avg_delay_hours, 2),
        "crs_score": round(crs_score, 2),
        "shipments_count": shipments_count,
        "trend": trend,
    }


def get_carrier_otd_trend(carrier_id, organisation_id, db_session, months=12):
    """Return monthly OTD trend points for one carrier."""

    rows = _load_performance_rows_for_carrier(carrier_id, organisation_id, db_session, months)
    if not rows:
        return _shipment_trend_points_for_carrier(carrier_id, organisation_id, db_session, months)

    points: list[dict[str, Any]] = []

    for row in rows:
        points.append(
            {
                "period": f"{int(row.period_year):04d}-{int(row.period_month):02d}",
                "otd_rate": round(_safe_float(row.otd_rate, 0.0) * 100.0, 2),
                "total_shipments": int(row.total_shipments or 0),
                "avg_delay_hours": round(_safe_float(row.avg_delay_hours, 0.0), 2),
            }
        )

    return points


def get_carrier_lane_breakdown(carrier_id, organisation_id, db_session, months=3):
    """Return lane-level performance stats for a carrier."""

    rows = _load_performance_rows_for_carrier(carrier_id, organisation_id, db_session, months)
    if not rows:
        return _shipment_lane_breakdown_for_carrier(carrier_id, organisation_id, db_session, months)

    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "total_shipments": 0,
            "on_time_count": 0,
            "delay_weighted_sum": 0.0,
            "reliability_weighted_sum": 0.0,
        }
    )

    for row in rows:
        lane = f"{row.origin_region} -> {row.destination_region}"
        bucket = grouped[lane]
        shipments_count = int(row.total_shipments or 0)
        bucket["total_shipments"] += shipments_count
        bucket["on_time_count"] += int(row.on_time_count or 0)
        bucket["delay_weighted_sum"] += _safe_float(row.avg_delay_hours, 0.0) * shipments_count
        bucket["reliability_weighted_sum"] += _safe_float(row.reliability_score, 0.0) * shipments_count

    lane_rows = []
    for lane, values in grouped.items():
        total_shipments = max(int(values["total_shipments"]), 1)
        otd_rate = (values["on_time_count"] / total_shipments) * 100.0
        avg_delay_hours = values["delay_weighted_sum"] / total_shipments
        crs_score = values["reliability_weighted_sum"] / total_shipments

        lane_rows.append(
            {
                "lane": lane,
                "otd_rate": round(otd_rate, 2),
                "total_shipments": total_shipments,
                "avg_delay_hours": round(avg_delay_hours, 2),
                "crs_score": round(crs_score, 2),
            }
        )

    lane_rows.sort(key=lambda item: item["total_shipments"], reverse=True)
    return lane_rows


def _compute_trend_direction_for_carrier(carrier_id, organisation_id, db_session, months):
    months = max(int(months or 1), 1)
    period_key_expr = (CarrierPerformance.period_year * 100) + CarrierPerformance.period_month
    current_cutoff = _period_cutoff_value(months)

    def _load_trend_rows(org_filter):
        prior_cutoff = _shift_period_key(current_cutoff, months)
        current_rows = (
            db_session.query(CarrierPerformance)
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                org_filter,
                period_key_expr >= current_cutoff,
            )
            .all()
        )
        prior_rows = (
            db_session.query(CarrierPerformance)
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                org_filter,
                period_key_expr >= prior_cutoff,
                period_key_expr < current_cutoff,
            )
            .all()
        )

        if current_rows:
            return current_rows, prior_rows

        latest_period = (
            db_session.query(func.max(period_key_expr))
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                org_filter,
            )
            .scalar()
        )
        if latest_period is None:
            return [], []

        latest_period = int(latest_period)
        historical_current_cutoff = _shift_period_key(latest_period, months - 1)
        historical_prior_cutoff = _shift_period_key(historical_current_cutoff, months)

        current_rows = (
            db_session.query(CarrierPerformance)
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                org_filter,
                period_key_expr >= historical_current_cutoff,
                period_key_expr <= latest_period,
            )
            .all()
        )
        prior_rows = (
            db_session.query(CarrierPerformance)
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                org_filter,
                period_key_expr >= historical_prior_cutoff,
                period_key_expr < historical_current_cutoff,
            )
            .all()
        )
        return current_rows, prior_rows

    current_rows, prior_rows = _load_trend_rows(CarrierPerformance.organisation_id == organisation_id)
    if not current_rows:
        current_rows, prior_rows = _load_trend_rows(CarrierPerformance.organisation_id.is_(None))
    if not current_rows:
        return _compute_trend_direction_from_shipments(carrier_id, organisation_id, db_session, months)

    def weighted_otd(rows: list[CarrierPerformance]) -> float | None:
        if not rows:
            return None
        total_shipments = sum(int(row.total_shipments or 0) for row in rows)
        if total_shipments <= 0:
            return None
        on_time = sum(int(row.on_time_count or 0) for row in rows)
        return (on_time / total_shipments) * 100.0

    current_otd = weighted_otd(current_rows)
    prior_otd = weighted_otd(prior_rows)

    if current_otd is None or prior_otd is None:
        return "neutral"
    if current_otd > prior_otd + 0.5:
        return "up"
    if current_otd < prior_otd - 0.5:
        return "down"
    return "neutral"


def get_all_carriers_comparison(organisation_id, db_session, months=3):
    """Aggregate comparable OTD/delay/CRS metrics across all accessible carriers."""

    org_uuid = _coerce_uuid(organisation_id)
    if org_uuid is None:
        return []

    shipment_carrier_ids = [
        row[0]
        for row in (
            db_session.query(Shipment.carrier_id)
            .filter(
                Shipment.organisation_id == org_uuid,
                Shipment.carrier_id.isnot(None),
                Shipment.is_archived.is_(False),
            )
            .distinct()
            .all()
        )
        if row[0] is not None
    ]

    if not shipment_carrier_ids:
        return []

    carriers = (
        db_session.query(Carrier)
        .filter(Carrier.id.in_(shipment_carrier_ids))
        .order_by(Carrier.name.asc())
        .all()
    )

    summaries: list[dict[str, Any]] = []
    for carrier in carriers:
        rows = _load_performance_rows_for_carrier(carrier.id, org_uuid, db_session, months)
        if not rows:
            shipment_summary = _shipment_summary_for_carrier(carrier, org_uuid, db_session, months)
            if shipment_summary:
                summaries.append(shipment_summary)
            continue

        shipments_count = sum(int(row.total_shipments or 0) for row in rows)
        if shipments_count <= 0:
            continue

        on_time_total = sum(int(row.on_time_count or 0) for row in rows)
        weighted_delay = sum(_safe_float(row.avg_delay_hours, 0.0) * int(row.total_shipments or 0) for row in rows)
        weighted_reliability = sum(
            _safe_float(row.reliability_score, 0.0) * int(row.total_shipments or 0)
            for row in rows
        )

        otd_rate = (on_time_total / shipments_count) * 100.0
        avg_delay_hours = weighted_delay / shipments_count
        crs_score = weighted_reliability / shipments_count
        trend = _compute_trend_direction_for_carrier(carrier.id, org_uuid, db_session, months)

        summaries.append(
            {
                "carrier_id": str(carrier.id),
                "carrier_name": carrier.name,
                "mode": carrier.mode,
                "otd_rate": round(otd_rate, 2),
                "avg_delay_hours": round(avg_delay_hours, 2),
                "crs_score": round(crs_score, 2),
                "shipments_count": shipments_count,
                "trend": trend,
            }
        )

    summaries.sort(key=lambda item: item["otd_rate"], reverse=True)
    return summaries


def _build_carrier_commentary_prompt(
    carrier_name,
    mode,
    otd_rate_pct,
    trend_direction,
    avg_delay_hours,
    best_lane,
    worst_lane,
    total_shipments,
    period_months,
):
    return f"""You are a supply chain analytics AI. Analyze the following carrier performance data and generate a structured assessment.

CARRIER DATA:
- Carrier Name: {carrier_name}
- Transport Mode: {mode}
- On-Time Delivery Rate (last {period_months} months): {otd_rate_pct:.1f}%
- Performance Trend: {trend_direction} (compared to prior period)
- Average Delay (when late): {avg_delay_hours:.1f} hours
- Best Performing Lane: {best_lane}
- Worst Performing Lane: {worst_lane}
- Total Shipments Analyzed: {total_shipments}

Generate a JSON response with EXACTLY this structure (no additional fields, no markdown, pure JSON only):
{{
    "overall_trend_sentence": "One sentence describing {carrier_name}'s overall reliability trend based on the {otd_rate_pct:.1f}% OTD rate and {trend_direction} trend.",
    "strength_or_concern_sentence": "One sentence highlighting the most significant strength or concern from the data.",
    "forward_looking_sentence": "One sentence forward-looking observation about whether this carrier is reliable for future bookings.",
    "otd_assessment": "one of: excellent / good / fair / poor / very_poor",
    "confidence_level": "one of: high / medium / low",
    "risk_flags": ["array of specific risk strings, empty if none"],
    "recommended_action": "one of: continue / monitor / escalate / avoid"
}}

Return ONLY the JSON object. No explanation text. No markdown fences. No preamble. No postamble."""


def generate_carrier_ai_commentary(
    carrier,
    performance_summary,
    lane_breakdown,
    trend_direction,
    app_context,
    refresh: bool = False,
    organisation_id=None,
    period_months: int = 3,
    user_id=None,
):
    """Generate or fetch cache-backed structured carrier commentary."""

    app = _get_app(app_context)
    from app.extensions import db

    org_id = organisation_id or performance_summary.get("organisation_id")
    if org_id is None:
        return {
            "success": False,
            "fallback": True,
            "structured_data": None,
            "formatted_response": "Carrier commentary unavailable due to missing organisation context.",
            "formatted_html": ai_service.render_markdown_to_html(
                "Carrier commentary unavailable due to missing organisation context."
            ),
            "regeneration_count": 0,
            "served_stale": False,
            "stale_warning": None,
        }

    content_key = f"carrier_{carrier.id}_{int(period_months)}m"

    otd_rate = _safe_float(performance_summary.get("otd_rate"), 0.0)
    avg_delay_hours = _safe_float(performance_summary.get("avg_delay_hours"), 0.0)

    sorted_lanes = sorted(
        lane_breakdown or [],
        key=lambda item: _safe_float(item.get("otd_rate"), 0.0),
        reverse=True,
    )

    best_lane = sorted_lanes[0] if sorted_lanes else {"lane": "N/A", "otd_rate": otd_rate}
    worst_lane = sorted_lanes[-1] if sorted_lanes else {"lane": "N/A", "otd_rate": otd_rate}

    trend_text = {
        "up": "improving",
        "down": "declining",
        "neutral": "stable",
    }.get((trend_direction or "neutral").lower(), "stable")

    total_shipments = int(performance_summary.get("shipments_count") or 0)

    def _prompt_builder() -> str:
        return _build_carrier_commentary_prompt(
            carrier_name=carrier.name,
            mode=carrier.mode,
            otd_rate_pct=otd_rate,
            trend_direction=trend_text,
            avg_delay_hours=avg_delay_hours,
            best_lane=best_lane.get("lane") or "N/A",
            worst_lane=worst_lane.get("lane") or "N/A",
            total_shipments=total_shipments,
            period_months=int(period_months),
        )

    def _fallback() -> str:
        return (
            f"Carrier {carrier.name} has {otd_rate:.1f}% OTD over {int(period_months)} months with a {trend_text} trend. "
            f"Strongest lane is {best_lane.get('lane') or 'N/A'} and weakest lane is {worst_lane.get('lane') or 'N/A'}. "
            "Continue active lane-level monitoring before high-SLA allocations."
        )

    ttl = int(app.config.get("AI_CACHE_TTL_CARRIER_COMMENTARY", 86400) or 86400)
    result = ai_service.get_or_generate_ai_content(
        organisation_id=org_id,
        content_type="carrier_commentary",
        content_key=content_key,
        prompt_builder_fn=_prompt_builder,
        db_session=db.session,
        app_context=app,
        force_regenerate=bool(refresh),
        use_web_search=False,
        expected_format="json",
        expires_in_seconds=ttl,
        user_id=user_id,
        expected_schema=ai_service.CARRIER_COMMENTARY_SCHEMA,
        fallback_builder_fn=_fallback,
    )

    if result.get("fallback"):
        fallback_structured = {
            "overall_trend_sentence": f"Carrier {carrier.name} trend is {trend_text} with {otd_rate:.1f}% OTD.",
            "strength_or_concern_sentence": f"Best lane is {best_lane.get('lane') or 'N/A'} and worst lane is {worst_lane.get('lane') or 'N/A'}.",
            "forward_looking_sentence": "Maintain lane-level monitoring for future bookings.",
            "otd_assessment": "good" if otd_rate >= 80 else "fair" if otd_rate >= 65 else "poor",
            "confidence_level": "medium" if total_shipments >= 10 else "low",
            "risk_flags": [],
            "recommended_action": "monitor",
            "parse_error": True,
        }
        markdown_text = ai_service.format_gemini_text_response(
            ai_service.build_structured_markdown("carrier_commentary", fallback_structured)
        )
        return {
            "success": False,
            "served_stale": False,
            "stale_warning": None,
            "structured_data": fallback_structured,
            "formatted_response": markdown_text,
            "formatted_html": ai_service.render_markdown_to_html(markdown_text),
            "regeneration_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }

    structured_data = result.get("structured_data") or {}
    markdown_text = result.get("formatted_response") or ai_service.build_structured_markdown(
        "carrier_commentary",
        structured_data,
    )
    return {
        "success": True,
        "served_stale": bool(result.get("served_stale")),
        "stale_warning": result.get("stale_warning"),
        "structured_data": structured_data,
        "formatted_response": markdown_text,
        "formatted_html": ai_service.render_markdown_to_html(markdown_text),
        "regeneration_count": int(result.get("regeneration_count") or 0),
        "generated_at": result.get("updated_at") or result.get("created_at"),
        "record": result,
    }


__all__ = [
    "poll_carrier_for_updates",
    "ingest_historical_performance_data",
    "get_carrier_otd_trend",
    "get_carrier_lane_breakdown",
    "get_all_carriers_comparison",
    "generate_carrier_ai_commentary",
]
