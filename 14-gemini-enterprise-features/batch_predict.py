# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Batch prediction — offline Gemini inference at 50% cost discount.

Pattern: write a JSONL of requests to GCS → create a batch job → poll briefly
to show lifecycle → results land asynchronously in GCS JSONL.

Use for: large offline workloads where real-time latency doesn't matter —
backfills, bulk enrichment, evaluation pipelines, scheduled nightly jobs.

Trade-offs vs online:
- 50% cheaper per token.
- No QPM (queries-per-minute) pressure from shared quota.
- Async — jobs can take minutes to hours (SLA: within 24h).
- Not suitable for user-facing, latency-sensitive requests.

Prerequisites:
- Set GOOGLE_CLOUD_PROJECT and GCS_BUCKET (must exist and be writable).
- Service account needs roles/storage.objectAdmin on the bucket.
"""

import os
import json
import time

from google import genai
from google.genai import types
from google.cloud import storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
MODEL = "gemini-2.5-flash"

# Update to your own writable GCS bucket before running.
BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")
INPUT_BLOB = "batch-samples/batch_input.jsonl"
INPUT_URI = f"gs://{BUCKET}/{INPUT_BLOB}"
OUTPUT_URI = f"gs://{BUCKET}/batch-samples/batch_output/"


def create_client():
    """Create and return a Vertex AI Gemini client."""
    return genai.Client(vertexai=True, project=PROJECT_ID, location="global")


def write_input_jsonl(bucket_name=BUCKET, blob_name=INPUT_BLOB):
    """
    Writes a JSONL batch input file to GCS.

    Each line is a self-contained generate_content request body under "request".
    The format mirrors the REST API request schema.

    Args:
        bucket_name: GCS bucket name (must exist, must be writable).
        blob_name: Object path within the bucket.

    Returns:
        str: GCS URI of the written file.
    """
    prompts = [
        "Define 'egress cost' in one sentence.",
        "Define 'committed use discount' in one sentence.",
        "Define 'rightsizing' in one sentence.",
    ]
    lines = [
        json.dumps({
            "request": {
                "contents": [{"role": "user", "parts": [{"text": p}]}],
                "generationConfig": {"maxOutputTokens": 256},
            }
        })
        for p in prompts
    ]
    gcs_uri = f"gs://{bucket_name}/{blob_name}"
    storage.Client(project=PROJECT_ID).bucket(bucket_name).blob(blob_name).upload_from_string(
        "\n".join(lines), content_type="application/jsonl"
    )
    print(f"Wrote {len(lines)} requests to {gcs_uri}")
    return gcs_uri


def submit_batch_job(client, input_uri=INPUT_URI, output_uri=OUTPUT_URI):
    """
    Submits a Gemini batch prediction job.

    The job is asynchronous — this call returns immediately with a job
    object in a pending/running state. Results appear in output_uri when done.

    Args:
        client: genai.Client instance.
        input_uri: GCS URI of the JSONL input file.
        output_uri: GCS URI prefix for output files.

    Returns:
        The batch job object (contains .name and .state).
    """
    job = client.batches.create(
        model=MODEL,
        src=input_uri,
        config=types.CreateBatchJobConfig(dest=output_uri),
    )
    return job


def poll_batch_job(client, job_name, polls=6, interval_seconds=10):
    """
    Polls a batch job for up to polls * interval_seconds seconds.

    Batch jobs typically complete well after this demo poll window —
    this is intentionally short just to show the lifecycle states.

    Args:
        client: genai.Client instance.
        job_name: Job name from submit_batch_job().name.
        polls: Number of poll iterations.
        interval_seconds: Seconds between polls.

    Returns:
        The final job object after polling.
    """
    terminal_states = {
        types.JobState.JOB_STATE_SUCCEEDED,
        types.JobState.JOB_STATE_FAILED,
        types.JobState.JOB_STATE_CANCELLED,
    }
    job = None
    for _ in range(polls):
        time.sleep(interval_seconds)
        job = client.batches.get(name=job_name)
        print(f"  state = {job.state}")
        if job.state in terminal_states:
            break
    return job


def run_batch_predict():
    """Entry point for the batch prediction demo."""
    input_uri = write_input_jsonl()

    client = create_client()

    print(f"\n=== Submitting Batch Job ===")
    job = submit_batch_job(client, input_uri=input_uri, output_uri=OUTPUT_URI)
    print(f"Job name : {job.name}")
    print(f"State    : {job.state}")

    print("\nPolling for ~60s (job will likely still be running)...")
    job = poll_batch_job(client, job.name)

    print("\n=== What Happens Next ===")
    print(f"- Job continues asynchronously; results land in {OUTPUT_URI}")
    print(f"- Check later: client.batches.get(name='{job.name}')")
    print("- Why batch? 50% cheaper than online, no QPM pressure.")
    print("  Ideal for backfills, evals, and bulk enrichment pipelines.")
