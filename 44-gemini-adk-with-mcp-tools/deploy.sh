#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

echo "Project: $GOOGLE_CLOUD_PROJECT"
echo ""

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required GCP APIs..."
gcloud services enable \
  aiplatform.googleapis.com \
  developerknowledge.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"

echo ""
echo "Enabling Developer Knowledge MCP surface (BOTH commands required)..."
echo "NOTE: 200 on initialize + 403 on tool calls = this step was skipped."
gcloud beta services mcp enable developerknowledge.googleapis.com \
  --project="$GOOGLE_CLOUD_PROJECT"

echo ""
echo "Authenticating Application Default Credentials..."
gcloud auth application-default login --no-launch-browser || true

echo ""
echo "Setup complete."
echo ""
echo "Run the demo:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
echo ""
echo "Optional — run the ADK agent in the dev UI:"
echo "  adk web   (requires an agent package with root_agent = build_agent())"
