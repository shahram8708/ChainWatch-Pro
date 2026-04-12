"""Shipment routes for list, detail, CRUD, CSV import/export, and route decisions."""

from __future__ import annotations

import csv
import io
import logging
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user
from sqlalchemy import and_, case, func, insert, or_

from app.extensions import db
from app.forms.shipment_forms import (
    MODE_CHOICES,
    RouteDecisionForm,
    ShipmentCreateForm,
    ShipmentEditForm,
    ShipmentFilterForm,
    ShipmentImportForm,
)
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.disruption_score import DisruptionScore
from app.models.route_recommendation import RouteRecommendation
from app.models.shipment import Shipment
from app.services import ai_service
from app.services import razorpay_service
from app.services.disruption_engine import PORT_COORDINATES as DRS_PORT_COORDINATES
from app.utils.decorators import login_required, role_required, verified_required
from werkzeug.utils import secure_filename


shipments_bp = Blueprint("shipments", __name__, url_prefix="/shipments")
logger = logging.getLogger(__name__)


RISK_RANGES = {
    "critical": (81, 100),
    "warning": (61, 80),
    "watch": (31, 60),
    "green": (0, 30),
}

SHIPMENT_MODE_TO_PERFORMANCE_MODE = {
    "ocean_fcl": "ocean",
    "ocean_lcl": "ocean",
    "air": "air",
    "road": "road",
    "rail": "rail",
    "multimodal": "multimodal",
}

MODE_CHOICES_SET = {value for value, _ in MODE_CHOICES}

PORT_COORDINATES = {
    **{code: [float(coords[0]), float(coords[1])] for code, coords in DRS_PORT_COORDINATES.items()},
    # Keep additional aliases frequently used in manual shipment entry.
    "AEJEA": [25.01, 55.06],
    "INBLR": [13.20, 77.71],
    "INDEL": [28.56, 77.10],
}


@shipments_bp.before_request
@login_required
@verified_required
def _shipment_guards():
    """Apply baseline auth guards to shipment routes."""


def _coerce_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _risk_level_for_score(score: float | Decimal | None) -> str:
    numeric = float(score or 0)
    if numeric >= 81:
        return "critical"
    if numeric >= 61:
        return "warning"
    if numeric >= 31:
        return "watch"
    return "green"


def _risk_range(risk_level: str | None):
    if not risk_level:
        return None
    return RISK_RANGES.get(risk_level.strip().lower())


def _carrier_choices_for_org(organisation_id) -> list[tuple[str, str]]:
    carriers = (
        Carrier.query.outerjoin(Shipment, Shipment.carrier_id == Carrier.id)
        .filter(
            or_(
                Carrier.is_global_carrier.is_(True),
                Shipment.organisation_id == organisation_id,
            )
        )
        .distinct(Carrier.id)
        .order_by(Carrier.name.asc())
        .all()
    )

    choices = [("", "Select Carrier")]
    choices.extend((str(carrier.id), carrier.name) for carrier in carriers)
    return choices


def _filter_carrier_rows(organisation_id) -> list[Carrier]:
    return (
        Carrier.query.join(Shipment, Shipment.carrier_id == Carrier.id)
        .filter(Shipment.organisation_id == organisation_id)
        .distinct(Carrier.id)
        .order_by(Carrier.name.asc())
        .all()
    )


def _resolve_sort(sort_key: str | None, order: str | None) -> tuple[str, str]:
    sort = (sort_key or "disruption_risk_score").strip().lower()
    allowed = {
        "disruption_risk_score",
        "external_reference",
        "carrier_name",
        "origin_port_code",
        "destination_port_code",
        "mode",
        "status",
        "estimated_arrival",
        "cargo_value_inr",
        "created_at",
    }
    if sort not in allowed:
        sort = "disruption_risk_score"

    direction = (order or "desc").strip().lower()
    if direction not in {"asc", "desc"}:
        direction = "desc"

    return sort, direction


def _apply_sort(query, sort: str, direction: str):
    if sort == "carrier_name":
        expr = Carrier.name
    else:
        expr = getattr(Shipment, sort, Shipment.disruption_risk_score)

    if direction == "asc":
        return query.order_by(expr.asc().nullslast(), Shipment.created_at.desc())
    return query.order_by(expr.desc().nullslast(), Shipment.created_at.desc())


def _build_shipments_base_query(organisation_id):
    recent_cutoff = datetime.utcnow() - timedelta(days=30)

    query = (
        Shipment.query.with_entities(Shipment, Carrier.name.label("carrier_name"))
        .outerjoin(Carrier, Carrier.id == Shipment.carrier_id)
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status != "cancelled",
            or_(
                Shipment.status != "delivered",
                Shipment.updated_at >= recent_cutoff,
                Shipment.actual_arrival >= recent_cutoff,
            ),
        )
    )

    return query


def _apply_shipments_filters(query, params: dict[str, Any]):
    status_value = (params.get("status") or "").strip()
    carrier_value = (params.get("carrier_id") or "").strip()
    mode_value = (params.get("mode") or "").strip()
    risk_value = (params.get("risk") or "").strip().lower()
    search_term = (params.get("q") or "").strip()

    if status_value:
        query = query.filter(Shipment.status == status_value)

    if carrier_value:
        carrier_id = _coerce_uuid(carrier_value)
        if carrier_id:
            query = query.filter(Shipment.carrier_id == carrier_id)

    if mode_value:
        query = query.filter(Shipment.mode == mode_value)

    risk_range = _risk_range(risk_value)
    if risk_value == "critical_warning":
        query = query.filter(Shipment.disruption_risk_score >= 61)
    elif risk_range:
        query = query.filter(
            Shipment.disruption_risk_score >= risk_range[0],
            Shipment.disruption_risk_score <= risk_range[1],
        )

    if search_term:
        like_term = f"%{search_term}%"
        query = query.filter(
            or_(
                Shipment.external_reference.ilike(like_term),
                Shipment.customer_name.ilike(like_term),
                Shipment.origin_port_code.ilike(like_term),
                Shipment.destination_port_code.ilike(like_term),
            )
        )

    return query


def _shipments_stats(organisation_id) -> dict[str, int]:
    delivered_cutoff = datetime.utcnow() - timedelta(days=30)
    row = (
        db.session.query(
            func.coalesce(
                func.sum(
                    case(
                        (and_(Shipment.is_archived.is_(False), Shipment.status.in_(["pending", "in_transit", "delayed", "at_customs"])), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("total_active"),
            func.coalesce(
                func.sum(
                    case(
                        (and_(Shipment.is_archived.is_(False), Shipment.status == "in_transit"), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("in_transit"),
            func.coalesce(
                func.sum(
                    case(
                        (and_(Shipment.is_archived.is_(False), Shipment.status == "delayed"), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("delayed"),
            func.coalesce(
                func.sum(
                    case(
                        (
                            and_(
                                Shipment.is_archived.is_(False),
                                Shipment.status == "delivered",
                                Shipment.updated_at >= delivered_cutoff,
                            ),
                            1,
                        ),
                        else_=0,
                    )
                ),
                0,
            ).label("delivered_30d"),
        )
        .filter(Shipment.organisation_id == organisation_id)
        .one()
    )

    return {
        "total_active": int(row.total_active or 0),
        "in_transit": int(row.in_transit or 0),
        "delayed": int(row.delayed or 0),
        "delivered_30d": int(row.delivered_30d or 0),
    }


def _build_milestones(shipment: Shipment) -> list[dict[str, Any]]:
    milestones: list[dict[str, Any]] = []

    milestones.append(
        {
            "name": "Shipment Created",
            "planned_datetime": shipment.created_at,
            "actual_datetime": shipment.created_at,
            "status": "completed_on_time",
            "delta_text": "On time",
        }
    )

    departure_status = "upcoming"
    if shipment.actual_departure:
        if shipment.estimated_departure and shipment.actual_departure <= shipment.estimated_departure:
            departure_status = "completed_on_time"
            departure_delta = "On time"
        else:
            departure_status = "completed_late"
            if shipment.estimated_departure:
                late = shipment.actual_departure - shipment.estimated_departure
                departure_delta = f"+{int(late.total_seconds() // 3600)}h late"
            else:
                departure_delta = "Completed"
    elif shipment.status in {"in_transit", "delayed", "at_customs", "delivered"}:
        departure_status = "in_progress"
        departure_delta = "In progress"
    else:
        departure_delta = "Expected"

    milestones.append(
        {
            "name": "Departure",
            "planned_datetime": shipment.estimated_departure,
            "actual_datetime": shipment.actual_departure,
            "status": departure_status,
            "delta_text": departure_delta,
        }
    )

    if shipment.current_location_name:
        milestones.append(
            {
                "name": "Transit Hub Arrival",
                "planned_datetime": None,
                "actual_datetime": None,
                "status": "in_progress" if shipment.status in {"in_transit", "delayed"} else "completed_on_time",
                "delta_text": shipment.current_location_name,
            }
        )

    if shipment.status in {"at_customs", "delivered"}:
        milestones.append(
            {
                "name": "Customs Clearance",
                "planned_datetime": None,
                "actual_datetime": shipment.updated_at if shipment.status == "delivered" else None,
                "status": "in_progress" if shipment.status == "at_customs" else "completed_on_time",
                "delta_text": "Processing" if shipment.status == "at_customs" else "Cleared",
            }
        )

    arrival_status = "upcoming"
    if shipment.actual_arrival:
        if shipment.estimated_arrival and shipment.actual_arrival <= shipment.estimated_arrival:
            arrival_status = "completed_on_time"
            arrival_delta = "On time"
        else:
            arrival_status = "completed_late"
            if shipment.estimated_arrival:
                late_delta = shipment.actual_arrival - shipment.estimated_arrival
                arrival_delta = f"+{int(late_delta.total_seconds() // 3600)}h late"
            else:
                arrival_delta = "Completed"
    elif shipment.status in {"in_transit", "delayed", "at_customs"}:
        if shipment.estimated_arrival and shipment.estimated_arrival < datetime.utcnow():
            arrival_status = "missed"
            arrival_delta = "Missed"
        else:
            arrival_status = "in_progress"
            arrival_delta = "Expected"
    else:
        arrival_delta = "Expected"

    milestones.append(
        {
            "name": "Final Delivery",
            "planned_datetime": shipment.estimated_arrival,
            "actual_datetime": shipment.actual_arrival,
            "status": arrival_status,
            "delta_text": arrival_delta,
        }
    )

    return milestones


def _shipment_or_404(shipment_id: uuid.UUID) -> Shipment:
    shipment = Shipment.query.filter_by(id=shipment_id).first_or_404()
    if shipment.organisation_id != current_user.organisation_id:
        abort(403)
    return shipment


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    candidate = str(value).strip()
    if not candidate:
        return None

    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(tz=None).replace(tzinfo=None)
    return parsed


def _resolve_carrier_mode(shipment_mode: str) -> str:
    return SHIPMENT_MODE_TO_PERFORMANCE_MODE.get(shipment_mode, "multimodal")


def _port_coords(port_code: str | None):
    if not port_code:
        return None
    normalized = (port_code or "").upper().strip()
    if len(normalized) < 5:
        normalized = normalized.ljust(5, "X")
    return PORT_COORDINATES.get(normalized)


@shipments_bp.route("", methods=["GET", "POST"])
def list_shipments():
    """Render shipments list with filters and support CSV export."""

    organisation_id = current_user.organisation_id

    source_args = request.args if request.method == "GET" else request.form
    form = ShipmentFilterForm(source_args)

    carriers = _filter_carrier_rows(organisation_id)
    form.carrier_id.choices = [("", "All Carriers")]
    form.carrier_id.choices.extend((str(carrier.id), carrier.name) for carrier in carriers)

    sort, order = _resolve_sort(source_args.get("sort"), source_args.get("order"))

    query = _build_shipments_base_query(organisation_id)
    query = _apply_shipments_filters(query, source_args)
    query = _apply_sort(query, sort, order)

    export_requested = (source_args.get("export") or "").strip().lower() == "csv"

    selected_ids_csv = (request.form.get("selected_ids") or "").strip()
    selected_ids: list[uuid.UUID] = []
    if selected_ids_csv:
        for raw_id in selected_ids_csv.split(","):
            parsed = _coerce_uuid(raw_id.strip())
            if parsed:
                selected_ids.append(parsed)

    if export_requested:
        export_query = query
        if selected_ids:
            export_query = export_query.filter(Shipment.id.in_(selected_ids))

        export_rows = export_query.all()

        csv_stream = io.StringIO()
        writer = csv.writer(csv_stream)
        writer.writerow(
            [
                "Shipment ID",
                "External Reference",
                "Carrier",
                "Mode",
                "Origin",
                "Destination",
                "Status",
                "DRS Score",
                "Risk Level",
                "Estimated Departure",
                "Estimated Arrival",
                "Cargo Value (₹)",
                "Customer Name",
                "Created At",
            ]
        )

        for shipment, carrier_name in export_rows:
            writer.writerow(
                [
                    str(shipment.id),
                    shipment.external_reference or "",
                    carrier_name or "",
                    shipment.mode,
                    shipment.origin_port_code,
                    shipment.destination_port_code,
                    shipment.status,
                    float(shipment.disruption_risk_score or 0),
                    _risk_level_for_score(shipment.disruption_risk_score).title(),
                    shipment.estimated_departure.isoformat() if shipment.estimated_departure else "",
                    shipment.estimated_arrival.isoformat() if shipment.estimated_arrival else "",
                    float(shipment.cargo_value_inr or 0),
                    shipment.customer_name or "",
                    shipment.created_at.isoformat() if shipment.created_at else "",
                ]
            )

        today = datetime.utcnow().strftime("%Y%m%d")
        response = Response(csv_stream.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = f"attachment; filename=shipments_export_{today}.csv"

        AuditLog.log(
            db,
            event_type="shipments_exported",
            description=f"Exported {len(export_rows)} shipment rows.",
            organisation_id=organisation_id,
            actor_user=current_user,
            metadata={
                "filter_params": {
                    "q": source_args.get("q", ""),
                    "status": source_args.get("status", ""),
                    "carrier_id": source_args.get("carrier_id", ""),
                    "mode": source_args.get("mode", ""),
                    "risk": source_args.get("risk", ""),
                    "sort": sort,
                    "order": order,
                    "selected_ids": [str(item) for item in selected_ids],
                },
                "count": len(export_rows),
            },
            ip_address=request.remote_addr,
        )

        return response

    page = max(source_args.get("page", 1, type=int), 1)
    shipments = query.paginate(page=page, per_page=25, error_out=False)
    stats = _shipments_stats(organisation_id)

    filters = {
        "q": (source_args.get("q") or "").strip(),
        "status": (source_args.get("status") or "").strip(),
        "carrier_id": (source_args.get("carrier_id") or "").strip(),
        "mode": (source_args.get("mode") or "").strip(),
        "risk": (source_args.get("risk") or "").strip(),
        "sort": sort,
        "order": order,
    }

    return render_template(
        "app/shipments/list.html",
        form=form,
        shipments=shipments,
        carriers=carriers,
        filters=filters,
        sort=sort,
        order=order,
        stats=stats,
    )


@shipments_bp.route("/new", methods=["GET", "POST"])
@role_required("admin", "manager")
def new_shipment():
    """Create a new shipment for the current organisation."""

    form = ShipmentCreateForm()
    form.carrier_id.choices = _carrier_choices_for_org(current_user.organisation_id)

    if form.validate_on_submit():
        usage = razorpay_service.enforce_plan_limits(current_user.organisation, "shipments", db.session)
        if not usage.get("allowed", False):
            flash("You've reached your plan's shipment limit. Please upgrade your plan.", "danger")
            return redirect(url_for("shipments.list_shipments"))

        carrier_id = _coerce_uuid(form.carrier_id.data)

        shipment = Shipment(
            organisation_id=current_user.organisation_id,
            external_reference=(form.external_reference.data or "").strip(),
            carrier_id=carrier_id,
            mode=form.mode.data,
            origin_port_code=(form.origin_port_code.data or "").strip().upper(),
            destination_port_code=(form.destination_port_code.data or "").strip().upper(),
            origin_address=(form.origin_address.data or "").strip() or None,
            destination_address=(form.destination_address.data or "").strip() or None,
            estimated_departure=form.estimated_departure.data,
            estimated_arrival=form.estimated_arrival.data,
            cargo_value_inr=form.cargo_value_inr.data,
            customer_name=(form.customer_name.data or "").strip() or None,
            status="pending",
            disruption_risk_score=0.00,
        )

        db.session.add(shipment)
        db.session.commit()

        AuditLog.log(
            db,
            event_type="shipment_created",
            description=f"Created shipment {shipment.external_reference}.",
            organisation_id=current_user.organisation_id,
            actor_user=current_user,
            shipment_id=shipment.id,
            metadata={"shipment_id": str(shipment.id)},
            ip_address=request.remote_addr,
        )

        flash(f"Shipment {shipment.external_reference} created successfully.", "success")
        return redirect(url_for("shipments.detail", id=shipment.id))

    return render_template("app/shipments/new.html", form=form)


@shipments_bp.get("/<uuid:id>")
def detail(id: uuid.UUID):
    """Render shipment detail with disruption, route, and alert context."""

    shipment = _shipment_or_404(id)

    latest_disruption = (
        DisruptionScore.query.filter_by(shipment_id=shipment.id)
        .order_by(DisruptionScore.computed_at.desc())
        .first()
    )

    history_cutoff = datetime.utcnow() - timedelta(days=30)
    disruption_history_rows = (
        DisruptionScore.query.filter(
            DisruptionScore.shipment_id == shipment.id,
            DisruptionScore.computed_at >= history_cutoff,
        )
        .order_by(DisruptionScore.computed_at.asc())
        .all()
    )
    disruption_history = [
        {
            "computed_at": row.computed_at.isoformat() if row.computed_at else None,
            "drs_total": float(row.drs_total or 0),
        }
        for row in disruption_history_rows
    ]

    milestones = _build_milestones(shipment)

    route_recommendations = (
        RouteRecommendation.query.filter(
            RouteRecommendation.shipment_id == shipment.id,
            RouteRecommendation.status == "pending",
        )
        .order_by(RouteRecommendation.option_label.asc())
        .all()
    )

    recent_alerts = (
        Alert.query.filter(
            Alert.organisation_id == current_user.organisation_id,
            Alert.shipment_id == shipment.id,
        )
        .order_by(Alert.created_at.desc())
        .limit(5)
        .all()
    )

    ai_disruption_summary = None
    effective_drs = float(latest_disruption.drs_total if latest_disruption else shipment.disruption_risk_score or 0)
    if latest_disruption is not None and effective_drs > 30:
        ai_disruption_summary = ai_service.generate_shipment_disruption_summary(
            shipment,
            latest_disruption,
            current_app._get_current_object(),
        )

    carrier_performance = None
    if shipment.carrier_id:
        origin_region = (shipment.origin_port_code or "")[:2]
        destination_region = (shipment.destination_port_code or "")[:2]
        mode = _resolve_carrier_mode(shipment.mode)

        carrier_performance = (
            CarrierPerformance.query.filter(
                CarrierPerformance.carrier_id == shipment.carrier_id,
                CarrierPerformance.mode == mode,
                CarrierPerformance.origin_region.ilike(f"{origin_region}%"),
                CarrierPerformance.destination_region.ilike(f"{destination_region}%"),
                or_(
                    CarrierPerformance.organisation_id == current_user.organisation_id,
                    CarrierPerformance.organisation_id.is_(None),
                ),
            )
            .order_by(
                case((CarrierPerformance.organisation_id == current_user.organisation_id, 0), else_=1),
                CarrierPerformance.period_year.desc(),
                CarrierPerformance.period_month.desc(),
            )
            .first()
        )

    origin_coords = _port_coords(shipment.origin_port_code)
    destination_coords = _port_coords(shipment.destination_port_code)
    current_coords = None
    if shipment.current_latitude is not None and shipment.current_longitude is not None:
        current_coords = [float(shipment.current_latitude), float(shipment.current_longitude)]

    route_alternatives_map = []
    for recommendation in route_recommendations:
        route_alternatives_map.append(
            {
                "id": str(recommendation.id),
                "option": recommendation.option_label,
                "coordinates": [],
            }
        )

    shipment_map_data = {
        "shipment_id": str(shipment.id),
        "origin": {
            "code": shipment.origin_port_code,
            "address": shipment.origin_address,
            "coordinates": origin_coords,
        },
        "destination": {
            "code": shipment.destination_port_code,
            "address": shipment.destination_address,
            "coordinates": destination_coords,
        },
        "waypoints": [],
        "actual_path": [current_coords] if current_coords else [],
        "current_position": current_coords,
        "route_alternatives": route_alternatives_map,
        "ehs_signals": (latest_disruption.ehs_signals if latest_disruption else None) or [],
        "status": shipment.status,
    }

    decision_form = RouteDecisionForm()

    return render_template(
        "app/shipments/detail.html",
        shipment=shipment,
        latest_disruption=latest_disruption,
        disruption_history=disruption_history,
        milestones=milestones,
        route_recommendations=route_recommendations,
        recent_alerts=recent_alerts,
        ai_disruption_summary=ai_disruption_summary,
        carrier_performance=carrier_performance,
        decision_form=decision_form,
        shipment_map_data=shipment_map_data,
    )


@shipments_bp.post("/<uuid:shipment_id>/regenerate-disruption-summary")
@role_required("admin", "manager")
def regenerate_disruption_summary(shipment_id: uuid.UUID):
    """Regenerate shipment disruption AI summary for one shipment."""

    shipment = Shipment.query.filter(
        Shipment.id == shipment_id,
        Shipment.organisation_id == current_user.organisation_id,
    ).first()
    if shipment is None:
        return jsonify({"success": False, "message": "Shipment not found."}), 404

    latest_disruption = (
        DisruptionScore.query.filter(DisruptionScore.shipment_id == shipment.id)
        .order_by(DisruptionScore.computed_at.desc())
        .first()
    )
    if latest_disruption is None:
        return jsonify({"success": False, "message": "No disruption data available for this shipment."}), 400

    payload = ai_service.generate_shipment_disruption_summary(
        shipment,
        latest_disruption,
        current_app._get_current_object(),
        force_regenerate=True,
        user_id=current_user.id,
    )

    AuditLog.log(
        db,
        event_type="ai_content_regenerated",
        description=f"Regenerated shipment disruption summary for {shipment.external_reference}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        shipment_id=shipment.id,
        metadata={
            "content_type": "shipment_disruption_summary",
            "content_key": f"shipment_{shipment.id}",
            "triggered_by": current_user.email,
        },
        ip_address=request.remote_addr,
    )

    return jsonify(
        {
            "success": True,
            "content_html": payload.get("formatted_html") or "",
            "raw_markdown": payload.get("formatted_response") or "",
            "generated_at": payload.get("generated_at") or datetime.utcnow().isoformat(),
            "regeneration_count": int(payload.get("regeneration_count") or 0),
            "structured_data": payload.get("structured_data") or {},
            "served_stale": bool(payload.get("served_stale")),
            "stale_warning": payload.get("stale_warning"),
        }
    )


@shipments_bp.route("/<uuid:id>/edit", methods=["GET", "POST"])
@role_required("admin", "manager")
def edit(id: uuid.UUID):
    """Update editable shipment fields."""

    shipment = _shipment_or_404(id)

    form = ShipmentEditForm(obj=shipment)
    form.carrier_id.choices = _carrier_choices_for_org(current_user.organisation_id)

    if request.method == "GET":
        form.carrier_id.data = str(shipment.carrier_id) if shipment.carrier_id else ""

    if form.validate_on_submit():
        shipment.carrier_id = _coerce_uuid(form.carrier_id.data)
        shipment.mode = form.mode.data
        shipment.status = form.status.data
        shipment.origin_port_code = (form.origin_port_code.data or "").strip().upper()
        shipment.destination_port_code = (form.destination_port_code.data or "").strip().upper()
        shipment.origin_address = (form.origin_address.data or "").strip() or None
        shipment.destination_address = (form.destination_address.data or "").strip() or None
        shipment.estimated_departure = form.estimated_departure.data
        shipment.estimated_arrival = form.estimated_arrival.data
        shipment.actual_departure = form.actual_departure.data
        shipment.actual_arrival = form.actual_arrival.data
        shipment.current_latitude = form.current_latitude.data
        shipment.current_longitude = form.current_longitude.data
        shipment.current_location_name = (form.current_location_name.data or "").strip() or None
        shipment.cargo_value_inr = form.cargo_value_inr.data
        shipment.customer_name = (form.customer_name.data or "").strip() or None

        db.session.commit()

        AuditLog.log(
            db,
            event_type="shipment_updated",
            description=f"Updated shipment {shipment.external_reference}.",
            organisation_id=current_user.organisation_id,
            actor_user=current_user,
            shipment_id=shipment.id,
            metadata={"shipment_id": str(shipment.id)},
            ip_address=request.remote_addr,
        )

        flash("Shipment updated successfully.", "success")
        return redirect(url_for("shipments.detail", id=shipment.id))

    return render_template("app/shipments/edit.html", form=form, shipment=shipment)


@shipments_bp.route("/import", methods=["GET", "POST"])
@role_required("admin", "manager")
def import_shipments():
    """Import shipment rows from CSV with validation and summary reporting."""

    form = ShipmentImportForm()
    results = None

    if form.validate_on_submit():
        file_storage = form.csv_file.data
        filename = secure_filename(file_storage.filename or "")
        if not filename.lower().endswith(".csv"):
            form.csv_file.errors.append("Only CSV files accepted.")
            return render_template("app/shipments/import.html", form=form, results=results)

        file_storage.stream.seek(0, io.SEEK_END)
        file_size = file_storage.stream.tell()
        file_storage.stream.seek(0)

        if file_size > 5 * 1024 * 1024:
            form.csv_file.errors.append("CSV file must be 5MB or smaller.")
            return render_template("app/shipments/import.html", form=form, results=results)

        try:
            content = file_storage.stream.read().decode("utf-8-sig")
        except UnicodeDecodeError:
            form.csv_file.errors.append("Unable to parse file. Please upload UTF-8 encoded CSV.")
            return render_template("app/shipments/import.html", form=form, results=results)

        reader = csv.DictReader(io.StringIO(content))
        required_columns = {
            "external_reference",
            "origin_port_code",
            "destination_port_code",
            "mode",
            "estimated_departure",
            "estimated_arrival",
        }

        incoming_columns = set(reader.fieldnames or [])
        if not required_columns.issubset(incoming_columns):
            missing = ", ".join(sorted(required_columns - incoming_columns))
            form.csv_file.errors.append(f"Missing required columns: {missing}")
            return render_template("app/shipments/import.html", form=form, results=results)

        validated_rows: list[dict[str, Any]] = []
        error_rows: list[dict[str, Any]] = []
        skipped_over_limit = 0

        for row_number, row in enumerate(reader, start=2):
            if len(validated_rows) >= 500:
                skipped_over_limit += 1
                continue

            ext_ref = (row.get("external_reference") or "").strip()
            origin = (row.get("origin_port_code") or "").strip().upper()
            destination = (row.get("destination_port_code") or "").strip().upper()
            mode = (row.get("mode") or "").strip().lower()
            est_departure = _parse_iso_datetime(row.get("estimated_departure"))
            est_arrival = _parse_iso_datetime(row.get("estimated_arrival"))

            if not ext_ref:
                error_rows.append({"row": row_number, "external_reference": "", "reason": "external_reference is required"})
                continue
            if len(origin) < 3 or len(origin) > 5:
                error_rows.append({"row": row_number, "external_reference": ext_ref, "reason": "origin_port_code must be 3-5 chars"})
                continue
            if len(destination) < 3 or len(destination) > 5:
                error_rows.append({"row": row_number, "external_reference": ext_ref, "reason": "destination_port_code must be 3-5 chars"})
                continue
            if mode not in MODE_CHOICES_SET:
                error_rows.append({"row": row_number, "external_reference": ext_ref, "reason": "mode is invalid"})
                continue
            if est_departure is None or est_arrival is None:
                error_rows.append({"row": row_number, "external_reference": ext_ref, "reason": "estimated dates must be ISO format"})
                continue
            if est_arrival <= est_departure:
                error_rows.append({"row": row_number, "external_reference": ext_ref, "reason": "estimated_arrival must be after estimated_departure"})
                continue

            validated_rows.append(
                {
                    "external_reference": ext_ref,
                    "origin_port_code": origin,
                    "destination_port_code": destination,
                    "mode": mode,
                    "estimated_departure": est_departure,
                    "estimated_arrival": est_arrival,
                    "carrier_name": (row.get("carrier_name") or "").strip(),
                    "origin_address": (row.get("origin_address") or "").strip() or None,
                    "destination_address": (row.get("destination_address") or "").strip() or None,
                    "customer_name": (row.get("customer_name") or "").strip() or None,
                    "cargo_value_inr": (row.get("cargo_value_inr") or "").strip() or None,
                }
            )

        carrier_cache = {
            carrier.name.lower(): carrier
            for carrier in Carrier.query.order_by(Carrier.name.asc()).all()
        }

        update_existing = bool(form.update_existing.data)
        created_count = 0
        updated_count = 0
        new_shipment_rows: list[dict[str, Any]] = []

        for payload in validated_rows:
            carrier_id = None
            carrier_name = payload.get("carrier_name")
            if carrier_name:
                carrier = carrier_cache.get(carrier_name.lower())
                if carrier is None:
                    carrier = Carrier(
                        name=carrier_name,
                        mode=_resolve_carrier_mode(payload["mode"]),
                        tracking_api_type="manual",
                        is_global_carrier=False,
                    )
                    db.session.add(carrier)
                    db.session.flush()
                    carrier_cache[carrier_name.lower()] = carrier
                carrier_id = carrier.id

            cargo_value = None
            if payload.get("cargo_value_inr"):
                try:
                    cargo_value = Decimal(payload["cargo_value_inr"])
                except Exception:
                    cargo_value = None

            existing = None
            if update_existing:
                existing = Shipment.query.filter(
                    Shipment.organisation_id == current_user.organisation_id,
                    Shipment.external_reference == payload["external_reference"],
                ).first()

            if existing:
                existing.carrier_id = carrier_id
                existing.mode = payload["mode"]
                existing.origin_port_code = payload["origin_port_code"]
                existing.destination_port_code = payload["destination_port_code"]
                existing.origin_address = payload["origin_address"]
                existing.destination_address = payload["destination_address"]
                existing.estimated_departure = payload["estimated_departure"]
                existing.estimated_arrival = payload["estimated_arrival"]
                existing.customer_name = payload["customer_name"]
                existing.cargo_value_inr = cargo_value
                updated_count += 1
                continue

            timestamp = datetime.utcnow()
            new_shipment_rows.append(
                {
                    "organisation_id": current_user.organisation_id,
                    "external_reference": payload["external_reference"],
                    "carrier_id": carrier_id,
                    "mode": payload["mode"],
                    "origin_port_code": payload["origin_port_code"],
                    "destination_port_code": payload["destination_port_code"],
                    "origin_address": payload["origin_address"],
                    "destination_address": payload["destination_address"],
                    "estimated_departure": payload["estimated_departure"],
                    "estimated_arrival": payload["estimated_arrival"],
                    "customer_name": payload["customer_name"],
                    "cargo_value_inr": cargo_value,
                    "status": "pending",
                    "disruption_risk_score": 0.00,
                    "sla_breach_probability": 0.00,
                    "is_archived": False,
                    "created_at": timestamp,
                    "updated_at": timestamp,
                }
            )

        if new_shipment_rows:
            db.session.execute(insert(Shipment), new_shipment_rows)
            created_count = len(new_shipment_rows)

        db.session.commit()

        total_errors = len(error_rows)
        if skipped_over_limit:
            total_errors += skipped_over_limit

        AuditLog.log(
            db,
            event_type="shipments_imported",
            description=(
                f"Imported shipments via CSV. Created={created_count}, Updated={updated_count}, Errors={total_errors}."
            ),
            organisation_id=current_user.organisation_id,
            actor_user=current_user,
            metadata={
                "created": created_count,
                "updated": updated_count,
                "errors": total_errors,
            },
            ip_address=request.remote_addr,
        )

        flash(
            f"Successfully imported {created_count} shipments. {total_errors} rows had errors.",
            "success" if total_errors == 0 else "warning",
        )

        results = {
            "created": created_count,
            "updated": updated_count,
            "errors": total_errors,
            "error_rows": error_rows,
            "skipped_over_limit": skipped_over_limit,
        }

    return render_template("app/shipments/import.html", form=form, results=results)


@shipments_bp.get("/import/template")
def import_template():
    """Download CSV template for shipment imports."""

    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        [
            "external_reference",
            "carrier_name",
            "mode",
            "origin_port_code",
            "destination_port_code",
            "estimated_departure",
            "estimated_arrival",
            "origin_address",
            "destination_address",
            "customer_name",
            "cargo_value_inr",
        ]
    )
    writer.writerow(
        [
            "PO-IND-10021",
            "Maersk Line",
            "ocean_fcl",
            "INNSA",
            "DEHAM",
            "2026-04-15T10:00:00",
            "2026-05-08T16:00:00",
            "Nhava Sheva Port, India",
            "Hamburg Port, Germany",
            "Aster Retail Pvt Ltd",
            "1450000.00",
        ]
    )
    writer.writerow(
        [
            "AIR-DEL-8830",
            "DHL Express",
            "air",
            "INDEL",
            "AEJEA",
            "2026-04-18T06:30:00",
            "2026-04-19T14:30:00",
            "IGI Airport Cargo Terminal",
            "Jebel Ali Logistics Zone",
            "Nexa Components",
            "485000.00",
        ]
    )
    writer.writerow(
        [
            "ROAD-BLR-4421",
            "XPO Logistics",
            "road",
            "INBLR",
            "INDEL",
            "2026-04-20T08:00:00",
            "2026-04-22T18:00:00",
            "Bengaluru Distribution Center",
            "Delhi Regional Hub",
            "UrbanKart",
            "220000.00",
        ]
    )

    csv_bytes = io.BytesIO(stream.getvalue().encode("utf-8"))
    csv_bytes.seek(0)

    return send_file(
        csv_bytes,
        mimetype="text/csv",
        as_attachment=True,
        download_name="chainwatchpro_shipments_template.csv",
    )


@shipments_bp.post("/<uuid:id>/archive")
@role_required("admin", "manager")
def archive(id: uuid.UUID):
    """Archive a shipment from active views."""

    shipment = Shipment.query.filter(
        Shipment.id == id,
        Shipment.organisation_id == current_user.organisation_id,
    ).first()
    if shipment is None:
        return jsonify({"success": False, "message": "Shipment not found."}), 404

    shipment.is_archived = True
    db.session.commit()

    AuditLog.log(
        db,
        event_type="shipment_archived",
        description=f"Archived shipment {shipment.external_reference}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        shipment_id=shipment.id,
        metadata={"shipment_id": str(shipment.id)},
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True}), 200


@shipments_bp.post("/<uuid:shipment_id>/recommendations/<uuid:rec_id>/approve")
@role_required("admin", "manager")
def approve_recommendation(shipment_id: uuid.UUID, rec_id: uuid.UUID):
    """Approve one recommendation and dismiss other pending options."""

    form = RouteDecisionForm()
    if not form.validate_on_submit():
        return jsonify({"success": False, "message": "Invalid route decision form data."}), 400

    if str(rec_id) != (form.recommendation_id.data or "").strip():
        return jsonify({"success": False, "message": "Recommendation ID mismatch."}), 400

    recommendation = (
        RouteRecommendation.query.join(Shipment, Shipment.id == RouteRecommendation.shipment_id)
        .filter(
            RouteRecommendation.id == rec_id,
            RouteRecommendation.shipment_id == shipment_id,
            Shipment.organisation_id == current_user.organisation_id,
        )
        .first()
    )

    if recommendation is None:
        return jsonify({"success": False, "message": "Recommendation not found."}), 404

    recommendation.status = "approved"
    recommendation.decided_by = current_user.id
    recommendation.decided_at = datetime.utcnow()
    recommendation.decision_notes = (form.decision_notes.data or "").strip() or None

    (
        RouteRecommendation.query.filter(
            RouteRecommendation.shipment_id == shipment_id,
            RouteRecommendation.id != recommendation.id,
            RouteRecommendation.status == "pending",
        ).update({RouteRecommendation.status: "dismissed"}, synchronize_session=False)
    )

    parent_shipment = recommendation.shipment
    if parent_shipment.status != "in_transit":
        parent_shipment.status = "in_transit"

    db.session.commit()

    AuditLog.log(
        db,
        event_type="reroute_approved",
        description=f"Approved route option {recommendation.option_label} for shipment {parent_shipment.external_reference}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        shipment_id=parent_shipment.id,
        recommendation_id=recommendation.id,
        metadata={
            "option_label": recommendation.option_label,
            "strategy": recommendation.strategy,
            "cost_delta_inr": float(recommendation.cost_delta_inr or 0),
            "revised_eta": recommendation.revised_eta.isoformat() if recommendation.revised_eta else None,
            "decision_notes": recommendation.decision_notes,
        },
        ip_address=request.remote_addr,
    )

    try:
        from celery_worker import compute_disruption_scores_single

        compute_disruption_scores_single.apply_async(args=[str(parent_shipment.id)], countdown=30, queue="high")
    except Exception:
        logger.exception("Failed to queue post-approval recompute task shipment_id=%s", parent_shipment.id)

    return (
        jsonify(
            {
                "success": True,
                "option_label": recommendation.option_label,
                "revised_eta": recommendation.revised_eta.isoformat() if recommendation.revised_eta else None,
                "decided_by_name": current_user.full_name,
                "decided_at": recommendation.decided_at.isoformat() if recommendation.decided_at else None,
            }
        ),
        200,
    )


@shipments_bp.post("/<uuid:shipment_id>/recommendations/<uuid:rec_id>/dismiss")
@role_required("admin", "manager")
def dismiss_recommendation(shipment_id: uuid.UUID, rec_id: uuid.UUID):
    """Dismiss a pending route recommendation."""

    form = RouteDecisionForm()
    if not form.validate_on_submit():
        return jsonify({"success": False, "message": "Invalid route decision form data."}), 400

    if str(rec_id) != (form.recommendation_id.data or "").strip():
        return jsonify({"success": False, "message": "Recommendation ID mismatch."}), 400

    recommendation = (
        RouteRecommendation.query.join(Shipment, Shipment.id == RouteRecommendation.shipment_id)
        .filter(
            RouteRecommendation.id == rec_id,
            RouteRecommendation.shipment_id == shipment_id,
            Shipment.organisation_id == current_user.organisation_id,
        )
        .first()
    )

    if recommendation is None:
        return jsonify({"success": False, "message": "Recommendation not found."}), 404

    recommendation.status = "dismissed"
    recommendation.decided_by = current_user.id
    recommendation.decided_at = datetime.utcnow()
    recommendation.decision_notes = (form.decision_notes.data or "").strip() or None

    db.session.commit()

    AuditLog.log(
        db,
        event_type="reroute_dismissed",
        description=f"Dismissed route option {recommendation.option_label}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        shipment_id=shipment_id,
        recommendation_id=recommendation.id,
        metadata={
            "option_label": recommendation.option_label,
            "decision_notes": recommendation.decision_notes,
        },
        ip_address=request.remote_addr,
    )

    try:
        from celery_worker import compute_disruption_scores_single

        compute_disruption_scores_single.apply_async(args=[str(shipment_id)], countdown=30, queue="high")
    except Exception:
        logger.exception("Failed to queue post-dismiss recompute task shipment_id=%s", shipment_id)

    return jsonify({"success": True}), 200


