"""Staging · Reviews

Reads the bronze reviews MV and prepares it for the raw vault.
Reviews link to orders (and transitively to customers and restaurants
via lnk_order).

Downstream consumers:
  hub_review, lnk_review_order, sat_review
"""
from pyspark import pipelines as dp

from _dv_hash import DV_LOAD_DTS, dv_record_source, hash_diff, hash_key

CATALOG_SCHEMA = "restaurant_chain_db.staging"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.stg_reviews",
    comment="Staged reviews with DV hash keys, hash-diff, and audit metadata",
    table_properties={"quality": "staging"},
)
@dp.expect_all_or_fail(
    {
        "valid_review_id": "review_id IS NOT NULL",
        "valid_order_id": "order_id IS NOT NULL",
    }
)
def stg_reviews():
    df = spark.read.table("restaurant_chain_db.01_bronze.reviews")

    # ── Hash keys ─────────────────────────────────────────────────
    df = hash_key(df, ["review_id"], "hk_review")
    df = hash_key(df, ["order_id"], "hk_order")

    # Link hash key: review ↔ order
    df = hash_key(
        df,
        ["review_id", "order_id"],
        "hk_lnk_review_order",
    )

    # ── Hash-diff for sat_review ──────────────────────────────────
    df = hash_diff(
        df,
        ["review_text", "rating", "review_timestamp"],
    )

    # ── Metadata ──────────────────────────────────────────────────
    df = df.withColumn("load_dts", DV_LOAD_DTS())
    df = df.withColumn("record_source", dv_record_source("BRONZE_REVIEWS"))

    return df.select(
        "hk_review", "hk_order", "hk_lnk_review_order", "hash_diff",
        "review_id", "order_id", "customer_id", "restaurant_id",
        "review_text", "rating", "review_timestamp",
        "load_dts", "record_source",
    )
