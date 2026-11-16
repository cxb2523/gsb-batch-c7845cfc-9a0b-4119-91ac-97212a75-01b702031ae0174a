"""Streaming parse / reject / dedup logic for Event Hub order messages.

Everything here is plain PySpark (no SDP decorators) so the exact same logic
runs inside Lakeflow SDP and inside the local pytest harness.

Design
------
1. :func:`split_parsed_orders` parses each raw landing record with
   ``from_json`` and routes it deterministically:

   * malformed JSON payload                 -> rejection reason ``malformed_json``
   * parsed but ``order_id`` null/blank     -> rejection reason ``missing_order_id``
   * otherwise                              -> parsed order stream

   Bad records are *explicitly* split off instead of relying on a soft
   expectation, so nothing is silently dropped and bad messages never reach the
   ``orders`` table.

2. :func:`rejection_records` projects bad records into the persisted
   ``01_bronze.order_rejections`` table (raw value, event_time,
   ingestion_time, reason).

3. :func:`live_orders` projects good records into the ``orders`` schema and
   applies *streaming* deduplication on ``order_id`` — a stateful
   ``dropDuplicates`` behind a watermark, evaluated per micro-batch with no
   full-table recomputation. In production the SDP ``merge_flow`` then enforces
   the same one-row-per-order_id guarantee against the target table, absorbing
   duplicates redelivered across micro-batches or pipeline restarts.
"""
from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql.functions import (
    col,
    current_timestamp,
    from_json,
    lit,
    to_timestamp,
    trim,
)
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
    TimestampType,
)

# Rejection reason vocabulary — keep stable, it is persisted and queried.
REASON_MALFORMED_JSON = "malformed_json"
REASON_MISSING_ORDER_ID = "missing_order_id"

# How late a redelivered message may arrive relative to the newest seen
# event_time and still be collapsed by the streaming state store. Cross-batch
# duplicates older than this are still absorbed by the target-table MERGE.
DEFAULT_WATERMARK_DELAY = "24 hours"

ITEM_SCHEMA = StructType([
    StructField("item_id", StringType()),
    StructField("name", StringType()),
    StructField("category", StringType()),
    StructField("quantity", IntegerType()),
    StructField("unit_price", DoubleType()),
    StructField("subtotal", DoubleType()),
])

# Column name used to capture unparseable payloads via from_json's PERMISSIVE
# mode (columnNameOfCorruptRecord). It must be the first field of the schema.
CORRUPT_RECORD_COL = "_corrupt_record"

ORDER_SCHEMA = StructType([
    StructField(CORRUPT_RECORD_COL, StringType(), True),
    StructField("order_id", StringType()),
    StructField("timestamp", StringType()),
    StructField("restaurant_id", StringType()),
    StructField("customer_id", StringType()),
    StructField("order_type", StringType()),
    StructField("items", ArrayType(ITEM_SCHEMA)),
    StructField("total_amount", DoubleType()),
    StructField("payment_method", StringType()),
    StructField("order_status", StringType()),
    StructField("created_at", StringType()),
])

# Order fields (excluding the internal corrupt-record column) — the shape of
# the good records handed to downstream consumers.
ORDER_FIELD_NAMES = [f.name for f in ORDER_SCHEMA.fields
                     if f.name != CORRUPT_RECORD_COL]

# Persisted shape of 01_bronze.order_rejections.
REJECTION_SCHEMA = StructType([
    StructField("raw_value", StringType(), True),
    StructField("event_time", TimestampType(), True),
    StructField("ingestion_time", TimestampType(), True),
    StructField("rejection_reason", StringType(), False),
    StructField("rejected_at", TimestampType(), False),
])


def split_parsed_orders(raw_stream: DataFrame):
    """Parse raw landing messages and return ``(good, rejected)`` streams.

    ``good`` has the same column shape as the original ``eventhub_parsed``
    temporary view (the parsed order fields plus ``event_time`` and
    ``ingestion_time``) so downstream consumers are unchanged.

    ``rejected`` carries the original string ``value`` plus the two landing
    timestamps and a non-null ``rejection_reason``.

    Two distinct failure modes are classified:
      * unparseable JSON -> the corrupt-record column is populated ->
        ``malformed_json``;
      * valid JSON whose order_id is null or blank -> ``missing_order_id``.
    """
    parsed = (
        raw_stream.withColumn(
            "parsed",
            from_json(
                col("value"),
                ORDER_SCHEMA,
                {"columnNameOfCorruptRecord": CORRUPT_RECORD_COL},
            ),
        )
        .select(
            col("value").alias("raw_value"),
            col("event_time"),
            col("ingestion_time"),
            col("parsed"),
        )
    )

    # PERMISSIVE from_json fills the corrupt-record field with the original
    # payload when the JSON cannot be parsed; well-formed records leave it null.
    malformed = parsed.filter(
        col(f"parsed.{CORRUPT_RECORD_COL}").isNotNull()
    ).withColumn("rejection_reason", lit(REASON_MALFORMED_JSON))

    structurally_valid = parsed.filter(
        col(f"parsed.{CORRUPT_RECORD_COL}").isNull()
    )

    missing_id = structurally_valid.filter(
        col("parsed.order_id").isNull()
        | (trim(col("parsed.order_id")) == lit(""))
    ).withColumn("rejection_reason", lit(REASON_MISSING_ORDER_ID))

    good = structurally_valid.filter(
        col("parsed.order_id").isNotNull()
        & (trim(col("parsed.order_id")) != lit(""))
    ).select(*[col(f"parsed.{name}").alias(name) for name in ORDER_FIELD_NAMES],
             "event_time", "ingestion_time")

    rejected = malformed.unionByName(missing_id).select(
        "raw_value", "event_time", "ingestion_time", "rejection_reason"
    )
    return good, rejected


def rejection_records(rejected_stream: DataFrame) -> DataFrame:
    """Shape rejected records for the persisted ``order_rejections`` table."""
    return rejected_stream.select(
        col("raw_value"),
        col("event_time"),
        col("ingestion_time"),
        col("rejection_reason"),
        current_timestamp().alias("rejected_at"),
    )


def live_orders(
    parsed_stream: DataFrame,
    watermark_delay: str = DEFAULT_WATERMARK_DELAY,
) -> DataFrame:
    """Project parsed orders into the bronze ``orders`` schema and dedup.

    Deduplication is streaming and incremental: a watermark on the Event Hub
    ``event_time`` bounds the state store, while ``dropDuplicates("order_id")``
    collapses repeated deliveries within the watermark window per micro-batch.
    No full-table scan or full recomputation is involved. The production
    ``merge_flow`` adds the durable, cross-restart guarantee on the target.
    """
    selected = parsed_stream.select(
        col("order_id"),
        to_timestamp(col("timestamp")).alias("order_timestamp"),
        col("restaurant_id"),
        col("customer_id"),
        col("order_type"),
        col("items"),
        col("total_amount"),
        col("payment_method"),
        col("order_status"),
        col("event_time"),
        current_timestamp().alias("loaded_at"),
    )

    deduped = (
        selected.withWatermark("event_time", watermark_delay)
        .dropDuplicates(["order_id"])
        .drop("event_time")
        .withColumn("source", lit("live"))
    )

    # Final column order matches the historical append flow / existing table.
    return deduped.select(
        "order_id",
        "order_timestamp",
        "restaurant_id",
        "customer_id",
        "order_type",
        "items",
        "total_amount",
        "payment_method",
        "order_status",
        "source",
        "loaded_at",
    )


def merge_orders_batch(batch_df: DataFrame, batch_id: int, target_table: str):
    """``foreachBatch`` MERGE emulating the SDP ``merge_flow`` locally.

    Idempotent upsert keyed on ``order_id``: duplicates delivered in any
    micro-batch (or in a re-run after a crash) update the existing row instead
    of inserting a second copy. Used only by the local test harness; in SDP the
    declarative ``@dp.merge_flow`` performs the equivalent operation.
    """
    from delta.tables import DeltaTable
    from pyspark.sql import SparkSession

    spark = SparkSession.getActiveSession()
    target = DeltaTable.forName(spark, target_table)
    (
        target.alias("t")
        .merge(batch_df.alias("s"), "t.order_id = s.order_id")
        .whenMatchedUpdateAll()
        .whenNotMatchedInsertAll()
        .execute()
    )
