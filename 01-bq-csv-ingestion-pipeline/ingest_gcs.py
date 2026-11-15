"""
Functions for loading CSV from Cloud Storage into BigQuery using an explicit schema.

GCS loads are server-side: BigQuery reads the object directly, so this
scales to very large files with no local bandwidth cost. Explicit schema
is production-safe — avoids autodetect surprises on re-runs.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
BUCKET_NAME = os.environ.get("GCS_BUCKET", "your-gcs-bucket")
TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.sales_data_gcs"
GCS_URI = f"gs://{BUCKET_NAME}/data/sales_data.csv"

# Explicit schema matching sales_data.csv
SALES_SCHEMA = [
    bigquery.SchemaField("order_id", "INTEGER"),
    bigquery.SchemaField("order_date", "DATE"),
    bigquery.SchemaField("customer_name", "STRING"),
    bigquery.SchemaField("region", "STRING"),
    bigquery.SchemaField("product", "STRING"),
    bigquery.SchemaField("category", "STRING"),
    bigquery.SchemaField("quantity", "INTEGER"),
    bigquery.SchemaField("unit_price", "FLOAT"),
    bigquery.SchemaField("total_amount", "FLOAT"),
]


def load_csv_from_gcs(
    gcs_uri: str = GCS_URI,
    table_id: str = TABLE_ID,
    schema: list = None,
) -> None:
    """
    Load a CSV from Cloud Storage into BigQuery with an explicit schema.

    Uses load_table_from_uri for server-side ingestion — BigQuery reads
    directly from GCS with no local bandwidth cost. Explicit schema prevents
    silent type-coercion bugs that autodetect can introduce on re-runs.

    Args:
        gcs_uri:  GCS URI of the CSV file (gs://bucket/path/file.csv).
        table_id: Fully-qualified BigQuery table ID (project.dataset.table).
        schema:   List of SchemaField objects. Defaults to SALES_SCHEMA.
    """
    if schema is None:
        schema = SALES_SCHEMA

    client = bigquery.Client(project=PROJECT_ID)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        schema=schema,
        skip_leading_rows=1,  # the CSV has a header row
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,  # idempotent
    )

    print(f"Loading {gcs_uri}")
    print(f"     -> {table_id} ...")

    # GCS loads are server-side: BigQuery reads the object directly, so this
    # scales to very large files with no local bandwidth cost.
    load_job = client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    load_job.result()

    table = client.get_table(table_id)
    print(f"\nLoad complete.")
    print(f"Rows loaded   : {table.num_rows:,}")
    print(f"Table size    : {table.num_bytes / 1024:.1f} KiB")
    print(f"Schema fields : {[f.name for f in table.schema]}")
