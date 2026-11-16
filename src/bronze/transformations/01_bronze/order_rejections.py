"""Bronze · Order Rejections (Streaming Table)

Queryable audit trail of Event Hub messages that could not be parsed into an
order. Every rejected message is recorded here instead of being silently
dropped, and is guaranteed never to appear in ``01_bronze.orders``.

Columns
-------
raw_value          Original, untouched message payload (string).
event_time         Event Hub record timestamp from the landing table.
ingestion_time     Time the message entered the landing table.
rejection_reason   One of:
                     * malformed_json    — payload is not valid order JSON
                     * missing_order_id  — parsed, but order_id is null/blank
rejected_at        Processing time at which the rejection was recorded.

The append flow is incremental and state-free (each bad message is appended
once); no full recomputation is involved.
"""
import os
import sys

from pyspark import pipelines as dp
from pyspark.sql.functions import col

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
from _shared.order_ingest import rejection_records  # noqa: E402

dp.create_streaming_table(
    name="restaurant_chain_db.01_bronze.order_rejections",
    comment=(
        "Rejected Event Hub order messages: raw payload, landing timestamps "
        "and rejection reason (malformed_json | missing_order_id)"
    ),
    table_properties={"quality": "bronze"},
)


@dp.append_flow(target="restaurant_chain_db.01_bronze.order_rejections")
def order_rejections_from_eventhub():
    """Stream every unparseable / order_id-less message into the audit table."""
    return rejection_records(
        spark.readStream.table("eventhub_rejected")
    ).select(
        col("raw_value"),
        col("event_time"),
        col("ingestion_time"),
        col("rejection_reason"),
        col("rejected_at"),
    )
