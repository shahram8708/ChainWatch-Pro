"""Blueprint package for application routes."""

from app.routes.alerts import alerts_bp
from app.routes.api import api_bp
from app.routes.auth import auth_bp
from app.routes.carrier_intel import carrier_intel_bp
from app.routes.dashboard import dashboard_bp
from app.routes.executive import executive_bp
from app.routes.onboarding import onboarding_bp
from app.routes.optimizer import optimizer_bp
from app.routes.planner import planner_bp
from app.routes.public import public_bp
from app.routes.reports import reports_bp
from app.routes.risk_map import risk_map_bp
from app.routes.audit import audit_bp
from app.routes.settings import settings_bp
from app.routes.shipments import shipments_bp
from app.routes.superadmin import superadmin_bp
from app.routes.webhooks import webhooks_bp

__all__ = [
    "auth_bp",
    "public_bp",
    "onboarding_bp",
    "dashboard_bp",
    "shipments_bp",
    "optimizer_bp",
    "alerts_bp",
    "api_bp",
    "carrier_intel_bp",
    "planner_bp",
    "risk_map_bp",
    "executive_bp",
    "reports_bp",
    "audit_bp",
    "settings_bp",
    "superadmin_bp",
    "webhooks_bp",
]
