"""Choose the process workspace after signing in."""
from flask import flash, redirect, render_template, request, session, url_for

from utils.auth import current_user, login_required


@login_required
def choose_portal():
    if request.method == "POST":
        portal = request.form.get("portal", "")
        if portal in ("acr", "me2m"):
            session["active_portal"] = portal
            return redirect(url_for("dashboard", process=portal))
        flash("Choose Action Code or ME2M to continue.", "error")
    return render_template("choose_portal.html", user=current_user(),
                           selected=session.get("active_portal", "acr"))
