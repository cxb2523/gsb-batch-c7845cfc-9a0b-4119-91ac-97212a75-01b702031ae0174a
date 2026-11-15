"""Marts · Aggregate — Restaurant Performance Daily

Equivalent to gold_serving_analytics.restaurant_performance_daily.
Sourced from marts.fact_order instead of silver fact_orders + dim joins.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.agg_restaurant_performance_daily",
    comment="Daily restaurant performance — operations, executive, and finance dashboards",
    table_properties={"quality": "marts"},
    cluster_by=["order_date"],
)
@dp.expect("valid_orders", "total_orders > 0")
@dp.expect("valid_revenue", "total_revenue >= 0")
def agg_restaurant_performance_daily():
    return (
        spark.read.table(f"{CATALOG_SCHEMA}.fact_order")
        .groupBy(
            "restaurant_id",
            "restaurant_name",
            "restaurant_city",
            "order_date",
            "order_year",
            "order_quarter",
            "order_month",
            "order_day_of_week",
            "is_weekend",
        )
        .agg(
            F.count("*").alias("total_orders"),
            F.round(F.sum("total_amount"), 2).alias("total_revenue"),
            F.round(F.avg("total_amount"), 2).alias("avg_order_value"),
            F.countDistinct("customer_id").alias("unique_customers"),
            F.sum(F.when(F.col("order_type") == "dine_in", 1).otherwise(0)).alias("dine_in_orders"),
            F.sum(F.when(F.col("order_type") == "takeaway", 1).otherwise(0)).alias("takeaway_orders"),
            F.sum(F.when(F.col("order_type") == "delivery", 1).otherwise(0)).alias("delivery_orders"),
            F.sum(F.when(F.col("payment_method") == "card", 1).otherwise(0)).alias("card_orders"),
            F.sum(F.when(F.col("payment_method") == "cash", 1).otherwise(0)).alias("cash_orders"),
            F.sum(F.when(F.col("payment_method") == "wallet", 1).otherwise(0)).alias("wallet_orders"),
            F.sum("item_count").alias("total_items_sold"),
            F.round(F.avg("item_count"), 1).alias("avg_items_per_order"),
        )
    )
