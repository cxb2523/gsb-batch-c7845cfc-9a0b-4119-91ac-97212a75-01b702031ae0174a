# Author: TechTrapture | https://techtrapture.com | vishal.bulbule@techtrapture.com
# YouTube: https://www.youtube.com/@TechTrapture | Academy: https://academy.techtrapture.com

"""
Streaming insert producer using insert_rows_json (legacy streaming API).

When to use which ingestion path:
  insert_rows_json (this):  Simple, per-row latency in seconds, billed per GB
                            streamed. Good for low-volume, low-latency trickle.
  Load jobs:               FREE ingestion, batch-oriented — default for pipelines.
  Storage Write API:       Modern streaming path — cheaper than legacy streaming,
                           exactly-once semantics, gRPC throughput. Prefer for
                           new high-volume real-time ingestion.

Rows land in a buffer and are queryable immediately but may not appear in
table.num_rows until the buffer is flushed (~90 min).
"""

import os
import uuid
from datetime import datetime, timezone

from google.cloud import bigquery

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "your-project-id")
DATASET_ID = os.environ.get("BQ_DATASET", "your_dataset")
TABLE_ID = f"{PROJECT_ID}.{DATASET_ID}.events_stream"

# Simulated event catalogue for demo batches
_EVENT_CATALOGUE = [
    ("page_view", "/home", "US-East"),
    ("page_view", "/pricing", "US-West"),
    ("click", "signup_btn", "EU"),
    ("click", "demo_cta", "APAC"),
    ("purchase", "order_bq_slots", "US-East"),
    ("purchase", "order_vertex_ai", "US-West"),
    ("api_call", "embeddings_endpoint", "EU"),
    ("api_call", "gemini_endpoint", "APAC"),
]


def _build_rows(batch_id: int, batch_size: int) -> list[dict]:
    """Generate a batch of synthetic event rows."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for i in range(batch_size):
        event_type, payload, region = _EVENT_CATALOGUE[(batch_id * batch_size + i) % len(_EVENT_CATALOGUE)]
        rows.append({
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "event_ts": now,
            "payload": payload,
            "region": region,
            "batch_id": batch_id,
        })
    return rows


def stream_batch(client: bigquery.Client, batch_id: int, batch_size: int = 5) -> bool:
    """
    Stream one batch of rows into TABLE_ID.

    Uses event_id as the row_id for best-effort deduplication within the
    short streaming buffer window. Returns True on success.
    """
    rows = _build_rows(batch_id, batch_size)
    print(f"\n--- Batch {batch_id}: streaming {len(rows)} rows -> {TABLE_ID} ---")
    for r in rows:
        print(f"  [{r['event_type']:12s}] {r['payload']:<30} region={r['region']}")

    errors = client.insert_rows_json(
        TABLE_ID,
        rows,
        row_ids=[r["event_id"] for r in rows],
    )

    if errors:
        print(f"  Errors in batch {batch_id}: {errors}")
        return False

    print(f"  Batch {batch_id} streamed successfully ({len(rows)} rows).")
    return True


def stream_from_csv(client: bigquery.Client, csv_path: str) -> bool:
    """
    Stream all rows from a local CSV file into TABLE_ID.

    Each CSV row maps to an events_stream row with event_type='csv_import'.
    Demonstrates how to bridge batch CSV data into the streaming path.
    """
    import csv
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for i, line in enumerate(reader):
            rows.append({
                "event_id": str(uuid.uuid4()),
                "event_type": "csv_import",
                "event_ts": now,
                "payload": f"order_id={line.get('order_id', i)}  product={line.get('product', '')}",
                "region": line.get("region", "unknown"),
                "batch_id": 99,
            })

    print(f"\n--- CSV import: streaming {len(rows)} rows from {csv_path} ---")
    errors = client.insert_rows_json(
        TABLE_ID,
        rows,
        row_ids=[r["event_id"] for r in rows],
    )

    if errors:
        print(f"  Errors during CSV import: {errors}")
        return False

    print(f"  CSV import complete ({len(rows)} rows streamed).")
    return True
