"""Marts · Aggregate — Business Monthly Base

Equivalent to gold_serving_analytics.business_monthly_base.
Rolls up daily restaurant performance to monthly grain.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.agg_business_monthly_base",
    comment="Monthly business performance base — aggregates daily to monthly grain",
    table_properties={"quality": "marts"},
)
@dp.expect("valid_orders", "total_orders > 0 AND total_revenue >= 0")
def agg_business_monthly_base():
    return (
        spark.read.table(f"{CATALOG_SCHEMA}.agg_restaurant_performance_daily")
        .groupBy("restaurant_id", "restaurant_name", "restaurant_city", "order_year", "order_month")
        .agg(
            F.sum("total_orders").alias("total_orders"),
            F.round(F.sum("total_revenue"), 2).alias("total_revenue"),
            F.round(F.sum("total_revenue") / F.sum("total_orders"), 2).alias("avg_order_value"),
            F.sum("unique_customers").alias("unique_customers"),
            F.sum("dine_in_orders").alias("dine_in_orders"),
            F.sum("takeaway_orders").alias("takeaway_orders"),
            F.sum("delivery_orders").alias("delivery_orders"),
            F.sum("total_items_sold").alias("total_items_sold"),
        )
    )
