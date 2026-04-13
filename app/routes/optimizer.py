"""Route Optimizer blueprint for at-risk shipment rerouting decisions."""

from __future__ import annotations

import uuid
from datetime import datetime

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.extensions import db
from app.forms.optimizer_forms import OptimizerShipmentSelectorForm, RouteDecisionForm
from app.models.alert import Alert
from app.models.audit_log import AuditLog
from app.models.disruption_score import DisruptionScore
from app.models.route_recommendation import RouteRecommendation
from app.models.shipment import Shipment
from app.utils.decorators import login_required, role_required, verified_required

optimizer_bp = Blueprint("optimizer", __name__, url_prefix="/optimizer")


@optimizer_bp.before_request
@login_required
@verified_required
def _optimizer_guards():
    """Apply auth guards to optimizer routes."""


def _coerce_uuid(value: str | None) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _shipment_for_org_or_404(shipment_id: uuid.UUID) -> Shipment:
    shipment = Shipment.query.filter_by(id=shipment_id).first_or_404()
    if shipment.organisation_id != current_user.organisation_id:
        abort(403)
    return shipment


@optimizer_bp.get("")
def index():
    """Render optimizer workspace for at-risk shipment recommendations."""

    at_risk_shipments = (
        Shipment.query.filter(
            Shipment.organisation_id == current_user.organisation_id,
            Shipment.is_archived.is_(False),
            Shipment.status.in_(["pending", "in_transit", "delayed", "at_customs"]),
            Shipment.disruption_risk_score >= 60,
        )
        .order_by(Shipment.disruption_risk_score.desc(), Shipment.updated_at.desc())
        .all()
    )

    selector_form = OptimizerShipmentSelectorForm(request.args)
    selector_form.set_shipment_choices(at_risk_shipments)

    optimization_history_subquery = (
        db.session.query(
            RouteRecommendation.shipment_id.label("shipment_id"),
            db.func.max(RouteRecommendation.created_at).label("last_optimized_at"),
            db.func.count(RouteRecommendation.id).label("total_recommendations"),
        )
        .join(Shipment, Shipment.id == RouteRecommendation.shipment_id)
        .filter(
            Shipment.organisation_id == current_user.organisation_id,
            Shipment.is_archived.is_(False),
        )
        .group_by(RouteRecommendation.shipment_id)
        .subquery()
    )

    recent_optimization_history = (
        db.session.query(
            Shipment.id.label("shipment_id"),
            Shipment.external_reference.label("external_reference"),
            Shipment.origin_port_code.label("origin_port_code"),
            Shipment.destination_port_code.label("destination_port_code"),
            Shipment.status.label("shipment_status"),
            Shipment.disruption_risk_score.label("drs_score"),
            optimization_history_subquery.c.last_optimized_at,
            optimization_history_subquery.c.total_recommendations,
        )
        .join(optimization_history_subquery, optimization_history_subquery.c.shipment_id == Shipment.id)
        .order_by(optimization_history_subquery.c.last_optimized_at.desc())
        .all()
    )

    selected_shipment = None
    selected_shipment_id = _coerce_uuid(request.args.get("shipment_id"))
    if selected_shipment_id:
        selected_shipment = Shipment.query.filter(
            Shipment.id == selected_shipment_id,
            Shipment.organisation_id == current_user.organisation_id,
        ).first()

    recommendations: list[RouteRecommendation] = []
    latest_drs = None
    latest_alert = None

    if selected_shipment is not None:
        recommendations = (
            RouteRecommendation.query.filter(
                RouteRecommendation.shipment_id == selected_shipment.id,
                RouteRecommendation.status.in_(["pending", "approved", "dismissed", "expired"]),
            )
            .order_by(RouteRecommendation.option_label.asc())
            .all()
        )

        latest_drs = (
            DisruptionScore.query.filter(DisruptionScore.shipment_id == selected_shipment.id)
            .order_by(DisruptionScore.computed_at.desc())
            .first()
        )

        latest_alert = (
            Alert.query.filter(
                Alert.organisation_id == current_user.organisation_id,
                Alert.shipment_id == selected_shipment.id,
            )
            .order_by(Alert.created_at.desc())
            .first()
        )

    decision_form = RouteDecisionForm()

    return render_template(
        "app/optimizer/index.html",
        selector_form=selector_form,
        at_risk_shipments=at_risk_shipments,
        recent_optimization_history=recent_optimization_history,
        selected_shipment=selected_shipment,
        recommendations=recommendations,
        latest_drs=latest_drs,
        latest_alert=latest_alert,
        decision_form=decision_form,
    )


@optimizer_bp.post("/<uuid:rec_id>/approve")
@role_required("admin", "manager")
def approve_recommendation(rec_id: uuid.UUID):
    """Approve a pending recommendation from the optimizer full-page flow."""

    form = RouteDecisionForm()
    if not form.validate_on_submit():
        flash("Invalid route decision form submission.", "danger")
        return redirect(url_for("optimizer.index", shipment_id=request.form.get("shipment_id")))

    if str(rec_id) != (form.recommendation_id.data or "").strip():
        flash("Recommendation ID mismatch.", "danger")
        return redirect(url_for("optimizer.index", shipment_id=request.form.get("shipment_id")))

    recommendation = (
        RouteRecommendation.query.join(Shipment, Shipment.id == RouteRecommendation.shipment_id)
        .filter(
            RouteRecommendation.id == rec_id,
            Shipment.organisation_id == current_user.organisation_id,
        )
        .first()
    )

    if recommendation is None:
        flash("Recommendation not found.", "danger")
        return redirect(url_for("optimizer.index"))

    recommendation.status = "approved"
    recommendation.decided_by = current_user.id
    recommendation.decided_at = datetime.utcnow()
    recommendation.decision_notes = (form.decision_notes.data or "").strip() or None

    (
        RouteRecommendation.query.filter(
            RouteRecommendation.shipment_id == recommendation.shipment_id,
            RouteRecommendation.id != recommendation.id,
            RouteRecommendation.status == "pending",
        ).update({RouteRecommendation.status: "dismissed"}, synchronize_session=False)
    )

    db.session.commit()

    AuditLog.log(
        db,
        event_type="reroute_approved",
        description=f"Approved route option {recommendation.option_label} from optimizer.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        shipment_id=recommendation.shipment_id,
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

        compute_disruption_scores_single.apply_async(args=[str(recommendation.shipment_id)], countdown=30, queue="high")
    except Exception:
        pass

    flash("Route recommendation approved successfully.", "success")
    return redirect(url_for("optimizer.index", shipment_id=recommendation.shipment_id))


@optimizer_bp.post("/<uuid:rec_id>/dismiss")
@role_required("admin", "manager")
def dismiss_recommendation(rec_id: uuid.UUID):
    """Dismiss a pending recommendation from the optimizer full-page flow."""

    form = RouteDecisionForm()
    if not form.validate_on_submit():
        flash("Invalid route decision form submission.", "danger")
        return redirect(url_for("optimizer.index", shipment_id=request.form.get("shipment_id")))

    if str(rec_id) != (form.recommendation_id.data or "").strip():
        flash("Recommendation ID mismatch.", "danger")
        return redirect(url_for("optimizer.index", shipment_id=request.form.get("shipment_id")))

    recommendation = (
        RouteRecommendation.query.join(Shipment, Shipment.id == RouteRecommendation.shipment_id)
        .filter(
            RouteRecommendation.id == rec_id,
            Shipment.organisation_id == current_user.organisation_id,
        )
        .first()
    )

    if recommendation is None:
        flash("Recommendation not found.", "danger")
        return redirect(url_for("optimizer.index"))

    recommendation.status = "dismissed"
    recommendation.decided_by = current_user.id
    recommendation.decided_at = datetime.utcnow()
    recommendation.decision_notes = (form.decision_notes.data or "").strip() or None

    db.session.commit()

    AuditLog.log(
        db,
        event_type="reroute_dismissed",
        description=f"Dismissed route option {recommendation.option_label} from optimizer.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        shipment_id=recommendation.shipment_id,
        recommendation_id=recommendation.id,
        metadata={
            "option_label": recommendation.option_label,
            "decision_notes": recommendation.decision_notes,
        },
        ip_address=request.remote_addr,
    )

    flash("Route recommendation dismissed.", "info")
    return redirect(url_for("optimizer.index", shipment_id=recommendation.shipment_id))


@optimizer_bp.get("/trigger/<uuid:shipment_id>")
@role_required("admin", "manager")
def trigger_generation(shipment_id: uuid.UUID):
    """Manually trigger route alternative generation for an at-risk shipment."""

    shipment = Shipment.query.filter(
        Shipment.id == shipment_id,
        Shipment.organisation_id == current_user.organisation_id,
    ).first()
    if shipment is None:
        return jsonify({"success": False, "message": "Shipment not found."}), 404

    from celery_worker import generate_route_alternatives_for_shipment

    generate_route_alternatives_for_shipment.apply_async(args=[str(shipment.id)], queue="low")
    return (
        jsonify(
            {
                "success": True,
                "message": "Route alternatives are being generated. Refresh in 30 seconds.",
            }
        ),
        200,
    )
