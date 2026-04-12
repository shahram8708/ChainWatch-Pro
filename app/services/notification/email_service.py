"""Email notification service for account and alert communication."""

from __future__ import annotations

import logging
from smtplib import SMTPException

from flask import current_app, render_template, url_for
from flask_mail import Message

from app.utils.helpers import send_async_email

logger = logging.getLogger(__name__)


def send_verification_email(user, token) -> bool:
    """Send account verification email with a 24-hour verification link."""

    try:
        verification_url = url_for("auth.verify_email", token=token, _external=True)
        html = render_template(
            "email/verify.html",
            user=user,
            verification_url=verification_url,
            subject="Verify your ChainWatch Pro email address",
        )
        msg = Message(
            subject="Verify your ChainWatch Pro email address",
            recipients=[user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except SMTPException:
        logger.exception("SMTP error while sending verification email for user_id=%s", user.id)
    except Exception:
        logger.exception("Unexpected error while sending verification email for user_id=%s", user.id)
    return False


def send_password_reset_email(user, token) -> bool:
    """Send password reset email with a 1-hour reset link."""

    try:
        reset_url = url_for("auth.reset_password", token=token, _external=True)
        html = render_template(
            "email/reset_password.html",
            user=user,
            reset_url=reset_url,
            subject="Reset your ChainWatch Pro password",
        )
        msg = Message(
            subject="Reset your ChainWatch Pro password",
            recipients=[user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except SMTPException:
        logger.exception("SMTP error while sending reset email for user_id=%s", user.id)
    except Exception:
        logger.exception("Unexpected error while sending reset email for user_id=%s", user.id)
    return False


def send_welcome_email(user) -> bool:
    """Send welcome email after successful account verification."""

    try:
        dashboard_url = url_for("dashboard.index", _external=True)
        html = render_template(
            "email/welcome.html",
            user=user,
            dashboard_url=dashboard_url,
            subject="Welcome to ChainWatch Pro — Let's get started",
        )
        msg = Message(
            subject="Welcome to ChainWatch Pro — Let's get started",
            recipients=[user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except SMTPException:
        logger.exception("SMTP error while sending welcome email for user_id=%s", user.id)
    except Exception:
        logger.exception("Unexpected error while sending welcome email for user_id=%s", user.id)
    return False


def send_alert_notification_email(user, alert, shipment) -> bool:
    """Send alert notification email with shipment and action context."""

    try:
        view_url = url_for("dashboard.index", _external=True)
        html = render_template(
            "email/alert_notification.html",
            user=user,
            alert=alert,
            shipment=shipment,
            view_url=view_url,
            subject=f"⚠ {str(alert.severity).upper()} Alert: {alert.title}",
        )
        msg = Message(
            subject=f"⚠ {str(alert.severity).upper()} Alert: {alert.title}",
            recipients=[user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except SMTPException:
        logger.exception("SMTP error while sending alert email for user_id=%s alert_id=%s", user.id, alert.id)
    except Exception:
        logger.exception(
            "Unexpected error while sending alert email for user_id=%s alert_id=%s",
            user.id,
            alert.id,
        )
    return False


def send_team_invitation_email(inviter, invited_user, token) -> bool:
    """Send a team invitation email with verification link to invited teammate."""

    try:
        verification_url = url_for("auth.verify_email", token=token, _external=True)
        html = render_template(
            "email/team_invitation.html",
            inviter=inviter,
            invited_user=invited_user,
            user=invited_user,
            verification_url=verification_url,
            subject="You were invited to ChainWatch Pro",
        )
        msg = Message(
            subject="You were invited to ChainWatch Pro",
            recipients=[invited_user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except SMTPException:
        logger.exception("SMTP error while sending team invitation for user_id=%s", invited_user.id)
    except Exception:
        logger.exception("Unexpected error while sending team invitation for user_id=%s", invited_user.id)
    return False


def send_team_invitation_email_with_credentials(inviting_user, invited_user, temporary_password, app_context) -> bool:
    """Send invitation email containing temporary login credentials."""

    try:
        flask_app = app_context or current_app._get_current_object()
        with flask_app.app_context():
            login_url = url_for("auth.login", _external=True)
            organisation = getattr(invited_user, "organisation", None) or getattr(inviting_user, "organisation", None)
            organisation_name = getattr(organisation, "name", "ChainWatch Pro")

            subject = f"You've been invited to join {organisation_name} on ChainWatch Pro"
            html = render_template(
                "email/team_invitation_with_credentials.html",
                inviter=inviting_user,
                invited_user=invited_user,
                user=invited_user,
                organisation_name=organisation_name,
                temporary_password=temporary_password,
                login_url=login_url,
                support_email=flask_app.config.get("SUPPORT_EMAIL", "support@chainwatchpro.com"),
                subject=subject,
            )
            msg = Message(
                subject=subject,
                recipients=[invited_user.email],
                sender=flask_app.config["MAIL_DEFAULT_SENDER"],
            )
            msg.html = html
            send_async_email(flask_app, msg)
        return True
    except SMTPException:
        logger.exception(
            "SMTP error while sending credential invitation for user_id=%s",
            invited_user.id,
        )
    except Exception:
        logger.exception(
            "Unexpected error while sending credential invitation for user_id=%s",
            invited_user.id,
        )
    return False


def send_email_change_verification_email(user, new_email: str, token: str) -> bool:
    """Send verification mail to confirm a requested email change."""

    try:
        verification_url = url_for("auth.verify_email", token=token, pending_email=new_email, _external=True)
        html = (
            "<p>Hello "
            f"{user.full_name},</p>"
            "<p>We received a request to change your ChainWatch Pro email address.</p>"
            f"<p>Please verify your new email by clicking this link: <a href=\"{verification_url}\">Verify new email</a>.</p>"
            "<p>If you did not request this change, you can safely ignore this email.</p>"
        )
        msg = Message(
            subject="Verify your new ChainWatch Pro email",
            recipients=[new_email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception("Failed to send email-change verification user_id=%s", user.id)
    return False


def send_subscription_cancellation_email(user, organisation) -> bool:
    """Send confirmation email when a subscription is cancelled."""

    try:
        billing_url = url_for("settings.billing", _external=True)
        html = (
            f"<p>Hello {user.full_name},</p>"
            "<p>Your ChainWatch Pro subscription has been cancelled and will remain active until the end of the current billing period.</p>"
            f"<p>Organisation: <strong>{organisation.name}</strong></p>"
            f"<p>You can review billing details here: <a href=\"{billing_url}\">Billing Settings</a></p>"
        )
        msg = Message(
            subject="Subscription cancelled - ChainWatch Pro",
            recipients=[user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception("Failed subscription cancellation email user_id=%s", user.id)
    return False


def send_payment_failed_email(user, organisation, payment_payload=None) -> bool:
    """Send payment-failure notification to workspace admin."""

    try:
        amount_paise = 0
        if isinstance(payment_payload, dict):
            amount_paise = int(payment_payload.get("amount", 0) or 0)
        amount_inr = amount_paise / 100.0
        billing_url = url_for("settings.billing", _external=True)

        html = (
            f"<p>Hello {user.full_name},</p>"
            "<p>A recent ChainWatch Pro subscription payment attempt failed.</p>"
            f"<p>Organisation: <strong>{organisation.name}</strong><br>Amount: <strong>INR {amount_inr:,.2f}</strong></p>"
            f"<p>Please update your payment details from <a href=\"{billing_url}\">Billing Settings</a>.</p>"
        )
        msg = Message(
            subject="Payment failed - ChainWatch Pro subscription",
            recipients=[user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception("Failed payment-failure email user_id=%s", user.id)
    return False


def send_account_deletion_request_email(requesting_user, organisation) -> bool:
    """Notify support and organisation admin about account deletion requests."""

    try:
        from datetime import datetime

        from app.models.user import User

        recipients = [current_app.config.get("SUPPORT_EMAIL", "support@chainwatchpro.com")]

        org_admin = (
            User
            .query.filter(
                User.organisation_id == organisation.id,
                User.role == "admin",
                User._is_active.is_(True),
            )
            .order_by(User.created_at.asc())
            .first()
        )
        if org_admin is not None and org_admin.email not in recipients:
            recipients.append(org_admin.email)

        html = (
            "<p>An account deletion request has been submitted.</p>"
            f"<p>Organisation: <strong>{organisation.name}</strong><br>"
            f"Requested by: <strong>{requesting_user.email}</strong><br>"
            f"Requested at: <strong>{datetime.utcnow().strftime('%d %b %Y %H:%M UTC')}</strong></p>"
            "<p>Please process according to data retention policy.</p>"
        )

        msg = Message(
            subject="Account deletion request - ChainWatch Pro",
            recipients=recipients,
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception(
            "Failed deletion-request email user_id=%s organisation_id=%s",
            requesting_user.id,
            organisation.id,
        )
    return False


def send_org_suspension_email(admin_user, organisation, reason: str) -> bool:
    """Notify an organisation admin that their workspace has been suspended."""

    try:
        html = (
            f"<p>Hello {admin_user.full_name},</p>"
            f"<p>Your organisation <strong>{organisation.name}</strong> has been suspended by ChainWatch Pro platform administration.</p>"
            f"<p><strong>Reason:</strong> {reason}</p>"
            "<p>If you believe this is an error, contact support immediately.</p>"
        )
        msg = Message(
            subject=f"Organisation suspended on ChainWatch Pro - {organisation.name}",
            recipients=[admin_user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception("Failed organisation suspension email organisation_id=%s", organisation.id)
    return False


def send_data_deletion_confirmation_email(recipient_email: str, organisation_name: str) -> bool:
    """Send final confirmation that organisation data deletion is complete."""

    if not recipient_email:
        return False

    try:
        html = (
            "<p>Hello,</p>"
            f"<p>This is to confirm that all ChainWatch Pro data for <strong>{organisation_name}</strong> has been permanently deleted.</p>"
            "<p>If this action was unexpected, please contact support immediately.</p>"
        )
        msg = Message(
            subject=f"Data deletion completed - {organisation_name}",
            recipients=[recipient_email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception("Failed data deletion confirmation email recipient=%s", recipient_email)
    return False


def send_superadmin_role_change_email(user, granted: bool, reason: str | None = None) -> bool:
    """Notify user when SuperAdmin role is granted or revoked."""

    action = "granted" if granted else "revoked"
    reason_line = f"<p><strong>Reason:</strong> {reason}</p>" if reason else ""

    try:
        html = (
            f"<p>Hello {user.full_name},</p>"
            f"<p>Your ChainWatch Pro account has been {action} platform SuperAdmin access.</p>"
            f"{reason_line}"
            "<p>If this change was unexpected, contact support immediately.</p>"
        )
        msg = Message(
            subject=f"SuperAdmin access {action} - ChainWatch Pro",
            recipients=[user.email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception("Failed superadmin role change email user_id=%s", user.id)
    return False


def send_platform_announcement_email(recipient_email: str, subject: str, message_html: str) -> bool:
    """Send a platform-wide announcement email to a single recipient."""

    try:
        html = (
            "<div style=\"font-family:Inter,Arial,sans-serif;color:#1A1A2E;line-height:1.6;\">"
            "<h2 style=\"margin-bottom:12px;\">ChainWatch Pro Platform Announcement</h2>"
            f"<div>{message_html}</div>"
            "</div>"
        )
        msg = Message(
            subject=subject,
            recipients=[recipient_email],
            sender=current_app.config["MAIL_DEFAULT_SENDER"],
        )
        msg.html = html
        send_async_email(current_app._get_current_object(), msg)
        return True
    except Exception:
        logger.exception("Failed platform announcement email recipient=%s", recipient_email)
    return False
