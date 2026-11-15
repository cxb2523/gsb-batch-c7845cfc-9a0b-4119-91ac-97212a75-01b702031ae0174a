# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Orchestrator: BigQuery External Tables + AI Embeddings

Flow:
  1. Create (or reuse) the BQ dataset.
  2. Create an external table over the GCS sales CSV.
  3. Query the external table to verify data is accessible.
  4. Create a remote embedding model (requires BQ connection setup).
  5. Generate embeddings for sample product descriptions.

Set env vars before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
  export GCS_BUCKET=your-gcs-bucket           # must contain data/sales_data.csv
  export BQ_DATASET=your_dataset              # optional, defaults to your_dataset
  export BQ_CONNECTION_ID=vertex-ai-conn      # optional, name of the BQ connection
"""

import os

from google.cloud import bigquery

from external_table import create_external_table, query_external_table
from ai_embeddings import create_remote_model, generate_embeddings

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

EXTERNAL_TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.sales_external"
GCS_URI = f"gs://{GCS_BUCKET}/data/sales_data.csv"

SAMPLE_TEXTS = [
    "BigQuery is a serverless, cost-effective data warehouse for analytics at scale.",
    "Cloud Run executes stateless containers on demand with automatic scaling.",
    "Vertex AI provides managed infrastructure for training and serving ML models.",
    "Pub/Sub is a fully managed real-time messaging service for event streaming.",
]


def ensure_dataset(client: bigquery.Client) -> None:
    """Create the BQ dataset if it does not already exist."""
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = "US"
    dataset_ref.description = "GCP Data Engineering Hub — external tables + AI demo."
    dataset_ref.labels = {"env": "demo", "owner": "techtrapture"}
    dataset = client.create_dataset(dataset_ref, exists_ok=True)
    print(f"Dataset ready : {dataset.full_dataset_id} (location={dataset.location})")


def main():
    print("=" * 60)
    print("  Project 06: BQ External Tables + AI Embeddings")
    print("=" * 60)

    client = bigquery.Client(project=PROJECT_ID)

    # Step 1: Dataset
    print("\n[1/4] Ensuring dataset exists ...")
    ensure_dataset(client)

    # Step 2 & 3: External table + query
    print("\n[2/4] Creating external table over GCS ...")
    create_external_table(client, EXTERNAL_TABLE_ID, GCS_URI)

    print("\n[3/4] Querying external table ...")
    query_external_table(client, EXTERNAL_TABLE_ID)

    # Step 4 & 5: AI embeddings (requires BQ connection — gracefully degrades)
    print("\n[4/4] Generating AI embeddings via ML.GENERATE_EMBEDDING ...")
    if create_remote_model(client):
        generate_embeddings(client, SAMPLE_TEXTS)
    else:
        print("Skipping embedding generation — complete the BQ connection setup first.")

    print("\nDone.")


if __name__ == "__main__":
    main()
