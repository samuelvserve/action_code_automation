"""Scheduler for automatic pipeline runs.

Enabled schedules live in the DB. A daemon thread (started once when the app
imports) checks every ~20s whether any schedule is due: the current time has
reached the schedule's HH:MM on a matching weekday, and it has not already
run today. A run that was missed while the app was down is picked up on next
start (catch-up), since due-ness is based on "date not yet run".
"""
import logging
import threading
import time
from datetime import datetime, timedelta

from database import db
from me2m_pipeline import REPORT_TIMEZONE
from services import me2m_runner
from services.pipeline_runner import start_run
from utils.auth import user_allowed_codes

_POLL_SECONDS = 20
_stop_event = None
_thread = None

VALID_STATUSES = ("success", "failed", "cancelled")


def _parse_days(days_str):
    """'' = every day; otherwise comma-separated weekday ints (0=Mon..6=Sun)."""
    if not days_str:
        return None  # every day
    try:
        days = sorted({int(d) for d in days_str.split(",") if d.strip() != ""})
        return days if days else None
    except ValueError:
        return None


def _is_due(sched, now):
    if not sched.get("enabled"):
        return False
    days = _parse_days(sched.get("days", ""))
    if days is not None and now.weekday() not in days:
        return False
    try:
        hh, mm = sched["run_time"].split(":")
        run_minute = int(hh) * 60 + int(mm)
    except (ValueError, KeyError, AttributeError):
        return False
    now_minute = now.hour * 60 + now.minute
    return now_minute >= run_minute and sched.get("last_run_date") != now.strftime("%Y-%m-%d")


def next_run(sched, now=None):
    """Compute the next datetime a schedule will fire (for the UI)."""
    now = now or datetime.now(REPORT_TIMEZONE)
    if not sched.get("enabled"):
        return None
    if _is_due(sched, now):
        return now
    days = _parse_days(sched.get("days", ""))
    try:
        hh, mm = sched["run_time"].split(":")
        run_minute = int(hh) * 60 + int(mm)
    except (ValueError, KeyError, AttributeError):
        return None

    for offset in range(8):
        candidate = now.replace(second=0, microsecond=0) + timedelta(days=offset)
        if days is not None and candidate.weekday() not in days:
            continue
        candidate = candidate.replace(hour=int(hh or 0), minute=int(mm))
        if candidate > now:
            return candidate
    return None


def check_me2m_schedules(now=None):
    """Dispatch each due ME2M schedule once per local day, with isolated inputs."""
    now = now or datetime.now(REPORT_TIMEZONE)
    for sched in db.query("SELECT * FROM me2m_schedules WHERE enabled=1"):
        if not _is_due(sched, now):
            continue
        previous = db.query_one("SELECT status FROM me2m_runs WHERE id=?", (sched["last_run_id"],))
        if previous and previous["status"] == "running":
            continue
        today = now.strftime("%Y-%m-%d")
        with db.get_conn() as conn:
            claim = conn.execute(
                "UPDATE me2m_schedules SET last_run_date=?,last_run_at=?,last_error=NULL "
                "WHERE id=? AND enabled=1 AND (last_run_date IS NULL OR last_run_date!=?)",
                (today, now.strftime("%Y-%m-%d %H:%M:%S"), sched["id"], today),
            )
            if not claim.rowcount:
                continue
        source = None
        try:
            owner = db.query_one("SELECT * FROM users WHERE username=?", (sched["created_by"],))
            if not owner:
                raise ValueError("The schedule owner no longer exists. Create a new schedule.")
            source, name = me2m_runner.stage_folder_workbook(sched["input_folder"])
            run_id = me2m_runner.start_run(source, name, today, owner["username"],
                                           user_allowed_codes(owner), schedule_id=sched["id"])
            db.execute("UPDATE me2m_schedules SET last_run_id=? WHERE id=?", (run_id, sched["id"]))
            logging.getLogger("scheduler").info("ME2M schedule #%s started report #%s", sched["id"], run_id)
        except Exception as exc:
            if source:
                me2m_runner.cleanup_upload(source)
            db.execute("UPDATE me2m_schedules SET last_error=? WHERE id=?", (str(exc)[:2000], sched["id"]))
            logging.getLogger("scheduler").error("ME2M schedule #%s could not start: %s", sched["id"], exc)


def _loop():
    while not _stop_event.is_set():
        now = datetime.now(REPORT_TIMEZONE)
        try:
            for sched in db.query("SELECT * FROM schedules WHERE enabled = 1"):
                if not _is_due(sched, now):
                    continue
                codes = [c.strip() for c in (sched["purchasing_codes"] or "").split(",") if c.strip()]
                if not codes:
                    continue
                try:
                    run_id = start_run(
                        sched["run_type"], codes,
                        f"schedule:{sched['id']}",
                    )
                    db.execute(
                        "UPDATE schedules SET last_run_at=?, last_run_date=? WHERE id=?",
                        (now.strftime("%Y-%m-%d %H:%M:%S"), now.strftime("%Y-%m-%d"), sched["id"]),
                    )
                    logging.getLogger("scheduler").info(
                        "Schedule #%s (%s) triggered run #%s", sched["id"], sched["name"], run_id
                    )
                except Exception as e:  # noqa: BLE001
                    logging.getLogger("scheduler").error(
                        "Schedule #%s failed to trigger: %s", sched["id"], e
                    )
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            logging.getLogger("scheduler").error("Scheduler loop error: %s", e)
        try:
            check_me2m_schedules()
        except Exception as exc:
            logging.getLogger("scheduler").error("ME2M scheduler loop error: %s", exc)
        _stop_event.wait(_POLL_SECONDS)


def start():
    """Start the scheduler thread once (idempotent)."""
    global _stop_event, _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event = threading.Event()
    _thread = threading.Thread(target=_loop, daemon=True, name="acr-scheduler")
    _thread.start()
