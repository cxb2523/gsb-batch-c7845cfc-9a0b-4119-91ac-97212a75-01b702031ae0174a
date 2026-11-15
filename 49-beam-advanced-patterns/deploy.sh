#!/usr/bin/env bash
set -euo pipefail

export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-your-project-id}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

# DirectRunner requires NO GCP setup — runs entirely locally.
# This script documents the DataflowRunner path for production use.

echo "=== Project 49: Beam Advanced Patterns ==="
echo "DirectRunner: no GCP project or credentials required."
echo "Run: python main.py"
echo ""
echo "=== Optional: DataflowRunner setup ==="
echo "If you want to run on managed Dataflow (streaming windowing example):"
echo ""

gcloud config set project "$GOOGLE_CLOUD_PROJECT"

gcloud services enable \
  dataflow.googleapis.com \
  bigquery.googleapis.com \
  storage.googleapis.com \
  pubsub.googleapis.com

echo ""
echo "For streaming windowing (Pub/Sub source):"
echo "  1. Create a Pub/Sub topic: gcloud pubsub topics create events"
echo "  2. Replace beam.Create in windowing.py with:"
echo "       beam.io.ReadFromPubSub(subscription=..., timestamp_attribute='event_time')"
echo "  3. Add streaming=True to PipelineOptions"
echo "  4. Run with DataflowRunner"
echo ""
echo "WARNING: A streaming Dataflow job runs 24/7 — minimum ~1 worker VM billed"
echo "         continuously even at zero traffic. Cancel it explicitly when done:"
echo "  gcloud dataflow jobs cancel <JOB_ID> --region=${GOOGLE_CLOUD_LOCATION}"
echo ""
echo "Setup complete. Run: python main.py"
