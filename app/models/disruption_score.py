"""Disruption score model storing DRS snapshots and sub-scores."""

from __future__ import annotations

import uuid

from app.extensions import db
from app.models.types import GUID, JSONType


class DisruptionScore(db.Model):
    """Stores historical disruption risk computations for a shipment."""

    __tablename__ = "disruption_scores"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    shipment_id = db.Column(
        GUID(),
        db.ForeignKey("shipments.id"),
        nullable=False,
        index=True,
    )
    computed_at = db.Column(db.DateTime, nullable=False, index=True)
    drs_total = db.Column(db.Numeric(5, 2), nullable=False)
    tvs = db.Column(db.Numeric(5, 2), nullable=False)
    mcs = db.Column(db.Numeric(5, 2), nullable=False)
    ehs = db.Column(db.Numeric(5, 2), nullable=False)
    crs = db.Column(db.Numeric(5, 2), nullable=False)
    dtas = db.Column(db.Numeric(5, 2), nullable=False)
    cps = db.Column(db.Numeric(5, 2), nullable=False)
    ehs_signals = db.Column(JSONType(), nullable=True)

    shipment = db.relationship("Shipment", back_populates="disruption_scores")

    def to_dict(self) -> dict:
        """Serialize disruption score fields."""

        return {
            "id": str(self.id),
            "shipment_id": str(self.shipment_id),
            "computed_at": self.computed_at.isoformat() if self.computed_at else None,
            "drs_total": float(self.drs_total),
            "tvs": float(self.tvs),
            "mcs": float(self.mcs),
            "ehs": float(self.ehs),
            "crs": float(self.crs),
            "dtas": float(self.dtas),
            "cps": float(self.cps),
            "ehs_signals": self.ehs_signals,
        }

    def __repr__(self) -> str:
        return (
            f"<DisruptionScore id={self.id} shipment_id={self.shipment_id} "
            f"drs_total={self.drs_total}>"
        )
