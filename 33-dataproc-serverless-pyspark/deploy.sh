#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export GCS_BUCKET="${GCS_BUCKET:-your-gcs-bucket}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required GCP APIs..."
gcloud services enable dataproc.googleapis.com storage.googleapis.com

echo ""
echo "Creating GCS staging bucket: gs://$GCS_BUCKET"
gcloud storage buckets create "gs://$GCS_BUCKET" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --location="$GOOGLE_CLOUD_LOCATION" \
  --uniform-bucket-level-access 2>/dev/null || echo "  Bucket already exists."

echo ""
echo "Granting Dataproc worker role to default compute service account..."
PROJECT_NUMBER=$(gcloud projects describe "$GOOGLE_CLOUD_PROJECT" --format="value(projectNumber)")
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
  --member="serviceAccount:${COMPUTE_SA}" \
  --role="roles/dataproc.worker" \
  --condition=None 2>/dev/null || echo "  IAM binding already exists or skipped."

echo ""
echo "Setup complete."
echo ""
echo "IMPORTANT: By default DRY_RUN=true — no real batch will be submitted."
echo "To submit a REAL paid Dataproc Serverless batch, run:"
echo ""
echo "  export DRY_RUN=false"
echo "  python main.py"
echo ""
echo "Standard run (dry run, no Dataproc cost):"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
