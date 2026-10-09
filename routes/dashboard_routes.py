"""Dashboard views: stat cards, charts, per-code breakdown, filters."""
import json
from datetime import datetime, timedelta

from flask import redirect, render_template, request, session, url_for

from config import RUN_STATUSES, RUN_TYPES
from database import db
from services.dashboard_stats import chart_data, run_metrics
from utils.auth import current_user, login_required, user_allowed_codes
from utils.dates import iso_week


@login_required
def index():
    return redirect(url_for("dashboard"))


@login_required
def dashboard():
    if session.get("active_portal") == "me2m":
        from routes import me2m_routes
        return me2m_routes.dashboard()
    user = current_user()
    allowed = user_allowed_codes(user)  # None = unrestricted (admin, or unallocated buyer)

    period_raw = request.args.get("period", "7")
    period = int(period_raw) if period_raw in ("7", "14", "30", "0") else 7
    code = request.args.get("code", "").strip().upper()
    if allowed is not None and code and code not in allowed:
        code = ""  # ignore attempts to filter to a code outside the buyer's allocation
    rtype = request.args.get("type", "")
    status = request.args.get("status", "")

    sql = "SELECT * FROM runs WHERE 1=1"
    params = []
    if period and period > 0:
        since = (datetime.now() - timedelta(days=period - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sql += " AND started_at >= ?"
        params.append(since.strftime("%Y-%m-%d %H:%M:%S"))
    if code:
        sql += " AND purchasing_codes LIKE ?"
        params.append(f"%{code}%")
    elif allowed is not None:
        # No specific code chosen - a restricted buyer only sees runs that
        # touch at least one of their allocated codes.
        sql += " AND (" + " OR ".join(["purchasing_codes LIKE ?"] * len(allowed)) + ")"
        params.extend(f"%{c}%" for c in allowed)
    if rtype in RUN_TYPES:
        sql += " AND run_type = ?"
        params.append(rtype)
    if status in RUN_STATUSES:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY started_at DESC"
    runs = db.query(sql, params)

    success = [r for r in runs if r["status"] == "success"]
    failed = [r for r in runs if r["status"] == "failed"]
    running = [r for r in runs if r["status"] == "running"]
    cancelled = [r for r in runs if r["status"] in ("cancelled", "interrupted")]

    chart_codes = [code] if code else allowed
    total_rows = sum(run_metrics(r, "acr", codes=chart_codes)[0] for r in runs)
    total_action = sum(run_metrics(r, "acr", codes=chart_codes)[1] for r in runs)
    success_rate = round(100 * len(success) / len(runs)) if runs else 0

    # --- chart data (daily buckets, or weekly for all-time) ---------------
    day_labels, day_runs, day_actions = [], [], []
    if period == 0:
        buckets = {}
        for r in runs:
            wk = iso_week(r["started_at"])
            b = buckets.setdefault(wk, {"runs": 0, "actions": 0})
            b["runs"] += 1
            b["actions"] += r["action_items"] or 0
        for wk, b in sorted(buckets.items()):
            day_labels.append(wk)
            day_runs.append(b["runs"])
            day_actions.append(b["actions"])
    else:
        n = period if period > 0 else 7
        for i in range(n - 1, -1, -1):
            day = (datetime.now() - timedelta(days=i))
            day_key = day.strftime("%Y-%m-%d")
            day_runs_count = sum(1 for r in runs if r["started_at"].startswith(day_key))
            day_actions_sum = sum(r["action_items"] or 0 for r in runs if r["started_at"].startswith(day_key))
            day_labels.append(day.strftime("%a %d"))
            day_runs.append(day_runs_count)
            day_actions.append(day_actions_sum)

    # --- per purchasing code ---------------------------------------------
    code_stats = {}
    for r in runs:
        if r["status"] != "success":
            continue
        per_code = {}
        if r.get("code_stats"):
            try:
                per_code = json.loads(r["code_stats"])
            except (TypeError, json.JSONDecodeError):
                per_code = {}
        if per_code:
            # Per-code stats recorded by the pipeline (accurate for
            # multi-code runs).
            for code_sel, s in per_code.items():
                agg = code_stats.setdefault(code_sel, {"runs": 0, "rows": 0, "action_items": 0})
                agg["runs"] += 1
                agg["rows"] += s.get("rows", 0)
                agg["action_items"] += s.get("action_items", 0)
        else:
            # Fallback for runs recorded before per-code stats existed.
            for code_sel in r["purchasing_codes"].split(","):
                agg = code_stats.setdefault(code_sel, {"runs": 0, "rows": 0, "action_items": 0})
                agg["runs"] += 1
                agg["rows"] += r["total_rows"] or 0
                agg["action_items"] += r["action_items"] or 0
    code_rows = sorted(code_stats.items(), key=lambda kv: kv[1]["action_items"], reverse=True)

    # Distinct codes across ALL runs (so the filter can target any code).
    all_runs = db.query("SELECT purchasing_codes FROM runs")
    all_codes = sorted({c for r in all_runs for c in r["purchasing_codes"].split(",") if c})
    if allowed is not None:
        all_codes = [c for c in all_codes if c in allowed]

    recent = runs[:8]

    return render_template(
        "dashboard.html",
        charts=chart_data(runs, period, "acr", codes=chart_codes),
        user=user,
        allowed_codes=allowed,
        runs=runs,
        n_success=len(success),
        n_failed=len(failed),
        n_running=len(running),
        n_cancelled=len(cancelled),
        total_rows=total_rows,
        total_action=total_action,
        success_rate=success_rate,
        day_labels=day_labels,
        day_runs=day_runs,
        day_actions=day_actions,
        code_stats=code_stats,
        code_rows=code_rows,
        recent=recent,
        all_codes=all_codes,
        run_types=RUN_TYPES,
        statuses=RUN_STATUSES,
        f_period=period,
        f_code=code,
        f_type=rtype,
        f_status=status,
    )
