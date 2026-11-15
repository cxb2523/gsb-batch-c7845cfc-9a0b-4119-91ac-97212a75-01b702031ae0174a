"""Raw Vault · Satellite — Customer

Tracks the full history of customer descriptive attributes.
A new row is inserted only when hash_diff changes, implementing
SCD2-equivalent behaviour through the Data Vault pattern.

Grain: one row per (hk_customer, hash_diff) — each unique attribute
snapshot is stored exactly once.

Columns:
  hk_customer   : FK → hub_customer  (part of composite PK)
  hash_diff     : SHA-256 of all descriptive attributes  (part of composite PK)
  load_dts      : When this version was first observed
  record_source : Originating source system
  name … join_date : Descriptive attributes (PII already masked in bronze)
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.sat_customer",
    comment="Satellite — customer attribute history (PII masked)",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_customer"],
)
@dp.expect_all_or_fail(
    {
        "fk_not_null": "hk_customer IS NOT NULL",
        "hash_diff_not_null": "hash_diff IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def sat_customer():
    return (
        spark.read.table("restaurant_chain_db.staging.stg_customers")
        .groupBy(
            "hk_customer",
            "hash_diff",
            "name",
            "email",
            "phone",
            "city",
            "join_date",
        )
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
        .select(
            "hk_customer", "hash_diff",
            "load_dts", "record_source",
            "name", "email", "phone", "city", "join_date",
        )
    )
