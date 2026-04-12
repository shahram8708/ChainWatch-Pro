"""Demo lead model for public demo request capture."""

from __future__ import annotations

import uuid
from datetime import datetime

from app.extensions import db
from app.models.types import GUID


class DemoLead(db.Model):
    """Stores demo requests submitted from public website pages."""

    __tablename__ = "demo_leads"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(255), nullable=False, index=True)
    company_name = db.Column(db.String(255), nullable=False)
    job_title = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(40), nullable=True)
    company_size = db.Column(db.String(50), nullable=False)
    monthly_shipments = db.Column(db.String(50), nullable=False)
    primary_use_case = db.Column(db.String(120), nullable=False)
    preferred_demo_time = db.Column(db.String(80), nullable=False)
    message = db.Column(db.Text, nullable=True)
    is_contacted = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

    def to_dict(self) -> dict:
        """Serialize lead for admin views and exports."""

        return {
            "id": str(self.id),
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "company_name": self.company_name,
            "job_title": self.job_title,
            "phone": self.phone,
            "company_size": self.company_size,
            "monthly_shipments": self.monthly_shipments,
            "primary_use_case": self.primary_use_case,
            "preferred_demo_time": self.preferred_demo_time,
            "message": self.message,
            "is_contacted": self.is_contacted,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return f"<DemoLead id={self.id} email={self.email!r} company={self.company_name!r}>"
