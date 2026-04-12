"""Alert center routes, detail pane rendering, and acknowledge actions."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from flask import Blueprint, abort, current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import case
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.forms.alert_forms import AlertFilterForm
from app.models.alert import Alert
from app.models.ai_generated_content import AIGeneratedContent
from app.models.audit_log import AuditLog
from app.models.disruption_score import DisruptionScore
from app.models.shipment import Shipment
from app.services.external_data import news_monitor_service
from app.utils.decorators import login_required, role_required, verified_required
from app.utils.helpers import format_datetime_user


alerts_bp = Blueprint("alerts", __name__, url_prefix="/alerts")


def _coerce_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _severity_order_expr():
    return case(
        (Alert.severity == "critical", 1),
        (Alert.severity == "warning", 2),
        (Alert.severity == "watch", 3),
        else_=4,
    )


def _build_alert_filter_form(organisation_id, args) -> AlertFilterForm:
    form = AlertFilterForm(args)

    alert_types = (
        db.session.query(Alert.alert_type)
        .filter(Alert.organisation_id == organisation_id)
        .distinct()
        .order_by(Alert.alert_type.asc())
        .all()
    )
    form.set_alert_type_choices([item.alert_type for item in alert_types if item.alert_type])

    return form


def _alerts_query(organisation_id):
    return (
        Alert.query.options(
            joinedload(Alert.shipment),
            joinedload(Alert.acknowledging_user),
        )
        .filter(Alert.organisation_id == organisation_id)
    )


def _attach_ai_cache_metadata(alert: Alert | None) -> Alert | None:
    if alert is None:
        return None

    cache_row = (
        AIGeneratedContent.query.filter(
            AIGeneratedContent.organisation_id == alert.organisation_id,
            AIGeneratedContent.content_type == "alert_description",
            AIGeneratedContent.content_key == f"alert_{alert.id}",
        )
        .order_by(AIGeneratedContent.updated_at.desc())
        .first()
    )

    alert.ai_regeneration_count = int(cache_row.regeneration_count or 0) if cache_row else 0
    alert.ai_generated_at = cache_row.updated_at if cache_row and cache_row.updated_at else alert.created_at
    return alert


def _apply_filters(query, params):
    severity_value = (params.get("severity") or "all").strip().lower()
    if severity_value in {"critical", "warning", "watch", "info"}:
        query = query.filter(Alert.severity == severity_value)

    acknowledged_value = (params.get("acknowledged") or "all").strip().lower()
    if acknowledged_value in {"true", "acknowledged"}:
        query = query.filter(Alert.is_acknowledged.is_(True))
    elif acknowledged_value in {"false", "unacknowledged"}:
        query = query.filter(Alert.is_acknowledged.is_(False))

    shipment_id = _coerce_uuid(params.get("shipment_id"))
    if shipment_id:
        query = query.filter(Alert.shipment_id == shipment_id)

    alert_type = (params.get("alert_type") or "all").strip()
    if alert_type and alert_type.lower() != "all":
        query = query.filter(Alert.alert_type == alert_type)

    return query


@alerts_bp.before_request
@login_required
@verified_required
def _alerts_guards():
    """Apply baseline auth guards to alert routes."""


@alerts_bp.get("")
def index():
    """Render Alert Center feed and selected detail panel."""

    organisation_id = current_user.organisation_id
    filter_form = _build_alert_filter_form(organisation_id, request.args)

    query = _alerts_query(organisation_id)
    query = _apply_filters(query, request.args)

    query = query.order_by(
        Alert.is_acknowledged.asc(),
        _severity_order_expr().asc(),
        Alert.created_at.desc(),
    )

    page = max(request.args.get("page", 1, type=int), 1)
    alerts_pagination = query.paginate(page=page, per_page=20, error_out=False)

    now = datetime.utcnow()
    one_hour_ago = now - timedelta(hours=1)
    seven_days_ago = now - timedelta(days=7)
    start_of_day = datetime(now.year, now.month, now.day)

    new_last_hour = (
        Alert.query.filter(
            Alert.organisation_id == organisation_id,
            Alert.created_at >= one_hour_ago,
        ).count()
    )

    acknowledged_today = (
        Alert.query.filter(
            Alert.organisation_id == organisation_id,
            Alert.is_acknowledged.is_(True),
            Alert.acknowledged_at >= start_of_day,
        ).count()
    )

    resolved_today = acknowledged_today

    acknowledged_last_week = (
        Alert.query.filter(
            Alert.organisation_id == organisation_id,
            Alert.is_acknowledged.is_(True),
            Alert.acknowledged_at >= seven_days_ago,
        ).all()
    )

    avg_resolution_hours = None
    if acknowledged_last_week:
        total_seconds = 0.0
        for item in acknowledged_last_week:
            if item.acknowledged_at and item.created_at:
                total_seconds += (item.acknowledged_at - item.created_at).total_seconds()
        avg_resolution_hours = round(total_seconds / max(len(acknowledged_last_week), 1) / 3600, 1)

    selected_alert_id = _coerce_uuid(request.args.get("selected_alert_id"))
    selected_alert = None

    if selected_alert_id:
        selected_alert = (
            Alert.query.options(
                joinedload(Alert.shipment),
                joinedload(Alert.acknowledging_user),
            )
            .filter(
                Alert.id == selected_alert_id,
                Alert.organisation_id == organisation_id,
            )
            .first()
        )

    if selected_alert is None and alerts_pagination.items:
        selected_alert = alerts_pagination.items[0]

    selected_alert = _attach_ai_cache_metadata(selected_alert)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest" and request.args.get("partial") == "feed":
        return render_template(
            "app/alerts/_feed.html",
            alerts_pagination=alerts_pagination,
            selected_alert=selected_alert,
            filters=request.args,
        )

    return render_template(
        "app/alerts/index.html",
        filter_form=filter_form,
        alerts_pagination=alerts_pagination,
        selected_alert=selected_alert,
        stats={
            "new_last_hour": new_last_hour,
            "acknowledged_today": acknowledged_today,
            "resolved_today": resolved_today,
            "avg_resolution_hours": avg_resolution_hours,
            "total_active": Alert.query.filter(
                Alert.organisation_id == organisation_id,
                Alert.is_acknowledged.is_(False),
            ).count(),
        },
        filters={
            "severity": request.args.get("severity", "all"),
            "acknowledged": request.args.get("acknowledged", "all"),
            "shipment_id": request.args.get("shipment_id", ""),
            "alert_type": request.args.get("alert_type", "all"),
        },
    )


@alerts_bp.get("/<uuid:id>/detail")
def detail_partial(id: uuid.UUID):
    """Render only the right pane alert detail HTML for AJAX loading."""

    alert = (
        Alert.query.options(
            joinedload(Alert.shipment),
            joinedload(Alert.acknowledging_user),
        )
        .filter_by(id=id)
        .first_or_404()
    )

    if alert.organisation_id != current_user.organisation_id:
        abort(403)

    alert = _attach_ai_cache_metadata(alert)
    return render_template("app/alerts/_detail.html", selected_alert=alert)


@alerts_bp.post("/<uuid:id>/regenerate-description")
@role_required("admin", "manager")
def regenerate_description(id: uuid.UUID):
    """Regenerate enriched alert description using centralized AI cache orchestration."""

    alert = (
        Alert.query.options(
            joinedload(Alert.shipment),
            joinedload(Alert.shipment).joinedload(Shipment.carrier),
        )
        .filter(Alert.id == id, Alert.organisation_id == current_user.organisation_id)
        .first()
    )
    if alert is None:
        return jsonify({"success": False, "message": "Alert not found."}), 404

    if alert.shipment is None or alert.shipment.organisation_id != current_user.organisation_id:
        return jsonify({"success": False, "message": "Alert resource is not accessible."}), 403

    latest_drs = (
        DisruptionScore.query.filter(DisruptionScore.shipment_id == alert.shipment_id)
        .order_by(DisruptionScore.computed_at.desc())
        .first()
    )
    ehs_signals = getattr(latest_drs, "ehs_signals", None) or {}
    drs_total = float(alert.drs_at_alert or alert.shipment.disruption_risk_score or 0.0)

    ai_payload = news_monitor_service.generate_alert_description_with_gemini(
        alert_type=alert.alert_type,
        shipment=alert.shipment,
        drs_total=drs_total,
        ehs_signals=ehs_signals,
        app_context=current_app._get_current_object(),
        alert_id=alert.id,
        force_regenerate=True,
        user_id=current_user.id,
    )

    structured = ai_payload.get("structured_data") or {}
    enriched_title = (structured.get("enriched_title") or "").strip()
    full_description = (structured.get("full_description") or "").strip()

    if enriched_title:
        alert.title = enriched_title[:80]
    if full_description:
        alert.description = full_description
    db.session.commit()

    AuditLog.log(
        db,
        event_type="ai_content_regenerated",
        description=f"Regenerated AI alert description for alert {alert.id}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        alert_id=alert.id,
        shipment_id=alert.shipment_id,
        metadata={
            "content_type": "alert_description",
            "content_key": f"alert_{alert.id}",
            "triggered_by": current_user.email,
        },
        ip_address=request.remote_addr,
    )

    return jsonify(
        {
            "success": True,
            "content_html": ai_payload.get("formatted_html") or "",
            "raw_markdown": ai_payload.get("formatted_response") or "",
            "generated_at": ai_payload.get("generated_at") or datetime.utcnow().isoformat(),
            "regeneration_count": int(ai_payload.get("regeneration_count") or 0),
            "structured_data": structured,
            "title": alert.title,
            "description": alert.description,
            "served_stale": bool(ai_payload.get("served_stale")),
            "stale_warning": ai_payload.get("stale_warning"),
        }
    )


@alerts_bp.post("/<uuid:id>/acknowledge")
def acknowledge(id: uuid.UUID):
    """Acknowledge an alert via AJAX."""

    alert = Alert.query.filter(
        Alert.id == id,
        Alert.organisation_id == current_user.organisation_id,
    ).first()
    if alert is None:
        return jsonify({"success": False, "message": "Alert not found"}), 404

    if alert.is_acknowledged:
        return jsonify({"success": False, "message": "Already acknowledged"}), 200

    alert.is_acknowledged = True
    alert.acknowledged_by = current_user.id
    alert.acknowledged_at = datetime.utcnow()
    db.session.commit()

    AuditLog.log(
        db,
        event_type="alert_acknowledged",
        description=f"Acknowledged alert {alert.title}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        alert_id=alert.id,
        shipment_id=alert.shipment_id,
        metadata={
            "alert_id": str(alert.id),
            "shipment_id": str(alert.shipment_id) if alert.shipment_id else None,
        },
        ip_address=request.remote_addr,
    )

    formatted_time = format_datetime_user(
        alert.acknowledged_at,
        getattr(current_user, "timezone", "UTC"),
        "%d %b %Y %H:%M",
    )

    return (
        jsonify(
            {
                "success": True,
                "acknowledged_by": current_user.full_name,
                "acknowledged_at": formatted_time,
            }
        ),
        200,
    )


@alerts_bp.post("/acknowledge-bulk")
@alerts_bp.post("/<uuid:id>/acknowledge-bulk")
def acknowledge_bulk(id: uuid.UUID | None = None):
    """Bulk acknowledge alerts after validating tenant ownership for all IDs."""

    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("alert_ids")

    if not isinstance(raw_ids, list) or not raw_ids:
        return jsonify({"success": False, "message": "alert_ids must be a non-empty list"}), 400

    alert_ids: list[uuid.UUID] = []
    for raw_id in raw_ids:
        parsed = _coerce_uuid(raw_id)
        if not parsed:
            return jsonify({"success": False, "message": "Invalid alert ID in request"}), 400
        alert_ids.append(parsed)

    unique_ids = list(dict.fromkeys(alert_ids))

    owned_count = (
        Alert.query.filter(
            Alert.id.in_(unique_ids),
            Alert.organisation_id == current_user.organisation_id,
        ).count()
    )
    if owned_count != len(unique_ids):
        return jsonify({"success": False, "message": "One or more alerts are not accessible"}), 403

    now = datetime.utcnow()
    updated_count = (
        Alert.query.filter(
            Alert.id.in_(unique_ids),
            Alert.organisation_id == current_user.organisation_id,
            Alert.is_acknowledged.is_(False),
        ).update(
            {
                Alert.is_acknowledged: True,
                Alert.acknowledged_by: current_user.id,
                Alert.acknowledged_at: now,
            },
            synchronize_session=False,
        )
    )

    db.session.commit()

    AuditLog.log(
        db,
        event_type="alerts_bulk_acknowledged",
        description=f"Bulk acknowledged {updated_count} alerts.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={"count": int(updated_count), "alert_ids": [str(item) for item in unique_ids]},
        ip_address=request.remote_addr,
    )

    return jsonify({"success": True, "count": int(updated_count)}), 200
