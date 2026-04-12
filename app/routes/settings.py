"""Settings routes for profile, team, integrations, alerts, and billing."""

from __future__ import annotations

import csv
import io
import json
import logging
import secrets
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import requests
from cryptography.fernet import Fernet, InvalidToken
from flask import Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, url_for
from flask import session
from flask_login import current_user
from sqlalchemy import case, or_
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.utils import secure_filename

from app.extensions import db
from app.forms.settings_forms import (
    AlertRuleForm,
    BulkTeamImportForm,
    CarrierCSVImportForm,
    CarrierConnectForm,
    ChangePasswordForm,
    GlobalAlertSettingsForm,
    TeamInviteForm,
    UserProfileForm,
)
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.shipment import Shipment
from app.models.user import User
from app.services import carrier_tracker
from app.services import razorpay_service
from app.services import team_import_service
from app.services.notification import email_service
from app.utils.decorators import login_required, role_required, verified_required
from app.utils.helpers import generate_csv_template_response, generate_secure_temporary_password


settings_bp = Blueprint("settings", __name__)
logger = logging.getLogger(__name__)

PROFILE_PHOTO_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _org_profile() -> dict:
    profile = current_user.organisation.org_profile_data or {}
    if not isinstance(profile, dict):
        profile = {}
    current_user.organisation.org_profile_data = profile
    return profile


def _save_org_profile(profile: dict) -> None:
    current_user.organisation.org_profile_data = profile
    flag_modified(current_user.organisation, "org_profile_data")
    db.session.commit()


def _profile_photo_upload_dir() -> Path:
    static_root = Path(current_app.static_folder or (Path(current_app.root_path).parent / "static")).resolve()
    configured_dir = str(current_app.config.get("PROFILE_PHOTO_UPLOAD_DIR", "")).strip()

    if configured_dir:
        candidate = Path(configured_dir)
        if not candidate.is_absolute():
            candidate = static_root / candidate
    else:
        candidate = static_root / "uploads" / "profile_photos"

    upload_dir = candidate.resolve()
    try:
        upload_dir.relative_to(static_root)
    except ValueError:
        upload_dir = (static_root / "uploads" / "profile_photos").resolve()

    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _relative_photo_path(file_path: Path) -> str:
    static_root = Path(current_app.static_folder or (Path(current_app.root_path).parent / "static")).resolve()
    return file_path.resolve().relative_to(static_root).as_posix()


def _delete_profile_photo(relative_path: str | None) -> None:
    if not relative_path:
        return

    normalized = str(relative_path).replace("\\", "/").lstrip("/")

    static_root = Path(current_app.static_folder or (Path(current_app.root_path).parent / "static")).resolve()
    target = (static_root / normalized).resolve()
    upload_root = _profile_photo_upload_dir().resolve()

    try:
        target.relative_to(upload_root)
    except ValueError:
        return

    if target.exists() and target.is_file():
        target.unlink()


def _store_profile_photo(file_storage) -> str:
    original_name = secure_filename(file_storage.filename or "")
    extension = Path(original_name).suffix.lower()
    if extension not in PROFILE_PHOTO_ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported profile photo format.")

    user_part = str(current_user.id).replace("-", "")
    generated_name = f"{user_part}_{secrets.token_hex(12)}{extension}"

    upload_dir = _profile_photo_upload_dir()
    destination = upload_dir / generated_name

    file_storage.stream.seek(0)
    file_storage.save(destination)

    return _relative_photo_path(destination)


def _active_settings_tab_from_endpoint(endpoint: str) -> str:
    suffix = (endpoint or "").split(".")[-1]

    if suffix in {
        "profile",
        "request_deletion",
    }:
        return "profile"
    if suffix in {
        "team",
        "invite_team_member",
        "remove_team_member",
        "update_team_role",
        "resend_invite",
        "cancel_invite",
        "download_team_csv_template",
        "bulk_import_team_members",
        "bulk_import_error_report",
    }:
        return "team"
    if suffix in {
        "integrations",
        "connect_integration",
        "disconnect_integration",
        "test_integration",
        "import_integration_csv",
    }:
        return "integrations"
    if suffix in {
        "alerts",
        "delete_alert_rule",
        "toggle_alert_rule",
        "test_alert_webhook",
    }:
        return "alerts"
    if suffix in {
        "billing",
        "upgrade_billing",
        "verify_billing_payment",
        "cancel_billing",
    }:
        return "billing"
    return "profile"


def _mask_secret(value: str) -> str:
    if not value:
        return ""
    visible = value[-4:] if len(value) >= 4 else value
    return "*" * max(0, len(value) - len(visible)) + visible


def _get_fernet() -> Fernet:
    key = (current_app.config.get("ENCRYPTION_KEY", "") or "").strip()
    if not key or key.startswith("<"):
        raise ValueError("ENCRYPTION_KEY is not configured.")

    try:
        return Fernet(key.encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "ENCRYPTION_KEY is invalid. It must be 32 url-safe base64-encoded bytes."
        ) from exc


def _encrypt_credentials(payload: dict) -> dict:
    fernet = _get_fernet()
    encrypted = {}
    for key, value in payload.items():
        if value is None:
            continue
        raw = str(value).strip()
        if not raw:
            continue
        encrypted[key] = fernet.encrypt(raw.encode("utf-8")).decode("utf-8")
    return encrypted


def _decrypt_credentials(payload: dict) -> dict:
    fernet = _get_fernet()
    decrypted = {}
    for key, value in payload.items():
        if not isinstance(value, str):
            decrypted[key] = value
            continue
        try:
            decrypted[key] = fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            decrypted[key] = ""
    return decrypted


def _supports_json_response() -> bool:
    if request.is_json:
        return True
    accepts = (request.headers.get("Accept") or "").lower()
    return "application/json" in accepts


def _team_usage() -> dict:
    usage = razorpay_service.get_usage_meters(current_user.organisation, db.session)
    users_meter = usage.get("users", {})
    users_meter.setdefault("used", 0)
    users_meter.setdefault("limit", None)
    users_meter.setdefault("percentage", 0.0)
    return usage


@settings_bp.before_request
@login_required
@verified_required
def _guards():
    g.active_settings_tab = _active_settings_tab_from_endpoint(request.endpoint or "")


@settings_bp.app_context_processor
def _inject_settings_tab():
    return {"active_settings_tab": getattr(g, "active_settings_tab", "profile")}


@settings_bp.route("/profile", methods=["GET", "POST"])
def profile():
    profile_form = UserProfileForm()
    password_form = ChangePasswordForm()

    if request.method == "GET":
        profile_form.first_name.data = current_user.first_name
        profile_form.last_name.data = current_user.last_name
        profile_form.email.data = current_user.email
        profile_form.phone.data = current_user.phone
        profile_form.timezone.data = current_user.timezone
        profile_form.remove_profile_photo.data = False
        profile_form.alert_email_enabled.data = current_user.alert_email_enabled
        profile_form.alert_sms_enabled.data = current_user.alert_sms_enabled

    if request.method == "POST":
        form_type = (request.form.get("form_type") or "").strip().lower()

        if form_type == "profile" and profile_form.validate_on_submit():
            changed_fields = []

            if current_user.first_name != profile_form.first_name.data:
                current_user.first_name = profile_form.first_name.data
                changed_fields.append("first_name")
            if current_user.last_name != profile_form.last_name.data:
                current_user.last_name = profile_form.last_name.data
                changed_fields.append("last_name")
            if (current_user.phone or "") != (profile_form.phone.data or ""):
                current_user.phone = profile_form.phone.data or None
                changed_fields.append("phone")
            if current_user.timezone != profile_form.timezone.data:
                current_user.timezone = profile_form.timezone.data
                changed_fields.append("timezone")
            if bool(current_user.alert_email_enabled) != bool(profile_form.alert_email_enabled.data):
                current_user.alert_email_enabled = bool(profile_form.alert_email_enabled.data)
                changed_fields.append("alert_email_enabled")
            if bool(current_user.alert_sms_enabled) != bool(profile_form.alert_sms_enabled.data):
                current_user.alert_sms_enabled = bool(profile_form.alert_sms_enabled.data)
                changed_fields.append("alert_sms_enabled")

            requested_email = (profile_form.email.data or "").strip().lower()
            if requested_email and requested_email != current_user.email:
                duplicate = User.query.filter(
                    User.email == requested_email,
                    User.id != current_user.id,
                ).first()
                if duplicate is not None:
                    flash("That email is already in use by another account.", "danger")
                    return render_template(
                        "app/settings/profile.html",
                        profile_form=profile_form,
                        password_form=password_form,
                        timezone_choices=profile_form.timezone.choices,
                    )

                token = current_user.generate_verification_token()
                current_user.is_verified = False
                changed_fields.extend(["email_pending_verification", "is_verified"])

                org_profile = _org_profile()
                pending_map = org_profile.get("pending_email_changes", {})
                if not isinstance(pending_map, dict):
                    pending_map = {}
                pending_map[str(current_user.id)] = requested_email
                org_profile["pending_email_changes"] = pending_map
                current_user.organisation.org_profile_data = org_profile
                flag_modified(current_user.organisation, "org_profile_data")

                email_service.send_email_change_verification_email(
                    user=current_user,
                    new_email=requested_email,
                    token=token,
                )
                flash("Please verify your new email address.", "warning")

            uploaded_photo = profile_form.profile_photo.data
            has_uploaded_photo = bool(uploaded_photo and getattr(uploaded_photo, "filename", ""))
            remove_photo_requested = bool(profile_form.remove_profile_photo.data)

            if has_uploaded_photo:
                try:
                    new_photo_path = _store_profile_photo(uploaded_photo)
                except ValueError:
                    profile_form.profile_photo.errors.append("Unsupported image type. Use JPG, PNG, or WEBP.")
                    return render_template(
                        "app/settings/profile.html",
                        profile_form=profile_form,
                        password_form=password_form,
                        timezone_choices=profile_form.timezone.choices,
                    )

                previous_photo_path = current_user.profile_photo_path
                current_user.profile_photo_path = new_photo_path
                changed_fields.append("profile_photo_path")

                if previous_photo_path and previous_photo_path != new_photo_path:
                    _delete_profile_photo(previous_photo_path)
            elif remove_photo_requested and current_user.profile_photo_path:
                old_photo_path = current_user.profile_photo_path
                current_user.profile_photo_path = None
                changed_fields.append("profile_photo_removed")
                _delete_profile_photo(old_photo_path)

            db.session.commit()

            AuditLog.log(
                db,
                event_type="profile_updated",
                description=f"Updated profile settings for {current_user.email}.",
                organisation_id=current_user.organisation_id,
                actor_user=current_user,
                metadata={"fields_changed": changed_fields},
                ip_address=request.remote_addr,
            )

            flash("Profile updated successfully.", "success")
            return redirect(url_for("settings.profile"))

        if form_type == "password" and password_form.validate_on_submit():
            if not current_user.check_password(password_form.current_password.data):
                flash("Current password is incorrect.", "danger")
                return render_template(
                    "app/settings/profile.html",
                    profile_form=profile_form,
                    password_form=password_form,
                    timezone_choices=profile_form.timezone.choices,
                )

            current_user.set_password(password_form.new_password.data)
            db.session.commit()

            AuditLog.log(
                db,
                event_type="password_changed",
                description=f"Changed password for {current_user.email}.",
                organisation_id=current_user.organisation_id,
                actor_user=current_user,
                ip_address=request.remote_addr,
            )

            flash("Password changed successfully.", "success")
            return redirect(url_for("settings.profile"))

    return render_template(
        "app/settings/profile.html",
        profile_form=profile_form,
        password_form=password_form,
        timezone_choices=profile_form.timezone.choices,
    )


@settings_bp.post("/profile/request-deletion")
def request_deletion():
    org_profile = _org_profile()
    org_profile["deletion_requested"] = True
    org_profile["deletion_requested_at"] = datetime.utcnow().isoformat()
    org_profile["deletion_requested_by"] = str(current_user.id)
    _save_org_profile(org_profile)

    email_service.send_account_deletion_request_email(current_user, current_user.organisation)

    AuditLog.log(
        db,
        event_type="account_deletion_requested",
        description=f"Account deletion requested by {current_user.email}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        ip_address=request.remote_addr,
    )

    flash("Your deletion request was submitted. Our team will contact you shortly.", "warning")
    return redirect(url_for("settings.profile"))


@settings_bp.get("/team")
@role_required("admin", "manager")
def team():
    role_rank = case(
        (User.role == "admin", 0),
        (User.role == "manager", 1),
        else_=2,
    )

    team_members = (
        User.query.filter(
            User.organisation_id == current_user.organisation_id,
            User._is_active.is_(True),
        )
        .order_by(role_rank.asc(), User.first_name.asc(), User.last_name.asc())
        .all()
    )

    pending_invites = (
        User.query.filter(
            User.organisation_id == current_user.organisation_id,
            User._is_active.is_(True),
            User.must_change_password.is_(True),
        )
        .order_by(User.invitation_sent_at.desc().nullslast(), User.created_at.desc())
        .all()
    )

    usage = _team_usage()
    user_meter = usage.get("users", {})
    users_limit = user_meter.get("limit")
    users_used = int(user_meter.get("used", 0) or 0)
    if users_limit is None:
        remaining_seats = "Unlimited"
    else:
        remaining_seats = max(int(users_limit) - users_used, 0)

    bulk_import_result = session.pop("bulk_import_result", None)

    return render_template(
        "app/settings/team.html",
        team_members=team_members,
        pending_invites=pending_invites,
        invite_form=TeamInviteForm(),
        bulk_import_form=BulkTeamImportForm(),
        bulk_import_result=bulk_import_result,
        users_limit=users_limit,
        users_used=users_used,
        remaining_seats=remaining_seats,
        usage=usage,
    )


@settings_bp.get("/team/download-csv-template")
@role_required("admin", "manager")
def download_team_csv_template():
    response = generate_csv_template_response("team_invite")
    try:
        AuditLog.log(
            db,
            event_type="csv_template_downloaded",
            description="Downloaded team CSV invitation template.",
            organisation_id=current_user.organisation_id,
            actor_user=current_user,
            ip_address=request.remote_addr,
        )
    except Exception:
        db.session.rollback()

    return response


@settings_bp.post("/team/bulk-import")
@role_required("admin")
def bulk_import_team_members():
    form = BulkTeamImportForm()
    if not form.validate_on_submit():
        flash("Please upload a valid CSV file before importing team members.", "danger")
        session["bulk_import_result"] = {
            "success": False,
            "error": "invalid_upload",
            "validation_errors": [],
            "rows_skipped_due_to_seat_limit": 0,
            "users_created": 0,
        }
        return redirect(url_for("settings.team"))

    if not bool(form.confirm_seat_limit.data):
        flash("Please confirm you understand seat limit handling before continuing.", "warning")
        return redirect(url_for("settings.team"))

    uploaded_file = form.csv_file.data
    if uploaded_file is None or not getattr(uploaded_file, "filename", ""):
        flash("Please choose a CSV file to import.", "danger")
        return redirect(url_for("settings.team"))

    if not str(uploaded_file.filename).lower().endswith(".csv"):
        flash("Only CSV files are accepted. Please download the template and save as .csv", "danger")
        return redirect(url_for("settings.team"))

    stream = uploaded_file.stream
    stream.seek(0, io.SEEK_END)
    file_size = stream.tell()
    stream.seek(0)
    if file_size > 2 * 1024 * 1024:
        flash("CSV file is too large. Maximum allowed size is 2MB.", "danger")
        return redirect(url_for("settings.team"))

    result = team_import_service.process_team_csv_import(
        csv_file_stream=stream,
        organisation=current_user.organisation,
        inviting_user=current_user,
        db_session=db,
        app_context=current_app._get_current_object(),
    )

    session["bulk_import_result"] = result
    session["bulk_import_error_report_rows"] = list(result.get("validation_errors", []))

    if result.get("success"):
        created = int(result.get("users_created", 0) or 0)
        skipped = int(result.get("rows_skipped_due_to_seat_limit", 0) or 0) + len(result.get("validation_errors", []))
        flash(f"Import complete: {created} users added, {skipped} rows skipped.", "success")
    else:
        error_type = result.get("error")
        if error_type == "no_seats_available":
            flash("No seats are available on your current plan. Upgrade to add more users.", "danger")
        elif error_type == "invalid_format":
            missing = ", ".join(result.get("missing_headers", []))
            flash(f"Invalid CSV format. Missing headers: {missing}", "danger")
        elif error_type == "empty_file":
            flash("The uploaded CSV file is empty.", "danger")
        elif error_type == "file_too_large":
            flash("CSV file is too large. Maximum allowed size is 2MB.", "danger")
        else:
            flash("Team import failed. Please review your CSV and try again.", "danger")

    return redirect(url_for("settings.team"))


@settings_bp.get("/team/bulk-import/error-report")
@role_required("admin", "manager")
def bulk_import_error_report():
    errors = session.get("bulk_import_error_report_rows", [])
    if not isinstance(errors, list) or not errors:
        flash("No import error report is available to download.", "warning")
        return redirect(url_for("settings.team"))

    csv_buffer = io.StringIO()
    writer = csv.writer(csv_buffer)
    writer.writerow(["Row", "Email", "Error"])
    for item in errors:
        writer.writerow([
            item.get("row", ""),
            item.get("email", ""),
            item.get("error", ""),
        ])

    response = current_app.response_class(csv_buffer.getvalue(), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=chainwatchpro_team_import_errors.csv"
    return response


@settings_bp.post("/team/invite")
@role_required("admin", "manager")
def invite_team_member():
    form = TeamInviteForm()
    if not form.validate_on_submit():
        flash("Please correct the invitation form errors.", "danger")
        return redirect(url_for("settings.team"))

    limit = razorpay_service.enforce_plan_limits(current_user.organisation, "users", db.session)
    if not limit.get("allowed", False):
        flash("User seat limit reached. Upgrade your plan to add more members.", "danger")
        return redirect(url_for("settings.team"))

    invited_email = (form.email.data or "").strip().lower()
    existing = User.query.filter(User.email == invited_email).first()
    if existing is not None:
        flash("That email is already registered on ChainWatch Pro.", "warning")
        return redirect(url_for("settings.team"))

    local_part = invited_email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ").strip()
    first_name = (local_part.split(" ", 1)[0] if local_part else "Team").title()[:100]
    last_name = (local_part.split(" ", 1)[1] if " " in local_part else "Member").title()[:100]
    temp_password = generate_secure_temporary_password()

    user = User(
        email=invited_email,
        first_name=first_name or "Team",
        last_name=last_name or "Member",
        role=form.role.data,
        is_verified=True,
        organisation_id=current_user.organisation_id,
        must_change_password=True,
        invited_by_user_id=current_user.id,
        invitation_sent_at=datetime.utcnow(),
        account_source="manual_invite",
        alert_email_enabled=True,
        alert_sms_enabled=False,
        onboarding_step_completed=4,
    )
    user.set_password(temp_password)
    user.temporary_password_hash = user.password_hash

    db.session.add(user)
    db.session.commit()

    email_service.send_team_invitation_email_with_credentials(
        current_user,
        user,
        temp_password,
        current_app._get_current_object(),
    )

    AuditLog.log(
        db,
        event_type="team_member_invited",
        description=f"Invited {invited_email} as {form.role.data}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={
            "invited_email": invited_email,
            "role": form.role.data,
            "account_source": "manual_invite",
        },
        ip_address=request.remote_addr,
    )

    flash(f"Invitation sent to {invited_email}.", "success")
    return redirect(url_for("settings.team"))


@settings_bp.post("/team/<uuid:user_id>/remove")
@role_required("admin")
def remove_team_member(user_id: uuid.UUID):
    if user_id == current_user.id:
        return jsonify({"success": False, "error": "You cannot remove yourself."}), 400

    target = User.query.filter_by(id=user_id).first_or_404()
    if target.organisation_id != current_user.organisation_id:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    if target.role == "admin":
        other_admins = (
            User.query.filter(
                User.organisation_id == current_user.organisation_id,
                User._is_active.is_(True),
                User.role == "admin",
                User.id != target.id,
            ).count()
        )
        if other_admins == 0:
            return jsonify({"success": False, "error": "Cannot remove the last admin."}), 400

    target.is_active = False
    db.session.commit()

    AuditLog.log(
        db,
        event_type="team_member_removed",
        description=f"Removed team member {target.email}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={"user_email": target.email},
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True})


@settings_bp.post("/team/<uuid:user_id>/role")
@role_required("admin")
def update_team_role(user_id: uuid.UUID):
    payload = request.get_json(silent=True) or {}
    new_role = (payload.get("role") or "").strip().lower()
    if new_role not in {"admin", "manager", "viewer"}:
        return jsonify({"success": False, "error": "Invalid role."}), 400

    if user_id == current_user.id:
        return jsonify({"success": False, "error": "You cannot change your own role."}), 400

    target = User.query.filter_by(id=user_id).first_or_404()
    if target.organisation_id != current_user.organisation_id:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    old_role = target.role
    if old_role == "admin" and new_role != "admin":
        remaining_admins = (
            User.query.filter(
                User.organisation_id == current_user.organisation_id,
                User._is_active.is_(True),
                User.role == "admin",
                User.id != target.id,
            ).count()
        )
        if remaining_admins == 0:
            return jsonify({"success": False, "error": "Cannot demote the last admin."}), 400

    target.role = new_role
    db.session.commit()

    AuditLog.log(
        db,
        event_type="role_updated",
        description=f"Updated role for {target.email} from {old_role} to {new_role}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={"user_email": target.email, "old_role": old_role, "new_role": new_role},
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True, "new_role": new_role})


@settings_bp.post("/team/<uuid:user_id>/resend-invite")
@role_required("admin", "manager")
def resend_invite(user_id: uuid.UUID):
    user = User.query.filter_by(id=user_id).first_or_404()
    if user.organisation_id != current_user.organisation_id:
        return jsonify({"success": False, "error": "Forbidden"}), 403

    if not user.must_change_password:
        return jsonify({"success": False, "error": "Invitation already accepted."}), 400

    temp_password = generate_secure_temporary_password()
    user.set_password(temp_password)
    user.temporary_password_hash = user.password_hash
    user.invited_by_user_id = current_user.id
    user.invitation_sent_at = datetime.utcnow()
    db.session.commit()

    sent = email_service.send_team_invitation_email_with_credentials(
        current_user,
        user,
        temp_password,
        current_app._get_current_object(),
    )
    if not sent:
        return jsonify({"success": False, "error": "Failed to send invitation email."}), 500

    AuditLog.log(
        db,
        event_type="invitation_resent",
        description=f"Resent invitation to {user.email}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={"target_email": user.email},
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True, "message": f"Invitation resent to {user.email}"})


@settings_bp.post("/team/<uuid:user_id>/cancel-invite")
@role_required("admin", "manager")
def cancel_invite(user_id: uuid.UUID):
    user = User.query.filter_by(id=user_id).first_or_404()
    if user.organisation_id != current_user.organisation_id:
        return jsonify({"success": False, "error": "Forbidden"}), 403
    if not user.must_change_password:
        return jsonify({"success": False, "error": "Cannot cancel an accepted invite."}), 400
    if user.last_login_at is not None:
        return jsonify({"success": False, "error": "Cannot cancel invite after first login."}), 400

    target_email = user.email
    db.session.delete(user)
    db.session.commit()

    AuditLog.log(
        db,
        event_type="invitation_cancelled",
        description=f"Cancelled pending invitation for {target_email}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={"target_email": target_email},
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True, "message": "Invitation cancelled."})


@settings_bp.get("/integrations")
@role_required("admin", "manager")
def integrations():
    carriers = (
        Carrier.query.outerjoin(Shipment, Shipment.carrier_id == Carrier.id)
        .filter(
            or_(
                Carrier.is_global_carrier.is_(True),
                Shipment.organisation_id == current_user.organisation_id,
            )
        )
        .distinct(Carrier.id)
        .order_by(Carrier.name.asc())
        .all()
    )

    profile = _org_profile()
    credentials_map = profile.get("carrier_credentials", {})
    if not isinstance(credentials_map, dict):
        credentials_map = {}

    rendered = []
    for carrier in carriers:
        raw = credentials_map.get(str(carrier.id)) or {}
        connected = bool(raw)

        masked_key = ""
        if connected:
            try:
                decrypted = _decrypt_credentials(raw)
                masked_key = _mask_secret(decrypted.get("api_key", ""))
            except Exception:
                masked_key = ""

        rendered.append(
            {
                "carrier": carrier,
                "connected": connected,
                "masked_api_key": masked_key,
                "last_tested_at": raw.get("last_tested_at") if isinstance(raw, dict) else None,
            }
        )

    usage = razorpay_service.get_usage_meters(current_user.organisation, db.session)
    carrier_meter = usage.get("carriers", {})

    return render_template(
        "app/settings/integrations.html",
        global_carriers=rendered,
        connected_count=int(carrier_meter.get("used", 0) or 0),
        carrier_limit=carrier_meter.get("limit"),
        usage=usage,
        connect_form=CarrierConnectForm(),
        csv_import_form=CarrierCSVImportForm(),
    )


@settings_bp.post("/integrations/connect")
@role_required("admin", "manager")
def connect_integration():
    form = CarrierConnectForm()
    if not form.validate_on_submit():
        return jsonify({"success": False, "errors": form.errors}), 400

    try:
        carrier_id = uuid.UUID(form.carrier_id.data)
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "Invalid carrier id."}), 400

    carrier = Carrier.query.filter_by(id=carrier_id).first_or_404()

    profile = _org_profile()
    credentials_map = profile.get("carrier_credentials", {})
    if not isinstance(credentials_map, dict):
        credentials_map = {}

    already_connected = str(carrier.id) in credentials_map
    if not already_connected:
        limit = razorpay_service.enforce_plan_limits(current_user.organisation, "carriers", db.session)
        if not limit.get("allowed", False):
            return jsonify({"success": False, "error": "Carrier integration limit reached."}), 403

    try:
        encrypted = _encrypt_credentials(
            {
                "api_key": form.api_key.data,
                "api_secret": form.api_secret.data,
                "api_endpoint": form.api_endpoint.data,
            }
        )
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        return jsonify({"success": False, "error": "Unable to store credentials securely."}), 500

    encrypted["connected_at"] = datetime.utcnow().isoformat()
    encrypted.setdefault("last_tested_at", None)

    credentials_map[str(carrier.id)] = encrypted
    profile["carrier_credentials"] = credentials_map
    current_user.organisation.org_profile_data = profile
    flag_modified(current_user.organisation, "org_profile_data")
    db.session.commit()

    AuditLog.log(
        db,
        event_type="carrier_connected",
        description=f"Connected carrier integration for {carrier.name}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={"carrier_name": carrier.name, "carrier_id": str(carrier.id)},
        ip_address=request.remote_addr,
    )

    flash(f"Carrier {carrier.name} connected successfully.", "success")
    return jsonify({"success": True})


@settings_bp.post("/integrations/<uuid:carrier_id>/disconnect")
@role_required("admin", "manager")
def disconnect_integration(carrier_id: uuid.UUID):
    carrier = Carrier.query.filter_by(id=carrier_id).first_or_404()

    profile = _org_profile()
    credentials_map = profile.get("carrier_credentials", {})
    if not isinstance(credentials_map, dict):
        credentials_map = {}

    if str(carrier_id) in credentials_map:
        credentials_map.pop(str(carrier_id), None)
        profile["carrier_credentials"] = credentials_map
        current_user.organisation.org_profile_data = profile
        flag_modified(current_user.organisation, "org_profile_data")
        db.session.commit()

        AuditLog.log(
            db,
            event_type="carrier_disconnected",
            description=f"Disconnected carrier integration for {carrier.name}.",
            organisation_id=current_user.organisation_id,
            actor_user=current_user,
            metadata={"carrier_name": carrier.name, "carrier_id": str(carrier.id)},
            ip_address=request.remote_addr,
        )

    return jsonify({"success": True})


@settings_bp.post("/integrations/<uuid:carrier_id>/test")
@role_required("admin", "manager")
def test_integration(carrier_id: uuid.UUID):
    carrier = Carrier.query.filter_by(id=carrier_id).first_or_404()

    profile = _org_profile()
    credentials_map = profile.get("carrier_credentials", {})
    if not isinstance(credentials_map, dict):
        credentials_map = {}

    encrypted = credentials_map.get(str(carrier_id))
    if not isinstance(encrypted, dict):
        return jsonify({"success": False, "latency_ms": None, "message": "Carrier is not connected."}), 404

    try:
        credentials = _decrypt_credentials(encrypted)
    except ValueError as exc:
        return jsonify({"success": False, "latency_ms": None, "message": str(exc)}), 400
    except Exception:
        return jsonify({"success": False, "latency_ms": None, "message": "Credential decryption failed."}), 500

    endpoint = (credentials.get("api_endpoint") or carrier.website_url or "").strip()
    if carrier.tracking_api_type and carrier.tracking_api_type.upper() == "REST":
        if not endpoint:
            return jsonify({"success": False, "latency_ms": None, "message": "No API endpoint configured."}), 400

        start = time.perf_counter()
        try:
            response = requests.get(endpoint, timeout=5)
            latency_ms = int((time.perf_counter() - start) * 1000)

            if response.status_code in {401, 403}:
                return jsonify(
                    {
                        "success": False,
                        "latency_ms": latency_ms,
                        "message": "Authentication failure while testing carrier endpoint.",
                    }
                )

            if 200 <= response.status_code < 500:
                encrypted["last_tested_at"] = datetime.utcnow().isoformat()
                credentials_map[str(carrier_id)] = encrypted
                profile["carrier_credentials"] = credentials_map
                current_user.organisation.org_profile_data = profile
                flag_modified(current_user.organisation, "org_profile_data")
                db.session.commit()
                return jsonify(
                    {
                        "success": True,
                        "latency_ms": latency_ms,
                        "message": f"Connection successful ({latency_ms}ms).",
                    }
                )

            return jsonify(
                {
                    "success": False,
                    "latency_ms": latency_ms,
                    "message": "Carrier endpoint returned an unexpected server response.",
                }
            )
        except requests.Timeout:
            return jsonify({"success": False, "latency_ms": None, "message": "Connection timeout."})
        except requests.RequestException:
            return jsonify({"success": False, "latency_ms": None, "message": "Network error while connecting."})

    return jsonify({"success": True, "latency_ms": None, "message": "Manual integration is connected."})


@settings_bp.post("/integrations/import-csv")
@role_required("admin", "manager")
def import_integration_csv():
    form = CarrierCSVImportForm()
    if not form.validate_on_submit():
        flash("Please upload a valid CSV file.", "danger")
        return redirect(url_for("settings.integrations"))

    carrier_name = (form.carrier_name.data or "").strip()
    carrier = Carrier.query.filter(Carrier.name.ilike(carrier_name)).first()
    if carrier is None:
        carrier = Carrier(
            name=carrier_name,
            mode="multimodal",
            tracking_api_type="MANUAL",
            is_global_carrier=False,
        )
        db.session.add(carrier)
        db.session.flush()

    try:
        file_bytes = form.csv_file.data.read()
        decoded = file_bytes.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(decoded))
        rows = [row for row in reader if isinstance(row, dict)]
    except Exception:
        flash("Could not parse the uploaded CSV file.", "danger")
        db.session.rollback()
        return redirect(url_for("settings.integrations"))

    summary = carrier_tracker.ingest_historical_performance_data(
        carrier_name=carrier.name,
        organisation_id=current_user.organisation_id,
        shipment_data_rows=rows,
        db_session=db.session,
    )

    flash(
        (
            f"Imported carrier data. Processed {summary.get('records_processed', 0)} rows, "
            f"updated {summary.get('groups_updated', 0)} groups, created {summary.get('groups_created', 0)} groups."
        ),
        "success",
    )
    return redirect(url_for("settings.integrations"))


@settings_bp.route("/alerts", methods=["GET", "POST"])
@role_required("admin", "manager")
def alerts():
    profile = _org_profile()

    alert_rules = profile.get("alert_rules", [])
    if not isinstance(alert_rules, list):
        alert_rules = []

    settings = profile.get("alert_settings", {})
    if not isinstance(settings, dict):
        settings = {}

    rule_form = AlertRuleForm()
    global_form = GlobalAlertSettingsForm()

    if request.method == "GET":
        global_form.drs_warning_threshold.data = int(settings.get("drs_warning_threshold", 60) or 60)
        global_form.drs_critical_threshold.data = int(settings.get("drs_critical_threshold", 80) or 80)
        global_form.alert_frequency.data = settings.get("alert_frequency", "immediate")
        global_form.webhook_url.data = settings.get("webhook_url", "")
        global_form.webhook_enabled.data = bool(settings.get("webhook_enabled", False))

    if request.method == "POST":
        form_type = (request.form.get("form_type") or "").strip().lower()

        if form_type == "global" and global_form.validate_on_submit():
            profile["alert_settings"] = {
                "drs_warning_threshold": int(global_form.drs_warning_threshold.data),
                "drs_critical_threshold": int(global_form.drs_critical_threshold.data),
                "alert_frequency": global_form.alert_frequency.data,
                "webhook_url": (global_form.webhook_url.data or "").strip(),
                "webhook_enabled": bool(global_form.webhook_enabled.data),
            }
            _save_org_profile(profile)
            flash("Global alert settings saved.", "success")
            return redirect(url_for("settings.alerts"))

        if form_type == "rule" and rule_form.validate_on_submit():
            channels = []
            if rule_form.notify_email.data:
                channels.append("email")
            if rule_form.notify_sms.data:
                channels.append("sms")
            if rule_form.notify_webhook.data:
                channels.append("webhook")

            rule = {
                "rule_id": str(uuid.uuid4()),
                "rule_type": rule_form.rule_type.data,
                "target_id": (rule_form.target_identifier.data or "").strip(),
                "condition": rule_form.condition.data,
                "threshold_value": rule_form.threshold_value.data,
                "notification_channels": channels,
                "is_active": True,
                "created_at": datetime.utcnow().isoformat(),
            }

            alert_rules.append(rule)
            profile["alert_rules"] = alert_rules
            _save_org_profile(profile)

            AuditLog.log(
                db,
                event_type="alert_rule_created",
                description="Created custom alert rule.",
                organisation_id=current_user.organisation_id,
                actor_user=current_user,
                metadata=rule,
                ip_address=request.remote_addr,
            )

            flash("Alert rule added.", "success")
            return redirect(url_for("settings.alerts"))

    return render_template(
        "app/settings/alerts.html",
        alert_rules=alert_rules,
        rule_form=rule_form,
        global_settings_form=global_form,
    )


@settings_bp.post("/alerts/rules/<rule_id>/delete")
@role_required("admin", "manager")
def delete_alert_rule(rule_id: str):
    profile = _org_profile()
    alert_rules = profile.get("alert_rules", [])
    if not isinstance(alert_rules, list):
        alert_rules = []

    profile["alert_rules"] = [rule for rule in alert_rules if str(rule.get("rule_id")) != str(rule_id)]
    _save_org_profile(profile)
    return jsonify({"success": True})


@settings_bp.post("/alerts/rules/<rule_id>/toggle")
@role_required("admin", "manager")
def toggle_alert_rule(rule_id: str):
    profile = _org_profile()
    alert_rules = profile.get("alert_rules", [])
    if not isinstance(alert_rules, list):
        alert_rules = []

    toggled = False
    value = False
    for rule in alert_rules:
        if str(rule.get("rule_id")) == str(rule_id):
            rule["is_active"] = not bool(rule.get("is_active", True))
            value = bool(rule["is_active"])
            toggled = True
            break

    if toggled:
        profile["alert_rules"] = alert_rules
        _save_org_profile(profile)

    return jsonify({"success": bool(toggled), "is_active": value})


@settings_bp.post("/alerts/test-webhook")
@role_required("admin", "manager")
def test_alert_webhook():
    profile = _org_profile()
    alert_settings = profile.get("alert_settings", {})
    if not isinstance(alert_settings, dict):
        alert_settings = {}

    webhook_url = (request.form.get("webhook_url") or alert_settings.get("webhook_url") or "").strip()
    if not webhook_url:
        return jsonify({"success": False, "message": "Webhook URL is required."}), 400

    payload = {
        "event": "chainwatchpro.webhook.test",
        "timestamp": datetime.utcnow().isoformat(),
        "organisation_id": str(current_user.organisation_id),
        "message": "This is a test webhook from ChainWatch Pro.",
    }

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=5,
            headers={"Content-Type": "application/json"},
        )
        if 200 <= response.status_code < 300:
            return jsonify({"success": True, "message": "Webhook test delivered successfully."})
        return jsonify({"success": False, "message": f"Webhook returned HTTP {response.status_code}."})
    except requests.Timeout:
        return jsonify({"success": False, "message": "Webhook test timed out."})
    except requests.RequestException:
        return jsonify({"success": False, "message": "Webhook test failed due to network error."})


@settings_bp.get("/billing")
def billing():
    organisation = current_user.organisation
    usage = razorpay_service.get_usage_meters(organisation, db.session)
    invoice_history = razorpay_service.get_invoice_history(organisation, count=12)

    subscription_details = None
    if organisation.razorpay_subscription_id:
        subscription_details = razorpay_service.get_subscription_details(organisation.razorpay_subscription_id)

    trial_days_remaining = 0
    if organisation.is_trial_active and organisation.trial_ends_at:
        trial_days_remaining = max((organisation.trial_ends_at - datetime.utcnow()).days, 0)

    return render_template(
        "app/settings/billing.html",
        organisation=organisation,
        usage=usage,
        plans=razorpay_service.RAZORPAY_PLANS,
        invoice_history=invoice_history,
        subscription_details=subscription_details,
        razorpay_key_id=current_app.config.get("RAZORPAY_KEY_ID", ""),
        current_plan=organisation.subscription_plan,
        is_trial=organisation.is_trial_active,
        trial_days_remaining=trial_days_remaining,
    )


@settings_bp.post("/billing/upgrade")
@role_required("admin")
def upgrade_billing():
    payload = request.get_json(silent=True) or {}
    plan = (payload.get("plan") or "").strip().lower()
    billing_cycle = (payload.get("billing_cycle") or "monthly").strip().lower()

    if plan not in {"starter", "professional", "enterprise"}:
        return jsonify({"success": False, "error": "Invalid plan selection."}), 400
    if billing_cycle not in {"monthly", "annual"}:
        return jsonify({"success": False, "error": "Invalid billing cycle."}), 400
    if plan == current_user.organisation.subscription_plan:
        return jsonify({"success": False, "error": "You are already on this plan."}), 400

    if plan == "enterprise":
        query = urlencode({"subject": "Enterprise Plan Inquiry"})
        return jsonify({"success": False, "redirect_url": f"/contact?{query}"}), 200

    razorpay_key_id = str(current_app.config.get("RAZORPAY_KEY_ID", "") or "").strip()
    razorpay_key_secret = str(current_app.config.get("RAZORPAY_KEY_SECRET", "") or "").strip()
    if not razorpay_key_id or not razorpay_key_secret:
        logger.error(
            "Billing upgrade blocked due to missing Razorpay credentials org_id=%s",
            current_user.organisation_id,
        )
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Billing is not configured right now. Please contact support.",
                }
            ),
            503,
        )

    selected = razorpay_service.RAZORPAY_PLANS[plan]
    amount = selected["price_monthly_inr"] if billing_cycle == "monthly" else selected["price_annual_inr"]
    if not amount:
        return jsonify({"success": False, "error": "Selected plan cannot be billed online."}), 400

    plan_config = razorpay_service.resolve_plan_configuration(plan, billing_cycle)
    if plan_config.get("plan_id"):
        subscription = razorpay_service.create_subscription(
            current_user.organisation,
            plan,
            billing_cycle,
            db.session,
        )
        if subscription is None:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Unable to start subscription right now. Please try again shortly.",
                    }
                ),
                502,
            )

        return jsonify(
            {
                "success": True,
                "checkout_mode": "subscription",
                "subscription_id": subscription.get("id"),
                "razorpay_key_id": current_app.config.get("RAZORPAY_KEY_ID", ""),
                "amount_inr": amount,
                "currency": "INR",
            }
        )

    logger.warning(
        "Billing upgrade using order fallback org_id=%s plan=%s cycle=%s missing_config_key=%s",
        current_user.organisation_id,
        plan,
        billing_cycle,
        plan_config.get("config_key", ""),
    )

    order = razorpay_service.create_one_time_order(
        current_user.organisation,
        plan,
        billing_cycle,
        db.session,
    )
    if order is None:
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Unable to start payment right now. Please try again shortly.",
                }
            ),
            502,
        )

    return jsonify(
        {
            "success": True,
            "checkout_mode": "order",
            "order_id": order.get("id"),
            "razorpay_key_id": current_app.config.get("RAZORPAY_KEY_ID", ""),
            "amount_inr": amount,
            "currency": "INR",
        }
    )


@settings_bp.post("/billing/verify-payment")
@role_required("admin")
def verify_billing_payment():
    payload = request.get_json(silent=True) or {}

    payment_id = payload.get("razorpay_payment_id")
    subscription_id = payload.get("razorpay_subscription_id")
    order_id = payload.get("razorpay_order_id")
    signature = payload.get("razorpay_signature")

    profile = _org_profile()
    pending = profile.get("pending_subscription", {})
    if not isinstance(pending, dict):
        pending = {}

    payment_mode = pending.get("payment_mode", "subscription")
    plan = pending.get("plan", current_user.organisation.subscription_plan)
    billing_cycle = pending.get("billing_cycle", profile.get("billing_cycle", "monthly"))

    if payment_mode == "order" or order_id:
        expected_order_id = pending.get("order_id")
        if not expected_order_id or not order_id or order_id != expected_order_id:
            return jsonify({"success": False, "error": "Payment verification failed"}), 400
        if not razorpay_service.verify_order_payment_signature(order_id, payment_id, signature):
            return jsonify({"success": False, "error": "Payment verification failed"}), 400
    else:
        expected_subscription_id = pending.get("subscription_id")
        if expected_subscription_id:
            if expected_subscription_id != subscription_id:
                return jsonify({"success": False, "error": "Payment verification failed"}), 400
        elif not subscription_id or current_user.organisation.razorpay_subscription_id != subscription_id:
            return jsonify({"success": False, "error": "Payment verification failed"}), 400
        if not razorpay_service.verify_payment_signature(payment_id, subscription_id, signature):
            return jsonify({"success": False, "error": "Payment verification failed"}), 400

    current_user.organisation.subscription_status = "active"
    if plan in {"starter", "professional", "enterprise"}:
        current_user.organisation.subscription_plan = plan

    profile["billing_cycle"] = billing_cycle
    if payment_mode == "order" or order_id:
        due_days = 30 if billing_cycle == "monthly" else 365
        profile["manual_billing"] = {
            "payment_mode": "order",
            "last_payment_id": payment_id,
            "last_order_id": order_id,
            "next_due_at": (datetime.utcnow() + timedelta(days=due_days)).isoformat(),
        }

    profile.pop("pending_subscription", None)
    current_user.organisation.org_profile_data = profile
    flag_modified(current_user.organisation, "org_profile_data")

    db.session.commit()

    amount_paise = razorpay_service.RAZORPAY_PLANS.get(plan, {}).get(
        "price_monthly_inr" if billing_cycle == "monthly" else "price_annual_inr",
        0,
    ) or 0

    AuditLog.log(
        db,
        event_type="subscription_upgraded",
        description=f"Subscription upgraded to {plan} ({billing_cycle}).",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={
            "plan": plan,
            "billing_cycle": billing_cycle,
            "payment_id": payment_id,
            "subscription_id": subscription_id,
            "order_id": order_id,
            "payment_mode": payment_mode,
            "amount_inr": float(amount_paise) / 100.0,
        },
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True})


@settings_bp.post("/billing/cancel")
@role_required("admin")
def cancel_billing():
    if not current_user.organisation.razorpay_subscription_id:
        profile = _org_profile()
        manual_billing = profile.get("manual_billing", {}) if isinstance(profile, dict) else {}
        if isinstance(manual_billing, dict) and manual_billing.get("payment_mode") == "order":
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "This plan uses one-time payment. There is no auto-renew subscription to cancel.",
                    }
                ),
                400,
            )

    result = razorpay_service.cancel_subscription(current_user.organisation, db.session)
    if not result.get("success"):
        return jsonify({"success": False, "error": "Unable to cancel subscription."}), 500

    AuditLog.log(
        db,
        event_type="subscription_cancelled",
        description="Cancelled active subscription at period end.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={"cancel_at": result.get("cancel_at")},
        ip_address=request.remote_addr,
    )

    return jsonify(
        {
            "success": True,
            "message": "Your subscription will remain active until the end of the current billing period.",
            "cancel_at": result.get("cancel_at"),
        }
    )
