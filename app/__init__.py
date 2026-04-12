"""Flask application factory for ChainWatch Pro."""

from __future__ import annotations

import logging
import mimetypes
import sys
import uuid
from datetime import datetime, timedelta

import pytz
from flask import Flask, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_user
from sqlalchemy import func

from app.commands import ensure_default_superadmin, register_superadmin_commands
from config import get_config
from app.extensions import csrf, db, init_redis, limiter, login_manager, mail, migrate
from app.utils.helpers import format_inr, format_datetime_user, get_current_org

logger = logging.getLogger(__name__)


def _is_flask_db_command() -> bool:
    """Return True when app is booted for flask db subcommands."""

    # Flask CLI argv patterns include: flask db upgrade, flask db migrate, etc.
    argv = [arg.strip().lower() for arg in sys.argv[1:3]]
    return bool(argv) and argv[0] == "db"


def _register_filters(app: Flask) -> None:
    """Register custom Jinja2 filters used across templates."""

    def format_datetime(value):
        if value is None:
            return "-"

        timezone_name = "UTC"
        if current_user.is_authenticated and getattr(current_user, "timezone", None):
            timezone_name = current_user.timezone

        return format_datetime_user(value, timezone_name)

    def format_currency_inr(value):
        return format_inr(value)

    def risk_level_label(drs):
        score = float(drs or 0)
        if score >= 81:
            return "Critical"
        if score >= 61:
            return "Warning"
        if score >= 31:
            return "Watch"
        return "Green"

    def risk_level_color(drs):
        label = risk_level_label(drs)
        mapping = {
            "Critical": "#D32F2F",
            "Warning": "#FF8C00",
            "Watch": "#F59E0B",
            "Green": "#00A86B",
        }
        return mapping[label]

    def risk_level_bg(drs):
        label = risk_level_label(drs)
        classes = {
            "Critical": "bg-danger-subtle text-danger-emphasis",
            "Warning": "bg-warning-subtle text-warning-emphasis",
            "Watch": "bg-amber-subtle text-warning-emphasis",
            "Green": "bg-success-subtle text-success-emphasis",
        }
        return classes[label]

    def time_ago(dt):
        if dt is None:
            return "-"

        now = datetime.utcnow().replace(tzinfo=pytz.UTC)
        if dt.tzinfo is None:
            moment = dt.replace(tzinfo=pytz.UTC)
        else:
            moment = dt.astimezone(pytz.UTC)

        diff_seconds = int((now - moment).total_seconds())
        if diff_seconds < 60:
            return f"{max(diff_seconds, 1)} seconds ago"
        if diff_seconds < 3600:
            minutes = diff_seconds // 60
            return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
        if diff_seconds < 86400:
            hours = diff_seconds // 3600
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        if diff_seconds < 172800:
            return "Yesterday"

        days = diff_seconds // 86400
        return f"{days} days ago"

    app.jinja_env.filters["format_datetime"] = format_datetime
    app.jinja_env.filters["format_currency_inr"] = format_currency_inr
    app.jinja_env.filters["risk_level_label"] = risk_level_label
    app.jinja_env.filters["risk_level_color"] = risk_level_color
    app.jinja_env.filters["risk_level_bg"] = risk_level_bg
    app.jinja_env.filters["time_ago"] = time_ago


def _register_context_processor(app: Flask) -> None:
    """Inject shared context variables used in templates."""

    @app.context_processor
    def inject_global_context():
        current_org = None
        unread_alert_count = 0
        active_shipment_count = 0

        if current_user.is_authenticated:
            current_org = get_current_org()
            if current_org is not None:
                try:
                    from app.models.alert import Alert
                    from app.models.shipment import Shipment

                    unread_subquery = (
                        db.session.query(func.count(Alert.id))
                        .filter(
                            Alert.organisation_id == current_org.id,
                            Alert.is_acknowledged.is_(False),
                        )
                        .scalar_subquery()
                    )

                    active_shipments_subquery = (
                        db.session.query(func.count(Shipment.id))
                        .filter(
                            Shipment.organisation_id == current_org.id,
                            Shipment.is_archived.is_(False),
                            Shipment.status != "delivered",
                        )
                        .scalar_subquery()
                    )

                    counts = db.session.query(
                        unread_subquery.label("unread_alert_count"),
                        active_shipments_subquery.label("active_shipment_count"),
                    ).one()

                    unread_alert_count = int(counts.unread_alert_count or 0)
                    active_shipment_count = int(counts.active_shipment_count or 0)
                except Exception:
                    unread_alert_count = 0
                    active_shipment_count = 0
                    logger.exception("Failed to count unread alerts for context processor.")

        return {
            "current_org": current_org,
            "unread_alert_count": unread_alert_count,
            "active_shipment_count": active_shipment_count,
            "current_year": datetime.utcnow().year,
        }


def _register_security_headers(app: Flask) -> None:
    """Apply strict security headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response


def _register_error_handlers(app: Flask) -> None:
    """Register custom application error handlers."""

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("errors/403.html", error=error), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html", error=error), 404

    @app.errorhandler(500)
    def internal_server_error(error):
        db.session.rollback()
        return render_template("errors/500.html", error=error), 500


def create_app(config_name: str = "development") -> Flask:
    """Application factory entrypoint."""

    # Ensure consistent MIME types for module scripts across OS environments.
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")

    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.config.from_object(get_config(config_name))

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    mail.init_app(app)
    limiter.init_app(app)
    csrf.init_app(app)
    redis_client = init_redis(app)

    if app.config.get("USE_REDIS", False):
        if redis_client is None:
            logger.warning("Redis client initialization failed. Caching-backed features will degrade gracefully.")
        else:
            logger.info("Redis client initialized successfully for URL=%s", app.config.get("REDIS_URL"))
    else:
        logger.info("Redis is disabled for environment=%s.", app.config.get("ENV_NAME", config_name))

    from app.models import (  # noqa: F401
        Alert,
        AIGeneratedContent,
        AuditLog,
        Carrier,
        CarrierPerformance,
        DemoLead,
        DisruptionScore,
        FeatureFlag,
        Organisation,
        RouteOption,
        RouteRecommendation,
        Shipment,
        User,
    )

    from app.routes.auth import auth_bp
    from app.routes.dashboard import dashboard_bp
    from app.routes.shipments import shipments_bp
    from app.routes.alerts import alerts_bp
    from app.routes.api import api_bp
    from app.routes.onboarding import onboarding_bp
    from app.routes.optimizer import optimizer_bp
    from app.routes.carrier_intel import carrier_intel_bp
    from app.routes.planner import planner_bp
    from app.routes.risk_map import risk_map_bp
    from app.routes.public import public_bp
    from app.routes.executive import executive_bp
    from app.routes.reports import reports_bp
    from app.routes.audit import audit_bp
    from app.routes.settings import settings_bp
    from app.routes.superadmin import superadmin_bp
    from app.routes.webhooks import webhooks_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(public_bp)
    app.register_blueprint(onboarding_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(shipments_bp)
    app.register_blueprint(alerts_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(optimizer_bp)
    app.register_blueprint(carrier_intel_bp)
    app.register_blueprint(planner_bp)
    app.register_blueprint(risk_map_bp)

    app.register_blueprint(executive_bp, url_prefix="/executive")
    app.register_blueprint(reports_bp, url_prefix="/reports")
    app.register_blueprint(audit_bp, url_prefix="/audit-log")
    app.register_blueprint(settings_bp, url_prefix="/settings")
    sa_prefix = app.config.get("SUPERADMIN_URL_PREFIX", "/sa-panel")
    app.register_blueprint(superadmin_bp, url_prefix=sa_prefix)
    csrf.exempt(webhooks_bp)
    app.register_blueprint(webhooks_bp, url_prefix="/webhooks")

    _register_filters(app)
    _register_context_processor(app)
    _register_error_handlers(app)
    _register_security_headers(app)
    register_superadmin_commands(app)

    if _is_flask_db_command():
        logger.info("Skipping default SuperAdmin bootstrap during flask db command.")
    else:
        try:
            ensure_default_superadmin(app)
        except Exception:
            logger.exception("Failed to auto-create default SuperAdmin account.")

    @app.before_request
    def enforce_auth_flow():
        g.current_org = None
        endpoint = request.endpoint or ""

        auth_guest_only_endpoints = {
            "auth.login",
            "auth.register",
            "auth.forgot_password",
        }

        auth_allowed_for_unverified = {
            "auth.forced_password_change",
            "auth.verify_pending",
            "auth.verify_email",
            "auth.resend_verification",
            "auth.logout",
            "auth.login",
            "auth.forgot_password",
            "auth.reset_password",
            "static",
        }

        if current_user.is_authenticated:
            if session.get("superadmin_impersonating"):
                started_at_raw = session.get("impersonation_started_at")
                started_at = None
                if started_at_raw:
                    try:
                        started_at = datetime.fromisoformat(started_at_raw)
                    except ValueError:
                        started_at = None

                timeout_minutes = int(app.config.get("SUPERADMIN_SESSION_TIMEOUT_MINUTES", 30) or 30)
                if started_at and datetime.utcnow() - started_at > timedelta(minutes=timeout_minutes):
                    original_id_text = session.get("superadmin_original_user_id")
                    impersonated_org_id_text = session.get("impersonating_org_id")
                    restored = False
                    if original_id_text:
                        try:
                            parsed_original_id = uuid.UUID(original_id_text)
                            from app.models.audit_log import AuditLog
                            from app.models.user import User

                            original_user = User.query.filter_by(id=parsed_original_id, role="superadmin").first()
                            if original_user is not None:
                                login_user(original_user)
                                session["superadmin_last_elevated_at"] = datetime.utcnow().isoformat()
                                restored = True

                                try:
                                    from app.models.organisation import Organisation

                                    platform_org = Organisation.query.filter_by(name="ChainWatch Pro Internal").first()
                                    if platform_org is not None:
                                        event_org_id = platform_org.id
                                    else:
                                        event_org_id = original_user.organisation_id

                                    db.session.add(
                                        AuditLog(
                                            organisation_id=event_org_id,
                                            actor_user_id=original_user.id,
                                            actor_label=f"SuperAdmin:{original_user.email}",
                                            event_type="superadmin_impersonation_timeout",
                                            description="Impersonation session expired automatically after timeout.",
                                            metadata_json={"impersonated_org_id": impersonated_org_id_text},
                                            ip_address=request.remote_addr,
                                        )
                                    )
                                    db.session.commit()
                                except Exception:
                                    db.session.rollback()
                                    logger.exception("Failed to log superadmin_impersonation_timeout event.")
                        except (ValueError, TypeError):
                            restored = False

                    session.pop("superadmin_impersonating", None)
                    session.pop("superadmin_original_user_id", None)
                    session.pop("impersonating_org_id", None)
                    session.pop("impersonation_started_at", None)

                    flash("Impersonation session expired after 30 minutes for security.", "warning")

                    if restored and impersonated_org_id_text:
                        try:
                            parsed_org_id = uuid.UUID(impersonated_org_id_text)
                            return redirect(url_for("superadmin.organisation_detail", org_id=parsed_org_id))
                        except (ValueError, TypeError):
                            return redirect(url_for("superadmin.dashboard"))

                    if restored:
                        return redirect(url_for("superadmin.dashboard"))
                    return redirect(url_for("auth.login"))

            if getattr(current_user, "must_change_password", False):
                allowed_force_change_endpoints = {
                    "auth.forced_password_change",
                    "auth.logout",
                    "static",
                }
                if endpoint not in allowed_force_change_endpoints:
                    session["force_password_change_user_id"] = str(current_user.id)
                    session["force_password_change_email"] = current_user.email
                    return redirect(url_for("auth.forced_password_change"))

            if endpoint in auth_guest_only_endpoints:
                return redirect("/dashboard")

            if endpoint.startswith("public."):
                return None

            if not current_user.is_verified and endpoint not in auth_allowed_for_unverified:
                return redirect(url_for("auth.verify_pending", email=current_user.email))

        return None

    return app
