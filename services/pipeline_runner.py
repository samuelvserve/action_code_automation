"""Background execution of the Action Code pipeline.

Runs are tracked in the `runs` table. A run starts immediately (status
'running') and executes on a daemon thread so the UI stays responsive; the
browser polls /run/<id>/status for progress. Every run writes its own log
file under logs/ and optionally emails the result.
"""
import contextlib
import io
import json
import logging
import shutil
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import action_code_pipeline as pipeline
from config import INPUT_DIR, LOG_DIR, MANUAL_UPLOAD_DIR, OUTPUT_DIR, RUN_TYPES
from database import db
from services import mailer

_RUNS = {}  # run_id -> thread, for introspection
# run_ids the user asked to stop; checked between pipeline steps.
_CANCEL = set()


def _ts():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log_path(run_id):
    """Per-date log file: logs/YYYY-MM-DD/run_<id>.log"""
    date_dir = LOG_DIR / datetime.now().strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    return date_dir / f"run_{run_id}.log"


def stop_run(run_id):
    """Request a running pipeline to stop at the next step boundary."""
    run = db.query_one("SELECT id, status FROM runs WHERE id = ?", (run_id,))
    if not run:
        raise ValueError("Run not found")
    if run["status"] != "running":
        raise ValueError("Only running runs can be stopped")
    _CANCEL.add(run_id)
    return True


def start_run(run_type, purchasing_codes, triggered_by, input_dir=None):
    """Validate inputs, record the run, and kick off the background thread.

    input_dir: optional per-run source-files directory (e.g. a staged folder
    of manually uploaded files). Defaults to config.INPUT_DIR when None.
    """
    if run_type not in RUN_TYPES:
        raise ValueError(f"Unknown run type: {run_type}")
    codes = [c.strip().upper() for c in purchasing_codes if c and c.strip()]
    if not codes:
        raise ValueError("Select at least one purchasing code")
    if len(codes) > 4:
        raise ValueError("Select at most 4 purchasing codes per run")

    source_mode = "upload" if input_dir else "folder"
    run_id = db.execute(
        "INSERT INTO runs (run_type, purchasing_codes, status, started_at, triggered_by, source_mode) "
        "VALUES (?, ?, 'running', ?, ?, ?)",
        (run_type, ",".join(codes), _ts(), triggered_by, source_mode),
    )

    thread = threading.Thread(
        target=_execute_run, args=(run_id, run_type, codes, input_dir), daemon=True, name=f"acr-run-{run_id}"
    )
    _RUNS[run_id] = thread
    thread.start()
    return run_id


class _Tee(io.TextIOBase):
    """Write to the run log file AND mirror to the original stdout."""
    def __init__(self, log_handle, original_stdout):
        self.log_handle = log_handle
        self.original_stdout = original_stdout

    def write(self, text):
        try:
            self.log_handle.write(text)
            self.log_handle.flush()
        except Exception:  # noqa: BLE001 - never break the run on log I/O
            pass
        try:
            self.original_stdout.write(text)
            self.original_stdout.flush()
        except Exception:  # noqa: BLE001
            pass
        return len(text)

    def flush(self):
        try:
            self.log_handle.flush()
        except Exception:  # noqa: BLE001
            pass


def _setup_logger(run_id, log_path):
    logger = logging.getLogger(f"acr_run_{run_id}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(sh)

    # The pipeline logs through the module-level `logging` API; point the
    # "action_code_pipeline" logger at the same file.
    plogger = logging.getLogger("action_code_pipeline")
    plogger.setLevel(logging.INFO)
    plogger.handlers.clear()
    plogger.addHandler(fh)
    plogger.propagate = False
    return logger, fh


def _log_rel(log_path):
    """Log file stored in the DB, relative to LOG_DIR (keeps the date part)."""
    return str(log_path.relative_to(LOG_DIR)).replace("\\", "/")


def _execute_run(run_id, run_type, codes, input_dir=None):
    log_path = _log_path(run_id)
    log_rel = _log_rel(log_path)
    logger, fh = _setup_logger(run_id, log_path)
    started = time.time()
    effective_input_dir = input_dir or INPUT_DIR
    # Capture the pipeline's print() progress into the run log as well.
    tee = _Tee(fh.stream, sys.stdout)
    redirect = contextlib.redirect_stdout(tee)
    try:
        logger.info("=== Run #%s started | type=%s | purchasing=%s ===", run_id, run_type, codes)
        logger.info(
            "Input dir: %s%s", effective_input_dir, " (manually uploaded for this run)" if input_dir else ""
        )
        db.execute("UPDATE runs SET current_step=? WHERE id=?", ("Starting pipeline...", run_id))

        def report_step(text):
            db.execute("UPDATE runs SET current_step=? WHERE id=?", (text, run_id))

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_name = f"Master_File_Action_Code_{'_'.join(codes)}_{run_type}_{stamp}.xlsx"

        with redirect:
            stats = pipeline.run_pipeline(
                purchasing_groups=codes,
                input_dir=effective_input_dir,
                output_dir=OUTPUT_DIR,
                output_name=output_name,
                should_cancel=lambda: run_id in _CANCEL,
                on_step=report_step,
            )

        duration = round(time.time() - started, 1)
        db.execute(
            "UPDATE runs SET status='success', finished_at=?, duration_sec=?, "
            "total_rows=?, action_items=?, total_demand=?, code_stats=?, "
            "output_file=?, log_file=?, current_step=NULL WHERE id=?",
            (
                _ts(),
                duration,
                stats["total_rows"],
                stats["action_items"],
                stats["total_demand"],
                json.dumps(stats.get("by_code", {})),
                Path(stats["output_path"]).name,
                log_rel,
                run_id,
            ),
        )
        logger.info(
            "=== Run #%s finished OK | rows=%s | action_items=%s | duration=%ss ===",
            run_id, stats["total_rows"], stats["action_items"], duration,
        )
    except pipeline.PipelineCancelled:
        duration = round(time.time() - started, 1)
        logger.info("=== Run #%s STOPPED by user | duration=%ss ===", run_id, duration)
        db.execute(
            "UPDATE runs SET status='cancelled', finished_at=?, duration_sec=?, "
            "error='Stopped by user', log_file=?, current_step=NULL WHERE id=?",
            (_ts(), duration, log_rel, run_id),
        )
    except Exception as exc:  # noqa: BLE001 - report anything to the UI/log
        duration = round(time.time() - started, 1)
        error = f"{type(exc).__name__}: {exc}"
        logger.error("=== Run #%s FAILED ===\n%s", run_id, traceback.format_exc())
        db.execute(
            "UPDATE runs SET status='failed', finished_at=?, duration_sec=?, "
            "error=?, log_file=?, current_step=NULL WHERE id=?",
            (_ts(), duration, error[:2000], log_rel, run_id),
        )
    finally:
        # Best-effort email (only after the DB row is final).
        try:
            row = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
            mailer.notify_run(row)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to send notification email: %s", e)
        # Manually uploaded source files are only needed for the duration of
        # this run - remove the staged copy now that every step has read it,
        # regardless of how the run ended.
        if input_dir:
            try:
                if str(Path(input_dir).resolve()).startswith(str(MANUAL_UPLOAD_DIR.resolve())):
                    shutil.rmtree(input_dir, ignore_errors=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to clean up uploaded source files: %s", e)
        _RUNS.pop(run_id, None)
        _CANCEL.discard(run_id)
