"""Marts · Fact — Review (with AI Sentiment)

One row per review. Joins pit_review (review attributes) with
sat_review_sentiment (AI enrichment) and resolves customer/restaurant
context via lnk_review_order → bridge_order.

Ghost record handling: Reviews referencing non-existent orders get
customer_id/restaurant_id = 'UNKNOWN' via COALESCE + LEFT JOIN.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.fact_review",
    comment="Review fact — one row per review with AI sentiment and order/customer/restaurant context",
    table_properties={
        "quality": "marts",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["review_date"],
)
@dp.expect_all_or_drop(
    {
        "valid_review_id": "review_id IS NOT NULL",
        "valid_order_id": "order_id IS NOT NULL",
    }
)
@dp.expect("valid_rating", "rating BETWEEN 1 AND 5")
@dp.expect("valid_sentiment", "sentiment IN ('positive', 'neutral', 'negative')")
def fact_review():
    pit_rev = (
        spark.read.table("restaurant_chain_db.business_vault.pit_review")
        .filter("effective_to IS NULL")
    )
    hub_rev = spark.read.table("restaurant_chain_db.raw_vault.hub_review").select("hk_review", "review_id")
    sentiment = spark.read.table("restaurant_chain_db.business_vault.sat_review_sentiment").select(
        "hk_review", "sentiment",
        "issue_delivery", "issue_delivery_reason",
        "issue_food_quality", "issue_food_quality_reason",
        "issue_pricing", "issue_pricing_reason",
        "issue_portion_size", "issue_portion_size_reason",
    )

    # Resolve review → order → customer/restaurant via link + bridge
    lnk_ro = spark.read.table("restaurant_chain_db.raw_vault.lnk_review_order").select(
        "hk_review", "hk_order"
    )
    hub_ord = spark.read.table("restaurant_chain_db.raw_vault.hub_order").select("hk_order", "order_id")
    bridge = spark.read.table("restaurant_chain_db.business_vault.bridge_order").select(
        "hk_order", "customer_id", "restaurant_id"
    )

    # Denormalized restaurant name
    dim_rest = spark.read.table(f"{CATALOG_SCHEMA}.dim_restaurant").select(
        "restaurant_id", "restaurant_name", "restaurant_city"
    )

    return (
        pit_rev
        .join(hub_rev, "hk_review", "inner")
        .join(sentiment, "hk_review", "left")
        .join(lnk_ro, "hk_review", "inner")
        .join(hub_ord, "hk_order", "inner")
        # LEFT JOIN bridge — orphan reviews (order not in bridge) get NULL customer/restaurant
        .join(bridge, "hk_order", "left")
        .join(dim_rest, "restaurant_id", "left")
        # Ghost record handling: COALESCE NULLs to 'UNKNOWN'
        .select(
            "review_id",
            "order_id",
            F.coalesce("customer_id", F.lit("UNKNOWN")).alias("customer_id"),
            F.coalesce("restaurant_id", F.lit("UNKNOWN")).alias("restaurant_id"),
            F.coalesce("restaurant_name", F.lit("Unknown")).alias("restaurant_name"),
            F.coalesce("restaurant_city", F.lit("Unknown")).alias("restaurant_city"),
            "rating",
            "review_text",
            "review_timestamp",
            F.to_date("review_timestamp").alias("review_date"),
            F.year("review_timestamp").alias("review_year"),
            F.month("review_timestamp").alias("review_month"),
            F.quarter("review_timestamp").alias("review_quarter"),
            F.dayofweek("review_timestamp").alias("review_day_of_week"),
            F.hour("review_timestamp").alias("review_hour"),
            F.dayofweek("review_timestamp").isin(1, 7).alias("is_weekend"),
            "sentiment",
            "issue_delivery", "issue_delivery_reason",
            "issue_food_quality", "issue_food_quality_reason",
            "issue_pricing", "issue_pricing_reason",
            "issue_portion_size", "issue_portion_size_reason",
        )
    )
