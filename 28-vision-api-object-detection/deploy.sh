#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required APIs..."
gcloud services enable aiplatform.googleapis.com \
                       ml.googleapis.com \
                       vision.googleapis.com

echo ""
echo "Setup complete."
echo "  Project : $GOOGLE_CLOUD_PROJECT"
echo "  Location: $GOOGLE_CLOUD_LOCATION"
echo ""
echo "IMPORTANT: Cloud Vision API (vision.googleapis.com) has been enabled."
echo "           Label detection, landmark detection, and OCR (Steps 2 & 3)"
echo "           require this API. The object detection step (Step 1) uses"
echo "           Gemini and does NOT require the Vision API."
echo ""
echo "Run:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
