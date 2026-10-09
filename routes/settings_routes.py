"""Settings: mail config + users (admin only)."""
from flask import flash, redirect, render_template, request, session, url_for

from config import PURCHASING_CODES
from database import db
from services import mailer
from utils.auth import (
    admin_required, change_password, create_user, current_user, list_users,
    login_required, update_user_codes,
)


@login_required
@admin_required
def settings():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "save_mail":
            cfg = {
                "enabled": request.form.get("enabled") == "on",
                "host": request.form.get("host", ""),
                "port": request.form.get("port", "587"),
                "use_tls": request.form.get("use_tls") == "on",
                "username": request.form.get("username", ""),
                "password": request.form.get("password", ""),
                "from_addr": request.form.get("from_addr", ""),
                "to_list": request.form.get("to_list", ""),
                "notify_on": request.form.get("notify_on", "failure"),
                "subject_prefix": request.form.get("subject_prefix", "[ACR Dashboard]"),
            }
            mailer.save_mail_config(cfg)
            flash("Mail notification settings saved.", "success")
        elif action == "test_mail":
            try:
                recipients = mailer.send_mail(
                    "Test notification",
                    "This is a test email from the Action Code Dashboard.\n"
                    "Your mail notification settings are working correctly.",
                )
                flash(f"Test email sent to: {', '.join(recipients)}", "success")
            except Exception as e:  # noqa: BLE001
                flash(f"Test email FAILED: {e}", "error")
        elif action == "add_user":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            full_name = request.form.get("full_name", "").strip()
            role = request.form.get("role", "buyer")
            codes = [c.strip().upper() for c in request.form.getlist("allowed_codes") if c.strip()]
            if not username or not password:
                flash("Username and password are required.", "error")
                return redirect(url_for("settings", open="add-user"))
            try:
                create_user(username, password, full_name, role, allowed_codes=codes)
                flash(f"User '{username}' created.", "success")
            except ValueError as e:
                flash(str(e), "error")
                return redirect(url_for("settings", open="add-user"))
        elif action == "update_codes":
            user_id = request.form.get("user_id", type=int)
            codes = [c.strip().upper() for c in request.form.getlist("allowed_codes") if c.strip()]
            target = db.query_one("SELECT username FROM users WHERE id = ?", (user_id,))
            if not target:
                flash("User not found.", "error")
            else:
                update_user_codes(user_id, codes)
                label = ", ".join(codes) if codes else "all codes (unrestricted)"
                flash(f"Purchasing codes for '{target['username']}' set to: {label}.", "success")
        return redirect(url_for("settings"))

    mail_cfg = mailer.get_mail_config()
    users = list_users()
    return render_template(
        "settings.html",
        user=current_user(),
        mail_cfg=mail_cfg,
        users=users,
        current_username=session.get("username"),
        purchasing_codes=PURCHASING_CODES,
        open_modal=request.args.get("open", ""),
    )


@login_required
@admin_required
def delete_user(user_id):
    target = db.query_one("SELECT username, role FROM users WHERE id = ?", (user_id,))
    if not target:
        flash("User not found.", "error")
    elif target["username"] == session.get("username"):
        flash("You cannot delete your own account.", "error")
    elif target["role"] == "admin" and db.query_one("SELECT COUNT(*) AS n FROM users WHERE role='admin'")["n"] <= 1:
        flash("Cannot delete the last admin account.", "error")
    else:
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        flash(f"User '{target['username']}' deleted.", "success")
    return redirect(url_for("settings"))


@login_required
def change_password_view():
    """Allow any logged-in user to change their own password."""
    if request.method == "POST":
        current_pw = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if new_pw != confirm_pw:
            flash("New password and confirmation do not match.", "error")
            return redirect(url_for("change_password"))
        try:
            change_password(session["user_id"], current_pw, new_pw)
            flash("Password changed successfully.", "success")
            return redirect(url_for("dashboard"))
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("change_password"))
    return render_template("change_password.html", user=current_user())


@login_required
@admin_required
def admin_reset_password(user_id):
    """Admin-only: reset any user's password without needing the current one."""
    target = db.query_one("SELECT id, username FROM users WHERE id = ?", (user_id,))
    if not target:
        flash("User not found.", "error")
        return redirect(url_for("settings"))
    if request.method == "POST":
        new_pw = request.form.get("new_password", "")
        confirm_pw = request.form.get("confirm_password", "")
        if new_pw != confirm_pw:
            flash("Passwords do not match.", "error")
            return redirect(url_for("settings", open=f"reset-{user_id}"))
        if len(new_pw) < 8:
            flash("Password must be at least 8 characters long.", "error")
            return redirect(url_for("settings", open=f"reset-{user_id}"))
        from werkzeug.security import generate_password_hash
        db.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (generate_password_hash(new_pw), user_id),
        )
        flash(f"Password for '{target['username']}' has been reset.", "success")
    return redirect(url_for("settings"))
