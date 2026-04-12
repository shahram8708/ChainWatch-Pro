"""Carrier performance benchmarking model."""

from __future__ import annotations

import uuid

from app.extensions import db
from app.models.types import GUID


class CarrierPerformance(db.Model):
    """Performance metrics for carrier lanes by time period."""

    __tablename__ = "carrier_performance"

    __table_args__ = (
        db.UniqueConstraint(
            "carrier_id",
            "organisation_id",
            "origin_region",
            "destination_region",
            "mode",
            "period_year",
            "period_month",
            name="uq_carrier_performance_period",
        ),
    )

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    carrier_id = db.Column(
        GUID(),
        db.ForeignKey("carriers.id"),
        nullable=False,
        index=True,
    )
    organisation_id = db.Column(
        GUID(),
        db.ForeignKey("organisations.id"),
        nullable=True,
        index=True,
    )
    origin_region = db.Column(db.String(100), nullable=False)
    destination_region = db.Column(db.String(100), nullable=False)
    mode = db.Column(
        db.Enum("ocean", "air", "road", "rail", "multimodal", name="performance_mode_enum"),
        nullable=False,
    )
    period_year = db.Column(db.SmallInteger, nullable=False)
    period_month = db.Column(db.SmallInteger, nullable=False)
    total_shipments = db.Column(db.Integer, nullable=False)
    on_time_count = db.Column(db.Integer, nullable=False)
    otd_rate = db.Column(db.Numeric(5, 2), nullable=False)
    avg_delay_hours = db.Column(db.Numeric(6, 1), nullable=False)
    reliability_score = db.Column(db.Numeric(5, 2), nullable=False)

    carrier = db.relationship("Carrier", back_populates="carrier_performances")
    organisation = db.relationship("Organisation", back_populates="carrier_performances")

    @classmethod
    def for_organisation(cls, organisation_id):
        """Tenant-aware helper including global benchmark rows."""

        return cls.query.filter(
            db.or_(
                cls.organisation_id == organisation_id,
                cls.organisation_id.is_(None),
            )
        )

    def to_dict(self) -> dict:
        """Serialize carrier performance fields."""

        return {
            "id": str(self.id),
            "carrier_id": str(self.carrier_id),
            "organisation_id": str(self.organisation_id) if self.organisation_id else None,
            "origin_region": self.origin_region,
            "destination_region": self.destination_region,
            "mode": self.mode,
            "period_year": self.period_year,
            "period_month": self.period_month,
            "total_shipments": self.total_shipments,
            "on_time_count": self.on_time_count,
            "otd_rate": float(self.otd_rate),
            "avg_delay_hours": float(self.avg_delay_hours),
            "reliability_score": float(self.reliability_score),
        }

    def __repr__(self) -> str:
        return (
            f"<CarrierPerformance id={self.id} carrier_id={self.carrier_id} "
            f"period={self.period_year}-{self.period_month}>"
        )
