"""Audit log listing and compliance export routes."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import datetime, time

from flask import Blueprint, Response, render_template, request
from flask_login import current_user
from sqlalchemy import or_

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.shipment import Shipment
from app.utils.decorators import login_required, role_required, verified_required


audit_bp = Blueprint("audit", __name__)


@audit_bp.before_request
@login_required
@verified_required
@role_required("admin", "manager")
def _guards():
    """Apply audit route guards."""


def _parse_date(date_text: str | None):
    if not date_text:
        return None
    try:
        return datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_uuid(value: str | None):
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


@audit_bp.get("/")
def index():
    """Render audit log list with filters and optional CSV export."""

    organisation_id = current_user.organisation_id

    event_type_filter = (request.args.get("event_type") or "").strip()
    actor_filter = (request.args.get("actor") or "").strip()
    shipment_filter = (request.args.get("shipment_id") or "").strip()
    start_date_filter = _parse_date(request.args.get("start_date"))
    end_date_filter = _parse_date(request.args.get("end_date"))
    page = max(request.args.get("page", default=1, type=int), 1)

    query = AuditLog.query.filter(AuditLog.organisation_id == organisation_id)

    if event_type_filter:
        query = query.filter(AuditLog.event_type == event_type_filter)

    if actor_filter:
        query = query.filter(AuditLog.actor_label.ilike(f"%{actor_filter}%"))

    if shipment_filter:
        shipment_uuid = _parse_uuid(shipment_filter)
        if shipment_uuid is not None:
            query = query.filter(AuditLog.shipment_id == shipment_uuid)
        else:
            query = query.outerjoin(Shipment, Shipment.id == AuditLog.shipment_id).filter(
                Shipment.external_reference.ilike(f"%{shipment_filter}%")
            )

    if start_date_filter:
        query = query.filter(AuditLog.created_at >= datetime.combine(start_date_filter, time.min))

    if end_date_filter:
        query = query.filter(AuditLog.created_at <= datetime.combine(end_date_filter, time.max))

    query = query.order_by(AuditLog.created_at.desc())

    export_requested = (request.args.get("export") or "").strip().lower() == "csv"

    if export_requested:
        records = query.all()

        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(
            [
                "Event ID",
                "Timestamp (UTC)",
                "Event Type",
                "Actor",
                "Shipment ID",
                "Alert ID",
                "Description",
                "IP Address",
                "Metadata",
            ]
        )

        for item in records:
            writer.writerow(
                [
                    str(item.id),
                    item.created_at.isoformat() if item.created_at else "",
                    item.event_type,
                    item.actor_label,
                    str(item.shipment_id) if item.shipment_id else "",
                    str(item.alert_id) if item.alert_id else "",
                    item.description,
                    item.ip_address or "",
                    json.dumps(item.metadata_json or {}, default=str),
                ]
            )

        AuditLog.log(
            db,
            event_type="audit_log_exported",
            description="Exported audit log records to CSV.",
            organisation_id=organisation_id,
            actor_user=current_user,
            metadata={
                "filter_params": {
                    "event_type": event_type_filter,
                    "actor": actor_filter,
                    "shipment_id": shipment_filter,
                    "start_date": request.args.get("start_date"),
                    "end_date": request.args.get("end_date"),
                },
                "record_count": len(records),
            },
            ip_address=request.remote_addr,
        )

        export_date = datetime.utcnow().strftime("%Y%m%d")
        response = Response(stream.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = f"attachment; filename=audit_log_export_{export_date}.csv"
        return response

    pagination = query.paginate(page=page, per_page=50, error_out=False)
    total_count = pagination.total

    distinct_event_rows = (
        db.session.query(AuditLog.event_type)
        .filter(AuditLog.organisation_id == organisation_id)
        .distinct()
        .order_by(AuditLog.event_type.asc())
        .all()
    )
    event_types_for_filter = [row.event_type for row in distinct_event_rows]

    active_filters = {
        "event_type": event_type_filter,
        "actor": actor_filter,
        "shipment_id": shipment_filter,
        "start_date": request.args.get("start_date") or "",
        "end_date": request.args.get("end_date") or "",
    }

    return render_template(
        "app/audit/index.html",
        audit_entries=pagination.items,
        pagination=pagination,
        event_types_for_filter=event_types_for_filter,
        active_filters=active_filters,
        total_count=total_count,
    )
