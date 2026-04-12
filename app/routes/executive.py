"""Executive dashboard and AI brief routes."""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import case, func

from app.extensions import db
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.disruption_score import DisruptionScore
from app.models.route_recommendation import RouteRecommendation
from app.models.shipment import Shipment
from app.services import ai_service
from app.utils.decorators import login_required, role_required, verified_required


executive_bp = Blueprint("executive", __name__)


def _safe_float(value, default=0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _month_snapshot(org_id, year: int, month: int) -> float:
    row = (
        db.session.query(
            func.coalesce(func.sum(CarrierPerformance.on_time_count), 0).label("on_time"),
            func.coalesce(func.sum(CarrierPerformance.total_shipments), 0).label("total"),
        )
        .filter(
            CarrierPerformance.organisation_id == org_id,
            CarrierPerformance.period_year == year,
            CarrierPerformance.period_month == month,
        )
        .first()
    )

    total = int(getattr(row, "total", 0) or 0)
    if total <= 0:
        return 0.0
    return round((_safe_float(getattr(row, "on_time", 0)) / total) * 100.0, 2)


def _active_shipments(org_id):
    return (
        Shipment.query.filter(
            Shipment.organisation_id == org_id,
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
        )
        .order_by(Shipment.updated_at.desc())
        .all()
    )


def _otd_trend_proxy(org_id, start_dt: datetime, end_dt: datetime) -> list[dict]:
    rows = (
        db.session.query(
            func.date(DisruptionScore.computed_at).label("day"),
            func.count(DisruptionScore.id).label("total"),
            func.sum(case((DisruptionScore.drs_total < 31, 1), else_=0)).label("green_count"),
        )
        .join(Shipment, Shipment.id == DisruptionScore.shipment_id)
        .filter(
            Shipment.organisation_id == org_id,
            Shipment.is_archived.is_(False),
            DisruptionScore.computed_at >= start_dt,
            DisruptionScore.computed_at <= end_dt,
        )
        .group_by(func.date(DisruptionScore.computed_at))
        .order_by(func.date(DisruptionScore.computed_at).asc())
        .all()
    )

    day_map = {
        row.day: {
            "total": int(row.total or 0),
            "green": int(row.green_count or 0),
        }
        for row in rows
    }

    timeline = []
    cursor = start_dt.date()
    while cursor <= end_dt.date():
        item = day_map.get(cursor, {"total": 0, "green": 0})
        total = item["total"]
        rate = round((item["green"] / total) * 100.0, 2) if total > 0 else 0.0
        timeline.append({"date_label": cursor.strftime("%d %b"), "date": cursor.isoformat(), "otd_rate": rate})
        cursor += timedelta(days=1)

    return timeline


def _top_lanes(org_id):
    rows = (
        db.session.query(
            Shipment.origin_port_code,
            Shipment.destination_port_code,
            func.avg(Shipment.disruption_risk_score).label("avg_drs"),
            func.count(Shipment.id).label("shipment_count"),
            func.coalesce(func.sum(Shipment.cargo_value_inr), 0).label("cargo_value"),
        )
        .filter(
            Shipment.organisation_id == org_id,
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
            Shipment.disruption_risk_score > 40,
        )
        .group_by(Shipment.origin_port_code, Shipment.destination_port_code)
        .order_by(func.avg(Shipment.disruption_risk_score).desc())
        .limit(5)
        .all()
    )

    return [
        {
            "origin_port_code": row.origin_port_code,
            "destination_port_code": row.destination_port_code,
            "avg_drs": round(_safe_float(row.avg_drs), 2),
            "shipment_count": int(row.shipment_count or 0),
            "cargo_value_inr": round(_safe_float(row.cargo_value), 2),
        }
        for row in rows
    ]


def _top_carriers(org_id):
    risk_rows = (
        db.session.query(
            Shipment.carrier_id,
            Carrier.name,
            Carrier.mode,
            func.avg(Shipment.disruption_risk_score).label("avg_drs"),
            func.count(Shipment.id).label("shipment_count"),
        )
        .join(Carrier, Carrier.id == Shipment.carrier_id)
        .filter(
            Shipment.organisation_id == org_id,
            Shipment.is_archived.is_(False),
            Shipment.status.notin_(["delivered", "cancelled"]),
            Shipment.disruption_risk_score > 40,
        )
        .group_by(Shipment.carrier_id, Carrier.name, Carrier.mode)
        .order_by(func.avg(Shipment.disruption_risk_score).desc())
        .limit(5)
        .all()
    )

    otd_rows = (
        db.session.query(
            CarrierPerformance.carrier_id,
            func.sum(CarrierPerformance.on_time_count).label("on_time_count"),
            func.sum(CarrierPerformance.total_shipments).label("total_shipments"),
        )
        .filter(CarrierPerformance.organisation_id == org_id)
        .group_by(CarrierPerformance.carrier_id)
        .all()
    )
    otd_map = {}
    for row in otd_rows:
        total = int(row.total_shipments or 0)
        otd_map[row.carrier_id] = round((_safe_float(row.on_time_count) / total) * 100.0, 2) if total else 0.0

    return [
        {
            "carrier_id": str(row.carrier_id),
            "carrier_name": row.name,
            "mode": row.mode,
            "avg_drs": round(_safe_float(row.avg_drs), 2),
            "shipment_count": int(row.shipment_count or 0),
            "otd_rate": otd_map.get(row.carrier_id, 0.0),
        }
        for row in risk_rows
    ]


def _build_context(include_ai_brief: bool = True):
    org_id = current_user.organisation_id
    now = datetime.utcnow()

    active = _active_shipments(org_id)
    total_active = len(active)

    active_disruptions = [s for s in active if _safe_float(s.disruption_risk_score) > 60]
    active_disruptions_count = len(active_disruptions)
    active_disruptions_pct = round((active_disruptions_count / total_active) * 100.0, 2) if total_active else 0.0

    average_drs = round(
        (sum(_safe_float(s.disruption_risk_score) for s in active) / total_active) if total_active else 0.0,
        2,
    )

    financial_exposure_inr = round(
        sum(_safe_float(s.cargo_value_inr) for s in active if _safe_float(s.disruption_risk_score) > 60),
        2,
    )

    current_year = now.year
    current_month = now.month
    previous_year = current_year if current_month > 1 else current_year - 1
    previous_month = current_month - 1 if current_month > 1 else 12

    fleet_otd_rate = _month_snapshot(org_id, current_year, current_month)
    previous_otd = _month_snapshot(org_id, previous_year, previous_month)
    fleet_otd_delta = round(fleet_otd_rate - previous_otd, 2)
    fleet_otd_trend = "neutral"
    if fleet_otd_delta > 0.1:
        fleet_otd_trend = "up"
    elif fleet_otd_delta < -0.1:
        fleet_otd_trend = "down"

    week_start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    approved_reroutes = (
        db.session.query(RouteRecommendation)
        .join(Shipment, Shipment.id == RouteRecommendation.shipment_id)
        .filter(
            Shipment.organisation_id == org_id,
            RouteRecommendation.status == "approved",
            RouteRecommendation.decided_at >= week_start,
        )
        .all()
    )
    rerouting_decisions_this_week = len(approved_reroutes)
    rerouting_cost_delta_this_week = round(sum(_safe_float(r.cost_delta_inr) for r in approved_reroutes), 2)
    rerouting_savings_this_week_inr = round(max(0.0, -rerouting_cost_delta_this_week), 2)

    start_30d = now - timedelta(days=29)
    otd_trend_data = _otd_trend_proxy(org_id, start_30d, now)

    drs_distribution = {"green": 0, "watch": 0, "warning": 0, "critical": 0}
    for shipment in active:
        score = _safe_float(shipment.disruption_risk_score)
        if score <= 30:
            drs_distribution["green"] += 1
        elif score <= 60:
            drs_distribution["watch"] += 1
        elif score <= 80:
            drs_distribution["warning"] += 1
        else:
            drs_distribution["critical"] += 1

    fleet_health_data = dict(drs_distribution)
    drs_distribution_data = [
        {"label": "Green", "count": drs_distribution["green"]},
        {"label": "Watch", "count": drs_distribution["watch"]},
        {"label": "Warning", "count": drs_distribution["warning"]},
        {"label": "Critical", "count": drs_distribution["critical"]},
    ]

    top_at_risk_lanes = _top_lanes(org_id)
    top_at_risk_carriers = _top_carriers(org_id)

    critical_count = len([s for s in active if _safe_float(s.disruption_risk_score) >= 81])
    warning_count = len([s for s in active if 61 <= _safe_float(s.disruption_risk_score) <= 80])

    fleet_stats = {
        "total_active_shipments": total_active,
        "critical_count": critical_count,
        "warning_count": warning_count,
        "average_drs": average_drs,
        "fleet_otd_rate": fleet_otd_rate,
        "financial_exposure_inr": financial_exposure_inr,
        "rerouting_decisions_this_week": rerouting_decisions_this_week,
        "rerouting_savings_this_week_inr": rerouting_savings_this_week_inr,
    }

    top_risks = [
        {
            "label": f"{row['origin_port_code']} -> {row['destination_port_code']}",
            "score": row["avg_drs"],
            "shipments": row["shipment_count"],
        }
        for row in top_at_risk_lanes[:3]
    ]
    if len(top_risks) < 3:
        for row in top_at_risk_carriers:
            if len(top_risks) >= 3:
                break
            top_risks.append(
                {
                    "label": row["carrier_name"],
                    "score": row["avg_drs"],
                    "shipments": row["shipment_count"],
                }
            )

    if include_ai_brief:
        ai_brief_payload = ai_service.generate_executive_ai_brief(
            organisation=current_user.organisation,
            fleet_stats=fleet_stats,
            top_risks=top_risks,
            app_context=current_app._get_current_object(),
        )
        structured_brief = ai_brief_payload.get("structured_data") or {}
        brief_markdown = ai_brief_payload.get("formatted_response") or ""
        brief_html = ai_brief_payload.get("formatted_html") or ""
    else:
        ai_brief_payload = {}
        structured_brief = {}
        brief_markdown = ""
        brief_html = ""

    return {
        "fleet_otd_rate": fleet_otd_rate,
        "fleet_otd_trend": fleet_otd_trend,
        "fleet_otd_delta": fleet_otd_delta,
        "active_disruptions_count": active_disruptions_count,
        "active_disruptions_percentage": active_disruptions_pct,
        "average_drs": average_drs,
        "financial_exposure_inr": financial_exposure_inr,
        "rerouting_decisions_this_week": rerouting_decisions_this_week,
        "rerouting_cost_delta_this_week": rerouting_cost_delta_this_week,
        "rerouting_savings_this_week_inr": rerouting_savings_this_week_inr,
        "otd_trend_data": otd_trend_data,
        "fleet_health_data": fleet_health_data,
        "drs_distribution_data": drs_distribution_data,
        "fleet_stats": fleet_stats,
        "top_risks_for_ai": top_risks,
        "top_at_risk_lanes": top_at_risk_lanes,
        "top_at_risk_carriers": top_at_risk_carriers,
        "ai_brief_payload": ai_brief_payload,
        "ai_brief": structured_brief,
        "ai_brief_markdown": brief_markdown,
        "ai_brief_html": brief_html,
        "ai_brief_regeneration_count": int(ai_brief_payload.get("regeneration_count") or 0),
        "ai_brief_served_stale": bool(ai_brief_payload.get("served_stale")),
        "ai_brief_stale_warning": ai_brief_payload.get("stale_warning"),
        "generated_at": now,
    }


@executive_bp.before_request
@login_required
@verified_required
@role_required("admin", "manager")
def _guards():
    return None


@executive_bp.get("")
def executive_dashboard():
    context = _build_context()
    return render_template("app/dashboard/executive.html", **context)


@executive_bp.post("/refresh-brief")
@login_required
@role_required("admin", "manager")
def refresh_brief():
    context = _build_context(include_ai_brief=False)
    fleet_stats = context.get("fleet_stats") or {}
    top_risks = context.get("top_risks_for_ai") or []

    ai_payload = ai_service.generate_executive_ai_brief(
        organisation=current_user.organisation,
        fleet_stats=fleet_stats,
        top_risks=top_risks,
        app_context=current_app._get_current_object(),
        force_regenerate=True,
        user_id=current_user.id,
    )

    now = datetime.utcnow()
    iso_week = now.isocalendar()
    content_key = f"executive_{current_user.organisation_id}_week{iso_week.week}_{iso_week.year}"

    AuditLog.log(
        db,
        event_type="ai_content_regenerated",
        description="Regenerated executive AI brief.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={
            "content_type": "executive_brief",
            "content_key": content_key,
            "triggered_by": current_user.email,
        },
        ip_address=request.remote_addr,
    )

    return jsonify(
        {
            "success": True,
            "content_html": ai_payload.get("formatted_html") or "",
            "raw_markdown": ai_payload.get("formatted_response") or "",
            "structured_data": ai_payload.get("structured_data") or {},
            "served_stale": bool(ai_payload.get("served_stale")),
            "stale_warning": ai_payload.get("stale_warning"),
            "generated_at": ai_payload.get("generated_at") or datetime.utcnow().isoformat(),
            "regeneration_count": int(ai_payload.get("regeneration_count") or 0),
        }
    )
