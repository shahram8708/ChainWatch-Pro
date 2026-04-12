"""Risk heat map routes and shipment slide panel partial."""

from __future__ import annotations

import json
import logging
import uuid

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user

from app.extensions import db, get_redis_client
from app.models.audit_log import AuditLog
from app.models.shipment import Shipment
from app.services.external_data import port_data_service, weather_service
from app.utils.decorators import login_required, verified_required

logger = logging.getLogger(__name__)

risk_map_bp = Blueprint("risk_map", __name__, url_prefix="/risk-map")

ACTIVE_STATUSES = ["pending", "in_transit", "delayed", "at_customs"]


def _risk_level(score: float) -> str:
    if score >= 81:
        return "critical"
    if score >= 61:
        return "warning"
    if score >= 31:
        return "watch"
    return "green"


def _mode_family(mode: str) -> str:
    mode_norm = (mode or "").strip().lower()
    if mode_norm in {"ocean_fcl", "ocean_lcl"}:
        return "ocean"
    return mode_norm


def _active_shipments_map_payload(organisation_id):
    rows = (
        db.session.query(Shipment)
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(ACTIVE_STATUSES),
            Shipment.current_latitude.isnot(None),
            Shipment.current_longitude.isnot(None),
        )
        .order_by(Shipment.updated_at.desc())
        .all()
    )

    payload = []
    for shipment in rows:
        drs = float(shipment.disruption_risk_score or 0)
        payload.append(
            {
                "id": str(shipment.id),
                "external_reference": shipment.external_reference or str(shipment.id),
                "lat": float(shipment.current_latitude),
                "lng": float(shipment.current_longitude),
                "drs": drs,
                "risk_level": _risk_level(drs),
                "status": shipment.status,
                "carrier_name": shipment.carrier.name if shipment.carrier else "Unassigned",
                "carrier_id": str(shipment.carrier_id) if shipment.carrier_id else None,
                "mode": shipment.mode,
                "mode_family": _mode_family(shipment.mode),
                "origin_port_code": shipment.origin_port_code,
                "destination_port_code": shipment.destination_port_code,
                "current_location_name": shipment.current_location_name or "Location pending",
                "estimated_arrival": shipment.estimated_arrival.isoformat() if shipment.estimated_arrival else None,
            }
        )

    return payload


def _load_weather_alert_data(organisation_id, allow_live_refresh: bool = True):
    redis_client = get_redis_client()

    if redis_client is not None:
        key = f"org:{organisation_id}:weather_alert_locations"
        try:
            cached = redis_client.get(key)
            if cached:
                parsed = json.loads(cached)
                if isinstance(parsed, list):
                    return parsed
        except Exception:
            logger.exception("Failed reading org weather cache for risk map")

        try:
            aggregated = redis_client.get("external:weather_points")
            if aggregated:
                parsed = json.loads(aggregated)
                org_payload = parsed.get(str(organisation_id))
                if isinstance(org_payload, list):
                    return org_payload
        except Exception:
            logger.exception("Failed reading aggregated weather cache for risk map")

    if not allow_live_refresh:
        return []

    return weather_service.get_weather_alert_locations(
        organisation_id,
        db.session,
        current_app._get_current_object(),
    )


def _active_shipments_for_org(organisation_id):
    return (
        db.session.query(Shipment)
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(ACTIVE_STATUSES),
        )
        .all()
    )


def _focus_port_codes(active_shipments, limit: int = 12):
    codes: list[str] = []
    seen: set[str] = set()

    for shipment in active_shipments:
        for code in [shipment.origin_port_code, shipment.destination_port_code]:
            normalized = (code or "").strip().upper()
            if not normalized or normalized in seen:
                continue

            seen.add(normalized)
            codes.append(normalized)

            if len(codes) >= limit:
                return codes

    return codes


@risk_map_bp.before_request
@login_required
@verified_required
def _guards():
    """Apply auth guards for risk map routes."""


@risk_map_bp.get("")
def index():
    """Render full-screen risk heat map with overlays and filters."""

    org_id = current_user.organisation_id

    shipment_map_data = _active_shipments_map_payload(org_id)
    port_congestion_data = port_data_service.get_port_congestion_zones(
        current_app._get_current_object(),
        organisation_id=org_id,
        use_ai=False,
    )
    weather_alert_data = _load_weather_alert_data(org_id, allow_live_refresh=False)

    active_shipments = _active_shipments_for_org(org_id)

    distinct_carriers = sorted({shipment.carrier.name for shipment in active_shipments if shipment.carrier})
    distinct_modes = sorted({_mode_family(shipment.mode) for shipment in active_shipments if shipment.mode})

    try:
        AuditLog.log(
            db,
            event_type="risk_map_viewed",
            description="Viewed risk map overlays and shipment markers.",
            organisation_id=org_id,
            actor_user=current_user,
            metadata={
                "active_shipments_visible": len(shipment_map_data),
                "overlays_default_state": {
                    "active_shipments": True,
                    "port_congestion_zones": False,
                    "weather_alerts": False,
                },
            },
            ip_address=None,
        )
    except Exception:
        logger.exception("Failed writing risk_map_viewed audit log")

    return render_template(
        "app/risk_map/index.html",
        shipment_map_data=shipment_map_data,
        port_congestion_data=port_congestion_data,
        weather_alert_data=weather_alert_data,
        distinct_carriers=distinct_carriers,
        distinct_modes=distinct_modes,
        total_active_shipments=len(shipment_map_data),
    )


@risk_map_bp.get("/port-congestion-feed")
def port_congestion_feed():
    """Return targeted, AI-enriched congestion data for relevant shipment ports."""

    org_id = current_user.organisation_id

    try:
        limit = int(request.args.get("limit", 12) or 12)
    except (TypeError, ValueError):
        limit = 12
    limit = max(1, min(limit, 25))

    force_refresh = request.args.get("refresh") == "1"
    app = current_app._get_current_object()
    enable_ai = bool(app.config.get("GEMINI_API_KEY"))

    active_shipments = _active_shipments_for_org(org_id)
    focus_codes = _focus_port_codes(active_shipments, limit=limit)
    if not focus_codes:
        focus_codes = sorted(port_data_service.PORT_BASELINE_CONGESTION.keys())[:limit]

    zones = port_data_service.get_port_congestion_zones(
        app,
        organisation_id=org_id,
        port_codes=focus_codes,
        use_ai=enable_ai,
        force_regenerate=force_refresh,
        user_id=current_user.id,
    )

    return jsonify(
        {
            "success": True,
            "count": len(zones),
            "zones": zones,
            "cache_mode": "dynamic" if enable_ai else "baseline",
        }
    )


@risk_map_bp.get("/weather-alert-feed")
def weather_alert_feed():
    """Return weather alert locations for async map overlay refresh."""

    org_id = current_user.organisation_id
    force_refresh = request.args.get("refresh") == "1"

    if not force_refresh:
        cached = _load_weather_alert_data(org_id, allow_live_refresh=False)
        if cached:
            return jsonify(
                {
                    "success": True,
                    "count": len(cached),
                    "locations": cached,
                    "cache_hit": True,
                }
            )

    locations = weather_service.get_weather_alert_locations(
        org_id,
        db.session,
        current_app._get_current_object(),
    )
    return jsonify(
        {
            "success": True,
            "count": len(locations),
            "locations": locations,
            "cache_hit": False,
        }
    )


@risk_map_bp.get("/shipment-summary/<uuid:shipment_id>")
def shipment_summary(shipment_id: uuid.UUID):
    """Return shipment slide panel partial for clicked map marker."""

    shipment = (
        db.session.query(Shipment)
        .filter(Shipment.id == shipment_id, Shipment.organisation_id == current_user.organisation_id)
        .first()
    )
    if shipment is None:
        abort(404)

    drs_value = float(shipment.disruption_risk_score or 0)
    risk_level = _risk_level(drs_value)

    return render_template(
        "app/risk_map/_shipment_panel.html",
        shipment=shipment,
        drs_value=drs_value,
        risk_level=risk_level,
        days_remaining=shipment.days_to_delivery,
    )
