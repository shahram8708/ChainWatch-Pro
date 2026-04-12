"""Settings, billing, team, and report generation forms."""

from __future__ import annotations

import re

from flask import current_app, has_app_context
from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    HiddenField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
)
from wtforms.fields import DateField, EmailField
from wtforms.validators import (
    DataRequired,
    Email,
    EqualTo,
    Length,
    NumberRange,
    Optional,
    URL,
    ValidationError,
)

from app.services.report_service import REPORT_TYPES
from app.utils.validators import validate_password_strength


PHONE_REGEX = re.compile(r"^\+?[1-9]\d{7,14}$")


TIMEZONE_CHOICES = [
    ("UTC", "UTC"),
    ("Asia/Kolkata", "Asia/Kolkata"),
    ("America/New_York", "America/New_York"),
    ("America/Los_Angeles", "America/Los_Angeles"),
    ("Europe/London", "Europe/London"),
    ("Europe/Berlin", "Europe/Berlin"),
    ("Asia/Singapore", "Asia/Singapore"),
    ("Asia/Tokyo", "Asia/Tokyo"),
    ("Asia/Dubai", "Asia/Dubai"),
    ("Australia/Sydney", "Australia/Sydney"),
    ("America/Sao_Paulo", "America/Sao_Paulo"),
]


class UserProfileForm(FlaskForm):
    """User profile form for settings profile page."""

    first_name = StringField("First Name", validators=[DataRequired(), Length(min=2, max=100)])
    last_name = StringField("Last Name", validators=[DataRequired(), Length(min=2, max=100)])
    email = EmailField("Email", validators=[DataRequired(), Email()])
    phone = StringField("Phone", validators=[Optional(), Length(max=30)])
    timezone = SelectField("Timezone", choices=TIMEZONE_CHOICES, validators=[DataRequired()])
    profile_photo = FileField(
        "Profile Photo",
        validators=[
            Optional(),
            FileAllowed(["jpg", "jpeg", "png", "webp"], "Use JPG, PNG, or WEBP image files only."),
        ],
    )
    remove_profile_photo = BooleanField("Remove current photo", default=False)
    alert_email_enabled = BooleanField("Email Alerts", default=True)
    alert_sms_enabled = BooleanField("SMS Alerts", default=False)
    form_type = HiddenField(default="profile")
    submit = SubmitField("Save Profile")

    def validate_phone(self, field):
        value = (field.data or "").strip()
        if not value:
            return
        if not PHONE_REGEX.fullmatch(value):
            raise ValidationError("Enter a valid international phone number.")

    def validate_profile_photo(self, field):
        file_storage = field.data
        if not file_storage or not getattr(file_storage, "filename", ""):
            return

        stream = getattr(file_storage, "stream", None)
        if stream is None:
            raise ValidationError("Unable to process uploaded image.")

        try:
            current_position = stream.tell()
            stream.seek(0, 2)
            file_size = stream.tell()
            stream.seek(current_position)
        except OSError as exc:
            raise ValidationError("Unable to read uploaded image.") from exc

        max_size_bytes = 2 * 1024 * 1024
        if has_app_context():
            max_size_bytes = int(current_app.config.get("PROFILE_PHOTO_MAX_BYTES", max_size_bytes))

        if file_size > max_size_bytes:
            max_size_mb = max_size_bytes / (1024 * 1024)
            raise ValidationError(f"Profile photo must be {max_size_mb:.0f}MB or smaller.")


class ChangePasswordForm(FlaskForm):
    """Password change form for authenticated users."""

    current_password = PasswordField("Current Password", validators=[DataRequired()])
    new_password = PasswordField("New Password", validators=[DataRequired(), Length(min=8)])
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[DataRequired(), EqualTo("new_password", message="Passwords must match.")],
    )
    form_type = HiddenField(default="password")
    submit = SubmitField("Change Password")

    def validate_new_password(self, field):
        result = validate_password_strength(field.data)
        if not result["valid"]:
            raise ValidationError(" ".join(result["errors"]))


class TeamInviteForm(FlaskForm):
    """Team invite form with role constraints."""

    email = EmailField("Team Member Email", validators=[DataRequired(), Email()])
    role = SelectField(
        "Role",
        validators=[DataRequired()],
        choices=[
            ("manager", "Manager - can view and take actions"),
            ("viewer", "Viewer - read-only access"),
        ],
    )
    submit = SubmitField("Send Invitation")


class BulkTeamImportForm(FlaskForm):
    """Bulk team invite CSV upload form."""

    csv_file = FileField(
        "Team CSV File",
        validators=[
            FileRequired(),
            FileAllowed(
                ["csv"],
                "Only CSV files are accepted. Please download the template and save as .csv",
            ),
        ],
    )
    confirm_seat_limit = BooleanField(
        "I understand that if my CSV contains more users than my plan allows, only users up to my plan's seat limit will be imported.",
        default=False,
    )
    submit = SubmitField("Import Team Members from CSV")


class CarrierConnectForm(FlaskForm):
    """Carrier integration credential form."""

    carrier_id = HiddenField("Carrier ID", validators=[DataRequired()])
    api_key = StringField("API Key / Username", validators=[DataRequired(), Length(max=255)])
    api_secret = PasswordField("API Secret / Password", validators=[Optional(), Length(max=255)])
    api_endpoint = StringField(
        "Custom API Endpoint URL",
        validators=[Optional(), URL(require_tld=True, message="Enter a valid URL.")],
    )
    submit = SubmitField("Connect Carrier")

    def validate_api_endpoint(self, field):
        value = (field.data or "").strip()
        if not value:
            return
        if not value.lower().startswith("https://"):
            raise ValidationError("API endpoint must start with https://")


class CarrierCSVImportForm(FlaskForm):
    """CSV import form for manual carrier linking."""

    csv_file = FileField(
        "CSV File",
        validators=[FileRequired(), FileAllowed(["csv"], "Only CSV files accepted.")],
    )
    carrier_name = StringField(
        "Carrier Name (as it appears in your CSV)",
        validators=[DataRequired(), Length(min=2, max=255)],
    )
    submit = SubmitField("Import & Link Carrier")


class AlertRuleForm(FlaskForm):
    """Custom alert rule form."""

    rule_type = SelectField(
        "Rule Type",
        validators=[DataRequired()],
        choices=[
            ("shipment", "Specific Shipment"),
            ("carrier", "All Shipments for a Carrier"),
            ("lane", "All Shipments on a Lane"),
            ("customer", "All Shipments for a Customer"),
        ],
    )
    target_identifier = StringField(
        "Target Value (Shipment ID / Carrier Name / Lane Code / Customer Name)",
        validators=[DataRequired(), Length(max=255)],
    )
    condition = SelectField(
        "Condition",
        validators=[DataRequired()],
        choices=[
            ("drs_above", "DRS rises above threshold"),
            ("drs_below", "DRS falls below threshold"),
            ("status_change", "Shipment status changes"),
            ("carrier_delay", "Carrier delay detected"),
            ("sla_breach_imminent", "SLA breach within 48 hours"),
        ],
    )
    threshold_value = IntegerField(
        "Threshold Value (DRS score)",
        validators=[Optional(), NumberRange(min=0, max=100)],
    )
    notify_email = BooleanField("Notify via Email", default=True)
    notify_sms = BooleanField("Notify via SMS", default=False)
    notify_webhook = BooleanField("Notify via Webhook", default=False)
    form_type = HiddenField(default="rule")
    submit = SubmitField("Add Rule")


class GlobalAlertSettingsForm(FlaskForm):
    """Organisation-level global alert settings form."""

    drs_warning_threshold = IntegerField(
        "Warning Threshold",
        default=60,
        validators=[DataRequired(), NumberRange(min=30, max=79)],
    )
    drs_critical_threshold = IntegerField(
        "Critical Threshold",
        default=80,
        validators=[DataRequired(), NumberRange(min=60, max=100)],
    )
    alert_frequency = SelectField(
        "Alert Frequency",
        validators=[DataRequired()],
        choices=[
            ("immediate", "Immediate"),
            ("hourly", "Hourly Digest"),
            ("daily", "Daily Digest"),
        ],
    )
    webhook_url = StringField("Webhook URL", validators=[Optional(), URL(require_tld=True, message="Enter a valid URL.")])
    webhook_enabled = BooleanField("Enable Webhook", default=False)
    form_type = HiddenField(default="global")
    submit = SubmitField("Save Settings")

    def validate_webhook_url(self, field):
        value = (field.data or "").strip()
        if self.webhook_enabled.data and not value:
            raise ValidationError("Webhook URL is required when webhook is enabled.")
        if value and not value.lower().startswith("https://"):
            raise ValidationError("Webhook URL must start with https://")


class ReportGenerationForm(FlaskForm):
    """Report generation input form used by report exports UI."""

    report_type = SelectField("Report Type", validators=[DataRequired()], choices=[])
    start_date = DateField("Start Date", validators=[DataRequired()], format="%Y-%m-%d")
    end_date = DateField("End Date", validators=[DataRequired()], format="%Y-%m-%d")
    output_format = SelectField(
        "Output Format",
        validators=[DataRequired()],
        choices=[
            ("pdf", "PDF Report"),
            ("excel", "Excel Spreadsheet (.xlsx)"),
        ],
    )
    submit = SubmitField("Generate Report")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.report_type.choices = [
            (key, value.get("name", key.replace("_", " ").title())) for key, value in REPORT_TYPES.items()
        ]

    def validate_end_date(self, field):
        if self.start_date.data and field.data:
            if field.data < self.start_date.data:
                raise ValidationError("End date must be on or after start date.")
            if (field.data - self.start_date.data).days > 365:
                raise ValidationError("Date range cannot exceed 365 days.")
