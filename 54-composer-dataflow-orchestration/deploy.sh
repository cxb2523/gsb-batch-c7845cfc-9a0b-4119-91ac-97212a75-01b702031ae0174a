#!/usr/bin/env bash
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com
#
# deploy.sh — Build/push Flex Template to GCR, create GCS template bucket,
#             enable APIs, create Composer env, upload DAGs.
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
export PUBSUB_TOPIC="${PUBSUB_TOPIC:-your-pubsub-topic}"

# Flex Template image name in Artifact Registry
export AR_REPO="${AR_REPO:-dataflow-templates}"
export TEMPLATE_IMAGE="${TEMPLATE_IMAGE:-${GOOGLE_CLOUD_LOCATION}-docker.pkg.dev/${GOOGLE_CLOUD_PROJECT}/${AR_REPO}/sales-etl:latest}"
export TEMPLATE_SPEC_PATH="gs://${GCS_BUCKET}/dataflow/templates/sales-etl-template.json"

# Dataflow worker service account
export DATAFLOW_SA="dataflow-worker@${GOOGLE_CLOUD_PROJECT}.iam.gserviceaccount.com"

echo "============================================================"
echo " Composer Dataflow Orchestration — Deploy"
echo "============================================================"
echo " Project          : $GOOGLE_CLOUD_PROJECT"
echo " Location         : $GOOGLE_CLOUD_LOCATION"
echo " Composer env     : $COMPOSER_ENV"
echo " GCS bucket       : gs://$GCS_BUCKET"
echo " Template image   : $TEMPLATE_IMAGE"
echo " Template spec    : $TEMPLATE_SPEC_PATH"
echo " Dataflow SA      : $DATAFLOW_SA"
echo "============================================================"

# ---------------------------------------------------------------------------
# Step 1: Set project
# ---------------------------------------------------------------------------
gcloud config set project "$GOOGLE_CLOUD_PROJECT"

# ---------------------------------------------------------------------------
# Step 2: Enable required APIs
# ---------------------------------------------------------------------------
echo ""
echo "[1/9] Enabling required APIs..."
gcloud services enable \
    composer.googleapis.com \
    dataflow.googleapis.com \
    bigquery.googleapis.com \
    storage.googleapis.com \
    pubsub.googleapis.com \
    artifactregistry.googleapis.com \
    cloudbuild.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com

# ---------------------------------------------------------------------------
# Step 3: Create Artifact Registry repo for Flex Template container image
# ---------------------------------------------------------------------------
echo ""
echo "[2/9] Creating Artifact Registry repository: $AR_REPO"
gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker \
    --location="$GOOGLE_CLOUD_LOCATION" \
    --description="Dataflow Flex Template container images" \
    2>/dev/null || echo "AR repository already exists."

# ---------------------------------------------------------------------------
# Step 4: Create GCS bucket for Dataflow temp/staging/templates
# ---------------------------------------------------------------------------
echo ""
echo "[3/9] Creating GCS bucket: gs://$GCS_BUCKET"
gcloud storage buckets create "gs://$GCS_BUCKET" \
    --location="$GOOGLE_CLOUD_LOCATION" \
    --uniform-bucket-level-access 2>/dev/null || echo "Bucket already exists."

# GCS folder structure for Dataflow
for PREFIX in \
    "dataflow/temp/" \
    "dataflow/staging/" \
    "dataflow/templates/" \
    "landing/sales/" \
    "exports/"; do
    printf "placeholder" | gcloud storage cp - "gs://$GCS_BUCKET/${PREFIX}.keep"
done
echo "GCS folder structure created."

# ---------------------------------------------------------------------------
# Step 5: Create BigQuery dataset
# ---------------------------------------------------------------------------
echo ""
echo "[4/9] Creating BigQuery dataset: $BQ_DATASET"
bq --location="$BQ_LOCATION" mk \
    --dataset \
    --description "TechTrapture Dataflow Orchestration demo dataset" \
    "$GOOGLE_CLOUD_PROJECT:$BQ_DATASET" 2>/dev/null || echo "Dataset already exists."

# ---------------------------------------------------------------------------
# Step 6: Create Pub/Sub topic
# ---------------------------------------------------------------------------
echo ""
echo "[5/9] Creating Pub/Sub topic: $PUBSUB_TOPIC"
gcloud pubsub topics create "$PUBSUB_TOPIC" 2>/dev/null || echo "Topic already exists."

# ---------------------------------------------------------------------------
# Step 7: Create Dataflow worker service account and grant permissions
# ---------------------------------------------------------------------------
echo ""
echo "[6/9] Creating Dataflow worker service account: $DATAFLOW_SA"
gcloud iam service-accounts create "dataflow-worker" \
    --display-name="Dataflow Worker Service Account" \
    --description="Used by Dataflow workers for BigQuery/GCS access" \
    2>/dev/null || echo "Service account already exists."

echo "Granting IAM roles to Dataflow SA..."
for ROLE in \
    "roles/dataflow.worker" \
    "roles/bigquery.dataEditor" \
    "roles/bigquery.jobUser" \
    "roles/storage.objectAdmin"; do
    gcloud projects add-iam-policy-binding "$GOOGLE_CLOUD_PROJECT" \
        --member="serviceAccount:$DATAFLOW_SA" \
        --role="$ROLE" \
        --condition=None \
        --quiet
done
echo "IAM roles granted."

# ---------------------------------------------------------------------------
# Step 8: Build and push Flex Template image
#
# NOTE: You need a Dockerfile + main.py (Apache Beam pipeline) in a
#       flex_template/ subdirectory. This script shows the commands.
# ---------------------------------------------------------------------------
echo ""
echo "[7/9] Building Flex Template container image..."
echo ""
echo "  # Build and push the container image:"
echo "  docker build -t $TEMPLATE_IMAGE flex_template/"
echo "  docker push $TEMPLATE_IMAGE"
echo ""
echo "  # OR use Cloud Build (no local Docker required):"
echo "  gcloud builds submit flex_template/ --tag $TEMPLATE_IMAGE"
echo ""
echo "  # After the image is pushed, build the Flex Template spec JSON:"
echo "  gcloud dataflow flex-template build \"$TEMPLATE_SPEC_PATH\" \\"
echo "    --image \"$TEMPLATE_IMAGE\" \\"
echo "    --sdk-language PYTHON \\"
echo "    --metadata-file flex_template/metadata.json"
echo ""
echo "  NOTE: Replace the commands above with your actual Beam pipeline code."
echo "  See https://cloud.google.com/dataflow/docs/guides/templates/using-flex-templates"

# ---------------------------------------------------------------------------
# Step 9: Create Composer 2 environment
# ---------------------------------------------------------------------------
echo ""
echo "[8/9] Creating Cloud Composer 2 environment: $COMPOSER_ENV"
echo "      This takes 15–25 minutes..."
gcloud composer environments create "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    --image-version "composer-2-airflow-2.7.3" \
    --environment-size "small" \
    --service-account "$DATAFLOW_SA" \
    --async

echo ""
echo "      Monitor: gcloud composer environments describe $COMPOSER_ENV --location $GOOGLE_CLOUD_LOCATION"

# ---------------------------------------------------------------------------
# Step 10: Post-creation instructions
# ---------------------------------------------------------------------------
echo ""
echo "[9/9] After environment reaches RUNNING status:"
echo ""
echo "  # Set Airflow Variables"
echo "  VARS=("
echo "    'project_id=$GOOGLE_CLOUD_PROJECT'"
echo "    'gcp_region=$GOOGLE_CLOUD_LOCATION'"
echo "    'gcs_bucket=$GCS_BUCKET'"
echo "    'bq_dataset=$BQ_DATASET'"
echo "    'bq_location=$BQ_LOCATION'"
echo "    'pubsub_topic=$PUBSUB_TOPIC'"
echo "    'dataflow_flex_template_path=$TEMPLATE_SPEC_PATH'"
echo "    'dataflow_dry_run=false'"
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
echo "  # Run DRY_RUN first to validate config"
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    variables -- set dataflow_dry_run true"
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    dags trigger -- dataflow_orchestration_dag"

echo ""
echo "============================================================"
echo " Setup complete."
echo " Airflow UI:"
echo "  gcloud composer environments describe $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    --format='value(config.airflowUri)'"
echo "============================================================"
