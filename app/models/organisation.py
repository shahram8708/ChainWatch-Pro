"""Organisation model for tenant-level account data."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.mutable import MutableDict

from app.extensions import db
from app.models.types import GUID, JSONType


class Organisation(db.Model):
    """Represents a customer organisation in ChainWatch Pro."""

    __tablename__ = "organisations"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    name = db.Column(db.String(255), nullable=False)
    industry = db.Column(db.String(100), nullable=True)
    company_size_range = db.Column(db.String(50), nullable=True)
    monthly_shipment_volume = db.Column(db.Integer, nullable=True)
    subscription_plan = db.Column(
        db.Enum("starter", "professional", "enterprise", name="subscription_plan_enum"),
        nullable=False,
        default="starter",
    )
    subscription_status = db.Column(
        db.Enum("active", "trial", "expired", "cancelled", name="subscription_status_enum"),
        nullable=False,
        default="trial",
    )
    trial_ends_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    default_currency = db.Column(db.String(3), nullable=False, default="INR")
    sla_breach_threshold_hours = db.Column(db.Integer, nullable=False, default=24)
    onboarding_complete = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    org_profile_data = db.Column(MutableDict.as_mutable(JSONType()), nullable=False, default=dict)
    razorpay_subscription_id = db.Column(db.String(255), nullable=True)
    razorpay_customer_id = db.Column(db.String(255), nullable=True)

    users = db.relationship("User", back_populates="organisation", lazy="dynamic")
    shipments = db.relationship("Shipment", back_populates="organisation", lazy="dynamic")
    alerts = db.relationship("Alert", back_populates="organisation", lazy="dynamic")
    ai_generated_contents = db.relationship(
        "AIGeneratedContent",
        back_populates="organisation",
        lazy="dynamic",
    )
    audit_logs = db.relationship("AuditLog", back_populates="organisation", lazy="dynamic")
    carrier_performances = db.relationship(
        "CarrierPerformance",
        back_populates="organisation",
        lazy="dynamic",
    )

    @property
    def is_trial_active(self) -> bool:
        """Return True when the organisation trial period is still active."""

        return (
            self.subscription_status == "trial"
            and self.trial_ends_at is not None
            and self.trial_ends_at > datetime.utcnow()
        )

    @property
    def shipment_limit(self) -> int | None:
        """Return plan-based shipment limits where enterprise is unlimited."""

        limits = {
            "starter": 50,
            "professional": 500,
            "enterprise": None,
        }
        return limits.get(self.subscription_plan, 50)

    def to_dict(self) -> dict:
        """Serialize organisation fields for API and template use."""

        return {
            "id": str(self.id),
            "name": self.name,
            "industry": self.industry,
            "company_size_range": self.company_size_range,
            "monthly_shipment_volume": self.monthly_shipment_volume,
            "subscription_plan": self.subscription_plan,
            "subscription_status": self.subscription_status,
            "trial_ends_at": self.trial_ends_at.isoformat() if self.trial_ends_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "default_currency": self.default_currency,
            "sla_breach_threshold_hours": self.sla_breach_threshold_hours,
            "onboarding_complete": self.onboarding_complete,
            "is_active": self.is_active,
            "org_profile_data": self.org_profile_data or {},
            "razorpay_subscription_id": self.razorpay_subscription_id,
            "razorpay_customer_id": self.razorpay_customer_id,
            "is_trial_active": self.is_trial_active,
            "shipment_limit": self.shipment_limit,
        }

    def __repr__(self) -> str:
        return f"<Organisation id={self.id} name={self.name!r} plan={self.subscription_plan}>"
