"""Marts · Fact — Order Item (Line-Level)

One row per item per order. Joins bridge_order_item (full relationship
chain) with pit_order_item (line-item payload) and pit_order
(order-header context).

Equivalent to the existing gold _order_items_enriched + fact_order_items.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.marts"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.fact_order_item",
    comment="Order line-item fact — one row per item per order with full context",
    table_properties={
        "quality": "marts",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["order_date", "restaurant_id"],
)
@dp.expect_all_or_drop(
    {
        "valid_order_id": "order_id IS NOT NULL",
        "valid_item_id": "item_id IS NOT NULL",
        "valid_restaurant_id": "restaurant_id IS NOT NULL",
    }
)
@dp.expect("valid_quantity", "quantity > 0")
@dp.expect("valid_unit_price", "unit_price >= 0")
def fact_order_item():
    bridge = spark.read.table("restaurant_chain_db.business_vault.bridge_order_item")
    pit_oi = (
        spark.read.table("restaurant_chain_db.business_vault.pit_order_item")
        .filter("effective_to IS NULL")
    )
    pit_ord = (
        spark.read.table("restaurant_chain_db.business_vault.pit_order")
        .filter("effective_to IS NULL")
        .select("hk_order", "order_timestamp", "order_type", "payment_method", "order_status")
    )

    # Denormalized dim names
    dim_rest = spark.read.table(f"{CATALOG_SCHEMA}.dim_restaurant").select(
        "restaurant_id", "restaurant_name", "restaurant_city"
    )
    dim_menu = spark.read.table(f"{CATALOG_SCHEMA}.dim_menu_item").select(
        "restaurant_id", "item_id",
        F.col("price").alias("current_price"),
        "is_vegetarian", "spice_level",
    )

    return (
        bridge
        .join(pit_oi, "hk_lnk_order_item", "inner")
        .join(pit_ord, "hk_order", "inner")
        .join(dim_rest, "restaurant_id", "left")
        .join(dim_menu, ["restaurant_id", "item_id"], "left")
        .select(
            "order_id",
            "restaurant_id",
            "customer_id",
            "item_id",
            "restaurant_name",
            "restaurant_city",
            F.to_date("order_timestamp").alias("order_date"),
            F.year("order_timestamp").alias("order_year"),
            F.month("order_timestamp").alias("order_month"),
            "order_type",
            "payment_method",
            "order_status",
            "line_number",
            "item_name",
            "item_category",
            "quantity",
            "unit_price",
            "subtotal",
            "current_price",
            "is_vegetarian",
            "spice_level",
        )
    )
