# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Load phase — GCS staging, BigQuery batch load job, and incremental MERGE upsert.

ETL pattern used:
  1. Write Parquet locally, upload to GCS (durable, replayable artifact)
  2. BQ load job from GCS (FREE — no bytes-scanned cost, shared slot pool)
  3. Verify row count (never trust a pipeline that doesn't count its rows)
  4. Incremental MERGE: land delta in staging, upsert into target — idempotent

Why stage on GCS instead of loading the DataFrame directly?
  - Decoupling: the staged file survives failed loads; replayable without
    re-extracting from the source
  - Other consumers (Spark, Dataproc, external tables) can read the same file
  - Parquet means no schema/CSV-parsing config in the load job
"""

from datetime import date
from pathlib import Path

import pandas as pd
from google.cloud import bigquery, storage


# ─── GCS staging ─────────────────────────────────────────────────────────────

def stage_to_gcs(df: pd.DataFrame, local_parquet: str | Path, bucket: str, blob_name: str, project_id: str) -> str:
    """Write DataFrame as Parquet locally, then upload to GCS.

    Args:
        df: Cleaned DataFrame.
        local_parquet: Local path to write the Parquet file.
        bucket: GCS bucket name (must exist, or create it first).
        blob_name: Object path within the bucket.
        project_id: GCP project ID.

    Returns:
        GCS URI (gs://bucket/blob_name).
    """
    local_parquet = Path(local_parquet)
    local_parquet.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(local_parquet, engine="pyarrow", compression="snappy", index=False)

    gcs_client = storage.Client(project=project_id)
    blob = gcs_client.bucket(bucket).blob(blob_name)
    blob.upload_from_filename(local_parquet)

    uri = f"gs://{bucket}/{blob_name}"
    print(f"  staged: {uri} ({local_parquet.stat().st_size:,} bytes)")
    return uri


# ─── BigQuery batch load ──────────────────────────────────────────────────────

def ensure_dataset(client: bigquery.Client, project_id: str, dataset_id: str, location: str = "US") -> None:
    """Create the BQ dataset if it doesn't exist (idempotent)."""
    ds = bigquery.Dataset(f"{project_id}.{dataset_id}")
    ds.location = location
    client.create_dataset(ds, exists_ok=True)


def load_from_gcs(client: bigquery.Client, gcs_uri: str, table_id: str) -> str:
    """Run a BigQuery batch load job from a GCS Parquet file.

    Load jobs are FREE (no bytes-scanned cost, uses shared slot pool).
    WRITE_TRUNCATE makes the whole pipeline idempotent — re-running replaces,
    never duplicates.

    Args:
        client: BigQuery client.
        gcs_uri: GCS URI of the Parquet file.
        table_id: Fully-qualified BQ table ID.

    Returns:
        Job ID of the completed load job.
    """
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_uri(gcs_uri, table_id, job_config=job_config)
    job.result()  # surfaces load errors (type clashes, quota) as exceptions
    print(f"  BQ load job: {job.job_id} -> {table_id}")
    return job.job_id


def verify_row_count(client: bigquery.Client, table_id: str, expected: int) -> int:
    """Count rows in BQ and assert against expected. Raises on mismatch."""
    row = next(iter(client.query(f"SELECT COUNT(*) AS n FROM `{table_id}`").result()))
    actual = row.n
    print(f"  verify: {actual} rows in {table_id} (expected {expected})")
    if actual != expected:
        raise SystemExit(f"FAIL: row count mismatch ({actual} != {expected})")
    return actual


# ─── Incremental MERGE ───────────────────────────────────────────────────────

def build_merge_sql(target: str, staging: str) -> str:
    """Build the MERGE SQL for incremental upsert keyed on order_id.

    Why MERGE beats truncate-reload at scale:
      - Cost: truncate-reload rewrites the WHOLE table every run.
        MERGE touches only affected partitions/rows.
      - Idempotency: re-running a replayed batch is a no-op insert-wise.
      - Late data: corrections update in place; truncate-reload forces full
        history retention in the source forever.
      - Pair with partitioning on order_date + clustering on order_id so the
        MERGE prunes to touched partitions instead of scanning the whole target.
    """
    return f"""
MERGE `{target}` T
USING `{staging}` S
ON T.order_id = S.order_id
WHEN MATCHED THEN UPDATE SET
  order_date    = S.order_date,
  customer_name = S.customer_name,
  region        = S.region,
  product       = S.product,
  category      = S.category,
  quantity      = S.quantity,
  unit_price    = S.unit_price,
  total_amount  = S.total_amount
WHEN NOT MATCHED THEN INSERT ROW
"""


def _count(client: bigquery.Client, table: str) -> int:
    return next(iter(client.query(f"SELECT COUNT(*) n FROM `{table}`").result())).n


def run_incremental_merge(
    client: bigquery.Client,
    batch_df: pd.DataFrame,
    target_table: str,
    staging_table: str,
    label: str = "batch",
) -> int:
    """Land a batch into staging then MERGE into the target.

    Steps:
      1. WRITE_TRUNCATE into staging — staging holds ONLY this batch, so a
         crashed run leaves nothing half-merged.
      2. Atomic MERGE — single transaction in BigQuery.

    Args:
        client: BigQuery client.
        batch_df: Delta/increment DataFrame.
        target_table: Fully-qualified target table ID.
        staging_table: Fully-qualified staging table ID.
        label: Display label for this run.

    Returns:
        Row count of the target table after the MERGE.
    """
    # Stage the delta
    client.load_table_from_dataframe(
        batch_df, staging_table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
        ),
    ).result()

    # Atomic upsert
    merge_sql = build_merge_sql(target_table, staging_table)
    result = client.query(merge_sql).result()
    target_count = _count(client, target_table)
    print(f"  {label}: staged {len(batch_df)} rows, "
          f"merge affected {result.num_dml_affected_rows} rows -> "
          f"target now {target_count} rows")
    return target_count


def seed_target(client: bigquery.Client, df: pd.DataFrame, target_table: str) -> int:
    """Bootstrap the target table with the historical portion of data."""
    client.load_table_from_dataframe(
        df, target_table,
        job_config=bigquery.LoadJobConfig(
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE
        ),
    ).result()
    count = _count(client, target_table)
    print(f"  bootstrap: seeded target with {count} rows")
    return count
