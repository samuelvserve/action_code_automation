"""Login / logout views."""
from flask import flash, redirect, render_template, request, session, url_for

from config import DEFAULT_ADMIN_USER
from utils.auth import user_allowed_codes, verify_login


def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard" if session.get("active_portal") else "choose_portal"))
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = verify_login(username, password)
        if user:
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["allowed_codes"] = user_allowed_codes(user)
            flash(f"Welcome back, {user['full_name'] or user['username']}.", "success")
            return redirect(url_for("choose_portal"))
        flash("Invalid username or password.", "error")
    return render_template("login.html", default_user=DEFAULT_ADMIN_USER)


def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("login"))
