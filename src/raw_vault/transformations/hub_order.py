"""Raw Vault · Hub — Order

One row per unique order_id.

Columns:
  hk_order       : SHA-256 hash of (SALT || order_id)  — PK
  order_id       : Natural business key
  load_dts       : Earliest observed load timestamp
  record_source  : Source that first introduced this key
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.hub_order",
    comment="Hub — unique order business keys",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_order"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_order IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_order) = 64",
        "bk_not_null": "order_id IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def hub_order():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_orders")
        .select("hk_order", "order_id", "load_dts", "record_source")
        .groupBy("hk_order", "order_id")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
