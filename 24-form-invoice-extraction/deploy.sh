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
echo "  1. Create processor IDs in the GCP Console:"
echo "     Document AI > Create Processor > Form Parser"
echo "     Document AI > Create Processor > Invoice Parser"
echo ""
echo "  2. Export the processor IDs:"
echo "     export DOCAI_FORM_PROCESSOR_ID=your-form-parser-id"
echo "     export DOCAI_INVOICE_PROCESSOR_ID=your-invoice-parser-id"
echo ""
echo "  3. Install dependencies and run:"
echo "     python3 -m venv .venv && source .venv/bin/activate"
echo "     pip install -r requirements.txt"
echo "     python main.py"
echo ""
echo "  # Run individual parsers:"
echo "     python main.py --mode form"
echo "     python main.py --mode invoice"
