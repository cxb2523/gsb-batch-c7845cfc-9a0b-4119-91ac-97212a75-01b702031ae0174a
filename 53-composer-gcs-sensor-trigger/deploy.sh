#!/usr/bin/env bash
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com
#
# deploy.sh — Create GCS buckets, Pub/Sub topic, Composer env, upload DAGs
# Usage: chmod +x deploy.sh && ./deploy.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export COMPOSER_ENV="${COMPOSER_ENV:-your-composer-env}"
export LANDING_BUCKET="${LANDING_BUCKET:-your-gcs-bucket}"
export ARCHIVE_BUCKET="${ARCHIVE_BUCKET:-your-gcs-bucket}"    # can be same bucket, different prefix
export OUTPUT_BUCKET="${OUTPUT_BUCKET:-your-gcs-bucket}"
export PUBSUB_TOPIC="${PUBSUB_TOPIC:-your-pubsub-topic}"

echo "============================================================"
echo " Composer GCS Sensor Trigger — Deploy"
echo "============================================================"
echo " Project       : $GOOGLE_CLOUD_PROJECT"
echo " Location      : $GOOGLE_CLOUD_LOCATION"
echo " Composer env  : $COMPOSER_ENV"
echo " Landing bucket: gs://$LANDING_BUCKET"
echo " Pub/Sub topic : $PUBSUB_TOPIC"
echo "============================================================"

# ---------------------------------------------------------------------------
# Step 1: Set project
# ---------------------------------------------------------------------------
gcloud config set project "$GOOGLE_CLOUD_PROJECT"

# ---------------------------------------------------------------------------
# Step 2: Enable APIs
# ---------------------------------------------------------------------------
echo ""
echo "[1/6] Enabling required APIs..."
gcloud services enable \
    composer.googleapis.com \
    storage.googleapis.com \
    pubsub.googleapis.com \
    logging.googleapis.com

# ---------------------------------------------------------------------------
# Step 3: Create GCS bucket (landing, archive, and output can share one bucket)
# ---------------------------------------------------------------------------
echo ""
echo "[2/6] Creating GCS bucket: gs://$LANDING_BUCKET"
gcloud storage buckets create "gs://$LANDING_BUCKET" \
    --location="$GOOGLE_CLOUD_LOCATION" \
    --uniform-bucket-level-access 2>/dev/null || echo "Bucket already exists."

# Create logical folder structure using placeholder objects
echo "Creating GCS folder structure..."
for PREFIX in "landing/events/" "archive/processed/" "processed/" "exports/"; do
    printf "placeholder" | gcloud storage cp - "gs://$LANDING_BUCKET/${PREFIX}.keep"
done
echo "GCS folder structure created."

# ---------------------------------------------------------------------------
# Step 4: Create Pub/Sub topic for completion notifications
# ---------------------------------------------------------------------------
echo ""
echo "[3/6] Creating Pub/Sub topic: $PUBSUB_TOPIC"
gcloud pubsub topics create "$PUBSUB_TOPIC" 2>/dev/null || echo "Topic already exists."

# Create a pull subscription for testing
SUBSCRIPTION="${PUBSUB_TOPIC}-sub"
gcloud pubsub subscriptions create "$SUBSCRIPTION" \
    --topic="$PUBSUB_TOPIC" \
    --ack-deadline=60 2>/dev/null || echo "Subscription already exists."
echo "Pub/Sub subscription created: $SUBSCRIPTION"

# ---------------------------------------------------------------------------
# Step 5: Upload sample payload to trigger the pipeline
# ---------------------------------------------------------------------------
echo ""
echo "[4/6] Uploading sample payload to GCS landing prefix..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
gcloud storage cp data/sample_payload.json \
    "gs://$LANDING_BUCKET/landing/events/sales_${TIMESTAMP}.json"
echo "Uploaded: gs://$LANDING_BUCKET/landing/events/sales_${TIMESTAMP}.json"
echo "This file will trigger the GCS sensor on the next poll cycle."

# ---------------------------------------------------------------------------
# Step 6: Create Composer 2 environment
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
echo "      Monitor: gcloud composer environments describe $COMPOSER_ENV --location $GOOGLE_CLOUD_LOCATION"

# ---------------------------------------------------------------------------
# Step 7: Post-creation instructions
# ---------------------------------------------------------------------------
echo ""
echo "[6/6] After environment reaches RUNNING status:"
echo ""
echo "  # Set Airflow Variables"
echo "  VARS=("
echo "    'project_id=$GOOGLE_CLOUD_PROJECT'"
echo "    'landing_bucket=$LANDING_BUCKET'"
echo "    'archive_bucket=$ARCHIVE_BUCKET'"
echo "    'output_bucket=$OUTPUT_BUCKET'"
echo "    'pubsub_topic=$PUBSUB_TOPIC'"
echo "    'gcs_watch_prefix=landing/events/'"
echo "  )"
echo "  for KV in \"\${VARS[@]}\"; do"
echo "    KEY=\${KV%%=*}; VAL=\${KV##*=}"
echo "    gcloud composer environments run $COMPOSER_ENV \\"
echo "      --location $GOOGLE_CLOUD_LOCATION \\"
echo "      variables -- set \"\$KEY\" \"\$VAL\""
echo "  done"
echo ""
echo "  # Upload DAGs"
echo "  export COMPOSER_BUCKET=\$(gcloud composer environments describe $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION --format='value(config.dagGcsPrefix)')"
echo "  gcloud storage cp dags/*.py \"\$COMPOSER_BUCKET/\""
echo ""
echo "  # To retrigger: drop another file in the landing prefix:"
echo "  TIMESTAMP=\$(date +%Y%m%d_%H%M%S)"
echo "  gcloud storage cp data/sample_payload.json \\"
echo "    gs://$LANDING_BUCKET/landing/events/sales_\${TIMESTAMP}.json"
echo ""
echo "  # Monitor Pub/Sub notifications:"
echo "  gcloud pubsub subscriptions pull $SUBSCRIPTION --auto-ack"

echo ""
echo "============================================================"
echo " Setup complete."
echo "============================================================"
