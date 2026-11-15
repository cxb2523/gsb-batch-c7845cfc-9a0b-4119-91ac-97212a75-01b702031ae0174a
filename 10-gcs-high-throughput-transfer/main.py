# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Orchestrator: GCS High-Throughput Transfer Patterns

Demonstrates three distinct high-throughput / no-disk-IO data transfer patterns:

  Pattern A — In-memory streaming (stream.py)
    DataFrame -> BytesIO -> GCS and back, no disk I/O.
    Use case: Cloud Run / Cloud Functions where disk is ephemeral.

  Pattern B — Parallel bulk transfer (parallel_transfer.py)
    N files uploaded/downloaded concurrently via transfer_manager worker pool.
    Use case: ETL pipelines, bulk exports, batch data movement.

  Pattern C — pandas over gs:// (pandas_gcs.py)
    pd.read_csv("gs://...") via gcsfs — zero boilerplate for analytics reads.
    Use case: notebooks, analytics scripts, ad-hoc exploration.

Set env vars before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
  export GCS_BUCKET=your-gcs-bucket       # must exist; Pattern C needs data/sales_data.csv

Upload the CSV first (Pattern C prerequisite):
  gsutil cp data/sales_data.csv gs://<your-gcs-bucket>/data/sales_data.csv
"""

import os

from google.cloud import storage

from stream import run_stream_demo
from parallel_transfer import run_parallel_transfer_demo
from pandas_gcs import run_pandas_gcs_demo

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
GCS_BUCKET = os.environ.get("GCS_BUCKET", "your-gcs-bucket")


def main():
    print("=" * 60)
    print("  Project 10: GCS High-Throughput Transfer Patterns")
    print("=" * 60)
    print(f"\nProject : {PROJECT_ID}")
    print(f"Bucket  : gs://{GCS_BUCKET}")

    client = storage.Client(project=PROJECT_ID)

    # Pattern A: In-memory streaming
    print("\n" + "=" * 60)
    print("  Pattern A: In-Memory Streaming (BytesIO)")
    print("=" * 60)
    run_stream_demo(client)

    # Pattern B: Parallel bulk transfer
    print("\n" + "=" * 60)
    print("  Pattern B: Parallel Bulk Transfer (transfer_manager)")
    print("=" * 60)
    run_parallel_transfer_demo(client)

    # Pattern C: pandas over gs://
    print("\n" + "=" * 60)
    print("  Pattern C: pandas over gs:// URI (gcsfs)")
    print("=" * 60)
    run_pandas_gcs_demo()

    print("\n" + "=" * 60)
    print("  Summary: When to use what")
    print("=" * 60)
    print("  A — BytesIO streaming  : serverless (Cloud Run/Functions), no disk required")
    print("  B — transfer_manager   : bulk ETL, N files, CPU-bound parallelism")
    print("  C — pandas gs://       : analytics, notebooks, ad-hoc reads, zero boilerplate")
    print("\nDone.")


if __name__ == "__main__":
    main()
