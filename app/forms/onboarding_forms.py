"""Onboarding forms for ChainWatch Pro setup wizard."""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField
from wtforms import (
    BooleanField,
    FieldList,
    HiddenField,
    IntegerField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    SubmitField,
)
from wtforms.fields import EmailField
from wtforms.validators import DataRequired, Email, NumberRange, Optional, URL, ValidationError
from wtforms.widgets import CheckboxInput, ListWidget


class MultipleCheckboxField(SelectMultipleField):
    """Render a SelectMultipleField as a checkbox list."""

    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


class OnboardingStep1Form(FlaskForm):
    """Collect company shipping profile used for initial baseline setup."""

    industry = SelectField(
        "Industry",
        validators=[DataRequired()],
        choices=[
            ("Logistics & 3PL", "Logistics & 3PL"),
            ("E-commerce & Retail", "E-commerce & Retail"),
            ("Manufacturing", "Manufacturing"),
            ("Automotive", "Automotive"),
            ("Pharmaceutical & Healthcare", "Pharmaceutical & Healthcare"),
            ("Food & Beverage", "Food & Beverage"),
            ("Consumer Electronics", "Consumer Electronics"),
            ("Apparel & Fashion", "Apparel & Fashion"),
            ("Energy & Chemicals", "Energy & Chemicals"),
            ("Other", "Other"),
        ],
    )
    company_size = SelectField(
        "Company Size",
        validators=[DataRequired()],
        choices=[
            ("1-10", "1-10"),
            ("11-50", "11-50"),
            ("51-200", "51-200"),
            ("201-500", "201-500"),
            ("501-1000", "501-1000"),
            ("1000+", "1000+"),
        ],
    )
    monthly_shipment_volume = SelectField(
        "Monthly Shipment Volume",
        validators=[DataRequired()],
        choices=[
            ("Under 50", "Under 50"),
            ("50-200", "50-200"),
            ("200-500", "200-500"),
            ("500-2000", "500-2,000"),
            ("2000-10000", "2,000-10,000"),
            ("Over 10000", "Over 10,000"),
        ],
    )
    shipping_modes = MultipleCheckboxField(
        "Shipping Modes",
        choices=[
            ("ocean_fcl", "Ocean FCL"),
            ("ocean_lcl", "Ocean LCL"),
            ("air", "Air Freight"),
            ("road", "Road/Truck"),
            ("rail", "Rail"),
            ("multimodal", "Multimodal"),
        ],
    )
    primary_trade_lanes = MultipleCheckboxField(
        "Primary Trade Lanes",
        choices=[
            ("east_asia_to_na", "East Asia -> North America"),
            ("east_asia_to_europe", "East Asia -> Europe"),
            ("south_asia_to_europe", "South Asia -> Europe"),
            ("south_asia_to_na", "South Asia -> North America"),
            ("middle_east_to_europe", "Middle East -> Europe"),
            ("intra_europe", "Intra-Europe"),
            ("intra_asia", "Intra-Asia"),
            ("europe_to_na", "Europe -> North America"),
            ("south_america_to_na", "South America -> North America"),
            ("africa_to_europe", "Africa -> Europe"),
            ("domestic_india", "Domestic India"),
            ("other", "Other"),
        ],
    )
    typical_cargo_types = MultipleCheckboxField(
        "Typical Cargo Types",
        choices=[
            ("general", "General Cargo"),
            ("temperature_controlled", "Temperature-Controlled"),
            ("hazmat", "Hazardous Materials"),
            ("high_value", "High-Value / Insured"),
            ("bulk", "Bulk Cargo"),
            ("project", "Project Cargo / Oversized"),
        ],
    )
    current_visibility_tools = SelectField(
        "Current Visibility Tools",
        validators=[DataRequired()],
        choices=[
            ("carrier_portals_manual", "Carrier portals only (manual)"),
            ("basic_tms", "Basic TMS"),
            ("advanced_tms", "Advanced TMS (SAP TM, Oracle TMS)"),
            ("standalone_visibility", "Standalone visibility platform"),
            ("spreadsheets_only", "Spreadsheets only"),
            ("none", "No current tracking system"),
        ],
    )
    submit = SubmitField("Save & Continue →")

    def validate_shipping_modes(self, field):
        if not field.data:
            raise ValidationError("Select at least one shipping mode.")

    def validate_primary_trade_lanes(self, field):
        if not field.data:
            raise ValidationError("Select at least one primary trade lane.")


class OnboardingStep2Form(FlaskForm):
    """Collect initial carrier setup preference and optional historical CSV upload."""

    setup_method = RadioField(
        "Setup Method",
        validators=[DataRequired()],
        choices=[
            ("csv", "Upload historical shipment CSV for baseline analysis"),
            ("manual", "Select carriers manually"),
            ("skip", "Skip for now - I'll add carriers later"),
        ],
        default="manual",
    )
    selected_carriers = MultipleCheckboxField("Select Carriers", choices=[])
    csv_file = FileField(
        "Shipment CSV",
        validators=[FileAllowed(["csv"], "Only CSV files are accepted.")],
    )
    api_credential_note = HiddenField("API Credential Placeholder")
    submit = SubmitField("Save & Continue →")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from app.models.carrier import Carrier

        carriers = Carrier.query.filter_by(is_global_carrier=True).order_by(Carrier.name.asc()).all()
        self.selected_carriers.choices = [(str(carrier.id), carrier.name) for carrier in carriers]

    def validate_selected_carriers(self, field):
        if self.setup_method.data == "manual" and not field.data:
            raise ValidationError("Select at least one carrier when using manual setup.")

    def validate_csv_file(self, field):
        if self.setup_method.data == "csv" and not field.data:
            raise ValidationError("Please upload a CSV file for baseline analysis.")


class OnboardingStep3Form(FlaskForm):
    """Collect alert channel settings, thresholds, and team invite details."""

    alert_email = BooleanField("Email notifications (always on)", default=True)
    alert_sms = BooleanField("SMS for Critical alerts only (requires phone number)", default=False)
    alert_webhook = BooleanField("Webhook / Slack / Teams integration", default=False)
    webhook_url = StringField("Webhook URL", validators=[Optional(), URL(require_tld=True, message="Enter a valid webhook URL.")])
    drs_warning_threshold = IntegerField(
        "Warning alert threshold (DRS score)",
        validators=[DataRequired(), NumberRange(min=30, max=79)],
        default=60,
    )
    drs_critical_threshold = IntegerField(
        "Critical alert threshold (DRS score)",
        validators=[DataRequired(), NumberRange(min=60, max=100)],
        default=80,
    )
    alert_frequency = SelectField(
        "Alert Frequency",
        validators=[DataRequired()],
        choices=[
            ("immediate", "Immediate - alert as soon as threshold is crossed"),
            ("hourly", "Hourly digest - batch alerts once per hour"),
            ("daily", "Daily digest - one summary email per day"),
        ],
    )
    team_invite_emails = FieldList(
        StringField("Invite Team Member", validators=[Optional(), Email(message="Enter a valid email address.")]),
        min_entries=1,
        max_entries=5,
    )
    team_role = SelectField(
        "Invited Team Role",
        validators=[DataRequired()],
        choices=[
            ("manager", "Manager - can view and take actions"),
            ("viewer", "Viewer - read-only access"),
        ],
    )
    submit = SubmitField("Save Preferences & Continue →")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        while len(self.team_invite_emails) < 5:
            self.team_invite_emails.append_entry()

    def validate_webhook_url(self, field):
        if self.alert_webhook.data:
            if not field.data:
                raise ValidationError("Webhook URL is required when webhook alerts are enabled.")
            if not field.data.startswith("https://"):
                raise ValidationError("Webhook URL must start with https://")

    def validate_drs_critical_threshold(self, field):
        warning = self.drs_warning_threshold.data or 0
        critical = field.data or 0
        if critical <= warning:
            raise ValidationError("Critical threshold must be greater than warning threshold.")


class OnboardingStep4Form(FlaskForm):
    """Collect dashboard personalization preferences before onboarding completion."""

    show_active_shipments_card = BooleanField("Total Active Shipments", default=True)
    show_critical_alerts_card = BooleanField("Critical Alerts Count", default=True)
    show_warning_alerts_card = BooleanField("Warning Alerts Count", default=True)
    show_otd_rate_card = BooleanField("Fleet On-Time Delivery Rate", default=True)
    show_financial_exposure_card = BooleanField("Financial Exposure (INR value at risk)", default=False)

    default_risk_filter = SelectField(
        "Default Risk Filter",
        validators=[DataRequired()],
        choices=[
            ("all", "All shipments"),
            ("critical_warning", "Critical & Warning only"),
            ("critical_only", "Critical only"),
        ],
    )
    default_mode_filter = SelectField(
        "Default Mode Filter",
        validators=[DataRequired()],
        choices=[
            ("all", "All modes"),
            ("ocean_fcl", "Ocean FCL only"),
            ("air", "Air freight only"),
            ("road", "Road/Truck only"),
        ],
    )
    default_sort = SelectField(
        "Default Sort Order",
        validators=[DataRequired()],
        choices=[
            ("drs_desc", "Risk Score - Highest First (recommended)"),
            ("eta_asc", "ETA - Soonest First"),
            ("created_desc", "Recently Added First"),
        ],
    )
    default_page_size = SelectField(
        "Default Page Size",
        validators=[DataRequired()],
        choices=[("25", "25 rows per page"), ("50", "50 rows per page"), ("100", "100 rows per page")],
        default="25",
    )
    timezone = SelectField(
        "Timezone",
        validators=[DataRequired()],
        choices=[
            ("Asia/Kolkata", "Asia/Kolkata (IST)"),
            ("UTC", "UTC"),
            ("America/New_York", "America/New_York (EST)"),
            ("America/Los_Angeles", "America/Los_Angeles (PST)"),
            ("Europe/London", "Europe/London (GMT)"),
            ("Europe/Berlin", "Europe/Berlin (CET)"),
            ("Asia/Tokyo", "Asia/Tokyo (JST)"),
            ("Asia/Singapore", "Asia/Singapore (SGT)"),
            ("Australia/Sydney", "Australia/Sydney (AEST)"),
        ],
        default="Asia/Kolkata",
    )
    submit = SubmitField("Complete Setup & Go to Dashboard 🚀")
