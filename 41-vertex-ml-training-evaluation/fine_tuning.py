# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Supervised Fine-Tuning of Gemini 2.5 Flash.

Uploads a JSONL training set to GCS, then launches a supervised fine-tuning
job via the Gen AI SDK. Use when prompt engineering plateaus and you need
consistent style/format/domain behavior.

COST WARNING: Tuning jobs are billed per training token x epochs and can cost
tens of dollars even on small datasets. DRY_RUN=True (default) prevents any
billable tuning job from being submitted.
"""

import json
import os

from google.cloud import storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"

GCS_BUCKET = os.environ.get("GOOGLE_CLOUD_BUCKET", "your-gcs-bucket")
GCS_BLOB = "samples-tmp/tuning/train_demo.jsonl"
BASE_MODEL = "gemini-2.5-flash"
TUNED_DISPLAY_NAME = "gcp-hub-flash-sft"

# Tiny demo dataset: teach a terse "GCP one-liner" answer style.
TRAIN_EXAMPLES = [
    ("What is Cloud Run?", "Serverless containers. Deploy, scale to zero, pay per request."),
    ("What is BigQuery?", "Serverless data warehouse. SQL over petabytes, pay per scan."),
    ("What is Pub/Sub?", "Global message bus. Decouple producers and consumers, at-least-once."),
    ("What is GKE?", "Managed Kubernetes. Google runs the control plane, you run pods."),
    ("What is Vertex AI?", "Unified ML platform. Train, tune, deploy, and monitor models."),
]


def write_jsonl_to_gcs():
    """Write demo JSONL training file to GCS and return its URI."""
    lines = []
    for question, answer in TRAIN_EXAMPLES:
        lines.append(json.dumps({
            "contents": [
                {"role": "user", "parts": [{"text": question}]},
                {"role": "model", "parts": [{"text": answer}]},
            ]
        }))
    payload = "\n".join(lines)

    bucket = storage.Client(project=PROJECT_ID).bucket(GCS_BUCKET)
    bucket.blob(GCS_BLOB).upload_from_string(payload, content_type="application/jsonl")
    uri = f"gs://{GCS_BUCKET}/{GCS_BLOB}"
    print(f"[fine_tuning] Training JSONL uploaded: {uri} ({len(TRAIN_EXAMPLES)} examples)")
    return uri


def run_fine_tuning():
    """Upload training data and optionally submit a supervised fine-tuning job."""
    print(f"[fine_tuning] Project: {PROJECT_ID} | Location: {LOCATION} | DRY_RUN: {DRY_RUN}")
    print("-" * 78)

    train_uri = write_jsonl_to_gcs()

    if DRY_RUN:
        print()
        print("[fine_tuning] DRY RUN — no tuning job submitted. The real run would call:")
        print()
        print("   from google import genai")
        print("   from google.genai import types")
        print("   client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)")
        print("   client.tunings.tune(")
        print(f"       base_model='{BASE_MODEL}',")
        print(f"       training_dataset=types.TuningDataset(gcs_uri='{train_uri}'),")
        print("       config=types.CreateTuningJobConfig(")
        print(f"           tuned_model_display_name='{TUNED_DISPLAY_NAME}',")
        print("           epoch_count=3))")
        print()
        print("*** COST WARNING ***: SFT is billed per training token x epochs.")
        print("Even small jobs cost real money; large corpora can cost $100s.")
        print("Enable real tuning: DRY_RUN=false python main.py")
        return None

    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

    print(f"[fine_tuning] Submitting supervised tuning job on {BASE_MODEL}...")
    tuning_job = client.tunings.tune(
        base_model=BASE_MODEL,
        training_dataset=types.TuningDataset(gcs_uri=train_uri),
        config=types.CreateTuningJobConfig(
            tuned_model_display_name=TUNED_DISPLAY_NAME,
            epoch_count=3,
        ),
    )
    print(f"[fine_tuning] Job name: {tuning_job.name}")
    print(f"[fine_tuning] State   : {tuning_job.state}")

    print()
    print("Monitor progress:")
    print(f"   client.tunings.get(name='{tuning_job.name}').state")
    print("   Console: Vertex AI -> Tuning -> select the job (loss curves).")
    print()
    print("Use the tuned model once JOB_STATE_SUCCEEDED:")
    print("   endpoint = client.tunings.get(name=job.name).tuned_model.endpoint")
    print("   client.models.generate_content(model=endpoint, contents='...')")
    print()
    print("Cleanup: delete the tuned model endpoint in Vertex AI -> Online")
    print("prediction if you no longer need it (deployed endpoints bill hourly).")

    return tuning_job
