"""
Functions for loading a local CSV file into BigQuery.
Streams the local file bytes into a load job using load_table_from_file
with schema autodetect — suitable for one-off loads from a local machine.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os
from pathlib import Path

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.sales_data"

# Default CSV path: data/sales_data.csv relative to this file.
DEFAULT_CSV_PATH = Path(__file__).resolve().parent / "data" / "sales_data.csv"


def load_csv_local(csv_path: Path = DEFAULT_CSV_PATH, table_id: str = TABLE_ID) -> None:
    """
    Load a local CSV file into BigQuery using schema autodetect.

    Uses load_table_from_file which streams the local file bytes into a
    BigQuery load job. WRITE_TRUNCATE makes runs idempotent.

    Args:
        csv_path: Path to the local CSV file.
        table_id:  Fully-qualified BigQuery table ID (project.dataset.table).
    """
    if not csv_path.exists():
        print(f"ERROR: CSV file not found at {csv_path}")
        print("Expected sales_data.csv under the data/ folder.")
        return

    client = bigquery.Client(project=PROJECT_ID)

    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,        # header row
        autodetect=True,            # let BigQuery infer column names + types
        # WRITE_TRUNCATE makes the sample idempotent — re-runs replace data.
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )

    print(f"Loading {csv_path.name} -> {table_id} ...")

    # load_table_from_file streams the local file bytes into a load job.
    with open(csv_path, "rb") as source_file:
        load_job = client.load_table_from_file(
            source_file, table_id, job_config=job_config
        )

    load_job.result()  # block until the load job finishes

    table = client.get_table(table_id)
    print(f"\nLoad complete.")
    print(f"Rows loaded : {table.num_rows:,}")
    print("Detected schema:")
    for field in table.schema:
        print(f"  - {field.name:<15} {field.field_type}")
