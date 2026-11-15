"""Raw Vault · Satellite — Review

Tracks the full history of review attributes.
Note: review_text may contain PII — NLP-based redaction
recommended for downstream consumption (same as bronze).

Grain: one row per (hk_review, hash_diff).
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.sat_review",
    comment="Satellite — review text and rating history",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_review"],
)
@dp.expect_all_or_fail(
    {
        "fk_not_null": "hk_review IS NOT NULL",
        "hash_diff_not_null": "hash_diff IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def sat_review():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_reviews")
        .groupBy(
            "hk_review",
            "hash_diff",
            "review_text",
            "rating",
            "review_timestamp",
        )
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
        .select(
            "hk_review", "hash_diff",
            "load_dts", "record_source",
            "review_text", "rating", "review_timestamp",
        )
    )
