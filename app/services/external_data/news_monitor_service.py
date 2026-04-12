"""Gemini-powered route event and alert enrichment via centralized AI service."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from flask import current_app
from flask_login import current_user

from app.extensions import db
from app.models.shipment import Shipment
from app.services import ai_service
from app.services.disruption_engine import PORT_NAMES, _port_code_to_region

logger = logging.getLogger(__name__)


def _get_app(app_context):
    if app_context is not None:
        return app_context
    return current_app._get_current_object()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _resolve_organisation_id(organisation_id=None):
    if organisation_id is not None:
        return organisation_id

    try:
        if current_user.is_authenticated:
            return current_user.organisation_id
    except Exception:
        return None

    return None


def _derive_transit_regions(origin_region: str, destination_region: str) -> str:
    lane_map: dict[tuple[str, str], list[str]] = {
        ("East Asia", "Europe North"): [
            "South China Sea",
            "Strait of Malacca",
            "Indian Ocean",
            "Suez Canal",
            "North Sea",
        ],
        ("East Asia", "North America West Coast"): ["North Pacific", "Trans-Pacific lane"],
        ("East Asia", "North America East Coast"): ["North Pacific", "Panama Canal", "US East Coast"],
        ("South Asia", "Europe North"): ["Arabian Sea", "Red Sea", "Suez Canal"],
        ("Middle East", "Europe North"): ["Gulf of Aden", "Red Sea", "Suez Canal"],
        ("Southeast Asia", "Australia East Coast"): ["Java Sea", "Coral Sea"],
        ("Europe North", "North America East Coast"): ["North Atlantic"],
    }

    if (origin_region, destination_region) in lane_map:
        return ", ".join(lane_map[(origin_region, destination_region)])
    if (destination_region, origin_region) in lane_map:
        return ", ".join(lane_map[(destination_region, origin_region)])

    return f"{origin_region} and {destination_region} transit corridor"


def _build_route_event_risk_prompt(
    origin_port_name,
    origin_country,
    destination_port_name,
    destination_country,
    transit_regions,
    current_date_str,
):
    return f"""Search for current supply chain disruptions affecting cargo shipments on this route.

ROUTE DETAILS:
- Origin: {origin_port_name}, {origin_country}
- Destination: {destination_port_name}, {destination_country}
- Transit Regions: {transit_regions}
- Query Date: {current_date_str}

Search for: port strikes, labor disputes, geopolitical tensions, natural disasters, regulatory changes, port congestion, infrastructure issues affecting this specific route right now.

Return EXACTLY this JSON structure (no markdown, pure JSON):
{{
    "event_score": <integer 0-100 where 0=no disruption, 100=route completely blocked>,
    "event_description": "<2-3 sentence summary of current risk situation on this route>",
    "active_events": [
        {{
            "event_type": "<strike|geopolitical|weather|regulatory|infrastructure|congestion|none>",
            "severity": "<critical|high|medium|low>",
            "location": "<affected location>",
            "description": "<one sentence>",
            "estimated_duration": "<ongoing|2-3 days|1 week|unknown>"
        }}
    ],
    "search_performed": true,
    "data_freshness": "<real_time|recent|historical>",
    "recommended_monitoring": "<increased|standard|reduced>"
}}

If no active disruptions found, set event_score to 0-15 and active_events to empty array.
Return ONLY the JSON object. No explanation text. No markdown fences. No preamble. No postamble."""


def get_route_event_risk(
    origin_port_code,
    destination_port_code,
    app_context,
    organisation_id=None,
    force_regenerate: bool = False,
    user_id=None,
):
    """Return route event risk using one cached grounded Gemini call."""

    app = _get_app(app_context)
    org_id = _resolve_organisation_id(organisation_id)

    origin_code = (origin_port_code or "").upper().strip()
    destination_code = (destination_port_code or "").upper().strip()
    date_key = datetime.utcnow().strftime("%Y%m%d")

    if org_id is None:
        return {
            "event_score": 50.0,
            "event_description": "Event intelligence unavailable due to missing organisation context.",
            "active_events": [],
            "search_performed": False,
            "data_freshness": "historical",
            "recommended_monitoring": "standard",
            "cached": False,
            "served_stale": False,
            "stale_warning": None,
        }

    origin_name, origin_country = PORT_NAMES.get(origin_code, (origin_code, "Unknown"))
    destination_name, destination_country = PORT_NAMES.get(destination_code, (destination_code, "Unknown"))
    origin_region = _port_code_to_region(origin_code)
    destination_region = _port_code_to_region(destination_code)
    transit_regions = _derive_transit_regions(origin_region, destination_region)
    current_date_str = datetime.utcnow().strftime("%Y-%m-%d")

    content_key = f"route_{origin_code}_{destination_code}_{date_key}"

    def _prompt_builder() -> str:
        return _build_route_event_risk_prompt(
            origin_port_name=origin_name,
            origin_country=origin_country,
            destination_port_name=destination_name,
            destination_country=destination_country,
            transit_regions=transit_regions,
            current_date_str=current_date_str,
        )

    def _fallback() -> str:
        return (
            f"Unable to fetch real-time events for {origin_name} to {destination_name}. "
            "Apply standard monitoring cadence until grounded intelligence recovers."
        )

    ttl = int(app.config.get("AI_CACHE_TTL_ROUTE_EVENT_RISK", 1800) or 1800)
    result = ai_service.get_or_generate_ai_content(
        organisation_id=org_id,
        content_type="route_event_risk",
        content_key=content_key,
        prompt_builder_fn=_prompt_builder,
        db_session=db.session,
        app_context=app,
        force_regenerate=bool(force_regenerate),
        use_web_search=True,
        expected_format="json",
        expires_in_seconds=ttl,
        user_id=user_id,
        expected_schema=ai_service.ROUTE_EVENT_RISK_SCHEMA,
        fallback_builder_fn=_fallback,
    )

    if result.get("fallback"):
        return {
            "event_score": 50.0,
            "event_description": _fallback(),
            "active_events": [],
            "search_performed": False,
            "data_freshness": "historical",
            "recommended_monitoring": "standard",
            "cached": False,
            "served_stale": False,
            "stale_warning": None,
        }

    structured = result.get("structured_data") or {}
    return {
        "event_score": float(max(0.0, min(100.0, _safe_float(structured.get("event_score"), 50.0)))),
        "event_description": (structured.get("event_description") or "").strip() or "No significant event risk identified.",
        "active_events": structured.get("active_events") if isinstance(structured.get("active_events"), list) else [],
        "search_performed": bool(structured.get("search_performed", True)),
        "data_freshness": structured.get("data_freshness") or "recent",
        "recommended_monitoring": structured.get("recommended_monitoring") or "standard",
        "cached": bool(result.get("cache_hit")),
        "served_stale": bool(result.get("served_stale")),
        "stale_warning": result.get("stale_warning"),
        "regeneration_count": int(result.get("regeneration_count") or 0),
        "generated_at": result.get("updated_at") or result.get("created_at"),
    }


def scan_all_active_routes(organisation_id, db_session, app_context):
    """Scan distinct active shipment routes for one organisation."""

    route_pairs = (
        db_session.query(Shipment.origin_port_code, Shipment.destination_port_code)
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(["pending", "in_transit", "delayed", "at_customs"]),
        )
        .distinct()
        .all()
    )

    results: dict[tuple[str, str], dict[str, Any]] = {}
    for origin_port_code, destination_port_code in route_pairs:
        origin_code = (origin_port_code or "").upper().strip()
        destination_code = (destination_port_code or "").upper().strip()
        try:
            results[(origin_code, destination_code)] = get_route_event_risk(
                origin_code,
                destination_code,
                app_context,
                organisation_id=organisation_id,
            )
        except Exception:
            logger.exception(
                "Route event scan failed for org_id=%s route=%s->%s",
                organisation_id,
                origin_code,
                destination_code,
            )
            results[(origin_code, destination_code)] = {
                "event_score": 50.0,
                "event_description": "Event scan failed; using neutral fallback.",
                "active_events": [],
                "search_performed": False,
                "data_freshness": "historical",
                "recommended_monitoring": "standard",
                "cached": False,
                "served_stale": False,
                "stale_warning": None,
            }

    return results


def _build_alert_description_prompt(
    alert_type,
    origin_port,
    destination_port,
    drs_total,
    ehs_signals,
):
    signals_str = "\n".join(
        [
            f"  - {k}: {v}"
            for k, v in (ehs_signals or {}).items()
            if isinstance(v, (int, float))
        ]
    )
    if not signals_str:
        signals_str = "  - No numeric signals available"

    return f"""You are a supply chain risk intelligence AI. Generate an enriched alert description.

ALERT DATA:
- Alert Type: {alert_type}
- Route: {origin_port} -> {destination_port}
- Current DRS Score: {drs_total:.1f}/100
- Risk Signals:
{signals_str}

Generate a JSON response with EXACTLY this structure (pure JSON only):
{{
    "enriched_title": "<improved alert title, max 80 characters>",
    "cause_sentence": "<one sentence: what is happening and why>",
    "impact_sentence": "<one sentence: likely downstream SLA and delivery impact>",
    "action_sentence": "<one sentence: standard industry response for this disruption>",
    "full_description": "<combine cause, impact, and action into a flowing 3-sentence paragraph>",
    "severity_justification": "<brief reason for severity level>",
    "recommended_action_code": "<reroute|delay_acceptance|insurance_claim|monitor|escalate_to_carrier|customs_inquiry>"
}}

Return ONLY the JSON object. No explanation text. No markdown fences. No preamble. No postamble."""


def generate_alert_description_with_gemini(
    alert_type,
    shipment,
    drs_total,
    ehs_signals,
    app_context,
    alert_id,
    force_regenerate: bool = False,
    user_id=None,
):
    """Generate or fetch cached enriched alert description JSON."""

    app = _get_app(app_context)
    org_id = _resolve_organisation_id(getattr(shipment, "organisation_id", None))
    if org_id is None:
        description = (
            f"{alert_type.replace('_', ' ').title()} detected on route {shipment.origin_port_code} -> {shipment.destination_port_code}. "
            "Review rerouting and customer communication actions to protect SLA."
        )
        return {
            "success": False,
            "structured_data": {
                "enriched_title": f"{alert_type.replace('_', ' ').title()} Alert",
                "cause_sentence": description,
                "impact_sentence": "",
                "action_sentence": "",
                "full_description": description,
                "severity_justification": "AI unavailable",
                "recommended_action_code": "monitor",
                "parse_error": True,
            },
            "formatted_response": description,
            "formatted_html": ai_service.render_markdown_to_html(description),
            "served_stale": False,
            "stale_warning": None,
            "regeneration_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }

    content_key = f"alert_{alert_id}"
    drs_total_value = _safe_float(drs_total, 0.0)

    def _prompt_builder() -> str:
        return _build_alert_description_prompt(
            alert_type=alert_type,
            origin_port=shipment.origin_port_code,
            destination_port=shipment.destination_port_code,
            drs_total=drs_total_value,
            ehs_signals=ehs_signals or {},
        )

    def _fallback() -> str:
        return (
            f"{alert_type.replace('_', ' ').title()} detected on route {shipment.origin_port_code} -> {shipment.destination_port_code}. "
            f"Current DRS is {drs_total_value:.1f}/100. Review rerouting and escalation actions immediately."
        )

    alert_ttl = app.config.get("AI_CACHE_TTL_ALERT_DESCRIPTION", 0)
    expires_in_seconds = int(alert_ttl) if int(alert_ttl or 0) > 0 else None

    result = ai_service.get_or_generate_ai_content(
        organisation_id=org_id,
        content_type="alert_description",
        content_key=content_key,
        prompt_builder_fn=_prompt_builder,
        db_session=db.session,
        app_context=app,
        force_regenerate=bool(force_regenerate),
        use_web_search=False,
        expected_format="json",
        expires_in_seconds=expires_in_seconds,
        user_id=user_id,
        expected_schema=ai_service.ALERT_DESCRIPTION_SCHEMA,
        fallback_builder_fn=_fallback,
    )

    if result.get("fallback"):
        structured = {
            "enriched_title": f"{alert_type.replace('_', ' ').title()} Alert",
            "cause_sentence": _fallback(),
            "impact_sentence": "Delivery SLA may be affected if no mitigation is applied.",
            "action_sentence": "Increase lane monitoring and prepare reroute contingency.",
            "full_description": _fallback(),
            "severity_justification": "Automated fallback due to AI unavailability.",
            "recommended_action_code": "monitor",
            "parse_error": True,
        }
        markdown_text = ai_service.build_structured_markdown("alert_description", structured)
        return {
            "success": False,
            "structured_data": structured,
            "formatted_response": markdown_text,
            "formatted_html": ai_service.render_markdown_to_html(markdown_text),
            "served_stale": False,
            "stale_warning": None,
            "regeneration_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }

    structured = result.get("structured_data") or {}
    markdown_text = result.get("formatted_response") or ai_service.build_structured_markdown(
        "alert_description",
        structured,
    )
    return {
        "success": True,
        "structured_data": structured,
        "formatted_response": markdown_text,
        "formatted_html": ai_service.render_markdown_to_html(markdown_text),
        "served_stale": bool(result.get("served_stale")),
        "stale_warning": result.get("stale_warning"),
        "regeneration_count": int(result.get("regeneration_count") or 0),
        "generated_at": result.get("updated_at") or result.get("created_at"),
        "record": result,
    }
