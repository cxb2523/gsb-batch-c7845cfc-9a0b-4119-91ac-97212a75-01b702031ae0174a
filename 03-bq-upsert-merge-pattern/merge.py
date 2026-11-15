"""
BigQuery MERGE upsert functions — the canonical incremental-load pattern.

Land a batch in a staging table, then MERGE into the target: updates matched
rows and inserts new ones in a single atomic statement. One commit — readers
never see a half-applied batch.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")

TARGET = f"{PROJECT_ID}.{DATASET_ID}.products_target"
STAGING = f"{PROJECT_ID}.{DATASET_ID}.products_staging"


def seed_target(client: bigquery.Client) -> None:
    """
    Seed the target table with an initial three-row product state.

    Args:
        client: BigQuery client.
    """
    client.query(f"""
        CREATE OR REPLACE TABLE `{TARGET}` AS
        SELECT * FROM UNNEST([
            STRUCT(1 AS product_id, 'Laptop'  AS product, 1200.0 AS price, DATE '2026-06-01' AS updated_on),
            STRUCT(2, 'Desk',    350.0, DATE '2026-06-01'),
            STRUCT(3, 'Monitor', 220.0, DATE '2026-06-01')
        ])
    """).result()
    print(f"Target seeded  : {TARGET} (3 rows)")


def load_staging(client: bigquery.Client) -> None:
    """
    Land an incremental batch into the staging table.

    Real pipelines load staging from GCS/files; staging is replaced each
    batch (WRITE_TRUNCATE semantics via CREATE OR REPLACE) so re-runs are safe.

    The batch contains: 1 price update (product_id=2) and 2 new rows (4, 5).

    Args:
        client: BigQuery client.
    """
    client.query(f"""
        CREATE OR REPLACE TABLE `{STAGING}` AS
        SELECT * FROM UNNEST([
            STRUCT(2 AS product_id, 'Desk'     AS product, 379.0 AS price, DATE '2026-06-10' AS updated_on),  -- changed price
            STRUCT(4, 'Keyboard', 85.0,  DATE '2026-06-10'),                                                  -- new row
            STRUCT(5, 'Webcam',   60.0,  DATE '2026-06-10')                                                   -- new row
        ])
    """).result()
    print(f"Staging loaded : {STAGING} (3 rows: 1 update, 2 inserts)")


def run_merge(client: bigquery.Client) -> None:
    """
    Execute the MERGE statement: atomic upsert from staging into target.

    - WHEN MATCHED AND newer: UPDATE the row (updated_on guard skips no-op updates)
    - WHEN NOT MATCHED: INSERT the new row
    One statement, one commit — atomicity guaranteed.

    Args:
        client: BigQuery client.
    """
    # One statement, one commit: readers never see a half-applied batch.
    # The updated_on guard skips no-op updates (saves DML-touched bytes).
    merge_job = client.query(f"""
        MERGE `{TARGET}` T
        USING `{STAGING}` S
        ON T.product_id = S.product_id
        WHEN MATCHED AND S.updated_on > T.updated_on THEN
            UPDATE SET product = S.product, price = S.price, updated_on = S.updated_on
        WHEN NOT MATCHED THEN
            INSERT (product_id, product, price, updated_on)
            VALUES (S.product_id, S.product, S.price, S.updated_on)
    """)
    merge_job.result()
    print(f"MERGE complete : {merge_job.num_dml_affected_rows} rows affected")


def show_target(client: bigquery.Client) -> None:
    """
    Print the current state of the target table after the MERGE.

    Args:
        client: BigQuery client.
    """
    print("\n=== Target after MERGE ===")
    print(f"{'ID':<4} {'PRODUCT':<12} {'PRICE':>8} {'UPDATED_ON':>12}")
    for row in client.query(
        f"SELECT * FROM `{TARGET}` ORDER BY product_id"
    ).result():
        print(f"{row.product_id:<4} {row.product:<12} {row.price:>8.2f} {str(row.updated_on):>12}")

    print("\nPattern: load staging (truncate) -> MERGE -> drop/keep staging.")
    print("Add a WHEN NOT MATCHED BY SOURCE ... DELETE clause for full syncs.")


def run_upsert_pipeline(client: bigquery.Client = None) -> None:
    """
    Full upsert pipeline: seed target → load staging → MERGE → show result.

    Args:
        client: BigQuery client. Created from environment if not provided.
    """
    if client is None:
        client = bigquery.Client(project=PROJECT_ID)
    seed_target(client)
    load_staging(client)
    run_merge(client)
    show_target(client)
