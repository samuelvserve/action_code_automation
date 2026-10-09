"""Action Code Dashboard - Flask web UI for the AC12 automation pipeline.

Entry point only: creates the app, registers the route modules (routes/),
initialises the database (database/), and starts the scheduler (services/).

Run with:  python app.py   (serves on http://127.0.0.1:5000)
"""
from datetime import datetime

from flask import Flask, redirect, request, session, url_for

from config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USER, HOST, PORT
from database import db
from routes import (
    auth_routes, dashboard_routes, files_routes, history_routes,
    logs_routes, me2m_routes, me2m_schedule_routes, portal_routes, run_routes, schedule_routes, settings_routes,
)
from services import scheduler
from utils.auth import ensure_default_user

app = Flask(__name__)
app.config.from_object("config")

db.init_db()
ensure_default_user()

# Runs left 'running' by a restart/crash are orphans - mark them interrupted.
db.execute(
    "UPDATE runs SET status='interrupted', finished_at=?, "
    "error='Interrupted by application restart' WHERE status='running'",
    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
)
db.execute(
    "UPDATE me2m_runs SET status='interrupted', finished_at=?, current_step=NULL, "
    "error='Interrupted by application restart' WHERE status='running'",
    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),),
)

# Automatic scheduler (once scheduled, runs fire on their own).
scheduler.start()


# ---------------------------------------------------------------------------
# Route registry. Endpoints are explicit so url_for() names used in the
# templates (dashboard, run_page, view_log, ...) keep working unchanged.
# ---------------------------------------------------------------------------
def _register_routes(app):
    app.add_url_rule("/login", "login", auth_routes.login, methods=["GET", "POST"])
    app.add_url_rule("/logout", "logout", auth_routes.logout, methods=["GET"])
    app.add_url_rule("/portals", "choose_portal", portal_routes.choose_portal, methods=["GET", "POST"])
    app.add_url_rule("/", "index", dashboard_routes.index, methods=["GET"])
    app.add_url_rule("/dashboard", "dashboard", dashboard_routes.dashboard, methods=["GET"])
    app.add_url_rule("/run", "run_page", run_routes.run_page, methods=["GET", "POST"])
    app.add_url_rule("/run/<int:run_id>", "run_detail", run_routes.run_detail, methods=["GET"])
    app.add_url_rule("/run/<int:run_id>/stop", "stop_run", run_routes.stop_run, methods=["POST"])
    app.add_url_rule("/run/<int:run_id>/status", "run_status", run_routes.run_status, methods=["GET"])
    app.add_url_rule("/history", "history", history_routes.history, methods=["GET"])
    app.add_url_rule("/logs", "logs", logs_routes.logs, methods=["GET"])
    app.add_url_rule("/logs/clear", "clear_logs", logs_routes.clear_logs, methods=["POST"])
    app.add_url_rule("/logs/<path:filename>", "view_log", logs_routes.view_log, methods=["GET"])
    app.add_url_rule("/schedule", "schedule_page", schedule_routes.schedule_page, methods=["GET", "POST"])
    app.add_url_rule("/schedule/<int:sched_id>/toggle", "toggle_schedule", schedule_routes.toggle_schedule, methods=["POST"])
    app.add_url_rule("/schedule/<int:sched_id>/delete", "delete_schedule", schedule_routes.delete_schedule, methods=["POST"])
    app.add_url_rule("/settings", "settings", settings_routes.settings, methods=["GET", "POST"])
    app.add_url_rule("/settings/users/<int:user_id>/delete", "delete_user", settings_routes.delete_user, methods=["POST"])
    app.add_url_rule("/settings/users/<int:user_id>/reset-password", "admin_reset_password", settings_routes.admin_reset_password, methods=["POST"])
    app.add_url_rule("/change-password", "change_password", settings_routes.change_password_view, methods=["GET", "POST"])
    app.add_url_rule("/download/<path:filename>", "download", files_routes.download, methods=["GET"])
    app.add_url_rule("/me2m", "me2m_page", me2m_routes.process_page, methods=["GET", "POST"])
    app.add_url_rule("/me2m/history", "me2m_history", me2m_routes.history, methods=["GET"])
    app.add_url_rule("/me2m/schedule", "me2m_schedule", me2m_schedule_routes.schedule_page, methods=["GET", "POST"])
    app.add_url_rule("/me2m/schedule/<int:sched_id>/toggle", "me2m_toggle_schedule", me2m_schedule_routes.toggle_schedule, methods=["POST"])
    app.add_url_rule("/me2m/schedule/<int:sched_id>/delete", "me2m_delete_schedule", me2m_schedule_routes.delete_schedule, methods=["POST"])
    app.add_url_rule("/me2m/<int:run_id>", "me2m_detail", me2m_routes.run_detail, methods=["GET"])
    app.add_url_rule("/me2m/<int:run_id>/status", "me2m_status", me2m_routes.run_status, methods=["GET"])
    app.add_url_rule("/me2m/<int:run_id>/stop", "me2m_stop", me2m_routes.stop_run, methods=["POST"])
    app.add_url_rule("/me2m/<int:run_id>/download", "me2m_download", me2m_routes.download, methods=["GET"])
    app.add_url_rule("/me2m/<int:run_id>/log", "me2m_log", me2m_routes.view_log, methods=["GET"])


_register_routes(app)


@app.before_request
def require_portal_choice():
    endpoint = request.endpoint or ""
    if not session.get("user_id") or endpoint in ("login", "logout", "static", "choose_portal"):
        return None
    if session.get("active_portal") not in ("acr", "me2m"):
        return redirect(url_for("choose_portal"))
    if endpoint == "dashboard" and request.args.get("process") in ("acr", "me2m"):
        session["active_portal"] = request.args["process"]
    elif endpoint in ("me2m_page", "me2m_detail", "me2m_history", "me2m_log", "me2m_schedule"):
        session["active_portal"] = "me2m"
    elif endpoint in ("run_page", "run_detail", "history", "logs", "view_log", "schedule_page"):
        session["active_portal"] = "acr"


@app.context_processor
def inject_globals():
    portal = session.get("active_portal", "acr")
    return {"nav_active": request.endpoint or "", "now": datetime.now(),
            "active_portal": portal, "portal_name": "ME2M" if portal == "me2m" else "Action Code"}


if __name__ == "__main__":
    print(f"Action Code Dashboard -> http://{HOST}:{PORT}")
    print(f"Default login: {DEFAULT_ADMIN_USER} / {DEFAULT_ADMIN_PASSWORD}")
    app.run(host=HOST, port=PORT, debug=False, threaded=True)
