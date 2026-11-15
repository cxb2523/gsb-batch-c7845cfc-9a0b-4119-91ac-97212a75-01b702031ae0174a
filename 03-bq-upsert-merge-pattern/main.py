"""
BigQuery Upsert / MERGE Pattern — entry point.

Demonstrates the full incremental-load pipeline:
  1. Create partitioned + clustered table from GCS sales fixture
  2. Compare bytes scanned vs flat table (partition pruning proof)
  3. Run the MERGE upsert: seed target → load staging → MERGE → show result
  4. Copy table and create a point-in-time snapshot (backup-before-DML pattern)

Usage:
    python main.py
"""
# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

import os

from google.cloud import bigquery

from schema import (
    ensure_flat_table,
    create_partitioned_clustered_table,
    compare_flat_vs_partitioned,
    ensure_source_table,
    copy_table,
    create_snapshot,
)
from merge import run_upsert_pipeline

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")

SEPARATOR = "=" * 60


def section(title: str) -> None:
    print(f"\n{SEPARATOR}")
    print(f"  {title}")
    print(SEPARATOR)


def main():
    client = bigquery.Client(project=PROJECT_ID)

    section("1. Flat Table Load from GCS")
    ensure_flat_table(client)

    section("2. Create Partitioned + Clustered Table")
    create_partitioned_clustered_table(client)

    section("3. Partition Pruning: Bytes Comparison")
    compare_flat_vs_partitioned(client)

    section("4. MERGE Upsert Pipeline (Staging -> Target)")
    run_upsert_pipeline(client)

    section("5. Table Copy + Snapshot (Backup Pattern)")
    ensure_source_table(client)
    copy_table(client)
    create_snapshot(client)

    print(f"\n{SEPARATOR}")
    print("All upsert/merge demos complete.")


if __name__ == "__main__":
    main()
