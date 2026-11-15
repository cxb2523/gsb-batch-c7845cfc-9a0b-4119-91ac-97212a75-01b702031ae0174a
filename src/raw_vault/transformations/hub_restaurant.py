"""Raw Vault · Hub — Restaurant

One row per unique restaurant_id across all source systems.

Columns:
  hk_restaurant  : SHA-256 hash of (SALT || restaurant_id)  — PK
  restaurant_id  : Natural business key
  load_dts       : Earliest observed load timestamp
  record_source  : Source that first introduced this key
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.hub_restaurant",
    comment="Hub — unique restaurant business keys",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_restaurant"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_restaurant IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_restaurant) = 64",
        "bk_not_null": "restaurant_id IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def hub_restaurant():
    stg_rest = spark.read.table("restaurant_chain_db.staging.stg_restaurants").select(
        "hk_restaurant", "restaurant_id", "load_dts", "record_source"
    )
    stg_ord = spark.read.table("restaurant_chain_db.staging.stg_orders").select(
        "hk_restaurant", "restaurant_id", "load_dts", "record_source"
    )

    return (
        stg_rest.unionByName(stg_ord)
        .groupBy("hk_restaurant", "restaurant_id")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
