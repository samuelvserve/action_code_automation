"""Automatic scheduled runs."""
from flask import flash, redirect, render_template, request, session, url_for

from config import PURCHASING_CODES, RUN_TYPES, WEEKDAYS
from database import db
from services import scheduler
from utils.auth import current_user, login_required


@login_required
def schedule_page():
    if request.method == "POST":
        name = request.form.get("name", "").strip() or "Scheduled run"
        run_type = request.form.get("run_type", "Daily")
        codes = request.form.getlist("purchasing_codes")
        run_time = request.form.get("run_time", "").strip()
        days = ",".join(request.form.getlist("days"))
        enabled = 1 if request.form.get("enabled") == "on" else 0

        try:
            hh, mm = run_time.split(":")
            if not (0 <= int(hh) <= 23 and 0 <= int(mm) <= 59):
                raise ValueError
        except ValueError:
            flash("Enter a valid time in HH:MM format (e.g. 07:00).", "error")
            return redirect(url_for("schedule_page"))
        if not codes:
            flash("Select at least one purchasing code.", "error")
            return redirect(url_for("schedule_page"))
        if run_type not in RUN_TYPES:
            run_type = "Daily"
        if not days:
            days = ""  # every day

        db.execute(
            "INSERT INTO schedules (name, run_type, purchasing_codes, run_time, days, "
            "enabled, created_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, run_type, ",".join(codes), run_time, days, enabled, session["username"]),
        )
        flash(f"Schedule '{name}' saved - runs automatically at {run_time}.", "success")
        return redirect(url_for("schedule_page"))

    schedules = db.query("SELECT * FROM schedules ORDER BY enabled DESC, id DESC")
    for s in schedules:
        s["next_run"] = scheduler.next_run(s)
    return render_template(
        "schedule.html",
        user=current_user(),
        schedules=schedules,
        purchasing_codes=PURCHASING_CODES,
        run_types=RUN_TYPES,
        weekdays=WEEKDAYS,
    )


@login_required
def toggle_schedule(sched_id):
    s = db.query_one("SELECT id, enabled FROM schedules WHERE id = ?", (sched_id,))
    if not s:
        flash("Schedule not found.", "error")
    else:
        db.execute("UPDATE schedules SET enabled = ? WHERE id = ?", (0 if s["enabled"] else 1, sched_id))
        flash("Schedule " + ("enabled" if not s["enabled"] else "paused") + ".", "success")
    return redirect(url_for("schedule_page"))


@login_required
def delete_schedule(sched_id):
    db.execute("DELETE FROM schedules WHERE id = ?", (sched_id,))
    flash("Schedule deleted.", "success")
    return redirect(url_for("schedule_page"))
