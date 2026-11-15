"""Marts · Features — Restaurant Demand Features (ML-Ready)

Equivalent to gold_serving_analytics.restaurant_demand_features.
Lag features, rolling averages, WoW change, channel mix ratios.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.feat_restaurant_demand_features",
    comment="ML-ready demand forecasting features — lag, rolling, WoW, channel mix",
    table_properties={"quality": "marts"},
    cluster_by=["order_date"],
)
@dp.expect("valid_orders", "total_orders > 0")
def feat_restaurant_demand_features():
    daily = (
        spark.read.table(f"{CATALOG_SCHEMA}.agg_restaurant_performance_daily")
        .select(
            "restaurant_id", "order_date", "order_year", "order_month",
            "order_day_of_week", "is_weekend",
            "total_orders", "total_revenue", "unique_customers",
            "total_items_sold", "dine_in_orders", "takeaway_orders", "delivery_orders",
        )
    )

    w = Window.partitionBy("restaurant_id").orderBy("order_date")

    return (
        daily
        .withColumn("orders_lag_1d", F.lag("total_orders", 1).over(w))
        .withColumn("orders_lag_7d", F.lag("total_orders", 7).over(w))
        .withColumn("revenue_lag_1d", F.lag("total_revenue", 1).over(w))
        .withColumn("revenue_lag_7d", F.lag("total_revenue", 7).over(w))
        .withColumn("orders_rolling_7d_avg", F.round(F.avg("total_orders").over(w.rowsBetween(-6, 0)), 1))
        .withColumn("orders_rolling_14d_avg", F.round(F.avg("total_orders").over(w.rowsBetween(-13, 0)), 1))
        .withColumn("revenue_rolling_7d_avg", F.round(F.avg("total_revenue").over(w.rowsBetween(-6, 0)), 2))
        .withColumn(
            "orders_wow_change",
            F.when(F.col("orders_lag_7d") > 0,
                   F.round((F.col("total_orders") - F.col("orders_lag_7d")) / F.col("orders_lag_7d"), 4)),
        )
        .withColumn("delivery_ratio", F.round(F.col("delivery_orders") / F.col("total_orders"), 4))
        .withColumn("dine_in_ratio", F.round(F.col("dine_in_orders") / F.col("total_orders"), 4))
    )
