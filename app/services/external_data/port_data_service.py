"""Port congestion and customs risk scoring service."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from flask import current_app
from flask_login import current_user

from app.extensions import db
from app.services import ai_service
from app.services.disruption_engine import PORT_COORDINATES, PORT_NAMES

logger = logging.getLogger(__name__)

PORT_BASELINE_CONGESTION: dict[str, float] = {
    "CNSHA": 45,
    "CNNGB": 42,
    "CNSZX": 48,
    "CNSHK": 40,
    "SGSIN": 35,
    "MYPKG": 34,
    "THLCH": 38,
    "VNHPH": 36,
    "IDJKT": 44,
    "PHPMN": 50,
    "LKCMB": 43,
    "KRPUS": 38,
    "JPYOK": 32,
    "JPTYO": 34,
    "TWKHH": 37,
    "INBOM": 55,
    "INMAA": 52,
    "INNSA": 58,
    "INMUN": 50,
    "AEDXB": 30,
    "QADOH": 33,
    "SADMM": 35,
    "OMSOH": 31,
    "NLRTM": 40,
    "DEHAM": 43,
    "GBFXT": 41,
    "BEANR": 39,
    "FRLEH": 37,
    "ESBCN": 36,
    "ITGOA": 35,
    "TRIST": 46,
    "USNYC": 49,
    "USEWR": 53,
    "USLAX": 55,
    "USLGB": 58,
    "USOAK": 47,
    "USSEA": 44,
    "USSAV": 46,
    "USMIA": 42,
    "USHOU": 45,
    "CAVAN": 41,
    "MXVER": 48,
    "BRSSZ": 57,
    "BRRIO": 54,
    "CLVAP": 43,
    "ZADUR": 51,
    "EGALY": 49,
    "AUSYD": 34,
    "AUMEL": 33,
    "NZAKL": 31,
}

PORT_CUSTOMS_BASELINE: dict[str, float] = {
    "SGSIN": 18,
    "NLRTM": 20,
    "DEHAM": 22,
    "GBFXT": 24,
    "BEANR": 24,
    "CNSHA": 50,
    "CNNGB": 48,
    "CNSZX": 52,
    "CNSHK": 40,
    "INBOM": 55,
    "INMAA": 53,
    "INNSA": 58,
    "INMUN": 52,
    "USNYC": 32,
    "USEWR": 34,
    "USLAX": 36,
    "USLGB": 38,
    "USSEA": 30,
    "AEDXB": 24,
    "QADOH": 25,
    "SADMM": 33,
    "OMSOH": 28,
    "MYPKG": 32,
    "THLCH": 36,
    "VNHPH": 44,
    "IDJKT": 50,
    "PHPMN": 48,
    "LKCMB": 40,
    "KRPUS": 28,
    "JPYOK": 22,
    "TWKHH": 29,
    "BRSSZ": 62,
    "BRRIO": 64,
    "MXVER": 58,
    "CLVAP": 56,
    "ZADUR": 52,
    "EGALY": 60,
    "AUSYD": 23,
    "AUMEL": 21,
    "NZAKL": 20,
}

LAND_BORDER_CROSSINGS = {
    "INATT",
    "INPTL",
    "MXNLE",
    "USDET",
    "PLWAR",
    "TRKPI",
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


def _resolve_organisation_id(organisation_id=None):
    if organisation_id is not None:
        return organisation_id

    try:
        if current_user.is_authenticated:
            return current_user.organisation_id
    except Exception:
        return None

    return None


def _get_port_name(port_code: str) -> str:
    code = (port_code or "").strip().upper()
    if code in PORT_NAMES:
        return PORT_NAMES[code][0]
    return code or "Unknown"


def _build_port_congestion_prompt(port_code: str, port_name: str, current_date: str) -> str:
    return f"""You are a maritime logistics intelligence AI. Assess current congestion at this port.

PORT DETAILS:
- Port Code: {port_code}
- Port Name: {port_name}
- Query Date: {current_date}

Search for vessel queue length, berth occupancy, wait times, labor disruption, and terminal throughput impacts.

Return EXACTLY this JSON structure (pure JSON only):
{{
    "congestion_score": <integer 0-100>,
    "congestion_level": "<low|moderate|high|severe|critical>",
    "average_wait_days": <number>,
    "berth_availability": "<good|limited|poor|critical>",
    "reason": "<1-2 sentence reason for current congestion level>",
    "trend": "<improving|stable|worsening>",
    "data_source_note": "<brief note about where the data came from>"
}}

Return ONLY the JSON object. No explanation text. No markdown fences. No preamble. No postamble."""


def _get_port_congestion_from_gemini(
    port_code: str,
    app_context,
    organisation_id,
    force_regenerate: bool = False,
    user_id=None,
):
    app = _get_app(app_context)
    port_name = _get_port_name(port_code)
    date_key = datetime.utcnow().strftime("%Y%m%d")
    content_key = f"port_{port_code}_{date_key}"

    def _prompt_builder() -> str:
        return _build_port_congestion_prompt(
            port_code=port_code,
            port_name=port_name,
            current_date=datetime.utcnow().strftime("%Y-%m-%d"),
        )

    def _fallback() -> str:
        return f"Real-time congestion feed unavailable for {port_name} ({port_code}); baseline congestion applied."

    ttl = int(app.config.get("AI_CACHE_TTL_PORT_CONGESTION_ANALYSIS", 3600) or 3600)
    result = ai_service.get_or_generate_ai_content(
        organisation_id=organisation_id,
        content_type="port_congestion_analysis",
        content_key=content_key,
        prompt_builder_fn=_prompt_builder,
        db_session=db.session,
        app_context=app,
        force_regenerate=bool(force_regenerate),
        use_web_search=True,
        expected_format="json",
        expires_in_seconds=ttl,
        user_id=user_id,
        expected_schema=ai_service.PORT_CONGESTION_SCHEMA,
        fallback_builder_fn=_fallback,
    )

    baseline = _safe_float(PORT_BASELINE_CONGESTION.get(port_code), 45.0)
    if result.get("fallback"):
        structured = {
            "congestion_score": int(round(baseline)),
            "congestion_level": "moderate" if baseline >= 35 else "low",
            "average_wait_days": round(max(0.2, baseline / 20.0), 1),
            "berth_availability": "limited" if baseline >= 40 else "good",
            "reason": _fallback(),
            "trend": "stable",
            "data_source_note": "Fallback baseline due to temporary AI unavailability",
            "parse_error": True,
        }
        markdown_text = ai_service.build_structured_markdown("port_congestion_analysis", structured)
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
        "port_congestion_analysis",
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


def get_port_congestion_score(
    port_code,
    app_context,
    organisation_id=None,
    force_regenerate: bool = False,
    user_id=None,
):
    """Return congestion score (0-100) using baseline + Gemini dynamic enrichment."""

    code = (port_code or "").strip().upper()
    app = _get_app(app_context)
    org_id = _resolve_organisation_id(organisation_id)

    baseline = _safe_float(PORT_BASELINE_CONGESTION.get(code), 45.0)

    if org_id is None:
        return float(round(baseline, 2))

    dynamic_score = baseline
    try:
        payload = _get_port_congestion_from_gemini(
            code,
            app,
            organisation_id=org_id,
            force_regenerate=force_regenerate,
            user_id=user_id,
        )
        structured = payload.get("structured_data") or {}
        dynamic_score = _safe_float(structured.get("congestion_score"), baseline)
    except Exception:
        logger.exception("Gemini congestion enrichment failed for port=%s", code)

    final_score = max(0.0, min(100.0, (baseline * 0.40) + (dynamic_score * 0.60)))

    return float(round(final_score, 2))


def get_customs_risk_score(port_code, mode, app_context):
    """Return customs processing risk score (0-100) for port and transport mode."""

    code = (port_code or "").strip().upper()
    mode_norm = (mode or "").strip().lower()

    baseline = _safe_float(PORT_CUSTOMS_BASELINE.get(code), 45.0)

    if mode_norm == "air":
        baseline *= 0.6

    if mode_norm == "road" and code in LAND_BORDER_CROSSINGS:
        baseline *= 1.3

    score = max(0.0, min(100.0, baseline))

    return float(round(score, 2))


def _score_to_level(score: float) -> str:
    if score >= 80:
        return "critical"
    if score >= 60:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


def get_port_congestion_zones(
    app_context,
    organisation_id=None,
    port_codes: list[str] | None = None,
    use_ai: bool = False,
    force_regenerate: bool = False,
    user_id=None,
):
    """Return monitored port congestion points for map overlays.

    By default this returns fast baseline values. Enable ``use_ai`` for targeted
    real-time enrichment on a limited set of ports.
    """

    app = _get_app(app_context)
    org_id = _resolve_organisation_id(organisation_id)
    selected_codes = port_codes if port_codes else sorted(PORT_BASELINE_CONGESTION.keys())
    seen: set[str] = set()

    zones: list[dict[str, Any]] = []
    for raw_code in selected_codes:
        port_code = (raw_code or "").strip().upper()
        if not port_code or port_code in seen:
            continue

        seen.add(port_code)
        coords = PORT_COORDINATES.get(port_code)
        if not coords:
            continue

        score = _safe_float(PORT_BASELINE_CONGESTION.get(port_code), 45.0)
        if use_ai and org_id is not None:
            score = get_port_congestion_score(
                port_code,
                app,
                organisation_id=org_id,
                force_regenerate=force_regenerate,
                user_id=user_id,
            )

        zones.append(
            {
                "port_code": port_code,
                "port_name": _get_port_name(port_code),
                "latitude": coords[0],
                "longitude": coords[1],
                "congestion_score": float(round(score, 2)),
                "congestion_level": _score_to_level(score),
            }
        )

    return zones
