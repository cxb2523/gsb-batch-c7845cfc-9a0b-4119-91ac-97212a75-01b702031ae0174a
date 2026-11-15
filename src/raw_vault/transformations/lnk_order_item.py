"""Raw Vault · Link — Order Item

Models the relationship: order ↔ menu_item at the line-item grain.
One row per unique combination of (order_id, restaurant_id, item_id).

Columns:
  hk_lnk_order_item : SHA-256 hash of (SALT || order_id || restaurant_id || item_id)  — PK
  hk_order           : FK → hub_order
  hk_menu_item       : FK → hub_menu_item
  load_dts           : Earliest observed load timestamp
  record_source      : Source that first introduced this relationship
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.lnk_order_item",
    comment="Link — order ↔ menu_item at line-item grain",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_lnk_order_item"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_lnk_order_item IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_lnk_order_item) = 64",
        "fk_order_not_null": "hk_order IS NOT NULL",
        "fk_menu_item_not_null": "hk_menu_item IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def lnk_order_item():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_order_items")
        .select(
            "hk_lnk_order_item", "hk_order", "hk_menu_item",
            "load_dts", "record_source",
        )
        .groupBy("hk_lnk_order_item", "hk_order", "hk_menu_item")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
