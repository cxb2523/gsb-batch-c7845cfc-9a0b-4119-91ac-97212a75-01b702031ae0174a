#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

echo "Project: $GOOGLE_CLOUD_PROJECT"
echo ""

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling base GCP APIs..."
gcloud services enable \
  bigquery.googleapis.com \
  developerknowledge.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"

echo ""
echo "Enabling MCP surfaces (BOTH of these are required)..."
echo "NOTE: A 200 on initialize + 403 on tool calls means this step was skipped."
gcloud beta services mcp enable developerknowledge.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"
gcloud beta services mcp enable bigquery.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"

echo ""
echo "Creating BigQuery dataset (your_dataset) for testing..."
bq --project_id="$GOOGLE_CLOUD_PROJECT" mk \
  --dataset \
  --location=US \
  "${GOOGLE_CLOUD_PROJECT}:your_dataset" 2>/dev/null || echo "Dataset already exists — skipping."

echo ""
echo "Authenticating Application Default Credentials..."
gcloud auth application-default login --no-launch-browser || true

echo ""
echo "Setup complete."
echo ""
echo "Run the demo:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
