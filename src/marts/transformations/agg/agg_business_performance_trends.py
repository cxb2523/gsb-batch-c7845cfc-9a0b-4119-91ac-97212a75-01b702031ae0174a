"""Marts · Aggregate — Business Performance Trends

Equivalent to gold_serving_analytics.business_performance_trends.
MoM % change, rolling 3-month averages, and YTD cumulative.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.agg_business_performance_trends",
    comment="Monthly business trends with MoM % change, rolling 3-month averages, YTD",
    table_properties={"quality": "marts"},
)
@dp.expect("valid_orders", "total_orders > 0 AND total_revenue >= 0")
def agg_business_performance_trends():
    monthly = spark.read.table(f"{CATALOG_SCHEMA}.agg_business_monthly_base")

    w_lag = Window.partitionBy("restaurant_id").orderBy("order_year", "order_month")
    w_roll = Window.partitionBy("restaurant_id").orderBy("order_year", "order_month").rowsBetween(-2, 0)

    return (
        monthly
        .withColumn("prev_month_revenue", F.lag("total_revenue", 1).over(w_lag))
        .withColumn("prev_month_orders", F.lag("total_orders", 1).over(w_lag))
        .withColumn(
            "revenue_mom_pct",
            F.when(F.col("prev_month_revenue") > 0,
                   F.round((F.col("total_revenue") - F.col("prev_month_revenue")) / F.col("prev_month_revenue") * 100, 2)),
        )
        .withColumn(
            "orders_mom_pct",
            F.when(F.col("prev_month_orders") > 0,
                   F.round((F.col("total_orders") - F.col("prev_month_orders")) / F.col("prev_month_orders") * 100, 2)),
        )
        .withColumn("revenue_rolling_3mo_avg", F.round(F.avg("total_revenue").over(w_roll), 2))
        .withColumn("orders_rolling_3mo_avg", F.round(F.avg("total_orders").over(w_roll), 1))
        .withColumn(
            "ytd_revenue",
            F.round(F.sum("total_revenue").over(
                Window.partitionBy("restaurant_id", "order_year").orderBy("order_month").rowsBetween(Window.unboundedPreceding, 0)
            ), 2),
        )
        .drop("prev_month_revenue", "prev_month_orders")
    )
