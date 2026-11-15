#!/usr/bin/env bash
# Setup script for 55-bq-scd-slowly-changing-dimensions
# Replace placeholder values before running
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export BQ_DATASET="${BQ_DATASET:-your_dataset}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-US}"

echo "Project : $GOOGLE_CLOUD_PROJECT"
echo "Dataset : $BQ_DATASET"
echo "Location: $GOOGLE_CLOUD_LOCATION"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

# Enable BigQuery API
gcloud services enable bigquery.googleapis.com

# Create dataset if it doesn't exist
bq mk --location="$GOOGLE_CLOUD_LOCATION" \
      --dataset \
      --description="SCD demo dataset" \
      "${GOOGLE_CLOUD_PROJECT}:${BQ_DATASET}" 2>/dev/null || echo "Dataset already exists — skipping"

# Create all tables
echo "Creating tables..."
sed "s/your_project/${GOOGLE_CLOUD_PROJECT}/g; s/your_dataset/${BQ_DATASET}/g" sql/01_create_tables.sql \
  | bq query --use_legacy_sql=false --project_id="$GOOGLE_CLOUD_PROJECT"

# Load initial customer data into staging
echo "Loading initial data into staging..."
bq load \
  --source_format=CSV \
  --skip_leading_rows=1 \
  --autodetect \
  "${BQ_DATASET}.customers_staging" \
  data/customers_initial.csv

# Add load_timestamp (bq load doesn't support computed columns — patch via SQL)
bq query --use_legacy_sql=false --project_id="$GOOGLE_CLOUD_PROJECT" \
  "UPDATE \`${GOOGLE_CLOUD_PROJECT}.${BQ_DATASET}.customers_staging\`
   SET load_timestamp = TIMESTAMP('2024-01-01 00:00:00 UTC')
   WHERE load_timestamp IS NULL"

echo ""
echo "Setup complete. Run the SQLs in this order:"
echo "  1. bq query < sql/02_scd1_merge.sql"
echo "  2. bq query < sql/03_scd2_merge.sql"
echo "  3. bq query < sql/04_scd3_merge.sql"
echo "  Then reload data/customers_updates.csv and re-run to see history."
