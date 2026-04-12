"""Route optimization engine for generating alternative logistics strategies."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.route_option import RouteOption
from app.models.route_recommendation import RouteRecommendation
from app.services.disruption_engine import _port_code_to_region
from app.services.external_data import port_data_service, weather_service

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _normalize_mode(mode_value: str | None) -> str:
    mode = (mode_value or "").strip().lower()
    if mode in {"ocean_fcl", "ocean_lcl", "ocean", "ocean premium", "ocean standard", "ocean lcl express"}:
        return "ocean"
    if "air" in mode:
        return "air"
    if "road" in mode or "truck" in mode:
        return "road"
    if "rail" in mode:
        return "rail"
    if "multi" in mode:
        return "multimodal"
    return "multimodal"


def _build_generated_options(shipment, existing_count: int) -> list[dict[str, Any]]:
    mode = (shipment.mode or "").strip().lower()
    generated: list[dict[str, Any]] = []

    if mode in {"ocean_fcl", "ocean_lcl"}:
        generated.extend(
            [
                {
                    "strategy": "fastest",
                    "alt_carrier_name": "Emirates SkyCargo",
                    "alt_mode": "air",
                    "alt_route_description": (
                        f"Air uplift fallback from {shipment.origin_port_code} to {shipment.destination_port_code} "
                        "with immediate customs pre-clearance and last-mile trucking."
                    ),
                    "estimated_transit_days": 2.2,
                    "cost_delta_percent": 22.0,
                },
                {
                    "strategy": "hybrid",
                    "alt_carrier_name": "DHL Express",
                    "alt_mode": "multimodal",
                    "alt_route_description": (
                        f"Sea-air multimodal via Middle East gateway for {shipment.origin_port_code} to "
                        f"{shipment.destination_port_code}, balancing speed and spend."
                    ),
                    "estimated_transit_days": 5.0,
                    "cost_delta_percent": 11.0,
                },
                {
                    "strategy": "cost_optimized",
                    "alt_carrier_name": "CMA CGM",
                    "alt_mode": "ocean",
                    "alt_route_description": (
                        "Rebook to alternate ocean service with lower congestion transshipment node and "
                        "priority berth allotment."
                    ),
                    "estimated_transit_days": 11.0,
                    "cost_delta_percent": -4.0,
                },
            ]
        )
    elif mode == "air":
        generated.extend(
            [
                {
                    "strategy": "cost_optimized",
                    "alt_carrier_name": "Maersk Line",
                    "alt_mode": "multimodal",
                    "alt_route_description": (
                        "Road-air consolidated lane with controlled handoff to reduce premium air spend."
                    ),
                    "estimated_transit_days": 4.8,
                    "cost_delta_percent": -15.0,
                },
                {
                    "strategy": "hybrid",
                    "alt_carrier_name": "Kuehne+Nagel",
                    "alt_mode": "multimodal",
                    "alt_route_description": (
                        "Road plus feeder ocean contingency with SLA-buffered interchange windows."
                    ),
                    "estimated_transit_days": 6.4,
                    "cost_delta_percent": -9.0,
                },
                {
                    "strategy": "fastest",
                    "alt_carrier_name": "FedEx Express",
                    "alt_mode": "air",
                    "alt_route_description": "Priority direct air service with reserved uplift and expedited ground handling.",
                    "estimated_transit_days": 1.8,
                    "cost_delta_percent": 18.0,
                },
            ]
        )
    else:
        generated.extend(
            [
                {
                    "strategy": "fastest",
                    "alt_carrier_name": "UPS",
                    "alt_mode": "air",
                    "alt_route_description": "Emergency premium reroute using express line-haul and priority customs release.",
                    "estimated_transit_days": 2.7,
                    "cost_delta_percent": 16.0,
                },
                {
                    "strategy": "cost_optimized",
                    "alt_carrier_name": "MSC",
                    "alt_mode": "ocean",
                    "alt_route_description": "Cost-focused lane shift to lower-bunker and lower-congestion service window.",
                    "estimated_transit_days": 9.5,
                    "cost_delta_percent": -6.0,
                },
                {
                    "strategy": "hybrid",
                    "alt_carrier_name": "DB Schenker",
                    "alt_mode": "multimodal",
                    "alt_route_description": "Balanced multimodal reroute with resilient hub selection and backup transfer plan.",
                    "estimated_transit_days": 6.0,
                    "cost_delta_percent": 4.0,
                },
            ]
        )

    needed = max(0, 3 - existing_count)
    return generated[:needed]


def _lookup_base_otc(db_session, shipment, alt_carrier_name: str, origin_region: str, destination_region: str, alt_mode: str) -> tuple[float, Carrier | None]:
    carrier = (
        db_session.query(Carrier)
        .filter(func.lower(Carrier.name) == (alt_carrier_name or "").strip().lower())
        .first()
    )

    if carrier is None:
        return 65.0, None

    record = (
        db_session.query(CarrierPerformance)
        .filter(
            CarrierPerformance.carrier_id == carrier.id,
            CarrierPerformance.origin_region == origin_region,
            CarrierPerformance.destination_region == destination_region,
            CarrierPerformance.mode == _normalize_mode(alt_mode),
            CarrierPerformance.organisation_id.in_([shipment.organisation_id, None]),
        )
        .order_by(
            CarrierPerformance.period_year.desc(),
            CarrierPerformance.period_month.desc(),
            CarrierPerformance.organisation_id.is_(None).asc(),
        )
        .first()
    )

    if record is None:
        return 65.0, carrier

    return max(0.0, min(100.0, _safe_float(record.otd_rate, 0.65) * 100.0)), carrier


def _compute_candidate_metrics(shipment, option: dict[str, Any], db_session, app_context, origin_region: str, destination_region: str) -> dict[str, Any]:
    alt_carrier_name = option.get("alt_carrier_name")
    alt_mode = option.get("alt_mode")

    base_otc, carrier = _lookup_base_otc(
        db_session,
        shipment,
        alt_carrier_name,
        origin_region,
        destination_region,
        alt_mode,
    )

    weather_risk_payload = weather_service.get_route_weather_risk(
        shipment.origin_port_code,
        shipment.destination_port_code,
        _safe_float(shipment.current_latitude, None),
        _safe_float(shipment.current_longitude, None),
        app_context,
    )
    weather_risk = _safe_float(weather_risk_payload.get("risk_score"), 30.0)

    if weather_risk < 30:
        weather_adjustment = 1.0
    elif weather_risk <= 70:
        weather_adjustment = 0.90
    else:
        weather_adjustment = 0.75

    congestion_score = _safe_float(
        port_data_service.get_port_congestion_score(
            shipment.destination_port_code,
            app_context,
            organisation_id=shipment.organisation_id,
        ),
        45.0,
    )
    if congestion_score < 40:
        port_adjustment = 1.0
    elif congestion_score <= 70:
        port_adjustment = 0.92
    else:
        port_adjustment = 0.82

    on_time_confidence = base_otc * weather_adjustment * port_adjustment
    on_time_confidence = max(0.0, min(100.0, on_time_confidence))

    base_cargo_value = _safe_float(shipment.cargo_value_inr, 0.0) or 5_000_000.0
    cost_delta_inr = base_cargo_value * (_safe_float(option.get("cost_delta_percent"), 0.0) / 100.0)
    cost_delta_inr = float(round(cost_delta_inr / 100.0) * 100.0)

    now = datetime.utcnow()
    original_remaining_hours = max(
        0.0,
        (shipment.estimated_arrival - now).total_seconds() / 3600.0,
    )
    alt_transit_hours = _safe_float(option.get("estimated_transit_days"), 0.0) * 24.0
    transit_time_delta_hours = alt_transit_hours - original_remaining_hours

    revised_eta = now + timedelta(hours=alt_transit_hours)
    execution_deadline = revised_eta - timedelta(hours=48)

    return {
        "candidate_id": f"{alt_carrier_name}:{option.get('alt_route_description')}:{alt_mode}",
        "strategy": option.get("strategy") or "hybrid",
        "alt_carrier_name": alt_carrier_name,
        "alt_carrier_id": carrier.id if carrier else None,
        "alt_mode": alt_mode,
        "alt_route_description": option.get("alt_route_description"),
        "estimated_transit_days": _safe_float(option.get("estimated_transit_days"), 0.0),
        "cost_delta_percent": _safe_float(option.get("cost_delta_percent"), 0.0),
        "base_cargo_value": base_cargo_value,
        "cost_delta_inr": cost_delta_inr,
        "transit_time_delta_hours": float(round(transit_time_delta_hours, 2)),
        "on_time_confidence": float(round(on_time_confidence, 2)),
        "revised_eta": revised_eta,
        "execution_deadline": execution_deadline,
        "weather_risk_score": weather_risk,
        "port_congestion_score": congestion_score,
    }


def _pick_candidate(candidates: list[dict[str, Any]], selected_ids: set[str], key_fn, confidence_threshold: float | None = None) -> dict[str, Any] | None:
    filtered = [c for c in candidates if c["candidate_id"] not in selected_ids]
    if confidence_threshold is not None:
        confident = [c for c in filtered if _safe_float(c.get("on_time_confidence"), 0.0) >= confidence_threshold]
        if confident:
            return sorted(confident, key=key_fn)[0]
    if not filtered:
        return None
    return sorted(filtered, key=key_fn)[0]


def generate_route_alternatives(shipment, db_session, app_context):
    """Generate and persist three distinct route alternatives for at-risk shipments."""

    if shipment is None:
        return []

    now = datetime.utcnow()
    current_drs = _safe_float(shipment.disruption_risk_score, 0.0)

    if current_drs < 60.0:
        logger.info("Route optimizer skipped shipment_id=%s: DRS below threshold", shipment.id)
        return []

    existing_pending = (
        db_session.query(RouteRecommendation)
        .filter(
            RouteRecommendation.shipment_id == shipment.id,
            RouteRecommendation.status == "pending",
            RouteRecommendation.execution_deadline > now,
        )
        .order_by(RouteRecommendation.option_label.asc())
        .all()
    )
    if existing_pending:
        return existing_pending

    stale_pending = (
        db_session.query(RouteRecommendation)
        .filter(
            RouteRecommendation.shipment_id == shipment.id,
            RouteRecommendation.status == "pending",
            RouteRecommendation.execution_deadline <= now,
        )
        .all()
    )
    for item in stale_pending:
        item.status = "expired"

    existing_approved = (
        db_session.query(RouteRecommendation)
        .filter(
            RouteRecommendation.shipment_id == shipment.id,
            RouteRecommendation.status == "approved",
        )
        .first()
    )
    if existing_approved:
        db_session.commit()
        logger.info(
            "Route optimizer skipped shipment_id=%s: approved recommendation already exists",
            shipment.id,
        )
        return []

    execution_window_deadline = shipment.estimated_arrival - timedelta(hours=72)
    if execution_window_deadline < now:
        db_session.commit()
        logger.warning(
            "Route optimizer skipped shipment_id=%s: execution window has passed",
            shipment.id,
        )
        return []

    origin_region = _port_code_to_region(shipment.origin_port_code)
    destination_region = _port_code_to_region(shipment.destination_port_code)

    route_option_rows = (
        db_session.query(RouteOption)
        .filter(
            RouteOption.origin_region == origin_region,
            RouteOption.destination_region == destination_region,
            RouteOption.original_mode == shipment.mode,
            RouteOption.is_active.is_(True),
        )
        .all()
    )

    option_dicts: list[dict[str, Any]] = [
        {
            "strategy": row.strategy,
            "alt_carrier_name": row.alt_carrier_name,
            "alt_mode": row.alt_mode,
            "alt_route_description": row.alt_route_description,
            "estimated_transit_days": _safe_float(row.estimated_transit_days, 0.0),
            "cost_delta_percent": _safe_float(row.cost_delta_percent, 0.0),
        }
        for row in route_option_rows
    ]

    if len(option_dicts) < 3:
        option_dicts.extend(_build_generated_options(shipment, len(option_dicts)))

    if len(option_dicts) < 3:
        option_dicts.extend(_build_generated_options(shipment, 0))

    metric_candidates = [
        _compute_candidate_metrics(
            shipment,
            option,
            db_session,
            app_context,
            origin_region,
            destination_region,
        )
        for option in option_dicts
    ]

    selected_ids: set[str] = set()

    fastest_choice = _pick_candidate(
        metric_candidates,
        selected_ids,
        key_fn=lambda c: (c["transit_time_delta_hours"], -c["on_time_confidence"]),
        confidence_threshold=60.0,
    )
    if fastest_choice is None:
        fastest_choice = _pick_candidate(
            metric_candidates,
            selected_ids,
            key_fn=lambda c: (c["transit_time_delta_hours"], -c["on_time_confidence"]),
        )

    if fastest_choice:
        selected_ids.add(fastest_choice["candidate_id"])

    cost_choice = _pick_candidate(
        metric_candidates,
        selected_ids,
        key_fn=lambda c: (c["cost_delta_inr"], -c["on_time_confidence"]),
        confidence_threshold=75.0,
    )
    if cost_choice is None:
        cost_choice = _pick_candidate(
            metric_candidates,
            selected_ids,
            key_fn=lambda c: (c["cost_delta_inr"], -c["on_time_confidence"]),
            confidence_threshold=60.0,
        )
    if cost_choice is None:
        cost_choice = _pick_candidate(
            metric_candidates,
            selected_ids,
            key_fn=lambda c: (c["cost_delta_inr"], -c["on_time_confidence"]),
        )

    if cost_choice:
        selected_ids.add(cost_choice["candidate_id"])

    def hybrid_key(candidate):
        base_cargo_value = max(_safe_float(candidate.get("base_cargo_value"), 5_000_000.0), 1.0)
        hybrid_score = _safe_float(candidate.get("on_time_confidence"), 0.0) - abs(
            _safe_float(candidate.get("cost_delta_inr"), 0.0) / (base_cargo_value / 100.0)
        )
        return -hybrid_score

    hybrid_choice = _pick_candidate(
        metric_candidates,
        selected_ids,
        key_fn=hybrid_key,
    )

    if hybrid_choice:
        selected_ids.add(hybrid_choice["candidate_id"])

    selected_ordered = [
        ("A", "fastest", fastest_choice),
        ("B", "cost_optimized", cost_choice),
        ("C", "hybrid", hybrid_choice),
    ]

    selected_valid = [item for item in selected_ordered if item[2] is not None]

    if len(selected_valid) < 3:
        remaining = [c for c in metric_candidates if c["candidate_id"] not in selected_ids]
        remaining_sorted = sorted(
            remaining,
            key=lambda c: (-c["on_time_confidence"], c["cost_delta_inr"], c["transit_time_delta_hours"]),
        )
        strategy_fallback = ["fastest", "cost_optimized", "hybrid"]
        labels_fallback = ["A", "B", "C"]

        while len(selected_valid) < 3 and remaining_sorted:
            candidate = remaining_sorted.pop(0)
            idx = len(selected_valid)
            selected_valid.append((labels_fallback[idx], strategy_fallback[idx], candidate))

    for label, _, candidate in selected_valid:
        candidate["option_label"] = label

    created_rows: list[RouteRecommendation] = []
    for option_label, strategy_name, candidate in selected_valid[:3]:
        recommendation = RouteRecommendation(
            shipment_id=shipment.id,
            option_label=option_label,
            strategy=strategy_name,
            alt_carrier_id=candidate.get("alt_carrier_id"),
            alt_route_description=candidate.get("alt_route_description") or "Alternative route",
            revised_eta=candidate.get("revised_eta"),
            transit_time_delta_hours=round(_safe_float(candidate.get("transit_time_delta_hours"), 0.0), 1),
            cost_delta_inr=round(_safe_float(candidate.get("cost_delta_inr"), 0.0), 2),
            on_time_confidence=round(_safe_float(candidate.get("on_time_confidence"), 0.0), 2),
            execution_deadline=candidate.get("execution_deadline"),
            status="pending",
        )
        db_session.add(recommendation)
        created_rows.append(recommendation)

    db_session.commit()

    option_labels = [item.option_label for item in created_rows]
    option_confidence = {
        item.option_label: round(_safe_float(item.on_time_confidence), 2)
        for item in created_rows
    }

    if created_rows:
        AuditLog.log(
            db,
            event_type="route_generated",
            description=(
                f"Generated {len(created_rows)} route alternatives for shipment {shipment.external_reference}."
            ),
            organisation_id=shipment.organisation_id,
            shipment_id=shipment.id,
            metadata={
                "shipment_id": str(shipment.id),
                "option_count": len(created_rows),
                "trigger_drs": current_drs,
                "option_labels": option_labels,
                "on_time_confidence": option_confidence,
            },
        )

    return created_rows
