#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export GCS_BUCKET="${GCS_BUCKET:-${GOOGLE_CLOUD_PROJECT}-beam-dataflow}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "=== Enabling APIs ==="
gcloud services enable \
  dataflow.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  compute.googleapis.com

echo ""
echo "=== Creating GCS bucket for Dataflow staging + I/O ==="
if gsutil ls "gs://${GCS_BUCKET}" &>/dev/null; then
  echo "  Bucket gs://${GCS_BUCKET} already exists — skipping."
else
  gsutil mb -l "${GOOGLE_CLOUD_LOCATION}" "gs://${GCS_BUCKET}"
  echo "  Created gs://${GCS_BUCKET}"
fi

echo ""
echo "=== Uploading sample data to GCS ==="
gsutil cp data/sales_data.csv "gs://${GCS_BUCKET}/data/sales_data.csv"
echo "  Uploaded: gs://${GCS_BUCKET}/data/sales_data.csv"

echo ""
echo "=== Granting Dataflow worker SA required roles ==="
PROJECT_NUMBER=$(gcloud projects describe "${GOOGLE_CLOUD_PROJECT}" --format='value(projectNumber)')
WORKER_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud projects add-iam-policy-binding "${GOOGLE_CLOUD_PROJECT}" \
  --member="serviceAccount:${WORKER_SA}" \
  --role=roles/dataflow.worker \
  --quiet

gcloud projects add-iam-policy-binding "${GOOGLE_CLOUD_PROJECT}" \
  --member="serviceAccount:${WORKER_SA}" \
  --role=roles/storage.objectAdmin \
  --quiet

echo ""
echo "=== Setup complete ==="
echo ""
echo "Dry run (default — no cost, no job submitted):"
echo "  GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT} GCS_BUCKET=${GCS_BUCKET} python main.py"
echo ""
echo "Real Dataflow submission (BILLS WORKER VMs — ~a few cents for this dataset):"
echo "  GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT} \\"
echo "  GCS_BUCKET=${GCS_BUCKET} \\"
echo "  DRY_RUN=false python main.py"
echo ""
echo "Monitor jobs:"
echo "  gcloud dataflow jobs list --project=${GOOGLE_CLOUD_PROJECT} --region=${GOOGLE_CLOUD_LOCATION}"
echo ""
echo "REMINDER: Cancel any running job when done to avoid ongoing charges:"
echo "  gcloud dataflow jobs cancel <JOB_ID> --region=${GOOGLE_CLOUD_LOCATION}"
