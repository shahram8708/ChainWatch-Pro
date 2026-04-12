"""Authenticated JSON API endpoints for dashboard polling and data widgets."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from celery.result import AsyncResult
from flask import Blueprint, current_app, jsonify, request, session
from flask_login import current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.models.alert import Alert
from app.models.ai_generated_content import AIGeneratedContent
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.disruption_score import DisruptionScore
from app.models.route_recommendation import RouteRecommendation
from app.models.shipment import Shipment
from app.routes.dashboard import compute_dashboard_metrics
from app.services import carrier_tracker
from app.utils.decorators import login_required, role_required


api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


RISK_COLORS = {
    "critical": "#D32F2F",
    "warning": "#FF8C00",
    "watch": "#F59E0B",
    "green": "#00A86B",
}


def _risk_level(score: float | None) -> str:
    value = float(score or 0)
    if value >= 81:
        return "critical"
    if value >= 61:
        return "warning"
    if value >= 31:
        return "watch"
    return "green"


def _coerce_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _period_to_months(period: str | None) -> int:
    mapping = {
        "30d": 1,
        "90d": 3,
        "180d": 6,
        "365d": 12,
    }
    period_value = (period or "90d").strip().lower()
    return mapping.get(period_value, 3)


def _clip_text(value: str | None, max_length: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    clipped = text[: max_length - 3].rstrip()
    return f"{clipped}..."


def _search_score(query: str, *fields: str | None) -> int:
    query_text = " ".join(str(query or "").lower().split())
    if not query_text:
        return 0

    tokens = [token for token in query_text.split() if token]
    best = 0
    for raw_field in fields:
        candidate = " ".join(str(raw_field or "").lower().split())
        if not candidate:
            continue

        score = 0
        if candidate == query_text:
            score = max(score, 140)
        if candidate.startswith(query_text):
            score = max(score, 120)
        if query_text in candidate:
            score = max(score, 95)
        if tokens and all(token in candidate for token in tokens):
            score = max(score, 105)

        prefix_hits = sum(1 for token in tokens if candidate.startswith(token))
        score += prefix_hits * 6
        best = max(best, score)

    return best


@api_bp.before_request
@login_required
def _api_guards():
    """Require authenticated user for API routes."""


@api_bp.get("/shipments/map-data")
def shipment_map_data():
    """Return active shipment points for dashboard Leaflet map."""

    shipments = (
        Shipment.query.filter(
            Shipment.organisation_id == current_user.organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
            Shipment.current_latitude.isnot(None),
            Shipment.current_longitude.isnot(None),
        )
        .order_by(Shipment.updated_at.desc())
        .limit(500)
        .all()
    )

    payload = []
    for shipment in shipments:
        risk_level = _risk_level(float(shipment.disruption_risk_score or 0))
        payload.append(
            {
                "id": str(shipment.id),
                "lat": float(shipment.current_latitude),
                "lng": float(shipment.current_longitude),
                "drs": float(shipment.disruption_risk_score or 0),
                "risk_level": risk_level,
                "risk_color": RISK_COLORS[risk_level],
                "status": shipment.status,
                "external_reference": shipment.external_reference or "",
                "carrier_name": shipment.carrier.name if shipment.carrier else "",
                "origin_port_code": shipment.origin_port_code,
                "destination_port_code": shipment.destination_port_code,
                "current_location_name": shipment.current_location_name or "Location pending",
                "estimated_arrival": shipment.estimated_arrival.isoformat() if shipment.estimated_arrival else None,
            }
        )

    return jsonify(payload), 200


@api_bp.get("/shipments/<uuid:id>/drs-history")
def drs_history(id: uuid.UUID):
    """Return full disruption score history for one shipment."""

    shipment = Shipment.query.filter(
        Shipment.id == id,
        Shipment.organisation_id == current_user.organisation_id,
    ).first()
    if shipment is None:
        return jsonify({"success": False, "message": "Shipment not found"}), 404

    history = (
        DisruptionScore.query.filter_by(shipment_id=shipment.id)
        .order_by(DisruptionScore.computed_at.desc())
        .limit(100)
        .all()
    )

    history = list(reversed(history))

    payload = [
        {
            "timestamp": row.computed_at.isoformat() if row.computed_at else None,
            "drs_total": float(row.drs_total or 0),
            "tvs": float(row.tvs or 0),
            "mcs": float(row.mcs or 0),
            "ehs": float(row.ehs or 0),
            "crs": float(row.crs or 0),
            "dtas": float(row.dtas or 0),
            "cps": float(row.cps or 0),
        }
        for row in history
    ]

    return jsonify(payload), 200


@api_bp.get("/optimizer/recommendations/<uuid:shipment_id>")
def optimizer_recommendations(shipment_id: uuid.UUID):
    """Return pending optimizer recommendations for shipment polling UI."""

    shipment = Shipment.query.filter(
        Shipment.id == shipment_id,
        Shipment.organisation_id == current_user.organisation_id,
    ).first()
    if shipment is None:
        return jsonify({"success": False, "message": "Shipment not found"}), 404

    recommendations = (
        RouteRecommendation.query.filter(
            RouteRecommendation.shipment_id == shipment.id,
            RouteRecommendation.status == "pending",
        )
        .order_by(RouteRecommendation.option_label.asc())
        .all()
    )

    payload = {
        "has_recommendations": len(recommendations) > 0,
        "count": len(recommendations),
        "recommendations": [
            {
                "id": str(item.id),
                "option_label": item.option_label,
                "strategy": item.strategy,
                "revised_eta": item.revised_eta.isoformat() if item.revised_eta else None,
                "cost_delta_inr": float(item.cost_delta_inr or 0),
                "on_time_confidence": float(item.on_time_confidence or 0),
                "execution_deadline": item.execution_deadline.isoformat() if item.execution_deadline else None,
                "status": item.status,
            }
            for item in recommendations
        ],
    }
    return jsonify(payload), 200


@api_bp.get("/alerts/unread-count")
def unread_alert_count():
    """Return count of unacknowledged alerts for the current org."""

    count = (
        Alert.query.filter(
            Alert.organisation_id == current_user.organisation_id,
            Alert.is_acknowledged.is_(False),
        ).count()
    )
    return jsonify({"count": int(count)}), 200


@api_bp.get("/search/global")
def global_search():
    """Return ranked cross-module search results for top header search."""

    query_text = " ".join((request.args.get("q") or "").split())
    if len(query_text) < 2:
        return (
            jsonify(
                {
                    "query": query_text,
                    "total_results": 0,
                    "results": {
                        "pages": [],
                        "shipments": [],
                        "alerts": [],
                        "carriers": [],
                    },
                }
            ),
            200,
        )

    organisation_id = current_user.organisation_id
    like_term = f"%{query_text}%"
    per_group_limit = min(max(request.args.get("limit", default=6, type=int) or 6, 3), 12)

    shipment_rows = (
        db.session.query(Shipment, Carrier.name.label("carrier_name"))
        .outerjoin(Carrier, Carrier.id == Shipment.carrier_id)
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            or_(
                Shipment.external_reference.ilike(like_term),
                Shipment.customer_name.ilike(like_term),
                Shipment.origin_port_code.ilike(like_term),
                Shipment.destination_port_code.ilike(like_term),
                Shipment.current_location_name.ilike(like_term),
                Carrier.name.ilike(like_term),
            ),
        )
        .order_by(Shipment.updated_at.desc())
        .limit(per_group_limit * 6)
        .all()
    )

    alert_rows = (
        db.session.query(Alert, Shipment.external_reference.label("shipment_reference"))
        .outerjoin(Shipment, Shipment.id == Alert.shipment_id)
        .filter(
            Alert.organisation_id == organisation_id,
            or_(
                Alert.title.ilike(like_term),
                Alert.description.ilike(like_term),
                Alert.alert_type.ilike(like_term),
                Shipment.external_reference.ilike(like_term),
            ),
        )
        .order_by(Alert.created_at.desc())
        .limit(per_group_limit * 6)
        .all()
    )

    carrier_rows = (
        db.session.query(
            Carrier,
            func.count(Shipment.id).label("active_shipments"),
        )
        .join(Shipment, Shipment.carrier_id == Carrier.id)
        .filter(
            Shipment.organisation_id == organisation_id,
            Shipment.is_archived.is_(False),
            or_(
                Carrier.name.ilike(like_term),
                Carrier.scac_code.ilike(like_term),
            ),
        )
        .group_by(Carrier.id)
        .order_by(func.count(Shipment.id).desc(), Carrier.name.asc())
        .limit(per_group_limit * 4)
        .all()
    )

    shipment_results = []
    for shipment, carrier_name in shipment_rows:
        score = _search_score(
            query_text,
            shipment.external_reference,
            shipment.customer_name,
            shipment.origin_port_code,
            shipment.destination_port_code,
            shipment.current_location_name,
            carrier_name,
            shipment.status,
        )
        if score <= 0:
            continue

        if shipment.status in {"in_transit", "delayed", "at_customs"}:
            score += 6

        route_label = ""
        if shipment.origin_port_code and shipment.destination_port_code:
            route_label = f"{shipment.origin_port_code} -> {shipment.destination_port_code}"
        elif shipment.origin_port_code:
            route_label = f"Origin {shipment.origin_port_code}"
        elif shipment.destination_port_code:
            route_label = f"Destination {shipment.destination_port_code}"

        title = shipment.external_reference or shipment.customer_name or f"Shipment {str(shipment.id)[:8]}"
        meta = [shipment.status.replace("_", " ").title()]
        if carrier_name:
            meta.append(carrier_name)
        meta.append(f"DRS {float(shipment.disruption_risk_score or 0):.0f}")

        shipment_results.append(
            {
                "id": str(shipment.id),
                "title": title,
                "subtitle": route_label,
                "meta": meta,
                "url": f"/shipments/{shipment.id}",
                "_score": score,
            }
        )

    alert_results = []
    now = datetime.utcnow()
    for alert, shipment_reference in alert_rows:
        score = _search_score(
            query_text,
            alert.title,
            alert.description,
            alert.alert_type,
            shipment_reference,
        )
        if score <= 0:
            continue

        if not alert.is_acknowledged:
            score += 8
        if alert.severity == "critical":
            score += 10
        elif alert.severity == "warning":
            score += 6

        if alert.created_at:
            age_hours = max((now - alert.created_at).total_seconds() / 3600, 0.0)
            if age_hours <= 12:
                score += 8
            elif age_hours <= 48:
                score += 4

        meta = [alert.severity.title(), "Open" if not alert.is_acknowledged else "Acknowledged"]
        if shipment_reference:
            meta.append(shipment_reference)

        alert_results.append(
            {
                "id": str(alert.id),
                "title": alert.title,
                "subtitle": _clip_text(alert.description, max_length=115),
                "meta": meta,
                "url": f"/alerts?selected_alert_id={alert.id}",
                "_score": score,
            }
        )

    carrier_results = []
    for carrier, active_shipments in carrier_rows:
        score = _search_score(
            query_text,
            carrier.name,
            carrier.scac_code,
            carrier.mode,
        )
        if score <= 0:
            continue

        shipment_count = int(active_shipments or 0)
        score += min(shipment_count, 10)

        meta = [carrier.mode.title()]
        if carrier.scac_code:
            meta.append(carrier.scac_code)
        meta.append(f"{shipment_count} active shipments")

        carrier_results.append(
            {
                "id": str(carrier.id),
                "title": carrier.name,
                "subtitle": "Performance view and lane analytics",
                "meta": meta,
                "url": f"/carriers?carrier_id={carrier.id}",
                "_score": score,
            }
        )

    page_catalog = [
        {
            "id": "page_dashboard",
            "title": "Dashboard",
            "subtitle": "Live shipment health and disruption KPIs",
            "url": "/dashboard",
            "keywords": "overview metrics kpi operations",
        },
        {
            "id": "page_shipments",
            "title": "Shipments",
            "subtitle": "Track shipment status and route progress",
            "url": "/shipments",
            "keywords": "tracking containers freight eta",
        },
        {
            "id": "page_alerts",
            "title": "Alerts",
            "subtitle": "Disruption and exception alert center",
            "url": "/alerts",
            "keywords": "risk incidents warnings exceptions",
        },
        {
            "id": "page_optimizer",
            "title": "Route Optimizer",
            "subtitle": "Evaluate alternate routes and approve actions",
            "url": "/optimizer",
            "keywords": "reroute route optimization alternatives",
        },
        {
            "id": "page_risk_map",
            "title": "Risk Map",
            "subtitle": "Visualize geographic risk hotspots",
            "url": "/risk-map",
            "keywords": "map heat risk geospatial",
        },
        {
            "id": "page_carriers",
            "title": "Carrier Intelligence",
            "subtitle": "Carrier trends, OTD and lane analytics",
            "url": "/carriers",
            "keywords": "carrier performance otd lanes",
        },
        {
            "id": "page_planner",
            "title": "Scenario Planner",
            "subtitle": "Simulate disruption what-if scenarios",
            "url": "/planner",
            "keywords": "simulation planning what if",
        },
        {
            "id": "page_reports",
            "title": "Reports",
            "subtitle": "Export and schedule analytics reports",
            "url": "/reports",
            "keywords": "report export analytics summary",
        },
        {
            "id": "page_audit",
            "title": "Audit Log",
            "subtitle": "Review key user and system activities",
            "url": "/audit-log",
            "keywords": "audit activity events history",
        },
        {
            "id": "page_settings",
            "title": "Settings",
            "subtitle": "Manage profile, billing and integrations",
            "url": "/settings/profile",
            "keywords": "profile billing team integrations",
        },
    ]

    page_results = []
    for page in page_catalog:
        score = _search_score(
            query_text,
            page["title"],
            page["subtitle"],
            page["keywords"],
        )
        if score <= 0:
            continue

        page_results.append(
            {
                "id": page["id"],
                "title": page["title"],
                "subtitle": page["subtitle"],
                "meta": ["Navigation"],
                "url": page["url"],
                "_score": score,
            }
        )

    def _ranked(items: list[dict]) -> list[dict]:
        ranked = sorted(items, key=lambda item: item.get("_score", 0), reverse=True)
        deduped: list[dict] = []
        seen_ids: set[str] = set()

        for item in ranked:
            item_id = str(item.get("id") or "")
            if not item_id or item_id in seen_ids:
                continue

            seen_ids.add(item_id)
            payload = dict(item)
            payload.pop("_score", None)
            deduped.append(payload)
            if len(deduped) >= per_group_limit:
                break

        return deduped

    results = {
        "pages": _ranked(page_results),
        "shipments": _ranked(shipment_results),
        "alerts": _ranked(alert_results),
        "carriers": _ranked(carrier_results),
    }
    total_results = sum(len(items) for items in results.values())

    return (
        jsonify(
            {
                "query": query_text,
                "total_results": int(total_results),
                "results": results,
            }
        ),
        200,
    )


@api_bp.get("/admin/ai-cache-stats")
@role_required("admin")
def admin_ai_cache_stats():
    """Return per-organisation AI cache inventory and regeneration metrics."""

    org_id = current_user.organisation_id
    now = datetime.utcnow()
    today_start = datetime(now.year, now.month, now.day)
    week_start = today_start - timedelta(days=today_start.weekday())

    records = (
        AIGeneratedContent.query.filter(AIGeneratedContent.organisation_id == org_id)
        .order_by(AIGeneratedContent.updated_at.desc())
        .all()
    )

    total_cached_entries = len(records)
    by_content_type: dict[str, dict[str, float | int]] = {}

    grouped: dict[str, list[AIGeneratedContent]] = {}
    for row in records:
        grouped.setdefault(row.content_type, []).append(row)

    for content_type, rows in grouped.items():
        ages = []
        stale_count = 0
        for row in rows:
            if row.updated_at:
                ages.append(max((now - row.updated_at).total_seconds() / 3600.0, 0.0))
            if row.is_stale:
                stale_count += 1

        avg_age = round((sum(ages) / len(ages)) if ages else 0.0, 2)
        by_content_type[content_type] = {
            "count": len(rows),
            "avg_age_hours": avg_age,
            "stale_count": stale_count,
        }

    reusable_today = [
        row
        for row in records
        if row.updated_at
        and row.updated_at < today_start
        and not row.is_stale
        and (row.expires_at is None or row.expires_at >= now)
    ]
    total_gemini_calls_saved_today = len(reusable_today)

    oldest_cache_entry = None
    if records:
        oldest = min(records, key=lambda item: item.created_at or now)
        if oldest.created_at:
            oldest_cache_entry = oldest.created_at.isoformat() + "Z"

    total_regenerations_this_week = (
        AuditLog.query.filter(
            AuditLog.organisation_id == org_id,
            AuditLog.event_type == "ai_content_regenerated",
            AuditLog.created_at >= week_start,
        ).count()
    )

    return (
        jsonify(
            {
                "total_cached_entries": total_cached_entries,
                "by_content_type": by_content_type,
                "total_gemini_calls_saved_today": int(total_gemini_calls_saved_today),
                "oldest_cache_entry": oldest_cache_entry,
                "total_regenerations_this_week": int(total_regenerations_this_week),
            }
        ),
        200,
    )


@api_bp.get("/dashboard/metrics")
def dashboard_metrics():
    """Return dashboard KPI metrics for client-side polling."""

    metrics = compute_dashboard_metrics(current_user.organisation_id)
    return jsonify(metrics), 200


@api_bp.get("/carriers/performance")
def carriers_performance():
    """Return carrier comparison with optional selected carrier detail datasets."""

    period = (request.args.get("period") or "90d").strip().lower()
    months = _period_to_months(period)
    carrier_id = _coerce_uuid(request.args.get("carrier_id"))

    carriers = carrier_tracker.get_all_carriers_comparison(
        current_user.organisation_id,
        db_session=db.session,
        months=months,
    )

    selected_carrier_trend = None
    selected_carrier_lanes = None

    if carrier_id:
        selected_carrier_trend = carrier_tracker.get_carrier_otd_trend(
            carrier_id,
            current_user.organisation_id,
            db_session=db.session,
            months=months,
        )
        selected_carrier_lanes = carrier_tracker.get_carrier_lane_breakdown(
            carrier_id,
            current_user.organisation_id,
            db_session=db.session,
            months=months,
        )

    return (
        jsonify(
            {
                "carriers": carriers,
                "selected_carrier_trend": selected_carrier_trend,
                "selected_carrier_lanes": selected_carrier_lanes,
                "period": period if period in {"30d", "90d", "180d", "365d"} else "90d",
                "generated_at": datetime.utcnow().isoformat(),
            }
        ),
        200,
    )


@api_bp.get("/planner/simulation-status")
def planner_simulation_status():
    """Return simulation result status for current user session."""

    result_timestamp = session.get("planner_last_result_timestamp")
    has_result = bool(session.get("planner_last_result"))
    return (
        jsonify(
            {
                "has_result": has_result,
                "result_timestamp": result_timestamp if has_result else None,
            }
        ),
        200,
    )


@api_bp.post("/carriers/<uuid:carrier_id>/regenerate-commentary")
@role_required("admin", "manager")
def regenerate_carrier_commentary(carrier_id: uuid.UUID):
    """Regenerate cached carrier commentary for the authenticated organisation."""

    linked = (
        Shipment.query.filter(
            Shipment.organisation_id == current_user.organisation_id,
            Shipment.carrier_id == carrier_id,
            Shipment.is_archived.is_(False),
        ).first()
    )
    if linked is None:
        return jsonify({"success": False, "message": "Carrier is not linked to this organisation."}), 403

    carrier = Carrier.query.filter_by(id=carrier_id).first()
    if carrier is None:
        return jsonify({"success": False, "message": "Carrier not found."}), 404

    summaries = carrier_tracker.get_all_carriers_comparison(
        current_user.organisation_id,
        db_session=db.session,
        months=3,
    )
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

    lanes = carrier_tracker.get_carrier_lane_breakdown(
        carrier_id,
        current_user.organisation_id,
        db_session=db.session,
        months=3,
    )
    payload = carrier_tracker.generate_carrier_ai_commentary(
        carrier,
        summary,
        lanes,
        summary.get("trend", "neutral"),
        current_app._get_current_object(),
        refresh=True,
        organisation_id=current_user.organisation_id,
        period_months=3,
        user_id=current_user.id,
    )

    return (
        jsonify(
            {
                "success": True,
                "content_html": payload.get("formatted_html") or "",
                "raw_markdown": payload.get("formatted_response") or "",
                "structured_data": payload.get("structured_data") or {},
                "served_stale": bool(payload.get("served_stale")),
                "stale_warning": payload.get("stale_warning"),
                "generated_at": payload.get("generated_at") or datetime.utcnow().isoformat(),
                "regeneration_count": int(payload.get("regeneration_count") or 0),
            }
        ),
        200,
    )


@api_bp.get("/reports/<job_id>/status")
def report_status(job_id: str):
    """Return asynchronous report task status for Step 6-compatible polling."""

    from celery_worker import celery

    profile = current_user.organisation.org_profile_data or {}
    if not isinstance(profile, dict):
        profile = {}
    report_jobs = profile.get("report_jobs", {})
    if not isinstance(report_jobs, dict):
        report_jobs = {}

    job_meta = report_jobs.get(job_id)
    if not isinstance(job_meta, dict):
        return (
            jsonify(
                {
                    "status": "failed",
                    "progress": 0,
                    "download_url": None,
                    "error": "Job not found or not authorized.",
                    "report_type": None,
                    "output_format": None,
                }
            ),
            404,
        )

    if str(job_meta.get("organisation_id")) != str(current_user.organisation_id):
        return (
            jsonify(
                {
                    "status": "failed",
                    "progress": 0,
                    "download_url": None,
                    "error": "Job not found or not authorized.",
                    "report_type": None,
                    "output_format": None,
                }
            ),
            403,
        )

    task_result = AsyncResult(job_id, app=celery)

    try:
        celery_state = (task_result.state or "PENDING").upper()
    except Exception:
        current_app.logger.exception("Failed to read Celery task state for report job_id=%s", job_id)
        celery_state = str(job_meta.get("status") or "PENDING").upper()

    try:
        task_info = task_result.info
    except Exception:
        current_app.logger.exception("Failed to read Celery task info for report job_id=%s", job_id)
        task_info = {}

    info = task_info if isinstance(task_info, dict) else {}

    status_map = {
        "PENDING": "pending",
        "RECEIVED": "processing",
        "STARTED": "processing",
        "RETRY": "processing",
        "SUCCESS": "completed",
        "FAILURE": "failed",
    }
    status = status_map.get(celery_state, "processing")

    job_status = str(job_meta.get("status") or "").strip().lower()
    if job_status in {"pending", "processing", "completed", "failed"} and status in {"pending", "processing"}:
        status = job_status

    progress = 0
    if status == "completed":
        progress = 100
    elif status == "failed":
        progress = int(info.get("progress", 0)) if info else 0
    else:
        progress = int(info.get("progress", 0)) if info else 0

    task_result_payload = None
    if status in {"completed", "failed"}:
        try:
            task_result_payload = task_result.result
        except Exception:
            current_app.logger.exception("Failed to read Celery task result for report job_id=%s", job_id)
            task_result_payload = None

    download_url = None
    if status == "completed":
        if info.get("download_url"):
            download_url = info.get("download_url")
        elif isinstance(task_result_payload, dict):
            download_url = task_result_payload.get("download_url")
        elif job_meta.get("download_url"):
            download_url = job_meta.get("download_url")

    error = None
    if status == "failed":
        if info.get("error"):
            error = info.get("error")
        elif isinstance(task_result_payload, Exception):
            error = str(task_result_payload)
        elif isinstance(task_result_payload, dict):
            error = task_result_payload.get("error")
        elif job_meta.get("error"):
            error = job_meta.get("error")

    report_type = info.get("report_type") or job_meta.get("report_type")
    output_format = info.get("output_format") or job_meta.get("output_format")

    return (
        jsonify(
            {
                "status": status,
                "download_url": download_url,
                "progress": max(0, min(progress, 100)),
                "error": error,
                "report_type": report_type,
                "output_format": output_format,
            }
        ),
        200,
    )
