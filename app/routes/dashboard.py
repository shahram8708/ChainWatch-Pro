"""Dashboard routes for authenticated supply-chain operations views."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import aliased

from app.forms.shipment_forms import ShipmentFilterForm
from app.models.alert import Alert
from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.disruption_score import DisruptionScore
from app.models.route_recommendation import RouteRecommendation
from app.models.shipment import Shipment
from app.services import ai_service
from app.utils.decorators import login_required, org_required, verified_required


dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


RISK_RANGES = {
    "critical": (81, 100),
    "warning": (61, 80),
    "watch": (31, 60),
    "green": (0, 30),
}

PREFERENCE_SORT_MAP = {
    "drs_desc": ("disruption_risk_score", "desc"),
    "eta_asc": ("estimated_arrival", "asc"),
    "created_desc": ("created_at", "desc"),
}


def _risk_range(risk_level: str | None) -> tuple[int, int] | None:
    if not risk_level:
        return None
    return RISK_RANGES.get(risk_level.strip().lower())


def _coerce_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _resolve_sorting(sort_key: str | None, order: str | None) -> tuple[str, str]:
    sort = (sort_key or "disruption_risk_score").strip().lower()
    allowed = {
        "disruption_risk_score",
        "external_reference",
        "origin_port_code",
        "destination_port_code",
        "mode",
        "status",
        "estimated_arrival",
        "days_to_delivery",
        "created_at",
    }
    if sort not in allowed:
        sort = "disruption_risk_score"

    direction = (order or "desc").strip().lower()
    if direction not in {"asc", "desc"}:
        direction = "desc"

    return sort, direction


def _apply_sort(query, sort: str, direction: str):
    if sort == "days_to_delivery":
        expr = func.extract("epoch", Shipment.estimated_arrival - func.now())
    else:
        expr = getattr(Shipment, sort, Shipment.disruption_risk_score)

    if direction == "asc":
        query = query.order_by(expr.asc().nullslast())
    else:
        query = query.order_by(expr.desc().nullslast())

    return query.order_by(Shipment.created_at.desc())


def _monthly_otd_rate(organisation_id, year: int, month: int) -> float | None:
    row = (
        CarrierPerformance.query.with_entities(
            func.coalesce(func.sum(CarrierPerformance.on_time_count), 0),
            func.coalesce(func.sum(CarrierPerformance.total_shipments), 0),
        )
        .filter(
            CarrierPerformance.organisation_id == organisation_id,
            CarrierPerformance.period_year == year,
            CarrierPerformance.period_month == month,
        )
        .first()
    )

    if not row:
        return None

    on_time_count, total_shipments = row
    total_shipments = int(total_shipments or 0)
    if total_shipments <= 0:
        return None

    return round((float(on_time_count or 0) / total_shipments) * 100, 2)


def compute_dashboard_metrics(organisation_id) -> dict[str, Any]:
    """Compute KPI metrics used by dashboard HTML and API polling."""

    now = datetime.utcnow()
    current_year = now.year
    current_month = now.month

    if current_month == 1:
        previous_year = current_year - 1
        previous_month = 12
    else:
        previous_year = current_year
        previous_month = current_month - 1

    active_shipments = (
        Shipment.query.filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(["in_transit", "pending"]),
        ).count()
    )

    critical_count = (
        Alert.query.filter(
            Alert.organisation_id == organisation_id,
            Alert.severity == "critical",
            Alert.is_acknowledged.is_(False),
        ).count()
    )

    warning_count = (
        Alert.query.filter(
            Alert.organisation_id == organisation_id,
            Alert.severity == "warning",
            Alert.is_acknowledged.is_(False),
        ).count()
    )

    watch_count = (
        Alert.query.filter(
            Alert.organisation_id == organisation_id,
            Alert.severity == "watch",
            Alert.is_acknowledged.is_(False),
        ).count()
    )

    current_month_otd = _monthly_otd_rate(organisation_id, current_year, current_month)
    previous_month_otd = _monthly_otd_rate(organisation_id, previous_year, previous_month)

    if current_month_otd is None or previous_month_otd is None:
        otd_trend = "neutral"
    elif current_month_otd > previous_month_otd:
        otd_trend = "up"
    elif current_month_otd < previous_month_otd:
        otd_trend = "down"
    else:
        otd_trend = "neutral"

    return {
        "active_shipments": active_shipments,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "watch_count": watch_count,
        "otd_rate": current_month_otd,
        "otd_trend": otd_trend,
    }


def _build_shipment_query(organisation_id):
    latest_score_subquery = (
        DisruptionScore.query.with_entities(
            DisruptionScore.shipment_id.label("shipment_id"),
            func.max(DisruptionScore.computed_at).label("latest_computed_at"),
        )
        .group_by(DisruptionScore.shipment_id)
        .subquery()
    )

    latest_score = aliased(DisruptionScore)

    query = (
        Shipment.query.with_entities(
            Shipment,
            Carrier.name.label("carrier_name"),
            latest_score.drs_total.label("latest_drs_total"),
            latest_score.computed_at.label("latest_drs_computed_at"),
        )
        .outerjoin(Carrier, Carrier.id == Shipment.carrier_id)
        .outerjoin(
            latest_score_subquery,
            latest_score_subquery.c.shipment_id == Shipment.id,
        )
        .outerjoin(
            latest_score,
            and_(
                latest_score.shipment_id == Shipment.id,
                latest_score.computed_at == latest_score_subquery.c.latest_computed_at,
            ),
        )
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
        )
    )

    return query


@dashboard_bp.before_request
@login_required
@verified_required
@org_required
def _dashboard_guards():
    """Apply auth and organisation access controls to dashboard routes."""


@dashboard_bp.get("")
def index():
    """Render the main dashboard with KPI cards, risk table, alerts, and map widgets."""

    organisation_id = current_user.organisation_id
    metrics = compute_dashboard_metrics(organisation_id)

    preferences = {}
    org_profile_data = getattr(current_user.organisation, "org_profile_data", {}) or {}
    if isinstance(org_profile_data, dict):
        preferences = org_profile_data.get("dashboard_preferences", {}) or {}

    form = ShipmentFilterForm(request.args)

    carrier_rows = (
        Carrier.query.join(Shipment, Shipment.carrier_id == Carrier.id)
        .filter(Shipment.organisation_id == organisation_id)
        .distinct(Carrier.id)
        .order_by(Carrier.name.asc())
        .all()
    )
    form.carrier_id.choices = [("", "All Carriers")]
    form.carrier_id.choices.extend((str(carrier.id), carrier.name) for carrier in carrier_rows)

    preferred_mode = (preferences.get("default_mode_filter") or "all").strip().lower()
    preferred_risk = (preferences.get("default_risk_filter") or "all").strip().lower()
    preferred_sort = (preferences.get("default_sort") or "drs_desc").strip().lower()

    status_filter = (request.args.get("status") or "").strip()
    carrier_filter = (request.args.get("carrier_id") or "").strip()

    mode_filter = (request.args.get("mode") or "").strip()
    if not mode_filter and preferred_mode and preferred_mode != "all":
        mode_filter = preferred_mode

    risk_filter = (request.args.get("risk_level") or request.args.get("risk") or "").strip().lower()
    if not risk_filter and preferred_risk == "critical_only":
        risk_filter = "critical"
    elif not risk_filter and preferred_risk == "critical_warning":
        risk_filter = "critical_warning"

    search_query = (request.args.get("q") or "").strip()

    if request.args.get("sort") or request.args.get("order"):
        sort, order = _resolve_sorting(request.args.get("sort"), request.args.get("order"))
    else:
        sort, order = PREFERENCE_SORT_MAP.get(preferred_sort, ("disruption_risk_score", "desc"))

    shipment_query = _build_shipment_query(organisation_id)

    if status_filter:
        shipment_query = shipment_query.filter(Shipment.status == status_filter)

    if carrier_filter:
        carrier_uuid = _coerce_uuid(carrier_filter)
        if carrier_uuid:
            shipment_query = shipment_query.filter(Shipment.carrier_id == carrier_uuid)

    if mode_filter:
        shipment_query = shipment_query.filter(Shipment.mode == mode_filter)

    risk_range = _risk_range(risk_filter)
    if risk_filter == "critical_warning":
        shipment_query = shipment_query.filter(Shipment.disruption_risk_score >= 61)
    elif risk_range:
        shipment_query = shipment_query.filter(
            Shipment.disruption_risk_score >= risk_range[0],
            Shipment.disruption_risk_score <= risk_range[1],
        )

    if search_query:
        like_term = f"%{search_query}%"
        shipment_query = shipment_query.filter(
            or_(
                Shipment.external_reference.ilike(like_term),
                Shipment.customer_name.ilike(like_term),
            )
        )

    shipment_query = _apply_sort(shipment_query, sort, order)

    form.q.data = search_query
    form.status.data = status_filter
    form.carrier_id.data = carrier_filter
    form.mode.data = mode_filter
    if risk_filter in {"critical", "warning", "watch", "green"}:
        form.risk.data = risk_filter
    else:
        form.risk.data = ""

    page = request.args.get("page", 1, type=int)
    page = max(page, 1)
    shipments = shipment_query.paginate(page=page, per_page=25, error_out=False)

    severity_order = case(
        (Alert.severity == "critical", 1),
        (Alert.severity == "warning", 2),
        (Alert.severity == "watch", 3),
        else_=4,
    )

    recent_alerts = (
        Alert.query.outerjoin(Shipment, Shipment.id == Alert.shipment_id)
        .filter(
            Alert.organisation_id == organisation_id,
            Alert.is_acknowledged.is_(False),
        )
        .order_by(severity_order.asc(), Alert.created_at.desc())
        .limit(10)
        .all()
    )

    default_cards = ["active_shipments", "critical_alerts", "warning_alerts", "otd_rate"]
    enabled_cards = preferences.get("kpi_cards") or default_cards

    filter_params = {
        "q": search_query,
        "status": status_filter,
        "carrier_id": carrier_filter,
        "mode": mode_filter,
        "risk": risk_filter,
        "sort": sort,
        "order": order,
    }

    return render_template(
        "app/dashboard/index.html",
        form=form,
        shipments=shipments,
        metrics=metrics,
        recent_alerts=recent_alerts,
        carriers=carrier_rows,
        dashboard_preferences=preferences,
        enabled_cards=enabled_cards,
        filters=filter_params,
        sort=sort,
        order=order,
    )


def _week_start(dt: datetime) -> datetime:
    return dt - timedelta(days=dt.weekday())


def _fleet_otd_rate(organisation_id) -> float:
    now = datetime.utcnow()
    cutoff_year = now.year
    cutoff_month = now.month - 2
    while cutoff_month <= 0:
        cutoff_month += 12
        cutoff_year -= 1
    cutoff_month_value = (cutoff_year * 100) + cutoff_month

    rows = (
        CarrierPerformance.query.with_entities(
            func.coalesce(func.sum(CarrierPerformance.on_time_count), 0),
            func.coalesce(func.sum(CarrierPerformance.total_shipments), 0),
        )
        .filter(
            CarrierPerformance.organisation_id == organisation_id,
            ((CarrierPerformance.period_year * 100) + CarrierPerformance.period_month) >= cutoff_month_value,
        )
        .first()
    )

    if not rows:
        return 0.0

    on_time, total = rows
    total = int(total or 0)
    if total <= 0:
        return 0.0
    return round((float(on_time or 0) / total) * 100.0, 2)


@dashboard_bp.get("/executive")
def executive_dashboard():
    """Backward-compatible alias that redirects to the canonical executive dashboard."""

    return redirect(url_for("executive.executive_dashboard"))
