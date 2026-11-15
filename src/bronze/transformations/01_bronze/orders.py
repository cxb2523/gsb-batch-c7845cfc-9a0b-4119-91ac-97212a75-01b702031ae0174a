"""Bronze · Unified Orders

Conformed orders merging two sources via append flows:
  - Live:       Incremental EventHub stream (from eventhub_parsed temp view)
  - Historical: One-time batch backfill from landing historical_orders

Data notes:
  - Historical range: 2025-07-15 → 2026-01-11
  - EventHub live from: 2026-04-09
  - Gap: ~3 months (Jan 12 – Apr 8) with no order data
  - Zero order_id overlap between sources confirmed
  - EventHub has at-least-once delivery; dedup deferred to downstream
"""
from pyspark import pipelines as dp
from pyspark.sql.functions import col, current_timestamp, expr, from_json, lit, to_timestamp
from pyspark.sql.types import (
    ArrayType,
    DoubleType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

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
    comment="Unified orders — live EventHub stream merged with one-time historical backfill",
    table_properties={"quality": "bronze"},
)


@dp.append_flow(target="restaurant_chain_db.01_bronze.orders")
def orders_from_eventhub():
    """Live orders streamed from EventHub — processes incrementally."""
    return (
        spark.readStream
        .table("eventhub_parsed")
        .select(
            col("order_id"),
            to_timestamp(col("timestamp")).alias("order_timestamp"),
            col("restaurant_id"),
            col("customer_id"),
            col("order_type"),
            col("items"),
            col("total_amount"),
            col("payment_method"),
            col("order_status"),
            lit("live").alias("source"),
            current_timestamp().alias("loaded_at"),
        )
    )


@dp.append_flow(
    target="restaurant_chain_db.01_bronze.orders",
    once=True,
    comment="One-time backfill of historical SQL Server orders",
)
def orders_from_historical():
    """Historical orders — runs exactly once, re-runs only on full refresh."""
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
