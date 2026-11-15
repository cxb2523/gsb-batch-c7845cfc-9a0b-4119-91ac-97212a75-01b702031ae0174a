"""Raw Vault · Link — Review ↔ Order

Models the relationship: review ↔ order.
Customer and restaurant are reachable transitively via lnk_order,
so they are NOT duplicated here (avoids speculative links).

Columns:
  hk_lnk_review_order : SHA-256 hash of (SALT || review_id || order_id)  — PK
  hk_review            : FK → hub_review
  hk_order             : FK → hub_order
  load_dts             : Earliest observed load timestamp
  record_source        : Source that first introduced this relationship
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.lnk_review_order",
    comment="Link — review ↔ order relationship",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_lnk_review_order"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_lnk_review_order IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_lnk_review_order) = 64",
        "fk_review_not_null": "hk_review IS NOT NULL",
        "fk_order_not_null": "hk_order IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def lnk_review_order():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_reviews")
        .select(
            "hk_lnk_review_order", "hk_review", "hk_order",
            "load_dts", "record_source",
        )
        .groupBy("hk_lnk_review_order", "hk_review", "hk_order")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
