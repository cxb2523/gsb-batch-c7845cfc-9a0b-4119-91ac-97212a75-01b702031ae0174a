#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export GCS_BUCKET="${GCS_BUCKET:-${GOOGLE_CLOUD_PROJECT}-etl-demo}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable bigquery.googleapis.com \
                       storage.googleapis.com \
                       bigquerystorage.googleapis.com

echo ""
echo "Creating GCS bucket (if it doesn't exist)..."
gcloud storage buckets create "gs://${GCS_BUCKET}" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --location="US" \
  --uniform-bucket-level-access 2>/dev/null \
  || echo "  Bucket gs://${GCS_BUCKET} already exists — skipping."

echo ""
echo "Setup complete."
echo "  Project : $GOOGLE_CLOUD_PROJECT"
echo "  Bucket  : $GCS_BUCKET"
echo "  Dataset : sales_etl_demo (created automatically by main.py)"
echo ""
echo "Run:"
echo "  export GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT"
echo "  export GCS_BUCKET=$GCS_BUCKET"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
