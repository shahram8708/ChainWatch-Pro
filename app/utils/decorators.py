"""Authentication, authorization, and organisation guard decorators."""

from __future__ import annotations

from functools import wraps

from flask import abort, flash, redirect, request, url_for
from flask_login import current_user

from app.models.organisation import Organisation
from app.utils.helpers import is_feature_enabled


def login_required(view_func):
    """Require an authenticated user and preserve the next URL."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access ChainWatch Pro.", "warning")
            return redirect(url_for("auth.login", next=request.url))
        return view_func(*args, **kwargs)

    return wrapped


def role_required(*roles):
    """Require one of the provided roles to access the route."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please log in to access ChainWatch Pro.", "warning")
                return redirect(url_for("auth.login", next=request.url))

            if current_user.role not in roles:
                abort(403)

            return view_func(*args, **kwargs)

        return wrapped

    return decorator


def org_required(view_func):
    """Require a valid, non-expired organisation for the current user."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access ChainWatch Pro.", "warning")
            return redirect(url_for("auth.login", next=request.url))

        if not current_user.organisation_id:
            abort(403)

        organisation = Organisation.query.filter_by(id=current_user.organisation_id).first()
        if organisation is None:
            abort(403)

        if not bool(getattr(organisation, "is_active", True)):
            flash("Your organisation workspace is currently suspended. Contact support.", "danger")
            return redirect(url_for("auth.logout"))

        if organisation.subscription_status in {"expired", "cancelled"}:
            flash("Your subscription has expired. Please renew to continue.", "warning")
            return redirect("/settings/billing")

        return view_func(*args, **kwargs)

    return wrapped


def verified_required(view_func):
    """Require a verified account before route access."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            flash("Please log in to access ChainWatch Pro.", "warning")
            return redirect(url_for("auth.login", next=request.url))

        if not current_user.is_verified:
            flash("Please verify your email address to continue.", "warning")
            return redirect(url_for("auth.verify_pending", email=current_user.email))

        return view_func(*args, **kwargs)

    return wrapped


def superadmin_required(view_func):
    """Allow only platform SuperAdmin users to access protected routes."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login"))

        if current_user.role != "superadmin":
            abort(404)

        return view_func(*args, **kwargs)

    return wrapped


def feature_required(feature_flag_name: str):
    """Restrict route access when a feature is disabled for the current organisation."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                flash("Please log in to access ChainWatch Pro.", "warning")
                return redirect(url_for("auth.login", next=request.url))

            organisation = getattr(current_user, "organisation", None)
            if organisation is None:
                abort(403)

            if not is_feature_enabled(feature_flag_name, organisation):
                abort(404)

            return view_func(*args, **kwargs)

        return wrapped

    return decorator
