#!/usr/bin/env bash
# Setup for 02-bq-analytics-reporting — replace YOUR_* values before running
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export BQ_DATASET="${BQ_DATASET:-your_dataset}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable bigquery.googleapis.com

echo "Creating BigQuery dataset (if not exists) ..."
bq --location=US mk --dataset --description "GCP Data Engineering Hub demo dataset" \
  "${GOOGLE_CLOUD_PROJECT}:${BQ_DATASET}" 2>/dev/null || echo "Dataset already exists."

echo ""
echo "Setup complete. Run: python main.py"
