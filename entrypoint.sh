#!/bin/bash
# ── ACR Dashboard — Cloud Run entrypoint ────────────────────────────────────
# 1. Mounts the GCS bucket so all persistent data survives container restarts
# 2. Starts gunicorn with settings tuned for Cloud Run (1 worker, 4 threads)
# ─────────────────────────────────────────────────────────────────────────────
set -e

GCS_BUCKET="${GCS_BUCKET:-acr-dashboard-data}"

echo "[entrypoint] Mounting GCS bucket: gs://${GCS_BUCKET} -> /app/data"

# Create local mount points that mirror config.py paths
mkdir -p /app/data /app/inputs /app/outputs /app/logs /app/me2m

# Mount the GCS bucket at /app/data (SQLite DB + secret_key live here)
gcsfuse \
  --implicit-dirs \
  --log-severity ERROR \
  "${GCS_BUCKET}" /app/data

echo "[entrypoint] GCS bucket mounted OK"

# Gunicorn: 1 worker keeps SQLite from concurrent-write issues;
# 4 threads handle the light concurrent HTTP load of 1-3 users.
# timeout 300s covers long-running pipeline runs without killing the request.
echo "[entrypoint] Starting gunicorn on 0.0.0.0:${PORT}"
exec gunicorn \
  --workers 1 \
  --threads 4 \
  --timeout 300 \
  --bind "0.0.0.0:${PORT}" \
  --access-logfile - \
  --error-logfile - \
  app:app
