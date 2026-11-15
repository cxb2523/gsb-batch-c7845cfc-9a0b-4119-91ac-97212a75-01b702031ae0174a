# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Data estate inventory module: one-shot report of where your bytes live —
every BigQuery dataset/table with logical sizes, plus every GCS bucket
with storage class and location. Storage is the silent line item; it
compounds monthly while compute waste at least shows up in dashboards.
"""

import os

from google.api_core import exceptions
from google.cloud import bigquery, storage

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")


def fmt_gb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 ** 3):,.2f} GB"


def inventory_bigquery(project_id: str = PROJECT_ID) -> dict:
    """
    Lists all BigQuery datasets and their tables with logical sizes.

    Flags:
      - Tables without time partitioning (accumulate forever)

    Returns a dict with:
      - 'datasets': int
      - 'tables': int
      - 'total_bytes': int
    """
    # Where BQ bytes hide:
    #   - tables without partition expiration accumulate forever
    #   - logical vs physical billing: compressed (physical) billing can cut
    #     storage cost 50-80% on big datasets — check per-dataset
    #   - long-time-travel windows (up to 7d) inflate physical storage
    print("\nBIGQUERY DATASETS / TABLES")
    print("-" * 80)
    client = bigquery.Client(project=project_id)
    total_bytes = 0
    table_count = 0
    try:
        datasets = list(client.list_datasets())
    except exceptions.PermissionDenied:
        print("  Permission denied: needs roles/bigquery.metadataViewer on the project.")
        return {"datasets": 0, "tables": 0, "total_bytes": 0}
    if not datasets:
        print("  (no datasets)")
        return {"datasets": 0, "tables": 0, "total_bytes": 0}
    for ds in datasets:
        print(f"\n  Dataset: {ds.dataset_id}")
        for table_item in client.list_tables(ds.dataset_id):
            # list_tables returns light metadata; get_table fetches num_bytes
            t = client.get_table(table_item.reference)
            total_bytes += t.num_bytes or 0
            table_count += 1
            part = "partitioned" if t.time_partitioning else "NOT partitioned"
            print(f"    {t.table_id:<35} {fmt_gb(t.num_bytes or 0):>12}  "
                  f"{t.num_rows or 0:>12,} rows  ({part})")
    print(f"\n  BigQuery logical total: {fmt_gb(total_bytes)}")
    return {"datasets": len(datasets), "tables": table_count, "total_bytes": total_bytes}


def inventory_gcs(project_id: str = PROJECT_ID) -> dict:
    """
    Lists all GCS buckets with storage class, location, and lifecycle rule count.

    Flags:
      - Buckets with no lifecycle rules (bytes never age to cheaper classes)

    Returns a dict with:
      - 'buckets': int
      - 'no_lifecycle': int (buckets missing lifecycle rules)
    """
    # Where GCS bytes hide:
    #   - STANDARD-class buckets full of cold data (lifecycle rules fix this)
    #   - non-current object versions in versioned buckets (invisible in UI sums)
    #   - abandoned multipart uploads and old composite temp objects
    # Bucket-level size needs Monitoring metrics or Storage Insights; here we
    # report class/location, which drives the per-GB rate.
    print("\nCLOUD STORAGE BUCKETS")
    print("-" * 80)
    client = storage.Client(project=project_id)
    try:
        buckets = list(client.list_buckets())
    except exceptions.PermissionDenied:
        print("  Permission denied: needs storage.buckets.list, e.g. roles/viewer")
        print("  or roles/storage.admin on the project.")
        return {"buckets": 0, "no_lifecycle": 0}
    if not buckets:
        print("  (no buckets)")
        return {"buckets": 0, "no_lifecycle": 0}
    print(f"  {'BUCKET':<40} {'CLASS':<12} {'LOCATION':<15} LIFECYCLE")
    no_lifecycle = 0
    for b in buckets:
        # No lifecycle rules on a STANDARD bucket = bytes never age to cheaper
        # classes. Multi-region location costs ~2x single-region for storage.
        rules = len(list(b.lifecycle_rules or []))
        if rules == 0:
            no_lifecycle += 1
        lifecycle = f"{rules} rule(s)" if rules else "NONE <-- add age-based rules"
        print(f"  {b.name:<40} {b.storage_class:<12} {b.location:<15} {lifecycle}")
    print(f"\n  Buckets: {len(buckets)}")
    return {"buckets": len(buckets), "no_lifecycle": no_lifecycle}
