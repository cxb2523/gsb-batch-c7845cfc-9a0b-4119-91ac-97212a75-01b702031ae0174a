# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Orchestrator: BigQuery Streaming Inserts

Flow:
  1. Create (or reuse) the BQ dataset.
  2. Create (or reuse) the events_stream table with explicit schema.
  3. Stream 3 synthetic event batches via insert_rows_json.
  4. Stream all rows from data/sales_data.csv as csv_import events.
  5. Query the table to verify rows are immediately queryable.

Set env vars before running:
  export GOOGLE_CLOUD_PROJECT=your-project-id
  export BQ_DATASET=your_dataset    # optional, defaults to your_dataset
"""

import os
from pathlib import Path

from google.cloud import bigquery

from schema import ensure_dataset, ensure_events_table, TABLE_ID
from producer import stream_batch, stream_from_csv

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
CSV_PATH = Path(__file__).parent / "data" / "sales_data.csv"
NUM_SYNTHETIC_BATCHES = 3
BATCH_SIZE = 4


def query_recent_events(client: bigquery.Client, limit: int = 10) -> None:
    """Query the most recent streamed events to confirm they are queryable."""
    print(f"\n--- Querying latest {limit} events from {TABLE_ID} ---")
    rows = client.query(f"""
        SELECT event_id, event_type, event_ts, payload, region, batch_id
        FROM `{TABLE_ID}`
        ORDER BY event_ts DESC
        LIMIT {limit}
    """).result()

    print(f"{'EVENT_TYPE':<14} {'REGION':<10} {'BATCH':>6}  PAYLOAD")
    print("-" * 70)
    for row in rows:
        print(f"{row.event_type:<14} {row.region:<10} {row.batch_id:>6}  {row.payload}")


def main():
    print("=" * 60)
    print("  Project 07: BigQuery Streaming Inserts")
    print("=" * 60)

    client = bigquery.Client(project=PROJECT_ID)

    # Step 1: Dataset
    print("\n[1/5] Ensuring dataset exists ...")
    ensure_dataset(client)

    # Step 2: Table
    print("\n[2/5] Ensuring events_stream table exists ...")
    ensure_events_table(client)

    # Step 3: Synthetic event batches
    print(f"\n[3/5] Streaming {NUM_SYNTHETIC_BATCHES} synthetic event batches ...")
    for batch_id in range(NUM_SYNTHETIC_BATCHES):
        stream_batch(client, batch_id=batch_id, batch_size=BATCH_SIZE)

    # Step 4: CSV rows as streaming events
    print("\n[4/5] Streaming rows from data/sales_data.csv ...")
    stream_from_csv(client, str(CSV_PATH))

    # Step 5: Verify
    print("\n[5/5] Verifying — querying most recent events ...")
    query_recent_events(client, limit=10)

    print("\n")
    print("Note: streamed rows land in a buffer; they are queryable immediately")
    print("but may not show in table.num_rows until the buffer is flushed (~90 min).")
    print("\nDone.")


if __name__ == "__main__":
    main()
