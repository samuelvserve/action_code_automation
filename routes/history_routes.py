"""History: weekly stats, run table, monthly run files."""
from flask import render_template, request

from config import RUN_STATUSES, RUN_TYPES
from database import db
from utils.auth import current_user, login_required, user_allowed_codes
from utils.dates import iso_week


@login_required
def history():
    user = current_user()
    allowed = user_allowed_codes(user)  # None = unrestricted

    run_type = request.args.get("type", "")
    code = request.args.get("code", "").strip().upper()
    if allowed is not None and code and code not in allowed:
        code = ""
    status = request.args.get("status", "")

    sql = "SELECT * FROM runs WHERE 1=1"
    params = []
    if run_type in RUN_TYPES:
        sql += " AND run_type = ?"
        params.append(run_type)
    if code:
        sql += " AND purchasing_codes LIKE ?"
        params.append(f"%{code}%")
    elif allowed is not None:
        sql += " AND (" + " OR ".join(["purchasing_codes LIKE ?"] * len(allowed)) + ")"
        params.extend(f"%{c}%" for c in allowed)
    if status in RUN_STATUSES:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY id DESC LIMIT 300"
    runs = db.query(sql, params)

    # Weekly aggregation
    weekly = {}
    for r in runs:
        wk = iso_week(r["started_at"])
        w = weekly.setdefault(wk, {"runs": 0, "success": 0, "failed": 0, "other": 0, "rows": 0, "action_items": 0})
        w["runs"] += 1
        if r["status"] == "success":
            w["success"] += 1
        elif r["status"] == "failed":
            w["failed"] += 1
        else:
            w["other"] += 1
        w["rows"] += r["total_rows"] or 0
        w["action_items"] += r["action_items"] or 0
    weekly_rows = sorted(weekly.items(), reverse=True)

    # Monthly run files
    monthly = {}
    for r in runs:
        if not r["output_file"]:
            continue
        month = r["started_at"][:7]  # YYYY-MM
        monthly.setdefault(month, []).append(r)
    monthly_rows = sorted(monthly.items(), reverse=True)

    # Distinct codes present in history (for the filter dropdown)
    all_codes = sorted({c for r in runs for c in r["purchasing_codes"].split(",") if c})

    return render_template(
        "history.html",
        user=user,
        runs=runs,
        weekly_rows=weekly_rows,
        monthly_rows=monthly_rows,
        all_codes=all_codes,
        run_types=RUN_TYPES,
        f_type=run_type,
        f_code=code,
        f_status=status,
    )
