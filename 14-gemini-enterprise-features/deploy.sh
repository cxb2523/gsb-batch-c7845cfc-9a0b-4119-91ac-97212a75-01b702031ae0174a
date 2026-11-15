#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"
gcloud services enable aiplatform.googleapis.com storage.googleapis.com

echo "Setup complete. Run: python main.py"
echo "For batch prediction, also export: GCS_BUCKET=your-gcs-bucket"
