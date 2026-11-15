"""Raw Vault · Hub — Review

One row per unique review_id.

Columns:
  hk_review      : SHA-256 hash of (SALT || review_id)  — PK
  review_id      : Natural business key
  load_dts       : Earliest observed load timestamp
  record_source  : Source that first introduced this key
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.hub_review",
    comment="Hub — unique review business keys",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_review"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_review IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_review) = 64",
        "bk_not_null": "review_id IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def hub_review():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_reviews")
        .select("hk_review", "review_id", "load_dts", "record_source")
        .groupBy("hk_review", "review_id")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
