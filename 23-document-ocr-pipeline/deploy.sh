#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling Document AI API..."
gcloud services enable documentai.googleapis.com

echo ""
echo "Setup complete. Document AI API is now enabled."
echo ""
echo "Next steps:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo ""
echo "  # Run with the public sample PDF (auto-downloaded):"
echo "  python main.py"
echo ""
echo "  # Run with your own PDF:"
echo "  python main.py --pdf data/your-document.pdf"
echo ""
echo "  # Skip processor creation if you already have one:"
echo "  export DOCAI_PROCESSOR_ID=your-processor-id"
echo "  python main.py"
