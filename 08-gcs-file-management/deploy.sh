#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs ..."
gcloud services enable \
  storage.googleapis.com

echo ""
echo "============================================================"
echo "Setup complete. Run: python main.py"
echo "The script creates and tears down its own bucket automatically."
echo "============================================================"
