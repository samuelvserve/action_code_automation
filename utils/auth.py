"""Authentication for the Action Code Dashboard.

Users are stored in SQLite with Werkzeug-hashed passwords. Login state lives
in the Flask session cookie (server-side secret), so no extra extensions are
needed.
"""
import functools

from flask import flash, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from config import DEFAULT_ADMIN_PASSWORD, DEFAULT_ADMIN_USER
from database import db


def ensure_default_user():
    """Create the initial admin account on first launch."""
    if db.query_one("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN_USER,)):
        return
    db.execute(
        "INSERT INTO users (username, password_hash, full_name, role) "
        "VALUES (?, ?, ?, ?)",
        (
            DEFAULT_ADMIN_USER,
            generate_password_hash(DEFAULT_ADMIN_PASSWORD),
            "Administrator",
            "admin",
        ),
    )


def create_user(username, password, full_name="", role="buyer", allowed_codes=None):
    if db.query_one("SELECT id FROM users WHERE username = ?", (username,)):
        raise ValueError(f"Username '{username}' already exists")
    codes_str = ",".join(allowed_codes) if allowed_codes else ""
    return db.execute(
        "INSERT INTO users (username, password_hash, full_name, role, allowed_codes) "
        "VALUES (?, ?, ?, ?, ?)",
        (username, generate_password_hash(password), full_name, role, codes_str),
    )


def update_user_codes(user_id, allowed_codes):
    """Set (or clear, with an empty list) the purchasing codes a user may run.

    An empty list means "no restriction" - the user can run any code.
    """
    codes_str = ",".join(allowed_codes) if allowed_codes else ""
    db.execute("UPDATE users SET allowed_codes = ? WHERE id = ?", (codes_str, user_id))


def change_password(user_id, current_password, new_password):
    """Change a user's password after verifying their current one.

    Returns True on success, or raises ValueError with a human-readable message.
    """
    row = db.query_one("SELECT password_hash FROM users WHERE id = ?", (user_id,))
    if not row:
        raise ValueError("User not found.")
    if not check_password_hash(row["password_hash"], current_password):
        raise ValueError("Current password is incorrect.")
    if len(new_password) < 8:
        raise ValueError("New password must be at least 8 characters long.")
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), user_id),
    )
    return True


def list_users():
    return db.query(
        "SELECT id, username, full_name, role, allowed_codes, created_at "
        "FROM users ORDER BY username"
    )


def user_allowed_codes(user):
    """Codes this user may run, or None if they're unrestricted (admin, or

    a buyer with no allocation set - keeps existing buyers working as
    before this feature was added).
    """
    if not user or user.get("role") == "admin":
        return None
    raw = (user.get("allowed_codes") or "").strip()
    if not raw:
        return None
    return [c for c in raw.split(",") if c]


def verify_login(username, password):
    row = db.query_one(
        "SELECT id, username, password_hash, full_name, role, allowed_codes FROM users WHERE username = ?",
        (username,),
    )
    if row and check_password_hash(row["password_hash"], password):
        return row
    return None


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    """Restrict a view to admin users (must be applied under login_required)."""
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Admin access required.", "error")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)

    return wrapped


def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    return db.query_one(
        "SELECT id, username, full_name, role, allowed_codes FROM users WHERE id = ?", (uid,)
    )
