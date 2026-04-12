"""Webhook endpoints for third-party callbacks."""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import csrf, db
from app.models.audit_log import AuditLog
from app.models.organisation import Organisation
from app.models.user import User
from app.services import razorpay_service
from app.services.notification import email_service


webhooks_bp = Blueprint("webhooks", __name__)
logger = logging.getLogger(__name__)


@webhooks_bp.post("/razorpay")
@csrf.exempt
def razorpay_webhook():
    try:
        payload_body = request.get_data()
        signature = request.headers.get("X-Razorpay-Signature", "")

        if not razorpay_service.verify_webhook_signature(payload_body, signature):
            logger.warning(
                "Invalid Razorpay webhook signature from ip=%s",
                request.remote_addr,
            )
            return jsonify({"status": "invalid_signature"}), 400

        payload = request.get_json(silent=True) or {}
        event_type = str(payload.get("event") or "")

        subscription_entity = (
            ((payload.get("payload") or {}).get("subscription") or {}).get("entity") or {}
        )
        payment_entity = (((payload.get("payload") or {}).get("payment") or {}).get("entity") or {})
        invoice_entity = (((payload.get("payload") or {}).get("invoice") or {}).get("entity") or {})

        subscription_id = (
            subscription_entity.get("id")
            or payment_entity.get("subscription_id")
            or invoice_entity.get("subscription_id")
        )

        organisation = None
        if subscription_id:
            organisation = Organisation.query.filter_by(razorpay_subscription_id=subscription_id).first()

        if event_type == "subscription.activated":
            if organisation is not None:
                organisation.subscription_status = "active"

                profile = organisation.org_profile_data or {}
                if not isinstance(profile, dict):
                    profile = {}
                pending = profile.get("pending_subscription", {})
                if isinstance(pending, dict) and pending.get("subscription_id") == subscription_id:
                    plan = pending.get("plan")
                    billing_cycle = pending.get("billing_cycle", "monthly")
                    if plan in {"starter", "professional", "enterprise"}:
                        organisation.subscription_plan = plan
                    profile["billing_cycle"] = billing_cycle
                    profile.pop("pending_subscription", None)
                    organisation.org_profile_data = profile
                    flag_modified(organisation, "org_profile_data")

                AuditLog.log(
                    db,
                    event_type="subscription_upgraded",
                    description="Subscription activated via Razorpay webhook.",
                    organisation_id=organisation.id,
                    metadata={
                        "plan": organisation.subscription_plan,
                        "billing_cycle": (profile.get("billing_cycle") if isinstance(profile, dict) else "monthly"),
                        "payment_id": payment_entity.get("id"),
                        "amount_inr": float(payment_entity.get("amount", 0) or 0) / 100.0,
                    },
                    ip_address=request.remote_addr,
                )
            return jsonify({"status": "ok"}), 200

        if event_type == "subscription.charged":
            if organisation is not None:
                organisation.subscription_status = "active"
                AuditLog.log(
                    db,
                    event_type="subscription_charged",
                    description="Recurring subscription charge succeeded.",
                    organisation_id=organisation.id,
                    metadata={
                        "amount_inr": float(payment_entity.get("amount", 0) or 0) / 100.0,
                        "invoice_id": invoice_entity.get("id") or payment_entity.get("invoice_id"),
                    },
                    ip_address=request.remote_addr,
                )
            return jsonify({"status": "ok"}), 200

        if event_type == "subscription.cancelled":
            if organisation is not None:
                organisation.subscription_status = "cancelled"

                admin_user = (
                    User.query.filter(
                        User.organisation_id == organisation.id,
                        User.role == "admin",
                        User._is_active.is_(True),
                    )
                    .order_by(User.created_at.asc())
                    .first()
                )
                if admin_user is None:
                    admin_user = (
                        User.query.filter(
                            User.organisation_id == organisation.id,
                            User._is_active.is_(True),
                        )
                        .order_by(User.created_at.asc())
                        .first()
                    )

                if admin_user is not None:
                    email_service.send_subscription_cancellation_email(admin_user, organisation)

                AuditLog.log(
                    db,
                    event_type="subscription_cancelled",
                    description="Subscription cancelled from Razorpay webhook.",
                    organisation_id=organisation.id,
                    metadata={"cancel_at": datetime.utcnow().isoformat()},
                    ip_address=request.remote_addr,
                )
            return jsonify({"status": "ok"}), 200

        if event_type == "payment.failed":
            if organisation is not None:
                webhook_subscription_status = str(subscription_entity.get("status") or "").lower()
                if webhook_subscription_status == "halted":
                    organisation.subscription_status = "expired"

                admin_user = (
                    User.query.filter(
                        User.organisation_id == organisation.id,
                        User.role == "admin",
                        User._is_active.is_(True),
                    )
                    .order_by(User.created_at.asc())
                    .first()
                )
                if admin_user is None:
                    admin_user = (
                        User.query.filter(
                            User.organisation_id == organisation.id,
                            User._is_active.is_(True),
                        )
                        .order_by(User.created_at.asc())
                        .first()
                    )

                if admin_user is not None:
                    email_service.send_payment_failed_email(admin_user, organisation, payment_entity)

                AuditLog.log(
                    db,
                    event_type="subscription_payment_failed",
                    description="Subscription payment failed.",
                    organisation_id=organisation.id,
                    metadata={
                        "subscription_status": webhook_subscription_status,
                        "payment_id": payment_entity.get("id"),
                        "amount_inr": float(payment_entity.get("amount", 0) or 0) / 100.0,
                    },
                    ip_address=request.remote_addr,
                )
            return jsonify({"status": "ok"}), 200

        logger.info("Ignoring unsupported Razorpay webhook event=%s", event_type)
        return jsonify({"status": "ignored", "event": event_type}), 200
    except Exception:
        db.session.rollback()
        logger.exception("Unhandled Razorpay webhook exception")
        return jsonify({"status": "error"}), 500
