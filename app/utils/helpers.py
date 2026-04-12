"""General helper utilities used by routes, templates, and services."""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import secrets
import socket
import string
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from smtplib import SMTPException
from threading import Thread
from urllib.parse import urlparse

import pytz
from flask import Response, g
from flask_login import current_user

from app.extensions import mail
from app.utils.validators import validate_password_strength

logger = logging.getLogger(__name__)


def get_current_org():
    """Return the authenticated user's organisation and cache it on g."""

    if getattr(g, "current_org", None) is not None:
        return g.current_org

    if not current_user.is_authenticated or not getattr(current_user, "organisation_id", None):
        g.current_org = None
        return None

    from app.models.organisation import Organisation

    g.current_org = Organisation.query.filter_by(id=current_user.organisation_id).first()
    return g.current_org


def _format_indian_integer(number: str) -> str:
    if len(number) <= 3:
        return number

    last_three = number[-3:]
    remaining = number[:-3]
    grouped: list[str] = []

    while len(remaining) > 2:
        grouped.insert(0, remaining[-2:])
        remaining = remaining[:-2]

    if remaining:
        grouped.insert(0, remaining)

    grouped.append(last_three)
    return ",".join(grouped)


def format_inr(amount) -> str:
    """Format a number into Indian Rupee notation."""

    if amount is None:
        return "₹0.00"

    try:
        decimal_amount = Decimal(str(amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError):
        return "₹0.00"

    sign = "-" if decimal_amount < 0 else ""
    normalized = f"{abs(decimal_amount):.2f}"
    integer_part, fraction_part = normalized.split(".")
    grouped_integer = _format_indian_integer(integer_part)

    return f"{sign}₹{grouped_integer}.{fraction_part}"


def format_datetime_user(dt, user_timezone: str, fmt: str = "%d %b %Y %H:%M") -> str:
    """Convert UTC datetime to a user timezone and return formatted text."""

    if dt is None:
        return "-"

    timezone_name = user_timezone or "UTC"

    try:
        target_tz = pytz.timezone(timezone_name)
    except pytz.UnknownTimeZoneError:
        target_tz = pytz.UTC

    if dt.tzinfo is None:
        dt_utc = pytz.UTC.localize(dt)
    else:
        dt_utc = dt.astimezone(pytz.UTC)

    return dt_utc.astimezone(target_tz).strftime(fmt)


def paginate_query(query, page: int, per_page: int = 25):
    """Paginate a SQLAlchemy query safely."""

    return query.paginate(page=page, per_page=per_page, error_out=False)


def generate_shipment_id() -> str:
    """Generate a shipment reference ID in SHP-00001 format."""

    from app.models.shipment import Shipment

    next_number = Shipment.query.count() + 1
    return f"SHP-{next_number:05d}"


def hash_token(token: str) -> str:
    """Return SHA-256 hash for token storage and comparison."""

    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def send_async_email(app, msg) -> None:
    """Send email in a background thread without blocking requests."""

    def _normalize_mail_server(value: str | None) -> str:
        candidate = (value or "").strip()
        if not candidate:
            return ""

        if "://" in candidate:
            parsed = urlparse(candidate)
            if parsed.hostname:
                return parsed.hostname.strip()

        return candidate

    def _send_message(flask_app, message):
        with flask_app.app_context():
            mail_server = _normalize_mail_server(flask_app.config.get("MAIL_SERVER"))
            if not mail_server:
                logger.warning("Email skipped because MAIL_SERVER is empty.")
                return

            if mail_server == "smtp.yourprovider.com":
                logger.warning(
                    "Email skipped because MAIL_SERVER is still the placeholder value."
                )
                return

            flask_app.config["MAIL_SERVER"] = mail_server

            try:
                mail.send(message)
            except socket.gaierror as exc:
                logger.error(
                    "Failed to resolve SMTP host '%s' while sending email (errno=%s). "
                    "Verify MAIL_SERVER and local DNS/network.",
                    mail_server,
                    getattr(exc, "errno", "n/a"),
                )
            except OSError as exc:
                logger.error(
                    "Network error while sending email via SMTP host '%s': %s",
                    mail_server,
                    exc,
                )
            except SMTPException:
                logger.exception("SMTPException occurred while sending email message.")
            except Exception:
                logger.exception("Unexpected error occurred while sending email message.")

    Thread(target=_send_message, args=(app, msg), daemon=True).start()


def generate_secure_temporary_password() -> str:
    """Generate a strong temporary password that always passes platform validation."""

    uppercase_pool = string.ascii_uppercase
    digit_pool = string.digits
    special_pool = "!@#$%^&*()_+-=[]{}|;':\",.<>?/"
    lowercase_pool = string.ascii_lowercase

    while True:
        candidate = "".join(secrets.choice(uppercase_pool) for _ in range(3))
        candidate += "".join(secrets.choice(digit_pool) for _ in range(3))
        candidate += "".join(secrets.choice(special_pool) for _ in range(2))
        candidate += "".join(secrets.choice(lowercase_pool) for _ in range(4))

        validation = validate_password_strength(candidate)
        if validation.get("valid", False):
            return candidate


def generate_csv_template_response(template_type: str) -> Response:
    """Build and return downloadable CSV templates for admin import workflows."""

    normalized_type = (template_type or "").strip().lower()
    if normalized_type != "team_invite":
        raise ValueError("Unsupported CSV template type.")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["first_name", "last_name", "email", "role", "job_title", "phone"])
    writer.writerow([
        "John",
        "Doe",
        "john.doe@company.com",
        "manager",
        "Operations Manager",
        "+919876543210",
    ])
    writer.writerow([
        "Jane",
        "Smith",
        "jane.smith@company.com",
        "viewer",
        "Logistics Analyst",
        "+918765432100",
    ])

    response = Response(output.getvalue(), mimetype="text/csv")
    response.headers["Content-Type"] = "text/csv"
    response.headers["Content-Disposition"] = (
        "attachment; filename=chainwatchpro_team_import_template.csv"
    )
    return response


def is_feature_enabled(feature_flag_name: str, organisation) -> bool:
    """Evaluate whether a feature flag is enabled for an organisation."""

    from app.models.feature_flag import FeatureFlag

    name = (feature_flag_name or "").strip().lower()
    if not name:
        return False

    flag = FeatureFlag.query.filter_by(flag_name=name).first()
    if flag is None:
        return True

    org_id_text = str(getattr(organisation, "id", ""))
    enabled_for_org_ids = flag.enabled_for_org_ids if isinstance(flag.enabled_for_org_ids, list) else []
    if org_id_text and org_id_text in {str(item) for item in enabled_for_org_ids}:
        return True

    if not bool(flag.is_enabled_globally):
        return False

    enabled_for_plans = flag.enabled_for_plans if isinstance(flag.enabled_for_plans, list) else []
    if not enabled_for_plans:
        return True

    plan_name = (getattr(organisation, "subscription_plan", "") or "").strip().lower()
    return plan_name in {str(item).strip().lower() for item in enabled_for_plans}
