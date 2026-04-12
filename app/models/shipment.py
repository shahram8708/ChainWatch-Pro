"""Shipment model representing tracked freight movement."""

from __future__ import annotations

import uuid
from datetime import datetime
from math import ceil

from app.extensions import db
from app.models.types import GUID


class Shipment(db.Model):
    """Shipment record for organisation-level logistics tracking."""

    __tablename__ = "shipments"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    organisation_id = db.Column(
        GUID(),
        db.ForeignKey("organisations.id"),
        nullable=False,
        index=True,
    )
    external_reference = db.Column(db.String(100), nullable=True, index=True)
    carrier_id = db.Column(GUID(), db.ForeignKey("carriers.id"), nullable=True)
    mode = db.Column(
        db.Enum(
            "ocean_fcl",
            "ocean_lcl",
            "air",
            "road",
            "rail",
            "multimodal",
            name="shipment_mode_enum",
        ),
        nullable=False,
    )
    origin_port_code = db.Column(db.String(5), nullable=False)
    destination_port_code = db.Column(db.String(5), nullable=False)
    origin_address = db.Column(db.Text, nullable=True)
    destination_address = db.Column(db.Text, nullable=True)
    estimated_departure = db.Column(db.DateTime, nullable=False)
    estimated_arrival = db.Column(db.DateTime, nullable=False)
    actual_departure = db.Column(db.DateTime, nullable=True)
    actual_arrival = db.Column(db.DateTime, nullable=True)
    current_latitude = db.Column(db.Numeric(9, 6), nullable=True)
    current_longitude = db.Column(db.Numeric(9, 6), nullable=True)
    current_location_name = db.Column(db.String(255), nullable=True)
    status = db.Column(
        db.Enum(
            "pending",
            "in_transit",
            "delayed",
            "at_customs",
            "delivered",
            "cancelled",
            name="shipment_status_enum",
        ),
        nullable=False,
        default="pending",
    )
    disruption_risk_score = db.Column(db.Numeric(5, 2), nullable=False, default=0.00)
    sla_breach_probability = db.Column(db.Numeric(5, 2), nullable=False, default=0.00)
    cargo_value_inr = db.Column(db.Numeric(14, 2), nullable=True)
    customer_name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    is_archived = db.Column(db.Boolean, nullable=False, default=False)

    carrier = db.relationship("Carrier", back_populates="shipments")
    organisation = db.relationship("Organisation", back_populates="shipments")
    disruption_scores = db.relationship(
        "DisruptionScore",
        back_populates="shipment",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    alerts = db.relationship("Alert", back_populates="shipment", lazy="dynamic")
    route_recommendations = db.relationship(
        "RouteRecommendation",
        back_populates="shipment",
        lazy="dynamic",
        cascade="all, delete-orphan",
    )
    audit_logs = db.relationship("AuditLog", back_populates="shipment", lazy="dynamic")

    @classmethod
    def for_organisation(cls, organisation_id):
        """Tenant-safe query helper scoped by organisation ID."""

        return cls.query.filter_by(organisation_id=organisation_id)

    @property
    def risk_level(self) -> str:
        """Return normalized risk level based on DRS."""

        score = float(self.disruption_risk_score or 0)
        if score >= 81:
            return "critical"
        if score >= 61:
            return "warning"
        if score >= 31:
            return "watch"
        return "green"

    @property
    def risk_color(self) -> str:
        """Return design-system color hex for current risk level."""

        color_map = {
            "critical": "#D32F2F",
            "warning": "#FF8C00",
            "watch": "#F59E0B",
            "green": "#00A86B",
        }
        return color_map[self.risk_level]

    @property
    def days_to_delivery(self) -> int:
        """Return integer days until ETA (negative when overdue)."""

        if self.estimated_arrival is None:
            return 0

        seconds_remaining = (self.estimated_arrival - datetime.utcnow()).total_seconds()
        if seconds_remaining >= 0:
            return int(ceil(seconds_remaining / 86400))
        return int(seconds_remaining // 86400)

    @property
    def is_overdue(self) -> bool:
        """Return True if shipment has missed ETA and is not delivered."""

        return (
            self.estimated_arrival is not None
            and datetime.utcnow() > self.estimated_arrival
            and self.status != "delivered"
        )

    def to_dict(self) -> dict:
        """Serialize shipment fields for API and templates."""

        return {
            "id": str(self.id),
            "organisation_id": str(self.organisation_id),
            "external_reference": self.external_reference,
            "carrier_id": str(self.carrier_id) if self.carrier_id else None,
            "mode": self.mode,
            "origin_port_code": self.origin_port_code,
            "destination_port_code": self.destination_port_code,
            "origin_address": self.origin_address,
            "destination_address": self.destination_address,
            "estimated_departure": self.estimated_departure.isoformat() if self.estimated_departure else None,
            "estimated_arrival": self.estimated_arrival.isoformat() if self.estimated_arrival else None,
            "actual_departure": self.actual_departure.isoformat() if self.actual_departure else None,
            "actual_arrival": self.actual_arrival.isoformat() if self.actual_arrival else None,
            "current_latitude": float(self.current_latitude) if self.current_latitude is not None else None,
            "current_longitude": float(self.current_longitude) if self.current_longitude is not None else None,
            "current_location_name": self.current_location_name,
            "status": self.status,
            "disruption_risk_score": float(self.disruption_risk_score or 0),
            "sla_breach_probability": float(self.sla_breach_probability or 0),
            "cargo_value_inr": float(self.cargo_value_inr) if self.cargo_value_inr is not None else None,
            "customer_name": self.customer_name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "is_archived": self.is_archived,
            "risk_level": self.risk_level,
            "risk_color": self.risk_color,
            "days_to_delivery": self.days_to_delivery,
            "is_overdue": self.is_overdue,
        }

    def __repr__(self) -> str:
        return (
            f"<Shipment id={self.id} ref={self.external_reference!r} "
            f"status={self.status} risk={self.disruption_risk_score}>"
        )
