"""Feature flag model for platform-level rollout controls."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.extensions import db
from app.models.types import GUID, JSONType


class FeatureFlag(db.Model):
    """Platform feature flag with global, plan, and organisation targeting."""

    __tablename__ = "feature_flags"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    flag_name = db.Column(db.String(100), unique=True, nullable=False, index=True)
    is_enabled_globally = db.Column(db.Boolean, nullable=False, default=True)
    enabled_for_plans = db.Column(JSONType(), nullable=False, default=list)
    enabled_for_org_ids = db.Column(JSONType(), nullable=False, default=list)
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "flag_name": self.flag_name,
            "is_enabled_globally": bool(self.is_enabled_globally),
            "enabled_for_plans": self.enabled_for_plans if isinstance(self.enabled_for_plans, list) else [],
            "enabled_for_org_ids": self.enabled_for_org_ids if isinstance(self.enabled_for_org_ids, list) else [],
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return f"<FeatureFlag id={self.id} flag_name={self.flag_name!r}>"
