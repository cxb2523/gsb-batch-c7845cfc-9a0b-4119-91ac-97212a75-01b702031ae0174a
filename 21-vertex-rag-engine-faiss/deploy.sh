#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com

echo ""
echo "Creating BigQuery dataset (if it does not exist)..."
bq --project_id="$GOOGLE_CLOUD_PROJECT" mk --dataset \
  --location=US \
  "$GOOGLE_CLOUD_PROJECT:your_dataset" 2>/dev/null || echo "Dataset already exists — skipping."

echo ""
echo "Setup complete."
echo ""
echo "Next steps:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py faiss bq        # run FAISS + BQ demos (no corpus billing)"
echo "  python main.py                 # run all three (Vertex RAG creates a billable corpus)"
echo ""
echo "Region note for Vertex RAG Engine:"
echo "  The default region is europe-west4 (works without allowlisting)."
echo "  Override with: export RAG_ENGINE_LOCATION=europe-west4"
