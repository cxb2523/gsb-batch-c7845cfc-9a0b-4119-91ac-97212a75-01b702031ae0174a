"""Marts · Aggregate — Customer 360

Equivalent to gold_serving_analytics.customer_360.
Sourced from marts facts instead of silver dims/facts.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.agg_customer_360",
    comment="Customer 360 profile — lifetime metrics, preferences, satisfaction signals",
    table_properties={"quality": "marts"},
    cluster_by=["customer_city"],
)
@dp.expect("valid_orders", "total_orders > 0")
@dp.expect("valid_spend", "total_spend >= 0")
def agg_customer_360():
    orders = spark.read.table(f"{CATALOG_SCHEMA}.fact_order")
    items = spark.read.table(f"{CATALOG_SCHEMA}.fact_order_item")
    reviews = spark.read.table(f"{CATALOG_SCHEMA}.fact_review")

    order_metrics = (
        orders
        .groupBy("customer_id", "customer_city", "customer_join_date")
        .agg(
            F.count("*").alias("total_orders"),
            F.round(F.sum("total_amount"), 2).alias("total_spend"),
            F.round(F.avg("total_amount"), 2).alias("avg_order_value"),
            F.min("order_date").alias("first_order_date"),
            F.max("order_date").alias("last_order_date"),
            F.datediff(F.max("order_date"), F.min("order_date")).alias("customer_tenure_days"),
            F.countDistinct("restaurant_id").alias("distinct_restaurants_visited"),
            F.mode("restaurant_id").alias("favorite_restaurant_id"),
            F.mode("order_type").alias("favorite_order_type"),
            F.mode("payment_method").alias("favorite_payment_method"),
            F.round(
                F.avg(F.when(F.col("is_weekend"), 1).otherwise(0)) * 100, 1
            ).alias("weekend_order_pct"),
            F.round(
                F.avg(F.when(F.col("order_hour").between(10, 15), 1).otherwise(0)) * 100, 1
            ).alias("peak_hours_order_pct"),
        )
    )

    item_metrics = (
        items
        .groupBy("customer_id")
        .agg(
            F.countDistinct("item_id").alias("distinct_items_tried"),
            F.round(F.avg("quantity"), 2).alias("avg_qty_per_item"),
        )
    )

    review_metrics = (
        reviews
        .groupBy("customer_id")
        .agg(
            F.count("*").alias("review_count"),
            F.round(F.avg("rating"), 2).alias("avg_rating"),
            F.last("sentiment", ignorenulls=True).alias("latest_sentiment"),
        )
    )

    return (
        order_metrics
        .join(item_metrics, "customer_id", "left")
        .join(review_metrics, "customer_id", "left")
        .fillna({"review_count": 0})
    )
