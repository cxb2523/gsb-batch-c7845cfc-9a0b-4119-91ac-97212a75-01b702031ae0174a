#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GCS_BUCKET="${GCS_BUCKET:-your-gcs-bucket}"
export BQ_DATASET="${BQ_DATASET:-your_dataset}"
export BQ_CONNECTION_ID="${BQ_CONNECTION_ID:-vertex-ai-conn}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable \
  bigquery.googleapis.com \
  bigqueryconnection.googleapis.com \
  aiplatform.googleapis.com \
  storage.googleapis.com

echo ""
echo "Uploading sales_data.csv to gs://${GCS_BUCKET}/data/sales_data.csv ..."
gsutil cp data/sales_data.csv "gs://${GCS_BUCKET}/data/sales_data.csv"

echo ""
echo "============================================================"
echo "  BQ Connection Setup (required for AI embeddings)"
echo "============================================================"
echo ""
echo "Step 1: Create the Cloud Resource connection (US region):"
echo "  bq mk --connection --location=US \\"
echo "    --project_id=${GOOGLE_CLOUD_PROJECT} \\"
echo "    --connection_type=CLOUD_RESOURCE ${BQ_CONNECTION_ID}"
echo ""
echo "Step 2: Find the connection service account:"
echo "  bq show --connection ${GOOGLE_CLOUD_PROJECT}.US.${BQ_CONNECTION_ID}"
echo ""
echo "Step 3: Grant the service account Vertex AI access:"
echo "  gcloud projects add-iam-policy-binding ${GOOGLE_CLOUD_PROJECT} \\"
echo "    --member=\"serviceAccount:<connection-sa-from-step-2>\" \\"
echo "    --role=\"roles/aiplatform.user\""
echo ""
echo "============================================================"
echo "Setup complete. Run: python main.py"
echo "============================================================"
