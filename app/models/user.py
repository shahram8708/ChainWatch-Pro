"""User model with authentication and token management."""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta

import bcrypt
from flask import has_request_context, url_for

from app.extensions import db
from app.models.types import GUID
from app.utils.helpers import hash_token


class User(db.Model):
    """Application user tied to an organisation tenant."""

    __tablename__ = "users"

    id = db.Column(GUID(), primary_key=True, default=uuid.uuid4)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    first_name = db.Column(db.String(100), nullable=False)
    last_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(30), nullable=True)
    job_title = db.Column(db.String(100), nullable=True)
    profile_photo_path = db.Column(db.String(255), nullable=True)
    role = db.Column(
        db.Enum("superadmin", "admin", "manager", "viewer", name="user_role_enum"),
        nullable=False,
        default="manager",
    )
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    invited_by_user_id = db.Column(GUID(), db.ForeignKey("users.id"), nullable=True, index=True)
    invitation_sent_at = db.Column(db.DateTime, nullable=True)
    invitation_accepted_at = db.Column(db.DateTime, nullable=True)
    temporary_password_hash = db.Column(db.String(255), nullable=True)
    account_source = db.Column(db.String(50), nullable=False, default="manual_invite")
    superadmin_notes = db.Column(db.Text, nullable=True)
    is_verified = db.Column(db.Boolean, nullable=False, default=False)
    verification_token = db.Column(db.String(255), nullable=True)
    verification_token_expires_at = db.Column(db.DateTime, nullable=True)
    reset_token_hash = db.Column(db.String(255), nullable=True)
    reset_token_expires_at = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    organisation_id = db.Column(
        GUID(),
        db.ForeignKey("organisations.id"),
        nullable=False,
        index=True,
    )
    timezone = db.Column(db.String(50), nullable=False, default="UTC")
    _is_active = db.Column("is_active", db.Boolean, nullable=False, default=True)
    alert_email_enabled = db.Column(db.Boolean, nullable=False, default=True)
    alert_sms_enabled = db.Column(db.Boolean, nullable=False, default=False)
    onboarding_step_completed = db.Column(db.Integer, nullable=False, default=0)

    organisation = db.relationship("Organisation", back_populates="users")
    invited_by_user = db.relationship(
        "User",
        remote_side=[id],
        foreign_keys=[invited_by_user_id],
        backref=db.backref("invited_users", lazy="dynamic"),
    )
    acknowledged_alerts = db.relationship(
        "Alert",
        foreign_keys="Alert.acknowledged_by",
        back_populates="acknowledging_user",
        lazy="dynamic",
    )
    decided_recommendations = db.relationship(
        "RouteRecommendation",
        foreign_keys="RouteRecommendation.decided_by",
        back_populates="deciding_user",
        lazy="dynamic",
    )
    audit_logs = db.relationship(
        "AuditLog",
        foreign_keys="AuditLog.actor_user_id",
        back_populates="actor_user",
        lazy="dynamic",
    )
    regenerated_ai_contents = db.relationship(
        "AIGeneratedContent",
        foreign_keys="AIGeneratedContent.last_regenerated_by",
        back_populates="last_regenerated_by_user",
        lazy="dynamic",
    )

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return bool(self._is_active)

    @is_active.setter
    def is_active(self, value: bool) -> None:
        self._is_active = bool(value)

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return str(self.id)

    def set_password(self, password: str) -> None:
        """Hash and store user password using bcrypt with work factor 12."""

        hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12))
        self.password_hash = hashed.decode("utf-8")

    def check_password(self, password: str) -> bool:
        """Check password against stored bcrypt hash."""

        if not self.password_hash:
            return False
        return bcrypt.checkpw(password.encode("utf-8"), self.password_hash.encode("utf-8"))

    def generate_verification_token(self) -> str:
        """Generate and persist a plain verification token valid for 24 hours."""

        token = secrets.token_urlsafe(32)
        self.verification_token = token
        self.verification_token_expires_at = datetime.utcnow() + timedelta(hours=24)
        return token

    def verify_email_token(self, token: str) -> bool:
        """Validate and consume email verification token."""

        if not self.verification_token or not self.verification_token_expires_at:
            return False
        if self.verification_token_expires_at < datetime.utcnow():
            return False
        if not secrets.compare_digest(self.verification_token, token):
            return False

        self.is_verified = True
        self.verification_token = None
        self.verification_token_expires_at = None
        return True

    def generate_reset_token(self) -> str:
        """Generate reset token, storing only SHA-256 hash in the database."""

        token = secrets.token_urlsafe(32)
        self.reset_token_hash = hash_token(token)
        self.reset_token_expires_at = datetime.utcnow() + timedelta(hours=1)
        return token

    def verify_reset_token(self, token: str) -> bool:
        """Check password reset token against stored hash and expiry."""

        if not self.reset_token_hash or not self.reset_token_expires_at:
            return False
        if self.reset_token_expires_at < datetime.utcnow():
            return False

        incoming_hash = hash_token(token)
        return secrets.compare_digest(self.reset_token_hash, incoming_hash)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def initials(self) -> str:
        first = (self.first_name or "").strip()[:1]
        last = (self.last_name or "").strip()[:1]
        value = f"{first}{last}".upper()
        return value or "U"

    @property
    def profile_photo_url(self) -> str | None:
        if not self.profile_photo_path:
            return None

        normalized = self.profile_photo_path.replace("\\", "/").lstrip("/")
        if has_request_context():
            return url_for("static", filename=normalized)

        return f"/static/{normalized}"

    @property
    def is_admin(self) -> bool:
        return self.role in {"superadmin", "admin"}

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    @property
    def is_manager_or_above(self) -> bool:
        return self.role in {"superadmin", "admin", "manager"}

    def to_dict(self) -> dict:
        """Serialize non-sensitive user fields."""

        return {
            "id": str(self.id),
            "email": self.email,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "full_name": self.full_name,
            "initials": self.initials,
            "phone": self.phone,
            "job_title": self.job_title,
            "profile_photo_path": self.profile_photo_path,
            "profile_photo_url": self.profile_photo_url,
            "role": self.role,
            "must_change_password": self.must_change_password,
            "invited_by_user_id": str(self.invited_by_user_id) if self.invited_by_user_id else None,
            "invitation_sent_at": self.invitation_sent_at.isoformat() if self.invitation_sent_at else None,
            "invitation_accepted_at": self.invitation_accepted_at.isoformat() if self.invitation_accepted_at else None,
            "account_source": self.account_source,
            "is_verified": self.is_verified,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "organisation_id": str(self.organisation_id) if self.organisation_id else None,
            "timezone": self.timezone,
            "is_active": self.is_active,
            "alert_email_enabled": self.alert_email_enabled,
            "alert_sms_enabled": self.alert_sms_enabled,
            "onboarding_step_completed": self.onboarding_step_completed,
        }

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
