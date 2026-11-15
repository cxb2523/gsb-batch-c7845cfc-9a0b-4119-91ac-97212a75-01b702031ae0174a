#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export GCS_BUCKET="${GCS_BUCKET:-${GOOGLE_CLOUD_PROJECT}-beam-staging}"
export BQ_DATASET="your_dataset"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "=== Enabling APIs ==="
gcloud services enable \
  dataflow.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com

echo ""
echo "=== Creating GCS bucket (for WriteToBigQuery temp staging) ==="
if gsutil ls "gs://${GCS_BUCKET}" &>/dev/null; then
  echo "  Bucket gs://${GCS_BUCKET} already exists — skipping."
else
  gsutil mb -l "${GOOGLE_CLOUD_LOCATION}" "gs://${GCS_BUCKET}"
  echo "  Created gs://${GCS_BUCKET}"
fi

echo ""
echo "=== Creating BigQuery dataset ==="
if bq ls --datasets --project_id="${GOOGLE_CLOUD_PROJECT}" | grep -q "${BQ_DATASET}"; then
  echo "  Dataset ${BQ_DATASET} already exists — skipping."
else
  bq mk --dataset \
    --location="${GOOGLE_CLOUD_LOCATION}" \
    "${GOOGLE_CLOUD_PROJECT}:${BQ_DATASET}"
  echo "  Created dataset ${BQ_DATASET}"
fi

echo ""
echo "=== Setup complete ==="
echo "Run the pipeline:"
echo "  GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT} GCS_BUCKET=${GCS_BUCKET} python main.py"
echo ""
echo "After the pipeline completes, verify the load:"
echo "  bq head -n 5 ${GOOGLE_CLOUD_PROJECT}:${BQ_DATASET}.beam_sales"
