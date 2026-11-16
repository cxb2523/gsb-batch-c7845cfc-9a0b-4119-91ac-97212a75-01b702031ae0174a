"""Landing · EventHub Raw Stream

Raw Event Hubs messages ingested via an *injectable input source* (see
``_shared/eventhub_sources.py``). In production the source is the Kafka-
compatible Event Hub connector; tests inject a local-file / in-memory source
via the ``bronze.eventhub.source`` Spark conf, so no connection string or
Databricks secret is needed locally.

Published to 00_landing as a streaming table. Data is kept as-is —
JSON parsing happens downstream in the bronze temp views.

Production settings:
  failOnDataLoss=true   → Fail fast if offsets expire (never silently skip).
  maxOffsetsPerTrigger  → Cap batch size to prevent backlog spikes on restart.
  kafka.request/session → Resilience against transient EventHub delays.
"""
import os
import sys

from pyspark import pipelines as dp

# Make the shared, SDP-independent helper package importable
# (transformations/_shared).
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
from _shared.eventhub_sources import build_eventhub_source  # noqa: E402


# ── Landing ST ────────────────────────────────────────────────────

@dp.table(
    name="restaurant_chain_db.00_landing.eventhub_raw",
    comment="Raw Event Hubs messages ingested via the injectable Event Hub source",
    table_properties={
        "quality": "landing",
        "pipelines.reset.allowed": "false",
    },
)
@dp.expect("valid_topic", "topic IS NOT NULL")
@dp.expect("valid_value", "value IS NOT NULL")
@dp.expect("valid_event_time", "event_time IS NOT NULL")
@dp.expect("valid_ingestion_time", "ingestion_time IS NOT NULL")
def eventhub_raw():
    # Injection entry: bronze.eventhub.source = kafka | local-file
    # (tests may also pass an InMemoryEventHubSource programmatically).
    source = build_eventhub_source(spark, dbutils)
    return source.read_stream(spark)
