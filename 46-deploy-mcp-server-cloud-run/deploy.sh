#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

SERVICE="techtrapture-mcp-server"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable \
  run.googleapis.com \
  bigquery.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com

# Generate deployment artifacts (server.py + Dockerfile + requirements.txt)
echo "Generating deployment artifacts..."
python main.py 2>/dev/null || python cloud_run_deployer.py

echo ""
echo "=== Deploy the MCP server to Cloud Run ==="
echo "Run the following command to deploy (source-based build, private service):"
echo ""
echo "  gcloud run deploy ${SERVICE} \\"
echo "    --source ./mcp_server_deploy \\"
echo "    --project ${GOOGLE_CLOUD_PROJECT} \\"
echo "    --region ${GOOGLE_CLOUD_LOCATION} \\"
echo "    --no-allow-unauthenticated \\"
echo "    --set-env-vars GOOGLE_CLOUD_PROJECT=${GOOGLE_CLOUD_PROJECT}"
echo ""
echo "=== Grant Cloud Run SA access to BigQuery ==="
echo "  PROJECT_NUMBER=\$(gcloud projects describe ${GOOGLE_CLOUD_PROJECT} --format='value(projectNumber)')"
echo "  gcloud projects add-iam-policy-binding ${GOOGLE_CLOUD_PROJECT} \\"
echo "    --member=\"serviceAccount:\${PROJECT_NUMBER}-compute@developer.gserviceaccount.com\" \\"
echo "    --role=roles/bigquery.user"
echo ""
echo "=== Grant a caller invoke rights ==="
echo "  gcloud run services add-iam-policy-binding ${SERVICE} \\"
echo "    --region ${GOOGLE_CLOUD_LOCATION} \\"
echo "    --member=user:you@example.com \\"
echo "    --role=roles/run.invoker"
echo ""
echo "Setup complete. Artifacts in ./mcp_server_deploy/"
echo "Run: python main.py"
