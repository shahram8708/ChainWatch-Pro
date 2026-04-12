"""Persistent cache model for AI-generated feature content."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.extensions import db
from app.models.types import GUID, JSONType


AI_CONTENT_TYPES = (
    "carrier_commentary",
    "shipment_disruption_summary",
    "simulation_narrative",
    "executive_brief",
    "alert_description",
    "route_event_risk",
    "port_congestion_analysis",
)


class AIGeneratedContent(db.Model):
    """Stores Gemini outputs for cache-first AI feature rendering."""

    __tablename__ = "ai_generated_content"
    __table_args__ = (
        db.UniqueConstraint(
            "organisation_id",
            "content_type",
            "content_key",
            name="uq_ai_generated_content_org_type_key",
        ),
        db.Index(
            "ix_ai_generated_content_org_type_key",
            "organisation_id",
            "content_type",
            "content_key",
        ),
        db.Index("ix_ai_generated_content_type_updated_at", "content_type", "updated_at"),
    )

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    organisation_id = db.Column(
        GUID(),
        db.ForeignKey("organisations.id"),
        nullable=False,
        index=True,
    )
    content_type = db.Column(
        db.Enum(*AI_CONTENT_TYPES, name="ai_generated_content_type_enum"),
        nullable=False,
        index=True,
    )
    content_key = db.Column(db.String(255), nullable=False, index=True)
    raw_response = db.Column(db.Text, nullable=False)
    formatted_response = db.Column(db.Text, nullable=True)
    structured_data = db.Column(JSONType(), nullable=True)
    response_format = db.Column(
        db.Enum("markdown", "json", "plain_text", name="ai_response_format_enum"),
        nullable=False,
        default="markdown",
    )
    prompt_used = db.Column(db.Text, nullable=True)
    model_used = db.Column(db.String(50), nullable=False, default="gemini-2.5-flash")
    tokens_used = db.Column(db.Integer, nullable=True)
    generation_duration_ms = db.Column(db.Integer, nullable=True)
    is_stale = db.Column(db.Boolean, nullable=False, default=False)
    regeneration_count = db.Column(db.Integer, nullable=False, default=0)
    last_regenerated_by = db.Column(GUID(), db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=True)

    organisation = db.relationship("Organisation", back_populates="ai_generated_contents")
    last_regenerated_by_user = db.relationship(
        "User",
        foreign_keys=[last_regenerated_by],
        back_populates="regenerated_ai_contents",
    )

    def to_dict(self) -> dict:
        """Serialize cache record fields for route and API responses."""

        return {
            "id": str(self.id),
            "organisation_id": str(self.organisation_id),
            "content_type": self.content_type,
            "content_key": self.content_key,
            "raw_response": self.raw_response,
            "formatted_response": self.formatted_response,
            "structured_data": self.structured_data,
            "response_format": self.response_format,
            "prompt_used": self.prompt_used,
            "model_used": self.model_used,
            "tokens_used": self.tokens_used,
            "generation_duration_ms": self.generation_duration_ms,
            "is_stale": bool(self.is_stale),
            "regeneration_count": int(self.regeneration_count or 0),
            "last_regenerated_by": str(self.last_regenerated_by) if self.last_regenerated_by else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<AIGeneratedContent id={self.id} content_type={self.content_type!r} "
            f"content_key={self.content_key!r}>"
        )
