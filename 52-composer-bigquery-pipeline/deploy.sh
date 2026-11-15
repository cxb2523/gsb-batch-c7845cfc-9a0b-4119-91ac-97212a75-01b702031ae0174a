#!/usr/bin/env bash
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com
#
# deploy.sh — Set up GCS bucket, BigQuery dataset, Composer env, upload DAGs
# Usage: chmod +x deploy.sh && ./deploy.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export COMPOSER_ENV="${COMPOSER_ENV:-your-composer-env}"
export GCS_BUCKET="${GCS_BUCKET:-your-gcs-bucket}"
export BQ_DATASET="${BQ_DATASET:-your_dataset}"
export BQ_LOCATION="${BQ_LOCATION:-US}"

echo "============================================================"
echo " Composer BigQuery Pipeline — Deploy"
echo "============================================================"
echo " Project    : $GOOGLE_CLOUD_PROJECT"
echo " Location   : $GOOGLE_CLOUD_LOCATION"
echo " Composer   : $COMPOSER_ENV"
echo " GCS bucket : $GCS_BUCKET"
echo " BQ dataset : $BQ_DATASET"
echo "============================================================"

# ---------------------------------------------------------------------------
# Step 1: Set project
# ---------------------------------------------------------------------------
gcloud config set project "$GOOGLE_CLOUD_PROJECT"

# ---------------------------------------------------------------------------
# Step 2: Enable required APIs
# ---------------------------------------------------------------------------
echo ""
echo "[1/6] Enabling required APIs..."
gcloud services enable \
    composer.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    logging.googleapis.com

# ---------------------------------------------------------------------------
# Step 3: Create GCS bucket for data landing and results
# ---------------------------------------------------------------------------
echo ""
echo "[2/6] Creating GCS bucket: gs://$GCS_BUCKET"
gcloud storage buckets create "gs://$GCS_BUCKET" \
    --location="$GOOGLE_CLOUD_LOCATION" \
    --uniform-bucket-level-access 2>/dev/null || echo "Bucket already exists."

# Create GCS folder structure
echo "Creating GCS folder structure..."
echo "placeholder" | gcloud storage cp - "gs://$GCS_BUCKET/landing/sales/.keep"
echo "placeholder" | gcloud storage cp - "gs://$GCS_BUCKET/results/sales_summary/.keep"
echo "placeholder" | gcloud storage cp - "gs://$GCS_BUCKET/exports/.keep"

# ---------------------------------------------------------------------------
# Step 4: Upload sample data
# ---------------------------------------------------------------------------
echo ""
echo "[3/6] Uploading sample sales data..."
TODAY=$(date +%Y-%m-%d)
gcloud storage cp data/sales_data.csv \
    "gs://$GCS_BUCKET/landing/sales/$TODAY/sales_data.csv"
echo "Uploaded: gs://$GCS_BUCKET/landing/sales/$TODAY/sales_data.csv"

# ---------------------------------------------------------------------------
# Step 5: Create BigQuery dataset
# ---------------------------------------------------------------------------
echo ""
echo "[4/6] Creating BigQuery dataset: $BQ_DATASET (location: $BQ_LOCATION)..."
bq --location="$BQ_LOCATION" mk \
    --dataset \
    --description "TechTrapture Composer BigQuery Pipeline demo dataset" \
    "$GOOGLE_CLOUD_PROJECT:$BQ_DATASET" 2>/dev/null || echo "Dataset already exists."

# ---------------------------------------------------------------------------
# Step 6: Create Composer environment
# ---------------------------------------------------------------------------
echo ""
echo "[5/6] Creating Cloud Composer 2 environment: $COMPOSER_ENV"
echo "      This takes 15–25 minutes..."
gcloud composer environments create "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    --image-version "composer-2-airflow-2.7.3" \
    --environment-size "small" \
    --async

echo ""
echo "      Monitor progress:"
echo "      gcloud composer environments describe $COMPOSER_ENV --location $GOOGLE_CLOUD_LOCATION"

# ---------------------------------------------------------------------------
# Step 7: Post-creation — set Variables and upload DAGs
# ---------------------------------------------------------------------------
echo ""
echo "[6/6] After the environment reaches RUNNING status, run:"
echo ""
echo "  # Set Airflow Variables"
echo "  for VAR_KV in \\"
echo "    'project_id=$GOOGLE_CLOUD_PROJECT' \\"
echo "    'gcs_bucket=$GCS_BUCKET' \\"
echo "    'bq_dataset=$BQ_DATASET' \\"
echo "    'bq_location=$BQ_LOCATION'; do"
echo "    KEY=\${VAR_KV%%=*}"
echo "    VAL=\${VAR_KV##*=}"
echo "    gcloud composer environments run $COMPOSER_ENV \\"
echo "      --location $GOOGLE_CLOUD_LOCATION \\"
echo "      variables -- set \"\$KEY\" \"\$VAL\""
echo "  done"
echo ""
echo "  # Get Composer GCS bucket"
echo "  export COMPOSER_BUCKET=\$(gcloud composer environments describe $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION --format='value(config.dagGcsPrefix)')"
echo ""
echo "  # Upload DAGs + SQL files"
echo "  gcloud storage cp dags/*.py \"\$COMPOSER_BUCKET/\""
echo "  gcloud storage cp sql/*.sql gs://\$GCS_BUCKET/sql/"

echo ""
echo "============================================================"
echo " Setup complete. Airflow UI:"
echo "  gcloud composer environments describe $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    --format='value(config.airflowUri)'"
echo "============================================================"
