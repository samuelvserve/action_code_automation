"""SQLite helpers for the Action Code Dashboard.

Uses Python's stdlib sqlite3 - one short-lived connection per operation so it
is safe to call from request threads and the background run thread alike.
"""
import json
import sqlite3
from contextlib import contextmanager

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    full_name     TEXT DEFAULT '',
    role          TEXT DEFAULT 'buyer',
    allowed_codes TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    run_type         TEXT NOT NULL,
    purchasing_codes TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'running',
    started_at       TEXT NOT NULL,
    finished_at      TEXT,
    duration_sec     REAL,
    total_rows       INTEGER,
    action_items     INTEGER,
    total_demand     REAL,
    output_file      TEXT,
    log_file         TEXT,
    error            TEXT,
    triggered_by     TEXT,
    source_mode      TEXT DEFAULT 'folder'
);

CREATE TABLE IF NOT EXISTS schedules (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    run_type         TEXT NOT NULL DEFAULT 'Daily',
    purchasing_codes TEXT NOT NULL,
    run_time         TEXT NOT NULL,
    days             TEXT DEFAULT '',
    enabled          INTEGER DEFAULT 1,
    last_run_at      TEXT,
    last_run_date    TEXT,
    created_by       TEXT,
    created_at       TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS me2m_runs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file       TEXT NOT NULL,
    report_date       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'running',
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    duration_sec      REAL,
    triggered_by      TEXT NOT NULL,
    total_rows        INTEGER,
    net_order_value   REAL,
    period_stats      TEXT,
    group_stats       TEXT,
    output_file       TEXT,
    log_file          TEXT,
    current_step      TEXT,
    error             TEXT
);
CREATE INDEX IF NOT EXISTS idx_me2m_runs_started ON me2m_runs(started_at);

CREATE TABLE IF NOT EXISTS me2m_schedules (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT NOT NULL,
    input_folder      TEXT NOT NULL,
    run_time          TEXT NOT NULL,
    days              TEXT DEFAULT '',
    enabled           INTEGER DEFAULT 1,
    created_by        TEXT NOT NULL,
    created_at        TEXT DEFAULT (datetime('now', 'localtime')),
    last_run_at       TEXT,
    last_run_date     TEXT,
    last_run_id       INTEGER,
    last_error        TEXT
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


def _migrate(conn):
    """Lightweight additive migrations for existing databases."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()}
    if "code_stats" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN code_stats TEXT")
    if "current_step" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN current_step TEXT")
    if "source_mode" not in cols:
        conn.execute("ALTER TABLE runs ADD COLUMN source_mode TEXT DEFAULT 'folder'")

    user_cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "allowed_codes" not in user_cols:
        conn.execute("ALTER TABLE users ADD COLUMN allowed_codes TEXT DEFAULT ''")

    me2m_cols = {r["name"] for r in conn.execute("PRAGMA table_info(me2m_runs)").fetchall()}
    if "schedule_id" not in me2m_cols:
        conn.execute("ALTER TABLE me2m_runs ADD COLUMN schedule_id INTEGER")


def query(sql, params=()):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def query_one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with get_conn() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


# ---------------------------------------------------------------------------
# settings helpers (small JSON values keyed by name)
# ---------------------------------------------------------------------------
def get_setting(key, default=None):
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return row["value"]


def set_setting(key, value):
    payload = json.dumps(value) if not isinstance(value, str) else value
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, payload),
        )
