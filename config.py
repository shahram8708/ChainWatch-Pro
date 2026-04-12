"""Application configuration for ChainWatch Pro."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from dotenv import load_dotenv
from sqlalchemy.engine import make_url

basedir = os.path.abspath(os.path.dirname(__file__))
project_root = Path(__file__).resolve().parent
load_dotenv(project_root / ".env", override=False)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _resolve_sqlite_uri(raw_value: str | None, default_relative_path: str) -> str:
    candidate = (raw_value or default_relative_path).strip()
    if "://" in candidate:
        try:
            parsed = make_url(candidate)
        except Exception:
            return candidate

        if not parsed.drivername.startswith("sqlite"):
            return candidate

        sqlite_target = parsed.database or ""
        if not sqlite_target or sqlite_target == ":memory:":
            return candidate
    else:
        sqlite_target = candidate

    decoded_target = unquote(sqlite_target)
    if decoded_target.startswith("/") and len(decoded_target) > 2 and decoded_target[2] == ":":
        decoded_target = decoded_target.lstrip("/")

    db_path = Path(decoded_target).expanduser()
    if not db_path.is_absolute():
        db_path = (project_root / db_path).resolve()

    return f"sqlite:///{db_path.as_posix()}"


def _normalize_mail_server(raw_value: str | None, default_value: str = "") -> str:
    candidate = (raw_value or default_value or "").strip()
    if not candidate:
        return ""

    if "://" in candidate:
        parsed = urlparse(candidate)
        if parsed.hostname:
            return parsed.hostname.strip()

    return candidate


DEFAULT_POSTGRES_URI = "postgresql://chainwatch_user:yourpassword@localhost:5432/chainwatchpro"
DEFAULT_DEV_SQLITE_URI = _resolve_sqlite_uri(
    os.getenv("DEV_DATABASE_URL") or os.getenv("DEV_SQLITE_PATH"),
    "instance/chainwatchpro_dev.sqlite3",
)
DEFAULT_TEST_SQLITE_URI = _resolve_sqlite_uri(
    os.getenv("TEST_DATABASE_URL") or os.getenv("TEST_SQLITE_PATH"),
    "instance/chainwatchpro_test.sqlite3",
)


class BaseConfig:
    """Base configuration shared across environments."""

    ENV_NAME = "base"

    SECRET_KEY = os.getenv("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_POSTGRES_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
    }

    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600

    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)

    MAIL_SERVER = _normalize_mail_server(
        os.getenv("MAIL_SERVER"),
        "smtp.yourprovider.com",
    )
    MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
    MAIL_USE_TLS = _as_bool(os.getenv("MAIL_USE_TLS"), True)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", "noreply@chainwatchpro.com")

    RATELIMIT_DEFAULT = "200 per day;50 per hour"
    USE_REDIS = _as_bool(os.getenv("USE_REDIS"), default=True)
    CELERY_ENABLED = _as_bool(os.getenv("CELERY_ENABLED"), default=USE_REDIS)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0") if USE_REDIS else ""
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL) if CELERY_ENABLED else ""
    RATELIMIT_STORAGE_URI = REDIS_URL if USE_REDIS and REDIS_URL else "memory://"

    ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY", "")

    RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
    RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
    RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
    RAZORPAY_PLAN_STARTER_MONTHLY = os.getenv("RAZORPAY_PLAN_STARTER_MONTHLY", "")
    RAZORPAY_PLAN_STARTER_ANNUAL = os.getenv("RAZORPAY_PLAN_STARTER_ANNUAL", "")
    RAZORPAY_PLAN_PROFESSIONAL_MONTHLY = os.getenv("RAZORPAY_PLAN_PROFESSIONAL_MONTHLY", "")
    RAZORPAY_PLAN_PROFESSIONAL_ANNUAL = os.getenv("RAZORPAY_PLAN_PROFESSIONAL_ANNUAL", "")
    RAZORPAY_PLAN_ENTERPRISE_MONTHLY = os.getenv("RAZORPAY_PLAN_ENTERPRISE_MONTHLY", "")
    RAZORPAY_PLAN_ENTERPRISE_ANNUAL = os.getenv("RAZORPAY_PLAN_ENTERPRISE_ANNUAL", "")

    REPORT_OUTPUT_DIR = os.path.join(basedir, "static", "reports")
    SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "support@chainwatchpro.com")

    SUPERADMIN_URL_PREFIX = os.environ.get("SUPERADMIN_URL_PREFIX", "/sa-panel")
    SUPERADMIN_EMAIL = os.environ.get("SUPERADMIN_EMAIL", "superadmin@chainwatchpro.internal")
    SUPERADMIN_PASSWORD = os.environ.get("SUPERADMIN_PASSWORD", "ChainWatch@SuperAdmin2026!")
    SUPERADMIN_SESSION_TIMEOUT_MINUTES = int(os.environ.get("SUPERADMIN_SESSION_TIMEOUT", "30"))
    SUPERADMIN_FIRST_NAME = os.environ.get("SUPERADMIN_FIRST_NAME", "Platform")
    SUPERADMIN_LAST_NAME = os.environ.get("SUPERADMIN_LAST_NAME", "Administrator")

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
    OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

    PROFILE_PHOTO_UPLOAD_DIR = os.getenv(
        "PROFILE_PHOTO_UPLOAD_DIR",
        str(project_root / "static" / "uploads" / "profile_photos"),
    )
    PROFILE_PHOTO_MAX_BYTES = int(os.getenv("PROFILE_PHOTO_MAX_BYTES", str(2 * 1024 * 1024)))

    AI_CACHE_TTL_CARRIER_COMMENTARY = int(os.getenv("AI_CACHE_TTL_CARRIER_COMMENTARY", "86400"))
    AI_CACHE_TTL_SHIPMENT_DISRUPTION_SUMMARY = int(os.getenv("AI_CACHE_TTL_SHIPMENT_DISRUPTION_SUMMARY", "900"))
    AI_CACHE_TTL_SIMULATION_NARRATIVE = int(os.getenv("AI_CACHE_TTL_SIMULATION_NARRATIVE", "3600"))
    AI_CACHE_TTL_EXECUTIVE_BRIEF = int(os.getenv("AI_CACHE_TTL_EXECUTIVE_BRIEF", "43200"))
    AI_CACHE_TTL_ALERT_DESCRIPTION = int(os.getenv("AI_CACHE_TTL_ALERT_DESCRIPTION", "0"))
    AI_CACHE_TTL_ROUTE_EVENT_RISK = int(os.getenv("AI_CACHE_TTL_ROUTE_EVENT_RISK", "1800"))
    AI_CACHE_TTL_PORT_CONGESTION_ANALYSIS = int(os.getenv("AI_CACHE_TTL_PORT_CONGESTION_ANALYSIS", "3600"))

    TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")

    TESTING = False


class DevelopmentConfig(BaseConfig):
    """Configuration for local development."""

    ENV_NAME = "development"
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    SQLALCHEMY_DATABASE_URI = DEFAULT_DEV_SQLITE_URI

    USE_REDIS = _as_bool(os.getenv("USE_REDIS"), default=False)
    CELERY_ENABLED = _as_bool(os.getenv("CELERY_ENABLED"), default=False)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0") if USE_REDIS else ""
    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL) if CELERY_ENABLED else ""
    RATELIMIT_STORAGE_URI = REDIS_URL if USE_REDIS and REDIS_URL else "memory://"


class ProductionConfig(BaseConfig):
    """Configuration for production."""

    ENV_NAME = "production"
    DEBUG = False
    SESSION_COOKIE_SECURE = True
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL", DEFAULT_POSTGRES_URI)

    USE_REDIS = _as_bool(os.getenv("USE_REDIS"), default=True)
    CELERY_ENABLED = _as_bool(os.getenv("CELERY_ENABLED"), default=True)
    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0") if USE_REDIS else ""

    if CELERY_ENABLED and not REDIS_URL:
        REDIS_URL = "redis://localhost:6379/0"

    CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL) if CELERY_ENABLED else ""
    RATELIMIT_STORAGE_URI = REDIS_URL if USE_REDIS and REDIS_URL else "memory://"


class TestingConfig(BaseConfig):
    """Configuration for tests."""

    ENV_NAME = "testing"
    TESTING = True
    WTF_CSRF_ENABLED = False
    SESSION_COOKIE_SECURE = False
    SQLALCHEMY_DATABASE_URI = DEFAULT_TEST_SQLITE_URI

    USE_REDIS = False
    CELERY_ENABLED = False
    REDIS_URL = ""
    CELERY_BROKER_URL = ""
    RATELIMIT_STORAGE_URI = "memory://"


config_by_name = {
    "development": DevelopmentConfig,
    "dev": DevelopmentConfig,
    "production": ProductionConfig,
    "prod": ProductionConfig,
    "testing": TestingConfig,
    "test": TestingConfig,
}


def get_config(config_name: str):
    """Return the configuration class for the provided environment name."""

    normalized = (config_name or "development").strip().lower()
    return config_by_name.get(normalized, DevelopmentConfig)
