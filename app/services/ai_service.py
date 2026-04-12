"""Centralized Gemini calling, cache orchestration, and response formatting."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any, Callable

import markdown
from flask import current_app
from google import genai
from google.genai import types
from markdownify import markdownify
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.models.ai_generated_content import AIGeneratedContent

logger = logging.getLogger(__name__)

MODEL_NAME = "gemini-2.5-flash"
STALE_CONTENT_WARNING = "⚠ Cached content — AI service temporarily unavailable"

CARRIER_COMMENTARY_SCHEMA = {
    "overall_trend_sentence": "string",
    "strength_or_concern_sentence": "string",
    "forward_looking_sentence": "string",
    "otd_assessment": "string",
    "confidence_level": "string",
    "risk_flags": [],
    "recommended_action": "string",
}

SIMULATION_NARRATIVE_SCHEMA = {
    "risk_assessment_paragraph": "string",
    "recommendation_paragraph": "string",
    "recommendation_level": "string",
    "top_risk_summary": "string",
    "suggested_alternatives": [],
    "monitoring_frequency": "string",
}

ROUTE_EVENT_RISK_SCHEMA = {
    "event_score": "integer",
    "event_description": "string",
    "active_events": [],
    "search_performed": True,
    "data_freshness": "string",
    "recommended_monitoring": "string",
}

ALERT_DESCRIPTION_SCHEMA = {
    "enriched_title": "string",
    "cause_sentence": "string",
    "impact_sentence": "string",
    "action_sentence": "string",
    "full_description": "string",
    "severity_justification": "string",
    "recommended_action_code": "string",
}

SHIPMENT_DISRUPTION_SUMMARY_SCHEMA = {
    "disruption_sentence": "string",
    "impact_sentence": "string",
    "urgency_sentence": "string",
    "disruption_type": "string",
    "urgency_level": "string",
    "sla_at_risk": True,
    "days_until_critical": 0,
}

EXECUTIVE_BRIEF_SCHEMA = {
    "fleet_status_paragraph": "string",
    "risk_areas": [],
    "operations_recommendations": [],
    "overall_health_assessment": "string",
    "week_summary_headline": "string",
}

PORT_CONGESTION_SCHEMA = {
    "congestion_score": "integer",
    "congestion_level": "string",
    "average_wait_days": 0,
    "berth_availability": "string",
    "reason": "string",
    "trend": "string",
    "data_source_note": "string",
}


class GeminiCallError(Exception):
    """Raised when a Gemini API call fails after retry handling."""


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def _set_env_api_key(app) -> None:
    api_key = app.config.get("GEMINI_API_KEY")
    if api_key:
        os.environ["GEMINI_API_KEY"] = api_key


def _extract_tokens_used(response: Any) -> int | None:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return None

    for attr in [
        "total_token_count",
        "total_tokens",
        "total_tokens_count",
    ]:
        value = getattr(usage, attr, None)
        if isinstance(value, int):
            return value

    prompt_tokens = getattr(usage, "prompt_token_count", None)
    candidate_tokens = getattr(usage, "candidates_token_count", None)
    if isinstance(prompt_tokens, int) and isinstance(candidate_tokens, int):
        return prompt_tokens + candidate_tokens

    return None


def call_gemini_single(prompt: str, use_web_search: bool = False, app_context=None) -> str:
    """
    Single entry point for ALL Gemini API calls in the application.
    This function is the only place that instantiates the Gemini client
    and calls generate_content().
    """

    app = _get_app(app_context)
    _set_env_api_key(app)

    call_gemini_single.last_metadata = {
        "model_used": MODEL_NAME,
        "tokens_used": None,
    }

    try:
        client = genai.Client()

        if use_web_search:
            config = types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())]
            )
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config=config,
            )
        else:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
            )

        call_gemini_single.last_metadata = {
            "model_used": MODEL_NAME,
            "tokens_used": _extract_tokens_used(response),
        }
        return (response.text or "").strip()
    except Exception as exc:
        logger.error("Gemini API call failed: %s", str(exc), exc_info=True)
        raise GeminiCallError(str(exc)) from exc


call_gemini_single.last_metadata = {
    "model_used": MODEL_NAME,
    "tokens_used": None,
}


def format_gemini_text_response(raw_text: str) -> str:
    """Convert raw Gemini text into cleaner markdown."""

    if not raw_text:
        return ""

    text = raw_text.strip()

    if re.search(r"<[^>]+>", text):
        text = markdownify(text)

    text = re.sub(r"^[•●◦▪]\s+", "- ", text, flags=re.MULTILINE)
    text = re.sub(r"^(\d+)[.):]\s+", r"\1. ", text, flags=re.MULTILINE)
    text = re.sub(r"^([A-Z][^.\n]{3,50}):$", r"## \1", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)

    markdown.markdown(text, extensions=["extra", "nl2br"])
    return text


def render_markdown_to_html(markdown_text: str) -> str:
    """Render markdown text to HTML for safe template display."""

    if not markdown_text:
        return ""

    return markdown.markdown(
        markdown_text,
        extensions=["extra", "nl2br", "tables", "fenced_code"],
    )


def _attempt_json_repair(text: str) -> str:
    """Attempt lightweight JSON repair for known Gemini output issues."""

    text = re.sub(r",\s*([}\]])", r"\1", text)
    text = re.sub(r"(?<![\\])'([^']*)'(?=\s*:)", r'"\1"', text)
    text = re.sub(r"(\{|,)\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:", r'\1 "\2":', text)
    return text


def _validate_schema(result: Any, expected_schema: dict | None) -> Any:
    """Warn for missing expected top-level keys and return parsed result."""

    if expected_schema and isinstance(result, dict):
        missing_keys = [key for key in expected_schema.keys() if key not in result]
        if missing_keys:
            logger.warning("Parsed JSON missing expected keys: %s", missing_keys)
    return result


def parse_gemini_json_response(raw_text: str, expected_schema: dict | None = None) -> Any:
    """Parse JSON output robustly from Gemini responses."""

    if not raw_text:
        return {
            "parse_error": True,
            "raw": "",
            "reason": "empty_response",
        }

    text = raw_text.strip()

    try:
        result = json.loads(text)
        return _validate_schema(result, expected_schema)
    except json.JSONDecodeError:
        pass

    json_fence_pattern = r"```(?:json)?\s*\n([\s\S]*?)\n```"
    fence_match = re.search(json_fence_pattern, text, re.IGNORECASE)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            return _validate_schema(result, expected_schema)
        except json.JSONDecodeError:
            pass

    json_object_pattern = r"\{[\s\S]*\}"
    json_array_pattern = r"\[[\s\S]*\]"

    for pattern in [json_object_pattern, json_array_pattern]:
        matches = re.findall(pattern, text)
        matches.sort(key=len, reverse=True)
        for match in matches:
            try:
                result = json.loads(match)
                return _validate_schema(result, expected_schema)
            except json.JSONDecodeError:
                fixed = _attempt_json_repair(match)
                if fixed:
                    try:
                        result = json.loads(fixed)
                        return _validate_schema(result, expected_schema)
                    except json.JSONDecodeError:
                        continue

    logger.error("Failed to parse Gemini JSON response. Raw text (first 500 chars): %s", text[:500])
    return {
        "parse_error": True,
        "raw": raw_text,
        "reason": "all_strategies_failed",
    }


def _get_cache_record(organisation_id, content_type: str, content_key: str, db_session):
    """Load cache row without freshness checks."""

    return (
        db_session.query(AIGeneratedContent)
        .filter(
            AIGeneratedContent.organisation_id == organisation_id,
            AIGeneratedContent.content_type == content_type,
            AIGeneratedContent.content_key == content_key,
        )
        .first()
    )


def get_cached_ai_content(organisation_id, content_type: str, content_key: str, db_session):
    """Return a valid non-stale, non-expired cache record or None."""

    try:
        record = _get_cache_record(organisation_id, content_type, content_key, db_session)
        if record is None:
            return None

        if bool(record.is_stale):
            return None

        if record.expires_at is not None and record.expires_at < datetime.utcnow():
            return None

        return record
    except Exception:
        logger.exception(
            "Failed cache lookup for content_type=%s content_key=%s organisation_id=%s",
            content_type,
            content_key,
            organisation_id,
        )
        return None


def _record_to_payload(record: AIGeneratedContent | None) -> dict[str, Any] | None:
    if record is None:
        return None
    return record.to_dict()


def _upsert_statement(db_session, values: dict[str, Any], user_id):
    bind = None
    try:
        # Flask-SQLAlchemy/SQLAlchemy can defer session.bind until a mapped bind is resolved.
        bind = db_session.get_bind(mapper=AIGeneratedContent)
    except TypeError:
        try:
            bind = db_session.get_bind()
        except Exception:
            bind = None
    except Exception:
        bind = getattr(db_session, "bind", None)

    dialect_name = ""
    if bind is not None and getattr(bind, "dialect", None) is not None:
        dialect_name = (bind.dialect.name or "").lower()

    regeneration_base = AIGeneratedContent.regeneration_count
    if user_id is not None:
        regeneration_expr = regeneration_base + 1
        last_regenerated_expr = user_id
    else:
        regeneration_expr = regeneration_base
        last_regenerated_expr = AIGeneratedContent.last_regenerated_by

    set_values = {
        "raw_response": values["raw_response"],
        "formatted_response": values["formatted_response"],
        "structured_data": values["structured_data"],
        "response_format": values["response_format"],
        "prompt_used": values["prompt_used"],
        "model_used": values["model_used"],
        "tokens_used": values["tokens_used"],
        "generation_duration_ms": values["generation_duration_ms"],
        "expires_at": values["expires_at"],
        "is_stale": False,
        "updated_at": values["updated_at"],
        "regeneration_count": regeneration_expr,
        "last_regenerated_by": last_regenerated_expr,
    }

    if dialect_name == "postgresql":
        stmt = pg_insert(AIGeneratedContent).values(**values)
        return stmt.on_conflict_do_update(
            index_elements=["organisation_id", "content_type", "content_key"],
            set_=set_values,
        )

    if dialect_name == "sqlite":
        stmt = sqlite_insert(AIGeneratedContent).values(**values)
        return stmt.on_conflict_do_update(
            index_elements=["organisation_id", "content_type", "content_key"],
            set_=set_values,
        )

    return None


def save_ai_content(
    organisation_id,
    content_type: str,
    content_key: str,
    raw_response: str,
    formatted_response: str | None,
    structured_data: Any,
    response_format: str,
    prompt_used: str,
    generation_duration_ms: int | None,
    db_session,
    user_id=None,
    expires_at: datetime | None = None,
    tokens_used: int | None = None,
    model_used: str = MODEL_NAME,
):
    """Persist AI content using atomic upsert and return the saved row."""

    now = datetime.utcnow()
    values = {
        "id": None,
        "organisation_id": organisation_id,
        "content_type": content_type,
        "content_key": content_key,
        "raw_response": raw_response,
        "formatted_response": formatted_response,
        "structured_data": structured_data,
        "response_format": response_format,
        "prompt_used": prompt_used,
        "model_used": model_used,
        "tokens_used": tokens_used,
        "generation_duration_ms": generation_duration_ms,
        "is_stale": False,
        "regeneration_count": 1 if user_id is not None else 0,
        "last_regenerated_by": user_id,
        "created_at": now,
        "updated_at": now,
        "expires_at": expires_at,
    }

    values.pop("id")

    try:
        stmt = _upsert_statement(db_session, values, user_id)
        if stmt is not None:
            db_session.execute(stmt)
        else:
            existing = _get_cache_record(organisation_id, content_type, content_key, db_session)
            if existing is None:
                existing = AIGeneratedContent(**values)
                db_session.add(existing)
            else:
                existing.raw_response = raw_response
                existing.formatted_response = formatted_response
                existing.structured_data = structured_data
                existing.response_format = response_format
                existing.prompt_used = prompt_used
                existing.model_used = model_used
                existing.tokens_used = tokens_used
                existing.generation_duration_ms = generation_duration_ms
                existing.expires_at = expires_at
                existing.is_stale = False
                existing.updated_at = now
                if user_id is not None:
                    existing.regeneration_count = int(existing.regeneration_count or 0) + 1
                    existing.last_regenerated_by = user_id

        db_session.commit()
        return _get_cache_record(organisation_id, content_type, content_key, db_session)
    except Exception:
        db_session.rollback()
        logger.exception(
            "Failed to save AI content content_type=%s content_key=%s organisation_id=%s",
            content_type,
            content_key,
            organisation_id,
        )
        raise


def invalidate_ai_content(organisation_id, content_type: str, content_key: str, db_session) -> bool:
    """Mark one cache record stale without deleting previous generated content."""

    try:
        updated = (
            db_session.query(AIGeneratedContent)
            .filter(
                AIGeneratedContent.organisation_id == organisation_id,
                AIGeneratedContent.content_type == content_type,
                AIGeneratedContent.content_key == content_key,
            )
            .update(
                {
                    AIGeneratedContent.is_stale: True,
                    AIGeneratedContent.updated_at: datetime.utcnow(),
                },
                synchronize_session=False,
            )
        )
        db_session.commit()
        return bool(updated)
    except Exception:
        db_session.rollback()
        logger.exception(
            "Failed to invalidate AI content content_type=%s content_key=%s organisation_id=%s",
            content_type,
            content_key,
            organisation_id,
        )
        return False


def _json_to_markdown(content_type: str, structured_data: Any) -> str:
    """Build user-facing markdown snippets from structured JSON AI payloads."""

    if not isinstance(structured_data, dict):
        return format_gemini_text_response(str(structured_data or ""))

    if content_type == "carrier_commentary":
        risk_flags = structured_data.get("risk_flags") or []
        flags_md = "\n".join(f"- {item}" for item in risk_flags) if risk_flags else "- None"
        return (
            f"{structured_data.get('overall_trend_sentence', '')}\n\n"
            f"{structured_data.get('strength_or_concern_sentence', '')}\n\n"
            f"{structured_data.get('forward_looking_sentence', '')}\n\n"
            f"- OTD assessment: **{structured_data.get('otd_assessment', 'unknown')}**\n"
            f"- Confidence: **{structured_data.get('confidence_level', 'unknown')}**\n"
            f"- Recommended action: **{structured_data.get('recommended_action', 'monitor')}**\n"
            f"- Risk flags:\n{flags_md}"
        ).strip()

    if content_type == "simulation_narrative":
        alternatives = structured_data.get("suggested_alternatives") or []
        alternatives_md = "\n".join(f"- {item}" for item in alternatives) if alternatives else "- None"
        return (
            f"{structured_data.get('risk_assessment_paragraph', '')}\n\n"
            f"{structured_data.get('recommendation_paragraph', '')}\n\n"
            f"- Recommendation level: **{structured_data.get('recommendation_level', 'amber')}**\n"
            f"- Top risk: {structured_data.get('top_risk_summary', '')}\n"
            f"- Monitoring frequency: **{structured_data.get('monitoring_frequency', 'daily')}**\n"
            f"- Suggested alternatives:\n{alternatives_md}"
        ).strip()

    if content_type == "route_event_risk":
        events = structured_data.get("active_events") or []
        if events:
            events_md = "\n".join(
                (
                    f"- **{event.get('event_type', 'none')}** ({event.get('severity', 'low')}) at "
                    f"{event.get('location', 'unknown')}: {event.get('description', '')} "
                    f"(Duration: {event.get('estimated_duration', 'unknown')})"
                )
                for event in events
                if isinstance(event, dict)
            )
        else:
            events_md = "- None"

        return (
            f"{structured_data.get('event_description', '')}\n\n"
            f"- Event score: **{structured_data.get('event_score', 0)} / 100**\n"
            f"- Data freshness: **{structured_data.get('data_freshness', 'recent')}**\n"
            f"- Recommended monitoring: **{structured_data.get('recommended_monitoring', 'standard')}**\n"
            f"- Active events:\n{events_md}"
        ).strip()

    if content_type == "alert_description":
        return (
            f"{structured_data.get('full_description', '')}\n\n"
            f"- Severity rationale: {structured_data.get('severity_justification', '')}\n"
            f"- Recommended action code: **{structured_data.get('recommended_action_code', 'monitor')}**"
        ).strip()

    if content_type == "shipment_disruption_summary":
        days_until = structured_data.get("days_until_critical")
        days_until_text = "unknown" if days_until is None else str(days_until)
        return (
            f"{structured_data.get('disruption_sentence', '')}\n\n"
            f"{structured_data.get('impact_sentence', '')}\n\n"
            f"{structured_data.get('urgency_sentence', '')}\n\n"
            f"- Disruption type: **{structured_data.get('disruption_type', 'none')}**\n"
            f"- Urgency level: **{structured_data.get('urgency_level', 'monitoring')}**\n"
            f"- SLA at risk: **{'Yes' if structured_data.get('sla_at_risk') else 'No'}**\n"
            f"- Days until critical: **{days_until_text}**"
        ).strip()

    if content_type == "executive_brief":
        risk_areas = structured_data.get("risk_areas") or []
        risks_md = "\n".join(
            (
                f"- {item.get('rank', '?')}. **{item.get('area', 'Unknown')}**: {item.get('description', '')} "
                f"(Action: {item.get('recommended_action', '')})"
            )
            for item in risk_areas
            if isinstance(item, dict)
        ) or "- None"
        ops = structured_data.get("operations_recommendations") or []
        ops_md = "\n".join(f"- {item}" for item in ops) if ops else "- None"
        return (
            f"## {structured_data.get('week_summary_headline', 'Weekly Executive Brief')}\n\n"
            f"{structured_data.get('fleet_status_paragraph', '')}\n\n"
            f"- Overall health: **{structured_data.get('overall_health_assessment', 'stable')}**\n"
            f"- Top risk areas:\n{risks_md}\n"
            f"- Operations recommendations:\n{ops_md}"
        ).strip()

    if content_type == "port_congestion_analysis":
        return (
            f"{structured_data.get('reason', '')}\n\n"
            f"- Congestion score: **{structured_data.get('congestion_score', 0)} / 100**\n"
            f"- Congestion level: **{structured_data.get('congestion_level', 'moderate')}**\n"
            f"- Average wait: **{structured_data.get('average_wait_days', 0)} days**\n"
            f"- Berth availability: **{structured_data.get('berth_availability', 'limited')}**\n"
            f"- Trend: **{structured_data.get('trend', 'stable')}**\n"
            f"- Data source: {structured_data.get('data_source_note', '')}"
        ).strip()

    return format_gemini_text_response(json.dumps(structured_data, indent=2, ensure_ascii=True))


def build_structured_markdown(content_type: str, structured_data: Any) -> str:
    """Public helper for rendering structured AI JSON into markdown."""

    return _json_to_markdown(content_type, structured_data)


def get_or_generate_ai_content(
    organisation_id,
    content_type: str,
    content_key: str,
    prompt_builder_fn: Callable[[], str],
    db_session,
    app_context,
    force_regenerate: bool = False,
    use_web_search: bool = False,
    expected_format: str = "markdown",
    expires_in_seconds: int | None = None,
    user_id=None,
    expected_schema: dict | None = None,
    fallback_builder_fn: Callable[[], str] | None = None,
):
    """Master cache-first orchestration for all AI feature generation."""

    cached_record = None
    if not force_regenerate:
        cached_record = get_cached_ai_content(organisation_id, content_type, content_key, db_session)
        if cached_record is not None:
            logger.info(
                "ai_content_request content_type=%s content_key=%s cache=hit duration_ms=0 user_triggered=%s",
                content_type,
                content_key,
                bool(user_id),
            )
            payload = _record_to_payload(cached_record) or {}
            payload.update(
                {
                    "success": True,
                    "cache_hit": True,
                    "served_stale": False,
                    "stale_warning": None,
                }
            )
            return payload

    stale_record = None
    try:
        stale_record = _get_cache_record(organisation_id, content_type, content_key, db_session)
    except Exception:
        stale_record = None

    logger.info(
        "ai_content_request content_type=%s content_key=%s cache=miss duration_ms=0 user_triggered=%s",
        content_type,
        content_key,
        bool(user_id),
    )

    prompt = prompt_builder_fn()
    start_time = time.time()

    try:
        raw_response = call_gemini_single(
            prompt,
            use_web_search=use_web_search,
            app_context=app_context,
        )
        generation_duration_ms = int((time.time() - start_time) * 1000)

        structured_data = None
        formatted_response = None
        response_format = expected_format

        if expected_format == "json":
            structured_data = parse_gemini_json_response(raw_response, expected_schema=expected_schema)
            formatted_response = _json_to_markdown(content_type, structured_data)
            response_format = "json"
        elif expected_format in {"markdown", "plain_text"}:
            formatted_response = format_gemini_text_response(raw_response)
            response_format = expected_format
        else:
            formatted_response = format_gemini_text_response(raw_response)
            response_format = "markdown"

        expires_at = None
        if expires_in_seconds is not None and int(expires_in_seconds) > 0:
            expires_at = datetime.utcnow() + timedelta(seconds=int(expires_in_seconds))

        meta = getattr(call_gemini_single, "last_metadata", {}) or {}
        saved = save_ai_content(
            organisation_id=organisation_id,
            content_type=content_type,
            content_key=content_key,
            raw_response=raw_response,
            formatted_response=formatted_response,
            structured_data=structured_data,
            response_format=response_format,
            prompt_used=prompt,
            generation_duration_ms=generation_duration_ms,
            db_session=db_session,
            user_id=user_id,
            expires_at=expires_at,
            tokens_used=meta.get("tokens_used"),
            model_used=meta.get("model_used") or MODEL_NAME,
        )

        logger.info(
            "ai_content_generated content_type=%s content_key=%s cache=miss duration_ms=%s user_triggered=%s",
            content_type,
            content_key,
            generation_duration_ms,
            bool(user_id),
        )

        payload = _record_to_payload(saved) or {}
        payload.update(
            {
                "success": True,
                "cache_hit": False,
                "served_stale": False,
                "stale_warning": None,
            }
        )
        return payload
    except GeminiCallError:
        if stale_record is not None:
            try:
                stale_record.is_stale = False
                stale_record.updated_at = datetime.utcnow()
                db_session.commit()
            except Exception:
                db_session.rollback()

            logger.warning(
                "Serving stale AI content after Gemini failure content_type=%s content_key=%s",
                content_type,
                content_key,
            )
            payload = _record_to_payload(stale_record) or {}
            payload.update(
                {
                    "success": True,
                    "cache_hit": True,
                    "served_stale": True,
                    "stale_warning": STALE_CONTENT_WARNING,
                }
            )
            return payload

        fallback_content = (
            fallback_builder_fn()
            if callable(fallback_builder_fn)
            else "AI output is temporarily unavailable. Please try regenerate shortly."
        )
        return {
            "success": False,
            "fallback": True,
            "content": fallback_content,
            "served_stale": False,
            "stale_warning": None,
            "content_type": content_type,
            "content_key": content_key,
        }


def generate_shipment_disruption_summary(
    shipment,
    latest_drs_record,
    app_context,
    force_regenerate: bool = False,
    user_id=None,
):
    """Generate or fetch shipment disruption summary from DB cache."""

    app = _get_app(app_context)
    from app.extensions import db

    content_key = f"shipment_{shipment.id}"

    drs_total = _safe_float(getattr(latest_drs_record, "drs_total", None), _safe_float(shipment.disruption_risk_score, 0.0))
    cargo_value = _safe_float(shipment.cargo_value_inr, 0.0)
    sla_probability = _safe_float(shipment.sla_breach_probability, 0.0)
    urgency_level = (
        "immediate"
        if drs_total >= 81
        else "high"
        if drs_total >= 61
        else "medium"
        if drs_total >= 31
        else "monitoring"
    )
    days_until_critical = 0 if drs_total >= 81 else max(1, int(round((81 - drs_total) / 8.0)))

    ehs_signals = getattr(latest_drs_record, "ehs_signals", None) or {}
    weather_score = _safe_float(ehs_signals.get("weather_score"), 50.0) if isinstance(ehs_signals, dict) else 50.0
    port_score = _safe_float(ehs_signals.get("port_congestion_score"), 50.0) if isinstance(ehs_signals, dict) else 50.0
    event_score = _safe_float(ehs_signals.get("event_score"), 50.0) if isinstance(ehs_signals, dict) else 50.0
    customs_score = _safe_float(ehs_signals.get("customs_score"), 50.0) if isinstance(ehs_signals, dict) else 50.0

    disruption_type = "combined"
    if max(weather_score, port_score, event_score, customs_score) <= 20:
        disruption_type = "none"
    elif weather_score >= max(port_score, event_score, customs_score):
        disruption_type = "weather"
    elif port_score >= max(weather_score, event_score, customs_score):
        disruption_type = "port_congestion"
    elif event_score >= max(weather_score, port_score, customs_score):
        disruption_type = "geopolitical"
    elif customs_score >= max(weather_score, port_score, event_score):
        disruption_type = "customs"

    def _build_prompt() -> str:
        return f"""You are a supply chain risk intelligence AI. Analyze this shipment and generate a strict JSON disruption summary.

SHIPMENT DATA:
- Shipment ID: {shipment.id}
- Route: {shipment.origin_port_code} -> {shipment.destination_port_code}
- Mode: {shipment.mode}
- Carrier: {shipment.carrier.name if shipment.carrier else 'Unassigned'}
- Current DRS Score: {drs_total:.1f}/100
- SLA Breach Probability: {sla_probability:.1f}%
- Cargo Value INR: {cargo_value:.2f}
- Risk Signals: weather={weather_score:.1f}, port_congestion={port_score:.1f}, event={event_score:.1f}, customs={customs_score:.1f}

Generate a JSON response with EXACTLY this structure (pure JSON only):
{{
    "disruption_sentence": "one sentence describing current or likely disruption",
    "impact_sentence": "one sentence describing shipment-specific SLA/cargo impact",
    "urgency_sentence": "one sentence describing urgency and immediate action",
    "disruption_type": "weather|port_congestion|carrier_delay|geopolitical|customs|combined|none",
    "urgency_level": "immediate|high|medium|low|monitoring",
    "sla_at_risk": true,
    "days_until_critical": 2
}}

Return ONLY the JSON object. No explanation text. No markdown fences. No preamble. No postamble."""

    def _fallback() -> str:
        return (
            f"Disruption risk is currently {drs_total:.1f}/100 on {shipment.origin_port_code} -> {shipment.destination_port_code}. "
            f"SLA breach probability is {sla_probability:.1f}% with cargo exposure of INR {cargo_value:.2f}. "
            f"Urgency level is {urgency_level}; begin immediate risk monitoring and lane contingency prep."
        )

    ttl = _safe_int(app.config.get("AI_CACHE_TTL_SHIPMENT_DISRUPTION_SUMMARY"), 900)
    result = get_or_generate_ai_content(
        organisation_id=shipment.organisation_id,
        content_type="shipment_disruption_summary",
        content_key=content_key,
        prompt_builder_fn=_build_prompt,
        db_session=db.session,
        app_context=app,
        force_regenerate=force_regenerate,
        use_web_search=False,
        expected_format="json",
        expires_in_seconds=ttl,
        user_id=user_id,
        expected_schema=SHIPMENT_DISRUPTION_SUMMARY_SCHEMA,
        fallback_builder_fn=_fallback,
    )

    if result.get("fallback"):
        structured = {
            "disruption_sentence": _fallback().split(". ")[0] + ".",
            "impact_sentence": _fallback().split(". ")[1] + "." if ". " in _fallback() else "",
            "urgency_sentence": _fallback().split(". ")[-1],
            "disruption_type": disruption_type,
            "urgency_level": urgency_level,
            "sla_at_risk": sla_probability >= 25.0,
            "days_until_critical": None if urgency_level == "monitoring" else days_until_critical,
            "parse_error": True,
        }
        markdown_text = _json_to_markdown("shipment_disruption_summary", structured)
        return {
            "success": False,
            "served_stale": False,
            "stale_warning": None,
            "structured_data": structured,
            "formatted_response": markdown_text,
            "formatted_html": render_markdown_to_html(markdown_text),
            "regeneration_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }

    structured_data = result.get("structured_data") or {}
    markdown_text = result.get("formatted_response") or _json_to_markdown("shipment_disruption_summary", structured_data)
    return {
        "success": True,
        "served_stale": bool(result.get("served_stale")),
        "stale_warning": result.get("stale_warning"),
        "structured_data": structured_data,
        "formatted_response": markdown_text,
        "formatted_html": render_markdown_to_html(markdown_text),
        "regeneration_count": _safe_int(result.get("regeneration_count"), 0),
        "generated_at": result.get("updated_at") or result.get("created_at"),
        "record": result,
    }


def generate_executive_ai_brief(
    organisation,
    fleet_stats,
    top_risks,
    app_context,
    force_regenerate: bool = False,
    user_id=None,
):
    """Generate or fetch cached executive AI brief (weekly key)."""

    app = _get_app(app_context)
    from app.extensions import db

    now = datetime.utcnow()
    iso = now.isocalendar()
    content_key = f"executive_{organisation.id}_week{iso.week}_{iso.year}"

    total_active = _safe_int(fleet_stats.get("total_active_shipments"), 0)
    critical_count = _safe_int(fleet_stats.get("critical_count"), 0)
    warning_count = _safe_int(fleet_stats.get("warning_count"), 0)
    average_drs = _safe_float(fleet_stats.get("average_drs"), 0.0)
    fleet_otd_rate = _safe_float(fleet_stats.get("fleet_otd_rate"), 0.0)
    reroutes_this_week = _safe_int(
        fleet_stats.get("reroutes_this_week", fleet_stats.get("rerouting_decisions_this_week", 0)),
        0,
    )
    reroute_savings = _safe_float(
        fleet_stats.get("reroute_savings_inr", fleet_stats.get("rerouting_savings_this_week_inr", 0.0)),
        0.0,
    )

    risk_lines = []
    for idx, risk in enumerate((top_risks or [])[:3], start=1):
        risk_lines.append(
            f"{idx}. {risk.get('label', 'Unknown area')} - score {float(risk.get('score', 0.0)):.1f}"
        )
    risk_lines_text = "\n".join(risk_lines) if risk_lines else "1. No acute risk area identified"

    def _build_prompt() -> str:
        return f"""You are a senior supply chain executive intelligence AI for ChainWatch Pro.

WEEKLY FLEET SNAPSHOT:
- Organisation: {organisation.name}
- Active Shipments: {total_active}
- Critical Shipments: {critical_count}
- Warning Shipments: {warning_count}
- Average DRS: {average_drs:.1f}
- Fleet OTD Rate: {fleet_otd_rate:.1f}%
- Reroutes This Week: {reroutes_this_week}
- Estimated Reroute Savings INR: {reroute_savings:.2f}

TOP RISK AREAS:
{risk_lines_text}

Generate a JSON response with EXACTLY this structure (pure JSON only):
{{
    "fleet_status_paragraph": "current fleet status summary paragraph",
    "risk_areas": [
        {{
            "rank": 1,
            "area": "lane/carrier/risk area",
            "description": "one sentence risk description",
            "recommended_action": "specific recommended action"
        }}
    ],
    "operations_recommendations": ["recommendation 1", "recommendation 2"],
    "overall_health_assessment": "healthy|stable|concerning|critical",
    "week_summary_headline": "headline max 12 words"
}}

Return ONLY the JSON object. No explanation text. No markdown fences. No preamble. No postamble."""

    def _fallback() -> str:
        return (
            f"Fleet status shows {total_active} active shipments with {critical_count} critical and {warning_count} warning exposures. "
            f"Average DRS is {average_drs:.1f} with fleet OTD at {fleet_otd_rate:.1f}%. "
            f"Run weekly mitigation review focused on top risk lanes and tighten reroute execution for SLA protection."
        )

    ttl = _safe_int(app.config.get("AI_CACHE_TTL_EXECUTIVE_BRIEF"), 43200)
    result = get_or_generate_ai_content(
        organisation_id=organisation.id,
        content_type="executive_brief",
        content_key=content_key,
        prompt_builder_fn=_build_prompt,
        db_session=db.session,
        app_context=app,
        force_regenerate=force_regenerate,
        use_web_search=False,
        expected_format="json",
        expires_in_seconds=ttl,
        user_id=user_id,
        expected_schema=EXECUTIVE_BRIEF_SCHEMA,
        fallback_builder_fn=_fallback,
    )

    if result.get("fallback"):
        structured = {
            "fleet_status_paragraph": _fallback(),
            "risk_areas": [],
            "operations_recommendations": [
                "Increase daily monitoring for high-risk lanes.",
                "Reduce reroute decision latency with operations war-room review.",
            ],
            "overall_health_assessment": "stable",
            "week_summary_headline": "Weekly risk posture requires proactive controls",
            "parse_error": True,
        }
        markdown_text = _json_to_markdown("executive_brief", structured)
        return {
            "success": False,
            "served_stale": False,
            "stale_warning": None,
            "structured_data": structured,
            "formatted_response": markdown_text,
            "formatted_html": render_markdown_to_html(markdown_text),
            "regeneration_count": 0,
            "generated_at": datetime.utcnow().isoformat(),
        }

    structured_data = result.get("structured_data") or {}
    markdown_text = result.get("formatted_response") or _json_to_markdown("executive_brief", structured_data)
    return {
        "success": True,
        "served_stale": bool(result.get("served_stale")),
        "stale_warning": result.get("stale_warning"),
        "structured_data": structured_data,
        "formatted_response": markdown_text,
        "formatted_html": render_markdown_to_html(markdown_text),
        "regeneration_count": _safe_int(result.get("regeneration_count"), 0),
        "generated_at": result.get("updated_at") or result.get("created_at"),
        "record": result,
    }


__all__ = [
    "GeminiCallError",
    "STALE_CONTENT_WARNING",
    "call_gemini_single",
    "format_gemini_text_response",
    "render_markdown_to_html",
    "build_structured_markdown",
    "parse_gemini_json_response",
    "get_cached_ai_content",
    "save_ai_content",
    "invalidate_ai_content",
    "get_or_generate_ai_content",
    "generate_shipment_disruption_summary",
    "generate_executive_ai_brief",
    "CARRIER_COMMENTARY_SCHEMA",
    "SIMULATION_NARRATIVE_SCHEMA",
    "ROUTE_EVENT_RISK_SCHEMA",
    "ALERT_DESCRIPTION_SCHEMA",
    "SHIPMENT_DISRUPTION_SUMMARY_SCHEMA",
    "EXECUTIVE_BRIEF_SCHEMA",
    "PORT_CONGESTION_SCHEMA",
]
