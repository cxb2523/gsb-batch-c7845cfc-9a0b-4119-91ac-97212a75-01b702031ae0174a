"""Marts · Aggregate — Menu Item Ranked Monthly

Equivalent to gold_serving_analytics.menu_item_ranked_monthly.
Revenue rank and percentage share per restaurant-month.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.agg_menu_item_ranked_monthly",
    comment="Menu item revenue ranking per restaurant-month — ABC analysis",
    table_properties={"quality": "marts"},
)
def agg_menu_item_ranked_monthly():
    w = Window.partitionBy("restaurant_id", "order_year", "order_month").orderBy(F.desc("total_revenue"))
    w_total = Window.partitionBy("restaurant_id", "order_year", "order_month")

    return (
        spark.read.table(f"{CATALOG_SCHEMA}.agg_menu_item_performance_monthly")
        .withColumn("revenue_rank_in_restaurant", F.rank().over(w))
        .withColumn(
            "pct_of_restaurant_revenue",
            F.round(F.col("total_revenue") / F.sum("total_revenue").over(w_total) * 100, 2),
        )
    )
