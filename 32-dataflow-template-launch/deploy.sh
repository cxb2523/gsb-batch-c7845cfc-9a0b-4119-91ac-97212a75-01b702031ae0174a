#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export GCS_BUCKET="${GCS_BUCKET:-your-gcs-bucket}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required GCP APIs..."
gcloud services enable dataflow.googleapis.com storage.googleapis.com

echo ""
echo "Creating GCS staging/output bucket: gs://$GCS_BUCKET"
gcloud storage buckets create "gs://$GCS_BUCKET" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --location="$GOOGLE_CLOUD_LOCATION" \
  --uniform-bucket-level-access 2>/dev/null || echo "  Bucket already exists."

echo ""
echo "Setup complete."
echo ""
echo "IMPORTANT: By default DRY_RUN=true — no real job will be submitted."
echo "To submit a REAL paid Dataflow job, run:"
echo ""
echo "  export DRY_RUN=false"
echo "  python main.py"
echo ""
echo "Standard run (dry run, no cost):"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
