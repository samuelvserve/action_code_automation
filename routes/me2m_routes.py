"""ME2M upload, progress, history, statistics and authorized downloads."""
import json
import uuid
from datetime import date, datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from config import ME2M_LOG_DIR, ME2M_MAX_UPLOAD_BYTES, ME2M_OUTPUT_DIR, ME2M_UPLOAD_DIR, RUN_STATUSES
from database import db
from me2m_pipeline import CATEGORIES, REPORT_TIMEZONE, report_today
from services import me2m_runner
from services.dashboard_stats import chart_data
from utils.auth import current_user, login_required, user_allowed_codes


def _visible_runs(user):
    if user["role"] == "admin":
        return db.query("SELECT * FROM me2m_runs ORDER BY id DESC")
    return db.query("SELECT * FROM me2m_runs WHERE triggered_by=? ORDER BY id DESC", (user["username"],))


def _get_run(run_id):
    run = db.query_one("SELECT * FROM me2m_runs WHERE id=?", (run_id,))
    if not run:
        abort(404)
    user = current_user()
    if not user or (user["role"] != "admin" and run["triggered_by"] != user["username"]):
        abort(403)
    return run


def _summary(runs, group=""):
    counts = {c: 0 for c in CATEGORIES}
    values = {c: 0.0 for c in CATEGORIES}
    groups = {}
    total_rows = 0
    total_value = 0.0
    for run in runs:
        if run["status"] != "success":
            continue
        by_group = json.loads(run["group_stats"] or "{}")
        for name, result in by_group.items():
            if group and name != group:
                continue
            agg = groups.setdefault(name, {"rows": 0, "net_order_value": 0.0,
                                          "counts": {c: 0 for c in CATEGORIES},
                                          "values": {c: 0.0 for c in CATEGORIES}})
            agg["rows"] += result["rows"]
            agg["net_order_value"] += result["net_order_value"]
            total_rows += result["rows"]
            total_value += result["net_order_value"]
            for c in CATEGORIES:
                counts[c] += result["counts"].get(c, 0)
                values[c] += result["values"].get(c, 0.0)
                agg["counts"][c] += result["counts"].get(c, 0)
                agg["values"][c] += result["values"].get(c, 0.0)
    return {
        "summary_counts": counts, "summary_values": values, "total_rows": total_rows,
        "total_value": total_value, "categories": CATEGORIES,
        "group_rows": sorted(groups.items()),
    }


@login_required
def process_page():
    user = current_user()
    as_of = report_today().isoformat()
    if request.method == "POST":
        source = None
        try:
            if request.content_length and request.content_length > ME2M_MAX_UPLOAD_BYTES + 1024 * 1024:
                raise ValueError("The ME2M workbook must be 50 MB or smaller.")
            as_of = request.form.get("report_date", "")
            try:
                as_of = date.fromisoformat(as_of).isoformat()
            except (TypeError, ValueError):
                raise ValueError("Choose a valid reporting date.") from None
            upload = request.files.get("workbook")
            if not upload or not upload.filename:
                raise ValueError("Choose an ME2M workbook to upload.")
            if not upload.filename.lower().endswith(".xlsx"):
                raise ValueError("Upload an .xlsx workbook.")
            name = secure_filename(upload.filename) or "ME2M.xlsx"
            folder = ME2M_UPLOAD_DIR / uuid.uuid4().hex
            folder.mkdir(parents=True, exist_ok=False)
            source = folder / "source.xlsx"
            size = 0
            with source.open("wb") as target:
                while chunk := upload.stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > ME2M_MAX_UPLOAD_BYTES:
                        raise ValueError("The ME2M workbook must be 50 MB or smaller.")
                    target.write(chunk)
            if not size:
                raise ValueError("The uploaded workbook is empty.")
            run_id = me2m_runner.start_run(source, name, as_of, user["username"], user_allowed_codes(user))
            flash(f"ME2M report #{run_id} started.", "success")
            return redirect(url_for("me2m_detail", run_id=run_id))
        except ValueError as exc:
            flash(str(exc), "error")
            if source:
                me2m_runner.cleanup_upload(source)
        except OSError:
            if source:
                me2m_runner.cleanup_upload(source)
            flash("The workbook could not be saved. Please retry.", "error")
    return render_template("me2m.html", user=user, recent=_visible_runs(user)[:50],
                           report_date=as_of, allowed_codes=user_allowed_codes(user))


@login_required
def run_detail(run_id):
    run = _get_run(run_id)
    return render_template("me2m_detail.html", user=current_user(), run=run, **_summary([run]))


@login_required
def history():
    user = current_user()
    status = request.args.get("status", "")
    query = request.args.get("q", "").strip()
    runs = _visible_runs(user)
    if status in RUN_STATUSES:
        runs = [r for r in runs if r["status"] == status]
    if query:
        runs = [r for r in runs if query.lower() in r["source_file"].lower()
                or query.lower() in (r["output_file"] or "").lower()]
    return render_template("me2m_history.html", user=user, recent=runs[:300], statuses=RUN_STATUSES,
                           f_status=status, f_query=query)


@login_required
def run_status(run_id):
    run = _get_run(run_id)
    return jsonify({key: run[key] for key in (
        "id", "status", "current_step", "total_rows", "net_order_value", "finished_at", "error",
    )})


@login_required
def stop_run(run_id):
    _get_run(run_id)
    try:
        me2m_runner.stop_run(run_id)
        flash("Stop requested. The report will stop after its current step.", "warning")
    except ValueError as exc:
        flash(str(exc), "error")
    return redirect(url_for("me2m_detail", run_id=run_id))


@login_required
def download(run_id):
    run = _get_run(run_id)
    if run["status"] != "success" or not run["output_file"]:
        abort(404)
    path = (ME2M_OUTPUT_DIR / run["output_file"]).resolve()
    if not path.is_relative_to(ME2M_OUTPUT_DIR.resolve()) or not path.is_file():
        abort(404)
    return send_file(path, as_attachment=True, download_name=path.name)


@login_required
def view_log(run_id):
    run = _get_run(run_id)
    if not run["log_file"]:
        abort(404)
    path = (ME2M_LOG_DIR / run["log_file"]).resolve()
    if not path.is_relative_to(ME2M_LOG_DIR.resolve()) or not path.is_file():
        abort(404)
    return render_template("me2m_log.html", user=current_user(), run=run,
                           content=path.read_text(encoding="utf-8", errors="replace"))


def dashboard():
    """Called by the authenticated dashboard's ME2M process tab."""
    user = current_user()
    all_runs = _visible_runs(user)
    period_raw = request.args.get("period", "7")
    period = int(period_raw) if period_raw in ("7", "14", "30", "0") else 7
    status = request.args.get("status", "")
    status = status if status in RUN_STATUSES else ""
    group_raw = request.args.get("group", "").strip()
    group = group_raw if group_raw == "(blank)" else group_raw.upper()
    all_groups = sorted({g for r in all_runs for g in json.loads(r["group_stats"] or "{}")})
    runs = all_runs
    if period:
        since = (datetime.now(REPORT_TIMEZONE).date() - timedelta(days=period - 1)).isoformat()
        runs = [r for r in runs if r["started_at"][:10] >= since]
    if status:
        runs = [r for r in runs if r["status"] == status]
    if group:
        runs = [r for r in runs if group in json.loads(r["group_stats"] or "{}")]
    successful = sum(r["status"] == "success" for r in runs)
    return render_template(
        "me2m_dashboard.html", user=user, runs=runs, recent=runs[:8], all_groups=all_groups,
        charts=chart_data(runs, period, "me2m", group=group),
        f_period=period, f_status=status, f_group=group, statuses=RUN_STATUSES,
        n_success=successful, n_failed=sum(r["status"] == "failed" for r in runs),
        n_running=sum(r["status"] == "running" for r in runs),
        n_cancelled=sum(r["status"] in ("cancelled", "interrupted") for r in runs),
        success_rate=round(100 * successful / len(runs)) if runs else 0,
        **_summary(runs, group),
    )
