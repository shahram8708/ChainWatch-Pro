"""Celery worker entrypoint and background task orchestration for ChainWatch Pro."""

from __future__ import annotations

import logging
import os
import time
import uuid
import json
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

import redis
from celery import Celery, Task
from celery.schedules import crontab
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm.attributes import flag_modified

from app import create_app
from app.extensions import db, get_redis_client
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.disruption_score import DisruptionScore
from app.models.organisation import Organisation
from app.models.route_recommendation import RouteRecommendation
from app.models.shipment import Shipment
from app.models.user import User
from app.services import alert_service, carrier_tracker, disruption_engine, route_optimizer
from app.services import report_service
from app.services.external_data import news_monitor_service, port_data_service, weather_service
from app.services.notification import email_service, sms_service, webhook_service

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _is_celery_enabled(flask_app) -> bool:
    return bool(flask_app.config.get("CELERY_ENABLED", False))


def _get_active_organisations() -> list[Organisation]:
    return (
        Organisation.query.filter(Organisation.subscription_status.in_(["active", "trial"]))
        .order_by(Organisation.created_at.asc())
        .all()
    )


def _mode_to_performance_mode(mode: str | None) -> str:
    return disruption_engine._mode_to_performance_mode(mode)


def _interpolate_position(shipment: Shipment) -> tuple[float | None, float | None, str]:
    origin_code = (shipment.origin_port_code or "").upper().strip()
    destination_code = (shipment.destination_port_code or "").upper().strip()

    origin_coords = disruption_engine.PORT_COORDINATES.get(origin_code)
    destination_coords = disruption_engine.PORT_COORDINATES.get(destination_code)

    if not origin_coords or not destination_coords:
        return None, None, "Position unavailable"

    if not shipment.estimated_departure or not shipment.estimated_arrival:
        return origin_coords[0], origin_coords[1], f"Near {origin_code}"

    now = datetime.utcnow()
    departure = shipment.actual_departure or shipment.estimated_departure

    total_seconds = max((shipment.estimated_arrival - shipment.estimated_departure).total_seconds(), 1.0)
    elapsed_seconds = max((now - departure).total_seconds(), 0.0)
    progress = max(0.0, min(1.0, elapsed_seconds / total_seconds))

    lat = origin_coords[0] + ((destination_coords[0] - origin_coords[0]) * progress)
    lng = origin_coords[1] + ((destination_coords[1] - origin_coords[1]) * progress)

    if progress <= 0.02:
        location_name = f"Departing {origin_code}"
    elif progress >= 0.98:
        location_name = f"Approaching {destination_code}"
    else:
        location_name = f"In transit ({int(progress * 100)}% route completion)"

    return lat, lng, location_name


def make_celery(flask_app) -> Celery:
    """Create Celery app bound to Flask app configuration and context."""

    celery_enabled = _is_celery_enabled(flask_app)

    if celery_enabled:
        broker_url = flask_app.config.get("CELERY_BROKER_URL") or flask_app.config.get("REDIS_URL")
        backend_url = flask_app.config.get("REDIS_URL")
        beat_schedule = {
            "poll-carrier-updates": {
                "task": "chainwatchpro.poll_carrier_updates",
                "schedule": crontab(minute="*/15"),
            },
            "compute-disruption-scores": {
                "task": "chainwatchpro.compute_disruption_scores_all",
                "schedule": crontab(minute="5,20,35,50"),
            },
            "ingest-external-data": {
                "task": "chainwatchpro.ingest_external_data",
                "schedule": crontab(minute=0),
            },
            "update-carrier-performance": {
                "task": "chainwatchpro.update_carrier_performance",
                "schedule": crontab(minute=0, hour=2),
            },
        }
    else:
        broker_url = "memory://"
        backend_url = "cache+memory://"
        beat_schedule = {}

    celery_app = Celery(
        "chainwatchpro",
        broker=broker_url,
        backend=backend_url,
    )

    celery_app.conf.update(
        broker_url=broker_url,
        result_backend=backend_url,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        broker_connection_retry_on_startup=True,
        worker_concurrency=max(1, (os.cpu_count() or 2)),
        task_default_queue="default",
        task_always_eager=not celery_enabled,
        task_store_eager_result=not celery_enabled,
        task_eager_propagates=False,
        task_routes={
            "chainwatchpro.poll_carrier_updates": {"queue": "high"},
            "chainwatchpro.compute_disruption_scores_all": {"queue": "high"},
            "chainwatchpro.compute_disruption_scores_single": {"queue": "high"},
            "chainwatchpro.send_notifications": {"queue": "high"},
            "chainwatchpro.ingest_external_data": {"queue": "default"},
            "chainwatchpro.generate_route_alternatives_for_shipment": {"queue": "low"},
            "chainwatchpro.update_carrier_performance": {"queue": "low"},
            "chainwatchpro.send_webhook_retry": {"queue": "default"},
            "chainwatchpro.generate_report": {"queue": "default"},
        },
        task_annotations={
            "*": {
                "max_retries": 3,
                "retry_backoff": True,
                "retry_backoff_max": 600,
            }
        },
        beat_schedule=beat_schedule,
    )

    class FlaskContextTask(Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with flask_app.app_context():
                return self.run(*args, **kwargs)

    celery_app.Task = FlaskContextTask
    return celery_app


def _startup_checks(flask_app, celery_app) -> None:
    """Validate Flask app, Redis broker, and DB connectivity for worker startup."""

    with flask_app.app_context():
        celery_enabled = _is_celery_enabled(flask_app)
        app_ok = True
        redis_ok = not celery_enabled
        db_ok = False

        try:
            _ = flask_app.config.get("SECRET_KEY")
            app_ok = True
        except Exception:
            app_ok = False

        if celery_enabled:
            broker_url = celery_app.conf.get("broker_url")
            try:
                broker_client = redis.from_url(broker_url)
                redis_ok = bool(broker_client.ping())
            except Exception:
                redis_ok = False

        try:
            db.session.execute(text("SELECT 1"))
            db_ok = True
        except Exception:
            db_ok = False

        logger.info(
            "Celery startup check: flask_app=%s celery=%s redis_broker=%s database=%s",
            "reachable" if app_ok else "unreachable",
            "enabled" if celery_enabled else "disabled",
            "reachable" if redis_ok else "unreachable",
            "reachable" if db_ok else "unreachable",
        )


def _build_carrier_performance_upsert_stmt(payload: dict[str, Any]):
    conflict_columns = [
        "carrier_id",
        "organisation_id",
        "origin_region",
        "destination_region",
        "mode",
        "period_year",
        "period_month",
    ]
    update_payload = {
        "total_shipments": payload["total_shipments"],
        "on_time_count": payload["on_time_count"],
        "otd_rate": payload["otd_rate"],
        "avg_delay_hours": payload["avg_delay_hours"],
        "reliability_score": payload["reliability_score"],
    }

    bind = db.session.get_bind()
    dialect_name = bind.dialect.name if bind is not None else ""

    if dialect_name == "postgresql":
        statement = pg_insert(CarrierPerformance).values(**payload)
    else:
        statement = sqlite_insert(CarrierPerformance).values(**payload)

    return statement.on_conflict_do_update(
        index_elements=conflict_columns,
        set_=update_payload,
    )


flask_app = create_app(os.getenv("FLASK_ENV", "development"))
celery = make_celery(flask_app)
_startup_checks(flask_app, celery)

if not _is_celery_enabled(flask_app):
    logger.info(
        "Celery is disabled for environment=%s. Tasks run eagerly in-process.",
        flask_app.config.get("ENV_NAME", os.getenv("FLASK_ENV", "development")),
    )


@celery.task(name="chainwatchpro.poll_carrier_updates", bind=True)
def poll_carrier_updates(self):
    """Poll carrier updates and refresh in-transit/pending shipment positions."""

    with flask_app.app_context():
        total_updated = 0
        touched_shipment_ids: set[str] = set()

        organisations = _get_active_organisations()
        for org in organisations:
            org_shipments = (
                Shipment.query.filter(
                    Shipment.organisation_id == org.id,
                    Shipment.is_archived.is_(False),
                    Shipment.status.in_(["in_transit", "pending"]),
                )
                .all()
            )
            now = datetime.utcnow()

            carrier_ids = sorted({shipment.carrier_id for shipment in org_shipments if shipment.carrier_id})
            for carrier_id in carrier_ids:
                carrier = Carrier.query.filter_by(id=carrier_id).first()
                if carrier is None:
                    continue

                summary = carrier_tracker.poll_carrier_for_updates(
                    carrier,
                    org,
                    db.session,
                    flask_app,
                )
                total_updated += int(summary.get("shipments_updated", 0) or 0)

                if int(summary.get("shipments_updated", 0) or 0) > 0:
                    candidate_ids = (
                        Shipment.query.with_entities(Shipment.id)
                        .filter(
                            Shipment.organisation_id == org.id,
                            Shipment.carrier_id == carrier.id,
                            Shipment.is_archived.is_(False),
                            Shipment.status.in_(["pending", "in_transit", "delayed", "at_customs"]),
                        )
                        .all()
                    )
                    for candidate in candidate_ids:
                        touched_shipment_ids.add(str(candidate.id))

            unassigned_shipments = [shipment for shipment in org_shipments if shipment.carrier_id is None]
            for shipment in unassigned_shipments:
                changed = False

                if shipment.status == "pending" and shipment.estimated_departure and shipment.estimated_departure <= now:
                    shipment.status = "in_transit"
                    shipment.actual_departure = shipment.actual_departure or now
                    changed = True

                lat, lng, location_name = _interpolate_position(shipment)
                if lat is not None and lng is not None:
                    shipment.current_latitude = round(lat, 6)
                    shipment.current_longitude = round(lng, 6)
                    shipment.current_location_name = location_name
                    shipment.updated_at = now
                    changed = True

                if changed:
                    total_updated += 1
                    touched_shipment_ids.add(str(shipment.id))

        if touched_shipment_ids:
            db.session.commit()
            for shipment_id in sorted(touched_shipment_ids):
                compute_disruption_scores_single.apply_async(args=[shipment_id], countdown=60, queue="high")
        else:
            db.session.rollback()

        logger.info("poll_carrier_updates completed with updated_shipments=%s", total_updated)
        return {"updated_shipments": total_updated}


@celery.task(name="chainwatchpro.compute_disruption_scores_all", bind=True)
def compute_disruption_scores_all(self):
    """Compute DRS for all active in-transit shipments across active organisations."""

    with flask_app.app_context():
        processed = 0
        errors = 0
        skipped_timeout = 0

        for org in _get_active_organisations():
            shipments = (
                Shipment.query.filter(
                    Shipment.organisation_id == org.id,
                    Shipment.is_archived.is_(False),
                    Shipment.status == "in_transit",
                )
                .all()
            )

            for shipment in shipments:
                started = time.monotonic()
                try:
                    latest_before = (
                        DisruptionScore.query.filter(DisruptionScore.shipment_id == shipment.id)
                        .order_by(DisruptionScore.computed_at.desc())
                        .first()
                    )
                    previous_drs = _safe_float(
                        latest_before.drs_total if latest_before else shipment.disruption_risk_score,
                        0.0,
                    )

                    result = disruption_engine.compute_drs(shipment, db.session, flask_app)
                    elapsed = time.monotonic() - started
                    if elapsed > 30.0:
                        logger.warning(
                            "Skipping post-DRS steps due to timeout shipment_id=%s elapsed=%.2fs",
                            shipment.id,
                            elapsed,
                        )
                        skipped_timeout += 1
                        continue

                    new_drs = _safe_float(result.get("drs_total"), 50.0)
                    ehs_signals = result.get("ehs_signals") or {}

                    alert_service.generate_alerts_for_shipment(
                        shipment,
                        new_drs,
                        previous_drs,
                        ehs_signals,
                        db.session,
                        flask_app,
                    )

                    has_pending_recommendation = (
                        RouteRecommendation.query.filter(
                            RouteRecommendation.shipment_id == shipment.id,
                            RouteRecommendation.status == "pending",
                        ).first()
                        is not None
                    )
                    if new_drs >= 60 and not has_pending_recommendation:
                        generate_route_alternatives_for_shipment.apply_async(
                            args=[str(shipment.id)],
                            queue="low",
                        )

                    processed += 1
                except Exception:
                    errors += 1
                    logger.exception("DRS batch processing failed shipment_id=%s", shipment.id)
                    db.session.rollback()

        logger.info(
            "compute_disruption_scores_all completed processed=%s errors=%s timeout_skips=%s",
            processed,
            errors,
            skipped_timeout,
        )
        return {
            "processed": processed,
            "errors": errors,
            "timeout_skips": skipped_timeout,
        }


@celery.task(name="chainwatchpro.compute_disruption_scores_single", bind=True)
def compute_disruption_scores_single(self, shipment_id: str):
    """Compute DRS for a single shipment after position updates."""

    with flask_app.app_context():
        parsed_id = _coerce_uuid(shipment_id)
        if parsed_id is None:
            logger.warning("Invalid shipment_id passed to compute_disruption_scores_single: %s", shipment_id)
            return {"success": False, "error": "invalid_shipment_id"}

        shipment = Shipment.query.filter_by(id=parsed_id).first()
        if shipment is None:
            return {"success": False, "error": "shipment_not_found"}

        try:
            latest_before = (
                DisruptionScore.query.filter(DisruptionScore.shipment_id == shipment.id)
                .order_by(DisruptionScore.computed_at.desc())
                .first()
            )
            previous_drs = _safe_float(
                latest_before.drs_total if latest_before else shipment.disruption_risk_score,
                0.0,
            )

            result = disruption_engine.compute_drs(shipment, db.session, flask_app)
            new_drs = _safe_float(result.get("drs_total"), 50.0)
            ehs_signals = result.get("ehs_signals") or {}

            alert_service.generate_alerts_for_shipment(
                shipment,
                new_drs,
                previous_drs,
                ehs_signals,
                db.session,
                flask_app,
            )

            has_pending_recommendation = (
                RouteRecommendation.query.filter(
                    RouteRecommendation.shipment_id == shipment.id,
                    RouteRecommendation.status == "pending",
                ).first()
                is not None
            )
            if new_drs >= 60 and not has_pending_recommendation:
                generate_route_alternatives_for_shipment.apply_async(args=[str(shipment.id)], queue="low")

            return {"success": True, "shipment_id": str(shipment.id), "drs": new_drs}
        except Exception:
            db.session.rollback()
            logger.exception("compute_disruption_scores_single failed shipment_id=%s", shipment.id)
            return {"success": False, "error": "processing_failed"}


@celery.task(name="chainwatchpro.ingest_external_data", bind=True)
def ingest_external_data(self):
    """Ingest and cache external weather, route-event, and port congestion intelligence."""

    with flask_app.app_context():
        weather_points_total = 0
        route_pairs_total = 0

        all_weather_payload: dict[str, Any] = {}
        all_route_payload: dict[str, Any] = {}

        for org in _get_active_organisations():
            weather_points = weather_service.get_weather_alert_locations(org.id, db.session, flask_app)
            route_event_map = news_monitor_service.scan_all_active_routes(org.id, db.session, flask_app)

            weather_points_total += len(weather_points)
            route_pairs_total += len(route_event_map)

            all_weather_payload[str(org.id)] = weather_points
            all_route_payload[str(org.id)] = {
                f"{origin}->{destination}": payload
                for (origin, destination), payload in route_event_map.items()
            }

        congestion_zones = port_data_service.get_port_congestion_zones(flask_app)

        redis_client = get_redis_client()
        if redis_client is not None:
            try:
                redis_client.setex("external:weather_points", 3600, json.dumps(all_weather_payload))
                redis_client.setex("external:route_events", 1800, json.dumps(all_route_payload))
                redis_client.setex("external:port_congestion_zones", 3600, json.dumps(congestion_zones))
            except Exception:
                logger.exception("Failed to cache aggregated external data payloads")

        logger.info(
            "ingest_external_data completed weather_points=%s route_pairs=%s congestion_ports=%s",
            weather_points_total,
            route_pairs_total,
            len(congestion_zones),
        )
        return {
            "weather_points": weather_points_total,
            "route_pairs": route_pairs_total,
            "congestion_ports": len(congestion_zones),
        }


@celery.task(name="chainwatchpro.generate_route_alternatives_for_shipment", bind=True)
def generate_route_alternatives_for_shipment(self, shipment_id: str):
    """Generate route alternatives for one shipment and notify organisation users."""

    with flask_app.app_context():
        parsed_id = _coerce_uuid(shipment_id)
        if parsed_id is None:
            return {"success": False, "error": "invalid_shipment_id"}

        shipment = Shipment.query.filter_by(id=parsed_id).first()
        if shipment is None:
            return {"success": False, "error": "shipment_not_found"}

        try:
            recommendations = route_optimizer.generate_route_alternatives(shipment, db.session, flask_app)
            if recommendations:
                existing_alert = (
                    Alert.query.filter(
                        Alert.shipment_id == shipment.id,
                        Alert.alert_type == "route_alternatives_ready",
                        Alert.created_at >= datetime.utcnow() - timedelta(hours=2),
                    )
                    .order_by(Alert.created_at.desc())
                    .first()
                )

                if existing_alert is None:
                    info_alert = Alert(
                        organisation_id=shipment.organisation_id,
                        shipment_id=shipment.id,
                        alert_type="route_alternatives_ready",
                        severity="info",
                        title="Route Alternatives Ready",
                        description=(
                            "AI-generated route alternatives are ready for review in the Route Optimizer. "
                            "Approve or dismiss an option before execution deadline."
                        ),
                        drs_at_alert=shipment.disruption_risk_score,
                        is_acknowledged=False,
                    )
                    db.session.add(info_alert)
                    db.session.commit()
                    send_notifications.apply_async(args=[str(info_alert.id)], queue="high")

            return {
                "success": True,
                "shipment_id": str(shipment.id),
                "count": len(recommendations),
            }
        except Exception:
            db.session.rollback()
            logger.exception("generate_route_alternatives_for_shipment failed shipment_id=%s", shipment.id)
            return {"success": False, "error": "generation_failed"}


@celery.task(name="chainwatchpro.send_notifications", bind=True)
def send_notifications(self, alert_id: str):
    """Dispatch email, SMS, and webhook notifications for a given alert."""

    with flask_app.app_context():
        parsed_id = _coerce_uuid(alert_id)
        if parsed_id is None:
            return {"success": False, "error": "invalid_alert_id"}

        alert = Alert.query.filter_by(id=parsed_id).first()
        if alert is None:
            return {"success": False, "error": "alert_not_found"}

        shipment = Shipment.query.filter_by(id=alert.shipment_id).first() if alert.shipment_id else None
        organisation = Organisation.query.filter_by(id=alert.organisation_id).first()
        if shipment is None or organisation is None:
            return {"success": False, "error": "context_missing"}

        users = (
            User.query.filter(
                User.organisation_id == organisation.id,
                User.role.in_(["admin", "manager"]),
                User._is_active.is_(True),
            )
            .all()
        )

        notification_summary = {
            "emails": {"attempted": 0, "sent": 0, "failed": 0},
            "sms": {"attempted": 0, "sent": 0, "failed": 0},
            "webhooks": {"attempted": 0, "sent": 0, "failed": 0},
        }

        for user in users:
            if not user.alert_email_enabled:
                continue
            notification_summary["emails"]["attempted"] += 1
            try:
                sent = email_service.send_alert_notification_email(user, alert, shipment)
                if sent:
                    notification_summary["emails"]["sent"] += 1
                else:
                    notification_summary["emails"]["failed"] += 1
            except Exception:
                notification_summary["emails"]["failed"] += 1
                logger.exception(
                    "Email notification failed user_id=%s alert_id=%s",
                    user.id,
                    alert.id,
                )

        if alert.severity == "critical":
            sms_results = sms_service.send_sms_to_org_critical_subscribers(
                alert,
                shipment,
                organisation.id,
                db.session,
                flask_app,
            )
            notification_summary["sms"]["attempted"] = len(sms_results)
            notification_summary["sms"]["sent"] = len([r for r in sms_results if r.get("success")])
            notification_summary["sms"]["failed"] = len([r for r in sms_results if not r.get("success")])

        webhook_results = webhook_service.send_webhook_to_org_subscribers(
            alert,
            shipment,
            organisation.id,
            db.session,
            flask_app,
        )
        notification_summary["webhooks"]["attempted"] = len(webhook_results)
        notification_summary["webhooks"]["sent"] = len([r for r in webhook_results if r.get("success")])
        notification_summary["webhooks"]["failed"] = len([r for r in webhook_results if not r.get("success")])

        AuditLog.log(
            db,
            event_type="notifications_dispatched",
            description=(
                f"Notification dispatch completed for alert {alert.title} "
                f"(severity={alert.severity})."
            ),
            organisation_id=organisation.id,
            shipment_id=shipment.id,
            alert_id=alert.id,
            metadata=notification_summary,
        )

        return {
            "success": True,
            "alert_id": str(alert.id),
            "summary": notification_summary,
        }


@celery.task(name="chainwatchpro.update_carrier_performance", bind=True)
def update_carrier_performance(self):
    """Aggregate delivered shipments into monthly carrier performance records."""

    with flask_app.app_context():
        cutoff = datetime.utcnow() - timedelta(days=30)

        shipments = (
            Shipment.query.filter(
                Shipment.status == "delivered",
                Shipment.actual_arrival.isnot(None),
                Shipment.actual_arrival >= cutoff,
                Shipment.carrier_id.isnot(None),
                Shipment.is_archived.is_(False),
            )
            .all()
        )

        grouped: dict[tuple[Any, ...], list[Shipment]] = defaultdict(list)
        global_grouped: dict[tuple[Any, ...], list[Shipment]] = defaultdict(list)

        for shipment in shipments:
            origin_region = disruption_engine._port_code_to_region(shipment.origin_port_code)
            destination_region = disruption_engine._port_code_to_region(shipment.destination_port_code)
            mode = _mode_to_performance_mode(shipment.mode)
            period_year = shipment.actual_arrival.year
            period_month = shipment.actual_arrival.month

            key = (
                shipment.carrier_id,
                shipment.organisation_id,
                origin_region,
                destination_region,
                mode,
                period_year,
                period_month,
            )
            grouped[key].append(shipment)

            global_key = (
                shipment.carrier_id,
                None,
                origin_region,
                destination_region,
                mode,
                period_year,
                period_month,
            )
            global_grouped[global_key].append(shipment)

        upsert_count = 0

        def compute_metrics(shipment_rows: list[Shipment]) -> tuple[int, int, float, float, float]:
            total_shipments = len(shipment_rows)
            on_time_count = len(
                [
                    row
                    for row in shipment_rows
                    if row.actual_arrival is not None
                    and row.estimated_arrival is not None
                    and row.actual_arrival <= row.estimated_arrival
                ]
            )

            otd_rate = (on_time_count / total_shipments) if total_shipments > 0 else 0.0

            late_delays = [
                (row.actual_arrival - row.estimated_arrival).total_seconds() / 3600.0
                for row in shipment_rows
                if row.actual_arrival is not None
                and row.estimated_arrival is not None
                and row.actual_arrival > row.estimated_arrival
            ]
            avg_delay_hours = mean(late_delays) if late_delays else 0.0
            reliability_score = otd_rate * 100.0
            return total_shipments, on_time_count, otd_rate, avg_delay_hours, reliability_score

        all_groups = [*grouped.items(), *global_grouped.items()]
        for key, shipment_rows in all_groups:
            (
                carrier_id,
                organisation_id,
                origin_region,
                destination_region,
                mode,
                period_year,
                period_month,
            ) = key

            total_shipments, on_time_count, otd_rate, avg_delay_hours, reliability_score = compute_metrics(
                shipment_rows
            )

            payload = {
                "carrier_id": carrier_id,
                "organisation_id": organisation_id,
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

            stmt = _build_carrier_performance_upsert_stmt(payload)
            db.session.execute(stmt)
            upsert_count += 1

        db.session.commit()

        logger.info("update_carrier_performance completed upserts=%s", upsert_count)
        return {"updated_records": upsert_count}


@celery.task(name="chainwatchpro.generate_report", bind=True)
def generate_report_task(
    self,
    report_type: str,
    organisation_id: str,
    start_date: str,
    end_date: str,
    output_format: str,
    requesting_user_id: str,
):
    """Generate one report on-demand and persist metadata for UI downloads."""

    started_at = time.monotonic()
    org_uuid = _coerce_uuid(organisation_id)
    requester_uuid = _coerce_uuid(requesting_user_id)

    with flask_app.app_context():
        try:
            self.update_state(
                state="PROGRESS",
                meta={
                    "progress": 10,
                    "status": "Querying data...",
                    "report_type": report_type,
                    "output_format": output_format,
                },
            )

            if org_uuid is None:
                raise ValueError("Invalid organisation_id")

            start_dt = datetime.fromisoformat(str(start_date)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_dt = datetime.fromisoformat(str(end_date)).replace(hour=23, minute=59, second=59, microsecond=999999)

            self.update_state(
                state="PROGRESS",
                meta={
                    "progress": 80,
                    "status": "Generating file...",
                    "report_type": report_type,
                    "output_format": output_format,
                },
            )

            result = report_service.generate_report(
                report_type=report_type,
                organisation_id=org_uuid,
                start_date=start_dt,
                end_date=end_dt,
                output_format=output_format,
                db_session=db.session,
                app_context=flask_app,
            )

            if not result.get("success"):
                raise ValueError(result.get("error") or "Report generation failed")

            organisation = Organisation.query.filter_by(id=org_uuid).first()
            if organisation is None:
                raise ValueError("Organisation not found")

            file_path = result.get("file_path")
            filename = result.get("filename")
            file_size_bytes = os.path.getsize(file_path) if file_path and os.path.exists(file_path) else 0
            download_url = f"/reports/download/{filename}"

            profile = organisation.org_profile_data or {}
            if not isinstance(profile, dict):
                profile = {}

            generated_reports = profile.get("generated_reports", [])
            if not isinstance(generated_reports, list):
                generated_reports = []

            generated_payload = {
                "organisation_id": str(org_uuid),
                "filename": filename,
                "report_type": report_type,
                "start_date": start_date,
                "end_date": end_date,
                "output_format": output_format,
                "generated_at": datetime.utcnow().isoformat(),
                "file_size_bytes": file_size_bytes,
                "download_url": download_url,
            }
            generated_reports.append(generated_payload)
            profile["generated_reports"] = generated_reports[-20:]

            report_jobs = profile.get("report_jobs", {})
            if not isinstance(report_jobs, dict):
                report_jobs = {}

            report_job = report_jobs.get(self.request.id)
            if not isinstance(report_job, dict):
                report_job = {
                    "organisation_id": str(org_uuid),
                    "report_type": report_type,
                    "output_format": output_format,
                    "start_date": start_date,
                    "end_date": end_date,
                    "requested_at": datetime.utcnow().isoformat(),
                    "requested_by": str(requester_uuid) if requester_uuid else None,
                }
            report_job["status"] = "completed"
            report_job["filename"] = filename
            report_job["download_url"] = download_url
            report_job["error"] = None
            report_job["completed_at"] = datetime.utcnow().isoformat()
            report_jobs[self.request.id] = report_job
            profile["report_jobs"] = report_jobs

            organisation.org_profile_data = profile
            flag_modified(organisation, "org_profile_data")
            db.session.commit()

            actor_user = User.query.filter_by(id=requester_uuid).first() if requester_uuid else None
            generation_time_seconds = round(time.monotonic() - started_at, 2)
            AuditLog.log(
                db,
                event_type="report_generated",
                description=f"Generated {report_type} report file {filename}.",
                organisation_id=org_uuid,
                actor_user=actor_user,
                metadata={
                    "report_type": report_type,
                    "filename": filename,
                    "file_size_bytes": file_size_bytes,
                    "generation_time_seconds": generation_time_seconds,
                },
            )

            success_payload = {
                "progress": 100,
                "status": "completed",
                "download_url": download_url,
                "report_type": report_type,
                "output_format": output_format,
            }
            self.update_state(state="SUCCESS", meta=success_payload)
            return success_payload
        except Exception as exc:
            db.session.rollback()
            logger.exception("generate_report_task failed org_id=%s type=%s", organisation_id, report_type)

            if org_uuid is not None:
                try:
                    organisation = Organisation.query.filter_by(id=org_uuid).first()
                    if organisation is not None:
                        profile = organisation.org_profile_data or {}
                        if not isinstance(profile, dict):
                            profile = {}
                        report_jobs = profile.get("report_jobs", {})
                        if not isinstance(report_jobs, dict):
                            report_jobs = {}

                        report_job = report_jobs.get(self.request.id)
                        if not isinstance(report_job, dict):
                            report_job = {
                                "organisation_id": str(org_uuid),
                                "report_type": report_type,
                                "output_format": output_format,
                                "start_date": start_date,
                                "end_date": end_date,
                                "requested_at": datetime.utcnow().isoformat(),
                                "requested_by": str(requester_uuid) if requester_uuid else None,
                            }
                        report_job["status"] = "failed"
                        report_job["error"] = str(exc)
                        report_job["completed_at"] = datetime.utcnow().isoformat()
                        report_jobs[self.request.id] = report_job
                        profile["report_jobs"] = report_jobs
                        organisation.org_profile_data = profile
                        flag_modified(organisation, "org_profile_data")
                        db.session.commit()
                except Exception:
                    db.session.rollback()

            # Let Celery mark FAILURE with a real exception object.
            # Writing a plain dict into FAILURE state breaks backend exception decoding.
            raise RuntimeError(str(exc)) from exc


@celery.task(name="chainwatchpro.send_webhook_retry", bind=True)
def send_webhook_retry(self, alert_id: str, shipment_id: str, organisation_id: str, webhook_url: str, attempt: int = 1):
    """Retry webhook delivery after transient failures with exponential backoff scheduling."""

    with flask_app.app_context():
        alert_uuid = _coerce_uuid(alert_id)
        shipment_uuid = _coerce_uuid(shipment_id)
        organisation_uuid = _coerce_uuid(organisation_id)

        if not alert_uuid or not shipment_uuid or not organisation_uuid:
            return {"success": False, "error": "invalid_retry_payload"}

        alert = Alert.query.filter_by(id=alert_uuid).first()
        shipment = Shipment.query.filter_by(id=shipment_uuid).first()
        organisation = Organisation.query.filter_by(id=organisation_uuid).first()

        if not alert or not shipment or not organisation:
            return {"success": False, "error": "retry_context_missing"}

        if "hooks.slack.com" in (webhook_url or "").lower():
            result = webhook_service._send_slack_webhook(
                webhook_url,
                alert,
                shipment,
                organisation,
                flask_app,
                attempt=attempt,
            )
        else:
            result = webhook_service.send_webhook_notification(
                webhook_url,
                alert,
                shipment,
                organisation,
                flask_app,
                attempt=attempt,
            )
        return result


@celery.task(name="chainwatchpro.send_platform_announcement_batch", bind=True)
def send_platform_announcement_batch(self, recipient_emails: list[str], subject: str, message_html: str, offset: int = 0):
    """Send platform announcement emails in capped batches of 50 recipients per minute."""

    with flask_app.app_context():
        recipients = [email for email in (recipient_emails or []) if isinstance(email, str) and email.strip()]
        start_index = max(int(offset or 0), 0)
        batch_size = 50
        batch = recipients[start_index : start_index + batch_size]

        sent = 0
        failed = 0
        for email in batch:
            try:
                ok = email_service.send_platform_announcement_email(email.strip(), subject, message_html)
                if ok:
                    sent += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
                logger.exception("Failed platform announcement email recipient=%s", email)

        next_offset = start_index + len(batch)
        remaining = max(len(recipients) - next_offset, 0)
        if remaining > 0:
            send_platform_announcement_batch.apply_async(
                args=[recipients, subject, message_html, next_offset],
                countdown=60,
                queue="default",
            )

        return {
            "total_recipients": len(recipients),
            "batch_start": start_index,
            "batch_sent": sent,
            "batch_failed": failed,
            "remaining": remaining,
        }


if __name__ == "__main__":
    if _is_celery_enabled(flask_app):
        logger.info(
            "Celery worker loaded. Start worker with: celery -A celery_worker.celery worker --loglevel=info -Q high,default,low"
        )
    else:
        logger.info("Celery is disabled in current environment. External worker process is not required.")
