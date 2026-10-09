# ── ACR Dashboard — Cloud Run Dockerfile ───────────────────────────────────
# Clean, minimal image. Persistent storage (SQLite DB, inputs, outputs, logs)
# is handled by Cloud Run's native GCS volume mount — no gcsfuse needed.
# GCS bucket is mounted at /app/data via --add-volume in gcloud run deploy.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

WORKDIR /app

# Install Python deps first (layer cache: only rebuilds when requirements change)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Cloud Run injects PORT env var; default to 8080
ENV ACR_HOST=0.0.0.0
ENV PORT=8080

EXPOSE 8080

# gunicorn: 1 worker (keeps SQLite safe from concurrent writes)
#           4 threads (handles light 1-3 user concurrency)
#           300s timeout (covers long pipeline runs)
CMD exec gunicorn \
      --workers 1 \
      --threads 4 \
      --timeout 300 \
      --bind "0.0.0.0:${PORT}" \
      --access-logfile - \
      --error-logfile - \
      app:app
