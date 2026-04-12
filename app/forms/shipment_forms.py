"""Shipment management forms for dashboard and shipment workflows."""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    DecimalField,
    HiddenField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.fields import DateTimeLocalField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError


MODE_CHOICES = [
    ("ocean_fcl", "Ocean FCL"),
    ("ocean_lcl", "Ocean LCL"),
    ("air", "Air Freight"),
    ("road", "Road/Truck"),
    ("rail", "Rail"),
    ("multimodal", "Multimodal"),
]

STATUS_CHOICES = [
    ("pending", "Pending"),
    ("in_transit", "In Transit"),
    ("delayed", "Delayed"),
    ("at_customs", "At Customs"),
    ("delivered", "Delivered"),
    ("cancelled", "Cancelled"),
]


class ShipmentCreateForm(FlaskForm):
    """Create a new shipment record."""

    external_reference = StringField(
        "Booking / PO Reference",
        validators=[DataRequired(), Length(min=3, max=100)],
    )
    carrier_id = SelectField("Carrier", coerce=str, choices=[])
    mode = SelectField("Mode", validators=[DataRequired()], choices=MODE_CHOICES)
    origin_port_code = StringField(
        "Origin Port / Airport Code (IATA or LOCODE)",
        validators=[DataRequired(), Length(min=3, max=5)],
    )
    destination_port_code = StringField(
        "Destination Port / Airport Code (IATA or LOCODE)",
        validators=[DataRequired(), Length(min=3, max=5)],
    )
    origin_address = TextAreaField("Origin Address", validators=[Optional(), Length(max=500)])
    destination_address = TextAreaField("Destination Address", validators=[Optional(), Length(max=500)])
    estimated_departure = DateTimeLocalField(
        "Estimated Departure",
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired()],
    )
    estimated_arrival = DateTimeLocalField(
        "Estimated Arrival",
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired()],
    )
    cargo_value_inr = DecimalField(
        "Cargo Value (₹ INR)",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    customer_name = StringField("Customer Name", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Create Shipment")

    def validate_estimated_arrival(self, field):
        if self.estimated_departure.data and field.data:
            if field.data <= self.estimated_departure.data:
                raise ValidationError("Estimated arrival must be after estimated departure.")

    def validate_origin_port_code(self, field):
        field.data = (field.data or "").strip().upper()

    def validate_destination_port_code(self, field):
        field.data = (field.data or "").strip().upper()


class ShipmentEditForm(FlaskForm):
    """Update an existing shipment record."""

    carrier_id = SelectField("Carrier", coerce=str, choices=[])
    mode = SelectField("Mode", validators=[DataRequired()], choices=MODE_CHOICES)
    status = SelectField("Status", validators=[DataRequired()], choices=STATUS_CHOICES)
    origin_port_code = StringField(
        "Origin Port / Airport Code (IATA or LOCODE)",
        validators=[DataRequired(), Length(min=3, max=5)],
    )
    destination_port_code = StringField(
        "Destination Port / Airport Code (IATA or LOCODE)",
        validators=[DataRequired(), Length(min=3, max=5)],
    )
    origin_address = TextAreaField("Origin Address", validators=[Optional(), Length(max=500)])
    destination_address = TextAreaField("Destination Address", validators=[Optional(), Length(max=500)])
    estimated_departure = DateTimeLocalField(
        "Estimated Departure",
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired()],
    )
    estimated_arrival = DateTimeLocalField(
        "Estimated Arrival",
        format="%Y-%m-%dT%H:%M",
        validators=[DataRequired()],
    )
    actual_departure = DateTimeLocalField(
        "Actual Departure",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )
    actual_arrival = DateTimeLocalField(
        "Actual Arrival",
        format="%Y-%m-%dT%H:%M",
        validators=[Optional()],
    )
    current_latitude = DecimalField(
        "Current Latitude",
        validators=[Optional(), NumberRange(min=-90, max=90)],
        places=6,
    )
    current_longitude = DecimalField(
        "Current Longitude",
        validators=[Optional(), NumberRange(min=-180, max=180)],
        places=6,
    )
    current_location_name = StringField("Current Location", validators=[Optional(), Length(max=255)])
    cargo_value_inr = DecimalField(
        "Cargo Value (₹ INR)",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    customer_name = StringField("Customer Name", validators=[Optional(), Length(max=255)])
    submit = SubmitField("Update Shipment")

    def validate_estimated_arrival(self, field):
        if self.estimated_departure.data and field.data:
            if field.data <= self.estimated_departure.data:
                raise ValidationError("Estimated arrival must be after estimated departure.")

    def validate_actual_arrival(self, field):
        if self.actual_departure.data and field.data:
            if field.data < self.actual_departure.data:
                raise ValidationError("Actual arrival cannot be before actual departure.")

    def validate_origin_port_code(self, field):
        field.data = (field.data or "").strip().upper()

    def validate_destination_port_code(self, field):
        field.data = (field.data or "").strip().upper()


class ShipmentImportForm(FlaskForm):
    """Bulk import shipments from CSV."""

    csv_file = FileField(
        "Select CSV File",
        validators=[FileRequired(), FileAllowed(["csv"], "Only CSV files accepted.")],
    )
    update_existing = BooleanField(
        "Update existing shipments if External Reference already exists",
        default=False,
    )
    submit = SubmitField("Import Shipments")


class ShipmentFilterForm(FlaskForm):
    """Filter and sorting controls for shipment lists."""

    class Meta:
        csrf = False

    q = StringField("Search", validators=[Optional(), Length(max=255)])
    status = SelectField(
        "Status",
        choices=[("", "All Statuses"), *STATUS_CHOICES],
        validators=[Optional()],
    )
    carrier_id = SelectField(
        "Carrier",
        coerce=str,
        choices=[("", "All Carriers")],
        validators=[Optional()],
    )
    mode = SelectField(
        "Mode",
        choices=[("", "All Modes"), *MODE_CHOICES],
        validators=[Optional()],
    )
    risk = SelectField(
        "Risk Level",
        choices=[
            ("", "All Risk Levels"),
            ("critical", "Critical (81-100)"),
            ("warning", "Warning (61-80)"),
            ("watch", "Watch (31-60)"),
            ("green", "Green (0-30)"),
        ],
        validators=[Optional()],
    )
    sort = HiddenField("sort")
    order = HiddenField("order")


class RouteDecisionForm(FlaskForm):
    """Approve or dismiss a route recommendation."""

    decision_notes = TextAreaField(
        "Decision Notes (optional)",
        validators=[Optional(), Length(max=1000)],
    )
    recommendation_id = HiddenField("recommendation_id", validators=[DataRequired()])
    submit_approve = SubmitField("Approve Reroute")
    submit_dismiss = SubmitField("Dismiss")
