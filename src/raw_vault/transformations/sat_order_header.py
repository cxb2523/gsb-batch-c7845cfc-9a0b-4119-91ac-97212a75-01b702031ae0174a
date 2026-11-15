"""Raw Vault · Multi-Active Satellite — Order Header

Tracks the full history of order-level descriptive attributes.
This is a MULTI-ACTIVE SATELLITE (DV 2.1 pattern): when order_status
changes (e.g. placed → confirmed → delivered), a new hash_diff is
computed, creating a new satellite row. The pit_order downstream
provides effective_from/effective_to dating for each version.

The insert-only GROUP BY approach means each unique attribute snapshot
is stored exactly once. Multiple rows per hk_order represent status
progression over time — the defining characteristic of a multi-active
satellite vs. a standard single-active satellite.

Grain: one row per (hk_order, hash_diff_order_header) — insert-only SCD2.
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.sat_order_header",
    comment="Multi-active satellite — order header attribute history (status, payment, amounts)",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_order"],
)
@dp.expect_all_or_fail(
    {
        "fk_not_null": "hk_order IS NOT NULL",
        "hash_diff_not_null": "hash_diff IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def sat_order_header():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_orders")
        .groupBy(
            "hk_order",
            "hash_diff_order_header",
            "order_timestamp",
            "order_type",
            "total_amount",
            "payment_method",
            "order_status",
            "source",
        )
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
        .select(
            "hk_order",
            F.col("hash_diff_order_header").alias("hash_diff"),
            "load_dts",
            "record_source",
            "order_timestamp",
            "order_type",
            "total_amount",
            "payment_method",
            "order_status",
            "source",
        )
    )
