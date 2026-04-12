"""Carrier Intelligence routes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.shipment import Shipment
from app.services import carrier_tracker
from app.utils.decorators import login_required, role_required, verified_required

carrier_intel_bp = Blueprint("carrier_intel", __name__, url_prefix="/carriers")

PERIOD_TO_MONTHS = {
    "30d": 1,
    "90d": 3,
    "180d": 6,
    "365d": 12,
}

ACTIVE_SHIPMENT_STATUSES = ["pending", "in_transit", "delayed", "at_customs"]


def _coerce_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _connected_carrier_ids_from_profile(profile_data: dict) -> set[uuid.UUID]:
    candidate_lists = [
        profile_data.get("selected_carrier_ids"),
        profile_data.get("connected_carrier_ids"),
        (profile_data.get("integrations") or {}).get("carrier_ids"),
    ]

    carrier_ids: set[uuid.UUID] = set()
    for values in candidate_lists:
        if not isinstance(values, list):
            continue
        for value in values:
            parsed = _coerce_uuid(value)
            if parsed:
                carrier_ids.add(parsed)

    return carrier_ids


def _parse_generated_at(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value

    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


@carrier_intel_bp.before_request
@login_required
@verified_required
def _guards():
    """Apply auth guards for carrier intelligence routes."""


@carrier_intel_bp.get("")
def index():
    """Render carrier intelligence overview and lane detail analytics."""

    period = (request.args.get("period") or "90d").strip().lower()
    if period not in PERIOD_TO_MONTHS:
        period = "90d"
    months = PERIOD_TO_MONTHS[period]

    org_id = current_user.organisation_id
    profile_data = getattr(current_user.organisation, "org_profile_data", {}) or {}
    if not isinstance(profile_data, dict):
        profile_data = {}

    shipment_carrier_ids = {
        row[0]
        for row in (
            db.session.query(Shipment.carrier_id)
            .filter(
                Shipment.organisation_id == org_id,
                Shipment.carrier_id.isnot(None),
                Shipment.status.in_(ACTIVE_SHIPMENT_STATUSES),
                Shipment.is_archived.is_(False),
            )
            .distinct()
            .all()
        )
        if row[0] is not None
    }

    connected_ids = shipment_carrier_ids | _connected_carrier_ids_from_profile(profile_data)

    selected_carrier_id = _coerce_uuid(request.args.get("carrier_id"))

    carriers_summary = carrier_tracker.get_all_carriers_comparison(org_id, db.session, months)
    summary_by_id = {str(item["carrier_id"]): item for item in carriers_summary}

    otd_trend_data: dict[str, list[dict]] = {}
    now_dt = datetime.utcnow()

    for summary in carriers_summary:
        carrier_id_text = str(summary.get("carrier_id"))
        carrier_uuid = _coerce_uuid(carrier_id_text)
        if carrier_uuid is None:
            continue
        otd_trend_data[carrier_id_text] = carrier_tracker.get_carrier_otd_trend(
            carrier_uuid,
            org_id,
            db.session,
            months,
        )

        carrier_obj = db.session.query(Carrier).filter(Carrier.id == carrier_uuid).first()
        lane_data = carrier_tracker.get_carrier_lane_breakdown(
            carrier_uuid,
            org_id,
            db.session,
            months,
        )
        commentary_payload = carrier_tracker.generate_carrier_ai_commentary(
            carrier_obj,
            summary,
            lane_data,
            summary.get("trend") or "neutral",
            current_app._get_current_object(),
            refresh=False,
            organisation_id=org_id,
            period_months=months,
            user_id=None,
        )

        summary["ai_commentary"] = commentary_payload.get("structured_data") or {}
        summary["ai_commentary_markdown"] = commentary_payload.get("formatted_response") or ""
        summary["ai_commentary_html"] = commentary_payload.get("formatted_html") or ""
        summary["ai_generated_at"] = _parse_generated_at(commentary_payload.get("generated_at")) or now_dt
        summary["ai_regeneration_count"] = int(commentary_payload.get("regeneration_count") or 0)
        summary["ai_served_stale"] = bool(commentary_payload.get("served_stale"))
        summary["ai_stale_warning"] = commentary_payload.get("stale_warning")

    selected_carrier = None
    lane_breakdown = []
    if selected_carrier_id:
        if connected_ids and selected_carrier_id not in connected_ids:
            selected_carrier = None
        else:
            selected_carrier = db.session.query(Carrier).filter(Carrier.id == selected_carrier_id).first()
            lane_breakdown = carrier_tracker.get_carrier_lane_breakdown(
                selected_carrier_id,
                org_id,
                db.session,
                months,
            )

    return render_template(
        "app/carrier_intel/index.html",
        carriers_summary=carriers_summary,
        otd_trend_data=otd_trend_data,
        selected_carrier=selected_carrier,
        selected_summary=summary_by_id.get(str(selected_carrier_id)) if selected_carrier_id else None,
        lane_breakdown=lane_breakdown,
        period=period,
        period_months=months,
    )


@carrier_intel_bp.post("/regenerate-commentary/<uuid:carrier_id>")
@role_required("admin", "manager")
def regenerate_commentary(carrier_id: uuid.UUID):
    """Regenerate AI commentary for one carrier and return normalized payload."""

    org_id = current_user.organisation_id
    carrier = db.session.query(Carrier).filter(Carrier.id == carrier_id).first()
    if carrier is None:
        return jsonify({"success": False, "message": "Carrier not found."}), 404

    linked = (
        db.session.query(Shipment.id)
        .filter(
            Shipment.organisation_id == org_id,
            Shipment.carrier_id == carrier_id,
            Shipment.is_archived.is_(False),
        )
        .first()
    )
    if linked is None:
        return jsonify({"success": False, "message": "Carrier is not linked to your organisation."}), 403

    period = (request.args.get("period") or request.form.get("period") or "90d").strip().lower()
    months = PERIOD_TO_MONTHS.get(period, 3)

    summaries = carrier_tracker.get_all_carriers_comparison(org_id, db.session, months)
    summary = next((item for item in summaries if str(item.get("carrier_id")) == str(carrier_id)), None)
    if summary is None:
        summary = {
            "carrier_id": str(carrier.id),
            "carrier_name": carrier.name,
            "mode": carrier.mode,
            "otd_rate": 0.0,
            "avg_delay_hours": 0.0,
            "crs_score": 0.0,
            "shipments_count": 0,
            "trend": "neutral",
        }

    lane_breakdown = carrier_tracker.get_carrier_lane_breakdown(
        carrier_id,
        org_id,
        db.session,
        months,
    )

    commentary_payload = carrier_tracker.generate_carrier_ai_commentary(
        carrier,
        summary,
        lane_breakdown,
        summary.get("trend", "neutral"),
        current_app._get_current_object(),
        refresh=True,
        organisation_id=org_id,
        period_months=months,
        user_id=current_user.id,
    )

    generated_at = commentary_payload.get("generated_at") or datetime.utcnow().isoformat()

    AuditLog.log(
        db,
        event_type="ai_content_regenerated",
        description=f"Regenerated AI content for carrier commentary: {carrier.name}.",
        organisation_id=org_id,
        actor_user=current_user,
        metadata={
            "content_type": "carrier_commentary",
            "content_key": f"carrier_{carrier.id}_{months}m",
            "triggered_by": current_user.email,
        },
        ip_address=request.remote_addr,
    )

    return jsonify(
        {
            "success": True,
            "content_html": commentary_payload.get("formatted_html") or "",
            "raw_markdown": commentary_payload.get("formatted_response") or "",
            "structured_data": commentary_payload.get("structured_data") or {},
            "served_stale": bool(commentary_payload.get("served_stale")),
            "stale_warning": commentary_payload.get("stale_warning"),
            "generated_at": generated_at,
            "regeneration_count": int(commentary_payload.get("regeneration_count") or 0),
        }
    )
