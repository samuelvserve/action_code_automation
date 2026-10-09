"""Background ME2M execution with isolated records, outputs and logs."""
import json
import logging
import shutil
import threading
import time
import uuid
from datetime import date, datetime
from pathlib import Path

from config import ME2M_LOG_DIR, ME2M_MAX_UPLOAD_BYTES, ME2M_OUTPUT_DIR, ME2M_UPLOAD_DIR
from database import db
from me2m_pipeline import ME2MCancelled, REPORT_TIMEZONE, build_report


_RUNS = {}


def _now():
    return datetime.now(REPORT_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def cleanup_upload(source):
    folder = Path(source).resolve().parent
    root = ME2M_UPLOAD_DIR.resolve()
    if folder != root and folder.is_relative_to(root) and folder.exists():
        shutil.rmtree(folder)


def stage_folder_workbook(input_folder):
    """Copy the newest source workbook into an isolated run staging folder."""
    folder = Path(input_folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError("The ME2M input folder is unavailable. Check the schedule folder.")
    candidates = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".xlsx"
                  and not p.name.startswith("~$") and "_processed" not in p.stem.lower()]
    if not candidates:
        raise ValueError("No source .xlsx workbook was found in the ME2M input folder.")
    workbook = max(candidates, key=lambda p: (p.stat().st_mtime_ns, p.name))
    size = workbook.stat().st_size
    if not 0 < size <= ME2M_MAX_UPLOAD_BYTES:
        raise ValueError("The ME2M workbook must be nonempty and 50 MB or smaller.")
    destination = ME2M_UPLOAD_DIR / uuid.uuid4().hex / "source.xlsx"
    destination.parent.mkdir(parents=True, exist_ok=False)
    try:
        copied = 0
        with workbook.open("rb") as source, destination.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                copied += len(chunk)
                if copied > ME2M_MAX_UPLOAD_BYTES:
                    raise ValueError("The ME2M workbook must be 50 MB or smaller.")
                target.write(chunk)
        if copied != size:
            raise ValueError("The source workbook changed while it was being read. Retry after saving it.")
    except Exception:
        cleanup_upload(destination)
        raise
    return destination, workbook.name


def start_run(source, source_name, report_date, triggered_by, allowed_codes=None, schedule_id=None):
    source = Path(source).resolve()
    if not source.is_relative_to(ME2M_UPLOAD_DIR.resolve()) or not source.is_file():
        raise ValueError("Upload an ME2M workbook before starting a report.")
    report_date = date.fromisoformat(str(report_date)).isoformat()
    run_id = db.execute(
        "INSERT INTO me2m_runs (source_file, report_date, started_at, triggered_by, current_step, schedule_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (source_name, report_date, _now(), triggered_by, "Preparing ME2M report", schedule_id),
    )
    cancel = threading.Event()
    thread = threading.Thread(
        target=_execute_run,
        args=(run_id, source, report_date, allowed_codes, cancel),
        daemon=True, name=f"me2m-run-{run_id}",
    )
    _RUNS[run_id] = (thread, cancel)
    try:
        thread.start()
    except Exception as exc:
        _RUNS.pop(run_id, None)
        db.execute(
            "UPDATE me2m_runs SET status='failed', finished_at=?, error=?, current_step=NULL WHERE id=?",
            (_now(), "The report worker could not start. Please retry.", run_id),
        )
        raise ValueError("The report worker could not start. Please retry.") from exc
    return run_id


def stop_run(run_id):
    row = db.query_one("SELECT status FROM me2m_runs WHERE id=?", (run_id,))
    worker = _RUNS.get(run_id)
    if not row or row["status"] != "running" or not worker:
        raise ValueError("Only an active ME2M run can be stopped.")
    worker[1].set()
    db.execute("UPDATE me2m_runs SET current_step=? WHERE id=?", ("Stop requested; finishing current step", run_id))


def _execute_run(run_id, source, report_date, allowed_codes, cancel):
    started = time.monotonic()
    logger = logging.getLogger(f"me2m.run.{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handler = None
    try:
        log_rel = f"{_now()[:10]}/me2m_{run_id}.log"
        log_path = ME2M_LOG_DIR / log_rel
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s | %(message)s"))
        logger.addHandler(handler)
        db.execute("UPDATE me2m_runs SET log_file=? WHERE id=?", (log_rel, run_id))
        output = ME2M_OUTPUT_DIR / f"ME2M_{report_date}_Processed_run{run_id}.xlsx"
        logger.info("ME2M #%s | reporting date %s | source %s", run_id, report_date, source.name)

        def report_step(text):
            logger.info(text)
            db.execute("UPDATE me2m_runs SET current_step=? WHERE id=?", (text, run_id))

        stats = build_report(
            source, output, report_date=report_date, allowed_codes=allowed_codes,
            on_step=report_step, should_cancel=cancel.is_set,
        )
        db.execute(
            "UPDATE me2m_runs SET status='success', finished_at=?, duration_sec=?, total_rows=?, "
            "net_order_value=?, period_stats=?, group_stats=?, output_file=?, current_step=NULL WHERE id=?",
            (_now(), round(time.monotonic() - started, 1), stats["total_rows"], stats["net_order_value"],
             json.dumps({"counts": stats["counts"], "values": stats["values"]}),
             json.dumps(stats["by_group"]), output.name, run_id),
        )
        logger.info("Completed: %s rows, net order value %.2f, source sheet %s",
                    stats["total_rows"], stats["net_order_value"], stats["source_sheet"])
        logger.info("Period counts: %s", stats["counts"])
    except ME2MCancelled:
        logger.info("Stopped by user")
        db.execute(
            "UPDATE me2m_runs SET status='cancelled', finished_at=?, duration_sec=?, "
            "error='Stopped by user', current_step=NULL WHERE id=?",
            (_now(), round(time.monotonic() - started, 1), run_id),
        )
    except Exception as exc:
        logger.exception("ME2M report failed")
        error = str(exc) if isinstance(exc, ValueError) else f"{type(exc).__name__}: {exc}"
        db.execute(
            "UPDATE me2m_runs SET status='failed', finished_at=?, duration_sec=?, error=?, current_step=NULL WHERE id=?",
            (_now(), round(time.monotonic() - started, 1), error[:2000], run_id),
        )
    finally:
        try:
            cleanup_upload(source)
        except OSError:
            logger.warning("Could not remove the staged upload", exc_info=True)
        if handler:
            logger.removeHandler(handler)
            handler.close()
        _RUNS.pop(run_id, None)
