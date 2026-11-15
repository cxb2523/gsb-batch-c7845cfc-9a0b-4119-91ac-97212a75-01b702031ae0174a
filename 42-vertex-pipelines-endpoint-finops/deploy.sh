#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export GOOGLE_CLOUD_BUCKET="${GOOGLE_CLOUD_BUCKET:-your-gcs-bucket}"

echo "Project  : $GOOGLE_CLOUD_PROJECT"
echo "Location : $GOOGLE_CLOUD_LOCATION"
echo "Bucket   : $GOOGLE_CLOUD_BUCKET (pipeline root for real runs)"
echo ""

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required GCP APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  storage.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"

echo ""
echo "Authenticating Application Default Credentials..."
gcloud auth application-default login --no-launch-browser || true

echo ""
echo "Setup complete."
echo ""
echo "Run the pipeline:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
echo ""
echo "Pipeline defaults to DRY_RUN=true (compile only, no billable jobs)."
echo "To submit a real Vertex Pipeline job:"
echo "  DRY_RUN=false python main.py"
