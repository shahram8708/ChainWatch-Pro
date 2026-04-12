"""Forms for Route Optimizer page workflows."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import SelectField, SubmitField

from app.forms.shipment_forms import RouteDecisionForm


class OptimizerShipmentSelectorForm(FlaskForm):
    """GET form used to select at-risk shipment for optimizer analysis."""

    class Meta:
        csrf = False

    shipment_id = SelectField("At-Risk Shipment", coerce=str, choices=[])
    submit = SubmitField("Analyze")

    def set_shipment_choices(self, shipments) -> None:
        choices = [("", "Select an at-risk shipment")]
        for shipment in shipments:
            drs_score = float(shipment.disruption_risk_score or 0)
            label = (
                f"{shipment.external_reference or str(shipment.id)[:8]} — "
                f"DRS {drs_score:.1f} — "
                f"{shipment.origin_port_code} -> {shipment.destination_port_code}"
            )
            choices.append((str(shipment.id), label))
        self.shipment_id.choices = choices


__all__ = ["OptimizerShipmentSelectorForm", "RouteDecisionForm"]
