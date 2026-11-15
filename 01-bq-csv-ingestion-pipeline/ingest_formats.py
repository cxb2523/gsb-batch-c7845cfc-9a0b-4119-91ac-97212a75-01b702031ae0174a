"""
Functions for loading NEWLINE_DELIMITED_JSON and Parquet files from GCS into BigQuery.

Shows the two most common non-CSV ingestion formats side by side.
Parquet is preferred for pipelines: typed, compressed, and splittable.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import io
import json
import os

import pandas as pd
from google.cloud import bigquery, storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
BUCKET_NAME = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

ROWS = [
    {"product": "Laptop", "category": "Electronics", "quantity": 3, "unit_price": 1200.0},
    {"product": "Desk", "category": "Furniture", "quantity": 5, "unit_price": 350.5},
    {"product": "Monitor", "category": "Electronics", "quantity": 7, "unit_price": 220.0},
]


def upload_fixtures(bucket) -> None:
    """
    Upload sample JSONL and Parquet fixtures to GCS.

    JSONL: one JSON object per line (NOT a JSON array).
    Parquet: self-describing columnar format — schema travels with the file.

    Args:
        bucket: google.cloud.storage.Bucket object to upload into.
    """
    # JSONL: one JSON object per line (NOT a JSON array).
    jsonl = "\n".join(json.dumps(r) for r in ROWS)
    bucket.blob("data/demo_products.jsonl").upload_from_string(
        jsonl, content_type="application/json"
    )

    # Parquet: self-describing columnar format — schema travels with the file.
    buf = io.BytesIO()
    pd.DataFrame(ROWS).to_parquet(buf, index=False)
    bucket.blob("data/demo_products.parquet").upload_from_string(
        buf.getvalue(), content_type="application/octet-stream"
    )
    print(f"Uploaded demo_products.jsonl + demo_products.parquet to gs://{BUCKET_NAME}/data/")


def load_jsonl_from_gcs(bq_client, bucket_name: str = BUCKET_NAME) -> None:
    """
    Load a newline-delimited JSON file from GCS into BigQuery.

    JSONL needs autodetect or an explicit schema. WRITE_TRUNCATE keeps
    re-runs idempotent.

    Args:
        bq_client:   BigQuery client.
        bucket_name: GCS bucket containing demo_products.jsonl.
    """
    json_table = f"{PROJECT_ID}.{DATASET_ID}.demo_products_json"
    json_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
        autodetect=True,  # JSONL needs autodetect or an explicit schema
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    bq_client.load_table_from_uri(
        f"gs://{bucket_name}/data/demo_products.jsonl",
        json_table,
        job_config=json_config,
    ).result()
    print(f"\n[JSONL]   loaded {bq_client.get_table(json_table).num_rows} rows -> {json_table}")


def load_parquet_from_gcs(bq_client, bucket_name: str = BUCKET_NAME) -> None:
    """
    Load a Parquet file from GCS into BigQuery.

    No autodetect needed: Parquet embeds its own schema. WRITE_TRUNCATE
    keeps re-runs idempotent.

    Args:
        bq_client:   BigQuery client.
        bucket_name: GCS bucket containing demo_products.parquet.
    """
    pq_table = f"{PROJECT_ID}.{DATASET_ID}.demo_products_parquet"
    pq_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        # No autodetect needed: Parquet embeds its own schema.
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    bq_client.load_table_from_uri(
        f"gs://{bucket_name}/data/demo_products.parquet",
        pq_table,
        job_config=pq_config,
    ).result()
    print(f"[PARQUET] loaded {bq_client.get_table(pq_table).num_rows} rows -> {pq_table}")
    print("\nTip: prefer Parquet for pipelines — typed, compressed, splittable.")


def load_json_and_parquet(bucket_name: str = BUCKET_NAME) -> None:
    """
    Orchestrates: upload fixtures to GCS, then load both JSONL and Parquet into BigQuery.

    Args:
        bucket_name: GCS bucket to use for fixture staging.
    """
    bq = bigquery.Client(project=PROJECT_ID)
    gcs = storage.Client(project=PROJECT_ID)
    upload_fixtures(gcs.bucket(bucket_name))
    load_jsonl_from_gcs(bq, bucket_name)
    load_parquet_from_gcs(bq, bucket_name)
