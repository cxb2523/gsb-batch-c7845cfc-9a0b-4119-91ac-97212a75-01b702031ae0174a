"""Bronze · Unified Orders

Conformed orders merging two sources:
  - Live:       Incremental EventHub stream (from eventhub_parsed temp view)
  - Historical: One-time batch backfill from landing historical_orders

Data notes:
  - Historical range: 2025-07-15 → 2026-01-11
  - EventHub live from: 2026-04-09
  - Gap: ~3 months (Jan 12 – Apr 8) with no order data
  - Zero order_id overlap between sources confirmed

Duplicate handling (Event Hub is at-least-once)
-----------------------------------------------
The live flow is an idempotent **merge flow** keyed on ``order_id``:

  * a watermarked, stateful ``dropDuplicates("order_id")`` collapses repeated
    deliveries within each micro-batch — purely incremental, no full scan;
  * the MERGE then matches against the target table, so a redelivered order
    (even across micro-batches or pipeline restarts) updates the existing row
    instead of being appended a second time.

Exactly one row per ``order_id`` therefore exists in this table, regardless of
how many times Event Hub delivered the message. The historical ``once`` flow
below is intentionally left as an append flow with unchanged semantics.
"""
import os
import sys

from pyspark import pipelines as dp
from pyspark.sql.functions import col, current_timestamp, expr, from_json, lit
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
from _shared.order_ingest import DEFAULT_WATERMARK_DELAY, live_orders  # noqa: E402

ITEM_SCHEMA = StructType([
    StructField("item_id",    StringType()),
    StructField("name",       StringType()),
    StructField("category",   StringType()),
    StructField("quantity",   IntegerType()),
    StructField("unit_price", DoubleType()),
    StructField("subtotal",   DoubleType()),
])

dp.create_streaming_table(
    name="restaurant_chain_db.01_bronze.orders",
    # Primary-key declaration required by the merge_flow below. It constrains
    # MERGE matching only; the historical once append flow is unaffected.
    primary_keys=["order_id"],
    comment="Unified orders — live EventHub stream merged with one-time historical backfill",
    table_properties={"quality": "bronze"},
)


@dp.merge_flow(
    target="restaurant_chain_db.01_bronze.orders",
    comment=(
        "Live EventHub orders. Idempotent MERGE on order_id absorbs at-least-"
        "once redelivery; watermarked dropDuplicates handles intra-batch dupes."
    ),
)
def orders_from_eventhub():
    """Live orders streamed from EventHub — incremental, deduped, upserted."""
    # Optional tuning knob (default 24h); bounds the dropDuplicates state.
    watermark_delay = spark.conf.get(
        "bronze.eventhub.watermark.delay", DEFAULT_WATERMARK_DELAY
    )
    # dropDuplicates inside live_orders guarantees one source row per
    # order_id per micro-batch, so the MERGE never sees ambiguous matches;
    # the target-table match absorbs cross-batch / restart redelivery.
    return live_orders(
        spark.readStream.table("eventhub_parsed"), watermark_delay
    )


# MERGE contract for the flow above. Declarative in SDP; the local test
# harness applies the equivalent clauses in foreachBatch (see
# _shared.order_ingest.merge_orders_batch):
#   ON      t.order_id = s.order_id
#   WHEN MATCHED     THEN UPDATE SET *   (redelivery refreshes the row)
#   WHEN NOT MATCHED THEN INSERT *        (first sighting)


@dp.append_flow(
    target="restaurant_chain_db.01_bronze.orders",
    once=True,
    comment="One-time backfill of historical SQL Server orders",
)
def orders_from_historical():
    """Historical orders — runs exactly once, re-runs only on full refresh.

    Unchanged backfill semantics: plain append, no dedup/MERGE, executed once.
    """
    return (
        spark.read
        .table("restaurant_chain_db.00_landing.historical_orders")
        .withColumn("items_parsed", from_json(col("items"), ArrayType(ITEM_SCHEMA)))
        .select(
            col("order_id"),
            col("order_timestamp"),
            col("restaurant_id"),
            col("customer_id"),
            col("order_type"),
            col("items_parsed").alias("items"),
            expr(
                "ROUND(AGGREGATE(items_parsed, CAST(0.0 AS DOUBLE), "
                "(acc, x) -> acc + x.subtotal), 2)"
            ).alias("total_amount"),
            col("payment_method"),
            col("order_status"),
            lit("historical").alias("source"),
            current_timestamp().alias("loaded_at"),
        )
    )
