"""Business Vault · PIT — Customer (Lean SCD2-Style)

One row per satellite version with effective_from / effective_to.
LEAD(load_dts) OVER (PARTITION BY hk_customer) computes effective_to.

Lean design: 504 rows (one per version) vs classic PIT's 184K rows/year.
Query pattern: WHERE effective_to IS NULL (current) or
               WHERE effective_from <= @date AND (effective_to IS NULL OR effective_to > @date)
"""
from pyspark import pipelines as dp
from pyspark.sql import functions as F
from pyspark.sql.window import Window

CATALOG_SCHEMA = "restaurant_chain_db.business_vault"


@dp.materialized_view(
    name=f"{CATALOG_SCHEMA}.pit_customer",
    comment="Lean PIT — customer attribute versions with effective_from/to dating",
    table_properties={
        "quality": "business_vault",
        "delta.deletedFileRetentionDuration": "interval 30 days",
        "delta.logRetentionDuration": "interval 90 days",
    },
    cluster_by=["hk_customer"],
)
@dp.expect_all_or_fail(
    {
        "pk_not_null": "hk_customer IS NOT NULL",
        "pk_valid_hash": "LENGTH(hk_customer) = 64",
        "effective_from_not_null": "effective_from IS NOT NULL",
    }
)
@dp.expect("valid_date_order", "effective_to IS NULL OR effective_to > effective_from")
def pit_customer():
    w = Window.partitionBy("hk_customer").orderBy("load_dts")

    return (
        spark.read.table("restaurant_chain_db.raw_vault.sat_customer")
        .select(
            "hk_customer",
            F.col("load_dts").alias("effective_from"),
            F.lead("load_dts").over(w).alias("effective_to"),
            "name",
            "email",
            "phone",
            "city",
            "join_date",
            "record_source",
        )
    )
