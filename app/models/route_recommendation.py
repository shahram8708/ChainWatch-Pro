"""Route recommendation model for dynamic logistics options."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.extensions import db
from app.models.types import GUID


class RouteRecommendation(db.Model):
    """Alternative route strategy recommendation generated for a shipment."""

    __tablename__ = "route_recommendations"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    shipment_id = db.Column(
        GUID(),
        db.ForeignKey("shipments.id"),
        nullable=False,
        index=True,
    )
    option_label = db.Column(db.String(1), nullable=False)
    strategy = db.Column(
        db.Enum("fastest", "cost_optimized", "hybrid", name="route_strategy_enum"),
        nullable=False,
    )
    alt_carrier_id = db.Column(GUID(), db.ForeignKey("carriers.id"), nullable=True)
    alt_route_description = db.Column(db.Text, nullable=False)
    revised_eta = db.Column(db.DateTime, nullable=False)
    transit_time_delta_hours = db.Column(db.Numeric(6, 1), nullable=False)
    cost_delta_inr = db.Column(db.Numeric(10, 2), nullable=False)
    on_time_confidence = db.Column(db.Numeric(5, 2), nullable=False)
    execution_deadline = db.Column(db.DateTime, nullable=False)
    status = db.Column(
        db.Enum("pending", "approved", "dismissed", "expired", name="recommendation_status_enum"),
        nullable=False,
        default="pending",
    )
    decision_notes = db.Column(db.Text, nullable=True)
    decided_by = db.Column(GUID(), db.ForeignKey("users.id"), nullable=True)
    decided_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shipment = db.relationship("Shipment", back_populates="route_recommendations")
    alt_carrier = db.relationship(
        "Carrier",
        foreign_keys=[alt_carrier_id],
        back_populates="route_recommendations",
        overlaps="alt_recommendations",
    )
    deciding_user = db.relationship(
        "User",
        foreign_keys=[decided_by],
        back_populates="decided_recommendations",
    )
    audit_logs = db.relationship("AuditLog", back_populates="recommendation", lazy="dynamic")

    @classmethod
    def for_organisation(cls, organisation_id):
        """Tenant-safe query helper via shipment organisation join."""

        from app.models.shipment import Shipment

        return cls.query.join(Shipment).filter(Shipment.organisation_id == organisation_id)

    @property
    def execution_window_minutes(self) -> int:
        """Return minutes remaining before the option expires."""

        delta = self.execution_deadline - datetime.utcnow()
        minutes = int(delta.total_seconds() // 60)
        return max(minutes, 0)

    def to_dict(self) -> dict:
        """Serialize recommendation fields."""

        return {
            "id": str(self.id),
            "shipment_id": str(self.shipment_id),
            "option_label": self.option_label,
            "strategy": self.strategy,
            "alt_carrier_id": str(self.alt_carrier_id) if self.alt_carrier_id else None,
            "alt_route_description": self.alt_route_description,
            "revised_eta": self.revised_eta.isoformat() if self.revised_eta else None,
            "transit_time_delta_hours": float(self.transit_time_delta_hours),
            "cost_delta_inr": float(self.cost_delta_inr),
            "on_time_confidence": float(self.on_time_confidence),
            "execution_deadline": self.execution_deadline.isoformat() if self.execution_deadline else None,
            "status": self.status,
            "decision_notes": self.decision_notes,
            "decided_by": str(self.decided_by) if self.decided_by else None,
            "decided_at": self.decided_at.isoformat() if self.decided_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "execution_window_minutes": self.execution_window_minutes,
        }

    def __repr__(self) -> str:
        return (
            f"<RouteRecommendation id={self.id} shipment_id={self.shipment_id} "
            f"option={self.option_label} status={self.status}>"
        )
