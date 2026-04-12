"""Carrier reference model and seed data."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.extensions import db
from app.models.types import GUID


class Carrier(db.Model):
    """Carrier entity used for shipment and recommendation associations."""

    __tablename__ = "carriers"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    scac_code = db.Column(db.String(10), nullable=True, index=True)
    name = db.Column(db.String(255), nullable=False)
    mode = db.Column(
        db.Enum("ocean", "air", "road", "rail", "multimodal", name="carrier_mode_enum"),
        nullable=False,
    )
    tracking_api_type = db.Column(db.String(50), nullable=True)
    is_global_carrier = db.Column(db.Boolean, nullable=False, default=False)
    website_url = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    shipments = db.relationship("Shipment", back_populates="carrier", lazy="dynamic")
    route_recommendations = db.relationship(
        "RouteRecommendation",
        foreign_keys="RouteRecommendation.alt_carrier_id",
        back_populates="alt_carrier",
        lazy="dynamic",
        overlaps="alt_recommendations",
    )
    alt_recommendations = db.relationship(
        "RouteRecommendation",
        foreign_keys="RouteRecommendation.alt_carrier_id",
        lazy="dynamic",
        overlaps="route_recommendations,alt_carrier",
    )
    carrier_performances = db.relationship(
        "CarrierPerformance",
        back_populates="carrier",
        lazy="dynamic",
    )

    @classmethod
    def seed_global_carriers(cls, db_instance) -> int:
        """Insert globally available real-world carriers if not already present."""

        carriers = [
            {
                "name": "Maersk Line",
                "scac_code": "MAEU",
                "mode": "ocean",
                "tracking_api_type": "REST",
                "website_url": "https://www.maersk.com",
            },
            {
                "name": "Mediterranean Shipping Company",
                "scac_code": "MSCU",
                "mode": "ocean",
                "tracking_api_type": "REST",
                "website_url": "https://www.msc.com",
            },
            {
                "name": "CMA CGM",
                "scac_code": "CMDU",
                "mode": "ocean",
                "tracking_api_type": "REST",
                "website_url": "https://www.cma-cgm.com",
            },
            {
                "name": "Evergreen Line",
                "scac_code": "EGLV",
                "mode": "ocean",
                "tracking_api_type": "REST",
                "website_url": "https://www.evergreen-line.com",
            },
            {
                "name": "DHL Express",
                "scac_code": "DHLE",
                "mode": "air",
                "tracking_api_type": "REST",
                "website_url": "https://www.dhl.com",
            },
            {
                "name": "FedEx Express",
                "scac_code": "FDEG",
                "mode": "air",
                "tracking_api_type": "REST",
                "website_url": "https://www.fedex.com",
            },
            {
                "name": "UPS",
                "scac_code": "UPSN",
                "mode": "air",
                "tracking_api_type": "REST",
                "website_url": "https://www.ups.com",
            },
            {
                "name": "Emirates SkyCargo",
                "scac_code": "EKSC",
                "mode": "air",
                "tracking_api_type": "REST",
                "website_url": "https://www.skycargo.com",
            },
            {
                "name": "DB Schenker",
                "scac_code": "DBSK",
                "mode": "multimodal",
                "tracking_api_type": "EDI",
                "website_url": "https://www.dbschenker.com",
            },
            {
                "name": "Kuehne+Nagel",
                "scac_code": "KHNN",
                "mode": "multimodal",
                "tracking_api_type": "EDI",
                "website_url": "https://home.kuehne-nagel.com",
            },
            {
                "name": "XPO Logistics",
                "scac_code": "XPOL",
                "mode": "road",
                "tracking_api_type": "REST",
                "website_url": "https://www.xpo.com",
            },
            {
                "name": "J.B. Hunt",
                "scac_code": "JBHU",
                "mode": "road",
                "tracking_api_type": "REST",
                "website_url": "https://www.jbhunt.com",
            },
            {
                "name": "CEVA Logistics",
                "scac_code": "CEVA",
                "mode": "multimodal",
                "tracking_api_type": "EDI",
                "website_url": "https://www.cevalogistics.com",
            },
            {
                "name": "DSV",
                "scac_code": "DSVV",
                "mode": "multimodal",
                "tracking_api_type": "EDI",
                "website_url": "https://www.dsv.com",
            },
            {
                "name": "Nippon Express",
                "scac_code": "NPEX",
                "mode": "multimodal",
                "tracking_api_type": "EDI",
                "website_url": "https://www.nipponexpress.com",
            },
        ]

        inserted_count = 0
        for record in carriers:
            existing = cls.query.filter_by(name=record["name"]).first()
            if existing:
                continue
            db_instance.session.add(
                cls(
                    name=record["name"],
                    scac_code=record["scac_code"],
                    mode=record["mode"],
                    tracking_api_type=record["tracking_api_type"],
                    is_global_carrier=True,
                    website_url=record["website_url"],
                )
            )
            inserted_count += 1

        if inserted_count:
            db_instance.session.commit()

        return inserted_count

    def to_dict(self) -> dict:
        """Serialize carrier fields."""

        return {
            "id": str(self.id),
            "scac_code": self.scac_code,
            "name": self.name,
            "mode": self.mode,
            "tracking_api_type": self.tracking_api_type,
            "is_global_carrier": self.is_global_carrier,
            "website_url": self.website_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<Carrier id={self.id} name={self.name!r} mode={self.mode}>"
