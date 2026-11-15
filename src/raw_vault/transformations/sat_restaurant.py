"""Raw Vault · Satellite — Restaurant

Tracks the full history of restaurant descriptive attributes.

Grain: one row per (hk_restaurant, hash_diff).
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.sat_restaurant",
    comment="Satellite — restaurant attribute history",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_restaurant"],
)
@dp.expect_all_or_fail(
    {
        "fk_not_null": "hk_restaurant IS NOT NULL",
        "hash_diff_not_null": "hash_diff IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def sat_restaurant():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_restaurants")
        .groupBy(
            "hk_restaurant",
            "hash_diff",
            "name",
            "city",
            "country",
            "address",
            "opening_date",
            "phone",
        )
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
        .select(
            "hk_restaurant", "hash_diff",
            "load_dts", "record_source",
            "name", "city", "country", "address", "opening_date", "phone",
        )
    )
