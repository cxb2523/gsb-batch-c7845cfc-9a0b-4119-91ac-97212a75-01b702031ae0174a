#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export BQ_DATASET="${BQ_DATASET:-your_dataset}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable \
  bigquery.googleapis.com

echo ""
echo "============================================================"
echo "Setup complete. Run: python main.py"
echo "============================================================"
