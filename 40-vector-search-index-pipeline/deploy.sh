#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  storage.googleapis.com

echo ""
echo "Required IAM roles for the running principal:"
echo "  roles/aiplatform.user  (create indexes, endpoints, query)"
echo "  roles/storage.admin    (GCS bucket for index staging — needed for batch index creation)"
echo ""
echo "Grant with:"
echo "  gcloud projects add-iam-policy-binding \$GOOGLE_CLOUD_PROJECT \\"
echo "    --member='user:YOU@example.com' --role='roles/aiplatform.user'"
echo ""
echo "--- GCS BUCKET FOR INDEX STAGING (needed for non-streaming batch index creation) ---"
echo "  If you plan to bulk-load vectors from a JSON/CSV file in GCS:"
echo "  1. Create a GCS bucket in the same region as your index:"
echo "       gsutil mb -l \$GOOGLE_CLOUD_LOCATION gs://\${GOOGLE_CLOUD_PROJECT}-vs-staging"
echo "  2. Upload your embedding JSON files there before calling create_tree_ah_index."
echo "  For STREAM_UPDATE (this project's default), no GCS bucket is required."
echo ""
echo "--- COST WARNING ---"
echo "  DRY_RUN=True by default — the script prints a plan without creating resources."
echo "  A deployed Vector Search endpoint bills PER NODE-HOUR 24/7 even with zero queries."
echo "  Smallest configuration: ~\$60-80+/month. Always undeploy when done."
echo "  To enable real resource creation:"
echo "    DRY_RUN=false python main.py"
echo ""
echo "Setup complete. Run: python main.py"
