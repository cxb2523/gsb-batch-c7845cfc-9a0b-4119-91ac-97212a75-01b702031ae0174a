"""Raw Vault · Satellite — Order Item (on Link)

Satellite attached to lnk_order_item, storing the transactional
payload for each order line (quantity, price, subtotal).

This is a link-satellite — its parent key is the link hash key,
not a hub hash key.

Grain: one row per (hk_lnk_order_item, hash_diff_order_item).
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.sat_order_item",
    comment="Link-satellite — order line-item transactional payload",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_lnk_order_item"],
)
@dp.expect_all_or_fail(
    {
        "fk_not_null": "hk_lnk_order_item IS NOT NULL",
        "hash_diff_not_null": "hash_diff IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def sat_order_item():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_order_items")
        .groupBy(
            "hk_lnk_order_item",
            "hash_diff_order_item",
            "line_number",
            "item_name",
            "item_category",
            "quantity",
            "unit_price",
            "subtotal",
        )
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
        .select(
            "hk_lnk_order_item",
            F.col("hash_diff_order_item").alias("hash_diff"),
            "load_dts",
            "record_source",
            "line_number",
            "item_name",
            "item_category",
            "quantity",
            "unit_price",
            "subtotal",
        )
    )
