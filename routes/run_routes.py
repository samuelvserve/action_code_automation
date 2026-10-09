"""Run control: start runs, live status, stop."""
import uuid
import json
from datetime import datetime

import action_code_pipeline as pipeline
from flask import flash, jsonify, redirect, render_template, request, session, url_for

from config import MANUAL_UPLOAD_DIR, PURCHASING_CODES, RUN_TYPES, SOURCE_FILE_LABELS
from database import db
from services import pipeline_runner
from utils.auth import current_user, login_required, user_allowed_codes


def _stage_uploaded_sources():
    """Save the six manually-uploaded source files (Instant Run's "Upload
    files for this run" option) to a fresh per-run folder, under the exact
    filenames action_code_pipeline.py expects.

    Returns (input_dir, error_message). On any missing/invalid file,
    input_dir is None and error_message describes what's wrong; nothing is
    written to disk in that case.
    """
    missing = []
    for key, label in SOURCE_FILE_LABELS.items():
        f = request.files.get(f"src_{key}")
        if not f or not f.filename:
            missing.append(label)
        elif not f.filename.lower().endswith(".xlsx"):
            missing.append(f"{label} (must be .xlsx)")
    if missing:
        return None, "Upload all six source files (.xlsx): missing/invalid - " + "; ".join(missing)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = MANUAL_UPLOAD_DIR / f"{stamp}_{uuid.uuid4().hex[:8]}"
    run_dir.mkdir(parents=True, exist_ok=True)
    for key in SOURCE_FILE_LABELS:
        target_name = pipeline.FILES[key].name
        request.files[f"src_{key}"].save(run_dir / target_name)
    return run_dir, None


@login_required
def run_page():
    user = current_user()
    allowed = user_allowed_codes(user)  # None = unrestricted

    if request.method == "POST":
        run_type = request.form.get("run_type", "Daily")
        codes = request.form.getlist("purchasing_codes")
        source_mode = request.form.get("source_mode", "folder")
        not_allowed = [c for c in codes if allowed is not None and c.upper() not in allowed]
        if not_allowed:
            flash(
                f"You're not allocated to run: {', '.join(not_allowed)}. "
                "Ask an admin to update your purchasing codes in Settings.",
                "error",
            )
        else:
            input_dir = None
            upload_error = None
            if source_mode == "upload":
                input_dir, upload_error = _stage_uploaded_sources()

            if upload_error:
                flash(upload_error, "error")
            else:
                try:
                    run_id = pipeline_runner.start_run(
                        run_type, codes, session["username"], input_dir=input_dir
                    )
                    flash(f"Run #{run_id} started in the background.", "success")
                    return redirect(url_for("run_detail", run_id=run_id))
                except ValueError as e:
                    flash(str(e), "error")

    recent = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 10")
    codes_for_ui = [c for c in PURCHASING_CODES if allowed is None or c in allowed]
    return render_template(
        "run.html",
        user=user,
        purchasing_codes=codes_for_ui,
        allowed_codes=allowed,
        run_types=RUN_TYPES,
        recent=recent,
        source_file_labels=SOURCE_FILE_LABELS,
    )


@login_required
def run_detail(run_id):
    run = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not run:
        flash("Run not found.", "error")
        return redirect(url_for("dashboard"))
    user = current_user()
    allowed = user_allowed_codes(user)
    per_code = json.loads(run.get("code_stats") or "{}")
    code_rows = [(code, stats) for code, stats in sorted(per_code.items()) if allowed is None or code in allowed]
    return render_template("run_detail.html", user=user, run=run, code_rows=code_rows)


@login_required
def stop_run(run_id):
    try:
        pipeline_runner.stop_run(run_id)
        flash(f"Stop requested for run #{run_id} - it will stop at the next step.", "warning")
    except ValueError as e:
        flash(str(e), "error")
    return redirect(url_for("run_detail", run_id=run_id))


@login_required
def run_status(run_id):
    run = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not run:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "id": run["id"],
        "status": run["status"],
        "started_at": run["started_at"],
        "finished_at": run["finished_at"],
        "duration_sec": run["duration_sec"],
        "total_rows": run["total_rows"],
        "action_items": run["action_items"],
        "error": run["error"],
        "output_file": run["output_file"],
        "current_step": run.get("current_step"),
    })
