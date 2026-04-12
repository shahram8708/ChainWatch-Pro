"""Scenario Planner routes."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user
from sqlalchemy import func, or_

from app.extensions import db
from app.forms.simulation_forms import ScenarioPlannerForm
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.carrier_performance import CarrierPerformance
from app.models.shipment import Shipment
from app.services import simulation_service
from app.services.disruption_engine import PORT_NAMES, _mode_to_performance_mode, _port_code_to_region
from app.utils.decorators import feature_required, login_required, role_required, verified_required

logger = logging.getLogger(__name__)

planner_bp = Blueprint("planner", __name__, url_prefix="/planner")

STARTER_MONTHLY_SIMULATION_LIMIT = 5


def _coerce_uuid(value) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _first_day_next_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def _profile_dict(organisation) -> dict:
    profile = organisation.org_profile_data or {}
    if isinstance(profile, dict):
        return profile
    return {}


def _available_carriers_for_org(organisation_id):
    shipment_carrier_ids = {
        row[0]
        for row in (
            db.session.query(Shipment.carrier_id)
            .filter(
                Shipment.organisation_id == organisation_id,
                Shipment.carrier_id.isnot(None),
                Shipment.is_archived.is_(False),
            )
            .distinct()
            .all()
        )
        if row[0] is not None
    }

    profile = _profile_dict(current_user.organisation)
    for key in ["selected_carrier_ids", "connected_carrier_ids"]:
        values = profile.get(key)
        if isinstance(values, list):
            for value in values:
                parsed = _coerce_uuid(value)
                if parsed:
                    shipment_carrier_ids.add(parsed)

    query = db.session.query(Carrier)
    if shipment_carrier_ids:
        query = query.filter(
            or_(
                Carrier.id.in_(list(shipment_carrier_ids)),
                Carrier.is_global_carrier.is_(True),
            )
        )
    else:
        query = query.filter(Carrier.is_global_carrier.is_(True))

    carriers = query.order_by(Carrier.name.asc()).all()

    return carriers


def _carrier_form_choices(carriers):
    choices = [("", "Select Carrier")]
    for carrier in carriers:
        label = f"{carrier.name} ({carrier.mode.replace('_', ' ').title()})"
        choices.append((str(carrier.id), label))
    return choices


def _planner_usage_status(organisation):
    """Return (allowed, used_count, limit_count)."""

    if organisation.subscription_plan != "starter":
        return True, 0, None

    try:
        today = datetime.utcnow().date()
        profile = _profile_dict(organisation)

        used_count = int(profile.get("planner_simulations_this_month", 0) or 0)
        reset_date_raw = profile.get("planner_simulations_reset_date")

        reset_date = None
        if reset_date_raw:
            try:
                reset_date = datetime.fromisoformat(str(reset_date_raw)).date()
            except ValueError:
                reset_date = None

        if reset_date is None:
            reset_date = _first_day_next_month(today)
            profile["planner_simulations_this_month"] = used_count
            profile["planner_simulations_reset_date"] = reset_date.isoformat()
            organisation.org_profile_data = profile
            db.session.commit()

        if today >= reset_date:
            used_count = 0
            reset_date = _first_day_next_month(today)
            profile["planner_simulations_this_month"] = 0
            profile["planner_simulations_reset_date"] = reset_date.isoformat()
            organisation.org_profile_data = profile
            db.session.commit()

        return used_count < STARTER_MONTHLY_SIMULATION_LIMIT, used_count, STARTER_MONTHLY_SIMULATION_LIMIT
    except Exception:
        logger.exception("Failed evaluating planner usage limits; allowing through")
        db.session.rollback()
        return True, 0, STARTER_MONTHLY_SIMULATION_LIMIT


def _increment_planner_usage(organisation):
    if organisation.subscription_plan != "starter":
        return

    try:
        today = datetime.utcnow().date()
        profile = _profile_dict(organisation)

        used_count = int(profile.get("planner_simulations_this_month", 0) or 0)
        reset_date_raw = profile.get("planner_simulations_reset_date")
        try:
            reset_date = datetime.fromisoformat(str(reset_date_raw)).date() if reset_date_raw else None
        except ValueError:
            reset_date = None

        if reset_date is None or today >= reset_date:
            used_count = 0
            reset_date = _first_day_next_month(today)

        profile["planner_simulations_this_month"] = used_count + 1
        profile["planner_simulations_reset_date"] = reset_date.isoformat()

        organisation.org_profile_data = profile
        db.session.commit()
    except Exception:
        logger.exception("Failed incrementing planner usage counter")
        db.session.rollback()


def _choose_comparison_carriers(submitted_carrier_id, organisation_id, origin_region, destination_region, mode):
    accessible_ids = {
        str(row[0])
        for row in (
            db.session.query(Shipment.carrier_id)
            .filter(
                Shipment.organisation_id == organisation_id,
                Shipment.carrier_id.isnot(None),
                Shipment.is_archived.is_(False),
            )
            .distinct()
            .all()
        )
        if row[0] is not None
    }
    accessible_ids |= {
        str(row[0])
        for row in db.session.query(Carrier.id).filter(Carrier.is_global_carrier.is_(True)).all()
    }

    comparison_ids = [str(submitted_carrier_id)]

    perf_mode = _mode_to_performance_mode(mode)

    lane_rows = (
        db.session.query(
            CarrierPerformance.carrier_id,
            func.sum(CarrierPerformance.on_time_count).label("on_time"),
            func.sum(CarrierPerformance.total_shipments).label("total"),
        )
        .filter(
            CarrierPerformance.origin_region == origin_region,
            CarrierPerformance.destination_region == destination_region,
            CarrierPerformance.mode == perf_mode,
            or_(
                CarrierPerformance.organisation_id == organisation_id,
                CarrierPerformance.organisation_id.is_(None),
            ),
        )
        .group_by(CarrierPerformance.carrier_id)
        .all()
    )

    lane_ranked = []
    for row in lane_rows:
        total = int(row.total or 0)
        if total <= 0:
            continue
        otd = float(row.on_time or 0) / total
        lane_ranked.append((str(row.carrier_id), otd))

    lane_ranked.sort(key=lambda item: item[1], reverse=True)

    for carrier_id_text, _ in lane_ranked:
        if carrier_id_text in comparison_ids:
            continue
        if carrier_id_text not in accessible_ids:
            continue
        comparison_ids.append(carrier_id_text)
        if len(comparison_ids) >= 3:
            return comparison_ids

    global_rank_rows = (
        db.session.query(
            CarrierPerformance.carrier_id,
            func.sum(CarrierPerformance.on_time_count).label("on_time"),
            func.sum(CarrierPerformance.total_shipments).label("total"),
        )
        .filter(
            or_(
                CarrierPerformance.organisation_id == organisation_id,
                CarrierPerformance.organisation_id.is_(None),
            )
        )
        .group_by(CarrierPerformance.carrier_id)
        .all()
    )

    ranked_global = []
    for row in global_rank_rows:
        total = int(row.total or 0)
        if total <= 0:
            continue
        ranked_global.append((str(row.carrier_id), float(row.on_time or 0) / total))
    ranked_global.sort(key=lambda item: item[1], reverse=True)

    for carrier_id_text, _ in ranked_global:
        if carrier_id_text in comparison_ids:
            continue
        if carrier_id_text not in accessible_ids:
            continue
        comparison_ids.append(carrier_id_text)
        if len(comparison_ids) >= 3:
            return comparison_ids

    for carrier_id_text in sorted(accessible_ids):
        if carrier_id_text in comparison_ids:
            continue
        comparison_ids.append(carrier_id_text)
        if len(comparison_ids) >= 3:
            break

    return comparison_ids[:3]


def _port_suggestions(limit=60):
    suggestions = []
    for code, (name, country) in sorted(PORT_NAMES.items()):
        suggestions.append({
            "code": code,
            "label": f"{code} - {name}, {country}",
        })
    return suggestions[:limit]


def _apply_session_form_values(form, form_payload):
    if not isinstance(form_payload, dict):
        return

    form.origin_port_code.data = form_payload.get("origin_port_code") or ""
    form.destination_port_code.data = form_payload.get("destination_port_code") or ""
    form.mode.data = form_payload.get("mode") or ""
    form.carrier_id.data = form_payload.get("carrier_id") or ""

    ship_date_raw = form_payload.get("estimated_ship_date")
    if ship_date_raw:
        try:
            form.estimated_ship_date.data = datetime.fromisoformat(ship_date_raw).date()
        except ValueError:
            form.estimated_ship_date.data = None

    cargo_value_raw = form_payload.get("cargo_value_inr")
    if cargo_value_raw not in [None, ""]:
        try:
            form.cargo_value_inr.data = Decimal(str(cargo_value_raw))
        except Exception:
            form.cargo_value_inr.data = None

    sla_days_raw = form_payload.get("sla_requirement_days")
    if sla_days_raw not in [None, ""]:
        try:
            form.sla_requirement_days.data = int(sla_days_raw)
        except (TypeError, ValueError):
            form.sla_requirement_days.data = None


@planner_bp.before_request
@login_required
@verified_required
@feature_required("scenario_planner_enabled")
def _guards():
    """Apply auth guards for planner routes."""


@planner_bp.route("", methods=["GET", "POST"])
def index():
    """Render and execute scenario planner simulations."""

    organisation = current_user.organisation
    subscription_plan = organisation.subscription_plan
    allowed, used_count, monthly_limit = _planner_usage_status(organisation)

    carriers = _available_carriers_for_org(current_user.organisation_id)

    if request.method == "POST":
        form = ScenarioPlannerForm()
        form.carrier_id.choices = _carrier_form_choices(carriers)

        if not form.validate_on_submit():
            return render_template(
                "app/planner/index.html",
                form=form,
                available_carriers=carriers,
                simulation_result=None,
                comparison_results=None,
                form_submitted=False,
                today_date=date.today().isoformat(),
                subscription_plan=subscription_plan,
                planner_used_count=used_count,
                planner_monthly_limit=monthly_limit,
                port_suggestions=_port_suggestions(),
            )

        if not allowed:
            flash(
                "You've used all 5 scenario simulations for this month. Upgrade to Professional for unlimited simulations.",
                "warning",
            )
            return redirect("/settings/billing")

        carrier_id = _coerce_uuid(form.carrier_id.data)
        if carrier_id is None:
            flash("Please select a valid carrier.", "danger")
            return render_template(
                "app/planner/index.html",
                form=form,
                available_carriers=carriers,
                simulation_result=None,
                comparison_results=None,
                form_submitted=False,
                today_date=date.today().isoformat(),
                subscription_plan=subscription_plan,
                planner_used_count=used_count,
                planner_monthly_limit=monthly_limit,
                port_suggestions=_port_suggestions(),
            )

        simulation_params = {
            "origin_port_code": form.origin_port_code.data,
            "destination_port_code": form.destination_port_code.data,
            "mode": form.mode.data,
            "carrier_id": carrier_id,
            "estimated_ship_date": datetime.combine(form.estimated_ship_date.data, datetime.min.time()),
            "cargo_value_inr": float(form.cargo_value_inr.data or 0.0),
            "sla_requirement_days": int(form.sla_requirement_days.data or 1),
            "organisation_id": current_user.organisation_id,
        }

        simulation_result = simulation_service.run_simulation(
            simulation_params,
            db.session,
            current_app._get_current_object(),
        )

        origin_region = _port_code_to_region(form.origin_port_code.data)
        destination_region = _port_code_to_region(form.destination_port_code.data)

        comparison_candidate_ids = _choose_comparison_carriers(
            carrier_id,
            current_user.organisation_id,
            origin_region,
            destination_region,
            form.mode.data,
        )

        comparison_results = simulation_service.run_carrier_comparison_simulation(
            origin_port_code=form.origin_port_code.data,
            destination_port_code=form.destination_port_code.data,
            mode=form.mode.data,
            ship_date=datetime.combine(form.estimated_ship_date.data, datetime.min.time()),
            cargo_value_inr=float(form.cargo_value_inr.data or 0.0),
            sla_days=int(form.sla_requirement_days.data or 1),
            candidate_carrier_ids=comparison_candidate_ids,
            organisation_id=current_user.organisation_id,
            db_session=db.session,
            app_context=current_app._get_current_object(),
        )

        form_payload = {
            "origin_port_code": form.origin_port_code.data,
            "destination_port_code": form.destination_port_code.data,
            "mode": form.mode.data,
            "carrier_id": str(carrier_id),
            "estimated_ship_date": form.estimated_ship_date.data.isoformat() if form.estimated_ship_date.data else None,
            "cargo_value_inr": float(form.cargo_value_inr.data or 0.0),
            "sla_requirement_days": int(form.sla_requirement_days.data or 1),
        }

        session["planner_last_result"] = simulation_result
        session["planner_last_comparison"] = comparison_results
        session["planner_last_form"] = form_payload
        session["planner_last_result_timestamp"] = datetime.utcnow().isoformat()
        session.modified = True

        carrier_name = "Unknown Carrier"
        selected_carrier = db.session.query(Carrier).filter(Carrier.id == carrier_id).first()
        if selected_carrier is not None:
            carrier_name = selected_carrier.name

        AuditLog.log(
            db,
            event_type="scenario_simulation_run",
            description=(
                f"Scenario simulation run for {form.origin_port_code.data} to {form.destination_port_code.data} "
                f"using {carrier_name}."
            ),
            organisation_id=current_user.organisation_id,
            actor_user=current_user,
            metadata={
                "origin": form.origin_port_code.data,
                "destination": form.destination_port_code.data,
                "mode": form.mode.data,
                "carrier_name": carrier_name,
                "drs_at_departure": simulation_result.get("drs_at_departure"),
                "drs_at_arrival": simulation_result.get("drs_at_arrival"),
                "recommendation_level": simulation_result.get("booking_recommendation_level"),
            },
            ip_address=request.remote_addr,
        )

        _increment_planner_usage(organisation)
        _, used_count, monthly_limit = _planner_usage_status(organisation)

        return render_template(
            "app/planner/index.html",
            form=form,
            available_carriers=carriers,
            simulation_result=simulation_result,
            comparison_results=comparison_results,
            form_submitted=True,
            today_date=date.today().isoformat(),
            subscription_plan=subscription_plan,
            planner_used_count=used_count,
            planner_monthly_limit=monthly_limit,
            port_suggestions=_port_suggestions(),
        )

    form = ScenarioPlannerForm()
    form.carrier_id.choices = _carrier_form_choices(carriers)

    simulation_result = session.get("planner_last_result")
    comparison_results = session.get("planner_last_comparison")
    form_payload = session.get("planner_last_form")

    form_submitted = bool(simulation_result)
    if form_submitted:
        _apply_session_form_values(form, form_payload)

    return render_template(
        "app/planner/index.html",
        form=form,
        available_carriers=carriers,
        simulation_result=simulation_result if form_submitted else None,
        comparison_results=comparison_results if form_submitted else None,
        form_submitted=form_submitted,
        today_date=date.today().isoformat(),
        subscription_plan=subscription_plan,
        planner_used_count=used_count,
        planner_monthly_limit=monthly_limit,
        port_suggestions=_port_suggestions(),
    )


@planner_bp.post("/regenerate-narrative")
@role_required("admin", "manager")
def regenerate_narrative():
    """Force regenerate simulation AI narrative for submitted scenario params."""

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        payload = {}

    if not payload:
        payload = session.get("planner_last_form") or {}

    origin_port_code = (payload.get("origin_port_code") or "").strip().upper()
    destination_port_code = (payload.get("destination_port_code") or "").strip().upper()
    mode = (payload.get("mode") or "").strip().lower()
    carrier_id = _coerce_uuid(payload.get("carrier_id"))
    ship_date_raw = payload.get("estimated_ship_date")
    cargo_value_inr = float(payload.get("cargo_value_inr") or 0.0)
    sla_requirement_days = int(payload.get("sla_requirement_days") or 1)

    if not all([origin_port_code, destination_port_code, mode, carrier_id, ship_date_raw]):
        return jsonify({"success": False, "message": "Missing required simulation params."}), 400

    try:
        if isinstance(ship_date_raw, str):
            parsed_ship_date = datetime.fromisoformat(ship_date_raw)
        else:
            parsed_ship_date = datetime.combine(ship_date_raw, datetime.min.time())
    except Exception:
        return jsonify({"success": False, "message": "Invalid estimated ship date."}), 400

    accessible_carriers = {str(item.id) for item in _available_carriers_for_org(current_user.organisation_id)}
    if str(carrier_id) not in accessible_carriers:
        return jsonify({"success": False, "message": "Carrier is not accessible for your organisation."}), 403

    simulation_params = {
        "origin_port_code": origin_port_code,
        "destination_port_code": destination_port_code,
        "mode": mode,
        "carrier_id": carrier_id,
        "estimated_ship_date": parsed_ship_date,
        "cargo_value_inr": cargo_value_inr,
        "sla_requirement_days": sla_requirement_days,
        "organisation_id": current_user.organisation_id,
    }

    simulation_result = simulation_service.run_simulation(
        simulation_params,
        db.session,
        current_app._get_current_object(),
        include_ai_narrative=False,
    )

    ai_payload = simulation_service.generate_simulation_ai_narrative(
        simulation_params,
        simulation_result,
        current_app._get_current_object(),
        force_regenerate=True,
        user_id=current_user.id,
    )

    date_key = parsed_ship_date.strftime("%Y%m%d")
    content_key = (
        f"simulation_{current_user.organisation_id}_{origin_port_code}_{destination_port_code}_"
        f"{carrier_id}_{date_key}"
    )

    AuditLog.log(
        db,
        event_type="ai_content_regenerated",
        description=(
            f"Regenerated simulation narrative for {origin_port_code} -> {destination_port_code}."
        ),
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={
            "content_type": "simulation_narrative",
            "content_key": content_key,
            "triggered_by": current_user.email,
        },
        ip_address=request.remote_addr,
    )

    if isinstance(session.get("planner_last_result"), dict):
        last_result = dict(session.get("planner_last_result") or {})
        structured = ai_payload.get("structured_data") or {}
        last_result["ai_narrative"] = (
            f"{structured.get('risk_assessment_paragraph', '').strip()}\n\n"
            f"{structured.get('recommendation_paragraph', '').strip()}"
        ).strip()
        last_result["ai_narrative_markdown"] = ai_payload.get("formatted_response") or ""
        last_result["ai_narrative_html"] = ai_payload.get("formatted_html") or ""
        last_result["ai_narrative_structured"] = structured
        last_result["ai_narrative_fallback"] = not bool(ai_payload.get("success"))
        last_result["ai_narrative_served_stale"] = bool(ai_payload.get("served_stale"))
        last_result["ai_narrative_stale_warning"] = ai_payload.get("stale_warning")
        last_result["ai_regeneration_count"] = int(ai_payload.get("regeneration_count") or 0)
        last_result["ai_generated_at"] = ai_payload.get("generated_at")
        session["planner_last_result"] = last_result
        session["planner_last_result_timestamp"] = ai_payload.get("generated_at") or datetime.utcnow().isoformat()
        session.modified = True

    return jsonify(
        {
            "success": True,
            "content_html": ai_payload.get("formatted_html") or "",
            "raw_markdown": ai_payload.get("formatted_response") or "",
            "generated_at": ai_payload.get("generated_at") or datetime.utcnow().isoformat(),
            "regeneration_count": int(ai_payload.get("regeneration_count") or 0),
            "structured_data": ai_payload.get("structured_data") or {},
            "served_stale": bool(ai_payload.get("served_stale")),
            "stale_warning": ai_payload.get("stale_warning"),
        }
    )
