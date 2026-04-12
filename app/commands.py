"""Application CLI commands for platform administration."""

from __future__ import annotations

import logging
from datetime import datetime

import click

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.organisation import Organisation
from app.models.user import User

logger = logging.getLogger(__name__)

_DEFAULT_SUPERADMIN_EMAIL = "superadmin@chainwatchpro.internal"
_DEFAULT_SUPERADMIN_PASSWORD = "ChainWatch@SuperAdmin2026!"


def _platform_org() -> Organisation:
    org = Organisation.query.filter_by(name="ChainWatch Pro Internal").first()
    if org:
        return org

    org = Organisation(
        name="ChainWatch Pro Internal",
        industry="SaaS",
        subscription_plan="enterprise",
        subscription_status="active",
        onboarding_complete=True,
        is_active=True,
    )
    db.session.add(org)
    db.session.commit()
    return org


def ensure_default_superadmin(app) -> tuple[bool, User | None]:
    """Create default SuperAdmin if no SuperAdmin exists in the platform."""

    with app.app_context():
        existing = User.query.filter_by(role="superadmin").first()
        if existing is not None:
            logger.info("SuperAdmin already exists - skipping creation.")
            return False, existing

        platform_org = _platform_org()

        email = app.config.get("SUPERADMIN_EMAIL", _DEFAULT_SUPERADMIN_EMAIL).strip().lower()
        password = app.config.get("SUPERADMIN_PASSWORD", _DEFAULT_SUPERADMIN_PASSWORD)
        first_name = app.config.get("SUPERADMIN_FIRST_NAME", "Platform").strip() or "Platform"
        last_name = app.config.get("SUPERADMIN_LAST_NAME", "Administrator").strip() or "Administrator"

        user = User.query.filter_by(email=email).first()
        if user is None:
            user = User(
                email=email,
                first_name=first_name,
                last_name=last_name,
                role="superadmin",
                is_verified=True,
                is_active=True,
                must_change_password=False,
                account_source="superadmin_created",
                onboarding_step_completed=4,
                organisation_id=platform_org.id,
                superadmin_notes="Auto-created platform SuperAdmin account.",
            )
            user.set_password(password)
            db.session.add(user)
        else:
            user.role = "superadmin"
            user.is_verified = True
            user.is_active = True
            user.must_change_password = False
            user.account_source = "superadmin_created"
            user.organisation_id = platform_org.id
            user.superadmin_notes = "Promoted to SuperAdmin by automatic bootstrap."
            user.set_password(password)

        db.session.commit()

        audit = AuditLog(
            organisation_id=platform_org.id,
            actor_user_id=user.id,
            actor_label=f"SuperAdmin:{user.email}",
            event_type="superadmin_account_created",
            description="Default SuperAdmin account created automatically.",
            metadata_json={"email": user.email},
            created_at=datetime.utcnow(),
        )
        db.session.add(audit)
        db.session.commit()

        if email == _DEFAULT_SUPERADMIN_EMAIL or password == _DEFAULT_SUPERADMIN_PASSWORD:
            click.secho(
                "[ChainWatch Pro] WARNING: SuperAdmin is using default credentials. Change immediately in production.",
                fg="yellow",
                bold=True,
            )

        return True, user


def register_superadmin_commands(app) -> None:
    """Register SuperAdmin related Flask CLI commands."""

    @app.cli.command("create-superadmin")
    def create_superadmin_command():
        created, user = ensure_default_superadmin(app)
        if created and user is not None:
            click.secho(
                f"[ChainWatch Pro] SuperAdmin created successfully. Email: {user.email} - CHANGE THIS PASSWORD IMMEDIATELY IN PRODUCTION.",
                fg="green",
                bold=True,
            )
            return

        click.secho("[ChainWatch Pro] SuperAdmin already exists - skipping creation.", fg="cyan")

    @app.cli.command("list-superadmins")
    def list_superadmins_command():
        with app.app_context():
            superadmins = (
                User.query.filter_by(role="superadmin")
                .order_by(User.created_at.asc())
                .all()
            )

            if not superadmins:
                click.echo("No SuperAdmin users found.")
                return

            for user in superadmins:
                last_login = user.last_login_at.isoformat() if user.last_login_at else "Never"
                click.echo(f"{user.email} | Last Login: {last_login}")
