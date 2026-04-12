"""Platform-level SuperAdmin routes for ChainWatch Pro."""

from __future__ import annotations

import csv
import io
import json
import logging
import uuid
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_user
from sqlalchemy import func, or_, text

from app.extensions import db, get_redis_client
from app.models.ai_generated_content import AIGeneratedContent
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.carrier_performance import CarrierPerformance
from app.models.disruption_score import DisruptionScore
from app.models.feature_flag import FeatureFlag
from app.models.organisation import Organisation
from app.models.route_recommendation import RouteRecommendation
from app.models.shipment import Shipment
from app.models.user import User
from app.services import razorpay_service
from app.services.notification import email_service
from app.utils.helpers import generate_secure_temporary_password

logger = logging.getLogger(__name__)

# Security note: keep this URL prefix configurable via SUPERADMIN_URL_PREFIX in production.
superadmin_bp = Blueprint("superadmin", __name__)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _parse_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _response_wants_json() -> bool:
    if request.is_json:
        return True
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept


def _platform_org() -> Organisation:
    org = Organisation.query.filter_by(name="ChainWatch Pro Internal").first()
    if org is None:
        org = Organisation(
            name="ChainWatch Pro Internal",
            industry="SaaS",
            subscription_plan="enterprise",
            subscription_status="active",
            onboarding_complete=True,
            is_active=True,
        )
        db.session.add(org)
        db.session.commit()
    return org


def _log_superadmin_action(
    event_type: str,
    description: str,
    organisation_id=None,
    metadata: dict | None = None,
    actor_user=None,
) -> None:
    actor = actor_user or current_user
    target_org_id = organisation_id or _platform_org().id

    entry = AuditLog(
        organisation_id=target_org_id,
        actor_user_id=getattr(actor, "id", None),
        actor_label=f"SuperAdmin:{getattr(actor, 'email', 'system')}",
        event_type=event_type,
        description=description,
        metadata_json=metadata or {},
        ip_address=request.remote_addr,
    )
    db.session.add(entry)
    db.session.commit()


def _is_recent_superadmin_elevation(max_age_minutes: int = 15) -> bool:
    elevated_at_raw = session.get("superadmin_last_elevated_at")
    elevated_at = _parse_iso_datetime(elevated_at_raw)
    if elevated_at is None:
        return False
    return datetime.utcnow() - elevated_at <= timedelta(minutes=max_age_minutes)


def _require_recent_superadmin_elevation():
    if _is_recent_superadmin_elevation(15):
        return None

    # Redirecting back to a POST-only endpoint causes a 405 after re-auth.
    # For non-GET actions, prefer the referring management page instead.
    next_url = request.url if request.method == "GET" else (request.referrer or url_for("superadmin.dashboard"))

    if _response_wants_json():
        return (
            jsonify(
                {
                    "success": False,
                    "reauth_required": True,
                    "reauth_url": url_for("superadmin.re_authenticate", next=next_url),
                }
            ),
            401,
        )

    flash("Please re-authenticate before performing this sensitive action.", "warning")
    return redirect(url_for("superadmin.re_authenticate", next=next_url))


def _org_admin_user(organisation_id) -> User | None:
    admin = (
        User.query.filter(
            User.organisation_id == organisation_id,
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
            User.organisation_id == organisation_id,
            User._is_active.is_(True),
        )
        .order_by(User.created_at.asc())
        .first()
    )


@superadmin_bp.before_request
def _guard_superadmin_routes():
    endpoint = request.endpoint or ""

    if endpoint == "superadmin.exit_impersonation" and session.get("superadmin_impersonating"):
        return None

    if not current_user.is_authenticated:
        return redirect(url_for("auth.login"))

    if current_user.role != "superadmin":
        abort(404)

    return None


@superadmin_bp.get("")
def dashboard():
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)

    total_organisations = Organisation.query.count()
    total_users = User.query.count()
    total_active_shipments = (
        Shipment.query.filter(
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
        ).count()
    )

    alerts_today = Alert.query.filter(Alert.created_at >= today_start).count()
    gemini_calls_today = AIGeneratedContent.query.filter(AIGeneratedContent.created_at >= today_start).count()

    plan_breakdown_raw = (
        db.session.query(Organisation.subscription_plan, func.count(Organisation.id))
        .group_by(Organisation.subscription_plan)
        .all()
    )
    plan_breakdown = {row[0]: int(row[1]) for row in plan_breakdown_raw}
    total_active_subscriptions = sum(
        int(count)
        for plan, count in plan_breakdown.items()
        if plan in {"starter", "professional", "enterprise"}
    )

    mrr_inr = 0.0
    for org in Organisation.query.filter(Organisation.subscription_status == "active").all():
        plan = razorpay_service.RAZORPAY_PLANS.get(
            org.subscription_plan,
            razorpay_service.RAZORPAY_PLANS["starter"],
        )
        paise = plan.get("price_monthly_inr")
        if paise:
            mrr_inr += float(paise) / 100.0

    new_orgs_this_week = Organisation.query.filter(
        Organisation.created_at >= now - timedelta(days=7)
    ).count()

    activity_rows = (
        db.session.query(AuditLog, Organisation.name)
        .outerjoin(Organisation, Organisation.id == AuditLog.organisation_id)
        .order_by(AuditLog.created_at.desc())
        .limit(50)
        .all()
    )
    recent_activity = [
        {
            "event": row[0],
            "organisation_name": row[1] or "Unknown",
        }
        for row in activity_rows
    ]

    at_risk_organisations = (
        Organisation.query.filter(
            or_(
                Organisation.subscription_status == "expired",
                Organisation.trial_ends_at <= now + timedelta(days=3),
            )
        )
        .order_by(Organisation.trial_ends_at.asc().nullsfirst(), Organisation.created_at.asc())
        .limit(25)
        .all()
    )

    return render_template(
        "superadmin/dashboard.html",
        total_organisations=total_organisations,
        total_active_subscriptions=total_active_subscriptions,
        plan_breakdown=plan_breakdown,
        total_users=total_users,
        total_active_shipments=total_active_shipments,
        alerts_today=alerts_today,
        gemini_calls_today=gemini_calls_today,
        mrr_inr=round(mrr_inr, 2),
        new_orgs_this_week=new_orgs_this_week,
        recent_activity=recent_activity,
        at_risk_organisations=at_risk_organisations,
    )


@superadmin_bp.get("/organisations")
def organisations():
    page = max(request.args.get("page", default=1, type=int), 1)
    search = (request.args.get("q") or "").strip()
    plan = (request.args.get("plan") or "").strip().lower()
    status = (request.args.get("status") or "").strip().lower()

    query = Organisation.query
    if search:
        query = query.filter(Organisation.name.ilike(f"%{search}%"))
    if plan in {"starter", "professional", "enterprise"}:
        query = query.filter(Organisation.subscription_plan == plan)
    if status in {"active", "trial", "expired", "cancelled"}:
        query = query.filter(Organisation.subscription_status == status)

    pagination = query.order_by(Organisation.created_at.desc()).paginate(page=page, per_page=50, error_out=False)

    org_rows = []
    for org in pagination.items:
        users_count = User.query.filter(
            User.organisation_id == org.id,
            User._is_active.is_(True),
        ).count()
        shipments_count = Shipment.query.filter(
            Shipment.organisation_id == org.id,
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
        ).count()
        last_activity = (
            AuditLog.query.filter(AuditLog.organisation_id == org.id)
            .order_by(AuditLog.created_at.desc())
            .first()
        )

        org_rows.append(
            {
                "organisation": org,
                "users_count": users_count,
                "active_shipments_count": shipments_count,
                "last_activity": last_activity.created_at if last_activity else None,
            }
        )

    return render_template(
        "superadmin/organisations.html",
        pagination=pagination,
        org_rows=org_rows,
        filters={"q": search, "plan": plan, "status": status},
    )


@superadmin_bp.get("/organisations/<uuid:org_id>")
def organisation_detail(org_id: uuid.UUID):
    organisation = Organisation.query.filter_by(id=org_id).first_or_404()

    users = (
        User.query.filter(User.organisation_id == organisation.id)
        .order_by(User.created_at.asc())
        .all()
    )

    audit_entries = (
        AuditLog.query.filter(AuditLog.organisation_id == organisation.id)
        .order_by(AuditLog.created_at.desc())
        .limit(100)
        .all()
    )

    usage_stats = {
        "shipments": Shipment.query.filter(Shipment.organisation_id == organisation.id).count(),
        "alerts": Alert.query.filter(Alert.organisation_id == organisation.id).count(),
        "ai_calls": AIGeneratedContent.query.filter(
            AIGeneratedContent.organisation_id == organisation.id
        ).count(),
        "reports": AuditLog.query.filter(
            AuditLog.organisation_id == organisation.id,
            AuditLog.event_type == "report_generated",
        ).count(),
    }

    subscription_history = (
        AuditLog.query.filter(
            AuditLog.organisation_id == organisation.id,
            AuditLog.event_type.in_(
                [
                    "subscription_upgraded",
                    "subscription_charged",
                    "subscription_cancelled",
                    "subscription_payment_failed",
                    "subscription_overridden",
                ]
            ),
        )
        .order_by(AuditLog.created_at.desc())
        .limit(25)
        .all()
    )

    return render_template(
        "superadmin/organisation_detail.html",
        organisation=organisation,
        users=users,
        audit_entries=audit_entries,
        usage_stats=usage_stats,
        subscription_history=subscription_history,
    )


@superadmin_bp.post("/organisations/<uuid:org_id>/edit")
def edit_organisation(org_id: uuid.UUID):
    organisation = Organisation.query.filter_by(id=org_id).first_or_404()

    previous = organisation.to_dict()

    organisation.name = (request.form.get("name") or organisation.name).strip()
    organisation.industry = (request.form.get("industry") or organisation.industry or "").strip() or None

    plan = (request.form.get("subscription_plan") or organisation.subscription_plan).strip().lower()
    if plan in {"starter", "professional", "enterprise"}:
        organisation.subscription_plan = plan

    status = (request.form.get("subscription_status") or organisation.subscription_status).strip().lower()
    if status in {"active", "trial", "expired", "cancelled"}:
        organisation.subscription_status = status

    trial_ends_raw = (request.form.get("trial_ends_at") or "").strip()
    if trial_ends_raw:
        try:
            organisation.trial_ends_at = datetime.fromisoformat(trial_ends_raw)
        except ValueError:
            pass

    organisation.is_active = bool(request.form.get("is_active") == "on")

    profile = organisation.org_profile_data or {}
    if not isinstance(profile, dict):
        profile = {}

    overrides = profile.get("plan_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}

    for key in ["shipment_limit", "carrier_limit", "user_limit"]:
        raw = (request.form.get(key) or "").strip()
        if raw == "":
            overrides.pop(key, None)
            continue
        try:
            overrides[key] = int(raw)
        except ValueError:
            continue

    profile["plan_overrides"] = overrides
    organisation.org_profile_data = profile

    db.session.commit()

    _log_superadmin_action(
        event_type="organisation_edited",
        description=f"Updated organisation {organisation.name}.",
        organisation_id=organisation.id,
        metadata={"before": previous, "after": organisation.to_dict()},
    )

    flash("Organisation updated successfully.", "success")
    return redirect(url_for("superadmin.organisation_detail", org_id=organisation.id))


@superadmin_bp.post("/organisations/<uuid:org_id>/override-subscription")
def override_subscription(org_id: uuid.UUID):
    organisation = Organisation.query.filter_by(id=org_id).first_or_404()

    override_reason = (request.form.get("override_reason") or "").strip()
    if not override_reason:
        return jsonify({"success": False, "error": "Override reason is required."}), 400

    plan = (request.form.get("subscription_plan") or organisation.subscription_plan).strip().lower()
    status = (request.form.get("subscription_status") or organisation.subscription_status).strip().lower()
    trial_ends_raw = (request.form.get("trial_ends_at") or "").strip()

    if plan in {"starter", "professional", "enterprise"}:
        organisation.subscription_plan = plan
    if status in {"active", "trial", "expired", "cancelled"}:
        organisation.subscription_status = status

    if trial_ends_raw:
        try:
            organisation.trial_ends_at = datetime.fromisoformat(trial_ends_raw)
        except ValueError:
            pass

    profile = organisation.org_profile_data or {}
    if not isinstance(profile, dict):
        profile = {}
    profile["subscription_override"] = {
        "reason": override_reason,
        "overridden_by": current_user.email,
        "overridden_at": datetime.utcnow().isoformat(),
    }
    organisation.org_profile_data = profile
    db.session.commit()

    _log_superadmin_action(
        event_type="subscription_overridden",
        description=f"Subscription override applied for {organisation.name}.",
        organisation_id=organisation.id,
        metadata={
            "subscription_plan": organisation.subscription_plan,
            "subscription_status": organisation.subscription_status,
            "trial_ends_at": organisation.trial_ends_at.isoformat() if organisation.trial_ends_at else None,
            "reason": override_reason,
        },
    )

    flash("Subscription override applied successfully.", "success")
    return redirect(url_for("superadmin.organisation_detail", org_id=organisation.id))


@superadmin_bp.post("/organisations/<uuid:org_id>/impersonate")
def impersonate_org(org_id: uuid.UUID):
    organisation = Organisation.query.filter_by(id=org_id).first_or_404()
    admin_user = _org_admin_user(organisation.id)
    if admin_user is None:
        flash("No active organisation admin found for impersonation.", "danger")
        return redirect(url_for("superadmin.organisations"))

    original_user_id = str(current_user.id)
    original_email = current_user.email

    session["superadmin_impersonating"] = True
    session["superadmin_original_user_id"] = original_user_id
    session["impersonating_org_id"] = str(organisation.id)
    session["impersonation_started_at"] = datetime.utcnow().isoformat()

    _log_superadmin_action(
        event_type="superadmin_impersonation_started",
        description=f"Started impersonation for {organisation.name} as {admin_user.email}.",
        organisation_id=organisation.id,
        metadata={
            "impersonated_org": organisation.name,
            "impersonated_user_email": admin_user.email,
            "superadmin_email": original_email,
        },
    )

    login_user(admin_user)
    flash("Impersonation started. You are now viewing the organisation as its admin user.", "warning")
    return redirect("/dashboard")


@superadmin_bp.get("/exit-impersonation")
def exit_impersonation():
    if not session.get("superadmin_impersonating"):
        return redirect(url_for("superadmin.dashboard"))

    original_user_id = _parse_uuid(session.get("superadmin_original_user_id"))
    impersonated_org_id = _parse_uuid(session.get("impersonating_org_id"))

    original_user = User.query.filter_by(id=original_user_id, role="superadmin").first()
    if original_user is None:
        session.pop("superadmin_impersonating", None)
        session.pop("superadmin_original_user_id", None)
        session.pop("impersonating_org_id", None)
        session.pop("impersonation_started_at", None)
        flash("Original SuperAdmin session could not be restored. Please sign in again.", "danger")
        return redirect(url_for("auth.login"))

    login_user(original_user)
    session["superadmin_last_elevated_at"] = datetime.utcnow().isoformat()

    session.pop("superadmin_impersonating", None)
    session.pop("superadmin_original_user_id", None)
    session.pop("impersonating_org_id", None)
    session.pop("impersonation_started_at", None)

    _log_superadmin_action(
        event_type="superadmin_impersonation_ended",
        description="Exited impersonation mode.",
        organisation_id=impersonated_org_id or _platform_org().id,
        actor_user=original_user,
    )

    flash("Exited impersonation mode.", "success")
    if impersonated_org_id:
        return redirect(url_for("superadmin.organisation_detail", org_id=impersonated_org_id))
    return redirect(url_for("superadmin.dashboard"))


@superadmin_bp.post("/organisations/<uuid:org_id>/suspend")
def suspend_organisation(org_id: uuid.UUID):
    organisation = Organisation.query.filter_by(id=org_id).first_or_404()

    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("Suspension reason is required.", "danger")
        return redirect(url_for("superadmin.organisation_detail", org_id=organisation.id))

    organisation.subscription_status = "cancelled"
    organisation.is_active = False
    db.session.commit()

    admin_user = _org_admin_user(organisation.id)
    if admin_user is not None:
        email_service.send_org_suspension_email(admin_user, organisation, reason)

    _log_superadmin_action(
        event_type="organisation_suspended",
        description=f"Suspended organisation {organisation.name}.",
        organisation_id=organisation.id,
        metadata={"reason": reason},
    )

    flash(f"Organisation {organisation.name} has been suspended.", "warning")
    return redirect(url_for("superadmin.organisation_detail", org_id=organisation.id))


@superadmin_bp.get("/organisations/<uuid:org_id>/delete")
def delete_organisation_get(org_id: uuid.UUID):
    flash("Hard delete must be submitted from the confirmation form.", "warning")
    return redirect(url_for("superadmin.organisation_detail", org_id=org_id))


@superadmin_bp.post("/organisations/<uuid:org_id>/delete")
def delete_organisation(org_id: uuid.UUID):
    reauth_response = _require_recent_superadmin_elevation()
    if reauth_response is not None:
        return reauth_response

    organisation = Organisation.query.filter_by(id=org_id).first_or_404()

    confirm_text = (request.form.get("confirm_text") or "")
    if confirm_text != organisation.name:
        return jsonify({"success": False, "error": "Confirmation text does not match organisation name."}), 400

    admin_user = _org_admin_user(organisation.id)
    admin_email = admin_user.email if admin_user else None
    org_name = organisation.name

    _log_superadmin_action(
        event_type="organisation_deleted",
        description=f"Hard deleted organisation {org_name}.",
        organisation_id=_platform_org().id,
        metadata={"deleted_org_name": org_name, "deleted_org_id": str(organisation.id)},
    )

    shipment_ids_query = db.session.query(Shipment.id).filter(Shipment.organisation_id == organisation.id)

    try:
        AuditLog.query.filter(AuditLog.organisation_id == organisation.id).delete(synchronize_session=False)
        Alert.query.filter(Alert.organisation_id == organisation.id).delete(synchronize_session=False)
        DisruptionScore.query.filter(
            DisruptionScore.shipment_id.in_(shipment_ids_query)
        ).delete(synchronize_session=False)
        RouteRecommendation.query.filter(
            RouteRecommendation.shipment_id.in_(shipment_ids_query)
        ).delete(synchronize_session=False)
        Shipment.query.filter(Shipment.organisation_id == organisation.id).delete(synchronize_session=False)
        CarrierPerformance.query.filter(
            CarrierPerformance.organisation_id == organisation.id
        ).delete(synchronize_session=False)
        AIGeneratedContent.query.filter(
            AIGeneratedContent.organisation_id == organisation.id
        ).delete(synchronize_session=False)
        User.query.filter(User.organisation_id == organisation.id).delete(synchronize_session=False)
        Organisation.query.filter(Organisation.id == organisation.id).delete(synchronize_session=False)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed hard delete for organisation_id=%s", organisation.id)
        return jsonify({"success": False, "error": "Hard delete failed. Review server logs."}), 500

    if admin_email:
        email_service.send_data_deletion_confirmation_email(admin_email, org_name)

    flash(f"Organisation {org_name} and all related data have been permanently deleted.", "success")
    return redirect(url_for("superadmin.organisations"))


@superadmin_bp.get("/users")
def users():
    page = max(request.args.get("page", default=1, type=int), 1)
    search = (request.args.get("q") or "").strip()
    org_filter = _parse_uuid(request.args.get("org_id"))
    role_filter = (request.args.get("role") or "").strip().lower()
    status_filter = (request.args.get("status") or "").strip().lower()
    account_source_filter = (request.args.get("account_source") or "").strip().lower()

    query = User.query.join(Organisation, Organisation.id == User.organisation_id)
    if search:
        query = query.filter(
            or_(
                User.email.ilike(f"%{search}%"),
                User.first_name.ilike(f"%{search}%"),
                User.last_name.ilike(f"%{search}%"),
            )
        )

    if org_filter:
        query = query.filter(User.organisation_id == org_filter)
    if role_filter in {"superadmin", "admin", "manager", "viewer"}:
        query = query.filter(User.role == role_filter)
    if status_filter == "active":
        query = query.filter(User._is_active.is_(True))
    elif status_filter == "inactive":
        query = query.filter(User._is_active.is_(False))

    if account_source_filter:
        query = query.filter(User.account_source == account_source_filter)

    pagination = query.order_by(User.created_at.desc()).paginate(page=page, per_page=50, error_out=False)

    organisations_list = Organisation.query.order_by(Organisation.name.asc()).all()
    return render_template(
        "superadmin/users.html",
        pagination=pagination,
        organisations=organisations_list,
        filters={
            "q": search,
            "org_id": str(org_filter) if org_filter else "",
            "role": role_filter,
            "status": status_filter,
            "account_source": account_source_filter,
        },
    )


@superadmin_bp.post("/users/<uuid:user_id>/reset-password")
def force_reset_user_password(user_id: uuid.UUID):
    user = User.query.filter_by(id=user_id).first_or_404()

    temp_password = generate_secure_temporary_password()
    user.set_password(temp_password)
    user.temporary_password_hash = user.password_hash
    user.must_change_password = True
    user.invitation_sent_at = datetime.utcnow()
    user.invited_by_user_id = current_user.id
    db.session.commit()

    email_service.send_team_invitation_email_with_credentials(
        current_user,
        user,
        temp_password,
        current_app._get_current_object(),
    )

    _log_superadmin_action(
        event_type="superadmin_forced_password_reset",
        description=f"Forced password reset for {user.email}.",
        organisation_id=user.organisation_id,
        metadata={"target_user_email": user.email},
    )

    return jsonify({"success": True, "message": f"Temporary password sent to {user.email}"})


@superadmin_bp.post("/users/<uuid:user_id>/deactivate")
def deactivate_user(user_id: uuid.UUID):
    user = User.query.filter_by(id=user_id).first_or_404()

    if user.role == "superadmin":
        active_superadmins = User.query.filter(User.role == "superadmin", User._is_active.is_(True)).count()
        if active_superadmins <= 1:
            return jsonify({"success": False, "error": "Cannot deactivate the last SuperAdmin."}), 400

    user.is_active = False
    db.session.commit()

    _log_superadmin_action(
        event_type="superadmin_user_deactivated",
        description=f"Deactivated user {user.email}.",
        organisation_id=user.organisation_id,
        metadata={"target_user_email": user.email},
    )

    return jsonify({"success": True, "message": f"User {user.email} deactivated"})


@superadmin_bp.post("/users/<uuid:user_id>/promote-admin")
def promote_user_to_admin(user_id: uuid.UUID):
    user = User.query.filter_by(id=user_id).first_or_404()
    if user.role == "superadmin":
        return jsonify({"success": False, "error": "SuperAdmin role cannot be changed from this action."}), 400

    user.role = "admin"
    db.session.commit()

    _log_superadmin_action(
        event_type="superadmin_user_promoted_admin",
        description=f"Promoted {user.email} to admin role.",
        organisation_id=user.organisation_id,
        metadata={"target_user_email": user.email},
    )

    return jsonify({"success": True, "message": f"{user.email} promoted to admin"})


@superadmin_bp.post("/users/<uuid:user_id>/grant-superadmin")
def grant_superadmin(user_id: uuid.UUID):
    reauth_response = _require_recent_superadmin_elevation()
    if reauth_response is not None:
        return reauth_response

    target_user = User.query.filter_by(id=user_id).first_or_404()

    payload = request.get_json(silent=True) if request.is_json else {}
    reason = request.form.get("reason") or (payload.get("reason") if isinstance(payload, dict) else "")
    reason = (reason or "").strip()
    confirm = request.form.get("confirm") or (payload.get("confirm") if isinstance(payload, dict) else False)
    confirmed = bool(confirm in [True, "true", "on", "1", 1])

    if not reason:
        return jsonify({"success": False, "error": "Reason is required."}), 400
    if not confirmed:
        return jsonify({"success": False, "error": "Confirmation is required."}), 400
    if target_user.role == "superadmin":
        return jsonify({"success": False, "error": "User is already a SuperAdmin."}), 400

    target_user.role = "superadmin"
    target_user.superadmin_notes = reason
    db.session.commit()

    email_service.send_superadmin_role_change_email(target_user, granted=True, reason=reason)

    _log_superadmin_action(
        event_type="superadmin_role_granted",
        description=f"Granted SuperAdmin role to {target_user.email}.",
        organisation_id=target_user.organisation_id,
        metadata={"target_user_email": target_user.email, "reason": reason},
    )

    return jsonify({"success": True, "message": f"SuperAdmin granted to {target_user.email}"})


@superadmin_bp.post("/users/<uuid:user_id>/revoke-superadmin")
def revoke_superadmin(user_id: uuid.UUID):
    target_user = User.query.filter_by(id=user_id).first_or_404()
    if target_user.role != "superadmin":
        return jsonify({"success": False, "error": "Target user is not a SuperAdmin."}), 400

    superadmin_count = User.query.filter(User.role == "superadmin", User._is_active.is_(True)).count()
    if superadmin_count <= 1:
        return jsonify({"success": False, "error": "Cannot revoke the last SuperAdmin."}), 400

    target_user.role = "admin"
    previous_notes = (target_user.superadmin_notes or "").strip()
    revocation_note = f"Revoked by {current_user.email} at {datetime.utcnow().isoformat()}"
    target_user.superadmin_notes = f"{previous_notes}\n{revocation_note}".strip()
    db.session.commit()

    email_service.send_superadmin_role_change_email(target_user, granted=False)

    _log_superadmin_action(
        event_type="superadmin_role_revoked",
        description=f"Revoked SuperAdmin role for {target_user.email}.",
        organisation_id=target_user.organisation_id,
        metadata={"target_user_email": target_user.email},
    )

    return jsonify({"success": True, "message": f"SuperAdmin revoked for {target_user.email}"})


@superadmin_bp.get("/platform-stats")
def platform_stats():
    today = datetime.utcnow().date()
    thirty_days_ago = today - timedelta(days=29)

    ship_rows = (
        db.session.query(func.date(Shipment.created_at), func.count(Shipment.id))
        .filter(func.date(Shipment.created_at) >= thirty_days_ago)
        .group_by(func.date(Shipment.created_at))
        .all()
    )
    ship_map = {str(row[0]): int(row[1]) for row in ship_rows}

    alert_rows = (
        db.session.query(func.date(Alert.created_at), func.count(Alert.id))
        .filter(func.date(Alert.created_at) >= thirty_days_ago)
        .group_by(func.date(Alert.created_at))
        .all()
    )
    alert_map = {str(row[0]): int(row[1]) for row in alert_rows}

    ai_rows = (
        db.session.query(func.date(AIGeneratedContent.created_at), func.count(AIGeneratedContent.id))
        .filter(func.date(AIGeneratedContent.created_at) >= thirty_days_ago)
        .group_by(func.date(AIGeneratedContent.created_at))
        .all()
    )
    ai_map = {str(row[0]): int(row[1]) for row in ai_rows}

    org_active_rows = (
        db.session.query(func.date(AuditLog.created_at), func.count(func.distinct(AuditLog.organisation_id)))
        .filter(func.date(AuditLog.created_at) >= thirty_days_ago)
        .group_by(func.date(AuditLog.created_at))
        .all()
    )
    active_org_map = {str(row[0]): int(row[1]) for row in org_active_rows}

    labels = []
    shipments_series = []
    alerts_series = []
    ai_series = []
    active_org_series = []

    for offset in range(30):
        day = thirty_days_ago + timedelta(days=offset)
        key = day.isoformat()
        labels.append(day.strftime("%d %b"))
        shipments_series.append(ship_map.get(key, 0))
        alerts_series.append(alert_map.get(key, 0))
        ai_series.append(ai_map.get(key, 0))
        active_org_series.append(active_org_map.get(key, 0))

    weekly_buckets = {}
    for org in Organisation.query.order_by(Organisation.created_at.asc()).all():
        created = (org.created_at or datetime.utcnow()).date()
        week_start = created - timedelta(days=created.weekday())
        weekly_buckets[week_start] = weekly_buckets.get(week_start, 0) + 1

    sorted_weeks = sorted(weekly_buckets.items(), key=lambda item: item[0])[-12:]
    weekly_labels = [week.strftime("%d %b") for week, _ in sorted_weeks]
    weekly_counts = [count for _, count in sorted_weeks]

    plan_distribution_rows = (
        db.session.query(Organisation.subscription_plan, func.count(Organisation.id))
        .group_by(Organisation.subscription_plan)
        .all()
    )
    plan_labels = [row[0].title() for row in plan_distribution_rows]
    plan_counts = [int(row[1]) for row in plan_distribution_rows]

    top_active_orgs = (
        db.session.query(Organisation.name, func.count(Shipment.id).label("shipment_count"))
        .join(Shipment, Shipment.organisation_id == Organisation.id)
        .group_by(Organisation.id, Organisation.name)
        .order_by(func.count(Shipment.id).desc())
        .limit(10)
        .all()
    )

    top_drs_orgs = (
        db.session.query(Organisation.name, func.avg(Shipment.disruption_risk_score).label("avg_drs"))
        .join(Shipment, Shipment.organisation_id == Organisation.id)
        .filter(Shipment.is_archived.is_(False))
        .group_by(Organisation.id, Organisation.name)
        .order_by(func.avg(Shipment.disruption_risk_score).desc())
        .limit(10)
        .all()
    )

    mrr_inr = 0.0
    for org in Organisation.query.filter(Organisation.subscription_status == "active").all():
        plan = razorpay_service.RAZORPAY_PLANS.get(
            org.subscription_plan,
            razorpay_service.RAZORPAY_PLANS["starter"],
        )
        paise = plan.get("price_monthly_inr")
        if paise:
            mrr_inr += float(paise) / 100.0

    return render_template(
        "superadmin/platform_stats.html",
        labels=labels,
        shipments_series=shipments_series,
        alerts_series=alerts_series,
        ai_series=ai_series,
        active_org_series=active_org_series,
        weekly_labels=weekly_labels,
        weekly_counts=weekly_counts,
        plan_labels=plan_labels,
        plan_counts=plan_counts,
        top_active_orgs=top_active_orgs,
        top_drs_orgs=top_drs_orgs,
        mrr_inr=round(mrr_inr, 2),
    )


@superadmin_bp.get("/audit-log")
def platform_audit_log():
    page = max(request.args.get("page", default=1, type=int), 1)
    org_id = _parse_uuid(request.args.get("org_id"))
    event_type = (request.args.get("event_type") or "").strip()
    actor = (request.args.get("actor") or "").strip()
    start_date_raw = (request.args.get("start_date") or "").strip()
    end_date_raw = (request.args.get("end_date") or "").strip()

    query = AuditLog.query
    if org_id:
        query = query.filter(AuditLog.organisation_id == org_id)
    if event_type:
        query = query.filter(AuditLog.event_type == event_type)
    if actor:
        query = query.filter(AuditLog.actor_label.ilike(f"%{actor}%"))
    if start_date_raw:
        try:
            start_date = datetime.strptime(start_date_raw, "%Y-%m-%d")
            query = query.filter(AuditLog.created_at >= start_date)
        except ValueError:
            pass
    if end_date_raw:
        try:
            end_date = datetime.strptime(end_date_raw, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(AuditLog.created_at < end_date)
        except ValueError:
            pass

    query = query.order_by(AuditLog.created_at.desc())

    if (request.args.get("export") or "").lower() == "csv":
        rows = query.limit(5000).all()
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(["Timestamp", "Organisation", "Event", "Actor", "Description", "IP", "Metadata"])

        org_map = {
            str(org.id): org.name
            for org in Organisation.query.filter(Organisation.id.in_([row.organisation_id for row in rows])).all()
        }
        for row in rows:
            writer.writerow(
                [
                    row.created_at.isoformat() if row.created_at else "",
                    org_map.get(str(row.organisation_id), "Unknown"),
                    row.event_type,
                    row.actor_label,
                    row.description,
                    row.ip_address or "",
                    json.dumps(row.metadata_json or {}),
                ]
            )

        response = current_app.response_class(stream.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=chainwatchpro_platform_audit_log.csv"
        return response

    pagination = query.paginate(page=page, per_page=50, error_out=False)
    organisations_list = Organisation.query.order_by(Organisation.name.asc()).all()
    event_types = [
        row[0]
        for row in db.session.query(AuditLog.event_type).distinct().order_by(AuditLog.event_type.asc()).all()
    ]

    return render_template(
        "superadmin/audit_log.html",
        pagination=pagination,
        organisations=organisations_list,
        event_types=event_types,
        filters={
            "org_id": str(org_id) if org_id else "",
            "event_type": event_type,
            "actor": actor,
            "start_date": start_date_raw,
            "end_date": end_date_raw,
        },
    )


@superadmin_bp.get("/gemini-usage")
def gemini_usage():
    total_cached_entries = AIGeneratedContent.query.count()

    by_type_rows = (
        db.session.query(AIGeneratedContent.content_type, func.count(AIGeneratedContent.id))
        .group_by(AIGeneratedContent.content_type)
        .all()
    )
    by_type = [{"content_type": row[0], "count": int(row[1])} for row in by_type_rows]

    stale_cutoff = datetime.utcnow() - timedelta(days=7)
    stale_count = AIGeneratedContent.query.filter(
        AIGeneratedContent.updated_at < stale_cutoff
    ).count()
    non_stale_count = max(total_cached_entries - stale_count, 0)
    cache_hit_rate = round((non_stale_count / total_cached_entries) * 100.0, 2) if total_cached_entries else 0.0

    regenerated_today = AIGeneratedContent.query.filter(
        AIGeneratedContent.updated_at >= datetime.utcnow() - timedelta(days=1),
        AIGeneratedContent.regeneration_count > 0,
    ).count()

    oldest_stale_entries = (
        AIGeneratedContent.query.filter(AIGeneratedContent.updated_at < stale_cutoff)
        .order_by(AIGeneratedContent.updated_at.asc())
        .limit(20)
        .all()
    )

    top_orgs_rows_query = (
        db.session.query(Organisation.id, Organisation.name, func.count(AIGeneratedContent.id).label("count"))
        .join(AIGeneratedContent, AIGeneratedContent.organisation_id == Organisation.id)
        .group_by(Organisation.id, Organisation.name)
        .order_by(func.count(AIGeneratedContent.id).desc())
        .limit(10)
        .all()
    )
    top_orgs_rows = [
        {"id": row[0], "name": row[1], "count": int(row[2])}
        for row in top_orgs_rows_query
    ]

    return render_template(
        "superadmin/gemini_usage.html",
        total_cached_entries=total_cached_entries,
        by_type=by_type,
        cache_hit_rate=cache_hit_rate,
        regenerated_today=regenerated_today,
        oldest_stale_entries=oldest_stale_entries,
        top_orgs_rows=top_orgs_rows,
        stale_cutoff=stale_cutoff,
    )


@superadmin_bp.post("/gemini-usage/clear-stale-cache")
def clear_stale_cache():
    cutoff = datetime.utcnow() - timedelta(days=7)
    updated = AIGeneratedContent.query.filter(
        AIGeneratedContent.updated_at < cutoff
    ).update({"is_stale": True}, synchronize_session=False)
    db.session.commit()

    _log_superadmin_action(
        event_type="gemini_cache_marked_stale",
        description="Marked stale Gemini cache entries older than 7 days.",
        metadata={"updated_rows": int(updated or 0)},
    )

    flash(f"Marked {int(updated or 0)} cached entries as stale.", "success")
    return redirect(url_for("superadmin.gemini_usage"))


@superadmin_bp.get("/system-health")
def system_health():
    now = datetime.utcnow()

    database_status = "green"
    database_error = None
    table_counts = {}
    longest_running_query = "Not available for current database backend"

    try:
        db.session.execute(text("SELECT 1"))
        table_counts = {
            "organisations": Organisation.query.count(),
            "users": User.query.count(),
            "shipments": Shipment.query.count(),
            "alerts": Alert.query.count(),
            "audit_logs": AuditLog.query.count(),
            "ai_generated_content": AIGeneratedContent.query.count(),
        }
        if "postgresql" in str(db.engine.url):
            longest = db.session.execute(
                text(
                    """
                    SELECT COALESCE(MAX(EXTRACT(EPOCH FROM (now() - query_start))), 0)
                    FROM pg_stat_activity
                    WHERE state != 'idle'
                    """
                )
            ).scalar()
            longest_running_query = f"{float(longest or 0):.2f} seconds"
    except Exception as exc:
        database_status = "red"
        database_error = str(exc)

    redis_status = "amber"
    redis_metrics = {"memory_used": "n/a", "db_size": 0}
    redis_client = get_redis_client()
    if redis_client is not None:
        try:
            info = redis_client.info()
            redis_status = "green"
            redis_metrics = {
                "memory_used": info.get("used_memory_human", "unknown"),
                "db_size": redis_client.dbsize(),
            }
        except Exception:
            redis_status = "red"

    celery_status = "amber"
    celery_metrics = {
        "workers_online": 0,
        "queue_sizes": {"high": 0, "default": 0, "low": 0},
    }
    try:
        from celery_worker import celery as celery_app  # noqa: WPS433

        inspect = celery_app.control.inspect(timeout=1)
        pings = inspect.ping() or {}
        celery_metrics["workers_online"] = len(pings)

        if redis_client is not None:
            for queue_name in ["high", "default", "low"]:
                celery_metrics["queue_sizes"][queue_name] = int(redis_client.llen(queue_name) or 0)

        celery_status = "green" if celery_metrics["workers_online"] > 0 else "amber"
    except Exception:
        celery_status = "red"

    last_invitation = (
        User.query.filter(User.invitation_sent_at.isnot(None))
        .order_by(User.invitation_sent_at.desc())
        .first()
    )
    smtp_errors_last_hour = AuditLog.query.filter(
        AuditLog.event_type.ilike("%email%failed%"),
        AuditLog.created_at >= now - timedelta(hours=1),
    ).count()
    email_status = "green" if smtp_errors_last_hour == 0 else "amber"

    last_gemini_success = (
        AIGeneratedContent.query.order_by(AIGeneratedContent.updated_at.desc()).first()
    )
    last_gemini_failure = (
        AuditLog.query.filter(AuditLog.event_type.in_(["gemini_api_failed", "ai_generation_failed"]))
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    gemini_status = "green" if last_gemini_success else "amber"

    return render_template(
        "superadmin/system_health.html",
        database_status=database_status,
        database_error=database_error,
        table_counts=table_counts,
        longest_running_query=longest_running_query,
        redis_status=redis_status,
        redis_metrics=redis_metrics,
        celery_status=celery_status,
        celery_metrics=celery_metrics,
        email_status=email_status,
        last_email_sent_at=last_invitation.invitation_sent_at if last_invitation else None,
        smtp_errors_last_hour=smtp_errors_last_hour,
        gemini_status=gemini_status,
        last_gemini_success=last_gemini_success.updated_at if last_gemini_success else None,
        last_gemini_failure=last_gemini_failure.created_at if last_gemini_failure else None,
    )


@superadmin_bp.route("/feature-flags", methods=["GET", "POST"])
def feature_flags():
    if request.method == "POST":
        flag_name = (request.form.get("flag_name") or "").strip().lower()
        description = (request.form.get("description") or "").strip()
        enabled_globally = bool(request.form.get("is_enabled_globally") == "on")
        enabled_for_plans = [item.strip().lower() for item in request.form.getlist("enabled_for_plans") if item.strip()]

        raw_org_ids = (request.form.get("enabled_for_org_ids") or "").strip()
        parsed_org_ids = []
        if raw_org_ids:
            for item in raw_org_ids.split(","):
                parsed = _parse_uuid(item.strip())
                if parsed:
                    parsed_org_ids.append(str(parsed))

        if not flag_name:
            flash("Feature flag name is required.", "danger")
            return redirect(url_for("superadmin.feature_flags"))

        flag = FeatureFlag.query.filter_by(flag_name=flag_name).first()
        if flag is None:
            flag = FeatureFlag(flag_name=flag_name)
            db.session.add(flag)

        flag.is_enabled_globally = enabled_globally
        flag.enabled_for_plans = enabled_for_plans or ["starter", "professional", "enterprise"]
        flag.enabled_for_org_ids = parsed_org_ids
        flag.description = description or None
        db.session.commit()

        _log_superadmin_action(
            event_type="feature_flag_updated",
            description=f"Updated feature flag {flag_name}.",
            metadata=flag.to_dict(),
        )

        flash(f"Feature flag '{flag_name}' updated.", "success")
        return redirect(url_for("superadmin.feature_flags"))

    flags = FeatureFlag.query.order_by(FeatureFlag.flag_name.asc()).all()
    organisations_list = Organisation.query.order_by(Organisation.name.asc()).all()
    return render_template(
        "superadmin/feature_flags.html",
        flags=flags,
        organisations=organisations_list,
    )


@superadmin_bp.get("/announcement")
def announcement():
    task_id = session.get("platform_announcement_task_id")
    task_status = None
    if task_id:
        try:
            from celery_worker import celery as celery_app  # noqa: WPS433

            async_result = celery_app.AsyncResult(task_id)
            task_status = async_result.status
        except Exception:
            task_status = "UNKNOWN"

    return render_template(
        "superadmin/announcement.html",
        plans=["starter", "professional", "enterprise"],
        organisations=Organisation.query.order_by(Organisation.name.asc()).all(),
        task_id=task_id,
        task_status=task_status,
    )


@superadmin_bp.post("/send-platform-announcement")
def send_platform_announcement():
    subject = (request.form.get("subject") or "").strip()
    message = (request.form.get("message") or "").strip()
    target_audience = (request.form.get("target_audience") or "all_admins").strip().lower()

    if not subject or not message:
        flash("Subject and message are required for platform announcements.", "danger")
        return redirect(url_for("superadmin.announcement"))

    query = User.query.filter(User._is_active.is_(True))
    if target_audience == "all_admins":
        query = query.filter(User.role == "admin")
    elif target_audience == "all_users":
        pass
    elif target_audience == "specific_plan":
        selected_plan = (request.form.get("target_plan") or "").strip().lower()
        query = query.join(Organisation, Organisation.id == User.organisation_id).filter(
            Organisation.subscription_plan == selected_plan,
            User.role == "admin",
        )
    elif target_audience == "specific_org":
        org_id = _parse_uuid(request.form.get("target_org_id"))
        if not org_id:
            flash("Please select a valid organisation.", "danger")
            return redirect(url_for("superadmin.announcement"))
        query = query.filter(User.organisation_id == org_id)

    recipients = sorted({user.email for user in query.all() if user.email})
    if not recipients:
        flash("No recipients matched the selected audience.", "warning")
        return redirect(url_for("superadmin.announcement"))

    task_id = None
    try:
        from celery_worker import send_platform_announcement_batch  # noqa: WPS433

        async_task = send_platform_announcement_batch.delay(recipients, subject, message)
        task_id = async_task.id
        session["platform_announcement_task_id"] = task_id
    except Exception:
        for email in recipients:
            email_service.send_platform_announcement_email(email, subject, message)

    _log_superadmin_action(
        event_type="platform_announcement_sent",
        description="Triggered platform announcement broadcast.",
        metadata={
            "subject": subject,
            "target_audience": target_audience,
            "recipient_count": len(recipients),
            "task_id": task_id,
        },
    )

    flash(f"Announcement queued for {len(recipients)} recipients.", "success")
    return redirect(url_for("superadmin.announcement"))


@superadmin_bp.route("/re-authenticate", methods=["GET", "POST"])
def re_authenticate():
    next_url = request.values.get("next") or url_for("superadmin.dashboard")

    if request.method == "POST":
        password = request.form.get("password") or ""
        if not current_user.check_password(password):
            flash("Incorrect password. Please try again.", "danger")
            return render_template("superadmin/re_authenticate.html", next_url=next_url), 401

        session["superadmin_last_elevated_at"] = datetime.utcnow().isoformat()
        flash("Re-authentication successful.", "success")
        return redirect(next_url)

    return render_template("superadmin/re_authenticate.html", next_url=next_url)
