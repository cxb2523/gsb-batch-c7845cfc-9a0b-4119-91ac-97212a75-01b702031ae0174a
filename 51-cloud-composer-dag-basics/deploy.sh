#!/usr/bin/env bash
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com
#
# deploy.sh — Create Cloud Composer environment and upload DAGs
# Usage: chmod +x deploy.sh && ./deploy.sh
set -euo pipefail

# ---------------------------------------------------------------------------
# Config — override with env vars or edit defaults here
# ---------------------------------------------------------------------------
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export COMPOSER_ENV="${COMPOSER_ENV:-your-composer-env}"
export COMPOSER_ENV_SIZE="${COMPOSER_ENV_SIZE:-small}"
export AIRFLOW_VERSION="${AIRFLOW_VERSION:-2.7.3}"
export PYTHON_VERSION="${PYTHON_VERSION:-3.11}"

echo "============================================================"
echo " Cloud Composer DAG Basics — Deploy"
echo "============================================================"
echo " Project  : $GOOGLE_CLOUD_PROJECT"
echo " Location : $GOOGLE_CLOUD_LOCATION"
echo " Env name : $COMPOSER_ENV"
echo "============================================================"

# ---------------------------------------------------------------------------
# Step 1: Set project
# ---------------------------------------------------------------------------
gcloud config set project "$GOOGLE_CLOUD_PROJECT"

# ---------------------------------------------------------------------------
# Step 2: Enable required APIs
# ---------------------------------------------------------------------------
echo ""
echo "[1/5] Enabling required GCP APIs..."
gcloud services enable \
    composer.googleapis.com \
    storage.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com

# ---------------------------------------------------------------------------
# Step 3: Create Composer 2 environment (takes 15–25 minutes)
# ---------------------------------------------------------------------------
echo ""
echo "[2/5] Creating Cloud Composer 2 environment: $COMPOSER_ENV"
echo "      This typically takes 15–25 minutes. Please wait..."

gcloud composer environments create "$COMPOSER_ENV" \
    --location "$GOOGLE_CLOUD_LOCATION" \
    --image-version "composer-2-airflow-${AIRFLOW_VERSION}" \
    --environment-size "$COMPOSER_ENV_SIZE" \
    --async

echo ""
echo "      Environment creation started in background (--async)."
echo "      Monitor progress:"
echo "      gcloud composer environments describe $COMPOSER_ENV --location $GOOGLE_CLOUD_LOCATION"
echo ""
echo "      Wait for status RUNNING before uploading DAGs."
echo "      To wait synchronously, rerun WITHOUT the --async flag."

# ---------------------------------------------------------------------------
# Step 4: Set Airflow Variables (run after environment is RUNNING)
# ---------------------------------------------------------------------------
echo ""
echo "[3/5] Once the environment is RUNNING, set Airflow Variables:"
echo ""
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    variables -- set project_id $GOOGLE_CLOUD_PROJECT"
echo ""
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    variables -- set gcs_bucket your-gcs-bucket"
echo ""
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    variables -- set bq_dataset your_dataset"
echo ""
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    variables -- set data_quality_level standard"

# ---------------------------------------------------------------------------
# Step 5: Upload DAGs (run after environment is RUNNING)
# ---------------------------------------------------------------------------
echo ""
echo "[4/5] To upload DAGs, first get the Composer GCS bucket:"
echo ""
echo "  export COMPOSER_BUCKET=\$("
echo "    gcloud composer environments describe $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    --format='value(config.dagGcsPrefix)'"
echo "  )"
echo "  # COMPOSER_BUCKET will be: gs://YOUR_AUTO_GENERATED_BUCKET/dags"
echo ""
echo "  gcloud storage cp dags/*.py \$COMPOSER_BUCKET/"
echo "  gcloud storage cp data/sample_input.csv gs://your-gcs-bucket/landing/sample_input.csv"

# ---------------------------------------------------------------------------
# Step 6: Trigger DAGs
# ---------------------------------------------------------------------------
echo ""
echo "[5/5] Trigger DAGs via CLI (after upload):"
echo ""
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    dags trigger -- basics_dag"
echo ""
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    dags trigger -- branching_dag"
echo ""
echo "  gcloud composer environments run $COMPOSER_ENV \\"
echo "    --location $GOOGLE_CLOUD_LOCATION \\"
echo "    dags trigger -- taskgroup_dag"

echo ""
echo "============================================================"
echo " Setup complete."
echo " Visit the Airflow UI:"
echo " gcloud composer environments describe $COMPOSER_ENV \\"
echo "   --location $GOOGLE_CLOUD_LOCATION \\"
echo "   --format='value(config.airflowUri)'"
echo "============================================================"
