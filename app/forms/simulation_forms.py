"""Scenario planner simulation forms."""

from __future__ import annotations

from datetime import date

from flask_wtf import FlaskForm
from wtforms import DateField, DecimalField, IntegerField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError


MODE_CHOICES = [
    ("ocean_fcl", "Ocean FCL"),
    ("ocean_lcl", "Ocean LCL"),
    ("air", "Air Freight"),
    ("road", "Road/Truck"),
    ("rail", "Rail"),
    ("multimodal", "Multimodal"),
]


class ScenarioPlannerForm(FlaskForm):
    """Collect user-defined scenario parameters for DRS simulation."""

    origin_port_code = StringField(
        "Origin Port / Airport Code",
        validators=[DataRequired(), Length(min=3, max=5)],
    )
    destination_port_code = StringField(
        "Destination Port / Airport Code",
        validators=[DataRequired(), Length(min=3, max=5)],
    )
    mode = SelectField("Mode", validators=[DataRequired()], choices=MODE_CHOICES)
    carrier_id = SelectField("Primary Carrier", coerce=str, validators=[DataRequired()], choices=[])
    estimated_ship_date = DateField(
        "Estimated Ship Date",
        validators=[DataRequired()],
        format="%Y-%m-%d",
    )
    cargo_value_inr = DecimalField(
        "Cargo Value (INR)",
        validators=[Optional(), NumberRange(min=0)],
        places=2,
    )
    sla_requirement_days = IntegerField(
        "SLA Requirement (days)",
        validators=[DataRequired(), NumberRange(min=1, max=180)],
    )
    submit = SubmitField("Run Simulation")

    def validate_origin_port_code(self, field):
        field.data = (field.data or "").strip().upper()

    def validate_destination_port_code(self, field):
        field.data = (field.data or "").strip().upper()

    def validate_estimated_ship_date(self, field):
        if field.data and field.data < date.today():
            raise ValidationError("Estimated ship date cannot be in the past.")
