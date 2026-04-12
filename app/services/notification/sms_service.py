"""Twilio SMS notification service for critical shipment alerts."""

from __future__ import annotations

import logging
from typing import Any

from flask import current_app
from twilio.rest import Client

from app.extensions import get_redis_client
from app.models.user import User

logger = logging.getLogger(__name__)


def _get_app(app_context):
    if app_context is not None:
        return app_context
    return current_app._get_current_object()


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return "unknown"

    digits = "".join(char for char in phone if char.isdigit())
    if len(digits) <= 4:
        return f"***{digits}"
    return f"***{digits[-4:]}"


def _build_sms_body(alert, shipment) -> str:
    base_prefix = "ChainWatch Pro CRITICAL ALERT: "
    suffix = (
        f" — Shipment {shipment.external_reference or 'N/A'} at risk. "
        f"DRS: {round(float(alert.drs_at_alert or 0), 1)}/100. Action required. Log in to review."
    )

    max_title_len = max(0, 160 - len(base_prefix) - len(suffix))
    title = (alert.title or "Critical disruption").strip()
    if len(title) > max_title_len:
        title = title[: max(0, max_title_len - 3)].rstrip() + "..."

    body = f"{base_prefix}{title}{suffix}"
    if len(body) > 160:
        body = body[:157].rstrip() + "..."
    return body


def send_critical_alert_sms(user, alert, shipment, app_context):
    """Send a single critical alert SMS message to one user via Twilio."""

    if not getattr(user, "alert_sms_enabled", False):
        return {"success": False, "message_sid": None, "error": "SMS disabled for user"}

    if not getattr(user, "phone", None):
        return {"success": False, "message_sid": None, "error": "User phone missing"}

    if getattr(alert, "severity", "") != "critical":
        return {"success": False, "message_sid": None, "error": "Alert is not critical"}

    app = _get_app(app_context)

    account_sid = app.config.get("TWILIO_ACCOUNT_SID")
    auth_token = app.config.get("TWILIO_AUTH_TOKEN")
    from_number = app.config.get("TWILIO_FROM_NUMBER")

    if not account_sid or not auth_token or not from_number:
        logger.warning("Twilio credentials incomplete; SMS skipped for user_id=%s", user.id)
        return {"success": False, "message_sid": None, "error": "Twilio credentials missing"}

    sms_body = _build_sms_body(alert, shipment)

    try:
        client = Client(account_sid, auth_token)
        message = client.messages.create(
            body=sms_body,
            from_=from_number,
            to=user.phone,
        )
        logger.info(
            "Critical SMS sent user_id=%s alert_id=%s message_sid=%s phone=%s",
            user.id,
            alert.id,
            message.sid,
            _mask_phone(user.phone),
        )
        return {"success": True, "message_sid": message.sid, "error": None}
    except Exception as exc:
        logger.error(
            "Critical SMS send failed user_id=%s alert_id=%s phone=%s error=%s",
            user.id,
            alert.id,
            _mask_phone(user.phone),
            str(exc),
            exc_info=True,
        )
        return {"success": False, "message_sid": None, "error": str(exc)}


def send_sms_to_org_critical_subscribers(alert, shipment, organisation_id, db_session, app_context):
    """Send critical SMS to active admin/manager subscribers in an organisation."""

    if getattr(alert, "severity", "") != "critical":
        return []

    users = (
        db_session.query(User)
        .filter(
            User.organisation_id == organisation_id,
            User.role.in_(["admin", "manager"]),
            User.alert_sms_enabled.is_(True),
            User._is_active.is_(True),
        )
        .all()
    )

    redis_client = get_redis_client()
    results: list[dict[str, Any]] = []

    for user in users:
        rate_key = f"sms:sent:{user.id}:{shipment.id}:{alert.alert_type}"
        should_skip = False

        if redis_client is not None:
            try:
                if redis_client.get(rate_key):
                    should_skip = True
                else:
                    redis_client.setex(rate_key, 14400, "1")
            except Exception:
                logger.debug("SMS rate-limit cache check failed key=%s", rate_key, exc_info=True)

        if should_skip:
            results.append(
                {
                    "user_id": str(user.id),
                    "success": False,
                    "message_sid": None,
                    "error": "Rate limited",
                }
            )
            continue

        send_result = send_critical_alert_sms(user, alert, shipment, app_context)
        send_result["user_id"] = str(user.id)
        results.append(send_result)

    return results
