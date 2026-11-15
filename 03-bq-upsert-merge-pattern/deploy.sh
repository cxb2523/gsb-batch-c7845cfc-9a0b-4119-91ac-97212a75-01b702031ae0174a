#!/usr/bin/env bash
# Setup for 03-bq-upsert-merge-pattern — replace YOUR_* values before running
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export GCS_BUCKET="${GCS_BUCKET:-your-gcs-bucket}"
export BQ_DATASET="${BQ_DATASET:-your_dataset}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable bigquery.googleapis.com storage.googleapis.com

echo "Creating BigQuery dataset (if not exists) ..."
bq --location=US mk --dataset --description "GCP Data Engineering Hub demo dataset" \
  "${GOOGLE_CLOUD_PROJECT}:${BQ_DATASET}" 2>/dev/null || echo "Dataset already exists."

echo "Creating GCS bucket (if not exists) ..."
gcloud storage buckets create "gs://${GCS_BUCKET}" \
  --location=US \
  --uniform-bucket-level-access 2>/dev/null || echo "Bucket already exists."

echo "Uploading sales_data.csv to GCS ..."
gcloud storage cp data/sales_data.csv "gs://${GCS_BUCKET}/data/sales_data.csv"

echo ""
echo "Setup complete. Run: python main.py"
