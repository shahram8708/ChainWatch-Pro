"""Multi-step onboarding wizard routes for ChainWatch Pro."""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy import func

from app.extensions import db
from app.forms.onboarding_forms import (
    OnboardingStep1Form,
    OnboardingStep2Form,
    OnboardingStep3Form,
    OnboardingStep4Form,
)
from app.models.audit_log import AuditLog
from app.models.carrier import Carrier
from app.models.shipment import Shipment
from app.services import carrier_tracker
from app.models.user import User
from app.services.notification import email_service
from app.utils.decorators import login_required, verified_required
from app.utils.helpers import generate_secure_temporary_password

logger = logging.getLogger(__name__)

onboarding_bp = Blueprint("onboarding", __name__, url_prefix="/onboarding")


def _get_onboarding_progress(user) -> int:
    """Return the current onboarding step completed for a user."""

    try:
        return int(getattr(user, "onboarding_step_completed", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _ensure_profile_data(organisation) -> dict:
    """Ensure org profile data is always a mutable dictionary."""

    profile = organisation.org_profile_data or {}
    if not isinstance(profile, dict):
        profile = {}
    organisation.org_profile_data = profile
    return profile


def _monthly_volume_midpoint(choice: str) -> int:
    """Map monthly shipment range label to midpoint integer."""

    mapping = {
        "Under 50": 25,
        "50-200": 125,
        "200-500": 350,
        "500-2000": 1250,
        "2000-10000": 6000,
        "Over 10000": 12000,
    }
    return mapping.get(choice, 125)


def _parse_datetime(value: str) -> datetime | None:
    """Parse date string across common onboarding CSV date formats."""

    if not value:
        return None

    candidate = value.strip()
    if not candidate:
        return None

    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]

    try:
        parsed_iso = datetime.fromisoformat(candidate.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        parsed_iso = None

    if parsed_iso is not None:
        return parsed_iso

    for dt_format in formats:
        try:
            return datetime.strptime(candidate, dt_format)
        except ValueError:
            continue

    return None


def _normalize_shipment_mode(value: str) -> str:
    """Convert user-provided mode strings to shipment enum-compatible values."""

    normalized = (value or "").strip().lower()
    mapping = {
        "ocean": "ocean_fcl",
        "ocean fcl": "ocean_fcl",
        "fcl": "ocean_fcl",
        "ocean lcl": "ocean_lcl",
        "lcl": "ocean_lcl",
        "air": "air",
        "air freight": "air",
        "road": "road",
        "road/truck": "road",
        "truck": "road",
        "rail": "rail",
        "multimodal": "multimodal",
    }
    return mapping.get(normalized, "multimodal")


def _normalize_carrier_mode(value: str) -> str:
    """Convert free-text mode values into carrier enum-compatible values."""

    shipment_mode = _normalize_shipment_mode(value)
    mapping = {
        "ocean_fcl": "ocean",
        "ocean_lcl": "ocean",
        "air": "air",
        "road": "road",
        "rail": "rail",
        "multimodal": "multimodal",
    }
    return mapping.get(shipment_mode, "multimodal")


def _normalize_port_code(value: str) -> str:
    """Convert any location text into a compact 5-char uppercase code for schema compatibility."""

    raw = (value or "").strip().upper()
    cleaned = "".join(char for char in raw if char.isalnum())
    if not cleaned:
        return "UNKWN"
    if len(cleaned) >= 5:
        return cleaned[:5]
    return cleaned.ljust(5, "X")


def _onboarding_guard():
    """Redirect users who already completed onboarding back to dashboard."""

    organisation = current_user.organisation
    if organisation and organisation.onboarding_complete:
        flash("Your team has already completed onboarding.", "info")
        return redirect("/dashboard")
    return None


def _mark_step_completed(step_number: int) -> None:
    """Persist onboarding step progression monotonically."""

    current_user.onboarding_step_completed = max(_get_onboarding_progress(current_user), step_number)


def _build_progress_payload(step_number: int) -> dict:
    """Build shared progress metadata passed to onboarding templates."""

    shipment_limit = current_user.organisation.shipment_limit
    return {
        "current_step": step_number,
        "completed_step": _get_onboarding_progress(current_user),
        "progress_percent": int((step_number / 4) * 100),
        "shipment_limit_label": "unlimited" if shipment_limit is None else str(shipment_limit),
    }


@onboarding_bp.route("/step1", methods=["GET", "POST"])
@login_required
@verified_required
def step1():
    """Capture initial shipping profile and baseline configuration metadata."""

    blocked = _onboarding_guard()
    if blocked:
        return blocked

    organisation = current_user.organisation
    form = OnboardingStep1Form()
    profile = _ensure_profile_data(organisation)

    if request.method == "GET" and _get_onboarding_progress(current_user) >= 1:
        form.industry.data = organisation.industry
        form.company_size.data = organisation.company_size_range
        if organisation.monthly_shipment_volume:
            midpoint_map = {
                25: "Under 50",
                125: "50-200",
                350: "200-500",
                1250: "500-2000",
                6000: "2000-10000",
                12000: "Over 10000",
            }
            form.monthly_shipment_volume.data = midpoint_map.get(
                int(organisation.monthly_shipment_volume),
                "50-200",
            )
        form.shipping_modes.data = profile.get("shipping_modes", [])
        form.primary_trade_lanes.data = profile.get("primary_trade_lanes", [])
        form.typical_cargo_types.data = profile.get("typical_cargo_types", [])
        form.current_visibility_tools.data = profile.get("current_visibility_tools", "carrier_portals_manual")

    if form.validate_on_submit():
        organisation.industry = form.industry.data
        organisation.company_size_range = form.company_size.data
        organisation.monthly_shipment_volume = _monthly_volume_midpoint(form.monthly_shipment_volume.data)

        profile["shipping_modes"] = form.shipping_modes.data
        profile["primary_trade_lanes"] = form.primary_trade_lanes.data
        profile["typical_cargo_types"] = form.typical_cargo_types.data
        profile["current_visibility_tools"] = form.current_visibility_tools.data
        profile["monthly_shipment_volume_label"] = form.monthly_shipment_volume.data

        _mark_step_completed(1)

        try:
            db.session.commit()
            return redirect(url_for("onboarding.step2"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed to save onboarding step 1")
            flash("We could not save Step 1 right now. Please try again.", "danger")

    return render_template(
        "onboarding/step1.html",
        form=form,
        **_build_progress_payload(1),
    )


@onboarding_bp.route("/step2/download-template", methods=["GET"])
@login_required
@verified_required
def step2_download_template():
    """Download shipment CSV template used for onboarding historical import."""

    blocked = _onboarding_guard()
    if blocked:
        return blocked

    template_stream = io.StringIO()
    writer = csv.writer(template_stream)
    writer.writerow(
        [
            "shipment_id",
            "carrier_name",
            "origin",
            "destination",
            "mode",
            "estimated_departure",
            "estimated_arrival",
            "actual_arrival",
        ]
    )
    writer.writerow(
        [
            "SHP-10021",
            "Maersk Line",
            "INNSA",
            "DEHAM",
            "Ocean FCL",
            "2026-03-08 09:00",
            "2026-03-28 18:00",
            "2026-03-29 06:10",
        ]
    )
    writer.writerow(
        [
            "SHP-10022",
            "DHL Express",
            "BLR",
            "DXB",
            "Air Freight",
            "2026-03-12 14:00",
            "2026-03-14 07:00",
            "",
        ]
    )

    csv_bytes = io.BytesIO(template_stream.getvalue().encode("utf-8"))
    csv_bytes.seek(0)

    return send_file(
        csv_bytes,
        mimetype="text/csv",
        as_attachment=True,
        download_name="chainwatchpro_onboarding_template.csv",
    )


@onboarding_bp.route("/step2", methods=["GET", "POST"])
@login_required
@verified_required
def step2():
    """Handle carrier setup and historical shipment baseline import."""

    blocked = _onboarding_guard()
    if blocked:
        return blocked

    if _get_onboarding_progress(current_user) < 1:
        flash("Please complete Step 1 first.", "warning")
        return redirect(url_for("onboarding.step1"))

    organisation = current_user.organisation
    profile = _ensure_profile_data(organisation)

    if Carrier.query.filter_by(is_global_carrier=True).count() == 0:
        Carrier.seed_global_carriers(db)

    if request.method == "GET" and request.args.get("skip") == "true":
        profile["setup_method"] = "skip"
        _mark_step_completed(2)
        try:
            db.session.commit()
            flash("Carrier setup skipped for now. You can configure carriers later in settings.", "info")
            return redirect(url_for("onboarding.step3"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed skipping onboarding step 2")
            flash("Could not skip Step 2 right now. Please try again.", "danger")

    form = OnboardingStep2Form()

    if request.method == "GET" and _get_onboarding_progress(current_user) >= 2:
        form.setup_method.data = profile.get("setup_method", "manual")
        form.selected_carriers.data = profile.get("selected_carrier_ids", [])

    if form.validate_on_submit():
        setup_method = form.setup_method.data

        if setup_method == "csv":
            csv_upload = form.csv_file.data
            csv_content = csv_upload.stream.read().decode("utf-8-sig") if csv_upload else ""
            reader = csv.DictReader(io.StringIO(csv_content))
            required_columns = {
                "shipment_id",
                "carrier_name",
                "origin",
                "destination",
                "mode",
                "estimated_departure",
                "estimated_arrival",
            }

            incoming_columns = {item.strip().lower() for item in (reader.fieldnames or [])}
            if not required_columns.issubset(incoming_columns):
                flash("Invalid CSV format. Please download the template and try again.", "danger")
                return render_template(
                    "onboarding/step2.html",
                    form=form,
                    **_build_progress_payload(2),
                )

            imported_count = 0
            skipped_invalid = 0
            skipped_limit = 0
            historical_performance_rows: list[dict] = []

            for row_index, row in enumerate(reader, start=1):
                if row_index > 200:
                    skipped_limit += 1
                    continue

                carrier_name = (row.get("carrier_name") or "").strip()
                shipment_ref = (row.get("shipment_id") or "").strip()

                if not carrier_name or not shipment_ref:
                    skipped_invalid += 1
                    continue

                est_departure = _parse_datetime(row.get("estimated_departure", ""))
                est_arrival = _parse_datetime(row.get("estimated_arrival", ""))
                actual_arrival = _parse_datetime(row.get("actual_arrival", ""))

                if est_departure is None or est_arrival is None:
                    skipped_invalid += 1
                    continue

                carrier = Carrier.query.filter(func.lower(Carrier.name) == carrier_name.lower()).first()
                if carrier is None:
                    carrier = Carrier(
                        name=carrier_name,
                        mode=_normalize_carrier_mode(row.get("mode", "")),
                        tracking_api_type="manual",
                        is_global_carrier=False,
                    )
                    db.session.add(carrier)
                    db.session.flush()

                shipment = Shipment(
                    organisation_id=organisation.id,
                    external_reference=shipment_ref,
                    carrier_id=carrier.id,
                    mode=_normalize_shipment_mode(row.get("mode", "")),
                    origin_port_code=_normalize_port_code(row.get("origin", "")),
                    destination_port_code=_normalize_port_code(row.get("destination", "")),
                    origin_address=(row.get("origin") or "").strip() or None,
                    destination_address=(row.get("destination") or "").strip() or None,
                    estimated_departure=est_departure,
                    estimated_arrival=est_arrival,
                    actual_arrival=actual_arrival,
                    status="delivered" if actual_arrival else "in_transit",
                )
                db.session.add(shipment)
                imported_count += 1

                historical_performance_rows.append(
                    {
                        "carrier_name": carrier_name,
                        "origin_port_code": _normalize_port_code(row.get("origin", "")),
                        "destination_port_code": _normalize_port_code(row.get("destination", "")),
                        "mode": _normalize_shipment_mode(row.get("mode", "")),
                        "estimated_arrival": est_arrival,
                        "actual_arrival": actual_arrival,
                    }
                )

            profile["setup_method"] = "csv"
            profile["historical_shipments_imported"] = imported_count

            _mark_step_completed(2)

            try:
                db.session.commit()
                flash(
                    f"Successfully imported {imported_count} historical shipments for baseline analysis.",
                    "success",
                )

                if historical_performance_rows:
                    try:
                        carrier_tracker.ingest_historical_performance_data(
                            carrier_name=None,
                            organisation_id=organisation.id,
                            shipment_data_rows=historical_performance_rows,
                            db_session=db.session,
                        )
                    except Exception:
                        db.session.rollback()
                        logger.exception("Carrier performance ingestion failed during onboarding Step 2")

                if skipped_limit > 0:
                    flash("Only the first 200 rows were imported. Additional rows were skipped.", "warning")
                if skipped_invalid > 0:
                    flash(f"Skipped {skipped_invalid} invalid row(s) with missing or malformed data.", "warning")
                return redirect(url_for("onboarding.step3"))
            except Exception:
                db.session.rollback()
                logger.exception("Failed importing onboarding CSV data")
                flash("CSV import failed. Please review your file and try again.", "danger")

        elif setup_method == "manual":
            selected_carrier_ids = list(dict.fromkeys(form.selected_carriers.data or []))
            profile["setup_method"] = "manual"
            profile["selected_carrier_ids"] = selected_carrier_ids

            _mark_step_completed(2)

            try:
                db.session.commit()
                return redirect(url_for("onboarding.step3"))
            except Exception:
                db.session.rollback()
                logger.exception("Failed saving manual carrier selection")
                flash("Could not save selected carriers. Please try again.", "danger")

        else:
            profile["setup_method"] = "skip"
            _mark_step_completed(2)
            try:
                db.session.commit()
                return redirect(url_for("onboarding.step3"))
            except Exception:
                db.session.rollback()
                logger.exception("Failed saving skip state for onboarding step 2")
                flash("Could not continue right now. Please try again.", "danger")

    return render_template(
        "onboarding/step2.html",
        form=form,
        global_carriers=Carrier.query.filter_by(is_global_carrier=True).order_by(Carrier.name.asc()).all(),
        **_build_progress_payload(2),
    )


@onboarding_bp.route("/step3", methods=["GET", "POST"])
@login_required
@verified_required
def step3():
    """Capture alert policy, thresholds, and team invitation preferences."""

    blocked = _onboarding_guard()
    if blocked:
        return blocked

    if _get_onboarding_progress(current_user) < 2:
        flash("Please complete Step 2 first.", "warning")
        return redirect(url_for("onboarding.step2"))

    organisation = current_user.organisation
    profile = _ensure_profile_data(organisation)

    if request.method == "GET" and request.args.get("skip") == "true":
        profile["alert_preferences"] = {
            "alert_email": True,
            "alert_sms": False,
            "alert_webhook": False,
            "webhook_url": "",
            "drs_warning_threshold": 60,
            "drs_critical_threshold": 80,
            "alert_frequency": "immediate",
        }
        _mark_step_completed(3)
        try:
            db.session.commit()
            flash("Alert preferences skipped. Defaults were applied.", "info")
            return redirect(url_for("onboarding.step4"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed skipping onboarding step 3")
            flash("Could not skip Step 3 right now. Please try again.", "danger")

    form = OnboardingStep3Form()

    if request.method == "GET" and _get_onboarding_progress(current_user) >= 3:
        alert_preferences = profile.get("alert_preferences", {})
        form.alert_email.data = bool(alert_preferences.get("alert_email", True))
        form.alert_sms.data = bool(alert_preferences.get("alert_sms", False))
        form.alert_webhook.data = bool(alert_preferences.get("alert_webhook", False))
        form.webhook_url.data = alert_preferences.get("webhook_url", "")
        form.drs_warning_threshold.data = int(alert_preferences.get("drs_warning_threshold", 60))
        form.drs_critical_threshold.data = int(alert_preferences.get("drs_critical_threshold", 80))
        form.alert_frequency.data = alert_preferences.get("alert_frequency", "immediate")
        saved_invites = profile.get("pending_invites", [])
        for idx, email in enumerate(saved_invites[:5]):
            form.team_invite_emails[idx].data = email
        form.team_role.data = profile.get("team_invite_role", "manager")

    if form.validate_on_submit():
        profile["alert_preferences"] = {
            "alert_email": bool(form.alert_email.data),
            "alert_sms": bool(form.alert_sms.data),
            "alert_webhook": bool(form.alert_webhook.data),
            "webhook_url": (form.webhook_url.data or "").strip(),
            "drs_warning_threshold": int(form.drs_warning_threshold.data),
            "drs_critical_threshold": int(form.drs_critical_threshold.data),
            "alert_frequency": form.alert_frequency.data,
        }
        profile["team_invite_role"] = form.team_role.data

        invite_emails = []
        invited_count = 0

        for field in form.team_invite_emails.entries:
            email_raw = (field.data or "").strip().lower()
            if not email_raw:
                continue

            invite_emails.append(email_raw)
            existing_user = User.query.filter_by(email=email_raw).first()
            if existing_user is not None:
                continue

            invited_user = User(
                email=email_raw,
                first_name="Team",
                last_name="Member",
                role=form.team_role.data,
                organisation_id=organisation.id,
                is_verified=True,
                must_change_password=True,
                invited_by_user_id=current_user.id,
                invitation_sent_at=datetime.utcnow(),
                account_source="manual_invite",
                onboarding_step_completed=4,
            )
            temp_password = generate_secure_temporary_password()
            invited_user.set_password(temp_password)
            invited_user.temporary_password_hash = invited_user.password_hash

            db.session.add(invited_user)
            db.session.flush()

            db.session.add(
                AuditLog(
                    organisation_id=organisation.id,
                    actor_user_id=current_user.id,
                    actor_label=current_user.email,
                    event_type="team_member_invited",
                    description=f"{current_user.email} invited {email_raw} as {form.team_role.data}.",
                    metadata_json={
                        "invited_email": email_raw,
                        "invited_role": form.team_role.data,
                        "account_source": "manual_invite",
                    },
                    ip_address=request.remote_addr,
                )
            )

            email_service.send_team_invitation_email_with_credentials(
                current_user,
                invited_user,
                temp_password,
                current_app._get_current_object(),
            )
            invited_count += 1

        profile["pending_invites"] = invite_emails
        _mark_step_completed(3)

        try:
            db.session.commit()
            if invited_count:
                flash(f"Invitations sent to {invited_count} new team member(s).", "success")
            return redirect(url_for("onboarding.step4"))
        except Exception:
            db.session.rollback()
            logger.exception("Failed saving onboarding step 3")
            flash("Could not save Step 3 right now. Please try again.", "danger")

    return render_template(
        "onboarding/step3.html",
        form=form,
        **_build_progress_payload(3),
    )


@onboarding_bp.route("/step4", methods=["GET", "POST"])
@login_required
@verified_required
def step4():
    """Finalize dashboard defaults and complete onboarding."""

    blocked = _onboarding_guard()
    if blocked:
        return blocked

    if _get_onboarding_progress(current_user) < 3:
        flash("Please complete Step 3 first.", "warning")
        return redirect(url_for("onboarding.step3"))

    organisation = current_user.organisation
    profile = _ensure_profile_data(organisation)

    if request.method == "GET" and request.args.get("skip") == "true":
        profile["dashboard_preferences"] = {
            "kpi_cards": [
                "active_shipments",
                "critical_alerts",
                "warning_alerts",
                "otd_rate",
            ],
            "default_risk_filter": "all",
            "default_mode_filter": "all",
            "default_sort": "drs_desc",
            "default_page_size": "25",
            "timezone": "Asia/Kolkata",
        }
        organisation.onboarding_complete = True
        current_user.timezone = "Asia/Kolkata"
        current_user.onboarding_step_completed = 4
        db.session.add(
            AuditLog(
                organisation_id=organisation.id,
                actor_user_id=current_user.id,
                actor_label=current_user.email,
                event_type="onboarding_completed",
                description="Onboarding completed via skip path on step 4.",
                metadata_json={"source": "skip"},
                ip_address=request.remote_addr,
            )
        )
        try:
            db.session.commit()
            flash("Welcome to ChainWatch Pro! Your workspace is ready.", "success")
            return redirect("/dashboard")
        except Exception:
            db.session.rollback()
            logger.exception("Failed completing onboarding via skip path")
            flash("Could not complete onboarding right now. Please try again.", "danger")

    form = OnboardingStep4Form()

    if request.method == "GET":
        dashboard_prefs = profile.get("dashboard_preferences", {})
        selected_cards = set(dashboard_prefs.get("kpi_cards", []))
        if selected_cards:
            form.show_active_shipments_card.data = "active_shipments" in selected_cards
            form.show_critical_alerts_card.data = "critical_alerts" in selected_cards
            form.show_warning_alerts_card.data = "warning_alerts" in selected_cards
            form.show_otd_rate_card.data = "otd_rate" in selected_cards
            form.show_financial_exposure_card.data = "financial_exposure" in selected_cards
        form.default_risk_filter.data = dashboard_prefs.get("default_risk_filter", "all")
        form.default_mode_filter.data = dashboard_prefs.get("default_mode_filter", "all")
        form.default_sort.data = dashboard_prefs.get("default_sort", "drs_desc")
        form.default_page_size.data = dashboard_prefs.get("default_page_size", "25")
        form.timezone.data = dashboard_prefs.get("timezone", "Asia/Kolkata")

    if form.validate_on_submit():
        selected_kpis = []
        if form.show_active_shipments_card.data:
            selected_kpis.append("active_shipments")
        if form.show_critical_alerts_card.data:
            selected_kpis.append("critical_alerts")
        if form.show_warning_alerts_card.data:
            selected_kpis.append("warning_alerts")
        if form.show_otd_rate_card.data:
            selected_kpis.append("otd_rate")
        if form.show_financial_exposure_card.data:
            selected_kpis.append("financial_exposure")

        profile["dashboard_preferences"] = {
            "kpi_cards": selected_kpis,
            "default_risk_filter": form.default_risk_filter.data,
            "default_mode_filter": form.default_mode_filter.data,
            "default_sort": form.default_sort.data,
            "default_page_size": form.default_page_size.data,
            "timezone": form.timezone.data,
        }

        organisation.onboarding_complete = True
        current_user.onboarding_step_completed = 4
        current_user.timezone = form.timezone.data

        db.session.add(
            AuditLog(
                organisation_id=organisation.id,
                actor_user_id=current_user.id,
                actor_label=current_user.email,
                event_type="onboarding_completed",
                description="Completed onboarding setup wizard.",
                metadata_json={"selected_kpis": selected_kpis},
                ip_address=request.remote_addr,
            )
        )

        try:
            db.session.commit()
            flash("🎉 Welcome to ChainWatch Pro! Your workspace is ready.", "success")
            return redirect("/dashboard")
        except Exception:
            db.session.rollback()
            logger.exception("Failed saving onboarding step 4")
            flash("Could not complete onboarding right now. Please try again.", "danger")

    return render_template(
        "onboarding/step4.html",
        form=form,
        **_build_progress_payload(4),
    )
