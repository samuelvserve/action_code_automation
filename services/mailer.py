"""Mail notifications for run results.

SMTP configuration is stored in the DB settings table (editable from the
Settings page) and used to email run summaries on success and/or failure.
"""
import smtplib
import ssl
from email.message import EmailMessage

from database import db

MAIL_SETTINGS_KEY = "mail_config"

DEFAULT_MAIL_CONFIG = {
    "enabled": False,
    "host": "smtp.office365.com",
    "port": 587,
    "use_tls": True,
    "username": "",
    "password": "",
    "from_addr": "",
    "to_list": "",
    "notify_on": "failure",  # "success" | "failure" | "both"
    "subject_prefix": "[ACR Dashboard]",
}


def get_mail_config():
    cfg = db.get_setting(MAIL_SETTINGS_KEY, {})
    merged = dict(DEFAULT_MAIL_CONFIG)
    merged.update(cfg or {})
    return merged


def save_mail_config(cfg):
    clean = {k: (v or "").strip() if isinstance(v, str) else v for k, v in cfg.items()}
    db.set_setting(MAIL_SETTINGS_KEY, clean)
    return clean


def _recipients(cfg):
    return [r.strip() for r in cfg.get("to_list", "").split(",") if r.strip()]


def send_mail(subject, body, cfg=None):
    """Send one email; raises on failure so the caller can surface errors."""
    cfg = cfg or get_mail_config()
    if not cfg.get("enabled"):
        return None
    if not cfg.get("host") or not cfg.get("from_addr"):
        raise RuntimeError("SMTP host and From address are required")

    recipients = _recipients(cfg)
    if not recipients:
        raise RuntimeError("No recipients configured")

    prefix = cfg.get("subject_prefix", "").strip()
    msg = EmailMessage()
    msg["Subject"] = f"{prefix} {subject}" if prefix else subject
    msg["From"] = cfg["from_addr"]
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    port = int(cfg.get("port") or 25)
    use_tls = bool(cfg.get("use_tls"))
    if use_tls:
        context = ssl.create_default_context()
        with smtplib.SMTP(cfg["host"], port, timeout=30) as server:
            server.starttls(context=context)
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], port, timeout=30) as server:
            if cfg.get("username") and cfg.get("password"):
                server.login(cfg["username"], cfg["password"])
            server.send_message(msg)
    return recipients


def notify_run(run_row):
    """Send a run-result email according to the configured notify_on rule."""
    cfg = get_mail_config()
    if not cfg.get("enabled"):
        return None

    status = run_row["status"]
    rule = cfg.get("notify_on", "failure")
    if status == "success" and rule not in ("success", "both"):
        return None
    if status == "failed" and rule not in ("failure", "both"):
        return None

    codes = run_row["purchasing_codes"]
    if status == "success":
        subject = f"Run #{run_row['id']} SUCCESS - {codes}"
        body = (
            f"Action Code pipeline run completed successfully.\n\n"
            f"  Run ID:        {run_row['id']}\n"
            f"  Type:          {run_row['run_type']}\n"
            f"  Purchasing:    {codes}\n"
            f"  Started:       {run_row['started_at']}\n"
            f"  Duration:      {run_row['duration_sec']}s\n"
            f"  Total rows:    {run_row['total_rows']}\n"
            f"  Action items:  {run_row['action_items']} (A/B class, DOS < 21 days)\n"
            f"  Output file:   {run_row['output_file']}\n"
        )
    else:
        subject = f"Run #{run_row['id']} FAILED - {codes}"
        body = (
            f"Action Code pipeline run FAILED.\n\n"
            f"  Run ID:      {run_row['id']}\n"
            f"  Type:        {run_row['run_type']}\n"
            f"  Purchasing:  {codes}\n"
            f"  Started:     {run_row['started_at']}\n"
            f"  Error:       {(run_row.get('error') or 'unknown')[:1000]}\n"
            f"\nCheck the dashboard logs for details.\n"
        )
    return send_mail(subject, body, cfg)
