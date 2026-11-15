#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  cloudbilling.googleapis.com \
  bigquery.googleapis.com \
  recommender.googleapis.com

echo ""
echo "Required IAM roles for the running principal:"
echo "  roles/billing.viewer      (ON THE BILLING ACCOUNT, not the project)"
echo "  roles/recommender.viewer  (on the project — covers all compute recommenders)"
echo "  roles/bigquery.dataViewer (on the billing export dataset, for cost queries)"
echo ""
echo "Grant billing viewer on the billing account:"
echo "  BILLING_ACCOUNT=\$(gcloud beta billing projects describe \$GOOGLE_CLOUD_PROJECT \\"
echo "    --format='value(billingAccountName)')"
echo "  gcloud beta billing accounts add-iam-policy-binding \"\$BILLING_ACCOUNT\" \\"
echo "    --member='user:YOU@example.com' --role='roles/billing.viewer'"
echo ""
echo "Grant recommender viewer on the project:"
echo "  gcloud projects add-iam-policy-binding \$GOOGLE_CLOUD_PROJECT \\"
echo "    --member='user:YOU@example.com' --role='roles/recommender.viewer'"
echo ""
echo "--- BILLING EXPORT SETUP (one-time, #1 FinOps prerequisite) ---"
echo "  1. Go to: https://console.cloud.google.com/billing -> Billing export"
echo "  2. Enable 'Detailed usage cost' (not just Standard) into a BQ dataset."
echo "     Detailed = resource-level rows; Standard = service-level only."
echo "  3. Export is NOT retroactive — enable on day one of every org."
echo "  4. Once active, set the env var:"
echo "     export BILLING_EXPORT_TABLE=your-project-id.dataset.gcp_billing_export_resource_v1_XXXXXX"
echo ""
echo "Setup complete. Run: python main.py"
