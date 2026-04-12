"""Performance report generation, preview, and download routes."""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.forms.settings_forms import ReportGenerationForm
from app.models.audit_log import AuditLog
from app.services import report_service
from app.utils.decorators import login_required, verified_required


reports_bp = Blueprint("reports", __name__)


STARTER_MONTHLY_REPORT_LIMIT = 5


def _first_day_next_month(today: date) -> date:
    if today.month == 12:
        return date(today.year + 1, 1, 1)
    return date(today.year, today.month + 1, 1)


def _ensure_profile_data(organisation) -> dict:
    profile = organisation.org_profile_data or {}
    if not isinstance(profile, dict):
        profile = {}
    organisation.org_profile_data = profile
    return profile


def _ensure_report_usage_window(organisation) -> tuple[int, date]:
    today = datetime.utcnow().date()
    profile = _ensure_profile_data(organisation)

    used = int(profile.get("report_exports_this_month", 0) or 0)
    reset_raw = profile.get("report_exports_reset_date")

    try:
        reset_date = datetime.fromisoformat(str(reset_raw)).date() if reset_raw else None
    except ValueError:
        reset_date = None

    if reset_date is None:
        reset_date = _first_day_next_month(today)
        profile["report_exports_this_month"] = used
        profile["report_exports_reset_date"] = reset_date.isoformat()
        flag_modified(organisation, "org_profile_data")
        db.session.commit()

    if today >= reset_date:
        used = 0
        reset_date = _first_day_next_month(today)
        profile["report_exports_this_month"] = 0
        profile["report_exports_reset_date"] = reset_date.isoformat()
        flag_modified(organisation, "org_profile_data")
        db.session.commit()

    return used, reset_date


def _recent_reports_for_org(organisation_id, generated_reports: list[dict]) -> list[dict]:
    reports = [
        report
        for report in generated_reports
        if str(report.get("organisation_id")) == str(organisation_id)
    ]

    reports.sort(
        key=lambda item: item.get("generated_at", ""),
        reverse=True,
    )
    return reports[:20]


def _serialize_preview(report_type: str, data) -> dict:
    if report_type == "monthly_performance":
        return {
            "summary": {
                "total_shipments": data.get("total_shipments", 0),
                "otd_rate": round(float(data.get("otd_rate", 0.0)) * 100.0, 2),
                "average_drs": data.get("average_drs", 0.0),
                "critical_alerts_generated": data.get("critical_alerts_generated", 0),
                "estimated_savings_inr": data.get("estimated_savings_inr", 0.0),
            },
            "shipment_detail": (data.get("shipment_detail") or [])[:10],
            "carrier_performance": (data.get("carrier_performance") or [])[:10],
            "alerts": (data.get("alerts") or [])[:10],
            "week_otd_trend": (data.get("week_otd_trend") or [])[:10],
        }

    if report_type == "carrier_comparison":
        return {
            "summary": {
                "carriers": len(data),
                "top_otd": round(max((item.get("otd_rate", 0.0) for item in data), default=0.0) * 100.0, 2),
            },
            "rows": data[:10],
        }

    if report_type == "lane_risk_analysis":
        return {
            "summary": {
                "lanes": len(data),
                "highest_drs": max((item.get("average_drs", 0.0) for item in data), default=0.0),
            },
            "rows": data[:10],
        }

    if report_type == "disruption_audit":
        return {
            "summary": {
                "shipments": len(data),
                "highest_peak_drs": max((item.get("peak_drs", 0.0) for item in data), default=0.0),
            },
            "rows": data[:10],
        }

    return {"summary": {}, "rows": []}


@reports_bp.before_request
@login_required
@verified_required
def _guards():
    """Apply report route authentication guards."""


@reports_bp.get("/")
def index():
    """Render reports page with generation form and recent report list."""

    organisation = current_user.organisation
    profile = _ensure_profile_data(organisation)

    generated_reports = profile.get("generated_reports", [])
    if not isinstance(generated_reports, list):
        generated_reports = []

    recent_reports = _recent_reports_for_org(current_user.organisation_id, generated_reports)

    form = ReportGenerationForm()

    today_date = datetime.utcnow().date()
    default_start_date = today_date - timedelta(days=30)

    used_count, reset_date = _ensure_report_usage_window(organisation)
    report_usage = {
        "used": used_count,
        "limit": STARTER_MONTHLY_REPORT_LIMIT if organisation.subscription_plan == "starter" else None,
        "reset_date": reset_date.isoformat() if reset_date else None,
    }

    return render_template(
        "app/reports/index.html",
        report_types=report_service.REPORT_TYPES,
        form=form,
        recent_reports=recent_reports,
        today_date=today_date,
        default_start_date=default_start_date,
        report_usage=report_usage,
    )


@reports_bp.post("/generate")
def generate():
    """Queue asynchronous report generation task."""

    form = ReportGenerationForm()
    if not form.validate_on_submit():
        return jsonify({"success": False, "errors": form.errors}), 400

    report_type = form.report_type.data
    start_date = form.start_date.data
    end_date = form.end_date.data
    output_format = form.output_format.data

    if end_date < start_date:
        return jsonify({"success": False, "error": "End date must be after start date."}), 400
    if (end_date - start_date).days > 365:
        return jsonify({"success": False, "error": "Date range cannot exceed 365 days."}), 400

    organisation = current_user.organisation
    profile = _ensure_profile_data(organisation)

    used_count, reset_date = _ensure_report_usage_window(organisation)
    if organisation.subscription_plan == "starter" and used_count >= STARTER_MONTHLY_REPORT_LIMIT:
        flash("Starter plan allows up to 5 report exports per month. Upgrade to continue exporting.", "warning")
        return jsonify({"success": False, "error": "Monthly report export limit reached."}), 403

    from celery_worker import generate_report_task

    try:
        task = generate_report_task.apply_async(
            kwargs={
                "report_type": report_type,
                "organisation_id": str(current_user.organisation_id),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "output_format": output_format,
                "requesting_user_id": str(current_user.id),
            }
        )
    except Exception as exc:
        current_app.logger.exception("Failed to enqueue report task.")
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Report queue is temporarily unavailable. Please try again in a moment.",
                    "details": str(exc),
                }
            ),
            503,
        )

    immediate_status = "queued"
    immediate_download_url = None
    immediate_error = None

    if task.ready():
        if task.successful():
            immediate_status = "completed"
            if isinstance(task.result, dict):
                immediate_download_url = task.result.get("download_url")
        else:
            immediate_status = "failed"
            if isinstance(task.result, dict):
                immediate_error = task.result.get("error")
            elif task.result is not None:
                immediate_error = str(task.result)

    # In eager mode, task execution may have updated org_profile_data already.
    # Reload before mutating report_jobs to avoid clobbering generated_reports metadata.
    try:
        db.session.expire(organisation, ["org_profile_data"])
    except Exception:
        db.session.expire_all()
    profile = _ensure_profile_data(organisation)

    if organisation.subscription_plan == "starter":
        profile["report_exports_this_month"] = used_count + 1
        profile["report_exports_reset_date"] = reset_date.isoformat()

    report_jobs = profile.get("report_jobs", {})
    if not isinstance(report_jobs, dict):
        report_jobs = {}

    report_jobs[task.id] = {
        "organisation_id": str(current_user.organisation_id),
        "report_type": report_type,
        "output_format": output_format,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "requested_at": datetime.utcnow().isoformat(),
        "requested_by": str(current_user.id),
        "status": immediate_status,
        "download_url": immediate_download_url,
        "error": immediate_error,
        "completed_at": datetime.utcnow().isoformat() if immediate_status in {"completed", "failed"} else None,
    }
    profile["report_jobs"] = report_jobs

    organisation.org_profile_data = profile
    flag_modified(organisation, "org_profile_data")
    db.session.commit()

    AuditLog.log(
        db,
        event_type="report_generation_started",
        description=f"Queued {report_type} report generation.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={
            "report_type": report_type,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "output_format": output_format,
        },
        ip_address=request.remote_addr,
    )

    return jsonify(
        {
            "success": True,
            "job_id": task.id,
            "message": "Report generation started. This typically takes 10-30 seconds.",
        }
    )


@reports_bp.get("/download/<filename>")
def download(filename: str):
    """Download a generated report if it belongs to the current organisation."""

    organisation = current_user.organisation
    profile = _ensure_profile_data(organisation)
    generated_reports = profile.get("generated_reports", [])
    if not isinstance(generated_reports, list):
        generated_reports = []

    report_meta = None
    for item in generated_reports:
        if item.get("filename") == filename and str(item.get("organisation_id")) == str(current_user.organisation_id):
            report_meta = item
            break

    if report_meta is None:
        report_jobs = profile.get("report_jobs", {})
        if isinstance(report_jobs, dict):
            for job in report_jobs.values():
                if not isinstance(job, dict):
                    continue
                if str(job.get("organisation_id")) != str(current_user.organisation_id):
                    continue
                if str(job.get("status") or "").strip().lower() != "completed":
                    continue

                candidate_name = job.get("filename")
                candidate_url = str(job.get("download_url") or "")
                if candidate_name == filename or candidate_url.endswith(f"/{filename}"):
                    report_meta = {
                        "filename": filename,
                        "report_type": job.get("report_type"),
                    }
                    break

    if report_meta is None:
        abort(403)

    if filename != os.path.basename(filename):
        abort(403)

    output_dir = current_app.config.get(
        "REPORT_OUTPUT_DIR",
        os.path.join(current_app.root_path, "..", "static", "reports"),
    )
    file_path = os.path.join(output_dir, filename)
    if not os.path.exists(file_path):
        abort(404)

    extension = os.path.splitext(filename)[1].lower()
    if extension == ".pdf":
        mimetype = "application/pdf"
    elif extension == ".xlsx":
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        mimetype = "application/octet-stream"

    AuditLog.log(
        db,
        event_type="report_downloaded",
        description=f"Downloaded report file {filename}.",
        organisation_id=current_user.organisation_id,
        actor_user=current_user,
        metadata={
            "filename": filename,
            "report_type": report_meta.get("report_type"),
        },
        ip_address=request.remote_addr,
    )

    return send_file(file_path, mimetype=mimetype, as_attachment=True, download_name=filename)


@reports_bp.get("/preview/<report_type>")
def preview(report_type: str):
    """Return JSON preview data for a selected report type and range."""

    if report_type not in report_service.REPORT_TYPES:
        return jsonify({"success": False, "error": "Invalid report type."}), 400

    start_raw = request.args.get("start_date")
    end_raw = request.args.get("end_date")

    try:
        start_date = datetime.strptime(start_raw, "%Y-%m-%d") if start_raw else datetime.utcnow() - timedelta(days=30)
        end_date = datetime.strptime(end_raw, "%Y-%m-%d") if end_raw else datetime.utcnow()
    except ValueError:
        return jsonify({"success": False, "error": "Invalid date format. Use YYYY-MM-DD."}), 400

    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    if end_date < start_date:
        return jsonify({"success": False, "error": "End date must be after start date."}), 400

    generator_map = {
        "monthly_performance": report_service._generate_monthly_performance_data,
        "carrier_comparison": report_service._generate_carrier_comparison_data,
        "lane_risk_analysis": report_service._generate_lane_risk_data,
        "disruption_audit": report_service._generate_disruption_audit_data,
    }

    data = generator_map[report_type](
        current_user.organisation_id,
        start_date,
        end_date,
        db.session,
    )

    return jsonify(
        {
            "success": True,
            "report_type": report_type,
            "preview": _serialize_preview(report_type, data),
        }
    )
