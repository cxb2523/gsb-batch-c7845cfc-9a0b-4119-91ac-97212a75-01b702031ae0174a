#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export DOCAI_GCS_BUCKET="${DOCAI_GCS_BUCKET:-your-gcs-bucket}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  documentai.googleapis.com \
  storage.googleapis.com

echo ""
echo "Ensuring GCS bucket exists: gs://${DOCAI_GCS_BUCKET}"
gcloud storage buckets create "gs://${DOCAI_GCS_BUCKET}" \
  --project="$GOOGLE_CLOUD_PROJECT" \
  --location=US \
  --uniform-bucket-level-access 2>/dev/null || echo "Bucket already exists — skipping."

echo ""
echo "Setup complete. Document AI API and GCS bucket are ready."
echo ""
echo "Next steps:"
echo "  1. Create processor IDs in the GCP Console:"
echo "     For batch demo  : Document AI > Create Processor > OCR Processor"
echo "     For layout demo : Document AI > Create Processor > Layout Parser"
echo ""
echo "  2. Export variables:"
echo "     export DOCAI_PROCESSOR_ID=your-processor-id"
echo "     export DOCAI_GCS_BUCKET=${DOCAI_GCS_BUCKET}"
echo ""
echo "  3. Install and run:"
echo "     python3 -m venv .venv && source .venv/bin/activate"
echo "     pip install -r requirements.txt"
echo "     python main.py                   # both demos"
echo "     python main.py --mode batch      # batch GCS only"
echo "     python main.py --mode layout     # layout chunking only"
