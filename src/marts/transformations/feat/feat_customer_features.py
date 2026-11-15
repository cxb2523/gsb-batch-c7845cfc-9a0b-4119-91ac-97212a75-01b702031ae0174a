"""Marts · Features — Customer Features (ML-Ready)

Equivalent to gold_serving_analytics.customer_features.
RFM, behavioral, time patterns, and satisfaction features
for churn prediction, CLV forecasting, customer segmentation.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.feat_customer_features",
    comment="ML-ready customer features — RFM, behavioral, satisfaction",
    table_properties={
        "quality": "marts",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["customer_id"],
)
@dp.expect("valid_frequency", "frequency > 0")
@dp.expect("valid_monetary", "monetary >= 0")
def feat_customer_features():
    orders = spark.read.table(f"{CATALOG_SCHEMA}.fact_order")
    items = spark.read.table(f"{CATALOG_SCHEMA}.fact_order_item")
    reviews = spark.read.table(f"{CATALOG_SCHEMA}.fact_review")

    max_date = F.current_date()  # Always compute features relative to today

    order_features = (
        orders
        .groupBy("customer_id")
        .agg(
            F.datediff(max_date, F.max("order_date")).alias("recency_days"),
            F.count("*").alias("frequency"),
            F.round(F.sum("total_amount"), 2).alias("monetary"),
            F.sum(F.when(F.datediff(max_date, F.col("order_date")) <= 30, 1).otherwise(0)).alias("orders_last_30d"),
            F.sum(F.when(F.datediff(max_date, F.col("order_date")) <= 90, 1).otherwise(0)).alias("orders_last_90d"),
            F.round(F.sum(F.when(F.datediff(max_date, F.col("order_date")) <= 30, F.col("total_amount")).otherwise(0)), 2).alias("spend_last_30d"),
            F.round(F.sum(F.when(F.datediff(max_date, F.col("order_date")) <= 90, F.col("total_amount")).otherwise(0)), 2).alias("spend_last_90d"),
            F.round(F.avg("total_amount"), 2).alias("avg_order_value"),
            F.round(F.avg("item_count"), 2).alias("avg_items_per_order"),
            F.countDistinct("restaurant_id").alias("distinct_restaurants"),
            F.round(F.avg(F.when(F.col("is_weekend"), 1).otherwise(0)), 4).alias("weekend_order_ratio"),
            F.round(F.avg(F.when(F.col("order_hour").between(10, 15), 1).otherwise(0)), 4).alias("peak_hours_ratio"),
            F.round(F.avg(F.when(F.col("order_type") == "dine_in", 1).otherwise(0)), 4).alias("dine_in_ratio"),
            F.round(F.avg(F.when(F.col("order_type") == "takeaway", 1).otherwise(0)), 4).alias("takeaway_ratio"),
            F.round(F.avg(F.when(F.col("order_type") == "delivery", 1).otherwise(0)), 4).alias("delivery_ratio"),
            F.round(F.avg(F.when(F.col("payment_method") == "card", 1).otherwise(0)), 4).alias("card_ratio"),
            F.round(F.avg(F.when(F.col("payment_method") == "cash", 1).otherwise(0)), 4).alias("cash_ratio"),
            F.round(F.avg(F.when(F.col("payment_method") == "wallet", 1).otherwise(0)), 4).alias("wallet_ratio"),
            F.datediff(F.max("order_date"), F.min("order_date")).alias("customer_tenure_days"),
            F.when(
                F.count("*") > 1,
                F.round(F.datediff(F.max("order_date"), F.min("order_date")) / (F.count("*") - 1), 1),
            ).otherwise(F.lit(None)).alias("avg_days_between_orders"),
        )
    )

    item_features = (
        items
        .groupBy("customer_id")
        .agg(F.countDistinct("item_id").alias("distinct_items_tried"))
    )

    review_features = (
        reviews
        .groupBy("customer_id")
        .agg(
            F.count("*").alias("review_count"),
            F.round(F.avg("rating"), 2).alias("avg_rating"),
            F.round(F.avg(F.when(F.col("sentiment") == "positive", 1).otherwise(0)), 4).alias("positive_review_ratio"),
            F.max(
                F.when(
                    (F.col("issue_delivery") == "true")
                    | (F.col("issue_food_quality") == "true")
                    | (F.col("issue_pricing") == "true")
                    | (F.col("issue_portion_size") == "true"),
                    1,
                ).otherwise(0)
            ).alias("has_any_issue"),
        )
    )

    return (
        order_features
        .join(item_features, "customer_id", "left")
        .join(review_features, "customer_id", "left")
        .fillna({
            "review_count": 0,
            "avg_rating": 0.0,
            "positive_review_ratio": 0.0,
            "has_any_issue": 0,
            "distinct_items_tried": 0,
        })
    )
