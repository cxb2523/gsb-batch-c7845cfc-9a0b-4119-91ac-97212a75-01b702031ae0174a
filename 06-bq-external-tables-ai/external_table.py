# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
External Table over a GCS CSV.

Creates an external table that reads the sales CSV directly from GCS
(zero BigQuery storage cost), then queries it. Data stays in GCS and
is read at query time — no load job, no BQ storage charges.

Trade-offs vs native tables:
  + Zero BQ storage cost; data has one source of truth in GCS.
  + New files matching the URI are visible immediately (no re-load).
  - Slower: no clustering, no partition pruning (unless hive-partitioned).
  - No DML, no streaming, weaker cache; bytes billed = file size.
  Rule of thumb: external for landing/exploration, native for repeated queries.
"""

import os

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")


def create_external_table(client: bigquery.Client, table_id: str, gcs_uri: str) -> bigquery.Table:
    """Create (or replace) a BQ external table pointing at a GCS CSV."""
    external_config = bigquery.ExternalConfig("CSV")
    external_config.source_uris = [gcs_uri]
    external_config.autodetect = True
    external_config.options.skip_leading_rows = 1

    table = bigquery.Table(table_id)
    table.external_data_configuration = external_config

    client.delete_table(table_id, not_found_ok=True)  # idempotent re-create
    table = client.create_table(table)
    print(f"External table created : {table_id}")
    print(f"Backed by              : {gcs_uri}")
    return table


def query_external_table(client: bigquery.Client, table_id: str) -> None:
    """Run a regional revenue aggregation over the external table."""
    print(f"\n--- Querying external table: {table_id} ---")
    rows = client.query(f"""
        SELECT region, COUNT(*) AS orders, ROUND(SUM(total_amount), 2) AS revenue
        FROM `{table_id}`
        GROUP BY region
        ORDER BY revenue DESC
    """).result()

    print(f"{'REGION':<12} {'ORDERS':>8} {'REVENUE':>14}")
    print("-" * 36)
    for row in rows:
        print(f"{row.region:<12} {row.orders:>8} {row.revenue:>14,.2f}")
