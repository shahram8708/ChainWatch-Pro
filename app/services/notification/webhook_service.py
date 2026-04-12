"""Outbound webhook delivery service for alert notifications."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime
from typing import Any

import requests
from flask import current_app

from app.models.organisation import Organisation

logger = logging.getLogger(__name__)


def _get_app(app_context):
    if app_context is not None:
        return app_context
    return current_app._get_current_object()


def _platform_url(app, shipment_id) -> str:
    base = (app.config.get("APP_BASE_URL") or "http://localhost:5000").rstrip("/")
    return f"{base}/shipments/{shipment_id}"


def _build_payload(alert, shipment, organisation, app) -> dict[str, Any]:
    return {
        "event": "alert.triggered",
        "timestamp": datetime.utcnow().isoformat(),
        "organisation_id": str(organisation.id),
        "alert": {
            "id": str(alert.id),
            "type": alert.alert_type,
            "severity": alert.severity,
            "title": alert.title,
            "description": alert.description,
            "drs_at_alert": float(alert.drs_at_alert or 0),
            "created_at": alert.created_at.isoformat() if alert.created_at else None,
        },
        "shipment": {
            "id": str(shipment.id),
            "external_reference": shipment.external_reference,
            "origin_port_code": shipment.origin_port_code,
            "destination_port_code": shipment.destination_port_code,
            "carrier_name": shipment.carrier.name if shipment.carrier else None,
            "mode": shipment.mode,
            "status": shipment.status,
            "disruption_risk_score": float(shipment.disruption_risk_score or 0),
            "estimated_arrival": shipment.estimated_arrival.isoformat() if shipment.estimated_arrival else None,
        },
        "platform_url": _platform_url(app, shipment.id),
    }


def _build_signature(payload_bytes: bytes, secret: str | None) -> str:
    signing_key = (secret or "").encode("utf-8")
    return hmac.new(signing_key, payload_bytes, hashlib.sha256).hexdigest()


def _schedule_retry(
    alert_id: str,
    shipment_id: str,
    organisation_id: str,
    webhook_url: str,
    attempt: int,
    app=None,
) -> None:
    if attempt > 3:
        return

    if app is not None and not bool(app.config.get("CELERY_ENABLED", False)):
        return

    backoff_map = {1: 30, 2: 60, 3: 120}
    countdown = backoff_map.get(attempt)
    if countdown is None:
        return

    try:
        from celery_worker import send_webhook_retry

        send_webhook_retry.apply_async(
            kwargs={
                "alert_id": alert_id,
                "shipment_id": shipment_id,
                "organisation_id": organisation_id,
                "webhook_url": webhook_url,
                "attempt": attempt,
            },
            countdown=countdown,
            queue="default",
        )
    except Exception:
        logger.exception(
            "Failed to schedule webhook retry alert_id=%s shipment_id=%s attempt=%s",
            alert_id,
            shipment_id,
            attempt,
        )


def send_webhook_notification(webhook_url, alert, shipment, organisation, app_context, attempt: int = 0):
    """Send alert payload to one webhook endpoint with signature and retry support."""

    if not webhook_url or not str(webhook_url).lower().startswith("https://"):
        logger.warning("Non-HTTPS webhook URL skipped org_id=%s url=%s", organisation.id, webhook_url)
        return {
            "success": False,
            "status_code": None,
            "response_body": None,
            "error": "Webhook URL must use https://",
        }

    app = _get_app(app_context)
    org_profile_data = organisation.org_profile_data or {}
    webhook_secret = org_profile_data.get("webhook_secret")

    payload = _build_payload(alert, shipment, organisation, app)
    payload_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = _build_signature(payload_bytes, webhook_secret)

    headers = {
        "Content-Type": "application/json",
        "X-ChainWatch-Event": "alert.triggered",
        "X-ChainWatch-Signature": signature,
        "User-Agent": "ChainWatch-Pro/1.0",
    }

    try:
        response = requests.post(
            webhook_url,
            data=payload_bytes,
            headers=headers,
            timeout=10,
        )

        if 200 <= response.status_code < 300:
            logger.info(
                "Webhook delivered alert_id=%s shipment_id=%s status=%s",
                alert.id,
                shipment.id,
                response.status_code,
            )
            return {
                "success": True,
                "status_code": response.status_code,
                "response_body": response.text[:2000],
                "error": None,
            }

        if 400 <= response.status_code < 500:
            logger.warning(
                "Webhook client error alert_id=%s shipment_id=%s status=%s body=%s",
                alert.id,
                shipment.id,
                response.status_code,
                response.text[:500],
            )
            return {
                "success": False,
                "status_code": response.status_code,
                "response_body": response.text[:2000],
                "error": "Webhook returned 4xx",
            }

        logger.error(
            "Webhook server error alert_id=%s shipment_id=%s status=%s body=%s",
            alert.id,
            shipment.id,
            response.status_code,
            response.text[:500],
        )
        _schedule_retry(
            str(alert.id),
            str(shipment.id),
            str(organisation.id),
            webhook_url,
            attempt + 1,
            app=app,
        )
        return {
            "success": False,
            "status_code": response.status_code,
            "response_body": response.text[:2000],
            "error": "Webhook returned 5xx",
        }
    except requests.Timeout:
        logger.error(
            "Webhook timeout alert_id=%s shipment_id=%s attempt=%s",
            alert.id,
            shipment.id,
            attempt,
            exc_info=True,
        )
        _schedule_retry(
            str(alert.id),
            str(shipment.id),
            str(organisation.id),
            webhook_url,
            attempt + 1,
            app=app,
        )
        return {
            "success": False,
            "status_code": None,
            "response_body": None,
            "error": "Webhook timeout",
        }
    except Exception as exc:
        logger.error(
            "Webhook send failed alert_id=%s shipment_id=%s error=%s",
            alert.id,
            shipment.id,
            str(exc),
            exc_info=True,
        )
        _schedule_retry(
            str(alert.id),
            str(shipment.id),
            str(organisation.id),
            webhook_url,
            attempt + 1,
            app=app,
        )
        return {
            "success": False,
            "status_code": None,
            "response_body": None,
            "error": str(exc),
        }


def _severity_color(severity: str) -> str:
    mapping = {
        "critical": "#D32F2F",
        "warning": "#FF8C00",
        "watch": "#F59E0B",
        "info": "#0077CC",
    }
    return mapping.get((severity or "").lower(), "#0077CC")


def _send_slack_webhook(webhook_url: str, alert, shipment, organisation, app_context, attempt: int = 0) -> dict[str, Any]:
    app = _get_app(app_context)

    if not webhook_url.lower().startswith("https://"):
        logger.warning("Non-HTTPS Slack webhook URL skipped org_id=%s", organisation.id)
        return {
            "success": False,
            "status_code": None,
            "response_body": None,
            "error": "Webhook URL must use https://",
        }

    payload = {
        "text": f"ChainWatch Pro alert: {alert.title}",
        "attachments": [
            {
                "color": _severity_color(alert.severity),
                "fields": [
                    {"title": "Severity", "value": alert.severity.title(), "short": True},
                    {
                        "title": "Shipment",
                        "value": shipment.external_reference or str(shipment.id),
                        "short": True,
                    },
                    {"title": "Route", "value": f"{shipment.origin_port_code} -> {shipment.destination_port_code}", "short": False},
                    {
                        "title": "DRS",
                        "value": f"{round(float(alert.drs_at_alert or 0), 1)}/100",
                        "short": True,
                    },
                    {
                        "title": "Open in ChainWatch Pro",
                        "value": _platform_url(app, shipment.id),
                        "short": False,
                    },
                ],
            }
        ],
    }

    try:
        response = requests.post(webhook_url, json=payload, timeout=10)
        success = 200 <= response.status_code < 300
        if success:
            logger.info("Slack webhook sent org_id=%s alert_id=%s", organisation.id, alert.id)
        elif response.status_code >= 500:
            _schedule_retry(
                str(alert.id),
                str(shipment.id),
                str(organisation.id),
                webhook_url,
                attempt + 1,
                app=app,
            )
        else:
            logger.warning(
                "Slack webhook 4xx org_id=%s alert_id=%s status=%s",
                organisation.id,
                alert.id,
                response.status_code,
            )

        return {
            "success": success,
            "status_code": response.status_code,
            "response_body": response.text[:2000],
            "error": None if success else "Slack webhook send failed",
        }
    except Exception as exc:
        logger.error("Slack webhook send error org_id=%s alert_id=%s error=%s", organisation.id, alert.id, str(exc), exc_info=True)
        _schedule_retry(
            str(alert.id),
            str(shipment.id),
            str(organisation.id),
            webhook_url,
            attempt + 1,
            app=app,
        )
        return {
            "success": False,
            "status_code": None,
            "response_body": None,
            "error": str(exc),
        }


def send_webhook_to_org_subscribers(alert, shipment, organisation_id, db_session, app_context):
    """Send webhook notification to organisation-configured endpoints."""

    organisation = db_session.query(Organisation).filter_by(id=organisation_id).first()
    if organisation is None:
        return []

    profile = organisation.org_profile_data or {}
    if not profile.get("alert_webhook"):
        return []

    webhook_url = (profile.get("webhook_url") or "").strip()
    if not webhook_url:
        return []

    if "hooks.slack.com" in webhook_url.lower():
        result = _send_slack_webhook(webhook_url, alert, shipment, organisation, app_context, attempt=0)
        return [result]

    result = send_webhook_notification(webhook_url, alert, shipment, organisation, app_context)
    return [result]
