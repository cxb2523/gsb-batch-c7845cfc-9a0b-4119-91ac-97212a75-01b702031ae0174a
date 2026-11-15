"""Marts · Fact — Order Header

One row per order. Joins bridge_order (relationship keys) with
pit_order (current attribute version). Enriches with restaurant
and customer names from dims for denormalized analytics.

Equivalent to the existing gold _orders_enriched temp view but
with full DV lineage and DQ expectations.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.fact_order",
    comment="Order fact — one row per order with denormalized restaurant/customer context",
    table_properties={
        "quality": "marts",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["order_date"],
)
@dp.expect_all_or_drop(
    {
        "valid_order_id": "order_id IS NOT NULL",
        "valid_customer_id": "customer_id IS NOT NULL",
        "valid_restaurant_id": "restaurant_id IS NOT NULL",
        "valid_order_timestamp": "order_timestamp IS NOT NULL",
    }
)
@dp.expect("valid_total_amount", "total_amount >= 0")
@dp.expect("valid_order_type", "order_type IN ('dine_in', 'takeaway', 'delivery')")
@dp.expect("valid_payment_method", "payment_method IN ('card', 'cash', 'wallet')")
def fact_order():
    bridge = spark.read.table("restaurant_chain_db.business_vault.bridge_order")
    pit_ord = (
        spark.read.table("restaurant_chain_db.business_vault.pit_order")
        .filter("effective_to IS NULL")
    )

    # Denormalized restaurant/customer names for analytics
    dim_rest = spark.read.table(f"{CATALOG_SCHEMA}.dim_restaurant").select(
        "restaurant_id", "restaurant_name", "restaurant_city"
    )
    dim_cust = spark.read.table(f"{CATALOG_SCHEMA}.dim_customer").select(
        "customer_id", "customer_city", F.col("join_date").alias("customer_join_date")
    )

    # Item count per order from bridge_order_item
    item_counts = (
        spark.read.table("restaurant_chain_db.business_vault.bridge_order_item")
        .groupBy("hk_order")
        .agg(F.count("*").alias("item_count"))
    )

    return (
        bridge
        .join(pit_ord, "hk_order", "inner")
        .join(dim_rest, "restaurant_id", "left")
        .join(dim_cust, "customer_id", "left")
        .join(item_counts, "hk_order", "left")
        .select(
            "order_id",
            "customer_id",
            "restaurant_id",
            "restaurant_name",
            "restaurant_city",
            "customer_city",
            "customer_join_date",
            F.to_date("order_timestamp").alias("order_date"),
            F.year("order_timestamp").alias("order_year"),
            F.quarter("order_timestamp").alias("order_quarter"),
            F.month("order_timestamp").alias("order_month"),
            F.dayofweek("order_timestamp").alias("order_day_of_week"),
            F.hour("order_timestamp").alias("order_hour"),
            F.dayofweek("order_timestamp").isin(1, 7).alias("is_weekend"),
            "order_timestamp",
            "order_type",
            "total_amount",
            "payment_method",
            "order_status",
            F.coalesce("item_count", F.lit(0)).alias("item_count"),
        )
    )
