"""Marts · Aggregate — Menu Item Performance Monthly

Equivalent to gold_serving_analytics.menu_item_performance_monthly.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.agg_menu_item_performance_monthly",
    comment="Monthly menu item performance — marketing, menu engineering, pricing strategy",
    table_properties={"quality": "marts"},
)
@dp.expect("valid_qty", "total_qty_sold > 0")
@dp.expect("valid_revenue", "total_revenue >= 0")
def agg_menu_item_performance_monthly():
    return (
        spark.read.table(f"{CATALOG_SCHEMA}.fact_order_item")
        .groupBy(
            "restaurant_id",
            "restaurant_name",
            "restaurant_city",
            "item_id",
            "item_name",
            "item_category",
            "is_vegetarian",
            "spice_level",
            "current_price",
            "order_year",
            "order_month",
        )
        .agg(
            F.sum("quantity").alias("total_qty_sold"),
            F.round(F.sum("subtotal"), 2).alias("total_revenue"),
            F.countDistinct("order_id").alias("unique_orders"),
            F.round(F.avg("quantity"), 2).alias("avg_qty_per_order"),
        )
    )
