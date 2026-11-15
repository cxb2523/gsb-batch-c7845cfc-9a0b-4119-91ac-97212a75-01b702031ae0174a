#!/usr/bin/env bash
# Setup for 04-bq-finops-cost-visibility — replace YOUR_* values before running
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export BQ_DATASET="${BQ_DATASET:-your_dataset}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable bigquery.googleapis.com bigquerydatatransfer.googleapis.com

echo "Creating BigQuery dataset (if not exists) ..."
bq --location=US mk --dataset --description "GCP Data Engineering Hub demo dataset" \
  "${GOOGLE_CLOUD_PROJECT}:${BQ_DATASET}" 2>/dev/null || echo "Dataset already exists."

echo ""
echo "IAM note: The INFORMATION_SCHEMA FinOps queries require"
echo "  roles/bigquery.resourceViewer  (or bigquery.jobs.listAll)"
echo "  on project ${GOOGLE_CLOUD_PROJECT}"
echo ""
echo "Setup complete. Run: python main.py"
