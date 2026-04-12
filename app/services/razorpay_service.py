"""Razorpay subscription, billing, and plan-limit service for ChainWatch Pro."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime

import razorpay
from flask import current_app
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models.shipment import Shipment
from app.models.user import User

logger = logging.getLogger(__name__)


RAZORPAY_PLANS = {
    "starter": {
        "plan_id_monthly": None,
        "plan_id_annual": None,
        "monthly_plan_config_key": "RAZORPAY_PLAN_STARTER_MONTHLY",
        "annual_plan_config_key": "RAZORPAY_PLAN_STARTER_ANNUAL",
        "name": "Starter",
        "price_monthly_inr": 1249900,
        "price_annual_inr": 9999200,
        "shipment_limit": 50,
        "carrier_limit": 3,
        "user_limit": 2,
        "features": [
            "Up to 50 shipments",
            "3 carriers",
            "2 users",
            "Email alerts",
            "Basic optimizer",
            "5 simulations/month",
            "5 PDF exports/month",
        ],
    },
    "professional": {
        "plan_id_monthly": None,
        "plan_id_annual": None,
        "monthly_plan_config_key": "RAZORPAY_PLAN_PROFESSIONAL_MONTHLY",
        "annual_plan_config_key": "RAZORPAY_PLAN_PROFESSIONAL_ANNUAL",
        "name": "Professional",
        "price_monthly_inr": 3329900,
        "price_annual_inr": 26639200,
        "shipment_limit": 500,
        "carrier_limit": 15,
        "user_limit": 10,
        "features": [
            "Up to 500 shipments",
            "15 carriers",
            "10 users",
            "Email, SMS, Webhook",
            "Unlimited optimizer",
            "Unlimited simulations",
            "Unlimited exports",
        ],
    },
    "enterprise": {
        "plan_id_monthly": None,
        "plan_id_annual": None,
        "monthly_plan_config_key": "RAZORPAY_PLAN_ENTERPRISE_MONTHLY",
        "annual_plan_config_key": "RAZORPAY_PLAN_ENTERPRISE_ANNUAL",
        "name": "Enterprise",
        "price_monthly_inr": None,
        "price_annual_inr": None,
        "shipment_limit": None,
        "carrier_limit": None,
        "user_limit": None,
        "features": [
            "Unlimited shipments",
            "Unlimited carriers",
            "Unlimited users",
            "SSO & SLA",
            "Dedicated CSM",
            "Advanced compliance",
        ],
    },
}


def _resolved_plans() -> dict:
    plans = {}
    for plan_key, payload in RAZORPAY_PLANS.items():
        cloned = dict(payload)
        cloned["plan_id_monthly"] = str(current_app.config.get(payload["monthly_plan_config_key"], "") or "").strip()
        cloned["plan_id_annual"] = str(current_app.config.get(payload["annual_plan_config_key"], "") or "").strip()
        plans[plan_key] = cloned
    return plans


def resolve_plan_configuration(plan_name, billing_cycle) -> dict:
    normalized_plan = (plan_name or "").strip().lower()
    normalized_cycle = (billing_cycle or "monthly").strip().lower()

    if normalized_plan not in RAZORPAY_PLANS or normalized_cycle not in {"monthly", "annual"}:
        return {
            "is_valid": False,
            "plan": normalized_plan,
            "billing_cycle": normalized_cycle,
            "plan_id": "",
            "config_key": "",
        }

    selected = RAZORPAY_PLANS[normalized_plan]
    config_key = selected["monthly_plan_config_key"] if normalized_cycle == "monthly" else selected["annual_plan_config_key"]
    plan_id = str(current_app.config.get(config_key, "") or "").strip()

    return {
        "is_valid": True,
        "plan": normalized_plan,
        "billing_cycle": normalized_cycle,
        "plan_id": plan_id,
        "config_key": config_key,
    }


def get_razorpay_client() -> razorpay.Client:
    key_id = current_app.config.get("RAZORPAY_KEY_ID", "")
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "")
    return razorpay.Client(auth=(key_id, key_secret))


def _as_float_inr(paise_amount) -> float:
    try:
        if paise_amount is None:
            return 0.0
        return round(float(paise_amount) / 100.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _format_unix_date(epoch_seconds) -> str | None:
    try:
        if epoch_seconds is None:
            return None
        return datetime.utcfromtimestamp(int(epoch_seconds)).strftime("%d %b %Y")
    except (TypeError, ValueError, OSError):
        return None


def _format_iso_date(iso_value) -> str | None:
    if not iso_value:
        return None
    try:
        if isinstance(iso_value, datetime):
            dt_value = iso_value
        else:
            dt_value = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
        return dt_value.strftime("%d %b %Y")
    except (TypeError, ValueError):
        return None


def _get_org_admin(organisation):
    admin = (
        User.query.filter(
            User.organisation_id == organisation.id,
            User.role == "admin",
            User._is_active.is_(True),
        )
        .order_by(User.created_at.asc())
        .first()
    )
    if admin:
        return admin
    return (
        User.query.filter(
            User.organisation_id == organisation.id,
            User._is_active.is_(True),
        )
        .order_by(User.created_at.asc())
        .first()
    )


def create_razorpay_customer(organisation, user):
    if organisation is None or user is None:
        return None

    client = get_razorpay_client()
    payload = {
        "name": organisation.name,
        "email": user.email,
        "notes": {
            "organisation_id": str(organisation.id),
            "plan": organisation.subscription_plan,
        },
    }
    if getattr(user, "phone", None):
        payload["contact"] = user.phone

    try:
        customer = client.customer.create(payload)
        organisation.razorpay_customer_id = customer.get("id")
        db.session.commit()
        return customer
    except razorpay.errors.BadRequestError as exc:
        logger.error(
            "Razorpay customer creation failed org_id=%s payload=%s error=%s",
            organisation.id,
            json.dumps(payload, default=str),
            str(exc),
        )
        db.session.rollback()
        return None
    except Exception:
        logger.exception("Unexpected Razorpay customer creation error org_id=%s", organisation.id)
        db.session.rollback()
        return None


def create_subscription(organisation, plan_name, billing_cycle, db_session):
    if organisation is None:
        return None

    plan_config = resolve_plan_configuration(plan_name, billing_cycle)
    if not plan_config["is_valid"]:
        return None

    normalized_plan = plan_config["plan"]
    normalized_cycle = plan_config["billing_cycle"]
    selected_plan_id = plan_config["plan_id"]
    config_key = plan_config["config_key"]

    plans = _resolved_plans()
    selected = plans[normalized_plan]
    if not selected_plan_id:
        logger.error(
            "Missing Razorpay plan id for plan=%s cycle=%s config_key=%s org_id=%s",
            normalized_plan,
            normalized_cycle,
            config_key,
            getattr(organisation, "id", None),
        )
        return None

    if not organisation.razorpay_customer_id:
        admin_user = _get_org_admin(organisation)
        customer = create_razorpay_customer(organisation, admin_user)
        if customer is None:
            return None

    client = get_razorpay_client()
    payload = {
        "plan_id": selected_plan_id,
        "customer_id": organisation.razorpay_customer_id,
        "quantity": 1,
        "total_count": 120,
        "start_at": int(datetime.utcnow().timestamp()) + 86400,
        "notify_info": {
            "notify_phone": 1,
            "notify_email": 1,
            "sms_notify": 1,
        },
    }

    try:
        subscription = client.subscription.create(payload)
        organisation.razorpay_subscription_id = subscription.get("id")

        profile = organisation.org_profile_data or {}
        if not isinstance(profile, dict):
            profile = {}
        profile["billing_cycle"] = normalized_cycle
        profile["pending_subscription"] = {
            "plan": normalized_plan,
            "billing_cycle": normalized_cycle,
            "subscription_id": subscription.get("id"),
            "created_at": datetime.utcnow().isoformat(),
        }
        organisation.org_profile_data = profile
        flag_modified(organisation, "org_profile_data")

        db_session.commit()
        return subscription
    except Exception as exc:
        logger.error(
            "Razorpay subscription creation failed org_id=%s payload=%s error=%s",
            organisation.id,
            json.dumps(payload, default=str),
            str(exc),
        )
        db_session.rollback()
        return None


def create_one_time_order(organisation, plan_name, billing_cycle, db_session):
    if organisation is None:
        return None

    normalized_plan = (plan_name or "").strip().lower()
    normalized_cycle = (billing_cycle or "monthly").strip().lower()
    if normalized_plan not in RAZORPAY_PLANS or normalized_cycle not in {"monthly", "annual"}:
        return None

    selected = RAZORPAY_PLANS.get(normalized_plan, {})
    amount_paise = selected.get("price_monthly_inr") if normalized_cycle == "monthly" else selected.get("price_annual_inr")
    if not isinstance(amount_paise, int) or amount_paise <= 0:
        logger.error(
            "Invalid amount for order creation plan=%s cycle=%s amount=%s",
            normalized_plan,
            normalized_cycle,
            amount_paise,
        )
        return None

    receipt_suffix = str(getattr(organisation, "id", "")).replace("-", "")[:10]
    receipt = f"cw_{normalized_plan}_{normalized_cycle}_{receipt_suffix}_{int(datetime.utcnow().timestamp())}"

    payload = {
        "amount": int(amount_paise),
        "currency": "INR",
        "receipt": receipt[:40],
        "payment_capture": 1,
        "notes": {
            "organisation_id": str(organisation.id),
            "plan": normalized_plan,
            "billing_cycle": normalized_cycle,
            "flow": "order_checkout",
        },
    }

    client = get_razorpay_client()
    try:
        order = client.order.create(payload)

        profile = organisation.org_profile_data or {}
        if not isinstance(profile, dict):
            profile = {}
        profile["billing_cycle"] = normalized_cycle
        profile["pending_subscription"] = {
            "plan": normalized_plan,
            "billing_cycle": normalized_cycle,
            "payment_mode": "order",
            "order_id": order.get("id"),
            "created_at": datetime.utcnow().isoformat(),
        }
        organisation.org_profile_data = profile
        flag_modified(organisation, "org_profile_data")

        db_session.commit()
        return order
    except Exception as exc:
        logger.error(
            "Razorpay order creation failed org_id=%s payload=%s error=%s",
            organisation.id,
            json.dumps(payload, default=str),
            str(exc),
        )
        db_session.rollback()
        return None


def cancel_subscription(organisation, db_session):
    if organisation is None or not organisation.razorpay_subscription_id:
        return {"success": False, "cancel_at": None}

    client = get_razorpay_client()
    try:
        try:
            response = client.subscription.cancel(
                organisation.razorpay_subscription_id,
                cancel_at_cycle_end=True,
            )
        except TypeError:
            response = client.subscription.cancel(
                organisation.razorpay_subscription_id,
                {"cancel_at_cycle_end": 1},
            )

        organisation.subscription_status = "cancelled"
        db_session.commit()

        cancel_at = response.get("cancel_at") or response.get("current_end")
        return {
            "success": True,
            "cancel_at": _format_unix_date(cancel_at),
        }
    except Exception:
        logger.exception(
            "Razorpay subscription cancellation failed org_id=%s subscription_id=%s",
            organisation.id,
            organisation.razorpay_subscription_id,
        )
        db_session.rollback()
        return {"success": False, "cancel_at": None}


def verify_payment_signature(razorpay_payment_id, razorpay_subscription_id, razorpay_signature) -> bool:
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "")
    if not key_secret:
        return False

    expected = hmac.new(
        key_secret.encode(),
        f"{razorpay_payment_id}|{razorpay_subscription_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(razorpay_signature or ""))


def verify_order_payment_signature(razorpay_order_id, razorpay_payment_id, razorpay_signature) -> bool:
    key_secret = current_app.config.get("RAZORPAY_KEY_SECRET", "")
    if not key_secret:
        return False

    expected = hmac.new(
        key_secret.encode(),
        f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(razorpay_signature or ""))


def verify_webhook_signature(payload_body, webhook_signature) -> bool:
    webhook_secret = current_app.config.get("RAZORPAY_WEBHOOK_SECRET", "")
    if not webhook_secret or not webhook_signature:
        return False

    body = payload_body if isinstance(payload_body, (bytes, bytearray)) else str(payload_body or "").encode()
    expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, str(webhook_signature))


def get_subscription_details(subscription_id):
    if not subscription_id:
        return None

    try:
        client = get_razorpay_client()
        return client.subscription.fetch(subscription_id)
    except Exception:
        logger.exception("Failed fetching Razorpay subscription details subscription_id=%s", subscription_id)
        return None


def get_invoice_history(organisation, count=12) -> list[dict]:
    if organisation is None or not organisation.razorpay_subscription_id:
        return []

    try:
        client = get_razorpay_client()
        response = client.invoice.all(
            {
                "type": "invoice",
                "subscription_id": organisation.razorpay_subscription_id,
                "count": int(count or 12),
            }
        )
    except Exception:
        logger.exception(
            "Failed to fetch invoice history org_id=%s subscription_id=%s",
            organisation.id,
            organisation.razorpay_subscription_id,
        )
        return []

    rows = response.get("items", []) if isinstance(response, dict) else []
    invoices = []
    for row in rows:
        amount_due = row.get("amount_due")
        if amount_due is None:
            amount_due = row.get("amount")
        invoices.append(
            {
                "invoice_id": row.get("id"),
                "invoice_number": row.get("invoice_number") or row.get("receipt") or "-",
                "amount_inr": _as_float_inr(amount_due),
                "status": row.get("status", "unknown"),
                "billing_start": _format_unix_date(row.get("period_start")),
                "billing_end": _format_unix_date(row.get("period_end")),
                "pdf_url": row.get("short_url") or row.get("pdf_url"),
            }
        )
    return invoices


def enforce_plan_limits(organisation, resource_type, db_session) -> dict:
    if organisation is None:
        return {"allowed": False, "current_count": 0, "limit": 0, "plan": "starter"}

    if resource_type not in {"shipments", "carriers", "users"}:
        raise ValueError("resource_type must be one of 'shipments', 'carriers', 'users'")

    plan_name = (organisation.subscription_plan or "starter").strip().lower()
    plan = RAZORPAY_PLANS.get(plan_name, RAZORPAY_PLANS["starter"])

    if resource_type == "shipments":
        current_count = (
            db_session.query(Shipment)
            .filter(
                Shipment.organisation_id == organisation.id,
                Shipment.is_archived.is_(False),
                Shipment.status != "cancelled",
            )
            .count()
        )
        limit = plan.get("shipment_limit")
    elif resource_type == "users":
        current_count = (
            db_session.query(User)
            .filter(
                User.organisation_id == organisation.id,
                User._is_active.is_(True),
            )
            .count()
        )
        limit = plan.get("user_limit")
    else:
        profile = organisation.org_profile_data or {}
        if not isinstance(profile, dict):
            profile = {}
        creds = profile.get("carrier_credentials", {})
        if not isinstance(creds, dict):
            creds = {}
        current_count = len(creds)
        limit = plan.get("carrier_limit")

    if limit is None:
        return {
            "allowed": True,
            "current_count": int(current_count),
            "limit": None,
            "plan": plan_name,
        }

    return {
        "allowed": int(current_count) < int(limit),
        "current_count": int(current_count),
        "limit": int(limit),
        "plan": plan_name,
    }


def get_usage_meters(organisation, db_session) -> dict:
    if organisation is None:
        return {
            "shipments": {"used": 0, "limit": 0, "percentage": 0.0},
            "carriers": {"used": 0, "limit": 0, "percentage": 0.0},
            "users": {"used": 0, "limit": 0, "percentage": 0.0},
            "plan": "starter",
            "billing_cycle": "monthly",
            "next_billing_date": None,
            "subscription_status": "expired",
        }

    shipment_usage = enforce_plan_limits(organisation, "shipments", db_session)
    carrier_usage = enforce_plan_limits(organisation, "carriers", db_session)
    user_usage = enforce_plan_limits(organisation, "users", db_session)

    def pct(used, limit):
        if limit in (None, 0):
            return 0.0
        return round(min((float(used) / float(limit)) * 100.0, 100.0), 2)

    profile = organisation.org_profile_data or {}
    if not isinstance(profile, dict):
        profile = {}

    next_billing_date = None
    if organisation.razorpay_subscription_id:
        details = get_subscription_details(organisation.razorpay_subscription_id)
        if isinstance(details, dict):
            next_billing_date = _format_unix_date(details.get("current_end") or details.get("charge_at"))

    if not next_billing_date and organisation.is_trial_active and organisation.trial_ends_at:
        next_billing_date = organisation.trial_ends_at.strftime("%d %b %Y")

    if not next_billing_date:
        manual_billing = profile.get("manual_billing", {})
        if isinstance(manual_billing, dict):
            next_billing_date = _format_iso_date(manual_billing.get("next_due_at"))

    return {
        "shipments": {
            "used": shipment_usage["current_count"],
            "limit": shipment_usage["limit"],
            "percentage": pct(shipment_usage["current_count"], shipment_usage["limit"]),
        },
        "carriers": {
            "used": carrier_usage["current_count"],
            "limit": carrier_usage["limit"],
            "percentage": pct(carrier_usage["current_count"], carrier_usage["limit"]),
        },
        "users": {
            "used": user_usage["current_count"],
            "limit": user_usage["limit"],
            "percentage": pct(user_usage["current_count"], user_usage["limit"]),
        },
        "plan": plan_name if (plan_name := (organisation.subscription_plan or "starter")) else "starter",
        "billing_cycle": profile.get("billing_cycle", "monthly"),
        "next_billing_date": next_billing_date,
        "subscription_status": organisation.subscription_status,
    }


__all__ = [
    "RAZORPAY_PLANS",
    "resolve_plan_configuration",
    "get_razorpay_client",
    "create_razorpay_customer",
    "create_subscription",
    "create_one_time_order",
    "cancel_subscription",
    "verify_payment_signature",
    "verify_order_payment_signature",
    "verify_webhook_signature",
    "get_subscription_details",
    "get_invoice_history",
    "enforce_plan_limits",
    "get_usage_meters",
]
