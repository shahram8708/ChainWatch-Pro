"""Audit log model for security and product event tracking."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.extensions import db
from app.models.types import GUID, JSONType


class AuditLog(db.Model):
    """Immutable audit entries for user and system actions."""

    __tablename__ = "audit_log"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    organisation_id = db.Column(
        GUID(),
        db.ForeignKey("organisations.id"),
        nullable=False,
        index=True,
    )
    actor_user_id = db.Column(GUID(), db.ForeignKey("users.id"), nullable=True)
    actor_label = db.Column(db.String(50), nullable=False)
    event_type = db.Column(db.String(100), nullable=False, index=True)
    shipment_id = db.Column(GUID(), db.ForeignKey("shipments.id"), nullable=True, index=True)
    alert_id = db.Column(GUID(), db.ForeignKey("alerts.id"), nullable=True)
    recommendation_id = db.Column(
        GUID(),
        db.ForeignKey("route_recommendations.id"),
        nullable=True,
    )
    description = db.Column(db.Text, nullable=False)
    metadata_json = db.Column("metadata", JSONType(), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    organisation = db.relationship("Organisation", back_populates="audit_logs")
    actor_user = db.relationship("User", back_populates="audit_logs")
    shipment = db.relationship("Shipment", back_populates="audit_logs")
    alert = db.relationship("Alert", back_populates="audit_logs")
    recommendation = db.relationship("RouteRecommendation", back_populates="audit_logs")

    @classmethod
    def for_organisation(cls, organisation_id):
        """Tenant-safe query helper scoped by organisation ID."""

        return cls.query.filter_by(organisation_id=organisation_id)

    @classmethod
    def log(
        cls,
        db_instance,
        event_type,
        description,
        organisation_id,
        actor_user=None,
        **kwargs,
    ):
        """Create and commit a new audit log entry."""

        entry = cls(
            organisation_id=organisation_id,
            actor_user_id=getattr(actor_user, "id", None),
            actor_label=getattr(actor_user, "email", "System") if actor_user else "System",
            event_type=event_type,
            shipment_id=kwargs.get("shipment_id"),
            alert_id=kwargs.get("alert_id"),
            recommendation_id=kwargs.get("recommendation_id"),
            description=description,
            metadata_json=kwargs.get("metadata"),
            ip_address=kwargs.get("ip_address"),
        )
        db_instance.session.add(entry)
        db_instance.session.commit()
        return entry

    def to_dict(self) -> dict:
        """Serialize audit log fields."""

        return {
            "id": str(self.id),
            "organisation_id": str(self.organisation_id),
            "actor_user_id": str(self.actor_user_id) if self.actor_user_id else None,
            "actor_label": self.actor_label,
            "event_type": self.event_type,
            "shipment_id": str(self.shipment_id) if self.shipment_id else None,
            "alert_id": str(self.alert_id) if self.alert_id else None,
            "recommendation_id": str(self.recommendation_id) if self.recommendation_id else None,
            "description": self.description,
            "metadata": self.metadata_json,
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<AuditLog id={self.id} event_type={self.event_type!r} actor={self.actor_label!r}>"
