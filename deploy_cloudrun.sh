#!/usr/bin/env bash
# =============================================================================
#  ACR Dashboard — Cloud Run Deployment Script
#  Project : ambient-segment-468709-a1
#  Region  : asia-south1
#  VM being replaced: acr-automation (e2-small, asia-south1-a)
# =============================================================================

set -euo pipefail

PROJECT="ambient-segment-468709-a1"
REGION="asia-south1"
SERVICE="acr-dashboard"
REPO="cloud-run-source-deploy"       # already exists in your project
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}:latest"
SA_NAME="acr-dashboard-sa"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
BUCKET="acr-dashboard-data"

echo ""
echo "============================================================"
echo "  ACR Dashboard — Cloud Run Deployment"
echo "  Project : ${PROJECT}"
echo "  Region  : ${REGION}"
echo "  Image   : ${IMAGE}"
echo "============================================================"
echo ""

# ── STEP 1: Enable required APIs ─────────────────────────────────────────────
echo "[1/8] Enabling required GCP APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  cloudbuild.googleapis.com \
  --project="${PROJECT}"

# ── STEP 2: Create GCS bucket for persistent data ────────────────────────────
echo "[2/8] Creating GCS bucket: gs://${BUCKET}..."
gcloud storage buckets create "gs://${BUCKET}" \
  --location="${REGION}" \
  --uniform-bucket-level-access \
  --project="${PROJECT}" 2>/dev/null || echo "  Bucket already exists — skipping."

# ── STEP 3: Create Service Account ───────────────────────────────────────────
echo "[3/8] Creating service account: ${SA_EMAIL}..."
gcloud iam service-accounts create "${SA_NAME}" \
  --display-name="ACR Dashboard Service Account" \
  --project="${PROJECT}" 2>/dev/null || echo "  Service account already exists — skipping."

# ── STEP 4: Grant permissions ─────────────────────────────────────────────────
echo "[4/8] Granting GCS permissions to service account..."
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/storage.objectAdmin"

# Allow service account to act as itself (needed for gcsfuse token refresh)
gcloud projects add-iam-policy-binding "${PROJECT}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountTokenCreator" 2>/dev/null || true

# ── STEP 5: Copy existing VM data to GCS ─────────────────────────────────────
echo "[5/8] NOTE: Copy your existing data from the VM to GCS bucket BEFORE deploying."
echo "  Run on your VM (acr-automation):"
echo ""
echo "    gcloud storage cp -r /path/to/acr_dashboard/data/*  gs://${BUCKET}/data/"
echo ""
echo "  (Skip if fresh install — the app will create the DB on first run)"
echo ""
read -p "  Press ENTER to continue with build & deploy..."

# ── STEP 6: Build container image via Cloud Build ────────────────────────────
echo "[6/8] Building container image with Cloud Build..."
gcloud builds submit \
  --tag "${IMAGE}" \
  --project="${PROJECT}" \
  "$(dirname "$0")"

# ── STEP 7: Deploy to Cloud Run ───────────────────────────────────────────────
echo "[7/8] Deploying to Cloud Run..."
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --platform managed \
  --memory 1Gi \
  --cpu 1 \
  --min-instances 1 \
  --max-instances 3 \
  --timeout 300 \
  --concurrency 10 \
  --service-account "${SA_EMAIL}" \
  --set-env-vars "GCS_BUCKET=${BUCKET},ACR_HOST=0.0.0.0" \
  --no-allow-unauthenticated \
  --project="${PROJECT}"

# ── STEP 8: Add IAP / allow your users ───────────────────────────────────────
echo "[8/8] Deployment complete!"
echo ""
SERVICE_URL=$(gcloud run services describe "${SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT}" \
  --format="value(status.url)")
echo "  ✅ Service URL: ${SERVICE_URL}"
echo ""
echo "  The service is NOT publicly accessible (--no-allow-unauthenticated)."
echo "  To allow your team to access it, run:"
echo ""
echo "    gcloud run services add-iam-policy-binding ${SERVICE} \\"
echo "      --region=${REGION} \\"
echo "      --member='user:YOUR_EMAIL@gmail.com' \\"
echo "      --role='roles/run.invoker' \\"
echo "      --project=${PROJECT}"
echo ""
echo "  Or to make it fully public (not recommended for internal tools):"
echo "    gcloud run services add-iam-policy-binding ${SERVICE} \\"
echo "      --region=${REGION} --member='allUsers' --role='roles/run.invoker'"
echo ""
echo "  🧹 Once verified, stop the old VM to save costs:"
echo "    gcloud compute instances stop acr-automation --zone=asia-south1-a --project=${PROJECT}"
echo ""
