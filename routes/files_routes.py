"""File downloads (output files only, path-traversal guarded)."""
from flask import flash, redirect, send_from_directory, url_for

from config import OUTPUT_DIR
from database import db
from utils.auth import current_user, login_required, user_allowed_codes


@login_required
def download(filename):
    target = (OUTPUT_DIR / filename).resolve()
    if not target.is_relative_to(OUTPUT_DIR) or not target.is_file():
        flash("File not found.", "error")
        return redirect(url_for("history"))

    allowed = user_allowed_codes(current_user())
    if allowed is not None:
        run = db.query_one("SELECT purchasing_codes FROM runs WHERE output_file = ?", (filename,))
        run_codes = set(run["purchasing_codes"].split(",")) if run else set()
        if not run_codes & set(allowed):
            flash("You're not allocated to download this file.", "error")
            return redirect(url_for("history"))

    return send_from_directory(OUTPUT_DIR, filename, as_attachment=True)
