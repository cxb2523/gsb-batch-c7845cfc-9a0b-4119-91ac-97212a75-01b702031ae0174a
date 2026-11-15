#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-gcp-hub-mcp-server}"

echo "Project  : $GOOGLE_CLOUD_PROJECT"
echo "Location : $GOOGLE_CLOUD_LOCATION"
echo "Service  : $SERVICE_NAME"
echo ""

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required GCP APIs..."
gcloud services enable \
  bigquery.googleapis.com \
  storage.googleapis.com \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"

echo ""
echo "Authenticating Application Default Credentials..."
gcloud auth application-default login --no-launch-browser || true

echo ""
echo "=== LOCAL RUN ==="
echo "To run the MCP server locally:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
echo ""
echo "In a second terminal, test with:"
echo "  python client_example.py"
echo ""

echo "=== CLOUD RUN DEPLOYMENT ==="
echo "To build and deploy to Cloud Run (requires Docker or gcloud builds):"
echo ""
echo "  # Option 1: gcloud run deploy with source (no Dockerfile needed)"
echo "  gcloud run deploy $SERVICE_NAME \\"
echo "    --source . \\"
echo "    --region=$GOOGLE_CLOUD_LOCATION \\"
echo "    --project=$GOOGLE_CLOUD_PROJECT \\"
echo "    --set-env-vars=GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT \\"
echo "    --allow-unauthenticated \\"
echo "    --port=8080 \\"
echo "    --min-instances=0 \\"
echo "    --max-instances=10"
echo ""
echo "  # Option 2: authenticated (recommended for production)"
echo "  gcloud run deploy $SERVICE_NAME \\"
echo "    --source . \\"
echo "    --region=$GOOGLE_CLOUD_LOCATION \\"
echo "    --project=$GOOGLE_CLOUD_PROJECT \\"
echo "    --set-env-vars=GOOGLE_CLOUD_PROJECT=$GOOGLE_CLOUD_PROJECT \\"
echo "    --no-allow-unauthenticated \\"
echo "    --port=8080"
echo ""
echo "  # After deploy, get the URL:"
echo "  SERVER_URL=\$(gcloud run services describe $SERVICE_NAME --region=$GOOGLE_CLOUD_LOCATION --format='value(status.url)')/mcp"
echo ""
echo "  # Test with the example client:"
echo "  SERVER_URL=\$SERVER_URL python client_example.py"
echo ""
echo "Setup complete."
