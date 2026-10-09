"""Central configuration for the Action Code Dashboard (Flask app)."""
import os
import secrets
from pathlib import Path

# Paths are resolved against this file so the app works no matter where it is
# launched from.
BASE_DIR = Path(__file__).resolve().parent
INPUT_DIR = BASE_DIR / "inputs"
OUTPUT_DIR = BASE_DIR / "outputs"
LOG_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "acr_app.db"

# Create runtime directories up front.
for _d in (INPUT_DIR, OUTPUT_DIR, LOG_DIR, DATA_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# Purchasing codes selectable in the UI.
PURCHASING_CODES = [
    "D07", "D09", "D13", "D17", "D25", "D20",
    "D40", "D16", "D30", "S18", "S02", "S04",
]
DEFAULT_PURCHASING_CODE = "D13"

# Run types offered by the UI.
RUN_TYPES = ["Instant Run", "Daily", "Weekly", "Monthly"]

# Run statuses the UI understands (success / failed / running / user-stop /
# app-restart-mid-run).
RUN_STATUSES = ("success", "failed", "running", "cancelled", "interrupted")

# Weekday options for schedules (ISO weekday ints: 0=Monday .. 6=Sunday).
WEEKDAYS = [
    ("0", "Monday"), ("1", "Tuesday"), ("2", "Wednesday"),
    ("3", "Thursday"), ("4", "Friday"), ("5", "Saturday"), ("6", "Sunday"),
]

# Session security. Persisted across restarts so users stay logged in.
_SECRET_FILE = DATA_DIR / "secret_key"
if _SECRET_FILE.exists():
    SECRET_KEY = _SECRET_FILE.read_text().strip()
else:
    SECRET_KEY = secrets.token_hex(32)
    _SECRET_FILE.write_text(SECRET_KEY)

# Flask — Cloud Run injects PORT; ACR_HOST/ACR_PORT used for local dev.
# On Cloud Run: HOST=0.0.0.0, PORT=8080 (set via env vars in Dockerfile).
# Locally:      HOST=127.0.0.1, PORT=5000 (unchanged defaults).
DEBUG = os.environ.get("FLASK_DEBUG", "0") == "1"
HOST = os.environ.get("ACR_HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", os.environ.get("ACR_PORT", "5000")))

# Default login created on first init (reads from env var or falls back to standard defaults).
DEFAULT_ADMIN_USER = os.environ.get("ACR_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASSWORD = os.environ.get("ACR_ADMIN_PASSWORD", "ACR@Adm1n#2024!")

# Downloadable outputs live under this directory only (path-traversal guard).
OUTPUT_DIR_RESOLVED = str(OUTPUT_DIR.resolve())
LOG_DIR_RESOLVED = str(LOG_DIR.resolve())

# Instant Run "source inputs" options. By default a run reads the six
# GAINS/SAP exports already sitting in inputs/ (see action_code_pipeline.FILES
# for the exact filenames). The Run page also offers a per-run alternative:
# upload the six files instead. SOURCE_FILE_LABELS keys must match
# action_code_pipeline.FILES keys exactly - they're used to build the upload
# form and to save each upload under the filename the pipeline expects.
SOURCE_FILE_LABELS = {
    "gains_raw": "GAINS raw export (AC12 hot)",
    "date_of_supply": "Date of Supply",
    "total_demand": "Total Demand (GAINS pivot)",
    "mb52_fert_oh": "SAP MB52 \u2014 Fert OH",
    "mb52_raw_oh": "SAP MB52 \u2014 Raw OH",
    "zexp": "SAP ZEXP \u2014 Current OO",
}

# Per-run staging area for manually uploaded source files. Each upload run
# gets its own uniquely-named subfolder here, which is deleted once that run
# finishes (success, failure, or stop) - see services/pipeline_runner.py.
MANUAL_UPLOAD_DIR = BASE_DIR / "inputs" / "_manual_uploads"
MANUAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# ME2M has its own files and run history, separate from the AC12 pipeline.
ME2M_DIR = BASE_DIR / "me2m"
ME2M_INPUT_DIR = ME2M_DIR / "inputs"
ME2M_UPLOAD_DIR = ME2M_DIR / "uploads"
ME2M_OUTPUT_DIR = ME2M_DIR / "outputs"
ME2M_LOG_DIR = ME2M_DIR / "logs"
ME2M_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
