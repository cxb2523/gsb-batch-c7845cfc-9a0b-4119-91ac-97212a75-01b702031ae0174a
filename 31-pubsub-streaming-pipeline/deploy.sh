#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export PUBSUB_TOPIC_ID="${PUBSUB_TOPIC_ID:-gcp-hub-demo-topic}"
export PUBSUB_SUBSCRIPTION_ID="${PUBSUB_SUBSCRIPTION_ID:-gcp-hub-demo-topic-pull}"

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

echo "Enabling required GCP APIs..."
gcloud services enable pubsub.googleapis.com

echo ""
echo "Creating Pub/Sub topic: $PUBSUB_TOPIC_ID"
gcloud pubsub topics create "$PUBSUB_TOPIC_ID" \
  --project="$GOOGLE_CLOUD_PROJECT" 2>/dev/null || echo "  Topic already exists."

echo ""
echo "Creating Pub/Sub pull subscription: $PUBSUB_SUBSCRIPTION_ID"
gcloud pubsub subscriptions create "$PUBSUB_SUBSCRIPTION_ID" \
  --topic="$PUBSUB_TOPIC_ID" \
  --ack-deadline=30 \
  --project="$GOOGLE_CLOUD_PROJECT" 2>/dev/null || echo "  Subscription already exists."

echo ""
echo "Setup complete."
echo ""
echo "Run:"
echo "  python3 -m venv .venv && source .venv/bin/activate"
echo "  pip install -r requirements.txt"
echo "  python main.py"
