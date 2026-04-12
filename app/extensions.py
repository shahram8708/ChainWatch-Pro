"""Flask extension instances for ChainWatch Pro."""

from __future__ import annotations

import uuid

import redis
from flask_login import LoginManager
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_mail import Mail
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect


db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
mail = Mail()
limiter = Limiter(key_func=get_remote_address)
csrf = CSRFProtect()
redis_client: redis.Redis | None = None

login_manager.login_view = "auth.login"
login_manager.login_message = "Please log in to access ChainWatch Pro."
login_manager.login_message_category = "warning"


def init_redis(app) -> redis.Redis | None:
    """Initialize and cache a Redis client instance for shared app usage."""

    global redis_client

    if not bool(app.config.get("USE_REDIS", False)):
        redis_client = None
        app.extensions["redis_client"] = None
        return None

    redis_url = app.config.get("REDIS_URL")
    if not redis_url:
        redis_client = None
        app.extensions["redis_client"] = None
        return None

    try:
        redis_client = redis.from_url(redis_url, decode_responses=True)
        app.extensions["redis_client"] = redis_client
        return redis_client
    except Exception:
        redis_client = None
        app.extensions["redis_client"] = None
        return None


def get_redis_client() -> redis.Redis | None:
    """Return the initialized Redis client if available."""

    return redis_client


@login_manager.user_loader
def load_user(user_id: str):
    """Load a user from the database using a UUID user ID."""

    try:
        parsed_id = uuid.UUID(user_id)
    except (TypeError, ValueError):
        return None

    from app.models.user import User

    return User.query.filter_by(id=parsed_id).first()
