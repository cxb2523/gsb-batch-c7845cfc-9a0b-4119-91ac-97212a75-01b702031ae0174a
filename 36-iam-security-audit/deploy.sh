#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  serviceusage.googleapis.com \
  cloudresourcemanager.googleapis.com \
  iam.googleapis.com

echo ""
echo "Required IAM roles for the running principal:"
echo "  roles/serviceusage.serviceUsageViewer  (or roles/viewer)"
echo "  roles/iam.securityReviewer             (or roles/viewer)"
echo ""
echo "Grant with:"
echo "  gcloud projects add-iam-policy-binding \$GOOGLE_CLOUD_PROJECT \\"
echo "    --member='user:YOU@example.com' --role='roles/iam.securityReviewer'"
echo ""
echo "Setup complete. Run: python main.py"
