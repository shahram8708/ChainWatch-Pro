"""Application entrypoint for ChainWatch Pro."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import unquote

from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app import create_app
from app.extensions import db


logger = logging.getLogger(__name__)
project_root = Path(__file__).resolve().parent
load_dotenv(project_root / ".env", override=False)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _ensure_postgres_database_exists(database_uri: str) -> None:
    """Create the PostgreSQL database if it does not exist yet."""

    url = make_url(database_uri)
    if not url.drivername.startswith("postgresql"):
        return

    database_name = url.database
    if not database_name:
        return

    admin_database = os.getenv("POSTGRES_MAINTENANCE_DB", "postgres")
    admin_url = url.set(database=admin_database)
    engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", future=True)

    try:
        with engine.connect() as connection:
            db_exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            ).scalar()

            if not db_exists:
                safe_db_name = database_name.replace('"', '""')
                connection.execute(text(f'CREATE DATABASE "{safe_db_name}"'))
                logger.info("Created PostgreSQL database '%s'.", database_name)
    finally:
        engine.dispose()


def _ensure_sqlite_database_path_exists(database_uri: str) -> None:
    """Create parent directories for file-based SQLite URIs."""

    url = make_url(database_uri)
    if not url.drivername.startswith("sqlite"):
        return

    database_name = url.database or ""
    if not database_name or database_name == ":memory:":
        return

    decoded_path = unquote(database_name)
    if decoded_path.startswith("/") and len(decoded_path) > 2 and decoded_path[2] == ":":
        decoded_path = decoded_path.lstrip("/")

    db_file_path = Path(decoded_path).expanduser()
    if not db_file_path.is_absolute():
        db_file_path = (project_root / db_file_path).resolve()
    db_file_path.parent.mkdir(parents=True, exist_ok=True)


def _bootstrap_database() -> None:
    """Ensure the configured database and tables exist for local startup."""

    auto_create_db = _as_bool(os.getenv("AUTO_CREATE_DB"), default=config_name == "development")
    if not auto_create_db:
        return

    database_uri = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    if not database_uri:
        logger.warning("SQLALCHEMY_DATABASE_URI is empty. Database bootstrap skipped.")
        return

    try:
        _ensure_sqlite_database_path_exists(database_uri)
        _ensure_postgres_database_exists(database_uri)
    except Exception:
        logger.exception("Failed while ensuring database target exists.")

    try:
        with app.app_context():
            db.create_all()
            logger.info("Database tables verified/created successfully.")
    except Exception:
        logger.exception("Failed while creating database tables.")


config_name = os.getenv("FLASK_ENV", "development").strip().lower()
app = create_app(config_name=config_name)
_bootstrap_database()


if __name__ == "__main__":
    app.run(debug=bool(app.config.get("DEBUG", False)), use_reloader=False)
