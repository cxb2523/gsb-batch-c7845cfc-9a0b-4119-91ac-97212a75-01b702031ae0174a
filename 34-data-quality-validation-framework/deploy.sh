#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "This project runs locally using pandas — no GCP APIs required."
echo ""
echo "To integrate with GCP pipelines:"
echo "  - Wrap in a Cloud Run Job and gate your Dataflow/BQ load task on exit code."
echo "  - Set SAVE_REPORT=true to emit a JSON report (e.g. upload to GCS for audit trail)."
echo ""
echo "Setup complete."
echo ""
echo "Run:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
echo ""
echo "With JSON report output:"
echo "  SAVE_REPORT=true python main.py"
