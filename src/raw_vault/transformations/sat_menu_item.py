"""Raw Vault · Satellite — Menu Item

Tracks the full history of menu item attributes (price, ingredients, etc.).
Particularly useful for detecting price changes over time.

Grain: one row per (hk_menu_item, hash_diff).
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.sat_menu_item",
    comment="Satellite — menu item attribute history (incl. price changes)",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_menu_item"],
)
@dp.expect_all_or_fail(
    {
        "fk_not_null": "hk_menu_item IS NOT NULL",
        "hash_diff_not_null": "hash_diff IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def sat_menu_item():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_menu_items")
        .groupBy(
            "hk_menu_item",
            "hash_diff",
            "name",
            "category",
            "price",
            "ingredients",
            "is_vegetarian",
            "spice_level",
        )
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
        .select(
            "hk_menu_item", "hash_diff",
            "load_dts", "record_source",
            "name", "category", "price",
            "ingredients", "is_vegetarian", "spice_level",
        )
    )
