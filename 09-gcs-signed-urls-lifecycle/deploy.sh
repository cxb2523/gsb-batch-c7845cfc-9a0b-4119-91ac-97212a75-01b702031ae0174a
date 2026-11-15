#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GCS_BUCKET="${GCS_BUCKET:-your-gcs-bucket}"
export SIGNER_SA="${SIGNER_SA:-${GOOGLE_CLOUD_PROJECT}@appspot.gserviceaccount.com}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable \
  storage.googleapis.com \
  iam.googleapis.com

echo ""
echo "============================================================"
echo "  Signed URL Auth Setup"
echo "============================================================"
echo ""
echo "Signed URLs require a service account that can sign bytes."
echo "Two options:"
echo ""
echo "Option A — Impersonation (recommended, no key file):"
echo "  Grant your user the token creator role on the signer SA:"
echo ""
echo "  gcloud iam service-accounts add-iam-policy-binding ${SIGNER_SA} \\"
echo "    --member=\"user:\$(gcloud config get-value account)\" \\"
echo "    --role=\"roles/iam.serviceAccountTokenCreator\""
echo ""
echo "  Then export:"
echo "  export SIGNER_SA=${SIGNER_SA}"
echo ""
echo "Option B — Service account key file (simpler for local dev):"
echo "  1) Create / download a key for any SA:"
echo "       gcloud iam service-accounts keys create key.json \\"
echo "         --iam-account=${SIGNER_SA}"
echo "  2) Point ADC at the key:"
echo "       export GOOGLE_APPLICATION_CREDENTIALS=\$(pwd)/key.json"
echo "  WARNING: never commit key.json to source control."
echo ""
echo "============================================================"
echo "Setup complete. Run: python main.py"
echo "============================================================"
