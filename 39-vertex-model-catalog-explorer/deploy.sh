#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  aiplatform.googleapis.com

echo ""
echo "Required IAM roles for the running principal:"
echo "  roles/aiplatform.user  (list publisher models on Vertex AI)"
echo ""
echo "Grant with:"
echo "  gcloud projects add-iam-policy-binding \$GOOGLE_CLOUD_PROJECT \\"
echo "    --member='user:YOU@example.com' --role='roles/aiplatform.user'"
echo ""
echo "NOTE: Listing publisher models is a read-only call — no cost is incurred."
echo ""
echo "Setup complete. Run: python main.py"
