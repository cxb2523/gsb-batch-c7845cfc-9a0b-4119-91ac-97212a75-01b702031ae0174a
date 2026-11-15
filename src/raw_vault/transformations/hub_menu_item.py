"""Raw Vault · Hub — Menu Item

One row per unique (restaurant_id, item_id) pair.
Menu items are restaurant-scoped: the same item_id at different
restaurants represents distinct business entities.

Columns:
  hk_menu_item   : SHA-256 hash of (SALT || restaurant_id || item_id)  — PK
  restaurant_id  : Part of composite business key
  item_id        : Part of composite business key
  load_dts       : Earliest observed load timestamp
  record_source  : Source that first introduced this key
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.hub_menu_item",
    comment="Hub — unique menu-item business keys (restaurant-scoped)",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_menu_item"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_menu_item IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_menu_item) = 64",
        "bk_restaurant_not_null": "restaurant_id IS NOT NULL",
        "bk_item_not_null": "item_id IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def hub_menu_item():
    stg_menu = spark.read.table("restaurant_chain_db.staging.stg_menu_items").select(
        "hk_menu_item", "restaurant_id", "item_id", "load_dts", "record_source"
    )
    stg_oi = spark.read.table("restaurant_chain_db.staging.stg_order_items").select(
        "hk_menu_item", "restaurant_id", "item_id", "load_dts", "record_source"
    )

    return (
        stg_menu.unionByName(stg_oi)
        .groupBy("hk_menu_item", "restaurant_id", "item_id")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
