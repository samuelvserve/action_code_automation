"""Log browsing (per-date folders under logs/)."""
from datetime import datetime

from flask import flash, redirect, render_template, url_for

from config import LOG_DIR
from utils.auth import admin_required, current_user, login_required


@login_required
def logs():
    files = []
    for p in sorted(LOG_DIR.rglob("*.log"), reverse=True):
        rel = p.relative_to(LOG_DIR)
        parts = rel.parts
        files.append({
            "date": parts[0] if len(parts) > 1 else "(misc)",
            "name": str(rel).replace("\\", "/"),
            "size": p.stat().st_size,
            "mtime": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        })
    by_date = {}
    for f in files:
        by_date.setdefault(f["date"], []).append(f)
    grouped = sorted(by_date.items(), reverse=True)
    return render_template("logs.html", user=current_user(), grouped=grouped)


@login_required
@admin_required
def clear_logs():
    """Delete all finished run logs. Logs of currently running runs are kept
    (their files are locked/open and cannot be deleted on Windows)."""
    cleared = kept = 0
    for p in list(LOG_DIR.rglob("*.log")):
        try:
            p.unlink()
            cleared += 1
        except OSError:
            kept += 1
    # Tidy up now-empty date folders.
    for d in sorted(LOG_DIR.iterdir(), reverse=True):
        if d.is_dir() and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass
    msg = f"Cleared {cleared} log file(s)."
    if kept:
        msg += f" {kept} kept (belong to runs still in progress)."
    flash(msg, "success")
    return redirect(url_for("logs"))


@login_required
def view_log(filename):
    path = (LOG_DIR / filename).resolve()
    if not path.is_relative_to(LOG_DIR) or not path.is_file():
        flash("Log file not found.", "error")
        return redirect(url_for("logs"))
    content = path.read_text(encoding="utf-8", errors="replace")
    return render_template(
        "view_log.html", user=current_user(), filename=filename, content=content
    )
