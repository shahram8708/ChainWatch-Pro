"""Alert generation and statistics service for disruption intelligence."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from sqlalchemy import and_, func

from app.extensions import db
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.services.external_data import news_monitor_service

logger = logging.getLogger(__name__)

SEVERITY_PRIORITY = {
    "critical": 4,
    "warning": 3,
    "watch": 2,
    "info": 1,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _drs_level(score: float) -> str:
    if score >= 81:
        return "critical"
    if score >= 61:
        return "warning"
    if score >= 31:
        return "watch"
    return "green"


def _has_recent_unacknowledged_alert(
    shipment_id,
    alert_type: str,
    db_session,
    within_hours: int | None = 6,
) -> bool:
    query = db_session.query(Alert.id).filter(
        Alert.shipment_id == shipment_id,
        Alert.alert_type == alert_type,
        Alert.is_acknowledged.is_(False),
    )
    if within_hours is not None:
        cutoff = datetime.utcnow() - timedelta(hours=within_hours)
        query = query.filter(Alert.created_at >= cutoff)

    exists = query.first()
    return exists is not None


def classify_alert_severity(alert_type, score):
    """Map alert type and trigger score to alert severity."""

    alert_type_norm = (alert_type or "").strip().lower()
    value = _safe_float(score, 0.0)

    if alert_type_norm == "sla_breach_imminent":
        return "critical"

    if alert_type_norm == "drs_threshold":
        if value >= 81:
            return "critical"
        if value >= 61:
            return "warning"
        if value >= 31:
            return "watch"
        return "info"

    if alert_type_norm == "weather_event":
        if value >= 85:
            return "critical"
        if value >= 70:
            return "warning"
        return "watch"

    if alert_type_norm == "port_congestion":
        if value >= 80:
            return "warning"
        if value >= 65:
            return "watch"
        return "info"

    if alert_type_norm == "geopolitical_event":
        if value >= 80:
            return "critical"
        if value >= 60:
            return "warning"
        return "watch"

    if alert_type_norm == "carrier_delay_pattern":
        if value < 25:
            return "warning"
        if value < 40:
            return "watch"
        return "info"

    return "info"


def _build_title(alert_type: str, severity: str, shipment, ehs_signals: dict[str, Any]) -> str:
    if alert_type == "drs_threshold":
        return f"DRS Threshold Crossed: {severity.title()} Level"

    if alert_type == "weather_event":
        return f"Severe Weather Risk on Route {shipment.origin_port_code} -> {shipment.destination_port_code}"

    if alert_type == "port_congestion":
        return f"Port Congestion Detected at {shipment.destination_port_code}"

    if alert_type == "geopolitical_event":
        return "Geopolitical Risk on Route"

    if alert_type == "sla_breach_imminent":
        return "SLA Breach Imminent"

    if alert_type == "carrier_delay_pattern":
        return "Carrier Delay Pattern Detected"

    if alert_type == "route_alternatives_ready":
        return "Route Alternatives Ready for Review"

    return "Operational Alert"


def _queue_notification_task(alert_id: str) -> None:
    try:
        from celery_worker import send_notifications

        send_notifications.delay(alert_id)
    except Exception:
        logger.exception("Failed to queue send_notifications for alert_id=%s", alert_id)


def _create_alert(
    shipment,
    alert_type: str,
    trigger_score: float,
    new_drs: float,
    ehs_signals: dict[str, Any],
    db_session,
    app_context,
) -> Alert:
    severity = classify_alert_severity(alert_type, trigger_score)
    title = _build_title(alert_type, severity, shipment, ehs_signals)
    description = (
        f"{alert_type.replace('_', ' ').title()} detected on route {shipment.origin_port_code} "
        f"-> {shipment.destination_port_code}. Current DRS is {round(float(new_drs), 1)}. "
        "Review rerouting and operational mitigation actions."
    )

    alert = Alert(
        organisation_id=shipment.organisation_id,
        shipment_id=shipment.id,
        alert_type=alert_type,
        severity=severity,
        title=title,
        description=description,
        drs_at_alert=round(new_drs, 2),
        is_acknowledged=False,
        created_at=datetime.utcnow(),
    )
    db_session.add(alert)
    db_session.flush()

    ai_payload = news_monitor_service.generate_alert_description_with_gemini(
        alert_type,
        shipment,
        new_drs,
        ehs_signals,
        app_context,
        alert_id=alert.id,
        force_regenerate=False,
        user_id=None,
    )
    structured = ai_payload.get("structured_data") if isinstance(ai_payload, dict) else None
    if isinstance(structured, dict):
        enriched_title = (structured.get("enriched_title") or "").strip()
        full_description = (structured.get("full_description") or "").strip()
        if enriched_title:
            alert.title = enriched_title[:80]
        if full_description:
            alert.description = full_description

    db_session.commit()
    return alert


def generate_alerts_for_shipment(shipment, new_drs, previous_drs, ehs_signals, db_session, app_context):
    """Generate alert records for threshold crossings and hazard signals."""

    generated_alerts: list[Alert] = []
    ehs_signals = ehs_signals or {}

    new_drs_val = _safe_float(new_drs, 0.0)
    previous_drs_val = _safe_float(previous_drs, 0.0)

    previous_level = _drs_level(previous_drs_val)
    new_level = _drs_level(new_drs_val)

    threshold_crossings: list[tuple[str, float]] = []
    if previous_drs_val < 31 <= new_drs_val:
        threshold_crossings.append(("watch", 31.0))
    if previous_drs_val < 61 <= new_drs_val:
        threshold_crossings.append(("warning", 61.0))
    if previous_drs_val < 81 <= new_drs_val:
        threshold_crossings.append(("critical", 81.0))

    for _, threshold_score in threshold_crossings:
        generated_alerts.append(
            _create_alert(
                shipment,
                "drs_threshold",
                threshold_score,
                new_drs_val,
                ehs_signals,
                db_session,
                app_context,
            )
        )

    if threshold_crossings:
        AuditLog.log(
            db,
            event_type="drs_threshold_crossed",
            description=(
                f"DRS threshold crossed for shipment {shipment.external_reference}: "
                f"{previous_drs_val:.2f} -> {new_drs_val:.2f}."
            ),
            organisation_id=shipment.organisation_id,
            shipment_id=shipment.id,
            metadata={
                "previous_drs": round(previous_drs_val, 2),
                "new_drs": round(new_drs_val, 2),
                "previous_level": previous_level,
                "new_level": new_level,
            },
        )

    weather_score = _safe_float(ehs_signals.get("weather_score"), 0.0)
    if weather_score >= 70 and not _has_recent_unacknowledged_alert(
        shipment.id,
        "weather_event",
        db_session,
        within_hours=6,
    ):
        generated_alerts.append(
            _create_alert(
                shipment,
                "weather_event",
                weather_score,
                new_drs_val,
                ehs_signals,
                db_session,
                app_context,
            )
        )

    congestion_score = _safe_float(ehs_signals.get("port_congestion_score"), 0.0)
    if congestion_score >= 65 and not _has_recent_unacknowledged_alert(
        shipment.id,
        "port_congestion",
        db_session,
        within_hours=6,
    ):
        generated_alerts.append(
            _create_alert(
                shipment,
                "port_congestion",
                congestion_score,
                new_drs_val,
                ehs_signals,
                db_session,
                app_context,
            )
        )

    event_score = _safe_float(ehs_signals.get("event_score"), 0.0)
    if event_score >= 60 and not _has_recent_unacknowledged_alert(
        shipment.id,
        "geopolitical_event",
        db_session,
        within_hours=6,
    ):
        generated_alerts.append(
            _create_alert(
                shipment,
                "geopolitical_event",
                event_score,
                new_drs_val,
                ehs_signals,
                db_session,
                app_context,
            )
        )

    sla_probability = _safe_float(shipment.sla_breach_probability, 0.0)
    if sla_probability >= 0.75 and not _has_recent_unacknowledged_alert(
        shipment.id,
        "sla_breach_imminent",
        db_session,
        within_hours=None,
    ):
        generated_alerts.append(
            _create_alert(
                shipment,
                "sla_breach_imminent",
                sla_probability * 100.0,
                new_drs_val,
                ehs_signals,
                db_session,
                app_context,
            )
        )

    crs_score = _safe_float(ehs_signals.get("crs_score"), 100.0)
    if crs_score < 40 and not _has_recent_unacknowledged_alert(
        shipment.id,
        "carrier_delay_pattern",
        db_session,
        within_hours=12,
    ):
        generated_alerts.append(
            _create_alert(
                shipment,
                "carrier_delay_pattern",
                crs_score,
                new_drs_val,
                ehs_signals,
                db_session,
                app_context,
            )
        )

    if generated_alerts:
        most_severe_alert = sorted(
            generated_alerts,
            key=lambda item: SEVERITY_PRIORITY.get(item.severity, 0),
            reverse=True,
        )[0]
        AuditLog.log(
            db,
            event_type="disruption_detected",
            description=(
                f"Disruption detected for shipment {shipment.external_reference} "
                f"with severity {most_severe_alert.severity}."
            ),
            organisation_id=shipment.organisation_id,
            shipment_id=shipment.id,
            alert_id=most_severe_alert.id,
            metadata={
                "alert_type": most_severe_alert.alert_type,
                "severity": most_severe_alert.severity,
                "drs_total": round(new_drs_val, 2),
                "ehs_signals_summary": {
                    "weather_score": weather_score,
                    "port_congestion_score": congestion_score,
                    "customs_score": _safe_float(ehs_signals.get("customs_score"), 0.0),
                    "event_score": event_score,
                    "crs_score": crs_score,
                },
            },
        )

        for alert in generated_alerts:
            _queue_notification_task(str(alert.id))

    return generated_alerts


def get_alert_stats(organisation_id, db_session):
    """Return aggregate alert statistics for dashboard/API panels."""

    now = datetime.utcnow()
    one_hour_ago = now - timedelta(hours=1)
    start_of_day = datetime(now.year, now.month, now.day)

    new_last_hour = (
        db_session.query(func.count(Alert.id))
        .filter(
            Alert.organisation_id == organisation_id,
            Alert.created_at >= one_hour_ago,
        )
        .scalar()
        or 0
    )

    acknowledged_today = (
        db_session.query(func.count(Alert.id))
        .filter(
            Alert.organisation_id == organisation_id,
            Alert.is_acknowledged.is_(True),
            Alert.acknowledged_at >= start_of_day,
        )
        .scalar()
        or 0
    )

    total_active = (
        db_session.query(func.count(Alert.id))
        .filter(
            Alert.organisation_id == organisation_id,
            Alert.is_acknowledged.is_(False),
        )
        .scalar()
        or 0
    )

    resolution_rows = (
        db_session.query(Alert.created_at, Alert.acknowledged_at)
        .filter(
            Alert.organisation_id == organisation_id,
            Alert.is_acknowledged.is_(True),
            Alert.acknowledged_at.isnot(None),
            Alert.created_at.isnot(None),
        )
        .all()
    )

    resolution_hours = [
        (ack - created).total_seconds() / 3600.0
        for created, ack in resolution_rows
        if created and ack and ack >= created
    ]

    avg_resolution_hours = round(mean(resolution_hours), 2) if resolution_hours else 0.0

    return {
        "new_last_hour": int(new_last_hour),
        "acknowledged_today": int(acknowledged_today),
        "avg_resolution_hours": float(avg_resolution_hours),
        "total_active": int(total_active),
    }
