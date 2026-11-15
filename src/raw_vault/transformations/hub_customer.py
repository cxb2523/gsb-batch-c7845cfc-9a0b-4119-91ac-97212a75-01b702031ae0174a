"""Raw Vault · Hub — Customer

One row per unique customer_id across all source systems.
Deduplication is handled by GROUP BY on the hash key — the MV's
incremental refresh ensures only new staging rows are processed.

Columns:
  hk_customer    : SHA-256 hash of (SALT || customer_id)   — PK
  customer_id    : Natural business key
  load_dts       : Earliest observed load timestamp
  record_source  : Source that first introduced this key
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F

CATALOG_SCHEMA = "restaurant_chain_db.raw_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.hub_customer",
    comment="Hub — unique customer business keys",
    table_properties={
        "quality": "raw_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_customer"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_customer IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_customer) = 64",
        "bk_not_null": "customer_id IS NOT NULL",
        "load_dts_not_null": "load_dts IS NOT NULL",
    }
)
def hub_customer():
    # Customers appear in two staging sources: stg_customers and stg_orders.
    # UNION ALL then GROUP BY guarantees one hub row per business key.
    stg_cust = spark.read.table("restaurant_chain_db.staging.stg_customers").select(
        "hk_customer", "customer_id", "load_dts", "record_source"
    )
    stg_ord = spark.read.table("restaurant_chain_db.staging.stg_orders").select(
        "hk_customer", "customer_id", "load_dts", "record_source"
    )

    return (
        stg_cust.unionByName(stg_ord)
        .groupBy("hk_customer", "customer_id")
        .agg(
            F.min("load_dts").alias("load_dts"),
            F.min("record_source").alias("record_source"),
        )
    )
