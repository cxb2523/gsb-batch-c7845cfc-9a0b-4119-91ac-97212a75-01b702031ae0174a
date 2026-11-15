# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Project 30: BigQuery ETL Pipeline
Full ETL flow: extract CSV -> transform with pandas -> stage as Parquet on GCS
-> batch load to BigQuery -> verify row count -> run incremental MERGE upsert.

Prerequisites:
  - Set GOOGLE_CLOUD_PROJECT and GCS_BUCKET env vars
  - Run ./deploy.sh to enable required APIs and create the GCS bucket
  - gcloud auth application-default login

Cost estimate (small dataset, 20 rows):
  - BQ load jobs: FREE
  - BQ queries: fractions of a cent (min 10 MB billing per query)
  - GCS: negligible (< 1 MB Parquet file, few seconds storage)
"""

import os
from datetime import date
from pathlib import Path

import pandas as pd
from google.cloud import bigquery

from extract import load_csv, roundtrip_to_bq
from transform import clean_for_bq, add_derived_columns, summary_stats
from load import (
    ensure_dataset, stage_to_gcs, load_from_gcs,
    verify_row_count, run_incremental_merge, seed_target,
)

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")
DATASET_ID = "sales_etl_demo"

# Table IDs
ROUNDTRIP_TABLE = f"{PROJECT_ID}.{DATASET_ID}.sales_roundtrip"
ETL_TABLE = f"{PROJECT_ID}.{DATASET_ID}.sales_etl"
INCREMENTAL_TARGET = f"{PROJECT_ID}.{DATASET_ID}.sales_incremental"
INCREMENTAL_STAGING = f"{PROJECT_ID}.{DATASET_ID}.sales_incremental_staging"

CSV_PATH = Path(__file__).parent / "data" / "sales_data.csv"
PARQUET_LOCAL = Path(__file__).parent / "output" / "sales_etl.parquet"
GCS_BLOB = "etl-demo/sales_data.parquet"


def main():
    print("=" * 60)
    print("Project 30: BigQuery ETL Pipeline")
    print("=" * 60)
    print(f"Project : {PROJECT_ID}")
    print(f"Bucket  : {GCS_BUCKET}")
    print(f"Dataset : {DATASET_ID}\n")

    # Shared BQ client
    bq_client = bigquery.Client(project=PROJECT_ID)
    ensure_dataset(bq_client, PROJECT_ID, DATASET_ID)
    print(f"Dataset ready: {PROJECT_ID}.{DATASET_ID}\n")

    # --- Step 1: Extract + pandas roundtrip ----------------------------------
    print("--- Step 1: Extract CSV + pandas <-> BigQuery roundtrip ---")
    df_raw = load_csv(CSV_PATH)
    print(f"  local frame: {df_raw.shape}")
    roundtrip_to_bq(df_raw, PROJECT_ID, ROUNDTRIP_TABLE)
    print()

    # --- Step 2: Transform ---------------------------------------------------
    print("--- Step 2: Transform (clean + shape for BQ) ---")
    df_clean = clean_for_bq(df_raw)
    summary_stats(df_clean)
    print()

    # --- Step 3: Stage to GCS ------------------------------------------------
    print("--- Step 3: Stage Parquet to GCS ---")
    gcs_uri = stage_to_gcs(
        df=df_clean,
        local_parquet=PARQUET_LOCAL,
        bucket=GCS_BUCKET,
        blob_name=GCS_BLOB,
        project_id=PROJECT_ID,
    )
    print()

    # --- Step 4: Load from GCS to BigQuery -----------------------------------
    print("--- Step 4: BigQuery batch load job (FREE) ---")
    load_from_gcs(bq_client, gcs_uri, ETL_TABLE)
    verify_row_count(bq_client, ETL_TABLE, len(df_clean))
    print()

    # --- Step 5: Incremental MERGE -------------------------------------------
    print("--- Step 5: Incremental MERGE (upsert pattern) ---")

    # Prepare: convert order_date back to date for BQ load
    df_bq = df_clean.copy()
    # order_date is already a Python date object from clean_for_bq

    # Bootstrap target with first 80% of rows ("history")
    split = int(len(df_bq) * 0.8)
    history = df_bq.iloc[:split].copy()
    delta = df_bq.iloc[split:].copy()
    seed_target(bq_client, history, INCREMENTAL_TARGET)

    # Simulate "new day": fresh rows + one correction to an existing order
    correction = history.iloc[[0]].copy()
    correction = correction.copy()
    correction["quantity"] = correction["quantity"] + 1
    correction["total_amount"] = correction["quantity"] * correction["unit_price"]
    correction["order_date"] = date.today()
    batch = pd.concat([delta, correction], ignore_index=True)

    run_incremental_merge(bq_client, batch, INCREMENTAL_TARGET, INCREMENTAL_STAGING, "run 1 (new day) ")

    # Idempotency proof: replay same batch — counts must not change
    before = next(iter(bq_client.query(
        f"SELECT COUNT(*) n FROM `{INCREMENTAL_TARGET}`"
    ).result())).n
    run_incremental_merge(bq_client, batch, INCREMENTAL_TARGET, INCREMENTAL_STAGING, "run 2 (replayed)")
    after = next(iter(bq_client.query(
        f"SELECT COUNT(*) n FROM `{INCREMENTAL_TARGET}`"
    ).result())).n
    assert before == after, f"Replay changed row count: {before} -> {after}"
    print(f"\n  OK: replay was a no-op insert-wise ({before} == {after}). Safe to re-run failed days.")

    print()
    print("=" * 60)
    print("Done. Tables created:")
    print(f"  {ROUNDTRIP_TABLE}")
    print(f"  {ETL_TABLE}")
    print(f"  {INCREMENTAL_TARGET}")
    print(f"\nQuery: SELECT * FROM `{ETL_TABLE}` LIMIT 10")
    print("=" * 60)


if __name__ == "__main__":
    main()
