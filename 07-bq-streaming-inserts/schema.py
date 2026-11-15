# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Dataset and table creation for the streaming inserts demo.

Provides idempotent helpers to ensure the BQ dataset and events_stream table
exist before producers start inserting rows. Labels flow into billing export —
always set them for FinOps attribution.
"""

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.events_stream"


def ensure_dataset(client: bigquery.Client) -> bigquery.Dataset:
    """Create the dataset if it does not already exist (idempotent)."""
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = "US"
    dataset_ref.description = "GCP Data Engineering Hub — streaming inserts demo."
    dataset_ref.labels = {"env": "demo", "owner": "techtrapture", "purpose": "streaming"}
    dataset = client.create_dataset(dataset_ref, exists_ok=True)
    print(f"Dataset ready : {dataset.full_dataset_id} (location={dataset.location})")
    return dataset


def ensure_events_table(client: bigquery.Client) -> bigquery.Table:
    """Create the events_stream table with explicit schema (idempotent)."""
    schema = [
        bigquery.SchemaField("event_id", "STRING", mode="REQUIRED",
                             description="Unique event key — used for best-effort dedup"),
        bigquery.SchemaField("event_type", "STRING",
                             description="e.g. page_view, click, purchase"),
        bigquery.SchemaField("event_ts", "TIMESTAMP",
                             description="UTC timestamp of the event"),
        bigquery.SchemaField("payload", "STRING",
                             description="Freeform event payload or metadata"),
        bigquery.SchemaField("region", "STRING",
                             description="Originating region"),
        bigquery.SchemaField("batch_id", "INTEGER",
                             description="Batch number from the producer"),
    ]

    table = bigquery.Table(TABLE_ID, schema=schema)
    table.description = "Real-time event stream ingested via legacy streaming API."
    table.labels = {"env": "demo", "team": "techtrapture", "ingest": "streaming"}

    table = client.create_table(table, exists_ok=True)
    print(f"Table ready   : {table.full_table_id}")
    print("Schema:")
    for field in table.schema:
        print(f"  - {field.name:<15} {field.field_type:<10} ({field.mode})")
    return table
