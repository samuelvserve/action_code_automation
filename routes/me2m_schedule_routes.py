"""Separate folder-based schedules for ME2M reports."""
from datetime import datetime
from pathlib import Path

from flask import abort, flash, redirect, render_template, request, url_for

from config import ME2M_INPUT_DIR, WEEKDAYS
from database import db
from me2m_pipeline import REPORT_TIMEZONE
from services import scheduler
from utils.auth import current_user, login_required


def _get_schedule(sched_id):
    schedule = db.query_one("SELECT * FROM me2m_schedules WHERE id=?", (sched_id,))
    if not schedule:
        abort(404)
    user = current_user()
    if user["role"] != "admin" and schedule["created_by"] != user["username"]:
        abort(403)
    return schedule


@login_required
def schedule_page():
    user = current_user()
    if request.method == "POST":
        try:
            name = request.form.get("name", "").strip() or "ME2M scheduled report"
            run_time = request.form.get("run_time", "").strip()
            parsed = datetime.strptime(run_time, "%H:%M")
            run_time = parsed.strftime("%H:%M")
            days = request.form.getlist("days")
            if any(d not in {str(i) for i in range(7)} for d in days):
                raise ValueError("Choose valid weekdays.")
            folder_text = request.form.get("input_folder", "").strip()
            if not folder_text:
                raise ValueError("Enter the folder that contains your ME2M source workbooks.")
            folder = Path(folder_text).expanduser()
            if not folder.is_absolute() or not folder.is_dir():
                raise ValueError("Enter an existing folder using its full path.")
            db.execute(
                "INSERT INTO me2m_schedules (name,input_folder,run_time,days,enabled,created_by) "
                "VALUES (?,?,?,?,?,?)",
                (name, str(folder.resolve()), run_time, ",".join(sorted(set(days))),
                 1 if request.form.get("enabled") == "on" else 0, user["username"]),
            )
            flash(f"ME2M schedule '{name}' saved.", "success")
            return redirect(url_for("me2m_schedule"))
        except (ValueError, OSError) as exc:
            message = str(exc) if not isinstance(exc, OSError) else "The input folder cannot be accessed."
            if "does not match format" in message:
                message = "Choose a valid run time in HH:MM format."
            flash(message, "error")
    ME2M_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    if user["role"] == "admin":
        schedules = db.query("SELECT * FROM me2m_schedules ORDER BY enabled DESC,id DESC")
    else:
        schedules = db.query("SELECT * FROM me2m_schedules WHERE created_by=? ORDER BY enabled DESC,id DESC",
                             (user["username"],))
    now = datetime.now(REPORT_TIMEZONE)
    for schedule in schedules:
        schedule["next_run"] = scheduler.next_run(schedule, now)
        if schedule["last_run_id"]:
            last = db.query_one("SELECT status,error FROM me2m_runs WHERE id=?", (schedule["last_run_id"],))
            schedule["last_status"] = last["status"] if last else None
            if last and last["status"] == "failed" and last["error"] and not schedule["last_error"]:
                schedule["last_error"] = last["error"]
    return render_template("me2m_schedule.html", user=user, schedules=schedules,
                           weekdays=WEEKDAYS, default_folder=str(ME2M_INPUT_DIR), form=request.form)


@login_required
def toggle_schedule(sched_id):
    schedule = _get_schedule(sched_id)
    enabled = 0 if schedule["enabled"] else 1
    db.execute("UPDATE me2m_schedules SET enabled=?,last_run_date=CASE WHEN ?=1 THEN NULL ELSE last_run_date END "
               "WHERE id=?", (enabled, enabled, sched_id))
    flash("ME2M schedule enabled." if enabled else "ME2M schedule paused.", "success")
    return redirect(url_for("me2m_schedule"))


@login_required
def delete_schedule(sched_id):
    _get_schedule(sched_id)
    db.execute("DELETE FROM me2m_schedules WHERE id=?", (sched_id,))
    flash("ME2M schedule deleted.", "success")
    return redirect(url_for("me2m_schedule"))
