"""Alert model for disruption and logistics notifications."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.extensions import db
from app.models.types import GUID


class Alert(db.Model):
    """Represents a risk event or action-triggering alert."""

    __tablename__ = "alerts"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    organisation_id = db.Column(
        GUID(),
        db.ForeignKey("organisations.id"),
        nullable=False,
        index=True,
    )
    shipment_id = db.Column(
        GUID(),
        db.ForeignKey("shipments.id"),
        nullable=True,
        index=True,
    )
    alert_type = db.Column(db.String(50), nullable=False)
    severity = db.Column(
        db.Enum("critical", "warning", "watch", "info", name="alert_severity_enum"),
        nullable=False,
    )
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    drs_at_alert = db.Column(db.Numeric(5, 2), nullable=True)
    is_acknowledged = db.Column(db.Boolean, nullable=False, default=False)
    acknowledged_by = db.Column(GUID(), db.ForeignKey("users.id"), nullable=True)
    acknowledged_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    organisation = db.relationship("Organisation", back_populates="alerts")
    shipment = db.relationship("Shipment", back_populates="alerts")
    acknowledging_user = db.relationship(
        "User",
        foreign_keys=[acknowledged_by],
        back_populates="acknowledged_alerts",
    )
    audit_logs = db.relationship("AuditLog", back_populates="alert", lazy="dynamic")

    @classmethod
    def for_organisation(cls, organisation_id):
        """Tenant-safe query helper scoped by organisation ID."""

        return cls.query.filter_by(organisation_id=organisation_id)

    def to_dict(self) -> dict:
        """Serialize alert fields."""

        return {
            "id": str(self.id),
            "organisation_id": str(self.organisation_id),
            "shipment_id": str(self.shipment_id) if self.shipment_id else None,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "drs_at_alert": float(self.drs_at_alert) if self.drs_at_alert is not None else None,
            "is_acknowledged": self.is_acknowledged,
            "acknowledged_by": str(self.acknowledged_by) if self.acknowledged_by else None,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Alert id={self.id} severity={self.severity} title={self.title!r}>"
