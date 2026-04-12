"""Authentication routes for registration, login, verification, and password reset."""

from __future__ import annotations

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_user, logout_user
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db, limiter
from app.forms.auth_forms import (
    ForcedPasswordChangeForm,
    ForgotPasswordForm,
    LoginForm,
    RegistrationForm,
    ResetPasswordForm,
)
from app.models.audit_log import AuditLog
from app.models.organisation import Organisation
from app.models.user import User
from app.services.notification.email_service import (
    send_password_reset_email,
    send_verification_email,
    send_welcome_email,
)
from app.utils.decorators import login_required, verified_required
from app.utils.helpers import hash_token

logger = logging.getLogger(__name__)

auth_bp = Blueprint("auth", __name__)

FORCED_PASSWORD_CHANGE_TOKEN_TTL_SECONDS = 15 * 60


def _build_force_password_change_token(user_id: str, timestamp: int) -> str:
    payload = f"{user_id}:{timestamp}"
    secret_key = current_app.config.get("SECRET_KEY", "")
    signature = hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{timestamp}:{signature}"


def _is_valid_force_password_change_token(token: str, user_id: str) -> bool:
    if not token or ":" not in token:
        return False

    try:
        timestamp_text, provided_signature = token.split(":", 1)
        timestamp = int(timestamp_text)
    except (TypeError, ValueError):
        return False

    now_ts = int(datetime.utcnow().timestamp())
    if now_ts - timestamp > FORCED_PASSWORD_CHANGE_TOKEN_TTL_SECONDS:
        return False

    expected = _build_force_password_change_token(user_id=user_id, timestamp=timestamp)
    expected_signature = expected.split(":", 1)[1]
    return hmac.compare_digest(expected_signature, provided_signature)


@auth_bp.app_errorhandler(429)
def ratelimit_handler(error):
    """Render clear user feedback when route limits are exceeded."""

    endpoint = request.endpoint or ""
    if endpoint == "auth.login":
        flash("Too many failed login attempts. Please wait 15 minutes before trying again.", "danger")
        return render_template("auth/login.html", form=LoginForm()), 429
    if endpoint == "auth.register":
        flash("Too many registration attempts. Please try again in a little while.", "danger")
        return render_template("auth/register.html", form=RegistrationForm()), 429
    if endpoint == "auth.forgot_password":
        flash("Too many password reset requests. Please try again later.", "danger")
        return render_template("auth/forgot_password.html", form=ForgotPasswordForm()), 429

    return render_template("errors/429.html"), 429


@auth_bp.route("/register", methods=["GET", "POST"])
@limiter.limit("10 per hour", methods=["POST"])
def register():
    """Register a new organisation and initial user account."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = RegistrationForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        existing_user = User.query.filter_by(email=email).first()
        if existing_user is not None:
            form.email.errors.append("An account with this email already exists.")
            return render_template("auth/register.html", form=form), 400

        shipment_volume_map = {
            "Under 50": 50,
            "50-200": 200,
            "200-500": 500,
            "500-2000": 2000,
            "2000+": 2001,
        }

        organisation = Organisation(
            name=form.company_name.data.strip(),
            company_size_range=form.company_size.data,
            monthly_shipment_volume=shipment_volume_map.get(form.monthly_shipment_volume.data),
            subscription_plan="starter",
            subscription_status="trial",
            trial_ends_at=datetime.utcnow() + timedelta(days=14),
            default_currency="INR",
        )

        user = User(
            email=email,
            first_name=form.first_name.data.strip(),
            last_name=form.last_name.data.strip(),
            role="admin",
            organisation=organisation,
            account_source="self_registered",
            must_change_password=False,
        )
        user.set_password(form.password.data)
        token = user.generate_verification_token()

        try:
            db.session.add(organisation)
            db.session.add(user)
            db.session.commit()

            send_verification_email(user, token)

            AuditLog.log(
                db,
                event_type="user_registered",
                description=f"User {user.email} registered and verification email sent.",
                organisation_id=organisation.id,
                actor_user=user,
                ip_address=request.remote_addr,
            )

            session["registration_email"] = user.email
            flash("Account created successfully. Please verify your email to continue.", "success")
            return redirect(url_for("auth.verify_pending", email=user.email))
        except Exception:
            db.session.rollback()
            logger.exception("Registration failed for email=%s", email)
            flash("Unable to create your account at the moment. Please try again.", "danger")

    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    "5 per 15 minutes",
    methods=["POST"],
    error_message="Too many failed login attempts. Please wait 15 minutes before trying again.",
)
def login():
    """Authenticate a user and route based on verification/onboarding state."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()

        if user is None or not user.check_password(form.password.data):
            flash("Invalid email or password.", "danger")
            return render_template("auth/login.html", form=form), 401

        if not user.is_active:
            flash("Your account has been suspended. Contact support.", "danger")
            return render_template("auth/login.html", form=form), 403

        if not user.is_verified:
            session["registration_email"] = user.email
            flash("Please verify your email address before signing in.", "warning")
            return redirect(url_for("auth.verify_pending", email=user.email))

        if user.must_change_password:
            session["force_password_change_user_id"] = str(user.id)
            session["force_password_change_email"] = user.email
            session["force_password_change_started_at"] = datetime.utcnow().isoformat()
            return redirect(url_for("auth.forced_password_change"))

        login_user(user, remember=form.remember_me.data)
        user.last_login_at = datetime.utcnow()

        if user.role == "superadmin":
            session["superadmin_last_elevated_at"] = datetime.utcnow().isoformat()
            session.pop("superadmin_impersonating", None)
            session.pop("superadmin_original_user_id", None)
            session.pop("impersonating_org_id", None)
            session.pop("impersonation_started_at", None)
        else:
            session.pop("superadmin_last_elevated_at", None)

        try:
            db.session.commit()
            AuditLog.log(
                db,
                event_type="user_login",
                description=f"User {user.email} logged in.",
                organisation_id=user.organisation_id,
                actor_user=user,
                ip_address=request.remote_addr,
            )
        except Exception:
            db.session.rollback()
            logger.exception("Failed to persist login metadata for user_id=%s", user.id)

        if not user.organisation.onboarding_complete:
            return redirect("/onboarding/step1")

        next_url = request.args.get("next")
        if next_url:
            return redirect(next_url)
        return redirect("/dashboard")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/change-password-required", methods=["GET", "POST"])
def forced_password_change():
    """Handle mandatory password change for invited users before full login."""

    forced_user_id = session.get("force_password_change_user_id")
    forced_email = session.get("force_password_change_email")
    if not forced_user_id or not forced_email:
        return redirect(url_for("auth.login"))

    try:
        parsed_user_id = uuid.UUID(forced_user_id)
    except (TypeError, ValueError):
        session.pop("force_password_change_user_id", None)
        session.pop("force_password_change_email", None)
        session.pop("force_password_change_token", None)
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(id=parsed_user_id).first()
    if user is None or user.email != forced_email or not user.must_change_password:
        session.pop("force_password_change_user_id", None)
        session.pop("force_password_change_email", None)
        session.pop("force_password_change_token", None)
        flash("Your temporary login session has expired. Please sign in again.", "warning")
        return redirect(url_for("auth.login"))

    form = ForcedPasswordChangeForm()

    if request.method == "GET":
        token = _build_force_password_change_token(
            user_id=str(user.id),
            timestamp=int(datetime.utcnow().timestamp()),
        )
        session["force_password_change_token"] = token
        form.change_token.data = token
        return render_template("auth/forced_password_change.html", form=form, forced_email=user.email)

    expected_token = session.get("force_password_change_token")
    if not expected_token:
        flash("Your password change token has expired. Please sign in again.", "danger")
        return redirect(url_for("auth.login"))

    if not form.validate_on_submit():
        form.change_token.data = expected_token
        return render_template("auth/forced_password_change.html", form=form, forced_email=user.email), 400

    if form.change_token.data != expected_token or not _is_valid_force_password_change_token(
        token=form.change_token.data,
        user_id=str(user.id),
    ):
        flash("Invalid password change token. Please sign in again.", "danger")
        session.pop("force_password_change_user_id", None)
        session.pop("force_password_change_email", None)
        session.pop("force_password_change_token", None)
        return redirect(url_for("auth.login"))

    user.set_password(form.new_password.data)
    user.must_change_password = False
    user.temporary_password_hash = None
    user.invitation_accepted_at = datetime.utcnow()
    user.last_login_at = datetime.utcnow()

    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed forced password change commit for user_id=%s", user.id)
        flash("Could not update your password right now. Please try again.", "danger")
        return render_template("auth/forced_password_change.html", form=form, forced_email=user.email), 500

    session.pop("force_password_change_user_id", None)
    session.pop("force_password_change_email", None)
    session.pop("force_password_change_started_at", None)
    session.pop("force_password_change_token", None)

    login_user(user)

    if user.role == "superadmin":
        session["superadmin_last_elevated_at"] = datetime.utcnow().isoformat()

    try:
        AuditLog.log(
            db,
            event_type="invitation_accepted",
            description=f"Invited user {user.email} completed mandatory password setup.",
            organisation_id=user.organisation_id,
            actor_user=user,
            ip_address=request.remote_addr,
        )
    except Exception:
        db.session.rollback()
        logger.exception("Failed invitation_accepted audit event user_id=%s", user.id)

    flash("Welcome! Your password has been set successfully. You're all set.", "success")
    if not user.organisation.onboarding_complete:
        return redirect("/onboarding/step1")
    return redirect("/dashboard")


@auth_bp.route("/logout")
@login_required
def logout():
    """End user session and record logout event."""

    user = current_user
    org_id = user.organisation_id
    email = user.email

    try:
        AuditLog.log(
            db,
            event_type="user_logout",
            description=f"User {email} logged out.",
            organisation_id=org_id,
            actor_user=user,
            ip_address=request.remote_addr,
        )
    except Exception:
        db.session.rollback()
        logger.exception("Failed to write logout audit event for user_id=%s", user.id)

    logout_user()
    flash("You have been logged out.", "info")
    return redirect("/")


@auth_bp.route("/verify")
def verify_pending():
    """Render verify pending state after registration or login attempt."""

    email = request.args.get("email") or session.get("registration_email")
    return render_template("auth/verify.html", status="pending", email=email)


@auth_bp.route("/verify-email/<token>")
def verify_email(token):
    """Verify email token and activate account."""

    user = User.query.filter_by(verification_token=token).first()
    if user is None:
        return render_template("auth/verify.html", status="invalid"), 400

    if not user.verify_email_token(token):
        return render_template("auth/verify.html", status="expired", email=user.email), 400

    pending_email = (request.args.get("pending_email") or "").strip().lower()
    old_email = user.email

    try:
        if pending_email:
            duplicate = User.query.filter(User.email == pending_email, User.id != user.id).first()
            if duplicate is not None:
                flash("That email is already in use. Please try another address.", "danger")
                return redirect(url_for("settings.profile"))

            org = user.organisation
            profile = org.org_profile_data or {}
            if not isinstance(profile, dict):
                profile = {}
            pending_map = profile.get("pending_email_changes", {})
            if not isinstance(pending_map, dict):
                pending_map = {}

            expected_email = (pending_map.get(str(user.id)) or "").strip().lower()
            if expected_email and expected_email == pending_email:
                user.email = pending_email
                pending_map.pop(str(user.id), None)
                profile["pending_email_changes"] = pending_map
                org.org_profile_data = profile
                flag_modified(org, "org_profile_data")

        db.session.commit()
        AuditLog.log(
            db,
            event_type="email_verified",
            description=f"User {user.email} verified email successfully.",
            organisation_id=user.organisation_id,
            actor_user=user,
            metadata={
                "old_email": old_email,
                "new_email": user.email,
            },
            ip_address=request.remote_addr,
        )
        send_welcome_email(user)
        flash("Your email has been verified. Please log in.", "success")
        return redirect(url_for("auth.login"))
    except Exception:
        db.session.rollback()
        logger.exception("Failed email verification commit for user_id=%s", user.id)
        flash("Verification completed, but we could not finalize your setup. Please log in.", "warning")
        return redirect(url_for("auth.login"))


@auth_bp.route("/resend-verification")
def resend_verification():
    """Resend verification email for unverified user."""

    email = (request.args.get("email") or session.get("registration_email") or "").strip().lower()
    if not email:
        flash("We could not determine your email address. Please register or login again.", "warning")
        return redirect(url_for("auth.login"))

    user = User.query.filter_by(email=email, is_verified=False).first()
    if user is None:
        flash("No pending verification found for this email.", "warning")
        return redirect(url_for("auth.login"))

    token = user.generate_verification_token()
    try:
        db.session.commit()
        send_verification_email(user, token)
        flash("Verification email resent.", "success")
    except Exception:
        db.session.rollback()
        logger.exception("Failed to resend verification for user_id=%s", user.id)
        flash("Could not resend verification email right now. Please try again.", "danger")

    return redirect(url_for("auth.verify_pending", email=user.email))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", methods=["POST"])
def forgot_password():
    """Initiate password reset flow without revealing account existence."""

    if current_user.is_authenticated:
        return redirect("/dashboard")

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        email = form.email.data.strip().lower()
        user = User.query.filter_by(email=email).first()

        if user and user.is_verified:
            token = user.generate_reset_token()
            try:
                db.session.commit()
                send_password_reset_email(user, token)
            except Exception:
                db.session.rollback()
                logger.exception("Failed to generate/send reset token for email=%s", email)

        flash(
            "If an account exists with that email, you will receive a password reset link shortly.",
            "info",
        )
        return redirect(url_for("auth.login"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    """Validate reset token and allow secure password update."""

    token_hash = hash_token(token)
    user = User.query.filter_by(reset_token_hash=token_hash).first()

    if user is None:
        return render_template("auth/reset_password.html", status="invalid", form=None), 400

    if user.reset_token_expires_at is None or user.reset_token_expires_at < datetime.utcnow():
        return render_template("auth/reset_password.html", status="expired", form=None), 400

    form = ResetPasswordForm()

    if request.method == "GET":
        return render_template("auth/reset_password.html", status="valid", form=form)

    if form.validate_on_submit():
        if not user.verify_reset_token(token):
            return render_template("auth/reset_password.html", status="invalid", form=None), 400

        user.set_password(form.new_password.data)
        user.reset_token_hash = None
        user.reset_token_expires_at = None
        if user.must_change_password:
            user.must_change_password = False
            user.temporary_password_hash = None
            user.invitation_accepted_at = user.invitation_accepted_at or datetime.utcnow()

        try:
            db.session.commit()
            AuditLog.log(
                db,
                event_type="password_reset",
                description=f"User {user.email} reset password.",
                organisation_id=user.organisation_id,
                actor_user=user,
                ip_address=request.remote_addr,
            )
            flash("Password reset successfully. Please log in.", "success")
            return redirect(url_for("auth.login"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed password reset commit for user_id=%s", user.id)
            flash("Could not reset password right now. Please try again.", "danger")

    return render_template("auth/reset_password.html", status="valid", form=form)

