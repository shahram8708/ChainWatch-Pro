"""Alert center forms for filtering and feed controls."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField


class AlertFilterForm(FlaskForm):
    """Filter form for Alert Center feed."""

    class Meta:
        csrf = False

    severity = SelectField(
        "Severity",
        choices=[
            ("all", "All Severities"),
            ("critical", "Critical"),
            ("warning", "Warning"),
            ("watch", "Watch"),
            ("info", "Info"),
        ],
        default="all",
    )
    acknowledged = SelectField(
        "Acknowledged",
        choices=[
            ("unacknowledged", "Unacknowledged"),
            ("acknowledged", "Acknowledged"),
            ("all", "All"),
        ],
        default="unacknowledged",
    )
    alert_type = SelectField("Alert Type", choices=[("all", "All Types")], default="all")

    def set_alert_type_choices(self, alert_types: list[str]) -> None:
        dynamic_choices = [("all", "All Types")]
        dynamic_choices.extend((value, value.replace("_", " ").title()) for value in alert_types)
        self.alert_type.choices = dynamic_choices
