"""Scenario simulation engine for projected DRS and booking recommendations."""

from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Any

from flask import current_app
from sqlalchemy import func, or_

from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.route_option import RouteOption
from app.models.shipment import Shipment
from app.services import ai_service
from app.services.disruption_engine import PORT_NAMES, _mode_to_performance_mode, _port_code_to_region
from app.services.external_data import news_monitor_service, port_data_service, weather_service

logger = logging.getLogger(__name__)

DEFAULT_TRANSIT_DAYS: dict[tuple[str, str, str], float] = {
    ("East Asia", "North America West Coast", "ocean_fcl"): 16.0,
    ("East Asia", "North America West Coast", "ocean_lcl"): 21.0,
    ("East Asia", "North America East Coast", "ocean_fcl"): 28.0,
    ("East Asia", "Europe North", "ocean_fcl"): 27.0,
    ("East Asia", "Europe North", "air"): 3.0,
    ("South Asia", "Europe North", "ocean_fcl"): 19.0,
    ("South Asia", "Europe South", "ocean_fcl"): 17.0,
    ("South Asia", "North America West Coast", "ocean_fcl"): 24.0,
    ("South Asia", "Middle East", "ocean_fcl"): 7.0,
    ("Middle East", "Europe North", "ocean_fcl"): 15.0,
    ("Middle East", "South Asia", "ocean_fcl"): 8.0,
    ("Europe North", "North America East Coast", "ocean_fcl"): 12.0,
    ("Europe North", "East Asia", "ocean_fcl"): 29.0,
    ("Southeast Asia", "Australia East Coast", "ocean_fcl"): 12.0,
    ("Southeast Asia", "North America West Coast", "ocean_fcl"): 18.0,
    ("North America West Coast", "East Asia", "ocean_fcl"): 13.0,
    ("North America East Coast", "Europe North", "ocean_fcl"): 11.0,
    ("North America East Coast", "South Asia", "ocean_fcl"): 27.0,
    ("Europe South", "Middle East", "road"): 9.0,
    ("South Asia", "Southeast Asia", "road"): 10.0,
    ("East Asia", "South Asia", "rail"): 9.0,
    ("East Asia", "Middle East", "ocean_fcl"): 17.0,
    ("Middle East", "North America East Coast", "air"): 2.0,
    ("South Asia", "Middle East", "air"): 1.5,
}

MODE_FALLBACK_TRANSIT_DAYS = {
    "air": 2.5,
    "road": 7.0,
    "rail": 10.0,
    "multimodal": 14.0,
    "ocean_fcl": 22.0,
    "ocean_lcl": 25.0,
}


MODE_DISPLAY = {
    "ocean_fcl": "Ocean FCL",
    "ocean_lcl": "Ocean LCL",
    "air": "Air Freight",
    "road": "Road/Truck",
    "rail": "Rail",
    "multimodal": "Multimodal",
}


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


def _port_label(port_code: str) -> str:
    code = (port_code or "").strip().upper()
    if code in PORT_NAMES:
        name, country = PORT_NAMES[code]
        return f"{name} ({code}, {country})"
    return code or "Unknown"


def _drs_composite(tvs: float, mcs: float, ehs: float, crs: float, dtas: float, cps: float) -> float:
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
    return max(0.0, min(100.0, drs))


def _lane_health_rating(otd_rate_pct: float) -> str:
    if otd_rate_pct >= 90.0:
        return "Excellent"
    if otd_rate_pct >= 80.0:
        return "Good"
    if otd_rate_pct >= 70.0:
        return "Fair"
    if otd_rate_pct >= 60.0:
        return "Poor"
    return "Very Poor"


def _lane_health_score(otd_rate_pct: float, avg_delay_hours: float) -> float:
    delay_component = max(0.0, 100.0 - min(avg_delay_hours * 3.0, 100.0))
    score = (otd_rate_pct * 0.72) + (delay_component * 0.28)
    return max(0.0, min(100.0, score))


def _load_lane_performance(carrier_id, organisation_id, origin_region, destination_region, mode, db_session):
    perf_mode = _mode_to_performance_mode(mode)

    org_rows = (
        db_session.query(CarrierPerformance)
        .filter(
            CarrierPerformance.carrier_id == carrier_id,
            CarrierPerformance.organisation_id == organisation_id,
            CarrierPerformance.origin_region == origin_region,
            CarrierPerformance.destination_region == destination_region,
            CarrierPerformance.mode == perf_mode,
        )
        .order_by(CarrierPerformance.period_year.desc(), CarrierPerformance.period_month.desc())
        .limit(12)
        .all()
    )

    rows = org_rows
    source = "organisation"
    if not rows:
        rows = (
            db_session.query(CarrierPerformance)
            .filter(
                CarrierPerformance.carrier_id == carrier_id,
                CarrierPerformance.organisation_id.is_(None),
                CarrierPerformance.origin_region == origin_region,
                CarrierPerformance.destination_region == destination_region,
                CarrierPerformance.mode == perf_mode,
            )
            .order_by(CarrierPerformance.period_year.desc(), CarrierPerformance.period_month.desc())
            .limit(12)
            .all()
        )
        source = "global"

    if not rows:
        return {
            "lane_otd_rate": 0.78,
            "lane_avg_delay_hours": 14.0,
            "lane_crs_score": 72.0,
            "source": "fallback",
            "mode": perf_mode,
        }

    total_shipments = sum(int(row.total_shipments or 0) for row in rows)
    total_shipments = max(total_shipments, 1)
    on_time = sum(int(row.on_time_count or 0) for row in rows)

    lane_otd_rate = on_time / total_shipments
    lane_avg_delay_hours = (
        sum(_safe_float(row.avg_delay_hours, 0.0) * int(row.total_shipments or 0) for row in rows)
        / total_shipments
    )
    lane_crs_score = (
        sum(_safe_float(row.reliability_score, 0.0) * int(row.total_shipments or 0) for row in rows)
        / total_shipments
    )

    return {
        "lane_otd_rate": max(0.0, min(1.0, lane_otd_rate)),
        "lane_avg_delay_hours": max(0.0, lane_avg_delay_hours),
        "lane_crs_score": max(0.0, min(100.0, lane_crs_score)),
        "source": source,
        "mode": perf_mode,
    }


def _estimate_transit_days(origin_region, destination_region, mode, db_session) -> float:
    db_value = (
        db_session.query(func.avg(RouteOption.estimated_transit_days))
        .filter(
            RouteOption.origin_region == origin_region,
            RouteOption.destination_region == destination_region,
            RouteOption.original_mode == mode,
            RouteOption.is_active.is_(True),
        )
        .scalar()
    )

    transit_days = _safe_float(db_value, 0.0)
    if transit_days > 0:
        return transit_days

    if (origin_region, destination_region, mode) in DEFAULT_TRANSIT_DAYS:
        return DEFAULT_TRANSIT_DAYS[(origin_region, destination_region, mode)]
    if (destination_region, origin_region, mode) in DEFAULT_TRANSIT_DAYS:
        return DEFAULT_TRANSIT_DAYS[(destination_region, origin_region, mode)]

    return MODE_FALLBACK_TRANSIT_DAYS.get(mode, 14.0)


def _build_projection_data(ship_date: datetime, arrival_date: datetime, drs_departure: float, drs_arrival: float):
    projection = []
    total_seconds = max((arrival_date - ship_date).total_seconds(), 1.0)

    for idx in range(8):
        t = idx / 7.0
        sigmoid = 1.0 / (1.0 + math.exp(-7.0 * (t - 0.5)))
        shaped_progress = (t * 0.72) + (sigmoid * 0.28)

        point_drs = drs_departure + ((drs_arrival - drs_departure) * shaped_progress)
        timestamp = ship_date + timedelta(seconds=total_seconds * t)

        projection.append(
            {
                "timestamp": timestamp.isoformat(),
                "drs": round(max(0.0, min(100.0, point_drs)), 2),
            }
        )

    return projection


def _build_top_risk_factors(weather_risk, port_risk, event_risk, lane_crs_score):
    factors = [
        {
            "factor_name": "Weather Volatility",
            "score": round(weather_risk, 2),
            "description": "Adverse weather patterns can introduce departure or transit delays.",
            "icon_class": "bi-cloud-lightning-rain",
            "drs_contribution": weather_risk * 0.20,
        },
        {
            "factor_name": "Port Congestion",
            "score": round(port_risk, 2),
            "description": "High congestion increases berth wait times and terminal dwell risk.",
            "icon_class": "bi-building",
            "drs_contribution": port_risk * 0.20,
        },
        {
            "factor_name": "Route Event Risk",
            "score": round(event_risk, 2),
            "description": "Geopolitical or labor events may disrupt the planned corridor.",
            "icon_class": "bi-exclamation-triangle",
            "drs_contribution": event_risk * 0.20,
        },
        {
            "factor_name": "Carrier Reliability Risk",
            "score": round(100.0 - lane_crs_score, 2),
            "description": "Historical carrier consistency for this lane influences SLA predictability.",
            "icon_class": "bi-truck",
            "drs_contribution": (100.0 - lane_crs_score) * 0.15,
        },
    ]

    factors.sort(key=lambda item: item["drs_contribution"], reverse=True)
    return [
        {
            "factor_name": item["factor_name"],
            "score": item["score"],
            "description": item["description"],
            "icon_class": item["icon_class"],
        }
        for item in factors[:3]
    ]


def _normalize_recommendation_level(value: str | None) -> str:
    text = (value or "").strip().lower()
    if text.startswith("green"):
        return "green"
    if text.startswith("red"):
        return "red"
    return "amber"


def _build_simulation_content_key(simulation_params: dict[str, Any]) -> str:
    ship_date = simulation_params.get("estimated_ship_date")
    if isinstance(ship_date, datetime):
        date_key = ship_date.strftime("%Y%m%d")
    else:
        date_key = str(ship_date or "").replace("-", "")[:8] or datetime.utcnow().strftime("%Y%m%d")

    origin = (simulation_params.get("origin_port_code") or "UNK").strip().upper()
    destination = (simulation_params.get("destination_port_code") or "UNK").strip().upper()
    carrier_id = simulation_params.get("carrier_id") or "none"
    org_id = simulation_params.get("organisation_id")

    return f"simulation_{org_id}_{origin}_{destination}_{carrier_id}_{date_key}"


def _build_simulation_narrative_prompt(
    origin_port,
    destination_port,
    mode,
    carrier_name,
    cargo_value_inr,
    sla_days,
    drs_at_departure,
    drs_at_arrival,
    recommendation_level,
    top_3_risk_factors,
    lane_health_rating,
    carrier_otd_pct,
    sla_breach_probability_pct,
):
    risk_factors_str = "\n".join(
        [
            f"  - {r['factor_name']}: score {r['score']}/100 — {r['description']}"
            for r in (top_3_risk_factors or [])
            if isinstance(r, dict)
        ]
    )
    if not risk_factors_str:
        risk_factors_str = "  - No dominant risk factor identified"

    return f"""You are a senior logistics risk analyst AI for ChainWatch Pro supply chain platform.

SHIPMENT SCENARIO DETAILS:
- Route: {origin_port} -> {destination_port}
- Mode: {mode}
- Carrier: {carrier_name}
- Cargo Value: ₹{cargo_value_inr:,.2f} INR
- SLA Requirement: {sla_days} days
- Projected DRS at Departure: {drs_at_departure:.1f}/100
- Projected DRS at Arrival: {drs_at_arrival:.1f}/100
- Overall Booking Recommendation: {recommendation_level.upper()}
- SLA Breach Probability: {sla_breach_probability_pct:.1f}%
- Lane Health Rating: {lane_health_rating}
- Carrier Historical OTD: {carrier_otd_pct:.1f}%

TOP 3 RISK FACTORS:
{risk_factors_str}

Generate a JSON response with EXACTLY this structure (pure JSON only, no markdown, no explanation):
{{
    "risk_assessment_paragraph": "Full paragraph describing overall risk assessment and key driving factors. Must reference specific numbers from the data above. Professional logistics tone.",
    "recommendation_paragraph": "Full paragraph with concrete recommendation. If GREEN: confirm booking and monitoring advice. If AMBER: specific mitigations and alternative carriers. If RED: strongly advise against and provide concrete alternatives with specific details.",
    "recommendation_level": "{recommendation_level.lower()}",
    "top_risk_summary": "Single sentence max summarizing the biggest risk.",
    "suggested_alternatives": ["up to 3 specific actionable alternative strings, empty array if recommendation is green"],
    "monitoring_frequency": "daily or every_6_hours or hourly or real_time based on risk level"
}}

Return ONLY the JSON object. No explanation text. No markdown fences. No preamble. No postamble."""


def generate_simulation_ai_narrative(
    simulation_params,
    simulation_results,
    app_context,
    force_regenerate: bool = False,
    user_id=None,
):
    """Generate or fetch cached 2-paragraph simulation narrative."""

    app = _get_app(app_context)
    from app.extensions import db

    org_id = simulation_params.get("organisation_id")
    if org_id is None:
        fallback = "Simulation narrative unavailable due to missing organisation context."
        return {
            "success": False,
            "served_stale": False,
            "stale_warning": None,
            "structured_data": {
                "risk_assessment_paragraph": fallback,
                "recommendation_paragraph": "",
                "recommendation_level": "amber",
                "top_risk_summary": fallback,
                "suggested_alternatives": [],
                "monitoring_frequency": "daily",
                "parse_error": True,
            },
            "formatted_response": fallback,
            "formatted_html": ai_service.render_markdown_to_html(fallback),
            "regeneration_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }

    content_key = _build_simulation_content_key(simulation_params)

    origin_port = (simulation_params.get("origin_port_code") or "").strip().upper()
    destination_port = (simulation_params.get("destination_port_code") or "").strip().upper()
    mode = MODE_DISPLAY.get(simulation_params.get("mode"), simulation_params.get("mode"))
    carrier_name = simulation_results.get("carrier_performance_summary", {}).get("carrier_name", "Unknown Carrier")
    cargo_value_inr = _safe_float(simulation_params.get("cargo_value_inr"), 0.0)
    sla_days = int(simulation_params.get("sla_requirement_days") or 0)
    drs_at_departure = _safe_float(simulation_results.get("drs_at_departure"), 0.0)
    drs_at_arrival = _safe_float(simulation_results.get("drs_at_arrival"), 0.0)
    recommendation_level = _normalize_recommendation_level(
        simulation_results.get("booking_recommendation_level")
    )
    top_3_risk_factors = simulation_results.get("top_3_risk_factors") or []
    lane_health_rating = simulation_results.get("lane_health_rating") or "Fair"
    carrier_otd_pct = _safe_float(simulation_results.get("lane_otd_rate_pct"), 0.0)
    sla_breach_probability_pct = _safe_float(simulation_results.get("sla_breach_probability"), 0.0) * 100.0

    def _prompt_builder() -> str:
        return _build_simulation_narrative_prompt(
            origin_port=origin_port,
            destination_port=destination_port,
            mode=mode,
            carrier_name=carrier_name,
            cargo_value_inr=cargo_value_inr,
            sla_days=sla_days,
            drs_at_departure=drs_at_departure,
            drs_at_arrival=drs_at_arrival,
            recommendation_level=recommendation_level,
            top_3_risk_factors=top_3_risk_factors,
            lane_health_rating=lane_health_rating,
            carrier_otd_pct=carrier_otd_pct,
            sla_breach_probability_pct=sla_breach_probability_pct,
        )

    def _fallback() -> str:
        return (
            f"Projected DRS changes from {drs_at_departure:.1f} at departure to {drs_at_arrival:.1f} at arrival for "
            f"{origin_port} -> {destination_port}, with historical carrier OTD at {carrier_otd_pct:.1f}%. "
            f"Recommendation level is {recommendation_level.upper()}; maintain proactive monitoring and mitigation readiness."
        )

    ttl = int(app.config.get("AI_CACHE_TTL_SIMULATION_NARRATIVE", 3600) or 3600)
    result = ai_service.get_or_generate_ai_content(
        organisation_id=org_id,
        content_type="simulation_narrative",
        content_key=content_key,
        prompt_builder_fn=_prompt_builder,
        db_session=db.session,
        app_context=app,
        force_regenerate=bool(force_regenerate),
        use_web_search=False,
        expected_format="json",
        expires_in_seconds=ttl,
        user_id=user_id,
        expected_schema=ai_service.SIMULATION_NARRATIVE_SCHEMA,
        fallback_builder_fn=_fallback,
    )

    if result.get("fallback"):
        structured = {
            "risk_assessment_paragraph": _fallback(),
            "recommendation_paragraph": "Continue mitigation monitoring and reroute readiness.",
            "recommendation_level": recommendation_level,
            "top_risk_summary": "Simulation AI response unavailable; fallback assessment applied.",
            "suggested_alternatives": [],
            "monitoring_frequency": "daily",
            "parse_error": True,
        }
        markdown_text = ai_service.build_structured_markdown("simulation_narrative", structured)
        return {
            "success": False,
            "served_stale": False,
            "stale_warning": None,
            "structured_data": structured,
            "formatted_response": markdown_text,
            "formatted_html": ai_service.render_markdown_to_html(markdown_text),
            "regeneration_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }

    structured = result.get("structured_data") or {}
    markdown_text = result.get("formatted_response") or ai_service.build_structured_markdown(
        "simulation_narrative",
        structured,
    )
    return {
        "success": True,
        "served_stale": bool(result.get("served_stale")),
        "stale_warning": result.get("stale_warning"),
        "structured_data": structured,
        "formatted_response": markdown_text,
        "formatted_html": ai_service.render_markdown_to_html(markdown_text),
        "regeneration_count": int(result.get("regeneration_count") or 0),
        "generated_at": result.get("updated_at") or result.get("created_at"),
        "record": result,
    }


def run_simulation(simulation_params, db_session, app_context, include_ai_narrative: bool = True):
    """Run the full 4-step future shipment simulation algorithm."""

    app = _get_app(app_context)

    origin_port_code = (simulation_params.get("origin_port_code") or "").strip().upper()
    destination_port_code = (simulation_params.get("destination_port_code") or "").strip().upper()
    mode = (simulation_params.get("mode") or "").strip().lower()
    carrier_id = simulation_params.get("carrier_id")
    organisation_id = simulation_params.get("organisation_id")

    estimated_ship_date = simulation_params.get("estimated_ship_date")
    if isinstance(estimated_ship_date, str):
        estimated_ship_date = datetime.fromisoformat(estimated_ship_date)
    if estimated_ship_date is None:
        estimated_ship_date = datetime.utcnow()
    if not isinstance(estimated_ship_date, datetime):
        estimated_ship_date = datetime.combine(estimated_ship_date, datetime.min.time())

    cargo_value_inr = _safe_float(simulation_params.get("cargo_value_inr"), 0.0)
    sla_requirement_days = int(simulation_params.get("sla_requirement_days") or 0)

    carrier = db_session.query(Carrier).filter(Carrier.id == carrier_id).first()
    origin_region = _port_code_to_region(origin_port_code)
    destination_region = _port_code_to_region(destination_port_code)

    lane_perf = _load_lane_performance(
        carrier_id,
        organisation_id,
        origin_region,
        destination_region,
        mode,
        db_session,
    )

    lane_otd_rate = _safe_float(lane_perf["lane_otd_rate"], 0.78)
    lane_avg_delay_hours = _safe_float(lane_perf["lane_avg_delay_hours"], 14.0)
    lane_crs_score = _safe_float(lane_perf["lane_crs_score"], 72.0)

    lane_otd_rate_pct = lane_otd_rate * 100.0
    lane_health_rating = _lane_health_rating(lane_otd_rate_pct)
    lane_health_score = _lane_health_score(lane_otd_rate_pct, lane_avg_delay_hours)

    try:
        port_congestion_score = _safe_float(
            port_data_service.get_port_congestion_score(
                destination_port_code,
                app,
                organisation_id=organisation_id,
            ),
            50.0,
        )
    except Exception:
        logger.exception("Port congestion lookup failed in simulation")
        port_congestion_score = 50.0

    try:
        weather_payload = weather_service.get_route_weather_risk(
            origin_port_code,
            destination_port_code,
            None,
            None,
            app,
        )
        weather_risk_score = _safe_float(weather_payload.get("risk_score"), 50.0)
    except Exception:
        logger.exception("Weather lookup failed in simulation")
        weather_risk_score = 50.0

    try:
        event_payload = news_monitor_service.get_route_event_risk(
            origin_port_code,
            destination_port_code,
            app,
            organisation_id=organisation_id,
        )
        event_risk_score = _safe_float(event_payload.get("event_score"), 50.0)
    except Exception:
        logger.exception("Event lookup failed in simulation")
        event_risk_score = 50.0

    ehs_baseline = max(weather_risk_score, port_congestion_score, event_risk_score)

    tvs_departure = 50.0
    mcs_departure = 95.0

    days_to_departure = (estimated_ship_date.date() - datetime.utcnow().date()).days
    if days_to_departure > 14:
        ehs_departure = ehs_baseline * 0.70
    elif days_to_departure <= 7:
        ehs_departure = ehs_baseline
    else:
        ehs_departure = ehs_baseline * 0.85
    ehs_departure = max(0.0, min(100.0, ehs_departure))

    crs_departure = lane_crs_score
    dtas_departure = 0.0

    sla_tightness_score = max(0.0, 1.0 - (sla_requirement_days / 3.0))
    cargo_component = min(40.0, (cargo_value_inr / 10_000_000.0) * 40.0)
    cps = max(0.0, min(100.0, cargo_component + (sla_tightness_score * 60.0)))

    drs_at_departure = _drs_composite(
        tvs_departure,
        mcs_departure,
        ehs_departure,
        crs_departure,
        dtas_departure,
        cps,
    )

    estimated_transit_days = _estimate_transit_days(origin_region, destination_region, mode, db_session)
    estimated_arrival = estimated_ship_date + timedelta(days=estimated_transit_days)

    tvs_by_rating = {
        "Excellent": 85.0,
        "Good": 75.0,
        "Fair": 65.0,
        "Poor": 50.0,
        "Very Poor": 35.0,
    }
    tvs_arrival = tvs_by_rating.get(lane_health_rating, 65.0)
    mcs_arrival = lane_otd_rate_pct
    ehs_arrival = min(100.0, ehs_departure * 1.15)

    drs_at_arrival = _drs_composite(
        tvs_arrival,
        mcs_arrival,
        ehs_arrival,
        crs_departure,
        dtas_departure,
        cps,
    )

    drs_projection_chart_data = _build_projection_data(
        estimated_ship_date,
        estimated_arrival,
        drs_at_departure,
        drs_at_arrival,
    )

    base_breach = max(0.0, min(1.0, 1.0 - lane_otd_rate))

    if ehs_arrival > 70:
        ehs_adjustment = 0.20
    elif ehs_arrival >= 50:
        ehs_adjustment = 0.10
    else:
        ehs_adjustment = 0.0

    if port_congestion_score > 65:
        congestion_adjustment = 0.15
    elif port_congestion_score >= 40:
        congestion_adjustment = 0.07
    else:
        congestion_adjustment = 0.0

    sla_breach_probability = min(1.0, base_breach + ehs_adjustment + congestion_adjustment)

    if drs_at_arrival >= 65 or sla_breach_probability >= 0.50:
        booking_recommendation_level = "Red — High Risk"
    elif (40 <= drs_at_arrival < 65) or (0.25 <= sla_breach_probability < 0.50):
        booking_recommendation_level = "Amber — Proceed with Caution"
    else:
        booking_recommendation_level = "Green — Proceed"

    top_3_risk_factors = _build_top_risk_factors(
        weather_risk_score,
        port_congestion_score,
        event_risk_score,
        lane_crs_score,
    )

    simulation_results = {
        "drs_at_departure": round(drs_at_departure, 2),
        "drs_at_arrival": round(drs_at_arrival, 2),
        "drs_projection_chart_data": drs_projection_chart_data,
        "lane_health_rating": lane_health_rating,
        "lane_health_score": round(lane_health_score, 2),
        "lane_otd_rate": round(lane_otd_rate, 4),
        "lane_otd_rate_pct": round(lane_otd_rate_pct, 2),
        "lane_avg_delay_hours": round(lane_avg_delay_hours, 2),
        "sla_breach_probability": round(sla_breach_probability, 4),
        "booking_recommendation_level": booking_recommendation_level,
        "top_3_risk_factors": top_3_risk_factors,
        "port_congestion_score": round(port_congestion_score, 2),
        "weather_risk_score": round(weather_risk_score, 2),
        "event_risk_score": round(event_risk_score, 2),
        "carrier_performance_summary": {
            "carrier_id": str(carrier.id) if carrier else None,
            "carrier_name": carrier.name if carrier else "Unknown Carrier",
            "carrier_mode": carrier.mode if carrier else None,
            "lane_source": lane_perf.get("source"),
            "origin_region": origin_region,
            "destination_region": destination_region,
            "mode": mode,
            "lane_crs_score": round(lane_crs_score, 2),
            "estimated_transit_days": round(estimated_transit_days, 1),
        },
        "estimated_arrival": estimated_arrival.isoformat(),
    }

    if include_ai_narrative:
        ai_payload = generate_simulation_ai_narrative(
            simulation_params,
            simulation_results,
            app,
            force_regenerate=False,
            user_id=None,
        )
        structured = ai_payload.get("structured_data") or {}
        narrative_text = (
            f"{structured.get('risk_assessment_paragraph', '').strip()}\n\n"
            f"{structured.get('recommendation_paragraph', '').strip()}"
        ).strip()

        simulation_results["ai_narrative"] = narrative_text
        simulation_results["ai_narrative_markdown"] = ai_payload.get("formatted_response") or ""
        simulation_results["ai_narrative_html"] = ai_payload.get("formatted_html") or ""
        simulation_results["ai_narrative_structured"] = structured
        simulation_results["ai_narrative_fallback"] = not bool(ai_payload.get("success"))
        simulation_results["ai_narrative_served_stale"] = bool(ai_payload.get("served_stale"))
        simulation_results["ai_narrative_stale_warning"] = ai_payload.get("stale_warning")
        simulation_results["ai_regeneration_count"] = int(ai_payload.get("regeneration_count") or 0)
        simulation_results["ai_generated_at"] = ai_payload.get("generated_at")
    else:
        simulation_results["ai_narrative"] = ""
        simulation_results["ai_narrative_markdown"] = ""
        simulation_results["ai_narrative_html"] = ""
        simulation_results["ai_narrative_structured"] = {}
        simulation_results["ai_narrative_fallback"] = False
        simulation_results["ai_narrative_served_stale"] = False
        simulation_results["ai_narrative_stale_warning"] = None
        simulation_results["ai_regeneration_count"] = 0
        simulation_results["ai_generated_at"] = None

    return simulation_results


def _org_accessible_carrier_ids(organisation_id, db_session) -> set[str]:
    org_carrier_ids = {
        str(row[0])
        for row in (
            db_session.query(Shipment.carrier_id)
            .filter(
                Shipment.organisation_id == organisation_id,
                Shipment.carrier_id.isnot(None),
                Shipment.is_archived.is_(False),
            )
            .distinct()
            .all()
        )
        if row[0] is not None
    }

    global_ids = {
        str(row[0])
        for row in db_session.query(Carrier.id).filter(Carrier.is_global_carrier.is_(True)).all()
    }

    return org_carrier_ids | global_ids


def _estimate_cost_range_inr(carrier: Carrier, origin_region: str, destination_region: str, mode: str, cargo_value_inr: float, db_session):
    route_rows = (
        db_session.query(RouteOption)
        .filter(
            RouteOption.origin_region == origin_region,
            RouteOption.destination_region == destination_region,
            RouteOption.original_mode == mode,
            RouteOption.is_active.is_(True),
            func.lower(RouteOption.alt_carrier_name) == carrier.name.lower(),
        )
        .all()
    )

    if not route_rows:
        route_rows = (
            db_session.query(RouteOption)
            .filter(
                RouteOption.origin_region == origin_region,
                RouteOption.destination_region == destination_region,
                RouteOption.original_mode == mode,
                RouteOption.is_active.is_(True),
            )
            .limit(20)
            .all()
        )

    base_value = cargo_value_inr if cargo_value_inr > 0 else 5_000_000.0

    if route_rows:
        deltas = [_safe_float(row.cost_delta_percent, 0.0) for row in route_rows]
        min_delta = min(deltas)
        max_delta = max(deltas)
    else:
        min_delta = -5.0
        max_delta = 8.0

    min_cost = base_value * (1.0 + (min_delta / 100.0))
    max_cost = base_value * (1.0 + (max_delta / 100.0))

    return {
        "min": round(min(min_cost, max_cost), 2),
        "max": round(max(min_cost, max_cost), 2),
    }


def run_carrier_comparison_simulation(
    origin_port_code,
    destination_port_code,
    mode,
    ship_date,
    cargo_value_inr,
    sla_days,
    candidate_carrier_ids,
    organisation_id,
    db_session,
    app_context,
):
    """Run simulation across up to 3 candidate carriers and rank outcomes."""

    accessible_ids = _org_accessible_carrier_ids(organisation_id, db_session)
    unique_candidates = []
    seen = set()

    for candidate_id in candidate_carrier_ids or []:
        candidate_text = str(candidate_id)
        if candidate_text in seen:
            continue
        seen.add(candidate_text)
        if candidate_text not in accessible_ids:
            logger.warning(
                "Skipping inaccessible carrier candidate_id=%s organisation_id=%s",
                candidate_text,
                organisation_id,
            )
            continue
        unique_candidates.append(candidate_text)
        if len(unique_candidates) >= 3:
            break

    results = []
    origin_region = _port_code_to_region(origin_port_code)
    destination_region = _port_code_to_region(destination_port_code)

    for candidate_id in unique_candidates:
        carrier = db_session.query(Carrier).filter(Carrier.id == candidate_id).first()
        if carrier is None:
            continue

        params = {
            "origin_port_code": origin_port_code,
            "destination_port_code": destination_port_code,
            "mode": mode,
            "carrier_id": candidate_id,
            "estimated_ship_date": ship_date,
            "cargo_value_inr": cargo_value_inr,
            "sla_requirement_days": sla_days,
            "organisation_id": organisation_id,
        }

        simulation_result = run_simulation(
            params,
            db_session,
            app_context,
            include_ai_narrative=False,
        )
        simulation_result["carrier_name"] = carrier.name
        simulation_result["carrier_id"] = str(carrier.id)
        simulation_result["estimated_cost_range_inr"] = _estimate_cost_range_inr(
            carrier,
            origin_region,
            destination_region,
            mode,
            _safe_float(cargo_value_inr, 0.0),
            db_session,
        )
        results.append(simulation_result)

    if not results:
        return []

    mid_costs = [
        (_safe_float(item["estimated_cost_range_inr"].get("min")) + _safe_float(item["estimated_cost_range_inr"].get("max"))) / 2.0
        for item in results
    ]
    min_mid = min(mid_costs)
    max_mid = max(mid_costs)

    for idx, result in enumerate(results):
        midpoint = mid_costs[idx]
        if max_mid > min_mid:
            cost_normalized = ((midpoint - min_mid) / (max_mid - min_mid)) * 100.0
        else:
            cost_normalized = 50.0

        composite_score = (
            (_safe_float(result.get("drs_at_arrival"), 0.0) * 0.5)
            + (_safe_float(result.get("sla_breach_probability"), 0.0) * 50.0)
            + (cost_normalized * 0.2)
        )

        result["cost_normalized"] = round(cost_normalized, 2)
        result["composite_rank_score"] = round(composite_score, 2)

    results.sort(key=lambda item: item["composite_rank_score"])

    for rank, result in enumerate(results, start=1):
        result["rank"] = rank

    return results


__all__ = [
    "run_simulation",
    "generate_simulation_ai_narrative",
    "run_carrier_comparison_simulation",
]
