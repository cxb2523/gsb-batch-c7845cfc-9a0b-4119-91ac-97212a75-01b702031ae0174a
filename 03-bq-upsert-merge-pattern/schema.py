"""
Table creation functions: partitioned/clustered table setup and table copy/snapshot.

Covers DDL for production-grade table design (partition + cluster) and the
lightweight backup pattern (snapshot) to take before risky DML operations.
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
BUCKET_NAME = os.environ.get("GCS_BUCKET", "your-gcs-bucket")

FLAT_TABLE = f"{PROJECT_ID}.{DATASET_ID}.sales_flat"
PART_TABLE = f"{PROJECT_ID}.{DATASET_ID}.sales_partitioned"
GCS_URI = f"gs://{BUCKET_NAME}/data/sales_data.csv"

SOURCE_TABLE = f"{PROJECT_ID}.{DATASET_ID}.copy_demo_source"
COPY_TABLE = f"{PROJECT_ID}.{DATASET_ID}.copy_demo_copy"
SNAPSHOT_TABLE = f"{PROJECT_ID}.{DATASET_ID}.copy_demo_snapshot"


def ensure_flat_table(client: bigquery.Client) -> str:
    """
    Load the sales fixture from GCS as a plain (unpartitioned) table.

    Args:
        client: BigQuery client.

    Returns:
        Fully-qualified table ID of the flat table.
    """
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        autodetect=True,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_uri(GCS_URI, FLAT_TABLE, job_config=job_config).result()
    print(f"Flat table ready        : {FLAT_TABLE}")
    return FLAT_TABLE


def create_partitioned_clustered_table(client: bigquery.Client) -> str:
    """
    Create a date-partitioned, clustered table from the flat sales table using CTAS DDL.

    Partitioning prunes whole date ranges; clustering sorts blocks within
    each partition so equality/range filters on those columns scan less.

    Args:
        client: BigQuery client.

    Returns:
        Fully-qualified table ID of the partitioned table.
    """
    client.query(f"""
        CREATE OR REPLACE TABLE `{PART_TABLE}`
        PARTITION BY order_date
        CLUSTER BY region, category
        AS SELECT * FROM `{FLAT_TABLE}`
    """).result()
    print(f"Partitioned table ready : {PART_TABLE}")
    return PART_TABLE


def measure_bytes(client: bigquery.Client, sql: str) -> int:
    """
    Dry-run a query and return estimated bytes processed (free, no slots consumed).

    Args:
        client: BigQuery client.
        sql:    SQL query string to estimate.

    Returns:
        Estimated bytes processed.
    """
    cfg = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
    return client.query(sql, job_config=cfg).total_bytes_processed


def compare_flat_vs_partitioned(client: bigquery.Client) -> None:
    """
    Run the same filter query against flat and partitioned tables via dry run
    and print the bytes-processed comparison.

    Args:
        client: BigQuery client.
    """
    where = "WHERE order_date BETWEEN '2026-01-01' AND '2026-01-31' AND region = 'US-East'"
    flat_sql = f"SELECT SUM(total_amount) FROM `{FLAT_TABLE}` {where}"
    part_sql = f"SELECT SUM(total_amount) FROM `{PART_TABLE}` {where}"

    flat_bytes = measure_bytes(client, flat_sql)
    part_bytes = measure_bytes(client, part_sql)

    print("\n=== Bytes processed (dry run, cache off) ===")
    print(f"Flat table        : {flat_bytes:>12,} bytes")
    print(f"Partitioned table : {part_bytes:>12,} bytes")
    if flat_bytes:
        saved = (1 - part_bytes / flat_bytes) * 100
        print(f"Savings           : {saved:.1f}% less data scanned")
    print("\nNote: on a tiny fixture the delta is small; on TB-scale tables")
    print("partition pruning is routinely a 10-100x on-demand cost reduction.")


def ensure_source_table(client: bigquery.Client) -> None:
    """
    Create a source table from Shakespeare word counts for copy/snapshot demos.

    Args:
        client: BigQuery client.
    """
    client.query(f"""
        CREATE OR REPLACE TABLE `{SOURCE_TABLE}` AS
        SELECT word, SUM(word_count) AS total_count
        FROM `bigquery-public-data.samples.shakespeare`
        GROUP BY word LIMIT 500
    """).result()
    print(f"Source ready    : {SOURCE_TABLE}")


def copy_table(client: bigquery.Client) -> None:
    """
    Copy a table using a copy job.

    Copy jobs are free and metadata-fast — no query slots, no scan cost.

    Args:
        client: BigQuery client.
    """
    copy_config = bigquery.CopyJobConfig(
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE  # idempotent
    )
    client.copy_table(SOURCE_TABLE, COPY_TABLE, job_config=copy_config).result()
    print(f"Copy created    : {COPY_TABLE}")


def create_snapshot(client: bigquery.Client) -> None:
    """
    Create a point-in-time table SNAPSHOT of the source table.

    Snapshots are read-only and billed only for bytes that later DIVERGE
    from the source — the lightweight "backup before risky DML" tool.
    A previous snapshot is dropped first for idempotency.

    Args:
        client: BigQuery client.
    """
    # A snapshot cannot be overwritten in place, so for idempotency we drop
    # any previous snapshot first (not_found_ok avoids first-run failure).
    client.delete_table(SNAPSHOT_TABLE, not_found_ok=True)

    snapshot_config = bigquery.CopyJobConfig(
        # SNAPSHOT makes the destination a read-only, point-in-time snapshot.
        # Storage is billed only for bytes that later DIVERGE from the source.
        operation_type=bigquery.job.OperationType.SNAPSHOT
    )
    client.copy_table(SOURCE_TABLE, SNAPSHOT_TABLE, job_config=snapshot_config).result()
    print(f"Snapshot created: {SNAPSHOT_TABLE}")

    snap = client.get_table(SNAPSHOT_TABLE)
    print(f"\nSnapshot type   : {snap.table_type}")
    print(f"Snapshot rows   : {snap.num_rows:,}")
    print("\nRestore pattern : copy_table(snapshot, target, operation_type=RESTORE)")
