#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

# DirectRunner requires NO GCP setup — runs entirely locally.
# This script is a no-op for local runs, but documents the DataflowRunner path.

echo "=== Project 47: Beam Data Transformation ==="
echo "DirectRunner: no GCP project or credentials required."
echo "Run: python main.py"
echo ""
echo "=== Optional: DataflowRunner setup ==="
echo "If you want to run these pipelines on the managed Dataflow service:"
echo ""

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

gcloud services enable \
  dataflow.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com

echo ""
echo "Create a GCS bucket for staging:"
echo "  gsutil mb -l ${GOOGLE_CLOUD_LOCATION} gs://${GOOGLE_CLOUD_PROJECT}-beam-staging"
echo ""
echo "Upload input data to GCS:"
echo "  gsutil cp data/sales_data.csv gs://${GOOGLE_CLOUD_PROJECT}-beam-staging/data/"
echo ""
echo "Then update PipelineOptions in csv_transform.py:"
echo "  runner='DataflowRunner'"
echo "  project='${GOOGLE_CLOUD_PROJECT}'"
echo "  region='${GOOGLE_CLOUD_LOCATION}'"
echo "  temp_location='gs://${GOOGLE_CLOUD_PROJECT}-beam-staging/tmp/'"
echo ""
echo "Setup complete. Run: python main.py"
