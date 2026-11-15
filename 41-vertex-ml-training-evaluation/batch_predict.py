# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Gemini Batch Prediction with BigQuery Input/Output.

Creates a BigQuery input table of prompts, submits a Gemini batch prediction
job via the Gen AI SDK with bq:// source and destination, and polls briefly.
Batch jobs run at a ~50% discount vs online requests — ideal for offline scoring.
"""

import json
import os
import time

from google import genai
from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

DATASET_ID = "your_dataset"
INPUT_TABLE = f"{PROJECT_ID}.{DATASET_ID}.gemini_batch_input"
OUTPUT_TABLE = f"{PROJECT_ID}.{DATASET_ID}.gemini_batch_output"
MODEL = "gemini-2.5-flash"

PROMPTS = [
    "In one sentence, what is Cloud Run?",
    "In one sentence, what is BigQuery?",
    "In one sentence, what is Vertex AI Vector Search?",
]


def create_input_table(bq_client):
    """Create dataset + input table with a 'request' JSON column (idempotent)."""
    bq_client.create_dataset(
        bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}"), exists_ok=True
    )

    schema = [bigquery.SchemaField("request", "JSON")]
    table = bigquery.Table(INPUT_TABLE, schema=schema)
    bq_client.create_table(table, exists_ok=True)

    rows = [
        {
            "request": json.dumps(
                {"contents": [{"role": "user", "parts": [{"text": p}]}]}
            )
        }
        for p in PROMPTS
    ]
    # Truncate-and-load keeps reruns idempotent.
    job_config = bigquery.LoadJobConfig(
        schema=schema, write_disposition="WRITE_TRUNCATE"
    )
    bq_client.load_table_from_json(rows, INPUT_TABLE, job_config=job_config).result()
    print(f"[batch_predict] Input table ready: {INPUT_TABLE} ({len(rows)} prompts)")
    return INPUT_TABLE


def submit_batch_job(input_table, output_table):
    """Submit Gemini batch prediction job and poll for up to ~2 minutes."""
    client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

    print(f"[batch_predict] Submitting batch job (50% discount vs online inference)...")
    job = client.batches.create(
        model=MODEL,
        src=f"bq://{input_table}",
        config={"dest": f"bq://{output_table}"},
    )
    print(f"[batch_predict] Job name : {job.name}")
    print(f"[batch_predict] State    : {job.state}")

    print("[batch_predict] Polling for up to ~2 minutes (job may finish later)...")
    terminal = {"JOB_STATE_SUCCEEDED", "JOB_STATE_FAILED", "JOB_STATE_CANCELLED"}
    for i in range(8):
        time.sleep(15)
        job = client.batches.get(name=job.name)
        print(f"[batch_predict] [{(i + 1) * 15:>3}s] state = {job.state}")
        if str(job.state).split(".")[-1] in terminal:
            break

    return job


def run_batch_prediction():
    """Full batch prediction flow: create table, submit job, poll."""
    print(f"[batch_predict] Project: {PROJECT_ID} | Location: {LOCATION} | Model: {MODEL}")
    print("-" * 78)

    bq_client = bigquery.Client(project=PROJECT_ID)
    input_table = create_input_table(bq_client)
    job = submit_batch_job(input_table, OUTPUT_TABLE)

    print()
    if str(job.state).endswith("SUCCEEDED"):
        print(f"[batch_predict] DONE — results in {OUTPUT_TABLE}")
    else:
        print("[batch_predict] Job still running (normal for batch). Check later with:")
        print(f"   client.batches.get(name='{job.name}')")
        print(f"   Results will land in: bq://{OUTPUT_TABLE}")

    print(f"\nInspect results:")
    print(f"   bq query 'SELECT * FROM `{OUTPUT_TABLE}` LIMIT 10'")

    return job
