#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GCS_BUCKET="${GCS_BUCKET:-your-gcs-bucket}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable \
  storage.googleapis.com

echo ""
echo "Uploading sales_data.csv to gs://${GCS_BUCKET}/data/sales_data.csv ..."
echo "(Required for Pattern C: pandas over gs://)"
gsutil cp data/sales_data.csv "gs://${GCS_BUCKET}/data/sales_data.csv"

echo ""
echo "============================================================"
echo "Setup complete. Run: python main.py"
echo "============================================================"
