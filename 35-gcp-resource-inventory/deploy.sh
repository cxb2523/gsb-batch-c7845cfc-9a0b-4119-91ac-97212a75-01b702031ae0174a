#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required GCP APIs..."
gcloud services enable \
  cloudresourcemanager.googleapis.com \
  cloudasset.googleapis.com

echo ""
echo "IAM requirements:"
echo "  Project-level inventory : roles/cloudasset.viewer on the project"
echo "  Org-wide inventory      : roles/cloudasset.viewer at the ORG node"
echo "  Project listing         : roles/browser (or roles/viewer) at ORG node"
echo ""
echo "To grant project-level Cloud Asset Inventory viewer:"
echo "  gcloud projects add-iam-policy-binding \$GOOGLE_CLOUD_PROJECT \\"
echo "    --member='user:you@example.com' \\"
echo "    --role='roles/cloudasset.viewer'"
echo ""
echo "Setup complete."
echo ""
echo "Run:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
echo ""
echo "For org-wide search (Step 3), set your org ID:"
echo "  export GCP_ORG_ID=\$(gcloud organizations list --format='value(name)' | head -1 | cut -d/ -f2)"
echo "  python main.py"
