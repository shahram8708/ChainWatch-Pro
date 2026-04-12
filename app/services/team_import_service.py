"""Bulk team import service for CSV-based invited user creation."""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from datetime import datetime

from email_validator import EmailNotValidError, validate_email

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.user import User
from app.services import razorpay_service
from app.services.notification import email_service
from app.utils.helpers import generate_secure_temporary_password

logger = logging.getLogger(__name__)

_REQUIRED_HEADERS = {"first_name", "last_name", "email", "role"}
_PHONE_REGEX = re.compile(r"^\+?[1-9]\d{7,14}$")


def _read_stream_bytes(csv_file_stream):
    if csv_file_stream is None:
        return b""

    if hasattr(csv_file_stream, "seek"):
        csv_file_stream.seek(0)

    raw = csv_file_stream.read()
    if isinstance(raw, str):
        return raw.encode("utf-8")
    if isinstance(raw, bytes):
        return raw
    return b""


def _normalize_headers(fieldnames):
    headers = []
    for field in fieldnames or []:
        headers.append((field or "").strip().lower())
    return headers


def _row_is_empty(row_dict: dict) -> bool:
    for value in row_dict.values():
        if (value or "").strip():
            return False
    return True


def process_team_csv_import(csv_file_stream, organisation, inviting_user, db_session, app_context) -> dict:
    """Validate, import, and invite team members from an uploaded CSV stream."""

    session_obj = db_session.session if hasattr(db_session, "session") else db_session

    file_bytes = _read_stream_bytes(csv_file_stream)
    file_size = len(file_bytes)

    if file_size == 0:
        return {"success": False, "error": "empty_file"}

    if file_size > 2 * 1024 * 1024:
        return {"success": False, "error": "file_too_large", "max_size_mb": 2}

    try:
        decoded = file_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        return {"success": False, "error": "invalid_encoding"}

    reader = csv.DictReader(io.StringIO(decoded))
    normalized_headers = set(_normalize_headers(reader.fieldnames))

    missing_headers = sorted(header for header in _REQUIRED_HEADERS if header not in normalized_headers)
    if missing_headers:
        return {
            "success": False,
            "error": "invalid_format",
            "missing_headers": missing_headers,
        }

    parsed_rows = []
    for row_index, row in enumerate(reader, start=2):
        row_data = {str(key or "").strip().lower(): (value or "").strip() for key, value in row.items()}
        if _row_is_empty(row_data):
            continue
        parsed_rows.append(
            {
                "row": row_index,
                "first_name": row_data.get("first_name", ""),
                "last_name": row_data.get("last_name", ""),
                "email": row_data.get("email", ""),
                "role": row_data.get("role", ""),
                "job_title": row_data.get("job_title", ""),
                "phone": row_data.get("phone", ""),
            }
        )

    total_csv_rows = len(parsed_rows)
    if total_csv_rows == 0:
        return {"success": False, "error": "empty_file"}

    current_count = (
        User.query.filter(
            User.organisation_id == organisation.id,
            User._is_active.is_(True),
        ).count()
    )

    plan_key = (organisation.subscription_plan or "starter").strip().lower()
    plan_config = razorpay_service.RAZORPAY_PLANS.get(plan_key, razorpay_service.RAZORPAY_PLANS["starter"])
    user_limit = plan_config.get("user_limit")

    if user_limit is None:
        available_seats = float("inf")
    else:
        available_seats = max(int(user_limit) - int(current_count), 0)

    if available_seats == 0:
        return {
            "success": False,
            "error": "no_seats_available",
            "current_count": int(current_count),
            "limit": int(user_limit),
        }

    email_counts: dict[str, int] = {}
    for row in parsed_rows:
        email_key = (row.get("email") or "").strip().lower()
        if not email_key:
            continue
        email_counts[email_key] = email_counts.get(email_key, 0) + 1

    candidate_emails = [email for email in email_counts.keys() if email]
    existing_emails = {
        item.email.strip().lower()
        for item in session_obj.query(User.email).filter(User.email.in_(candidate_emails)).all()
        if item.email
    }

    valid_rows = []
    validation_errors = []
    duplicate_count = 0
    validation_error_count = 0

    for row in parsed_rows:
        row_number = int(row["row"])
        first_name = (row.get("first_name") or "").strip()
        last_name = (row.get("last_name") or "").strip()
        email_raw = (row.get("email") or "").strip().lower()
        role_value = (row.get("role") or "").strip().lower()
        job_title = (row.get("job_title") or "").strip() or None
        phone = (row.get("phone") or "").strip() or None

        if len(first_name) < 2 or len(first_name) > 100:
            validation_error_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": email_raw,
                    "error": "First name must be between 2 and 100 characters.",
                }
            )
            continue

        if len(last_name) < 2 or len(last_name) > 100:
            validation_error_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": email_raw,
                    "error": "Last name must be between 2 and 100 characters.",
                }
            )
            continue

        normalized_email = ""
        if not email_raw:
            validation_error_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": email_raw,
                    "error": "Email is required.",
                }
            )
            continue

        try:
            normalized_email = validate_email(email_raw, check_deliverability=False).email.lower()
        except EmailNotValidError:
            validation_error_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": email_raw,
                    "error": "Invalid email format",
                }
            )
            continue

        if role_value not in {"manager", "viewer"}:
            validation_error_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": normalized_email,
                    "error": "Role must be manager or viewer.",
                }
            )
            continue

        if phone and not _PHONE_REGEX.fullmatch(phone):
            validation_error_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": normalized_email,
                    "error": "Phone number must be in international format.",
                }
            )
            continue

        if email_counts.get(normalized_email, 0) > 1:
            duplicate_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": normalized_email,
                    "error": "Duplicate email found in uploaded CSV.",
                }
            )
            continue

        if normalized_email in existing_emails:
            duplicate_count += 1
            validation_errors.append(
                {
                    "row": row_number,
                    "email": normalized_email,
                    "error": "Email already registered",
                }
            )
            continue

        valid_rows.append(
            {
                "row": row_number,
                "first_name": first_name,
                "last_name": last_name,
                "email": normalized_email,
                "role": role_value,
                "job_title": job_title,
                "phone": phone,
            }
        )

    if user_limit is None:
        rows_skipped_due_to_seat_limit = 0
        rows_to_create = valid_rows
    else:
        rows_to_create = valid_rows[: int(available_seats)]
        rows_skipped_due_to_seat_limit = max(len(valid_rows) - int(available_seats), 0)

    users_with_passwords = []
    created_users = []

    try:
        for row in rows_to_create:
            temp_password = generate_secure_temporary_password()
            user = User(
                id=uuid.uuid4(),
                email=row["email"],
                first_name=row["first_name"],
                last_name=row["last_name"],
                phone=row["phone"],
                role=row["role"],
                organisation_id=organisation.id,
                is_verified=True,
                is_active=True,
                must_change_password=True,
                account_source="csv_bulk_import",
                invited_by_user_id=inviting_user.id,
                invitation_sent_at=datetime.utcnow(),
                onboarding_step_completed=4,
            )
            user.set_password(temp_password)
            user.temporary_password_hash = user.password_hash
            user.job_title = row["job_title"]

            session_obj.add(user)
            users_with_passwords.append((user, temp_password))
            created_users.append(
                {
                    "email": user.email,
                    "name": user.full_name,
                    "role": user.role,
                }
            )

        session_obj.commit()
    except Exception as exc:
        session_obj.rollback()
        logger.exception("Bulk team CSV import failed to commit for org_id=%s", organisation.id)
        return {
            "success": False,
            "error": "database_error",
            "detail": str(exc),
        }

    emails_sent = 0
    emails_failed = 0
    for user, temp_password in users_with_passwords:
        try:
            sent = email_service.send_team_invitation_email_with_credentials(
                inviting_user=inviting_user,
                invited_user=user,
                temporary_password=temp_password,
                app_context=app_context,
            )
            if sent:
                emails_sent += 1
            else:
                emails_failed += 1
        except Exception:
            emails_failed += 1
            logger.exception("Failed to send CSV import invitation email user_id=%s", user.id)

    try:
        AuditLog.log(
            db,
            event_type="csv_bulk_import_completed",
            description=f"Completed CSV team import for {organisation.name}.",
            organisation_id=organisation.id,
            actor_user=inviting_user,
            metadata={
                "total_rows_in_csv": total_csv_rows,
                "rows_processed": len(rows_to_create),
                "users_created": len(created_users),
                "rows_skipped_validation_error": validation_error_count,
                "rows_skipped_duplicate": duplicate_count,
                "rows_skipped_seat_limit": rows_skipped_due_to_seat_limit,
                "invited_by": inviting_user.email,
            },
        )
    except Exception:
        session_obj.rollback()
        logger.exception("Failed to log csv_bulk_import_completed audit event org_id=%s", organisation.id)

    return {
        "success": True,
        "users_created": len(created_users),
        "emails_sent": emails_sent,
        "emails_failed": emails_failed,
        "rows_skipped_due_to_seat_limit": rows_skipped_due_to_seat_limit,
        "validation_errors": validation_errors,
        "seat_limit_info": {
            "plan": plan_key,
            "limit": user_limit,
            "was_at": int(current_count),
            "available_was": None if user_limit is None else int(available_seats),
            "imported": len(created_users),
            "skipped_limit": rows_skipped_due_to_seat_limit,
        },
        "created_users": created_users,
    }
