"""Route option reference model used for recommendation generation."""

from __future__ import annotations

import uuid

from app.extensions import db
from app.models.types import GUID


class RouteOption(db.Model):
    """Seedable global route alternatives by lane and strategy."""

    __tablename__ = "route_options"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    origin_region = db.Column(db.String(100), nullable=False)
    destination_region = db.Column(db.String(100), nullable=False)
    original_mode = db.Column(
        db.Enum(
            "ocean_fcl",
            "ocean_lcl",
            "air",
            "road",
            "rail",
            "multimodal",
            name="route_option_original_mode_enum",
        ),
        nullable=False,
    )
    strategy = db.Column(
        db.Enum("fastest", "cost_optimized", "hybrid", name="route_option_strategy_enum"),
        nullable=False,
    )
    alt_carrier_name = db.Column(db.String(255), nullable=False)
    alt_mode = db.Column(db.String(50), nullable=False)
    alt_route_description = db.Column(db.Text, nullable=False)
    estimated_transit_days = db.Column(db.Numeric(5, 1), nullable=False)
    cost_delta_percent = db.Column(db.Numeric(6, 2), nullable=False)
    baseline_on_time_rate = db.Column(db.Numeric(5, 2), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    @classmethod
    def seed(cls, db_instance) -> int:
        """Insert realistic reference route options across major trade lanes."""

        options = [
            {
                "origin_region": "East Asia",
                "destination_region": "North America West Coast",
                "original_mode": "ocean_fcl",
                "strategy": "fastest",
                "alt_carrier_name": "Maersk Line",
                "alt_mode": "Ocean Premium",
                "alt_route_description": "Shanghai to Los Angeles premium direct sailing with priority berth.",
                "estimated_transit_days": 14.5,
                "cost_delta_percent": 18.0,
                "baseline_on_time_rate": 0.87,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "North America West Coast",
                "original_mode": "ocean_fcl",
                "strategy": "cost_optimized",
                "alt_carrier_name": "Evergreen Line",
                "alt_mode": "Ocean Standard",
                "alt_route_description": "Ningbo to Long Beach via Busan consolidation for reduced lane cost.",
                "estimated_transit_days": 19.0,
                "cost_delta_percent": -9.5,
                "baseline_on_time_rate": 0.79,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "North America West Coast",
                "original_mode": "ocean_lcl",
                "strategy": "hybrid",
                "alt_carrier_name": "CMA CGM",
                "alt_mode": "Ocean LCL Express",
                "alt_route_description": "Shenzhen to Oakland weekly express feeder with reduced transshipment.",
                "estimated_transit_days": 16.0,
                "cost_delta_percent": 4.0,
                "baseline_on_time_rate": 0.83,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "North America East Coast",
                "original_mode": "ocean_fcl",
                "strategy": "fastest",
                "alt_carrier_name": "MSC",
                "alt_mode": "Ocean via Panama",
                "alt_route_description": "Yantian to Savannah via Panama Canal with premium slot guarantee.",
                "estimated_transit_days": 25.0,
                "cost_delta_percent": 13.0,
                "baseline_on_time_rate": 0.84,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "North America East Coast",
                "original_mode": "ocean_fcl",
                "strategy": "cost_optimized",
                "alt_carrier_name": "COSCO Shipping",
                "alt_mode": "Ocean Standard",
                "alt_route_description": "Qingdao to New York via transshipment at Piraeus for lower cost.",
                "estimated_transit_days": 31.0,
                "cost_delta_percent": -11.0,
                "baseline_on_time_rate": 0.75,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "Europe North",
                "original_mode": "ocean_fcl",
                "strategy": "fastest",
                "alt_carrier_name": "Hapag-Lloyd",
                "alt_mode": "Ocean Priority",
                "alt_route_description": "Busan to Rotterdam direct service with priority inland drayage.",
                "estimated_transit_days": 23.0,
                "cost_delta_percent": 15.0,
                "baseline_on_time_rate": 0.86,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "Europe North",
                "original_mode": "ocean_lcl",
                "strategy": "cost_optimized",
                "alt_carrier_name": "ONE",
                "alt_mode": "Ocean LCL",
                "alt_route_description": "Xiamen to Hamburg via Singapore consolidation corridor.",
                "estimated_transit_days": 29.5,
                "cost_delta_percent": -12.5,
                "baseline_on_time_rate": 0.77,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "Europe North",
                "original_mode": "air",
                "strategy": "hybrid",
                "alt_carrier_name": "Emirates SkyCargo",
                "alt_mode": "Air",
                "alt_route_description": "Hong Kong to Amsterdam via Dubai with controlled transshipment window.",
                "estimated_transit_days": 3.5,
                "cost_delta_percent": 9.0,
                "baseline_on_time_rate": 0.91,
            },
            {
                "origin_region": "South Asia",
                "destination_region": "Europe North",
                "original_mode": "ocean_fcl",
                "strategy": "fastest",
                "alt_carrier_name": "Maersk Line",
                "alt_mode": "Ocean",
                "alt_route_description": "Nhava Sheva to Felixstowe direct service with berth window assurance.",
                "estimated_transit_days": 18.0,
                "cost_delta_percent": 11.0,
                "baseline_on_time_rate": 0.85,
            },
            {
                "origin_region": "South Asia",
                "destination_region": "Europe North",
                "original_mode": "ocean_fcl",
                "strategy": "cost_optimized",
                "alt_carrier_name": "CMA CGM",
                "alt_mode": "Ocean",
                "alt_route_description": "Mundra to Antwerp via Jebel Ali relay for cost savings.",
                "estimated_transit_days": 22.0,
                "cost_delta_percent": -8.0,
                "baseline_on_time_rate": 0.78,
            },
            {
                "origin_region": "South Asia",
                "destination_region": "Europe South",
                "original_mode": "ocean_lcl",
                "strategy": "hybrid",
                "alt_carrier_name": "MSC",
                "alt_mode": "Ocean LCL",
                "alt_route_description": "Chennai to Genoa via transshipment at Colombo using scheduled feeder.",
                "estimated_transit_days": 20.5,
                "cost_delta_percent": -2.5,
                "baseline_on_time_rate": 0.81,
            },
            {
                "origin_region": "South Asia",
                "destination_region": "Middle East",
                "original_mode": "air",
                "strategy": "fastest",
                "alt_carrier_name": "Qatar Airways Cargo",
                "alt_mode": "Air",
                "alt_route_description": "Delhi to Doha direct uplift with same-day customs handoff.",
                "estimated_transit_days": 1.2,
                "cost_delta_percent": 14.0,
                "baseline_on_time_rate": 0.94,
            },
            {
                "origin_region": "Middle East",
                "destination_region": "Europe North",
                "original_mode": "air",
                "strategy": "hybrid",
                "alt_carrier_name": "Emirates SkyCargo",
                "alt_mode": "Air",
                "alt_route_description": "Dubai to Frankfurt dedicated cargo lane with backup capacity.",
                "estimated_transit_days": 1.7,
                "cost_delta_percent": 6.5,
                "baseline_on_time_rate": 0.93,
            },
            {
                "origin_region": "Middle East",
                "destination_region": "Europe North",
                "original_mode": "ocean_fcl",
                "strategy": "cost_optimized",
                "alt_carrier_name": "CMA CGM",
                "alt_mode": "Ocean",
                "alt_route_description": "Jebel Ali to Rotterdam via Suez standard service.",
                "estimated_transit_days": 16.0,
                "cost_delta_percent": -6.0,
                "baseline_on_time_rate": 0.82,
            },
            {
                "origin_region": "Middle East",
                "destination_region": "South Asia",
                "original_mode": "ocean_lcl",
                "strategy": "fastest",
                "alt_carrier_name": "MSC",
                "alt_mode": "Ocean",
                "alt_route_description": "Dammam to Mumbai priority vessel with reduced dwell handling.",
                "estimated_transit_days": 7.5,
                "cost_delta_percent": 7.0,
                "baseline_on_time_rate": 0.88,
            },
            {
                "origin_region": "Europe North",
                "destination_region": "North America East Coast",
                "original_mode": "ocean_fcl",
                "strategy": "hybrid",
                "alt_carrier_name": "Hapag-Lloyd",
                "alt_mode": "Ocean",
                "alt_route_description": "Hamburg to New York with Halifax contingency call option.",
                "estimated_transit_days": 11.0,
                "cost_delta_percent": 3.5,
                "baseline_on_time_rate": 0.86,
            },
            {
                "origin_region": "Europe North",
                "destination_region": "North America East Coast",
                "original_mode": "air",
                "strategy": "fastest",
                "alt_carrier_name": "Lufthansa Cargo",
                "alt_mode": "Air",
                "alt_route_description": "Frankfurt to JFK direct cargo flight with same-day release.",
                "estimated_transit_days": 1.0,
                "cost_delta_percent": 22.0,
                "baseline_on_time_rate": 0.95,
            },
            {
                "origin_region": "Europe North",
                "destination_region": "East Asia",
                "original_mode": "air",
                "strategy": "cost_optimized",
                "alt_carrier_name": "Turkish Cargo",
                "alt_mode": "Air",
                "alt_route_description": "Amsterdam to Seoul via Istanbul consolidation lane.",
                "estimated_transit_days": 2.8,
                "cost_delta_percent": -7.5,
                "baseline_on_time_rate": 0.88,
            },
            {
                "origin_region": "North America West Coast",
                "destination_region": "East Asia",
                "original_mode": "ocean_fcl",
                "strategy": "fastest",
                "alt_carrier_name": "ONE",
                "alt_mode": "Ocean",
                "alt_route_description": "Seattle to Yokohama express service with reduced transpacific dwell.",
                "estimated_transit_days": 12.0,
                "cost_delta_percent": 10.0,
                "baseline_on_time_rate": 0.85,
            },
            {
                "origin_region": "North America West Coast",
                "destination_region": "East Asia",
                "original_mode": "ocean_fcl",
                "strategy": "cost_optimized",
                "alt_carrier_name": "Evergreen Line",
                "alt_mode": "Ocean",
                "alt_route_description": "Vancouver to Kaohsiung standard service with lower bunker surcharge.",
                "estimated_transit_days": 15.0,
                "cost_delta_percent": -8.5,
                "baseline_on_time_rate": 0.8,
            },
            {
                "origin_region": "North America East Coast",
                "destination_region": "Europe North",
                "original_mode": "ocean_lcl",
                "strategy": "hybrid",
                "alt_carrier_name": "MSC",
                "alt_mode": "Ocean",
                "alt_route_description": "Norfolk to Antwerp with optimized feeder transfer in Le Havre.",
                "estimated_transit_days": 13.5,
                "cost_delta_percent": 2.0,
                "baseline_on_time_rate": 0.84,
            },
            {
                "origin_region": "North America East Coast",
                "destination_region": "Europe North",
                "original_mode": "air",
                "strategy": "fastest",
                "alt_carrier_name": "FedEx Express",
                "alt_mode": "Air",
                "alt_route_description": "Memphis to Paris overnight freight corridor.",
                "estimated_transit_days": 1.1,
                "cost_delta_percent": 19.0,
                "baseline_on_time_rate": 0.94,
            },
            {
                "origin_region": "South Asia",
                "destination_region": "Southeast Asia",
                "original_mode": "road",
                "strategy": "cost_optimized",
                "alt_carrier_name": "Nippon Express",
                "alt_mode": "Multimodal",
                "alt_route_description": "India to Thailand via coastal feeder and inland bonded trucking.",
                "estimated_transit_days": 9.5,
                "cost_delta_percent": -5.5,
                "baseline_on_time_rate": 0.82,
            },
            {
                "origin_region": "Southeast Asia",
                "destination_region": "Australia East Coast",
                "original_mode": "ocean_fcl",
                "strategy": "fastest",
                "alt_carrier_name": "Maersk Line",
                "alt_mode": "Ocean",
                "alt_route_description": "Singapore to Sydney direct weekly service with priority discharge.",
                "estimated_transit_days": 11.5,
                "cost_delta_percent": 8.0,
                "baseline_on_time_rate": 0.88,
            },
            {
                "origin_region": "Southeast Asia",
                "destination_region": "Australia East Coast",
                "original_mode": "ocean_lcl",
                "strategy": "hybrid",
                "alt_carrier_name": "CMA CGM",
                "alt_mode": "Ocean",
                "alt_route_description": "Port Klang to Brisbane with transshipment at Singapore.",
                "estimated_transit_days": 14.0,
                "cost_delta_percent": -1.0,
                "baseline_on_time_rate": 0.83,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "South Asia",
                "original_mode": "rail",
                "strategy": "fastest",
                "alt_carrier_name": "DB Schenker",
                "alt_mode": "Multimodal Rail",
                "alt_route_description": "Chengdu to Kolkata via trans-Himalayan rail-road corridor.",
                "estimated_transit_days": 8.0,
                "cost_delta_percent": 12.0,
                "baseline_on_time_rate": 0.81,
            },
            {
                "origin_region": "Europe North",
                "destination_region": "Middle East",
                "original_mode": "road",
                "strategy": "hybrid",
                "alt_carrier_name": "DSV",
                "alt_mode": "Road",
                "alt_route_description": "Istanbul gateway trucking relay to GCC markets.",
                "estimated_transit_days": 6.5,
                "cost_delta_percent": 4.5,
                "baseline_on_time_rate": 0.85,
            },
            {
                "origin_region": "Europe North",
                "destination_region": "Middle East",
                "original_mode": "air",
                "strategy": "cost_optimized",
                "alt_carrier_name": "Kuehne+Nagel",
                "alt_mode": "Air Consolidated",
                "alt_route_description": "Amsterdam to Dubai consolidated uplift with planned 8h handling window.",
                "estimated_transit_days": 2.4,
                "cost_delta_percent": -5.0,
                "baseline_on_time_rate": 0.9,
            },
            {
                "origin_region": "North America West Coast",
                "destination_region": "South Asia",
                "original_mode": "air",
                "strategy": "hybrid",
                "alt_carrier_name": "UPS",
                "alt_mode": "Air",
                "alt_route_description": "Los Angeles to Bengaluru via Dubai with dual-leg protection.",
                "estimated_transit_days": 2.9,
                "cost_delta_percent": 3.0,
                "baseline_on_time_rate": 0.9,
            },
            {
                "origin_region": "North America East Coast",
                "destination_region": "South Asia",
                "original_mode": "ocean_fcl",
                "strategy": "cost_optimized",
                "alt_carrier_name": "MSC",
                "alt_mode": "Ocean",
                "alt_route_description": "Charleston to Nhava Sheva via Suez standard schedule.",
                "estimated_transit_days": 27.0,
                "cost_delta_percent": -10.0,
                "baseline_on_time_rate": 0.76,
            },
            {
                "origin_region": "East Asia",
                "destination_region": "Middle East",
                "original_mode": "ocean_fcl",
                "strategy": "hybrid",
                "alt_carrier_name": "COSCO Shipping",
                "alt_mode": "Ocean",
                "alt_route_description": "Shanghai to Jebel Ali with strategic call at Singapore.",
                "estimated_transit_days": 17.0,
                "cost_delta_percent": 1.5,
                "baseline_on_time_rate": 0.84,
            },
            {
                "origin_region": "South Asia",
                "destination_region": "North America West Coast",
                "original_mode": "ocean_lcl",
                "strategy": "fastest",
                "alt_carrier_name": "Maersk Line",
                "alt_mode": "Ocean",
                "alt_route_description": "Mundra to Oakland with expedited transshipment at Singapore.",
                "estimated_transit_days": 24.0,
                "cost_delta_percent": 10.5,
                "baseline_on_time_rate": 0.83,
            },
            {
                "origin_region": "South Asia",
                "destination_region": "North America West Coast",
                "original_mode": "air",
                "strategy": "cost_optimized",
                "alt_carrier_name": "DHL Express",
                "alt_mode": "Air Consolidated",
                "alt_route_description": "Delhi to San Francisco via Leipzig consolidation lane.",
                "estimated_transit_days": 3.1,
                "cost_delta_percent": -6.2,
                "baseline_on_time_rate": 0.89,
            },
        ]

        inserted_count = 0
        for row in options:
            exists = cls.query.filter_by(
                origin_region=row["origin_region"],
                destination_region=row["destination_region"],
                original_mode=row["original_mode"],
                strategy=row["strategy"],
                alt_carrier_name=row["alt_carrier_name"],
                alt_route_description=row["alt_route_description"],
            ).first()
            if exists:
                continue

            db_instance.session.add(cls(**row))
            inserted_count += 1

        if inserted_count:
            db_instance.session.commit()

        return inserted_count

    def to_dict(self) -> dict:
        """Serialize route option fields."""

        return {
            "id": str(self.id),
            "origin_region": self.origin_region,
            "destination_region": self.destination_region,
            "original_mode": self.original_mode,
            "strategy": self.strategy,
            "alt_carrier_name": self.alt_carrier_name,
            "alt_mode": self.alt_mode,
            "alt_route_description": self.alt_route_description,
            "estimated_transit_days": float(self.estimated_transit_days),
            "cost_delta_percent": float(self.cost_delta_percent),
            "baseline_on_time_rate": float(self.baseline_on_time_rate),
            "is_active": self.is_active,
        }

    def __repr__(self) -> str:
        return (
            f"<RouteOption id={self.id} {self.origin_region!r}->{self.destination_region!r} "
            f"strategy={self.strategy}>"
        )
