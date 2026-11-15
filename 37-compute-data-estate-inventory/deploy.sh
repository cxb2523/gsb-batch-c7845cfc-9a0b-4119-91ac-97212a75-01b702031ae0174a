#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  compute.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com

echo ""
echo "Required IAM roles for the running principal:"
echo "  roles/compute.viewer          (list VMs and disks)"
echo "  roles/bigquery.metadataViewer (list datasets and tables)"
echo "  roles/storage.objectViewer    (list buckets)"
echo ""
echo "Grant with (example for viewer):"
echo "  gcloud projects add-iam-policy-binding \$GOOGLE_CLOUD_PROJECT \\"
echo "    --member='user:YOU@example.com' --role='roles/viewer'"
echo ""
echo "Setup complete. Run: python main.py"
